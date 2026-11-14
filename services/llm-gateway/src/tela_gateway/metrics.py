"""Gateway metrics (US-010). Labels are bounded sets: no tenant, user or prompt content."""

from __future__ import annotations

from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest

CALLS = Counter("tela_gateway_calls_total", "Generate calls by outcome", ["provider", "status"])
TOKENS = Counter("tela_gateway_tokens_total", "Tokens through the gateway", ["provider", "model", "kind"])
PROVIDER_DURATION = Histogram("tela_gateway_provider_duration_seconds", "Provider round trip", ["provider"])
POLICY_REFUSALS = Counter("tela_gateway_policy_refusals_total", "Calls refused by policy", ["code"])


def render_metrics() -> tuple[bytes, str]:
    return generate_latest(), CONTENT_TYPE_LATEST
