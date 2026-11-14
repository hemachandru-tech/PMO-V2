"""US-254 Plan B through Ask (the source's ``ask_mode`` is ``fields``, US-256): the frame grounded on fields, approved named conditions and
linked values, compiled by the shape's template, run on the scoped path. The TPG fixture has 65 open projects (the
live Dataverse source of run ``01a1ca5c`` had 103), 11 of them in GCC - UAE."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Any

import httpx
import pmo_oracle as oracle
import psycopg
import pytest
from api_testkit import needs_db
from pmo_testkit import World, h, setup_world
from test_agent_runs_api import detail, runs
from test_ask_api import ask, by_event, parse_sse

from tela_api.core.settings import get_settings

pytestmark = [needs_db, pytest.mark.integration]

OPEN = [p for p in oracle.PROJECTS if oracle.RULES["open_projects"](p)]


@pytest.fixture(autouse=True)
def fields_mode(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(get_settings(), "ask_frame", "rules")
    yield


@pytest.fixture
async def world(stack: httpx.AsyncClient, owner_conn: psycopg.Connection, make_token: Callable[..., str],
                tpg_source: dict[str, Any]) -> World:
    out = await setup_world(stack, owner_conn, make_token, tpg_source)
    resp = await stack.put(f"/api/v1/connections/{out.connection_id}/ask-mode", headers=h(out.tokens["admin"]),
                           json={"mode": "fields"})  # US-256: Plan B is the source's answer mode
    assert resp.status_code == 200, resp.text
    return out


async def test_the_01a1ca5c_question_lists_every_open_project(stack: httpx.AsyncClient, world: World) -> None:
    turn_id, events = await ask(stack, world.tokens["admin"], "Show the List of open Projects")
    ev = by_event(events)
    assert "plan" in ev, ev.get("error")
    spec = ev["plan"]["spec"]
    assert spec["breakdowns"][0] == {"ref": "field:projects.name"} and spec["having"] == {"op": ">", "value": 0}
    assert spec["measure"]["adhoc"]["where"] == {"field": "current_state", "op": "in",
                                                 "values": ["Project Created", "Active", "On Hold"]}
    done = ev["complete"]
    assert done["row_count"] == len(OPEN) == 65  # one row per open project, none of the 35 closed with a 0
    # W5: "open" is the approved term, and Total projects (a count with no rule) is certified
    assert done["certified"] and done["badge"] == "certified"
    (used,) = done["fields"]["conditions_used"]
    assert used["term"] == "Open projects" and used["source"] == "pack_measure" and used["how"] == "definition"
    assert used["source_label"] == "certified measure"
    run = (await runs(stack, world.tokens["admin"], turn_id=turn_id))[0]
    d = await detail(stack, world.tokens["admin"], run["id"])
    step = next(s for s in d["steps"] if s["label"] == "Planned by fields: ok")
    assert step["detail"]["frame"]["frame"]["shape"] == "list_of_records"
    assert not [s for s in d["steps"] if s["label"].startswith("Question frame (shadow)")]  # recorded once, here


async def test_how_many_open_projects_in_uae(stack: httpx.AsyncClient, world: World) -> None:
    _, events = await ask(stack, world.tokens["admin"], "How many open projects in UAE")
    ev = by_event(events)
    assert ev["plan"]["planner"] == "fields"
    where = ev["plan"]["spec"]["measure"]["adhoc"]["where"]
    assert {"field": "region", "op": "=", "value": "GCC - UAE"} in where["all"]
    expected = sum(1 for p in OPEN if p["region"] == "GCC - UAE")
    assert ev["complete"]["rows"] == [[expected]] and expected == 11
    sources = {u["source"] for u in ev["complete"]["fields"]["conditions_used"]}
    assert sources == {"pack_measure", "value"}


async def test_negation_and_scope(stack: httpx.AsyncClient, world: World) -> None:
    """"not in UAE" negates the value; the Creator's Gulf scope still applies (RULE-004)."""
    _, events = await ask(stack, world.tokens["creator"], "How many open projects not in UAE")
    ev = by_event(events)
    expected = sum(1 for p in OPEN if p["region"] == "KSA")  # Gulf scope: KSA and GCC - UAE; UAE left out
    assert ev["complete"]["rows"] == [[expected]], ev["plan"]["spec"]


async def test_breakdown_and_trend(stack: httpx.AsyncClient, world: World) -> None:
    _, events = await ask(stack, world.tokens["admin"], "open projects by region")
    ev = by_event(events)
    assert ev["plan"]["spec"]["breakdowns"] == [{"ref": "dimension:region"}]
    got = {r[0]: r[1] for r in ev["complete"]["rows"]}
    assert got["KSA"] == sum(1 for p in OPEN if p["region"] == "KSA")
    _, events = await ask(stack, world.tokens["admin"], "projects by planned deployment date by month")
    ev = by_event(events)
    assert ev["plan"]["spec"]["time"] == {"ref": "dimension:planned_deployment_date", "grain": "month"}
    assert "complete" in ev


async def test_a_shape_without_a_template_is_refused(stack: httpx.AsyncClient, world: World) -> None:
    _, events = await ask(stack, world.tokens["admin"], "distribution of projects")
    ev = by_event(events)
    assert "error" in ev and "can't answer this kind of question yet" in ev["error"]["message"]


async def test_an_unknown_term_is_named_not_guessed(stack: httpx.AsyncClient, world: World) -> None:
    _, events = await ask(stack, world.tokens["admin"], "How many zorbified projects")
    ev = by_event(events)
    assert "error" in ev and "zorbified" in ev["error"]["message"]


async def test_an_unknown_subject_asks_and_the_choice_is_kept(stack: httpx.AsyncClient, world: World) -> None:
    token = world.tokens["nomsa"]  # a Viewer, no free-hand grant: Plan B runs under the answer mode, in her scope
    mine = sum(1 for p in oracle.PROJECTS if p["cio"] == "Nomsa Osei")
    turn_id, events = await ask(stack, token, "How many zorbs")
    clarify = by_event(events)["clarify"]
    assert clarify["kind"] == "field" and clarify["key"] == "fields:subject:zorbs"
    option = next(o for o in clarify["options"] if o["value"] == "entity:projects")
    resp = await stack.post(f"/api/v1/ask/turns/{turn_id}/clarification", headers=h(token),
                            json={"option_id": option["id"]})
    assert resp.status_code == 202, resp.text
    stream = await stack.get(resp.json()["stream_url"], headers=h(token))
    ev = by_event(parse_sse(stream.text))
    assert ev["complete"]["rows"] == [[mine]] and ev["complete"]["certified"]
    got = await stack.get(f"/api/v1/ask/turns/{turn_id}", headers=h(token))
    assert got.json()["result"]["rows"] == [[mine]]  # read back without a free-hand grant
