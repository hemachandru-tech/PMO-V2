"""Direct SQL in Ask (ENH-028 US-257a; FEAT-078 / FEAT-031; DEC-30): AG-10 writes the SQL, the Custom SQL pipeline
checks, scopes and runs it.

DEC-30 amends ADR-001 for this one opt-in mode only. ``answer`` takes a question (and its ``QuestionFrame``, US-253)
for one connection and:

1. refuses engines that are not SQL (MongoDB, the Dataverse Web API: ``custom_sql.dialect_for``), a definition
   question (Knowledge answers those) and a tenant without a model, before any call;
2. sends AG-10 (``sql_draft``) the approved schema for the question (``schema_context``: hidden fields left out,
   certified measure rules), the kind of answer the frame asks for, and the approved named conditions the question
   uses (US-252, order of authority) written as SQL;
3. passes the draft through the Custom SQL pipeline (``QueryService.custom_sql_check``: validator, allow-list,
   hidden fields, scope injection, ``assert_scoped``, limits) and checks the frame's shape against the statement's
   columns (a list needs one row per record, a number one aggregate, a breakdown a grouping and an aggregate);
4. on a failed check asks AG-10 **once** to repair it with the reason; then refuses. A statement that writes, reads
   a hidden field, uses forbidden nodes or fails the scope is refused at once, never repaired; every failure is a
   ``custom_sql.rejected`` audit event (security ones as security events);
5. runs it on the single scoped path (``QueryService.execute_custom``: scope injected and re-verified, row cap,
   timeout, signed statement);
6. when it returns no rows, looks up the values its WHERE clause filters on (same scoped path, ``missing_values``);
   values the data doesn't hold go back to AG-10 **once** with the column's real values, and the new SQL is checked
   and run like the first. A failed value repair keeps the first answer, and the card says which values matched
   nothing (``value_notes``). Run 01a1ce59: "KPI colour" became ``tpg_totalactual_state IN (1, 2, 3)``, 0 rows.

The answer always carries the Custom SQL badge, shows the SQL and is not certified, so it can't go on a published
dashboard until certified (RULE-009). Ask routes a source whose answer mode is ``direct_sql`` (US-256) here with
the question's frame (US-253, US-257b); the gold runs measure the mode the same way (US-259).
"""

from __future__ import annotations

import difflib
import hashlib
import json
import re
import time
from dataclasses import dataclass, field
from typing import Any

from sqlglot import exp

from tela_api.ask.frame import QuestionFrame, Shape, rules_frame
from tela_api.ask.rule_check import Gap, figures_missing, repair_prompt, rule_gaps
from tela_api.audit import service as audit
from tela_api.content.common import visual_options
from tela_api.content.fields import model_context
from tela_api.content.sql_context import (
    AG10_ROLE,
    SqlDraftOut,
    context_columns,
    _terms,
    relevant_tables,
    ruled,
    schema_context,
    selected_rules,
)
from tela_api.core.db import fetch_one, tenant_tx
from tela_api.identity.principal import Principal
from tela_api.knowledge import conditions as named
from tela_api.llm import trace
from tela_api.llm.client import AgentFailed, run_agent
from tela_api.query import custom_sql
from tela_api.query.asof import reporting_date
from tela_api.query.compiler import DIALECTS
from tela_api.query.service import QueryContext, QueryService
from tela_api.query.viz import visualise
from tela_common.errors import ErrorCode, TelaError
from tela_query_spec.expr import All, Any_, Compare, InSet, Not, NullCheck, split_ref
from tela_query_spec.model import ModelSnapshot

AGENT, TASK = "AG-10", "sql_draft"
SURFACE = "ask_direct_sql"  # audit details; the query log records the run as an Ask query (surface "ask")
QUERY_SURFACE = "ask"
LIST_LIMIT = 500
PREVIEW_ROWS = 100
# Failures that show intent or policy, not a slip: refused at once, never sent back for a repair.
NO_REPAIR = frozenset({ErrorCode.SQL_MULTIPLE_STATEMENTS, ErrorCode.SQL_NOT_SELECT, ErrorCode.SQL_FORBIDDEN_NODE,
                       ErrorCode.SQL_FIELD_HIDDEN, ErrorCode.SCOPE_REWRITE_FAILED, ErrorCode.ENGINE_NOT_SUPPORTED})
PUBLISH_NOTE = "Direct SQL answers carry the Custom SQL badge and can't go on a published dashboard until certified."

SHAPE_RULES: dict[str, str] = {
    "number": ("Answer with one figure: a single aggregate (count, sum, min, max or average) and no GROUP BY. "
               "When the question asks for multiple figures (e.g. both count of X and count of Y), select each aggregate as its own column."),
    "list_of_records": (f"Answer with a list of records: one row per record, its name or title first and at most 5 "
                        f"other columns the question needs; no aggregates, no GROUP BY, never select *; at most "
                        f"{LIST_LIMIT} rows."),
    "record_detail": ("Answer with the matching record's details: its row, its name or title first and the columns "
                      "the question asks for; no aggregates."),
    "breakdown": ("Answer with a breakdown: select the columns the question groups by and the one aggregate it asks "
                  "for, then GROUP BY those columns."),
    "ranking": ("Answer with a ranking: the column ranked and its aggregate, GROUP BY that column, ORDER BY the "
                "aggregate and keep only the number asked for (10 when none)."),
    "trend": ("Answer with a trend: the date column cut to the period asked for (month when none) and the aggregate, "
              "grouped and ordered by that period."),
    "comparison": ("Answer with a comparison: the aggregate for each thing compared, side by side as columns or "
                   "grouped by the column that tells them apart."),
    "share": "Answer with a share: the part and the total as aggregates, or the percentage as one aggregate.",
    "distribution": "Answer with a distribution: bands of the value as one column and the count in each band.",
}
SHAPE_NAMES = {"number": "one figure", "list_of_records": "a list of records", "record_detail": "a record's details",
               "breakdown": "a breakdown", "ranking": "a ranking", "trend": "a trend", "comparison": "a comparison",
               "share": "a share", "distribution": "a distribution"}


@dataclass
class DirectAnswer:
    """What Direct SQL gives Ask: an answer (``ok``) or a refusal with its reason (``refused``)."""

    status: str  # ok | refused
    shape: str
    sql: str | None = None
    explanation: str | None = None
    message: str | None = None
    reason: str | None = None  # engine | definition | no_model | model_failed | check_failed | shape | run_failed
    repaired: bool = False
    checks: list[dict[str, Any]] = field(default_factory=list)
    conditions: list[dict[str, Any]] = field(default_factory=list)
    result: dict[str, Any] | None = None
    visual: str | None = None
    badge: str = "custom_sql"
    context: dict[str, Any] = field(default_factory=dict)  # what the answer relied on: data date, tables, rules
    certified: bool = False
    publishable: bool = False
    value_notes: list[str] = field(default_factory=list)  # filter values the data doesn't hold (no rows)
    viz: dict[str, Any] | None = None  # the Ask card's chart and the other suitable ones (US-260)

    def out(self) -> dict[str, Any]:
        body: dict[str, Any] = {
            "status": self.status, "shape": self.shape, "sql": self.sql, "explanation": self.explanation,
            "message": self.message, "reason": self.reason, "repaired": self.repaired, "checks": self.checks,
            "value_notes": self.value_notes,
            "conditions": self.conditions, "badge": self.badge, "badges": [self.badge], "certified": self.certified,
            "publishable": self.publishable, "publish_note": PUBLISH_NOTE, "suggested_visual": self.visual,
            "context": self.context}
        if self.result is not None:
            r = self.result
            body.update({k: r.get(k) for k in ("columns", "row_count", "truncated", "as_of", "duration_ms",
                                                 "scope", "model_version", "warnings")})
            body["rows"] = r["rows"][:PREVIEW_ROWS]
        return body


class _Rejected(Exception):
    def __init__(self, step: str, error: TelaError, checks: list[dict[str, Any]],
                 prepared: custom_sql.Prepared | None = None) -> None:
        super().__init__(error.message)
        self.step, self.error, self.checks = step, error, checks
        self.prepared = prepared  # set when only the answer shape failed: every safety check passed


# --------------------------------------------------------------------------- the prompt


def _sql_value(v: Any) -> str | None:
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, int | float):
        return repr(v)
    if isinstance(v, str):
        return "'" + v.replace("'", "''") + "'"
    return None  # AsOf / Today: said in words instead


def _column(ref: str, entity: str, snap: ModelSnapshot, hidden: frozenset[str]) -> str | None:
    e_key, f_key = split_ref(entity, ref)
    ent = snap.entity(e_key)
    f = ent.field(f_key) if ent else None
    if ent is None or f is None or f"{e_key}.{f_key}" in hidden:
        return None
    return f"{ent.source_object}.{f.source_column}"


def _leaf(c: Any, col: Any) -> str | None:
    if isinstance(c, Compare):
        left = col(c.field)
        right = col(c.other_field) if c.other_field else _sql_value(c.value)
        op = "<>" if c.op == "!=" else c.op
        return f"{left} {op} {right}" if left and right else None
    if isinstance(c, InSet):
        left, vals = col(c.field), [_sql_value(v) for v in c.values]
        if left is None or None in vals:
            return None
        return f"{left} {'NOT IN' if c.op == 'not_in' else 'IN'} ({', '.join(v for v in vals if v)})"
    if isinstance(c, NullCheck):
        left = col(c.field)
        return f"{left} IS {'NOT ' if c.op == 'not_null' else ''}NULL" if left else None
    return None  # a row value check (days late, a calculator): said in words instead


def condition_sql(cond: Any, entity: str, snap: ModelSnapshot, hidden: frozenset[str]) -> str | None:
    """A named condition as a SQL predicate on source columns, or None when it uses something SQL can't say plainly
    (a reporting-date offset, a calculator) or a field hidden from the person."""
    def col(ref: str) -> str | None:
        return _column(ref, entity, snap, hidden)

    def walk(c: Any) -> str | None:
        if isinstance(c, All | Any_):
            parts = [walk(x) for x in (c.all if isinstance(c, All) else c.any)]
            if None in parts:
                return None
            joiner = " AND " if isinstance(c, All) else " OR "
            return "(" + joiner.join(p for p in parts if p) + ")"
        if isinstance(c, Not):
            inner = walk(c.not_)
            return f"NOT ({inner})" if inner else None
        return _leaf(c, col)

    return walk(cond)


def _hidden_in(cond: Any, entity: str, hidden: frozenset[str]) -> bool:
    from tela_query_spec.expr import condition_fields

    return any("{}.{}".format(*split_ref(entity, ref)) in hidden for ref in condition_fields(cond))


def condition_hints(question: str, rows: list[dict[str, Any]], snap: ModelSnapshot,
                    hidden: frozenset[str]) -> list[dict[str, Any]]:
    """The approved conditions the question uses (order of authority, US-252), as SQL where it can be written; a
    held conflict gives the certified measure's rule. A condition reading a hidden field is left out."""
    out: list[dict[str, Any]] = []
    for m in named.authority(question, rows):
        row = m.condition or {}
        cond, entity = row.get("condition"), row.get("entity", "")
        if m.use == "measure" and m.measure_ref:
            measure = snap.measure(m.measure_ref.removeprefix("measure:"))
            if measure is not None and (rule := named.from_measure(measure)):
                cond, entity = rule, measure.entity
        expr = named.parse(cond)
        if expr is None or _hidden_in(expr, entity, hidden):
            continue
        out.append({"term": m.term, "entity": entity, "use": m.use, "conflict": m.conflict,
                    "sql": condition_sql(expr, entity, snap, hidden),
                    "rule": named.rule_text(cond, entity, snap)})
    return out


def without_ruled_terms(snap: ModelSnapshot, hints: list[dict[str, Any]], question: str) -> list[dict[str, Any]]:
    """Agreed terms that a business rule of the model also defines are left out: the rule is the organisation's own
    definition, and two conditions for one term make the model pick either."""
    rules = selected_rules(snap, set(relevant_tables(snap, question)), question)
    return [h for h in hints if not ruled(h["term"], rules, snap)]


def system_prompt(snap: ModelSnapshot, hidden: frozenset[str], question: str, frame: QuestionFrame,
                  hints: list[dict[str, Any]], values: dict[str, list[str]] | None = None,
                  as_of: str | None = None) -> str:
    """AG-10's instructions: generic SQL-writing rules only. What the data means (terms, default scope, which column
    names a record) comes from the approved model: column descriptions and business rules in the schema context."""
    dialect = DIALECTS.get(snap.engine)
    out = (AG10_ROLE + " No comments, no semicolons, no other tables and no :page_filters. The query is checked, "
           "limited to the rows the person may see and run.\n" + SHAPE_RULES[frame.shape]
           + " Where a business rule, an agreed term or a certified measure below matches the question, use its "
             "condition exactly instead of guessing values. Where a column lists its values, compare it only with "
             "those values, written as listed, and pick the column whose values hold what the question names. "
             "Use only table and column names that appear character for character in the context below. "
             "Group and label by the text column people read (a column described as a name or label), never by a "
             "key or GUID; a column described as the record's name is the one to show for a record. "
             "When the question names a page, tab or report, read it as its subject: a business rule or column "
             "for that subject decides the query; never filter a column on the page's name. "
             "First write the plan: up to 6 short steps naming the tables, how they join (on the links listed), the "
             "filters and business rules, any calculation, the grouping and the order. When the question needs more "
             "than one step (a ranking over several conditions, values from two tables, a calculation reused), write "
             "the SQL with one common table expression per step (WITH step1 AS (...), step2 AS (...) SELECT ...). "
             "Keep the explanation to one sentence and say which business rules you applied.")
    out += "\n\n" + schema_context(snap, hidden, question, dialect=dialect, values=values, as_of=as_of)
    hints = without_ruled_terms(snap, hints, question)
    if hints:
        out += "\n\nAgreed terms (use these conditions exactly):\n" + "\n".join(
            f"- {h['term']}: " + (f"WHERE {h['sql']}" if h["sql"] else h["rule"] or "") for h in hints)
    return out


# --------------------------------------------------------------------------- the checks


def shape_problem(tree: exp.Expression, shape: str) -> str | None:
    """Why the statement's columns don't give the frame's kind of answer, or None."""
    cols = custom_sql.output_columns(tree)
    aggs = [c for c in cols if c.kind == "measure"]
    dims = [c for c in cols if c.kind != "measure"]
    want = SHAPE_NAMES.get(shape, shape)
    if shape in ("list_of_records", "record_detail"):
        if aggs or not dims:
            return f"The question asks for {want}: select one row per record with its columns and no aggregates."
    elif shape == "number":
        # One figure, or several side by side when the question asks for several ("how many X and how many Y"):
        # aggregates only, no grouping columns.
        if not aggs or dims:
            return f"The question asks for {want}: select the aggregate figure(s) only, with no grouping columns."
    elif shape in ("breakdown", "ranking", "trend", "distribution"):
        if not aggs or not dims:
            return f"The question asks for {want}: select the grouping column(s) and an aggregate."
    elif not aggs:
        return f"The question asks for {want}: select the aggregates compared."
    return None


async def _check(service: QueryService, principal: Principal, connection_id: str, version_id: str, sql: str,
                 shape: str, request_id: str | None, turn_id: str | None) -> custom_sql.Prepared:
    prepared: custom_sql.Prepared
    try:
        prepared, _, _ = await service.custom_sql_check(
            sql, connection_id=connection_id, principal=principal, model_version_id=version_id,
            ctx=QueryContext(surface=QUERY_SURFACE, request_id=request_id, turn_id=turn_id))
    except custom_sql.CheckFailed as failed:
        raise _Rejected(failed.step, failed.error, failed.checks) from failed
    if problem := shape_problem(prepared.tree, shape):
        checks = [*prepared.checks, {"name": "answer_shape", "ok": False, "code": "shape", "detail": problem}]
        raise _Rejected("answer_shape", TelaError(ErrorCode.VALIDATION_ERROR, problem), checks, prepared)
    prepared.checks.append({"name": "answer_shape", "ok": True, "detail": shape})
    return prepared


def _why(failed: _Rejected) -> dict[str, Any]:
    """What exactly failed, for the audit and for AG-10's repair: the parser's words, the scope rewrite's reason and
    the object it named (kept for admins; never row values)."""
    d = failed.error.details or {}
    admin = d.get("admin_detail") or {}
    out = {"reason": d.get("reason") or admin.get("reason"), "parse_error": d.get("parse_error"),
           "object": admin.get("object") or admin.get("entity"), "field": admin.get("field")}
    return {k: v for k, v in out.items() if v}


REPAIRABLE_SCOPE = frozenset({"object_not_in_model", "unaliased_table"})  # a misnamed table, not a scope breach


def _repairable(failed: _Rejected) -> bool:
    """A scope rewrite that failed only because a table was misnamed gets the one repair like any other mistake (the
    corrected SQL passes every check again); a real scope problem stays refused."""
    return (failed.error.code == ErrorCode.SCOPE_REWRITE_FAILED
            and _why(failed).get("reason") in REPAIRABLE_SCOPE)


def repair_request(question: str, sql: str, failed: _Rejected) -> str:
    """AG-10's one repair: its SQL, the failed step and the precise reason, so the fix is not a guess."""
    why = _why(failed)
    lines = [question, "", "Your previous SQL:", sql, "",
             f"It failed the check ({failed.step}): {failed.error.message}"]
    if "parse_error" in why:
        lines.append(f"The parser said: {why['parse_error']}")
    if why.get("reason") and why["reason"] != "parse error":
        lines.append(f"Reason: {why['reason']}" + (f" ({why['object']})" if "object" in why else ""))
    if allowed := (failed.error.details or {}).get("allowed"):
        lines.append("Tables you may use, exactly as named: " + ", ".join(allowed))
    lines.append("Write a corrected query. Keep every table and column name exactly as the schema lists it.")
    return "\n".join(lines)


async def _rejected(principal: Principal, connection_id: str, failed: _Rejected, request_id: str | None,
                    attempt: int) -> None:
    security = failed.error.code in custom_sql.SECURITY_CODES
    async with tenant_tx(principal.tenant_id) as conn:
        await audit.record(conn, tenant_id=principal.tenant_id, action="custom_sql.rejected",
                           object_type="connection", object_id=connection_id, outcome="deny",
                           actor_id=principal.user_id, correlation_id=request_id,
                           severity="security" if security else "normal",
                           details={"step": failed.step, "code": str(failed.error.code), "surface": SURFACE,
                                    "attempt": attempt, **_why(failed)})
    await trace.step("validate", f"The drafted SQL failed the check: {failed.step}", agent=AGENT, status="failed",
                     detail={"step": failed.step, "code": str(failed.error.code), "message": failed.error.message,
                             "attempt": attempt})


# --------------------------------------------------------------------------- the filter values (run 01a1ce59)
#
# A query that passes every check can still filter on values the data doesn't hold (``tpg_totalactual_state IN
# (1, 2, 3)``) and answer "No rows match" as if that were the answer. When a query returns no rows, the values its
# WHERE clause compares columns with are looked up in the data, through the same scoped path (so only values the
# person may see come back); values that aren't there go back to AG-10 once with the column's real values.

MAX_PROBED = 3  # filter columns looked up when a query returns no rows

# --------------------------------------------------------------------------- column values up front
#
# A model that only sees column names guesses which column holds a word and how it is spelled ("manager" in `role`
# when `staff_type` holds it; status 'done' when the data says 'C'). When the tenant allows samples (AI settings ->
# "Sample field values"), the listed text columns with few distinct values come with those values. Values are read
# through the same scoped path as any answer (only rows the person may see), never for personal, sensitive or
# confidential fields or for emails, phone numbers, names, identifiers and free text, and are cached briefly.

PROFILE_COLUMNS = 30  # text columns profiled for one question (the context's order: the question's first)
PROFILE_DISTINCT = 25  # up to this many distinct values: always a short list
PROFILE_CODES = 100  # up to this many, when every value is a short code (``net_sales``, ``order_count``)
CODE_MAX_LEN = 40  # longest value that still counts as a code
PROFILE_TTL_S = 900
NO_VALUES_SENSITIVITY = frozenset({"personal", "sensitive", "confidential"})
NO_VALUES_SEMANTIC = frozenset({"email", "phone", "person_name", "identifier", "free_text"})
_profiles: dict[tuple[str, str, str], tuple[float, list[str] | None]] = {}


def profiled(f: Any) -> bool:
    """A text column whose distinct values may be shown to the model."""
    return (f.data_type == "text" and f.is_queryable and f.sensitivity not in NO_VALUES_SENSITIVITY
            and (f.semantic_type or "") not in NO_VALUES_SEMANTIC)


def values_sql(entity: Any, column: str, dialect: str) -> str:
    """``SELECT DISTINCT col FROM t WHERE col IS NOT NULL`` with one row more than a short list may hold."""
    col = exp.column(column, quoted=True)
    q = (exp.select(col).distinct().from_(exp.to_table(entity.source_object))
         .where(exp.Not(this=exp.Is(this=col.copy(), expression=exp.Null()))))
    return str(q.limit(PROFILE_CODES + 1).sql(dialect=dialect))


SAMPLE_ROWS = 5  # rows of each short-listed table shown to AG-10, so it sees how the data really looks
SAMPLE_TABLES = 3
SAMPLE_COLUMNS = 14
SAMPLE_CHARS = 40
_samples: dict[tuple[str, str, str], tuple[float, list[str]]] = {}


def sample_sql(entity: Any, columns: list[str], dialect: str) -> str:
    """``SELECT c1, c2 … FROM t LIMIT 5``: a few real rows of a table (only columns the model shows)."""
    q = exp.select(*(exp.column(c, quoted=True) for c in columns)).from_(exp.to_table(entity.source_object))
    return str(q.limit(SAMPLE_ROWS).sql(dialect=dialect))


def sample_columns(entity: Any, hidden: frozenset[str]) -> list[Any]:
    """The columns of a sample: queryable, not hidden, not personal, not json; record name and vocabularies first."""
    ok = [f for f in entity.fields if f.is_queryable and f"{entity.key}.{f.key}" not in hidden
          and f.sensitivity != "personal" and f.data_type != "json" and f.semantic_type not in ("json", "free_text")]
    first = set(entity.display_fields or [])
    ok.sort(key=lambda f: (f.key not in first, not f.values, f.data_type not in ("date", "datetime"), f.key))
    return ok[:SAMPLE_COLUMNS]


async def sample_rows(run: Any, snap: ModelSnapshot, hidden: frozenset[str], tables: list[str], dialect: str,
                      connection_id: str) -> dict[str, list[str]]:
    """{"table": ["col | col …", "v | v …" × 5]} for the first short-listed tables. Cached per model version for a
    few minutes; a table that can't be read is skipped (samples only ever add to the context)."""
    out: dict[str, list[str]] = {}
    now = time.monotonic()
    by_key = {e.key: e for e in snap.entities}
    for t in tables[:SAMPLE_TABLES]:
        e = by_key.get(t)
        if e is None:
            continue
        key = (connection_id, str(snap.model_version_id), t)
        hit = _samples.get(key)
        if hit is None or now - hit[0] > PROFILE_TTL_S:
            cols = sample_columns(e, hidden)
            lines: list[str] = []
            if cols:
                try:
                    found = await run(sample_sql(e, [f.source_column for f in cols], dialect))
                    lines = [" | ".join(f.source_column for f in cols)] + [
                        " | ".join("" if v is None else str(v)[:SAMPLE_CHARS] for v in r) for r in found["rows"]]
                except TelaError:
                    lines = []
            hit = (now, lines)
            _samples[key] = hit
        if hit[1]:
            out[e.source_object] = hit[1]
    return out


def samples_context(samples: dict[str, list[str]]) -> str:
    if not samples:
        return ""
    parts = ["\n\nSAMPLE ROWS (5 real rows per table, to see how values are written; they are not the answer):"]
    for table, lines in samples.items():
        parts.append(f"{table}:\n" + "\n".join(f"  {ln}" for ln in lines))
    return "\n".join(parts)


def conversation_note(conversation: list[dict[str, Any]] | None) -> str:
    """The earlier turns of this conversation with the SQL each ran: a follow-up ("give the list", "only these", "by
    month") keeps the earlier answer's tables, filters and population; a new topic ignores them."""
    turns = [t for t in conversation or [] if t.get("question")]
    if not turns:
        return ""
    lines = ["EARLIER IN THIS CONVERSATION (oldest first). If the new question continues it (\"the list\", "
             "\"these\", \"them\", \"by month\", \"only X\", \"which ones\"), keep the latest earlier answer's "
             "tables, WHERE conditions and population exactly and change only what the new question asks (for "
             "example a count becomes the list of the same records). If the new question is a new topic, ignore "
             "this."]
    for i, t in enumerate(turns, 1):
        lines.append(f"{i}. Question: {t['question']}")
        if t.get("sql"):
            lines.append(f"   SQL: {t['sql']}")
        if t.get("row_count") is not None:
            lines.append(f"   Rows returned: {t['row_count']}")
    return "\n".join(lines)


def short_list(vals: list[str]) -> bool:
    """A column's values worth showing: a short list, or a longer list of short codes (not free text)."""
    if len(vals) <= PROFILE_DISTINCT:
        return True
    return len(vals) <= PROFILE_CODES and all(len(v) <= CODE_MAX_LEN and v.count(" ") <= 3 for v in vals)


async def column_values(run: Any, snap: ModelSnapshot, hidden: frozenset[str], question: str, dialect: str,
                        connection_id: str) -> dict[str, list[str]]:
    """{"entity.field": [values]} for the context's short-list text columns. A lookup that fails is skipped:
    values only ever add to the context."""
    out: dict[str, list[str]] = {}
    now = time.monotonic()
    picked = [(e, f) for e, f in context_columns(snap, hidden, question)
              if profiled(f) and f"{e.key}.{f.key}" not in hidden][:PROFILE_COLUMNS]
    for e, f in picked:
        key = (connection_id, str(snap.model_version_id), f"{e.key}.{f.key}")
        hit = _profiles.get(key)
        if hit is None or now - hit[0] > PROFILE_TTL_S:
            try:
                found = await run(values_sql(e, f.source_column, dialect))
                vals = [str(r[0]) for r in found["rows"] if r and r[0] is not None]
                hit = (now, vals if short_list(vals) else None)
            except TelaError:
                hit = (now, None)
            _profiles[key] = hit
        if hit[1]:
            out[f"{e.key}.{f.key}"] = hit[1]
    return out


def matched_nothing(result: dict[str, Any]) -> bool:
    """No rows, or one row of totals that are all 0 or blank: how an empty match looks for a count or a sum."""
    rows = result.get("rows") or []
    if not rows:
        return True
    return len(rows) == 1 and all(v in (0, None, "") or (isinstance(v, (int, float)) and v == 0) for v in rows[0])
SAMPLE = 25  # values of a column shown to AG-10 and in the note
_NO = object()


@dataclass
class Filter:
    """One ``column = value`` / ``column IN (values)`` of a WHERE clause, on a governed table."""

    entity: Any
    column: str  # the source column
    literals: list[exp.Expression]
    values: list[Any]


def _literal(node: exp.Expression) -> Any:
    if isinstance(node, exp.Literal):
        if node.is_string:
            return str(node.this)
        try:
            return int(node.this)
        except ValueError:
            return float(node.this)
    if isinstance(node, exp.Neg) and isinstance(node.this, exp.Literal) and not node.this.is_string:
        v = _literal(node.this)
        return -v if v is not _NO else _NO
    if isinstance(node, exp.Boolean):
        return bool(node.this)
    return _NO


def value_key(v: Any) -> str:
    """How a filter value and a value from the data are compared: numbers by value (1 = 1.0 = '1'), text without
    case or outer spaces."""
    from decimal import Decimal, InvalidOperation

    if isinstance(v, bool):
        return str(v).lower()
    try:
        return format(Decimal(str(v).strip()).normalize(), "f")
    except (InvalidOperation, ValueError):
        return str(v).strip().lower()


def _negated(node: exp.Expression, stop: exp.Expression) -> bool:
    p = node.parent
    while p is not None and p is not stop:
        if isinstance(p, exp.Not):
            return True
        p = p.parent
    return False


def filters(tree: exp.Expression, aliases: dict[str, Any]) -> list[Filter]:
    """The WHERE clause's equality and IN filters on columns of governed tables; NOT, <> and NOT IN are left out
    (an exclusion doesn't empty a result by a wrong value)."""
    where = tree.args.get("where")
    if where is None:
        return []
    out: dict[tuple[str, str], Filter] = {}
    for node in where.find_all(exp.EQ, exp.In):
        if _negated(node, where):
            continue
        if isinstance(node, exp.In):
            if node.args.get("query") is not None or not isinstance(node.this, exp.Column):
                continue
            col, lits = node.this, list(node.expressions)
        else:
            left, right = node.this, node.expression
            col, other = (left, right) if isinstance(left, exp.Column) else (right, left)
            if not isinstance(col, exp.Column):
                continue
            lits = [other]
        values = [_literal(x) for x in lits]
        if not values or any(v is _NO for v in values):
            continue
        ent = _entity_of(col, aliases)
        name = col.name.lower()
        if ent is None or not any(f.source_column.lower() == name for f in ent.fields):
            continue
        f = out.setdefault((ent.key, name), Filter(ent, next(
            f.source_column for f in ent.fields if f.source_column.lower() == name), [], []))
        for lit, v in zip(lits, values, strict=True):
            if value_key(v) not in {value_key(x) for x in f.values}:
                f.literals.append(lit.copy())
                f.values.append(v)
    return list(out.values())


def _entity_of(col: exp.Column, aliases: dict[str, Any]) -> Any:
    if col.table:
        return aliases.get(col.table.strip('"').lower())
    if len(aliases) == 1:
        return next(iter(aliases.values()))
    name = col.name.lower()
    holders = [e for e in aliases.values() if any(f.source_column.lower() == name for f in e.fields)]
    return holders[0] if len(holders) == 1 else None


@dataclass
class Missing:
    """Filter values a column doesn't hold, and some of the values it does."""

    column: str
    table: str
    values: list[Any]
    known: list[Any]

    def note(self) -> str:
        said = ", ".join(repr(v) for v in self.values)
        if not self.known:
            return f"{self.column} has no values at all in the rows you may see, so {said} matches nothing."
        return (f"{self.column} has no {said}; its values include "
                + ", ".join(repr(v) for v in self.known) + ".")


def probe_sql(f: Filter, dialect: str, *, among: bool) -> str:
    """``SELECT DISTINCT col FROM t [WHERE col IN (values)]``, at most ``SAMPLE`` rows: which filter values the data
    holds (``among``), or a sample of the values it does."""
    column = exp.column(f.column, quoted=True)
    q = exp.select(column).distinct().from_(exp.to_table(f.entity.source_object))
    if among:
        q = q.where(exp.In(this=column.copy(), expressions=[x.copy() for x in f.literals]))
    return str(q.limit(SAMPLE).sql(dialect=dialect))


async def missing_values(run: Any, prepared: custom_sql.Prepared) -> list[Missing]:
    """The filter values the data doesn't hold, by up to two small scoped queries per filter column (``run``
    takes SQL and returns a result). A lookup that fails is skipped: this check only ever adds a note."""
    out: list[Missing] = []
    for f in filters(prepared.tree, prepared.aliases)[:MAX_PROBED]:
        try:
            found = await run(probe_sql(f, prepared.dialect, among=True))
            held = {value_key(r[0]) for r in found["rows"]}
            absent = [v for v in f.values if value_key(v) not in held]
            if not absent:
                continue
            sample = await run(probe_sql(f, prepared.dialect, among=False))
        except TelaError:
            continue
        known = [r[0] for r in sample["rows"] if r and r[0] is not None]
        out.append(Missing(f.column, f.entity.source_object, absent, known))
    return out


def value_repair_prompt(question: str, sql: str, missing: list[Missing]) -> str:
    lines = "\n".join(f"- {m.note()}" for m in missing)
    return (f"{question}\n\nYour previous SQL:\n{sql}\n\nIt ran and returned no rows, because the data doesn't hold "
            f"these filter values:\n{lines}\nIf the column is the one the question means, use its real values; if "
            f"it is not, use another listed column. Write a corrected query.")


# --------------------------------------------------------------------------- the answer


VAGUE = {"give", "show", "list", "lists", "get", "me", "all", "the", "a", "an", "of", "please", "display", "details",
         "detail", "data", "records", "record", "rows", "them", "these", "those", "it", "this", "that", "more", "again",
         "full", "complete", "table", "everything", "same", "now", "only", "what", "is", "are", "there", "how", "many",
         "much", "which", "ones", "one", "count", "number", "total", "who", "where", "when", "do", "does", "we",
         "have", "has", "in", "on", "for", "and", "or", "by", "with", "to", "can", "you", "i", "my", "our"}


def names_nothing(snap: ModelSnapshot, question: str) -> bool:
    """True when the question names nothing the model knows (no table, column, measure or stored value): "give the
    list" in a new chat. Such a question is a follow-up without anything to follow, so it is asked about, not
    guessed. Any word that is in the model is enough."""
    qw = {w for w in re.findall(r"[a-z0-9]+", question.lower()) if w not in VAGUE and len(w) > 1}
    if not qw:
        return True
    known: set[str] = set()
    for e in snap.entities:
        known |= set(re.findall(r"[a-z0-9]+", f"{e.key} {e.label}".lower().replace("_", " ")))
        for f in e.fields:
            if not f.is_queryable:
                continue
            known |= set(re.findall(r"[a-z0-9]+", f"{f.key} {f.label}".lower().replace("_", " ")))
            for v in f.values:
                known |= set(re.findall(r"[a-z0-9]+", str(v).lower()))
    for m in snap.measures:
        known |= set(re.findall(r"[a-z0-9]+", f"{m.key} {m.label}".lower().replace("_", " ")))
    for r in snap.rules:
        known |= set(re.findall(r"[a-z0-9]+", (r.term or "").lower()))
    def stem(w: str) -> str:  # coaches → coach, deliveries → delivery, players → player
        for end, put in (("ies", "y"), ("ches", "ch"), ("shes", "sh"), ("sses", "ss"), ("xes", "x"), ("s", "")):
            if w.endswith(end) and len(w) > len(end) + 2:
                return w[: -len(end)] + put
        return w

    stems = sorted({stem(k) for k in known})
    pool = set(stems)
    return not any(stem(w) in pool or (len(w) >= 4 and difflib.get_close_matches(stem(w), stems, n=1, cutoff=0.8))
                   for w in qw)


def _refuse(shape: str, reason: str, message: str, **kw: Any) -> DirectAnswer:
    trace.set_outcome("refused")
    return DirectAnswer(status="refused", shape=shape, reason=reason, message=message, **kw)


async def answer(service: QueryService, gateway: Any, principal: Principal, *, connection_id: str, question: str,
                 frame: QuestionFrame | None = None, request_id: str | None = None,
                 turn_id: str | None = None, surface: str = QUERY_SURFACE,
                 scope_note: str | None = None, scope: dict[str, Any] | None = None,
                 conversation: list[dict[str, Any]] | None = None) -> DirectAnswer:
    """Answer one question on one connection with SQL AG-10 writes, checked and run by the Custom SQL pipeline.
    ``surface`` is what the query log records the run as: an Ask query, or ``evaluation`` for a gold run (US-259)."""
    frame = frame or rules_frame(question)
    shape: Shape = frame.shape
    async with tenant_tx(principal.tenant_id) as conn:
        connection = await service._connection(conn, connection_id)
        snap, hidden = await model_context(conn, principal, connection_id)
        rows = await named.approved(conn, connection_id, snap.model_version_id)
        llm = await fetch_one(conn, "SELECT allow_samples FROM ops.llm_settings")
    try:
        dialect = custom_sql.dialect_for(connection["engine"], connection["descriptor"])
    except TelaError as exc:
        return _refuse(shape, "engine", f"Direct SQL isn't available for this source. {exc.message}")

    async def run(statement: str) -> dict[str, Any]:
        return await service.execute_custom(statement, connection_id=connection_id,
                                            model_version_id=snap.model_version_id, principal=principal,
                                            ctx=QueryContext(surface=surface, request_id=request_id,
                                                             turn_id=turn_id))
    if shape == "definition":
        return _refuse(shape, "definition", "Direct SQL answers questions about the data; what a term means is in "
                       "Knowledge.")
    if gateway is None:
        return _refuse(shape, "no_model", "Direct SQL needs a language model, and none is set up.")

    # A follow-up reads its tables, columns and samples with the conversation's earlier questions.
    topic = " ".join([*(t["question"] for t in conversation or [] if t.get("question")), question])
    if not conversation and not scope_note and names_nothing(snap, question):
        tables = ", ".join(e.label for e in snap.entities[:6])
        return _refuse(shape, "unclear", "This reads like a follow-up, but there is no earlier answer in this chat. "
                       f"What should it be about? For example one of: {tables}.")
    hints = condition_hints(question, rows, snap, hidden)
    values: dict[str, list[str]] = {}
    if llm is not None and llm["allow_samples"]:  # the tenant allows the model to see a column's values
        values = await column_values(run, snap, hidden, topic, dialect, connection_id)
        # The vocabulary the model builder profiled is complete and approved: it wins over a value probe.
        for e in snap.entities:
            for f in e.fields:
                if f.values and f.is_queryable and f"{e.key}.{f.key}" not in hidden:
                    values[f"{e.key}.{f.key}"] = list(f.values)
        await trace.step("decision", f"Direct SQL: {len(values)} columns' values given to AG-10", agent=AGENT,
                         detail={"columns": sorted(values)})
    as_of = reporting_date(snap.settings, principal.time_zone).isoformat()
    system = system_prompt(snap, hidden, topic, frame, hints, values, as_of=as_of)
    listed = relevant_tables(snap, topic)
    samples: dict[str, list[str]] = {}
    if llm is not None and llm["allow_samples"]:  # a few real rows of the short-listed tables
        samples = await sample_rows(run, snap, hidden, listed, dialect, connection_id)
        system += samples_context(samples)
    used_rules = selected_rules(snap, set(listed), question)
    by_key = {e.key: e for e in snap.entities}
    context = {"data_date": as_of, "data_date_set": bool(snap.settings.get("reporting_date")),
               "model_version": snap.version_no,
               "tables": [by_key[k].source_object for k in listed if k in by_key],
               "rules": [{"term": r.term, "sql": r.sql, "always": r.always, "origin": r.origin, "cite": r.cite}
                         for r in used_rules],
               "rules_in_model": sum(1 for r in snap.rules if r.status == "ok"),
               "prompt_chars": len(system)}
    await trace.step("decision", f"Direct SQL: asking AG-10 for {SHAPE_NAMES.get(shape, shape)}", agent=AGENT,
                     detail={"shape": shape, "conditions": [h["term"] for h in hints],
                             "rules": [r.term for r in used_rules], "data_date": as_of,
                             "prompt_chars": len(system)})
    # Asked on a dashboard: the dashboard's widgets and the reader's filters travel with the question (and with the
    # answer memory's key, so the same words on another dashboard or with other filters are drafted afresh).
    memo = conversation_note(conversation)
    user = "\n\n".join(x for x in (memo, f"NEW QUESTION: {question}" if memo else question, scope_note) if x)
    sql, note, repaired = "", None, False
    if scope:
        context["dashboard"] = scope
    if samples:
        context["samples"] = sorted(samples)
    if memo:
        context["earlier_turns"] = len(conversation or [])
    checks: list[dict[str, Any]] = []
    prepared: custom_sql.Prepared | None = None
    shape_note: str | None = None
    # Answer memory: the same question on the same approved model reuses the SQL that passed every check last
    # time, so it gets the same answer every time (no LLM call); it still goes through the check and runs live.
    mkey = memory_key(principal.tenant_id, connection_id, snap.model_version_id,
                      question + "".join(f"|{hashlib.sha256(x.encode()).hexdigest()}" for x in (scope_note, memo)
                                         if x))
    remembered = await recall(service, mkey)
    if remembered and remembered.get("sql"):
        try:
            prepared = await _check(service, principal, connection_id, snap.model_version_id, remembered["sql"],
                                    shape, request_id, turn_id)
            sql, note = remembered["sql"], remembered.get("explanation")
            context["from_memory"] = True
            await trace.step("decision", "Direct SQL: reused the checked SQL of the same question", agent=AGENT)
        except _Rejected:
            prepared = None
    for attempt in (1, 2) if prepared is None else ():
        try:
            out = await run_agent(gateway, SqlDraftOut, tenant_id=principal.tenant_id, user_id=principal.user_id,
                                  agent=AGENT, task=TASK, system=system, user=user, turn_id=turn_id)
        except AgentFailed as exc:
            return _refuse(shape, "model_failed", "I couldn't write SQL for that question.", sql=sql or None,
                           repaired=repaired, conditions=hints, checks=checks, context=context,
                           explanation=f"The model gave no usable answer ({exc.code}).")
        sql, note = custom_sql.statement_text(out.sql), out.explanation or None
        try:
            prepared = await _check(service, principal, connection_id, snap.model_version_id, sql, shape, request_id,
                                    turn_id)
            break
        except _Rejected as failed:
            checks = failed.checks
            await _rejected(principal, connection_id, failed, request_id, attempt)
            if attempt == 2 and failed.step == "answer_shape" and failed.prepared is not None:
                # The kind of answer is the rules' guess from the question's words, not a safety check: SQL that
                # passed every check runs, with a note, rather than being refused for answering differently.
                prepared = failed.prepared
                prepared.checks.append({"name": "answer_shape", "ok": True,
                                        "detail": f"differs from the guessed {SHAPE_NAMES.get(shape, shape)}"})
                shape_note = (f"Answered as written: the question looked like it wanted "
                              f"{SHAPE_NAMES.get(shape, shape)}.")
                await trace.step("validate", "The answer's shape differs from the guess; ran it anyway", agent=AGENT,
                                 detail={"shape": shape})
                break
            if attempt == 2 or (failed.error.code in NO_REPAIR and not _repairable(failed)):
                reason = "shape" if failed.step == "answer_shape" else "check_failed"
                return _refuse(shape, reason, f"The SQL written for this question failed the check: "
                               f"{failed.error.message}", sql=sql, repaired=repaired, checks=checks,
                               conditions=hints)
            repaired = True
            user = repair_request(question, sql, failed)
            await trace.step("retry", "Asked AG-10 to repair the SQL once", agent=AGENT,
                             detail={"step": failed.step})
    assert prepared is not None
    await trace.step("validate", "The drafted SQL passed the check" + (" after one repair" if repaired else ""),
                     agent=AGENT, detail={"checks": [c["name"] for c in prepared.checks]})
    # The model's business rules are enforced, not only suggested: what the SQL leaves out goes back once by name.
    gaps = [] if context.get("from_memory") else rule_gaps(sql, snap, question, used_rules, dialect)
    short = None if context.get("from_memory") else figures_missing(question, sql, dialect)
    if gaps or short:
        await trace.step("validate", f"The SQL leaves out {len(gaps)} business rules"
                         + ("; returns fewer figures than asked" if short else "") + "; asked AG-10 to fix it",
                         agent=AGENT, status="failed", detail={"rules": [g.rule.term for g in gaps], "figures": short})
        fixed = await _rule_repair(gateway, service, principal, connection_id, snap, shape, question, system, sql,
                                   gaps, dialect, as_of, used_rules, request_id, turn_id,
                                   notes=[short] if short else None)
        if fixed is not None:
            sql, note, prepared, left = fixed
            repaired = True
            context["rules_enforced"] = [g.rule.term for g in gaps if g.rule.term not in {x.rule.term for x in left}]
            context["rules_still_missing"] = [g.rule.term for g in left]
        else:
            context["rules_still_missing"] = [g.rule.term for g in gaps]
            if short:
                context["figures_short"] = True

    started = time.monotonic()
    try:
        result = await run(sql)
    except TelaError as exc:
        await trace.step("query", "The SQL could not be run", agent=AGENT, status="failed",
                         detail={"code": str(exc.code)})
        # The database's own reason goes back to AG-10 for one repair (a GROUP BY that differs from the SELECT, a
        # function the dialect lacks): the error text was thrown away before and the question failed blind.
        fixed = await _run_repair(gateway, service, principal, connection_id, snap.model_version_id, shape, question,
                                  system, sql, exc, run, request_id, turn_id)
        if fixed is None:
            return _refuse(shape, "run_failed", f"The SQL passed the check but could not be run: {exc.message}",
                           sql=sql, repaired=repaired, checks=prepared.checks, conditions=hints, context=context)
        sql, note, prepared, result = fixed
        repaired = True
    result["duration_ms"] = int((time.monotonic() - started) * 1000)
    await trace.step("query", f"Ran the SQL: {result['row_count']} rows", agent=AGENT,
                     duration_ms=result["duration_ms"])
    notes: list[str] = [shape_note] if shape_note else []
    if matched_nothing(result) and (missing := await missing_values(run, prepared)):
        await trace.step("validate", "No rows: the data doesn't hold some filter values", agent=AGENT,
                         status="failed", detail={"missing": [m.note() for m in missing]})
        fixed = await _value_repair(gateway, service, principal, connection_id, snap.model_version_id, shape,
                                    question, system, sql, missing, run, request_id, turn_id)
        if fixed is None:
            notes = [*notes, *(m.note() for m in missing)]
        else:
            sql, note, prepared, result, fixed_notes = fixed
            notes = [*notes, *fixed_notes]
            repaired = True
    async with tenant_tx(principal.tenant_id) as conn:
        await audit.record(conn, tenant_id=principal.tenant_id, action="custom_sql.asked", object_type="connection",
                           object_id=connection_id, outcome="success", actor_id=principal.user_id,
                           correlation_id=request_id,
                           details={"surface": SURFACE, "shape": shape, "repaired": repaired,
                                    "ast_hash": prepared.ast_hash, "row_count": result["row_count"]})
    if not context.get("from_memory") and not context.get("rules_still_missing") and not context.get("figures_short"):
        await remember(service, mkey, sql, note)
    viz = answer_viz(result, snap)
    return DirectAnswer(status="ok", shape=shape, sql=sql, explanation=note, repaired=repaired,
                        checks=prepared.checks, conditions=hints, result=result, visual=viz.get("visual"),
                        value_notes=notes, viz=viz, context=context)


def answer_viz(result: dict[str, Any], snap: ModelSnapshot) -> dict[str, Any]:
    """The chart a Direct SQL answer is drawn as, like any Ask answer's ``viz`` event, plus ``visuals`` (suitable and
    disabled, with reasons) and ``charts``: the chart for each suitable visual, so the card's Suggestions switch the
    chart without another query (US-260). No rows: no chart."""
    if not result.get("rows"):
        return {"visual": None, "alternatives": [], "disabled": [], "chart_spec": None, "note": None,
                "visuals": None, "charts": {}}
    viz = visualise(result, snap)
    options = visual_options(result, snap)
    charts: dict[str, Any] = {}
    for visual in options["suitable"]:
        try:
            charts[visual] = viz["chart_spec"] if visual == viz["visual"] else visualise(result, snap, visual)[
                "chart_spec"]
        except Exception:  # noqa: BLE001, S112 - a visual that can't be drawn is just not offered
            continue
    options["suitable"] = [v for v in options["suitable"] if v in charts]
    return {**viz, "visuals": options, "charts": charts}


async def _value_repair(gateway: Any, service: QueryService, principal: Principal, connection_id: str,
                        version_id: str, shape: str, question: str, system: str, sql: str, missing: list[Missing],
                        run: Any, request_id: str | None, turn_id: str | None) -> tuple[
                            str, str | None, custom_sql.Prepared, dict[str, Any], list[str]] | None:
    """One more AG-10 call with the columns' real values (on top of the check repair: a different cause). The new
    SQL goes through the same check and scoped run; if any of that fails, None and the first answer stands with its
    notes. Returns the SQL, its explanation, the check, the result and the notes still true of the new SQL."""
    await trace.step("retry", "Asked AG-10 to use values the data holds", agent=AGENT,
                     detail={"step": "filter_values"})
    try:
        out = await run_agent(gateway, SqlDraftOut, tenant_id=principal.tenant_id, user_id=principal.user_id,
                              agent=AGENT, task=TASK, system=system,
                              user=value_repair_prompt(question, sql, missing), turn_id=turn_id)
        new_sql = custom_sql.statement_text(out.sql)
        prepared = await _check(service, principal, connection_id, version_id, new_sql, shape, request_id, turn_id)
        started = time.monotonic()
        result = await run(new_sql)
    except _Rejected as failed:
        await _rejected(principal, connection_id, failed, request_id, 3)
        return None
    except (AgentFailed, TelaError):
        await trace.step("query", "The value repair gave no usable SQL; the first answer stands", agent=AGENT,
                         status="failed")
        return None
    result["duration_ms"] = int((time.monotonic() - started) * 1000)
    await trace.step("query", f"Ran the repaired SQL: {result['row_count']} rows", agent=AGENT,
                     duration_ms=result["duration_ms"])
    still = await missing_values(run, prepared) if result["row_count"] == 0 else []
    return new_sql, out.explanation or None, prepared, result, [m.note() for m in still]


def run_error_text(exc: TelaError) -> str:
    """The database's reason for refusing the SQL, as plain text for a repair (no values, no secrets)."""
    details = exc.details if isinstance(exc.details, dict) else {}
    admin = details.get("admin_detail") if isinstance(details.get("admin_detail"), dict) else {}
    parts = [exc.message]
    for k in ("source_message", "error", "reason", "detail"):
        for v in (details.get(k), admin.get(k)):
            if v and str(v)[:400] not in parts:
                parts.append(str(v)[:400])
    code = details.get("sqlstate") or admin.get("sqlstate")
    if code:
        parts.append(f"(database error code {code})")
    return " ".join(parts)


async def _run_repair(gateway: Any, service: QueryService, principal: Principal, connection_id: str,
                      version_id: str, shape: str, question: str, system: str, sql: str, exc: TelaError, run: Any,
                      request_id: str | None, turn_id: str | None
                      ) -> tuple[str, str | None, custom_sql.Prepared, dict[str, Any]] | None:
    """One AG-10 repair after the database refused SQL that passed the check; None when that fails too."""
    await trace.step("retry", "Asked AG-10 to repair SQL the database refused", agent=AGENT,
                     detail={"step": "run", "code": str(exc.code)})
    user = (f"Question: {question}\nThis SQL passed the checks but the database refused it:\n{sql}\n"
            f"Database said: {run_error_text(exc)}\nWrite the corrected query. Every non-aggregate expression in "
            "SELECT must appear in GROUP BY exactly as selected.")
    try:
        out = await run_agent(gateway, SqlDraftOut, tenant_id=principal.tenant_id, user_id=principal.user_id,
                              agent=AGENT, task=TASK, system=system, user=user, turn_id=turn_id)
        new_sql = custom_sql.statement_text(out.sql)
        prepared = await _check(service, principal, connection_id, version_id, new_sql, shape, request_id, turn_id)
        started = time.monotonic()
        result = await run(new_sql)
    except _Rejected as failed:
        await _rejected(principal, connection_id, failed, request_id, 3)
        return None
    except (AgentFailed, TelaError):
        await trace.step("query", "The run repair gave no usable SQL", agent=AGENT, status="failed")
        return None
    result["duration_ms"] = int((time.monotonic() - started) * 1000)
    await trace.step("query", f"Ran the repaired SQL: {result['row_count']} rows", agent=AGENT,
                     duration_ms=result["duration_ms"])
    return new_sql, out.explanation or None, prepared, result


async def _rule_repair(gateway: Any, service: QueryService, principal: Principal, connection_id: str,
                       snap: ModelSnapshot, shape: str, question: str, system: str, sql: str, gaps: list[Gap],
                       dialect: str | None, as_of: str, rules: list[Any], request_id: str | None,
                       turn_id: str | None, notes: list[str] | None = None
                       ) -> tuple[str, str | None, custom_sql.Prepared, list[Gap]] | None:
    """One AG-10 call naming the rules the SQL left out. The new SQL must pass the same check and leave out fewer
    rules, or the first SQL stands. Returns the SQL, its explanation, the check and the rules still missing."""
    try:
        out = await run_agent(gateway, SqlDraftOut, tenant_id=principal.tenant_id, user_id=principal.user_id,
                              agent=AGENT, task=TASK, system=system,
                              user=repair_prompt(question, sql, gaps, snap, as_of, notes), turn_id=turn_id)
        new_sql = custom_sql.statement_text(out.sql)
        prepared = await _check(service, principal, connection_id, snap.model_version_id, new_sql, shape,
                                request_id, turn_id)
    except _Rejected as failed:
        await _rejected(principal, connection_id, failed, request_id, 3)
        return None
    except (AgentFailed, TelaError):
        await trace.step("validate", "The rule repair gave no usable SQL; the first SQL stands", agent=AGENT,
                         status="failed")
        return None
    left = rule_gaps(new_sql, snap, question, rules, dialect)
    still_short = bool(notes) and figures_missing(question, new_sql, dialect) is not None
    fixed_figures = bool(notes) and not still_short
    if len(left) > len(gaps) or (len(left) == len(gaps) and not fixed_figures):
        await trace.step("validate", "The rule repair applied no more rules; the first SQL stands", agent=AGENT,
                         status="failed", detail={"rules": [g.rule.term for g in left]})
        return None
    await trace.step("validate", f"The repaired SQL applies {len(gaps) - len(left)} more business rules",
                     agent=AGENT, detail={"still_missing": [g.rule.term for g in left]})
    return new_sql, out.explanation or None, prepared, left


MEMORY_TTL_S = 30 * 24 * 3600  # a checked answer's SQL is reused for the same question for 30 days


def memory_key(tenant_id: str, connection_id: str, version_id: str, question: str) -> str:
    """The same question on the same approved model version: case, spacing and end punctuation don't matter."""
    norm = re.sub(r"\s+", " ", question.strip().lower()).rstrip(" ?.!")
    return "tela:sqlmem:" + hashlib.sha256(f"{tenant_id}|{connection_id}|{version_id}|{norm}".encode()).hexdigest()


async def recall(service: QueryService, key: str) -> dict[str, Any] | None:
    try:
        raw = await service.cache.backend.get(key)
        return json.loads(raw) if raw else None
    except Exception:  # noqa: BLE001 - memory is a convenience; a miss just writes new SQL
        return None


async def remember(service: QueryService, key: str, sql: str, explanation: str | None) -> None:
    try:
        await service.cache.backend.set(key, json.dumps({"sql": sql, "explanation": explanation}).encode(),
                                        MEMORY_TTL_S)
    except Exception:  # noqa: BLE001
        return
