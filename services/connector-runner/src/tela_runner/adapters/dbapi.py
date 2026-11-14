"""Shared machinery for engines reached through a blocking DB-API driver (US-113 MySQL, US-114 SQL Server,
US-110 Dataverse TDS).

The driver calls run in worker threads. Each source key (connection + credential
set) gets a small pool of configured sessions and a concurrency semaphore; the
least recently used idle pools are closed beyond ``max_pools``. Engines supply:

* ``_connect`` — open one session with the engine's read-only session controls;
* ``_begin`` — start a read-only unit of work with the statement timeout and
  return whether the session is still read-only (re-checked on every checkout);
* ``_privileges`` — the read-only privilege proof (RULE-001), fail closed;
* ``_catalog`` — objects, columns and keys in the approved schemas;
* ``_classify`` — map a driver error to a stable Tela error.

Row cap (+1 to detect truncation), payload cap and JSON conversion are common.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from datetime import time as dtime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

import sqlglot
from sqlglot import exp

from tela_common.errors import ErrorCode, TelaError
from tela_common.logging import get_logger
from tela_common.sql_guard import FORBIDDEN_NODES
from tela_runner.adapters.base import (
    Catalog,
    CatalogObject,
    ConnectionInfo,
    Limits,
    ReadOnlyVerdict,
    ResultSet,
    TestResult,
)
from tela_runner.adapters.params import bind_positional

log = get_logger(__name__)


def jsonable(value: Any) -> Any:
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() and abs(value) < 2**53 else float(value)
    if isinstance(value, datetime | date | dtime):
        return value.isoformat()
    if isinstance(value, timedelta):
        return str(value)
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, bytes | bytearray | memoryview):
        return "[BINARY]"
    return value


def collect_rows(fetchmany: Callable[[int], list[Any]], limits: Limits) -> list[list[Any]]:
    """Fetch up to ``max_rows + 1`` rows (truncation detection) within the payload cap."""
    rows: list[list[Any]] = []
    size = 0
    while len(rows) <= limits.max_rows:
        batch = fetchmany(min(1000, limits.max_rows + 1 - len(rows)))
        if not batch:
            break
        for r in batch:
            out = [jsonable(v) for v in r]
            size += len(json.dumps(out, default=str))
            if size > limits.max_bytes:
                raise TelaError(ErrorCode.PAYLOAD_TOO_LARGE, "The result is too large. Add filters or fewer columns.")
            rows.append(out)
    return rows


@dataclass
class RawObject:
    schema: str
    name: str
    kind: str  # table | view
    row_estimate: int | None = None
    columns: list[tuple[str, str, bool]] = field(default_factory=list)  # (name, type, nullable)
    primary_key: list[str] = field(default_factory=list)
    foreign_keys: list[dict[str, Any]] = field(default_factory=list)  # {columns, ref_object, ref_columns}
    display_name: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)  # engine metadata (e.g. Dataverse choice labels)


def build_catalog(raw: list[RawObject], per_schema: dict[str, int]) -> Catalog:
    result: list[CatalogObject] = []
    for o in raw:
        fk_by_col = {fk["columns"][0]: f"{fk['ref_object']}.{fk['ref_columns'][0]}" for fk in o.foreign_keys
                     if len(fk["columns"]) == 1}
        columns: list[dict[str, Any]] = [{"name": n, "type": t, "nullable": nl, "pk": n in o.primary_key,
                                          "fk_ref": fk_by_col.get(n)} for n, t, nl in o.columns]
        for col in columns:
            meta = (o.extra.get("columns") or {}).get(col["name"])
            if meta:
                col.update(meta)
        shape = {"kind": o.kind, "columns": [[n, t, nl] for n, t, nl in o.columns], "pk": o.primary_key,
                 "fks": o.foreign_keys}
        checksum = hashlib.sha256(json.dumps(shape, sort_keys=True).encode()).hexdigest()
        result.append(CatalogObject(qualified_name=f"{o.schema}.{o.name}", kind=o.kind, columns=columns,
                                    primary_key=o.primary_key, foreign_keys=o.foreign_keys,
                                    row_estimate=o.row_estimate, checksum=checksum))
    overall = hashlib.sha256("|".join(f"{o.qualified_name}:{o.checksum}" for o in result).encode()).hexdigest()
    return Catalog(result, overall, complete=True, schemas=per_schema)


class _Pool:
    def __init__(self, concurrency: int) -> None:
        self.idle: list[Any] = []
        self.slots = asyncio.Semaphore(concurrency)
        self.concurrency = concurrency
        self.last_used = time.monotonic()

    @property
    def busy(self) -> bool:
        return self.slots._value != self.concurrency


class DbApiAdapter:
    engine = ""
    dialect = ""
    param_style: Literal["format", "qmark"] = "format"
    # Engines without a session read-only switch refuse anything but one SELECT themselves (defence in depth;
    # the runner's guard has already checked the signed statement).
    statement_backstop = False
    read_only_checks: tuple[str, ...] = ()
    empty_literal: str | None = None  # write empty-string values as this constant (see ``bind_positional``)

    def __init__(self, *, concurrency: int = 4, read_only_ttl_s: int = 60, allow_insecure_tls: bool = False,
                 max_pools: int = 32, **_: Any) -> None:
        self.concurrency = concurrency
        self.read_only_ttl_s = read_only_ttl_s
        self.allow_insecure_tls = allow_insecure_tls
        self.max_pools = max_pools
        self._pools: dict[str, _Pool] = {}
        self._verdicts: dict[str, tuple[float, ReadOnlyVerdict]] = {}
        self._columns: dict[str, tuple[float, dict[str, list[tuple[str, str]]]]] = {}
        self._running: dict[str, tuple[ConnectionInfo, Any, Any]] = {}  # execution -> (info, conn, cursor)
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------ engine hooks (blocking)

    def _connect(self, info: ConnectionInfo) -> Any:
        raise NotImplementedError

    def _begin(self, cur: Any, limits: Limits) -> bool:
        raise NotImplementedError

    def _end(self, conn: Any) -> None:
        conn.rollback()

    def _link_failed(self, exc: Exception) -> bool:
        """The session dropped before the statement ran: retried once on a new connection (a read is safe to
        repeat). Engines opt in."""
        return False

    def _server_version(self, cur: Any) -> str | None:
        return None

    def _privileges(self, conn: Any, info: ConnectionInfo) -> list[str]:
        raise NotImplementedError

    def _catalog(self, conn: Any, info: ConnectionInfo, names: list[str] | None) -> list[RawObject]:
        raise NotImplementedError

    def _classify(self, exc: Exception) -> TelaError:
        raise NotImplementedError

    def _column_kind(self, description: tuple[Any, ...]) -> str:
        return "text"

    def _cancel_running(self, info: ConnectionInfo, conn: Any, cur: Any) -> None:
        cur.cancel()

    def _auth_failed(self, exc: Exception) -> bool:
        return False

    def _key_material(self, info: ConnectionInfo) -> str:
        return json.dumps([info.descriptor, sorted(info.credentials.items())], sort_keys=True, default=str)

    def capabilities(self) -> dict[str, Any]:
        return {"dialect": self.dialect, "custom_sql": True, "ctes": True, "window_functions": True,
                "session_read_only": True}

    # ------------------------------------------------------------------ pools

    def _key(self, info: ConnectionInfo) -> str:
        return hashlib.sha256(f"{info.connection_id}|{self._key_material(info)}".encode()).hexdigest()

    async def _pool(self, info: ConnectionInfo) -> _Pool:
        key = self._key(info)
        async with self._lock:
            pool = self._pools.get(key)
            if pool is None:
                while len(self._pools) >= self.max_pools:
                    idle = [k for k, p in self._pools.items() if not p.busy]
                    if not idle:
                        break
                    victim = min(idle, key=lambda k: self._pools[k].last_used)
                    old = self._pools.pop(victim)
                    await asyncio.to_thread(self._close_all, old.idle)
                pool = self._pools[key] = _Pool(self.concurrency)
            pool.last_used = time.monotonic()
        return pool

    @staticmethod
    def _close_all(conns: list[Any]) -> None:
        for c in conns:
            try:
                c.close()
            except Exception:  # noqa: BLE001, S110 - closing a dead session must not fail the request
                pass

    def _open(self, info: ConnectionInfo) -> Any:
        try:
            return self._connect(info)
        except TelaError:
            raise
        except Exception as exc:
            raise TelaError(ErrorCode.SOURCE_UNREACHABLE, "The data source is not reachable right now.",
                            {"auth_failed": self._auth_failed(exc)}) from exc

    # ------------------------------------------------------------------ RULE-001

    async def verify_read_only(self, info: ConnectionInfo, *, force: bool = False) -> ReadOnlyVerdict:
        key = self._key(info)
        hit = self._verdicts.get(key)
        if hit and not force and time.monotonic() - hit[0] < self.read_only_ttl_s:
            return hit[1]

        def check() -> list[str]:
            conn = self._open(info)
            try:
                reasons = self._privileges(conn, info)
                cur = conn.cursor()
                if not self._begin(cur, Limits()):
                    reasons.append("session read-only flag could not be applied")
                self._end(conn)
                return reasons
            finally:
                self._close_all([conn])

        try:
            reasons = await asyncio.to_thread(check)
        except TelaError:
            raise
        except Exception as exc:  # noqa: BLE001 - an unreadable privilege answer is unsafe (fail closed)
            log.warning("runner.read_only_check_failed", engine=self.engine, error=type(exc).__name__)
            reasons = ["privileges could not be verified"]
        verdict = ReadOnlyVerdict("unsafe" if reasons else "safe", list(self.read_only_checks), reasons)
        self._verdicts[key] = (time.monotonic(), verdict)
        return verdict

    async def test_connection(self, info: ConnectionInfo) -> TestResult:
        started = time.monotonic()

        def probe() -> str | None:
            conn = self._open(info)
            try:
                return self._server_version(conn.cursor())
            finally:
                self._close_all([conn])

        try:
            version = await asyncio.to_thread(probe)
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

    # ------------------------------------------------------------------ introspection

    async def introspect(self, info: ConnectionInfo, *, max_objects: int = 2000,
                         names: list[str] | None = None) -> Catalog:
        def read() -> list[RawObject]:
            conn = self._open(info)
            try:
                return self._catalog(conn, info, names)
            finally:
                self._close_all([conn])

        try:
            raw = await asyncio.to_thread(read)
        except TelaError:
            raise
        except Exception as exc:
            raise self._classify(exc) from exc
        per_schema: dict[str, int] = {}
        for o in raw:
            per_schema[o.schema] = per_schema.get(o.schema, 0) + 1
        if names is None and len(raw) > max_objects:
            return Catalog([], "", complete=False, schemas=per_schema)
        return build_catalog(raw, per_schema)

    def forget_columns(self, info: ConnectionInfo) -> None:
        prefix = self._key(info) + "|"
        for key in [k for k in self._columns if k.startswith(prefix)]:
            self._columns.pop(key, None)

    async def object_columns(self, info: ConnectionInfo, names: list[str]) -> dict[str, list[tuple[str, str]]]:
        key = self._key(info) + "|" + ",".join(sorted(names))
        hit = self._columns.get(key)
        if hit and time.monotonic() - hit[0] < self.read_only_ttl_s:
            return hit[1]
        catalog = await self.introspect(info, names=sorted(names))
        cols = {o.qualified_name.lower(): [(c["name"], c["type"]) for c in o.columns] for o in catalog.objects}
        self._columns[key] = (time.monotonic(), cols)
        return cols

    # ------------------------------------------------------------------ execution

    def _assert_select(self, sql: str) -> None:
        try:
            trees = [t for t in sqlglot.parse(sql, read=self.dialect) if t is not None]
        except Exception:  # noqa: BLE001 - unparseable is refused
            trees = []
        if (len(trees) != 1 or not isinstance(trees[0], exp.Select | exp.Union | exp.Intersect | exp.Except)
                or any(isinstance(n, FORBIDDEN_NODES) for n in trees[0].walk())):
            raise TelaError(ErrorCode.READ_ONLY_VIOLATION, "Write attempt refused by the connector.")

    async def execute(self, info: ConnectionInfo, sql: str, params: dict[str, Any], limits: Limits,
                      execution_id: str) -> ResultSet:
        if self.statement_backstop:
            self._assert_select(sql)
        pool = await self._pool(info)
        bound, values = bind_positional(sql, self.dialect, params, self.param_style, empty_literal=self.empty_literal)
        started = time.monotonic()
        async with pool.slots:
            conn = pool.idle.pop() if pool.idle else None

            def run(c: Any) -> tuple[list[dict[str, str]], list[list[Any]], Any]:
                cur = c.cursor()
                self._running[execution_id] = (info, c, cur)
                try:
                    if not self._begin(cur, limits):
                        raise TelaError(ErrorCode.READ_ONLY_VIOLATION,
                                        "The data source account is no longer verifiably read-only.")
                    cur.execute(bound, values)  # compiled, signed and guarded SQL; values bound
                    if time.monotonic() - started > limits.timeout_ms / 1000 + 0.5:
                        # Some engines end an interrupted call quietly (MySQL SLEEP returns 1): past the
                        # deadline the result is discarded, whatever the engine says.
                        raise TelaError(ErrorCode.QUERY_TIMEOUT,
                                        "The query took too long and was stopped. Try narrowing it.")
                    columns = [{"name": d[0], "type": self._column_kind(d)} for d in (cur.description or [])]
                    rows = collect_rows(cur.fetchmany, limits)
                    self._end(c)
                    return columns, rows, c
                except BaseException:
                    self._close_all([c])  # a failed session is never reused
                    raise

            def run_once_more() -> tuple[list[dict[str, str]], list[list[Any]], Any]:
                try:
                    return run(conn or self._open(info))
                except Exception as exc:
                    if not self._link_failed(exc) or time.monotonic() - started > limits.timeout_ms / 1000:
                        raise
                    log.info("runner.link_retry", engine=self.engine)
                    return run(self._open(info))

            try:
                columns, rows, used = await asyncio.to_thread(run_once_more)
            except TelaError as exc:
                if exc.code == ErrorCode.SCHEMA_CHANGED:
                    self.forget_columns(info)
                raise
            except Exception as exc:
                err = self._classify(exc)
                if err.code == ErrorCode.SCHEMA_CHANGED:
                    self.forget_columns(info)
                raise err from exc
            finally:
                self._running.pop(execution_id, None)
            pool.idle.append(used)
        truncated = len(rows) > limits.max_rows
        return ResultSet(columns, rows[: limits.max_rows], truncated, int((time.monotonic() - started) * 1000))

    async def cancel(self, execution_id: str) -> bool:
        entry = self._running.get(execution_id)
        if entry is None:
            return False
        try:
            await asyncio.to_thread(self._cancel_running, *entry)
        except Exception as exc:  # noqa: BLE001 - best effort; the statement timeout still applies
            log.warning("runner.cancel_failed", engine=self.engine, error=type(exc).__name__)
            return False
        return True

    async def close(self) -> None:
        for pool in self._pools.values():
            await asyncio.to_thread(self._close_all, pool.idle)
        self._pools.clear()
