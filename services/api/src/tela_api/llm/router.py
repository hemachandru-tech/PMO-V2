"""AI settings and usage (API-053, API-054; FEAT-049; US-042).

* Providers are chosen from the deployment allow-list reported by the gateway (DEC-01): Ollama, Anthropic,
  OpenAI, Azure OpenAI (with the tenant's endpoint) and Gemini.
* API keys are write-only: they go straight to the secrets store, one reference per provider, and only ``key_set``
  is returned. Switching to a keyed provider needs its key.
* Changing the embedding model clears the stored vectors; the re-embed job rebuilds them (retrieval uses full
  text meanwhile), because vectors from two models cannot be compared.
* Saving a provider runs a test call through the gateway; a failing test is not saved.
* Egress toggles (samples, aggregates) default off (RULE-019); the API's context builders read them.
* Usage aggregates the metadata-only ledger (DATA-046); costs use configured rates, never fetched ones.
* LLM review (US-239, DEC-28): every agent's rules draft goes to the LLM, which confirms, corrects or asks. When the
  provider changes and the Admin doesn't say otherwise, it is on for a hosted provider and off for a local one.
* Planner fit (US-191, DEC-23): the settings carry the AG-03 option (local or hosted), the recommended and minimum
  planner model for it, and a warning when the current planner model fails the schema check too often.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import text

from tela_api.audit import service as audit
from tela_api.core.db import fetch_all, fetch_one, tenant_tx
from tela_api.core.settings import get_settings
from tela_api.identity.deps import require
from tela_api.identity.permissions import Perm
from tela_api.identity.principal import Principal
from tela_api.llm import planner_fit
from tela_api.llm.client import GatewayClient
from tela_api.llm.review import review_default
from tela_common.errors import ErrorCode, TelaError
from tela_common.secrets import SecretsStore, secret_ref

router = APIRouter(prefix="/api/v1/admin", tags=["ai-settings"])
ROLES = ("planner", "narrator", "advisor", "embeddings")
ENDPOINT = r"^https://[A-Za-z0-9.\-]+(:[0-9]+)?/?$"


def _gateway(request: Request) -> GatewayClient:
    client: GatewayClient | None = getattr(request.app.state, "gateway", None)
    if client is None:
        raise TelaError(ErrorCode.PLANNER_UNAVAILABLE, "The LLM gateway is not configured.")
    return client


async def tenant_llm_settings(conn: Any) -> dict[str, Any]:
    row = await fetch_one(conn, "SELECT * FROM ops.llm_settings")
    return dict(row) if row else {"provider": None, "models": {}, "allow_samples": False,
                                  "allow_aggregates": False, "monthly_token_budget": 0, "rates": {},
                                  "secret_ref": None, "endpoint": None, "last_test": None, "llm_review": False,
                                  "review_agents": {}}


def _out(row: dict[str, Any], catalog: dict[str, Any], planner: dict[str, Any]) -> dict[str, Any]:
    return {"provider": row["provider"], "models": row["models"], "allow_samples": row["allow_samples"],
            "allow_aggregates": row["allow_aggregates"], "monthly_token_budget": row["monthly_token_budget"],
            "rates": row["rates"], "key_set": bool(row["secret_ref"]), "endpoint": row.get("endpoint"),
            "last_test": row["last_test"], "llm_review": row["llm_review"],
            "llm_review_default": review_default(row["provider"]), "allowed_providers": catalog.get("allowed", []),
            "keyed_providers": catalog.get("keyed", []), "embedding_providers": catalog.get("embedders", []),
            "aggregates_allowed": get_settings().llm_allow_aggregates, "planner_health": planner}


def _embedding_choice(provider: str | None, models: Mapping[Any, Any], embedders: list[str]) -> tuple[str, str] | None:
    """The gateway's rule: the tenant's provider embeds when it can and a model is chosen; None = deployment's."""
    model = (models or {}).get("embeddings")
    return (provider, model) if provider and model and provider in embedders else None


async def _catalog(request: Request) -> dict[str, Any]:
    try:
        out: dict[str, Any] = await _gateway(request).providers()
    except TelaError:
        return {}
    return out


@router.get("/llm-settings")
async def get_llm_settings(request: Request,
                           principal: Principal = Depends(require(Perm.SETTINGS_MANAGE))) -> dict[str, Any]:
    catalog = await _catalog(request)
    async with tenant_tx(principal.tenant_id) as conn:
        row = await tenant_llm_settings(conn)
        return _out(row, catalog, await _planner(conn, row, catalog))


async def _planner(conn: Any, row: dict[str, Any], catalog: dict[str, Any]) -> dict[str, Any]:
    """Planner health for the model AG-03 uses: the tenant's, else the deployment's default."""
    if row["provider"]:
        return await planner_fit.health(conn, row["provider"], (row["models"] or {}).get("planner"))
    return await planner_fit.health(conn, catalog.get("default_provider"),
                                    catalog.get("planner_model") or catalog.get("default_model"))


class Rate(BaseModel):
    input_per_mtok: float = Field(ge=0, le=1000)
    output_per_mtok: float = Field(ge=0, le=1000)


class SettingsIn(BaseModel):
    provider: str | None = Field(default=None, max_length=40)
    models: dict[Literal["planner", "narrator", "advisor", "embeddings"], str] = Field(default_factory=dict)
    allow_samples: bool = False
    allow_aggregates: bool = False
    monthly_token_budget: int = Field(default=0, ge=0, le=10_000_000_000)
    rates: dict[str, Rate] = Field(default_factory=dict)
    api_key: str | None = Field(default=None, min_length=8, max_length=500)
    endpoint: str | None = Field(default=None, max_length=200, pattern=ENDPOINT)
    llm_review: bool | None = None  # None: keep it, or the provider's default when the provider changes


@router.put("/llm-settings")
async def put_settings(body: SettingsIn, request: Request,
                       principal: Principal = Depends(require(Perm.SETTINGS_MANAGE))) -> dict[str, Any]:
    gateway = _gateway(request)
    catalog = await gateway.providers()
    allowed: list[str] = catalog["allowed"]
    keyed: list[str] = catalog.get("keyed", [])
    if body.provider and body.provider not in allowed:
        raise TelaError(ErrorCode.PROVIDER_NOT_ALLOWED, "This model provider is not approved for this deployment.",
                        {"allowed": allowed})
    if body.provider and not body.models.get("planner"):
        raise TelaError(ErrorCode.VALIDATION_ERROR, "Choose a planner model for the provider.")
    if body.provider == "azure_openai" and not body.endpoint:
        raise TelaError(ErrorCode.VALIDATION_ERROR,
                        "Enter the Azure OpenAI endpoint (https://<resource>.openai.azure.com).")
    if body.allow_aggregates and not get_settings().llm_allow_aggregates:
        raise TelaError(ErrorCode.VALIDATION_ERROR,
                        "This deployment does not allow totals and counts to reach a model.")
    async with tenant_tx(principal.tenant_id) as conn:
        before = await tenant_llm_settings(conn)
    # One key per provider, so switching providers never sends one provider's key to another.
    ref = secret_ref(principal.tenant_id, "llm", body.provider) if body.provider in keyed else None
    keep_ref = before["secret_ref"] if before["provider"] == body.provider else None
    if ref and not body.api_key and keep_ref != ref:
        raise TelaError(ErrorCode.VALIDATION_ERROR, f"Enter the API key for {body.provider}.")
    review = body.llm_review
    if review is None:
        review = before["llm_review"] if before["provider"] == body.provider else review_default(body.provider)
    if ref and body.api_key:
        store: SecretsStore = request.app.state.secrets
        await store.put(ref, {"api_key": body.api_key})
    async with tenant_tx(principal.tenant_id) as conn:
        await conn.execute(
            text("""
                INSERT INTO ops.llm_settings (tenant_id, provider, models, allow_samples, allow_aggregates,
                  monthly_token_budget, rates, secret_ref, endpoint, llm_review, updated_by)
                VALUES (:t, :p, CAST(:m AS jsonb), :s, :a, :b, CAST(:r AS jsonb), :ref, :e, :rv, :by)
                ON CONFLICT (tenant_id) DO UPDATE SET provider = EXCLUDED.provider, models = EXCLUDED.models,
                  allow_samples = EXCLUDED.allow_samples, allow_aggregates = EXCLUDED.allow_aggregates,
                  monthly_token_budget = EXCLUDED.monthly_token_budget, rates = EXCLUDED.rates,
                  secret_ref = EXCLUDED.secret_ref, endpoint = EXCLUDED.endpoint,
                  llm_review = EXCLUDED.llm_review, updated_by = EXCLUDED.updated_by
            """),
            {"t": principal.tenant_id, "p": body.provider, "m": json.dumps(body.models),
             "s": body.allow_samples, "a": body.allow_aggregates, "b": body.monthly_token_budget,
             "r": json.dumps({k: v.model_dump() for k, v in body.rates.items()}), "ref": ref,
             "e": body.endpoint if body.provider == "azure_openai" else None, "rv": review,
             "by": principal.user_id},
        )
    # Test call through the gateway (credentials and model reachable). A failure rolls the change back.
    test: dict[str, Any] = {"ok": True, "tested_at": datetime.now(UTC).isoformat()}
    if body.provider:
        try:
            out = await gateway.generate(tenant_id=principal.tenant_id, user_id=principal.user_id, agent="AG-01",
                                         task="intent", messages=[{"role": "user", "content": "Reply with OK."}],
                                         max_tokens=8)
            test.update(latency_ms=out.get("latency_ms"), model=out.get("model"))
        except TelaError as exc:
            test = {"ok": False, "code": exc.code, "message": exc.message, "tested_at": test["tested_at"]}
    async with tenant_tx(principal.tenant_id) as conn:
        if not test["ok"]:
            await conn.execute(
                text("UPDATE ops.llm_settings SET provider = :p, models = CAST(:m AS jsonb), secret_ref = :ref, "
                     "endpoint = :e, llm_review = :rv, last_test = CAST(:lt AS jsonb)"),
                {"p": before["provider"], "m": json.dumps(before["models"]), "ref": before["secret_ref"],
                 "e": before.get("endpoint"), "rv": before["llm_review"], "lt": json.dumps(test)})
        else:
            await conn.execute(text("UPDATE ops.llm_settings SET last_test = CAST(:lt AS jsonb)"),
                               {"lt": json.dumps(test)})
        await audit.record(
            conn, tenant_id=principal.tenant_id, action="llm.settings.changed", object_type="llm_settings",
            object_id=principal.tenant_id, outcome="success" if test["ok"] else "failure",
            actor_id=principal.user_id, severity="high", correlation_id=getattr(request.state, "request_id", None),
            details={"provider": body.provider, "models": body.models, "allow_samples": body.allow_samples,
                     "allow_aggregates": body.allow_aggregates, "key_changed": bool(body.api_key),
                     "endpoint": body.endpoint, "llm_review": review, "test_ok": test["ok"]},
        )
        embedders: list[str] = catalog.get("embedders", [])
        if test["ok"] and (_embedding_choice(before["provider"], before["models"], embedders)
                           != _embedding_choice(body.provider, body.models, embedders)):
            await clear_embeddings(conn)
        row = await tenant_llm_settings(conn)
        planner = await _planner(conn, row, catalog)
        if test["ok"] and (before["provider"], before["models"]) != (body.provider, body.models):
            # A provider or model change re-runs the gold set (US-057); it warns, never blocks.
            from tela_api.knowledge.gold import queue_run

            await queue_run(conn, principal.tenant_id, principal.user_id, trigger="llm_settings")
    if not test["ok"]:
        raise TelaError(ErrorCode.PLANNER_UNAVAILABLE, "The test call failed, so the provider was not saved: "
                        + str(test.get("message")), {"test": test})
    return _out(row, catalog, planner)


async def clear_embeddings(conn: Any) -> None:
    """Vectors of another embedding model cannot be compared: drop them; the re-embed job (every 20 min) rebuilds."""
    await conn.execute(text("UPDATE rag.knowledge_chunks SET embedding = NULL WHERE embedding IS NOT NULL"))
    await conn.execute(text("UPDATE rag.knowledge_documents SET embedding_model = NULL"))
    await conn.execute(text("UPDATE rag.gold_examples SET embedding = NULL, embedding_model = NULL "
                            "WHERE embedding IS NOT NULL"))


@router.get("/usage")
async def usage(group_by: Literal["model", "user", "task", "day"] = "model", days: int = 30,
                principal: Principal = Depends(require(Perm.SETTINGS_MANAGE))) -> dict[str, Any]:
    days = max(1, min(days, 366))
    column = {"model": "provider || ':' || model", "user": "coalesce(user_id::text, 'system')",
              "task": "task", "day": "to_char(created_at, 'YYYY-MM-DD')"}[group_by]
    async with tenant_tx(principal.tenant_id) as conn:
        settings = await tenant_llm_settings(conn)
        rows = await fetch_all(
            conn,
            f"""
            SELECT {column} AS key, model, count(*) AS calls,
                   coalesce(sum(prompt_tokens), 0) AS prompt_tokens,
                   coalesce(sum(completion_tokens), 0) AS completion_tokens,
                   count(*) FILTER (WHERE status = 'blocked') AS blocked,
                   count(*) FILTER (WHERE status NOT IN ('ok', 'blocked')) AS failed,
                   percentile_cont(0.5) WITHIN GROUP (ORDER BY latency_ms) AS p50_ms,
                   percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms) AS p95_ms
            FROM ops.llm_invocations WHERE created_at >= :since
            GROUP BY 1, 2 ORDER BY 1
            """,  # noqa: S608 - column comes from a fixed mapping, never from input
            {"since": datetime.now(UTC) - timedelta(days=days)},
        )
    rates = settings["rates"] or {}
    out: dict[str, dict[str, Any]] = {}
    for r in rows:
        item = out.setdefault(r["key"], {"key": r["key"], "calls": 0, "prompt_tokens": 0, "completion_tokens": 0,
                                         "blocked": 0, "failed": 0, "estimated_cost": 0.0,
                                         "p50_ms": r["p50_ms"], "p95_ms": r["p95_ms"]})
        for k in ("calls", "prompt_tokens", "completion_tokens", "blocked", "failed"):
            item[k] += int(r[k])
        rate = rates.get(r["model"]) or {}
        item["estimated_cost"] += (int(r["prompt_tokens"]) * float(rate.get("input_per_mtok", 0))
                                   + int(r["completion_tokens"]) * float(rate.get("output_per_mtok", 0))) / 1e6
    return {"group_by": group_by, "days": days, "items": list(out.values()),
            "cost_note": "Estimated from the configured rates, not from provider bills."}
