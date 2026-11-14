"""AG-03 planner model fit (US-191; DEC-23: Local and Hosted are both offered).

* **Option** — a tenant plans with a local model (Ollama: nothing leaves the network) or a hosted DEC-01 provider
  (the question, model metadata and knowledge, never rows). Each option has a recommended model and a minimum,
  from the US-191 measurements (EXEC-109).
* **Serial providers** — a local Ollama runs vote samples one after another, so three samples take three times as
  long; voting drops to one sample there (``ask_vote_serial_providers``).
* **Health** — the share of the current planner model's AG-03 calls that failed the schema check in the ledger
  (``ops.llm_invocations``, DATA-046); AI settings warn when it is too high.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from tela_api.core.db import fetch_all, fetch_one
from tela_api.core.settings import get_settings
from tela_api.llm.client import GatewayClient
from tela_common.errors import TelaError

LOCAL = ("ollama", "local")


def option(provider: str | None) -> str | None:
    """``local`` or ``hosted`` for a provider; None when no model is configured."""
    if not provider:
        return None
    return "local" if provider in LOCAL else "hosted"


async def planner_provider(conn: Any, gateway: GatewayClient | None) -> str | None:
    """The provider AG-03 calls go to: the tenant's choice, else the deployment's default."""
    row = await fetch_one(conn, "SELECT provider FROM ops.llm_settings")
    if row and row["provider"]:
        return str(row["provider"])
    if gateway is None:
        return None
    try:
        catalog = await gateway.catalog()
    except TelaError:
        return None
    default = catalog.get("default_provider")
    return str(default) if default else None


def vote_samples(provider: str | None) -> int:
    """How many AG-03 samples to vote on: one on a provider that runs them one after another (local Ollama)."""
    s = get_settings()
    return 1 if provider in s.ask_vote_serial_providers else s.ask_vote_samples


def recommendation(opt: str | None) -> dict[str, str | None]:
    s = get_settings()
    if opt is None:
        return {"recommended": None, "minimum": None}
    return {"recommended": s.planner_recommended.get(opt), "minimum": s.planner_minimum.get(opt)}


async def health(conn: Any, provider: str | None, model: str | None) -> dict[str, Any]:
    """The planner model's AG-03 calls over the last ``planner_health_days`` days and whether to warn."""
    s = get_settings()
    opt = option(provider)
    out: dict[str, Any] = {"option": opt, "provider": provider, "model": model, "days": s.planner_health_days,
                           "calls": 0, "schema_failures": 0, "timeouts": 0, "failure_rate": None, "warn": False,
                           **recommendation(opt)}
    if not provider or not model:
        return out
    since = datetime.now(UTC) - timedelta(days=s.planner_health_days)
    rows = await fetch_all(conn, """
        SELECT status, count(*) AS n FROM ops.llm_invocations
        WHERE agent = 'AG-03' AND provider = :p AND model = :m AND created_at >= :since
        GROUP BY status""", {"p": provider, "m": model, "since": since})
    by = {r["status"]: int(r["n"]) for r in rows}
    calls = sum(by.values()) - by.get("blocked", 0)
    out.update(calls=calls, schema_failures=by.get("invalid_output", 0), timeouts=by.get("timeout", 0))
    if calls:
        out["failure_rate"] = round(out["schema_failures"] / calls, 3)
        out["warn"] = calls >= s.planner_health_min_calls and out["failure_rate"] >= s.planner_schema_warn_rate
    return out
