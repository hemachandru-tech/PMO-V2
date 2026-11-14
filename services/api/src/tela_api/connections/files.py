"""Excel and CSV sources (US-108; FEAT-011; API-011, API-068; DATA-013; ADR-012; DEC-07; RISK-006).

An Admin uploads a spreadsheet as a governed source. The upload is checked before anything is stored:

* ``.xlsx`` or ``.csv`` only, at most 50 MB (MSG-027);
* the content must match the extension: an ``.xlsx`` is a ZIP package with a workbook part; macro-enabled
  packages (``vbaProject.bin`` or a macro content type) and password-protected files (an OLE container
  holding ``EncryptedPackage``) are refused; a CSV must be UTF-8 text without NUL bytes;
* ZIP members may not expand beyond 20 times the upload or 500 MB (zip bombs);
* an optional malware scanner (``TELA_FILE_SCAN_URL``) must answer "clean" when configured;
* the SHA-256 is recorded and the runner verifies it before every conversion.

Files live at ``tenant/{t}/files/{file_id}/v{n}``. The runner profiles each sheet (header row, types, merged
cells, mixed-type and formula-only columns) during the connection test and schema read; the profile is kept
as the version's ``sheet_manifest`` for the confirmation screen. The Admin confirms sheets, header rows,
column types and personal-data columns; retention is chosen at upload (DEC-07: until replaced + 30 days).

A replacement is uploaded as a **pending** version and profiled; the Admin sees the diff (sheets and columns
added, removed or retyped, and the certified definitions that would break, E-13) and then activates or
discards it. Activating re-tests and re-reads the schema, so widgets on removed sheets become Broken. An
approved model pins the file version it was approved on (``source_pin``); queries for it keep reading that
version until a new model version is approved. Expired versions that no approved model pins are purged by
the nightly sweep.
"""

from __future__ import annotations

import hashlib
import io
import json
import zipfile
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from tela_api import outbox
from tela_api.audit import service as audit
from tela_api.connections.router import _etag, _invalidate_cache, _load, _out, _rid, _runner, _runner_payload
from tela_api.content.exports import _store, require_worker
from tela_api.core.db import fetch_all, fetch_one, tenant_tx
from tela_api.identity.deps import require
from tela_api.identity.permissions import Perm
from tela_api.identity.principal import Principal
from tela_common.errors import ErrorCode, TelaError
from tela_common.ids import new_id
from tela_common.logging import get_logger
from tela_common.objectstore import ObjectStore, tenant_key

log = get_logger(__name__)
router = APIRouter(prefix="/api/v1/connections", tags=["connections"])
internal = APIRouter(prefix="/api/v1/internal/files", tags=["internal"], include_in_schema=False)

MSG_FILE = "Only .xlsx or .csv up to 50 MB, without macros or passwords."  # MSG-027
CONTENT_TYPES = {"xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "csv": "text/csv"}
ENGINES = {"xlsx": "excel", "csv": "csv"}
OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
MAX_EXPANDED = 500 * 1024 * 1024
RETENTION_GRACE = timedelta(days=30)  # DEC-07: retained until replaced + 30 days
TYPES = ("integer", "decimal", "date", "datetime", "boolean", "text")


def _fail(reason: str, message: str = MSG_FILE) -> TelaError:
    return TelaError(ErrorCode.VALIDATION_ERROR, message, {"reason": reason})


def check_upload(filename: str, data: bytes, max_bytes: int) -> str:
    """Return the format (``xlsx`` | ``csv``) or raise MSG-027 with a machine-readable reason."""
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext in ("xlsm", "xltm", "xlsb", "xls"):
        raise _fail("macro_or_legacy" if ext != "xls" else "legacy_format")
    if ext not in ENGINES:
        raise _fail("unsupported_type")
    if not data:
        raise _fail("empty")
    if len(data) > max_bytes:
        raise TelaError(ErrorCode.PAYLOAD_TOO_LARGE, MSG_FILE, {"reason": "too_large"})
    if data.startswith(OLE_MAGIC):
        raise _fail("password_protected" if b"E\x00n\x00c\x00r\x00y\x00p\x00t\x00e\x00d" in data[:1 << 20]
                    else "legacy_format")
    if ext == "xlsx":
        if not data.startswith(b"PK\x03\x04"):
            raise _fail("content_mismatch")
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                names = {i.filename for i in z.infolist()}
                expanded = sum(i.file_size for i in z.infolist())
                if expanded > min(MAX_EXPANDED, 20 * len(data) + 10 * 1024 * 1024):
                    raise _fail("too_large_expanded")
                if "[Content_Types].xml" not in names or "xl/workbook.xml" not in names:
                    raise _fail("content_mismatch")
                types = z.read("[Content_Types].xml").decode("utf-8", "replace").lower()
                if any(n.lower().endswith("vbaproject.bin") for n in names) or "macroenabled" in types:
                    raise _fail("macro_enabled")
        except zipfile.BadZipFile as exc:
            raise _fail("content_mismatch") from exc
        return "xlsx"
    head = data[: 1 << 20]
    if b"\x00" in head:
        raise _fail("content_mismatch")
    try:
        data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise _fail("not_utf8", "Save the CSV as UTF-8 and upload it again.") from exc
    return "csv"


async def _scan(request: Request, data: bytes) -> str:
    """Optional malware scan (FEAT-011): an HTTP scanner that answers {"clean": true|false}. Fails closed."""
    url = getattr(request.app.state.settings, "file_scan_url", None)
    if not url:
        return "not_configured"
    import httpx

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(url, content=data, headers={"Content-Type": "application/octet-stream"})
            clean = resp.status_code == 200 and resp.json().get("clean") is True
    except Exception as exc:
        raise TelaError(ErrorCode.STORAGE_UNAVAILABLE, "The file could not be scanned. Try again later.") from exc
    if not clean:
        raise _fail("malware", "The file was flagged by the malware scanner and was not stored.")
    return "clean"


async def _read(request: Request, file: UploadFile) -> tuple[str, bytes, str, str]:
    name = (file.filename or "upload").rsplit("/", 1)[-1].rsplit("\\", 1)[-1][:200]
    limit = int(request.app.state.settings.file_source_max_upload_mb) * 1024 * 1024
    data = await file.read(limit + 1)
    fmt = check_upload(name, data, limit)
    scan = await _scan(request, data)
    return name, data, fmt, scan


def _file_out(r: dict[str, Any]) -> dict[str, Any]:
    return {"version": r["version_no"], "status": r["status"], "filename": r["original_filename"],
            "size_bytes": r["size_bytes"], "sha256": r["sha256"], "sheets": r["sheet_manifest"],
            "retention_policy": r["retention_policy"], "retention_days": r["retention_days"],
            "retention_until": r["retention_until"], "classification": r["classification"],
            "replaced_at": r["replaced_at"], "uploaded_at": r["created_at"]}


async def _versions(conn: AsyncConnection, connection_id: Any) -> list[dict[str, Any]]:
    return [dict(r) for r in await fetch_all(
        conn, "SELECT * FROM conn.file_sources WHERE connection_id = :c ORDER BY version_no DESC",
        {"c": connection_id})]


def _descriptor_file(version: dict[str, Any], fmt: str, sheets: list[dict[str, Any]]) -> dict[str, Any]:
    uploaded = version.get("created_at") or datetime.now(UTC)
    return {"file_id": str(version["id"]), "version": version["version_no"], "object_key": version["object_key"],
            "sha256": version["sha256"], "format": fmt, "filename": version["original_filename"], "sheets": sheets,
            "uploaded_at": uploaded.isoformat()}


# --------------------------------------------------------------------------- create (API-011)


@router.post("/files", status_code=201)
async def upload_source(request: Request, file: UploadFile = File(...),  # noqa: B008 - FastAPI form parameter
                        name: str = Form(min_length=1, max_length=80),
                        retention_policy: Literal["replaced_plus_30d", "fixed_days"] = Form("replaced_plus_30d"),
                        retention_days: int | None = Form(default=None, ge=1, le=3650),
                        classification: Literal["internal", "confidential", "restricted"] = Form("confidential"),
                        principal: Principal = Depends(require(Perm.CONNECTIONS_MANAGE))) -> dict[str, Any]:
    if (retention_policy == "fixed_days") != (retention_days is not None):
        raise TelaError(ErrorCode.VALIDATION_ERROR, "Choose how long to keep the file.",
                        {"reason": "retention_required"})
    filename, data, fmt, scan = await _read(request, file)
    connection_id, file_id = new_id(), new_id()
    key = tenant_key(principal.tenant_id, "files", file_id, "v1")
    async with tenant_tx(principal.tenant_id) as conn:
        if await fetch_one(conn, "SELECT 1 FROM conn.connections WHERE name = :n AND deleted_at IS NULL", {"n": name}):
            raise TelaError(ErrorCode.CONFLICT, "A connection with this name already exists.")
    await (await _store(request)).put(key, data, CONTENT_TYPES[fmt])
    digest = hashlib.sha256(data).hexdigest()
    version = {"id": file_id, "version_no": 1, "object_key": key, "sha256": digest, "original_filename": filename}
    descriptor = {"file": _descriptor_file(version, fmt, []), "approved_schemas": ["main"]}
    until = datetime.now(UTC) + timedelta(days=retention_days) if retention_days else None
    async with tenant_tx(principal.tenant_id) as conn:
        await conn.execute(
            text("INSERT INTO conn.connections (id, tenant_id, name, engine, descriptor, status, created_by) "
                 "VALUES (:id, :t, :n, :e, CAST(:d AS jsonb), 'draft', :by)"),
            {"id": connection_id, "t": principal.tenant_id, "n": name, "e": ENGINES[fmt],
             "d": json.dumps(descriptor), "by": principal.user_id})
        await conn.execute(
            text("INSERT INTO conn.file_sources (id, tenant_id, connection_id, version_no, object_key, "
                 "original_filename, content_type, size_bytes, sha256, status, retention_policy, retention_days, "
                 "retention_until, classification, created_by) VALUES (:id, :t, :c, 1, :k, :fn, :ct, :b, :h, "
                 "'active', :rp, :rd, :ru, :cl, :by)"),
            {"id": file_id, "t": principal.tenant_id, "c": connection_id, "k": key, "fn": filename,
             "ct": CONTENT_TYPES[fmt], "b": len(data), "h": digest, "rp": retention_policy, "rd": retention_days,
             "ru": until, "cl": classification, "by": principal.user_id})
        await audit.record(conn, tenant_id=principal.tenant_id, action="connection.created", object_type="connection",
                           object_id=connection_id, outcome="success", actor_id=principal.user_id,
                           correlation_id=_rid(request),
                           details={"engine": ENGINES[fmt], "name": name, "file_bytes": len(data), "scan": scan,
                                    "classification": classification, "retention": retention_policy})
        row = await _load(conn, connection_id)
    return {"connection_id": connection_id, **_out(row)}


@router.get("/{connection_id}/files")
async def list_files(connection_id: str,
                     principal: Principal = Depends(require(Perm.CONNECTIONS_MANAGE))) -> dict[str, Any]:
    async with tenant_tx(principal.tenant_id) as conn:
        row = await _load(conn, connection_id)
        if row["engine"] not in ENGINES.values():
            raise TelaError(ErrorCode.VALIDATION_ERROR, "This connection is not a file source.")
        versions = await _versions(conn, row["id"])
    return {"settings": (row["descriptor"].get("file") or {}).get("sheets") or [],
            "items": [_file_out(v) for v in versions]}


# --------------------------------------------------------------------------- confirm sheets, types, retention


class ColumnSetting(BaseModel):
    header: str = Field(min_length=1, max_length=255)
    name: str | None = Field(default=None, pattern=r"^[a-z_][a-z0-9_]{0,62}$")
    type: Literal["integer", "decimal", "date", "datetime", "boolean", "text"] | None = None
    personal: bool = False


class SheetSetting(BaseModel):
    sheet: str = Field(min_length=1, max_length=255)
    include: bool = True
    header_row: int = Field(default=1, ge=1, le=1000)
    table: str | None = Field(default=None, pattern=r"^[a-z_][a-z0-9_]{0,62}$")
    columns: list[ColumnSetting] = Field(default_factory=list, max_length=1000)


class FileSettingsIn(BaseModel):
    sheets: list[SheetSetting] = Field(max_length=200)
    retention_policy: Literal["replaced_plus_30d", "fixed_days"] | None = None
    retention_days: int | None = Field(default=None, ge=1, le=3650)
    classification: Literal["internal", "confidential", "restricted"] | None = None


@router.put("/{connection_id}/files/settings")
async def confirm_settings(connection_id: str, body: FileSettingsIn, request: Request,
                           principal: Principal = Depends(require(Perm.CONNECTIONS_MANAGE))) -> dict[str, Any]:
    """The Admin's confirmed sheets, header rows, column types and personal-data columns (and retention). The
    source must be re-tested and its schema re-read afterwards, like any credential change."""
    if not any(s.include for s in body.sheets):
        raise TelaError(ErrorCode.VALIDATION_ERROR, "Include at least one sheet.")
    tables = [s.table for s in body.sheets if s.table]
    if len(tables) != len(set(tables)):
        raise TelaError(ErrorCode.VALIDATION_ERROR, "Two sheets have the same table name.")
    if body.retention_policy == "fixed_days" and body.retention_days is None:
        raise TelaError(ErrorCode.VALIDATION_ERROR, "Choose how long to keep the file.",
                        {"reason": "retention_required"})
    async with tenant_tx(principal.tenant_id) as conn:
        row = await _load(conn, connection_id)
        if row["engine"] not in ENGINES.values():
            raise TelaError(ErrorCode.VALIDATION_ERROR, "This connection is not a file source.")
        descriptor = dict(row["descriptor"])
        descriptor["file"] = {**descriptor["file"],
                              "sheets": [s.model_dump(exclude_none=True) for s in body.sheets]}
        await conn.execute(
            text("UPDATE conn.connections SET descriptor = CAST(:d AS jsonb), status = CASE WHEN status = "
                 "'disabled' THEN status ELSE 'draft' END, read_only_verified_at = NULL WHERE id = :id"),
            {"d": json.dumps(descriptor), "id": row["id"]})
        if body.retention_policy or body.classification:
            policy = body.retention_policy
            await conn.execute(
                text("UPDATE conn.file_sources SET retention_policy = coalesce(:rp, retention_policy), "
                     "retention_days = CASE WHEN :rp IS NULL THEN retention_days WHEN :rp = 'fixed_days' "
                     "THEN :rd ELSE NULL END, retention_until = CASE WHEN :rp = 'fixed_days' THEN created_at + "
                     "make_interval(days => :rd) WHEN :rp = 'replaced_plus_30d' THEN NULL ELSE retention_until END, "
                     "classification = coalesce(:cl, classification) WHERE connection_id = :c AND status = 'active'"),
                {"rp": policy, "rd": body.retention_days, "cl": body.classification, "c": row["id"]})
        await audit.record(conn, tenant_id=principal.tenant_id, action="connection.file_settings_confirmed",
                           object_type="connection", object_id=connection_id, outcome="success",
                           actor_id=principal.user_id, correlation_id=_rid(request),
                           details={"sheets": len(body.sheets), "included": sum(s.include for s in body.sheets),
                                    "personal_columns": sum(c.personal for s in body.sheets for c in s.columns),
                                    "retention": body.retention_policy, "classification": body.classification})
        await _invalidate_cache(request, conn, connection_id)
        updated = await _load(conn, connection_id)
    return {**_out(updated), "retest_required": True}


# --------------------------------------------------------------------------- replace (E-13)


def _diff(before: list[dict[str, Any]], after: list[dict[str, Any]],
          model: dict[str, Any] | None) -> dict[str, Any]:
    old = {o["qualified_name"]: {c["name"]: c["type"] for c in o["columns"]} for o in before}
    new = {o["qualified_name"]: {c["name"]: c["type"] for c in o["columns"]} for o in after}
    changed = []
    for q in sorted(set(old) & set(new)):
        removed = sorted(set(old[q]) - set(new[q]))
        added = sorted(set(new[q]) - set(old[q]))
        retyped = sorted(c for c in set(old[q]) & set(new[q]) if old[q][c] != new[q][c])
        if removed or added or retyped:
            changed.append({"object": q, "removed_columns": removed, "added_columns": added, "retyped": retyped})
    breaks = []
    for ent in (model or {}).get("entities", []):
        cols = new.get(ent["source_object"])
        if cols is None:
            breaks.append({"entity": ent["key"], "problem": "sheet removed"})
            continue
        gone = [f["source_column"] for f in ent["fields"] if f["source_column"] not in cols]
        retyped = [f["source_column"] for f in ent["fields"]
                   if f["source_column"] in cols and old.get(ent["source_object"], {}).get(f["source_column"])
                   not in (None, cols[f["source_column"]])]
        if gone or retyped:
            breaks.append({"entity": ent["key"], "problem": "columns removed or changed",
                           "columns": sorted(gone + retyped)})
    return {"added": sorted(set(new) - set(old)), "removed": sorted(set(old) - set(new)), "changed": changed,
            "breaks_model": breaks}


@router.post("/{connection_id}/files", status_code=201)
async def upload_version(connection_id: str, request: Request,
                         file: UploadFile = File(...),  # noqa: B008 - FastAPI form parameter
                         principal: Principal = Depends(require(Perm.CONNECTIONS_MANAGE))) -> dict[str, Any]:
    """Upload a replacement as a pending version and show what it changes before it is activated (E-13)."""
    filename, data, fmt, scan = await _read(request, file)
    async with tenant_tx(principal.tenant_id) as conn:
        row = await _load(conn, connection_id)
        if row["engine"] != ENGINES[fmt]:
            raise TelaError(ErrorCode.VALIDATION_ERROR, f"Upload a .{'xlsx' if row['engine'] == 'excel' else 'csv'} "
                            "file to replace this source.")
        versions = await _versions(conn, row["id"])
        if any(v["status"] == "pending" for v in versions):
            raise TelaError(ErrorCode.CONFLICT, "A replacement is already waiting. Activate or discard it first.")
        active = next(v for v in versions if v["status"] == "active")
        before = await fetch_all(conn, "SELECT qualified_name, columns FROM conn.connection_objects WHERE "
                                       "connection_id = :c AND removed_at IS NULL", {"c": row["id"]})
        model = await fetch_one(conn, "SELECT v.definition FROM model.data_models m JOIN model.model_versions v "
                                      "ON v.id = m.current_version_id WHERE m.connection_id = :c", {"c": row["id"]})
    version_no = max(v["version_no"] for v in versions) + 1
    key = tenant_key(principal.tenant_id, "files", str(active["id"]), f"v{version_no}")
    await (await _store(request)).put(key, data, CONTENT_TYPES[fmt])
    digest = hashlib.sha256(data).hexdigest()
    file_id = new_id()
    pending = {"id": file_id, "version_no": version_no, "object_key": key, "sha256": digest,
               "original_filename": filename}
    sheets = (row["descriptor"].get("file") or {}).get("sheets") or []
    probe = {**_runner_payload(principal, row),
             "descriptor": {**row["descriptor"], "file": _descriptor_file(pending, fmt, sheets)}, "max_objects": 2000}
    try:
        catalog = await _runner(request).post("introspect", probe, timeout_s=90)
    except TelaError:
        await (await _store(request)).delete(key)
        raise
    async with tenant_tx(principal.tenant_id) as conn:
        await conn.execute(
            text("INSERT INTO conn.file_sources (id, tenant_id, connection_id, version_no, object_key, "
                 "original_filename, content_type, size_bytes, sha256, sheet_manifest, status, retention_policy, "
                 "retention_days, classification, created_by) VALUES (:id, :t, :c, :n, :k, :fn, :ct, :b, :h, "
                 "CAST(:m AS jsonb), 'pending', :rp, :rd, :cl, :by)"),
            {"id": file_id, "t": principal.tenant_id, "c": row["id"], "n": version_no, "k": key, "fn": filename,
             "ct": CONTENT_TYPES[fmt], "b": len(data), "h": digest,
             "m": json.dumps((catalog.get("profile") or {}).get("sheets") or []),
             "rp": active["retention_policy"], "rd": active["retention_days"], "cl": active["classification"],
             "by": principal.user_id})
        await audit.record(conn, tenant_id=principal.tenant_id, action="connection.file_uploaded",
                           object_type="connection", object_id=connection_id, outcome="success",
                           actor_id=principal.user_id, correlation_id=_rid(request),
                           details={"version": version_no, "file_bytes": len(data), "scan": scan})
    diff = _diff([dict(b) for b in before], catalog["objects"], model["definition"] if model else None)
    return {"version": version_no, "status": "pending", "diff": diff,
            "sheets": (catalog.get("profile") or {}).get("sheets") or []}


@router.post("/{connection_id}/files/{version}/activate")
async def activate_version(connection_id: str, version: int, request: Request,
                           principal: Principal = Depends(require(Perm.CONNECTIONS_MANAGE))) -> dict[str, Any]:
    from tela_api.connections.introspection import run_introspection
    from tela_api.connections.router import run_test

    async with tenant_tx(principal.tenant_id) as conn:
        row = await _load(conn, connection_id)
        versions = {v["version_no"]: v for v in await _versions(conn, row["id"])}
        pending = versions.get(version)
        if pending is None or pending["status"] != "pending":
            raise TelaError(ErrorCode.NOT_FOUND, "That replacement is not waiting to be activated.")
        active = next(v for v in versions.values() if v["status"] == "active")
        retire_at = datetime.now(UTC) + RETENTION_GRACE if active["retention_policy"] == "replaced_plus_30d" \
            else active["retention_until"]
        await conn.execute(text("UPDATE conn.file_sources SET status = 'replaced', replaced_at = now(), "
                                "retention_until = :u WHERE id = :id"), {"u": retire_at, "id": active["id"]})
        until = (datetime.now(UTC) + timedelta(days=pending["retention_days"])) if pending["retention_days"] else None
        await conn.execute(text("UPDATE conn.file_sources SET status = 'active', retention_until = :u WHERE id = :id"),
                           {"u": until, "id": pending["id"]})
        descriptor = dict(row["descriptor"])
        fmt = "csv" if row["engine"] == "csv" else "xlsx"
        descriptor["file"] = _descriptor_file(pending, fmt, (descriptor.get("file") or {}).get("sheets") or [])
        await conn.execute(
            text("UPDATE conn.connections SET descriptor = CAST(:d AS jsonb), status = CASE WHEN status = 'disabled' "
                 "THEN status ELSE 'connected' END WHERE id = :id"), {"d": json.dumps(descriptor), "id": row["id"]})
        await audit.record(conn, tenant_id=principal.tenant_id, action="connection.file_activated",
                           object_type="connection", object_id=connection_id, outcome="success",
                           actor_id=principal.user_id, correlation_id=_rid(request), severity="high",
                           details={"version": version, "replaced": active["version_no"]})
        await outbox.emit(conn, tenant_id=principal.tenant_id, topic="connection.changed", aggregate_id=connection_id,
                          payload={"connection_id": connection_id, "file_version": version})
        await _invalidate_cache(request, conn, connection_id)
    test = await run_test(request, principal, connection_id)
    intro = await run_introspection(request, principal, connection_id) if test["status"] == "connected" else None
    return {"version": version, "test": test, "introspection": intro}


@router.post("/{connection_id}/files/{version}/discard")
async def discard_version(connection_id: str, version: int, request: Request,
                          principal: Principal = Depends(require(Perm.CONNECTIONS_MANAGE))) -> dict[str, Any]:
    async with tenant_tx(principal.tenant_id) as conn:
        row = await _load(conn, connection_id)
        res = await conn.execute(
            text("UPDATE conn.file_sources SET status = 'discarded', retention_until = now() WHERE connection_id = :c "
                 "AND version_no = :n AND status = 'pending'"), {"c": row["id"], "n": version})
        if not res.rowcount:
            raise TelaError(ErrorCode.NOT_FOUND, "That replacement is not waiting to be activated.")
        await audit.record(conn, tenant_id=principal.tenant_id, action="connection.file_discarded",
                           object_type="connection", object_id=connection_id, outcome="success",
                           actor_id=principal.user_id, correlation_id=_rid(request), details={"version": version})
    return {"version": version, "status": "discarded", "etag": _etag(row)}


# --------------------------------------------------------------------------- retention sweep (DEC-07)


@internal.post("/sweep")
async def sweep(request: Request, _: dict[str, Any] = Depends(require_worker)) -> dict[str, int]:
    """Delete expired file versions no approved model pins (DATA-013 retention), across tenants."""
    async with tenant_tx(None) as conn:
        rows = await fetch_all(conn, "SELECT * FROM conn.f_expired_file_versions(200)")
    store: ObjectStore | None = getattr(request.app.state, "object_store", None)
    purged = 0
    for r in rows:
        if store is None:
            break
        try:
            await store.delete(r["object_key"])
        except TelaError:
            log.warning("files.sweep_delete_failed", file_id=str(r["id"]))
            continue
        async with tenant_tx(str(r["tenant_id"])) as conn:
            await conn.execute(text("UPDATE conn.file_sources SET status = 'purged', purged_at = now() "
                                    "WHERE id = :id"), {"id": r["id"]})
            await audit.record(conn, tenant_id=str(r["tenant_id"]), action="connection.file_purged",
                               object_type="connection", object_id=str(r["connection_id"]), outcome="success",
                               actor_id=None, correlation_id=None, details={"version": r["version_no"]})
        purged += 1
    return {"expired": len(rows), "purged": purged}
