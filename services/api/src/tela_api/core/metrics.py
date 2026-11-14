"""Prometheus metrics skeleton (US-010; technical_document §Error handling, logging and observability)."""

from __future__ import annotations

import os

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
    multiprocess,
)

if os.environ.get("PROMETHEUS_MULTIPROC_DIR"):  # must exist before the first metric is created
    os.makedirs(os.environ["PROMETHEUS_MULTIPROC_DIR"], exist_ok=True)

REQUEST_DURATION = Histogram(
    "tela_http_request_duration_seconds", "API request duration", ["method", "route", "status"]
)
TURN_DURATION = Histogram("tela_turn_duration_seconds", "Ask turn duration")
PLAN_DURATION = Histogram("tela_ask_plan_duration_seconds", "Ask planning time by path (US-204)", ["path"])
QUERY_DURATION = Histogram("tela_query_duration_seconds", "Source query duration", ["engine"])
VALIDATION_REJECTIONS = Counter("tela_validation_rejections_total", "Query validation rejections", ["code"])
SCOPE_DENIALS = Counter("tela_scope_denials_total", "Requests refused because scope could not be applied")
AUTH_FAILURES = Counter("tela_auth_failures_total", "Rejected access tokens", ["reason"])
# LLM tokens are counted where the call happens: tela_gateway_tokens_total (tela_gateway.metrics).
CACHE_HIT_RATIO = Gauge("tela_cache_hit_ratio", "Result cache hit ratio", multiprocess_mode="livemax")
EXPORT_JOBS = Counter("tela_export_jobs_total", "Export jobs", ["status"])
OUTBOX_LAG = Gauge("tela_outbox_lag_seconds", "Age of the oldest unpublished outbox event",
                  multiprocess_mode="livemax")
OUTBOX_RELAY_UP = Gauge("tela_outbox_relay_up", "1 while the worker keeps reporting outbox lag, 0 when it went quiet",
                      multiprocess_mode="livemax")
OUTBOX_LAG_KEY = "tela:metrics:outbox_lag"  # written by the worker relay (tela_worker.tasks.outbox)


async def refresh_outbox_lag(redis: object | None) -> None:
    """Copy the worker's last lag sample into the gauge; no sample inside its TTL = relay down."""
    if redis is None:
        return
    try:
        raw = await redis.get(OUTBOX_LAG_KEY)  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001 - a Redis outage must not break the scrape
        raw = None
    if raw is None:
        OUTBOX_RELAY_UP.set(0)
        return
    OUTBOX_LAG.set(float(raw))
    OUTBOX_RELAY_UP.set(1)


def render_metrics() -> tuple[bytes, str]:
    """Gunicorn runs several workers: with ``PROMETHEUS_MULTIPROC_DIR`` set, aggregate across them."""
    if os.environ.get("PROMETHEUS_MULTIPROC_DIR"):
        registry = CollectorRegistry()
        multiprocess.MultiProcessCollector(registry)  # type: ignore[no-untyped-call]
        return generate_latest(registry), CONTENT_TYPE_LATEST
    return generate_latest(), CONTENT_TYPE_LATEST
