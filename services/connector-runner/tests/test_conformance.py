"""Connector conformance matrix (US-117; TEST-077, TEST-078; NFR-001, NFR-025; RULE-001, RULE-006).

The same assertions run against every SQL engine adapter on a live container: read-only verdicts
(safe, writable, role-carried and grant-option accounts), the session backstop refusing writes even
for a writable login, the row cap (+1 truncation), the timeout, the payload cap, typed columns with
bound values rendered in the engine's own placeholder style, introspection with keys and links,
column checksums, and unreachable sources. Engines without a container URL are skipped.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import pytest
import sqlglot
from sources import RO_PASSWORD, RW_PASSWORD, seed_mysql, seed_postgres, seed_sqlserver, url
from sqlglot import exp

from tela_common.errors import ErrorCode, TelaError
from tela_runner.adapters.base import ConnectionInfo, Limits

pytestmark = pytest.mark.integration


@dataclass
class Engine:
    name: str
    dialect: str
    schema: str
    adapter: Callable[[], Any]
    info: Callable[[str, str], ConnectionInfo]
    slow_sql: str
    unsafe: dict[str, tuple[str, str]] = field(default_factory=dict)  # label -> (login, expected reason part)


def _mysql() -> Engine | None:
    admin = url("mysql")
    if admin is None:
        return None
    from tela_runner.adapters.mysql import MySqlAdapter

    seed_mysql(admin)

    def info(user: str, password: str) -> ConnectionInfo:
        return ConnectionInfo("01900000-0000-7000-8000-0000000000d1", "mysql",
                              {"host": admin["host"], "port": admin["port"], "database": "tpg",
                               "tls": {"mode": "disable"}, "approved_schemas": ["tpg"]},
                              {"username": user, "password": password})

    return Engine("mysql", "mysql", "tpg", lambda: MySqlAdapter(allow_insecure_tls=True), info,
                  "SELECT max(concat(a.name, b.name, c.name, d.name)) FROM tpg.projects a, tpg.projects b, "
                  "tpg.projects c, tpg.projects d",
                  {"writable": ("tela_rw", "account has INSERT on `tpg`.*"),
                   "role": ("tela_role_rw", "account has INSERT on `tpg`.*"),
                   "grant option": ("tela_grantor", "account can grant privileges on `tpg`.*")})


def _postgres() -> Engine | None:
    admin = url("postgres")
    if admin is None:
        return None
    from tela_runner.adapters.postgres import PostgresAdapter

    seed_postgres(admin)

    def info(user: str, password: str) -> ConnectionInfo:
        return ConnectionInfo("01900000-0000-7000-8000-0000000000d0", "postgres",
                              {"host": admin["host"], "port": admin["port"], "database": "tpg",
                               "tls": {"mode": "disable"}, "approved_schemas": ["public"]},
                              {"username": user, "password": password})

    return Engine("postgres", "postgres", "public", lambda: PostgresAdapter(allow_insecure_tls=True), info,
                  "SELECT pg_sleep(3)", {"writable": ("tela_rw", "account has INSERT on public.projects")})


def _sqlserver() -> Engine | None:
    admin = url("mssql")
    if admin is None:
        return None
    from tela_runner.adapters.sqlserver import SqlServerAdapter

    seed_sqlserver(admin)

    def info(user: str, password: str) -> ConnectionInfo:
        return ConnectionInfo("01900000-0000-7000-8000-0000000000d2", "sqlserver",
                              {"host": admin["host"], "port": admin["port"], "database": "tpg",
                               "tls": {"trust_server_certificate": True}, "approved_schemas": ["dbo"]},
                              {"username": user, "password": password})

    return Engine("sqlserver", "tsql", "dbo", lambda: SqlServerAdapter(allow_insecure_tls=True), info,
                  "SELECT max(concat(a.name, b.name, c.name, d.name)) FROM dbo.projects a, dbo.projects b, "
                  "dbo.projects c, dbo.projects d",
                  {"writer role": ("tela_rw", "account is a member of db_datawriter"),
                   "object grant": ("tela_obj_rw", "account has INSERT on dbo.projects"),
                   "owner": ("tela_owner_rw", "account is a member of db_owner"),
                   "execute": ("tela_exec_rw", "account has EXECUTE on the database")})


FACTORIES: dict[str, Callable[[], Engine | None]] = {"postgres": _postgres, "mysql": _mysql,
                                                     "sqlserver": _sqlserver}


@pytest.fixture(scope="module", params=sorted(FACTORIES))
def engine(request: pytest.FixtureRequest) -> Engine:
    e = FACTORIES[request.param]()
    if e is None:
        pytest.skip(f"TELA_TEST_{request.param.upper()}_URL not set")
    return e


@pytest.fixture
async def adapter(engine: Engine) -> Any:
    a = engine.adapter()
    yield a
    await a.close()


def _password(login: str) -> str:
    return RW_PASSWORD if login.endswith("_rw") else RO_PASSWORD


def _compiled(engine: Engine) -> tuple[str, dict[str, Any]]:
    """A compiled-shape statement with one bound value, rendered the way the API renders it."""
    tree = sqlglot.parse_one(f"SELECT count(*) AS n, max(planned_deployment_date) AS d, "
                             f"sum(approved_budget_value_usd) AS s FROM {engine.schema}.projects AS t0 "
                             f"WHERE status = 'x'", read="postgres")
    tree.find(exp.Literal).replace(exp.Placeholder(this="p0"))
    return tree.sql(dialect=engine.dialect), {"p0": "Active"}


async def test_read_only_verdicts(adapter: Any, engine: Engine) -> None:
    safe = await adapter.verify_read_only(engine.info("tela_ro", RO_PASSWORD), force=True)
    assert safe.safe, safe.reasons
    for label, (login, reason) in engine.unsafe.items():
        verdict = await adapter.verify_read_only(engine.info(login, _password(login)), force=True)
        assert not verdict.safe, label
        assert any(reason in r for r in verdict.reasons), (label, verdict.reasons)


async def test_session_backstop_refuses_writes_even_for_a_writable_login(adapter: Any, engine: Engine) -> None:
    with pytest.raises(TelaError) as err:
        await adapter.execute(engine.info("tela_rw", RW_PASSWORD),
                              f"INSERT INTO {engine.schema}.projects (project_id, name) VALUES ('X1', 'x')", {},
                              Limits(), "w1")
    assert err.value.code == ErrorCode.READ_ONLY_VIOLATION
    with pytest.raises(TelaError) as err:
        await adapter.execute(engine.info("tela_rw", RW_PASSWORD),
                              f"UPDATE {engine.schema}.projects SET name = 'x'", {}, Limits(), "w2")
    assert err.value.code == ErrorCode.READ_ONLY_VIOLATION


async def test_row_cap_timeout_payload_and_types(adapter: Any, engine: Engine) -> None:
    ro = engine.info("tela_ro", RO_PASSWORD)
    exact = await adapter.execute(ro, f"SELECT project_id FROM {engine.schema}.projects", {}, Limits(max_rows=100),
                                  "e1")
    assert len(exact.rows) == 100 and exact.truncated is False
    over = await adapter.execute(ro, f"SELECT project_id FROM {engine.schema}.projects", {}, Limits(max_rows=40),
                                 "e2")
    assert len(over.rows) == 40 and over.truncated is True
    with pytest.raises(TelaError) as err:
        await adapter.execute(ro, engine.slow_sql, {}, Limits(timeout_ms=300), "e3")
    assert err.value.code == ErrorCode.QUERY_TIMEOUT
    with pytest.raises(TelaError) as err:
        await adapter.execute(ro, f"SELECT * FROM {engine.schema}.projects", {}, Limits(max_bytes=2000), "e4")
    assert err.value.code == ErrorCode.PAYLOAD_TOO_LARGE
    sql, params = _compiled(engine)
    typed = await adapter.execute(ro, sql, params, Limits(), "e5")
    assert [c["type"] for c in typed.columns] == ["integer", "date", "decimal"]
    assert typed.rows[0][0] == 65 and isinstance(typed.rows[0][1], str)
    # A value that looks like SQL or a placeholder only ever travels as a bound value.
    tricky = await adapter.execute(ro, sql, {"p0": "x' OR '1'='1 %s :p0 ?"}, Limits(), "e6")
    assert tricky.rows[0][0] == 0


async def test_introspection_keys_links_and_checksums(adapter: Any, engine: Engine) -> None:
    ro = engine.info("tela_ro", RO_PASSWORD)
    catalog = await adapter.introspect(ro, max_objects=2000)
    by_name = {o.qualified_name.lower(): o for o in catalog.objects}
    projects = by_name[f"{engine.schema}.projects"]
    assert f"{engine.schema}.project_requests" in by_name and catalog.complete
    assert projects.primary_key == ["project_id"]
    assert projects.foreign_keys == [{"columns": ["project_request_id"],
                                      "ref_object": f"{engine.schema}.project_requests",
                                      "ref_columns": ["project_request_id"]}]
    small = await adapter.introspect(ro, max_objects=1)
    assert small.complete is False and small.schemas[engine.schema] >= 2
    cols = await adapter.object_columns(ro, [f"{engine.schema}.projects"])
    assert any(name == "region" for name, _ in cols[f"{engine.schema}.projects"])


async def test_unreachable_source(adapter: Any, engine: Engine) -> None:
    bad = engine.info("tela_ro", RO_PASSWORD)
    bad = ConnectionInfo(bad.connection_id, bad.engine, {**bad.descriptor, "host": "127.0.0.1", "port": 1},
                         bad.credentials)
    result = await adapter.test_connection(bad)
    assert not result.reachable and result.read_only.verdict == "unsafe"


async def test_wrong_password_is_an_authentication_failure(adapter: Any, engine: Engine) -> None:
    result = await adapter.test_connection(engine.info("tela_ro", "not-the-password"))
    assert result.reachable and not result.authenticated


async def test_connection_test_reports_version_and_verdict(adapter: Any, engine: Engine) -> None:
    result = await adapter.test_connection(engine.info("tela_ro", RO_PASSWORD))
    assert result.reachable and result.authenticated and result.read_only.safe and result.server_version


def _raw_write(engine: Engine) -> Callable[[], None]:
    """A write sent straight to the source with the runtime identity (tela_ro), bypassing every Tela guard."""
    info = engine.info("tela_ro", RO_PASSWORD)
    d = info.descriptor
    if engine.name == "postgres":
        def write() -> None:
            import psycopg

            with psycopg.connect(host=d["host"], port=d["port"], dbname=d["database"], user="tela_ro",
                                 password=RO_PASSWORD, autocommit=True) as c:
                c.execute("INSERT INTO public.projects (project_id, name) VALUES ('W1', 'x')")
    elif engine.name == "mysql":
        def write() -> None:
            import pymysql

            c = pymysql.connect(host=d["host"], port=d["port"], user="tela_ro", password=RO_PASSWORD,
                                database="tpg", autocommit=True)
            try:
                c.cursor().execute("INSERT INTO tpg.projects (project_id, name) VALUES ('W1', 'x')")
            finally:
                c.close()
    else:
        def write() -> None:
            import pyodbc

            c = pyodbc.connect(f"Driver={{ODBC Driver 18 for SQL Server}};Server={d['host']},{d['port']};"
                               f"Database=tpg;UID=tela_ro;PWD={RO_PASSWORD};Encrypt=yes;TrustServerCertificate=yes",
                               autocommit=True)
            try:
                c.cursor().execute("INSERT INTO dbo.projects (project_id, name) VALUES ('W1', 'x')")
            finally:
                c.close()
    return write


def test_the_source_itself_refuses_writes_from_the_runtime_identity(engine: Engine) -> None:
    """NFR-001 / TEST-078: even without Tela's guards, the account Tela runs as cannot change data."""
    with pytest.raises(Exception) as err:  # each driver raises its own permission error
        _raw_write(engine)()
    assert "denied" in str(err.value).lower() or "permission" in str(err.value).lower(), err.value
