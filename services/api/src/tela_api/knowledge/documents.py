"""Knowledge documents and the review gate (US-102; API-049, API-051) and the indexing endpoints (US-100).

Uploads (Markdown, plain text, PDF, DOCX; ≤ 25 MB) are stored, then indexed by the worker while they wait in
``pending_review``; retrieval only ever reads ``approved`` documents. A document whose text carries injection
markers can only be approved with an explicit acknowledgement (FEAT-014 A1). The approved model's definitions
are indexed on approval and approved by definition; the previous version's are retired (REQ-035).

The business definitions an indexed document states are proposed for review as it is indexed, and carried to each
newly approved model version (US-203, ``knowledge/definitions.py``).
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Literal

from fastapi import APIRouter, Depends, File, Form, Query, Request, Response, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from tela_api import outbox
from tela_api.audit import service as audit
from tela_api.content.common import notify
from tela_api.content.exports import _rid, _store, require_worker
from tela_api.core.db import fetch_all, fetch_one, tenant_tx
from tela_api.identity.deps import require
from tela_api.identity.permissions import Perm
from tela_api.identity.principal import Principal
from tela_api.knowledge import conditions, definitions
from tela_api.knowledge.corpus import model_chunks
from tela_api.knowledge.guidance import index_glossary, index_guidance
from tela_api.knowledge.index import recontextualise, reembed_missing, replace_chunks
from tela_api.knowledge.patterns import index_patterns
from tela_api.knowledge.retrieval import retrieve
from tela_api.model.store import load_snapshot
from tela_common import business_sheet
from tela_common.errors import ErrorCode, TelaError
from tela_common.ids import new_id
from tela_common.logging import get_logger
from tela_common.objectstore import tenant_key

log = get_logger(__name__)

router = APIRouter(prefix="/api/v1/knowledge", tags=["knowledge"])
internal = APIRouter(prefix="/api/v1/internal/knowledge", tags=["internal"], include_in_schema=False)

TYPES = {  # extension → (content type, magic prefix or None)
    "md": ("text/markdown", None), "markdown": ("text/markdown", None), "txt": ("text/plain", None),
    "pdf": ("application/pdf", b"%PDF"),
    "docx": ("application/vnd.openxmlformats-officedocument.wordprocessingml.document", b"PK\x03\x04"),
    "xlsx": (business_sheet.CONTENT_TYPE, business_sheet.MAGIC),  # a business-context sheet (US-251)
}
FAIL_MESSAGES = {
    "unsupported_type": "This file type can't be indexed.",
    "unreadable_document": "The file couldn't be read. Check it opens, then upload it again.",
    "encrypted_document": "The PDF is password-protected. Upload an unprotected copy.",
    "no_text": "No text was found in the file. Scanned pages need a text layer.",
    "too_many_chunks": "The document is too long to index. Split it into smaller documents.",
    "bad_template": "The sheet's first row isn't the business-context template's header. Download the template.",
    "index_failed": "Indexing failed. Try re-indexing in a few minutes.",
    "all_dropped": "Every passage contained a secret such as a key or password, so nothing was kept.",
}
# Built from the approved model (and its pack's guidance, US-104 / US-159); they follow it.
MODEL_KINDS = ("model_definition", "chart_pattern", "dashboard_rule", "report_blueprint", "analysis_recipe")
DOC_COLUMNS = ("d.id, d.connection_id, d.model_version_id, d.source_kind, d.title, d.original_filename, "
               "d.content_type, d.size_bytes, d.review_status, d.index_status, d.index_error, d.scan_flags, "
               "d.injection_flagged, d.acknowledged_by, d.reviewed_by, d.reviewed_at, d.review_note, "
               "d.embedding_model, d.chunk_count, d.indexed_at, d.created_by, d.created_at, d.updated_at")


def _out(r: dict[str, Any]) -> dict[str, Any]:
    """A document row for the API: ids as strings, no storage key or tenant."""
    return {k: (str(v) if v is not None and (k == "id" or k.endswith(("_id", "_by"))) else v)
            for k, v in r.items() if k not in ("object_key", "tenant_id")}


async def _doc(conn: AsyncConnection, doc_id: str) -> dict[str, Any]:
    try:
        row = await fetch_one(conn, f"SELECT {DOC_COLUMNS}, d.object_key, d.tenant_id FROM rag.knowledge_documents d "  # noqa: S608
                                    "WHERE d.id = CAST(:id AS uuid)", {"id": doc_id})
    except Exception:  # noqa: BLE001 - malformed id
        row = None
    if row is None:
        raise TelaError(ErrorCode.NOT_FOUND, "That document was not found.")
    return dict(row)


# --------------------------------------------------------------------------- API-049


@router.get("/documents")
async def list_documents(status: str | None = Query(default=None, max_length=20),
                         kind: str | None = Query(default=None, max_length=30),
                         principal: Principal = Depends(require(Perm.KNOWLEDGE_MANAGE))) -> dict[str, Any]:
    async with tenant_tx(principal.tenant_id) as conn:
        rows = await fetch_all(
            conn, f"SELECT {DOC_COLUMNS}, c.name AS connection_name, u.display_name AS created_by_name, "  # noqa: S608
                  "r.display_name AS reviewed_by_name, v.version_no AS model_version_no "
                  "FROM rag.knowledge_documents d LEFT JOIN conn.connections c ON c.id = d.connection_id "
                  "LEFT JOIN iam.users u ON u.id = d.created_by LEFT JOIN iam.users r ON r.id = d.reviewed_by "
                  "LEFT JOIN model.model_versions v ON v.id = d.model_version_id "
                  "WHERE (CAST(:s AS text) IS NULL OR d.review_status = :s) "
                  "AND (CAST(:k AS text) IS NULL OR d.source_kind = :k) ORDER BY d.created_at DESC LIMIT 500",
            {"s": status, "k": kind})
    return {"items": [_out(dict(r)) for r in rows]}


@router.post("/documents", status_code=201)
async def upload_document(request: Request, file: UploadFile = File(...),  # noqa: B008 - FastAPI form parameter
                          title: str | None = Form(default=None, max_length=200),
                          source_kind: Literal["uploaded_doc", "glossary"] = Form(default="uploaded_doc"),
                          connection_id: str | None = Form(default=None, max_length=64),
                          principal: Principal = Depends(require(Perm.KNOWLEDGE_MANAGE))) -> dict[str, Any]:
    name = (file.filename or "document").rsplit("/", 1)[-1][:200]
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if ext not in TYPES:
        raise TelaError(ErrorCode.VALIDATION_ERROR, "Upload a Markdown, text, PDF, Word (.docx) or Excel (.xlsx) file.",
                        {"allowed": sorted(TYPES)})
    limit = int(request.app.state.settings.knowledge_max_upload_mb) * 1024 * 1024
    data = await file.read(limit + 1)
    if len(data) > limit:
        raise TelaError(ErrorCode.PAYLOAD_TOO_LARGE, f"The file is larger than "
                        f"{request.app.state.settings.knowledge_max_upload_mb} MB.")
    content_type, magic = TYPES[ext]
    if not data or (magic and not data.startswith(magic)):
        raise TelaError(ErrorCode.VALIDATION_ERROR, "The file's contents don't match its type.")
    if ext == "xlsx":  # US-251: only the business-context template is taken, checked before it is stored
        try:
            terms = len(business_sheet.read(data))
        except business_sheet.SheetError as exc:
            raise TelaError(ErrorCode.VALIDATION_ERROR, str(exc), {"reason": exc.code,
                                                                  "header": list(business_sheet.HEADER)}) from exc
        if not connection_id:
            raise TelaError(ErrorCode.VALIDATION_ERROR, "Choose the source this sheet describes.",
                            {"field": "connection_id"})
        log.info("knowledge.sheet_uploaded", terms=terms)
    digest = hashlib.sha256(data).hexdigest()
    doc_id = new_id()
    key = tenant_key(principal.tenant_id, "knowledge", f"{doc_id}.{ext}")
    async with tenant_tx(principal.tenant_id) as conn:
        dup = await fetch_one(conn, "SELECT id, title FROM rag.knowledge_documents WHERE content_hash = :h AND "
                                    "review_status <> 'retired' AND source_kind <> 'model_definition'", {"h": digest})
        if dup is not None:
            raise TelaError(ErrorCode.CONFLICT, f"This file is already uploaded as “{dup['title']}”.",
                            {"document_id": str(dup["id"])})
        if connection_id:
            ok = await fetch_one(conn, "SELECT 1 FROM conn.connections WHERE id = CAST(:c AS uuid) AND "
                                       "deleted_at IS NULL", {"c": connection_id})
            if ok is None:
                raise TelaError(ErrorCode.VALIDATION_ERROR, "That connection was not found.")
    await (await _store(request)).put(key, data, content_type)
    clean_title = (title or "").strip() or re.sub(r"[_-]+", " ", name.rsplit(".", 1)[0]).strip()[:200] or "Document"
    async with tenant_tx(principal.tenant_id) as conn:
        await conn.execute(
            text("INSERT INTO rag.knowledge_documents (id, tenant_id, connection_id, source_kind, title, object_key, "
                 "original_filename, content_type, size_bytes, content_hash, created_by) VALUES (:id, :t, "
                 "CAST(:c AS uuid), :k, :title, :key, :n, :ct, :b, :h, :u)"),
            {"id": doc_id, "t": principal.tenant_id, "c": connection_id or None, "k": source_kind,
             "title": clean_title, "key": key, "n": name, "ct": content_type, "b": len(data), "h": digest,
             "u": principal.user_id})
        await outbox.emit(conn, tenant_id=principal.tenant_id, topic="knowledge.document_uploaded",
                          aggregate_id=doc_id, payload={"document_id": doc_id})
        await audit.record(conn, tenant_id=principal.tenant_id, action="knowledge.uploaded", object_type="document",
                           object_id=doc_id, outcome="success", actor_id=principal.user_id,
                           correlation_id=_rid(request),
                           details={"title": clean_title, "kind": source_kind, "bytes": len(data), "type": ext})
        return _out(await _doc(conn, doc_id))


@router.get("/business-context/template")
async def business_context_template(_: Principal = Depends(require(Perm.KNOWLEDGE_MANAGE))) -> Response:
    """The business-context spreadsheet template (US-251): one sheet, the fixed header row, an operator list."""
    return Response(content=business_sheet.template(), media_type=business_sheet.CONTENT_TYPE,
                    headers={"Content-Disposition": 'attachment; filename="tela-business-context.xlsx"'})


@router.get("/documents/{doc_id}")
async def get_document(doc_id: str, principal: Principal = Depends(require(Perm.KNOWLEDGE_MANAGE))) -> dict[str, Any]:
    async with tenant_tx(principal.tenant_id) as conn:
        doc = await _doc(conn, doc_id)
        chunks = await fetch_all(conn, "SELECT id, chunk_no, content, metadata, embedding IS NOT NULL AS embedded "
                                       "FROM rag.knowledge_chunks WHERE document_id = :d ORDER BY chunk_no LIMIT 200",
                                 {"d": doc["id"]})
    return {**_out(doc), "chunks": [{"id": str(c["id"]), "chunk_no": c["chunk_no"], "content": c["content"],
                                     "metadata": c["metadata"], "embedded": c["embedded"]} for c in chunks]}


class ReviewIn(BaseModel):
    decision: Literal["approve", "reject", "retire"]
    note: str | None = Field(default=None, max_length=1000)
    acknowledge_injection: bool = False


@router.post("/documents/{doc_id}/review")
async def review_document(doc_id: str, body: ReviewIn, request: Request,
                          principal: Principal = Depends(require(Perm.KNOWLEDGE_MANAGE))) -> dict[str, Any]:
    async with tenant_tx(principal.tenant_id) as conn:
        doc = await _doc(conn, doc_id)
        if doc["source_kind"] in MODEL_KINDS:
            raise TelaError(ErrorCode.CONFLICT, "Model definitions and chart patterns are approved with the model "
                            "itself.")
        status = doc["review_status"]
        allowed = {"approve": ("pending_review", "rejected", "retired"), "reject": ("pending_review",),
                   "retire": ("approved",)}[body.decision]
        if status not in allowed:
            raise TelaError(ErrorCode.CONFLICT, f"This document is {status.replace('_', ' ')}.", {"status": status})
        if body.decision == "approve":
            if doc["index_status"] != "indexed":
                raise TelaError(ErrorCode.CONFLICT, "Wait for indexing to finish before approving.",
                                {"index_status": doc["index_status"]})
            if doc["injection_flagged"] and not body.acknowledge_injection:
                raise TelaError(ErrorCode.CONFLICT, "This document contains text that looks like instructions to the "
                                "assistant. Read it, then confirm to approve it anyway.",
                                {"needs_acknowledgement": True, "scan_flags": doc["scan_flags"]})
        new = {"approve": "approved", "reject": "rejected", "retire": "retired"}[body.decision]
        await conn.execute(
            text("UPDATE rag.knowledge_documents SET review_status = :s, reviewed_by = :u, reviewed_at = now(), "
                 "review_note = :n, acknowledged_by = CASE WHEN :ack THEN CAST(:u AS uuid) ELSE acknowledged_by END "
                 "WHERE id = :id"),
            {"s": new, "u": principal.user_id, "n": body.note, "ack": bool(body.decision == "approve"
                                                                            and doc["injection_flagged"]),
             "id": doc["id"]})
        await audit.record(conn, tenant_id=principal.tenant_id, action=f"knowledge.{new}", object_type="document",
                           object_id=doc_id, outcome="success", actor_id=principal.user_id,
                           correlation_id=_rid(request),
                           severity="high" if doc["injection_flagged"] and new == "approved" else "normal",
                           details={"title": doc["title"], "injection_acknowledged": bool(
                               new == "approved" and doc["injection_flagged"]), "note": body.note})
        return _out(await _doc(conn, doc_id))


@router.post("/documents/{doc_id}/reindex", status_code=202)
async def reindex_document(doc_id: str, request: Request,
                           principal: Principal = Depends(require(Perm.KNOWLEDGE_MANAGE))) -> dict[str, Any]:
    async with tenant_tx(principal.tenant_id) as conn:
        doc = await _doc(conn, doc_id)
        if doc["index_status"] in ("queued", "indexing"):
            raise TelaError(ErrorCode.CONFLICT, "This document is already being indexed.")
        await conn.execute(text("UPDATE rag.knowledge_documents SET index_status = 'queued', index_error = NULL "
                                "WHERE id = :id"), {"id": doc["id"]})
        if doc["source_kind"] in MODEL_KINDS:
            await outbox.emit(conn, tenant_id=principal.tenant_id, topic="model.version_approved",
                              aggregate_id=str(doc["model_version_id"]), payload={"reindex": True})
        else:
            await outbox.emit(conn, tenant_id=principal.tenant_id, topic="knowledge.document_uploaded",
                              aggregate_id=doc_id, payload={"document_id": doc_id})
        await audit.record(conn, tenant_id=principal.tenant_id, action="knowledge.reindex_requested",
                           object_type="document", object_id=doc_id, outcome="success", actor_id=principal.user_id,
                           correlation_id=_rid(request))
    return {"status": "queued"}


@router.delete("/documents/{doc_id}", status_code=204)
async def delete_document(doc_id: str, request: Request,
                          principal: Principal = Depends(require(Perm.KNOWLEDGE_MANAGE))) -> Response:
    async with tenant_tx(principal.tenant_id) as conn:
        doc = await _doc(conn, doc_id)
        if doc["source_kind"] in MODEL_KINDS:
            raise TelaError(ErrorCode.CONFLICT, "Model definitions, chart patterns, dashboard rules and report "
                            "blueprints follow the model; they can't be deleted here.")
        await conn.execute(text("DELETE FROM rag.knowledge_documents WHERE id = :id"), {"id": doc["id"]})
        await audit.record(conn, tenant_id=principal.tenant_id, action="knowledge.deleted", object_type="document",
                           object_id=doc_id, outcome="success", actor_id=principal.user_id,
                           correlation_id=_rid(request), details={"title": doc["title"]})
    if doc["object_key"]:
        try:
            await (await _store(request)).delete(doc["object_key"])
        except TelaError:
            log.warning("knowledge.file_not_deleted", document_id=doc_id)
    return Response(status_code=204)


# --------------------------------------------------------------------------- API-051


class SearchIn(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    kinds: list[Literal["entity", "certified_measure", "glossary", "uploaded_doc", "chart_pattern",
                        "gold_summary", "dashboard_rule", "report_blueprint", "analysis_recipe"]] = Field(
        default_factory=list, max_length=9)
    k: int = Field(default=8, ge=1, le=20)
    connection_id: str | None = Field(default=None, max_length=64)


@router.post("/search")
async def search(body: SearchIn, request: Request,
                 principal: Principal = Depends(require(Perm.KNOWLEDGE_MANAGE))) -> dict[str, Any]:
    """Admin and debugging view of retrieval; agents call ``retrieve`` directly with the person's visibility."""
    async with tenant_tx(principal.tenant_id) as conn:
        res = await retrieve(conn, request.app.state.gateway, principal.tenant_id, body.query,
                             connection_id=body.connection_id, kinds=list(body.kinds) or None, k=body.k)
    return {"hits": [h.public() for h in res.hits], "limited_context": res.limited, "note": res.note,
            "reranked": res.reranked}


# --------------------------------------------------------------------------- worker-facing (internal)


class TenantIn(BaseModel):
    tenant_id: str = Field(max_length=64)


async def index_model_version(conn: AsyncConnection, gateway: Any, tenant_id: str, version_id: str, *,
                              packs_dir: str | None = None) -> dict[str, Any]:
    """(Re-)index one approved model version's definitions and its pack's chart patterns (US-104), dashboard rules
    and report blueprints (US-159), and retire older versions' (REQ-035)."""
    row = await fetch_one(conn, "SELECT v.id, v.status, v.version_no, v.approved_by, v.data_model_id, "
                                "m.connection_id, m.current_version_id FROM model.model_versions v JOIN "
                                "model.data_models m ON m.id = v.data_model_id WHERE v.id = CAST(:id AS uuid)",
                          {"id": version_id})
    if row is None or row["status"] == "draft":
        raise TelaError(ErrorCode.NOT_FOUND, "That model version is not approved.")
    snap = await load_snapshot(conn, tenant_id, version_id)
    chunks = model_chunks(snap)
    digest = hashlib.sha256(repr(sorted(c["content"] for c in chunks)).encode()).hexdigest()
    current = row["current_version_id"] is not None and str(row["current_version_id"]) == version_id
    doc = await fetch_one(conn, "SELECT id FROM rag.knowledge_documents WHERE model_version_id = :v AND "
                                "source_kind = 'model_definition'", {"v": row["id"]})
    doc_id = str(doc["id"]) if doc else new_id()
    status = "approved" if current else "retired"
    if doc is None:
        await conn.execute(
            text("INSERT INTO rag.knowledge_documents (id, tenant_id, connection_id, model_version_id, source_kind, "
                 "title, content_hash, review_status, index_status, reviewed_by, reviewed_at) VALUES (:id, :t, :c, "
                 ":v, 'model_definition', :title, :h, :s, 'indexing', :by, now())"),
            {"id": doc_id, "t": tenant_id, "c": row["connection_id"], "v": row["id"],
             "title": f"Model definitions v{row['version_no']}", "h": digest, "s": status, "by": row["approved_by"]})
    stored, report, model = await replace_chunks(conn, gateway, tenant_id, doc_id, chunks)
    await conn.execute(
        text("UPDATE rag.knowledge_documents SET content_hash = :h, review_status = :s, index_status = 'indexed', "
             "index_error = NULL, scan_flags = CAST(:f AS jsonb), chunk_count = :n, embedding_model = :m, "
             "indexed_at = now() WHERE id = :id"),
        {"h": digest, "s": status, "f": json.dumps(report.as_json()), "n": stored, "m": model,
         "id": doc_id})
    patterns = await index_patterns(conn, gateway, tenant_id, row, snap, status=status, packs_dir=packs_dir)
    guidance = await index_guidance(conn, gateway, tenant_id, row, snap, status=status, packs_dir=packs_dir)
    glossary = await index_glossary(conn, gateway, tenant_id, row, snap, current=current, packs_dir=packs_dir)
    carried = await definitions.carry_forward(conn, tenant_id, connection_id=str(row["connection_id"]),
                                              version_id=version_id, snap=snap) if current else 0
    if current:  # US-252: the named conditions follow the definitions, and the measures' own are read
        await conditions.carry_forward(conn, tenant_id, connection_id=str(row["connection_id"]),
                                       version_id=version_id, snap=snap)
    retired = await fetch_all(
        conn, "UPDATE rag.knowledge_documents d SET review_status = 'retired' FROM model.model_versions v "
              "WHERE d.model_version_id = v.id AND v.data_model_id = :dm AND d.source_kind IN ('model_definition', "
              "'chart_pattern', 'dashboard_rule', 'report_blueprint', 'analysis_recipe') "
              "AND d.model_version_id <> CAST(:cur AS uuid) AND d.review_status = 'approved' RETURNING d.id",
        {"dm": row["data_model_id"], "cur": str(row["current_version_id"] or version_id)})
    await audit.record(conn, tenant_id=tenant_id, action="knowledge.indexed", object_type="model_version",
                       object_id=version_id, outcome="success", actor_id=None, correlation_id=None,
                       details={"chunks": stored, "chart_patterns": patterns, **guidance, "glossary": glossary,
                                "definitions_carried": carried, "embedding_model": model,
                                "retired_documents": len(retired),
                                "limited": model is None})
    return {"document_id": doc_id, "chunks": stored, "chart_patterns": patterns, **guidance, "glossary": glossary,
            "definitions_carried": carried, "embedding_model": model,
            "current": current,
            "connection_id": str(row["connection_id"]), "approved_by": str(row["approved_by"])
            if row["approved_by"] else None}


@internal.post("/model-versions/{version_id}/index")
async def index_model(version_id: str, body: TenantIn, request: Request,
                      _: dict[str, Any] = Depends(require_worker)) -> dict[str, Any]:
    from tela_api.knowledge.gold import queue_run

    async with tenant_tx(body.tenant_id) as conn:
        out = await index_model_version(conn, request.app.state.gateway, body.tenant_id, version_id,
                                        packs_dir=request.app.state.settings.packs_dir)
        # Older chunks (uploaded documents, earlier versions) get the contextual header and new vectors (US-174).
        out["recontextualised"] = await recontextualise(conn, request.app.state.gateway, body.tenant_id)
        if out["current"] and out["approved_by"]:
            out["eval_run_id"] = await queue_run(conn, body.tenant_id, out["approved_by"], trigger="model_approval",
                                                 connection_id=out["connection_id"])
    return out


@internal.post("/documents/{doc_id}/start")
async def start_document(doc_id: str, body: TenantIn, _: dict[str, Any] = Depends(require_worker)) -> dict[str, Any]:
    async with tenant_tx(body.tenant_id) as conn:
        doc = await _doc(conn, doc_id)
        if doc["source_kind"] in MODEL_KINDS or not doc["object_key"]:
            raise TelaError(ErrorCode.CONFLICT, "Nothing to extract for this document.")
        await conn.execute(text("UPDATE rag.knowledge_documents SET index_status = 'indexing', index_error = NULL "
                                "WHERE id = :id"), {"id": doc["id"]})
    return {"content_type": doc["content_type"], "file_name": doc["original_filename"]}


@internal.get("/documents/{doc_id}/file")
async def document_file(doc_id: str, request: Request, tenant_id: str = Query(max_length=64),
                        _: dict[str, Any] = Depends(require_worker)) -> Response:
    async with tenant_tx(tenant_id) as conn:
        doc = await _doc(conn, doc_id)
    if not doc["object_key"]:
        raise TelaError(ErrorCode.NOT_FOUND, "That document has no file.")
    data = await (await _store(request)).get(doc["object_key"])
    return Response(content=data, media_type=doc["content_type"] or "application/octet-stream")


class ChunkIn(BaseModel):
    content: str = Field(max_length=4000)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ChunksIn(TenantIn):
    extractor: str = Field(max_length=40)
    chunks: list[ChunkIn] = Field(max_length=2000)


@internal.post("/documents/{doc_id}/chunks")
async def put_chunks(doc_id: str, body: ChunksIn, request: Request,
                     _: dict[str, Any] = Depends(require_worker)) -> dict[str, Any]:
    async with tenant_tx(body.tenant_id) as conn:
        doc = await _doc(conn, doc_id)
        if doc["index_status"] != "indexing":
            raise TelaError(ErrorCode.CONFLICT, "This document is not being indexed.")
        stored, report, model = await replace_chunks(
            conn, request.app.state.gateway, body.tenant_id, doc_id,
            [{"content": c.content, "metadata": {k: v for k, v in c.metadata.items()
                                                  if k in ("page", "heading", "row", "sheet")}}
             for c in body.chunks])
        status = "indexed" if stored else "failed"
        await conn.execute(
            text("UPDATE rag.knowledge_documents SET index_status = :st, index_error = :e, scan_flags = CAST(:f AS "
                 "jsonb), injection_flagged = :inj, chunk_count = :n, embedding_model = :m, indexed_at = now() "
                 "WHERE id = :id"),
            {"st": status, "e": None if stored else FAIL_MESSAGES["all_dropped" if report.dropped else "no_text"],
             "f": json.dumps({**report.as_json(), "extractor": body.extractor}), "inj": report.injection,
             "n": stored, "m": model, "id": doc["id"]})
        found = await definitions.propose(conn, body.tenant_id, doc) if stored else 0
        await audit.record(conn, tenant_id=body.tenant_id, action="knowledge.indexed", object_type="document",
                           object_id=doc_id, outcome="success" if stored else "failure", actor_id=None,
                           correlation_id=_rid(request), severity="high" if report.injection else "normal",
                           details={"chunks": stored, **report.as_json(), "embedding_model": model,
                                    "definitions": found})
        if doc["created_by"]:
            body_text = (f"“{doc['title']}” is indexed ({stored} passages) and waiting for review."
                         if stored else f"“{doc['title']}” couldn't be indexed.")
            if found:
                body_text += (f" It defines {found} business {'term' if found == 1 else 'terms'}, proposed as "
                              "definitions for review.")
            if report.injection:
                body_text += " Some passages look like instructions to the assistant; review them before approving."
            await notify(conn, body.tenant_id, [str(doc["created_by"])], kind="knowledge.indexed",
                         title="Document ready for review" if stored else "Document not indexed", body=body_text,
                         link="/admin/knowledge")
    return {"status": status, "chunks": stored, "injection_flagged": report.injection, "definitions": found}


class DocFailIn(TenantIn):
    error_code: str = Field(default="index_failed", max_length=60)


@internal.post("/documents/{doc_id}/fail")
async def fail_document(doc_id: str, body: DocFailIn, request: Request,
                        _: dict[str, Any] = Depends(require_worker)) -> dict[str, Any]:
    code = body.error_code if body.error_code in FAIL_MESSAGES else "index_failed"
    async with tenant_tx(body.tenant_id) as conn:
        doc = await _doc(conn, doc_id)
        await conn.execute(text("UPDATE rag.knowledge_documents SET index_status = 'failed', index_error = :e "
                                "WHERE id = :id"), {"e": FAIL_MESSAGES[code], "id": doc["id"]})
        await audit.record(conn, tenant_id=body.tenant_id, action="knowledge.indexed", object_type="document",
                           object_id=doc_id, outcome="failure", actor_id=None, correlation_id=_rid(request),
                           details={"reason": code})
        if doc["created_by"]:
            await notify(conn, body.tenant_id, [str(doc["created_by"])], kind="knowledge.indexed",
                         title="Document not indexed", body=f"“{doc['title']}”: {FAIL_MESSAGES[code]}",
                         link="/admin/knowledge")
    return {"status": "failed", "error_code": code}


@internal.post("/reembed")
async def reembed(request: Request, _: dict[str, Any] = Depends(require_worker)) -> dict[str, int]:
    gateway = request.app.state.gateway
    if gateway is None:
        return {"tenants": 0, "embedded": 0}
    async with tenant_tx(None) as conn:
        tenants = [str(r["tenant_id"]) for r in await fetch_all(conn, "SELECT tenant_id FROM rag.f_reembed_tenants()")]
    done = 0
    for t in tenants:
        async with tenant_tx(t) as conn:
            done += await reembed_missing(conn, gateway, t)
    return {"tenants": len(tenants), "embedded": done}
