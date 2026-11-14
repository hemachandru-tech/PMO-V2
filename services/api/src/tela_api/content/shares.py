"""Share with named people (API-042; FEAT-041; DATA-034; US-094).

View-only sharing of a widget, report or dashboard draft with 1 to 50 active colleagues, without publishing.
Recipients always see data in **their own** scope: a share never widens row access. A recipient with no data
access on a source still gets the share, with a warning to the sharer (E-11). Unsharing removes access at
once. ``edit`` sharing is reserved (Q-005).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from tela_api.audit import service as audit
from tela_api.content.common import MSG_FREEHAND_HELD, is_freehand, notify, principal_for_user
from tela_api.core.db import fetch_all, fetch_one, tenant_tx
from tela_api.identity.deps import require
from tela_api.identity.permissions import Perm
from tela_api.identity.principal import Principal
from tela_api.identity.scope import resolve_scope
from tela_common.errors import ErrorCode, TelaError
from tela_common.ids import new_id

router = APIRouter(prefix="/api/v1", tags=["shares"])
TABLES = {"widget": "content.widgets", "report": "content.reports", "dashboard": "content.dashboards"}
LINKS = {"widget": "/library", "report": "/reports/{id}", "dashboard": "/dashboards/{id}/draft"}


class ObjectRef(BaseModel):
    kind: str = Field(pattern="^(widget|report|dashboard)$")
    id: str = Field(max_length=64)


class ShareIn(BaseModel):
    object: ObjectRef
    with_user_ids: list[str] = Field(min_length=1, max_length=50)
    permission: str = Field(default="view", pattern="^(view|edit)$")


async def _owned(conn: AsyncConnection, principal: Principal, kind: str, object_id: str) -> dict[str, Any]:
    try:
        row = await fetch_one(conn, f"SELECT * FROM {TABLES[kind]} WHERE id = CAST(:id AS uuid) "  # noqa: S608
                                    "AND deleted_at IS NULL", {"id": object_id})
    except Exception:  # noqa: BLE001
        row = None
    if row is None or str(row.get("owner_id")) != principal.user_id:
        raise TelaError(ErrorCode.NOT_FOUND, f"That {kind} does not exist or is not yours.")
    return row


async def _connections(conn: AsyncConnection, kind: str, object_id: Any) -> list[dict[str, Any]]:
    if kind == "widget":
        sql = "SELECT DISTINCT c.id, c.name FROM content.widgets w JOIN conn.connections c ON c.id = w.connection_id " \
              "WHERE w.id = :id"
    elif kind == "report":
        sql = "SELECT DISTINCT c.id, c.name FROM content.report_items i JOIN content.widgets w ON w.id = i.widget_id " \
              "JOIN conn.connections c ON c.id = w.connection_id WHERE i.report_id = :id"
    else:
        sql = "SELECT DISTINCT c.id, c.name FROM content.dashboard_reports dr JOIN content.report_items i ON " \
              "i.report_id = dr.report_id JOIN content.widgets w ON w.id = i.widget_id JOIN conn.connections c ON " \
              "c.id = w.connection_id WHERE dr.dashboard_id = :id"
    return await fetch_all(conn, sql, {"id": object_id})


async def _warnings(conn: AsyncConnection, principal: Principal, kind: str, obj: dict[str, Any],
                    recipients: list[str], names: dict[str, str]) -> list[dict[str, Any]]:
    """E-11: a recipient without data access on a source still gets the share, but the sharer is told first."""
    warnings = []
    sources = await _connections(conn, kind, obj["id"])
    for uid in recipients:
        person = await principal_for_user(conn, principal, uid)
        for c in sources:
            try:
                scope = await resolve_scope(conn, person, str(c["id"]))
                ok = bool(scope.entities)
            except TelaError:
                ok = False
            if not ok:
                warnings.append({"user_id": uid, "name": names[uid], "connection": c["name"],
                                 "message": f"{names[uid]} has no data access on {c['name']} and will see empty "
                                            "widgets."})
    return warnings


def _share_out(r: dict[str, Any]) -> dict[str, Any]:
    return {"id": str(r["id"]), "object": {"kind": r["object_type"], "id": str(r["object_id"])},
            "user": {"id": str(r["grantee_user_id"]), "name": r.get("display_name")},
            "permission": r["permission"], "created_at": r["created_at"]}


@router.get("/shares")
async def list_shares(object_type: str | None = None, object_id: str | None = None,
                      principal: Principal = Depends(require(Perm.SHARE_CREATE))) -> dict[str, Any]:
    async with tenant_tx(principal.tenant_id) as conn:
        if object_type and object_id:
            if object_type not in TABLES:
                raise TelaError(ErrorCode.VALIDATION_ERROR, "Unknown object type.")
            obj = await _owned(conn, principal, object_type, object_id)
            rows = await fetch_all(conn, "SELECT s.*, u.display_name FROM content.shares s JOIN iam.users u ON "
                                         "u.id = s.grantee_user_id WHERE s.object_type = :o AND s.object_id = :id "
                                         "ORDER BY u.display_name", {"o": object_type, "id": obj["id"]})
        else:
            rows = await fetch_all(conn, "SELECT s.*, u.display_name FROM content.shares s JOIN iam.users u ON "
                                         "u.id = s.grantee_user_id WHERE s.shared_by = CAST(:u AS uuid) ORDER BY "
                                         "s.created_at DESC LIMIT 200", {"u": principal.user_id})
    return {"items": [_share_out(r) for r in rows]}


@router.post("/shares", status_code=201)
async def share(body: ShareIn, request: Request,
                principal: Principal = Depends(require(Perm.SHARE_CREATE))) -> dict[str, Any]:
    if body.permission == "edit":
        raise TelaError(ErrorCode.VALIDATION_ERROR, "Edit sharing is not available yet. Share view-only.")
    kind = body.object.kind
    recipients = sorted(set(body.with_user_ids) - {principal.user_id})
    if not recipients:
        raise TelaError(ErrorCode.VALIDATION_ERROR, "Choose at least one colleague to share with.")
    async with tenant_tx(principal.tenant_id) as conn:
        obj = await _owned(conn, principal, kind, body.object.id)
        if kind == "widget" and is_freehand(obj.get("spec")):
            raise TelaError(ErrorCode.CONFLICT, MSG_FREEHAND_HELD)  # US-166
        try:
            users = await fetch_all(conn, "SELECT id, display_name, status FROM iam.users WHERE "
                                          "id = ANY(CAST(:ids AS uuid[]))", {"ids": recipients})
        except Exception:  # noqa: BLE001
            users = []
        found = {str(u["id"]): u for u in users}
        bad = [r for r in recipients if r not in found or found[r]["status"] != "active"]
        if bad:
            raise TelaError(ErrorCode.VALIDATION_ERROR, "Share only with active colleagues.", {"user_ids": bad})
        warnings = await _warnings(conn, principal, kind, obj, recipients,
                                   {k: v["display_name"] for k, v in found.items()})
        created = []
        for uid in recipients:
            row = await fetch_one(
                conn, "INSERT INTO content.shares (id, tenant_id, object_type, object_id, grantee_user_id, permission, "
                      "shared_by) VALUES (:id, :t, :o, :obj, CAST(:u AS uuid), 'view', :by) ON CONFLICT "
                      "(object_type, object_id, grantee_user_id) DO NOTHING RETURNING id",
                {"id": new_id(), "t": principal.tenant_id, "o": kind, "obj": obj["id"], "u": uid,
                 "by": principal.user_id})
            if row is not None:
                created.append(uid)
        title = obj.get("title") or kind
        await notify(conn, principal.tenant_id, created, kind="share.received",
                     title=f"{principal.display_name} shared {title} with you",
                     body="You see it with your own data scope.", link=LINKS[kind].format(id=obj["id"]),
                     by=principal.user_id)
        await audit.record(conn, tenant_id=principal.tenant_id, action="share.created", object_type=kind,
                           object_id=str(obj["id"]), outcome="success", actor_id=principal.user_id,
                           correlation_id=getattr(request.state, "request_id", None),
                           details={"recipients": len(created), "warnings": len(warnings)})
        rows = await fetch_all(conn, "SELECT s.*, u.display_name FROM content.shares s JOIN iam.users u ON "
                                     "u.id = s.grantee_user_id WHERE s.object_type = :o AND s.object_id = :id",
                               {"o": kind, "id": obj["id"]})
    return {"shared_with": len(created), "already": len(recipients) - len(created), "warnings": warnings,
            "items": [_share_out(r) for r in rows]}


class ShareCheckIn(BaseModel):
    object: ObjectRef
    with_user_ids: list[str] = Field(min_length=1, max_length=50)


@router.post("/shares/check")
async def check_share(body: ShareCheckIn,
                      principal: Principal = Depends(require(Perm.SHARE_CREATE))) -> dict[str, Any]:
    """Per-person warnings before sharing (US-142; E-11). Changes nothing."""
    recipients = sorted(set(body.with_user_ids) - {principal.user_id})
    async with tenant_tx(principal.tenant_id) as conn:
        obj = await _owned(conn, principal, body.object.kind, body.object.id)
        try:
            users = await fetch_all(conn, "SELECT id, display_name FROM iam.users WHERE status = 'active' AND "
                                          "id = ANY(CAST(:ids AS uuid[]))", {"ids": recipients})
        except Exception:  # noqa: BLE001
            users = []
        names = {str(u["id"]): u["display_name"] for u in users}
        warnings = await _warnings(conn, principal, body.object.kind, obj, [r for r in recipients if r in names], names)
    return {"warnings": warnings}


@router.delete("/shares/{share_id}", status_code=204)
async def unshare(share_id: str, request: Request,
                  principal: Principal = Depends(require(Perm.SHARE_CREATE))) -> Response:
    async with tenant_tx(principal.tenant_id) as conn:
        try:
            row = await fetch_one(conn, "SELECT * FROM content.shares WHERE id = CAST(:id AS uuid)", {"id": share_id})
        except Exception:  # noqa: BLE001
            row = None
        if row is None:
            raise TelaError(ErrorCode.NOT_FOUND, "That share does not exist.")
        if str(row["shared_by"]) != principal.user_id:
            await _owned(conn, principal, row["object_type"], str(row["object_id"]))
        await conn.execute(text("DELETE FROM content.shares WHERE id = :id"), {"id": row["id"]})
        await audit.record(conn, tenant_id=principal.tenant_id, action="share.removed", object_type=row["object_type"],
                           object_id=str(row["object_id"]), outcome="success", actor_id=principal.user_id,
                           correlation_id=getattr(request.state, "request_id", None))
    return Response(status_code=204)


@router.get("/directory/users")
async def directory(q: str = "", principal: Principal = Depends(require(Perm.SHARE_CREATE))) -> dict[str, Any]:
    """Active colleagues for the share picker (name search; no e-mail or role details)."""
    async with tenant_tx(principal.tenant_id) as conn:
        rows = await fetch_all(conn, "SELECT id, display_name FROM iam.users WHERE status = 'active' AND id <> "
                                     "CAST(:me AS uuid) AND display_name ILIKE :q ORDER BY display_name LIMIT 20",
                               {"me": principal.user_id, "q": f"%{q.strip()[:60]}%"})
    return {"items": [{"id": str(r["id"]), "name": r["display_name"]} for r in rows]}
