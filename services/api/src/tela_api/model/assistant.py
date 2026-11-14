"""Model assistant (AG-04) and plain-language corrections (API-013; FEAT-007, FEAT-008; US-032, US-033).

* **Propose** — from the catalogue (names, types and keys only; no values unless the AI settings allow
  samples, and the runner never reads samples today), propose links, measures, dimensions and explicit
  questions. Every proposal is validated against the catalogue and the draft before it is shown (A3);
  inferred links are never auto-accepted (A1); the source checks a proposed link's cardinality with two
  counts (``COUNT(*)`` vs ``COUNT(DISTINCT key)``) under the Admin's model-building rights.
* **Correct** — turn one instruction into typed change operations; ambiguous instructions come back
  with a clarifying question whose options carry the operations (AG-02).
* The LLM goes through the gateway; when it is off, slow or wrong, deterministic rules produce the
  proposals and parse the documented phrasings (A2). Every patch records which path produced it.
* Nothing changes until **Apply**; **Undo** restores the exact previous draft.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, TypeAdapter
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from tela_api.audit import service as audit
from tela_api.core.db import fetch_all, fetch_one, tenant_tx
from tela_api.identity.deps import require
from tela_api.identity.permissions import Perm
from tela_api.identity.principal import Principal
from tela_api.llm import trace
from tela_api.llm.client import AgentFailed, GatewayClient, run_agent
from tela_api.llm.review import agent_review_on
from tela_api.llm.review import review as llm_review
from tela_api.llm.router import tenant_llm_settings
from tela_api.model.catalog import MONEY, PERSONAL, STATE, _key
from tela_api.model.patches import (
    MAX_OPS,
    MAX_QUESTIONS,
    AddDimension,
    AddLink,
    AddMeasure,
    AddMeasureCondition,
    ModelPatch,
    Op,
    ProposedQuestion,
    RenameField,
    RenameMeasure,
    SetDescription,
    SetFormat,
    SetSensitivity,
    SetValueLabel,
    apply_ops,
)
from tela_api.model.router import _draft_etag, _model, ensure_draft, insert_questions
from tela_common.errors import ErrorCode, TelaError
from tela_common.ids import new_id
from tela_common.logging import get_logger
from tela_query_spec.expr import InSet

router = APIRouter(prefix="/api/v1", tags=["model-assistant"])
log = get_logger(__name__)
OPS = TypeAdapter(list[Op])
DIMENSION_NAMES = re.compile(r"(region|priority|platform|portfolio|program|programme|category|type|phase|"
                             r"country|department|dept|segment|channel|owner|cio|manager)")

AG04_SYSTEM = (
    "You are Tela's data model assistant (AG-04). You see only table and column names, types and keys of a "
    "PMO database. Propose a data model as typed operations: add_link (from the many side to the one side), "
    "add_measure (count, count_distinct, sum, avg, min, max with a plain-language rule), add_dimension, "
    "rename_field, set_description, set_format, set_sensitivity, set_value_label, add_measure_condition. "
    "Never invent tables or columns. Ask an explicit question (questions[]) where meaning cannot be inferred, "
    "for example which currency a money column uses, whether VAT is included, or whether cancelled records "
    "count. Keys are lowercase identifiers."
)
AG04_CORRECT = (
    "You are Tela's data model assistant (AG-04). Turn the Admin's instruction into typed change operations on "
    "the draft model shown. Use only entities, fields, measures and dimensions that exist in it. If the "
    "instruction is ambiguous, return no operations and one question."
)


AG04_REVIEW = (
    "You are Tela's data model assistant (AG-04). Tela's rules turned the catalogue (table and column names, types "
    "and keys only) or the Admin's instruction into the typed change operations in the draft. A replacement is a "
    "whole patch: ops and questions, using only the tables, columns, entities, fields, measures and dimensions "
    "given; never invent one. Keys are lowercase identifiers."
)


def _dump(ops: list[Any]) -> list[dict[str, Any]]:
    return [op.model_dump(mode="json", by_alias=True, exclude_none=True) for op in ops]


def _valid(definition: dict[str, Any], patch: ModelPatch) -> list[str]:
    """US-245: a reviewed patch must keep at least one op the draft accepts, or ask a question."""
    _, results = apply_ops(definition, patch.ops)
    return [] if any(r["valid"] for r in results) or patch.questions else ["no valid proposals"]


def _summary(definition: dict[str, Any], ops: list[Any]) -> str:
    """The ops in a few words for an option label: each valid op's target and new value."""
    _, results = apply_ops(definition, ops)
    parts = [f"{r['target']}: {r['after']}" for r in results if r["valid"]][:3]
    return "; ".join(parts)[:160] or "no change"


def _same(a: list[Any], b: list[Any]) -> bool:
    key = lambda o: json.dumps(o, sort_keys=True)  # noqa: E731
    return sorted(map(key, _dump(a))) == sorted(map(key, _dump(b)))


# --------------------------------------------------------------------------- context


async def _context(conn: AsyncConnection, model: dict[str, Any], principal: Principal
                   ) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    draft = await ensure_draft(conn, model, principal)
    objects = await fetch_all(
        conn, "SELECT qualified_name, columns, keys FROM conn.connection_objects WHERE connection_id = :c "
              "AND removed_at IS NULL AND readable IS NOT FALSE ORDER BY qualified_name", {"c": model["connection_id"]})
    return draft, draft["definition"], objects


# Platform bookkeeping columns (Dataverse audit, ownership ids, currency bases, address blocks): never a dimension
# or measure, and on a real Dataverse model they are most of the ~1,400 fields. BUG-030: listing them all made the
# AG-04 prompt 80k+ characters and qwen3:8b timed out, so the prompts leave them out unless the draft uses them or
# the Admin names them.
SYSTEM_FIELD = re.compile(
    r"^(?:created|modified)by(?:name|yominame)?$|onbehalfby|yominame$|^owning(?:businessunit|team|user)"
    r"|^ownerid(?:type)?$|^(?:versionnumber|importsequencenumber|timezoneruleversionnumber|utcconversiontimezonecode"
    r"|overriddencreatedon|exchangerate|transactioncurrencyid|traversedpath|processid|stageid)$|_base$"
    r"|^address\d_")
PROMPT_BUDGET = 16_000  # characters of model or catalogue text in one AG-04 prompt (≈ 4k tokens)


def _system(name: str) -> bool:
    return bool(SYSTEM_FIELD.search(name.lower()))


def _mentioned(words: str, *names: str) -> bool:
    n = f" {_norm(words)} "
    variants = {v for name in names for v in (_norm(name), _norm(re.sub(r"^[a-z]+_", "", name.lower())))}
    return any(len(v) > 2 and f" {v} " in n for v in variants)


def _fit(blocks: list[tuple[str, str, int]], budget: int) -> str:
    """Whole lines while they fit, then a name-only line per remaining block: (name, line, field count)."""
    out: list[str] = []
    used = 0
    rest: list[str] = []
    for name, line, count in blocks:
        if used + len(line) <= budget:
            out.append(line)
            used += len(line) + 1
        else:
            rest.append(f"{name} ({count} fields)")
    if rest:
        out.append("Not shown (ask about one by name): " + ", ".join(rest))
    return "\n".join(out)


def _catalog_text(objects: list[dict[str, Any]], budget: int = PROMPT_BUDGET) -> str:
    blocks = []
    for o in objects:
        foreign = o["keys"].get("foreign") or []
        keys = set(o["keys"].get("primary") or []) | {c for fk in foreign for c in fk["columns"]}
        cols = ", ".join(f"{c['name']} {c['type']}" for c in o["columns"]
                         if c["name"] in keys or not _system(c["name"]))
        fks = "; ".join(f"{','.join(fk['columns'])} -> {fk['ref_object']}" for fk in o["keys"].get("foreign") or [])
        blocks.append((o["qualified_name"], f"{o['qualified_name']} (pk {','.join(o['keys'].get('primary') or [])}): "
                       f"{cols}" + (f" | fk {fks}" if fks else ""), len(o["columns"])))
    return _fit(blocks, budget)


def _draft_text(d: dict[str, Any], instruction: str = "", budget: int = PROMPT_BUDGET) -> str:
    """The draft for an AG-04 prompt: fields the draft uses or the instruction names always, platform bookkeeping
    columns never otherwise; entities the instruction names first, then those with measures or dimensions."""
    used = {(x["entity"], x["field"]) for x in d.get("dimensions", [])}
    used |= {(m["entity"], m.get("field")) for m in d.get("measures", [])}
    meas_ents = {m["entity"] for m in d.get("measures", [])} | {e for e, _ in used}

    def keep(e: dict[str, Any], f: dict[str, Any]) -> bool:
        return (f["key"] in e.get("primary_key", []) or (e["key"], f["key"]) in used
                or not _system(f.get("source_column") or f["key"])
                or (bool(instruction) and _mentioned(instruction, f["key"], f["label"])))

    named = _entities_named(d, instruction) if instruction else set()

    def rank(item: tuple[int, dict[str, Any]]) -> tuple[int, int]:
        i, e = item
        return (0 if e["key"] in named else 1 if e["key"] in meas_ents else 2, i)

    blocks = []
    for _, e in sorted(enumerate(d.get("entities", [])), key=rank):
        fields = ", ".join(f"{f['key']} ({f['label']}, {f['data_type']})" for f in e["fields"] if keep(e, f))
        blocks.append((e["key"], f"{e['key']}: {fields}", len(e["fields"])))
    ents = _fit(blocks, budget)
    meas = "; ".join(f"{m['key']} ({m['label']})" for m in d.get("measures", []))
    dims = "; ".join(f"{x['key']} ({x['label']} = {x['entity']}.{x['field']})" for x in d.get("dimensions", []))
    return f"Entities:\n{ents}\nMeasures: {meas or 'none'}\nDimensions: {dims or 'none'}"


# --------------------------------------------------------------------------- rules (fallback and baseline)


def rule_proposals(d: dict[str, Any], *, kinds: set[str] | None = None,
                   entities: set[str] | None = None) -> ModelPatch:
    """The rules' proposals; ``kinds`` (op names) and ``entities`` narrow them before the patch cap."""
    ops: list[Any] = []
    questions: list[ProposedQuestion] = []
    dims = {(x["entity"], x["field"]) for x in d.get("dimensions", [])}
    measures = {m["key"] for m in d.get("measures", [])}
    for e in d.get("entities", []):
        if entities is not None and e["key"] not in entities:
            continue
        columns = {f["source_column"].lower() for f in e["fields"]}
        for f in e["fields"]:
            name = f["source_column"].lower()
            if f["key"] in e["primary_key"] or (e["key"], f["key"]) in dims or _system(name):
                continue
            if f["data_type"] == "text" and f"{name}name" in columns:
                continue  # a Dataverse lookup or choice id: its "...name" column is the readable breakdown
            if f["data_type"] in ("date", "datetime"):
                ops.append(AddDimension(op="add_dimension", key=_key(f"{e['key']}_{f['key']}"),
                                        label=f["label"], entity=e["key"], field=f["key"], is_temporal=True,
                                        reason="Date column: lets answers trend over time."))
            elif f["data_type"] == "text" and STATE.search(name):
                ops.append(AddDimension(op="add_dimension", key=_key(f"{e['key']}_{f['key']}"), label=f["label"],
                                        entity=e["key"], field=f["key"], is_status=True,
                                        reason="Status column: a natural breakdown."))
                questions.append(ProposedQuestion(
                    question=f"Should cancelled or dropped {e['label'].lower()} count in totals? ({f['label']})",
                    options=["Yes, count them", "No, exclude them"], target=f"{e['key']}.{f['key']}"))
            elif f["data_type"] == "text" and DIMENSION_NAMES.search(name) and not PERSONAL.search(name):
                ops.append(AddDimension(op="add_dimension", key=_key(f"{e['key']}_{f['key']}"), label=f["label"],
                                        entity=e["key"], field=f["key"], reason="Grouping column."))
            elif f["data_type"] in ("integer", "decimal") and MONEY.search(name):
                key = _key(f"{e['key']}_{f['key']}_total")
                if key not in measures:
                    ops.append(AddMeasure(op="add_measure", key=key, label=f"Total {f['label'].lower()}",
                                          entity=e["key"], agg="sum", field=f["key"], format="currency",
                                          plain_rule=f"Sum of {f['label'].lower()} over {e['label'].lower()}.",
                                          reason="Money column."))
                questions.append(ProposedQuestion(
                    question=f"Is '{f['label']}' actual spend or a forecast, which currency is it in, and does it "
                             "include VAT?", options=["Forecast, USD, excludes VAT", "Actual spend, USD, excludes "
                                                      "VAT", "Mixed currencies"], target=f"{e['key']}.{f['key']}"))
    if kinds is not None:
        ops = [op for op in ops if op.op in kinds]
        if "add_measure" not in kinds:
            questions = [q for q in questions if "forecast" not in q.question.lower()]
        if "add_dimension" not in kinds:
            questions = [q for q in questions if "cancelled" not in q.question.lower()]
    # A source with many tables (pack 2.0 binds ten, real Dataverse has hundreds) proposes more than one patch holds:
    # keep the first ones, table by table; asking again after applying proposes the rest.
    return ModelPatch(ops=ops[:MAX_OPS], questions=questions[:MAX_QUESTIONS])


_RENAME = re.compile(r"^rename\s+(.+?)\s+(?:to|as)\s+(.+)$", re.I)
_FORMAT = re.compile(r"^format\s+(.+?)\s+as\s+(currency|usd|eur|aed|gbp|percent|percentage|integer|decimal)$", re.I)
_TREAT = re.compile(r"^(?:treat|show)\s+(\w[\w ]*?)\s+as\s+(\w[\w ]*?)(?:\s+everywhere)?$", re.I)
_EXCLUDE = re.compile(r"^exclude\s+(\w[\w ]*?)\s+(?:\w+\s+)?from\s+(.+)$", re.I)
_SENS = re.compile(r"^(.+?)\s+is\s+(public|internal|confidential|personal|sensitive)(?:\s+data)?$", re.I)
_MORE = re.compile(r"^(?:please\s+)?(?:add|propose|suggest|give\s+me|find)\s+(?:some\s+|more\s+|new\s+|other\s+)*"
                   r"(dimensions|measures|breakdowns|totals)(?:\s+(?:to|for|on|in)\s+(?:the\s+)?(.+?))?$", re.I)
_ADD_DIM = re.compile(r"^add\s+(?:a\s+)?(?:dimension|breakdown)\s+(?:for\s+|on\s+|by\s+)?(.+?)$"
                      r"|^add\s+(.+?)\s+as\s+a\s+(?:dimension|breakdown)$", re.I)
_DESCRIBE = re.compile(r"^(?:describe\s+(.+?)\s+as|(.+?)\s+means)\s+(.+)$", re.I)


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def _find_fields(d: dict[str, Any], name: str) -> list[tuple[str, dict[str, Any]]]:
    n = _norm(name)
    hits = [(e["key"], f) for e in d["entities"] for f in e["fields"]
            if n in (_norm(f["key"]), _norm(f["source_column"]), _norm(f["label"]))]
    return hits


def _find_measures(d: dict[str, Any], name: str) -> list[dict[str, Any]]:
    n = _norm(name)
    return [m for m in d["measures"] if n in (_norm(m["key"]), _norm(m["label"]))]


def _entities_named(d: dict[str, Any], words: str) -> set[str]:
    singular = " ".join(w[:-1] if len(w) > 3 and w.endswith("s") else w for w in _norm(words).split())
    return {e["key"] for e in d.get("entities", [])
            if _mentioned(f"{words} {singular}", e["key"], e.get("label", ""))}


def _more(d: dict[str, Any], what: str, where: str | None) -> list[Any] | None:
    """BUG-030: "add more dimensions [to projects]" is the rules' proposals of that kind, not an LLM call over the
    whole model; None when the named table isn't in the draft."""
    entities = _entities_named(d, where) if where else None
    if entities is not None and not entities:
        return None
    kind = "add_measure" if what.lower() in ("measures", "totals") else "add_dimension"
    ops = list(rule_proposals(d, kinds={kind}, entities=entities).ops)
    # Status and grouping columns read better than one created/modified date per table: they come first.
    return sorted(ops, key=lambda o: (not getattr(o, "is_status", False), bool(getattr(o, "is_temporal", False))))


def _add_dimension(e: str, f: dict[str, Any]) -> AddDimension:
    return AddDimension(op="add_dimension", key=_key(f"{e}_{f['key']}"), label=f["label"], entity=e, field=f["key"],
                        is_temporal=f["data_type"] in ("date", "datetime"),
                        is_status=bool(STATE.search(f["source_column"].lower())))


def parse_correction(d: dict[str, Any], instruction: str) -> tuple[list[Any], list[dict[str, Any]], list[str]]:
    """Deterministic parser for the documented phrasings. Returns (ops, clarifications, unparsed clauses)."""
    ops: list[Any] = []
    clarify: list[dict[str, Any]] = []
    unparsed: list[str] = []
    clauses = [c.strip(" .") for c in re.split(r",|;|\band\b(?=\s+(?:rename|format|treat|show|exclude|describe|add))",
                                                 instruction, flags=re.I) if c.strip(" .")]
    for clause in clauses:
        if m := _MORE.match(clause):
            found = _more(d, m.group(1), m.group(2))
            if found:
                ops.extend(found)
            else:
                unparsed.append(clause)
        elif m := _ADD_DIM.match(clause):
            name = (m.group(1) or m.group(2) or "").strip(" '\"")
            entity, _, field = name.rpartition(".")
            fields = [(e, f) for e, f in _find_fields(d, field or name) if not entity or e == entity]
            dims = {(x["entity"], x["field"]) for x in d.get("dimensions", [])}
            fields = [(e, f) for e, f in fields if (e, f["key"]) not in dims]
            if len(fields) == 1:
                ops.append(_add_dimension(*fields[0]))
            elif fields:
                clarify.append({"question": f"Which '{name}' should be a dimension?",
                                "options": [{"label": f"{e}.{f['key']} ({f['label']})", "ops": [_add_dimension(e, f)]}
                                            for e, f in fields[:6]]})
            else:
                unparsed.append(clause)
        elif m := _RENAME.match(clause):
            old, new = m.group(1).strip(" '\""), m.group(2).strip(" '\"")
            fields, measures = _find_fields(d, old), _find_measures(d, old)
            new_label = new
            new_label = re.sub(r",?\s*format(ted)?\s+as.*$", "", new_label, flags=re.I).strip()
            if len(fields) + len(measures) == 1:
                ops.append(RenameField(op="rename_field", entity=fields[0][0], field=fields[0][1]["key"],
                                       label=new_label) if fields else
                           RenameMeasure(op="rename_measure", measure=measures[0]["key"], label=new_label))
            elif fields or measures:
                clarify.append({"question": f"Which '{old}' do you mean?", "options": [
                    *({"label": f"{e}.{f['key']} ({f['label']})",
                       "ops": [RenameField(op="rename_field", entity=e, field=f["key"], label=new_label)]}
                      for e, f in fields),
                    *({"label": f"Measure {mm['label']}",
                       "ops": [RenameMeasure(op="rename_measure", measure=mm["key"], label=new_label)]}
                      for mm in measures)]})
            else:
                unparsed.append(clause)
        elif m := _FORMAT.match(clause):
            name, fmt = m.group(1).strip(" '\""), m.group(2).lower()
            fields = [(e, f) for e, f in _find_fields(d, name) if f["data_type"] in ("integer", "decimal")]
            kind: Any = {"usd": "currency", "eur": "currency", "aed": "currency", "gbp": "currency",
                    "percentage": "percent"}.get(fmt, fmt)
            currency = fmt.upper() if fmt in ("usd", "eur", "aed", "gbp") else None
            make: list[Any] = [SetFormat(op="set_format", entity=e, field=f["key"], format=kind, currency=currency)
                    for e, f in fields]
            if len(make) == 1:
                ops.extend(make)
            elif make:
                clarify.append({"question": f"Which '{name}' should be formatted as {kind}?",
                                "options": [{"label": f"{o.entity}.{o.field}", "ops": [o]} for o in make]})
            else:
                unparsed.append(clause)
        elif m := _TREAT.match(clause):
            raw, label = m.group(1).strip(" '\""), m.group(2).strip(" '\"")
            status_dims = [x for x in d["dimensions"] if x.get("is_status") or STATE.search(x["field"])
                           or "kpi" in x["field"].lower()]
            if status_dims:
                ops.extend(SetValueLabel(op="set_value_label", dimension=x["key"], raw=raw, label=label)
                           for x in status_dims)
            else:
                unparsed.append(clause)
        elif m := _EXCLUDE.match(clause):
            value, target = m.group(1).strip(" '\""), m.group(2).strip(" '\"")
            measures = _find_measures(d, target)
            if len(measures) != 1:
                unparsed.append(clause)
                continue
            meas = measures[0]
            ent = next(e for e in d["entities"] if e["key"] == meas["entity"])
            candidates = [f for f in ent["fields"] if f["data_type"] == "text" and STATE.search(f["source_column"])]
            make = [AddMeasureCondition(op="add_measure_condition", measure=meas["key"],
                                        condition=InSet(field=f["key"], op="not_in", values=[value]))
                    for f in candidates]
            if len(make) == 1:
                ops.extend(make)
            elif make:
                clarify.append({"question": f"Which field holds '{value}' for {meas['label']}?",
                                "options": [{"label": f["label"], "ops": [o]} for f, o in
                                            zip(candidates, make, strict=True)]})
            else:
                unparsed.append(clause)
        elif m := _SENS.match(clause):
            fields = _find_fields(d, m.group(1).strip(" '\""))
            if len(fields) == 1:
                ops.append(SetSensitivity(op="set_sensitivity", entity=fields[0][0], field=fields[0][1]["key"],
                                          sensitivity=m.group(2).lower()))  # type: ignore[arg-type]
            else:
                unparsed.append(clause)
        elif m := _DESCRIBE.match(clause):
            name = (m.group(1) or m.group(2) or "").strip(" '\"")
            fields = _find_fields(d, name)
            if len(fields) == 1:
                ops.append(SetDescription(op="set_description", entity=fields[0][0], field=fields[0][1]["key"],
                                          description=m.group(3).strip(" '\"")))
            else:
                unparsed.append(clause)
        else:
            unparsed.append(clause)
    return ops, clarify, unparsed


# --------------------------------------------------------------------------- persistence and impact


async def _affected(conn: AsyncConnection, connection_id: str, definition: dict[str, Any],
                    results: list[dict[str, Any]], ops: list[Any]) -> list[str]:
    measures = {getattr(op, "measure", None) for op in ops} - {None}
    fields = {(getattr(op, "entity", None), getattr(op, "field", None)) for op in ops}
    for m in definition.get("measures", []):
        if any(m["entity"] == e and f in json.dumps(m["expression"]) for e, f in fields if e and f):
            measures.add(m["key"])
    if not measures:
        return []
    rows = await fetch_all(
        conn, "SELECT DISTINCT w.title, d.title AS dashboard FROM content.widgets w JOIN content.dashboard_tabs t "
              "ON t.id = w.dashboard_tab_id JOIN content.dashboards d ON d.id = t.dashboard_id "
              "WHERE w.connection_id = :c AND w.spec->'measure'->>'ref' = ANY(:refs)",
        {"c": connection_id, "refs": [f"measure:{m}" for m in measures]})
    return [f"{r['dashboard']} · {r['title']}" for r in rows]


async def _store_patch(conn: AsyncConnection, principal: Principal, model: dict[str, Any], draft: dict[str, Any],
                       kind: str, source: str, message: str, ops: list[Any],
                       questions: list[dict[str, Any]]) -> dict[str, Any]:
    _, results = apply_ops(draft["definition"], ops)
    valid_ops = [op for op, r in zip(ops, results, strict=True) if r["valid"]]
    dropped = [r for r in results if not r["valid"]]
    patch_id = new_id()
    await conn.execute(
        text("INSERT INTO model.model_patches (id, tenant_id, data_model_id, model_version_id, kind, message_hash, "
             "source, ops, questions, after_revision, created_by) VALUES (:id, :t, :m, :v, :k, :h, :s, "
             "CAST(:ops AS jsonb), CAST(:q AS jsonb), :rev, :by)"),
        {"id": patch_id, "t": principal.tenant_id, "m": model["id"], "v": draft["id"], "k": kind,
         "h": hashlib.sha256(message.encode()).hexdigest(), "s": source,
         "ops": json.dumps([op.model_dump(mode="json", by_alias=True, exclude_none=True) for op in valid_ops]),
         "q": json.dumps(questions, default=lambda o: o.model_dump(mode="json", by_alias=True, exclude_none=True)),
         "rev": draft["draft_revision"], "by": principal.user_id},
    )
    _, previews = apply_ops(draft["definition"], valid_ops)
    affected = await _affected(conn, str(model["connection_id"]), draft["definition"], previews, valid_ops)
    if dropped:
        log.info("assistant.ops_dropped", count=len(dropped))
    return {"patch_id": patch_id, "kind": kind, "source": source,
            "ops": [{**r, "detail": op.model_dump(mode="json", by_alias=True, exclude_none=True)}
                    for op, r in zip(valid_ops, previews, strict=True)],
            "dropped": dropped, "questions": questions, "affected": affected,
            "base_etag": _draft_etag(draft["draft_revision"])}


def _sse(event: str, data: Any) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


class AssistIn(BaseModel):
    message: str | None = Field(default=None, max_length=2000)


@router.post("/models/{model_id}/assistant")
async def assistant(model_id: str, body: AssistIn, request: Request,
                    principal: Principal = Depends(require(Perm.MODEL_EDIT))) -> StreamingResponse:
    """SSE: ``proposal`` (a patch with its diff), ``question`` (explicit or clarifying), ``complete``, ``error``."""
    gateway: GatewayClient | None = getattr(request.app.state, "gateway", None)

    async def events() -> AsyncIterator[str]:
        async with trace.agent_run(principal.tenant_id, principal.user_id, "model_assist",
                                   title=(body.message or "").strip() or "Propose model changes",
                                   object_ref=f"model:{model_id}", agent="AG-04"):
            try:
                async with tenant_tx(principal.tenant_id) as conn:
                    model = await _model(conn, model_id)
                    draft, definition, objects = await _context(conn, model, principal)
                message = (body.message or "").strip()
                questions: list[dict[str, Any]] = []
                async with tenant_tx(principal.tenant_id) as conn:
                    settings = await tenant_llm_settings(conn)
                reviewing = gateway is not None and agent_review_on(settings, "AG-04")
                review: dict[str, Any] | None = None
                if not message and reviewing:
                    kind = "proposal"
                    patch, source, review = await _review_proposals(gateway, settings, principal, definition,
                                                                    objects)
                    ops = list(patch.ops)
                    questions = [q.model_dump() for q in patch.questions]
                    ops, link_notes = await _check_links(request, principal, model, definition, ops)
                    questions += link_notes
                elif not message:
                    kind = "proposal"
                    try:
                        patch = await run_agent(gateway, ModelPatch, tenant_id=principal.tenant_id,
                                                user_id=principal.user_id, agent="AG-04", task="model_assist",
                                                system=AG04_SYSTEM, user="Catalogue:\n" + _catalog_text(objects)
                                                + "\n\nCurrent draft:\n" + _draft_text(definition))
                        source = "llm"
                        _, results = apply_ops(definition, patch.ops)
                        if not any(r["valid"] for r in results) and not patch.questions:
                            raise AgentFailed("agent_failed", "no valid proposals")
                    except AgentFailed as exc:
                        log.info("assistant.fallback", reason=exc.code)
                        patch, source = rule_proposals(definition), "rules"
                    await trace.step("rule", f"Proposed {len(patch.ops)} change(s) by rules", agent="AG-04")
                    ops = list(patch.ops)
                    questions = [q.model_dump() for q in patch.questions]
                    ops, link_notes = await _check_links(request, principal, model, definition, ops)
                    questions += link_notes
                else:
                    kind = "correction"
                    ops, clarify, unparsed = parse_correction(definition, message)
                    source = "rules"
                    await trace.step("rule", f"Read the correction: {len(ops)} change(s), {len(clarify)} question(s)",
                                     agent="AG-04", detail={"unparsed": unparsed})
                    if reviewing and (ops or clarify):
                        ops, clarify, source, review = await _review_correction(gateway, settings, principal,
                                                                                definition, message, ops, clarify)
                    if unparsed and not ops and not clarify:
                        try:
                            patch = await run_agent(gateway, ModelPatch, tenant_id=principal.tenant_id,
                                                    user_id=principal.user_id, agent="AG-04", task="model_assist",
                                                    system=AG04_CORRECT,
                                                    user="Draft:\n" + _draft_text(definition, message)
                                                    + "\n\nInstruction: " + message)
                            ops, source = list(patch.ops), "llm"
                            questions = [q.model_dump() for q in patch.questions]
                        except AgentFailed:
                            questions = [{"question": "I couldn't turn that into a change. Try phrasings like "
                                          "\"rename X to Y\", \"format X as USD\", \"treat Yellow as Amber\" or "
                                          "\"exclude Cancelled from Open projects\".", "options": []}]
                    questions += [{"question": c["question"], "options": [o["label"] for o in c["options"]],
                                   "choices": [[op.model_dump(mode="json", by_alias=True, exclude_none=True)
                                                for op in o["ops"]] for o in c["options"]], "clarify": True}
                                  for c in clarify]
                async with tenant_tx(principal.tenant_id) as conn:
                    model = await _model(conn, model_id)
                    draft = await ensure_draft(conn, model, principal)
                    patch_out = await _store_patch(conn, principal, model, draft, kind, source, message or "propose",
                                                   ops, questions)
                    if review is not None:
                        patch_out["review"] = review
                    await audit.record(conn, tenant_id=principal.tenant_id, action="model.assistant.proposed",
                                       object_type="data_model", object_id=model_id, outcome="success",
                                       actor_id=principal.user_id, correlation_id=getattr(request.state, "request_id",
                                                                                          None),
                                       details={"kind": kind, "source": source, "ops": len(patch_out["ops"]),
                                                "review": (review or {}).get("verdict"),
                                                "dropped": len(patch_out["dropped"]), "questions": len(questions)})
                yield _sse("proposal", patch_out)
                for q in questions:
                    yield _sse("question", q)
                yield _sse("complete", {"patch_id": patch_out["patch_id"], "source": source,
                                        **({"review": review["verdict"]} if review else {})})
            except TelaError as exc:
                yield _sse("error", {"code": exc.code, "message": exc.message})

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})


async def _review_proposals(gateway: GatewayClient | None, settings: dict[str, Any], principal: Principal,
                            definition: dict[str, Any], objects: list[dict[str, Any]]
                            ) -> tuple[ModelPatch, str, dict[str, Any]]:
    """US-245 (DEC-28): the rules propose first; one ``model_assist`` review gets their patch as the draft, with the
    catalogue (names, types and keys only, DEC-27) and the current draft. A confirm, an ask or a failed review keeps
    the rules' patch; a correction that passes the same validation replaces it, shown "Corrected by AI" with the
    rules' ops it left out listed. Nothing changes until Apply either way."""
    rules = rule_proposals(definition)
    await trace.step("rule", f"Proposed {len(rules.ops)} change(s) by rules", agent="AG-04")
    got = await llm_review(gateway, ModelPatch, settings=settings, tenant_id=principal.tenant_id,
                           user_id=principal.user_id, agent="AG-04", task="model_assist", system=AG04_REVIEW,
                           request="Propose a data model for this catalogue.", draft=rules,
                           context={"catalogue": _catalog_text(objects), "current_draft": _draft_text(definition)},
                           check=lambda p: _valid(definition, p))
    review: dict[str, Any] = {"verdict": got.verdict, "reason": got.reason}
    if got.verdict == "correct" and got.replacement is not None:
        kept = {json.dumps(o, sort_keys=True) for o in _dump(got.replacement.ops)}
        review["rules_left_out"] = [o for o in _dump(rules.ops) if json.dumps(o, sort_keys=True) not in kept]
        return got.replacement, "llm", review
    if got.verdict == "ask":  # a proposal isn't a question: the rules' patch stands, the options' questions join it
        asked = {q.question for q in rules.questions}
        extra = [q for o in got.options for q in o.questions if q.question not in asked]
        rules = rules.model_copy(update={"questions": [*rules.questions, *extra][:MAX_QUESTIONS]})
    return rules, "rules", review


async def _review_correction(gateway: GatewayClient | None, settings: dict[str, Any], principal: Principal,
                             definition: dict[str, Any], message: str, ops: list[Any],
                             clarify: list[dict[str, Any]]
                             ) -> tuple[list[Any], list[dict[str, Any]], str, dict[str, Any]]:
    """US-245 (DEC-28): what the rules read from the instruction goes to one review as the draft. A confirm or a
    failed review keeps it; a correction with other ops never replaces it silently: both readings become one
    clarifying question; an ask offers its options beside the rules' reading. The rules' own questions stand."""
    draft = {"ops": _dump(ops), "questions": [{"question": c["question"], "options": [o["label"] for o in c["options"]]}
                                              for c in clarify]}
    got = await llm_review(gateway, ModelPatch, settings=settings, tenant_id=principal.tenant_id,
                           user_id=principal.user_id, agent="AG-04", task="model_assist", system=AG04_REVIEW,
                           request=message, draft=draft, context={"current_draft": _draft_text(definition, message)},
                           check=lambda p: _valid(definition, p))
    review: dict[str, Any] = {"verdict": got.verdict, "reason": got.reason}
    readings = [got.replacement] if got.verdict == "correct" and got.replacement is not None else got.options
    others = [r for r in readings if r.ops and not _same(r.ops, ops) and not _valid(definition, r)]
    if not ops or not others:  # a rules question, or the LLM read it the same way
        if got.verdict == "correct":
            review["verdict"] = "confirm"
        return ops, clarify, "rules", review
    options = [{"label": f"As written: {_summary(definition, ops)}", "ops": ops},
               *({"label": f"Or: {_summary(definition, r.ops)}", "ops": list(r.ops)} for r in others)]
    question = {"question": "Which change do you mean?", "options": options[:4]}
    return [], [question, *clarify], "rules", review


async def _check_links(request: Request, principal: Principal, model: dict[str, Any], definition: dict[str, Any],
                       ops: list[Any]) -> tuple[list[Any], list[dict[str, Any]]]:
    """Confirm proposed links' cardinality at the source: the 'one' side key must be unique (TEST-009)."""
    notes: list[dict[str, Any]] = []
    service = request.app.state.query_service
    out = []
    for op in ops:
        if not isinstance(op, AddLink):
            out.append(op)
            continue
        ent = next((e for e in definition.get("entities", []) if e["key"] == op.to_entity), None)
        unique = None
        if ent is not None and len(op.to_fields) == 1:
            try:
                unique = await service.column_is_unique(principal=principal, connection_id=str(model["connection_id"]),
                                                        source_object=ent["source_object"],
                                                        column=next(f["source_column"] for f in ent["fields"]
                                                                    if f["key"] == op.to_fields[0]))
            except (TelaError, StopIteration):
                unique = None
        if unique is False:
            op = op.model_copy(update={"cardinality": "many_to_one", "reason": "Inferred link — the target key is "
                                       "not unique; confirm cardinality."})
            notes.append({"question": f"{op.to_entity}.{op.to_fields[0]} is not unique, so {op.from_entity} → "
                                      f"{op.to_entity} may multiply rows. Is this link correct?",
                          "options": ["Keep it", "Remove it"], "target": f"{op.from_entity}->{op.to_entity}"})
        out.append(op)
    return out, notes


# --------------------------------------------------------------------------- apply, reject, undo


class ApplyIn(BaseModel):
    accept: list[int] | None = None  # indexes of ops to apply; None = all
    answers: dict[str, int] = Field(default_factory=dict)  # clarification index -> option index


@router.post("/models/{model_id}/patches/{patch_id}/apply")
async def apply_patch(model_id: str, patch_id: str, body: ApplyIn, request: Request,
                      principal: Principal = Depends(require(Perm.MODEL_EDIT))) -> dict[str, Any]:
    async with tenant_tx(principal.tenant_id) as conn:
        model = await _model(conn, model_id)
        patch = await fetch_one(conn, "SELECT * FROM model.model_patches WHERE id = CAST(:p AS uuid) "
                                      "AND data_model_id = :m", {"p": patch_id, "m": model["id"]})
        if patch is None:
            raise TelaError(ErrorCode.NOT_FOUND, "That proposal does not exist.")
        if patch["status"] != "pending":
            raise TelaError(ErrorCode.CONFLICT, f"That proposal is already {patch['status']}.")
        draft = await ensure_draft(conn, model, principal)
        if draft["id"] != patch["model_version_id"] or draft["draft_revision"] != patch["after_revision"]:
            raise TelaError(ErrorCode.PRECONDITION_FAILED, "The draft changed since this proposal. Ask again.")
        ops = OPS.validate_python(patch["ops"])
        chosen = ops if body.accept is None else [ops[i] for i in body.accept if 0 <= i < len(ops)]
        clarifications = [q for q in patch["questions"] if q.get("clarify")]
        for qi, option in body.answers.items():
            q = clarifications[int(qi)] if int(qi) < len(clarifications) else None
            if q is None or not 0 <= option < len(q["choices"]):
                raise TelaError(ErrorCode.VALIDATION_ERROR, "That answer is not one of the options.")
            chosen += OPS.validate_python(q["choices"][option])
        new_def, results = apply_ops(draft["definition"], chosen)
        bad = [r for r in results if not r["valid"]]
        if bad:
            raise TelaError(ErrorCode.MODEL_INVALID, "Some changes no longer apply to the draft.", {"issues": bad})
        await conn.execute(
            text("UPDATE model.model_versions SET definition = CAST(:d AS jsonb), draft_revision = draft_revision + 1 "
                 "WHERE id = :id"), {"d": json.dumps(new_def), "id": draft["id"]})
        await conn.execute(
            text("UPDATE model.model_patches SET status = 'applied', before_definition = CAST(:b AS jsonb), "
                 "after_revision = :rev WHERE id = :id"),
            {"b": json.dumps(draft["definition"]), "rev": draft["draft_revision"] + 1, "id": patch["id"]})
        explicit = [q for q in patch["questions"] if not q.get("clarify") and q.get("options")]
        if patch["kind"] == "proposal" and explicit:
            await insert_questions(conn, principal.tenant_id, str(draft["id"]),
                                   [{"question": q["question"], "options": q["options"],
                                     "target_ref": {"target": q.get("target")}} for q in explicit], principal.user_id)
        await audit.record(conn, tenant_id=principal.tenant_id, action="model.patch.applied", object_type="data_model",
                           object_id=model_id, outcome="success", actor_id=principal.user_id,
                           correlation_id=getattr(request.state, "request_id", None),
                           details={"patch_id": patch_id, "ops": [r["op"] for r in results]})
    return {"status": "applied", "applied": results, "draft_etag": _draft_etag(draft["draft_revision"] + 1)}


@router.post("/models/{model_id}/patches/{patch_id}/reject")
async def reject_patch(model_id: str, patch_id: str,
                       principal: Principal = Depends(require(Perm.MODEL_EDIT))) -> dict[str, Any]:
    async with tenant_tx(principal.tenant_id) as conn:
        model = await _model(conn, model_id)
        res = await conn.execute(
            text("UPDATE model.model_patches SET status = 'rejected' WHERE id = CAST(:p AS uuid) "
                 "AND data_model_id = :m AND status = 'pending' RETURNING id"), {"p": patch_id, "m": model["id"]})
        if res.first() is None:
            raise TelaError(ErrorCode.CONFLICT, "That proposal is not pending.")
    return {"status": "rejected"}


@router.post("/models/{model_id}/draft/undo")
async def undo(model_id: str, request: Request, if_match: str | None = Header(default=None),
               principal: Principal = Depends(require(Perm.MODEL_EDIT))) -> dict[str, Any]:
    """Undo the last applied change list (FEAT-008), only if nothing else changed the draft since."""
    async with tenant_tx(principal.tenant_id) as conn:
        model = await _model(conn, model_id)
        draft = await ensure_draft(conn, model, principal)
        patch = await fetch_one(conn, "SELECT * FROM model.model_patches WHERE data_model_id = :m AND "
                                      "model_version_id = :v AND status = 'applied' ORDER BY updated_at DESC LIMIT 1",
                                {"m": model["id"], "v": draft["id"]})
        if patch is None:
            raise TelaError(ErrorCode.CONFLICT, "There is nothing to undo.")
        if patch["after_revision"] != draft["draft_revision"] or (if_match and
                                                                  if_match != _draft_etag(draft["draft_revision"])):
            raise TelaError(ErrorCode.PRECONDITION_FAILED, "The draft changed after that change; undo it by hand.")
        await conn.execute(
            text("UPDATE model.model_versions SET definition = CAST(:d AS jsonb), draft_revision = draft_revision + 1 "
                 "WHERE id = :id"), {"d": json.dumps(patch["before_definition"]), "id": draft["id"]})
        await conn.execute(text("UPDATE model.model_patches SET status = 'undone' WHERE id = :id"), {"id": patch["id"]})
        await audit.record(conn, tenant_id=principal.tenant_id, action="model.patch.undone", object_type="data_model",
                           object_id=model_id, outcome="success", actor_id=principal.user_id,
                           correlation_id=getattr(request.state, "request_id", None),
                           details={"patch_id": str(patch["id"])})
    return {"status": "undone", "draft_etag": _draft_etag(draft["draft_revision"] + 1)}


@router.get("/models/{model_id}/patches")
async def list_patches(model_id: str, principal: Principal = Depends(require(Perm.MODEL_EDIT))) -> dict[str, Any]:
    async with tenant_tx(principal.tenant_id) as conn:
        model = await _model(conn, model_id)
        rows = await fetch_all(conn, "SELECT id, kind, source, status, ops, questions, created_at FROM "
                                     "model.model_patches WHERE data_model_id = :m ORDER BY created_at DESC LIMIT 50",
                               {"m": model["id"]})
    return {"items": [{**r, "id": str(r["id"])} for r in rows]}

