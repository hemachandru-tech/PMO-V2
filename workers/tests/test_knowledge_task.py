"""Knowledge indexing in the worker (US-100): extraction per type, chunking, the API round trip, outbox routing."""

from __future__ import annotations

import io
from typing import Any

import pytest

from tela_worker.api import ApiRefused
from tela_worker.knowledge import ExtractionError, extract_chunks, run_index
from tela_worker.tasks import outbox

DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def _pdf(text: str) -> bytes:
    """A one-page PDF with a real text layer (hand-built: pypdf reads but does not typeset)."""
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
            b"/Resources << /Font << /F1 5 0 R >> >> >>",
            b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    out, offsets = io.BytesIO(), []
    out.write(b"%PDF-1.4\n")
    for i, body in enumerate(objs, start=1):
        offsets.append(out.tell())
        out.write(b"%d 0 obj\n" % i + body + b"\nendobj\n")
    xref = out.tell()
    out.write(b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1))
    for off in offsets:
        out.write(b"%010d 00000 n \n" % off)
    out.write(b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref))
    return out.getvalue()


def _docx() -> bytes:
    import docx

    d = docx.Document()
    d.add_heading("Go-live", level=1)
    d.add_paragraph("A go-live is the production deployment of a project.")
    table = d.add_table(rows=1, cols=2)
    table.rows[0].cells[0].text = "EAC"
    table.rows[0].cells[1].text = "Estimate at completion"
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


def test_extracts_markdown_with_headings() -> None:
    name, chunks = extract_chunks(b"# Glossary\n\nOpen projects are the active book of work.\n", "text/markdown")
    assert name == "text" and chunks[0]["metadata"] == {"heading": "Glossary"}
    assert chunks[0]["content"].startswith("Glossary\nOpen projects")


def test_extracts_pdf_pages() -> None:
    name, chunks = extract_chunks(_pdf("Forecast cost is not actual spend."), "application/pdf")
    assert name == "pypdf" and chunks[0]["metadata"]["page"] == 1
    assert "Forecast cost is not actual spend." in chunks[0]["content"]


def test_extracts_docx_headings_and_tables() -> None:
    name, chunks = extract_chunks(_docx(), DOCX)
    text = "\n".join(c["content"] for c in chunks)
    assert name == "python-docx" and "production deployment" in text and "EAC | Estimate at completion" in text
    assert chunks[0]["metadata"]["heading"] == "Go-live"


@pytest.mark.parametrize(("data", "ctype", "code"), [
    (b"%PDF-1.4\n%%EOF", "application/pdf", "unreadable_document"),
    (b"PK\x03\x04broken", DOCX, "unreadable_document"),
    (b"\xff\xfe\x00bad", "text/plain", "unreadable_document"),
    (b"   \n\n ", "text/markdown", "no_text"),
    (b"x", "image/png", "unsupported_type"),
])
def test_extraction_failures_are_typed(data: bytes, ctype: str, code: str) -> None:
    with pytest.raises(ExtractionError) as exc:
        extract_chunks(data, ctype)
    assert exc.value.code == code


class FakeApi:
    def __init__(self, data: bytes, content_type: str = "text/markdown", refuse: str | None = None) -> None:
        self.data, self.content_type, self.refuse = data, content_type, refuse
        self.calls: list[tuple[str, Any]] = []

    def post(self, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        self.calls.append((path, body))
        if self.refuse and path.endswith(self.refuse):
            raise ApiRefused(409, "conflict")
        if path.endswith("/start"):
            return {"content_type": self.content_type, "file_name": "doc"}
        if path.endswith("/chunks"):
            return {"status": "indexed", "chunks": len((body or {})["chunks"])}
        return {"status": "ok", "chunks": 3}

    def get_file(self, path: str, params: dict[str, Any]) -> bytes:
        self.calls.append((path, params))
        return self.data

    def close(self) -> None:
        pass


def test_document_round_trip() -> None:
    api = FakeApi(b"# A\n\nSome text.")
    assert run_index("t1", "document", "d1", api) == "done"  # type: ignore[arg-type]
    paths = [p for p, _ in api.calls]
    base = "/api/v1/internal/knowledge/documents/d1"
    assert paths == [f"{base}/start", f"{base}/file", f"{base}/chunks"]
    body = api.calls[-1][1]
    assert body["tenant_id"] == "t1" and body["extractor"] == "text" and body["chunks"][0]["content"]


def test_extraction_failure_is_reported() -> None:
    api = FakeApi(b"not a pdf", content_type="application/pdf")
    assert run_index("t1", "document", "d1", api) == "failed"  # type: ignore[arg-type]
    assert api.calls[-1] == ("/api/v1/internal/knowledge/documents/d1/fail",
                             {"tenant_id": "t1", "error_code": "unreadable_document"})


def test_refusal_is_final_and_model_versions_index_in_the_api() -> None:
    assert run_index("t1", "document", "d1", FakeApi(b"x", refuse="/start")) == "refused"  # type: ignore[arg-type]
    api = FakeApi(b"")
    assert run_index("t1", "model_version", "v1", api) == "done"  # type: ignore[arg-type]
    assert api.calls == [("/api/v1/internal/knowledge/model-versions/v1/index", {"tenant_id": "t1"})]


def test_outbox_routes_knowledge_events(monkeypatch: pytest.MonkeyPatch) -> None:
    sent: list[tuple[str, list[str], str]] = []
    monkeypatch.setattr(outbox.app, "send_task", lambda name, args, queue: sent.append((name, args, queue)))
    outbox.HANDLERS["model.version_approved"]({"tenant_id": "t1", "aggregate_id": "v1", "payload": {}})
    outbox.HANDLERS["knowledge.document_uploaded"]({"tenant_id": "t1", "payload": {"document_id": "d1"}})
    outbox.HANDLERS["knowledge.evaluation_requested"]({"tenant_id": "t1", "payload": {"run_id": "r1"}})
    assert sent == [("tela_worker.tasks.knowledge.index_knowledge", ["t1", "model_version", "v1"], "index"),
                    ("tela_worker.tasks.knowledge.refresh_values", ["t1", "v1"], "index"),  # US-173
                    ("tela_worker.tasks.knowledge.index_knowledge", ["t1", "document", "d1"], "index"),
                    ("tela_worker.tasks.knowledge.evaluate_gold_set", ["t1", "r1"], "index")]
    # Events the API handles itself no longer dead-letter (EXEC-016 follow-up).
    for topic in ("dashboard.published", "certification.decided", "connection.changed"):
        outbox.HANDLERS[topic]({"tenant_id": "t1", "topic": topic, "payload": {}})


def test_value_index_tasks_call_the_api(monkeypatch: pytest.MonkeyPatch) -> None:
    """US-173: the approval event's refresh and the nightly one go through the internal API; a refusal is final."""
    from tela_worker.tasks import knowledge as tasks

    made: list[FakeApi] = []

    def fake(refuse: str | None = None) -> FakeApi:
        api = FakeApi(b"", refuse=refuse)
        made.append(api)
        return api

    monkeypatch.setattr(tasks, "InternalApi", fake)
    tasks.refresh_values("t1", "v1")
    tasks.refresh_values_nightly()
    assert [api.calls for api in made] == [
        [("/api/v1/internal/knowledge/model-versions/v1/values", {"tenant_id": "t1"})],
        [("/api/v1/internal/knowledge/values/nightly", None)]]
    monkeypatch.setattr(tasks, "InternalApi", lambda: fake(refuse="/values"))
    assert tasks.refresh_values("t1", "v1") == {"status": "refused"}
