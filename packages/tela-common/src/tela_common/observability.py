"""Shared observability helpers (US-010; NFR-017).

One correlation id per request: the edge proxy mints ``x-request-id``, every
service binds it to its log context and forwards it on outbound calls. Traces
are exported over OTLP only when an endpoint is configured; instrumentors are
optional so a service without the ``otel`` extra still runs.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, MutableMapping
from typing import TYPE_CHECKING, Any

import structlog

from tela_common.ids import new_id

if TYPE_CHECKING:  # ASGI types only; the worker has no starlette
    from starlette.types import ASGIApp, Message, Receive, Scope, Send

REQUEST_ID_HEADER = "x-request-id"
_SAFE_ID = re.compile(r"^[A-Za-z0-9\-]{8,64}$")


def safe_request_id(incoming: str | None) -> str:
    """Accept an upstream id only if it is plain and short (it ends up in logs and headers)."""
    return incoming if incoming and _SAFE_ID.match(incoming) else new_id()


def current_request_id() -> str | None:
    value = structlog.contextvars.get_contextvars().get("request_id")
    return str(value) if value else None


def propagation_headers(headers: dict[str, str] | None = None) -> dict[str, str]:
    """Outbound headers carrying the current correlation id (API → gateway/runner, worker → API)."""
    out = dict(headers or {})
    request_id = current_request_id()
    if request_id:
        out[REQUEST_ID_HEADER] = request_id
    return out


class RequestIdMiddleware:
    """Pure ASGI middleware: bind the request id to the log context and echo it on the response."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        incoming = next((v.decode("latin-1") for k, v in scope["headers"] if k == REQUEST_ID_HEADER.encode()), None)
        request_id = safe_request_id(incoming)
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)
        scope.setdefault("state", {})["request_id"] = request_id

        async def send_with_id(message: Message) -> None:
            if message["type"] == "http.response.start":
                message.setdefault("headers", []).append((REQUEST_ID_HEADER.encode(), request_id.encode()))
            await send(message)

        await self.app(scope, receive, send_with_id)


def add_trace_ids(_: Any, __: str, event: MutableMapping[str, Any]) -> MutableMapping[str, Any]:
    """structlog processor: put the active trace id on every log line so logs join traces in Grafana."""
    try:
        from opentelemetry import trace
    except ImportError:
        return event
    ctx = trace.get_current_span().get_span_context()
    if ctx.is_valid:
        event["trace_id"] = format(ctx.trace_id, "032x")
    return event


_TRACING_ON = False


def instrument_engine(engine: Any) -> None:
    """Trace one SQLAlchemy (async or sync) engine; no-op while tracing is off.

    Spans carry the statement text only: bound parameter values never leave the process (RULE-021).
    """
    if not _TRACING_ON:
        return
    from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor

    # The instrumentor pins sqlalchemy < 2.1; statement spans work on 2.1, so skip the version check.
    SQLAlchemyInstrumentor().instrument(engine=getattr(engine, "sync_engine", engine), enable_commenter=False,
                                        skip_dep_check=True)


def setup_tracing(service_name: str, endpoint: str | None, *, app: Any = None,
                  instrument: Iterable[str] = ("httpx",)) -> bool:
    """Start OTLP tracing when ``endpoint`` is set. Returns whether tracing is on.

    ``instrument`` picks from ``httpx``, ``celery``; ``app`` is a FastAPI app. Database engines are
    instrumented one by one with :func:`instrument_engine` (the global SQLAlchemy patch misses engine
    factories that a module imported before tracing started).
    """
    if not endpoint:
        return False
    from opentelemetry import trace
    from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    global _TRACING_ON
    _TRACING_ON = True
    provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint, insecure=True)))
    trace.set_tracer_provider(provider)
    wanted = set(instrument)
    if app is not None:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        FastAPIInstrumentor.instrument_app(app, excluded_urls="healthz,readyz,metrics")
    if "httpx" in wanted:
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
        HTTPXClientInstrumentor().instrument()
    if "celery" in wanted:
        from opentelemetry.instrumentation.celery import CeleryInstrumentor
        CeleryInstrumentor().instrument()  # type: ignore[no-untyped-call]
    return True
