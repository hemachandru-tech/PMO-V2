"""Requests for free-hand access (ENH-008, US-162; FEAT-060).

A person asks for ``ask.freehand`` on one connection ("Request free-hand access" in Ask, US-165); the request lands on
Admin → Access → Free-hand query as a pending row, and every Admin who manages access is notified.

* ``GET/POST /me/permission-requests`` — the person's own requests. One pending request per connection; none while a
  grant is live; only on a connection they can see.
* ``GET /admin/permission-requests`` (``mappings.manage``) — pending first, then decided ones with ``include_decided``.
* ``POST /admin/permission-requests/{id}/approve`` — creates the grant (same checks as Give access).
* ``POST /admin/permission-requests/{id}/decline`` — needs a reason, which the person sees.

The person is notified of either decision. Audit: ``permission.requested``, ``permission.granted`` (with the request
id), ``permission.request_declined``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from tela_api.audit import service as audit
from tela_api.content.common import notify, users_with_permission
from tela_api.core.db import fetch_all, fetch_one, tenant_tx
from tela_api.identity.deps import get_principal, require
from tela_api.identity.grants import WORDS, grant_out, insert_grant, user_sees_connection
from tela_api.identity.permissions import GRANTABLE, Perm
from tela_api.identity.principal import Principal, PrincipalResolver
from tela_common.errors import ErrorCode, TelaError
from tela_common.ids import new_id

me = APIRouter(prefix="/api/v1/me/permission-requests", tags=["me"])
router = APIRouter(prefix="/api/v1/admin/permission-requests", tags=["admin-access"])

DEFAULT_DAYS = 90
SCREEN = "/admin/access/freehand"

_SELECT = """
    SELECT q.id, q.user_id, u.display_name AS user_name, u.email::text AS user_email, q.permission_key,
           q.connection_id, c.name AS connection_name, q.note, q.status, q.requested_at, q.decided_by,
           d.display_name AS decided_by_name, q.decided_at, q.decision_reason, q.grant_id
    FROM iam.permission_requests q
    JOIN iam.users u ON u.id = q.user_id
    JOIN conn.connections c ON c.id = q.connection_id
    LEFT JOIN iam.users d ON d.id = q.decided_by
"""


def _rid(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def request_out(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "user": {"id": str(row["user_id"]), "display_name": row["user_name"], "email": row["user_email"]},
        "permission": row["permission_key"],
        "connection": {"id": str(row["connection_id"]), "name": row["connection_name"]},
        "note": row["note"],
        "status": row["status"],
        "requested_at": _iso(row["requested_at"]),
        "decided_by": {"id": str(row["decided_by"]), "display_name": row["decided_by_name"]}
        if row["decided_by"] else None,
        "decided_at": _iso(row["decided_at"]),
        "decision_reason": row["decision_reason"],
        "grant_id": str(row["grant_id"]) if row["grant_id"] else None,
    }


async def _pending(conn: AsyncConnection, request_id: str) -> dict[str, Any]:
    try:
        row = await fetch_one(conn, _SELECT + " WHERE q.id = CAST(:id AS uuid) FOR UPDATE OF q", {"id": request_id})
    except Exception:  # noqa: BLE001 - malformed id
        row = None
    if row is None:
        raise TelaError(ErrorCode.NOT_FOUND, "Request not found.")
    if row["status"] != "pending":
        raise TelaError(ErrorCode.CONFLICT, f"This request was already {row['status']}.", {"status": row["status"]})
    return row


# --------------------------------------------------------------------------- the person's own requests


class AccessRequestIn(BaseModel):
    connection_id: str
    permission: str = Perm.ASK_FREEHAND.value
    note: str | None = Field(default=None, max_length=500)


@me.get("")
async def my_requests(principal: Principal = Depends(get_principal)) -> dict[str, Any]:
    async with tenant_tx(principal.tenant_id) as conn:
        rows = await fetch_all(conn, _SELECT + " WHERE q.user_id = CAST(:u AS uuid) ORDER BY q.requested_at DESC "
                                               "LIMIT 50", {"u": principal.user_id})
    return {"items": [request_out(r) for r in rows]}


@me.post("", status_code=201)
async def create_request(body: AccessRequestIn, request: Request,
                         principal: Principal = Depends(get_principal)) -> dict[str, Any]:
    if body.permission not in GRANTABLE:
        raise TelaError(ErrorCode.VALIDATION_ERROR, f"'{body.permission}' can't be requested.",
                        {"permission": body.permission})
    if principal.can(body.permission, body.connection_id):
        raise TelaError(ErrorCode.CONFLICT, "You already have free-hand access on this connection.")
    note = (body.note or "").strip() or None
    async with tenant_tx(principal.tenant_id) as conn:
        try:
            connection = await fetch_one(
                conn, "SELECT id, name FROM conn.connections WHERE id = CAST(:c AS uuid) AND deleted_at IS NULL",
                {"c": body.connection_id})
        except Exception:  # noqa: BLE001 - malformed id
            connection = None
        if connection is None or not await user_sees_connection(conn, principal.user_id, body.connection_id):
            raise TelaError(ErrorCode.NOT_FOUND, "Connection not found.")
        open_request = await fetch_one(
            conn, "SELECT id FROM iam.permission_requests WHERE user_id = CAST(:u AS uuid) AND permission_key = :p "
                  "AND connection_id = CAST(:c AS uuid) AND status = 'pending'",
            {"u": principal.user_id, "p": body.permission, "c": body.connection_id})
        if open_request is not None:
            raise TelaError(ErrorCode.CONFLICT, "You have already asked; an Admin will look at it.",
                            {"request_id": str(open_request["id"])})
        request_id = new_id()
        await conn.execute(
            text("INSERT INTO iam.permission_requests (id, tenant_id, user_id, permission_key, connection_id, note) "
                 "VALUES (:id, :t, CAST(:u AS uuid), :p, CAST(:c AS uuid), :n)"),
            {"id": request_id, "t": principal.tenant_id, "u": principal.user_id, "p": body.permission,
             "c": body.connection_id, "n": note})
        await audit.record(
            conn, tenant_id=principal.tenant_id, action="permission.requested", object_type="permission_request",
            object_id=str(request_id), outcome="success", correlation_id=_rid(request), actor_id=principal.user_id,
            severity="security", details={"permission": body.permission, "connection_id": body.connection_id})
        admins = [u for u in await users_with_permission(conn, Perm.MAPPINGS_MANAGE) if u != principal.user_id]
        words = WORDS.get(body.permission, body.permission)
        await notify(conn, principal.tenant_id, admins, kind="permission.requested",
                     title=f"{principal.display_name} asks to {words} on {connection['name']}",
                     body=note or "No note given.", link=f"{SCREEN}?view=requests", by=principal.user_id)
        row = await fetch_one(conn, _SELECT + " WHERE q.id = :id", {"id": request_id})
    assert row is not None
    return request_out(row)


# --------------------------------------------------------------------------- Admin decisions


@router.get("")
async def list_requests(include_decided: bool = False,
                        principal: Principal = Depends(require(Perm.MAPPINGS_MANAGE))) -> dict[str, Any]:
    """Pending requests, oldest first; with ``include_decided`` the last 200 decided ones follow, newest first."""
    async with tenant_tx(principal.tenant_id) as conn:
        rows = await fetch_all(conn, _SELECT + " WHERE c.deleted_at IS NULL AND q.status = 'pending' "
                                               "ORDER BY q.requested_at")
        if include_decided:
            rows += await fetch_all(conn, _SELECT + " WHERE c.deleted_at IS NULL AND q.status <> 'pending' "
                                                    "ORDER BY q.decided_at DESC LIMIT 200")
    return {"items": [request_out(r) for r in rows]}


class AccessApproveIn(BaseModel):
    expires_at: datetime | None = None
    reason: str | None = Field(default=None, max_length=500)


@router.post("/{request_id}/approve")
async def approve_request(request_id: str, body: AccessApproveIn, request: Request,
                          principal: Principal = Depends(require(Perm.MAPPINGS_MANAGE))) -> dict[str, Any]:
    """Give access as asked: the end date defaults to 90 days, the reason to the person's note."""
    expires = body.expires_at or datetime.now(UTC) + timedelta(days=DEFAULT_DAYS)
    async with tenant_tx(principal.tenant_id) as conn:
        q = await _pending(conn, request_id)
        reason = (body.reason or "").strip() or q["note"] or "Requested from Ask"
        grant = await insert_grant(conn, principal, user_id=str(q["user_id"]), connection_id=str(q["connection_id"]),
                                   permission=q["permission_key"], reason=reason, expires_at=expires,
                                   correlation_id=_rid(request), request_id=request_id)
        await conn.execute(
            text("UPDATE iam.permission_requests SET status = 'approved', decided_by = CAST(:by AS uuid), "
                 "decided_at = now(), grant_id = :g WHERE id = CAST(:id AS uuid)"),
            {"id": request_id, "by": principal.user_id, "g": grant["id"]})
        words = WORDS.get(q["permission_key"], q["permission_key"])
        await notify(conn, principal.tenant_id, [str(q["user_id"])], kind="permission.request_approved",
                     title=f"You can now {words} on {q['connection_name']}",
                     body=f"Until {grant['expires_at']:%d %b %Y}. Approved by {principal.display_name}.",
                     link="/ask", by=principal.user_id)
        row = await fetch_one(conn, _SELECT + " WHERE q.id = CAST(:id AS uuid)", {"id": request_id})
    resolver: PrincipalResolver = request.app.state.principal_resolver
    resolver.invalidate(user_id=str(q["user_id"]))
    assert row is not None
    return {"request": request_out(row), "grant": grant_out(grant)}


class AccessDeclineIn(BaseModel):
    reason: str = Field(min_length=1, max_length=500)


@router.post("/{request_id}/decline")
async def decline_request(request_id: str, body: AccessDeclineIn, request: Request,
                          principal: Principal = Depends(require(Perm.MAPPINGS_MANAGE))) -> dict[str, Any]:
    reason = body.reason.strip()
    if not reason:
        raise TelaError(ErrorCode.VALIDATION_ERROR, "Say why, so the person knows what to do next.",
                        {"field": "reason"})
    async with tenant_tx(principal.tenant_id) as conn:
        q = await _pending(conn, request_id)
        await conn.execute(
            text("UPDATE iam.permission_requests SET status = 'declined', decided_by = CAST(:by AS uuid), "
                 "decided_at = now(), decision_reason = :r WHERE id = CAST(:id AS uuid)"),
            {"id": request_id, "by": principal.user_id, "r": reason})
        await audit.record(
            conn, tenant_id=principal.tenant_id, action="permission.request_declined",
            object_type="permission_request", object_id=request_id, outcome="success", correlation_id=_rid(request),
            actor_id=principal.user_id, severity="security",
            details={"user_id": str(q["user_id"]), "permission": q["permission_key"],
                     "connection_id": str(q["connection_id"])})
        words = WORDS.get(q["permission_key"], q["permission_key"])
        await notify(conn, principal.tenant_id, [str(q["user_id"])], kind="permission.request_declined",
                     title=f"Your request to {words} on {q['connection_name']} was declined",
                     body=reason, link="/ask", by=principal.user_id)
        row = await fetch_one(conn, _SELECT + " WHERE q.id = CAST(:id AS uuid)", {"id": request_id})
    assert row is not None
    return request_out(row)
