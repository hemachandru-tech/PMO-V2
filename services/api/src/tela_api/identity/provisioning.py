"""Provisioning status, reconcile and ownership reassignment (US-118 support, US-119; FEAT-004; E-17).

* The per-tenant SCIM bearer token is created and rotated here; only its SHA-256 hash is stored and the token is
  shown once.
* Ending someone's sessions has two halves. Tela refuses every token issued before ``users.sessions_revoked_at``
  at once (``principal.py``); ending their Keycloak sessions is a ``revoke_sessions`` task that may fail, so it is
  retried with backoff by ``scim_reconcile`` and the Admins are alerted when it gives up (S03 §5.4).
* ``scim_reconcile`` (hourly worker job, or Reconcile on the Admin page) retries due tasks, fixes drift it can fix
  (memberships of deprovisioned people, missing session revocations, stale role summaries) and reports what needs
  an Admin: people who sign in but are not provisioned, groups Entra deleted that still grant roles, and
  deprovisioned people who still own content. The Admins are notified when the findings change.
* An Admin can move a former user's content to someone else (E-17). Copies the new owner already has (same
  widget definition, same personal field) stay with the former owner and are counted as skipped.
"""

from __future__ import annotations

import hashlib
import json
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from tela_api.audit import service as audit
from tela_api.content.common import notify, users_with_permission
from tela_api.content.exports import require_worker
from tela_api.core.db import fetch_all, fetch_one, tenant_tx
from tela_api.core.settings import Settings
from tela_api.identity.deps import require
from tela_api.identity.permissions import Perm
from tela_api.identity.principal import Principal, PrincipalResolver
from tela_common.errors import ErrorCode, TelaError
from tela_common.ids import new_id
from tela_common.logging import get_logger

log = get_logger(__name__)

router = APIRouter(prefix="/api/v1/admin", tags=["admin-access"])
internal = APIRouter(prefix="/api/v1/internal/scim", tags=["internal"], include_in_schema=False)

TOKEN_PREFIX = "tela_scim_"  # noqa: S105 - a label, not a secret
MAX_ATTEMPTS = 5
LOG_RETENTION_DAYS = 30
RUNS_KEPT = 200
LIST_LIMIT = 20


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _rid(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


# --------------------------------------------------------------------------- shared helpers (SCIM uses them too)


async def recompute_roles(conn: AsyncConnection, user_ids: list[str] | None = None) -> list[str]:
    """Refresh ``effective_roles_cache`` from current memberships; returns the users whose roles changed."""
    rows = await fetch_all(
        conn,
        """
        WITH calc AS (
          SELECT u.id, CASE WHEN u.status = 'deprovisioned' THEN '[]'::jsonb ELSE coalesce(
            (SELECT jsonb_agg(DISTINCT r.role_key ORDER BY r.role_key) FROM iam.f_effective_roles(u.id) r),
            '[]'::jsonb) END AS roles
          FROM iam.users u
          WHERE CAST(:ids AS uuid[]) IS NULL OR u.id = ANY(CAST(:ids AS uuid[])))
        UPDATE iam.users u SET effective_roles_cache = c.roles FROM calc c
        WHERE u.id = c.id AND u.effective_roles_cache IS DISTINCT FROM c.roles
        RETURNING u.id
        """,
        {"ids": user_ids},
    )
    return [str(r["id"]) for r in rows]


def invalidate_principals(app: Any, user_ids: list[str] | set[str]) -> None:
    """Drop cached principals so role, scope and status changes apply on the next request."""
    resolver: PrincipalResolver = app.state.principal_resolver
    for uid in user_ids:
        resolver.invalidate(user_id=uid)


async def enqueue_revoke(conn: AsyncConnection, tenant_id: str, user_id: str) -> str:
    task_id = str(new_id())
    await conn.execute(text("INSERT INTO ops.provisioning_tasks (id, tenant_id, kind, user_id) "
                            "VALUES (:id, :t, 'revoke_sessions', :u)"), {"id": task_id, "t": tenant_id, "u": user_id})
    return task_id


class IdpSessions:
    """Ends a person's Keycloak sessions through the admin REST API (client credentials)."""

    def __init__(self, settings: Settings, http: httpx.AsyncClient | None = None) -> None:
        self.settings = settings
        self._http = http

    @property
    def configured(self) -> bool:
        return bool(self.settings.idp_admin_client_id and self.settings.idp_admin_client_secret)

    async def logout(self, issuer: str, subject: str) -> None:
        public_base, sep, realm = issuer.rstrip("/").rpartition("/realms/")
        if not sep or not realm:
            raise ValueError("the issuer is not a Keycloak realm URL")
        base = (self.settings.idp_admin_url or public_base).rstrip("/")
        http = self._http or httpx.AsyncClient(timeout=5.0)
        try:
            token = await http.post(f"{base}/realms/{realm}/protocol/openid-connect/token", data={
                "grant_type": "client_credentials", "client_id": self.settings.idp_admin_client_id,
                "client_secret": self.settings.idp_admin_client_secret})
            token.raise_for_status()
            resp = await http.post(f"{base}/admin/realms/{realm}/users/{subject}/logout",
                                   headers={"Authorization": f"Bearer {token.json()['access_token']}"})
            if resp.status_code != 404:  # the person never had a Keycloak account: nothing to end
                resp.raise_for_status()
        finally:
            if self._http is None:
                await http.aclose()


async def run_tasks(app: Any, tenant_id: str, task_ids: list[str] | None = None) -> dict[str, int]:
    """Run due side-effect tasks. HTTP calls happen outside transactions; failures back off 2^n minutes."""
    sessions: IdpSessions = app.state.idp_sessions
    async with tenant_tx(tenant_id) as conn:
        due = await fetch_all(
            conn,
            "SELECT t.id, t.user_id, t.attempts FROM ops.provisioning_tasks t WHERE t.status = 'pending' "
            "AND t.next_attempt_at <= now() AND (CAST(:ids AS uuid[]) IS NULL OR t.id = ANY(CAST(:ids AS uuid[]))) "
            "ORDER BY t.created_at LIMIT 100",
            {"ids": task_ids},
        )
        identities = {
            str(t["id"]): await fetch_all(conn, "SELECT issuer, subject FROM iam.external_identities "
                                                "WHERE user_id = :u", {"u": t["user_id"]})
            for t in due
        }
    counts = {"done": 0, "skipped": 0, "retrying": 0, "failed": 0}
    for task in due:
        tid = str(task["id"])
        status, error = "done", None
        if not sessions.configured:
            status = "skipped"
        else:
            try:
                for ident in identities[tid]:
                    await sessions.logout(ident["issuer"], ident["subject"])
            except Exception as exc:  # noqa: BLE001 - any failure is retried with backoff
                error = f"{type(exc).__name__}: {exc}"[:500]
                attempts = int(task["attempts"]) + 1
                status = "failed" if attempts >= MAX_ATTEMPTS else "pending"
        async with tenant_tx(tenant_id) as conn:
            if error is None:
                await conn.execute(text("UPDATE ops.provisioning_tasks SET status = :s, finished_at = now(), "
                                        "last_error = NULL WHERE id = :id"), {"s": status, "id": tid})
            else:
                attempts = int(task["attempts"]) + 1
                await conn.execute(
                    text("UPDATE ops.provisioning_tasks SET status = :s, attempts = :a, last_error = :e, "
                         "next_attempt_at = :next, finished_at = CASE WHEN :s = 'failed' THEN now() END "
                         "WHERE id = :id"),
                    {"s": status, "a": attempts, "e": error, "id": tid,
                     "next": datetime.now(UTC) + timedelta(minutes=2 ** attempts)})
                log.warning("provisioning.task_failed", task_id=tid, attempts=attempts, final=status == "failed")
        counts[status if status != "pending" else "retrying"] += 1
    return counts


# --------------------------------------------------------------------------- reconcile


async def owned_content(conn: AsyncConnection, user_id: str | None = None) -> list[dict[str, Any]]:
    """Content still owned by deprovisioned people (or by one person)."""
    rows = await fetch_all(
        conn,
        """
        SELECT u.id, u.display_name, u.status, u.deprovisioned_at,
          (SELECT count(*) FROM content.dashboards d WHERE d.owner_id = u.id AND d.status <> 'deleted') AS dashboards,
          (SELECT count(*) FROM content.reports r WHERE r.owner_id = u.id AND r.deleted_at IS NULL) AS reports,
          (SELECT count(*) FROM content.widgets w WHERE w.owner_id = u.id AND w.deleted_at IS NULL) AS widgets,
          (SELECT count(*) FROM content.custom_sql_definitions s
            WHERE s.owner_id = u.id AND s.deleted_at IS NULL) AS custom_sql,
          (SELECT count(*) FROM content.personal_fields f WHERE f.owner_id = u.id AND f.deleted_at IS NULL) AS fields
        FROM iam.users u
        WHERE (CAST(:u AS uuid) IS NULL AND u.status = 'deprovisioned') OR u.id = CAST(:u AS uuid)
        ORDER BY u.display_name
        """,
        {"u": user_id},
    )
    keys = ("dashboards", "reports", "widgets", "custom_sql", "fields")
    out = []
    for r in rows:
        owned = {k: int(r[k]) for k in keys}
        if user_id is None and not any(owned.values()):
            continue
        out.append({"user": {"id": str(r["id"]), "display_name": r["display_name"], "status": r["status"]},
                    "deprovisioned_at": _iso(r["deprovisioned_at"]), "owned": owned})
    return out


async def _findings(conn: AsyncConnection, tenant_id: str) -> dict[str, Any]:
    """Fix what can be fixed; return counts and the lists an Admin has to act on."""
    removed = await fetch_all(conn, "DELETE FROM iam.idp_group_members m USING iam.users u WHERE u.id = m.user_id "
                                    "AND u.status = 'deprovisioned' RETURNING m.user_id")
    from tela_api.identity.grants import remove_for_user

    gone = await fetch_all(conn, "SELECT DISTINCT g.user_id FROM iam.permission_grants g JOIN iam.users u "
                                 "ON u.id = g.user_id WHERE g.removed_at IS NULL AND u.status = 'deprovisioned'")
    grants_removed = 0
    for row in gone:
        grants_removed += await remove_for_user(conn, tenant_id, str(row["user_id"]), reason="scim",
                                                correlation_id=None)
    unrevoked = await fetch_all(conn, "UPDATE iam.users SET sessions_revoked_at = now() WHERE status IN "
                                      "('suspended', 'deprovisioned') AND sessions_revoked_at IS NULL RETURNING id")
    for row in unrevoked:
        await enqueue_revoke(conn, tenant_id, str(row["id"]))
    refreshed = await recompute_roles(conn)
    unlinked = await fetch_all(
        conn, "SELECT id, display_name, email::text AS email, last_login_at FROM iam.users WHERE status = 'active' "
              "AND scim_external_id IS NULL ORDER BY display_name")
    deleted_groups = await fetch_all(
        conn, "SELECT g.id, g.display_name, g.external_group_id, count(m.id) AS mappings FROM iam.idp_groups g "
              "JOIN iam.group_role_mappings m ON m.group_id = g.id WHERE g.scim_deleted_at IS NOT NULL "
              "GROUP BY g.id ORDER BY g.display_name")
    failed = await fetch_all(
        conn, "SELECT t.id, t.kind, t.user_id, u.display_name, t.attempts, t.last_error, t.alerted "
              "FROM ops.provisioning_tasks t JOIN iam.users u ON u.id = t.user_id WHERE t.status = 'failed' "
              "ORDER BY t.created_at DESC")
    return {
        "fixed": {"memberships_removed": len(removed), "sessions_revoked": len(unrevoked),
                  "grants_removed": grants_removed},
        "roles_refreshed": refreshed,
        "unlinked_users": {"count": len(unlinked), "items": [
            {"id": str(r["id"]), "display_name": r["display_name"], "email": r["email"],
             "last_login_at": _iso(r["last_login_at"])} for r in unlinked[:LIST_LIMIT]]},
        "deleted_groups_mapped": [{"id": str(g["id"]), "display_name": g["display_name"],
                                   "external_group_id": g["external_group_id"], "mappings": int(g["mappings"])}
                                  for g in deleted_groups],
        "deprovisioned_owners": await owned_content(conn),
        "failed_tasks": [{"id": str(t["id"]), "kind": t["kind"], "user": {"id": str(t["user_id"]),
                          "display_name": t["display_name"]}, "attempts": t["attempts"], "last_error": t["last_error"],
                          "alerted": t["alerted"]} for t in failed],
    }


def _signature(f: dict[str, Any]) -> str:
    """What an Admin would be told about; the same findings are announced once."""
    key = {"unlinked": sorted(i["id"] for i in f["unlinked_users"]["items"]),
           "unlinked_count": f["unlinked_users"]["count"],
           "groups": sorted(g["id"] for g in f["deleted_groups_mapped"]),
           "owners": sorted(o["user"]["id"] for o in f["deprovisioned_owners"]),
           "failed": sorted(t["id"] for t in f["failed_tasks"])}
    return hashlib.sha256(json.dumps(key, sort_keys=True).encode()).hexdigest()[:16]


def _has_drift(f: dict[str, Any]) -> bool:
    return bool(any(f["fixed"].values()) or f["unlinked_users"]["count"] or f["deleted_groups_mapped"]
                or f["deprovisioned_owners"] or f["failed_tasks"])


def _drift_text(f: dict[str, Any]) -> str:
    parts = []
    if f["failed_tasks"]:
        parts.append(f"{len(f['failed_tasks'])} sign-out(s) failed after {MAX_ATTEMPTS} attempts")
    if f["unlinked_users"]["count"]:
        parts.append(f"{f['unlinked_users']['count']} active people are not provisioned from Entra")
    if f["deleted_groups_mapped"]:
        parts.append(f"{len(f['deleted_groups_mapped'])} group(s) deleted in Entra still have role mappings")
    if f["deprovisioned_owners"]:
        parts.append(f"{len(f['deprovisioned_owners'])} former user(s) still own content")
    fixed = f["fixed"]
    if fixed["memberships_removed"] or fixed["sessions_revoked"]:
        parts.append(f"fixed {fixed['memberships_removed']} membership(s) and {fixed['sessions_revoked']} "
                     "session revocation(s)")
    if fixed.get("grants_removed"):
        parts.append(f"removed {fixed['grants_removed']} permission grant(s) of former users")
    return "; ".join(parts) + "."


async def reconcile(app: Any, tenant_id: str, *, trigger: str, by: str | None = None,
                    request_id: str | None = None) -> dict[str, Any]:
    run_id = new_id()
    async with tenant_tx(tenant_id) as conn:
        running = await fetch_one(conn, "SELECT id FROM ops.provisioning_runs WHERE outcome = 'running' "
                                        "AND started_at > now() - interval '10 minutes' FOR UPDATE")
        if running is not None:
            raise TelaError(ErrorCode.CONFLICT, "A reconcile is already running. Try again in a few minutes.")
        previous = await fetch_one(conn, "SELECT summary FROM ops.provisioning_runs WHERE outcome IN ('drift', "
                                         "'failed') ORDER BY started_at DESC LIMIT 1")
        await conn.execute(text("INSERT INTO ops.provisioning_runs (id, tenant_id, trigger, triggered_by) "
                                "VALUES (:id, :t, :trigger, :by)"),
                           {"id": run_id, "t": tenant_id, "trigger": trigger, "by": by})
    try:
        tasks = await run_tasks(app, tenant_id)
        async with tenant_tx(tenant_id) as conn:
            findings = await _findings(conn, tenant_id)
        tasks_after = await run_tasks(app, tenant_id)  # sign-outs enqueued for revocations fixed just now
        tasks = {k: tasks[k] + tasks_after[k] for k in tasks}
        invalidate_principals(app, findings["roles_refreshed"])
    except Exception as exc:  # recorded, alerted, re-raised for the worker to retry
        error = f"{type(exc).__name__}"[:500]
        async with tenant_tx(tenant_id) as conn:
            await conn.execute(text("UPDATE ops.provisioning_runs SET outcome = 'failed', finished_at = now(), "
                                    "error = :e WHERE id = :id"), {"e": error, "id": run_id})
            admins = await users_with_permission(conn, Perm.MAPPINGS_MANAGE)
            await notify(conn, tenant_id, admins, kind="scim.sync_failed", title="Provisioning reconcile failed",
                         body="Tela could not reconcile users and groups from Entra. It will try again.",
                         link="/admin/provisioning")
            await audit.record(conn, tenant_id=tenant_id, action="scim.reconcile", object_type="tenant",
                               object_id=tenant_id, outcome="failure", actor_id=by, correlation_id=request_id,
                               details={"trigger": trigger, "error": error})
        raise
    drift = _has_drift(findings)
    signature = _signature(findings)
    summary = {**findings, "roles_refreshed": len(findings["roles_refreshed"]), "tasks": tasks,
               "signature": signature}
    outcome = "drift" if drift else "ok"
    async with tenant_tx(tenant_id) as conn:
        alerted = 0
        if drift and (previous is None or previous["summary"].get("signature") != signature):
            admins = await users_with_permission(conn, Perm.MAPPINGS_MANAGE)
            alerted = await notify(conn, tenant_id, admins, kind="scim.drift",
                                   title="Provisioning needs attention", body=_drift_text(findings),
                                   link="/admin/provisioning")
            await conn.execute(text("UPDATE ops.provisioning_tasks SET alerted = true WHERE status = 'failed'"))
        summary["alerted"] = alerted
        row = await fetch_one(
            conn, "UPDATE ops.provisioning_runs SET outcome = :o, finished_at = now(), summary = CAST(:s AS jsonb) "
                  "WHERE id = :id RETURNING id, trigger, triggered_by, started_at, finished_at, outcome, summary, "
                  "error",
            {"o": outcome, "s": json.dumps(summary), "id": run_id})
        await conn.execute(text("DELETE FROM ops.provisioning_log WHERE at < now() - make_interval(days => :d)"),
                           {"d": LOG_RETENTION_DAYS})
        await conn.execute(text("DELETE FROM ops.provisioning_runs WHERE id IN (SELECT id FROM "
                                "ops.provisioning_runs ORDER BY started_at DESC OFFSET :n)"), {"n": RUNS_KEPT})
        await audit.record(conn, tenant_id=tenant_id, action="scim.reconcile", object_type="tenant",
                           object_id=tenant_id, outcome="success", actor_id=by, correlation_id=request_id,
                           details={"trigger": trigger, "outcome": outcome, **findings["fixed"],
                                    "roles_refreshed": summary["roles_refreshed"], **tasks})
    assert row is not None
    return _run_out(row)


def _run_out(r: dict[str, Any]) -> dict[str, Any]:
    return {"id": str(r["id"]), "trigger": r["trigger"],
            "triggered_by": str(r["triggered_by"]) if r["triggered_by"] else None,
            "started_at": _iso(r["started_at"]), "finished_at": _iso(r["finished_at"]), "outcome": r["outcome"],
            "summary": r["summary"], "error": r["error"]}


# --------------------------------------------------------------------------- Admin API (US-119)


@router.get("/provisioning")
async def provisioning_status(request: Request,
                              principal: Principal = Depends(require(Perm.MAPPINGS_MANAGE))) -> dict[str, Any]:
    async with tenant_tx(principal.tenant_id) as conn:
        token = await fetch_one(
            conn, "SELECT k.prefix, k.created_at, k.last_used_at, u.display_name AS created_by FROM iam.scim_tokens k "
                  "LEFT JOIN iam.users u ON u.id = k.created_by WHERE k.revoked_at IS NULL")
        users = await fetch_all(conn, "SELECT status, count(*) AS n FROM iam.users WHERE provisioned_by = 'scim' "
                                      "OR scim_external_id IS NOT NULL GROUP BY status")
        groups = await fetch_one(conn, "SELECT count(*) FILTER (WHERE source = 'scim' AND scim_deleted_at IS NULL) "
                                       "AS active, count(*) FILTER (WHERE scim_deleted_at IS NOT NULL) AS deleted "
                                       "FROM iam.idp_groups")
        day = await fetch_one(conn, "SELECT count(*) AS requests, count(*) FILTER (WHERE outcome = 'error') AS errors,"
                                    " max(at) AS last_at FROM ops.provisioning_log WHERE at > now() - interval '1 day'")
        last_at = await fetch_one(conn, "SELECT max(at) AS at FROM ops.provisioning_log")
        recent = await fetch_all(
            conn, "SELECT l.at, l.method, l.resource, l.resource_id, l.operation, l.status, l.outcome, l.detail, "
                  "coalesce(u.display_name, g.display_name) AS name FROM ops.provisioning_log l "
                  "LEFT JOIN iam.users u ON u.id = l.resource_id LEFT JOIN iam.idp_groups g ON g.id = l.resource_id "
                  "ORDER BY l.at DESC LIMIT 25")
        tasks = await fetch_all(
            conn, "SELECT t.id, t.status, t.attempts, t.last_error, t.next_attempt_at, t.created_at, t.user_id, "
                  "u.display_name FROM ops.provisioning_tasks t JOIN iam.users u ON u.id = t.user_id "
                  "WHERE t.status IN ('pending', 'failed') ORDER BY t.created_at DESC LIMIT 50")
        runs = await fetch_all(conn, "SELECT id, trigger, triggered_by, started_at, finished_at, outcome, summary, "
                                     "error FROM ops.provisioning_runs ORDER BY started_at DESC LIMIT 10")
        owners = await owned_content(conn)
    assert groups is not None and day is not None and last_at is not None
    return {
        "enabled": token is not None,
        "endpoint_path": "/scim/v2",
        "token": {"prefix": token["prefix"], "created_at": _iso(token["created_at"]),
                  "created_by": token["created_by"], "last_used_at": _iso(token["last_used_at"])} if token else None,
        "idp_sessions_configured": request.app.state.idp_sessions.configured,
        "last_activity_at": _iso(last_at["at"]),
        "counts": {
            "users": {s: next((int(u["n"]) for u in users if u["status"] == s), 0)
                      for s in ("active", "pending", "suspended", "deprovisioned")},
            "groups": int(groups["active"]), "groups_deleted": int(groups["deleted"]),
            "requests_24h": int(day["requests"]), "errors_24h": int(day["errors"]),
        },
        "recent": [{"at": _iso(r["at"]), "method": r["method"], "resource": r["resource"],
                    "resource_id": str(r["resource_id"]) if r["resource_id"] else None, "name": r["name"],
                    "operation": r["operation"], "status": r["status"], "outcome": r["outcome"],
                    "detail": r["detail"]} for r in recent],
        "tasks": [{"id": str(t["id"]), "status": t["status"], "attempts": t["attempts"], "last_error": t["last_error"],
                   "next_attempt_at": _iso(t["next_attempt_at"]), "created_at": _iso(t["created_at"]),
                   "user": {"id": str(t["user_id"]), "display_name": t["display_name"]}} for t in tasks],
        "last_run": _run_out(runs[0]) if runs else None,
        "runs": [_run_out(r) for r in runs],
        "deprovisioned_owners": owners,
    }


@router.post("/provisioning/token", status_code=201)
async def create_token(request: Request,
                       principal: Principal = Depends(require(Perm.MAPPINGS_MANAGE))) -> dict[str, Any]:
    """Create or rotate the tenant's SCIM token. The previous one stops working at once; the new one is shown once."""
    token = TOKEN_PREFIX + secrets.token_urlsafe(32)
    async with tenant_tx(principal.tenant_id) as conn:
        rotated = await fetch_all(conn, "UPDATE iam.scim_tokens SET revoked_at = now(), revoked_by = :by "
                                        "WHERE revoked_at IS NULL RETURNING id", {"by": principal.user_id})
        row = await fetch_one(
            conn, "INSERT INTO iam.scim_tokens (id, tenant_id, token_hash, prefix, created_by) "
                  "VALUES (:id, :t, :h, :p, :by) RETURNING prefix, created_at",
            {"id": new_id(), "t": principal.tenant_id, "h": token_hash(token), "p": token[:len(TOKEN_PREFIX) + 4],
             "by": principal.user_id})
        await audit.record(conn, tenant_id=principal.tenant_id, action="scim.token.created", object_type="tenant",
                           object_id=principal.tenant_id, outcome="success", actor_id=principal.user_id,
                           correlation_id=_rid(request), severity="security", details={"rotated": bool(rotated)})
    assert row is not None
    return {"token": token, "prefix": row["prefix"], "created_at": _iso(row["created_at"]),
            "endpoint_path": "/scim/v2", "rotated": bool(rotated)}


@router.delete("/provisioning/token", status_code=204)
async def revoke_token(request: Request, principal: Principal = Depends(require(Perm.MAPPINGS_MANAGE))) -> Response:
    async with tenant_tx(principal.tenant_id) as conn:
        rows = await fetch_all(conn, "UPDATE iam.scim_tokens SET revoked_at = now(), revoked_by = :by "
                                     "WHERE revoked_at IS NULL RETURNING id", {"by": principal.user_id})
        if not rows:
            raise TelaError(ErrorCode.NOT_FOUND, "Provisioning is not turned on.")
        await audit.record(conn, tenant_id=principal.tenant_id, action="scim.token.revoked", object_type="tenant",
                           object_id=principal.tenant_id, outcome="success", actor_id=principal.user_id,
                           correlation_id=_rid(request), severity="security")
    return Response(status_code=204)


@router.post("/provisioning/reconcile")
async def reconcile_now(request: Request,
                        principal: Principal = Depends(require(Perm.MAPPINGS_MANAGE))) -> dict[str, Any]:
    return await reconcile(request.app, principal.tenant_id, trigger="manual", by=principal.user_id,
                           request_id=_rid(request))


class ReassignIn(BaseModel):
    to_user_id: str = Field(min_length=1, max_length=64)


_REASSIGN = {
    "dashboards": "UPDATE content.dashboards SET owner_id = :to WHERE owner_id = :from AND status <> 'deleted' "
                  "RETURNING id",
    "reports": "UPDATE content.reports SET owner_id = :to WHERE owner_id = :from AND deleted_at IS NULL RETURNING id",
    "widgets": "UPDATE content.widgets w SET owner_id = :to WHERE w.owner_id = :from AND w.deleted_at IS NULL "
               "AND (w.def_hash IS NULL OR NOT EXISTS (SELECT 1 FROM content.widgets x WHERE x.owner_id = :to "
               "AND x.def_hash = w.def_hash AND x.deleted_at IS NULL)) RETURNING w.id",
    "custom_sql": "UPDATE content.custom_sql_definitions SET owner_id = :to WHERE owner_id = :from "
                  "AND deleted_at IS NULL RETURNING id",
    "fields": "UPDATE content.personal_fields f SET owner_id = :to WHERE f.owner_id = :from AND f.deleted_at IS NULL "
              "AND NOT EXISTS (SELECT 1 FROM content.personal_fields x WHERE x.owner_id = :to "
              "AND x.deleted_at IS NULL AND ((x.model_version_id = f.model_version_id AND x.key = f.key) "
              "OR lower(x.label) = lower(f.label))) RETURNING f.id",
}


@router.post("/users/{user_id}/reassign-content")
async def reassign_content(user_id: str, body: ReassignIn, request: Request,
                           principal: Principal = Depends(require(Perm.MAPPINGS_MANAGE))) -> dict[str, Any]:
    """E-17: move a former user's dashboards, reports, widgets, custom SQL and personal fields to someone else."""
    async with tenant_tx(principal.tenant_id) as conn:
        try:
            people = await fetch_all(conn, "SELECT id, display_name, status FROM iam.users "
                                           "WHERE id = ANY(CAST(:ids AS uuid[]))", {"ids": [user_id, body.to_user_id]})
        except Exception:  # noqa: BLE001 - a malformed id is just "not found"
            people = []
        by_id = {str(p["id"]): p for p in people}
        source, target = by_id.get(user_id), by_id.get(body.to_user_id)
        if source is None or target is None:
            raise TelaError(ErrorCode.NOT_FOUND, "That person does not exist.")
        if source["status"] not in ("suspended", "deprovisioned"):
            raise TelaError(ErrorCode.CONFLICT, "Only content of people who are suspended or have left can be "
                                                "reassigned.")
        if user_id == body.to_user_id or target["status"] != "active":
            raise TelaError(ErrorCode.VALIDATION_ERROR, "Choose an active person as the new owner.")
        moved = {k: len(await fetch_all(conn, sql, {"from": user_id, "to": body.to_user_id}))
                 for k, sql in _REASSIGN.items()}
        left = (await owned_content(conn, user_id))[0]["owned"]
        await audit.record(conn, tenant_id=principal.tenant_id, action="ownership.reassigned", object_type="user",
                           object_id=user_id, outcome="success", actor_id=principal.user_id,
                           correlation_id=_rid(request), details={"to": body.to_user_id, **moved})
        if any(moved.values()):
            await notify(conn, principal.tenant_id, [body.to_user_id], kind="ownership.received",
                         title=f"You now own {source['display_name']}'s content",
                         body=", ".join(f"{n} {k.replace('_', ' ')}" for k, n in moved.items() if n) + ".",
                         link="/", by=principal.user_id)
    return {"moved": moved, "skipped": left,
            "to": {"id": body.to_user_id, "display_name": target["display_name"]}}


# --------------------------------------------------------------------------- scim_reconcile (hourly)


@internal.post("/reconcile")
async def reconcile_all(request: Request, _: dict[str, Any] = Depends(require_worker)) -> dict[str, Any]:
    async with tenant_tx(None) as conn:
        tenants = [str(r["tenant_id"]) for r in await fetch_all(conn, "SELECT tenant_id FROM iam.f_scim_tenants()")]
    out: dict[str, int] = {"tenants": len(tenants), "ok": 0, "drift": 0, "failed": 0, "busy": 0}
    for tenant_id in tenants:
        try:
            run = await reconcile(request.app, tenant_id, trigger="schedule", request_id=_rid(request))
            out[run["outcome"]] += 1
        except TelaError:
            out["busy"] += 1
        except Exception:  # noqa: BLE001 - recorded and alerted inside; other tenants still run
            out["failed"] += 1
    if out["failed"]:
        raise TelaError(ErrorCode.INTERNAL, "Reconcile failed for some tenants.", out)
    return out
