"""PostgreSQL adapter (US-024; RULE-001, RULE-006; API-062).

Read-only enforcement has two layers:

1. **Privilege proof** (``verify_read_only``): the login role and every role it
   belongs to is not superuser, cannot create roles or databases, replicate or
   bypass RLS; it has no INSERT/UPDATE/DELETE/TRUNCATE/TRIGGER/REFERENCES on any
   object in the approved schemas, no CREATE on those schemas and no CREATE on
   the database. The verdict is cached for at most 60 s per credential set.
2. **Session backstop**: every pooled connection runs with
   ``default_transaction_read_only = on`` and every statement runs inside a
   ``READ ONLY`` transaction with ``SET LOCAL statement_timeout``; the
   transaction's read-only flag is re-checked on every checkout.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import date, datetime
from datetime import time as dtime
from decimal import Decimal
from typing import Any
from uuid import UUID

import psycopg
from psycopg import sql as pgsql
from psycopg.conninfo import make_conninfo
from psycopg_pool import AsyncConnectionPool

from tela_common.errors import ErrorCode, TelaError
from tela_common.logging import get_logger
from tela_runner.adapters.base import (
    SCHEMA_CHANGED_MESSAGE,
    Catalog,
    CatalogObject,
    ConnectionInfo,
    Limits,
    ReadOnlyVerdict,
    ResultSet,
    TestResult,
)

log = get_logger(__name__)


WRITE_PRIVILEGES = ("INSERT", "UPDATE", "DELETE", "TRUNCATE", "TRIGGER", "REFERENCES")
ROLE_FLAGS = {
    "rolsuper": "is a superuser",
    "rolcreaterole": "can create roles",
    "rolcreatedb": "can create databases",
    "rolreplication": "can start replication",
    "rolbypassrls": "can bypass row-level security",
}

_ROLES_SQL = """
SELECT r.rolname, r.rolsuper, r.rolcreaterole, r.rolcreatedb, r.rolreplication, r.rolbypassrls
FROM pg_roles r WHERE pg_has_role(current_user, r.oid, 'MEMBER')
"""
_TABLE_PRIVS_SQL = """
SELECT n.nspname || '.' || c.relname AS obj, p.priv
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
CROSS JOIN unnest(%(privs)s::text[]) AS p(priv)
WHERE n.nspname = ANY(%(schemas)s) AND c.relkind IN ('r', 'p', 'v', 'm', 'f')
  AND has_table_privilege(c.oid, p.priv)
ORDER BY 1, 2
"""
_OBJECTS_SQL = """
SELECT c.oid, n.nspname, c.relname, c.relkind, c.reltuples::bigint AS reltuples
FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = ANY(%(schemas)s) AND c.relkind IN ('r', 'p', 'v', 'm') AND NOT c.relispartition
  AND (%(names)s::text[] IS NULL OR (n.nspname || '.' || c.relname) = ANY(%(names)s::text[]))
ORDER BY n.nspname, c.relname
"""
_COLUMNS_SQL = """
SELECT a.attrelid, a.attnum, a.attname, format_type(a.atttypid, a.atttypmod) AS type, NOT a.attnotnull AS nullable
FROM pg_attribute a WHERE a.attrelid = ANY(%(oids)s) AND a.attnum > 0 AND NOT a.attisdropped
ORDER BY a.attrelid, a.attnum
"""
_CONSTRAINTS_SQL = """
SELECT k.conrelid, k.contype, k.conkey, k.confkey, fn.nspname || '.' || fc.relname AS ref_object, k.confrelid
FROM pg_constraint k
LEFT JOIN pg_class fc ON fc.oid = k.confrelid
LEFT JOIN pg_namespace fn ON fn.oid = fc.relnamespace
WHERE k.conrelid = ANY(%(oids)s) AND k.contype IN ('p', 'f')
ORDER BY k.conrelid, k.conname
"""


def _jsonable(value: Any) -> Any:
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() and abs(value) < 2**53 else float(value)
    if isinstance(value, datetime | date | dtime):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, bytes | memoryview):
        return "[BINARY]"
    return value


def _type_kind(type_name: str) -> str:
    t = type_name.lower()
    if t in ("int2", "int4", "int8", "smallint", "integer", "bigint"):
        return "integer"
    if t in ("numeric", "float4", "float8", "real", "double precision", "money"):
        return "decimal"
    if t == "date":
        return "date"
    if t.startswith("timestamp"):
        return "datetime"
    if t in ("bool", "boolean"):
        return "boolean"
    if t in ("json", "jsonb"):
        return "json"
    return "text"


class PostgresAdapter:
    engine = "postgres"

    def __init__(self, *, pool_min: int = 1, pool_max: int = 5, concurrency: int = 4,
                 read_only_ttl_s: int = 60, allow_insecure_tls: bool = False, max_pools: int = 32,
                 **_: Any) -> None:
        self.pool_min, self.pool_max = pool_min, pool_max
        self.max_pools = max_pools
        self._last_used: dict[str, float] = {}
        self.concurrency = concurrency
        self.read_only_ttl_s = read_only_ttl_s
        self.allow_insecure_tls = allow_insecure_tls
        self._pools: dict[str, AsyncConnectionPool] = {}
        self._slots: dict[str, asyncio.Semaphore] = {}
        self._verdicts: dict[str, tuple[float, ReadOnlyVerdict]] = {}
        self._columns: dict[str, tuple[float, dict[str, list[tuple[str, str]]]]] = {}
        self._running: dict[str, psycopg.AsyncConnection[Any]] = {}
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------ connections

    def _conninfo(self, info: ConnectionInfo) -> str:
        d, c = info.descriptor, info.credentials
        sslmode = (d.get("tls") or {}).get("mode", "verify-full")
        if sslmode in ("disable", "allow", "prefer") and not self.allow_insecure_tls:
            raise TelaError(ErrorCode.VALIDATION_ERROR, "TLS is required for this connection.")
        via = d.get("via") or {}  # SSH tunnel (ENH-003): connect to it, verify TLS against the real host
        return make_conninfo(
            host=d["host"], hostaddr=via.get("host"), port=int(via.get("port") or d.get("port", 5432)),
            dbname=d["database"],
            user=c.get("username") or d.get("username"), password=c.get("password"),
            sslmode=sslmode, connect_timeout=10, application_name="tela-connector-runner",
        )

    def _key(self, info: ConnectionInfo) -> str:
        return hashlib.sha256(f"{info.connection_id}|{self._conninfo(info)}".encode()).hexdigest()

    @staticmethod
    async def _configure(conn: psycopg.AsyncConnection[Any]) -> None:
        await conn.set_autocommit(True)
        await conn.execute("SET default_transaction_read_only = on")
        await conn.execute("SET idle_in_transaction_session_timeout = '30s'")
        await conn.set_autocommit(False)
        await conn.set_read_only(True)

    async def _pool(self, info: ConnectionInfo) -> AsyncConnectionPool:
        key = self._key(info)
        async with self._lock:
            self._last_used[key] = time.monotonic()
            pool = self._pools.get(key)
            if pool is None:
                await self._evict()
                pool = AsyncConnectionPool(
                    self._conninfo(info), min_size=self.pool_min, max_size=self.pool_max, max_idle=300,
                    configure=self._configure, check=AsyncConnectionPool.check_connection, open=False,
                    name=f"src-{info.connection_id[:8]}",
                )
                await pool.open(wait=False)
                self._pools[key] = pool
                self._slots[key] = asyncio.Semaphore(self.concurrency)
        return pool

    async def _evict(self) -> None:
        """Bound the number of open pools (sources x credential sets): close the least recently used idle ones,
        so connections that were edited, disabled or deleted never hold source sessions forever."""
        while len(self._pools) >= self.max_pools:
            idle = [k for k in self._pools if self._slots[k]._value == self.concurrency]
            if not idle:
                return
            victim = min(idle, key=lambda k: self._last_used.get(k, 0.0))
            pool = self._pools.pop(victim)
            self._slots.pop(victim, None)
            self._last_used.pop(victim, None)
            await pool.close()

    @asynccontextmanager
    async def _connection(self, info: ConnectionInfo) -> AsyncIterator[psycopg.AsyncConnection[Any]]:
        pool = await self._pool(info)
        try:
            async with pool.connection(timeout=10) as conn:
                yield conn
        except (psycopg.OperationalError, TimeoutError) as exc:
            if isinstance(exc, psycopg.errors.QueryCanceled):
                raise
            raise TelaError(ErrorCode.SOURCE_UNREACHABLE, "The data source is not reachable right now.") from exc
        except Exception as exc:
            if exc.__class__.__name__ == "PoolTimeout":
                raise TelaError(ErrorCode.SOURCE_UNREACHABLE, "The data source is not reachable right now.") from exc
            raise

    @asynccontextmanager
    async def _direct(self, info: ConnectionInfo) -> AsyncIterator[psycopg.AsyncConnection[Any]]:
        """A one-off connection for tests and privilege checks, so a bad source never leaves a pool retrying."""
        try:
            conn = await psycopg.AsyncConnection.connect(self._conninfo(info))
        except psycopg.OperationalError as exc:
            raise TelaError(ErrorCode.SOURCE_UNREACHABLE, "The data source is not reachable right now.",
                            {"auth_failed": "authentication" in str(exc).lower()}) from exc
        try:
            await self._configure(conn)
            yield conn
        finally:
            await conn.close()

    # ------------------------------------------------------------------ RULE-001

    async def verify_read_only(self, info: ConnectionInfo, *, force: bool = False) -> ReadOnlyVerdict:
        key = self._key(info)
        hit = self._verdicts.get(key)
        if hit and not force and time.monotonic() - hit[0] < self.read_only_ttl_s:
            return hit[1]
        verdict = ReadOnlyVerdict("safe")
        async with self._direct(info) as conn, conn.transaction():
            cur = conn.cursor()
            await cur.execute(_ROLES_SQL)
            for row in await cur.fetchall():
                for i, (_flag, words) in enumerate(ROLE_FLAGS.items(), start=1):
                    if row[i]:
                        verdict.reasons.append(f"account (role {row[0]}) {words}")
            await cur.execute(_TABLE_PRIVS_SQL, {"privs": list(WRITE_PRIVILEGES), "schemas": info.schemas})
            writes = await cur.fetchall()
            for obj, priv in writes[:20]:
                verdict.reasons.append(f"account has {priv} on {obj}")
            if len(writes) > 20:
                verdict.reasons.append(f"and {len(writes) - 20} more write privileges")
            await cur.execute(
                "SELECT s FROM unnest(%(s)s::text[]) AS s "
                "WHERE EXISTS (SELECT 1 FROM pg_namespace n WHERE n.nspname = s) AND has_schema_privilege(s, 'CREATE')",
                {"s": info.schemas},
            )
            for (schema,) in await cur.fetchall():
                verdict.reasons.append(f"account can create objects in schema {schema}")
            await cur.execute("SELECT has_database_privilege(current_database(), 'CREATE')")
            row = await cur.fetchone()
            if row and row[0]:
                verdict.reasons.append("account can create schemas in the database")
            await cur.execute("SELECT current_setting('transaction_read_only')")
            row = await cur.fetchone()
            if not row or row[0] != "on":
                verdict.reasons.append("session read-only flag could not be applied")
        verdict.checks = ["no superuser or role-management attributes", "no write privileges on approved objects",
                          "no CREATE on approved schemas or the database", "session read-only applied"]
        if verdict.reasons:
            verdict.verdict = "unsafe"
        self._verdicts[key] = (time.monotonic(), verdict)
        return verdict

    async def test_connection(self, info: ConnectionInfo) -> TestResult:
        started = time.monotonic()
        try:
            async with self._direct(info) as conn, conn.transaction():
                cur = conn.cursor()
                await cur.execute("SHOW server_version")
                row = await cur.fetchone()
                version = row[0] if row else None
            latency = int((time.monotonic() - started) * 1000)
            verdict = await self.verify_read_only(info, force=True)
        except TelaError as exc:
            elapsed = int((time.monotonic() - started) * 1000)
            if exc.details.get("auth_failed"):
                return TestResult(True, False, ReadOnlyVerdict("unsafe", reasons=["could not sign in"]), elapsed,
                                  error="The username or password was rejected.")
            return TestResult(False, False, ReadOnlyVerdict("unsafe", reasons=["not reachable"]), elapsed,
                              error=exc.message)
        return TestResult(True, True, verdict, latency, version)

    def capabilities(self) -> dict[str, Any]:
        return {"dialect": "postgres", "custom_sql": True, "ctes": True, "window_functions": True,
                "session_read_only": True}

    # ------------------------------------------------------------------ introspection (FEAT-006)

    async def introspect(self, info: ConnectionInfo, *, max_objects: int = 2000,
                         names: list[str] | None = None) -> Catalog:
        async with self._connection(info) as conn, conn.transaction():
            cur = conn.cursor()
            await cur.execute(_OBJECTS_SQL, {"schemas": info.schemas, "names": names})
            objects = await cur.fetchall()
            per_schema: dict[str, int] = {}
            for o in objects:
                per_schema[o[1]] = per_schema.get(o[1], 0) + 1
            if names is None and len(objects) > max_objects:
                return Catalog([], "", complete=False, schemas=per_schema)
            oids = [o[0] for o in objects]
            await cur.execute(_COLUMNS_SQL, {"oids": oids})
            cols: dict[int, list[tuple[int, str, str, bool]]] = {}
            for rel, num, name, typ, nullable in await cur.fetchall():
                cols.setdefault(rel, []).append((num, name, typ, nullable))
            await cur.execute(_CONSTRAINTS_SQL, {"oids": oids})
            cons = await cur.fetchall()
        ref_cols: dict[int, dict[int, str]] = {rel: {n: name for n, name, _, _ in c} for rel, c in cols.items()}
        result: list[CatalogObject] = []
        for oid, schema, rel, kind, tuples in objects:
            by_num = {n: name for n, name, _, _ in cols.get(oid, [])}
            pk: list[str] = []
            fks: list[dict[str, Any]] = []
            for conrel, contype, conkey, confkey, ref_obj, confrel in cons:
                if conrel != oid:
                    continue
                if contype == "p":
                    pk = [by_num[n] for n in conkey]
                else:
                    fks.append({"columns": [by_num[n] for n in conkey], "ref_object": ref_obj,
                                "ref_columns": [ref_cols.get(confrel, {}).get(n, f"#{n}") for n in confkey]})
            fk_by_col = {c[0]: f"{fk['ref_object']}.{fk['ref_columns'][0]}" for fk in fks for c in [fk["columns"]]
                         if len(fk["columns"]) == 1}
            columns = [{"name": name, "type": typ, "nullable": nullable, "pk": name in pk,
                        "fk_ref": fk_by_col.get(name)} for _, name, typ, nullable in cols.get(oid, [])]
            shape = {"kind": kind, "columns": [[c["name"], c["type"], c["nullable"]] for c in columns],
                     "pk": pk, "fks": fks}
            checksum = hashlib.sha256(json.dumps(shape, sort_keys=True).encode()).hexdigest()
            result.append(CatalogObject(
                qualified_name=f"{schema}.{rel}", kind="view" if kind in ("v", "m") else "table", columns=columns,
                primary_key=pk, foreign_keys=fks, row_estimate=max(int(tuples), 0) if tuples is not None else None,
                checksum=checksum,
            ))
        overall = hashlib.sha256("|".join(f"{o.qualified_name}:{o.checksum}" for o in result).encode()).hexdigest()
        return Catalog(result, overall, complete=True, schemas=per_schema)

    def forget_columns(self, info: ConnectionInfo) -> None:
        prefix = self._key(info) + "|"
        for key in [k for k in self._columns if k.startswith(prefix)]:
            self._columns.pop(key, None)

    async def object_columns(self, info: ConnectionInfo, names: list[str]) -> dict[str, list[tuple[str, str]]]:
        """Current (column, type) pairs of the named objects; cached as long as the read-only verdict."""
        key = self._key(info) + "|" + ",".join(sorted(names))
        hit = self._columns.get(key)
        if hit and time.monotonic() - hit[0] < self.read_only_ttl_s:
            return hit[1]
        catalog = await self.introspect(info, names=sorted(names))
        cols = {o.qualified_name: [(c["name"], c["type"]) for c in o.columns] for o in catalog.objects}
        self._columns[key] = (time.monotonic(), cols)
        return cols

    # ------------------------------------------------------------------ execution

    async def execute(self, info: ConnectionInfo, sql: str, params: dict[str, Any], limits: Limits,
                      execution_id: str) -> ResultSet:
        key = self._key(info)
        await self._pool(info)
        started = time.monotonic()
        async with self._slots[key], self._connection(info) as conn:
            self._running[execution_id] = conn
            try:
                async with conn.transaction():
                    cur = conn.cursor()
                    await cur.execute("SELECT current_setting('transaction_read_only')")
                    flag = await cur.fetchone()
                    if not flag or flag[0] != "on":
                        raise TelaError(ErrorCode.READ_ONLY_VIOLATION,
                                        "The data source account is no longer verifiably read-only.")
                    await cur.execute(pgsql.SQL("SET LOCAL statement_timeout = {}").format(
                        pgsql.Literal(f"{int(limits.timeout_ms)}ms")))
                    await cur.execute(sql, params)  # compiled, signed and guarded SQL
                    columns = [{"name": d.name, "type": _type_kind(self._type_name(conn, d.type_code))}
                               for d in (cur.description or [])]
                    rows: list[list[Any]] = []
                    size = 0
                    while len(rows) <= limits.max_rows:
                        batch = await cur.fetchmany(min(1000, limits.max_rows + 1 - len(rows)))
                        if not batch:
                            break
                        for r in batch:
                            out = [_jsonable(v) for v in r]
                            size += len(json.dumps(out, default=str))
                            if size > limits.max_bytes:
                                raise TelaError(ErrorCode.PAYLOAD_TOO_LARGE,
                                                "The result is too large. Add filters or fewer columns.")
                            rows.append(out)
            except psycopg.errors.QueryCanceled as exc:
                raise TelaError(ErrorCode.QUERY_TIMEOUT,
                                "The query took too long and was stopped. Try narrowing it.") from exc
            except psycopg.errors.ReadOnlySqlTransaction as exc:
                raise TelaError(ErrorCode.READ_ONLY_VIOLATION, "Write attempt refused by the source.") from exc
            except psycopg.errors.InsufficientPrivilege as exc:
                raise TelaError(ErrorCode.SQL_OBJECT_NOT_ALLOWED,
                                "The data source refused access to an object.") from exc
            except (psycopg.errors.UndefinedColumn, psycopg.errors.UndefinedTable) as exc:
                self.forget_columns(info)
                raise TelaError(ErrorCode.SCHEMA_CHANGED, SCHEMA_CHANGED_MESSAGE) from exc
            except psycopg.Error as exc:
                # The SQLSTATE is logged; the message (which can quote values) is not.
                log.warning("runner.query_failed", sqlstate=exc.sqlstate)
                raise TelaError(ErrorCode.QUERY_FAILED, "The data source could not run this query.",
                                {"sqlstate": exc.sqlstate}) from exc
            finally:
                self._running.pop(execution_id, None)
        truncated = len(rows) > limits.max_rows
        return ResultSet(columns, rows[: limits.max_rows], truncated, int((time.monotonic() - started) * 1000))

    @staticmethod
    def _type_name(conn: psycopg.AsyncConnection[Any], oid: int) -> str:
        info = conn.adapters.types.get(oid)
        return info.name if info else "text"

    async def cancel(self, execution_id: str) -> bool:
        conn = self._running.get(execution_id)
        if conn is None:
            return False
        await conn.cancel_safe()
        return True

    async def close(self) -> None:
        for pool in self._pools.values():
            await pool.close()
        self._pools.clear()
