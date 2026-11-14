"""Export jobs (US-095; FEAT-045; technical_document §Background jobs: ``export`` queue, 1 attempt).

``render_export`` asks the API to start the job (the API re-checks access and returns the payload), renders the
file with the format's renderer and hands it back. A refusal from the API is final and already recorded there;
any other failure marks the job failed so the person can retry (FEAT-045 A3). Nothing here logs row values.
"""

from __future__ import annotations

from typing import Any

from tela_common.logging import get_logger
from tela_worker.api import ApiRefused, InternalApi
from tela_worker.celery_app import app
from tela_worker.export import CONTENT_TYPES, RENDERERS
from tela_worker.export import pdf as _pdf  # noqa: F401 - registers the PDF renderer
from tela_worker.export import xlsx as _xlsx  # noqa: F401 - registers the Excel renderer

log = get_logger(__name__)


def run_export(tenant_id: str, job_id: str, api: InternalApi | None = None) -> str:
    own = api is None
    api = api or InternalApi()
    try:
        try:
            payload = api.post(f"/api/v1/internal/exports/{job_id}/start", {"tenant_id": tenant_id})
        except ApiRefused as exc:
            log.info("export.refused", job_id=job_id, code=exc.code)
            return "refused"
        fmt = payload["job"]["format"]
        try:
            render = RENDERERS[fmt]
            data = render(payload)
            api.put_file(f"/api/v1/internal/exports/{job_id}/file", data, CONTENT_TYPES[fmt], {
                "tenant_id": tenant_id, "file_name": payload["file_name"], "row_count": payload["row_count"],
                "excluded_widgets": payload["excluded_widgets"]})
        except ApiRefused as exc:
            log.info("export.refused", job_id=job_id, code=exc.code)
            return "refused"
        except Exception as exc:  # noqa: BLE001 - any renderer failure becomes a visible, retryable job failure
            log.error("export.failed", job_id=job_id, format=fmt, error_type=type(exc).__name__)
            code = "format_unavailable" if isinstance(exc, KeyError) and fmt not in RENDERERS else "render_failed"
            api.post(f"/api/v1/internal/exports/{job_id}/fail", {"tenant_id": tenant_id, "error_code": code})
            return "failed"
        log.info("export.done", job_id=job_id, format=fmt)
        return "done"
    finally:
        if own:
            api.close()


@app.task(name="tela_worker.tasks.exports.render_export", acks_late=True)
def render_export(tenant_id: str, job_id: str) -> str:
    return run_export(tenant_id, job_id)


@app.task(name="tela_worker.tasks.exports.sweep_exports")
def sweep_exports() -> dict[str, Any]:
    api = InternalApi()
    try:
        out = api.post("/api/v1/internal/exports/sweep")
    finally:
        api.close()
    log.info("export.sweep", **out)
    return out


@app.task(name="tela_worker.tasks.exports.sweep_files")
def sweep_files() -> dict[str, Any]:
    """Retention of uploaded Excel/CSV versions (US-108; DEC-07): the API deletes expired, unpinned versions."""
    api = InternalApi()
    try:
        out = api.post("/api/v1/internal/files/sweep")
    finally:
        api.close()
    log.info("files.sweep", **out)
    return out
