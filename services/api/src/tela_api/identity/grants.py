"""Per-user permission grants (ENH-008, US-161; FEAT-060).

One permission given to one person on one connection, outside the roles (today ``ask.freehand``, which no role
holds, so Admins get it by grant too).

* ``GET/POST/DELETE /admin/permission-grants`` (``mappings.manage``). A grant needs a reason and an end date (at most
  a year away) and is refused for a connection the person cannot see: no access policy covers their roles.
* ``PATCH /admin/permission-grants/{id}`` extends (or shortens) a live grant (US-162); ``GET .../connections?user_id=``
  lists the connections with whether the person can see each, for the Give access dialog.
* ``last_used_at`` is set by ``mark_used`` when a free-hand question runs (US-163).
* A grant ends at its end date with no job: the check (``Principal.can``) compares the date at use.
* ``POST /internal/permission-grants/sweep`` — the daily ``permission_grant_sweep`` job: reminds the person and the
  Admin who gave it seven days before the end, and records ``permission.expired`` once the end has passed.
* A person deprovisioned by SCIM, or found deprovisioned by the reconcile, loses their grants
  (``remove_for_user``).

Audit: ``permission.granted``, ``permission.extended``, ``permission.removed``, ``permission.expired``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from tela_api.audit import service as audit
from tela_api.content.common import notify
from tela_api.content.exports import require_worker
from tela_api.core.db import fetch_all, fetch_one, tenant_tx
from tela_api.identity.deps import require
from tela_api.identity.permissions import GRANTABLE, Perm
from tela_api.identity.principal import Principal, PrincipalResolver
from tela_common.errors import ErrorCode, TelaError
from tela_common.ids import new_id

router = APIRouter(prefix="/api/v1/admin/permission-grants", tags=["admin-access"])
internal = APIRouter(prefix="/api/v1/internal/permission-grants", tags=["internal"], include_in_schema=False)

MAX_DAYS = 366
REMIND_DAYS = 7
WORDS: dict[str, str] = {Perm.ASK_FREEHAND.value: "ask free-hand questions"}
# Seeing everything on every connection comes with these role permissions (as in ``GET /connections``).
_SEES_ALL = (Perm.CONNECTIONS_MANAGE, Perm.POLICIES_MANAGE)

_SELECT = """
    SELECT g.id, g.user_id, u.display_name AS user_name, u.email::text AS user_email, g.permission_key,
           g.connection_id, c.name AS connection_name, g.reason, g.granted_by, b.display_name AS granted_by_name,
           g.granted_at, g.expires_at, g.removed_at, g.removed_by, r.display_name AS removed_by_name,
           g.removed_reason, g.last_used_at
    FROM iam.permission_grants g
    JOIN iam.users u ON u.id = g.user_id
    JOIN conn.connections c ON c.id = g.connection_id
    LEFT JOIN iam.users b ON b.id = g.granted_by
    LEFT JOIN iam.users r ON r.id = g.removed_by
"""


def _rid(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def status_of(row: dict[str, Any], now: datetime | None = None) -> str:
    if row["removed_at"] is not None:
        return "expired" if row["removed_reason"] == "expired" else "removed"
    return "active" if row["expires_at"] > (now or datetime.now(UTC)) else "expired"


def grant_out(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "user": {"id": str(row["user_id"]), "display_name": row["user_name"], "email": row["user_email"]},
        "permission": row["permission_key"],
        "connection": {"id": str(row["connection_id"]), "name": row["connection_name"]},
        "reason": row["reason"],
        "granted_by": {"id": str(row["granted_by"]), "display_name": row["granted_by_name"]}
        if row["granted_by"] else None,
        "granted_at": _iso(row["granted_at"]),
        "expires_at": _iso(row["expires_at"]),
        "removed_at": _iso(row["removed_at"]),
        "removed_by": {"id": str(row["removed_by"]), "display_name": row["removed_by_name"]}
        if row["removed_by"] else None,
        "removed_reason": row["removed_reason"],
        "last_used_at": _iso(row["last_used_at"]),
        "status": status_of(row),
    }


async def user_sees_connection(conn: AsyncConnection, user_id: str, connection_id: str) -> bool:
    """The person's roles reach this connection: a role that sees every connection, or an active access policy."""
    row = await fetch_one(
        conn,
        """
        WITH r AS (SELECT role_id FROM iam.f_effective_roles(CAST(:u AS uuid)))
        SELECT EXISTS (SELECT 1 FROM iam.role_permissions p WHERE p.role_id IN (SELECT role_id FROM r)
                         AND p.permission_key = ANY(CAST(:all AS text[])))
            OR EXISTS (SELECT 1 FROM iam.scope_policies s WHERE s.role_id IN (SELECT role_id FROM r)
                         AND s.connection_id = CAST(:c AS uuid) AND s.is_active) AS ok
        """,
        {"u": user_id, "c": connection_id, "all": list(_SEES_ALL)},
    )
    return bool(row and row["ok"])


async def grants_for(conn: AsyncConnection, user_id: str, connection_id: str | None = None) -> list[dict[str, Any]]:
    """The person's grants that still work (Effective Access), optionally on one connection."""
    rows = await fetch_all(
        conn, _SELECT + " WHERE g.user_id = CAST(:u AS uuid) AND g.removed_at IS NULL AND g.expires_at > now() "
                        "AND c.deleted_at IS NULL AND (CAST(:c AS uuid) IS NULL OR g.connection_id = CAST(:c AS uuid)) "
                        "ORDER BY c.name, g.permission_key", {"u": user_id, "c": connection_id})
    return [grant_out(r) for r in rows]


async def remove_for_user(conn: AsyncConnection, tenant_id: str, user_id: str, *, reason: str,
                          correlation_id: str | None, actor_id: str | None = None) -> int:
    """End every open grant of this person (SCIM deprovisioning, reconcile). Each removal is audited."""
    rows = await fetch_all(
        conn, "UPDATE iam.permission_grants SET removed_at = now(), removed_by = CAST(:by AS uuid), "
              "removed_reason = :why WHERE user_id = CAST(:u AS uuid) AND removed_at IS NULL "
              "RETURNING id, permission_key, connection_id",
        {"u": user_id, "by": actor_id, "why": reason})
    for r in rows:
        await audit.record(
            conn, tenant_id=tenant_id, action="permission.removed", object_type="permission_grant",
            object_id=str(r["id"]), outcome="success", correlation_id=correlation_id, actor_id=actor_id,
            severity="security", details={"user_id": user_id, "permission": r["permission_key"],
                                          "connection_id": str(r["connection_id"]), "reason": reason})
    return len(rows)


async def mark_used(conn: AsyncConnection, user_id: str, permission: str, connection_id: str) -> None:
    """Record that a live grant was used just now (shown as Last used on the Free-hand query screen)."""
    await conn.execute(
        text("UPDATE iam.permission_grants SET last_used_at = now() WHERE user_id = CAST(:u AS uuid) "
             "AND permission_key = :p AND connection_id = CAST(:c AS uuid) AND removed_at IS NULL "
             "AND expires_at > now()"), {"u": user_id, "p": permission, "c": connection_id})


def check_expiry(value: datetime, now: datetime | None = None) -> datetime:
    """The end date as an aware datetime: in the future and at most a year away."""
    now = now or datetime.now(UTC)
    expires = value if value.tzinfo else value.replace(tzinfo=UTC)
    if expires <= now:
        raise TelaError(ErrorCode.VALIDATION_ERROR, "The end date must be in the future.", {"field": "expires_at"})
    if expires > now + timedelta(days=MAX_DAYS):
        raise TelaError(ErrorCode.VALIDATION_ERROR, "A grant can last at most a year.", {"field": "expires_at"})
    return expires


async def insert_grant(conn: AsyncConnection, principal: Principal, *, user_id: str, connection_id: str,
                       permission: str, reason: str, expires_at: datetime, correlation_id: str | None,
                       request_id: str | None = None) -> dict[str, Any]:
    """Check and create one grant (Give access, or an approved request). Returns the stored row."""
    if permission not in GRANTABLE:
        raise TelaError(ErrorCode.VALIDATION_ERROR, f"'{permission}' can't be given to one person.",
                        {"permission": permission})
    reason = reason.strip()
    if not reason:
        raise TelaError(ErrorCode.VALIDATION_ERROR, "Say why this person needs it.", {"field": "reason"})
    now = datetime.now(UTC)
    expires = check_expiry(expires_at, now)
    try:
        user = await fetch_one(conn, "SELECT id, display_name, status FROM iam.users WHERE id = CAST(:u AS uuid)",
                               {"u": user_id})
        connection = await fetch_one(
            conn, "SELECT id, name FROM conn.connections WHERE id = CAST(:c AS uuid) AND deleted_at IS NULL",
            {"c": connection_id})
    except Exception:  # noqa: BLE001 - malformed id
        user = connection = None
    if user is None or user["status"] not in ("active", "pending"):
        raise TelaError(ErrorCode.NOT_FOUND, "That person was not found.")
    if connection is None:
        raise TelaError(ErrorCode.NOT_FOUND, "Connection not found.")
    if not await user_sees_connection(conn, user_id, connection_id):
        raise TelaError(
            ErrorCode.VALIDATION_ERROR,
            f"{user['display_name']} can't see {connection['name']}: no access policy on it covers their roles. "
            "Give one of their roles access to this connection first.",
            {"reason": "connection_not_visible", "user_id": user_id, "connection_id": connection_id})
    open_grant = await fetch_one(
        conn, "SELECT id, expires_at FROM iam.permission_grants WHERE user_id = CAST(:u AS uuid) "
              "AND permission_key = :p AND connection_id = CAST(:c AS uuid) AND removed_at IS NULL",
        {"u": user_id, "p": permission, "c": connection_id})
    if open_grant is not None and open_grant["expires_at"] > now:
        raise TelaError(ErrorCode.CONFLICT,
                        f"{user['display_name']} already has this on {connection['name']} until "
                        f"{open_grant['expires_at']:%d %b %Y}.", {"grant_id": str(open_grant["id"])})
    if open_grant is not None:  # ended by its date: close it so the new one is the only open grant
        await conn.execute(text("UPDATE iam.permission_grants SET removed_at = now(), removed_reason = 'expired' "
                                "WHERE id = :id"), {"id": open_grant["id"]})
    grant_id = new_id()
    await conn.execute(
        text("INSERT INTO iam.permission_grants (id, tenant_id, user_id, permission_key, connection_id, reason, "
             "granted_by, expires_at) VALUES (:id, :t, CAST(:u AS uuid), :p, CAST(:c AS uuid), :r, "
             "CAST(:by AS uuid), :exp)"),
        {"id": grant_id, "t": principal.tenant_id, "u": user_id, "p": permission,
         "c": connection_id, "r": reason, "by": principal.user_id, "exp": expires})
    details = {"user_id": user_id, "permission": permission, "connection_id": connection_id,
               "expires_at": expires.isoformat()}
    if request_id:
        details["request_id"] = request_id
    await audit.record(
        conn, tenant_id=principal.tenant_id, action="permission.granted", object_type="permission_grant",
        object_id=str(grant_id), outcome="success", correlation_id=correlation_id, actor_id=principal.user_id,
        severity="security", details=details)
    row = await fetch_one(conn, _SELECT + " WHERE g.id = :id", {"id": grant_id})
    assert row is not None
    return row


def _invalidate(request: Request, user_id: str) -> None:
    resolver: PrincipalResolver = request.app.state.principal_resolver
    resolver.invalidate(user_id=user_id)


# --------------------------------------------------------------------------- admin API


@router.get("")
async def list_grants(
    user_id: str | None = None,
    connection_id: str | None = None,
    permission: str | None = None,
    include_ended: bool = False,
    principal: Principal = Depends(require(Perm.MAPPINGS_MANAGE)),
) -> dict[str, Any]:
    """Grants, newest first. Ended ones (expired or removed) only with ``include_ended``."""
    where = ["(CAST(:u AS uuid) IS NULL OR g.user_id = CAST(:u AS uuid))",
             "(CAST(:c AS uuid) IS NULL OR g.connection_id = CAST(:c AS uuid))",
             "(CAST(:p AS text) IS NULL OR g.permission_key = CAST(:p AS text))", "c.deleted_at IS NULL"]
    if not include_ended:
        where.append("g.removed_at IS NULL AND g.expires_at > now()")
    try:
        async with tenant_tx(principal.tenant_id) as conn:
            rows = await fetch_all(conn, _SELECT + " WHERE " + " AND ".join(where) + " ORDER BY g.granted_at DESC",
                                   {"u": user_id, "c": connection_id, "p": permission})
    except Exception as exc:  # malformed id
        raise TelaError(ErrorCode.VALIDATION_ERROR, "Unknown person or connection.") from exc
    return {"items": [grant_out(r) for r in rows]}


class GrantIn(BaseModel):
    user_id: str
    connection_id: str
    permission: str = Perm.ASK_FREEHAND.value
    reason: str = Field(min_length=1, max_length=500)
    expires_at: datetime


@router.post("", status_code=201)
async def create_grant(body: GrantIn, request: Request,
                       principal: Principal = Depends(require(Perm.MAPPINGS_MANAGE))) -> dict[str, Any]:
    async with tenant_tx(principal.tenant_id) as conn:
        row = await insert_grant(conn, principal, user_id=body.user_id, connection_id=body.connection_id,
                                 permission=body.permission, reason=body.reason, expires_at=body.expires_at,
                                 correlation_id=_rid(request))
    _invalidate(request, body.user_id)
    return grant_out(row)


@router.get("/connections")
async def connections_for(user_id: str,
                          principal: Principal = Depends(require(Perm.MAPPINGS_MANAGE))) -> dict[str, Any]:
    """Every connection, with whether this person can see it and their live free-hand grant on it (Give access)."""
    async with tenant_tx(principal.tenant_id) as conn:
        try:
            user = await fetch_one(conn, "SELECT id FROM iam.users WHERE id = CAST(:u AS uuid)", {"u": user_id})
        except Exception:  # noqa: BLE001 - malformed id
            user = None
        if user is None:
            raise TelaError(ErrorCode.NOT_FOUND, "That person was not found.")
        rows = await fetch_all(conn, "SELECT id, name FROM conn.connections WHERE deleted_at IS NULL ORDER BY name")
        live = {g["connection"]["id"]: g for g in await grants_for(conn, user_id)
                if g["permission"] == Perm.ASK_FREEHAND}
        items = []
        for r in rows:
            cid = str(r["id"])
            g = live.get(cid)
            items.append({"id": cid, "name": r["name"], "visible": await user_sees_connection(conn, user_id, cid),
                          "grant": {"id": g["id"], "expires_at": g["expires_at"]} if g else None})
    return {"items": items}


class GrantExtendIn(BaseModel):
    expires_at: datetime


@router.patch("/{grant_id}")
async def extend_grant(grant_id: str, body: GrantExtendIn, request: Request,
                       principal: Principal = Depends(require(Perm.MAPPINGS_MANAGE))) -> dict[str, Any]:
    """Change the end date of a live grant (at most a year from today). The reminder is sent again for the new date."""
    expires = check_expiry(body.expires_at)
    async with tenant_tx(principal.tenant_id) as conn:
        try:
            old = await fetch_one(conn, "SELECT user_id, permission_key, connection_id, expires_at "
                                        "FROM iam.permission_grants WHERE id = CAST(:id AS uuid) "
                                        "AND removed_at IS NULL AND expires_at > now()", {"id": grant_id})
        except Exception:  # noqa: BLE001 - malformed id
            old = None
        if old is None:
            raise TelaError(ErrorCode.NOT_FOUND, "Grant not found, or it has already ended. Give access again instead.")
        await conn.execute(
            text("UPDATE iam.permission_grants SET expires_at = :exp, reminded_at = NULL WHERE id = CAST(:id AS uuid)"),
            {"id": grant_id, "exp": expires})
        await audit.record(
            conn, tenant_id=principal.tenant_id, action="permission.extended", object_type="permission_grant",
            object_id=grant_id, outcome="success", correlation_id=_rid(request), actor_id=principal.user_id,
            severity="security", details={"user_id": str(old["user_id"]), "permission": old["permission_key"],
                                          "connection_id": str(old["connection_id"]),
                                          "from": old["expires_at"].isoformat(), "to": expires.isoformat()})
        row = await fetch_one(conn, _SELECT + " WHERE g.id = CAST(:id AS uuid)", {"id": grant_id})
    _invalidate(request, str(old["user_id"]))
    assert row is not None
    return grant_out(row)


@router.delete("/{grant_id}", status_code=204)
async def remove_grant(grant_id: str, request: Request,
                       principal: Principal = Depends(require(Perm.MAPPINGS_MANAGE))) -> None:
    """Takes effect at once."""
    async with tenant_tx(principal.tenant_id) as conn:
        try:
            row = await fetch_one(
                conn, "UPDATE iam.permission_grants SET removed_at = now(), removed_by = CAST(:by AS uuid), "
                      "removed_reason = 'admin' WHERE id = CAST(:id AS uuid) AND removed_at IS NULL "
                      "RETURNING user_id, permission_key, connection_id",
                {"id": grant_id, "by": principal.user_id})
        except Exception:  # noqa: BLE001 - malformed id
            row = None
        if row is None:
            raise TelaError(ErrorCode.NOT_FOUND, "Grant not found, or already removed.")
        await audit.record(
            conn, tenant_id=principal.tenant_id, action="permission.removed", object_type="permission_grant",
            object_id=grant_id, outcome="success", correlation_id=_rid(request), actor_id=principal.user_id,
            severity="security", details={"user_id": str(row["user_id"]), "permission": row["permission_key"],
                                          "connection_id": str(row["connection_id"]), "reason": "admin"})
    _invalidate(request, str(row["user_id"]))


# --------------------------------------------------------------------------- daily sweep


async def sweep_tenant(conn: AsyncConnection, tenant_id: str) -> dict[str, int]:
    """Reminders seven days before the end; ``permission.expired`` once the end has passed (each only once)."""
    due = await fetch_all(
        conn, _SELECT + " WHERE g.removed_at IS NULL AND g.reminded_at IS NULL AND g.expires_at > now() "
                        "AND g.expires_at <= now() + make_interval(days => :d) AND c.deleted_at IS NULL",
        {"d": REMIND_DAYS})
    for g in due:
        words = WORDS.get(g["permission_key"], g["permission_key"])
        when = f"{g['expires_at']:%d %b %Y}"
        await notify(conn, tenant_id, [str(g["user_id"])], kind="permission.expiring",
                     title=f"Your access to {words} on {g['connection_name']} ends on {when}",
                     body="Ask your Admin to extend it if you still need it.", link="/ask")
        if g["granted_by"] and g["granted_by"] != g["user_id"]:
            await notify(conn, tenant_id, [str(g["granted_by"])], kind="permission.expiring",
                         title=f"{g['user_name']}'s access to {words} on {g['connection_name']} ends on {when}",
                         body=f"Reason given: {g['reason']}", link="/admin/access")
        await conn.execute(text("UPDATE iam.permission_grants SET reminded_at = now() WHERE id = :id"),
                           {"id": g["id"]})
    ended = await fetch_all(
        conn, "UPDATE iam.permission_grants SET expiry_recorded_at = now() WHERE removed_at IS NULL "
              "AND expiry_recorded_at IS NULL AND expires_at <= now() "
              "RETURNING id, user_id, permission_key, connection_id, expires_at")
    for g in ended:
        await audit.record(
            conn, tenant_id=tenant_id, action="permission.expired", object_type="permission_grant",
            object_id=str(g["id"]), outcome="success", correlation_id=None, severity="security",
            details={"user_id": str(g["user_id"]), "permission": g["permission_key"],
                     "connection_id": str(g["connection_id"]), "expires_at": g["expires_at"].isoformat()})
    return {"reminded": len(due), "expired": len(ended)}


@internal.post("/sweep")
async def sweep(_: dict[str, Any] = Depends(require_worker)) -> dict[str, int]:
    async with tenant_tx(None) as conn:
        tenants = [str(r["tenant_id"]) for r in await fetch_all(conn, "SELECT tenant_id FROM "
                                                                      "iam.f_permission_grant_tenants()")]
    reminded = expired = 0
    for tenant_id in tenants:
        async with tenant_tx(tenant_id) as conn:
            out = await sweep_tenant(conn, tenant_id)
        reminded += out["reminded"]
        expired += out["expired"]
    return {"tenants": len(tenants), "reminded": reminded, "expired": expired}
