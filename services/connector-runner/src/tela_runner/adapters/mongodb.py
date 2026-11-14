"""MongoDB adapter (US-112; API-065; ADR-014; RULE-001, RULE-006).

* **Read-only proof**: ``connectionStatus`` with ``showPrivileges`` must list only read actions (the built-in
  ``read`` role: ``find``, ``listCollections``, ``listIndexes``, ``collStats``, ``dbStats`` and the read-only
  ``changeStream``, ``dbHash``, ``killCursors``, ``listSearchIndexes``, ``planCacheRead``); any other
  action — insert, update, remove, createIndex, dropCollection, anything on the cluster — makes the account
  unsafe.
* **Credentials** come from the secrets store and are passed to the driver separately; the descriptor's URI
  never carries them (the API refuses one that does). TLS is on unless disabled in development.
* **Execution** is ``aggregate`` only, after the runner's pipeline guard (no ``$out``/``$merge``), with
  ``maxTimeMS`` and no disk use; at most ``max_rows + 1`` documents are read. MongoDB has no read-only
  session switch: the privilege proof, the guard and the adapter having no write call are the layers.
* **Introspection** samples up to 200 documents per collection in memory (never stored or returned):
  dotted field paths, BSON types, presence ratio, mixed types flagged; arrays of documents become child
  entities ``db.collection#array`` with ``_parent_id`` and ``_index`` keys.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from datetime import datetime
from decimal import Decimal
from typing import Any

from tela_common.errors import ErrorCode, TelaError
from tela_common.logging import get_logger
from tela_runner.adapters.base import (
    Catalog,
    ConnectionInfo,
    Limits,
    ReadOnlyVerdict,
    ResultSet,
    TestResult,
)
from tela_runner.adapters.dbapi import RawObject, build_catalog

log = get_logger(__name__)

# Exactly the actions of MongoDB 7's built-in ``read`` role (rolesInfo with showPrivileges).
READ_ACTIONS = frozenset({"find", "listCollections", "listIndexes", "collStats", "dbStats", "changeStream",
                          "dbHash", "killCursors", "listSearchIndexes", "planCacheRead"})
SAMPLE = 200
CHILD = "#"


def bson_type(v: Any) -> str:
    from bson import Decimal128, ObjectId

    if v is None:
        return "null"
    if isinstance(v, bool):
        return "bool"
    if isinstance(v, int):
        return "int" if -(2**31) <= v < 2**31 else "long"
    if isinstance(v, float):
        return "double"
    if isinstance(v, Decimal128 | Decimal):
        return "decimal"
    if isinstance(v, datetime):
        return "date"
    if isinstance(v, ObjectId):
        return "objectId"
    if isinstance(v, dict):
        return "object"
    if isinstance(v, list):
        return "array"
    return "string"


def flatten(doc: dict[str, Any], prefix: str = "") -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in doc.items():
        path = f"{prefix}{k}"
        if isinstance(v, dict) and v:
            out.update(flatten(v, path + "."))
        else:
            out[path] = v
    return out


def profile(docs: list[dict[str, Any]]) -> list[tuple[str, str, bool, dict[str, Any]]]:
    """(path, type, nullable, metadata) per field path seen in the sample, in first-seen order."""
    seen: dict[str, dict[str, Any]] = {}
    for d in docs:
        for path, v in flatten(d).items():
            s = seen.setdefault(path, {"types": set(), "count": 0, "nulls": 0})
            s["count"] += 1
            if v is None:
                s["nulls"] += 1
            else:
                s["types"].add(bson_type(v))
    n = max(len(docs), 1)
    out = []
    for path, s in seen.items():
        types = s["types"] - {"null"}
        numeric = {"int", "long", "double", "decimal"}
        if len(types) > 1 and types <= numeric:
            kind = "decimal" if types & {"double", "decimal"} else "long"
            flags: list[str] = []
        elif len(types) > 1:
            kind, flags = "mixed", ["mixed_types"]
        else:
            kind, flags = (next(iter(types)) if types else "null"), []
        if kind == "array":
            flags.append("array")
        presence = round(s["count"] / n, 3)
        out.append((path, kind, presence < 1 or s["nulls"] > 0, {"presence": presence, "flags": flags}))
    return out


def jsonable(v: Any) -> Any:
    from bson import Decimal128, ObjectId

    if isinstance(v, Decimal128):
        d = v.to_decimal()
        return int(d) if d == d.to_integral_value() and abs(d) < 2**53 else float(d)
    if isinstance(v, Decimal):
        return int(v) if v == v.to_integral_value() and abs(v) < 2**53 else float(v)
    if isinstance(v, datetime):
        v = v.replace(tzinfo=None) if v.tzinfo else v
        return v.date().isoformat() if (v.hour, v.minute, v.second, v.microsecond) == (0, 0, 0, 0) else v.isoformat()
    if isinstance(v, ObjectId):
        return str(v)
    if isinstance(v, bytes):
        return "[BINARY]"
    if isinstance(v, dict | list):
        return json.loads(json.dumps(v, default=lambda x: jsonable(x) if not isinstance(x, dict | list) else str(x)))
    return v


def _kind(v: Any) -> str:
    t = bson_type(v)
    return {"int": "integer", "long": "integer", "double": "decimal", "decimal": "decimal", "date": "datetime",
            "bool": "boolean"}.get(t, "text")


class MongoAdapter:
    engine = "mongodb"

    def __init__(self, *, concurrency: int = 4, read_only_ttl_s: int = 60, allow_insecure_tls: bool = False,
                 max_pools: int = 32, **_: Any) -> None:
        self.concurrency = concurrency
        self.read_only_ttl_s = read_only_ttl_s
        self.allow_insecure_tls = allow_insecure_tls
        self.max_pools = max_pools
        self._clients: dict[str, tuple[float, Any]] = {}
        self._verdicts: dict[str, tuple[float, ReadOnlyVerdict]] = {}
        self._running: dict[str, asyncio.Task[Any]] = {}

    # ------------------------------------------------------------------ connections

    def _key(self, info: ConnectionInfo) -> str:
        material = json.dumps([info.descriptor, sorted(info.credentials.items())], sort_keys=True, default=str)
        return hashlib.sha256(f"{info.connection_id}|{material}".encode()).hexdigest()

    def _options(self, info: ConnectionInfo) -> dict[str, Any]:
        d, c = info.descriptor, info.credentials
        uri = str(d.get("uri") or "")
        if "@" in uri.split("//", 1)[-1].split("/", 1)[0]:
            raise TelaError(ErrorCode.VALIDATION_ERROR, "The connection string must not contain credentials.")
        mode = (d.get("tls") or {}).get("mode", "verify-full")
        opts: dict[str, Any] = {"host": uri, "username": c.get("username") or d.get("username"),
                                "password": c.get("password"), "readPreference": d.get("read_preference")
                                or "secondaryPreferred", "appname": "tela-connector-runner",
                                "serverSelectionTimeoutMS": 10_000, "connectTimeoutMS": 10_000,
                                "maxPoolSize": self.concurrency, "tz_aware": True, "retryWrites": False}
        if mode == "disable":
            if not self.allow_insecure_tls:
                raise TelaError(ErrorCode.VALIDATION_ERROR, "TLS is required for this connection.")
            opts["tls"] = False
        else:
            opts["tls"] = True
            if mode == "require" and self.allow_insecure_tls:
                opts["tlsAllowInvalidCertificates"] = True
        via = d.get("via")
        if via:  # SSH tunnel (ENH-004): one host, reached at the tunnel's local port
            from urllib.parse import parse_qsl, urlencode, urlsplit

            parts = urlsplit(uri)
            query = [(k, v) for k, v in parse_qsl(parts.query) if k.lower() not in ("replicaset", "directconnection")]
            opts["host"] = f"mongodb://{via['host']}:{int(via['port'])}{parts.path or '/'}" + (
                f"?{urlencode(query)}" if query else "")
            opts["directConnection"] = True
            if opts["tls"]:
                # pymongo cannot check the certificate against the real host through 127.0.0.1: the chain is
                # still verified, only the name match is skipped (the SSH leg itself is pinned).
                opts["tlsAllowInvalidHostnames"] = True
        return opts

    async def _client(self, info: ConnectionInfo) -> Any:
        from pymongo import AsyncMongoClient

        key = self._key(info)
        hit = self._clients.get(key)
        if hit:
            self._clients[key] = (time.monotonic(), hit[1])
            return hit[1]
        while len(self._clients) >= self.max_pools:
            victim = min(self._clients, key=lambda k: self._clients[k][0])
            await self._clients.pop(victim)[1].close()
        client = AsyncMongoClient(**self._options(info))
        self._clients[key] = (time.monotonic(), client)
        return client

    def _db(self, client: Any, info: ConnectionInfo) -> Any:
        return client[str(info.descriptor.get("database"))]

    @staticmethod
    def _unreachable(exc: Exception) -> TelaError:
        from pymongo.errors import OperationFailure

        auth = isinstance(exc, OperationFailure) and exc.code in (18, 8000)
        return TelaError(ErrorCode.SOURCE_UNREACHABLE, "The data source is not reachable right now.",
                         {"auth_failed": auth})

    # ------------------------------------------------------------------ RULE-001

    async def verify_read_only(self, info: ConnectionInfo, *, force: bool = False) -> ReadOnlyVerdict:
        key = self._key(info)
        hit = self._verdicts.get(key)
        if hit and not force and time.monotonic() - hit[0] < self.read_only_ttl_s:
            return hit[1]
        try:
            status = await self._db(await self._client(info), info).command(
                {"connectionStatus": 1, "showPrivileges": True})
        except TelaError:
            raise
        except Exception as exc:
            raise self._unreachable(exc) from exc
        auth = status.get("authInfo") or {}
        reasons: list[str] = []
        if not auth.get("authenticatedUsers"):
            reasons.append("account is not signed in")
        for priv in auth.get("authenticatedUserPrivileges") or []:
            resource = priv.get("resource") or {}
            where = "the cluster" if resource.get("cluster") else (
                f"{resource.get('db') or '*'}.{resource.get('collection') or '*'}")
            for action in priv.get("actions") or []:
                if action not in READ_ACTIONS:
                    reasons.append(f"account has {action} on {where}")
        verdict = ReadOnlyVerdict("unsafe" if reasons else "safe",
                                  ["only read actions (connectionStatus with privileges)",
                                   "aggregate only, $out and $merge refused", "credentials outside the URI"],
                                  list(dict.fromkeys(reasons))[:30])
        self._verdicts[key] = (time.monotonic(), verdict)
        return verdict

    async def test_connection(self, info: ConnectionInfo) -> TestResult:
        started = time.monotonic()
        try:
            db = self._db(await self._client(info), info)
            build = await db.command("buildInfo")
            latency = int((time.monotonic() - started) * 1000)
            verdict = await self.verify_read_only(info, force=True)
        except TelaError as exc:
            elapsed = int((time.monotonic() - started) * 1000)
            if exc.details.get("auth_failed"):
                return TestResult(True, False, ReadOnlyVerdict("unsafe", reasons=["could not sign in"]), elapsed,
                                  error="The username or password was rejected.")
            return TestResult(False, False, ReadOnlyVerdict("unsafe", reasons=["not reachable"]), elapsed,
                              error=exc.message)
        except Exception as exc:  # noqa: BLE001
            err = self._unreachable(exc)
            elapsed = int((time.monotonic() - started) * 1000)
            if err.details.get("auth_failed"):
                return TestResult(True, False, ReadOnlyVerdict("unsafe", reasons=["could not sign in"]), elapsed,
                                  error="The username or password was rejected.")
            return TestResult(False, False, ReadOnlyVerdict("unsafe", reasons=["not reachable"]), elapsed,
                              error=err.message)
        return TestResult(True, True, verdict, latency, f"MongoDB {build.get('version')}")

    def capabilities(self) -> dict[str, Any]:
        return {"dialect": "mongodb", "custom_sql": False, "ctes": False, "window_functions": False,
                "session_read_only": False}

    # ------------------------------------------------------------------ introspection (sampling)

    async def introspect(self, info: ConnectionInfo, *, max_objects: int = 2000,
                         names: list[str] | None = None) -> Catalog:
        database = str(info.descriptor.get("database"))
        try:
            db = self._db(await self._client(info), info)
            colls = sorted(n for n in await db.list_collection_names(filter={"type": "collection"})
                           if not n.startswith("system."))
            wanted = {n.split(CHILD, 1)[0].lower() for n in names} if names is not None else None
            if names is None and len(colls) > max_objects:
                return Catalog([], "", complete=False, schemas={database: len(colls)})
            raw: list[RawObject] = []
            for name in colls:
                if wanted is not None and f"{database}.{name}".lower() not in wanted:
                    continue
                docs = await db[name].find({}, sort=[("_id", 1)], limit=SAMPLE).to_list(SAMPLE)
                estimate = await db[name].estimated_document_count()
                raw += self._objects(database, name, docs, estimate)
        except TelaError:
            raise
        except Exception as exc:
            raise self._unreachable(exc) from exc
        if names is not None:
            raw = [o for o in raw if f"{o.schema}.{o.name}".lower() in {n.lower() for n in names}]
        return build_catalog(raw, {database: len({o.name.split(CHILD)[0] for o in raw})})

    def _objects(self, database: str, name: str, docs: list[dict[str, Any]], estimate: int) -> list[RawObject]:
        fields = profile(docs)
        top = RawObject(database, name, "collection", estimate)
        children: list[RawObject] = []
        for path, kind, nullable, meta in fields:
            values = [d for d in docs if isinstance(_get(d, path), list)]
            items = [x for d in values for x in _get(d, path)]
            if kind == "array" and items and all(isinstance(x, dict) for x in items):
                child = RawObject(database, f"{name}{CHILD}{path}", "collection", len(items))
                child.columns = [("_parent_id", "objectId", False), ("_index", "long", False)]
                child.primary_key = ["_parent_id", "_index"]
                child.extra = {"columns": {}}
                for cpath, ckind, cnull, cmeta in profile(items):
                    child.columns.append((cpath, ckind, cnull))
                    child.extra["columns"][cpath] = cmeta
                child.foreign_keys = [{"columns": ["_parent_id"], "ref_object": f"{database}.{name}",
                                       "ref_columns": ["_id"]}]
                children.append(child)
                continue
            top.columns.append((path, kind, nullable))
            top.extra.setdefault("columns", {})[path] = meta
        top.primary_key = ["_id"] if any(c[0] == "_id" for c in top.columns) else []
        return [top, *children]

    async def object_columns(self, info: ConnectionInfo, names: list[str]) -> dict[str, list[tuple[str, str]]]:
        catalog = await self.introspect(info, names=sorted(names))
        return {o.qualified_name.lower(): [(c["name"], c["type"]) for c in o.columns] for o in catalog.objects}

    # ------------------------------------------------------------------ execution

    async def execute(self, info: ConnectionInfo, sql: str, params: dict[str, Any], limits: Limits,
                      execution_id: str) -> ResultSet:
        from bson import json_util
        from pymongo.errors import ExecutionTimeout, OperationFailure, PyMongoError

        doc = json_util.loads(sql)  # already checked by the runner's pipeline guard
        database, _, collection = str(doc["collection"]).partition(".")
        if database.lower() != str(info.descriptor.get("database") or "").lower():
            raise TelaError(ErrorCode.SQL_OBJECT_NOT_ALLOWED, "The data source refused access to an object.")
        columns: list[str] = list(doc["columns"])
        started = time.monotonic()

        async def run() -> list[list[Any]]:
            coll = (await self._client(info))[database][collection]
            cursor = await coll.aggregate(doc["pipeline"], maxTimeMS=int(limits.timeout_ms), allowDiskUse=False,
                                          batchSize=min(limits.max_rows + 1, 1000))
            rows: list[list[Any]] = []
            size = 0
            try:
                async for d in cursor:
                    row = [jsonable(d.get(c)) for c in columns]
                    size += len(json.dumps(row, default=str))
                    if size > limits.max_bytes:
                        raise TelaError(ErrorCode.PAYLOAD_TOO_LARGE,
                                        "The result is too large. Add filters or fewer columns.")
                    rows.append(row)
                    if len(rows) > limits.max_rows:
                        break
            finally:
                await cursor.close()
            return rows

        task = asyncio.ensure_future(run())
        self._running[execution_id] = task
        try:
            rows = await asyncio.wait_for(task, timeout=limits.timeout_ms / 1000 + 2)
        except (ExecutionTimeout, TimeoutError) as exc:
            raise TelaError(ErrorCode.QUERY_TIMEOUT, "The query took too long and was stopped. Try narrowing "
                            "it.") from exc
        except asyncio.CancelledError as exc:
            raise TelaError(ErrorCode.QUERY_TIMEOUT, "The request was cancelled.") from exc
        except OperationFailure as exc:
            if exc.code == 50:
                raise TelaError(ErrorCode.QUERY_TIMEOUT, "The query took too long and was stopped.") from exc
            if exc.code == 13:
                raise TelaError(ErrorCode.SQL_OBJECT_NOT_ALLOWED,
                                "The data source refused access to an object.") from exc
            log.warning("runner.query_failed", engine="mongodb", code=exc.code)
            raise TelaError(ErrorCode.QUERY_FAILED, "The data source could not run this query.",
                            {"code": exc.code}) from exc
        except PyMongoError as exc:
            raise TelaError(ErrorCode.SOURCE_UNREACHABLE, "The data source is not reachable right now.") from exc
        finally:
            self._running.pop(execution_id, None)
        if not rows and doc.get("empty_row") is not None:
            rows = [list(doc["empty_row"])]
        out_cols = [{"name": c, "type": next((_kind(r[i]) for r in rows if r[i] is not None), "text")}
                    for i, c in enumerate(columns)]
        truncated = len(rows) > limits.max_rows
        return ResultSet(out_cols, rows[: limits.max_rows], truncated, int((time.monotonic() - started) * 1000))

    async def cancel(self, execution_id: str) -> bool:
        task = self._running.get(execution_id)
        if task is None:
            return False
        task.cancel()
        return True

    async def close(self) -> None:
        for _, client in self._clients.values():
            await client.close()
        self._clients.clear()


def _get(doc: dict[str, Any], path: str) -> Any:
    cur: Any = doc
    for part in path.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur

