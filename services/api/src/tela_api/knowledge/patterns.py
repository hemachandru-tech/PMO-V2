"""Chart patterns as knowledge (US-104, FEAT-016; technical_document §knowledge ``chart_pattern``).

A pack can ship ``chart_patterns.yaml``: house chart conventions ("forecast is not spend", "keep lifecycle phases
in order") and, for some, a typed suggestion with the shape it applies to. Each approved model version of a
connection built from that pack gets one ``chart_pattern`` document, indexed beside the model definitions and
retired with them, so AG-07 retrieves the conventions of the version it advises on. Patterns name measures and
dimensions only, never values. Only patterns whose measures and dimensions exist in the model are kept.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from tela_api.core.db import fetch_one
from tela_api.knowledge.index import replace_chunks
from tela_api.packs.source import PackSource, installed_source, resolve
from tela_common.ids import new_id
from tela_query_spec.model import ModelSnapshot

SUGGEST_OPS = {"add_measure", "add_breakdown", "set_visual", "set_limit", "compare_previous", "sort"}


class PatternWhen(BaseModel):
    model_config = ConfigDict(extra="forbid")

    measure: list[str] = Field(default_factory=list)
    breakdown: list[str] = Field(default_factory=list)
    also_breakdown: list[str] = Field(default_factory=list)  # and one of these is a breakdown too (v0.2)
    no_breakdown: bool = False
    no_measure: list[str] = Field(default_factory=list)


class PatternSuggest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ops: list[dict[str, Any]] = Field(min_length=1, max_length=3)
    visual: str | None = None


class ChartPattern(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[a-z][a-z0-9_]{1,60}$")
    title: str = Field(max_length=120)
    guidance: str = Field(max_length=1200)
    reason: str | None = Field(default=None, max_length=200)
    when: PatternWhen | None = None
    suggest: PatternSuggest | None = None

    def refs(self) -> tuple[set[str], set[str]]:
        """(measure keys, dimension keys) this pattern names."""
        measures = set(self.when.measure + self.when.no_measure) if self.when else set()
        dims = set(self.when.breakdown + self.when.also_breakdown) if self.when else set()
        for op in self.suggest.ops if self.suggest else []:
            ref = str(op.get("ref") or "")
            if ref.startswith("measure:"):
                measures.add(ref.split(":", 1)[1])
            elif ref.startswith("dimension:"):
                dims.add(ref.split(":", 1)[1])
        return measures, dims


def load_patterns(pack: str | PackSource | None, packs_dir: str | Path | None = None) -> list[ChartPattern]:
    """The pack's chart patterns (a pack key reads its folder; a source reads where it is kept, US-230); an unknown
    pack or a pack without the document has none."""
    src = resolve(pack, packs_dir)
    data = src.document("chart_patterns") if src else None
    if data is None:
        return []
    patterns = [ChartPattern.model_validate(p) for p in data.get("patterns") or []]
    for p in patterns:
        bad = [o.get("op") for o in (p.suggest.ops if p.suggest else []) if o.get("op") not in SUGGEST_OPS]
        if bad:
            raise ValueError(f"chart pattern {p.id}: unknown ops {bad}")
    return patterns


def for_model(patterns: list[ChartPattern], snap: ModelSnapshot) -> list[ChartPattern]:
    """Patterns whose measures and dimensions all exist in this model version."""
    measures = {m.key for m in snap.measures if m.status == "certified"}
    dims = {d.key for d in snap.dimensions}
    return [p for p in patterns if p.refs()[0] <= measures and p.refs()[1] <= dims]


def pattern_chunks(patterns: list[ChartPattern], snap: ModelSnapshot) -> list[dict[str, Any]]:
    """One chunk per pattern; metadata names the measures so retrieval hides patterns about measures a person
    can't see (``Visibility``)."""
    labels = {f"measure:{m.key}": m.label for m in snap.measures} | {f"dimension:{d.key}": d.label
                                                                     for d in snap.dimensions}
    chunks = []
    for p in patterns:
        content = f"Chart pattern: {p.title}\n{p.guidance}"
        if p.suggest:
            names = [labels.get(str(o.get("ref")), str(o.get("value") or o.get("op"))) for o in p.suggest.ops]
            content += f"\nSuggested change: {', '.join(names)}"
            if p.suggest.visual:
                content += f" (shown as {p.suggest.visual.replace('_', ' ')})"
        measures, dims = p.refs()
        meta: dict[str, Any] = {"kind": "chart_pattern", "pattern": p.id, "term": p.title, "measures": sorted(measures),
                                "dimensions": sorted(dims)}
        if len(measures) == 1:
            meta["measure"] = next(iter(measures))
        chunks.append({"content": content[:1900], "metadata": meta})
    return chunks


async def pack_key_for(conn: AsyncConnection, data_model_id: str) -> str | None:
    row = await fetch_one(conn, "SELECT p.pack_key FROM model.data_models m JOIN model.starter_packs p ON "
                                "p.id = m.installed_pack_id WHERE m.id = :dm", {"dm": data_model_id})
    return str(row["pack_key"]) if row else None


async def pack_source_for(conn: AsyncConnection, data_model_id: str,
                          packs_dir: str | Path | None = None) -> PackSource | None:
    """The stored pack version a data model was built from (US-231: knowledge reads the database copy)."""
    return await installed_source(conn, data_model_id, packs_dir=packs_dir)


async def index_pack_document(conn: AsyncConnection, gateway: Any, tenant_id: str, version: dict[str, Any], *,
                              kind: str, title: str, chunks: list[dict[str, Any]], status: str) -> int:
    """(Re-)index one pack knowledge document (``chart_pattern``, ``dashboard_rule``, ``report_blueprint``,
    ``analysis_recipe``) of a model
    version; no chunks retires it. Returns the number of chunks stored."""
    doc = await fetch_one(conn, "SELECT id FROM rag.knowledge_documents WHERE model_version_id = :v AND "
                                "source_kind = :k", {"v": version["id"], "k": kind})
    if not chunks:
        if doc is not None:
            await conn.execute(text("UPDATE rag.knowledge_documents SET review_status = 'retired' WHERE id = :id"),
                               {"id": doc["id"]})
        return 0
    digest = hashlib.sha256(repr(sorted(c["content"] for c in chunks)).encode()).hexdigest()
    doc_id = str(doc["id"]) if doc else new_id()
    if doc is None:
        await conn.execute(
            text("INSERT INTO rag.knowledge_documents (id, tenant_id, connection_id, model_version_id, source_kind, "
                 "title, content_hash, review_status, index_status, reviewed_by, reviewed_at) VALUES (:id, :t, :c, "
                 ":v, :k, :title, :h, :s, 'indexing', :by, now())"),
            {"id": doc_id, "t": tenant_id, "c": version["connection_id"], "v": version["id"], "k": kind,
             "title": f"{title} v{version['version_no']}", "h": digest, "s": status, "by": version["approved_by"]})
    stored, report, model = await replace_chunks(conn, gateway, tenant_id, doc_id, chunks)
    await conn.execute(
        text("UPDATE rag.knowledge_documents SET content_hash = :h, review_status = :s, index_status = 'indexed', "
             "index_error = NULL, scan_flags = CAST(:f AS jsonb), chunk_count = :n, embedding_model = :m, "
             "indexed_at = now() WHERE id = :id"),
        {"h": digest, "s": status, "f": json.dumps(report.as_json()), "n": stored, "m": model, "id": doc_id})
    return stored


async def index_patterns(conn: AsyncConnection, gateway: Any, tenant_id: str, version: dict[str, Any],
                         snap: ModelSnapshot, *, status: str, packs_dir: str | None = None) -> int:
    """(Re-)index the chart patterns of one model version; approved with the model, retired with older versions.

    ``version`` is the model_versions row joined with its data model (``id``, ``data_model_id``,
    ``connection_id``, ``version_no``, ``approved_by``). Returns the number of chunks stored.
    """
    pack = await pack_source_for(conn, version["data_model_id"], packs_dir)
    patterns = for_model(load_patterns(pack), snap) if pack else []
    return await index_pack_document(conn, gateway, tenant_id, version, kind="chart_pattern", title="Chart patterns",
                                     chunks=pattern_chunks(patterns, snap) if patterns else [], status=status)
