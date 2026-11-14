"""Connector contract suite against a live PostgreSQL (US-024; TEST-077; RULE-001, RULE-006).

Every later adapter runs the same assertions. Needs the synthetic TPG source created by the API test
fixtures (database ``tpg`` with logins ``tela_ro`` and ``tela_rw``); skipped without a test database.
"""

from __future__ import annotations

import os
from typing import Any

import pytest
from psycopg.conninfo import conninfo_to_dict

from tela_common.errors import ErrorCode, TelaError
from tela_runner.adapters.base import ConnectionInfo, Limits
from tela_runner.adapters.postgres import PostgresAdapter

OWNER = os.environ.get("TELA_TEST_OWNER_DATABASE_URL")
pytestmark = [pytest.mark.skipif(not OWNER, reason="TELA_TEST_OWNER_DATABASE_URL not set"), pytest.mark.integration]


def _info(user: str, password: str) -> ConnectionInfo:
    parts = conninfo_to_dict(OWNER or "")
    return ConnectionInfo("01900000-0000-7000-8000-0000000000c1", "postgres",
                          {"host": parts.get("host", "localhost"), "port": int(parts.get("port", 5432)),
                           "database": "tpg", "tls": {"mode": "disable"}, "approved_schemas": ["public"]},
                          {"username": user, "password": password})


@pytest.fixture(scope="module")
def source() -> Any:
    import psycopg

    with psycopg.connect(OWNER or "", autocommit=True) as c:
        if not c.execute("SELECT 1 FROM pg_database WHERE datname = 'tpg'").fetchone():
            pytest.skip("run the API integration tests first: they create the synthetic source")
    return {"ro": _info("tela_ro", "ro_pw_local_only"), "rw": _info("tela_rw", "rw_pw_local_only")}


@pytest.fixture
async def adapter() -> Any:
    a = PostgresAdapter(allow_insecure_tls=True, read_only_ttl_s=60)
    yield a
    await a.close()


async def test_read_only_verdicts(adapter: PostgresAdapter, source: dict[str, ConnectionInfo]) -> None:
    safe = await adapter.verify_read_only(source["ro"], force=True)
    assert safe.safe and safe.reasons == []
    unsafe = await adapter.verify_read_only(source["rw"], force=True)
    assert not unsafe.safe and "account has INSERT on public.projects" in unsafe.reasons
    owner = conninfo_to_dict(OWNER or "")
    superuser = await adapter.verify_read_only(_info(owner["user"], owner["password"]), force=True)
    assert any("superuser" in r for r in superuser.reasons)


async def test_session_backstop_refuses_writes_even_for_a_writable_login(
    adapter: PostgresAdapter, source: dict[str, ConnectionInfo]
) -> None:
    with pytest.raises(TelaError) as err:
        await adapter.execute(source["rw"], "INSERT INTO projects (project_id, name) VALUES ('X', 'x')", {},
                              Limits(), "exec-1")
    assert err.value.code == ErrorCode.READ_ONLY_VIOLATION


async def test_row_cap_timeout_and_payload(adapter: PostgresAdapter, source: dict[str, ConnectionInfo]) -> None:
    ro = source["ro"]
    exact = await adapter.execute(ro, "SELECT project_id FROM projects LIMIT 100", {}, Limits(max_rows=100), "e1")
    assert len(exact.rows) == 100 and exact.truncated is False
    over = await adapter.execute(ro, "SELECT project_id FROM projects", {}, Limits(max_rows=40), "e2")
    assert len(over.rows) == 40 and over.truncated is True
    with pytest.raises(TelaError) as err:
        await adapter.execute(ro, "SELECT pg_sleep(2)", {}, Limits(timeout_ms=200), "e3")
    assert err.value.code == ErrorCode.QUERY_TIMEOUT
    with pytest.raises(TelaError) as err:
        await adapter.execute(ro, "SELECT * FROM projects", {}, Limits(max_bytes=2000), "e4")
    assert err.value.code == ErrorCode.PAYLOAD_TOO_LARGE
    typed = await adapter.execute(ro, "SELECT count(*) AS n, max(planned_deployment_date) AS d, "
                                      "sum(approved_budget_value_usd) AS s FROM projects WHERE status = %(p0)s",
                                  {"p0": "Active"}, Limits(), "e5")
    assert [c["type"] for c in typed.columns] == ["integer", "date", "decimal"]
    assert typed.rows[0][0] == 65 and isinstance(typed.rows[0][1], str)


async def test_introspection_and_column_checksums(adapter: PostgresAdapter,
                                                  source: dict[str, ConnectionInfo]) -> None:
    catalog = await adapter.introspect(source["ro"], max_objects=2000)
    names = {o.qualified_name for o in catalog.objects}
    assert {"public.projects", "public.project_requests"} <= names and catalog.complete
    small = await adapter.introspect(source["ro"], max_objects=1)
    assert small.complete is False and small.schemas["public"] >= 2
    cols = await adapter.object_columns(source["ro"], ["public.projects"])
    assert ("region", "character varying(64)") in cols["public.projects"]


async def test_unreachable_source(adapter: PostgresAdapter) -> None:
    bad = ConnectionInfo("c2", "postgres", {"host": "127.0.0.1", "port": 1, "database": "x",
                                             "tls": {"mode": "disable"}}, {"username": "u", "password": "p"})
    result = await adapter.test_connection(bad)
    assert not result.reachable and result.read_only.verdict == "unsafe"


def test_tls_required_outside_development() -> None:
    strict = PostgresAdapter(allow_insecure_tls=False)
    with pytest.raises(TelaError):
        strict._conninfo(_info("u", "p"))
