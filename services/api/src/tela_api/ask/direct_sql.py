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

import time
from dataclasses import dataclass, field
from typing import Any

from sqlglot import exp

from tela_api.ask.frame import QuestionFrame, Shape, rules_frame
from tela_api.audit import service as audit
from tela_api.content.common import visual_options
from tela_api.content.fields import model_context
from tela_api.content.sql_context import AG10_ROLE, SqlDraftOut, schema_context
from tela_api.core.db import tenant_tx
from tela_api.identity.principal import Principal
from tela_api.knowledge import conditions as named
from tela_api.llm import trace
from tela_api.llm.client import AgentFailed, run_agent
from tela_api.query import custom_sql
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
    "number": "Answer with one figure: a single aggregate (count, sum or average) and no GROUP BY.",
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
            "publishable": self.publishable, "publish_note": PUBLISH_NOTE, "suggested_visual": self.visual}
        if self.result is not None:
            r = self.result
            body.update({k: r.get(k) for k in ("columns", "row_count", "truncated", "as_of", "duration_ms",
                                                 "scope", "model_version", "warnings")})
            body["rows"] = r["rows"][:PREVIEW_ROWS]
        return body


class _Rejected(Exception):
    def __init__(self, step: str, error: TelaError, checks: list[dict[str, Any]]) -> None:
        super().__init__(error.message)
        self.step, self.error, self.checks = step, error, checks


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


def system_prompt(snap: ModelSnapshot, hidden: frozenset[str], question: str, frame: QuestionFrame,
                  hints: list[dict[str, Any]]) -> str:
    dialect = DIALECTS.get(snap.engine)
    out = (AG10_ROLE + " No comments, no semicolons, no other tables and no :page_filters. The query is checked, "
           "limited to the rows the person may see and run.\n" + SHAPE_RULES[frame.shape]
           + " Where an agreed term or a certified measure below matches the question, use its rule for the "
             "conditions instead of guessing values. Keep the explanation to one sentence.")
    out += "\n\n" + schema_context(snap, hidden, question, dialect=dialect)
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
        if len(aggs) != 1 or dims:
            return f"The question asks for {want}: select a single aggregate and nothing else."
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
        raise _Rejected("answer_shape", TelaError(ErrorCode.VALIDATION_ERROR, problem), checks)
    prepared.checks.append({"name": "answer_shape", "ok": True, "detail": shape})
    return prepared


async def _rejected(principal: Principal, connection_id: str, failed: _Rejected, request_id: str | None,
                    attempt: int) -> None:
    security = failed.error.code in custom_sql.SECURITY_CODES
    async with tenant_tx(principal.tenant_id) as conn:
        await audit.record(conn, tenant_id=principal.tenant_id, action="custom_sql.rejected",
                           object_type="connection", object_id=connection_id, outcome="deny",
                           actor_id=principal.user_id, correlation_id=request_id,
                           severity="security" if security else "normal",
                           details={"step": failed.step, "code": str(failed.error.code), "surface": SURFACE,
                                    "attempt": attempt, "reason": failed.error.details.get("reason")})
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


def _refuse(shape: str, reason: str, message: str, **kw: Any) -> DirectAnswer:
    trace.set_outcome("refused")
    return DirectAnswer(status="refused", shape=shape, reason=reason, message=message, **kw)


async def answer(service: QueryService, gateway: Any, principal: Principal, *, connection_id: str, question: str,
                 frame: QuestionFrame | None = None, request_id: str | None = None,
                 turn_id: str | None = None, surface: str = QUERY_SURFACE) -> DirectAnswer:
    """Answer one question on one connection with SQL AG-10 writes, checked and run by the Custom SQL pipeline.
    ``surface`` is what the query log records the run as: an Ask query, or ``evaluation`` for a gold run (US-259)."""
    frame = frame or rules_frame(question)
    shape: Shape = frame.shape
    async with tenant_tx(principal.tenant_id) as conn:
        connection = await service._connection(conn, connection_id)
        snap, hidden = await model_context(conn, principal, connection_id)
        rows = await named.approved(conn, connection_id, snap.model_version_id)
    try:
        custom_sql.dialect_for(connection["engine"], connection["descriptor"])
    except TelaError as exc:
        return _refuse(shape, "engine", f"Direct SQL isn't available for this source. {exc.message}")
    if shape == "definition":
        return _refuse(shape, "definition", "Direct SQL answers questions about the data; what a term means is in "
                       "Knowledge.")
    if gateway is None:
        return _refuse(shape, "no_model", "Direct SQL needs a language model, and none is set up.")

    hints = condition_hints(question, rows, snap, hidden)
    system = system_prompt(snap, hidden, question, frame, hints)
    await trace.step("decision", f"Direct SQL: asking AG-10 for {SHAPE_NAMES.get(shape, shape)}", agent=AGENT,
                     detail={"shape": shape, "conditions": [h["term"] for h in hints]})
    user, sql, note, repaired = question, "", None, False
    checks: list[dict[str, Any]] = []
    prepared: custom_sql.Prepared | None = None
    for attempt in (1, 2):
        try:
            out = await run_agent(gateway, SqlDraftOut, tenant_id=principal.tenant_id, user_id=principal.user_id,
                                  agent=AGENT, task=TASK, system=system, user=user, turn_id=turn_id)
        except AgentFailed as exc:
            return _refuse(shape, "model_failed", "I couldn't write SQL for that question.", sql=sql or None,
                           repaired=repaired, conditions=hints, checks=checks,
                           explanation=f"The model gave no usable answer ({exc.code}).")
        sql, note = custom_sql.statement_text(out.sql), out.explanation or None
        try:
            prepared = await _check(service, principal, connection_id, snap.model_version_id, sql, shape, request_id,
                                    turn_id)
            break
        except _Rejected as failed:
            checks = failed.checks
            await _rejected(principal, connection_id, failed, request_id, attempt)
            if attempt == 2 or failed.error.code in NO_REPAIR:
                reason = "shape" if failed.step == "answer_shape" else "check_failed"
                return _refuse(shape, reason, f"The SQL written for this question failed the check: "
                               f"{failed.error.message}", sql=sql, repaired=repaired, checks=checks,
                               conditions=hints)
            repaired = True
            user = (f"{question}\n\nYour previous SQL:\n{sql}\n\nIt failed the check ({failed.step}): "
                    f"{failed.error.message}\nWrite a corrected query.")
            await trace.step("retry", "Asked AG-10 to repair the SQL once", agent=AGENT,
                             detail={"step": failed.step})
    assert prepared is not None
    await trace.step("validate", "The drafted SQL passed the check" + (" after one repair" if repaired else ""),
                     agent=AGENT, detail={"checks": [c["name"] for c in prepared.checks]})

    async def run(statement: str) -> dict[str, Any]:
        return await service.execute_custom(statement, connection_id=connection_id,
                                            model_version_id=snap.model_version_id, principal=principal,
                                            ctx=QueryContext(surface=surface, request_id=request_id,
                                                             turn_id=turn_id))

    started = time.monotonic()
    try:
        result = await run(sql)
    except TelaError as exc:
        await trace.step("query", "The SQL could not be run", agent=AGENT, status="failed",
                         detail={"code": str(exc.code)})
        return _refuse(shape, "run_failed", f"The SQL passed the check but could not be run: {exc.message}",
                       sql=sql, repaired=repaired, checks=prepared.checks, conditions=hints)
    result["duration_ms"] = int((time.monotonic() - started) * 1000)
    await trace.step("query", f"Ran the SQL: {result['row_count']} rows", agent=AGENT,
                     duration_ms=result["duration_ms"])
    notes: list[str] = []
    if result["row_count"] == 0 and (missing := await missing_values(run, prepared)):
        await trace.step("validate", "No rows: the data doesn't hold some filter values", agent=AGENT,
                         status="failed", detail={"missing": [m.note() for m in missing]})
        fixed = await _value_repair(gateway, service, principal, connection_id, snap.model_version_id, shape,
                                    question, system, sql, missing, run, request_id, turn_id)
        if fixed is None:
            notes = [m.note() for m in missing]
        else:
            sql, note, prepared, result, notes = fixed
            repaired = True
    async with tenant_tx(principal.tenant_id) as conn:
        await audit.record(conn, tenant_id=principal.tenant_id, action="custom_sql.asked", object_type="connection",
                           object_id=connection_id, outcome="success", actor_id=principal.user_id,
                           correlation_id=request_id,
                           details={"surface": SURFACE, "shape": shape, "repaired": repaired,
                                    "ast_hash": prepared.ast_hash, "row_count": result["row_count"]})
    viz = answer_viz(result, snap)
    return DirectAnswer(status="ok", shape=shape, sql=sql, explanation=note, repaired=repaired,
                        checks=prepared.checks, conditions=hints, result=result, visual=viz.get("visual"),
                        value_notes=notes, viz=viz)


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
