"""Test fixtures.

Integration tests need a migrated PostgreSQL and two URLs:
  TELA_TEST_OWNER_DATABASE_URL  schema owner (used to reset data and bootstrap tenants)
  TELA_TEST_APP_DATABASE_URL    application role (non-owner, NOBYPASSRLS) used by the API
They are skipped when these are not set.
"""

from __future__ import annotations

import asyncio
import os
import time
from collections.abc import AsyncIterator, Callable, Iterator
from typing import Any

import httpx
import jwt
import psycopg
import pytest
from api_testkit import APP_URL, AUDIENCE, ISSUER, KID, OWNER_URL, reset_data
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm


@pytest.fixture(scope="session")
def rsa_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture(scope="session")
def jwks(rsa_key: rsa.RSAPrivateKey) -> dict[str, Any]:
    import json

    pub = json.loads(RSAAlgorithm.to_jwk(rsa_key.public_key()))
    pub.update({"kid": KID, "use": "sig", "alg": "RS256"})
    return {"keys": [pub]}


@pytest.fixture(scope="session")
def make_token(rsa_key: rsa.RSAPrivateKey) -> Callable[..., str]:
    def _make(
        sub: str = "user-1",
        *,
        groups: list[str] | None = None,
        name: str = "Test User",
        email: str | None = None,
        iss: str = ISSUER,
        aud: str = AUDIENCE,
        typ: str = "Bearer",
        exp_in: int = 3600,  # an hour: a long test (pack release checks, gold runs) outlives 5 minutes on CI
        kid: str = KID,
        extra: dict[str, Any] | None = None,
    ) -> str:
        now = int(time.time())
        claims = {"iss": iss, "aud": aud, "sub": sub, "iat": now, "exp": now + exp_in, "typ": typ,
                  "name": name, "groups": groups or [], **(extra or {})}
        if email:
            claims["email"] = email
        return jwt.encode(claims, rsa_key, algorithm="RS256", headers={"kid": kid})

    return _make


def _owner_dsn() -> str:
    assert OWNER_URL
    return OWNER_URL


@pytest.fixture
def owner_conn() -> Iterator[psycopg.Connection]:
    with psycopg.connect(_owner_dsn(), autocommit=True) as conn:
        yield conn








@pytest.fixture
async def api(jwks: dict[str, Any], owner_conn: psycopg.Connection, tmp_path: Any) -> AsyncIterator[httpx.AsyncClient]:
    from tela_api.core import db as db_module
    from tela_api.core.settings import Settings
    from tela_api.identity.jwt import JwtVerifier
    from tela_api.main import create_app

    reset_data(owner_conn)
    os.environ["TELA_SECRETS_DIR"] = str(tmp_path / "secrets")
    settings = Settings(env="test", database_url=APP_URL, oidc_issuer=ISSUER, oidc_audience=AUDIENCE,
                        principal_cache_seconds=0)
    app = create_app(settings)
    mock = httpx.MockTransport(lambda request: httpx.Response(200, json=jwks))
    app.state.jwt_verifier = JwtVerifier(
        issuer=ISSUER, audience=AUDIENCE, jwks_url="http://keycloak.test/certs", allowed_algs=["RS256"],
        access_token_types=["Bearer"], http=httpx.AsyncClient(transport=mock),
    )
    await db_module.dispose_engine()
    db_module.init_engine(settings)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://api.test") as client:
        client.app = app  # type: ignore[attr-defined]
        yield client
    await db_module.dispose_engine()


# --------------------------------------------------------------------------- P1: source database, runner, stack

import sys  # noqa: E402
from pathlib import Path  # noqa: E402

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "packs" / "pmo" / "fixtures"))
sys.path.insert(0, str(REPO / "services" / "connector-runner" / "tests"))  # shared adversarial corpus

SOURCE_DB = f"tpg_{os.environ['PYTEST_XDIST_WORKER']}" if os.environ.get("PYTEST_XDIST_WORKER") else "tpg"  # one per worker
RO_USER, RO_PASSWORD = "tela_ro", "ro_pw_local_only"
RW_USER, RW_PASSWORD = "tela_rw", "rw_pw_local_only"


def _dsn_for(database: str) -> str:
    from psycopg.conninfo import conninfo_to_dict, make_conninfo

    parts = conninfo_to_dict(_owner_dsn())
    parts["dbname"] = database
    return make_conninfo(**parts)


@pytest.fixture(scope="session")
def tpg_source() -> dict[str, Any]:
    """Synthetic TPG extract in its own database, with a read-only and a write-capable login."""
    if not OWNER_URL:
        pytest.skip("TELA_TEST_OWNER_DATABASE_URL not set")
    import tpg_synthetic
    from psycopg.conninfo import conninfo_to_dict

    with psycopg.connect(_owner_dsn(), autocommit=True) as admin:
        for user, pw in ((RO_USER, RO_PASSWORD), (RW_USER, RW_PASSWORD)):
            if not admin.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (user,)).fetchone():
                try:  # roles are cluster-wide: another xdist worker may create it first
                    admin.execute(f"CREATE ROLE {user} LOGIN PASSWORD '{pw}' NOSUPERUSER NOCREATEDB NOCREATEROLE")
                except (psycopg.errors.DuplicateObject, psycopg.errors.UniqueViolation):
                    pass
        if not admin.execute("SELECT 1 FROM pg_database WHERE datname = %s", (SOURCE_DB,)).fetchone():
            admin.execute(f"CREATE DATABASE {SOURCE_DB}")
    with psycopg.connect(_dsn_for(SOURCE_DB), autocommit=True) as src:
        tpg_synthetic.load(src)
        src.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
        src.execute(f"GRANT USAGE ON SCHEMA public TO {RO_USER}, {RW_USER}")
        src.execute(f"GRANT SELECT ON ALL TABLES IN SCHEMA public TO {RO_USER}, {RW_USER}")
        src.execute(f"GRANT INSERT ON projects TO {RW_USER}")
    parts = conninfo_to_dict(_owner_dsn())
    return {"host": parts.get("host", "localhost"), "port": int(parts.get("port", 5432)), "database": SOURCE_DB,
            "ro": (RO_USER, RO_PASSWORD), "rw": (RW_USER, RW_PASSWORD)}


@pytest.fixture(scope="session")
def signing_keys() -> dict[str, Any]:
    from tela_common import envelope

    private, public = envelope.generate_keypair()
    return {"service": "s3rv1ce-k3y-for-tests-0123456789-abcdefghijkl", "private": private, "public": public}


@pytest.fixture(scope="session")
def secrets_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return tmp_path_factory.mktemp("secrets")


@pytest.fixture(scope="session")
def runner_app(signing_keys: dict[str, Any], secrets_dir: Path) -> Any:
    from tela_common.secrets import FileSecretsStore
    from tela_runner.main import create_app as create_runner

    os.environ["TELA_SERVICE_SIGNING_KEY"] = signing_keys["service"]
    os.environ["TELA_EXECUTION_VERIFY_KEY"] = signing_keys["public"].decode()
    os.environ["TELA_ENV"] = "test"
    os.environ["TELA_RUNNER_MAX_POOLS"] = "8"  # many short-lived test connections: keep source sessions bounded
    app = create_runner(secrets=FileSecretsStore(secrets_dir))

    def forget_sources() -> None:  # a restored PMO world reuses its connection id (pmo_testkit snapshot)
        for adapter in app.state.adapters.values():
            for cache in ("_columns", "_verdicts"):
                getattr(adapter, cache, {}).clear()

    import pmo_testkit

    pmo_testkit.RESTORE_HOOKS.append(forget_sources)
    return app


@pytest.fixture
async def stack(jwks: dict[str, Any], owner_conn: psycopg.Connection, signing_keys: dict[str, Any],
                secrets_dir: Path, runner_app: Any, tpg_source: dict[str, Any]) -> AsyncIterator[httpx.AsyncClient]:
    """The API wired to an in-process connector runner and the synthetic source database."""
    from tela_api.core import db as db_module
    from tela_api.core.settings import Settings
    from tela_api.identity.jwt import JwtVerifier
    from tela_api.main import create_app
    from tela_api.runner_transport import RunnerTransport

    reset_data(owner_conn)
    os.environ["TELA_SECRETS_DIR"] = str(secrets_dir)
    os.environ.setdefault("TELA_MIGRATE_DATABASE_URL", _owner_dsn())
    settings = Settings(env="test", database_url=APP_URL, oidc_issuer=ISSUER, oidc_audience=AUDIENCE,
                        principal_cache_seconds=0, service_signing_key=signing_keys["service"],
                        execution_signing_key_pem=signing_keys["private"].decode(), runner_url="http://runner.test")
    app = create_app(settings)
    mock = httpx.MockTransport(lambda request: httpx.Response(200, json=jwks))
    app.state.jwt_verifier = JwtVerifier(
        issuer=ISSUER, audience=AUDIENCE, jwks_url="http://keycloak.test/certs", allowed_algs=["RS256"],
        access_token_types=["Bearer"], http=httpx.AsyncClient(transport=mock),
    )
    runner_http = httpx.AsyncClient(transport=httpx.ASGITransport(app=runner_app), timeout=60)
    transport = RunnerTransport("http://runner.test", signing_keys["service"].encode(), signing_keys["private"],
                                http=runner_http)
    app.state.runner = transport
    app.state.query_service.transport = transport
    app.state.gateway = None  # no LLM by default: every agent must fall back (tests opt in with fake_gateway)
    await db_module.dispose_engine()
    db_module.init_engine(settings)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://api.test") as client:
        client.app = app  # type: ignore[attr-defined]
        yield client
    from tela_api.ask import router as ask_router

    # Ask turns run as background tasks and may still be writing (the agent run log, ENH-024) after their stream
    # ended: let them finish before the next test's reset truncates the tables they hold locks on.
    if ask_router._tasks:
        await asyncio.wait(list(ask_router._tasks), timeout=30)
    await runner_http.aclose()
    await db_module.dispose_engine()


class FakeGateway:
    """Stands in for the LLM gateway: canned JSON per agent, records every call (no prompt text kept)."""

    def __init__(self, replies: dict[str, Any] | None = None, allowed: list[str] | None = None,
                 fail: str | None = None, embedder: Any = None, embed_fail: bool = False,
                 reranker: Any = None, rerank_fail: bool = False, default_provider: str | None = None) -> None:
        self.replies = replies or {}
        self.default_provider = default_provider
        self.allowed = allowed if allowed is not None else ["ollama"]
        self.fail = fail
        self.embedder = embedder  # text -> 1024 floats; default: the "request" direction trick below
        self.embed_fail = embed_fail
        self.reranker = reranker  # (query, text) -> score; default: no reranker deployed (scores null)
        self.rerank_fail = rerank_fail
        self.calls: list[dict[str, Any]] = []
        self.reranks: list[dict[str, Any]] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        import json as _json

        if request.url.path == "/providers":
            return httpx.Response(200, json={
                "allowed": self.allowed, "default_provider": self.default_provider, "default_model": None,
                "planner_model": None, "allow_aggregates": True,
                "keyed": ["anthropic", "azure_openai", "gemini", "openai"],
                "embedders": ["azure_openai", "gemini", "ollama", "openai"], "embed_dim": 1024})
        body = _json.loads(request.content)
        if request.url.path == "/rerank":
            self.reranks.append({"query": body["query"], "texts": body["texts"]})
            if self.rerank_fail:
                return httpx.Response(503, json={"error": {"code": "planner_unavailable",
                                                           "message": "The reranker is unavailable."}})
            scores = [self.reranker(body["query"], t) for t in body["texts"]] if self.reranker else None
            return httpx.Response(200, json={"model": "BAAI/bge-reranker-v2-m3" if scores else None,
                                             "scores": scores})
        if request.url.path == "/embed":
            self.calls.append({"agent": "embed", "task": "embed", "messages": []})
            if self.fail or self.embed_fail:
                return httpx.Response(503, json={"error": {"code": self.fail or "planner_unavailable",
                                                           "message": "model unavailable"}})
            if self.embedder is not None:
                return httpx.Response(200, json={"model": "mxbai-embed-large", "dim": 1024,
                                                 "vectors": [self.embedder(t) for t in body["texts"]]})
            # Same direction for texts that share a word with "demand"/"request", orthogonal otherwise.
            vecs = [[1.0, 0.0] + [0.0] * 1022 if "request" in t.lower() else [0.0, 1.0] + [0.0] * 1022
                    for t in body["texts"]]
            return httpx.Response(200, json={"model": "mxbai-embed-large", "dim": 1024, "vectors": vecs})
        self.calls.append({"agent": body["agent"], "task": body["task"], "messages": body["messages"],
                           "temperature": body.get("temperature"), "json_schema": body.get("json_schema"),
                           "capture": body.get("capture", False)})
        if self.fail:
            return httpx.Response(503, json={"error": {"code": self.fail, "message": "model unavailable"}})
        reply = self.replies.get(body["agent"], {})
        if callable(reply):  # a reply per call: (task, messages) -> JSON, e.g. a repair after a refusal (US-181)
            reply = reply(body["task"], body["messages"])
        if isinstance(reply, dict) and "__invalid__" in reply:  # the gateway's schema check failed (US-191)
            return httpx.Response(422, json={"error": {"code": "invalid_output", "message": "format check",
                                                       "details": {"text": reply["__invalid__"],
                                                                   "error": "measure: not a string"}}})
        out = {"text": _json.dumps(reply), "json": reply, "provider": "ollama", "model": "llama3.2:3b",
               "usage": {"prompt_tokens": 40, "completion_tokens": 9}, "latency_ms": 5}
        if body.get("capture"):  # what the real gateway returns for the agent run log (US-226)
            out["trace"] = {"messages": body["messages"], "reply": out["text"], "provider": "ollama",
                            "model": "llama3.2:3b", "cut": False, "scanner": "clean", "status": "ok",
                            "usage": out["usage"]}
        return httpx.Response(200, json=out)

    def client(self) -> Any:
        from tela_api.llm.client import GatewayClient

        return GatewayClient("http://gateway.test", b"k" * 40, http=httpx.AsyncClient(
            transport=httpx.MockTransport(self.handler)))
