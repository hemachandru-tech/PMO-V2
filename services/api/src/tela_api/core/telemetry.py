"""OpenTelemetry tracing, enabled when an OTLP endpoint is configured (US-010)."""

from __future__ import annotations

from fastapi import FastAPI

from tela_common.observability import setup_tracing as _setup


def setup_tracing(app: FastAPI, service_name: str, endpoint: str | None) -> None:
    _setup(service_name, endpoint, app=app, instrument=("httpx",))
