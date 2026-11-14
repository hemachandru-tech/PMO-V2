"""Business knowledge editors of a pack (ENH-025, US-233; FEAT-075): the Pack Studio's **Knowledge** tab.

Six kinds, each one ``model.pack_documents`` row of a tenant draft: glossary, value synonyms, dashboard rules and
wording terms, chart patterns, analysis recipes and gold examples (report blueprints are US-234's tab). Each is
checked on the schema its loader reads it with (``validate.DOCUMENTS``: ``guidance.py``, ``patterns.py``,
``values.py``), and each item is shown with the measures, dimensions and fields it names, the missing ones flagged:
a rule naming a measure the pack lacks is saved but warned about (release, US-238, decides what blocks).

* ``GET /packs/{id}/knowledge`` — every kind's count, revision and problem counts, the pack's names for the forms and
  the attached context documents (``attachments.py``).
* ``GET /packs/{id}/knowledge/{kind}`` — the document, each item's references and problems; ``ETag`` is the kind's
  revision (its body's SHA-256), separate from the manifest's, so editing the glossary never clashes with a measure.
* ``PUT /packs/{id}/knowledge/{kind}`` — replace the document (``If-Match`` required, 412 when stale); schema errors
  refuse it (400) with each error's path, e.g. ``glossary.terms[3].means``; reference warnings are saved.
* ``POST /packs/{id}/knowledge/{kind}/check`` — the same checks on a document that is not saved (the side panel).

System packs and released versions answer 409 to writes (clone, or open the next draft). Every route needs
``packs.author``.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

from fastapi import APIRouter, Header, Request, Response
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from tela_api.audit import service as audit
from tela_api.core.db import fetch_all, fetch_one, tenant_tx
from tela_api.identity.principal import Principal
from tela_api.packs.source import document_sha
from tela_api.packs.studio import store
from tela_api.packs.studio.deps import AUTHOR, rid
from tela_api.packs.studio.validate import DOCUMENTS, Report
from tela_common.errors import ErrorCode, TelaError
from tela_common.ids import new_id

router = APIRouter(prefix="/api/v1/packs", tags=["packs"])

# The kinds this tab edits and the list each document keeps its items in.
LISTS: dict[str, str] = {"glossary": "terms", "value_synonyms": "values", "dashboard_rules": "rules",
                         "chart_patterns": "patterns", "analysis_recipes": "recipes", "gold_examples": "examples"}
KNOWLEDGE_KINDS = tuple(LISTS)
_REF = re.compile(r"^(measure|dimension):([a-z][a-z0-9_]*)$")


def empty(kind: str) -> dict[str, Any]:
    """A new document of a kind, as a hand-written file starts."""
    return {"version": "0.1.0", "status": "draft", LISTS[kind]: []}


def etag(body: dict[str, Any]) -> str:
    return f'"k{document_sha(body)[:40]}"'


# --------------------------------------------------------------------------- what each item names


@dataclass
class Names:
    """What a pack's knowledge may name: measure and dimension keys, each entity's field keys."""

    measures: dict[str, dict[str, Any]]
    dimensions: dict[str, dict[str, Any]]
    fields: dict[str, set[str]]

    @classmethod
    def of(cls, head: dict[str, Any]) -> Names:
        return cls({str(m.get("key")): m for m in head.get("measures") or [] if isinstance(m, dict)},
                   {str(d.get("key")): d for d in head.get("dimensions") or [] if isinstance(d, dict)},
                   {str(e.get("key")): {str(f.get("key")) for f in e.get("fields") or [] if isinstance(f, dict)}
                    for e in head.get("entities") or [] if isinstance(e, dict)})

    def out(self) -> dict[str, Any]:
        return {"measures": [{"key": k, "label": m.get("label") or k, "entity": m.get("entity")}
                             for k, m in self.measures.items()],
                "dimensions": [{"key": k, "label": d.get("label") or k, "entity": d.get("entity")}
                               for k, d in self.dimensions.items()],
                "fields": {e: sorted(fs) for e, fs in self.fields.items()}}


Ref = tuple[str, str, str]  # (kind of name: measure | dimension | field, the name, its path in the item)


def _strings(value: Any) -> list[str]:
    return [str(v) for v in value] if isinstance(value, list) else []


def _spec_refs(value: Any, path: str) -> Iterator[Ref]:
    """``{"ref": "measure:x"}`` anywhere in a Query Spec (gold examples, recipe specs)."""
    if isinstance(value, dict):
        for k, v in value.items():
            if k == "ref" and isinstance(v, str) and (m := _REF.match(v)):
                yield m.group(1), m.group(2), f"{path}.ref"
            else:
                yield from _spec_refs(v, f"{path}.{k}")
    elif isinstance(value, list):
        for i, v in enumerate(value):
            yield from _spec_refs(v, f"{path}[{i}]")


def item_refs(kind: str, item: Any) -> list[Ref]:
    """The measures, dimensions and fields one item names, with where (relative to the item)."""
    if not isinstance(item, dict):
        return []
    out: list[Ref] = []
    if kind == "glossary":
        out += [("field", f, f"fields[{j}]") for j, f in enumerate(_strings(item.get("fields")))]
        if item.get("reading"):
            out.append(("measure", str(item["reading"]), "reading"))
        out += [("measure", m, f"instead_of[{j}]") for j, m in enumerate(_strings(item.get("instead_of")))]
    elif kind == "value_synonyms":
        out += [("dimension", d, f"dimensions[{j}]") for j, d in enumerate(_strings(item.get("dimensions")))]
    elif kind == "dashboard_rules":
        out += [("measure", m, f"measures[{j}]") for j, m in enumerate(_strings(item.get("measures")))]
    elif kind == "chart_patterns":
        when: dict[str, Any] = item["when"] if isinstance(item.get("when"), dict) else {}
        for key, what in (("measure", "measure"), ("no_measure", "measure"), ("breakdown", "dimension"),
                          ("also_breakdown", "dimension")):
            out += [(what, v, f"when.{key}[{j}]") for j, v in enumerate(_strings(when.get(key)))]
        suggest: dict[str, Any] = item["suggest"] if isinstance(item.get("suggest"), dict) else {}
        for j, op in enumerate(suggest.get("ops") or []):
            if isinstance(op, dict) and isinstance(op.get("ref"), str) and (m := _REF.match(op["ref"])):
                out.append((m.group(1), m.group(2), f"suggest.ops[{j}].ref"))
    elif kind == "analysis_recipes":
        for name, slot in (item.get("slots") or {}).items() if isinstance(item.get("slots"), dict) else ():
            if isinstance(slot, dict) and slot.get("kind") in ("measure", "dimension"):
                out += [(str(slot["kind"]), o, f"slots.{name}.options[{j}]")
                        for j, o in enumerate(_strings(slot.get("options")))]
    elif kind == "gold_examples":
        out += list(_spec_refs(item.get("spec"), "spec"))
    return out


def _missing(names: Names, ref: Ref) -> str | None:
    what, name, _ = ref
    if what == "measure":
        return None if name in names.measures else f"There is no measure {name!r} in this pack."
    if what == "dimension":
        return None if name in names.dimensions else f"There is no dimension {name!r} in this pack."
    entity, _, field = name.partition(".")
    if entity not in names.fields:
        return f"There is no entity {entity!r} in this pack (write fields as entity.field)."
    return None if field in names.fields[entity] else f"{entity} has no field {field!r}."


def item_title(kind: str, item: Any) -> str:
    if not isinstance(item, dict):
        return "?"
    key = {"glossary": "term", "value_synonyms": "value", "gold_examples": "question"}.get(kind, "title")
    return str(item.get(key) or item.get("id") or "")[:160]


def check_document(kind: str, body: Any, head: dict[str, Any]) -> dict[str, Any]:
    """A document's schema errors and reference warnings (paths as in the file: ``glossary.terms[3].means``) and
    each item with what it names."""
    report = Report()
    if not isinstance(body, dict):
        report.error(kind, "The document must be a mapping.")
        body = {}
    else:
        DOCUMENTS[kind](report, body)
    names = Names.of(head)
    items_key = LISTS[kind]
    raw = body.get(items_key)
    items = raw if isinstance(raw, list) else []
    if raw is not None and not isinstance(raw, list):
        report.error(f"{kind}.{items_key}", "Write the items as a list.")
    out_items: list[dict[str, Any]] = []
    for i, item in enumerate(items):
        base = f"{kind}.{items_key}[{i}]"
        refs = item_refs(kind, item)
        for ref in refs:
            if (msg := _missing(names, ref)) is not None:
                report.warn(f"{base}.{ref[2]}", msg)
        grouped: dict[str, list[str]] = {"measures": [], "dimensions": [], "fields": []}
        for what, name, _ in refs:
            group = grouped[f"{what}s"]
            if name not in group:
                group.append(name)
        missing = sorted({f"{what}:{name}" for what, name, p in refs if _missing(names, (what, name, p))})
        out_items.append({"index": i, "title": item_title(kind, item), "refs": grouped, "missing": missing})
    by_item: dict[int, dict[str, list[dict[str, str]]]] = {}
    head_re = re.compile(rf"^{re.escape(kind)}\.{re.escape(items_key)}\[(\d+)\]")
    document_errors: list[dict[str, str]] = []
    for level, problems in (("errors", report.errors), ("warnings", report.warnings)):
        for p in problems:
            m = head_re.match(p["path"])
            if m and int(m.group(1)) < len(out_items):
                by_item.setdefault(int(m.group(1)), {"errors": [], "warnings": []})[level].append(p)
            elif level == "errors":
                document_errors.append(p)
    for it in out_items:
        it.update(by_item.get(it["index"], {"errors": [], "warnings": []}))
    return {**report.out(), "document_errors": document_errors, "items": out_items}


# --------------------------------------------------------------------------- reading and saving


def _kind(kind: str) -> str:
    if kind not in LISTS:
        raise TelaError(ErrorCode.NOT_FOUND, "Unknown knowledge kind. Report blueprints are on the Reports tab."
                        if kind == "report_blueprints" else f"Unknown knowledge kind {kind!r}.")
    return kind


async def pack_for(conn: AsyncConnection, pack_id: str, *, write: bool = False) -> store.PackRow:
    """The pack the tenant can see; for a write, a tenant draft locked until the transaction ends. The row is read
    and checked first: row-level security hides system packs and released versions from ``FOR UPDATE``, so locking
    first would answer 404 where 409 ("clone to edit") is right."""
    row = await store.load(conn, pack_id)
    if write:
        store.require_editable(row)
        locked = await fetch_one(conn, "SELECT status FROM model.starter_packs WHERE id = CAST(:id AS uuid) "
                                       "FOR UPDATE", {"id": row.id})
        if locked is None or locked["status"] != "draft":
            raise TelaError(ErrorCode.CONFLICT, "This pack version is no longer a draft: reload it.")
    return row


async def read_document(conn: AsyncConnection, pack_id: str, kind: str) -> tuple[dict[str, Any], bool]:
    row = await fetch_one(conn, "SELECT body FROM model.pack_documents WHERE pack_id = CAST(:p AS uuid) AND "
                                "kind = :k", {"p": pack_id, "k": kind})
    return (dict(row["body"]), True) if row else (empty(kind), False)


async def write_document(conn: AsyncConnection, row: store.PackRow, kind: str, doc: dict[str, Any]) -> None:
    """Store a draft's knowledge document of a kind (new or replaced); the caller holds the row (``pack_for``)."""
    await conn.execute(
        text("INSERT INTO model.pack_documents (id, pack_id, tenant_id, kind, body, sha256) VALUES "
             "(CAST(:id AS uuid), CAST(:p AS uuid), CAST(:t AS uuid), :k, CAST(:b AS json), :s) "
             "ON CONFLICT (pack_id, kind) DO UPDATE SET body = EXCLUDED.body, sha256 = EXCLUDED.sha256"),
        {"id": new_id(), "p": row.id, "t": row.tenant_id, "k": kind, "b": json.dumps(doc, default=str),
         "s": document_sha(doc)})


def _pack_out(row: store.PackRow) -> dict[str, Any]:
    return {"id": row.id, "key": row.pack_key, "version": row.pack_version, "title": row.title,
            "status": row.status, "system": row.system, "read_only": not row.editable}


def kind_view(row: store.PackRow, kind: str, body: dict[str, Any], stored: bool) -> dict[str, Any]:
    return {"pack": _pack_out(row), "kind": kind, "list": LISTS[kind], "stored": stored, "document": body,
            "checks": check_document(kind, body, row.manifest), "names": Names.of(row.manifest).out(),
            "etag": etag(body)}


@router.get("/{pack_id}/knowledge")
async def knowledge_overview(pack_id: str, principal: Principal = AUTHOR) -> dict[str, Any]:
    from tela_api.packs.studio.attachments import list_attachments

    async with tenant_tx(principal.tenant_id) as conn:
        row = await pack_for(conn, pack_id)
        docs = {str(r["kind"]): dict(r["body"]) for r in await fetch_all(
            conn, "SELECT kind, body FROM model.pack_documents WHERE pack_id = CAST(:p AS uuid)", {"p": row.id})}
        attached = await list_attachments(conn, row.id)
    kinds = []
    for kind, items_key in LISTS.items():
        body = docs.get(kind)
        checks = check_document(kind, body if body is not None else empty(kind), row.manifest)
        kinds.append({"kind": kind, "stored": body is not None,
                      "count": len(body.get(items_key) or []) if isinstance(body, dict) else 0,
                      "version": (body or {}).get("version"), "errors": len(checks["errors"]),
                      "warnings": len(checks["warnings"]), "etag": etag(body if body is not None else empty(kind))})
    return {"pack": _pack_out(row), "kinds": kinds, "names": Names.of(row.manifest).out(), "attachments": attached}


@router.get("/{pack_id}/knowledge/{kind}")
async def get_knowledge(pack_id: str, kind: str, response: Response, principal: Principal = AUTHOR) -> dict[str, Any]:
    _kind(kind)
    async with tenant_tx(principal.tenant_id) as conn:
        row = await pack_for(conn, pack_id)
        body, stored = await read_document(conn, row.id, kind)
    out = kind_view(row, kind, body, stored)
    response.headers["ETag"] = out["etag"]
    return out


class KnowledgeIn(BaseModel):
    document: dict[str, Any]


@router.post("/{pack_id}/knowledge/{kind}/check")
async def check_knowledge(pack_id: str, kind: str, body: KnowledgeIn, principal: Principal = AUTHOR) -> dict[str, Any]:
    _kind(kind)
    async with tenant_tx(principal.tenant_id) as conn:
        row = await pack_for(conn, pack_id)
    return check_document(kind, body.document, row.manifest)


@router.put("/{pack_id}/knowledge/{kind}")
async def put_knowledge(pack_id: str, kind: str, body: KnowledgeIn, request: Request, response: Response,
                        if_match: str | None = Header(default=None), principal: Principal = AUTHOR) -> dict[str, Any]:
    _kind(kind)
    async with tenant_tx(principal.tenant_id) as conn:
        row = await pack_for(conn, pack_id, write=True)
        current, stored = await read_document(conn, row.id, kind)
        if not if_match:
            raise TelaError(ErrorCode.PRECONDITION_FAILED, "Send the document's revision in If-Match.")
        if if_match.strip().removeprefix("W/") != etag(current):
            raise TelaError(ErrorCode.PRECONDITION_FAILED, "Someone changed this document since you opened it. "
                                                           "Reload to see their changes, then make yours again.",
                            {"current_revision": etag(current)})
        doc = body.document
        checks = check_document(kind, doc, row.manifest)
        if checks["errors"]:
            raise TelaError(ErrorCode.VALIDATION_ERROR, f"Fix the errors before saving: {checks['errors'][0]['path']}"
                            f": {checks['errors'][0]['message']}",
                            {k: checks[k] for k in ("errors", "warnings", "document_errors")})
        await write_document(conn, row, kind, doc)
        await audit.record(conn, tenant_id=principal.tenant_id, action="pack.knowledge_saved", object_type="pack",
                           object_id=row.id, outcome="success", actor_id=principal.user_id,
                           correlation_id=rid(request),
                           details={"pack": row.pack_key, "version": row.pack_version, "kind": kind,
                                    "items": len(doc.get(LISTS[kind]) or []), "new": not stored,
                                    "warnings": len(checks["warnings"])})
    out = kind_view(row, kind, doc, True)
    response.headers["ETag"] = out["etag"]
    return out
