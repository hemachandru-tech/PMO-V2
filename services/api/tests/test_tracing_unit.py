"""US-010: SQL spans carry statements, never bound parameter values (RULE-021); httpx forwards trace context."""

from __future__ import annotations

import pytest
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from sqlalchemy import create_engine, text


@pytest.fixture
def spans():
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    engine = create_engine("sqlite://")
    SQLAlchemyInstrumentor().instrument(engine=engine, tracer_provider=provider, enable_commenter=False,
                                        skip_dep_check=True)
    yield exporter, engine
    SQLAlchemyInstrumentor().uninstrument()


def test_sql_spans_have_statements_but_not_parameter_values(spans) -> None:
    exporter, engine = spans
    with engine.connect() as conn:
        conn.execute(text("SELECT :secret"), {"secret": "SECRET-VALUE-123"})
    finished = exporter.get_finished_spans()
    assert any(s.attributes and "SELECT" in str(s.attributes.get("db.statement")) for s in finished)
    assert "SECRET-VALUE-123" not in repr([dict(s.attributes or {}) for s in finished])


def test_httpx_instrumentation_injects_traceparent() -> None:
    import httpx

    provider = TracerProvider()
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(request.headers)
        return httpx.Response(200)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    HTTPXClientInstrumentor.instrument_client(client, tracer_provider=provider)
    with provider.get_tracer("t").start_as_current_span("outer"):
        client.get("http://gateway/healthz")
    assert "traceparent" in seen
