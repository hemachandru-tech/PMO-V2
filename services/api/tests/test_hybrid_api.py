"""US-255 Hybrid mode through Ask (``TELA_ASK_MODE=hybrid``): Plan B that answers with a certified measure when the
frame's entity, aggregation and conditions equal one. The TPG fixture has 65 open projects."""

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
from test_ask_api import ask, by_event

from tela_api.core.settings import get_settings

pytestmark = [needs_db, pytest.mark.integration]

OPEN = [p for p in oracle.PROJECTS if oracle.RULES["open_projects"](p)]


@pytest.fixture
async def world(stack: httpx.AsyncClient, owner_conn: psycopg.Connection, make_token: Callable[..., str],
                tpg_source: dict[str, Any]) -> World:
    out = await setup_world(stack, owner_conn, make_token, tpg_source)
    await set_mode(stack, out, "hybrid")
    return out


async def set_mode(stack: httpx.AsyncClient, w: World, mode: str) -> None:
    """The source's Ask answer mode (US-256)."""
    resp = await stack.put(f"/api/v1/connections/{w.connection_id}/ask-mode", headers=h(w.tokens["admin"]),
                           json={"mode": mode})
    assert resp.status_code == 200, resp.text


@pytest.fixture(autouse=True)
def rules_frame(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(get_settings(), "ask_frame", "rules")
    yield


async def test_how_many_open_projects_uses_the_certified_measure(stack: httpx.AsyncClient, world: World) -> None:
    turn_id, events = await ask(stack, world.tokens["admin"], "How many open projects")
    ev = by_event(events)
    assert ev["plan"]["planner"] == "hybrid" and ev["plan"]["certified"]
    assert ev["plan"]["spec"]["measure"] == {"ref": "measure:open_projects"}
    done = ev["complete"]
    assert done["rows"] == [[len(OPEN)]] == [[65]] and done["certified"] and done["badge"] == "certified"
    assert done["fields"]["certified_by"] == "measure:open_projects"
    run = (await runs(stack, world.tokens["admin"], turn_id=turn_id))[0]
    d = await detail(stack, world.tokens["admin"], run["id"])
    step = next(s for s in d["steps"] if s["label"] == "Planned by hybrid: ok")
    assert step["detail"]["fields"]["hybrid"] == "measure:open_projects"


async def test_a_list_of_open_projects_has_as_many_rows_as_the_measure(stack: httpx.AsyncClient,
                                                                       world: World) -> None:
    token = world.tokens["admin"]
    _, events = await ask(stack, token, "list of open projects")
    ev = by_event(events)
    assert ev["plan"]["planner"] == "hybrid"
    spec = ev["plan"]["spec"]
    assert spec["measure"]["ref"] == "measure:_adhoc"  # B: one row per record, with the measure's condition
    assert spec["measure"]["adhoc"]["where"] == {"field": "current_state", "op": "in",
                                                 "values": ["Project Created", "Active", "On Hold"]}
    _, counted = await ask(stack, token, "How many open projects")
    assert ev["complete"]["row_count"] == by_event(counted)["complete"]["rows"][0][0] == 65
    assert ev["complete"]["certified"]


async def test_a_scoped_viewer_gets_the_measure_in_her_scope(stack: httpx.AsyncClient, world: World) -> None:
    mine = sum(1 for p in OPEN if p["cio"] == "Nomsa Osei")
    _, events = await ask(stack, world.tokens["nomsa"], "How many open projects")
    ev = by_event(events)
    assert ev["plan"]["spec"]["measure"] == {"ref": "measure:open_projects"} and ev["complete"]["rows"] == [[mine]]
    _, events = await ask(stack, world.tokens["nomsa"], "list of open projects")
    assert by_event(events)["complete"]["row_count"] == mine
