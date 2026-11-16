"""AG-03 reviews every rules plan when LLM review is on (ENH-026, US-240; DEC-28), with a scripted AG-03.

Acceptance: a confirm lifts the score and answers; a different, valid reading makes Ask ask with both readings (or,
assume first, answer the rules' plan with a note); an invalid correction leaves the rules' plan; review off or a local
provider left at its default makes no review call.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import httpx
import psycopg
import pytest
from api_testkit import needs_db
from conftest import FakeGateway
from pmo_testkit import World, h
from test_ask_api import ask, by_event
from test_confidence_api import world  # noqa: F401 - the fixture with the approved pack glossary

pytestmark = [needs_db, pytest.mark.integration]

LATE = "How many late projects?"  # rules: the pack's trap reads it as Overdue go-lives, confidence 0.6


def review_on(conn: psycopg.Connection, w: World, on: bool = True) -> None:
    conn.execute("INSERT INTO ops.llm_settings (tenant_id, provider, models, llm_review) VALUES (%s, 'anthropic', %s, "
                 "%s) ON CONFLICT (tenant_id) DO UPDATE SET llm_review = EXCLUDED.llm_review",
                 (w.tenant_id, json.dumps({"planner": "claude-opus-5-5"}), on))


def reviewer(verdict: dict[str, Any]) -> Callable[[str, list[Any]], Any]:
    """AG-03: the review frame gets ``verdict``; any other AG-03 call (candidate pick) gets nothing."""
    def reply(task: str, messages: list[dict[str, Any]]) -> Any:
        return verdict if "Reply with verdict" in messages[0]["content"] else {}
    return reply


def reviews(fake: FakeGateway) -> list[dict[str, Any]]:
    return [c for c in fake.calls if c["agent"] == "AG-03" and "Reply with verdict" in c["messages"][0]["content"]]


async def test_a_confirmed_rules_plan_is_answered_more_surely(stack: httpx.AsyncClient, world: World,
                                                              owner_conn: psycopg.Connection) -> None:
    review_on(owner_conn, world)
    fake = FakeGateway({"AG-03": reviewer({"verdict": "confirm", "reason": "late means overdue go-lives"})})
    stack.app.state.gateway = fake.client()  # type: ignore[attr-defined]
    _, events = await ask(stack, world.tokens["creator"], LATE)
    ev = by_event(events)
    assert ev["plan"]["planner"] == "rules" and ev["plan"]["spec"]["measure"]["ref"] == "measure:overdue_go_lives"
    done = ev["complete"]
    assert done["confidence"]["score"] == 0.7  # the trap's 0.6, one step up
    assert done["confidence"]["signals"]["review"] == "confirm"
    sent = reviews(fake)
    assert len(sent) == 1 and sent[0]["task"] == "plan_review" and sent[0]["temperature"] is None  # one call, no vote
    draft = json.loads(sent[0]["messages"][1]["content"])["draft"]
    assert draft["plan"] == "Overdue go-lives" and draft["spec"]["measure"] == {"ref": "measure:overdue_go_lives"}
    assert "rows" not in sent[0]["messages"][1]["content"]


async def test_a_different_reading_is_asked_never_swapped(stack: httpx.AsyncClient, world: World,
                                                          owner_conn: psycopg.Connection) -> None:
    review_on(owner_conn, world)
    fake = FakeGateway({"AG-03": reviewer({"verdict": "correct", "replacement": {"measure": "measure:open_projects"},
                                           "reason": "late could mean projects past their end date"})})
    stack.app.state.gateway = fake.client()  # type: ignore[attr-defined]
    creator = world.tokens["creator"]
    _, events = await ask(stack, creator, LATE)
    ev = by_event(events)
    assert [e[1] for e in events][-1] == "clarify" and "plan" not in ev
    q = ev["clarify"]
    assert q["kind"] == "reading" and [o["label"] for o in q["options"]][:2] == ["Overdue go-lives", "Open projects"]
    assert "the planner read it differently" in q["why"]

    # Assume first: the rules' plan is answered, the other reading named, the score left low.
    resp = await stack.put("/api/v1/me/preferences", headers=h(creator), json={"ask_verbosity": "assume_first"})
    assert resp.status_code == 200, resp.text
    _, events = await ask(stack, creator, LATE)
    done = by_event(events)["complete"]
    assert done["spec"]["measure"]["ref"] == "measure:overdue_go_lives"
    assert "The planner read this as: Open projects" in done["interpreted"]
    assert done["confidence"]["score"] == 0.45 and done["confidence"]["signals"]["review"] == "correct"


async def test_an_invalid_correction_leaves_the_rules_plan(stack: httpx.AsyncClient, world: World,
                                                           owner_conn: psycopg.Connection) -> None:
    review_on(owner_conn, world)
    fake = FakeGateway({"AG-03": reviewer({"verdict": "correct", "replacement": {"measure": "measure:made_up"}})})
    stack.app.state.gateway = fake.client()  # type: ignore[attr-defined]
    _, events = await ask(stack, world.tokens["creator"], LATE)
    done = by_event(events)["complete"]
    assert done["spec"]["measure"]["ref"] == "measure:overdue_go_lives"
    assert done["confidence"]["score"] == 0.6 and done["confidence"]["signals"]["review"] == "fallback"
    assert not any(i.startswith("The planner read this as") for i in done["interpreted"])


async def test_review_off_makes_no_call(stack: httpx.AsyncClient, world: World,
                                        owner_conn: psycopg.Connection) -> None:
    review_on(owner_conn, world, on=False)
    fake = FakeGateway({"AG-03": reviewer({"verdict": "correct", "replacement": {"measure": "measure:open_projects"}})})
    stack.app.state.gateway = fake.client()  # type: ignore[attr-defined]
    _, events = await ask(stack, world.tokens["creator"], LATE)
    done = by_event(events)["complete"]
    assert reviews(fake) == [] and "review" not in done["confidence"]["signals"]
    assert done["confidence"]["score"] == 0.6
