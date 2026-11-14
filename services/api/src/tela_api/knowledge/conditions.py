"""Named conditions and the order of authority (ENH-028, US-252; FEAT-078 / FEAT-007; DEC-29).

A business term ("open", "late", "in delivery") is stored **once** per connection and model version as a condition
Tela can run (``rag.named_conditions``): the term and its synonyms, the entity whose rows it selects, and the
condition in the measure-condition format (``tela_query_spec.expr.Condition``). Every Ask mode reads the same rows:

* **Where they come from** (``source``): an approved definition read from a document (US-203, ``document``) or a row
  of the business-context spreadsheet (US-251, ``definition_sheet``) — approved when its definition is, retired with
  it; and the rule of each certified count measure (``pack_measure``: "Open projects" → ``open`` on projects),
  approved with the model version, never over a definition's condition of the same term.
* **Order of authority** (``authority``): approved condition → certified measure → document text (a hint to the LLM
  only). A condition and the certified measure of the same term that select different rows are a **conflict**: kept
  with both rules and a row count each (``conflict``), listed in Admin → Knowledge, Conditions, and until the Admin
  chooses (``prefer``) Ask counts by the certified measure and says the two differ.
* **Carried forward** like definitions: a newly approved model version takes the connection's conditions over, read
  again on its model; a condition whose field the new model lacks waits as ``proposed`` with the gap.
* **Scope and hidden fields**: a condition is only offered to a person who can see every field it reads
  (``visible``); counts run through the query service, under the person's row scope.

``GET /knowledge/conditions`` lists them with counts; ``POST /knowledge/conditions/{id}/choose`` settles a conflict;
``POST /knowledge/conditions/{id}/check`` counts both rules again. Audit: ``knowledge.condition_chosen``.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field, TypeAdapter, ValidationError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from tela_api.ask.planner import words
from tela_api.audit import service as audit
from tela_api.content.exports import _rid
from tela_api.core.db import fetch_all, fetch_one, tenant_tx
from tela_api.governance.lookalike import _total, describe, differs
from tela_api.identity.deps import require
from tela_api.identity.permissions import Perm
from tela_api.identity.principal import Principal
from tela_api.knowledge import definitions
from tela_api.knowledge.definitions import singular, term_key
from tela_api.model.store import load_snapshot
from tela_api.query.service import QueryService
from tela_common.errors import ErrorCode, TelaError
from tela_common.ids import new_id
from tela_common.logging import get_logger
from tela_query_spec.expr import AggExpr, Condition, condition_fields, split_ref
from tela_query_spec.model import Measure, ModelSnapshot

log = get_logger(__name__)
router = APIRouter(prefix="/api/v1/knowledge", tags=["knowledge"])

Source = Literal["document", "definition_sheet", "pack_measure"]
COUNT_AGGS = ("count", "count_distinct")
FILLER = {"number", "of", "count", "total", "no", "the", "all"}
_CONDITION: TypeAdapter[Any] = TypeAdapter(Condition)


# --------------------------------------------------------------------------- reading a condition


def parse(cond: dict[str, Any] | None) -> Any | None:
    """A stored condition as its expression, or None when it is not one."""
    if not cond:
        return None
    try:
        return _CONDITION.validate_python(cond)
    except ValidationError:
        return None


def dump(cond: Any) -> dict[str, Any]:
    return dict(_CONDITION.dump_python(cond, mode="json", by_alias=True, exclude_none=True))


def from_rule(rule: dict[str, Any]) -> dict[str, Any]:
    """A definition's rule ({entity, field, op, values}; US-251's operators too) as a condition."""
    return dump(definitions.condition(rule))


def from_measure(m: Measure) -> dict[str, Any] | None:
    """The condition of a certified count measure ("Open projects" → status = Active), or None."""
    expr = m.expression
    if m.status != "certified" or not isinstance(expr, AggExpr) or expr.agg not in COUNT_AGGS or \
            expr.where is None or expr.value is not None:
        return None
    return dump(expr.where)


def measure_term(m: Measure) -> str:
    return " ".join(re.sub(r"\([^)]*\)", " ", m.label).split())[:80] or m.key.replace("_", " ")[:80]


def qualifier(term: str, entity: str, snap: ModelSnapshot) -> str:
    """The term without its entity's words: "Open projects" on Projects → "open" (the name US-254 composes with)."""
    ent = snap.entity(entity)
    noun = {singular(w) for w in words(ent.label)} | {singular(w) for w in words(entity.replace("_", " "))} \
        if ent else set()
    left = [w for w in words(term) if singular(w) not in noun and w not in FILLER]
    return " ".join(left) or term_key(term)


def gaps(cond: dict[str, Any], entity: str, snap: ModelSnapshot) -> list[str]:
    """What the model lacks to run the condition: its entity, a field it reads (queryable, on the entity or a
    linked one)."""
    ent = snap.entity(entity)
    if ent is None:
        return [f"The model has no {entity.replace('_', ' ')}."]
    expr = parse(cond)
    if expr is None:
        return ["The condition can't be read."]
    out = []
    for ref in sorted(condition_fields(expr)):
        e_key, f_key = split_ref(entity, ref)
        e = snap.entity(e_key)
        f = e.field(f_key) if e else None
        if f is None or not f.is_queryable:
            out.append(f"The model has no field “{f_key.replace('_', ' ')}” on {e.label if e else e_key}.")
    return out


def rule_text(cond: dict[str, Any] | None, entity: str, snap: ModelSnapshot) -> str | None:
    """"Projects where Current state is one of Project Created, Active, On Hold"."""
    expr = parse(cond)
    ent = snap.entity(entity)
    if expr is None:
        return None
    return f"{ent.label if ent else entity} where {describe(expr, ent)}"


def visible(row: dict[str, Any], fields: dict[str, set[str]] | None) -> bool:
    """The person can see every field the condition reads (None: no limit)."""
    if fields is None:
        return True
    expr = parse(row["condition"])
    if expr is None:
        return False
    for ref in condition_fields(expr):
        e_key, f_key = split_ref(row["entity"], ref)
        if f_key not in fields.get(e_key, set()):
            return False
    return True


def same_rows(a: dict[str, Any] | None, b: dict[str, Any] | None) -> bool:
    """Two conditions are the same rule (an "=" of one value is "in" that value; values compared ignoring case)."""
    def canon(c: dict[str, Any] | None) -> Any:
        if not c:
            return None
        if c.get("op") in ("=", "!=") and isinstance(c.get("value"), str) and "other_field" not in c:
            return ("set", c["field"], "in" if c["op"] == "=" else "not_in", frozenset([c["value"].lower()]))
        if c.get("op") in ("in", "not_in") and "values" in c and "field" in c:
            return ("set", c["field"], c["op"], frozenset(str(v).lower() for v in c["values"]))
        return json.dumps(c, sort_keys=True)
    return bool(canon(a) == canon(b))


# --------------------------------------------------------------------------- order of authority


@dataclass
class Meaning:
    """What a term in the question means, by the order of authority."""

    term: str
    use: Literal["condition", "measure", "text"]
    condition: dict[str, Any] | None = None  # the named condition row
    measure_ref: str | None = None
    conflict: bool = False  # a condition and a certified measure disagree and the Admin hasn't chosen


def held(row: dict[str, Any]) -> bool:
    """A conflict the Admin hasn't settled: the certified measure answers meanwhile."""
    return bool(row.get("conflict")) and row.get("prefer") is None


def decide(row: dict[str, Any]) -> Literal["condition", "measure"]:
    """Which rule answers for one approved condition: the condition unless it disagrees with the certified measure
    and the Admin hasn't chosen it (or chose the measure)."""
    if row["source"] == "pack_measure":
        return "measure"
    if row.get("measure_ref") and (held(row) or row.get("prefer") == "measure"):
        return "measure"
    return "condition"


def said(question: str, row: dict[str, Any]) -> bool:
    """The question uses the condition's term or a synonym (words, singular)."""
    q = " ".join(singular(w) for w in words(question))
    return any(k and re.search(rf"\b{re.escape(k)}\b", q) for k in
               {term_key(n) for n in [row["term"], *(row.get("synonyms") or [])]})


def authority(question: str, conds: list[dict[str, Any]], hints: list[dict[str, Any]] | None = None
              ) -> list[Meaning]:
    """The meanings of the question's terms in the order of authority (DEC-29): an approved condition, else the
    certified measure (a condition taken from it, or one held by a conflict), else document text (a hint only)."""
    out: list[Meaning] = []
    seen: set[str] = set()
    for row in conds:
        if row["status"] != "approved" or not said(question, row):
            continue
        seen.add(row["term_key"])
        use = decide(row)
        out.append(Meaning(term=row["term"], use=use, condition=row, measure_ref=row.get("measure_ref"),
                           conflict=held(row)))
    for h in hints or []:
        key = term_key(h.get("term", ""))
        if key and key not in seen:
            seen.add(key)
            out.append(Meaning(term=h["term"], use="text"))
    return out


def apply_to_definitions(defs: list[dict[str, Any]], conds: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The approved definitions the knowledge route (US-204) may use, read through their conditions: a definition
    whose condition the Admin chose the measure over is left out; one whose conflict is unsettled is ``held`` (the
    certified measure answers and the conflict is noted, US-205); an expressible definition with no approved
    condition is held too (its term is stored once, by another definition). Text-only definitions pass as they are."""
    by_def = {str(c["definition_id"]): c for c in conds if c.get("definition_id") and c["status"] == "approved"}
    out = []
    for d in defs:
        if not d.get("expressible"):
            out.append(d)
            continue
        c = by_def.get(str(d["id"]))
        if c is None:
            out.append({**d, "held": True})
            continue
        if c.get("prefer") == "measure":
            continue
        conflict = bool(c.get("conflict"))
        checks = {**(d.get("checks") or {}), "conflict": conflict}
        if conflict and (c["conflict"] or {}).get("measure"):
            checks["measure"] = c["conflict"]["measure"]
        out.append({**d, "checks": checks, "held": held(c), "condition_id": str(c["id"])})
    return out


# --------------------------------------------------------------------------- storing


_UPSERT = """
    INSERT INTO rag.named_conditions AS n (id, tenant_id, connection_id, model_version_id, term, term_key, synonyms,
        entity, condition, source, definition_id, measure_ref, status, gaps, conflict, prefer, decided_by, decided_at,
        note)
    VALUES (:id, :t, CAST(:c AS uuid), CAST(:v AS uuid), :term, :key, CAST(:syn AS jsonb), :entity,
        CAST(:cond AS jsonb), :source, CAST(:def AS uuid), :m, :status, CAST(:gaps AS jsonb), CAST(:conflict AS jsonb),
        :prefer, CAST(:by AS uuid), CAST(:at AS timestamptz), :note)
    ON CONFLICT (tenant_id, connection_id, model_version_id, term_key) DO UPDATE SET
        term = EXCLUDED.term, synonyms = EXCLUDED.synonyms, entity = EXCLUDED.entity, condition = EXCLUDED.condition,
        source = EXCLUDED.source, definition_id = EXCLUDED.definition_id, measure_ref = EXCLUDED.measure_ref,
        status = EXCLUDED.status, gaps = EXCLUDED.gaps, conflict = EXCLUDED.conflict, prefer = EXCLUDED.prefer,
        decided_by = EXCLUDED.decided_by, decided_at = EXCLUDED.decided_at, note = EXCLUDED.note
    WHERE CAST(:overrides AS boolean) OR n.source = 'pack_measure' OR n.status = 'retired'
    RETURNING id
"""


async def _put(conn: AsyncConnection, tenant_id: str, *, connection_id: str, version_id: str, term: str,
               entity: str, cond: dict[str, Any], source: Source, status: str, synonyms: list[str] | None = None,
               definition_id: str | None = None, measure_ref: str | None = None, gaps_: list[str] | None = None,
               conflict: dict[str, Any] | None = None, prefer: str | None = None,
               decided: dict[str, Any] | None = None, overrides: bool = True) -> str | None:
    """Store one condition (``overrides`` False: only over a measure's condition or a retired one)."""
    row = await fetch_one(conn, _UPSERT, {
        "id": new_id(), "t": tenant_id, "c": connection_id, "v": version_id, "term": term[:80],
        "key": term_key(term), "syn": json.dumps(list(dict.fromkeys(s for s in synonyms or [] if s))[:20]),
        "entity": entity, "cond": json.dumps(cond), "source": source, "def": definition_id, "m": measure_ref,
        "status": status, "gaps": json.dumps(gaps_ or []), "conflict": json.dumps(conflict) if conflict else None,
        "prefer": prefer, "by": (decided or {}).get("by"), "at": (decided or {}).get("at"),
        "note": (decided or {}).get("note"), "overrides": overrides})
    return str(row["id"]) if row else None


def conflict_of(checks: dict[str, Any] | None, cond: dict[str, Any], entity: str,
                snap: ModelSnapshot) -> dict[str, Any] | None:
    """Both rules and a row count each, from a definition's trial counts (``definitions.run_checks``)."""
    checks = checks or {}
    if not checks.get("conflict") or not checks.get("measure"):
        return None
    return {"condition": {"rule": rule_text(cond, entity, snap), "figure": checks.get("figure")},
            "measure": checks["measure"], "checked_at": checks.get("checked_at")}


async def _previous(conn: AsyncConnection, connection_id: str, key: str) -> dict[str, Any] | None:
    row = await fetch_one(conn, "SELECT * FROM rag.named_conditions WHERE connection_id = CAST(:c AS uuid) AND "
                                "term_key = :k ORDER BY updated_at DESC LIMIT 1", {"c": connection_id, "k": key})
    return dict(row) if row else None


async def source_of(conn: AsyncConnection, document_id: str) -> Source:
    """``definition_sheet`` for a business-context spreadsheet (US-251), ``document`` otherwise."""
    row = await fetch_one(conn, "SELECT content_type FROM rag.knowledge_documents WHERE id = CAST(:d AS uuid)",
                          {"d": document_id})
    mime = str((row or {}).get("content_type") or "")
    return "definition_sheet" if "spreadsheet" in mime else "document"


async def from_definition(conn: AsyncConnection, tenant_id: str, d: dict[str, Any], snap: ModelSnapshot, *,
                          decided_by: str | None = None) -> str | None:
    """Keep the condition of one definition in step with its review: approved with a rule → an approved condition
    (its conflict from the approval's trial counts; an Admin's earlier choice kept while both rules are the same);
    rejected, retired or text only → its condition retired, and the measure's own condition back in its place."""
    cid, vid, did = str(d["connection_id"]), str(d["model_version_id"]), str(d["id"])
    rule = d.get("rule")
    if d["status"] != "approved" or not d.get("expressible") or not rule:
        await conn.execute(text("UPDATE rag.named_conditions SET status = 'retired' WHERE definition_id = "
                                "CAST(:d AS uuid) AND model_version_id = CAST(:v AS uuid)"), {"d": did, "v": vid})
        await from_measures(conn, tenant_id, connection_id=cid, version_id=vid, snap=snap)
        return None
    cond = from_rule(rule)
    conflict = conflict_of(d.get("checks"), cond, rule["entity"], snap)
    prev = await _previous(conn, cid, term_key(d["term"]))
    keep = prev is not None and prev["source"] != "pack_measure" and same_rows(prev["condition"], cond) and \
        prev.get("measure_ref") == d.get("measure_ref")
    prefer = prev["prefer"] if keep and prev is not None and conflict else None
    decided = {"by": str(prev["decided_by"]) if prev["decided_by"] else None, "at": prev["decided_at"],
               "note": prev["note"]} if prefer and prev is not None else None
    m = snap.measure(d["measure_ref"].split(":", 1)[1]) if d.get("measure_ref") else None
    return await _put(conn, tenant_id, connection_id=cid, version_id=vid, term=d["term"], entity=rule["entity"],
                      cond=cond, source=await source_of(conn, str(d["document_id"])), status="approved",
                      synonyms=[m.label, *m.synonyms] if m else [], definition_id=did, measure_ref=d.get("measure_ref"),
                      conflict=conflict, prefer=prefer, decided=decided)


async def from_measures(conn: AsyncConnection, tenant_id: str, *, connection_id: str, version_id: str,
                        snap: ModelSnapshot) -> int:
    """Each certified count measure with a condition proposes it, approved with the model version ("Open projects"
    → open on Projects); never over a definition's condition of the same term. Measures gone or changed retire
    theirs."""
    keys: list[str] = []
    for m in snap.measures:
        cond = from_measure(m)
        if cond is None:
            continue
        term = measure_term(m)
        if await _put(conn, tenant_id, connection_id=connection_id, version_id=version_id, term=term, entity=m.entity,
                      cond=cond, source="pack_measure", status="approved", synonyms=list(m.synonyms),
                      measure_ref=f"measure:{m.key}", overrides=False) is not None:
            keys.append(term_key(term))
    await conn.execute(text("UPDATE rag.named_conditions SET status = 'retired' WHERE connection_id = CAST(:c AS uuid) "
                            "AND model_version_id = CAST(:v AS uuid) AND source = 'pack_measure' AND status <> "
                            "'retired' AND NOT (term_key = ANY(CAST(:k AS text[])))"),
                       {"c": connection_id, "v": version_id, "k": keys})
    return len(keys)


async def carry_forward(conn: AsyncConnection, tenant_id: str, *, connection_id: str, version_id: str,
                        snap: ModelSnapshot) -> int:
    """A newly approved model version takes over the connection's conditions (after ``definitions.carry_forward``):
    each approved definition's condition again, keeping the Admin's choice on a conflict; a condition whose
    definition the new model can't express waits as ``proposed`` with its gaps; the measures' own conditions are
    read from the new model. The older versions' rows are retired."""
    defs = await fetch_all(conn, "SELECT * FROM rag.knowledge_definitions WHERE connection_id = CAST(:c AS uuid) AND "
                                 "model_version_id = CAST(:v AS uuid) AND status = 'approved'",
                           {"c": connection_id, "v": version_id})
    n = 0
    for d in defs:
        d = dict(d)
        prev = await _previous(conn, connection_id, d["term_key"])
        if d["expressible"] and d["rule"]:
            if d.get("checks") is None and prev and prev.get("conflict"):  # the carried definition has no counts yet
                d["checks"] = {"conflict": True, "measure": prev["conflict"].get("measure"),
                               "figure": (prev["conflict"].get("condition") or {}).get("figure"),
                               "checked_at": prev["conflict"].get("checked_at")}
            await from_definition(conn, tenant_id, d, snap)
            n += 1
        elif prev is not None and prev["source"] != "pack_measure" and prev["status"] == "approved":
            await _put(conn, tenant_id, connection_id=connection_id, version_id=version_id, term=prev["term"],
                       entity=prev["entity"], cond=prev["condition"], source=prev["source"], status="proposed",
                       synonyms=prev["synonyms"], definition_id=str(d["id"]), measure_ref=d.get("measure_ref"),
                       gaps_=gaps(prev["condition"], prev["entity"], snap) or list(d.get("gaps") or []))
            n += 1
    await from_measures(conn, tenant_id, connection_id=connection_id, version_id=version_id, snap=snap)
    await conn.execute(text("UPDATE rag.named_conditions SET status = 'retired' WHERE connection_id = CAST(:c AS uuid) "
                            "AND model_version_id <> CAST(:v AS uuid) AND status <> 'retired'"),
                       {"c": connection_id, "v": version_id})
    return n


async def settle(conn: AsyncConnection, tenant_id: str, *, connection_id: str, version_id: str,
                 snap: ModelSnapshot) -> None:
    """After re-reading a document: a condition whose definition is no longer approved (changed, dropped, retired)
    is retired, and the measures' own conditions come back in their place."""
    await conn.execute(text("UPDATE rag.named_conditions n SET status = 'retired' WHERE n.connection_id = CAST(:c AS "
                            "uuid) AND n.model_version_id = CAST(:v AS uuid) AND n.source <> 'pack_measure' AND "
                            "n.status = 'approved' AND NOT EXISTS (SELECT 1 FROM rag.knowledge_definitions d WHERE "
                            "d.id = n.definition_id AND d.status = 'approved' AND d.expressible)"),
                       {"c": connection_id, "v": version_id})
    await from_measures(conn, tenant_id, connection_id=connection_id, version_id=version_id, snap=snap)


_APPROVED = """
    SELECT n.* FROM rag.named_conditions n
    LEFT JOIN rag.knowledge_definitions d ON d.id = n.definition_id
    LEFT JOIN rag.knowledge_documents kd ON kd.id = d.document_id
    WHERE n.connection_id = CAST(:c AS uuid) AND n.model_version_id = CAST(:v AS uuid) AND n.status = 'approved'
      AND (n.source = 'pack_measure' OR (d.status = 'approved' AND kd.review_status = 'approved'))
    ORDER BY n.term_key
"""


async def approved(conn: AsyncConnection, connection_id: str, model_version_id: str) -> list[dict[str, Any]]:
    """The approved conditions of a connection's model version (a definition's only while it and its document are
    approved)."""
    rows = await fetch_all(conn, _APPROVED, {"c": connection_id, "v": model_version_id})
    return [dict(r) for r in rows]


# --------------------------------------------------------------------------- counts


def condition_spec(row: dict[str, Any], snap: ModelSnapshot) -> dict[str, Any]:
    """The condition as a count of its entity's rows (an ad-hoc measure, counted like a definition, US-203)."""
    adhoc = {"entity": row["entity"], "agg": "count", "where": row["condition"], "label": row["term"][:120],
             "plain_rule": (rule_text(row["condition"], row["entity"], snap) or row["term"])[:500]}
    return {"model_version_id": snap.model_version_id, "connection_id": snap.connection_id,
            "measure": {"ref": "measure:_adhoc", "adhoc": adhoc}}


async def count_both(service: QueryService, principal: Principal, row: dict[str, Any],
                     snap: ModelSnapshot) -> dict[str, Any] | None:
    """Both rules and a row count each (under the person's scope), or None when they select the same rows or there
    is no certified measure of the term."""
    m = snap.measure(row["measure_ref"].split(":", 1)[1]) if row.get("measure_ref") else None
    if m is None or m.status != "certified" or row["source"] == "pack_measure":
        return None
    mine = from_measure(m)
    if mine is not None and same_rows(mine, row["condition"]):
        return None
    figure = await _total(service, principal, condition_spec(row, snap))
    mfig = await _total(service, principal, {"model_version_id": snap.model_version_id,
                                             "connection_id": snap.connection_id,
                                             "measure": {"ref": f"measure:{m.key}"}})
    if not differs(figure, mfig) and figure is not None and mfig is not None:
        return None
    ent = snap.entity(m.entity)
    expr = m.expression
    mrule = describe(expr.where, ent) if isinstance(expr, AggExpr) and expr.where is not None else m.plain_rule
    return {"condition": {"rule": rule_text(row["condition"], row["entity"], snap), "figure": figure},
            "measure": {"ref": f"measure:{m.key}", "label": m.label, "rule": mrule, "figure": mfig},
            "checked_at": datetime.now(UTC).isoformat()}


# --------------------------------------------------------------------------- API


_SELECT = """
    SELECT n.*, c.name AS connection_name, v.version_no, u.display_name AS decided_by_name,
           d.document_id, kd.title AS document_title
    FROM rag.named_conditions n
    JOIN conn.connections c ON c.id = n.connection_id
    JOIN model.model_versions v ON v.id = n.model_version_id
    LEFT JOIN iam.users u ON u.id = n.decided_by
    LEFT JOIN rag.knowledge_definitions d ON d.id = n.definition_id
    LEFT JOIN rag.knowledge_documents kd ON kd.id = d.document_id
"""


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def condition_out(r: dict[str, Any], snap: ModelSnapshot | None) -> dict[str, Any]:
    m = snap.measure(r["measure_ref"].split(":", 1)[1]) if snap is not None and r["measure_ref"] else None
    return {
        "id": str(r["id"]), "status": r["status"], "term": r["term"], "synonyms": r["synonyms"] or [],
        "name": qualifier(r["term"], r["entity"], snap) if snap is not None else r["term"],
        "entity": r["entity"], "condition": r["condition"],
        "rule_text": rule_text(r["condition"], r["entity"], snap) if snap is not None else None,
        "source": r["source"], "gaps": r["gaps"] or [],
        "measure": {"ref": r["measure_ref"], "label": m.label if m else None} if r["measure_ref"] else None,
        "conflict": r["conflict"], "prefer": r["prefer"],
        "uses": decide(r) if r["status"] == "approved" else None,
        "definition": {"id": str(r["definition_id"]), "document": {"id": str(r["document_id"]),
                                                                    "title": r["document_title"]}}
        if r["definition_id"] else None,
        "connection": {"id": str(r["connection_id"]), "name": r["connection_name"]},
        "model_version": {"id": str(r["model_version_id"]), "version_no": r["version_no"]},
        "decided_by": {"id": str(r["decided_by"]), "display_name": r["decided_by_name"]} if r["decided_by"] else None,
        "decided_at": _iso(r["decided_at"]), "note": r["note"],
        "created_at": _iso(r["created_at"]), "updated_at": _iso(r["updated_at"]),
    }


async def _row(conn: AsyncConnection, condition_id: str, *, lock: bool = False) -> dict[str, Any]:
    try:
        row = await fetch_one(conn, _SELECT + " WHERE n.id = CAST(:id AS uuid)" + (" FOR UPDATE OF n" if lock else ""),
                              {"id": condition_id})
    except Exception:  # noqa: BLE001 - a malformed id is not found
        row = None
    if row is None:
        raise TelaError(ErrorCode.NOT_FOUND, "That condition was not found.")
    return dict(row)


@router.get("/conditions")
async def list_conditions(status: Literal["proposed", "approved", "retired", "all"] = Query(default="all"),
                          connection_id: str | None = Query(default=None, max_length=64),
                          conflicts: bool = Query(default=False),
                          principal: Principal = Depends(require(Perm.KNOWLEDGE_MANAGE))) -> dict[str, Any]:
    async with tenant_tx(principal.tenant_id) as conn:
        rows = await fetch_all(
            conn, _SELECT + " WHERE (:s = 'all' AND n.status <> 'retired' OR n.status = :s) "
                            "AND (CAST(:c AS uuid) IS NULL OR n.connection_id = CAST(:c AS uuid)) "
                            "AND (NOT :conf OR n.conflict IS NOT NULL) "
                            "ORDER BY (n.conflict IS NOT NULL AND n.prefer IS NULL) DESC, n.term_key, c.name LIMIT 500",
            {"s": status, "c": connection_id, "conf": conflicts})
        counts = await fetch_one(
            conn, "SELECT count(*) FILTER (WHERE status = 'proposed') AS proposed, count(*) FILTER (WHERE status = "
                  "'approved') AS approved, count(*) FILTER (WHERE status = 'approved' AND conflict IS NOT NULL AND "
                  "prefer IS NULL) AS conflicts FROM rag.named_conditions WHERE status <> 'retired' AND "
                  "(CAST(:c AS uuid) IS NULL OR connection_id = CAST(:c AS uuid))", {"c": connection_id})
        snaps: dict[str, ModelSnapshot] = {}
        items = []
        for r in rows:
            vid = str(r["model_version_id"])
            if vid not in snaps:
                snaps[vid] = await load_snapshot(conn, principal.tenant_id, vid)
            items.append(condition_out(dict(r), snaps[vid]))
    return {"items": items, "counts": {k: int(v) for k, v in dict(counts or {}).items()}}


class ChooseIn(BaseModel):
    use: Literal["condition", "measure"]
    note: str | None = Field(default=None, max_length=500)


@router.post("/conditions/{condition_id}/choose")
async def choose(condition_id: str, body: ChooseIn, request: Request,
                 principal: Principal = Depends(require(Perm.KNOWLEDGE_MANAGE))) -> dict[str, Any]:
    """Settle a conflict: Ask counts the term by the condition or by the certified measure from now on."""
    async with tenant_tx(principal.tenant_id) as conn:
        row = await _row(conn, condition_id, lock=True)
        if row["status"] != "approved" or not row["conflict"]:
            raise TelaError(ErrorCode.CONFLICT, "Only an approved condition that differs from a certified measure "
                                                "can be chosen.", {"status": row["status"]})
        note = " ".join((body.note or "").split()) or None
        await conn.execute(text("UPDATE rag.named_conditions SET prefer = :p, decided_by = CAST(:u AS uuid), "
                                "decided_at = now(), note = :note WHERE id = :id"),
                           {"p": body.use, "u": principal.user_id, "note": note, "id": row["id"]})
        await audit.record(conn, tenant_id=principal.tenant_id, action="knowledge.condition_chosen",
                           object_type="named_condition", object_id=condition_id, outcome="success",
                           actor_id=principal.user_id, correlation_id=_rid(request),
                           details={"term": row["term"], "use": body.use, "measure": row["measure_ref"],
                                    "connection_id": str(row["connection_id"]), "note": note})
        snap = await load_snapshot(conn, principal.tenant_id, str(row["model_version_id"]))
        return condition_out(await _row(conn, condition_id), snap)


@router.post("/conditions/{condition_id}/check")
async def check(condition_id: str, request: Request,
                principal: Principal = Depends(require(Perm.KNOWLEDGE_MANAGE))) -> dict[str, Any]:
    """Count both rules again (under the Admin's scope): the conflict is kept, cleared or found. A choice already
    made stays while the two still differ."""
    async with tenant_tx(principal.tenant_id) as conn:
        row = await _row(conn, condition_id)
        snap = await load_snapshot(conn, principal.tenant_id, str(row["model_version_id"]))
    if row["status"] == "retired":
        raise TelaError(ErrorCode.CONFLICT, "This condition is retired.", {"status": row["status"]})
    found = await count_both(request.app.state.query_service, principal, row, snap)
    async with tenant_tx(principal.tenant_id) as conn:
        await conn.execute(text("UPDATE rag.named_conditions SET conflict = CAST(:c AS jsonb), gaps = CAST(:g AS "
                                "jsonb), prefer = CASE WHEN CAST(:c AS jsonb) IS NULL THEN NULL ELSE prefer END "
                                "WHERE id = :id"),
                           {"c": json.dumps(found) if found else None,
                            "g": json.dumps(gaps(row["condition"], row["entity"], snap)), "id": row["id"]})
        return condition_out(await _row(conn, condition_id), snap)
