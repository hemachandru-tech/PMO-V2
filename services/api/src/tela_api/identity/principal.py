"""Principal resolution (FEAT-001, FEAT-002; REQ-001, REQ-003, REQ-008).

Validated token claims → tenant (by trusted issuer) → internal user (by
immutable issuer + subject, never e-mail alone) → group memberships from the
token → effective roles (protected roles only when the mapping is approved) →
permission keys, plus the person's live per-connection grants (US-161). A user
with no mapped system role gets no access (deny by default, MSG-001).
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from tela_api.audit import service as audit
from tela_api.core.db import fetch_all, fetch_one, tenant_tx
from tela_api.identity.permissions import SYSTEM_LEVELS
from tela_common.errors import ErrorCode, TelaError
from tela_common.ids import new_id
from tela_common.logging import get_logger

log = get_logger(__name__)

NO_ACCESS_MESSAGE = "You don't have access to Tela yet. Ask your PMO admin."
LOGIN_AUDIT_GAP = timedelta(minutes=30)


@dataclass(frozen=True)
class Grant:
    """One permission given to this person on one connection (US-161). It ends at ``expires_at``, checked at use."""

    permission: str
    connection_id: str
    expires_at: datetime

    def live(self, now: datetime | None = None) -> bool:
        return self.expires_at > (now or datetime.now(UTC))


@dataclass(frozen=True)
class Principal:
    user_id: str
    tenant_id: str
    display_name: str
    email: str | None
    level: str
    roles: tuple[str, ...]
    role_ids: tuple[str, ...]
    audience_roles: tuple[str, ...]
    permissions: frozenset[str]
    groups: tuple[str, ...]
    scope_attributes: dict[str, dict[str, list[str]]] = field(default_factory=dict)
    locale: str | None = None
    time_zone: str | None = None
    groups_overage: bool = False
    grants: tuple[Grant, ...] = ()

    def has(self, permission: str) -> bool:
        return permission in self.permissions

    def can(self, permission: str, connection_id: str) -> bool:
        """Role permission, or a live grant of it on this connection. ``ask.freehand`` is only ever granted."""
        return permission in self.permissions or any(
            g.permission == permission and g.connection_id == connection_id and g.live() for g in self.grants)

    def granted_connections(self, permission: str) -> list[str]:
        return sorted({g.connection_id for g in self.grants if g.permission == permission and g.live()})


async def live_grants(conn: AsyncConnection, user_id: str) -> tuple[Grant, ...]:
    rows = await fetch_all(
        conn, "SELECT permission_key, connection_id, expires_at FROM iam.permission_grants g "
              "JOIN conn.connections c ON c.id = g.connection_id AND c.deleted_at IS NULL "
              "WHERE g.user_id = CAST(:u AS uuid) AND g.removed_at IS NULL AND g.expires_at > now() "
              "ORDER BY g.expires_at", {"u": user_id})
    return tuple(Grant(r["permission_key"], str(r["connection_id"]), r["expires_at"]) for r in rows)


def _claim_groups(claims: dict[str, Any], claim_name: str) -> tuple[list[str], bool]:
    raw = claims.get(claim_name)
    overage = "_claim_names" in claims and claim_name in claims.get("_claim_names", {})
    if raw is None:
        return [], overage
    if isinstance(raw, str):
        raw = [raw]
    groups = sorted({str(g).strip().lstrip("/") for g in raw if str(g).strip()})
    return groups, overage


def _merge_scope_attributes(roles: list[dict[str, Any]]) -> dict[str, dict[str, list[str]]]:
    """Scope attributes per role key, merged across all group mappings granting that role."""
    merged: dict[str, dict[str, list[str]]] = {}
    for r in roles:
        target = merged.setdefault(r["role_key"], {})
        for attr, values in (r["scope_attributes"] or {}).items():
            vals = values if isinstance(values, list) else [values]
            target[attr] = sorted({*target.get(attr, []), *map(str, vals)})
    return merged


def _level(roles: list[dict[str, Any]]) -> str | None:
    system_keys = {r["role_key"] for r in roles if r["kind"] == "system"}
    return next((lvl for lvl in SYSTEM_LEVELS if lvl in system_keys), None)


def _principal(tenant_id: str, user: dict[str, Any], roles: list[dict[str, Any]], perms: list[dict[str, Any]],
               level: str, groups: list[str], overage: bool, grants: tuple[Grant, ...] = ()) -> Principal:
    system_keys = {r["role_key"] for r in roles if r["kind"] == "system"}
    audience = sorted({r["role_key"] for r in roles if r["kind"] == "audience"})
    return Principal(
        user_id=str(user["id"]),
        tenant_id=tenant_id,
        display_name=user["display_name"],
        email=user["email"],
        level=level,
        roles=tuple(sorted(system_keys | set(audience))),
        role_ids=tuple(sorted({str(r["role_id"]) for r in roles})),
        audience_roles=tuple(audience),
        permissions=frozenset(p["permission_key"] for p in perms),
        groups=tuple(groups),
        scope_attributes=_merge_scope_attributes(roles),
        locale=user.get("locale"),
        time_zone=user.get("time_zone"),
        groups_overage=overage,
        grants=grants,
    )


async def principal_for_user(conn: AsyncConnection, tenant_id: str, user_id: str) -> Principal | None:
    """The person's access as it stands now, from their stored group memberships (no token).

    Background jobs that act for someone (exports, REQ-108) re-check access with this when they run, so a person
    suspended, deprovisioned or removed from their groups after asking gets nothing. ``None`` means no access.
    """
    user = await fetch_one(conn, "SELECT id, display_name, email::text AS email, status, locale, time_zone "
                                 "FROM iam.users WHERE id = CAST(:u AS uuid)", {"u": user_id})
    if user is None or user["status"] != "active":
        return None
    roles = await fetch_all(conn, "SELECT role_id, role_key, kind, scope_attributes FROM iam.f_effective_roles(:uid)",
                            {"uid": user["id"]})
    level = _level(roles)
    if level is None:
        return None
    perms = await fetch_all(conn, "SELECT DISTINCT permission_key FROM iam.role_permissions "
                                  "WHERE role_id = ANY(CAST(:ids AS uuid[]))",
                            {"ids": sorted({str(r["role_id"]) for r in roles})})
    groups = await fetch_all(conn, "SELECT g.external_group_id FROM iam.idp_group_members m JOIN iam.idp_groups g "
                                   "ON g.id = m.group_id WHERE m.user_id = :uid ORDER BY 1", {"uid": user["id"]})
    return _principal(tenant_id, user, roles, perms, level, [g["external_group_id"] for g in groups], False,
                      await live_grants(conn, str(user["id"])))


class PrincipalResolver:
    def __init__(self, *, groups_claim: str = "groups", cache_seconds: int = 60) -> None:
        self.groups_claim = groups_claim
        self.cache_seconds = cache_seconds
        self._cache: dict[str, tuple[float, Principal]] = {}

    def _cache_key(self, claims: dict[str, Any]) -> str:
        groups, _ = _claim_groups(claims, self.groups_claim)
        digest = hashlib.sha256(json.dumps(groups).encode()).hexdigest()[:16]
        return f"{claims['iss']}|{claims['sub']}|{digest}"

    def invalidate(self, tenant_id: str | None = None, user_id: str | None = None) -> None:
        if tenant_id is None and user_id is None:
            self._cache.clear()
            return
        for key, (_, p) in list(self._cache.items()):
            if (tenant_id and p.tenant_id == tenant_id) or (user_id and p.user_id == user_id):
                self._cache.pop(key, None)

    async def resolve(self, claims: dict[str, Any], *, request_id: str | None, client_ip: str | None) -> Principal:
        key = self._cache_key(claims)
        hit = self._cache.get(key)
        if hit and time.monotonic() - hit[0] < self.cache_seconds:
            return hit[1]
        principal = await self._resolve_uncached(claims, request_id=request_id, client_ip=client_ip)
        self._cache[key] = (time.monotonic(), principal)
        return principal

    async def _resolve_uncached(
        self, claims: dict[str, Any], *, request_id: str | None, client_ip: str | None
    ) -> Principal:
        async with tenant_tx(None) as conn:
            tenant = await fetch_one(
                conn, "SELECT id, status FROM iam.f_tenant_for_issuer(:iss)", {"iss": claims["iss"]}
            )
        if tenant is None or tenant["status"] != "active":
            raise TelaError(ErrorCode.NO_ACCESS, NO_ACCESS_MESSAGE, {"reason": "tenant_not_configured"})
        tenant_id = str(tenant["id"])
        groups, overage = _claim_groups(claims, self.groups_claim)

        # Denials are audited inside the transaction but raised only after it commits:
        # raising inside it would roll the audit event back.
        async with tenant_tx(tenant_id) as conn:
            user, is_new = await self._upsert_user(conn, tenant_id, claims)
            revoked = user.get("sessions_revoked_at")
            if user["status"] in ("suspended", "deprovisioned"):
                await audit.record(
                    conn, tenant_id=tenant_id, action="auth.login", object_type="user", object_id=str(user["id"]),
                    outcome="deny", correlation_id=request_id, actor_id=str(user["id"]), actor_ip=client_ip,
                    severity="security", details={"reason": f"user_{user['status']}"},
                )
                outcome: Principal | str = "user_inactive"
            elif revoked is not None and int(claims.get("iat", 0)) <= int(revoked.timestamp()):
                # Suspension or deprovisioning ended this session (US-118): sign in again for a new token.
                await audit.record(
                    conn, tenant_id=tenant_id, action="auth.login", object_type="user", object_id=str(user["id"]),
                    outcome="deny", correlation_id=request_id, actor_id=str(user["id"]), actor_ip=client_ip,
                    severity="security", details={"reason": "session_revoked"},
                )
                outcome = "session_revoked"
            else:
                outcome = await self._resolve_roles(
                    conn, tenant_id, user, is_new=is_new, groups=groups, overage=overage,
                    request_id=request_id, client_ip=client_ip,
                )
        if outcome == "session_revoked":
            raise TelaError(ErrorCode.TOKEN_INVALID, "Your session has ended. Please sign in again.",
                            {"reason": outcome})
        if isinstance(outcome, str):
            raise TelaError(ErrorCode.NO_ACCESS, NO_ACCESS_MESSAGE, {"reason": outcome})
        return outcome

    async def _resolve_roles(
        self, conn: AsyncConnection, tenant_id: str, user: dict[str, Any], *, is_new: bool, groups: list[str],
        overage: bool, request_id: str | None, client_ip: str | None,
    ) -> Principal | str:
        """Sync claim groups and resolve roles and permissions. Returns the principal or a denial reason."""
        user_id = str(user["id"])
        if groups or not overage:
            await self._sync_claim_groups(conn, tenant_id, user_id, groups)

        roles = await fetch_all(
            conn, "SELECT role_id, role_key, kind, scope_attributes FROM iam.f_effective_roles(:uid)",
            {"uid": user["id"]},
        )
        role_ids = sorted({str(r["role_id"]) for r in roles})
        perms = await fetch_all(
            conn,
            "SELECT DISTINCT permission_key FROM iam.role_permissions WHERE role_id = ANY(CAST(:ids AS uuid[]))",
            {"ids": role_ids},
        ) if role_ids else []

        system_keys = {r["role_key"] for r in roles if r["kind"] == "system"}
        level = _level(roles)
        audience = sorted({r["role_key"] for r in roles if r["kind"] == "audience"})

        if level is None:
            await audit.record(
                conn, tenant_id=tenant_id, action="auth.login", object_type="user", object_id=user_id,
                outcome="deny", correlation_id=request_id, actor_id=user_id, actor_ip=client_ip,
                details={"reason": "no_mapped_group", "group_count": len(groups)},
            )
            return "no_mapped_group"

        last_login = user["last_login_at"]
        await conn.execute(
            text(
                "UPDATE iam.users SET last_login_at = now(), status = 'active', "
                "effective_roles_cache = CAST(:roles AS jsonb) WHERE id = :id"
            ),
            {"id": user["id"], "roles": json.dumps(sorted(system_keys | set(audience)))},
        )
        if is_new or last_login is None or datetime.now(UTC) - last_login > LOGIN_AUDIT_GAP:
            await audit.record(
                conn, tenant_id=tenant_id, action="auth.login", object_type="user", object_id=user_id,
                outcome="success", correlation_id=request_id, actor_id=user_id, actor_ip=client_ip,
                details={"level": level, "audience_roles": audience, "groups_overage": overage},
            )

        return _principal(tenant_id, user, roles, perms, level, groups, overage, await live_grants(conn, user_id))

    async def _upsert_user(
        self, conn: AsyncConnection, tenant_id: str, claims: dict[str, Any]
    ) -> tuple[dict[str, Any], bool]:
        issuer, subject = claims["iss"], claims["sub"]
        display_name = (claims.get("name") or claims.get("preferred_username") or "Unknown user")[:200]
        email = claims.get("email")
        tid, oid = claims.get("tid"), claims.get("oid")
        existing = await fetch_one(
            conn,
            """
            SELECT u.id, u.display_name, u.email::text AS email, u.status, u.locale, u.time_zone, u.last_login_at,
                   u.sessions_revoked_at
            FROM iam.external_identities e JOIN iam.users u ON u.id = e.user_id
            WHERE e.issuer = :iss AND e.subject = :sub
            """,
            {"iss": issuer, "sub": subject},
        )
        if existing is None and oid:
            # Provisioned by SCIM before the first sign-in: link by the immutable Entra object id (US-118).
            existing = await fetch_one(
                conn,
                "SELECT id, display_name, email::text AS email, status, locale, time_zone, last_login_at, "
                "sessions_revoked_at FROM iam.users WHERE scim_external_id = :oid",
                {"oid": oid},
            )
            if existing:
                await conn.execute(
                    text(
                        "INSERT INTO iam.external_identities (id, tenant_id, user_id, provider, issuer, subject, "
                        "directory_tenant_id, directory_object_id, last_seen_at) "
                        "VALUES (:id, :tenant, :uid, 'entra', :iss, :sub, :tid, :oid, now())"
                    ),
                    {"id": new_id(), "tenant": tenant_id, "uid": existing["id"], "iss": issuer, "sub": subject,
                     "tid": tid, "oid": oid},
                )
                return existing, False
        if existing:
            await conn.execute(
                text(
                    "UPDATE iam.external_identities SET last_seen_at = now(), "
                    "directory_tenant_id = coalesce(:tid, directory_tenant_id), "
                    "directory_object_id = coalesce(:oid, directory_object_id) "
                    "WHERE issuer = :iss AND subject = :sub"
                ),
                {"iss": issuer, "sub": subject, "tid": tid, "oid": oid},
            )
            if existing["display_name"] != display_name or existing["email"] != email:
                await conn.execute(
                    text("UPDATE iam.users SET display_name = :n, email = :e WHERE id = :id"),
                    {"n": display_name, "e": email, "id": existing["id"]},
                )
                existing["display_name"], existing["email"] = display_name, email
            return existing, False

        user_id = new_id()
        await conn.execute(
            text(
                "INSERT INTO iam.users (id, tenant_id, display_name, email, status) "
                "VALUES (:id, :tenant, :name, :email, 'pending')"
            ),
            {"id": user_id, "tenant": tenant_id, "name": display_name, "email": email},
        )
        await conn.execute(
            text(
                "INSERT INTO iam.external_identities (id, tenant_id, user_id, provider, issuer, subject, "
                "directory_tenant_id, directory_object_id, last_seen_at) "
                "VALUES (:id, :tenant, :uid, :provider, :iss, :sub, :tid, :oid, now())"
            ),
            {
                "id": new_id(), "tenant": tenant_id, "uid": user_id,
                "provider": "entra" if oid else "oidc", "iss": issuer, "sub": subject, "tid": tid, "oid": oid,
            },
        )
        log.info("identity.user_created", user_id_hash=hashlib.sha256(user_id.encode()).hexdigest()[:16])
        return {
            "id": user_id, "display_name": display_name, "email": email, "status": "pending",
            "locale": None, "time_zone": None, "last_login_at": None, "sessions_revoked_at": None,
        }, True

    async def _sync_claim_groups(self, conn: AsyncConnection, tenant_id: str, user_id: str, groups: list[str]) -> None:
        """Make claim-sourced memberships match the token. SCIM/Graph memberships are untouched."""
        group_ids: list[str] = []
        for external_id in groups:
            row = await fetch_one(
                conn,
                """
                INSERT INTO iam.idp_groups (id, tenant_id, external_group_id, display_name, source, last_synced_at)
                VALUES (:id, :tenant, :ext, :ext, 'claim', now())
                ON CONFLICT (tenant_id, external_group_id) DO UPDATE SET last_synced_at = now()
                RETURNING id
                """,
                {"id": new_id(), "tenant": tenant_id, "ext": external_id[:200]},
            )
            assert row is not None
            group_ids.append(str(row["id"]))
        await conn.execute(
            text(
                "DELETE FROM iam.idp_group_members WHERE user_id = :uid AND source = 'claim' "
                "AND NOT (group_id = ANY(CAST(:gids AS uuid[])))"
            ),
            {"uid": user_id, "gids": group_ids},
        )
        for gid in group_ids:
            await conn.execute(
                text(
                    "INSERT INTO iam.idp_group_members (tenant_id, group_id, user_id, source) "
                    "VALUES (:tenant, :gid, :uid, 'claim') "
                    "ON CONFLICT (group_id, user_id) DO UPDATE SET observed_at = now()"
                ),
                {"tenant": tenant_id, "gid": gid, "uid": user_id},
            )
