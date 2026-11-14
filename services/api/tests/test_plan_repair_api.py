"""Schema linking and spec repair in Ask (US-181; FEAT-064), with a scripted AG-03 (FakeGateway).

Acceptance: AG-03 plans from the linked candidates, not the catalogue, and picks candidates itself only on a miss; a
plan the checks refuse goes back with typed errors (closest values from the person's own scope) at most twice; every
repair reason is logged and the top reasons are listed for an Admin. Linking per gold question and the typed errors
themselves are unit-tested in ``test_schema_linking_unit``.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx
import psycopg
import pytest
from api_testkit import needs_db
from conftest import FakeGateway
from pmo_testkit import World, h, setup_world
from test_ask_api import ask, by_event

pytestmark = [needs_db, pytest.mark.integration]

MISS = "zorbly glimps of the quantum widgets"  # names no measure: the rules find nothing, AG-03 plans


@pytest.fixture
async def world(stack: httpx.AsyncClient, owner_conn: psycopg.Connection, make_token: Callable[..., str],
                tpg_source: dict[str, Any]) -> World:
    return await setup_world(stack, owner_conn, make_token, tpg_source)


def scripted(*plans: dict[str, Any], pick: dict[str, Any] | None = None) -> Callable[[str, list[Any]], Any]:
    """AG-03: the candidate pick on a miss, then one plan per prompt (the last one repeated): the voted samples of the
    first round (US-182) share a prompt and get the same plan; each repair is a new prompt."""
    queue = list(plans)
    last: dict[str, Any] = {}

    def reply(task: str, messages: list[dict[str, Any]]) -> Any:
        if "choosing candidates" in messages[0]["content"]:
            return pick or {}
        prompt = messages[-1]["content"]
        if last.get("prompt") != prompt:
            last.update(prompt=prompt, plan=queue.pop(0) if len(queue) > 1 else queue[0])
        return last["plan"]
    return reply


def prompts(fake: FakeGateway) -> list[dict[str, Any]]:
    """One call per round: the voted first round (sampled) counts once, then each repair (not sampled)."""
    calls = plan_calls(fake)
    return calls[:1] + [c for c in calls[1:] if c["temperature"] is None]


def plan_calls(fake: FakeGateway) -> list[dict[str, Any]]:
    return [c for c in fake.calls if c["agent"] == "AG-03" and "choosing candidates" not in c["messages"][0]["content"]]


def repairs(owner_conn: psycopg.Connection) -> list[tuple[Any, ...]]:
    return owner_conn.execute("SELECT attempt, kind, ref, detail, repaired, turn_id::text FROM rag.plan_repairs "
                              "ORDER BY created_at, attempt").fetchall()


async def test_ag03_plans_from_linked_candidates(stack: httpx.AsyncClient, world: World) -> None:
    fake = FakeGateway({"AG-03": scripted({"measure": "measure:on_hold", "breakdowns": ["dimension:hold_reason"]},
                                          pick={"measures": ["measure:on_hold"],
                                                "dimensions": ["dimension:hold_reason"]})})
    stack.app.state.gateway = fake.client()  # type: ignore[attr-defined]
    _, events = await ask(stack, world.tokens["admin"], MISS)
    ev = by_event(events)
    assert ev["plan"]["planner"] == "llm" and ev["plan"]["spec"]["measure"] == {"ref": "measure:on_hold"}
    assert "complete" in ev
    cand = ev["retrieval"]["candidates"]
    assert cand["miss"] is True and cand["by"] == "rules"  # the event shows the deterministic pass
    picks = [c for c in fake.calls if c["agent"] == "AG-03" and "choosing candidates" in c["messages"][0]["content"]]
    assert len(picks) == 1 and "measure:hours_logged — Hours logged" in picks[0]["messages"][0]["content"]
    system = plan_calls(fake)[0]["messages"][0]["content"]
    assert "Candidates follow" in system and "- measure:on_hold — Projects on hold" in system
    assert system.count("\n- measure:") <= 5 and "measure:hours_logged" not in system  # not the whole catalogue
    assert ev["plan"]["interpreted"] == []

    # A question that names its measure needs no pick: the rules answer, or AG-03 plans from the linked candidates.
    fake.calls.clear()
    _, events = await ask(stack, world.tokens["admin"], "Open projects by region")
    ev = by_event(events)
    assert ev["retrieval"]["candidates"]["measures"][0]["ref"] == "measure:open_projects"
    assert not [c for c in fake.calls if c["agent"] == "AG-03"]


async def test_a_refused_value_is_repaired_from_the_persons_own_values(stack: httpx.AsyncClient, world: World,
                                                                      owner_conn: psycopg.Connection) -> None:
    good = {"measure": "measure:open_projects", "filters": [{"ref": "dimension:region", "values": ["KSA"]}]}
    fake = FakeGateway({"AG-03": scripted(
        {"measure": "measure:open_projects", "filters": [{"ref": "dimension:region", "values": ["KSAA"]}]}, good)})
    stack.app.state.gateway = fake.client()  # type: ignore[attr-defined]
    question = f"{MISS} in KSA"  # the question says the value the repaired plan filters on (US-182 back-check)
    turn_id, events = await ask(stack, world.tokens["creator"], question)  # Gulf regions only
    ev = by_event(events)
    assert ev["plan"]["spec"]["filters"] == [{"ref": "dimension:region", "op": "in", "values": ["KSA"]}]
    assert ev["plan"]["interpreted"] == ["Plan corrected: value 'KSAA' not found in dimension:region; closest: 'KSA'"]
    assert "complete" in ev
    calls = prompts(fake)
    assert len(calls) == 2 and len(plan_calls(fake)) == 4  # three voted samples, then one repair
    retry = calls[1]["messages"][-1]["content"]
    assert retry.startswith(question) and "Your previous plan was refused" in retry and '"KSAA"' in retry
    assert "- value 'KSAA' not found in dimension:region; closest: 'KSA'" in retry
    assert repairs(owner_conn) == [(1, "value_not_found", "dimension:region", "value not found in dimension:region",
                                    True, turn_id)]


async def test_repairs_stop_after_two_and_never_offer_values_outside_scope(
        stack: httpx.AsyncClient, world: World, owner_conn: psycopg.Connection) -> None:
    fake = FakeGateway({"AG-03": scripted(
        {"measure": "measure:open_projects", "filters": [{"ref": "dimension:region", "values": ["Jordan"]}]})})
    stack.app.state.gateway = fake.client()  # type: ignore[attr-defined]
    turn_id, events = await ask(stack, world.tokens["creator"], MISS)
    ev = by_event(events)
    assert ev["error"]["code"] == "unsupported_question"
    assert ev["error"]["message"] == "No Region called Jordan in your data."
    calls = prompts(fake)
    assert len(calls) == 3  # the plan (voted) and two repairs
    sent = " ".join(c["messages"][-1]["content"] for c in calls)
    assert "closest:" not in sent and "Francophone" not in sent and "East Africa" not in sent
    rows = repairs(owner_conn)
    assert [(r[0], r[1], r[4]) for r in rows] == [(1, "value_not_found", False), (2, "value_not_found", False),
                                                  (3, "value_not_found", False)]
    assert {r[5] for r in rows} == {turn_id}


async def test_unknown_references_are_repaired_and_reasons_listed(stack: httpx.AsyncClient, world: World,
                                                                  owner_conn: psycopg.Connection) -> None:
    fake = FakeGateway({"AG-03": scripted(
        {"measure": "measure:open_projectz", "breakdowns": ["dimension:regions"]},
        {"measure": "measure:open_projects", "breakdowns": ["dimension:regions"]},
        {"measure": "measure:open_projects", "breakdowns": ["dimension:region"]})})
    stack.app.state.gateway = fake.client()  # type: ignore[attr-defined]
    admin = world.tokens["admin"]
    _, events = await ask(stack, admin, MISS)
    ev = by_event(events)
    assert ev["plan"]["spec"]["breakdowns"] == [{"ref": "dimension:region"}] and "complete" in ev
    first = prompts(fake)[1]["messages"][-1]["content"]
    assert "- measure:open_projectz is not a certified measure you can use; closest: measure:open_projects" in first
    assert "- dimension:regions is not a dimension you can use; closest: dimension:region" in first
    assert [(r[0], r[1], r[4]) for r in repairs(owner_conn)] == [
        (1, "unknown_measure", True), (1, "unknown_dimension", True), (2, "unknown_dimension", True)]

    resp = await stack.get("/api/v1/knowledge/plan-repairs", headers=h(admin),
                           params={"connection_id": world.connection_id})
    assert resp.status_code == 200
    body = resp.json()
    assert body["reasons"][0]["ref"] == "dimension:regions" and body["reasons"][0]["n"] == 2
    assert body["reasons"][0]["repaired"] == 2
    assert {k["kind"]: k["n"] for k in body["by_kind"]} == {"unknown_dimension": 2, "unknown_measure": 1}
    denied = await stack.get("/api/v1/knowledge/plan-repairs", headers=h(world.tokens["creator"]),
                             params={"connection_id": world.connection_id})
    assert denied.status_code == 403
