"""Maintenance jobs: partitions, retention sweep (stub until DEC-12), starter pack update check, SCIM reconcile."""

from __future__ import annotations

from typing import Any

from tela_common.logging import get_logger
from tela_worker.api import InternalApi
from tela_worker.celery_app import app
from tela_worker.db import connect

log = get_logger(__name__)


@app.task(name="tela_worker.tasks.maintenance.ensure_partitions")
def ensure_partitions() -> int:
    created = 0
    with connect() as conn, conn.cursor() as cur:
        for table in ("ops.audit_events", "ops.query_executions"):
            cur.execute("SELECT util.ensure_month_partitions(%s::regclass, 2)", (table,))
            row = cur.fetchone()
            created += int(row[0]) if row else 0
        conn.commit()
    log.info("maintenance.partitions", created=created)
    return created


@app.task(name="tela_worker.tasks.maintenance.retention_sweep")
def retention_sweep() -> dict[str, str]:
    # Retention periods are pending DEC-12 (Q-018). The sweep is wired into the
    # schedule now and implemented in US-126.
    log.info("maintenance.retention_sweep", status="not_configured")
    return {"status": "not_configured"}


@app.task(name="tela_worker.tasks.maintenance.agent_runs_sweep")
def agent_runs_sweep() -> dict[str, Any]:
    """Daily (ENH-024, US-229): the API clears agent run LLM text after each tenant's days and deletes runs after 90."""
    api = InternalApi()
    try:
        out = api.post("/api/v1/internal/agent-runs/sweep")
    finally:
        api.close()
    log.info("maintenance.agent_runs_sweep", **out)
    return out


@app.task(name="tela_worker.tasks.maintenance.pack_update_check")
def pack_update_check() -> dict[str, Any]:
    """Daily (US-120): the API tells each tenant's Admins about newer starter pack versions, once per version."""
    api = InternalApi()
    try:
        out = api.post("/api/v1/internal/packs/update-check")
    finally:
        api.close()
    log.info("maintenance.pack_update_check", **out)
    return out


@app.task(name="tela_worker.tasks.maintenance.permission_grant_sweep")
def permission_grant_sweep() -> dict[str, Any]:
    """Daily (US-161): reminders seven days before a permission grant ends, and ``permission.expired`` after.

    Access itself ends at the grant's end date without this job (checked at use).
    """
    api = InternalApi()
    try:
        out = api.post("/api/v1/internal/permission-grants/sweep")
    finally:
        api.close()
    log.info("maintenance.permission_grant_sweep", **out)
    return out


@app.task(name="tela_worker.tasks.maintenance.scim_reconcile", bind=True, max_retries=2)
def scim_reconcile(self: Any) -> dict[str, Any]:
    """Hourly (US-119): the API retries failed sign-outs, fixes provisioning drift and alerts the Admins.

    Failures are retried with backoff (1, then 2 minutes); the API records each tenant's failed run and notifies.
    """
    api = InternalApi()
    try:
        out = api.post("/api/v1/internal/scim/reconcile")
    except Exception as exc:
        log.warning("maintenance.scim_reconcile_failed", attempt=self.request.retries + 1)
        raise self.retry(exc=exc, countdown=60 * 2 ** self.request.retries) from exc
    finally:
        api.close()
    log.info("maintenance.scim_reconcile", **out)
    return out
