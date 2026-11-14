"""LLM gateway service (US-011; ADR-008; RULE-019, RULE-021; API-060, API-061).

Order for every call: authenticate caller → policy (task, provider, aggregates,
budget, rate) → mask → leak scan (block = no outbound request) → provider →
output safety + schema check → metadata-only ledger.

Keyed providers (DEC-01) use the tenant's key, read from the secrets store with
the gateway's read-only identity; keys never reach logs, the ledger or callers.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import jsonschema
from fastapi import FastAPI, Header, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from tela_common import service_auth
from tela_common.errors import ErrorCode, TelaError
from tela_common.logging import configure_logging, get_logger
from tela_common.observability import RequestIdMiddleware, instrument_engine, setup_tracing
from tela_common.secrets import SecretNotFound, SecretsStore, assert_strong_key, build_secrets_store
from tela_gateway import __version__
from tela_gateway.ledger import DbLedger, Invocation, LedgerSink
from tela_gateway.masking import Masker
from tela_gateway.metrics import CALLS, POLICY_REFUSALS, PROVIDER_DURATION, TOKENS, render_metrics
from tela_gateway.policy import PolicyEngine
from tela_gateway.providers import EMBEDDERS, KEYED, Credentials, Provider, build_provider
from tela_gateway.scan import output_is_safe, scan_text
from tela_gateway.settings import GatewaySettings, get_settings
from tela_gateway.tenant_settings import DbTenantSettings, MemoryTenantSettings, TenantSettingsSource

log = get_logger(__name__)
SERVICE_AUDIENCE = "tela-llm-gateway"


class Message(BaseModel):
    role: str = Field(pattern="^(system|user|assistant)$")
    content: str = Field(max_length=200_000)


class GenerateIn(BaseModel):
    tenant_id: str
    user_id: str | None = None
    turn_id: str | None = None
    agent: str = Field(pattern=r"^AG-\d{2}$")
    task: str
    messages: list[Message] = Field(min_length=1, max_length=50)
    json_schema: dict[str, Any] | None = None
    max_tokens: int = Field(default=2048, ge=1, le=16_000)
    temperature: float | None = Field(default=None, ge=0, le=1)  # sampled plans for voting (US-182)
    known_names: list[str] = Field(default_factory=list, max_length=500)
    capture: bool = False  # return what was sent (masked) and the raw reply for the agent run log (US-226)


TRACE_CAP = 64_000  # characters of each message and of the reply returned for the run log


def _trace(masked: list[dict[str, str]], *, provider: str, model: str, reply: str | None,
           **extra: Any) -> dict[str, Any]:
    """What went to the model and what came back, for the API's agent run log (DEC-25). Only ever returned to the
    API that sent the request: never logged and never written to the ledger."""
    cut = any(len(m["content"]) > TRACE_CAP for m in masked) or (reply is not None and len(reply) > TRACE_CAP)
    return {"messages": [{"role": m["role"], "content": m["content"][:TRACE_CAP]} for m in masked],
            "reply": reply[:TRACE_CAP] if reply is not None else None, "provider": provider, "model": model,
            "cut": cut, **extra}


_FENCE = re.compile(r"^\s*```[A-Za-z]*[ \t]*\r?\n(.*?)\r?\n?```\s*$", re.DOTALL)


def unfence(text: str) -> str:
    """The JSON inside one Markdown code fence (```json ... ```), or the text as it came. Hosted models that are not
    held to the schema (Gemini, BUG-027) often fence their JSON even when told to answer with JSON only."""
    match = _FENCE.match(text)
    return match.group(1).strip() if match else text


class EmbedIn(BaseModel):
    tenant_id: str
    texts: list[str] = Field(min_length=1, max_length=256)


class RerankIn(BaseModel):
    tenant_id: str
    query: str = Field(min_length=1, max_length=2000)
    texts: list[str] = Field(min_length=1, max_length=64)


def create_app(
    settings: GatewaySettings | None = None,
    *,
    provider: Provider | None = None,
    ledger: LedgerSink | None = None,
    tenant_settings: TenantSettingsSource | None = None,
    secrets: SecretsStore | None = None,
    reranker: Any = None,
) -> FastAPI:
    settings = settings or get_settings()
    configure_logging("tela-llm-gateway", settings.log_level)
    key = assert_strong_key(settings.service_signing_key, name="TELA_SERVICE_SIGNING_KEY")

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = None
        if app.state.ledger is None:
            if not settings.async_database_url:
                raise SystemExit("TELA_DATABASE_URL[_FILE] is required for the invocation ledger")
            from sqlalchemy.ext.asyncio import create_async_engine

            engine = create_async_engine(settings.async_database_url, pool_size=5, pool_pre_ping=True)
            instrument_engine(engine)
            app.state.ledger = DbLedger(engine)
            if app.state.tenant_settings is None:
                app.state.tenant_settings = DbTenantSettings(engine)
        yield
        if engine is not None:
            await engine.dispose()

    app = FastAPI(title="Tela LLM gateway", version=__version__, lifespan=lifespan, docs_url=None)
    app.add_middleware(RequestIdMiddleware)
    setup_tracing("tela-llm-gateway", settings.otel_exporter_otlp_endpoint, app=app)
    app.state.ledger = ledger
    # Injected ledgers (tests) without a settings source fall back to the deployment defaults.
    app.state.tenant_settings = tenant_settings or (MemoryTenantSettings() if ledger is not None else None)
    app.state.policy = PolicyEngine(settings)
    app.state.provider = provider
    app.state.reranker = reranker
    if reranker is None and settings.rerank_url:
        from tela_gateway.providers.rerank import TeiReranker

        app.state.reranker = TeiReranker(settings.rerank_url, timeout=settings.rerank_timeout_seconds)
    app.state.secrets = secrets or (build_secrets_store(settings.secrets_backend, settings.secrets_addr, settings.env)
                                    if settings.secrets_backend else None)

    providers: dict[tuple[str, str | None, str | None], Provider] = {}
    keys: dict[str, tuple[float, Credentials]] = {}

    async def _credentials(tenant: dict[str, Any] | None) -> Credentials:
        ref = (tenant or {}).get("secret_ref")
        store: SecretsStore | None = app.state.secrets
        if not ref or store is None:
            raise TelaError(ErrorCode.PLANNER_UNAVAILABLE, "No API key is set for this model provider.")
        hit = keys.get(ref)
        if hit is None or time.monotonic() - hit[0] > 60:
            try:
                data = await store.get(ref)
            except SecretNotFound as exc:
                raise TelaError(ErrorCode.PLANNER_UNAVAILABLE, "No API key is set for this model provider.") from exc
            hit = (time.monotonic(), Credentials(api_key=data.get("api_key", "")))
            keys[ref] = hit
        # The endpoint is not secret and lives on the settings row, so an edit applies at once.
        return Credentials(api_key=hit[1].api_key, endpoint=(tenant or {}).get("endpoint"))

    async def _provider(name: str, tenant: dict[str, Any] | None) -> Provider:
        if app.state.provider is not None:
            injected: Provider = app.state.provider
            return injected
        creds = await _credentials(tenant) if name in KEYED else None
        key = (name, creds.endpoint if creds else None,
               hashlib.sha256(creds.api_key.encode()).hexdigest() if creds else None)
        if key not in providers:
            if len(providers) >= 64:  # keys rotate; old clients are dropped rather than kept forever
                providers.clear()
            providers[key] = build_provider(name, ollama_url=settings.ollama_url, timeout=settings.llm_timeout_seconds,
                                            credentials=creds, azure_api_version=settings.azure_openai_api_version)
        return providers[key]

    def _authenticate(authorization: str | None) -> dict[str, Any]:
        scheme, _, token = (authorization or "").partition(" ")
        if scheme.lower() != "bearer" or not token:
            raise TelaError(ErrorCode.SIGNATURE_INVALID, "service token required")
        claims: dict[str, Any] = service_auth.verify(key, token, aud=SERVICE_AUDIENCE)
        return claims

    @app.exception_handler(TelaError)
    async def _tela(_: Request, exc: TelaError) -> JSONResponse:
        status = 401 if exc.code == ErrorCode.SIGNATURE_INVALID else exc.status
        return JSONResponse({"error": {"code": exc.code, "message": exc.message, "details": exc.details}},
                            status_code=status)

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz")
    async def readyz() -> dict[str, Any]:
        return {"status": "ok", "llm_enabled": bool(settings.llm_provider)}

    @app.get("/metrics", include_in_schema=False)
    async def metrics() -> Response:
        body, content_type = render_metrics()
        return Response(body, media_type=content_type)

    @app.get("/providers")
    async def list_providers(authorization: str | None = Header(default=None)) -> dict[str, Any]:
        """The deployment allow-list (DEC-01): the API only lets Admins choose from these."""
        _authenticate(authorization)
        return {"allowed": settings.llm_allowed_providers, "default_provider": settings.llm_provider,
                "default_model": settings.llm_model_default,
                "planner_model": settings.llm_model_by_task.get("plan") or settings.llm_model_default,
                "keyed": sorted(KEYED),
                "embedders": sorted(EMBEDDERS), "allow_aggregates": settings.llm_allow_aggregates,
                "embed_dim": settings.embed_dim}

    @app.post("/generate")
    async def generate(body: GenerateIn, authorization: str | None = Header(default=None)) -> dict[str, Any]:
        _authenticate(authorization)
        policy: PolicyEngine = app.state.policy
        ledger_sink: LedgerSink = app.state.ledger
        tenant_source: TenantSettingsSource | None = app.state.tenant_settings
        tenant = await tenant_source.get(body.tenant_id) if tenant_source is not None else None
        try:
            provider_name, model = policy.check(tenant_id=body.tenant_id, user_id=body.user_id, task=body.task,
                                                tenant=tenant)
        except TelaError as exc:
            POLICY_REFUSALS.labels(code=str(exc.code)).inc()
            raise

        masker = Masker()
        masked = [{"role": m.role, "content": masker.mask(m.content, known_names=body.known_names)}
                  for m in body.messages]
        payload_hash = hashlib.sha256(json.dumps(masked, sort_keys=True).encode()).hexdigest()
        base: dict[str, Any] = {
            "tenant_id": body.tenant_id, "user_id": body.user_id, "turn_id": body.turn_id, "agent": body.agent,
            "task": body.task, "provider": provider_name, "model": model, "payload_hash": payload_hash,
        }

        # 1) secrets and injection markers on the raw text (masking could hide them);
        # 2) everything, including PII that masking did not catch, on the masked text.
        reasons: list[str] = []
        for raw, m in zip(body.messages, masked, strict=True):
            reasons.extend(scan_text(raw.content, include_pii=False).reasons)
            reasons.extend(scan_text(m["content"]).reasons)
        if reasons:
            unique = sorted(set(reasons))
            await ledger_sink.write(Invocation(**base, scanner_verdict="blocked", status="blocked",
                                               blocked_reason=",".join(unique)))
            CALLS.labels(provider=provider_name, status="blocked").inc()
            log.warning("gateway.leak_blocked", agent=body.agent, task=body.task, reason_classes=unique)
            details: dict[str, Any] = {"reasons": unique}
            if body.capture:  # the masked text and why it was stopped; never the text before masking
                details["trace"] = _trace(masked, provider=provider_name, model=model, reply=None, scanner="blocked")
            raise TelaError(ErrorCode.LEAK_BLOCKED, "The request was blocked by the data-safety check.", details)

        start = time.perf_counter()
        status = "ok"
        try:
            impl = await _provider(provider_name, tenant)
            sampled: dict[str, Any] = {"temperature": body.temperature} if body.temperature is not None else {}
            if provider_name == "ollama" and body.task in settings.ollama_think_tasks \
                    and model.startswith(tuple(settings.ollama_think_models)):
                sampled["think"] = True
            constrained = body.json_schema if provider_name in settings.llm_constrained_providers else None
            out = await impl.generate(model=model, messages=masked, json_schema=constrained,
                                      max_tokens=body.max_tokens, **sampled)
        except TelaError as exc:
            status = "timeout" if exc.code == ErrorCode.LLM_TIMEOUT else "error"
            CALLS.labels(provider=provider_name, status=status).inc()
            PROVIDER_DURATION.labels(provider=provider_name).observe(time.perf_counter() - start)
            await ledger_sink.write(Invocation(**base, scanner_verdict="clean", status=status,
                                               latency_ms=int((time.perf_counter() - start) * 1000)))
            if body.capture:
                exc.details = {**(exc.details or {}), "trace": _trace(
                    masked, provider=provider_name, model=model, reply=None, scanner="clean", status=status)}
            raise
        latency = int((time.perf_counter() - start) * 1000)
        policy.record_usage(body.tenant_id, out.prompt_tokens + out.completion_tokens)
        PROVIDER_DURATION.labels(provider=provider_name).observe(latency / 1000)
        TOKENS.labels(provider=provider_name, model=out.model, kind="prompt").inc(out.prompt_tokens)
        TOKENS.labels(provider=provider_name, model=out.model, kind="completion").inc(out.completion_tokens)

        usage = {"prompt_tokens": out.prompt_tokens, "completion_tokens": out.completion_tokens}
        parsed: Any = None
        schema_error: str | None = None
        answer = unfence(out.text) if body.json_schema is not None else out.text
        if not output_is_safe(out.text):
            status = "invalid_output"
        elif body.json_schema is not None:
            try:
                parsed = json.loads(answer)
                jsonschema.validate(parsed, body.json_schema)
            except json.JSONDecodeError:
                status, schema_error = "invalid_output", "The answer was not valid JSON."
            except jsonschema.ValidationError as exc:
                status, schema_error = "invalid_output", f"{'/'.join(map(str, exc.absolute_path))}: {exc.message}"

        CALLS.labels(provider=provider_name, status=status).inc()
        await ledger_sink.write(Invocation(**base, scanner_verdict="clean", status=status,
                                           prompt_tokens=out.prompt_tokens, completion_tokens=out.completion_tokens,
                                           latency_ms=latency))
        if status == "invalid_output":
            # A format failure returns the answer and the error so the caller can ask for one fix (US-191); an answer
            # that failed the safety check is never returned.
            details = {"text": out.text[:4000], "error": schema_error[:500]} if schema_error else {}
            if body.capture:  # an answer that failed the safety check is not kept either (RULE-020)
                details["trace"] = _trace(
                    masked, provider=provider_name, model=out.model, scanner="clean", status=status,
                    reply=out.text if schema_error else "discarded: the answer failed the output safety check",
                    usage=usage, think=bool(sampled.get("think")), constrained=constrained is not None)
            raise TelaError(ErrorCode.INVALID_OUTPUT, "The model's answer failed the safety or format check.", details)
        result: dict[str, Any] = {
            "text": answer,
            "json": parsed,
            "provider": provider_name,
            "model": out.model,
            "usage": usage,
            "latency_ms": latency,
        }
        if body.capture:
            result["trace"] = _trace(masked, provider=provider_name, model=out.model, reply=out.text, scanner="clean",
                                     status=status, usage=usage, think=bool(sampled.get("think")),
                                     constrained=constrained is not None)
        return result

    @app.post("/embed")
    async def embed(body: EmbedIn, authorization: str | None = Header(default=None)) -> dict[str, Any]:
        _authenticate(authorization)
        tenant_source: TenantSettingsSource | None = app.state.tenant_settings
        tenant = await tenant_source.get(body.tenant_id) if tenant_source is not None else None
        # The tenant's own provider when it has an embeddings service and a model is chosen; else the deployment's.
        name, model = settings.embed_provider, settings.embed_model
        chosen = ((tenant or {}).get("models") or {}).get("embeddings")
        tenant_provider = (tenant or {}).get("provider")
        if chosen and tenant_provider in EMBEDDERS and tenant_provider in settings.llm_allowed_providers:
            name, model = tenant_provider, chosen
        if name not in EMBEDDERS:
            raise TelaError(ErrorCode.PROVIDER_NOT_ALLOWED, "This embedding provider is not approved.")
        for t in body.texts:
            if not scan_text(t).clean:
                raise TelaError(ErrorCode.LEAK_BLOCKED, "A text was blocked by the data-safety check.")
        impl = await _provider(name, tenant)
        vectors = await impl.embed(model=model, texts=body.texts, dim=settings.embed_dim)
        if len(vectors) != len(body.texts) or any(len(v) != settings.embed_dim for v in vectors):
            raise TelaError(ErrorCode.INVALID_OUTPUT, "Embedding dimension does not match the index.")
        return {"model": model, "provider": name, "dim": settings.embed_dim, "vectors": vectors}

    @app.post("/rerank")
    async def rerank(body: RerankIn, authorization: str | None = Header(default=None)) -> dict[str, Any]:
        """Score retrieved knowledge against the question (US-174). The cross-encoder runs inside the platform, so
        nothing leaves it and no leak scan applies; ``scores`` is null when no reranker is deployed."""
        _authenticate(authorization)
        if app.state.reranker is None:
            return {"model": None, "scores": None}
        started = time.monotonic()
        try:
            scores = await app.state.reranker.rerank(query=body.query, texts=[t[:4000] for t in body.texts])
        except TelaError:
            CALLS.labels(provider="rerank", status="error").inc()
            raise
        PROVIDER_DURATION.labels(provider="rerank").observe(time.monotonic() - started)
        CALLS.labels(provider="rerank", status="ok").inc()
        return {"model": settings.rerank_model, "scores": scores}

    return app
