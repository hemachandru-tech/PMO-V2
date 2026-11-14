"""Free-hand questions in Ask (ENH-008 US-164 / US-165; FEAT-060 / FEAT-018).

With a live ``ask.freehand`` grant Ask turns the words into a rule over the model's fields (approved glossary
terms read first), runs it in the person's scope and labels it not certified, with the rule in words and one chip
per condition; the problem question keeps the certified *Past planning* and shows the rule beside it (D1).
Without a grant the refusal offers to request access. A chip edit reruns the changed rule.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pmo_oracle as oracle
import psycopg
import pytest
from api_testkit import needs_db
from pmo_testkit import World, h, setup_world
from test_ask_api import ask, by_event, parse_sse

pytestmark = [needs_db, pytest.mark.integration]

GRANTS = "/api/v1/admin/permission-grants"
STAGES = ["planning_stage_end_date", "design_stage_end_date", "build_stage_end_date", "test_stage_end_date",
          "implementation_stage_end_date", "warranty_stage_end_date"]
PROBLEM = ("How many projects (any state) are past the Plan stage, meaning projects that have at least one stage "
           "planned end date and whose S2 plan planned end date is blank or before today?")
RULE_Q = "How many projects without a budget code and whose planned deployment date is before today?"


@pytest.fixture
async def world(stack: httpx.AsyncClient, owner_conn: psycopg.Connection, make_token: Callable[..., str],
                tpg_source: dict[str, Any]) -> World:
    w = await setup_world(stack, owner_conn, make_token, tpg_source)
    me = await stack.get("/api/v1/me", headers=h(w.tokens["nomsa"]))
    w.extra["nomsa_id"] = me.json()["user_id"]
    # An approved glossary (FEAT-016): "S2" is the planning stage.
    doc = owner_conn.execute(
        "INSERT INTO rag.knowledge_documents (id, tenant_id, connection_id, source_kind, title, content_hash, "
        "review_status, index_status) VALUES (gen_random_uuid(), %s, %s, 'glossary', 'Glossary', 'g', 'approved', "
        "'indexed') RETURNING id", (w.tenant_id, w.connection_id)).fetchone()
    assert doc is not None
    owner_conn.execute("INSERT INTO rag.knowledge_chunks (id, tenant_id, document_id, chunk_no, content) VALUES "
                       "(gen_random_uuid(), %s, %s, 0, 'S2 means: planning stage. The Plan stage.')",
                       (w.tenant_id, doc[0]))
    return w


async def _grant(stack: httpx.AsyncClient, w: World) -> None:
    resp = await stack.post(GRANTS, headers=h(w.tokens["admin"]), json={
        "user_id": w.extra["nomsa_id"], "connection_id": w.connection_id, "reason": "Plan stage review",
        "expires_at": (datetime.now(UTC) + timedelta(days=30)).isoformat()})
    assert resp.status_code == 201, resp.text


def _mine(p: dict[str, Any]) -> bool:
    return bool(p["cio"] == "Nomsa Osei")


def _today() -> Any:
    return datetime.now(UTC).date()  # test users have no time zone


async def test_no_grant_refuses_and_offers_to_request_access(stack: httpx.AsyncClient, world: World) -> None:
    _, events = await ask(stack, world.tokens["nomsa"], PROBLEM)
    err = by_event(events)["error"]
    assert err["code"] == "unsupported_question"
    assert err["freehand_access"] == {"freehand": "can_request", "connection_id": world.connection_id,
                                      "connection": err["freehand_access"]["connection"]}
    assert err["examples"]  # the closest certified measures (US-167)


async def test_problem_question_certified_with_the_rule_beside_it(stack: httpx.AsyncClient, world: World) -> None:
    await _grant(stack, world)
    _, events = await ask(stack, world.tokens["nomsa"], PROBLEM)
    ev = by_event(events)
    assert ev["plan"]["planner"] == "freehand" and ev["plan"]["certified"] is True
    done = ev["complete"]
    assert done["certified"] is True and done["spec"]["measure"]["ref"] == "measure:baselining_in_scope"
    side = done["beside"]
    assert side["kind"] == "freehand" and side["certified"] is False
    today = _today()
    expected = sum(1 for p in oracle.PROJECTS if _mine(p) and any(p[s] is not None for s in STAGES)
                   and (p["planning_stage_end_date"] is None or p["planning_stage_end_date"] < today))
    assert side["value"] == expected
    assert "Planning stage planned end is before today" in side["plain_rule"]
    assert any("S2 read as planning stage" in t for t in done["interpreted"])


async def test_free_hand_answer_is_labelled_and_chips_rerun_it(stack: httpx.AsyncClient, world: World,
                                                               owner_conn: psycopg.Connection) -> None:
    await _grant(stack, world)
    turn_id, events = await ask(stack, world.tokens["nomsa"], RULE_Q)
    done = by_event(events)["complete"]
    today = _today()
    expected = sum(1 for p in oracle.PROJECTS if _mine(p) and p["budget_code"] is None
                   and p["planned_deployment_date"] is not None and p["planned_deployment_date"] < today)
    assert done["certified"] is False and done["badge"] == "not_certified" and done["can_export"] is False
    assert done["rows"][0][0] == expected
    assert done["rule"]["plain_rule"] == ("Count of projects where Budget code is blank and Planned deployment date "
                                          "is before today.")
    chips = done["rule"]["chips"]
    assert [(c["path"], c["field"], c["op"]) for c in chips] == [("0", "budget_code", "is_null"),
                                                                 ("1", "planned_deployment_date", "<")]
    assert "Not certified · built from your question" in by_event(events)["narrative"]["sentences"][0]["text"]
    # Drill to records: the rule's own records, scoped as usual.
    drill = await stack.post("/api/v1/query/records", headers=h(world.tokens["nomsa"]),
                             json={"spec": done["spec"], "mark": {}})
    assert drill.status_code == 200, drill.text
    assert drill.json()["total"] == expected
    # Change one chip: budget code is set instead of blank; the rule reruns.
    resp = await stack.post(f"/api/v1/ask/turns/{turn_id}/refine", headers=h(world.tokens["nomsa"]), json={
        "chip_ops": [{"op": "set_condition", "path": "0", "field": "budget_code", "operator": "not_null"}]})
    assert resp.status_code == 202, resp.text
    stream = await stack.get(resp.json()["stream_url"], headers=h(world.tokens["nomsa"]))
    again = by_event(parse_sse(stream.text))["complete"]
    expected2 = sum(1 for p in oracle.PROJECTS if _mine(p) and p["budget_code"] is not None
                    and p["planned_deployment_date"] is not None and p["planned_deployment_date"] < today)
    assert again["rows"][0][0] == expected2 and again["certified"] is False
    assert "Budget code is not blank" in again["rule"]["plain_rule"]
    # A bad chip is refused with a message, nothing runs.
    bad = await stack.post(f"/api/v1/ask/turns/{turn_id}/refine", headers=h(world.tokens["nomsa"]), json={
        "chip_ops": [{"op": "set_condition", "path": "0", "field": "budget_code", "operator": "<"}]})
    assert bad.status_code == 400
    audits = owner_conn.execute("SELECT count(*) FROM ops.audit_events WHERE action = 'query.freehand' AND "
                                "outcome = 'allow'").fetchone()
    assert audits is not None and audits[0] >= 2
