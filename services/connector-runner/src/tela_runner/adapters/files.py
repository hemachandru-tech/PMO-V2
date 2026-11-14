"""Excel and CSV sources through DuckDB (US-108, US-109; ADR-012; RISK-006).

The uploaded file lives in object storage. The runner downloads the pinned version
to tmpfs, checks its SHA-256 against the signed descriptor and converts it once into
a DuckDB database file (one table per included sheet; header row, names and column
types as the Admin confirmed them, inferred until then). Every execution opens that
database **read-only** in a fresh connection with external access disabled,
extension loading off and the configuration locked, and is interrupted at the
timeout. Converted databases are cached per file version and settings and evicted
least-recently-used.

Profiling (introspection) reports per sheet: the header row used, merged cells, and
per column the inferred type with flags for mixed types and formula-only cells
(formulas saved without their computed values). No sample values are returned.
"""

from __future__ import annotations

import asyncio
import csv
import hashlib
import io
import json
import os
import re
import threading
import time
import zipfile
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Protocol

import duckdb

from tela_common.errors import ErrorCode, TelaError
from tela_common.logging import get_logger
from tela_runner.adapters.base import (
    SCHEMA_CHANGED_MESSAGE,
    Catalog,
    ConnectionInfo,
    Limits,
    ReadOnlyVerdict,
    ResultSet,
    TestResult,
)
from tela_runner.adapters.dbapi import RawObject, build_catalog, collect_rows

log = get_logger(__name__)

SCHEMA = "main"
TYPES = ("integer", "decimal", "date", "datetime", "boolean", "text")
DUCK_TYPES = {"integer": "BIGINT", "decimal": "DOUBLE", "date": "DATE", "datetime": "TIMESTAMP",
              "boolean": "BOOLEAN", "text": "VARCHAR"}
_KIND = {"BIGINT": "integer", "INTEGER": "integer", "HUGEINT": "integer", "SMALLINT": "integer",
         "DOUBLE": "decimal", "DECIMAL": "decimal", "FLOAT": "decimal", "DATE": "date", "TIMESTAMP": "datetime",
         "BOOLEAN": "boolean"}
SAFE_CONFIG = {"enable_external_access": "false", "autoload_known_extensions": "false",
               "autoinstall_known_extensions": "false", "allow_unsigned_extensions": "false",
               "lock_configuration": "true", "threads": "2", "memory_limit": "512MB"}


class FileStore(Protocol):
    async def get(self, key: str) -> bytes: ...


def identifier(text: str, taken: set[str]) -> str:
    """A lower-case SQL identifier for a sheet or header (``Planned go-live`` → ``planned_go_live``)."""
    base = re.sub(r"[^a-z0-9]+", "_", str(text).strip().lower()).strip("_")[:60] or "column"
    if not re.match(r"^[a-z_]", base):
        base = f"c_{base}"
    name, n = base, 2
    while name in taken:
        name, n = f"{base}_{n}", n + 1
    taken.add(name)
    return name


def _kind_of(v: Any) -> str | None:
    if v is None or (isinstance(v, str) and not v.strip()):
        return None
    if isinstance(v, bool):
        return "boolean"
    if isinstance(v, int):
        return "integer"
    if isinstance(v, float | Decimal):
        return "integer" if float(v).is_integer() and abs(float(v)) < 2**53 else "decimal"
    if isinstance(v, datetime):
        return "date" if (v.hour, v.minute, v.second, v.microsecond) == (0, 0, 0, 0) else "datetime"
    if isinstance(v, date):
        return "date"
    return "text"


_INT = re.compile(r"^[+-]?\d{1,18}$")
_DEC = re.compile(r"^[+-]?(\d+\.\d*|\.\d+|\d+)([eE][+-]?\d+)?$")


def _text_kind(v: Any) -> str | None:
    """CSV cells are text: read numbers, ISO dates and true/false out of them."""
    if v is None or not str(v).strip():
        return None
    s = str(v).strip()
    if _INT.match(s):
        return "integer"
    if _DEC.match(s):
        return "decimal"
    if s.lower() in ("true", "false"):
        return "boolean"
    if re.match(r"^\d{4}-\d{2}-\d{2}", s):
        try:
            parsed = datetime.fromisoformat(s)
        except ValueError:
            return "text"
        return "date" if len(s) == 10 or (parsed.hour, parsed.minute, parsed.second) == (0, 0, 0) else "datetime"
    return "text"


def infer(kinds: set[str]) -> tuple[str, bool]:
    """(type, mixed?) from the kinds seen in a column."""
    if not kinds:
        return "text", False
    if kinds <= {"integer"}:
        return "integer", False
    if kinds <= {"integer", "decimal"}:
        return "decimal", False
    if kinds <= {"date"}:
        return "date", False
    if kinds <= {"date", "datetime"}:
        return "datetime", False
    if kinds == {"boolean"}:
        return "boolean", False
    return "text", len(kinds) > 1


def convert(v: Any, kind: str) -> Any:
    """Cast one cell to the confirmed type; a value that does not fit becomes NULL (counted by the caller)."""
    if v is None or (isinstance(v, str) and not v.strip()):
        return None
    try:
        if kind == "text":
            return v.isoformat() if isinstance(v, date | datetime) else str(v)
        if kind == "integer":
            f = float(v)
            if not f.is_integer():
                raise ValueError
            return int(f)
        if kind == "decimal":
            return float(v)
        if kind == "boolean":
            if isinstance(v, bool):
                return v
            s = str(v).strip().lower()
            if s in ("true", "yes", "y", "1"):
                return True
            if s in ("false", "no", "n", "0"):
                return False
            raise ValueError
        if kind in ("date", "datetime"):
            if isinstance(v, datetime):
                return v.date() if kind == "date" else v
            if isinstance(v, date):
                return v
            parsed = datetime.fromisoformat(str(v).strip())
            return parsed.date() if kind == "date" else parsed
    except (ValueError, TypeError, OverflowError):
        raise ValueError(kind) from None
    return v


# ----------------------------------------------------------------------------- reading the file


def _merged_cells(raw: bytes) -> dict[str, int]:
    """Merged ranges per worksheet part, read from the XML (openpyxl's read-only mode does not expose them)."""
    out: dict[str, int] = {}
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        for name in z.namelist():
            if name.startswith("xl/worksheets/") and name.endswith(".xml"):
                with z.open(name) as fh:
                    count = 0
                    tail = b""
                    while chunk := fh.read(1 << 20):
                        count += (tail + chunk).count(b"<mergeCell ")
                        tail = chunk[-11:]
                    out[name] = count
    return out


def read_sheets(raw: bytes, fmt: str, filename: str) -> list[dict[str, Any]]:
    """[{sheet, rows, formulas, merged}] — all cell values of every worksheet (or the CSV)."""
    if fmt == "csv":
        text = raw.decode("utf-8-sig")
        sample = text[:65536]
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
        except csv.Error:
            dialect = csv.excel
        rows = [list(r) for r in csv.reader(io.StringIO(text), dialect)]
        stem = filename.rsplit(".", 1)[0] or "data"
        return [{"sheet": stem, "rows": rows, "formulas": None, "merged": 0, "csv": True}]
    import openpyxl

    merged = _merged_cells(raw)
    values = openpyxl.load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
    formulas = openpyxl.load_workbook(io.BytesIO(raw), read_only=True, data_only=False)
    out = []
    try:
        for ws in values.worksheets:
            fws = formulas[ws.title]
            part = str(getattr(ws, "_worksheet_path", "") or "").lstrip("/")
            out.append({
                "sheet": ws.title,
                "rows": [list(r) for r in ws.iter_rows(values_only=True)],
                "formulas": [[isinstance(c, str) and c.startswith("=") for c in r]
                             for r in fws.iter_rows(values_only=True)],
                "merged": merged.get(part, 0),
            })
    finally:
        values.close()
        formulas.close()
    return out


def _header_row(rows: list[list[Any]]) -> int:
    """1-based: the first row with at least half its cells filled with text (titles above tables are skipped)."""
    for i, row in enumerate(rows[:50]):
        filled = [c for c in row if c not in (None, "")]
        if filled and sum(isinstance(c, str) for c in filled) >= max(1, len(filled) // 2) and len(filled) >= 1:
            width = max(len([c for c in r if c not in (None, "")]) for r in rows[i:i + 5])
            if len(filled) >= max(1, width // 2):
                return i + 1
    return 1


def profile(sheets: list[dict[str, Any]], settings: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Per sheet: table name, header row, columns {name, header, type, inferred, flags}. Settings (the Admin's
    confirmed sheets, header rows and types) win over inference; a sheet not in the settings is included."""
    by_sheet = {s["sheet"]: s for s in settings or []}
    tables: set[str] = set()
    out = []
    for sh in sheets:
        conf = by_sheet.get(sh["sheet"], {})
        rows: list[list[Any]] = sh["rows"]
        if not any(any(c not in (None, "") for c in r) for r in rows):
            continue
        header = int(conf.get("header_row") or _header_row(rows))
        head = rows[header - 1] if header - 1 < len(rows) else []
        data = rows[header:]
        while data and all(c in (None, "") for c in data[-1]):
            data.pop()
        width = max([len(head), *(len(r) for r in data)] or [0])
        conf_cols = {c.get("header"): c for c in conf.get("columns") or []}
        taken: set[str] = set()
        columns = []
        for j in range(width):
            label = head[j] if j < len(head) and head[j] not in (None, "") else f"Column {j + 1}"
            cells = [r[j] if j < len(r) else None for r in data]
            kind, mixed = infer({k for k in map(_text_kind if sh.get("csv") else _kind_of, cells) if k})
            flags = []
            if mixed:
                flags.append("mixed_types")
            fm = sh.get("formulas")
            if fm is not None:
                formula_cells = sum(1 for r in fm[header:header + len(data)] if j < len(r) and r[j])
                if formula_cells and all(c is None for c in cells):
                    flags.append("formula_only")
            if all(c in (None, "") for c in cells):
                flags.append("empty")
            c = conf_cols.get(str(label), {})
            chosen = c.get("type") if c.get("type") in TYPES else kind
            columns.append({"name": c.get("name") or identifier(str(label), taken), "header": str(label),
                            "type": chosen, "inferred": kind, "flags": flags, "index": j,
                            "personal": bool(c.get("personal"))})
            taken.add(str(columns[-1]["name"]))
        out.append({"sheet": sh["sheet"], "table": conf.get("table") or identifier(sh["sheet"], tables),
                    "include": conf.get("include", True), "header_row": header, "rows": len(data),
                    "merged_cells": sh.get("merged", 0), "columns": columns, "_data": data})
        tables.add(str(out[-1]["table"]))
    return out


def build_database(path: Path, tables: list[dict[str, Any]]) -> dict[str, int]:
    """Write the included sheets to a new DuckDB file; returns conversion failures per table."""
    tmp = path.with_suffix(".building")
    tmp.unlink(missing_ok=True)
    failures: dict[str, int] = {}
    con = duckdb.connect(str(tmp), config={"enable_external_access": "false",
                                           "autoload_known_extensions": "false",
                                           "autoinstall_known_extensions": "false"})
    try:
        for t in tables:
            if not t["include"]:
                continue
            cols = t["columns"]
            ddl = ", ".join(f'"{c["name"]}" {DUCK_TYPES[c["type"]]}' for c in cols)
            con.execute(f'CREATE TABLE "{t["table"]}" ({ddl})')
            bad = 0
            batch: list[tuple[Any, ...]] = []
            marks = ", ".join("?" for _ in cols)
            for r in t["_data"]:
                out = []
                for c in cols:
                    v = r[c["index"]] if c["index"] < len(r) else None
                    try:
                        out.append(convert(v, c["type"]))
                    except ValueError:
                        bad += 1
                        out.append(None)
                batch.append(tuple(out))
                if len(batch) >= 5000:
                    con.executemany(f'INSERT INTO "{t["table"]}" VALUES ({marks})', batch)  # noqa: S608
                    batch = []
            if batch:
                con.executemany(f'INSERT INTO "{t["table"]}" VALUES ({marks})', batch)  # noqa: S608
            failures[t["table"]] = bad
        con.execute("CHECKPOINT")
    finally:
        con.close()
    os.replace(tmp, path)
    return failures


# ----------------------------------------------------------------------------- the adapter


class FileAdapter:
    """Registered for both ``excel`` and ``csv``."""

    engine = "excel"

    def __init__(self, *, store: FileStore | None = None, cache_dir: str | None = None, cache_files: int = 16,
                 **_: Any) -> None:
        self.store = store
        self.cache_dir = Path(cache_dir or os.environ.get("TELA_RUNNER_FILE_DIR", "/tmp/tela-files"))  # noqa: S108
        self.cache_files = cache_files
        self._locks: dict[str, asyncio.Lock] = {}
        self._profiles: dict[str, dict[str, Any]] = {}
        self._running: dict[str, duckdb.DuckDBPyConnection] = {}

    # ------------------------------------------------------------------ file → database

    @staticmethod
    def _file(info: ConnectionInfo) -> dict[str, Any]:
        f = info.descriptor.get("file") or {}
        if not f.get("object_key") or not f.get("sha256"):
            raise TelaError(ErrorCode.SOURCE_UNREACHABLE, "The uploaded file for this source is missing.")
        return f

    def _cache_key(self, f: dict[str, Any]) -> str:
        settings = json.dumps(f.get("sheets") or [], sort_keys=True)
        return hashlib.sha256(f"{f['sha256']}|{settings}".encode()).hexdigest()[:32]

    async def _database(self, info: ConnectionInfo) -> tuple[Path, dict[str, Any]]:
        f = self._file(info)
        key = self._cache_key(f)
        path = self.cache_dir / f"{key}.duckdb"
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            if path.exists() and key in self._profiles:
                os.utime(path)
                return path, self._profiles[key]
            if self.store is None:
                raise TelaError(ErrorCode.STORAGE_UNAVAILABLE, "File storage is unavailable.")
            raw = await self.store.get(str(f["object_key"]))
            if hashlib.sha256(raw).hexdigest() != f["sha256"]:
                raise TelaError(ErrorCode.SIGNATURE_INVALID, "The stored file does not match its checksum.")
            self.cache_dir.mkdir(parents=True, exist_ok=True, mode=0o700)

            def convert_file() -> dict[str, Any]:
                sheets = read_sheets(raw, str(f.get("format") or "xlsx"), str(f.get("filename") or "data.csv"))
                tables = profile(sheets, f.get("sheets"))
                failures = build_database(path, tables)
                for t in tables:
                    t.pop("_data", None)
                    t["conversion_failures"] = failures.get(t["table"], 0)
                return {"tables": tables}

            try:
                prof = await asyncio.to_thread(convert_file)
            except TelaError:
                raise
            except Exception as exc:
                log.warning("runner.file_unreadable", error=type(exc).__name__)
                raise TelaError(ErrorCode.QUERY_FAILED, "The file could not be read. Check it opens, then upload "
                                "it again.") from exc
            self._profiles[key] = prof
            self._evict()
        return path, prof

    def _evict(self) -> None:
        files = sorted(self.cache_dir.glob("*.duckdb"), key=lambda p: p.stat().st_mtime, reverse=True)
        for old in files[self.cache_files:]:
            old.unlink(missing_ok=True)
            self._profiles.pop(old.stem, None)

    # ------------------------------------------------------------------ contract

    def capabilities(self) -> dict[str, Any]:
        return {"dialect": "duckdb", "custom_sql": True, "ctes": True, "window_functions": True,
                "session_read_only": True}

    async def verify_read_only(self, info: ConnectionInfo, *, force: bool = False) -> ReadOnlyVerdict:
        self._file(info)
        return ReadOnlyVerdict("safe", ["file source: no account to write with", "opened read-only in DuckDB",
                                        "external access and configuration locked"])

    async def test_connection(self, info: ConnectionInfo) -> TestResult:
        started = time.monotonic()
        try:
            _, prof = await self._database(info)
        except TelaError as exc:
            return TestResult(False, False, ReadOnlyVerdict("unsafe", reasons=["file not readable"]),
                              int((time.monotonic() - started) * 1000), error=exc.message)
        verdict = await self.verify_read_only(info)
        sheets = sum(1 for t in prof["tables"] if t["include"])
        return TestResult(True, True, verdict, int((time.monotonic() - started) * 1000),
                          f"DuckDB {duckdb.__version__}; {sheets} sheet(s)")

    async def introspect(self, info: ConnectionInfo, *, max_objects: int = 2000,
                         names: list[str] | None = None) -> Catalog:
        _, prof = await self._database(info)
        raw: list[RawObject] = []
        for t in prof["tables"]:
            if not t["include"] or (names is not None and f"{SCHEMA}.{t['table']}" not in names):
                continue
            o = RawObject(SCHEMA, t["table"], "sheet", t["rows"],
                          [(c["name"], DUCK_TYPES[c["type"]].lower(), True) for c in t["columns"]],
                          display_name=t["sheet"])
            o.extra = {"columns": {c["name"]: {"header": c["header"], "inferred": c["inferred"],
                                               "flags": c["flags"], "personal": c["personal"]}
                                   for c in t["columns"]}}
            raw.append(o)
        catalog = build_catalog(raw, {SCHEMA: len(raw)})
        catalog.profile = {"sheets": [{k: v for k, v in t.items() if k != "_data"} for t in prof["tables"]]}
        return catalog

    async def object_columns(self, info: ConnectionInfo, names: list[str]) -> dict[str, list[tuple[str, str]]]:
        catalog = await self.introspect(info, names=sorted(names))
        return {o.qualified_name.lower(): [(c["name"], c["type"]) for c in o.columns] for o in catalog.objects}

    async def execute(self, info: ConnectionInfo, sql: str, params: dict[str, Any], limits: Limits,
                      execution_id: str) -> ResultSet:
        path, _ = await self._database(info)
        started = time.monotonic()

        def run() -> tuple[list[dict[str, str]], list[list[Any]]]:
            con = duckdb.connect(str(path), read_only=True, config=dict(SAFE_CONFIG))
            self._running[execution_id] = con
            timer = threading.Timer(limits.timeout_ms / 1000, con.interrupt)
            timer.start()
            try:
                cur = con.execute(sql, params)  # $name placeholders bind by name
                columns = [{"name": d[0], "type": _KIND.get(str(d[1]).split("(")[0].upper(), "text")}
                           for d in (cur.description or [])]
                rows = collect_rows(cur.fetchmany, limits)
                return columns, rows
            finally:
                timer.cancel()
                self._running.pop(execution_id, None)
                con.close()

        try:
            columns, rows = await asyncio.to_thread(run)
        except TelaError:
            raise
        except duckdb.InterruptException as exc:
            raise TelaError(ErrorCode.QUERY_TIMEOUT, "The query took too long and was stopped. Try narrowing "
                            "it.") from exc
        except (duckdb.CatalogException, duckdb.BinderException) as exc:
            raise TelaError(ErrorCode.SCHEMA_CHANGED, SCHEMA_CHANGED_MESSAGE) from exc
        except (duckdb.InvalidInputException, duckdb.PermissionException) as exc:
            if "read-only" in str(exc).lower() or "read only" in str(exc).lower():
                raise TelaError(ErrorCode.READ_ONLY_VIOLATION, "Write attempt refused by the source.") from exc
            raise TelaError(ErrorCode.QUERY_FAILED, "The data source could not run this query.") from exc
        except duckdb.Error as exc:
            log.warning("runner.query_failed", engine="excel", error=type(exc).__name__)
            raise TelaError(ErrorCode.QUERY_FAILED, "The data source could not run this query.") from exc
        truncated = len(rows) > limits.max_rows
        return ResultSet(columns, rows[: limits.max_rows], truncated, int((time.monotonic() - started) * 1000))

    async def cancel(self, execution_id: str) -> bool:
        con = self._running.get(execution_id)
        if con is None:
            return False
        con.interrupt()
        return True

    async def close(self) -> None:
        for con in list(self._running.values()):
            con.interrupt()
