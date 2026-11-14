"""Look-alike detection and custom SQL semantic checks (FEAT-035, FEAT-036; RULE-011; US-084, US-085).

Candidates for a definition under review are certified measures (or dimensions, for groupings and bands) that
answer the "same question": the same base entity and aggregation, a shared synonym, or a name and rule whose
embedding similarity is at least 0.85 (local embedding model through the gateway; structural checks only when
it is unavailable). Both are run under the reviewer's scope and flagged when they differ by more than
max(0.5 %, 1 unit), with the predicate difference in words; identical results suggest Merge. Findings are
advisory: the Admin decides.

Custom SQL semantic checks compare the statement's aggregation, base table and WHERE columns with certified
measures on the same base and report conditions the SQL leaves out, with the row difference. Anything with
joins, subqueries, CTEs or unions is "review manually".
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection
from sqlglot import exp

from tela_api.core.db import fetch_all
from tela_api.identity.principal import Principal
from tela_api.llm.client import GatewayClient
from tela_api.query import custom_sql
from tela_api.query.service import QueryContext, QueryService
from tela_common.errors import TelaError
from tela_common.logging import get_logger
from tela_query_spec.expr import (
    AggExpr,
    All,
    Any_,
    AsOf,
    Check,
    Compare,
    InSet,
    Not,
    NullCheck,
    Today,
    condition_fields,
)
from tela_query_spec.model import Measure, ModelEntity, ModelSnapshot
from tela_query_spec.spec import PersonalMeasure

log = get_logger(__name__)
SIMILARITY = 0.85
AGG_OF = {exp.Count: "count", exp.Sum: "sum", exp.Avg: "avg", exp.Min: "min", exp.Max: "max"}
OPS = {"=": "is", "!=": "is not", "<": "is below", "<=": "is at most", ">": "is above", ">=": "is at least"}
DATE_OPS = {"=": "is", "!=": "is not", "<": "is before", "<=": "is on or before", ">": "is after",
            ">=": "is on or after"}
DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


def describe(cond: Any, ent: ModelEntity | None) -> str:
    """A measure condition in words, e.g. "Current state is not Cancelled"."""
    def label(key: str) -> str:
        f = ent.field(key) if ent else None
        return f.label if f else key.replace("_", " ")

    if isinstance(cond, Compare):
        v = cond.value
        rhs = label(cond.other_field) if cond.other_field else (
            "the reporting date" + (f" {v.asof_offset_days:+d} days" if v.asof_offset_days else "")
            if isinstance(v, AsOf) else
            "today" + (f" {v.offset_days:+d} days" if v.offset_days else "") if isinstance(v, Today) else str(v))
        dated = isinstance(v, AsOf | Today) or (isinstance(v, str) and DATE.fullmatch(v) is not None) or (
            cond.other_field is not None and ent is not None and (f := ent.field(cond.field)) is not None
            and f.data_type in ("date", "datetime"))
        return f"{label(cond.field)} {(DATE_OPS if dated else OPS)[cond.op]} {rhs}"
    if isinstance(cond, InSet):
        vals = ", ".join(str(v) for v in cond.values[:5])
        return f"{label(cond.field)} is {'not ' if cond.op == 'not_in' else ''}one of {vals}"
    if isinstance(cond, NullCheck):
        return f"{label(cond.field)} is {'not ' if cond.op == 'not_null' else ''}blank"
    if isinstance(cond, Check):
        rhs = ", ".join(str(v) for v in (cond.values or [])[:5]) if cond.values else str(cond.value)
        return f"a calculated value {OPS.get(cond.op, 'is one of' if cond.op == 'in' else 'is not one of')} {rhs}"
    if isinstance(cond, All):
        return " and ".join(describe(c, ent) for c in cond.all)
    if isinstance(cond, Any_):
        return "(" + " or ".join(describe(c, ent) for c in cond.any) + ")"
    if isinstance(cond, Not):
        return f"not ({describe(cond.not_, ent)})"
    return "a condition"


def differs(a: float | None, b: float | None) -> bool:
    if a is None or b is None:
        return False
    return abs(a - b) > max(0.005 * max(abs(a), abs(b)), 1)


def _agg(m: Measure) -> str | None:
    return m.expression.agg if isinstance(m.expression, AggExpr) else None


def _family(agg: str | None) -> str | None:
    return "count" if agg in ("count", "count_distinct") else agg


# --------------------------------------------------------------------------- embeddings


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


async def embed_texts(conn: AsyncConnection, gateway: GatewayClient | None, tenant_id: str,
                      texts: list[str]) -> list[list[float]] | None:
    """Embeddings with a per-tenant cache; None when the embedding model is unavailable."""
    if gateway is None or not texts:
        return None
    hashes = [hashlib.sha256(t.encode()).hexdigest() for t in texts]
    rows = await fetch_all(conn, "SELECT text_hash, CAST(embedding AS text) AS v FROM gov.definition_embeddings WHERE "
                                 "text_hash = ANY(CAST(:h AS text[]))", {"h": hashes})
    cached = {r["text_hash"]: json.loads(r["v"]) for r in rows}
    missing = [i for i, h in enumerate(hashes) if h not in cached]
    if missing:
        try:
            out = await gateway.embed(tenant_id=tenant_id, texts=[texts[i] for i in missing])
        except TelaError as exc:
            log.info("lookalike.embeddings_unavailable", code=str(exc.code))
            return None
        for i, vec in zip(missing, out["vectors"], strict=True):
            cached[hashes[i]] = vec
            await conn.execute(
                text("INSERT INTO gov.definition_embeddings (tenant_id, text_hash, model, embedding) VALUES (:t, :h, "
                     ":m, CAST(:v AS vector)) ON CONFLICT DO NOTHING"),
                {"t": tenant_id, "h": hashes[i], "m": out.get("model", "embed"), "v": json.dumps(vec)})
    return [cached[h] for h in hashes]


# --------------------------------------------------------------------------- candidates and comparison


async def _total(service: QueryService, principal: Principal, spec: dict[str, Any]) -> float | None:
    try:
        res = await service.execute(spec, principal, QueryContext(surface="certification_preview"))
    except TelaError:
        return None
    if res.get("hidden") or not res["rows"]:
        return 0.0 if not res.get("hidden") else None
    v = res["rows"][0][-1]
    return float(v) if isinstance(v, int | float) else None


async def _sql_total(service: QueryService, principal: Principal, item: dict[str, Any]) -> float | None:
    try:
        res = await service.execute_custom(item["sql_text"], connection_id=str(item["connection_id"]),
                                           model_version_id=str(item["model_version_id"]), principal=principal,
                                           ctx=QueryContext(surface="certification_preview"))
    except TelaError:
        return None
    idx = [i for i, c in enumerate(res["columns"]) if c["kind"] == "measure"]
    if len(idx) != 1:
        return None
    return float(sum(r[idx[0]] or 0 for r in res["rows"] if isinstance(r[idx[0]], int | float)))


async def find(conn: AsyncConnection, *, service: QueryService, gateway: GatewayClient | None, principal: Principal,
               kind: str, item: dict[str, Any], snap: ModelSnapshot) -> tuple[list[dict[str, Any]], bool]:
    """Look-alike findings for a definition (list) and whether embedding similarity was available."""
    base = {"model_version_id": snap.model_version_id, "connection_id": snap.connection_id}
    certified = [m for m in snap.measures if m.status == "certified"]
    findings: list[dict[str, Any]] = []

    if kind in ("grouping", "band"):
        d = item["definition"]
        src = d["source"].split(":", 1)[1]
        entity, field = (src.split(".", 1) if d["source"].startswith("field:") else
                         ((snap.dimension(src).entity, snap.dimension(src).field) if snap.dimension(src) else ("", "")))
        for dim in snap.dimensions:
            if dim.entity == entity and dim.field == field:
                findings.append({"similar_kind": "dimension", "similar_ref": f"dimension:{dim.key}", "similarity": 1.0,
                                 "reasons": ["same base field"], "label": dim.label,
                                 "difference_summary": f"The certified dimension {dim.label} shows the same field "
                                                       f"value by value; this {kind} buckets it.",
                                 "result_delta": None})
        return findings, False

    if kind == "join_measure":
        pm = PersonalMeasure.model_validate(item["definition"])
        entity, agg, label = pm.entity, pm.agg, pm.label
        item_value = await _total(service, principal, {**base, "measure": {"ref": f"measure:{pm.key}"},
                                                       "personal_measures": [pm.model_dump()]})
        item_where: set[str] = set()
    else:
        prepared = None
        try:
            prepared, _, _ = await service.custom_sql_check(
                item["sql_text"], connection_id=str(item["connection_id"]), principal=principal,
                ctx=QueryContext(surface="certification_preview"), model_version_id=str(item["model_version_id"]))
        except (custom_sql.CheckFailed, TelaError):
            return [], False
        ents = list({e.key for e in prepared.aliases.values()})
        entity = ents[0] if len(ents) == 1 else ""
        agg = _sql_agg(prepared.tree)
        label = item["title"]
        item_value = await _sql_total(service, principal, item)
        item_where = _where_columns(prepared.tree)

    structural = [m for m in certified if m.entity == entity and _family(_agg(m)) == _family(agg)]
    reasons: dict[str, list[str]] = {m.key: ["same base and aggregation"] for m in structural}
    norm = label.strip().lower()
    for m in certified:
        if norm == m.label.lower() or norm in (s.lower() for s in m.synonyms):
            reasons.setdefault(m.key, []).append("shared name or synonym")
    sims: dict[str, float] = {}
    vectors = await embed_texts(conn, gateway, principal.tenant_id,
                                [f"{label}: {item.get('plain_rule') or ''}"] + [f"{m.label}: {m.plain_rule}"
                                                                               for m in certified])
    if vectors:
        for m, vec in zip(certified, vectors[1:], strict=True):
            sims[m.key] = _cosine(vectors[0], vec)
            if sims[m.key] >= SIMILARITY:
                reasons.setdefault(m.key, []).append(f"similar meaning ({sims[m.key]:.2f})")
    ent = snap.entity(entity)
    for key, why in reasons.items():
        m = snap.measure(key)
        assert m is not None
        value = await _total(service, principal, {**base, "measure": {"ref": f"measure:{key}"}})
        where = m.expression.where if isinstance(m.expression, AggExpr) else None
        missing = sorted(condition_fields(where) - set(item_where))
        cols = {f.key: f.source_column.lower() for f in (ent.fields if ent else [])}
        missing = [f for f in missing if cols.get(f, f) not in item_where]
        if value is not None and item_value is not None and not differs(value, item_value):
            summary = "Same answer — Duplicate, consider Merge."
        elif where is not None and missing:
            summary = f"{m.label} only counts where {describe(where, ent)}; this definition does not."
        else:
            summary = f"{m.label} gives a different answer."
        findings.append({
            "similar_kind": "measure", "similar_ref": f"measure:{key}", "label": m.label,
            "similarity": round(max(1.0 if "same base and aggregation" in why else 0.9 if len(why) else 0,
                                    sims.get(key, 0.0)), 4),
            "reasons": why, "difference_summary": summary,
            "result_delta": {"item": item_value, "certified": value,
                             "delta": None if value is None or item_value is None else item_value - value,
                             "differs": differs(item_value, value)}})
    findings.sort(key=lambda f: -f["similarity"])
    return findings[:5], vectors is not None


def _sql_agg(tree: exp.Expression) -> str | None:
    for node in tree.find_all(exp.AggFunc):
        for cls, name in AGG_OF.items():
            if isinstance(node, cls):
                if name == "count" and isinstance(node.this, exp.Distinct):
                    return "count_distinct"
                return name
    return None


def _where_columns(tree: exp.Expression) -> set[str]:
    where = tree.args.get("where")
    return {c.name.lower() for c in where.find_all(exp.Column)} if where is not None else set()


async def semantic_checks(service: QueryService, principal: Principal, item: dict[str, Any],
                          snap: ModelSnapshot) -> list[dict[str, Any]]:
    """Compare a custom SQL statement with certified measures on the same base (US-085)."""
    try:
        prepared, _, _ = await service.custom_sql_check(
            item["sql_text"], connection_id=str(item["connection_id"]), principal=principal,
            ctx=QueryContext(surface="certification_preview"), model_version_id=str(item["model_version_id"]))
    except (custom_sql.CheckFailed, TelaError) as exc:
        message = exc.error.message if isinstance(exc, custom_sql.CheckFailed) else exc.message
        return [{"check": "valid", "status": "fails", "detail": message}]
    tree = prepared.tree
    if len(prepared.aliases) != 1 or list(tree.find_all(exp.Subquery)) or list(tree.find_all(exp.CTE)) or \
            not isinstance(tree, exp.Select) or tree.args.get("joins"):
        return [{"check": "complexity", "status": "review_manually",
                 "detail": "Too complex to compare automatically (joins, subqueries, CTEs or unions). Review it "
                           "manually; certification is still allowed."}]
    ent = next(iter(prepared.aliases.values()))
    agg = _sql_agg(tree)
    where_cols = _where_columns(tree)
    cols = {f.key: f.source_column.lower() for f in ent.fields}
    candidates = [m for m in snap.measures if m.status == "certified" and m.entity == ent.key
                  and _family(_agg(m)) == _family(agg)]
    if not candidates:
        return [{"check": "comparable_measure", "status": "info",
                 "detail": f"No certified measure on {ent.label} uses the same aggregation."}]
    sql_value = await _sql_total(service, principal, item)
    out: list[dict[str, Any]] = []
    base = {"model_version_id": snap.model_version_id, "connection_id": snap.connection_id}
    for m in candidates:
        where = m.expression.where if isinstance(m.expression, AggExpr) else None
        missing = [f for f in sorted(condition_fields(where)) if cols.get(f, f) not in where_cols]
        if not missing:
            out.append({"check": f"matches_{m.key}", "status": "matches", "measure": f"measure:{m.key}",
                        "detail": f"Applies the same conditions as {m.label}."})
            continue
        value = await _total(service, principal, {**base, "measure": {"ref": f"measure:{m.key}"}})
        labels = ", ".join((ent.field(f).label if ent.field(f) else f) for f in missing)
        out.append({"check": f"conditions_{m.key}", "status": "differs", "measure": f"measure:{m.key}",
                    "detail": f"{m.label} only counts where {describe(where, ent)}; this SQL has no condition on "
                              f"{labels}.",
                    "row_difference": None if value is None or sql_value is None else sql_value - value})
    return out
