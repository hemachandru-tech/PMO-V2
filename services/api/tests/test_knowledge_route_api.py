"""Knowledge-led planning route in Ask (ENH-017, US-204; FEAT-067 / FEAT-066 / FEAT-062), live on the TPG fixture.

Acceptance: with EXEC-115's mapping (the pack's Status read from current state), "open projects (current state
Project Created, Active or On Hold)" is counted by the stated states — every one of them — and says so, with the
certified Open projects figure beside it; the same question without criteria answers the certified measure; an
approved business definition of "open project" is applied the same way, cited by its passage, under its own badge.
No model is called on this route (W2: numbers from queries only).
"""

from __future__ import annotations

from typing import Any

import httpx
import psycopg
import pytest
from api_testkit import needs_db
from conftest import FakeGateway
from pmo_testkit import World, h, setup_world
from test_answer_sanity_api import exec115, world  # noqa: F401 - the fixtures
from test_ask_api import ask, by_event
from test_confidence_api import plan_calls, samples
from test_knowledge_definitions_api import (
    OPEN_STATES,
    by_term,
    indexed_doc,
    listing,
    record_inactive,  # noqa: F401 - the fixture
    review,
    source_count,
)
from test_knowledge_definitions_api import world as knowledge_world  # noqa: F401 - the fixture

pytestmark = [needs_db, pytest.mark.integration]

EXEC115 = "How many open projects (current state Project Created, Active or On Hold) are there?"
PLAIN = "How many open projects are there?"


def states(spec: dict[str, Any]) -> set[str]:
    return {v for f in spec["filters"] if f["ref"] == "dimension:current_state" for v in f["values"]}


async def test_exec115_question_is_counted_by_the_stated_states(stack: httpx.AsyncClient, exec115: World) -> None:  # noqa: F811
    fake = FakeGateway({"AG-03": samples({"measure": "measure:open_projects"})})
    stack.app.state.gateway = fake.client()  # type: ignore[attr-defined]
    _, events = await ask(stack, exec115.tokens["admin"], EXEC115)
    ev = by_event(events)
    assert ev["plan"]["planner"] == "knowledge" and plan_calls(fake) == []  # no model on this route
    assert ev["plan"]["spec"]["measure"] == {"ref": "measure:total_projects"}
    assert states(ev["plan"]["spec"]) == set(OPEN_STATES)  # On Hold kept, not read as Projects on hold
    said = "Counted by your definition: Current state in Active, Project Created, On Hold"
    assert ev["plan"]["interpreted"][0] == said
    stated = source_count("current_state = ANY(%s)", (list(OPEN_STATES),))
    assert ev["narrative"]["facts"]["value"] == stated
    assert ev["narrative"]["sentences"][0] == {"text": said, "cites": []}
    done = ev["complete"]
    assert done["badge"] == "your_definition" and done["certified"] is False and done["can_export"] is True
    assert done["counted_by"]["source"] == "question" and done["counted_by"]["measure"]["label"] == "Open projects"
    # The certified measure's own figure beside it (EXEC-115's mapping: current state Active).
    certified = source_count("current_state = 'Active'")
    assert certified != stated
    assert done["beside"]["label"] == "Open projects" and done["beside"]["value"] == certified
    assert done["beside"]["spec"]["filters"] == [] and "own_filters" not in done["beside"]


@pytest.fixture
async def decided(stack: httpx.AsyncClient, owner_conn: psycopg.Connection, make_token: Any,
                  tpg_source: dict[str, Any]) -> World:
    """The pack as released (2.2.0): open is the current state Project Created, Active or On Hold (DEC-24)."""
    return await setup_world(stack, owner_conn, make_token, tpg_source)


async def test_exec115_question_on_the_decided_rule_is_open_projects(stack: httpx.AsyncClient, decided: World) -> None:
    """US-202: the states EXEC-115 lists are RULE-026 itself, so the certified measure answers, with no filter."""
    _, events = await ask(stack, decided.tokens["admin"], EXEC115)
    ev = by_event(events)
    assert ev["plan"]["planner"] == "rules" and ev["plan"]["spec"]["measure"] == {"ref": "measure:open_projects"}
    assert ev["plan"]["spec"]["filters"] == []
    done = ev["complete"]
    assert done["certified"] and done["badge"] == "certified" and "counted_by" not in done
    assert ev["narrative"]["facts"]["value"] == source_count("current_state = ANY(%s)", (list(OPEN_STATES),))


async def test_the_same_question_without_criteria_answers_the_certified_measure(
        stack: httpx.AsyncClient, exec115: World) -> None:  # noqa: F811
    _, events = await ask(stack, exec115.tokens["admin"], PLAIN)
    ev = by_event(events)
    assert ev["plan"]["planner"] == "rules" and ev["plan"]["spec"]["measure"] == {"ref": "measure:open_projects"}
    assert ev["complete"]["certified"] and ev["complete"]["badge"] == "certified"
    assert "counted_by" not in ev["complete"] and ev["complete"]["beside"] is None


async def test_the_right_mapping_keeps_the_rules_and_their_checks(
        stack: httpx.AsyncClient, world: World) -> None:  # noqa: F811
    """Status reads the record state, the question the current state: not the rule's field, so no route (W4); the
    rules' plan leaves On Hold out and US-205 asks, with the stated states among the readings."""
    _, events = await ask(stack, world.tokens["admin"], EXEC115)
    ev = by_event(events)
    assert [e[1] for e in events][-1] == "clarify" and "plan" not in ev
    labels = [o["label"] for o in ev["clarify"]["options"]]
    assert labels[0] == "Open projects, Current state: Active, Project Created"
    assert "Projects, Current state: Active, Project Created, On Hold" in labels


async def test_an_approved_definition_is_applied_and_cited(
        stack: httpx.AsyncClient, knowledge_world: World, signing_keys: dict[str, Any],  # noqa: F811
        record_inactive: list[str]) -> None:  # noqa: F811
    world_ = knowledge_world
    admin = world_.tokens["admin"]
    await indexed_doc(stack, world_, signing_keys, approve=True)
    open_ = by_term((await listing(stack, admin))["items"])["Open project"]

    # Pending: the certified measure answers.
    ev = by_event((await ask(stack, admin, PLAIN))[1])
    assert ev["plan"]["planner"] == "rules" and ev["plan"]["spec"]["measure"] == {"ref": "measure:open_projects"}

    ok = await review(stack, admin, open_["id"], decision="approve")
    assert ok.status_code == 200 and ok.json()["conflict"], ok.text
    # US-252: until the Admin chooses, the certified measure answers and the conflict is noted.
    ev = by_event((await ask(stack, admin, PLAIN))[1])
    assert ev["plan"]["planner"] == "rules" and ev["plan"]["spec"]["measure"] == {"ref": "measure:open_projects"}
    assert ev["complete"]["badge"] == "definition_conflict" and ev["complete"]["certified"] is False
    assert "Not certified here: the approved definition “Open project” differs from Open projects" in \
        ev["complete"]["interpreted"]
    cond = (await stack.get("/api/v1/knowledge/conditions", headers=h(admin), params={"conflicts": True})).json()
    [held] = cond["items"]
    chose = await stack.post(f"/api/v1/knowledge/conditions/{held['id']}/choose", headers=h(admin),
                             json={"use": "condition"})
    assert chose.status_code == 200 and chose.json()["uses"] == "condition", chose.text
    ev = by_event((await ask(stack, admin, PLAIN))[1])
    assert ev["plan"]["planner"] == "knowledge" and ev["plan"]["spec"]["measure"] == {"ref": "measure:total_projects"}
    assert states(ev["plan"]["spec"]) == set(OPEN_STATES)
    said = "Counted by the approved definition “Open project”: Current state in Project Created, Active, On Hold"
    assert ev["plan"]["interpreted"][0] == said
    defined = source_count("current_state = ANY(%s)", (list(OPEN_STATES),))
    certified = source_count("status = 'Active'")
    assert ev["narrative"]["facts"]["value"] == defined and defined - certified == 3
    chunk = open_["passage"]["chunk_id"]
    assert ev["narrative"]["sentences"][0] == {"text": said, "cites": [f"chunk:{chunk}"]}
    used = ev["narrative"]["definitions_used"][0]["definition"]
    assert used["term"] == "Open project" and used["chunk_id"] == chunk and used["document"]["title"] == \
        "pmo definitions"
    done = ev["complete"]
    assert done["badge"] == "knowledge_definition" and done["certified"] is False
    assert done["beside"]["label"] == "Open projects" and done["beside"]["value"] == certified
    assert done["confidence"]["score"] >= done["confidence"]["threshold"]

    # US-205: a plan the route can't count by (two measures side by side) is not marked certified, and says why.
    ev = by_event((await ask(stack, admin, "Show open projects and closed projects"))[1])
    assert ev["plan"]["spec"]["extra_measures"] == [{"ref": "measure:inactive_projects"}]
    done = ev["complete"]
    assert done["certified"] is False and done["badge"] == "definition_conflict"
    assert done["definition_conflict"] == {"id": open_["id"], "term": "Open project"}
    assert "Not certified here: the approved definition “Open project” differs from Open projects" in \
        done["interpreted"]
