"""Free-hand answer → widget → Admin approval → certified widget and measure (ENH-008 US-166; FEAT-060 / EPIC-08).

A Creator with free-hand access adds a free-hand answer as a widget: it waits in **Pending certification** in their
library only (no report, share or other reader) and a certification request is raised. The Admin's queue shows the
question, the rule, the number and a sample of records, the compiled query and look-alikes. Approve (never under a
certified measure's name, D1) adds a certified measure through a model version, keeps the question as a synonym and
certifies the widget (D2); Reject and Ask for changes keep it out of reports. Every decision is notified and audited.
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
from pmo_testkit import World, h, run, setup_world, value
from test_ask_api import ask, by_event

pytestmark = [needs_db, pytest.mark.integration]

GRANTS = "/api/v1/admin/permission-grants"
RULE_Q = "How many projects without a budget code and whose planned deployment date is before today?"
OTHER_Q = "How many projects whose closure date is blank and percent complete is more than 90?"
NEW = {"key": "no_code_past_deployment", "label": "Projects without a budget code past deployment",
       "definition": "Projects with no budget code whose planned deployment date has passed.",
       "plain_rule": "Projects with a blank budget code and a planned deployment date before today."}


@pytest.fixture
async def world(stack: httpx.AsyncClient, owner_conn: psycopg.Connection, make_token: Callable[..., str],
                tpg_source: dict[str, Any]) -> World:
    w = await setup_world(stack, owner_conn, make_token, tpg_source)
    for persona in ("creator", "admin"):
        w.extra[f"{persona}_id"] = (await stack.get("/api/v1/me", headers=h(w.tokens[persona]))).json()["user_id"]
    resp = await stack.post(GRANTS, headers=h(w.tokens["admin"]), json={
        "user_id": w.extra["creator_id"], "connection_id": w.connection_id, "reason": "Free-hand pilot",
        "expires_at": (datetime.now(UTC) + timedelta(days=30)).isoformat()})
    assert resp.status_code == 201, resp.text
    return w


async def _pin(stack: httpx.AsyncClient, w: World, question: str, **body: Any) -> httpx.Response:
    turn_id, events = await ask(stack, w.tokens["creator"], question)
    assert by_event(events)["complete"]["certified"] is False
    return await stack.post("/api/v1/widgets", headers=h(w.tokens["creator"]), json={
        "from_turn_id": turn_id, "title": question[:60], "visual": "figure", **body})


def _rule(p: dict[str, Any]) -> bool:
    today = datetime.now(UTC).date()
    return p["budget_code"] is None and p["planned_deployment_date"] is not None \
        and p["planned_deployment_date"] < today


async def _request_for(stack: httpx.AsyncClient, w: World, widget: dict[str, Any]) -> dict[str, Any]:
    queue = (await stack.get("/api/v1/certification/queue", headers=h(w.tokens["admin"]))).json()["items"]
    return next(i for i in queue if i["request_id"] == widget["certification_request_id"])


async def test_pending_widget_is_held_then_approved_as_a_certified_measure(
        stack: httpx.AsyncClient, world: World, owner_conn: psycopg.Connection) -> None:
    creator, admin = world.tokens["creator"], world.tokens["admin"]
    # Never straight onto a report.
    report = (await stack.post("/api/v1/reports", headers=h(creator), json={"title": "Pilot"})).json()
    onto = await _pin(stack, world, RULE_Q, target={"report_id": report["id"]})
    assert onto.status_code == 409 and "library" in onto.json()["error"]["message"]

    resp = await _pin(stack, world, RULE_Q)
    assert resp.status_code == 201, resp.text
    widget = resp.json()
    assert widget["badges"] == ["pending_certification"] and widget["certification_request_id"]
    wid = widget["id"]
    # Held: not on a report, not shared, nobody else can open it.
    placed = await stack.post(f"/api/v1/reports/{report['id']}/items", headers=h(creator),
                              json={"kind": "widget", "widget_id": wid})
    assert placed.status_code == 409
    shared = await stack.post("/api/v1/shares", headers=h(creator), json={
        "object": {"kind": "widget", "id": wid}, "with_user_ids": [world.extra["admin_id"]], "permission": "view"})
    assert shared.status_code == 409
    assert (await stack.get(f"/api/v1/widgets/{wid}", headers=h(admin))).status_code == 404
    # The Fields page lists fields, not held rules.
    assert all(f["kind"] != "freehand" for f in (await stack.get("/api/v1/fields", headers=h(creator))).json()["items"])

    item = await _request_for(stack, world, widget)
    assert item["kind"] == "freehand" and item["question"] == RULE_Q
    detail = (await stack.get(f"/api/v1/certification/requests/{item['request_id']}", headers=h(admin))).json()
    assert detail["question"] == RULE_Q and detail["decisions"] == ["certify", "request_changes", "reject"]
    assert detail["rule"]["plain"] == ("Count of projects where Budget code is blank and Planned deployment date is "
                                       "before today.")
    expected_all = sum(1 for p in oracle.PROJECTS if _rule(p))  # the Admin's scope: all rows
    assert detail["results"]["rows"][0][0] == expected_all
    assert detail["results"]["sample"]["total"] == expected_all and len(detail["results"]["sample"]["rows"]) <= 10
    assert "budget_code" in (detail["generated"] or "").lower()
    assert any(f["ref"] == "measure:budget_code_set" for f in detail["lookalikes"])  # "budget code": similar name

    # D1: never under a certified measure's name or key.
    taken = await stack.post(f"/api/v1/certification/requests/{item['request_id']}/decision", headers=h(admin),
                             json={"decision": "certify", "measure": {**NEW, "label": "With a budget code"}})
    assert taken.status_code == 400 and "already has this name" in taken.json()["error"]["message"]
    assert (await stack.post(f"/api/v1/certification/requests/{item['request_id']}/decision", headers=h(admin),
                             json={"decision": "certify"})).status_code == 400  # name it first
    # Approve (D2): a certified measure through a new model version; the widget is certified on it.
    done = await stack.post(f"/api/v1/certification/requests/{item['request_id']}/decision", headers=h(admin),
                            json={"decision": "certify", "measure": NEW})
    assert done.status_code == 201, done.text
    out = done.json()
    assert out["status"] == "certified" and out["measure"] == "measure:no_code_past_deployment"
    w = (await stack.get(f"/api/v1/widgets/{wid}", headers=h(creator))).json()
    assert w["badges"] == ["certified"] and w["spec"]["measure"] == {"ref": "measure:no_code_past_deployment"}
    assert w["model_version_id"] == out["model_version_id"]
    catalog = (await stack.get(f"/api/v1/query/catalog?connection_id={world.connection_id}", headers=h(admin))).json()
    assert "measures" in catalog, catalog
    m = next(x for x in catalog["measures"] if x["ref"] == "measure:no_code_past_deployment")
    assert m["synonyms"] == [RULE_Q.rstrip("?")]
    # Everyone's Ask can use it now, without free-hand access; numbers stay in each reader's scope.
    karim = await run(stack, world.tokens["karim"], {"model_version_id": out["model_version_id"],
                                                     "connection_id": world.connection_id,
                                                     "measure": {"ref": "measure:no_code_past_deployment"}})
    assert karim.json()["certified"] is True
    assert value(karim) == sum(1 for p in oracle.PROJECTS if _rule(p) and p["cio"] == "Karim Haddad")
    # The certified widget can go on a report now.
    placed = await stack.post(f"/api/v1/reports/{report['id']}/items", headers=h(creator),
                              json={"kind": "widget", "widget_id": wid})
    assert placed.status_code == 201, placed.text
    note = owner_conn.execute("SELECT title FROM ops.notifications WHERE user_id = %s AND kind = "
                              "'certification.certified'", (world.extra["creator_id"],)).fetchone()
    assert note is not None and "Projects without a budget code past deployment" in note[0]
    audit_row = owner_conn.execute("SELECT details FROM ops.audit_events WHERE action = 'certification.decided' "
                                   "ORDER BY occurred_at DESC LIMIT 1").fetchone()
    assert audit_row is not None and audit_row[0]["measure"] == "measure:no_code_past_deployment"
    # US-175: the question becomes a suggested gold example on the new measure, waiting for review.
    gold = owner_conn.execute("SELECT question, expected_spec->'measure'->>'ref', status, origin FROM "
                              "rag.gold_examples WHERE origin = 'freehand'").fetchall()
    assert gold == [(RULE_Q, "measure:no_code_past_deployment", "suggested", "freehand")]


async def test_reject_and_ask_for_changes_keep_it_out_of_reports(stack: httpx.AsyncClient, world: World,
                                                                 owner_conn: psycopg.Connection) -> None:
    admin = world.tokens["admin"]
    first = (await _pin(stack, world, RULE_Q)).json()
    rid = first["certification_request_id"]
    no_note = await stack.post(f"/api/v1/certification/requests/{rid}/decision", headers=h(admin),
                               json={"decision": "request_changes"})
    assert no_note.status_code == 400
    changes = await stack.post(f"/api/v1/certification/requests/{rid}/decision", headers=h(admin),
                               json={"decision": "request_changes", "note": "Count blank planned dates too."})
    assert changes.status_code == 201 and changes.json()["status"] == "changes_requested"
    w = (await stack.get(f"/api/v1/widgets/{first['id']}", headers=h(world.tokens["creator"]))).json()
    assert w["badges"] == ["exploratory"] and w["spec"]["measure"]["ref"] == "measure:_adhoc"
    # Asked again: pending once more.
    item_id = owner_conn.execute("SELECT item_id FROM gov.certification_requests WHERE id = %s", (rid,)).fetchone()
    assert item_id is not None
    again = await stack.post("/api/v1/certification/requests", headers=h(world.tokens["creator"]),
                             json={"item": {"kind": "freehand", "id": str(item_id[0])}})
    assert again.status_code == 201, again.text
    w = (await stack.get(f"/api/v1/widgets/{first['id']}", headers=h(world.tokens["creator"]))).json()
    assert w["badges"] == ["pending_certification"]

    second = (await _pin(stack, world, OTHER_Q)).json()
    rid2 = second["certification_request_id"]
    assert (await stack.post(f"/api/v1/certification/requests/{rid2}/decision", headers=h(admin),
                             json={"decision": "reject"})).status_code == 400  # needs a reason
    rejected = await stack.post(f"/api/v1/certification/requests/{rid2}/decision", headers=h(admin), json={
        "decision": "reject", "note": "We already track this.", "merge_into": "measure:total_projects"})
    assert rejected.status_code == 201 and rejected.json()["status"] == "rejected"
    w = (await stack.get(f"/api/v1/widgets/{second['id']}", headers=h(world.tokens["creator"]))).json()
    assert w["badges"] == ["exploratory"]
    body = owner_conn.execute("SELECT body FROM ops.notifications WHERE user_id = %s AND kind = "
                              "'certification.rejected'", (world.extra["creator_id"],)).fetchone()
    assert body is not None and "total_projects" in body[0]
    # The model did not change for either.
    versions = owner_conn.execute("SELECT count(*) FROM model.model_versions WHERE status = 'approved'").fetchone()
    assert versions == (1,)
