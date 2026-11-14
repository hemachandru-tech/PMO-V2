"""The approved model as knowledge (US-100; diagrams/rag-indexing-flow.md).

One chunk per entity (with its fields, grain and the dimensions over it) and one per certified measure, so a
retrieved chunk never mixes two entities. Chunk metadata carries the keys retrieval filters on: ``entity``,
``fields`` (``entity.field``), ``measure`` and ``kind``; ``term``, ``entity_label`` and ``synonyms`` feed the
contextual header and lexical weights (US-174). Only definitions are indexed, never source rows or sample values.
"""

from __future__ import annotations

from typing import Any

from tela_common.chunking import MAX_CHARS
from tela_query_spec.expr import measure_refs
from tela_query_spec.model import ModelSnapshot


def _split(header: str, lines: list[str], limit: int = MAX_CHARS) -> list[str]:
    """``header`` then as many lines as fit; long entities continue in further chunks under the same header."""
    out: list[str] = []
    current = header
    for line in lines:
        line = line[: limit - len(header) - 2]
        if len(current) + 1 + len(line) > limit:
            out.append(current)
            current = f"{header} (continued)"
        current = f"{current}\n{line}"
    out.append(current)
    return out


def model_chunks(snap: ModelSnapshot) -> list[dict[str, Any]]:
    chunks: list[dict[str, Any]] = []
    for ent in snap.entities:
        dims = [d for d in snap.dimensions if d.entity == ent.key]
        header = f"Entity: {ent.label} ({ent.key})"
        if ent.grain:
            header += f"\nGrain: {ent.grain}"
        if ent.description:
            header += f"\n{ent.description}"
        lines = []
        for f in ent.fields:
            if not f.is_queryable:
                continue
            line = f"- {f.label} ({f.key}, {f.data_type})"
            if f.description:
                line += f": {f.description}"
            if f.blank_handling:
                line += f" Blank: {f.blank_handling}"
            lines.append(line)
        for d in dims:
            line = f"- Breakdown {d.label} (dimension {d.key})"
            if d.synonyms:
                line += f"; also called {', '.join(d.synonyms)}"
            if d.value_labels:
                line += "; shown as " + ", ".join(f"{k} → {v}" for k, v in d.value_labels.items())
            lines.append(line)
        links = [lk for lk in snap.links if lk.accepted and ent.key in (lk.from_entity, lk.to_entity)]
        for lk in links:
            other = lk.to_entity if lk.from_entity == ent.key else lk.from_entity
            lines.append(f"- Linked to {other} ({lk.cardinality.replace('_', ' ')})")
        meta = {"kind": "entity", "entity": ent.key, "entity_label": ent.label, "term": ent.label,
                "synonyms": [x for d in dims for x in (d.label, *d.synonyms)],
                "fields": [f"{ent.key}.{f.key}" for f in ent.fields if f.is_queryable],
                "dimensions": [d.key for d in dims]}
        for part in _split(header, lines):
            chunks.append({"content": part, "metadata": meta})
    entity_labels = {e.key: e.label for e in snap.entities}
    for m in snap.measures:
        if m.status != "certified":
            continue
        text = f"Certified measure: {m.label} (measure {m.key}) on {m.entity}\nRule: {m.plain_rule}"
        if m.blank_handling:
            text += f"\nBlank values: {m.blank_handling}"
        if m.synonyms:
            text += f"\nAlso called: {', '.join(m.synonyms)}"
        if m.rule_ref:
            text += f"\nReference: {m.rule_ref}"
        chunks.append({"content": text[:MAX_CHARS], "metadata": {
            "kind": "certified_measure", "entity": m.entity, "entity_label": entity_labels.get(m.entity, m.entity),
            "measure": m.key, "term": m.label, "synonyms": list(m.synonyms),
            "fields": sorted(measure_refs(m.entity, m.expression))}})
    return chunks
