"""Connector runner service (COMP-005; US-023).

The only component that reaches customer sources. Every request must carry

1. a short-lived service token (HMAC) proving the caller is the API, and
2. an Ed25519-signed envelope minted by the API. The runner holds only the
   public key, so it can verify instructions but never create them.

For ``/execute`` the runner also recomputes the statement hash, re-checks the
statement with its own guard (one read-only SELECT over the signed object
list), refuses schema drift on those objects, re-verifies read-only status
(cached ≤ 60 s) and fetches credentials from the secrets store with its own
identity. Results are masked before they leave (RULE-020). Sources behind a bastion are reached through an
SSH tunnel whose key also comes from the secrets store (ENH-003, ``tunnel.py``).
"""

from __future__ import annotations

import asyncio
import contextlib
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

from tela_common import envelope as env
from tela_common import service_auth
from tela_common.errors import ErrorCode, TelaError
from tela_common.logging import configure_logging, get_logger
from tela_common.observability import RequestIdMiddleware, setup_tracing
from tela_common.schema_checksum import columns_checksum
from tela_common.secrets import SecretsStore, assert_strong_key, build_secrets_store
from tela_runner import __version__
from tela_runner.adapters import build_adapters
from tela_runner.adapters.base import SCHEMA_CHANGED_MESSAGE, ConnectionInfo, ConnectorAdapter, Limits
from tela_runner.guards import mask_rows
from tela_runner.metrics import EXECUTION_DURATION, EXECUTIONS, render_metrics
from tela_runner.mongo_guard import check_pipeline
from tela_runner.sql_guard import check_statement
from tela_runner.tunnel import SshTunnelError, TunnelManager, library_refs
from tela_runner.webapi_guard import check_webapi

SERVICE_AUDIENCE = "tela-connector-runner"
FILE_ENGINES = ("excel", "csv")
DIALECT = {"postgres": "postgres", "mysql": "mysql", "sqlserver": "tsql", "dataverse": "tsql", "excel": "duckdb",
           "csv": "duckdb"}
log = get_logger(__name__)


def _read(name: str) -> bytes | None:
    path = os.environ.get(f"{name}_FILE")
    if path:
        return Path(path).read_bytes().strip()
    raw = os.environ.get(name)
    return raw.encode() if raw else None


def _file_store() -> Any:
    """Read-only object store access for uploaded Excel/CSV files (US-109). None when not configured."""
    endpoint = os.environ.get("TELA_OBJECT_STORE_ENDPOINT")
    bucket = os.environ.get("TELA_OBJECT_STORE_BUCKET")
    if not bucket:
        return None
    from tela_common.objectstore import ObjectStore

    secret = _read("TELA_OBJECT_STORE_SECRET_KEY")
    return ObjectStore(endpoint or None, bucket, os.environ.get("TELA_OBJECT_STORE_ACCESS_KEY"),
                       secret.decode() if secret else None)


class RunnerRequest(BaseModel):
    envelope: dict[str, Any]
    statement: str | None = None


def decode_params(raw: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for name, item in raw.items():
        t, v = item.get("t"), item.get("v")
        if v is None:
            out[name] = None
        elif t == "date":
            out[name] = date.fromisoformat(v)
        elif t == "datetime":
            out[name] = datetime.fromisoformat(v)
        elif t == "decimal":
            out[name] = Decimal(v)
        elif t in ("str", "int", "float", "bool"):
            out[name] = {"str": str, "int": int, "float": float, "bool": bool}[t](v)
        else:
            raise TelaError(ErrorCode.SIGNATURE_INVALID, "unsupported parameter type")
    return out


def create_app(*, secrets: SecretsStore | None = None, adapters: dict[str, ConnectorAdapter] | None = None) -> FastAPI:
    configure_logging("tela-connector-runner", os.environ.get("TELA_LOG_LEVEL", "INFO"))
    service_key = assert_strong_key(_read("TELA_SERVICE_SIGNING_KEY"), name="TELA_SERVICE_SIGNING_KEY")
    verify_pem = _read("TELA_EXECUTION_VERIFY_KEY")
    if not verify_pem:
        raise SystemExit("TELA_EXECUTION_VERIFY_KEY(_FILE) is required; refusing to start")
    verify_key = env.load_public_key(verify_pem)
    tela_env = os.environ.get("TELA_ENV", "local")
    store = secrets or build_secrets_store(os.environ.get("TELA_SECRETS_BACKEND", "file"),
                                           os.environ.get("TELA_SECRETS_ADDR"), tela_env)
    engines = adapters or build_adapters(
        store=_file_store(),
        concurrency=int(os.environ.get("TELA_RUNNER_CONCURRENCY_PER_CONNECTION", "4")),
        read_only_ttl_s=int(os.environ.get("TELA_READONLY_CHECK_TTL_SECONDS", "60")),
        allow_insecure_tls=tela_env in ("local", "dev", "test"),
        max_pools=int(os.environ.get("TELA_RUNNER_MAX_POOLS", "32")),
    )
    tunnels = TunnelManager(idle_s=int(os.environ.get("TELA_RUNNER_SSH_IDLE_SECONDS", "900")))

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        for adapter in engines.values():
            await adapter.close()
        await tunnels.close()

    app = FastAPI(title="Tela connector runner", version=__version__, docs_url=None, openapi_url=None,
                  lifespan=lifespan)
    app.state.adapters = engines
    app.add_middleware(RequestIdMiddleware)
    setup_tracing("tela-connector-runner", os.environ.get("TELA_OTEL_EXPORTER_OTLP_ENDPOINT") or None, app=app)

    @app.exception_handler(TelaError)
    async def _tela(_: Any, exc: TelaError) -> JSONResponse:
        status = 401 if exc.code == ErrorCode.SIGNATURE_INVALID else exc.status
        return JSONResponse({"error": {"code": exc.code, "message": exc.message}}, status_code=status)

    def authorise(authorization: str | None, body: RunnerRequest, op: str) -> dict[str, Any]:
        scheme, _, token = (authorization or "").partition(" ")
        if scheme.lower() != "bearer":
            raise TelaError(ErrorCode.SIGNATURE_INVALID, "service token required")
        service_auth.verify(service_key, token, aud=SERVICE_AUDIENCE)
        payload = env.verify(verify_key, body.envelope, statement=body.statement if op == "execute" else None)
        if payload.get("op") != op:
            raise TelaError(ErrorCode.SIGNATURE_INVALID, "envelope is for a different operation")
        return payload

    async def connection_info(p: dict[str, Any]) -> tuple[ConnectorAdapter, ConnectionInfo, dict[str, Any] | None]:
        """The adapter, the connection info it uses, and the SSH report when the source is tunnelled."""
        adapter = engines.get(p.get("engine", ""))
        if adapter is None:
            raise TelaError(ErrorCode.ENGINE_NOT_SUPPORTED, "This database engine is not supported yet.")
        descriptor = dict(p.get("descriptor") or {})
        if p["engine"] in FILE_ENGINES:  # no account: the uploaded file, inside the tenant's prefix (US-108)
            key = str((descriptor.get("file") or {}).get("object_key") or "")
            if not key.startswith(f"tenant/{p['tenant_id']}/files/") or ".." in key:
                raise TelaError(ErrorCode.SIGNATURE_INVALID, "file reference outside the tenant")
            return adapter, ConnectionInfo(p["connection_id"], p["engine"], descriptor, {}), None
        ref = p.get("secret_ref") or ""
        if not ref.startswith(f"tenants/{p['tenant_id']}/connections/"):
            raise TelaError(ErrorCode.SIGNATURE_INVALID, "secret reference outside the tenant")
        creds = await store.get(ref)
        if not descriptor.get("ssh"):
            return adapter, ConnectionInfo(p["connection_id"], p["engine"], descriptor, creds), None
        creds = dict(creds)
        for key_id, key_ref in library_refs(str(p["tenant_id"]), descriptor).items():  # shared keys (ENH-004)
            try:
                lib = await store.get(key_ref)
            except TelaError:
                raise SshTunnelError("auth", "An SSH key from the library is missing. Choose another key or upload "
                                     "it again on Admin → Data → SSH keys.") from None
            creds[f"lib_{key_id}_private_key"] = lib.get("private_key", "")
            creds[f"lib_{key_id}_certificate"] = lib.get("certificate", "")
        info, report = await tunnels.route(ConnectionInfo(p["connection_id"], p["engine"], descriptor, creds))
        return adapter, info, report

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/metrics", include_in_schema=False)
    async def metrics() -> Response:
        body, content_type = render_metrics()
        return Response(body, media_type=content_type)

    @app.get("/readyz")
    async def readyz() -> JSONResponse:
        ok = await store.healthy()
        return JSONResponse({"status": "ok" if ok else "degraded", "adapters": sorted(engines), "secrets": ok},
                            status_code=200 if ok else 503)

    @app.post("/test")
    async def test(body: RunnerRequest, authorization: str | None = Header(default=None)) -> dict[str, Any]:
        p = authorise(authorization, body, "test")
        try:
            adapter, info, report = await connection_info(p)
        except SshTunnelError as exc:  # the database was never reached: report the SSH step that failed
            return {"reachable": False, "authenticated": False, "latency_ms": None, "server_version": None,
                    "error": exc.message, "read_only": {"verdict": "unsafe", "checks": [], "reasons": []},
                    "ssh": exc.report()}
        result = await adapter.test_connection(info)
        ssh = {"ssh": report} if report else {}
        return {
            "reachable": result.reachable, "authenticated": result.authenticated, "latency_ms": result.latency_ms,
            "server_version": result.server_version, "error": result.error,
            "read_only": {"verdict": result.read_only.verdict, "checks": result.read_only.checks,
                          "reasons": result.read_only.reasons},
            "capabilities": adapter.capabilities(), **result.extra, **ssh,
        }

    @app.post("/introspect")
    async def introspect(body: RunnerRequest, authorization: str | None = Header(default=None)) -> dict[str, Any]:
        p = authorise(authorization, body, "introspect")
        adapter, info, _ = await connection_info(p)
        verdict = await adapter.verify_read_only(info)
        if not verdict.safe:
            raise TelaError(ErrorCode.READ_ONLY_VIOLATION, "The data source account is not verifiably read-only.")
        catalog = await adapter.introspect(info, max_objects=int(p.get("max_objects", 2000)))
        return {
            "complete": catalog.complete, "checksum": catalog.checksum, "schemas": catalog.schemas,
            "samples_stored": False, "profile": catalog.profile,
            "objects": [{
                "qualified_name": o.qualified_name, "kind": o.kind, "columns": o.columns,
                "primary_key": o.primary_key, "foreign_keys": o.foreign_keys, "row_estimate": o.row_estimate,
                "checksum": o.checksum, "readable": o.readable,
            } for o in catalog.objects],
        }

    @app.post("/execute")
    async def execute(body: RunnerRequest, request: Request,
                      authorization: str | None = Header(default=None)) -> dict[str, Any]:
        p = authorise(authorization, body, "execute")
        assert body.statement is not None
        allowed: dict[str, dict[str, Any]] = {k.lower(): v or {} for k, v in (p.get("allowed_objects") or {}).items()}
        if p["engine"] == "mongodb":
            check_pipeline(body.statement, set(allowed))
        elif p["engine"] == "dataverse" and (p.get("descriptor") or {}).get("mode") == "webapi":
            check_webapi(body.statement, set(allowed))
        else:
            check_statement(body.statement, DIALECT.get(p["engine"], "postgres"), set(allowed))
        adapter, info, _ = await connection_info(p)
        verdict = await adapter.verify_read_only(info)
        if not verdict.safe:
            raise TelaError(ErrorCode.READ_ONLY_VIOLATION, "The data source account is no longer verifiably read-only.")
        expected = {k: v for k, v in allowed.items() if v.get("checksum")}
        if expected:
            current = await adapter.object_columns(info, sorted(expected))
            drifted = []
            for name, spec in expected.items():
                wanted = {c.lower() for c in spec.get("columns") or []}
                live = [(c, t) for c, t in current.get(name, []) if c.lower() in wanted]
                if len(live) != len(wanted) or columns_checksum(live) != spec["checksum"]:
                    drifted.append(name)
            if drifted:
                log.warning("runner.schema_drift", objects=len(drifted))
                raise TelaError(ErrorCode.SCHEMA_CHANGED, SCHEMA_CHANGED_MESSAGE)
        lim = p.get("limits") or {}
        limits = Limits(max_rows=min(int(lim.get("max_rows", 10_000)), 10_000),
                        timeout_ms=min(int(lim.get("timeout_ms", 20_000)), 20_000),
                        max_bytes=min(int(lim.get("max_bytes", 10 * 1024 * 1024)), 10 * 1024 * 1024))
        execution_id = str(p["execution_id"])
        queried_at = datetime.now(UTC)
        task = asyncio.create_task(adapter.execute(info, body.statement, decode_params(p.get("params") or {}),
                                                   limits, execution_id))
        while not task.done():
            done, _ = await asyncio.wait({task}, timeout=0.25)
            if not done and await request.is_disconnected():  # NFR-026: cancel on client abort
                await adapter.cancel(execution_id)
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, TelaError):
                    await task
                raise TelaError(ErrorCode.QUERY_TIMEOUT, "The request was cancelled.")
        try:
            result = task.result()
        except Exception:
            EXECUTIONS.labels(engine=p["engine"], outcome="error").inc()
            raise
        EXECUTIONS.labels(engine=p["engine"], outcome="truncated" if result.truncated else "ok").inc()
        EXECUTION_DURATION.labels(engine=p["engine"]).observe(result.duration_ms / 1000)
        names = [c["name"] for c in result.columns]
        masked = mask_rows(names, result.rows, p.get("column_tags") or {})
        log.info("runner.executed", rows=len(result.rows), truncated=result.truncated,
                 duration_ms=result.duration_ms, statement_sha256=p["statement_sha256"][:16])
        return {"columns": result.columns, "rows": result.rows, "row_count": len(result.rows),
                "truncated": result.truncated, "as_of": queried_at.isoformat(), "duration_ms": result.duration_ms,
                "masked_cells": masked}

    @app.post("/cancel")
    async def cancel(body: RunnerRequest, authorization: str | None = Header(default=None)) -> dict[str, Any]:
        p = authorise(authorization, body, "cancel")
        adapter = engines.get(p.get("engine", ""))
        cancelled = bool(adapter and await adapter.cancel(str(p["execution_id"])))
        return {"cancelled": cancelled}

    return app
