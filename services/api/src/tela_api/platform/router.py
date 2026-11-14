"""Audit log viewer, notifications and the publish-target role list (API-052, API-057; FEAT-048; US-105, US-107).

* The audit viewer filters by actor, action, object, date, outcome and security flag, newest first, with an
  opaque cursor. CSV export is itself audited (``audit.exported``). Details never hold row values or secrets
  (they were redacted when written).
* Notifications are per person: unread list, mark one or all as read.
"""

from __future__ import annotations

import base64
import csv
import io
import json
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy import text

from tela_api.audit import service as audit
from tela_api.core.db import fetch_all, fetch_one, tenant_tx
from tela_api.identity.deps import require
from tela_api.identity.permissions import Perm
from tela_api.identity.principal import Principal
from tela_common.errors import ErrorCode, TelaError

router = APIRouter(prefix="/api/v1", tags=["platform"])
EXPORT_CAP = 10_000
LINKS = {"dashboard": "/dashboards/{id}", "report": "/reports/{id}", "connection": "/admin/connections",
         "data_model": "/admin/models/{id}", "personal_field": "/fields/{id}", "custom_sql": "/sql/{id}"}


def _cursor(value: str | None) -> int | None:
    if not value:
        return None
    try:
        return int(base64.urlsafe_b64decode(value.encode()).decode())
    except (ValueError, UnicodeDecodeError) as exc:
        raise TelaError(ErrorCode.VALIDATION_ERROR, "That page cursor is not valid.") from exc


def _filters(action: str | None, actor: str | None, object_type: str | None, object_id: str | None,
             outcome: str | None, security: bool | None, start: datetime | None,
             end: datetime | None) -> tuple[str, dict[str, Any]]:
    where: list[str] = ["true"]
    params: dict[str, Any] = {}
    if action:
        where.append("e.action LIKE :action")
        # "dashboard.published" is exact; "dashboard." or "dashboard" or "dash*" is a prefix.
        exact = "*" not in action and "." in action.strip(".")
        params["action"] = action.replace("*", "%") if "*" in action else action + ("" if exact else "%")
    if actor:
        where.append("(u.display_name ILIKE :actor OR CAST(e.actor_id AS text) = :actor_id)")
        params.update(actor=f"%{actor}%", actor_id=actor)
    if object_type:
        where.append("e.object_type = :otype")
        params["otype"] = object_type
    if object_id:
        where.append("e.object_id = :oid")
        params["oid"] = object_id
    if outcome:
        where.append("e.outcome = :outcome")
        params["outcome"] = outcome
    if security is not None:
        where.append("e.severity = 'security'" if security else "e.severity <> 'security'")
    if start:
        where.append("e.occurred_at >= :start")
        params["start"] = start
    if end:
        where.append("e.occurred_at < :end")
        params["end"] = end
    return " AND ".join(where), params


_SELECT = ("SELECT e.id, e.occurred_at, e.actor_id, u.display_name AS actor_name, e.action, e.object_type, "
           "e.object_id, e.outcome, e.severity, e.correlation_id, e.details FROM ops.audit_events e "
           "LEFT JOIN iam.users u ON u.id = e.actor_id WHERE ")


def _item(r: dict[str, Any]) -> dict[str, Any]:
    link = LINKS.get(r["object_type"])
    return {"id": r["id"], "at": r["occurred_at"], "actor": {"id": str(r["actor_id"]) if r["actor_id"] else None,
                                                             "name": r["actor_name"]},
            "action": r["action"], "object": {"type": r["object_type"], "id": r["object_id"],
                                              "link": link.format(id=r["object_id"]) if link and r["object_id"]
                                              else None},
            "outcome": r["outcome"], "security": r["severity"] == "security", "request_id": r["correlation_id"],
            "details": r["details"]}


@router.get("/admin/audit")
async def audit_events(action: str | None = None, actor: str | None = None, object_type: str | None = None,
                       object_id: str | None = None, outcome: str | None = None, security: bool | None = None,
                       start: datetime | None = Query(default=None, alias="from"),
                       end: datetime | None = Query(default=None, alias="to"),
                       cursor: str | None = None, limit: int = Query(default=50, ge=1, le=100),
                       principal: Principal = Depends(require(Perm.AUDIT_READ))) -> dict[str, Any]:
    where, params = _filters(action, actor, object_type, object_id, outcome, security, start, end)
    after = _cursor(cursor)
    if after is not None:
        where += " AND e.id < :after"
        params["after"] = after
    async with tenant_tx(principal.tenant_id) as conn:
        rows = await fetch_all(conn, _SELECT + where + " ORDER BY e.id DESC LIMIT :lim",
                               {**params, "lim": limit + 1})
        actions = await fetch_all(conn, "SELECT DISTINCT action FROM ops.audit_events ORDER BY action")
    more = len(rows) > limit
    rows = rows[:limit]
    nxt = base64.urlsafe_b64encode(str(rows[-1]["id"]).encode()).decode() if more and rows else None
    return {"items": [_item(r) for r in rows], "next_cursor": nxt, "actions": [a["action"] for a in actions]}


@router.get("/admin/audit/facets")
async def audit_facets(action: str | None = None, actor: str | None = None, object_type: str | None = None,
                       object_id: str | None = None, outcome: str | None = None, security: bool | None = None,
                       start: datetime | None = Query(default=None, alias="from"),
                       end: datetime | None = Query(default=None, alias="to"),
                       principal: Principal = Depends(require(Perm.AUDIT_READ))) -> dict[str, Any]:
    """Counts per action, object type, outcome and person for the audit filters (US-146).

    Each facet is counted under every other filter but its own, so choosing an action still shows how many events
    the other actions have. People are the 20 with the most events.
    """
    given: dict[str, Any] = {"action": action, "actor": actor, "object_type": object_type, "object_id": object_id,
             "outcome": outcome, "security": security, "start": start, "end": end}

    def where_without(name: str) -> tuple[str, dict[str, Any]]:
        args: dict[str, Any] = {**given, name: None}
        return _filters(args["action"], args["actor"], args["object_type"], args["object_id"], args["outcome"],
                        args["security"], args["start"], args["end"])

    columns = {"action": "e.action", "object_type": "e.object_type", "outcome": "e.outcome",
               "actor": "coalesce(u.display_name, 'System')"}
    out: dict[str, Any] = {}
    async with tenant_tx(principal.tenant_id) as conn:
        for name, column in columns.items():
            where, params = where_without(name)
            rows = await fetch_all(
                conn, f"SELECT {column} AS value, count(*) AS n FROM ops.audit_events e "  # noqa: S608 - fixed columns
                      f"LEFT JOIN iam.users u ON u.id = e.actor_id WHERE {where} GROUP BY 1 ORDER BY 2 DESC, 1 "
                      f"LIMIT {20 if name == 'actor' else 200}", params)
            out[name] = [{"value": r["value"], "count": r["n"]} for r in rows]
        where, params = where_without("security")
        sec = await fetch_one(conn, _COUNT.replace("count(*) AS n", "count(*) FILTER (WHERE e.severity = 'security') "
                                                                       "AS security") + where, params)
    total = await _count(principal, given)
    return {**out, "security": sec["security"] if sec else 0, "total": total}


_COUNT = "SELECT count(*) AS n FROM ops.audit_events e LEFT JOIN iam.users u ON u.id = e.actor_id WHERE "


async def _count(principal: Principal, given: dict[str, Any]) -> int:
    where, params = _filters(given["action"], given["actor"], given["object_type"], given["object_id"],
                             given["outcome"], given["security"], given["start"], given["end"])
    async with tenant_tx(principal.tenant_id) as conn:
        row = await fetch_one(conn, _COUNT + where, params)
    return int(row["n"]) if row else 0


@router.post("/admin/audit/export")
async def export_audit(request: Request, action: str | None = None, actor: str | None = None,
                       object_type: str | None = None, object_id: str | None = None, outcome: str | None = None,
                       security: bool | None = None, start: datetime | None = Query(default=None, alias="from"),
                       end: datetime | None = Query(default=None, alias="to"),
                       principal: Principal = Depends(require(Perm.AUDIT_READ))) -> Response:
    where, params = _filters(action, actor, object_type, object_id, outcome, security, start, end)
    async with tenant_tx(principal.tenant_id) as conn:
        rows = await fetch_all(conn, _SELECT + where + " ORDER BY e.id DESC LIMIT :lim",
                               {**params, "lim": EXPORT_CAP})
        await audit.record(conn, tenant_id=principal.tenant_id, action="audit.exported", object_type="audit_log",
                           outcome="success", actor_id=principal.user_id,
                           correlation_id=getattr(request.state, "request_id", None),
                           details={"rows": len(rows), "filters": {k: str(v) for k, v in params.items()}})
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["at", "actor", "action", "object_type", "object_id", "outcome", "security", "request_id",
                     "details"])
    for r in rows:
        writer.writerow([r["occurred_at"].isoformat(), r["actor_name"] or "", r["action"], r["object_type"],
                         r["object_id"] or "", r["outcome"], r["severity"] == "security", r["correlation_id"],
                         json.dumps(r["details"], default=str)])
    return Response(buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": "attachment; filename=tela-audit.csv"})


# --------------------------------------------------------------------------- notifications (API-057)


_NOTIFICATIONS = "SELECT id, kind, title, body, link, read_at, created_at FROM ops.notifications WHERE "


@router.get("/notifications")
async def notifications(unread: bool = False, kind: str | None = Query(default=None, max_length=60),
                        cursor: str | None = None, limit: int = Query(default=50, ge=1, le=100),
                        principal: Principal = Depends(require(Perm.DASHBOARDS_VIEW))) -> dict[str, Any]:
    """The person's notifications, newest first (API-057). ``kind`` filters by type; ``cursor`` pages (US-146)."""
    where = "user_id = :u AND (NOT :unread OR read_at IS NULL) AND (CAST(:kind AS text) IS NULL OR kind = :kind)"
    params: dict[str, Any] = {"u": principal.user_id, "unread": unread, "kind": kind, "lim": limit + 1}
    if cursor:
        try:
            at, nid = base64.urlsafe_b64decode(cursor.encode()).decode().split("|", 1)
            params.update(c_at=datetime.fromisoformat(at), c_id=nid)
        except (ValueError, UnicodeDecodeError) as exc:
            raise TelaError(ErrorCode.VALIDATION_ERROR, "That page cursor is not valid.") from exc
        where += " AND (created_at, id) < (:c_at, CAST(:c_id AS uuid))"
    async with tenant_tx(principal.tenant_id) as conn:
        sql = _NOTIFICATIONS + where + " ORDER BY created_at DESC, id DESC LIMIT :lim"
        rows = await fetch_all(conn, sql, params)
        count = await fetch_one(conn, "SELECT count(*) AS n FROM ops.notifications WHERE user_id = :u AND read_at IS "
                                      "NULL", {"u": principal.user_id})
        kinds = await fetch_all(conn, "SELECT kind, count(*) AS n, count(*) FILTER (WHERE read_at IS NULL) AS unread "
                                      "FROM ops.notifications WHERE user_id = :u GROUP BY kind ORDER BY kind",
                                {"u": principal.user_id})
    more = len(rows) > limit
    rows = rows[:limit]
    nxt = base64.urlsafe_b64encode(f"{rows[-1]['created_at'].isoformat()}|{rows[-1]['id']}".encode()).decode() \
        if more and rows else None
    return {"items": [{**r, "id": str(r["id"])} for r in rows], "unread_count": count["n"] if count else 0,
            "next_cursor": nxt, "kinds": [{"kind": k["kind"], "count": k["n"], "unread": k["unread"]} for k in kinds]}


@router.post("/notifications/{notification_id}/read", status_code=204)
async def mark_read(notification_id: str,
                    principal: Principal = Depends(require(Perm.DASHBOARDS_VIEW))) -> Response:
    async with tenant_tx(principal.tenant_id) as conn:
        try:
            res = await conn.execute(text("UPDATE ops.notifications SET read_at = coalesce(read_at, now()) WHERE "
                                          "id = CAST(:id AS uuid) AND user_id = :u"),
                                     {"id": notification_id, "u": principal.user_id})
        except Exception as exc:
            raise TelaError(ErrorCode.NOT_FOUND, "That notification does not exist.") from exc
    if res.rowcount == 0:
        raise TelaError(ErrorCode.NOT_FOUND, "That notification does not exist.")
    return Response(status_code=204)


@router.post("/notifications/read-all", status_code=204)
async def mark_all_read(principal: Principal = Depends(require(Perm.DASHBOARDS_VIEW))) -> Response:
    async with tenant_tx(principal.tenant_id) as conn:
        await conn.execute(text("UPDATE ops.notifications SET read_at = now() WHERE user_id = :u AND read_at IS NULL"),
                           {"u": principal.user_id})
    return Response(status_code=204)


@router.get("/roles")
async def roles(principal: Principal = Depends(require(Perm.DASHBOARDS_CREATE))) -> dict[str, Any]:
    """Roles a dashboard can be published to or previewed as (system and audience roles)."""
    async with tenant_tx(principal.tenant_id) as conn:
        rows = await fetch_all(conn, "SELECT id, key, name, kind FROM iam.roles ORDER BY kind DESC, name")
        # Active people per role (effective roles, so group mappings count): the publish audience step (US-142).
        members = {str(r["role_id"]): r["n"] for r in await fetch_all(
            conn, "SELECT er.role_id, count(DISTINCT u.id) AS n FROM iam.users u "
                  "CROSS JOIN LATERAL iam.f_effective_roles(u.id) er WHERE u.status = 'active' GROUP BY er.role_id")}
    return {"items": [{"id": str(r["id"]), "key": r["key"], "name": r["name"], "kind": r["kind"],
                       "members": members.get(str(r["id"]), 0)} for r in rows]}
