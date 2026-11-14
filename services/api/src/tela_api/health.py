"""Liveness, readiness and metrics (API-055; REC-012).

``/healthz`` checks only that the process serves requests. ``/readyz`` checks
dependencies. The edge proxy must not expose ``/readyz`` or ``/metrics``.
Object storage is reported but does not gate readiness: without it uploads and
exports fail, while dashboards and Ask keep working (API-068).
"""

from __future__ import annotations

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy import text

from tela_api.core.db import get_engine
from tela_api.core.metrics import refresh_outbox_lag, render_metrics

router = APIRouter(tags=["health"])


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/readyz")
async def readyz(request: Request) -> JSONResponse:
    checks: dict[str, str] = {}
    try:
        async with get_engine().connect() as conn:
            await conn.execute(text("SELECT 1"))
        checks["db"] = "ok"
    except Exception:  # noqa: BLE001 - readiness reports, never raises
        checks["db"] = "fail"

    redis = getattr(request.app.state, "redis", None)
    if redis is not None:
        try:
            await redis.ping()
            checks["redis"] = "ok"
        except Exception:  # noqa: BLE001
            checks["redis"] = "fail"

    secrets = getattr(request.app.state, "secrets", None)
    if secrets is not None:
        checks["secrets"] = "ok" if await secrets.healthy() else "fail"

    ok = all(v == "ok" for v in checks.values())

    store = getattr(request.app.state, "object_store", None)
    if store is not None:
        checks["storage"] = "ok" if await store.healthy() else "degraded"
    return JSONResponse({"status": "ok" if ok else "not_ready", "checks": checks}, status_code=200 if ok else 503)


@router.get("/metrics", include_in_schema=False)
async def metrics(request: Request) -> Response:
    await refresh_outbox_lag(getattr(request.app.state, "redis", None))
    body, content_type = render_metrics()
    return Response(body, media_type=content_type)
