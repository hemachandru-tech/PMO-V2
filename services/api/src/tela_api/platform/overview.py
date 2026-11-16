"""Admin console overview (US-143; FEAT-056): what needs an Admin today, in one call.

Counts and a few short labels only (a connection or model name, the oldest requester, the latest security action);
never data rows or event lists. Each section is computed only for people holding the permission of the screen it
links to, and is ``null`` otherwise, so the overview never shows more than those screens would.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncConnection

from tela_api.core.db import fetch_all, fetch_one, tenant_tx
from tela_api.governance.certification import usage as definition_usage
from tela_api.identity.deps import get_principal
from tela_api.identity.permissions import Perm
from tela_api.identity.principal import Principal
from tela_api.llm.router import tenant_llm_settings
from tela_common.errors import ErrorCode, TelaError

router = APIRouter(prefix="/api/v1", tags=["platform"])

SECTIONS: dict[str, tuple[Perm, ...]] = {
    "connections": (Perm.CONNECTIONS_MANAGE,),
    "models": (Perm.MODEL_EDIT, Perm.MODEL_APPROVE),
    "certification": (Perm.CERTIFICATION_DECIDE,),
    "publish_requests": (Perm.DASHBOARDS_PUBLISH,),
    "ai": (Perm.SETTINGS_MANAGE,),
    "security": (Perm.AUDIT_READ,),
    "jobs": (Perm.SETTINGS_MANAGE, Perm.CONNECTIONS_MANAGE),
    "value_synonyms": (Perm.KNOWLEDGE_MANAGE,),
}
HEALTHY = "connected"
ATTENTION = ("error", "incomplete")  # draft/testing are unfinished set-ups; disabled is a choice
AI_DAYS = 14
SECURITY_DAYS = 7
MAX_LABELS = 5


def _zone(tz: str | None) -> ZoneInfo:
    try:
        return ZoneInfo(tz or "UTC")
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise TelaError(ErrorCode.VALIDATION_ERROR, "That time zone is not known.", {"tz": tz}) from exc


async def _connections(conn: AsyncConnection) -> dict[str, Any]:
    rows = await fetch_all(conn, "SELECT status, count(*) AS n FROM conn.connections WHERE deleted_at IS NULL "
                                 "GROUP BY status")
    by_status = {r["status"]: int(r["n"]) for r in rows}
    attention = await fetch_all(
        conn, "SELECT id, name, status FROM conn.connections WHERE deleted_at IS NULL AND status = ANY(:s) "
              "ORDER BY status, name LIMIT :lim", {"s": list(ATTENTION), "lim": MAX_LABELS})
    return {"total": sum(by_status.values()), "healthy": by_status.get(HEALTHY, 0), "by_status": by_status,
            "attention": [{"id": str(r["id"]), "name": r["name"], "status": r["status"]} for r in attention]}


async def _models(conn: AsyncConnection) -> dict[str, Any]:
    rows = await fetch_all(
        conn,
        """
        SELECT m.id, m.status, c.name AS connection, cur.version_no, cur.approved_at, m.draft_version_id,
          (SELECT count(*) FROM model.model_patches p WHERE p.model_version_id = m.draft_version_id
             AND p.status = 'pending') AS pending_changes,
          (SELECT count(*) FROM model.model_questions q WHERE q.model_version_id = m.draft_version_id
             AND q.status = 'open' AND q.blocking) AS open_questions
        FROM model.data_models m JOIN conn.connections c ON c.id = m.connection_id AND c.deleted_at IS NULL
        LEFT JOIN model.model_versions cur ON cur.id = m.current_version_id
        ORDER BY c.name LIMIT :lim
        """, {"lim": MAX_LABELS})
    items = [{"id": str(r["id"]), "connection": r["connection"], "version_no": r["version_no"],
              "approved_at": r["approved_at"], "has_draft": r["draft_version_id"] is not None,
              "pending_changes": int(r["pending_changes"]), "open_questions": int(r["open_questions"]),
              "broken": r["status"] == "broken"} for r in rows]
    total = await fetch_one(conn, "SELECT count(*) AS n, count(draft_version_id) AS drafts, "
                                  "count(*) FILTER (WHERE status = 'broken') AS broken FROM model.data_models")
    return {"total": int(total["n"]) if total else 0, "drafts": int(total["drafts"]) if total else 0,
            "broken": int(total["broken"]) if total else 0, "items": items}


async def _certification(conn: AsyncConnection) -> dict[str, Any]:
    rows = await fetch_all(conn, "SELECT id, item_id, trigger, created_at, "
                                 "(SELECT count(*) FROM gov.lookalike_findings f WHERE f.request_id = r.id) AS similar "
                                 "FROM gov.certification_requests r WHERE status = 'pending' ORDER BY created_at")
    blocks = 0
    for r in rows:  # the same rule as the queue's "blocks a publish" filter
        if r["trigger"] == "publish_bound" or (await definition_usage(conn, str(r["item_id"])))["dashboards"]:
            blocks += 1
    return {"pending": len(rows), "blocks_publish": blocks, "with_lookalikes": sum(1 for r in rows if r["similar"]),
            "oldest_at": rows[0]["created_at"] if rows else None}


async def _publish_requests(conn: AsyncConnection) -> dict[str, Any]:
    row = await fetch_one(
        conn,
        """
        SELECT count(*) AS n, min(r.created_at) AS oldest_at,
          (SELECT u.display_name FROM content.publish_requests o JOIN iam.users u ON u.id = o.requested_by
           WHERE o.status = 'open' ORDER BY o.created_at LIMIT 1) AS oldest_by
        FROM content.publish_requests r WHERE r.status = 'open'
        """)
    assert row is not None
    return {"open": int(row["n"]), "oldest_at": row["oldest_at"], "oldest_by": row["oldest_by"]}


async def _value_synonyms(conn: AsyncConnection) -> dict[str, Any]:
    """Pack value synonyms waiting for review, by connection (US-215): until approved, "Saudi" or "Dubai" link
    nothing in Ask."""
    rows = await fetch_all(
        conn, "SELECT c.id, c.name, count(*) FILTER (WHERE s.source = 'pack') AS pack, count(*) AS n FROM "
              "rag.value_synonyms s JOIN conn.connections c ON c.id = s.connection_id AND c.deleted_at IS NULL WHERE "
              "s.status = 'pending_review' GROUP BY c.id, c.name ORDER BY count(*) DESC, c.name")
    return {"pending": sum(int(r["n"]) for r in rows), "pack_pending": sum(int(r["pack"]) for r in rows),
            "connections": [{"id": str(r["id"]), "name": r["name"], "pending": int(r["n"]),
                             "pack_pending": int(r["pack"])} for r in rows[:MAX_LABELS]]}


async def _ai(conn: AsyncConnection, today: datetime, zone: ZoneInfo) -> dict[str, Any]:
    settings = await tenant_llm_settings(conn)
    since = today - timedelta(days=AI_DAYS - 1)
    rows = await fetch_all(
        conn,
        """
        SELECT to_char(created_at AT TIME ZONE :tz, 'YYYY-MM-DD') AS day, model, count(*) AS calls,
               coalesce(sum(prompt_tokens), 0) AS prompt_tokens,
                   coalesce(sum(completion_tokens), 0) AS completion_tokens,
               count(*) FILTER (WHERE status = 'blocked') AS blocked,
               count(*) FILTER (WHERE status NOT IN ('ok', 'blocked')) AS failed
        FROM ops.llm_invocations WHERE created_at >= :since GROUP BY 1, 2
        """, {"tz": zone.key, "since": since})
    rates = settings["rates"] or {}
    days = {(since.date() + timedelta(days=i)).isoformat(): 0 for i in range(AI_DAYS)}
    out = {"calls": 0, "tokens": 0, "blocked": 0, "failed": 0, "estimated_cost": 0.0}
    for r in rows:
        days[r["day"]] = days.get(r["day"], 0) + int(r["calls"])
        if r["day"] != today.date().isoformat():
            continue
        rate = rates.get(r["model"]) or {}
        out["calls"] += int(r["calls"])
        out["tokens"] += int(r["prompt_tokens"]) + int(r["completion_tokens"])
        out["blocked"] += int(r["blocked"])
        out["failed"] += int(r["failed"])
        out["estimated_cost"] += (int(r["prompt_tokens"]) * float(rate.get("input_per_mtok", 0))
                                  + int(r["completion_tokens"]) * float(rate.get("output_per_mtok", 0))) / 1e6
    return {"provider": settings["provider"], "today": out,
            "daily_calls": [{"day": d, "calls": n} for d, n in sorted(days.items())]}


async def _security(conn: AsyncConnection, now: datetime) -> dict[str, Any]:
    since = now - timedelta(days=SECURITY_DAYS)
    rows = await fetch_all(conn, "SELECT action, count(*) AS n FROM ops.audit_events WHERE severity = 'security' "
                                 "AND occurred_at >= :since GROUP BY action ORDER BY 2 DESC", {"since": since})
    latest = await fetch_one(conn, "SELECT e.action, e.occurred_at, u.display_name AS actor FROM ops.audit_events e "
                                   "LEFT JOIN iam.users u ON u.id = e.actor_id WHERE e.severity = 'security' AND "
                                   "e.occurred_at >= :since ORDER BY e.id DESC LIMIT 1", {"since": since})
    return {"days": SECURITY_DAYS, "since": since, "total": sum(int(r["n"]) for r in rows),
            "by_action": {r["action"]: int(r["n"]) for r in rows},
            "latest": {"action": latest["action"], "at": latest["occurred_at"], "actor": latest["actor"]}
            if latest else None}


async def _jobs(conn: AsyncConnection, now: datetime) -> dict[str, Any]:
    since = now - timedelta(hours=24)
    jobs: list[dict[str, Any]] = []
    for key, label, action in (("introspection", "Read schema", "connection.introspected"),
                               ("cache_refresh", "Refresh cache", "connection.cache_refreshed")):
        row = await fetch_one(
            conn,
            """
            SELECT count(*) AS runs, count(*) FILTER (WHERE e.outcome = 'failure') AS failed,
                   max(e.occurred_at) AS last,
              (SELECT json_build_object('at', f.occurred_at, 'label', c.name) FROM ops.audit_events f
               LEFT JOIN conn.connections c ON CAST(c.id AS text) = f.object_id
               WHERE f.action = :a AND f.outcome = 'failure' AND f.occurred_at >= :since ORDER BY f.id DESC LIMIT 1)
                AS last_failure
            FROM ops.audit_events e WHERE e.action = :a AND e.occurred_at >= :since
            """, {"a": action, "since": since})
        assert row is not None
        jobs.append({"key": key, "label": label, "runs": int(row["runs"]), "failed": int(row["failed"]),
                     "last_run_at": row["last"], "last_failure": row["last_failure"]})
    row = await fetch_one(conn, "SELECT count(*) AS runs, count(*) FILTER (WHERE status = 'failed') AS failed, "
                                "max(updated_at) AS last, (SELECT json_build_object('at', updated_at, 'label', format) "
                                "FROM ops.export_jobs WHERE status = 'failed' AND updated_at >= :since ORDER BY "
                                "updated_at DESC LIMIT 1) AS last_failure FROM ops.export_jobs "
                                "WHERE created_at >= :since", {"since": since})
    assert row is not None
    jobs.append({"key": "exports", "label": "Exports", "runs": int(row["runs"]), "failed": int(row["failed"]),
                 "last_run_at": row["last"], "last_failure": row["last_failure"]})
    row = await fetch_one(conn, "SELECT count(*) FILTER (WHERE published_at >= :since) AS runs, "
                                "count(*) FILTER (WHERE dead_at >= :since) AS failed, "
                                "greatest(max(published_at), max(dead_at)) AS last, "
                                "(SELECT json_build_object('at', dead_at, 'label', topic) FROM ops.outbox_events WHERE "
                                "dead_at >= :since ORDER BY dead_at DESC LIMIT 1) AS last_failure "
                                "FROM ops.outbox_events WHERE published_at >= :since OR dead_at >= :since",
                         {"since": since})
    assert row is not None
    jobs.append({"key": "events", "label": "Deliver events", "runs": int(row["runs"]), "failed": int(row["failed"]),
                 "last_run_at": row["last"], "last_failure": row["last_failure"]})
    return {"hours": 24, "failed": sum(j["failed"] for j in jobs), "items": jobs}


@router.get("/admin/overview")
async def admin_overview(tz: str | None = None, principal: Principal = Depends(get_principal)) -> dict[str, Any]:
    """Counts for the Admin console home. ``tz`` (IANA name, default UTC) sets where "today" starts."""
    allowed = {name for name, perms in SECTIONS.items() if any(principal.has(p) for p in perms)}
    if not allowed:
        raise TelaError(ErrorCode.FORBIDDEN, "You don't have permission to do that.",
                        {"missing": sorted({p.value for perms in SECTIONS.values() for p in perms})})
    zone = _zone(tz)
    now = datetime.now(UTC)
    local = now.astimezone(zone)
    today = datetime.combine(local.date(), datetime.min.time(), tzinfo=zone)
    out: dict[str, Any] = {"generated_at": now, "tz": zone.key, "today": date.isoformat(local.date())}
    async with tenant_tx(principal.tenant_id) as conn:
        out["connections"] = await _connections(conn) if "connections" in allowed else None
        out["models"] = await _models(conn) if "models" in allowed else None
        out["certification"] = await _certification(conn) if "certification" in allowed else None
        out["publish_requests"] = await _publish_requests(conn) if "publish_requests" in allowed else None
        out["ai"] = await _ai(conn, today, zone) if "ai" in allowed else None
        out["security"] = await _security(conn, now) if "security" in allowed else None
        out["jobs"] = await _jobs(conn, now) if "jobs" in allowed else None
        out["value_synonyms"] = await _value_synonyms(conn) if "value_synonyms" in allowed else None
    return out
