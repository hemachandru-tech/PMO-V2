"""Context documents attached to a pack (ENH-025, US-233; FEAT-075; migration 0051).

A pack can carry the business context its knowledge comes from: a Markdown, text, PDF or Word file, stored like a
US-102 knowledge document (same types, size limit and content checks; the bytes in the object store under the
tenant's ``knowledge`` class, the row in ``model.pack_attachments``).

* ``POST /packs/{id}/knowledge/attachments`` (multipart ``file``, ``title``) — attach to a tenant draft.
* ``DELETE /packs/{id}/knowledge/attachments/{attachment_id}`` — take it off the draft.
* ``GET /packs/{id}/knowledge/attachments/{attachment_id}/file`` — download it.

A clone keeps its source's attachments (``copy_to_pack``; the rows share the stored file, which is deleted with the
last row naming it). **On install** each attachment is copied to the connection's knowledge as a new US-102
document in ``pending_review`` (``copy_to_connection``): the worker indexes it, an Admin approves it under Admin →
Knowledge before Ask reads it, and the US-203 definition proposals come from it as from any upload. A document
already on that connection (same content, not retired) is not copied again.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

from fastapi import APIRouter, File, Form, Request, Response, UploadFile
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection

from tela_api import outbox
from tela_api.audit import service as audit
from tela_api.content.exports import _store
from tela_api.core.db import fetch_all, fetch_one, tenant_tx
from tela_api.identity.principal import Principal
from tela_api.knowledge.documents import TYPES
from tela_api.packs.studio.deps import AUTHOR, rid
from tela_api.packs.studio.knowledge import pack_for
from tela_common.errors import ErrorCode, TelaError
from tela_common.ids import new_id
from tela_common.logging import get_logger
from tela_common.objectstore import tenant_key

log = get_logger(__name__)
router = APIRouter(prefix="/api/v1/packs", tags=["packs"])

_COLS = ("a.id, a.title, a.original_filename, a.content_type, a.size_bytes, a.content_hash, a.created_at, "
         "a.created_by, u.display_name AS created_by_name")


def _out(r: dict[str, Any]) -> dict[str, Any]:
    return {"id": str(r["id"]), "title": r["title"], "file_name": r["original_filename"],
            "content_type": r["content_type"], "size_bytes": r["size_bytes"], "sha256": r["content_hash"],
            "created_at": r["created_at"].isoformat() if r["created_at"] else None,
            "created_by": str(r["created_by"]) if r["created_by"] else None,
            "created_by_name": r.get("created_by_name")}


async def list_attachments(conn: AsyncConnection, pack_id: str) -> list[dict[str, Any]]:
    rows = await fetch_all(conn, f"SELECT {_COLS} FROM model.pack_attachments a LEFT JOIN iam.users u ON "  # noqa: S608
                                 "u.id = a.created_by WHERE a.pack_id = CAST(:p AS uuid) ORDER BY a.created_at, "
                                 "a.title", {"p": pack_id})
    return [_out(dict(r)) for r in rows]


async def _one(conn: AsyncConnection, pack_id: str, attachment_id: str) -> dict[str, Any]:
    try:
        row = await fetch_one(conn, "SELECT id, title, original_filename, content_type, object_key FROM "
                                    "model.pack_attachments WHERE id = CAST(:a AS uuid) AND pack_id = CAST(:p AS uuid)",
                              {"a": attachment_id, "p": pack_id})
    except Exception:  # noqa: BLE001 - malformed id
        row = None
    if row is None:
        raise TelaError(ErrorCode.NOT_FOUND, "That document is not attached to this pack.")
    return dict(row)


@router.post("/{pack_id}/knowledge/attachments", status_code=201)
async def attach(pack_id: str, request: Request, file: UploadFile = File(...),  # noqa: B008 - FastAPI form parameter
                 title: str | None = Form(default=None, max_length=200), principal: Principal = AUTHOR
                 ) -> dict[str, Any]:
    name = (file.filename or "document").rsplit("/", 1)[-1][:200]
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if ext not in TYPES:
        raise TelaError(ErrorCode.VALIDATION_ERROR, "Attach a Markdown, text, PDF or Word (.docx) file.",
                        {"allowed": sorted(TYPES)})
    limit_mb = int(request.app.state.settings.knowledge_max_upload_mb)
    data = await file.read(limit_mb * 1024 * 1024 + 1)
    if len(data) > limit_mb * 1024 * 1024:
        raise TelaError(ErrorCode.PAYLOAD_TOO_LARGE, f"The file is larger than {limit_mb} MB.")
    content_type, magic = TYPES[ext]
    if not data or (magic and not data.startswith(magic)):
        raise TelaError(ErrorCode.VALIDATION_ERROR, "The file's contents don't match its type.")
    digest = hashlib.sha256(data).hexdigest()
    attachment_id = str(new_id())
    async with tenant_tx(principal.tenant_id) as conn:
        row = await pack_for(conn, pack_id, write=True)
        dup = await fetch_one(conn, "SELECT title FROM model.pack_attachments WHERE pack_id = CAST(:p AS uuid) AND "
                                    "content_hash = :h", {"p": row.id, "h": digest})
        if dup is not None:
            raise TelaError(ErrorCode.CONFLICT, f"This file is already attached as “{dup['title']}”.")
    key = tenant_key(principal.tenant_id, "knowledge", "packs", row.id, f"{attachment_id}.{ext}")
    await (await _store(request)).put(key, data, content_type)
    clean = (title or "").strip() or re.sub(r"[_-]+", " ", name.rsplit(".", 1)[0]).strip()[:200] or "Document"
    async with tenant_tx(principal.tenant_id) as conn:
        row = await pack_for(conn, pack_id, write=True)
        try:
            async with conn.begin_nested():
                await conn.execute(text(
                    "INSERT INTO model.pack_attachments (id, pack_id, tenant_id, title, original_filename, "
                    "content_type, size_bytes, content_hash, object_key, created_by) VALUES (CAST(:id AS uuid), "
                    "CAST(:p AS uuid), CAST(:t AS uuid), :title, :n, :ct, :b, :h, :key, CAST(:u AS uuid))"),
                    {"id": attachment_id, "p": row.id, "t": row.tenant_id, "title": clean, "n": name,
                     "ct": content_type, "b": len(data), "h": digest, "key": key, "u": principal.user_id})
        except IntegrityError as exc:  # the same file attached meanwhile
            raise TelaError(ErrorCode.CONFLICT, "This file is already attached to the pack.") from exc
        await audit.record(conn, tenant_id=principal.tenant_id, action="pack.document_attached", object_type="pack",
                           object_id=row.id, outcome="success", actor_id=principal.user_id,
                           correlation_id=rid(request),
                           details={"pack": row.pack_key, "version": row.pack_version, "attachment": attachment_id,
                                    "title": clean, "type": ext, "bytes": len(data)})
        return next(a for a in await list_attachments(conn, row.id) if a["id"] == attachment_id)


@router.delete("/{pack_id}/knowledge/attachments/{attachment_id}", status_code=204)
async def detach(pack_id: str, attachment_id: str, request: Request, principal: Principal = AUTHOR) -> Response:
    async with tenant_tx(principal.tenant_id) as conn:
        row = await pack_for(conn, pack_id, write=True)
        att = await _one(conn, row.id, attachment_id)
        await conn.execute(text("DELETE FROM model.pack_attachments WHERE id = :a"), {"a": att["id"]})
        shared = await fetch_one(conn, "SELECT 1 FROM model.pack_attachments WHERE object_key = :k LIMIT 1",
                                 {"k": att["object_key"]})
        await audit.record(conn, tenant_id=principal.tenant_id, action="pack.document_detached", object_type="pack",
                           object_id=row.id, outcome="success", actor_id=principal.user_id,
                           correlation_id=rid(request),
                           details={"pack": row.pack_key, "version": row.pack_version, "attachment": attachment_id,
                                    "title": att["title"]})
    if shared is None:
        try:
            await (await _store(request)).delete(att["object_key"])
        except Exception:  # noqa: BLE001 - the row is gone; a stray object is swept by the bucket's lifecycle
            log.warning("pack_attachment_delete_failed", key=att["object_key"])
    return Response(status_code=204)


@router.get("/{pack_id}/knowledge/attachments/{attachment_id}/file")
async def download(pack_id: str, attachment_id: str, request: Request, principal: Principal = AUTHOR) -> Response:
    async with tenant_tx(principal.tenant_id) as conn:
        row = await pack_for(conn, pack_id)
        att = await _one(conn, row.id, attachment_id)
    data = await (await _store(request)).get(att["object_key"])
    safe = re.sub(r"[^A-Za-z0-9._-]", "-", att["original_filename"])[:200]
    return Response(content=data, media_type=att["content_type"],
                    headers={"Content-Disposition": f'attachment; filename="{safe}"'})


# --------------------------------------------------------------------------- clone and install


async def copy_to_pack(conn: AsyncConnection, principal: Principal, from_pack_id: str, to_pack_id: str) -> int:
    """A new draft made from another pack keeps its attachments (the stored files are shared, not copied)."""
    result = await conn.execute(text(
        "INSERT INTO model.pack_attachments (id, pack_id, tenant_id, title, original_filename, content_type, "
        "size_bytes, content_hash, object_key, created_by) SELECT gen_random_uuid(), CAST(:to AS uuid), "
        "CAST(:t AS uuid), title, original_filename, content_type, size_bytes, content_hash, object_key, "
        "CAST(:u AS uuid) FROM model.pack_attachments WHERE pack_id = CAST(:src AS uuid) AND "
        "(tenant_id = CAST(:t AS uuid))"),
        {"to": to_pack_id, "t": principal.tenant_id, "u": principal.user_id, "src": from_pack_id})
    return int(result.rowcount or 0)


async def copy_to_connection(request: Request, principal: Principal, pack_id: str, connection_id: str
                             ) -> dict[str, Any]:
    """Each of the pack's attachments becomes a knowledge document of this connection, waiting for review."""
    async with tenant_tx(principal.tenant_id) as conn:
        rows = await fetch_all(conn, "SELECT a.id, a.title, a.original_filename, a.content_type, a.size_bytes, "
                                     "a.content_hash, a.object_key, p.pack_key FROM model.pack_attachments a JOIN "
                                     "model.starter_packs p ON p.id = a.pack_id WHERE a.pack_id = CAST(:p AS uuid) "
                                     "ORDER BY a.created_at", {"p": pack_id})
        have = {str(r["content_hash"]) for r in await fetch_all(
            conn, "SELECT content_hash FROM rag.knowledge_documents WHERE connection_id = CAST(:c AS uuid) AND "
                  "review_status <> 'retired'", {"c": connection_id})}
    todo = [dict(r) for r in rows if str(r["content_hash"]) not in have]
    out: dict[str, Any] = {"attached": len(rows), "copied": [], "already_there": len(rows) - len(todo)}
    if not todo:
        return out
    store_ = await _store(request)
    copies: list[tuple[str, dict[str, Any], str]] = []
    for att in todo:
        doc_id = str(new_id())
        ext = att["original_filename"].rsplit(".", 1)[-1].lower()
        key = tenant_key(principal.tenant_id, "knowledge", f"{doc_id}.{ext}")
        await store_.put(key, await store_.get(att["object_key"]), att["content_type"])
        copies.append((doc_id, att, key))
    async with tenant_tx(principal.tenant_id) as conn:
        for doc_id, att, key in copies:
            await conn.execute(
                text("INSERT INTO rag.knowledge_documents (id, tenant_id, connection_id, source_kind, title, "
                     "object_key, original_filename, content_type, size_bytes, content_hash, created_by) VALUES "
                     "(:id, :t, CAST(:c AS uuid), 'uploaded_doc', :title, :key, :n, :ct, :b, :h, :u)"),
                {"id": doc_id, "t": principal.tenant_id, "c": connection_id, "title": att["title"], "key": key,
                 "n": att["original_filename"], "ct": att["content_type"], "b": att["size_bytes"],
                 "h": att["content_hash"], "u": principal.user_id})
            await outbox.emit(conn, tenant_id=principal.tenant_id, topic="knowledge.document_uploaded",
                              aggregate_id=doc_id, payload={"document_id": doc_id})
            await audit.record(conn, tenant_id=principal.tenant_id, action="knowledge.uploaded",
                               object_type="document", object_id=doc_id, outcome="success",
                               actor_id=principal.user_id, correlation_id=rid(request),
                               details={"title": att["title"], "kind": "uploaded_doc", "bytes": att["size_bytes"],
                                        "from_pack": att["pack_key"], "pack_id": pack_id,
                                        "attachment": str(att["id"])})
            out["copied"].append({"document_id": doc_id, "title": att["title"]})
    return out
