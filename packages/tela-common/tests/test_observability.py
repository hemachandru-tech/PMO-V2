"""US-010: one correlation id across services, trace ids in logs, tracing off without an endpoint."""

from __future__ import annotations

import json

import pytest
import structlog
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tela_common.logging import configure_logging, get_logger
from tela_common.observability import (
    REQUEST_ID_HEADER,
    RequestIdMiddleware,
    current_request_id,
    propagation_headers,
    safe_request_id,
    setup_tracing,
)


def _app() -> TestClient:
    app = FastAPI()
    app.add_middleware(RequestIdMiddleware)

    @app.get("/id")
    def read_id() -> dict[str, str | None]:
        return {"id": current_request_id(), "outbound": propagation_headers().get(REQUEST_ID_HEADER)}

    return TestClient(app)


def test_incoming_request_id_is_bound_echoed_and_forwarded() -> None:
    resp = _app().get("/id", headers={REQUEST_ID_HEADER: "req-12345678"})
    assert resp.headers[REQUEST_ID_HEADER] == "req-12345678"
    assert resp.json() == {"id": "req-12345678", "outbound": "req-12345678"}


@pytest.mark.parametrize("bad", ["", "short", "has space in it!!", "x" * 65, "line\nbreak-123456"])
def test_unsafe_request_id_is_replaced(bad: str) -> None:
    new = safe_request_id(bad)
    assert new != bad and 8 <= len(new) <= 64


def test_no_id_outside_a_request() -> None:
    structlog.contextvars.clear_contextvars()
    assert propagation_headers({"a": "b"}) == {"a": "b"}


def test_tracing_is_off_without_endpoint() -> None:
    assert setup_tracing("svc", None) is False
    assert setup_tracing("svc", "") is False


def test_log_lines_carry_request_id_and_active_trace_id(capsys: pytest.CaptureFixture[str]) -> None:
    from opentelemetry.sdk.trace import TracerProvider

    configure_logging("svc", "INFO")
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(request_id="req-12345678")
    tracer = TracerProvider().get_tracer("t")
    with tracer.start_as_current_span("work") as span:
        get_logger("t").info("hello")
        expected = format(span.get_span_context().trace_id, "032x")
    line = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert line["request_id"] == "req-12345678"
    assert line["trace_id"] == expected
