"""Certification: requests, queue, item detail and decisions (API-033 to API-036; FEAT-032 to FEAT-037;
RULE-009 to RULE-011; US-081 to US-086).

* A personal definition (grouping, band, cross-table measure or custom SQL) enters the Admin queue when its
  author requests it — allowed once it is shared or on a dashboard (MSG-022) — or automatically, "publish-bound",
  when a dashboard that uses it is sent for publishing. One pending request per item; asking again reuses it.
  Editing an item while pending stores the new snapshot, flags "changed since requested" and tells the Admins.
* The queue lists blocks-publish first, then oldest, with usage, look-alike and dependency flags.
* The item detail shows the rule in words (deterministic; AG-09 may rephrase), results across the reviewer's
  scope, where it is used, the generated SQL, look-alikes and, for custom SQL, semantic checks. Viewing is
  audited.
* Decisions are insert-only records with the definition at that moment. Certify updates badges everywhere; Merge
  switches widgets to the certified target and tells the author which widgets changed; Reject needs a note and
  returns the item to Personal. Self-certification is off unless the tenant policy allows it.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, Header, Request
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from tela_api import outbox
from tela_api.audit import service as audit
from tela_api.content.common import notify, remember, replay, users_with_permission
from tela_api.core.db import fetch_all, fetch_one, tenant_tx
from tela_api.governance import freehand_cert, lookalike
from tela_api.identity.deps import require
from tela_api.identity.permissions import Perm
from tela_api.identity.principal import Principal
from tela_api.llm import trace
from tela_api.llm.client import AgentFailed, run_agent
from tela_api.model.store import load_snapshot
from tela_api.query.service import QueryContext, QueryService
from tela_common.errors import ErrorCode, TelaError
from tela_common.ids import new_id
from tela_common.logging import get_logger
from tela_query_spec.spec import ref_key

log = get_logger(__name__)

router = APIRouter(prefix="/api/v1", tags=["certification"])
DECISION_ROUTE = "POST /certification/decision"
REQUEST_ROUTE = "POST /certification/requests"
MSG_NOT_ELIGIBLE = "Share it or add it to something you'll publish first."  # MSG-022
FIELD_KINDS = ("grouping", "band", "join_measure", "freehand")


def _service(request: Request) -> QueryService:
    service: QueryService = request.app.state.query_service
    return service


def _rid(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


# --------------------------------------------------------------------------- items


async def load_item(conn: AsyncConnection, kind: str, item_id: str) -> dict[str, Any]:
    try:
        if kind in FIELD_KINDS:
            row = await fetch_one(conn, "SELECT *, label AS name, definition_hash AS hash FROM content.personal_fields "
                                        "WHERE id = CAST(:id AS uuid) AND kind = :k AND deleted_at IS NULL",
                                  {"id": item_id, "k": kind})
        elif kind == "custom_sql":
            row = await fetch_one(conn, "SELECT *, title AS name, ast_hash AS hash, title || ': custom SQL' AS "
                                        "plain_rule FROM content.custom_sql_definitions WHERE id = CAST(:id AS uuid) "
                                        "AND deleted_at IS NULL", {"id": item_id})
        else:
            row = None
    except Exception:  # noqa: BLE001
        row = None
    if row is None:
        raise TelaError(ErrorCode.NOT_FOUND, "That definition does not exist.")
    return row


def item_snapshot(kind: str, item: dict[str, Any]) -> dict[str, Any]:
    body = {"sql": item["sql_text"], "dialect": item["dialect"]} if kind == "custom_sql" else \
        {"definition": item["definition"]}
    return {"kind": kind, "name": item["name"], "plain_rule": item["plain_rule"], "hash": item["hash"],
            "connection_id": str(item["connection_id"]), "model_version_id": str(item["model_version_id"]),
            "owner_id": str(item["owner_id"]), **body}


def _table(kind: str) -> str:
    return "content.custom_sql_definitions" if kind == "custom_sql" else "content.personal_fields"


async def usage(conn: AsyncConnection, item_id: str) -> dict[str, Any]:
    widgets = await fetch_all(conn, "SELECT w.id, w.title FROM content.widgets w WHERE w.deleted_at IS NULL AND "
                                    "w.definition_refs @> CAST(:r AS jsonb)", {"r": json.dumps([{"id": item_id}])})
    ids = [str(w["id"]) for w in widgets]
    reports = await fetch_all(conn, "SELECT DISTINCT r.id, r.title FROM content.report_items i JOIN content.reports r "
                                    "ON r.id = i.report_id AND r.deleted_at IS NULL WHERE i.widget_id = ANY(CAST(:w AS "
                                    "uuid[]))", {"w": ids}) if ids else []
    rids = [str(r["id"]) for r in reports]
    dashboards = await fetch_all(conn, "SELECT DISTINCT d.id, d.title, d.live_version_id IS NOT NULL AS live FROM "
                                       "content.dashboard_reports dr JOIN content.dashboards d ON d.id = "
                                       "dr.dashboard_id AND d.deleted_at IS NULL WHERE dr.report_id = ANY(CAST(:r AS "
                                       "uuid[]))", {"r": rids}) if rids else []
    shared = await fetch_one(conn, """
        SELECT count(*) AS n FROM content.shares s WHERE
          (s.object_type = 'widget' AND s.object_id = ANY(CAST(:w AS uuid[])))
          OR (s.object_type = 'report' AND s.object_id = ANY(CAST(:r AS uuid[])))
          OR (s.object_type = 'dashboard' AND s.object_id IN (SELECT dashboard_id FROM content.dashboard_reports
                                                              WHERE report_id = ANY(CAST(:r AS uuid[]))))""",
                             {"w": ids, "r": rids})
    return {"widgets": [{"id": str(w["id"]), "title": w["title"]} for w in widgets],
            "reports": [{"id": str(r["id"]), "title": r["title"]} for r in reports],
            "dashboards": [{"id": str(d["id"]), "title": d["title"], "live": d["live"]} for d in dashboards],
            "shared": bool(shared and shared["n"])}


async def enqueue(conn: AsyncConnection, principal: Principal, kind: str, item: dict[str, Any], *, trigger: str,
                  note: str | None = None) -> tuple[str, bool]:
    """Create or reuse the single pending request for an item. Returns (request id, reused)."""
    pending = await fetch_one(conn, "SELECT id, definition_hash FROM gov.certification_requests WHERE item_type = :k "
                                    "AND item_id = :id AND status = 'pending'", {"k": kind, "id": item["id"]})
    if pending is not None:
        return str(pending["id"]), True
    rid = new_id()
    use = await usage(conn, str(item["id"]))
    await conn.execute(
        text("INSERT INTO gov.certification_requests (id, tenant_id, item_type, item_id, item_snapshot, "
             "definition_hash, "
             "trigger, note, requested_by, usage_snapshot) VALUES (:id, :t, :k, :item, CAST(:s AS jsonb), :h, :tr, :n, "
             ":u, CAST(:use AS jsonb))"),
        {"id": rid, "t": principal.tenant_id, "k": kind, "item": item["id"],
         "s": json.dumps(item_snapshot(kind, item), default=str), "h": item["hash"], "tr": trigger, "n": note,
         "u": str(item["owner_id"]) if trigger == "publish_bound" else principal.user_id,
         "use": json.dumps(use, default=str)})
    await conn.execute(text(f"UPDATE {_table(kind)} SET cert_status = 'pending' WHERE id = :id"),  # noqa: S608
                       {"id": item["id"]})
    admins = [u for u in await users_with_permission(conn, Perm.CERTIFICATION_DECIDE) if u != principal.user_id]
    await notify(conn, principal.tenant_id, admins, kind="certification.requested",
                 title=f"Certification requested: {item['name']}",
                 body=(note or "") + f" ({kind.replace('_', ' ')}, {trigger.replace('_', '-')})",
                 link=f"/certification/{rid}", by=principal.user_id)
    await audit.record(conn, tenant_id=principal.tenant_id, action="certification.requested",
                       object_type=kind, object_id=str(item["id"]), outcome="success", actor_id=principal.user_id,
                       correlation_id=None, details={"request_id": rid, "trigger": trigger})
    return rid, False


async def on_item_changed(conn: AsyncConnection, principal: Principal, kind: str, item_id: str) -> None:
    """Edit while pending: new snapshot, "changed since requested", Admins told (US-081)."""
    pending = await fetch_one(conn, "SELECT id FROM gov.certification_requests WHERE item_type = :k AND item_id = "
                                    "CAST(:id AS uuid) AND status = 'pending'", {"k": kind, "id": item_id})
    if pending is None:
        return
    item = await load_item(conn, kind, item_id)
    await conn.execute(
        text("UPDATE gov.certification_requests SET item_snapshot = CAST(:s AS jsonb), definition_hash = :h, "
             "changed_since_requested = true WHERE id = :id"),
        {"s": json.dumps(item_snapshot(kind, item), default=str), "h": item["hash"], "id": pending["id"]})
    await conn.execute(text("DELETE FROM gov.lookalike_findings WHERE request_id = :id"), {"id": pending["id"]})
    admins = await users_with_permission(conn, Perm.CERTIFICATION_DECIDE)
    await notify(conn, principal.tenant_id, admins, kind="certification.changed",
                 title=f"{item['name']} changed while waiting for certification",
                 body="Review the new definition before deciding.", link=f"/certification/{pending['id']}")


async def enqueue_for_widgets(conn: AsyncConnection, principal: Principal, widget_specs: list[dict[str, Any]],
                              *, trigger: str) -> list[str]:
    """Enqueue every uncertified definition used by these widgets (publish-bound entry, RULE-010)."""
    out = []
    refs = {(r.get("kind"), r.get("id")) for spec in widget_specs for r in spec.get("definition_refs", [])}
    for spec in widget_specs:
        if spec.get("kind") == "custom_sql":
            refs.add(("custom_sql", spec.get("custom_sql_id")))
    for kind, item_id in sorted(r for r in refs if r[0] and r[1]):
        try:
            item = await load_item(conn, kind, item_id)
        except TelaError:
            continue
        if item["cert_status"] in ("certified", "merged"):
            continue
        rid, _ = await enqueue(conn, principal, kind, item, trigger=trigger)
        out.append(rid)
    return out


# --------------------------------------------------------------------------- request (API-033)


class ItemRef(BaseModel):
    kind: str = Field(pattern="^(grouping|band|join_measure|custom_sql|freehand)$")
    id: str


class RequestIn(BaseModel):
    item: ItemRef
    note: str | None = Field(default=None, max_length=1000)


@router.post("/certification/requests", status_code=201)
async def request_certification(body: RequestIn, request: Request,
                                idempotency_key: str | None = Header(default=None),
                                principal: Principal = Depends(require(Perm.CERTIFICATION_REQUEST))) -> dict[str, Any]:
    async with tenant_tx(principal.tenant_id) as conn:
        if (prior := await replay(conn, principal, REQUEST_ROUTE, idempotency_key)) is not None:
            return prior
        item = await load_item(conn, body.item.kind, body.item.id)
        if str(item["owner_id"]) != principal.user_id:
            raise TelaError(ErrorCode.NOT_FOUND, "That definition does not exist.")
        if item["cert_status"] in ("certified", "merged"):
            raise TelaError(ErrorCode.CONFLICT, "This definition is already certified.")
        use = await usage(conn, body.item.id)
        if body.item.kind != freehand_cert.KIND and not use["shared"] and not use["dashboards"]:
            raise TelaError(ErrorCode.CONFLICT, MSG_NOT_ELIGIBLE)
        rid, reused = await enqueue(conn, principal, body.item.kind, item, trigger="manual", note=body.note)
        if body.item.kind == freehand_cert.KIND:  # asked again after changes: the widget waits again
            await freehand_cert.switch_widgets(conn, item, measure_ref=None, model_version_id=None,
                                               state="pending_certification")
        if reused and body.note:
            await conn.execute(text("UPDATE gov.certification_requests SET note = :n WHERE id = :id"),
                               {"n": body.note, "id": rid})
        out = {"request_id": rid, "status": "pending", "reused": reused, "dependencies": [],
               "usage": {k: len(v) if isinstance(v, list) else v for k, v in use.items()}}
        await remember(conn, principal, REQUEST_ROUTE, idempotency_key, 201, out)
    return out


@router.get("/certification/mine")
async def my_requests(principal: Principal = Depends(require(Perm.CERTIFICATION_REQUEST))) -> dict[str, Any]:
    async with tenant_tx(principal.tenant_id) as conn:
        rows = await fetch_all(conn, "SELECT r.id, r.item_type, r.item_id, r.status, r.created_at, r.item_snapshot ->> "
                                     "'name' AS name, d.note AS decision_note, d.decision FROM "
                                     "gov.certification_requests r LEFT JOIN gov.certification_decisions d ON "
                                     "d.request_id = r.id WHERE r.requested_by = :u ORDER BY r.created_at DESC",
                               {"u": principal.user_id})
    return {"items": [{**r, "id": str(r["id"]), "item_id": str(r["item_id"])} for r in rows]}


# --------------------------------------------------------------------------- queue (API-034)


@router.get("/certification/queue")
async def queue(kind: str | None = None, owner: str | None = None, connection_id: str | None = None,
                blocks_publish: bool | None = None, status: str = "pending",
                principal: Principal = Depends(require(Perm.CERTIFICATION_DECIDE))) -> dict[str, Any]:
    async with tenant_tx(principal.tenant_id) as conn:
        rows = await fetch_all(
            conn,
            """
            SELECT r.*, u.display_name AS author,
              (SELECT count(*) FROM gov.lookalike_findings f WHERE f.request_id = r.id) AS lookalikes,
              EXISTS (SELECT 1 FROM gov.certification_requests d WHERE d.id = ANY(r.depends_on)
                      AND d.status = 'pending') AS dependency_pending
            FROM gov.certification_requests r JOIN iam.users u ON u.id = r.requested_by
            WHERE r.status = :status AND (CAST(:kind AS text) IS NULL OR r.item_type = :kind)
              AND (CAST(:owner AS text) IS NULL OR u.display_name ILIKE '%' || :owner || '%')
              AND (CAST(:conn AS text) IS NULL OR r.item_snapshot ->> 'connection_id' = :conn)
            """, {"status": status, "kind": kind, "owner": owner, "conn": connection_id})
        items = []
        for r in rows:
            use = await usage(conn, str(r["item_id"]))
            blocks = r["trigger"] == "publish_bound" or bool(use["dashboards"])
            if blocks_publish is not None and blocks != blocks_publish:
                continue
            reason = {"publish_bound": "on a dashboard sent for publishing", "shared": "shared with colleagues",
                      "manual": "requested by the author"}[r["trigger"]]
            if r["item_type"] == freehand_cert.KIND:
                reason = "a free-hand answer added as a widget"
            items.append({
                "request_id": str(r["id"]), "kind": r["item_type"], "item_id": str(r["item_id"]),
                "name": r["item_snapshot"]["name"], "author": {"id": str(r["requested_by"]), "name": r["author"]},
                "submitted_at": r["created_at"], "age_days": (datetime.now(UTC) - r["created_at"]).days,
                "reason": reason, "trigger": r["trigger"], "note": r["note"], "blocks_publish": blocks,
                "usage": {"widgets": len(use["widgets"]), "reports": len(use["reports"]),
                          "dashboards": len(use["dashboards"])},
                "pending_publishes": [d["title"] for d in use["dashboards"]],
                "lookalikes": r["lookalikes"], "dependency_pending": r["dependency_pending"],
                "changed_since_requested": r["changed_since_requested"], "status": r["status"],
                "connection_id": r["item_snapshot"].get("connection_id"),
                "question": (r["item_snapshot"].get("definition") or {}).get("question")
                if r["item_type"] == freehand_cert.KIND else None})
    items.sort(key=lambda i: (not i["blocks_publish"], i["submitted_at"]))
    return {"items": items}


# --------------------------------------------------------------------------- detail (API-035)


class RephraseOut(BaseModel):
    text: str = Field(max_length=1200)


AG09_SYSTEM = ("You are Tela's certification advisor (AG-09). Rephrase the rule below for a PMO reviewer in plain "
               "English, in at most three sentences. Keep every name, value and number exactly as given; add nothing.")


def _grounded(rephrased: str, rule: str) -> bool:
    numbers = set(re.findall(r"\d[\d,.]*", rephrased))
    return numbers <= set(re.findall(r"\d[\d,.]*", rule))


async def _request(conn: AsyncConnection, request_id: str) -> dict[str, Any]:
    try:
        row = await fetch_one(conn, "SELECT r.*, u.display_name AS author FROM gov.certification_requests r JOIN "
                                    "iam.users u ON u.id = r.requested_by WHERE r.id = CAST(:id AS uuid)",
                              {"id": request_id})
    except Exception:  # noqa: BLE001
        row = None
    if row is None:
        raise TelaError(ErrorCode.NOT_FOUND, "That certification request does not exist.")
    return row


@router.get("/certification/requests/{request_id}")
async def detail(request_id: str, request: Request,
                 principal: Principal = Depends(require(Perm.CERTIFICATION_DECIDE))) -> dict[str, Any]:
    service = _service(request)
    gateway = getattr(request.app.state, "gateway", None)
    async with tenant_tx(principal.tenant_id) as conn:
        r = await _request(conn, request_id)
        kind = r["item_type"]
        item = await load_item(conn, kind, str(r["item_id"]))
        snap = await load_snapshot(conn, principal.tenant_id, str(item["model_version_id"]))
        use = await usage(conn, str(item["id"]))
        deps = await fetch_all(conn, "SELECT id, status, item_snapshot ->> 'name' AS name FROM "
                                     "gov.certification_requests WHERE id = ANY(CAST(:d AS uuid[]))",
                               {"d": [str(x) for x in r["depends_on"]]})
    # Results across the reviewer's (audited) certification scope.
    base = {"model_version_id": snap.model_version_id, "connection_id": snap.connection_id}
    results: dict[str, Any] = {}
    generated: str | None = None
    try:
        if kind == freehand_cert.KIND:
            results, generated = await freehand_cert.preview(service, principal, item, _rid(request))
        elif kind == "custom_sql":
            res = await service.execute_custom(item["sql_text"], connection_id=str(item["connection_id"]),
                                               model_version_id=str(item["model_version_id"]), principal=principal,
                                               ctx=QueryContext(surface="certification_preview",
                                                                request_id=_rid(request)))
            results = {k: res[k] for k in ("columns", "row_count", "truncated", "as_of")} | {"rows": res["rows"][:50]}
            generated = item["sql_text"]
        else:
            from tela_api.content.fields import preview_definition

            results = await preview_definition(request, principal, kind, item["definition"], snap,
                                               str(item["connection_id"]))
            if kind == "join_measure":
                spec = {**base, "measure": {"ref": f"measure:{item['definition']['key']}"},
                        "personal_measures": [item["definition"]]}
            else:
                m = next((m for m in snap.measures if m.status == "certified"
                          and m.entity == _entity_of(item["definition"]["source"], snap)), None)
                spec = {**base, "measure": {"ref": f"measure:{m.key}" if m else "measure:none"},
                        "personal_fields": [item["definition"]],
                        "breakdowns": [{"ref": f"personal:{item['definition']['key']}"}]}
            generated = (await service.explain(spec, principal, QueryContext(surface="certification_preview")))["sql"]
    except TelaError as exc:
        results = {"error": exc.message}
    async with tenant_tx(principal.tenant_id) as conn:
        findings = await fetch_all(conn, "SELECT * FROM gov.lookalike_findings WHERE request_id = :id ORDER BY "
                                         "similarity DESC", {"id": r["id"]})
        embedded = True
        if kind == freehand_cert.KIND:
            findings = []
        if not findings and r["status"] == "pending" and kind != freehand_cert.KIND:
            found, embedded = await lookalike.find(conn, service=service, gateway=gateway, principal=principal,
                                                   kind=kind, item=item, snap=snap)
            for f in found:
                await conn.execute(
                    text("INSERT INTO gov.lookalike_findings (id, tenant_id, request_id, similar_kind, similar_ref, "
                         "similarity, reasons, difference_summary, result_delta) VALUES (:id, :t, :r, :k, :ref, :s, "
                         ":why, :d, CAST(:delta AS jsonb))"),
                    {"id": new_id(), "t": principal.tenant_id, "r": r["id"], "k": f["similar_kind"],
                     "ref": f["similar_ref"], "s": f["similarity"], "why": f["reasons"],
                     "d": f["difference_summary"], "delta": json.dumps(f["result_delta"], default=str)})
            findings = await fetch_all(conn, "SELECT * FROM gov.lookalike_findings WHERE request_id = :id ORDER BY "
                                             "similarity DESC", {"id": r["id"]})
        await audit.record(conn, tenant_id=principal.tenant_id, action="certification.viewed",
                           object_type=kind, object_id=str(item["id"]), outcome="success",
                           actor_id=principal.user_id, correlation_id=_rid(request),
                           details={"request_id": request_id})
    semantic = await lookalike.semantic_checks(service, principal, item, snap) if kind == "custom_sql" else []
    freehand_looks = await freehand_cert.lookalikes(service, principal, item, snap) if kind == freehand_cert.KIND \
        else None
    rephrased = None
    if gateway is not None:
        async with trace.agent_run(principal.tenant_id, principal.user_id, "certification", title=item["name"],
                                   object_ref=f"{kind}:{item['id']}", agent="AG-09"):
            try:
                out = await run_agent(gateway, RephraseOut, tenant_id=principal.tenant_id, user_id=principal.user_id,
                                      agent="AG-09", task="cert_review", system=AG09_SYSTEM, user=item["plain_rule"])
                rephrased = out.text if _grounded(out.text, item["plain_rule"]) else None
                await trace.step("validate", "Rephrasing kept to the rule's own words" if rephrased else
                                 "Rephrasing added words the rule doesn't have: not shown", agent="AG-09",
                                 status="ok" if rephrased else "failed")
            except AgentFailed:
                rephrased = None
    return {
        "request_id": request_id, "status": r["status"], "kind": kind, "item_id": str(item["id"]),
        "name": item["name"], "author": {"id": str(r["requested_by"]), "name": r["author"]},
        "trigger": r["trigger"], "note": r["note"], "submitted_at": r["created_at"],
        "changed_since_requested": r["changed_since_requested"],
        "rule": {"plain": item["plain_rule"], "rephrased": rephrased,
                 "blank_handling": _blank_text(kind, item), "source": "deterministic"},
        "definition": item_snapshot(kind, item), "results": results, "results_scope": "Your certification scope",
        "usage": use, "generated": generated, "dependencies": [{**d, "id": str(d["id"])} for d in deps],
        "lookalikes": freehand_looks if freehand_looks is not None else [
            {"ref": f["similar_ref"], "kind": f["similar_kind"], "similarity": float(f["similarity"]),
             "reasons": f["reasons"], "difference": f["difference_summary"], "result_delta": f["result_delta"]}
            for f in findings],
        "question": item["definition"].get("question") if kind == freehand_cert.KIND else None,
        "decisions": ["certify", "request_changes", "reject"] if kind == freehand_cert.KIND else
        ["certify", "merge", "reject"],
        "lookalike_note": None if embedded else "Meaning similarity unavailable — structural checks only.",
        "semantic_checks": semantic, "can_decide": await _can_decide(principal, r, item),
    }


def _entity_of(source: str, snap: Any) -> str | None:
    key = source.split(":", 1)[1]
    if source.startswith("dimension:"):
        d = snap.dimension(key)
        return d.entity if d else None
    return key.split(".", 1)[0]


def _blank_text(kind: str, item: dict[str, Any]) -> str:
    if kind == "grouping":
        return f"Blank and whitespace-only values go to {item['definition'].get('not_set', 'Not set')}."
    if kind == "band":
        return "Blank values go to Not set; values below the first edge go to Below."
    if kind == "join_measure":
        return "Blank values are not counted; records without the required link are not counted."
    if kind == freehand_cert.KIND:
        return "A blank field matches only an “is blank” condition; blank records are otherwise not counted."
    return "Blank handling is whatever the SQL does; review the statement."


async def _policy(conn: AsyncConnection) -> dict[str, Any]:
    row = await fetch_one(conn, "SELECT allow_self_certification FROM gov.policies")
    return {"allow_self_certification": bool(row and row["allow_self_certification"])}


async def _can_decide(principal: Principal, r: dict[str, Any], item: dict[str, Any]) -> dict[str, Any]:
    async with tenant_tx(principal.tenant_id) as conn:
        policy = await _policy(conn)
    own = principal.user_id in (str(item["owner_id"]), str(r["requested_by"]))
    if r["status"] != "pending":
        return {"allowed": False, "reason": "Already decided."}
    if own and not policy["allow_self_certification"]:
        return {"allowed": False, "reason": "You can't certify your own definition. Another Admin must decide."}
    return {"allowed": True, "reason": None}


# --------------------------------------------------------------------------- decision (API-036)


class DecisionIn(BaseModel):
    decision: str = Field(pattern="^(certify|merge|reject|request_changes)$")
    # US-166: Approve of a free-hand rule names the certified measure it becomes.
    measure: freehand_cert.MeasureIn | None = None
    merge_into: str | None = Field(default=None, pattern=r"^(measure|dimension):[a-z_][a-z0-9_]{0,62}$")
    note: str | None = Field(default=None, max_length=1000)
    confirm_different: bool = False
    reviewed_changes: bool = False


async def _refresh_badges(conn: AsyncConnection, item_id: str) -> list[str]:
    """Recompute widget badges after a decision: certified when every definition they use is certified."""
    changed = []
    for w in await fetch_all(conn, "SELECT id, title, definition_refs, certification_state, source_kind FROM "
                                   "content.widgets WHERE definition_refs @> CAST(:r AS jsonb) AND deleted_at IS NULL",
                             {"r": json.dumps([{"id": item_id}])}):
        states = []
        for ref in w["definition_refs"]:
            row = await fetch_one(conn, f"SELECT cert_status FROM {_table(ref['kind'])} WHERE id = "  # noqa: S608
                                        "CAST(:id AS uuid)", {"id": ref["id"]})
            states.append(row["cert_status"] if row else "personal")
        new = "certified" if all(s in ("certified", "merged") for s in states) else (
            "custom_sql" if w["source_kind"] == "custom_sql" else "personal")
        if new != w["certification_state"]:
            await conn.execute(text("UPDATE content.widgets SET certification_state = :s WHERE id = :id"),
                               {"s": new, "id": w["id"]})
            changed.append(w["title"])
    return changed


async def _merge_widgets(conn: AsyncConnection, kind: str, item: dict[str, Any], target: str) -> list[str]:
    """Merge: every widget using the item switches to the certified target (spec only; frozen versions keep theirs)."""
    changed = []
    item_id = str(item["id"])
    for w in await fetch_all(conn, "SELECT * FROM content.widgets WHERE definition_refs @> CAST(:r AS jsonb) AND "
                                   "deleted_at IS NULL", {"r": json.dumps([{"id": item_id}])}):
        spec = dict(w["spec"])
        refs = [r for r in w["definition_refs"] if r["id"] != item_id]
        visual, source_kind, custom_id = w["visual"], w["source_kind"], w["custom_sql_id"]
        if kind in ("grouping", "band"):
            key = item["definition"]["key"]
            spec["personal_fields"] = [p for p in spec.get("personal_fields", []) if p["key"] != key]
            spec["breakdowns"] = [{"ref": target} if b["ref"] == f"personal:{key}" else b
                                  for b in spec.get("breakdowns", [])]
        elif kind == "join_measure":
            key = item["definition"]["key"]
            spec["personal_measures"] = [p for p in spec.get("personal_measures", []) if p["key"] != key]
            if spec["measure"]["ref"] == f"measure:{key}":
                spec["measure"] = {"ref": target}
            spec["extra_measures"] = [{"ref": target} if e["ref"] == f"measure:{key}" else e
                                      for e in spec.get("extra_measures", [])]
        else:
            spec = {"model_version_id": spec["model_version_id"], "connection_id": spec["connection_id"],
                    "measure": {"ref": target}, "visual": {"type": "figure"}}
            visual, source_kind, custom_id = "figure", "spec", None
        if "definition_refs" in spec:
            spec["definition_refs"] = [r for r in spec["definition_refs"] if r["id"] != item_id]
        state = "certified"
        for ref in refs:
            row = await fetch_one(conn, f"SELECT cert_status FROM {_table(ref['kind'])} WHERE id = "  # noqa: S608
                                        "CAST(:id AS uuid)", {"id": ref["id"]})
            if row is None or row["cert_status"] not in ("certified", "merged"):
                state = "custom_sql" if source_kind == "custom_sql" else "personal"
        await conn.execute(
            text("UPDATE content.widgets SET spec = CAST(:s AS jsonb), definition_refs = CAST(:r AS "
                 "jsonb), visual = :v, "
                 "source_kind = :sk, custom_sql_id = CAST(:c AS uuid), def_hash = NULL, certification_state = :st "
                 "WHERE id = :id"),
            {"s": json.dumps(spec), "r": json.dumps(refs), "v": visual, "sk": source_kind,
             "c": str(custom_id) if custom_id else None, "st": state, "id": w["id"]})
        changed.append(w["title"])
    return changed


async def _suggest_gold(conn: AsyncConnection, request: Request, principal: Principal, item: dict[str, Any],
                        certified: dict[str, Any]) -> None:
    """A certified free-hand question becomes a suggested gold example on its new measure (US-175), for Admin review
    like any suggestion; a failure never undoes the certification."""
    from tela_api.knowledge.gold import suggest_example

    d = item["definition"]
    try:
        async with conn.begin_nested():
            snap = await load_snapshot(conn, principal.tenant_id, certified["model_version_id"])
            await suggest_example(conn, getattr(request.app.state, "gateway", None), principal,
                                  connection_id=str(item["connection_id"]), snap=snap, question=d.get("question", ""),
                                  spec={"measure": {"ref": certified["measure"]}, "breakdowns": d.get("breakdowns", []),
                                        "filters": []}, origin="freehand",
                                  notes=f"From the certified free-hand rule {certified['measure']}.")
    except Exception as exc:  # noqa: BLE001 - a by-product of certification
        log.warning("certification.gold_suggestion_failed", error_class=type(exc).__name__)


@router.post("/certification/requests/{request_id}/decision", status_code=201)
async def decide(request_id: str, body: DecisionIn, request: Request,
                 idempotency_key: str | None = Header(default=None),
                 principal: Principal = Depends(require(Perm.CERTIFICATION_DECIDE))) -> dict[str, Any]:
    route = f"{DECISION_ROUTE}/{request_id}"
    async with tenant_tx(principal.tenant_id) as conn:
        if (prior := await replay(conn, principal, route, idempotency_key)) is not None:
            return prior
        r = await _request(conn, request_id)
        if r["status"] != "pending":
            raise TelaError(ErrorCode.ALREADY_DECIDED, "This request has already been decided.")
        pending_deps = await fetch_all(conn, "SELECT item_snapshot ->> 'name' AS name FROM gov.certification_requests "
                                             "WHERE id = ANY(CAST(:d AS uuid[])) AND status = 'pending'",
                                       {"d": [str(x) for x in r["depends_on"]]})
        if pending_deps:
            raise TelaError(ErrorCode.DEPENDENCY_PENDING, "Decide what this depends on first: "
                            + ", ".join(d["name"] for d in pending_deps) + ".")
        kind = r["item_type"]
        item = await load_item(conn, kind, str(r["item_id"]))
        policy = await _policy(conn)
        if principal.user_id in (str(item["owner_id"]), str(r["requested_by"])) and \
                not policy["allow_self_certification"]:
            await audit.record(conn, tenant_id=principal.tenant_id, action="certification.self_decision_blocked",
                               object_type=kind, object_id=str(item["id"]), outcome="deny",
                               actor_id=principal.user_id, correlation_id=_rid(request), severity="high")
            denied = TelaError(ErrorCode.FORBIDDEN, "You can't certify your own definition. Another Admin must "
                               "decide, or the tenant policy must allow self-certification.")
        else:
            denied = None
    if denied is not None:
        raise denied
    if r["changed_since_requested"] and not body.reviewed_changes:
        raise TelaError(ErrorCode.CONFLICT, "This definition changed since it was requested. Review the new version "
                        "and confirm.", {"changed_since_requested": True})
    if body.decision == "reject" and not (body.note or "").strip():
        raise TelaError(ErrorCode.VALIDATION_ERROR, "Say why it is rejected; the author will see the note.")
    freehand = kind == freehand_cert.KIND
    if body.decision == "request_changes" and not freehand:
        raise TelaError(ErrorCode.VALIDATION_ERROR, "Ask for changes applies to free-hand rules.")
    if body.decision == "request_changes" and not (body.note or "").strip():
        raise TelaError(ErrorCode.VALIDATION_ERROR, "Say what should change; the author will see the note.")
    if freehand and body.decision == "merge":
        raise TelaError(ErrorCode.VALIDATION_ERROR, "Approve the rule as a new measure, or reject it and point to the "
                        "existing measure.")
    if freehand and body.decision == "certify" and body.measure is None:
        raise TelaError(ErrorCode.VALIDATION_ERROR, "Name the certified measure: key, name, definition and plain rule.")
    if freehand and body.merge_into and body.decision == "reject":
        async with tenant_tx(principal.tenant_id) as conn:
            snap = await load_snapshot(conn, principal.tenant_id, str(item["model_version_id"]))
        if snap.measure(ref_key(body.merge_into)) is None:
            raise TelaError(ErrorCode.VALIDATION_ERROR, "Point to a certified measure of the same model.")
    certified: dict[str, Any] | None = None
    if freehand and body.decision == "certify":
        assert body.measure is not None
        certified = await freehand_cert.approve(request, principal, item, body.measure)
    snap = None
    if body.decision == "merge":
        if not body.merge_into:
            raise TelaError(ErrorCode.VALIDATION_ERROR, "Choose the certified field to merge into.")
        async with tenant_tx(principal.tenant_id) as conn:
            snap = await load_snapshot(conn, principal.tenant_id, str(item["model_version_id"]))
            finding = await fetch_one(conn, "SELECT result_delta FROM gov.lookalike_findings WHERE request_id = :r AND "
                                            "similar_ref = :t", {"r": r["id"], "t": body.merge_into})
        want = "dimension" if kind in ("grouping", "band") else "measure"
        target_kind, key = body.merge_into.split(":", 1)
        exists = snap.dimension(key) if target_kind == "dimension" else snap.measure(key)
        if target_kind != want or exists is None:
            raise TelaError(ErrorCode.VALIDATION_ERROR, f"Merge a {kind.replace('_', ' ')} into a certified {want} of "
                            "the same model.")
        delta = (finding or {}).get("result_delta") or {}
        if delta.get("differs") and not body.confirm_different:
            raise TelaError(ErrorCode.CONFLICT, f"{exists.label} gives a different answer "
                            f"({delta.get('item')} vs {delta.get('certified')}). Confirm to merge anyway.",
                            {"result_delta": delta})

    async with tenant_tx(principal.tenant_id) as conn:
        r = await _request(conn, request_id)
        if r["status"] != "pending":
            raise TelaError(ErrorCode.ALREADY_DECIDED, "This request has already been decided.")
        item = await load_item(conn, kind, str(r["item_id"]))
        status = {"certify": "certified", "merge": "merged", "reject": "rejected",
                  "request_changes": "changes_requested"}[body.decision]
        await conn.execute(
            text("INSERT INTO gov.certification_decisions (id, tenant_id, request_id, decision, merged_into_ref, note, "
                 "definition_snapshot, decided_by) VALUES (:id, :t, :r, :d, CAST(:m AS jsonb), :n, CAST(:s AS jsonb), "
                 ":u)"),
            {"id": new_id(), "t": principal.tenant_id, "r": r["id"], "d": body.decision,
             "m": json.dumps({"ref": certified["measure"] if certified else body.merge_into})
             if certified or body.merge_into else None, "n": body.note,
             "s": json.dumps(item_snapshot(kind, item), default=str), "u": principal.user_id})
        await conn.execute(text("UPDATE gov.certification_requests SET status = :s WHERE id = :id"),
                           {"s": status, "id": r["id"]})
        item_status = {"certify": "certified", "merge": "merged", "reject": "rejected" if freehand else "personal",
                       "request_changes": "personal"}[body.decision]
        await conn.execute(text(f"UPDATE {_table(kind)} SET cert_status = :s, certified_as = :a "  # noqa: S608
                                "WHERE id = :id"),
                           {"s": item_status, "a": certified["measure"] if certified else body.merge_into,
                            "id": item["id"]})
        changed: list[str] = []
        if freehand:
            # Approve: the widgets read the new certified measure; otherwise they stay out of reports.
            changed = await freehand_cert.switch_widgets(
                conn, item, measure_ref=certified["measure"] if certified else None,
                model_version_id=certified["model_version_id"] if certified else None, state="exploratory")
            if certified:
                await _suggest_gold(conn, request, principal, item, certified)
        elif body.decision == "merge" and body.merge_into:
            changed = await _merge_widgets(conn, kind, item, body.merge_into)
        if not freehand:
            changed += [t for t in await _refresh_badges(conn, str(item["id"])) if t not in changed]
        if kind == "custom_sql" and body.decision == "certify":
            await conn.execute(text("UPDATE content.widgets SET certification_state = 'certified' WHERE custom_sql_id "
                                    "= :id AND deleted_at IS NULL"), {"id": item["id"]})
        title = {"certify": f"{item['name']} is certified",
                 "merge": f"{item['name']} was merged into {body.merge_into and ref_key(body.merge_into)}",
                 "reject": f"{item['name']} was not certified",
                 "request_changes": f"{item['name']}: changes asked before certification"}[body.decision]
        if certified and body.measure is not None:
            title = f"{item['name']} is certified as {body.measure.label}"
        msg = (body.note or "") + (f" Widgets changed: {', '.join(changed)}." if changed else "")
        if freehand and body.decision == "reject" and body.merge_into:
            msg += f" Use the certified measure {ref_key(body.merge_into)} instead."
        await notify(conn, principal.tenant_id, [str(item["owner_id"])], kind=f"certification.{status}", title=title,
                     body=msg.strip() or "No widgets needed to change.", link="/ask" if freehand else "/fields",
                     by=principal.user_id)
        await outbox.emit(conn, tenant_id=principal.tenant_id, topic="certification.decided",
                          aggregate_id=str(item["id"]), payload={"request_id": request_id, "decision": body.decision})
        await audit.record(conn, tenant_id=principal.tenant_id, action="certification.decided", object_type=kind,
                           object_id=str(item["id"]), outcome="success", actor_id=principal.user_id,
                           correlation_id=_rid(request),
                           details={"decision": body.decision, "request_id": request_id, "merge_into": body.merge_into,
                                    "widgets_changed": len(changed),
                                    **({"measure": certified["measure"], "model_version": certified["version"]}
                                       if certified else {})})
        out = {"request_id": request_id, "decision": body.decision, "status": status, "widgets_changed": changed,
               **({"measure": certified["measure"], "model_version_id": certified["model_version_id"],
                   "model_version": certified["version"]} if certified else {})}
        await remember(conn, principal, route, idempotency_key, 201, out)
    return out


# --------------------------------------------------------------------------- policy


class PolicyIn(BaseModel):
    allow_self_certification: bool


@router.get("/admin/certification-policy")
async def get_policy(principal: Principal = Depends(require(Perm.SETTINGS_MANAGE))) -> dict[str, Any]:
    async with tenant_tx(principal.tenant_id) as conn:
        return await _policy(conn)


@router.put("/admin/certification-policy")
async def put_policy(body: PolicyIn, request: Request,
                     principal: Principal = Depends(require(Perm.SETTINGS_MANAGE))) -> dict[str, Any]:
    async with tenant_tx(principal.tenant_id) as conn:
        await conn.execute(
            text("INSERT INTO gov.policies (tenant_id, allow_self_certification, updated_by) VALUES (:t, :a, :u) ON "
                 "CONFLICT (tenant_id) DO UPDATE SET allow_self_certification = :a, updated_by = :u, "
                 "updated_at = now()"),
            {"t": principal.tenant_id, "a": body.allow_self_certification, "u": principal.user_id})
        await audit.record(conn, tenant_id=principal.tenant_id, action="settings.certification_policy",
                           object_type="policy", outcome="success", actor_id=principal.user_id,
                           correlation_id=_rid(request), details=body.model_dump())
        return await _policy(conn)
