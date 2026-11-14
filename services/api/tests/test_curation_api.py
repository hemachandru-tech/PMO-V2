"""Curation queue from Ask's misses (ENH-016, US-198; FEAT-066 / FEAT-014), live.

Acceptance: refused questions, clarification choices, repaired plans and down-votes become proposals (a measure
synonym, a glossary term or a gold example) with the questions behind them and counts; held-out questions never do;
nothing applies until an Admin approves it; an approved synonym is a new model version that Ask plans by rules, an
approved term is approved glossary knowledge, an approved gold example fixes the down-vote behind it.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx
import psycopg
import pytest
from api_testkit import needs_db
from conftest import FakeGateway
from pmo_testkit import World, _no_failures, h, setup_world
from test_ask_api import ask, by_event
from test_confidence_api import choose
from test_knowledge_api import bag
from test_plan_repair_api import scripted

pytestmark = [needs_db, pytest.mark.integration]
Q = "/api/v1/knowledge/curation"
HELD_OUT = "How much demand has come in overall?"  # a US-190 paraphrase (held out) the rules refuse
STALLED = "Show me stalled projects by region"
RFQ = "How many RFQs are pending?"
OUTLAY = "total outlay cost by region"
MISS = "zorbly count of the quantum widgets"  # names no measure: AG-03 plans, and is repaired


@pytest.fixture
async def world(stack: httpx.AsyncClient, owner_conn: psycopg.Connection, make_token: Callable[..., str],
                tpg_source: dict[str, Any]) -> World:
    w = await setup_world(stack, owner_conn, make_token, tpg_source)
    stack.app.state.gateway = FakeGateway(embedder=bag).client()  # type: ignore[attr-defined]
    return w


async def collect(api: httpx.AsyncClient, token: str, **body: Any) -> dict[str, Any]:
    resp = await api.post(f"{Q}/collect", headers=h(token), json=body)
    assert resp.status_code == 200, resp.text
    return resp.json()


async def queue(api: httpx.AsyncClient, token: str, **params: str) -> dict[str, Any]:
    resp = await api.get(Q, headers=h(token), params=params)
    assert resp.status_code == 200, resp.text
    return resp.json()


def one(items: list[dict[str, Any]], kind: str, question: str) -> dict[str, Any]:
    [p] = [p for p in items if p["kind"] == kind and p["question"] == question]
    return p


async def test_misses_become_proposals_and_wait_for_review(stack: httpx.AsyncClient, world: World,
                                                           owner_conn: psycopg.Connection) -> None:
    admin, creator = world.tokens["admin"], world.tokens["creator"]
    # Refused: unknown words, and an abbreviation.
    for q in (STALLED, RFQ, HELD_OUT):
        _, events = await ask(stack, creator, q)
        assert by_event(events)["error"]["code"] == "unsupported_question", q
    # A clarification choice: "cost" read as forecast cost, with a word the model doesn't know.
    turn_id, events = await ask(stack, creator, OUTLAY)
    forecast = next(o for o in by_event(events)["clarify"]["options"] if o["label"] == "Forecast cost (EAC, USD)")
    assert by_event(await choose(stack, creator, turn_id, forecast["id"], events))["complete"]
    # A repaired plan: AG-03 names a measure the model doesn't have, in the person's words, then the right one.
    stack.app.state.gateway = FakeGateway({"AG-03": scripted(  # type: ignore[attr-defined]
        {"measure": "measure:quantum_widgets"}, {"measure": "measure:on_hold"})}, embedder=bag).client()
    _, events = await ask(stack, creator, MISS)
    assert by_event(events)["plan"]["spec"]["measure"] == {"ref": "measure:on_hold"}
    stack.app.state.gateway = FakeGateway(embedder=bag).client()  # type: ignore[attr-defined]
    # A down-vote marked wrong, with a comment.
    voted, events = await ask(stack, creator, "How many projects are on hold?")
    down = await stack.put(f"/api/v1/ask/turns/{voted}/feedback", headers=h(creator),
                           json={"vote": "down", "reason": "wrong", "comment": "I meant paused ones"})
    assert down.status_code == 200, down.text

    # Admins only.
    assert (await stack.post(f"{Q}/collect", headers=h(creator), json={})).status_code == 403
    assert (await stack.get(Q, headers=h(creator))).status_code == 403
    got = await collect(stack, admin)
    assert got["read"] == {"refused": 2, "clarification": 1, "repair": 1, "feedback": 1}
    assert got["held_out_skipped"] == 1 and got["added"] == 6
    again = await collect(stack, admin)  # each miss is read once
    assert again == {"added": 0, "read": {"refused": 0, "clarification": 0, "repair": 0, "feedback": 0},
                     "held_out_skipped": 1}

    data = await queue(stack, admin)
    items = data["items"]
    assert data["counts"]["pending"] == 6 and data["counts"]["pending_by_kind"] == {
        "measure_synonym": 3, "glossary_term": 1, "gold_example": 2}
    assert data["counts"]["pending_by_source"] == {"refused": 2, "clarification": 1, "repair": 1, "feedback": 1,
                                                   "direct_sql": 0}
    assert all(p["question"] != HELD_OUT and HELD_OUT not in [x["question"] for x in p["questions"]] for p in items)
    stalled = one(items, "measure_synonym", STALLED)
    assert stalled["phrase"] == "stalled projects" and stalled["seen"] == 1 and stalled["by_source"] == {"refused": 1}
    assert stalled["questions"][0]["question"] == STALLED and stalled["questions"][0]["source"] == "refused"
    rfq = one(items, "glossary_term", RFQ)
    assert rfq["phrase"] == "RFQs" and rfq["meaning"] is None
    outlay = one(items, "measure_synonym", OUTLAY)  # the answered question is a suggested gold example already
    assert outlay["phrase"] == "outlay" and outlay["measure"]["ref"] == "measure:forecast_cost_total"
    assert outlay["questions"][0]["detail"] == "Chose Forecast cost (EAC, USD)"
    assert [p for p in items if p["kind"] == "gold_example" and p["question"] == OUTLAY] == []
    widgets = one(items, "measure_synonym", MISS)
    assert widgets["phrase"] == "quantum widgets" and widgets["measure"] == {"ref": "measure:on_hold",
                                                                             "label": "Projects on hold"}
    one(items, "gold_example", MISS)
    flagged = one(items, "gold_example", "How many projects are on hold?")
    assert flagged["needs_fix"] and flagged["questions"][0]["detail"] == "Marked wrong: “I meant paused ones”"

    # Nothing applied yet: no new model version, no glossary document, no gold example.
    versions = owner_conn.execute("SELECT count(*) FROM model.model_versions").fetchone()[0]
    assert owner_conn.execute("SELECT count(*) FROM rag.gold_examples WHERE status = 'approved'").fetchone()[0] == 0
    assert owner_conn.execute("SELECT count(*) FROM rag.knowledge_documents WHERE title = 'Glossary from Ask "
                              "misses'").fetchone()[0] == 0

    # Synonym: the Admin picks the measure; it becomes the model's next version, and Ask plans the question by rules.
    no_measure = await stack.post(f"{Q}/{stalled['id']}/approve", headers=h(admin), json={})
    assert no_measure.status_code == 400 and "measure" in no_measure.text
    service = stack.app.state.query_service  # type: ignore[attr-defined]
    service.dry_run = _no_failures
    try:
        ok = await stack.post(f"{Q}/{stalled['id']}/approve", headers=h(admin),
                              json={"measure": "measure:on_hold", "note": "Stalled means on hold here."})
    finally:
        del service.dry_run
    assert ok.status_code == 200, ok.text
    assert ok.json()["status"] == "approved" and ok.json()["applied"]["phrase"] == "stalled projects"
    assert ok.json()["applied"]["version"] == 2 and ok.json()["reviewed_by"]["display_name"] == "Ada Admin"
    assert owner_conn.execute("SELECT count(*) FROM model.model_versions").fetchone()[0] == versions + 1
    _, events = await ask(stack, creator, STALLED)
    plan = by_event(events)["plan"]
    assert plan["planner"] == "rules" and plan["spec"]["measure"] == {"ref": "measure:on_hold"}
    assert plan["spec"]["breakdowns"] == [{"ref": "dimension:region"}]
    assert (await stack.post(f"{Q}/{stalled['id']}/approve", headers=h(admin), json={})).status_code == 409

    # Glossary term: the Admin writes what it means; it is approved glossary knowledge of the connection.
    assert (await stack.post(f"{Q}/{rfq['id']}/approve", headers=h(admin), json={})).status_code == 400
    term = await stack.post(f"{Q}/{rfq['id']}/approve", headers=h(admin),
                            json={"phrase": "RFQ", "meaning": "request for quotation"})
    assert term.status_code == 200, term.text
    doc = owner_conn.execute("SELECT d.review_status, d.connection_id::text, k.content FROM rag.knowledge_documents d "
                             "JOIN rag.knowledge_chunks k ON k.document_id = d.id WHERE d.id = %s",
                             (term.json()["applied"]["document_id"],)).fetchall()
    assert doc == [("approved", world.connection_id, "RFQ means: request for quotation.")]

    # Gold example from a down-vote: the spec must be corrected first; approving it fixes the down-vote.
    bad = await stack.post(f"{Q}/{flagged['id']}/approve", headers=h(admin), json={})
    assert bad.status_code == 400 and "correct the spec" in bad.text
    fixed = await stack.post(f"{Q}/{flagged['id']}/approve", headers=h(admin), json={
        "expected_spec": {"measure": {"ref": "measure:on_hold"}, "breakdowns": [{"ref": "dimension:hold_reason"}]}})
    assert fixed.status_code == 200, fixed.text
    gold_id = fixed.json()["applied"]["gold_example_id"]
    g = owner_conn.execute("SELECT status, tags, source_turn_id::text FROM rag.gold_examples WHERE id = %s",
                           (gold_id,)).fetchone()
    assert g == ("approved", ["from_misses"], voted)
    fb = owner_conn.execute("SELECT status, gold_example_id::text FROM chat.answer_feedback WHERE turn_id = %s",
                            (voted,)).fetchone()
    assert fb == ("fixed", gold_id)

    # Dismissed: not proposed again, and counted.
    gone = await stack.post(f"{Q}/{widgets['id']}/dismiss", headers=h(admin), json={"note": "AG-03's own words"})
    assert gone.status_code == 200 and gone.json()["status"] == "dismissed"
    assert (await stack.post(f"{Q}/{widgets['id']}/dismiss", headers=h(admin), json={})).status_code == 409
    counts = (await queue(stack, admin, status="all"))["counts"]
    assert (counts["pending"], counts["approved"], counts["dismissed"]) == (2, 3, 1)  # outlay and the repair's
    audit = owner_conn.execute("SELECT action, count(*) FROM ops.audit_events WHERE action LIKE "
                               "'knowledge.curation_%%' GROUP BY action ORDER BY action").fetchall()
    assert audit == [("knowledge.curation_approved", 3), ("knowledge.curation_dismissed", 1)]


async def test_a_held_out_question_never_becomes_a_gold_example(stack: httpx.AsyncClient, world: World) -> None:
    admin = world.tokens["admin"]
    _, events = await ask(stack, admin, "Open projects by region")
    assert by_event(events)["complete"]
    turn_id = by_event(events)["complete"]["turn_id"]
    await stack.put(f"/api/v1/ask/turns/{turn_id}/feedback", headers=h(admin), json={"vote": "down",
                                                                                     "reason": "not_what_i_asked"})
    await collect(stack, admin)
    [p] = (await queue(stack, admin))["items"]
    # Edited into a held-out question, it is refused.
    resp = await stack.post(f"{Q}/{p['id']}/approve", headers=h(admin), json={
        "question": HELD_OUT, "expected_spec": {"measure": {"ref": "measure:total_requests"}}})
    assert resp.status_code == 400 and "held-out" in resp.text
