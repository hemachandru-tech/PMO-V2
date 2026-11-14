"""Store knowledge chunks (US-100): scan, redact, embed through the gateway, upsert.

Per chunk, before anything leaves the platform:

* secrets (keys, tokens, connection strings) → the chunk is dropped and only the class is recorded;
* e-mail addresses, phone numbers and card numbers → replaced with placeholders;
* injection markers → kept as quoted data, never embedded (the gateway refuses them), and the document is
  flagged so an Admin must acknowledge it before approval (FEAT-014 A1, TEST-093).

Chunks are embedded in batches; when the gateway is off or fails, they are stored without an embedding and
full-text retrieval still finds them. The re-embed sweep fills them in later.

Every chunk gets a contextual header line (US-174), e.g. "Certified measure · Projects · Forecast cost (EAC) — Model
definitions v3", kept in ``metadata.context`` and embedded (and reranked) in front of the text; the stored content and
its citations stay as they were. ``metadata.term`` (the title or term, weight A in the full-text vector) and
``metadata.synonyms`` (weight B) feed lexical ranking. Chunks indexed before the header existed are brought up to
date and re-embedded on the next model approval (``recontextualise``).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from tela_api.core.db import fetch_all
from tela_api.llm.client import GatewayClient
from tela_common.errors import TelaError
from tela_common.ids import new_id
from tela_common.logging import get_logger
from tela_common.scan import PII_CLASSES, redact_cards, redact_pii, scan_text

log = get_logger(__name__)

EMBED_BATCH = 64
CONTEXT_VERSION = 1
KIND_LABELS = {"entity": "Entity", "certified_measure": "Certified measure", "glossary": "Glossary",
               "uploaded_doc": "Document", "chart_pattern": "Chart pattern", "gold_summary": "Answered question",
               "dashboard_rule": "Dashboard rule", "report_blueprint": "Report blueprint",
               "analysis_recipe": "Analysis recipe",
               "model_definition": "Model definition"}
DROP_CLASSES = frozenset({"anthropic_key", "openai_key", "google_key", "aws_key", "connection_string",
                          "private_key", "jwt"})


@dataclass
class Prepared:
    content: str
    metadata: dict[str, Any]
    embeddable: bool


@dataclass
class ScanReport:
    dropped: int = 0
    redacted: dict[str, int] = field(default_factory=dict)
    injection_chunks: list[int] = field(default_factory=list)
    leak_classes: list[str] = field(default_factory=list)

    @property
    def injection(self) -> bool:
        return bool(self.injection_chunks)

    def as_json(self) -> dict[str, Any]:
        return {"dropped": self.dropped, "redacted": self.redacted, "injection_chunks": self.injection_chunks,
                "leak_classes": sorted(set(self.leak_classes))}


def prepare(chunks: list[dict[str, Any]]) -> tuple[list[Prepared], ScanReport]:
    report = ScanReport()
    out: list[Prepared] = []
    for raw in chunks:
        content = str(raw.get("content", ""))[:2000].strip()
        if not content:
            continue
        reasons = scan_text(content).reasons
        leaks = [r for r in reasons if r in DROP_CLASSES]
        if leaks:
            report.dropped += 1
            report.leak_classes.extend(leaks)
            continue
        if "payment_card" in reasons:
            content, n = redact_cards(content)
            report.redacted["payment_card"] = report.redacted.get("payment_card", 0) + n
        if any(r in PII_CLASSES for r in reasons):
            content, found = redact_pii(content)
            for f in found:
                report.redacted[f] = report.redacted.get(f, 0) + 1
        injected = "injection_marker" in reasons
        meta = dict(raw.get("metadata") or {})
        if injected:
            report.injection_chunks.append(len(out))
            meta["injection_flagged"] = True
        out.append(Prepared(content, meta, embeddable=not injected))
    return out, report


def context_line(kind: str, meta: dict[str, Any], doc_title: str | None) -> str:
    """The chunk's header: kind · entity · term — document title (repeats left out); the chunk's own kind wins
    over the document's."""
    kind = str(meta.get("kind") or kind)
    parts = [KIND_LABELS.get(kind, kind.replace("_", " ").capitalize())]
    for part in (meta.get("entity_label") or meta.get("entity"), meta.get("term")):
        if part and str(part) not in parts:
            parts.append(str(part))
    line = " · ".join(parts)
    return f"{line} — {doc_title}" if doc_title and doc_title not in parts else line


def contextualise(meta: dict[str, Any], content: str, *, kind: str, doc_title: str | None) -> dict[str, Any]:
    """``meta`` with ``term`` (its own, the passage heading or the document title) and the header line."""
    out = dict(meta)
    out["term"] = str(out.get("term") or out.get("heading") or doc_title or content.split("\n", 1)[0])[:200]
    out["context"] = context_line(kind, out, doc_title)[:400]
    out["ctx"] = CONTEXT_VERSION
    return out


def embed_text(content: str, meta: dict[str, Any]) -> str:
    """What is embedded and reranked: the header line, then the text."""
    return f"{meta['context']}\n{content}" if meta.get("context") else content


async def embed(gateway: GatewayClient | None, tenant_id: str, texts: list[str]) -> tuple[list[list[float]] | None,
                                                                                         str | None]:
    """Vectors for ``texts`` (one gateway call per batch), or (None, None) when embedding is unavailable."""
    if gateway is None or not texts:
        return None, None
    vectors: list[list[float]] = []
    model = None
    try:
        for i in range(0, len(texts), EMBED_BATCH):
            out = await gateway.embed(tenant_id=tenant_id, texts=texts[i:i + EMBED_BATCH])
            vectors.extend(out["vectors"])
            model = out.get("model")
    except TelaError as exc:
        log.info("knowledge.embed_unavailable", code=str(exc.code))
        return None, None
    return vectors, model


def vector_literal(vec: list[float] | None) -> str | None:
    return json.dumps(vec) if vec is not None else None


async def replace_chunks(conn: AsyncConnection, gateway: GatewayClient | None, tenant_id: str, document_id: str,
                         chunks: list[dict[str, Any]]) -> tuple[int, ScanReport, str | None]:
    """Replace a document's chunks; returns (stored, scan report, embedding model or None)."""
    prepared, report = prepare(chunks)
    doc = await fetch_all(conn, "SELECT title, source_kind FROM rag.knowledge_documents WHERE id = :d",
                          {"d": document_id})
    title, kind = (doc[0]["title"], doc[0]["source_kind"]) if doc else (None, "uploaded_doc")
    for p in prepared:
        p.metadata = contextualise(p.metadata, p.content, kind=kind, doc_title=title)
    to_embed = [embed_text(p.content, p.metadata) for p in prepared if p.embeddable]
    vectors, model = await embed(gateway, tenant_id, to_embed)
    it = iter(vectors or [])
    await conn.execute(text("DELETE FROM rag.knowledge_chunks WHERE document_id = :d"), {"d": document_id})
    for no, p in enumerate(prepared):
        vec = next(it, None) if (p.embeddable and vectors is not None) else None
        await conn.execute(
            text("INSERT INTO rag.knowledge_chunks (id, tenant_id, document_id, chunk_no, content, metadata, "
                 "embedding) VALUES (:id, :t, :d, :n, :c, CAST(:m AS jsonb), CAST(:v AS vector))"),
            {"id": new_id(), "t": tenant_id, "d": document_id, "n": no, "c": p.content,
             "m": json.dumps(p.metadata, default=str), "v": vector_literal(vec)})
    return len(prepared), report, model


async def reembed_missing(conn: AsyncConnection, gateway: GatewayClient | None, tenant_id: str,
                          limit: int = 256) -> int:
    """Embed chunks and gold examples that were stored while the embedding service was down."""
    if gateway is None:
        return 0
    rows = await fetch_all(conn, "SELECT c.id, c.content, c.metadata FROM rag.knowledge_chunks c WHERE "
                                 "c.embedding IS NULL "
                                 "AND NOT coalesce((c.metadata->>'injection_flagged')::boolean, false) "
                                 "ORDER BY c.created_at LIMIT :n", {"n": limit})
    done = 0
    if rows:
        texts = [embed_text(r["content"], r["metadata"] or {}) for r in rows]
        vectors, model = await embed(gateway, tenant_id, texts)
        if vectors is not None:
            for r, v in zip(rows, vectors, strict=True):
                await conn.execute(text("UPDATE rag.knowledge_chunks SET embedding = CAST(:v AS vector) "
                                        "WHERE id = :id"), {"v": json.dumps(v), "id": r["id"]})
            await conn.execute(text("UPDATE rag.knowledge_documents d SET embedding_model = :m WHERE embedding_model "
                                    "IS NULL AND NOT EXISTS (SELECT 1 FROM rag.knowledge_chunks c WHERE c.document_id "
                                    "= d.id AND c.embedding IS NULL AND NOT coalesce((c.metadata->>"
                                    "'injection_flagged')::boolean, false))"), {"m": model})
            done += len(rows)
    golds = await fetch_all(conn, "SELECT id, question FROM rag.gold_examples WHERE embedding IS NULL AND status <> "
                                  "'rejected' ORDER BY created_at LIMIT :n", {"n": limit})
    golds = [g for g in golds if scan_text(g["question"]).clean]  # the gateway would refuse the whole batch
    if golds:
        vectors, model = await embed(gateway, tenant_id, [g["question"] for g in golds])
        if vectors is not None:
            for g, v in zip(golds, vectors, strict=True):
                await conn.execute(text("UPDATE rag.gold_examples SET embedding = CAST(:v AS vector), "
                                        "embedding_model = :m WHERE id = :id"),
                                   {"v": json.dumps(v), "m": model, "id": g["id"]})
            done += len(golds)
    return done


async def recontextualise(conn: AsyncConnection, gateway: GatewayClient | None, tenant_id: str,
                          limit: int = 2000) -> int:
    """Give chunks indexed before the current header version their term and header line, and re-embed them
    (US-174; run on model approval). Without an embedding service the vectors are cleared and the re-embed sweep
    fills them in later, so a chunk is never left with a vector that does not match its header."""
    rows = await fetch_all(conn, "SELECT c.id, c.content, c.metadata, d.title, d.source_kind FROM "
                                 "rag.knowledge_chunks c JOIN rag.knowledge_documents d ON d.id = c.document_id WHERE "
                                 "coalesce((c.metadata->>'ctx')::int, 0) < :v AND d.review_status <> 'retired' "
                                 "ORDER BY c.created_at LIMIT :n", {"v": CONTEXT_VERSION, "n": limit})
    if not rows:
        return 0
    metas = [contextualise(r["metadata"] or {}, r["content"], kind=r["source_kind"], doc_title=r["title"])
             for r in rows]
    embeddable = [i for i, m in enumerate(metas) if not m.get("injection_flagged")]
    vectors, _ = await embed(gateway, tenant_id, [embed_text(rows[i]["content"], metas[i]) for i in embeddable])
    vec = dict(zip(embeddable, vectors, strict=True)) if vectors is not None else {}
    for i, (r, m) in enumerate(zip(rows, metas, strict=True)):
        await conn.execute(text("UPDATE rag.knowledge_chunks SET metadata = CAST(:m AS jsonb), embedding = "
                                "CAST(:v AS vector) WHERE id = :id"),
                           {"m": json.dumps(m, default=str), "v": vector_literal(vec.get(i)), "id": r["id"]})
    return len(rows)
