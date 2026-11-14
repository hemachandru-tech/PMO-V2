"""Runner metrics (US-010). Engine and outcome only: never statements, objects or tenants."""

from __future__ import annotations

from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest

EXECUTIONS = Counter("tela_runner_executions_total", "Statements executed by outcome", ["engine", "outcome"])
EXECUTION_DURATION = Histogram("tela_runner_execution_duration_seconds", "Statement duration", ["engine"])


def render_metrics() -> tuple[bytes, str]:
    return generate_latest(), CONTENT_TYPE_LATEST
