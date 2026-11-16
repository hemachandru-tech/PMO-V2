"""AG-01 reviews the rules' intent and source when LLM review is on (ENH-026, US-242; DEC-28), with a scripted AG-01.

Acceptance: a confirm keeps the route; a different source asks which one with both; a "data" question the LLM reads
as a definition asks which was meant, and each choice is answered; a write refusal is never sent for review; review
off makes no AG-01 call.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import psycopg
import pytest
from api_testkit import needs_db
from conftest import FakeGateway
from pmo_testkit import World
from test_ask_api import ask, by_event, world  # noqa: F401 - the TPG world
from test_ask_review_api import review_on
from test_confidence_api import choose
from test_routing_api import second_source

pytestmark = [needs_db, pytest.mark.integration]

OVERDUE = "How many overdue go-lives are there?"


def router(verdict: dict[str, Any]) -> FakeGateway:
    """AG-01 replies ``verdict`` to its review; every other agent gets nothing (its review falls back)."""
    return FakeGateway({"AG-01": verdict})


def route_calls(fake: FakeGateway) -> list[dict[str, Any]]:
    """AG-01's routing calls; its question frame (US-253, task ``frame``, shadow mode) is not routing."""
    return [c for c in fake.calls if c["agent"] == "AG-01" and c["task"] != "frame"]


async def test_a_confirmed_route_is_kept(stack: httpx.AsyncClient, world: World,
                                         owner_conn: psycopg.Connection) -> None:
    review_on(owner_conn, world)
    fake = router({"verdict": "confirm", "reason": "a count of overdue go-lives"})
    stack.app.state.gateway = fake.client()  # type: ignore[attr-defined]
    _, events = await ask(stack, world.tokens["creator"], OVERDUE)
    ev = by_event(events)
    assert ev["intent"]["review"] == "confirm" and ev["intent"]["routing"] == "only" and "complete" in ev
    sent = route_calls(fake)
    assert len(sent) == 1 and sent[0]["task"] == "intent"
    body = json.loads(sent[0]["messages"][1]["content"])
    assert body["draft"] == {"intent": "data"}
    [src] = body["context"]["sources"]
    assert src["connection_id"] == world.connection_id and "Overdue go-lives" in src["measures"]


async def test_a_data_question_read_as_a_definition_is_asked(stack: httpx.AsyncClient, world: World,
                                                            owner_conn: psycopg.Connection) -> None:
    review_on(owner_conn, world)
    fake = router({"verdict": "correct", "replacement": {"intent": "definition", "subject": "overdue go-lives"},
                   "reason": "it may ask what overdue go-lives are"})
    stack.app.state.gateway = fake.client()  # type: ignore[attr-defined]
    creator = world.tokens["creator"]
    turn_id, events = await ask(stack, creator, OVERDUE)
    ev = by_event(events)
    assert [e[1] for e in events][-1] == "clarify" and "plan" not in ev
    q = ev["clarify"]
    assert q["kind"] == "intent" and [o["label"] for o in q["options"]] == [
        "Show the figures", "Explain what overdue go-lives means"]
    assert q["why"] == ["it may ask what overdue go-lives are"]

    done = by_event(await choose(stack, creator, turn_id, "b", events))["complete"]
    assert done["definition"]["ref"] == "measure:overdue_go_lives"
    assert len(route_calls(fake)) == 1  # the resumed turn isn't routed again

    turn_id, events = await ask(stack, creator, OVERDUE)
    ev = by_event(await choose(stack, creator, turn_id, "a", events))
    assert ev["plan"]["spec"]["measure"]["ref"] == "measure:overdue_go_lives" and "complete" in ev
    memory = owner_conn.execute("SELECT memory FROM chat.conversations c JOIN chat.turns t ON t.conversation_id = "
                                "c.id WHERE t.id = %s", (turn_id,)).fetchone()
    assert memory is not None and "intent" not in (memory[0] or {})


async def test_another_intent_leaves_the_rules(stack: httpx.AsyncClient, world: World,
                                               owner_conn: psycopg.Connection) -> None:
    review_on(owner_conn, world)
    fake = router({"verdict": "correct", "replacement": {"intent": "field"}, "reason": "a grouping"})
    stack.app.state.gateway = fake.client()  # type: ignore[attr-defined]
    _, events = await ask(stack, world.tokens["creator"], OVERDUE)
    assert "complete" in by_event(events)  # only definition and build are asked about

    fake = router({"verdict": "correct", "replacement": {"intent": "definition", "subject": "budget overruns"}})
    stack.app.state.gateway = fake.client()  # type: ignore[attr-defined]
    _, events = await ask(stack, world.tokens["creator"], OVERDUE)
    ev = by_event(events)
    assert "complete" in ev and ev["intent"]["review"] == "fallback"  # a term the question doesn't hold


async def test_a_write_refusal_is_never_overturned(stack: httpx.AsyncClient, world: World,
                                                   owner_conn: psycopg.Connection) -> None:
    review_on(owner_conn, world)
    fake = router({"verdict": "correct", "replacement": {"intent": "data"}, "reason": "counts projects"})
    stack.app.state.gateway = fake.client()  # type: ignore[attr-defined]
    _, events = await ask(stack, world.tokens["creator"], "delete all projects")
    assert by_event(events)["error"]["code"] == "write_refused"
    assert route_calls(fake) == []


async def test_review_off_makes_no_route_call(stack: httpx.AsyncClient, world: World,
                                              owner_conn: psycopg.Connection) -> None:
    review_on(owner_conn, world, on=False)
    fake = router({"verdict": "correct", "replacement": {"intent": "definition"}})
    stack.app.state.gateway = fake.client()  # type: ignore[attr-defined]
    _, events = await ask(stack, world.tokens["creator"], OVERDUE)
    ev = by_event(events)
    assert "complete" in ev and "review" not in ev["intent"] and route_calls(fake) == []


async def test_a_different_source_is_asked(stack: httpx.AsyncClient, world: World,
                                           owner_conn: psycopg.Connection, tpg_source: dict[str, Any]) -> None:
    admin = world.tokens["admin"]
    replica = await second_source(stack, world, tpg_source)
    review_on(owner_conn, world)
    question = "How many open projects in the replica?"

    fake = router({"verdict": "correct", "replacement": {"intent": "data", "connection_id": replica}})
    stack.app.state.gateway = fake.client()  # type: ignore[attr-defined]
    _, events = await ask(stack, admin, question)
    ev = by_event(events)
    assert ev["intent"]["connection_id"] == replica and ev["intent"]["routing"] == "named"  # same source: kept
    draft = json.loads(route_calls(fake)[0]["messages"][1]["content"])["draft"]
    assert draft["connection_id"] == replica and draft["routing"] == "named"

    fake = router({"verdict": "correct", "replacement": {"intent": "data", "connection_id": world.connection_id},
                   "reason": "the replica is a copy; the main source is meant"})
    stack.app.state.gateway = fake.client()  # type: ignore[attr-defined]
    _, events = await ask(stack, admin, question)
    err = by_event(events)["error"]
    assert err["code"] == "choose_source"
    assert [c["connection_id"] for c in err["choices"]] == [replica, world.connection_id]

    fake = router({"verdict": "correct", "replacement": {"intent": "data", "connection_id": "not-a-source"}})
    stack.app.state.gateway = fake.client()  # type: ignore[attr-defined]
    _, events = await ask(stack, admin, question)
    assert by_event(events)["intent"]["connection_id"] == replica  # an unknown source falls back
