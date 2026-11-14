"""Query rewriting and typed retrieval in Ask (US-175; FEAT-062 / FEAT-018).

Acceptance: a follow-up in a conversation is read against the previous answer ("what about KSA?", "same for forecast
cost"); a follow-up the rules can't read is rewritten by one small model call; AG-03 sees the conversation, field
profiles (in the person's scope, no person names) and the time context; clarified questions and approved answers
feed gold examples and value synonyms through the Admin review gate. Rules, quotas and MMR are unit-tested in
``test_query_rewrite_unit``; certified free-hand questions in ``test_freehand_certification_api``.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import httpx
import pmo_oracle as oracle
import psycopg
import pytest
from api_testkit import needs_db
from conftest import FakeGateway
from pmo_testkit import World, h, setup_world
from test_ask_api import ask, by_event, parse_sse
from test_value_index_api import refresh

pytestmark = [needs_db, pytest.mark.integration]


@pytest.fixture
async def world(stack: httpx.AsyncClient, owner_conn: psycopg.Connection, make_token: Callable[..., str],
                tpg_source: dict[str, Any]) -> World:
    return await setup_world(stack, owner_conn, make_token, tpg_source)


async def turn(api: httpx.AsyncClient, token: str, question: str, conversation: str | None = None
               ) -> tuple[str, dict[str, Any]]:
    body = {"conversation_id": conversation} if conversation else {}
    turn_id, events = await ask(api, token, question, **body)
    resp = await api.get(f"/api/v1/ask/turns/{turn_id}", headers=h(token))
    return resp.json()["conversation_id"], by_event(events)


async def test_follow_ups_read_the_previous_answer(stack: httpx.AsyncClient, world: World) -> None:
    token = world.tokens["admin"]
    conv, ev = await turn(stack, token, "Open projects by region")
    assert ev["retrieval"]["rewrite"] == {"question": "Open projects by region", "how": "as_asked", "parts": [],
                                          "glossary": []}

    _, ev = await turn(stack, token, "what about KSA?", conv)
    spec = ev["plan"]["spec"]
    assert spec["measure"] == {"ref": "measure:open_projects"} and spec["breakdowns"] == [{"ref": "dimension:region"}]
    assert spec["filters"] == [{"ref": "dimension:region", "op": "in", "values": ["KSA"]}]
    assert ev["retrieval"]["rewrite"]["how"] == "follow_up"
    assert ev["retrieval"]["rewrite"]["question"] == "Open projects by Region, Region: KSA"
    assert ev["complete"]["interpreted"] == ["Follow-up on “Open projects by region”", "Region: KSA"]
    assert ev["complete"]["rows"] == [["KSA", sum(1 for p in oracle.PROJECTS if p["region"] == "KSA"
                                                  and p["status"] == "Active")]]

    # A new measure keeps the breakdown and the filter.
    _, ev = await turn(stack, token, "same for forecast cost", conv)
    spec = ev["plan"]["spec"]
    assert spec["measure"] == {"ref": "measure:forecast_cost_total"}
    assert spec["breakdowns"] == [{"ref": "dimension:region"}] and spec["filters"][0]["values"] == ["KSA"]

    # A question that stands alone starts afresh, even in the same conversation.
    _, ev = await turn(stack, token, "How many projects are on hold by portfolio?", conv)
    assert ev["plan"]["spec"]["filters"] == [] and ev["plan"]["spec"]["measure"] == {"ref": "measure:on_hold"}
    assert not any(i.startswith("Follow-up") for i in ev["complete"]["interpreted"])

    # Without a conversation there is nothing to follow.
    _, ev = await turn(stack, token, "same for forecast cost")
    assert "Follow-up" not in json.dumps(ev)


async def test_a_follow_up_the_rules_cannot_read_is_rewritten_once(stack: httpx.AsyncClient, world: World) -> None:
    fake = FakeGateway({"AG-03": {"question": "Projects on hold by region"}})
    stack.app.state.gateway = fake.client()  # type: ignore[attr-defined]
    token = world.tokens["admin"]
    conv, _ = await turn(stack, token, "Projects by region")
    fake.calls.clear()
    _, ev = await turn(stack, token, "and the ones nobody is looking after", conv)
    assert ev["plan"]["planner"] == "rules"
    assert ev["plan"]["spec"]["measure"] == {"ref": "measure:on_hold"}
    assert ev["complete"]["interpreted"][0] == "Read as: Projects on hold by region"
    assert ev["retrieval"]["rewrite"]["how"] == "llm"
    calls = [c for c in fake.calls if c["agent"] != "embed"]
    assert [(c["agent"], c["task"]) for c in calls] == [("AG-03", "plan")]
    system = calls[0]["messages"][0]["content"]
    assert "standalone question" in system and 'asked="Projects by region" answered="Projects by Region"' in system


async def test_ag03_sees_the_conversation_profiles_and_time(stack: httpx.AsyncClient, world: World,
                                                            owner_conn: psycopg.Connection) -> None:
    # The plan answers the whole question: one leaving CIO out would be checked with the person (US-182).
    fake = FakeGateway({"AG-03": {"measure": "measure:schedule_risk", "breakdowns": ["dimension:region",
                                                                                  "dimension:cio"]}})
    stack.app.state.gateway = fake.client()  # type: ignore[attr-defined]
    token = world.tokens["creator"]  # Gulf regions only
    conv, _ = await turn(stack, token, "Open projects in KSA")
    fake.calls.clear()
    _, ev = await turn(stack, token, "where are we slipping by region and CIO", conv)
    assert ev["plan"]["planner"] == "llm" and ev["plan"]["spec"]["measure"] == {"ref": "measure:schedule_risk"}
    plan_calls = [c for c in fake.calls if c["agent"] == "AG-03"]
    system = plan_calls[-1]["messages"][0]["content"]
    assert 'asked="Open projects in KSA" answered="Open projects, Region: KSA"' in system
    assert 'reporting_date="' in system and 'fiscal_year_starts="January"' in system
    region = next(line for line in system.splitlines() if line.startswith('<field ref="dimension:region"'))
    assert 'filled="' in region and "KSA (" in region
    assert "Jordan" not in region  # values outside the person's scope never reach the model
    cio = next(line for line in system.splitlines() if line.startswith('<field ref="dimension:cio"'))
    assert "top=" not in cio  # a person-name field: type and fill rate only
    assert "Nomsa Osei" not in json.dumps(fake.calls) and "Karim Haddad" not in json.dumps(fake.calls)
    surfaces = {r[0] for r in owner_conn.execute("SELECT DISTINCT surface FROM ops.query_executions").fetchall()}
    assert "field_profile" in surfaces


async def test_a_clarified_question_is_suggested_as_a_gold_example(stack: httpx.AsyncClient, world: World,
                                                                   owner_conn: psycopg.Connection) -> None:
    token = world.tokens["admin"]
    turn_id, events = await ask(stack, token, "total cost by region")
    q = by_event(events)["clarify"]
    forecast = next(o for o in q["options"] if o["label"] == "Forecast cost (EAC, USD)")
    resumed = await stack.post(f"/api/v1/ask/turns/{turn_id}/clarification", headers=h(token),
                               json={"option_id": forecast["id"]})
    assert resumed.status_code == 202
    stream = await stack.get(f"/api/v1/ask/turns/{turn_id}/stream", headers=h(token, **{"Last-Event-ID": str(
        max(e[0] for e in events))}))
    assert parse_sse(stream.text)[-1][1] == "complete"
    rows = owner_conn.execute("SELECT id, question, expected_spec, status, origin, source_turn_id::text FROM "
                              "rag.gold_examples").fetchall()
    assert len(rows) == 1
    gid, question, spec, status, origin, source = rows[0]
    assert (question, status, origin, source) == ("total cost by region", "suggested", "clarification", turn_id)
    assert spec["measure"] == {"ref": "measure:forecast_cost_total"} and spec["breakdowns"] == [
        {"ref": "dimension:region"}]
    # Only an Admin's approval makes it a few-shot.
    review = await stack.post("/api/v1/knowledge/gold-examples/review", headers=h(token),
                              json={"ids": [str(gid)], "decision": "approve"})
    assert review.status_code == 200 and review.json()["updated"] == [str(gid)]
    # Asking the same again does not suggest it twice.
    turn_id, events = await ask(stack, token, "total cost by region")
    assert owner_conn.execute("SELECT count(*) FROM rag.gold_examples").fetchone() == (1,)


async def test_an_approved_answer_proposes_its_words_for_values(stack: httpx.AsyncClient, world: World,
                                                                owner_conn: psycopg.Connection,
                                                                signing_keys: dict[str, Any]) -> None:
    await refresh(stack, world, signing_keys)

    async def save(token: str, question: str) -> dict[str, Any]:
        turn_id, events = await ask(stack, token, question)
        spec = by_event(events)["plan"]["spec"]
        resp = await stack.post("/api/v1/knowledge/gold-examples", headers=h(token), json={
            "connection_id": world.connection_id, "question": question, "turn_id": turn_id,
            "expected_spec": {k: spec[k] for k in ("measure", "breakdowns", "filters")}})
        assert resp.status_code == 201, resp.text
        return {**resp.json(), "spec": spec}

    # An Admin's own answer is approved at once: the spelling slip becomes a synonym waiting for review. A spelling
    # link asks at "balanced" (US-217); "assume first" answers it with the note.
    resp = await stack.put("/api/v1/me/preferences", headers=h(world.tokens["admin"]),
                           json={"ask_verbosity": "assume_first"})
    assert resp.status_code == 200, resp.text
    mine = await save(world.tokens["admin"], "Open projects in Jordon")
    assert mine["status"] == "approved"
    assert mine["spec"]["filters"] == [{"ref": "dimension:region", "op": "=", "values": ["Jordan"]}]
    got = owner_conn.execute("SELECT dimension, stored_value, lower(synonym), status FROM rag.value_synonyms WHERE "
                             "source = 'answer'").fetchall()
    assert got == [("region", "Jordan", "jordon", "pending_review")]

    # A Creator's answer proposes nothing until approved; a synonym already waiting (the pack's "UAE") is not doubled.
    theirs = await save(world.tokens["creator"], "Open projects in UAE")
    assert theirs["status"] == "suggested"
    assert theirs["spec"]["filters"] == [{"ref": "dimension:region", "op": "=", "values": ["GCC - UAE"]}]
    review = await stack.post("/api/v1/knowledge/gold-examples/review", headers=h(world.tokens["admin"]),
                              json={"ids": [theirs["id"]], "decision": "approve"})
    assert review.status_code == 200 and review.json()["updated"] == [theirs["id"]]
    assert owner_conn.execute("SELECT count(*) FROM rag.value_synonyms WHERE dimension = 'region' AND normalised = "
                              "'uae'").fetchone() == (1,)
