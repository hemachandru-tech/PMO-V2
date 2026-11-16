"""Application factory (entry point ``tela_api.main:create_app``)."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from tela_api import __version__, health
from tela_api.agents import router as agents_router
from tela_api.ask import feedback as ask_feedback
from tela_api.ask import repair as plan_repairs_router
from tela_api.ask import router as ask_router
from tela_api.connections import files as file_sources_router
from tela_api.connections import router as connections_router
from tela_api.connections import ssh as ssh_keys_router
from tela_api.content import chat_edit as chat_edit_router
from tela_api.content import custom_sql_api as custom_sql_router
from tela_api.content import dashboard_ai as dashboard_ai_router
from tela_api.content import dashboards as dashboards_router
from tela_api.content import exports as exports_router
from tela_api.content import fields as fields_router
from tela_api.content import groups as dashboard_groups_router
from tela_api.content import planner as planner_router
from tela_api.content import reports as reports_router
from tela_api.content import shares as shares_router
from tela_api.content import starter_admin as starter_admin_router
from tela_api.content import suggestions as suggestions_router
from tela_api.content import v2_endpoints as pmo_v2_router
from tela_api.content import viewer as viewer_router
from tela_api.content import widgets as widgets_router
from tela_api.content import wording
from tela_api.core.db import dispose_engine, init_engine, tenant_tx
from tela_api.core.errors import install_error_handlers
from tela_api.core.middleware import RequestContextMiddleware
from tela_api.core.settings import Settings, get_settings
from tela_api.core.telemetry import setup_tracing
from tela_api.governance import certification as certification_router
from tela_api.home import router as home_router
from tela_api.identity import admin_router, grant_requests, grants, provisioning, scim
from tela_api.identity import router as identity_router
from tela_api.identity.jwt import JwtVerifier
from tela_api.identity.principal import PrincipalResolver
from tela_api.knowledge import conditions as conditions_router
from tela_api.knowledge import curation as curation_router
from tela_api.knowledge import definitions as definitions_router
from tela_api.knowledge import documents as knowledge_router
from tela_api.knowledge import gold as gold_router
from tela_api.knowledge import values as values_router
from tela_api.llm import router as llm_router
from tela_api.llm import runs_router as agent_runs_router
from tela_api.llm.client import GatewayClient
from tela_api.model import assistant as assistant_router
from tela_api.model import builder as builder_router
from tela_api.model import router as model_router
from tela_api.packs import blueprint_router as pack_blueprints_router
from tela_api.packs import router as packs_router
from tela_api.packs import studio as pack_studio
from tela_api.packs import updates as pack_updates_router
from tela_api.platform import branding as branding_router
from tela_api.platform import overview as overview_router
from tela_api.platform import router as platform_router
from tela_api.query import router as query_router
from tela_api.query.cache import MemoryBackend, RedisBackend, ResultCache
from tela_api.query.service import QueryService
from tela_api.runner_transport import RunnerTransport
from tela_common.logging import configure_logging
from tela_common.objectstore import ObjectStore
from tela_common.secrets import assert_strong_key, build_secrets_store


def _runner_transport(settings: Settings) -> RunnerTransport | None:
    """Signed transport to the connector runner; absent when the signing keys are not configured."""
    if not settings.execution_signing_key_pem or not settings.service_signing_key:
        if settings.env not in ("local", "dev", "test"):
            raise SystemExit("execution and service signing keys are required outside development")
        return None
    service_key = assert_strong_key(settings.service_signing_key, name="TELA_SERVICE_SIGNING_KEY")
    return RunnerTransport(settings.runner_url, service_key, settings.execution_signing_key_pem.encode(),
                           timeout_s=settings.query_timeout_seconds + 15)


def _object_store(settings: Settings) -> ObjectStore | None:
    """Exports and uploads (COMP-014); absent when no store is configured (exports then say so)."""
    if not settings.object_store_endpoint and not settings.object_store_access_key:
        return None
    return ObjectStore(settings.object_store_endpoint, settings.object_store_bucket,
                       settings.object_store_access_key, settings.object_store_secret_key,
                       sse=settings.object_store_sse, kms_key_id=settings.object_store_kms_key_id)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging("tela-api", settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        init_engine(settings)
        try:  # the wording check reads the stored system packs (US-231); the pack folders until they are loaded
            async with tenant_tx(None) as conn:
                await wording.load_stored(conn)
        except Exception:
            logging.getLogger(__name__).warning("pack_wording_not_loaded", exc_info=True)
        if settings.redis_url:
            from redis.asyncio import from_url

            app.state.redis = from_url(settings.redis_url)  # type: ignore[no-untyped-call]
            app.state.result_cache.backend = RedisBackend(app.state.redis)
        yield
        await gold_router.stop_runs(app)  # mark gold-suite runs in progress as interrupted (US-184)
        if app.state.runner is not None:
            await app.state.runner.aclose()
        if app.state.gateway is not None:
            await app.state.gateway.aclose()
        await app.state.jwt_verifier.aclose()
        if getattr(app.state, "redis", None) is not None:
            await app.state.redis.aclose()
        await dispose_engine()

    app = FastAPI(
        title="Tela API",
        version=__version__,
        lifespan=lifespan,
        docs_url="/api/docs" if settings.env in ("local", "dev") else None,
        openapi_url="/api/openapi.json",
    )
    app.state.settings = settings
    app.state.jwt_verifier = JwtVerifier(
        issuer=settings.oidc_issuer,
        audience=settings.oidc_audience,
        jwks_url=settings.jwks_url,
        allowed_algs=settings.oidc_allowed_algs,
        access_token_types=settings.oidc_access_token_types,
        cache_seconds=settings.jwks_cache_seconds,
    )
    app.state.principal_resolver = PrincipalResolver(
        groups_claim=settings.oidc_groups_claim, cache_seconds=settings.principal_cache_seconds
    )
    app.state.redis = None
    app.state.eval_tasks = set()  # gold-suite runs in progress (knowledge/gold.py::start_run)
    app.state.idp_sessions = provisioning.IdpSessions(settings)
    app.state.scim_limiter = scim.RateLimiter(settings.scim_rate_per_minute)
    app.state.secrets = build_secrets_store(settings.secrets_backend, settings.secrets_addr, settings.env)
    app.state.runner = _runner_transport(settings)
    app.state.object_store = _object_store(settings)
    app.state.result_cache = ResultCache(MemoryBackend())  # Redis replaces it at start-up when configured
    app.state.query_service = QueryService(app.state.runner, app.state.result_cache, settings)
    app.state.gateway = (GatewayClient(settings.gateway_url, settings.service_signing_key.encode(),
                                       timeout_s=settings.llm_timeout_seconds)
                         if settings.service_signing_key else None)

    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Authorization", "Content-Type", "If-Match", "Idempotency-Key", "X-Request-Id"],
        expose_headers=["ETag", "X-Request-Id"],
    )
    install_error_handlers(app)
    app.add_exception_handler(scim.ScimError, scim.scim_error_handler)

    app.include_router(health.router)
    app.include_router(identity_router.router)
    app.include_router(admin_router.router)
    app.include_router(provisioning.router)
    app.include_router(provisioning.internal)
    app.include_router(grants.router)
    app.include_router(grants.internal)
    app.include_router(grant_requests.me)
    app.include_router(grant_requests.router)
    app.include_router(scim.router)
    app.include_router(connections_router.router)
    app.include_router(ssh_keys_router.router)
    app.include_router(file_sources_router.router)
    app.include_router(file_sources_router.internal)
    app.include_router(model_router.router)
    app.include_router(assistant_router.router)
    app.include_router(builder_router.router)
    app.include_router(packs_router.router)
    app.include_router(pack_blueprints_router.router)
    app.include_router(pack_updates_router.router)
    app.include_router(pack_updates_router.internal)
    for studio_router in pack_studio.ROUTERS:  # Pack Studio, one router per area (US-232)
        app.include_router(studio_router)
    base_openapi = app.openapi

    def openapi() -> dict[str, Any]:  # the pack item models the Studio's forms use (US-232)
        return pack_studio.add_item_schemas(base_openapi())

    app.openapi = openapi  # type: ignore[method-assign]
    app.include_router(query_router.router)
    app.include_router(llm_router.router)
    app.include_router(agent_runs_router.router)
    app.include_router(agent_runs_router.internal)
    app.include_router(agents_router.router)
    app.include_router(viewer_router.router)
    app.include_router(home_router.router)
    app.include_router(ask_router.router)
    app.include_router(ask_feedback.router)
    app.include_router(custom_sql_router.router)
    app.include_router(widgets_router.router)
    app.include_router(fields_router.router)
    app.include_router(certification_router.router)
    app.include_router(planner_router.router)
    app.include_router(reports_router.router)
    app.include_router(chat_edit_router.router)
    app.include_router(suggestions_router.router)
    app.include_router(dashboard_ai_router.router)
    app.include_router(dashboards_router.router)
    app.include_router(dashboard_groups_router.router)
    app.include_router(starter_admin_router.router)
    app.include_router(shares_router.router)
    app.include_router(exports_router.router)
    app.include_router(exports_router.internal)
    app.include_router(knowledge_router.router)
    app.include_router(knowledge_router.internal)
    app.include_router(gold_router.router)
    app.include_router(gold_router.internal)
    app.include_router(values_router.router)
    app.include_router(values_router.internal)
    app.include_router(plan_repairs_router.router)
    app.include_router(curation_router.router)
    app.include_router(definitions_router.router)
    app.include_router(conditions_router.router)
    app.include_router(platform_router.router)
    app.include_router(branding_router.router)
    app.include_router(overview_router.router)
    app.include_router(pmo_v2_router.router)

    setup_tracing(app, "tela-api", settings.otel_exporter_otlp_endpoint)
    return app
