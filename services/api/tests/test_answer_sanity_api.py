"""Answer sanity checks in Ask (ENH-017, US-205; FEAT-067 / FEAT-064), on the TPG fixture with EXEC-115's mapping.

Acceptance: with the pack's Status read from current state (EXEC-115), "open projects (current state Project Created
or Active)" no longer answers certified at confidence 1.0 — the filter leaves Open projects unchanged while narrowing
the projects, so Ask asks with the readings (the projects counted by the stated states among them) or, with an LLM,
AG-03 plans it once more; the same question on the right mapping is answered by the rules as before.
"""

from __future__ import annotations

from typing import Any

import httpx
import psycopg
import pytest
from api_testkit import needs_db
from conftest import FakeGateway
from pmo_testkit import World, open_by_status_draft, setup_world
from psycopg.types.json import Jsonb
from test_ask_api import ask, by_event
from test_confidence_api import choose, plan_calls, samples

from tela_api.ask import stated
from tela_api.ask.orchestrator import MSG_REPLANNED

pytestmark = [needs_db, pytest.mark.integration]

QUESTION = "How many open projects (current state Project Created or Active) are there?"
STATES = {"ref": "dimension:current_state", "op": "in", "values": ["Active", "Project Created"]}
# The TPG fixture: status Active with current state Project Created 49, Active 13, On Hold 3.
OPEN, STATED, OPEN_EXEC115 = 65, 62, 13


@pytest.fixture
def no_route(monkeypatch: pytest.MonkeyPatch) -> None:
    """US-204 counts these questions by the stated states (``test_knowledge_route_api``); without that route, the
    checks of US-205 still catch them."""
    monkeypatch.setattr(stated, "from_question", lambda *a, **k: None)


@pytest.fixture
async def world(stack: httpx.AsyncClient, owner_conn: psycopg.Connection, make_token: Any,
                tpg_source: dict[str, Any]) -> World:
    """Open = Status Active, as pack 2.1.0 had RULE-026 (DEC-24 moved it to the current state)."""
    return await setup_world(stack, owner_conn, make_token, tpg_source,
                             before_approve=open_by_status_draft(owner_conn))


@pytest.fixture
async def exec115(stack: httpx.AsyncClient, owner_conn: psycopg.Connection, make_token: Any,
                  tpg_source: dict[str, Any]) -> World:
    """The model approved with the pack's Status field read from ``current_state``, as on TPG MySQL (EXEC-115), and open
    = Status Active, as pack 2.1.0 had RULE-026."""
    def mapping(world: World) -> None:
        row = owner_conn.execute("SELECT id, definition FROM model.model_versions WHERE data_model_id = %s AND "
                                 "status = 'draft'", (world.model_id,)).fetchone()
        assert row is not None
        projects = next(e for e in row[1]["entities"] if e["key"] == "projects")
        next(f for f in projects["fields"] if f["key"] == "status")["source_column"] = "current_state"
        owner_conn.execute("UPDATE model.model_versions SET definition = %s WHERE id = %s", (Jsonb(row[1]), row[0]))
        open_by_status_draft(owner_conn)(world)
    # US-199 now blocks two fields on one column at approval; EXEC-115's model predates it, so it is overridden here.
    return await setup_world(stack, owner_conn, make_token, tpg_source, before_approve=mapping,
                             override_note="EXEC-115 replay: Status read from current state.")


async def test_the_right_mapping_is_answered_by_the_rules_as_before(stack: httpx.AsyncClient, world: World) -> None:
    _, events = await ask(stack, world.tokens["admin"], QUESTION)
    ev = by_event(events)
    assert ev["plan"]["planner"] == "rules" and ev["plan"]["spec"]["filters"] == [STATES]
    assert ev["complete"]["certified"] and ev["complete"]["confidence"]["score"] == 1.0
    assert ev["narrative"]["facts"]["value"] == OPEN - 3  # status Active and the two states stated


async def test_exec115_mapping_asks_instead_of_answering_certified(
        stack: httpx.AsyncClient, exec115: World, no_route: None) -> None:
    world = exec115
    admin = world.tokens["admin"]
    turn_id, events = await ask(stack, admin, QUESTION)
    ev = by_event(events)
    assert [e[1] for e in events][-1] == "clarify" and "plan" not in ev
    q = ev["clarify"]
    assert q["kind"] == "reading" and any("don't change the total" in w for w in q["why"])
    labels = [o["label"] for o in q["options"]]
    assert labels[0] == "Open projects, Current state: Active, Project Created"
    stated = "Projects, Current state: Active, Project Created"
    assert stated in labels

    resumed = by_event(await choose(stack, admin, turn_id, q["options"][labels.index(stated)]["id"], events))
    assert resumed["plan"]["spec"]["measure"] == {"ref": "measure:total_projects"}
    assert resumed["plan"]["spec"]["filters"] == [STATES] and resumed["narrative"]["facts"]["value"] == STATED

    # Assume first answers the rules' reading, with the confidence below "balanced" and the reason on the card.
    resp = await stack.put("/api/v1/me/preferences", headers={"Authorization": f"Bearer {admin}"},
                           json={"ask_verbosity": "assume_first"})
    assert resp.status_code == 200, resp.text
    _, events = await ask(stack, admin, QUESTION)
    done = by_event(events)["complete"]
    assert done["confidence"]["score"] < 0.6 and done["confidence"]["signals"]["filters_no_effect"] is True
    assert by_event(events)["narrative"]["facts"]["value"] == OPEN_EXEC115
    assert any(n.get("kind") == "filters_no_effect" for n in by_event(events)["narrative"]["notes"])


async def test_exec115_mapping_is_planned_once_more_by_ag03(
        stack: httpx.AsyncClient, exec115: World, no_route: None) -> None:
    world = exec115
    fake = FakeGateway({"AG-03": samples({"measure": "measure:total_projects", "filters": [STATES],
                                          "confidence": 0.9, "reasoning_summary": "Projects in the stated states."})})
    stack.app.state.gateway = fake.client()  # type: ignore[attr-defined]
    admin = world.tokens["admin"]
    turn_id, events = await ask(stack, admin, QUESTION)
    assert len(plan_calls(fake)) == 3  # one re-plan, voted on as AG-03's plans are
    # AG-03's reading counts the stated states, not the named measure: without US-204's route, the person confirms
    # it (W3: use the stated definition and say so, or ask).
    q = by_event(events)["clarify"]
    assert q["options"][0]["label"] == "Projects, Current state: Active, Project Created"
    assert "the question names Open projects" in q["why"]
    resumed = by_event(await choose(stack, admin, turn_id, "a", events))
    assert resumed["plan"]["planner"] == "llm" and resumed["narrative"]["facts"]["value"] == STATED

    resp = await stack.put("/api/v1/me/preferences", headers={"Authorization": f"Bearer {admin}"},
                           json={"ask_verbosity": "assume_first"})
    assert resp.status_code == 200, resp.text
    _, events = await ask(stack, admin, QUESTION)
    ev = by_event(events)
    assert ev["plan"]["planner"] == "llm" and ev["plan"]["interpreted"][0] == MSG_REPLANNED
    assert ev["plan"]["spec"]["measure"] == {"ref": "measure:total_projects"}
    assert ev["narrative"]["facts"]["value"] == STATED


async def test_a_filter_with_an_effect_never_calls_ag03(stack: httpx.AsyncClient, world: World) -> None:
    fake = FakeGateway({"AG-03": samples({"measure": "measure:total_projects"})})
    stack.app.state.gateway = fake.client()  # type: ignore[attr-defined]
    _, events = await ask(stack, world.tokens["admin"], QUESTION)
    assert by_event(events)["plan"]["planner"] == "rules" and plan_calls(fake) == []
