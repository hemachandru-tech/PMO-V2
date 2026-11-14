"""Knowledge indexing (US-100; technical_document §Background jobs: ``index_knowledge``, ``index`` queue, 3 retries).

For an approved model version the API builds and embeds the definition chunks itself. For an uploaded document
the worker does the extraction: it fetches the file through the internal API, extracts text (Markdown/plain,
PDF with pypdf, DOCX with python-docx, a business-context XLSX row by row, US-251), normalises and chunks it, and posts the chunks back. The API scans each
chunk (leak, PII, injection), embeds the clean ones through the gateway and stores them; the document stays in
``pending_review`` until an Admin approves it. Nothing here logs document text.
"""

from __future__ import annotations

import io
from typing import Any

from tela_common.chunking import chunk_text, normalise
from tela_common.logging import get_logger
from tela_worker.api import ApiRefused, InternalApi

log = get_logger(__name__)

MAX_CHUNKS = 2000


class ExtractionError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _pdf(data: bytes) -> list[tuple[str, dict[str, Any]]]:
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            raise ExtractionError("encrypted_document")
        return [(page.extract_text() or "", {"page": n}) for n, page in enumerate(reader.pages, start=1)]
    except PdfReadError as exc:
        raise ExtractionError("unreadable_document") from exc


def _docx(data: bytes) -> list[tuple[str, dict[str, Any]]]:
    import zipfile

    import docx
    from docx.opc.exceptions import PackageNotFoundError

    try:
        document = docx.Document(io.BytesIO(data))
    except (PackageNotFoundError, zipfile.BadZipFile, KeyError, ValueError) as exc:
        raise ExtractionError("unreadable_document") from exc
    lines: list[str] = []
    for para in document.paragraphs:
        style = (para.style.name if para.style is not None else "") or ""
        if style.startswith("Heading") and para.text.strip():
            level = style.removeprefix("Heading").strip()
            lines.append(f"{'#' * (int(level) if level.isdigit() else 1)} {para.text.strip()}")
        else:
            lines.append(para.text)
    for table in document.tables:
        for row in table.rows:
            lines.append(" | ".join(cell.text.strip() for cell in row.cells))
        lines.append("")
    return [("\n".join(lines), {})]


def _text(data: bytes) -> list[tuple[str, dict[str, Any]]]:
    try:
        return [(data.decode("utf-8-sig"), {})]
    except UnicodeDecodeError as exc:
        raise ExtractionError("unreadable_document") from exc


SHEET = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
EXTRACTORS = {
    "application/pdf": ("pypdf", _pdf),
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ("python-docx", _docx),
    "text/markdown": ("text", _text),
    "text/plain": ("text", _text),
}


def _sheet(data: bytes) -> list[dict[str, Any]]:
    """A business-context sheet (US-251): one chunk per term, its cells kept for the definition proposals."""
    from tela_common import business_sheet

    try:
        rows = business_sheet.read(data)
    except business_sheet.SheetError as exc:
        raise ExtractionError(exc.code) from exc
    return [{"content": r.text(), "metadata": {"row": r.row, "sheet": r.cells()}} for r in rows]


def extract_chunks(data: bytes, content_type: str) -> tuple[str, list[dict[str, Any]]]:
    """Extract and chunk one document; returns the extractor name and the chunks as dicts."""
    if content_type == SHEET:
        return "openpyxl", _sheet(data)
    if content_type not in EXTRACTORS:
        raise ExtractionError("unsupported_type")
    name, extract = EXTRACTORS[content_type]
    chunks: list[dict[str, Any]] = []
    for text, meta in extract(data):
        for c in chunk_text(text, metadata=meta):
            chunks.append({"content": c.content, "metadata": c.metadata})
    if not chunks or not any(normalise(c["content"]) for c in chunks):
        raise ExtractionError("no_text")
    if len(chunks) > MAX_CHUNKS:
        raise ExtractionError("too_many_chunks")
    return name, chunks


def run_index(tenant_id: str, kind: str, ref_id: str, api: InternalApi | None = None) -> str:
    own = api is None
    api = api or InternalApi()
    base = "/api/v1/internal/knowledge"
    try:
        try:
            if kind == "model_version":
                out = api.post(f"{base}/model-versions/{ref_id}/index", {"tenant_id": tenant_id})
                log.info("knowledge.model_indexed", model_version_id=ref_id, chunks=out.get("chunks"))
                return "done"
            job = api.post(f"{base}/documents/{ref_id}/start", {"tenant_id": tenant_id})
            data = api.get_file(f"{base}/documents/{ref_id}/file", {"tenant_id": tenant_id})
            try:
                extractor, chunks = extract_chunks(data, job["content_type"])
            except ExtractionError as exc:
                log.info("knowledge.extract_failed", document_id=ref_id, code=exc.code)
                api.post(f"{base}/documents/{ref_id}/fail", {"tenant_id": tenant_id, "error_code": exc.code})
                return "failed"
            out = api.post(f"{base}/documents/{ref_id}/chunks",
                           {"tenant_id": tenant_id, "extractor": extractor, "chunks": chunks})
            log.info("knowledge.document_indexed", document_id=ref_id, chunks=out.get("chunks"))
            return "done"
        except ApiRefused as exc:
            log.info("knowledge.refused", kind=kind, ref_id=ref_id, code=exc.code)
            return "refused"
    finally:
        if own:
            api.close()
