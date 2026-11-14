"""Connections (API-007…API-010; FEAT-005, FEAT-006; US-028, US-030, US-041).

* Secrets are write-only: the password goes straight to the secrets store and
  the database keeps only ``secret_ref``; responses say whether a secret is set.
* A connection is ``draft`` until a test proves it reachable, signed in and
  read-only (RULE-001). Credential changes force a new test.
* Introspection stores names, types and keys only — never sample values.
* A source behind a bastion is reached through an SSH tunnel, optionally through jump hosts, with an uploaded
  or library key, a certificate or a password (ENH-003, ENH-004; ``ssh.py``). Secrets go to the store only;
  the descriptor keeps servers, users, key fingerprints and the host keys pinned by the first passing test.
* Every change invalidates the connection's cached results (ADR-010).

State machine (function_document, PRE-06 I-3): draft → testing → connected →
introspecting → connected | incomplete; any → error on a failed re-test of a
connected source; any → disabled; disabled → (enable + passing test) → connected.
"""

from __future__ import annotations

import json
import time
from collections import defaultdict, deque
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, Header, Request, Response
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from tela_api import outbox
from tela_api.audit import service as audit
from tela_api.connections.ssh import SSH_ENGINES, SshIn, check_target, pin_host_keys, ssh_settings, unchanged
from tela_api.core.db import fetch_all, fetch_one, tenant_tx
from tela_api.identity.deps import get_principal, require
from tela_api.identity.permissions import Perm
from tela_api.identity.principal import Principal
from tela_api.query.asof import FRESHNESS_MODES
from tela_api.runner_transport import RunnerTransport
from tela_common.errors import ErrorCode, TelaError
from tela_common.ids import new_id
from tela_common.secrets import SecretNotFound, SecretsStore, secret_ref

router = APIRouter(prefix="/api/v1", tags=["connections"])

# Engines created through this form. Excel and CSV sources are created by uploading a file (US-108).
SUPPORTED_ENGINES = ("postgres", "mysql", "sqlserver", "mongodb", "dataverse")
FILE_ENGINES = ("excel", "csv")
DEFAULT_PORTS = {"postgres": 5432, "mysql": 3306, "sqlserver": 1433}
TEST_RATE_PER_MIN = 10
_test_calls: dict[str, deque[float]] = defaultdict(deque)
_CREDENTIAL_KEYS = ("host", "port", "database", "username", "password", "tls", "approved_schemas", "uri",
                    "read_preference", "environment_url", "tenant_id", "client_id", "client_secret", "mode", "tables",
                    "ssh")
_DESCRIPTOR_KEYS = ("host", "port", "database", "username", "approved_schemas", "uri", "read_preference",
                    "environment_url", "tenant_id", "client_id", "mode", "tables", "webapi_format")

MSG_READ_ONLY_FAILED = "This account can write. Provide a read-only account."  # MSG-003
MSG_UNREACHABLE = "Tela could not reach this database. Check the host, port and network path."  # MSG-002
MSG_AUTH_FAILED = "The username or password was rejected."  # MSG-004


def _rid(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


def _runner(request: Request) -> RunnerTransport:
    transport: RunnerTransport | None = getattr(request.app.state, "runner", None)
    if transport is None:
        raise TelaError(ErrorCode.SOURCE_UNREACHABLE, "The connector service is not configured.")
    return transport


def _secrets(request: Request) -> SecretsStore:
    store: SecretsStore = request.app.state.secrets
    return store


def _etag(row: dict[str, Any]) -> str:
    return f'W/"{int(row["updated_at"].timestamp() * 1_000_000)}"'


class TlsIn(BaseModel):
    mode: Literal["disable", "require", "verify-ca", "verify-full"] = "verify-full"
    trust_server_certificate: bool = False  # SQL Server: development only (Encrypt itself is always on)


class FreshnessIn(BaseModel):
    """Freshness model per connection (US-041; REQ-118; DEC-02)."""

    mode: Literal["live", "cached", "source_asof"] = "live"
    cache_ttl_s: int = Field(default=900, ge=0, le=900)  # live: short scope-keyed cache (DEC-02)
    cache_ttl_minutes: int = Field(default=15, ge=1, le=1440)  # cached mode
    declared_at: datetime | None = None  # source_asof: a declared time ...
    entity: str | None = None  # ... or the newest value of a timestamp column
    field: str | None = None

    @model_validator(mode="after")
    def _source(self) -> FreshnessIn:
        if self.mode == "source_asof" and not self.declared_at and not (self.entity and self.field):
            raise ValueError("source as-of needs a declared time or an entity and timestamp field")
        return self

    def stored(self) -> dict[str, Any]:
        out: dict[str, Any] = {"mode": self.mode}
        if self.mode == "live":
            out["cache_ttl_s"] = self.cache_ttl_s
        elif self.mode == "cached":
            out["cache_ttl_minutes"] = self.cache_ttl_minutes
        else:
            out["cache_ttl_s"] = self.cache_ttl_s
            if self.declared_at:
                out["declared_at"] = self.declared_at.isoformat()
            else:
                out["entity"], out["field"] = self.entity, self.field
        assert out["mode"] in FRESHNESS_MODES
        return out


_IDENT = r"^[A-Za-z_][A-Za-z0-9_$-]{0,63}$"
_GUID = r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
ReadPreference = Literal["primary", "primaryPreferred", "secondary", "secondaryPreferred"]


def _check_schemas(v: list[str] | None) -> list[str] | None:
    import re

    for s in v or []:
        if not re.match(_IDENT, s):
            raise ValueError(f"'{s}' is not a valid schema name")
    return v


def _check_uri(v: str | None) -> str | None:
    """MongoDB connection string (REQ-031): no credentials in it, ever; they go to the secrets store."""
    from urllib.parse import urlsplit

    if v is None:
        return v
    if not v.startswith(("mongodb://", "mongodb+srv://")):
        raise ValueError("use a mongodb:// or mongodb+srv:// connection string")
    netloc = urlsplit(v).netloc
    if "@" in netloc:
        raise ValueError("remove the user name and password from the connection string; enter them below")
    if not netloc:
        raise ValueError("the connection string has no host")
    return v


def _check_tables(v: list[str] | None) -> list[str] | None:
    import re

    for t in v or []:
        if not re.match(r"^[a-z_][a-z0-9_]{0,99}$", t):
            raise ValueError(f"'{t}' is not a Dataverse logical table name")
    return v


class ConnectionIn(BaseModel):
    """One form for every engine (SCR-09, connection form validation table; US-115). Required fields depend on
    the engine; the descriptor keeps only non-secret settings (REQ-014)."""

    name: str = Field(min_length=1, max_length=80)
    engine: str
    host: str | None = Field(default=None, min_length=1, max_length=255)
    port: int | None = Field(default=None, ge=1, le=65535)
    database: str | None = Field(default=None, min_length=1, max_length=128)
    username: str | None = Field(default=None, min_length=1, max_length=128)
    password: str | None = Field(default=None, min_length=1, max_length=1024)
    tls: TlsIn = Field(default_factory=TlsIn)
    approved_schemas: list[str] | None = Field(default=None, min_length=1, max_length=50)
    # MongoDB
    uri: str | None = Field(default=None, max_length=1024)
    read_preference: ReadPreference = "secondaryPreferred"
    # Dataverse
    environment_url: str | None = Field(default=None, max_length=255)
    tenant_id: str | None = Field(default=None, pattern=_GUID)
    client_id: str | None = Field(default=None, pattern=_GUID)
    client_secret: str | None = Field(default=None, min_length=1, max_length=2048)
    mode: Literal["tds", "webapi"] = "tds"
    tables: list[str] | None = Field(default=None, max_length=200)
    webapi_format: Literal["odata", "fetchxml"] = "odata"
    # Development only: a SQL Server stand-in for the TDS endpoint (it cannot check Entra tokens) and its login.
    dev_tds: str | None = Field(default=None, max_length=255, pattern=r"^[A-Za-z0-9.\-]+(,\d{1,5})?$")
    tds_username: str | None = Field(default=None, max_length=128)
    tds_password: str | None = Field(default=None, max_length=1024)
    row_cap: int = Field(default=10_000, ge=1, le=10_000)
    timeout_ms: int = Field(default=20_000, ge=1000, le=20_000)
    freshness: FreshnessIn = Field(default_factory=FreshnessIn)
    ssh: SshIn | None = None

    _s = field_validator("approved_schemas")(classmethod(lambda cls, v: _check_schemas(v)))
    _u = field_validator("uri")(classmethod(lambda cls, v: _check_uri(v)))
    _t = field_validator("tables")(classmethod(lambda cls, v: _check_tables(v)))

    @model_validator(mode="after")
    def _per_engine(self) -> ConnectionIn:
        required = {
            "postgres": ("host", "database", "username", "password"),
            "mysql": ("host", "database", "username", "password"),
            "sqlserver": ("host", "database", "username", "password"),
            "mongodb": ("uri", "database", "username", "password"),
            "dataverse": ("environment_url", "tenant_id", "client_id", "client_secret"),
        }.get(self.engine, ())
        missing = [f for f in required if getattr(self, f) in (None, "")]
        if missing:
            raise ValueError(f"{', '.join(missing)} required for this engine")
        if self.engine == "sqlserver" and self.tls.mode == "disable":
            raise ValueError("SQL Server connections are always encrypted")
        if self.ssh and self.ssh.enabled and self.engine not in SSH_ENGINES:
            raise ValueError("SSH tunnels are available for PostgreSQL, MySQL, SQL Server and MongoDB connections")
        if self.engine == "dataverse":
            from urllib.parse import urlsplit

            u = urlsplit(self.environment_url or "")
            if u.scheme not in ("https", "http") or not u.hostname or u.path not in ("", "/"):
                raise ValueError("give the environment URL, e.g. https://contoso.crm4.dynamics.com")
        return self

    def descriptor(self) -> dict[str, Any]:
        if self.engine == "dataverse":
            from urllib.parse import urlsplit

            host = urlsplit(self.environment_url or "").hostname
            out = {"environment_url": (self.environment_url or "").rstrip("/"), "host": host,
                   "tenant_id": self.tenant_id, "client_id": self.client_id, "mode": self.mode,
                   "tables": self.tables or [], "approved_schemas": ["dbo"], "tls": self.tls.model_dump(),
                   "webapi_format": self.webapi_format}
            if self.dev_tds:
                out["dev_tds"] = self.dev_tds
            return out
        if self.engine == "mongodb":
            return {"uri": self.uri, "database": self.database, "username": self.username,
                    "read_preference": self.read_preference, "tls": self.tls.model_dump(),
                    "approved_schemas": [self.database]}
        schemas = self.approved_schemas or {"mysql": [self.database], "sqlserver": ["dbo"]}.get(self.engine,
                                                                                               ["public"])
        return {"host": self.host, "port": self.port or DEFAULT_PORTS[self.engine], "database": self.database,
                "username": self.username, "tls": self.tls.model_dump(), "approved_schemas": schemas}

    def secret(self) -> dict[str, str]:
        if self.engine == "dataverse":
            out = {"client_id": self.client_id or "", "client_secret": self.client_secret or ""}
            if self.dev_tds:
                out.update(tds_username=self.tds_username or "", tds_password=self.tds_password or "")
            return out
        return {"username": self.username or "", "password": self.password or ""}


class ConnectionPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    host: str | None = Field(default=None, min_length=1, max_length=255)
    port: int | None = Field(default=None, ge=1, le=65535)
    database: str | None = Field(default=None, min_length=1, max_length=128)
    username: str | None = Field(default=None, min_length=1, max_length=128)
    password: str | None = Field(default=None, min_length=1, max_length=1024)
    tls: TlsIn | None = None
    approved_schemas: list[str] | None = Field(default=None, min_length=1, max_length=50)
    uri: str | None = Field(default=None, max_length=1024)
    read_preference: ReadPreference | None = None
    environment_url: str | None = Field(default=None, max_length=255)
    tenant_id: str | None = Field(default=None, pattern=_GUID)
    client_id: str | None = Field(default=None, pattern=_GUID)
    client_secret: str | None = Field(default=None, min_length=1, max_length=2048)
    mode: Literal["tds", "webapi"] | None = None
    tables: list[str] | None = Field(default=None, max_length=200)
    webapi_format: Literal["odata", "fetchxml"] | None = None
    row_cap: int | None = Field(default=None, ge=1, le=10_000)
    timeout_ms: int | None = Field(default=None, ge=1000, le=20_000)
    freshness: FreshnessIn | None = None
    ssh: SshIn | None = None

    _s = field_validator("approved_schemas")(classmethod(lambda cls, v: _check_schemas(v)))
    _u = field_validator("uri")(classmethod(lambda cls, v: _check_uri(v)))
    _t = field_validator("tables")(classmethod(lambda cls, v: _check_tables(v)))


_SELECT = """
    SELECT c.id, c.name, c.engine, c.status, c.descriptor, c.secret_ref, c.read_only_verified_at,
           c.read_only_evidence, c.schema_checksum, c.freshness_policy, c.row_cap, c.timeout_ms,
           c.created_at, c.updated_at, c.ask_mode,
           m.id AS model_id, v.version_no AS model_version_no, m.status AS model_status,
           (SELECT max(e.created_at) FROM ops.query_executions e
              WHERE e.connection_id = c.id AND e.outcome = 'ok') AS last_queried_at
    FROM conn.connections c
    LEFT JOIN model.data_models m ON m.connection_id = c.id
    LEFT JOIN model.model_versions v ON v.id = m.current_version_id
"""


def _out(row: dict[str, Any]) -> dict[str, Any]:
    d = dict(row["descriptor"] or {})
    return {
        "id": str(row["id"]), "name": row["name"], "engine": row["engine"], "status": row["status"],
        "descriptor": d, "secret_set": bool(row["secret_ref"]),
        "read_only": {"verified_at": row["read_only_verified_at"], **(row["read_only_evidence"] or {})},
        "schema_checksum": row["schema_checksum"], "freshness": row["freshness_policy"],
        "row_cap": row["row_cap"], "timeout_ms": row["timeout_ms"], "ask_mode": row["ask_mode"],
        "model": {"id": str(row["model_id"]) if row["model_id"] else None, "version_no": row["model_version_no"],
                  "status": row["model_status"]},
        "data_as_of": row["last_queried_at"], "etag": _etag(row),
    }


async def _load(conn: AsyncConnection, connection_id: str) -> dict[str, Any]:
    row = await fetch_one(conn, _SELECT + " WHERE c.id = CAST(:id AS uuid) AND c.deleted_at IS NULL",
                          {"id": connection_id})
    if row is None:
        raise TelaError(ErrorCode.NOT_FOUND, "Connection not found.")
    return row


async def _invalidate_cache(request: Request, conn: AsyncConnection, connection_id: str) -> None:
    cache = getattr(request.app.state, "result_cache", None)
    if cache is not None:
        await cache.invalidate_connection(conn, connection_id)


def _dev(request: Request) -> bool:
    return request.app.state.settings.env in ("local", "dev", "test")


def _check_tls(request: Request, tls: TlsIn) -> None:
    if tls.mode == "disable" and not _dev(request):
        raise TelaError(ErrorCode.VALIDATION_ERROR, "TLS can only be disabled in development.")
    if tls.trust_server_certificate and not _dev(request):
        raise TelaError(ErrorCode.VALIDATION_ERROR, "Trusting the server certificate is only allowed in development.")


def _check_environment(request: Request, descriptor: dict[str, Any]) -> None:
    """Dataverse: an https://*.dynamics.com environment outside development (fakes run on plain http in dev)."""
    url = str(descriptor.get("environment_url") or "")
    if url and not _dev(request) and not (url.startswith("https://") and
                                          str(descriptor.get("host") or "").endswith(".dynamics.com")):
        raise TelaError(ErrorCode.VALIDATION_ERROR, "Use the https://<org>.crm.dynamics.com environment URL.")


# --------------------------------------------------------------------------- list / read


@router.get("/connections")
async def list_connections(principal: Principal = Depends(get_principal)) -> dict[str, Any]:
    async with tenant_tx(principal.tenant_id) as conn:
        if principal.has(Perm.CONNECTIONS_MANAGE) or principal.has(Perm.POLICIES_MANAGE):
            rows = await fetch_all(conn, _SELECT + " WHERE c.deleted_at IS NULL ORDER BY c.name")
            if principal.has(Perm.CONNECTIONS_MANAGE):
                return {"items": [_out(r) for r in rows]}
            return {"items": [{"id": str(r["id"]), "name": r["name"], "engine": r["engine"], "status": r["status"],
                               "read_only_verified_at": r["read_only_verified_at"]} for r in rows]}
        rows = await fetch_all(
            conn,
            """
            SELECT DISTINCT c.id, c.name, c.engine FROM conn.connections c
            JOIN iam.scope_policies p ON p.connection_id = c.id
            WHERE c.deleted_at IS NULL AND p.is_active AND p.role_id = ANY(CAST(:roles AS uuid[]))
            ORDER BY c.name
            """,
            {"roles": list(principal.role_ids)},
        )
    return {"items": [{**r, "id": str(r["id"])} for r in rows]}


@router.get("/connections/{connection_id}")
async def get_connection(connection_id: str, response: Response,
                         principal: Principal = Depends(require(Perm.CONNECTIONS_MANAGE))) -> dict[str, Any]:
    async with tenant_tx(principal.tenant_id) as conn:
        row = await _load(conn, connection_id)
    response.headers["ETag"] = _etag(row)
    return _out(row)


# --------------------------------------------------------------------------- create


@router.post("/connections", status_code=201)
async def create_connection(body: ConnectionIn, request: Request,
                            principal: Principal = Depends(require(Perm.CONNECTIONS_MANAGE))) -> dict[str, Any]:
    if body.engine in FILE_ENGINES:
        raise TelaError(ErrorCode.VALIDATION_ERROR, "Upload the file to create an Excel or CSV source.")
    if body.engine not in SUPPORTED_ENGINES:
        raise TelaError(ErrorCode.ENGINE_NOT_SUPPORTED, f"The {body.engine} connector is not available yet.")
    _check_tls(request, body.tls)
    if body.dev_tds and not _dev(request):
        raise TelaError(ErrorCode.VALIDATION_ERROR, "A TDS stand-in is only allowed in development.")
    connection_id = new_id()
    ref = secret_ref(principal.tenant_id, "connections", connection_id)
    descriptor = body.descriptor()
    _check_environment(request, descriptor)
    secret = body.secret()
    if body.ssh and body.ssh.enabled:
        check_target(body.engine, descriptor)
    async with tenant_tx(principal.tenant_id) as conn:
        exists = await fetch_one(conn, "SELECT 1 FROM conn.connections WHERE name = :n AND deleted_at IS NULL",
                                 {"n": body.name})
        if exists:
            raise TelaError(ErrorCode.CONFLICT, "A connection with this name already exists.")
        if body.ssh and body.ssh.enabled:
            descriptor["ssh"], ssh_secret = await ssh_settings(conn, body.ssh, None)
            secret.update({k: v for k, v in ssh_secret.items() if v})
        # Secret first: if the store is down nothing is created (fail closed, secrets_unavailable).
        await _secrets(request).put(ref, secret)
        await conn.execute(
            text(
                "INSERT INTO conn.connections (id, tenant_id, name, engine, descriptor, secret_ref, status, "
                "freshness_policy, row_cap, timeout_ms, created_by) VALUES (:id, :t, :n, :e, CAST(:d AS jsonb), "
                ":ref, 'draft', CAST(:f AS jsonb), :cap, :tmo, :by)"
            ),
            {"id": connection_id, "t": principal.tenant_id, "n": body.name, "e": body.engine,
             "d": json.dumps(descriptor), "ref": ref, "f": json.dumps(body.freshness.stored()),
             "cap": body.row_cap, "tmo": body.timeout_ms, "by": principal.user_id},
        )
        await audit.record(conn, tenant_id=principal.tenant_id, action="connection.created", object_type="connection",
                           object_id=connection_id, outcome="success", actor_id=principal.user_id,
                           correlation_id=_rid(request), details={"engine": body.engine, "name": body.name,
                                                                  "ssh": bool(descriptor.get("ssh"))})
        row = await _load(conn, connection_id)
    return {"connection_id": connection_id, "status": "draft", "engine": body.engine, **_out(row)}


# --------------------------------------------------------------------------- test (RULE-001)


def _rate_limit(connection_id: str) -> None:
    now = time.monotonic()
    calls = _test_calls[connection_id]
    while calls and now - calls[0] > 60:
        calls.popleft()
    if len(calls) >= TEST_RATE_PER_MIN:
        raise TelaError(ErrorCode.RATE_LIMITED, "Too many connection tests. Wait a minute and try again.")
    calls.append(now)


def _runner_payload(principal: Principal, row: dict[str, Any]) -> dict[str, Any]:
    return {"tenant_id": principal.tenant_id, "connection_id": str(row["id"]), "engine": row["engine"],
            "descriptor": row["descriptor"], "secret_ref": row["secret_ref"]}


async def run_test(request: Request, principal: Principal, connection_id: str) -> dict[str, Any]:
    _rate_limit(connection_id)
    async with tenant_tx(principal.tenant_id) as conn:
        row = await _load(conn, connection_id)
        if row["status"] == "disabled":
            raise TelaError(ErrorCode.CONNECTION_NOT_READY, "Enable the connection before testing it.")
        previous = row["status"]
        await conn.execute(text("UPDATE conn.connections SET status = 'testing' WHERE id = :id"), {"id": row["id"]})
    try:
        result = await _runner(request).post("test", _runner_payload(principal, row), timeout_s=45)
    except TelaError as exc:
        result = {"reachable": False, "authenticated": False, "latency_ms": None, "error": exc.message,
                  "read_only": {"verdict": "unsafe", "checks": [], "reasons": ["connector service unavailable"]}}
    passed = bool(result["reachable"] and result["authenticated"] and result["read_only"]["verdict"] == "safe")
    ssh = result.get("ssh")
    if passed:
        new_status = "connected"
        message = None
    else:
        new_status = "error" if previous in ("connected", "introspecting", "incomplete") else "draft"
        if ssh and not ssh.get("ok"):
            message = ssh.get("error") or MSG_UNREACHABLE
        elif result.get("suggest_mode"):
            message = result.get("error") or MSG_UNREACHABLE
        elif not result["reachable"]:
            message = MSG_UNREACHABLE
        elif not result["authenticated"]:
            message = result.get("error") if row["engine"] == "dataverse" and result.get("error") else MSG_AUTH_FAILED
        else:
            message = MSG_READ_ONLY_FAILED
    evidence = {"verdict": result["read_only"]["verdict"], "checks": result["read_only"].get("checks", []),
                "reasons": result["read_only"].get("reasons", []), "latency_ms": result.get("latency_ms"),
                "server_version": result.get("server_version"), "tested_at": datetime.now(UTC).isoformat()}
    async with tenant_tx(principal.tenant_id) as conn:
        pinned = await pin_host_keys(conn, row["id"], ssh) if passed and ssh else []
        await conn.execute(
            text(
                "UPDATE conn.connections SET status = :s, read_only_evidence = CAST(:ev AS jsonb), "
                "read_only_verified_at = CASE WHEN :ok THEN now() ELSE read_only_verified_at END WHERE id = :id"
            ),
            {"s": new_status, "ev": json.dumps(evidence), "ok": passed, "id": row["id"]},
        )
        await audit.record(
            conn, tenant_id=principal.tenant_id, action="connection.tested", object_type="connection",
            object_id=connection_id, outcome="success" if passed else "failure", actor_id=principal.user_id,
            correlation_id=_rid(request), severity="normal" if passed or result["reachable"] is False else "security",
            details={"verdict": evidence["verdict"], "reasons": evidence["reasons"][:10], "status": new_status,
                     **({"ssh_stage": ssh.get("stage")} if ssh and not ssh.get("ok") else {}),
                     **({"ssh_host_keys_pinned": pinned} if pinned else {})},
        )
        if not passed:
            await _invalidate_cache(request, conn, connection_id)
    return {
        "reachable": result["reachable"], "authenticated": result["authenticated"],
        "read_only": {"verdict": evidence["verdict"], "checks": evidence["checks"], "reasons": evidence["reasons"]},
        "latency_ms": result.get("latency_ms"), "server_version": result.get("server_version"),
        "status": new_status, "message": message, "suggest_mode": result.get("suggest_mode"),
        "ssh": {**ssh, "pinned": bool(pinned), "pinned_hops": [p["hop"] for p in pinned]} if ssh else None,
    }


@router.post("/connections/{connection_id}/test")
async def test_connection(connection_id: str, request: Request,
                          principal: Principal = Depends(require(Perm.CONNECTIONS_MANAGE))) -> dict[str, Any]:
    return await run_test(request, principal, connection_id)


# --------------------------------------------------------------------------- update / disable / enable / delete


async def _patch_secret(request: Request, ref: str, update: dict[str, str], *, complete: bool) -> None:
    """Merge ``update`` into the stored secret; if the store lost it, re-create it when the edit is a full secret."""
    try:
        await _secrets(request).patch(ref, update)
    except SecretNotFound:
        if not complete:
            raise TelaError(ErrorCode.SECRETS_UNAVAILABLE,
                            "The saved credential is missing. Enter the password again.") from None
        await _secrets(request).put(ref, update)


@router.patch("/connections/{connection_id}")
async def patch_connection(connection_id: str, body: ConnectionPatch, request: Request, response: Response,
                           if_match: str | None = Header(default=None),
                           principal: Principal = Depends(require(Perm.CONNECTIONS_MANAGE))) -> dict[str, Any]:
    if not if_match:
        raise TelaError(ErrorCode.PRECONDITION_FAILED, "Reload the connection and try again (If-Match required).")
    changes = body.model_dump(exclude_none=True)
    async with tenant_tx(principal.tenant_id) as conn:
        row = await _load(conn, connection_id)
        if if_match != _etag(row):
            raise TelaError(ErrorCode.PRECONDITION_FAILED, "Someone else changed this connection. Reload it.")
        if row["engine"] in FILE_ENGINES and any(k in changes for k in _CREDENTIAL_KEYS):
            raise TelaError(ErrorCode.VALIDATION_ERROR, "Upload a new version of the file instead.")
        descriptor = dict(row["descriptor"])
        for key in _DESCRIPTOR_KEYS:
            if key in changes:
                descriptor[key] = changes[key]
        if row["engine"] == "mongodb" and "database" in changes:
            descriptor["approved_schemas"] = [changes["database"]]
        if row["engine"] == "dataverse" and "environment_url" in changes:
            from urllib.parse import urlsplit

            descriptor["environment_url"] = str(changes["environment_url"]).rstrip("/")
            descriptor["host"] = urlsplit(descriptor["environment_url"]).hostname
        _check_environment(request, descriptor)
        if body.tls is not None:
            _check_tls(request, body.tls)
            descriptor["tls"] = body.tls.model_dump()
        ssh_secret: dict[str, str] = {}
        if body.ssh is not None:
            if body.ssh.enabled or descriptor.get("ssh"):
                ssh_block, ssh_secret = await ssh_settings(conn, body.ssh, descriptor.get("ssh"))
                if unchanged(ssh_block, descriptor.get("ssh"), ssh_secret):
                    changes.pop("ssh")  # the form sends the block back unchanged: not a credential change
                    ssh_secret = {}
                if ssh_block:
                    descriptor["ssh"] = ssh_block
                else:
                    descriptor.pop("ssh", None)
            else:
                changes.pop("ssh")  # turning off a tunnel that was never on changes nothing
        if descriptor.get("ssh"):
            check_target(row["engine"], descriptor)
        credential_change = any(k in changes for k in _CREDENTIAL_KEYS)
        # Merge patches: the API may write credentials but never read them back (US-007 store policy).
        if row["engine"] == "dataverse" and (body.client_secret is not None or body.client_id is not None):
            update = {"client_id": body.client_id or str(descriptor.get("client_id") or "")}
            if body.client_secret is not None:
                update["client_secret"] = body.client_secret
            await _patch_secret(request, row["secret_ref"], update, complete=bool(update.get("client_secret")))
        elif body.password is not None or body.username is not None:
            update = {"username": body.username or str(descriptor["username"])}
            if body.password is not None:
                update["password"] = body.password
            await _patch_secret(request, row["secret_ref"], update, complete=body.password is not None)
        if ssh_secret:
            await _patch_secret(request, row["secret_ref"], ssh_secret, complete=False)
        new_status = row["status"]
        if credential_change and row["status"] != "disabled":
            new_status = "draft"  # mandatory re-test after a credential change (A4)
        await conn.execute(
            text(
                "UPDATE conn.connections SET name = :n, descriptor = CAST(:d AS jsonb), status = :s, "
                "freshness_policy = CAST(:f AS jsonb), row_cap = :cap, timeout_ms = :tmo, "
                "read_only_verified_at = CASE WHEN :cred THEN NULL ELSE read_only_verified_at END WHERE id = :id"
            ),
            {"n": body.name or row["name"], "d": json.dumps(descriptor), "s": new_status,
             "f": json.dumps(body.freshness.stored() if body.freshness else row["freshness_policy"]),
             "cap": body.row_cap or row["row_cap"], "tmo": body.timeout_ms or row["timeout_ms"],
             "cred": credential_change, "id": row["id"]},
        )
        await audit.record(
            conn, tenant_id=principal.tenant_id, action="connection.updated", object_type="connection",
            object_id=connection_id, outcome="success", actor_id=principal.user_id, correlation_id=_rid(request),
            details={"fields": sorted(k for k in changes if k not in ("password", "client_secret"))
                     + (["password"] if body.password else []) + (["client_secret"] if body.client_secret else []),
                     "retest_required": credential_change},
        )
        await _invalidate_cache(request, conn, connection_id)
        await outbox.emit(conn, tenant_id=principal.tenant_id, topic="connection.changed", aggregate_id=connection_id,
                          payload={"connection_id": connection_id, "retest_required": credential_change})
        updated = await _load(conn, connection_id)
    response.headers["ETag"] = _etag(updated)
    return {**_out(updated), "retest_required": credential_change}


async def _set_status(request: Request, principal: Principal, connection_id: str, status: str, action: str) -> None:
    async with tenant_tx(principal.tenant_id) as conn:
        row = await _load(conn, connection_id)
        await conn.execute(text("UPDATE conn.connections SET status = :s WHERE id = :id"),
                           {"s": status, "id": row["id"]})
        await audit.record(conn, tenant_id=principal.tenant_id, action=action, object_type="connection",
                           object_id=connection_id, outcome="success", actor_id=principal.user_id,
                           correlation_id=_rid(request), severity="high")
        await _invalidate_cache(request, conn, connection_id)


@router.post("/connections/{connection_id}/disable")
async def disable_connection(connection_id: str, request: Request,
                             principal: Principal = Depends(require(Perm.CONNECTIONS_MANAGE))) -> dict[str, Any]:
    await _set_status(request, principal, connection_id, "disabled", "connection.disabled")
    return {"status": "disabled"}


@router.post("/connections/{connection_id}/enable")
async def enable_connection(connection_id: str, request: Request,
                            principal: Principal = Depends(require(Perm.CONNECTIONS_MANAGE))) -> dict[str, Any]:
    await _set_status(request, principal, connection_id, "draft", "connection.enabled")
    return await run_test(request, principal, connection_id)  # enabling always re-tests


@router.delete("/connections/{connection_id}", status_code=204)
async def delete_connection(connection_id: str, request: Request,
                            principal: Principal = Depends(require(Perm.CONNECTIONS_MANAGE))) -> Response:
    async with tenant_tx(principal.tenant_id) as conn:
        row = await _load(conn, connection_id)
        # A6: refuse while published content depends on it. Publishing arrives in P2; an approved model
        # counts as a dependant until then so certified definitions are never orphaned silently.
        dependants = await fetch_one(
            conn, "SELECT count(*) AS n FROM model.data_models WHERE connection_id = :id AND current_version_id "
                  "IS NOT NULL", {"id": row["id"]})
        if dependants and dependants["n"]:
            raise TelaError(ErrorCode.CONFLICT, "This connection has an approved model. Disable it instead.")
        await conn.execute(text("UPDATE conn.connections SET deleted_at = now(), status = 'disabled' WHERE id = :id"),
                           {"id": row["id"]})
        await audit.record(conn, tenant_id=principal.tenant_id, action="connection.deleted", object_type="connection",
                           object_id=connection_id, outcome="success", actor_id=principal.user_id,
                           correlation_id=_rid(request), severity="high")
        await _invalidate_cache(request, conn, connection_id)
    if row["secret_ref"]:
        await _secrets(request).delete(row["secret_ref"])
    return Response(status_code=204)


@router.post("/connections/{connection_id}/refresh")
async def refresh_connection(connection_id: str, request: Request,
                             principal: Principal = Depends(require(Perm.CONNECTIONS_MANAGE))) -> dict[str, Any]:
    """"Refresh now": drop every cached result for this connection (US-041)."""
    async with tenant_tx(principal.tenant_id) as conn:
        await _load(conn, connection_id)
        cache = request.app.state.result_cache
        dropped = await cache.invalidate_connection(conn, connection_id)
        await audit.record(conn, tenant_id=principal.tenant_id, action="connection.cache_refreshed",
                           object_type="connection", object_id=connection_id, outcome="success",
                           actor_id=principal.user_id, correlation_id=_rid(request), details={"entries": dropped})
    return {"invalidated": dropped}


# --------------------------------------------------------------------------- Ask answer mode (US-256)


class AskModeIn(BaseModel):
    mode: Literal["certified", "fields", "hybrid", "direct_sql"]


async def _mode_scores(conn: AsyncConnection, connection_id: Any) -> dict[str, dict[str, Any]]:
    """Each mode's latest finished gold run on this source: right, confident-wrong, clarified, refused and the
    median time of the questions it ran: a run on this source, or this source's questions in a run on every source.
    A run in a chosen mode (US-259) or over sources all in one mode counts; a run over sources in different modes
    doesn't."""
    rows = await fetch_all(
        conn,
        """
        SELECT DISTINCT ON (r.ask_mode) r.ask_mode, r.id, r.finished_at, r.plan_only, r.tags,
               count(x.id) AS total,
               count(x.id) FILTER (WHERE x.passed) AS right_,
               count(x.id) FILTER (WHERE x.confident_wrong) AS confident_wrong,
               count(x.id) FILTER (WHERE NOT x.passed AND x.status = 'clarify') AS clarified,
               count(x.id) FILTER (WHERE NOT x.passed AND x.status = 'unsupported') AS refused,
               percentile_disc(0.5) WITHIN GROUP (ORDER BY x.latency_ms) AS median_ms
        FROM rag.eval_runs r
        JOIN rag.eval_results x ON x.run_id = r.id
        LEFT JOIN rag.gold_examples g ON g.id = x.gold_example_id
        WHERE (r.connection_id = :c OR (r.connection_id IS NULL AND g.connection_id = :c))
          AND r.status = 'done' AND r.finished_at IS NOT NULL AND r.ask_mode IS NOT NULL
        GROUP BY r.id
        ORDER BY r.ask_mode, r.finished_at DESC
        """,
        {"c": connection_id},
    )
    return {r["ask_mode"]: {"run_id": str(r["id"]), "run_at": r["finished_at"], "plan_only": r["plan_only"],
                            "tags": r["tags"], "total": r["total"], "right": r["right_"],
                            "confident_wrong": r["confident_wrong"], "clarified": r["clarified"],
                            "refused": r["refused"], "median_ms": r["median_ms"]} for r in rows}


async def _ask_mode_out(conn: AsyncConnection, row: dict[str, Any]) -> dict[str, Any]:
    from tela_api.ask import modes

    scores = await _mode_scores(conn, row["id"])
    runs, why = modes.planner_for(row["ask_mode"], row["engine"], row["descriptor"])
    return {"connection_id": str(row["id"]), "connection": row["name"], "engine": row["engine"],
            "mode": row["ask_mode"], "answers_with": runs, "fallback_reason": why, "default": modes.DEFAULT,
            "choices": [{**c, "score": scores.get(c["mode"])}
                        for c in modes.choices(row["engine"], row["descriptor"])]}


@router.get("/connections/{connection_id}/ask-mode")
async def get_ask_mode(connection_id: str,
                       principal: Principal = Depends(require(Perm.CONNECTIONS_MANAGE))) -> dict[str, Any]:
    """Connections > Ask: the source's answer mode, the four choices and each one's latest gold score."""
    async with tenant_tx(principal.tenant_id) as conn:
        return await _ask_mode_out(conn, await _load(conn, connection_id))


@router.put("/connections/{connection_id}/ask-mode")
async def set_ask_mode(connection_id: str, body: AskModeIn, request: Request,
                       principal: Principal = Depends(require(Perm.CONNECTIONS_MANAGE))) -> dict[str, Any]:
    """Choose how Ask answers for this source (DEC-29). Audited; the source's cached results are dropped so no answer
    from the previous mode is served."""
    from tela_api.ask import modes

    async with tenant_tx(principal.tenant_id) as conn:
        row = await _load(conn, connection_id)
        if (why := modes.unavailable(body.mode, row["engine"], row["descriptor"])) is not None:
            raise TelaError(ErrorCode.VALIDATION_ERROR, why, details={"mode": body.mode})
        if body.mode != row["ask_mode"]:
            await conn.execute(text("UPDATE conn.connections SET ask_mode = :m WHERE id = :id"),
                               {"m": body.mode, "id": row["id"]})
            await audit.record(
                conn, tenant_id=principal.tenant_id, action="connection.ask_mode_changed", object_type="connection",
                object_id=connection_id, outcome="success", actor_id=principal.user_id, correlation_id=_rid(request),
                severity="high" if body.mode == "direct_sql" else "normal",
                details={"from": row["ask_mode"], "to": body.mode})
            await _invalidate_cache(request, conn, connection_id)
            row = await _load(conn, connection_id)
        return await _ask_mode_out(conn, row)


# --------------------------------------------------------------------------- introspection (US-030)


@router.post("/connections/{connection_id}/introspect", status_code=202)
async def introspect_connection(connection_id: str, request: Request,
                                principal: Principal = Depends(require(Perm.CONNECTIONS_MANAGE))) -> dict[str, Any]:
    from tela_api.connections.introspection import run_introspection

    return await run_introspection(request, principal, connection_id)


@router.get("/connections/{connection_id}/objects")
async def list_objects(connection_id: str,
                       principal: Principal = Depends(require(Perm.CONNECTIONS_MANAGE))) -> dict[str, Any]:
    async with tenant_tx(principal.tenant_id) as conn:
        row = await _load(conn, connection_id)
        objects = await fetch_all(
            conn,
            "SELECT qualified_name, object_kind, columns, keys, row_estimate, is_allowed, readable, introspected_at, "
            "removed_at "
            "FROM conn.connection_objects WHERE connection_id = :id ORDER BY qualified_name",
            {"id": row["id"]},
        )
    last = max((o["introspected_at"] for o in objects), default=None)
    return {
        "items": [{"object": o["qualified_name"], "kind": o["object_kind"],
                   "columns": [{k: c[k] for k in ("name", "type", "nullable")} for c in o["columns"]],
                   "keys": o["keys"], "row_estimate": o["row_estimate"], "allowed": o["is_allowed"],
                   "readable": o["readable"],
                   "removed": o["removed_at"] is not None} for o in objects],
        "introspected_at": last, "schema_checksum": row["schema_checksum"], "samples_stored": False,
    }
