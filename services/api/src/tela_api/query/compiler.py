"""Spec → SQL compiler (US-019; technical_document §SQL compilation; RULE-012, 013, 014, 023).

The compiler builds a sqlglot expression tree, never strings: every value is a
bound placeholder. It reads the source through plain table references; the
scope injector then wraps every governed table in its scoped derived table
(US-020) before limits are applied (US-021).

Grain rule (RULE-014): when any join on the path from the measure's entity
fans out (one-to-many), the compiler first builds a ``tela_base`` CTE with one
row per (root key, breakdown values) and aggregates over it. Counts therefore
count distinct root records and sums add each root record once per group.

US-156: measures may read fields of linked entities (``entity.field``), row
values (days between dates, row arithmetic, pack calculators such as PMO
calculated health), ``check`` conditions, medians and percentiles (PostgreSQL
and DuckDB only); computed dimensions break down by a value or by labelled
bands of it, and a dimension may label blanks ("Not set").
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from sqlglot import exp

from tela_api.query import calculators
from tela_api.query.validator import (
    Extra,
    FieldRef,
    ResolvedBreakdown,
    ResolvedExistsIn,
    ResolvedWindowOp,
    ValidatedSpec,
)
from tela_common.errors import ErrorCode, TelaError
from tela_query_spec.expr import (
    AggExpr,
    All,
    Any_,
    Arith,
    AsOf,
    Calc,
    Calculator,
    Check,
    Compare,
    DaysBetween,
    InSet,
    MeasureExpr,
    Not,
    NullCheck,
    Today,
    measure_aggs,
)
from tela_query_spec.model import Dimension, ModelEntity, ModelField, ModelSnapshot, step_fans_out
from tela_query_spec.spec import Filter, PersonalField, TimeRange

BASE_CTE = "tela_base"
COHORT_CTE = "tela_cohort"
COMPARE_CUR = "tela_cur"
COMPARE_PREV = "tela_prev"
PERCENTILE_DIALECTS = ("postgres", "duckdb")  # dialects this compiler can write a median / percentile for
DIALECTS = {"postgres": "postgres", "mysql": "mysql", "sqlserver": "tsql", "excel": "duckdb", "csv": "duckdb",
            "dataverse": "tsql"}
# Source profiles narrow a dialect: the Dataverse TDS endpoint accepts a subset of T-SQL (ADR-013). Until the
# US-058 spike confirms its limits, Tela avoids common table expressions there (provisional).
PROFILES = {"dataverse": {"ctes": False}}


class Params:
    """Allocates placeholder names and holds bound values."""

    def __init__(self, prefix: str = "p") -> None:
        self.prefix = prefix
        self.values: dict[str, Any] = {}

    def add(self, value: Any) -> exp.Placeholder:
        name = f"{self.prefix}{len(self.values)}"
        self.values[name] = value
        return exp.Placeholder(this=name)


@dataclass(frozen=True)
class ColumnMeta:
    name: str
    kind: str  # dimension | period | measure | currency
    label: str
    ref: str
    data_type: str
    format: str | None = None
    value_labels: dict[str, str] = field(default_factory=dict)
    sort_order: tuple[str, ...] | None = None
    field: str | None = None  # qualified model field (entity.field) behind a dimension column
    of: str | None = None  # a derived figure or share (US-176): the measure column it is worked out from


@dataclass
class CompiledQuery:
    tree: exp.Select
    params: Params
    columns: list[ColumnMeta]
    entities: dict[str, str]  # alias -> entity key
    fan_out: bool
    dialect: str
    used_fields: dict[str, set[str]]  # entity -> source columns the query needs

    def sql(self) -> str:
        return str(self.tree.sql(dialect=self.dialect))


def table_of(entity: ModelEntity) -> exp.Table:
    schema, _, name = entity.source_object.rpartition(".")
    return exp.Table(this=exp.to_identifier(name), db=exp.to_identifier(schema) if schema else None)


def _col(alias: str, column: str) -> exp.Column:
    return exp.column(column, table=alias, quoted=True)


def _fcol(alias: str, f: ModelField) -> exp.Expression:
    """A field's value: its column, the column's day for a date field over a date-time column, or the column as
    text for a text field over a yes/no, number or date column (yes/no reads "Yes" / "No", as the PMO pack expects)."""
    c = _col(alias, f.source_column)
    if f.reads_day:
        return exp.Cast(this=c, to=exp.DataType.build("date"))
    if f.reads_text and f.source_type == "boolean":
        flag = exp.Cast(this=c, to=exp.DataType.build("int"))
        return exp.Case().when(exp.EQ(this=flag, expression=exp.Literal.number(1)), exp.Literal.string("Yes")) \
            .when(exp.EQ(this=flag.copy(), expression=exp.Literal.number(0)), exp.Literal.string("No"))
    if f.reads_text:
        return exp.Cast(this=c, to=exp.DataType.build("varchar"))
    return c


def _and(conds: list[exp.Expression]) -> exp.Expression:
    out: exp.Expression = conds[0]
    for c in conds[1:]:
        out = exp.and_(out, c, copy=False)
    return out


def _or(conds: list[exp.Expression]) -> exp.Expression:
    out: exp.Expression = conds[0]
    for c in conds[1:]:
        out = exp.or_(out, c, copy=False)
    return exp.paren(out, copy=False) if len(conds) > 1 else out


def _true() -> exp.Expression:
    return exp.EQ(this=exp.Literal.number(1), expression=exp.Literal.number(1))


OUTER_ALIAS = "tela_q"


def order_outside(q: exp.Select, dialect: str) -> exp.Select:
    """MySQL and T-SQL sort NULLs differently from PostgreSQL, and sqlglot emulates PostgreSQL's order by
    repeating the sorted expression, which breaks ``only_full_group_by`` (MySQL) and ``SELECT DISTINCT``
    (T-SQL). For those dialects the ordering and paging move to ``SELECT * FROM (q) AS tela_q`` and refer
    only to q's output columns; a CTE moves to the outer query (T-SQL has no CTE inside a subquery)."""
    if dialect not in ("mysql", "tsql") or not q.args.get("order"):
        return q
    outputs = {e.this.sql(): e.alias for e in q.expressions if isinstance(e, exp.Alias)}
    names = {e.alias_or_name for e in q.expressions}

    def to_output(node: exp.Expression) -> exp.Expression:
        if isinstance(node, exp.Column) and not node.table and node.name in names:
            return exp.column(node.name, quoted=True)
        alias = outputs.get(node.sql())
        return exp.column(alias, quoted=True) if alias else node

    order = q.args["order"].pop()
    for ordered in order.expressions:
        ordered.set("this", ordered.this.transform(to_output))
        if any(isinstance(c, exp.Column) and c.table for c in ordered.find_all(exp.Column)):
            raise TelaError(ErrorCode.SPEC_INVALID, "This sort can't be compiled for the source.")
    limit, offset, with_ = q.args.get("limit"), q.args.get("offset"), q.args.get("with")
    for key, node in (("limit", limit), ("offset", offset), ("with", with_)):
        if node is not None:
            q.set(key, None)
    outer = exp.select(exp.Star()).from_(q.subquery(OUTER_ALIAS))
    outer.set("order", order)
    if limit is not None:
        outer.set("limit", limit)
    if offset is not None:
        outer.set("offset", offset)
    if with_ is not None:
        outer.set("with", with_)
    return outer


def require_basic(vs: ValidatedSpec, engine: str) -> None:
    """Document and Web API compilers don't build the US-156 features (row values, medians, linked fields,
    computed dimensions): such a figure is Unavailable there rather than approximated."""
    from tela_query_spec.expr import uses_extended

    refs = [b.field for b in vs.breakdowns] + [f for f, _ in vs.filters]
    if any(uses_extended(m.expression) for m in vs.measures) or any(r.computed for r in refs):
        raise TelaError(ErrorCode.MEASURE_UNAVAILABLE, "This figure isn't available from this source yet.",
                        {"admin_detail": {"reason": "calculation not supported", "engine": engine}})


# ---------------------------------------------------------------------------- date ranges (server as-of date)


def _quarter_start(d: date) -> date:
    return date(d.year, 3 * ((d.month - 1) // 3) + 1, 1)


def _add_months(d: date, months: int) -> date:
    y, m = divmod(d.month - 1 + months, 12)
    year, month = d.year + y, m + 1
    return date(year, month, min(d.day, calendar.monthrange(year, month)[1]))


def bucket_start(d: date, grain: str) -> date:
    """The first day of the period ``d`` falls in (weeks start on Monday)."""
    if grain == "day":
        return d
    if grain == "week":
        return d - timedelta(days=d.weekday())
    if grain == "month":
        return d.replace(day=1)
    if grain == "quarter":
        return _quarter_start(d)
    return date(d.year, 1, 1)


def shift_periods(d: date, grain: str, n: int) -> date:
    """``d`` moved by ``n`` periods of ``grain``."""
    if grain in ("day", "week"):
        return d + timedelta(days=n * (7 if grain == "week" else 1))
    return _add_months(d, n * {"month": 1, "quarter": 3, "year": 12}[grain])


def previous_window(start: date, end: date, grain: str | None) -> tuple[date, date]:
    """The window just before ``start`` to ``end``: with a grain, as many whole periods earlier as the window covers
    (July to December → January to June), so each period lines up with one before it; without one, the same
    number of days."""
    if grain:
        n = _periods_between(bucket_start(start, grain), bucket_start(end, grain), grain) + 1
        return shift_periods(start, grain, -n), shift_periods(end, grain, -n)
    length = (end - start).days
    prev_end = start - timedelta(days=1)
    return prev_end - timedelta(days=length), prev_end


def _periods_between(a: date, b: date, grain: str) -> int:
    if grain in ("day", "week"):
        return (b - a).days // (7 if grain == "week" else 1)
    return ((b.year - a.year) * 12 + b.month - a.month) // {"month": 1, "quarter": 3, "year": 12}[grain]


def aligned_period(prev: date, grain: str, kind: str, n: int) -> date:
    """The current window's period that an earlier period is compared with (US-178): ``n`` periods later for the
    previous period; for the same period last year a year later (52 weeks for weeks)."""
    if kind == "same_period_last_year":
        if grain == "week":
            return prev + timedelta(weeks=52)
        if grain == "day":
            try:
                return prev.replace(year=prev.year + 1)
            except ValueError:  # 29 Feb → 28 Feb
                return prev.replace(year=prev.year + 1, day=28)
        return _add_months(prev, 12)
    return shift_periods(prev, grain, n)


def _year_ago(d: date) -> date:
    """Same month and day one year earlier; Feb 29 → Feb 28."""
    try:
        return d.replace(year=d.year - 1)
    except ValueError:
        return d.replace(year=d.year - 1, day=28)


def resolve_range(rng: TimeRange, as_of: date) -> tuple[date | None, date | None]:
    """Inclusive (start, end) dates for a relative or absolute range."""
    if rng.relative is None:
        return rng.start, rng.end
    r = rng.relative
    if r == "today":
        return as_of, as_of
    if r.startswith("last_") and r.endswith("_days"):
        n = int(r.split("_")[1])
        return as_of - timedelta(days=n - 1), as_of
    if r.startswith("next_") and r.endswith("_days"):
        n = int(r.split("_")[1])
        return as_of, as_of + timedelta(days=n)
    if r.startswith("last_") and r.endswith("_months"):
        n = int(r.split("_")[1])
        return _add_months(as_of, -n) + timedelta(days=1), as_of
    if r == "this_month":
        return as_of.replace(day=1), as_of
    if r == "last_month":
        first = as_of.replace(day=1)
        prev_end = first - timedelta(days=1)
        return prev_end.replace(day=1), prev_end
    if r == "this_quarter":
        return _quarter_start(as_of), as_of
    if r == "last_quarter":
        start = _add_months(_quarter_start(as_of), -3)
        return start, _quarter_start(as_of) - timedelta(days=1)
    if r == "this_year":
        return date(as_of.year, 1, 1), as_of
    if r == "last_year":
        return date(as_of.year - 1, 1, 1), date(as_of.year - 1, 12, 31)
    raise TelaError(ErrorCode.SPEC_INVALID, "Unsupported time range.", {"admin_detail": {"relative": r}})


# ---------------------------------------------------------------------------- compiler


class SqlCompiler:
    def __init__(self, dialect: str = "postgres", profile: str | None = None) -> None:
        if dialect not in ("postgres", "mysql", "tsql", "duckdb"):
            raise TelaError(ErrorCode.ENGINE_NOT_SUPPORTED, "This database engine is not supported yet.")
        self.dialect = dialect
        self.ctes = PROFILES.get(profile or "", {}).get("ctes", True)

    def compile(self, vs: ValidatedSpec, *, as_of: date, params: Params | None = None,
                time_override: tuple[date, date] | None = None) -> CompiledQuery:
        # Period compare (US-178): compile twice with different time ranges and join on grain
        has_compare = vs.spec.compare is not None and vs.time is not None and vs.time[1].grain and vs.time[1].range
        if has_compare and time_override is None:
            return self._compile_period_compare(vs, as_of=as_of)

        snap = vs.snapshot
        p = params or Params()
        aliases, joins, fan_out = self._join_plan(snap, vs)
        used: dict[str, set[str]] = {}

        def col(entity: str, field_key: str) -> exp.Expression:
            if "." in field_key:  # a linked entity's field (US-156)
                entity, field_key = field_key.split(".", 1)
            ent = snap.entity(entity)
            assert ent is not None
            f = ent.field(field_key)
            if f is None:
                raise TelaError(ErrorCode.SPEC_INVALID, "Unknown field.", {"admin_detail": {"field": field_key}})
            used.setdefault(entity, set()).add(f.source_column)
            return _fcol(aliases[entity], f)

        root = vs.root.key
        for pk in vs.root.primary_key:
            col(root, pk)

        # Measures (and their aggregate inputs) live on the root entity.
        currency_extra: list[ResolvedBreakdown] = []
        for m in vs.measures[:1]:
            if m.currency_field and not self._single_currency(vs, m.currency_field):
                ent = snap.entity(m.entity)
                assert ent is not None
                cf = ent.field(m.currency_field)
                assert cf is not None
                if not any(b.field.qualified == f"{m.entity}.{cf.key}" for b in vs.breakdowns):
                    currency_extra.append(ResolvedBreakdown(f"field:{m.entity}.{cf.key}", "currency",
                                                            FieldRef(m.entity, cf, "Currency")))
        breakdowns = [*vs.breakdowns, *currency_extra]

        bexprs = [(b, self._breakdown_expr(b, col, p, as_of)) for b in breakdowns
                  if not (vs.cohort and b.output == "cohort")]
        # Cohort CTE (US-178): a subquery mapping root PKs → cohort period
        if vs.cohort:
            cohort_cte = self._cohort_cte(vs, snap, aliases, col, p, as_of, used)
            cohort_b = next((b for b in breakdowns if b.output == "cohort"), None)
            if cohort_b is not None:
                bexprs.append((cohort_b, exp.column("cohort", table=COHORT_CTE)))

        where = self._where(vs, col, p, as_of, aliases=aliases, time_override=time_override)

        sources: exp.Select = exp.select()
        # T-SQL never matches a GROUP BY expression holding a bound parameter (a grouping or label CASE) to the
        # same select-list expression; grouping by name over the one-row-per-root base gives the same answer.
        by_name = self.dialect == "tsql" and any(e.find(exp.Placeholder) for _, e in bexprs)
        if fan_out or by_name:
            inner = exp.select().from_(table_of(vs.root).as_(aliases[root]))
            for j in joins:
                inner = inner.join(j)
            # Cohort join (US-178)
            if vs.cohort:
                inner = self._join_cohort(inner, vs, aliases, col)
            pk_cols = [col(root, k) for k in vs.root.primary_key]
            inner = inner.select(*(c.as_(f"__pk{i}") for i, c in enumerate(pk_cols)))
            agg_inputs: list[tuple[AggExpr, str | None, str | None]] = []
            for mi, m in enumerate(vs.measures):
                for ai, agg in enumerate(measure_aggs(m.expression)):
                    a_name = f"__a{mi}_{ai}" if agg.field or agg.value is not None else None
                    w_name = f"__w{mi}_{ai}" if agg.where is not None else None
                    if a_name:
                        inner = inner.select(self._agg_input(agg, root, col, p, as_of).as_(a_name))
                    if w_name and agg.where is not None:
                        flag = exp.Case(ifs=[exp.If(this=self._cond(agg.where, root, col, p, as_of),
                                                    true=exp.Literal.number(1))],
                                        default=exp.Literal.number(0))
                        inner = inner.select(flag.as_(w_name))
                    agg_inputs.append((agg, a_name, w_name))
            for b, e in bexprs:
                inner = inner.select(e.as_(b.output))
            if where is not None:
                inner = inner.where(where)
            inner = inner.distinct()
            if vs.cohort:
                outer_base = exp.select().from_(exp.to_identifier(BASE_CTE))
                outer_base = outer_base.with_(COHORT_CTE, as_=cohort_cte).with_(BASE_CTE, as_=inner, append=True)
            else:
                outer_base = (exp.select().from_(exp.to_identifier(BASE_CTE)).with_(BASE_CTE, as_=inner) if self.ctes
                              else exp.select().from_(inner.subquery(BASE_CTE)))
            for b, _ in bexprs:
                outer_base = outer_base.select(exp.column(b.output, table=BASE_CTE).as_(b.output))
            for mi, m in enumerate(vs.measures):
                it = iter(agg_inputs[sum(len(measure_aggs(x.expression)) for x in vs.measures[:mi]):])
                outer_base = outer_base.select(self._measure_expr(m.expression,
                                               lambda a, it=it: self._base_agg(a, next(it))).as_(m.key))
            if bexprs:
                outer_base = outer_base.group_by(*(exp.column(b.output, table=BASE_CTE) for b, _ in bexprs))
            sources = outer_base
            order_target: dict[str, exp.Expression] = {b.ref: exp.column(b.output, table=BASE_CTE)
                                                       for b, _ in bexprs}
        else:
            q = exp.select().from_(table_of(vs.root).as_(aliases[root]))
            for j in joins:
                q = q.join(j)
            # Cohort join (US-178)
            if vs.cohort:
                q = self._join_cohort(q, vs, aliases, col)
                q = q.with_(COHORT_CTE, as_=cohort_cte)
            for b, e in bexprs:
                q = q.select(e.as_(b.output))
            for m in vs.measures:
                q = q.select(self._measure_expr(
                    m.expression, lambda a: self._direct_agg(a, root, col, p, as_of)).as_(m.key))
            if where is not None:
                q = q.where(where)
            if bexprs:
                q = q.group_by(*(e.copy() for _, e in bexprs))
            sources = q
            order_target = {b.ref: e for b, e in bexprs}

        if vs.extras:
            sources = self._with_extras(vs, sources, [b.output for b, _ in bexprs])
            order_target = {b.ref: exp.column(b.output) for b, _ in bexprs}
        win_columns: list[ColumnMeta] = []
        if vs.window_ops:
            sources, win_columns = self._with_window_ops(vs, sources, [b.output for b, _ in bexprs])
            order_target = {b.ref: exp.column(b.output) for b, _ in bexprs}
        sources = order_outside(self._order(vs, sources, order_target, p), self.dialect)
        columns = [self._column_meta(b) for b in breakdowns] + [
            ColumnMeta(m.key, "measure", m.label, f"measure:{m.key}", "decimal", m.format) for m in vs.measures
        ] + extra_columns(vs) + win_columns
        return CompiledQuery(sources, p, columns, {v: k for k, v in aliases.items()}, fan_out, self.dialect, used)

    # ------------------------------------------------------------------ period compare (US-178)

    def _compile_period_compare(self, vs: ValidatedSpec, *, as_of: date) -> CompiledQuery:
        """Compile two time windows (current and previous period) joined on the aligned period + breakdowns, with delta
        and % change columns for each measure (US-178, FEAT-063)."""
        import dataclasses

        assert vs.time is not None and vs.time[1].range is not None
        _fref, tspec = vs.time
        start, end = resolve_range(tspec.range, as_of)
        assert start is not None and end is not None
        spec = vs.spec
        grain = tspec.grain
        assert grain is not None
        kind = spec.compare.kind if spec.compare else "previous_period"
        if kind == "same_period_last_year":
            prev_start = _year_ago(start)
            prev_end = _year_ago(end)
        else:
            prev_start, prev_end = previous_window(start, end, grain)
        # Each earlier period is joined to the current period it is compared with, not to its own date (which
        # never falls in the current window): a CASE over the earlier window's periods, worked out here.
        n = _periods_between(bucket_start(start, grain), bucket_start(end, grain), grain) + 1
        pairs, b = [], bucket_start(prev_start, grain)
        while b <= prev_end:
            pairs.append((b, aligned_period(b, grain, kind, n)))
            b = shift_periods(b, grain, 1)

        def aligned(e: exp.Expression) -> exp.Expression:
            def day(d: date) -> exp.Expression:
                return exp.Cast(this=exp.Literal.string(d.isoformat()), to=exp.DataType.build("date"))
            return exp.Case(ifs=[exp.If(this=exp.EQ(this=e.copy(), expression=day(a)), true=day(t)) for a, t in pairs])

        # Strip compare to prevent recursion
        no_compare = spec.model_copy(update={"compare": None})
        vs_nc = dataclasses.replace(vs, spec=no_compare)

        # Compile current and previous with separate param namespaces
        cur_p = Params(prefix="c")
        cur = self.compile(vs_nc, as_of=as_of, params=cur_p)
        prev_p = Params(prefix="q")
        prev = self.compile(vs_nc, as_of=as_of, params=prev_p, time_override=(prev_start, prev_end))

        # Merge params
        merged = Params()
        merged.values = {**cur.params.values, **prev.params.values}

        # Strip ORDER BY and LIMIT from inner queries (they go on the final result)
        cur_tree = cur.tree.copy()
        prev_tree = prev.tree.copy()
        cur_order = cur_tree.args.get("order")
        for key in ("order", "limit", "offset"):
            for t in (cur_tree, prev_tree):
                t.set(key, None)
        # Move CTEs out of inner queries
        for t in (cur_tree, prev_tree):
            t.set("with", None)

        # Build join keys: period + all dimension columns
        join_keys = [c.name for c in cur.columns if c.kind in ("period", "dimension", "currency")]
        measure_keys = [c.name for c in cur.columns if c.kind == "measure"]

        # Final query: cur LEFT JOIN prev ON grain+breakdowns, with delta columns
        outer = exp.select()
        periods = {c.name for c in cur.columns if c.kind == "period"}

        def prev_key(k: str) -> exp.Expression:
            col = exp.column(k, table=COMPARE_PREV)
            return aligned(col) if k in periods else col

        for k in join_keys:
            outer = outer.select(exp.Coalesce(this=exp.column(k, table=COMPARE_CUR),
                                              expressions=[prev_key(k)]).as_(k))
        for mk in measure_keys:
            outer = outer.select(exp.column(mk, table=COMPARE_CUR).as_(mk))
            prev_name = f"{mk}__prev"
            delta_name = f"{mk}__delta"
            pct_name = f"{mk}__delta_pct"
            outer = outer.select(exp.column(mk, table=COMPARE_PREV).as_(prev_name))
            cur_val = exp.Coalesce(this=exp.column(mk, table=COMPARE_CUR),
                                   expressions=[exp.Literal.number(0)])
            prev_val = exp.Coalesce(this=exp.column(mk, table=COMPARE_PREV),
                                    expressions=[exp.Literal.number(0)])
            delta = exp.paren(exp.Sub(this=cur_val, expression=prev_val.copy()), copy=False)
            outer = outer.select(delta.as_(delta_name))
            pct = exp.paren(exp.Mul(
                this=calculators.ratio(delta.copy(),
                                       exp.Nullif(this=prev_val.copy(), expression=exp.Literal.number(0))),
                expression=exp.Literal.number(100)), copy=False)
            outer = outer.select(exp.Anonymous(this="ROUND", expressions=[
                exp.Cast(this=pct, to=exp.DataType.build("decimal(18, 4)")), exp.Literal.number(1)]).as_(pct_name))

        # Also carry through extra columns (derived, shares) from current period only
        extra_keys = [c.name for c in cur.columns if c.kind == "measure" and c.of is not None]
        for ek in extra_keys:
            if ek not in measure_keys:
                outer = outer.select(exp.column(ek, table=COMPARE_CUR).as_(ek))

        # Build the join condition
        if join_keys:
            on_conds = [exp.EQ(this=exp.column(k, table=COMPARE_CUR), expression=prev_key(k)) for k in join_keys]
            join_on = _and(on_conds)
            outer = outer.from_(exp.to_identifier(COMPARE_CUR))
            outer = outer.join(exp.Join(this=exp.to_identifier(COMPARE_PREV), on=join_on, side="FULL"))
        else:
            outer = outer.from_(exp.to_identifier(COMPARE_CUR))
            outer = outer.join(exp.Join(this=exp.to_identifier(COMPARE_PREV), on=_true(), kind="CROSS"))

        outer = outer.with_(COMPARE_CUR, as_=cur_tree).with_(COMPARE_PREV, as_=prev_tree, append=True)

        # Apply ordering on the final query
        if cur_order:
            outer.set("order", cur_order)

        outer = order_outside(outer, self.dialect)

        # Build columns metadata
        columns: list[ColumnMeta] = [c for c in cur.columns if c.kind in ("period", "dimension", "currency")]
        for c in cur.columns:
            if c.kind == "measure" and c.of is None:
                columns.append(c)
                columns.append(ColumnMeta(f"{c.name}__prev", "measure", f"{c.label} (previous)", c.ref,
                                          c.data_type, c.format, of=c.name))
                columns.append(ColumnMeta(f"{c.name}__delta", "measure", f"{c.label} (change)",
                                          f"delta:{c.ref}", "decimal", c.format, of=c.name))
                columns.append(ColumnMeta(f"{c.name}__delta_pct", "measure", f"{c.label} (% change)",
                                          f"delta_pct:{c.ref}", "decimal", "percent", of=c.name))
        for c in cur.columns:
            if c.kind == "measure" and c.of is not None and c.name not in measure_keys:
                columns.append(c)

        return CompiledQuery(outer, merged, columns, cur.entities, cur.fan_out, self.dialect, cur.used_fields)

    # ------------------------------------------------------------------ cohort (US-178)

    def _cohort_cte(self, vs: ValidatedSpec, snap: ModelSnapshot, aliases: dict[str, str],
                    col: Any, p: Params, as_of: date, used: dict[str, set[str]]) -> exp.Select:
        """Build the cohort CTE: SELECT root_pk, DATE_TRUNC(grain, AGG(date_field)) AS cohort
        FROM linked_entity GROUP BY root_pk."""
        rc = vs.cohort
        assert rc is not None
        linked_ent = snap.entity(rc.entity)
        assert linked_ent is not None
        c_alias = "tc0"
        date_f = linked_ent.field(rc.field.field.key)
        assert date_f is not None
        used.setdefault(rc.entity, set()).add(date_f.source_column)
        date_col = _fcol(c_alias, date_f)
        agg_cls = {"min": exp.Min, "max": exp.Max}[rc.agg]
        agg_date = exp.Cast(
            this=exp.TimestampTrunc(this=agg_cls(this=date_col), unit=exp.var(rc.grain.upper())),
            to=exp.DataType.build("date"))
        sub = exp.select()
        pk_selects = []
        for i, _rf in enumerate(rc.root_fields):
            linked_f = linked_ent.field(rc.linked_fields[i])
            assert linked_f is not None
            used.setdefault(rc.entity, set()).add(linked_f.source_column)
            fk_col = _col(c_alias, linked_f.source_column)
            pk_name = f"__cpk{i}"
            sub = sub.select(fk_col.as_(pk_name))
            pk_selects.append(pk_name)
        sub = sub.select(agg_date.as_("cohort"))
        sub = sub.from_(table_of(linked_ent).as_(c_alias))
        sub = sub.where(exp.Not(this=exp.Is(this=date_col.copy(), expression=exp.Null())))
        sub = sub.group_by(*(exp.to_identifier(n) for n in pk_selects))
        return sub

    def _join_cohort(self, q: exp.Select, vs: ValidatedSpec, aliases: dict[str, str],
                     col: Any) -> exp.Select:
        """JOIN the cohort CTE to the main query on root PK."""
        rc = vs.cohort
        assert rc is not None
        root = vs.root.key
        on_conds: list[exp.Expression] = []
        for i, rf in enumerate(rc.root_fields):
            root_f = vs.root.field(rf)
            assert root_f is not None
            on_conds.append(exp.EQ(
                this=_col(aliases[root], root_f.source_column),
                expression=exp.column(f"__cpk{i}", table=COHORT_CTE)))
        return q.join(exp.Join(this=exp.to_identifier(COHORT_CTE), on=_and(on_conds), side="LEFT"))

    # ------------------------------------------------------------------ joins

    def _join_plan(self, snap: ModelSnapshot, vs: ValidatedSpec) -> tuple[dict[str, str], list[exp.Join], bool]:
        root = vs.root.key
        aliases = {root: "t0"}
        joins: list[exp.Join] = []
        fan_out = False
        for target in sorted(vs.entities - {root}):
            path = snap.join_path(root, target)
            if path is None:  # validator already checked; defensive
                raise TelaError(ErrorCode.SPEC_INVALID, "No approved link to that data.")
            here = root
            for link, forward in path:
                there = link.to_entity if forward else link.from_entity
                fan_out = fan_out or step_fans_out(link, forward)
                if there in aliases:
                    here = there
                    continue
                aliases[there] = f"t{len(aliases)}"
                here_ent, there_ent = snap.entity(here), snap.entity(there)
                assert here_ent is not None and there_ent is not None
                here_fields = link.from_fields if forward else link.to_fields
                there_fields = link.to_fields if forward else link.from_fields
                on = _and([
                    exp.EQ(this=_col(aliases[here], self._src(here_ent, hf)),
                           expression=_col(aliases[there], self._src(there_ent, tf)))
                    for hf, tf in zip(here_fields, there_fields, strict=True)
                ])
                joins.append(exp.Join(this=table_of(there_ent).as_(aliases[there]), on=on, side="LEFT"))
                here = there
        return aliases, joins, fan_out

    @staticmethod
    def _src(ent: ModelEntity, field_key: str) -> str:
        f = ent.field(field_key)
        assert f is not None
        return str(f.source_column)

    # ------------------------------------------------------------------ conditions

    def _cond(self, cond: Any, entity: str, col: Any, p: Params, as_of: date) -> exp.Expression:
        if isinstance(cond, Compare):
            left = col(entity, cond.field)
            if cond.other_field is not None:
                right: exp.Expression = col(entity, cond.other_field)
            elif isinstance(cond.value, AsOf):
                right = p.add(as_of + timedelta(days=cond.value.asof_offset_days))
            elif isinstance(cond.value, Today):  # pinned by ``asof.pin_dates`` before compiling (US-163)
                raise TelaError(ErrorCode.SPEC_INVALID, "Today was not fixed for this run.")
            else:
                right = p.add(cond.value)
            op = {"=": exp.EQ, "!=": exp.NEQ, "<": exp.LT, "<=": exp.LTE, ">": exp.GT, ">=": exp.GTE}[cond.op]
            cmp: exp.Expression = op(this=left, expression=right)
            return cmp
        if isinstance(cond, InSet):
            node = exp.In(this=col(entity, cond.field), expressions=[p.add(v) for v in cond.values])
            return exp.Not(this=node) if cond.op == "not_in" else node
        if isinstance(cond, NullCheck):
            is_null = exp.Is(this=col(entity, cond.field), expression=exp.Null())
            return exp.Not(this=is_null) if cond.op == "not_null" else is_null
        if isinstance(cond, Check):
            value = self._value(cond.of, entity, col, p, as_of)
            if cond.op in ("in", "not_in"):
                node = exp.In(this=value, expressions=[p.add(v) for v in cond.values or []])
                return exp.Not(this=node) if cond.op == "not_in" else node
            cls = {"=": exp.EQ, "!=": exp.NEQ, "<": exp.LT, "<=": exp.LTE, ">": exp.GT, ">=": exp.GTE}[cond.op]
            return exp.paren(cls(this=value, expression=p.add(cond.value)), copy=False)
        if isinstance(cond, All):
            return exp.paren(_and([self._cond(c, entity, col, p, as_of) for c in cond.all]), copy=False)
        if isinstance(cond, Any_):
            return _or([self._cond(c, entity, col, p, as_of) for c in cond.any])
        if isinstance(cond, Not):
            return exp.Not(this=exp.paren(self._cond(cond.not_, entity, col, p, as_of), copy=False))
        raise TelaError(ErrorCode.SPEC_INVALID, "Unsupported condition.")

    def _where(self, vs: ValidatedSpec, col: Any, p: Params, as_of: date,
                aliases: dict[str, str] | None = None,
                time_override: tuple[date, date] | None = None) -> exp.Expression | None:
        conds: list[exp.Expression] = []
        for fref, flt in vs.filters:
            conds.append(self._filter(fref, flt, col, p, as_of))
        if vs.time is not None:
            fref, tspec = vs.time
            rng = tspec.range
            start: date | None
            end: date | None
            if time_override is not None:
                start, end = time_override
            elif rng is not None:
                start, end = resolve_range(rng, as_of)
            else:
                start, end = None, None
            if start is not None or end is not None:
                c = col(fref.entity, fref.field.key)
                if start is not None:
                    conds.append(exp.GTE(this=c.copy(), expression=p.add(start)))
                if end is not None:
                    if fref.field.data_type == "datetime":
                        conds.append(exp.LT(this=c.copy(), expression=p.add(end + timedelta(days=1))))
                    else:
                        conds.append(exp.LTE(this=c.copy(), expression=p.add(end)))
        # Semi-join filters (US-178): EXISTS subqueries
        for rei in vs.exists_in:
            conds.append(self._exists_in(rei, vs, col, aliases or {}, p, as_of))
        return _and(conds) if conds else None

    def _exists_in(self, rei: ResolvedExistsIn, vs: ValidatedSpec, col: Any,
                   aliases: dict[str, str], p: Params, as_of: date) -> exp.Expression:
        """Build an EXISTS (SELECT 1 FROM linked WHERE join_cond AND filters) subquery (US-178)."""
        linked_alias = f"ex_{rei.entity}"
        sub = exp.select(exp.Literal.number(1)).from_(table_of(rei.entity_obj).as_(linked_alias))
        join_conds: list[exp.Expression] = []
        root_alias = aliases.get(vs.root.key, "t0")
        for rf, lf in zip(rei.root_fields, rei.linked_fields, strict=True):
            root_f = vs.root.field(rf)
            linked_f = rei.entity_obj.field(lf)
            assert root_f is not None and linked_f is not None
            join_conds.append(exp.EQ(
                this=_col(root_alias, root_f.source_column),
                expression=_col(linked_alias, linked_f.source_column),
            ))
        for fref, flt in rei.filters:
            def ei_col(entity: str, field_key: str) -> exp.Expression:
                f = rei.entity_obj.field(field_key)
                assert f is not None
                return _fcol(linked_alias, f)
            join_conds.append(self._filter(fref, flt, ei_col, p, as_of))
        if rei.where is not None:  # the certified measure's rule (US-178 follow-up)
            def m_col(entity: str, field_key: str) -> exp.Expression:
                f = rei.entity_obj.field(field_key)
                assert f is not None
                return _fcol(linked_alias, f)
            join_conds.append(self._cond(rei.where, rei.entity, m_col, p, as_of))
        sub = sub.where(_and(join_conds))
        return exp.Exists(this=sub)

    def _filter(self, fref: FieldRef, flt: Filter, col: Any, p: Params, as_of: date) -> exp.Expression:
        if fref.computed:
            e = self._dim_expr(fref.computed, fref.entity, col, p, as_of)
            picked: exp.Expression = exp.In(this=e, expressions=[p.add(str(v)) for v in flt.values])
            return exp.Not(this=picked) if flt.op in ("!=", "not_in") else picked
        c = col(fref.entity, fref.field.key)
        if fref.not_set and fref.not_set in map(str, flt.values) and flt.op in ("=", "!=", "in", "not_in"):
            # "Not set" means blank (US-156): the other values filter as usual, OR'd with (or AND'd against) blank.
            rest = [v for v in flt.values if str(v) != fref.not_set]
            positive = flt.op in ("=", "in")
            blank = exp.Is(this=c.copy(), expression=exp.Null())
            if not rest:
                return blank if positive else exp.Not(this=blank)
            plain = FieldRef(fref.entity, fref.field, fref.label, fref.value_labels)
            others = self._filter(plain, flt.model_copy(update={"values": rest, "op": "in" if positive else "not_in"}),
                                  col, p, as_of)
            if positive:
                return exp.paren(exp.or_(others, blank, copy=False), copy=False)
            return exp.and_(others, exp.Not(this=blank), copy=False)
        values: list[Any] = list(flt.values)
        if fref.value_labels and flt.op in ("=", "!=", "in", "not_in"):
            # Filter on a display label (e.g. Amber) matches the raw values mapped to it (e.g. Yellow).
            labels = dict(fref.value_labels)
            expanded = [*values, *(raw for raw, lbl in labels.items() if lbl in map(str, values))]
            values = list(dict.fromkeys(expanded))
            op = {"=": "in", "!=": "not_in"}.get(flt.op, flt.op)
        else:
            op = flt.op
        if fref.field.data_type in ("date", "datetime"):
            values = [date.fromisoformat(str(v)[:10]) for v in values]
        if op in ("in", "not_in"):
            node: exp.Expression = exp.In(this=c, expressions=[p.add(v) for v in values])
            return exp.Not(this=node) if op == "not_in" else node
        if op == "between":
            return exp.Between(this=c, low=p.add(values[0]), high=p.add(values[1]))
        if op in ("is_null", "not_null"):
            node = exp.Is(this=c, expression=exp.Null())
            return exp.Not(this=node) if op == "not_null" else node
        cls = {"=": exp.EQ, "!=": exp.NEQ, "<": exp.LT, "<=": exp.LTE, ">": exp.GT, ">=": exp.GTE}[op]
        node = cls(this=c, expression=p.add(values[0]))
        return node

    def _single_currency(self, vs: ValidatedSpec, currency_field: str) -> bool:
        for fref, flt in vs.filters:
            if fref.field.key == currency_field and fref.entity == vs.root.key and (
                flt.op == "=" or (flt.op == "in" and len(flt.values) == 1)
            ):
                return True
        return False

    # ------------------------------------------------------------------ breakdowns and personal fields

    def _breakdown_expr(self, b: ResolvedBreakdown, col: Any, p: Params, as_of: date) -> exp.Expression:
        if b.field.computed and not b.grain and b.personal is None:
            return self._dim_expr(b.field.computed, b.field.entity, col, p, as_of)
        c: exp.Expression = col(b.field.entity, b.field.field.key)
        if b.grain:
            return exp.Cast(this=exp.TimestampTrunc(this=c, unit=exp.var(b.grain.upper())),
                            to=exp.DataType.build("date"))
        if b.personal is not None:
            return self._personal(b.personal, c, b.field, p)
        out: exp.Expression = c
        if b.field.value_labels:
            out = exp.Case(
                ifs=[exp.If(this=exp.EQ(this=c.copy(), expression=p.add(raw)), true=p.add(lbl))
                     for raw, lbl in b.field.value_labels],
                default=c.copy(),
            )
        if b.field.not_set:
            out = exp.Case(ifs=[exp.If(this=exp.Is(this=c.copy(), expression=exp.Null()),
                                       true=p.add(b.field.not_set))], default=out)
        return out

    # ------------------------------------------------------------------ row values and computed dimensions (US-156)

    def _days(self, start: exp.Expression, end: exp.Expression) -> exp.Expression:
        """Whole days from start to end (both dates)."""
        if self.dialect == "postgres":
            return exp.paren(exp.Sub(this=end, expression=start), copy=False)
        return exp.DateDiff(this=end, expression=start, unit=exp.var("DAY"))

    def _date(self, side: str | AsOf | Today, entity: str, col: Any, p: Params, as_of: date) -> exp.Expression:
        if isinstance(side, Today):
            raise TelaError(ErrorCode.SPEC_INVALID, "Today was not fixed for this run.")
        if isinstance(side, AsOf):
            return exp.Cast(this=p.add(as_of + timedelta(days=side.asof_offset_days)), to=exp.DataType.build("date"))
        return exp.Cast(this=col(entity, side), to=exp.DataType.build("date"))

    def _value(self, v: Any, entity: str, col: Any, p: Params, as_of: date) -> exp.Expression:
        if isinstance(v, str):
            out: exp.Expression = col(entity, v)
            return out
        if isinstance(v, int | float):
            return exp.Literal.number(v)
        if isinstance(v, DaysBetween):
            return self._days(self._date(v.days_from, entity, col, p, as_of), self._date(v.days_to, entity, col, p,
                                                                                         as_of))
        if isinstance(v, Calc):
            left, right = self._value(v.left, entity, col, p, as_of), self._value(v.right, entity, col, p, as_of)
            if v.calc == "div":
                return calculators.ratio(left, right)
            cls = {"add": exp.Add, "sub": exp.Sub, "mul": exp.Mul}[v.calc]
            return exp.paren(cls(this=left, expression=right), copy=False)
        if isinstance(v, Calculator):
            def field_or_none(key: str) -> exp.Expression | None:
                try:
                    out: exp.Expression = col(entity, key)
                    return out
                except TelaError:
                    return None

            return calculators.build(v.calculator, v.output, col=field_or_none, days=self._days,
                                     as_of=exp.Cast(this=p.add(as_of), to=exp.DataType.build("date")), text=p.add)
        raise TelaError(ErrorCode.SPEC_INVALID, "Unsupported value.")

    def _dim_expr(self, d: Dimension, entity: str, col: Any, p: Params, as_of: date) -> exp.Expression:
        """A computed dimension's label: the value itself, or the first band holding it (value > above, <= upto)."""
        v = self._value(d.of, entity, col, p, as_of)
        blank = exp.Is(this=v.copy(), expression=exp.Null())
        if not d.bands:
            if d.not_set:
                return exp.Case(ifs=[exp.If(this=blank, true=p.add(d.not_set))], default=v)
            return v
        ifs = [exp.If(this=blank, true=p.add(d.not_set) if d.not_set else exp.Null())]
        for band in d.bands:
            conds: list[exp.Expression] = []
            if band.above is not None:
                conds.append(exp.GT(this=v.copy(), expression=exp.Literal.number(band.above)))
            if band.upto is not None:
                conds.append(exp.LTE(this=v.copy(), expression=exp.Literal.number(band.upto)))
            ifs.append(exp.If(this=_and(conds) if conds else _true(), true=p.add(band.label)))
        return exp.Case(ifs=ifs, default=p.add(d.other))

    def _personal(self, pf: PersonalField, c: exp.Expression, fref: FieldRef, p: Params) -> exp.Expression:
        if pf.kind == "grouping":
            # RULE-012: blank → "Not set"; listed values → their group; anything else → catch-all.
            text: exp.Expression = c if fref.field.data_type == "text" else exp.Cast(
                this=c, to=exp.DataType.build("text"))
            trimmed = exp.Trim(this=text)
            norm: exp.Expression = trimmed if pf.case_sensitive else exp.Lower(this=trimmed.copy())
            ifs = [exp.If(this=exp.or_(exp.Is(this=trimmed.copy(), expression=exp.Null()),
                                       exp.EQ(this=trimmed.copy(), expression=exp.Literal.string("")), copy=False),
                          true=p.add(pf.not_set))]
            for g in pf.groups:
                vals = [str(v) if pf.case_sensitive else str(v).strip().lower() for v in g.values]
                ifs.append(exp.If(this=exp.In(this=norm.copy(), expressions=[p.add(v) for v in vals]),
                                  true=p.add(g.label)))
            return exp.Case(ifs=ifs, default=p.add(pf.catch_all))
        # RULE-013: half-open bands [lower, upper); the last band may close at an inclusive maximum.
        ifs = [exp.If(this=exp.Is(this=c.copy(), expression=exp.Null()), true=p.add("Not set")),
               exp.If(this=exp.LT(this=c.copy(), expression=p.add(pf.bands[0].lower)),
                      true=p.add(f"Below {pf.bands[0].lower:g}"))]
        for i, band in enumerate(pf.bands):
            last = i == len(pf.bands) - 1
            lower: exp.Expression = exp.GTE(this=c.copy(), expression=p.add(band.lower))
            if band.upper is None:
                cond: exp.Expression = lower
            elif last and pf.closing_max is not None and pf.closing_max == band.upper:
                cond = exp.and_(lower, exp.LTE(this=c.copy(), expression=p.add(band.upper)), copy=False)
            else:
                cond = exp.and_(lower, exp.LT(this=c.copy(), expression=p.add(band.upper)), copy=False)
            ifs.append(exp.If(this=cond, true=p.add(band.label)))
        last_upper = pf.bands[-1].upper
        return exp.Case(ifs=ifs, default=p.add(f"Above {last_upper:g}" if last_upper is not None else "Not set"))

    # ------------------------------------------------------------------ measures

    def _measure_expr(self, expr: MeasureExpr | float, leaf: Any) -> exp.Expression:
        if isinstance(expr, AggExpr):
            node: exp.Expression = leaf(expr)
            return node
        if not isinstance(expr, Arith):  # a plain number operand (US-156)
            return exp.Literal.number(expr)
        left, right = self._measure_expr(expr.left, leaf), self._measure_expr(expr.right, leaf)
        if expr.op == "div":
            out: exp.Expression = exp.Div(
                this=exp.Cast(this=left, to=exp.DataType.build("decimal")),
                expression=exp.Nullif(this=right, expression=exp.Literal.number(0)),
            )
        else:
            cls = {"add": exp.Add, "sub": exp.Sub, "mul": exp.Mul}[expr.op]
            out = cls(this=left, expression=right)
        out = exp.paren(out, copy=False)
        if expr.scale is not None:
            out = exp.paren(exp.Mul(this=out, expression=exp.Literal.number(expr.scale)), copy=False)
        return out

    def _agg_input(self, agg: AggExpr, root: str, col: Any, p: Params, as_of: date) -> exp.Expression:
        if agg.value is not None:
            return self._value(agg.value, root, col, p, as_of)
        assert agg.field is not None
        out: exp.Expression = col(root, agg.field)
        return out

    def _direct_agg(self, agg: AggExpr, root: str, col: Any, p: Params, as_of: date) -> exp.Expression:
        value: exp.Expression = (self._agg_input(agg, root, col, p, as_of) if agg.field or agg.value is not None
                                 else exp.Literal.number(1))
        if agg.where is not None:
            value = exp.Case(ifs=[exp.If(this=self._cond(agg.where, root, col, p, as_of), true=value)])
        return self._agg_node(agg, value, star=agg.field is None and agg.value is None and agg.where is None)

    def _base_agg(self, agg: AggExpr, names: tuple[AggExpr, str | None, str | None]) -> exp.Expression:
        _, a_name, w_name = names
        value: exp.Expression = exp.column(a_name, table=BASE_CTE) if a_name else exp.column("__pk0", table=BASE_CTE)
        if w_name:
            value = exp.Case(ifs=[exp.If(this=exp.EQ(this=exp.column(w_name, table=BASE_CTE),
                                                     expression=exp.Literal.number(1)), true=value)])
        return self._agg_node(agg, value, star=False)

    def _agg_node(self, agg: AggExpr, value: exp.Expression, *, star: bool) -> exp.Expression:
        if agg.agg == "count":
            return exp.Count(this=exp.Star() if star else value)
        if agg.agg == "count_distinct":
            return exp.Count(this=exp.Distinct(expressions=[value]))
        if agg.agg in ("median", "percentile"):
            q = exp.Literal.number(0.5 if agg.agg == "median" else agg.p)
            if self.dialect == PERCENTILE_DIALECTS[0]:
                return exp.WithinGroup(this=exp.PercentileCont(this=q),
                                       expression=exp.Order(expressions=[exp.Ordered(this=value)]))
            if self.dialect == "duckdb":
                return exp.Anonymous(this="quantile_cont", expressions=[value, q])
            raise TelaError(ErrorCode.MEASURE_UNAVAILABLE, "This figure needs a median or percentile, which this "
                            "database can't compute yet.", {"admin_detail": {"dialect": self.dialect}})
        cls = {"sum": exp.Sum, "avg": exp.Avg, "min": exp.Min, "max": exp.Max}[agg.agg]
        if agg.agg == "avg" and self.dialect == "tsql":  # T-SQL AVG over integers (day counts) truncates
            value = exp.Cast(this=value, to=exp.DataType.build("float"))
        node: exp.Expression = cls(this=value)
        return node

    # ------------------------------------------------------------------ derived figures and shares (US-176)

    def _with_extras(self, vs: ValidatedSpec, q: exp.Select, outputs: list[str]) -> exp.Select:
        """``SELECT d.*, <derived>, <share> FROM (q) AS tela_d``: each extra works on the grouped columns, and a
        share divides by ``SUM(x) OVER ()``, the total of every group (before any top N). A CTE moves out."""
        with_ = q.args.get("with")
        if with_ is not None:
            q.set("with", None)
        cols = {n: exp.column(n, table=EXTRA_ALIAS) for n in [*outputs, *(m.key for m in vs.measures)]}
        outer = exp.select(*(c.copy() for c in cols.values()))
        for e in vs.extras:  # a derived key comes from the spec: quoted, so "over" or "order" stay names
            outer = outer.select(extra_expr(e, cols).as_(e.key, quoted=True))
        outer = outer.from_(q.subquery(EXTRA_ALIAS))
        if with_ is not None:
            outer.set("with", with_)
        return outer

    # ------------------------------------------------------------------ window operations (US-177)

    def _with_window_ops(self, vs: ValidatedSpec, q: exp.Select,
                         outputs: list[str]) -> tuple[exp.Select, list[ColumnMeta]]:
        """Wrap the grouped query with window function columns. Reuses the "FROM (q) AS tela_w" wrapping pattern
        (like US-176). Window output columns are new companion/measure columns with labels and formats.
        Returns (new_query, window_column_metadata)."""
        with_ = q.args.get("with")
        if with_ is not None:
            q.set("with", None)

        all_cols = [*outputs, *(m.key for m in vs.measures), *(e.key for e in vs.extras)]
        cols = {n: exp.column(n, table=WINDOW_ALIAS) for n in all_cols}
        if self.dialect != "duckdb" and any(wo.kind == "percentile" for wo in vs.window_ops):
            q = self._percentile_positions(vs, q, all_cols)
        outer = exp.select(*(c.copy() for c in cols.values()))
        win_columns: list[ColumnMeta] = []

        def ties(wo: ResolvedWindowOp, *skip: str) -> list[exp.Expression]:
            """The other breakdowns, so equal values fall the same way on every engine."""
            return [cols[o].copy() for o in outputs if o != wo.partition_ref and o not in skip]

        for wo in vs.window_ops:
            mcol = cols[wo.measure_key].copy()
            partition: list[exp.Expression] = []
            if wo.partition_ref:
                partition = [cols[wo.partition_ref].copy()]

            if wo.kind == "top_n_per_group":
                order_dir = wo.params["order"] == "desc"
                rn = exp.Window(
                    this=exp.Anonymous(this="ROW_NUMBER", expressions=[]),
                    partition_by=partition,
                    order=exp.Order(expressions=_by_value(mcol, order_dir, ties(wo))),
                )
                rn_name = f"{wo.measure_key}__rn"
                outer = outer.select(rn.as_(rn_name))
                # We need to add another outer query to filter WHERE rn <= n
                win_columns.append(ColumnMeta(rn_name, "measure", "Row rank",
                                              f"window:top_n:{wo.measure_key}", "integer", of=wo.measure_key))

            elif wo.kind == "running_total":
                partition = partition or [cols[g].copy() for g in wo.params["groups"]]
                rt = exp.Window(
                    this=exp.Sum(this=mcol),
                    partition_by=partition or None,
                    order=exp.Order(expressions=_by_value(cols[wo.params["along"]].copy(), False,
                                                          ties(wo, wo.params["along"]))),
                    spec=exp.WindowSpec(kind="ROWS", start="UNBOUNDED", start_side="PRECEDING", end="CURRENT ROW"),
                )
                rt_name = f"{wo.measure_key}__cumsum"
                outer = outer.select(rt.as_(rt_name))
                m_obj = next((m for m in vs.measures if m.key == wo.measure_key), None)
                win_columns.append(ColumnMeta(rt_name, "measure",
                                              f"{m_obj.label if m_obj else wo.measure_key} (running total)",
                                              f"window:running_total:{wo.measure_key}", "decimal",
                                              m_obj.format if m_obj else None, of=wo.measure_key))

            elif wo.kind == "moving_avg":
                window_k = wo.params["window"]
                partition = partition or [cols[g].copy() for g in wo.params["groups"]]
                ma = exp.Window(
                    this=exp.Avg(this=exp.cast(mcol, "DOUBLE")),
                    partition_by=partition or None,
                    order=exp.Order(expressions=_by_value(cols[wo.params["along"]].copy(), False,
                                                          ties(wo, wo.params["along"]))),
                    spec=exp.WindowSpec(
                        kind="ROWS",
                        start=exp.Literal.number(window_k - 1),
                        start_side="PRECEDING",
                        end="CURRENT ROW",
                    ),
                )
                ma_name = f"{wo.measure_key}__mavg{window_k}"
                outer = outer.select(ma.as_(ma_name))
                m_obj = next((m for m in vs.measures if m.key == wo.measure_key), None)
                win_columns.append(ColumnMeta(ma_name, "measure",
                                              f"{m_obj.label if m_obj else wo.measure_key} ({window_k}-period avg)",
                                              f"window:moving_avg:{wo.measure_key}", "decimal",
                                              m_obj.format if m_obj else None, of=wo.measure_key))

            elif wo.kind == "rank":
                order_dir = wo.params["order"] == "desc"
                rank_expr = exp.Window(
                    this=exp.Anonymous(this="RANK", expressions=[]),
                    partition_by=partition or None,
                    order=exp.Order(expressions=_by_value(mcol, order_dir, [])),  # equal values share a rank
                )
                rk_name = f"{wo.measure_key}__rank"
                outer = outer.select(rank_expr.as_(rk_name))
                win_columns.append(ColumnMeta(rk_name, "measure", "Rank",
                                              f"window:rank:{wo.measure_key}", "integer", of=wo.measure_key))

            elif wo.kind == "bucket":
                k, order_dir = wo.params["k"], wo.params["order"] == "desc"
                labels = wo.params.get("labels", [])
                ntile = exp.Window(
                    this=exp.Anonymous(this="NTILE", expressions=[exp.Literal.number(k)]),
                    partition_by=partition or None,
                    order=exp.Order(expressions=_by_value(mcol, order_dir, ties(wo))),
                )
                bk_name = f"{wo.measure_key}__bucket"
                if labels:
                    # Map NTILE(k) 1..k to labels via CASE
                    ifs = [exp.If(this=exp.EQ(this=ntile.copy(), expression=exp.Literal.number(i + 1)),
                                  true=exp.Literal.string(labels[i])) for i in range(len(labels))]
                    bucket_expr: exp.Expression = exp.Case(ifs=ifs, default=exp.Null())
                else:
                    bucket_expr = ntile
                outer = outer.select(bucket_expr.as_(bk_name))
                win_columns.append(ColumnMeta(bk_name, "dimension", "Bucket",
                                              f"window:bucket:{wo.measure_key}", "text"))

            elif wo.kind == "percentile":
                q_val = wo.params["q"]
                p_name = wo.params["p"]
                if self.dialect == "duckdb":
                    pct: exp.Expression = exp.Window(
                        this=exp.Anonymous(this="quantile_cont",
                                           expressions=[exp.cast(mcol, "DOUBLE"), exp.Literal.number(q_val)]),
                        partition_by=partition or None,
                    )
                else:
                    pct = self._percentile_from_positions(wo, mcol, partition, q_val)
                pct_name = f"{wo.measure_key}__{p_name}"
                outer = outer.select(pct.as_(pct_name))
                m_obj = next((m for m in vs.measures if m.key == wo.measure_key), None)
                win_columns.append(ColumnMeta(pct_name, "measure",
                                              f"{m_obj.label if m_obj else wo.measure_key} ({p_name.upper()})",
                                              f"window:percentile:{wo.measure_key}", "decimal",
                                              m_obj.format if m_obj else None, of=wo.measure_key))

            elif wo.kind == "having":
                # Having: compiled as an outer WHERE on the grouped result
                op_str = wo.params["op"]
                value = wo.params["value"]
                cls_map = {"=": exp.EQ, "!=": exp.NEQ, "<": exp.LT, "<=": exp.LTE, ">": exp.GT, ">=": exp.GTE}
                having_cond = cls_map[op_str](this=mcol.copy(), expression=exp.Literal.number(value))
                outer = outer.where(having_cond)

        outer = outer.from_(q.subquery(WINDOW_ALIAS))
        if with_ is not None:
            outer.set("with", with_)

        # For top_n_per_group, add another wrapper to filter by row number
        top_n_op = next((wo for wo in vs.window_ops if wo.kind == "top_n_per_group"), None)
        if top_n_op is not None:
            rn_name = f"{top_n_op.measure_key}__rn"
            with2 = outer.args.get("with")
            if with2 is not None:
                outer.set("with", None)
            # Remove the __rn column from the final output (it was only for filtering)
            final_cols = list(all_cols)
            # Add all window columns except __rn
            for wc in win_columns:
                if wc.name != rn_name:
                    final_cols.append(wc.name)
            outer2 = exp.select(*(exp.column(n, table=TOPN_ALIAS) for n in final_cols))
            outer2 = outer2.from_(outer.subquery(TOPN_ALIAS))
            outer2 = outer2.where(exp.LTE(this=exp.column(rn_name, table=TOPN_ALIAS),
                                           expression=exp.Literal.number(top_n_op.params["n"])))
            if with2 is not None:
                outer2.set("with", with2)
            win_columns = [wc for wc in win_columns if wc.name != rn_name]
            return outer2, win_columns

        return outer, win_columns

    @staticmethod
    def _percentile_partition(wo: ResolvedWindowOp, alias: str) -> list[exp.Expression]:
        return [exp.column(wo.partition_ref, table=alias)] if wo.partition_ref else []

    def _percentile_positions(self, vs: ValidatedSpec, q: exp.Select, all_cols: list[str]) -> exp.Select:
        """PostgreSQL has no windowed PERCENTILE_CONT and MySQL has none at all, so percentiles are computed
        exactly from positions on every SQL engine but DuckDB: this layer adds each value's 0-based position
        among the non-null values of its group and the group's non-null count (``SELECT …, ROW_NUMBER() - 1,
        COUNT(m) FROM (q) AS tela_p``); ``_percentile_from_positions`` interpolates as PERCENTILE_CONT does."""
        inner = exp.select(*(exp.column(n, table=PCT_ALIAS) for n in all_cols))
        for wo in vs.window_ops:
            if wo.kind != "percentile":
                continue
            m = exp.column(wo.measure_key, table=PCT_ALIAS)
            part = self._percentile_partition(wo, PCT_ALIAS)
            nulls_apart = exp.Case(ifs=[exp.If(this=exp.Is(this=m.copy(), expression=exp.Null()),
                                               true=exp.Literal.number(1))], default=exp.Literal.number(0))
            rn = exp.Window(this=exp.Anonymous(this="ROW_NUMBER", expressions=[]),
                            partition_by=[*(e.copy() for e in part), nulls_apart],
                            order=exp.Order(expressions=[exp.Ordered(this=m.copy(), desc=False)]))
            cnt = exp.Window(this=exp.Count(this=m.copy()), partition_by=[e.copy() for e in part] or None)
            inner = inner.select(exp.Sub(this=rn, expression=exp.Literal.number(1)).as_(f"{wo.measure_key}__ppos"),
                                 cnt.as_(f"{wo.measure_key}__pcnt"))
        return inner.from_(q.subquery(PCT_ALIAS))

    @staticmethod
    def _percentile_from_positions(wo: ResolvedWindowOp, mcol: exp.Expression, partition: list[exp.Expression],
                                   q_val: float) -> exp.Expression:
        """PERCENTILE_CONT by linear interpolation:
        h = q*(n - 1); v(floor h) + (h - floor h)*(v(ceil h) - v(floor h))."""
        pos = exp.column(f"{wo.measure_key}__ppos", table=WINDOW_ALIAS)
        cnt = exp.column(f"{wo.measure_key}__pcnt", table=WINDOW_ALIAS)
        h = exp.Mul(this=exp.Literal.number(q_val),
                    expression=exp.Paren(this=exp.Sub(this=cnt, expression=exp.Literal.number(1))))
        lo, hi = exp.Floor(this=h.copy()), exp.Ceil(this=h.copy())

        def value_at(at: exp.Expression) -> exp.Expression:
            pick = exp.Case(ifs=[exp.If(this=exp.EQ(this=pos.copy(), expression=at),
                                        true=exp.cast(mcol.copy(), "DOUBLE"))])
            return exp.Window(this=exp.Max(this=pick), partition_by=[e.copy() for e in partition] or None)

        v_lo = value_at(lo.copy())
        frac = exp.Paren(this=exp.Sub(this=h.copy(), expression=lo.copy()))
        return exp.Add(this=v_lo.copy(), expression=exp.Mul(
            this=frac, expression=exp.Paren(this=exp.Sub(this=value_at(hi), expression=v_lo))))

    # ------------------------------------------------------------------ ordering and metadata

    def _order(self, vs: ValidatedSpec, q: exp.Select, targets: dict[str, exp.Expression], p: Params) -> exp.Select:
        spec = vs.spec
        sorts = list(spec.sort)
        if not sorts:
            grain_b = next((b for b in vs.breakdowns if b.grain), None)
            if grain_b is not None:
                return q.order_by(exp.Ordered(this=exp.column(grain_b.output), desc=False))
            top_n = next((wo for wo in vs.window_ops if wo.kind == "top_n_per_group"), None)
            if top_n is not None and top_n.partition_ref:  # each group's top rows together
                return q.order_by(exp.Ordered(this=exp.column(top_n.partition_ref), desc=False),
                                  exp.Ordered(this=exp.column(top_n.measure_key), desc=top_n.params["order"] == "desc"))
            if vs.breakdowns:
                return q.order_by(exp.Ordered(this=exp.column(vs.measures[0].key), desc=True))
            return q
        for s in sorts:
            desc = s.dir == "desc"
            if s.by == "measure":
                q = q.order_by(exp.Ordered(this=exp.column(vs.measures[0].key), desc=desc), append=True)
                continue
            if s.by.startswith("measure:"):
                q = q.order_by(exp.Ordered(this=exp.column(s.by.split(":", 1)[1]), desc=desc), append=True)
                continue
            extra = next((e for e in vs.extras if e.ref == s.by), None)
            if extra is not None:
                q = q.order_by(exp.Ordered(this=exp.column(extra.key, quoted=True), desc=desc), append=True)
                continue
            b = next((b for b in vs.breakdowns if b.ref == s.by), None)
            assert b is not None
            if b.field.sort_order and not b.grain and not b.personal:
                order = exp.Case(
                    ifs=[exp.If(this=exp.EQ(this=targets[s.by].copy(), expression=p.add(v)),
                                true=exp.Literal.number(i)) for i, v in enumerate(b.field.sort_order)],
                    default=exp.Literal.number(len(b.field.sort_order)),
                )
                q = q.order_by(exp.Ordered(this=order, desc=desc), append=True)
            else:
                q = q.order_by(exp.Ordered(this=exp.column(b.output), desc=desc), append=True)
        return q

    @staticmethod
    def _column_meta(b: ResolvedBreakdown) -> ColumnMeta:
        kind = "period" if b.grain else ("currency" if b.output == "currency" else "dimension")
        label = b.personal.key if b.personal else b.field.label
        return ColumnMeta(b.output, kind, label, b.ref, "date" if b.grain else b.field.field.data_type,
                          None, dict(b.field.value_labels), b.field.sort_order, b.field.qualified)


def _ties(exprs: list[exp.Expression]) -> list[exp.Ordered]:
    return [exp.Ordered(this=e, desc=False) for e in exprs]


def _by_value(value: exp.Expression, desc: bool, ties: list[exp.Expression]) -> list[exp.Ordered]:
    """Window order by a value with empty values last on every engine (PostgreSQL puts NULLs first when
    descending; MySQL, SQL Server and DuckDB last), then ``ties``."""
    nulls_last = exp.Case(ifs=[exp.If(this=exp.Is(this=value.copy(), expression=exp.Null()),
                                      true=exp.Literal.number(1))], default=exp.Literal.number(0))
    return [exp.Ordered(this=nulls_last, desc=False), exp.Ordered(this=value, desc=desc), *_ties(ties)]


EXTRA_ALIAS = "tela_d"
WINDOW_ALIAS = "tela_w"
PCT_ALIAS = "tela_p"  # positions for an exact percentile on engines without a windowed PERCENTILE_CONT
TOPN_ALIAS = "tela_tn"


def _pct(e: exp.Expression) -> exp.Expression:
    return exp.paren(exp.Mul(this=e, expression=exp.Literal.number(100)), copy=False)


def extra_expr(e: Extra, cols: dict[str, exp.Column]) -> exp.Expression:
    """A derived figure or share over the measures' result columns; a zero or blank divisor gives NULL."""
    a = cols[e.of].copy()
    if e.op == "share":
        return _pct(calculators.ratio(a, exp.Window(this=exp.Sum(this=a.copy()))))
    assert e.to is not None
    b = cols[e.to].copy()
    if e.op == "ratio":
        return calculators.ratio(a, b)
    if e.op == "difference":
        return exp.paren(exp.Sub(this=a, expression=b), copy=False)
    if e.op == "percent_of":
        return _pct(calculators.ratio(a, b))
    return _pct(calculators.ratio(exp.paren(exp.Sub(this=a, expression=b), copy=False), b.copy()))


def extra_value(e: Extra, values: dict[str, Any]) -> Any:
    """A derived figure over one row's measure values, for rows built outside the database (a figure over no
    rows); shares need a breakdown, so never come here."""
    a, b = values.get(e.of), values.get(e.to or "")
    if a is None or b is None:
        return None
    if e.op == "difference":
        return a - b
    if not b:
        return None
    if e.op == "ratio":
        return a / b
    return (a if e.op == "percent_of" else a - b) / b * 100


def extra_columns(vs: ValidatedSpec) -> list[ColumnMeta]:
    return [ColumnMeta(e.key, "measure", e.label, e.ref, "decimal", e.format, of=e.of) for e in vs.extras]


def compile_field_query(snapshot: ModelSnapshot, fref: FieldRef, kind: str, *, dialect: str,
                        limit: int | None = None) -> CompiledQuery:
    """Single-field helper queries over one entity: scoped distinct values (filter options, RULE-005), the newest
    value of a freshness column (RULE-018), and the field profile AG-03 reads (US-175): ``filled`` (rows with a value),
    ``min`` and ``top`` (the most frequent values with their row counts). Scope is injected like any other query."""
    ent = snapshot.entity(fref.entity)
    assert ent is not None
    c = _fcol("t0", fref.field)
    q = exp.select().from_(table_of(ent).as_("t0"))
    if kind == "distinct":
        q = (q.select(c.as_("value")).where(exp.Not(this=exp.Is(this=c.copy(), expression=exp.Null())))
             .distinct().order_by(exp.Ordered(this=exp.column("value"), desc=False)))
        if limit is not None:
            q = q.limit(limit)
        q = order_outside(q, dialect)
    elif kind == "max":
        q = q.select(exp.Max(this=c).as_("value"))
    elif kind == "min":
        q = q.select(exp.Min(this=c).as_("value"))
    elif kind == "count":
        q = q.select(exp.Count(this=exp.Star()).as_("value"))
    elif kind == "filled":
        q = q.select(exp.Count(this=c).as_("value"))
    elif kind == "top":
        q = (q.select(c.as_("value"), exp.Count(this=exp.Star()).as_("n"))
             .where(exp.Not(this=exp.Is(this=c.copy(), expression=exp.Null()))).group_by(c.copy())
             .order_by(exp.Ordered(this=exp.column("n"), desc=True), exp.Ordered(this=exp.column("value"), desc=False)))
        if limit is not None:
            q = q.limit(limit)
        q = order_outside(q, dialect)
    else:
        raise ValueError(kind)
    meta = [ColumnMeta("value", "dimension", fref.label, f"field:{fref.qualified}", fref.field.data_type,
                       field=fref.qualified)]
    if kind == "top":
        meta.append(ColumnMeta("n", "measure", "Rows", "rows", "integer"))
    return CompiledQuery(q, Params(), meta, {"t0": ent.key}, False, dialect, {ent.key: {fref.field.source_column}})


def _bucket_end(start: date, grain: str) -> date:
    if grain == "day":
        return start + timedelta(days=1)
    if grain == "week":
        return start + timedelta(days=7)
    if grain == "month":
        return _add_months(start, 1)
    if grain == "quarter":
        return _add_months(start, 3)
    return date(start.year + 1, 1, 1)


class RecordCompiler(SqlCompiler):
    """Records behind a mark (FEAT-044; RULE-025): the measure's own condition AND the clicked mark AND the
    widget and page filters, listing distinct root records (A2: join measures list distinct roots)."""

    sorts_records = True  # detail tables (US-158) sort in the database

    def compile_records(self, vs: ValidatedSpec, *, marks: dict[str, Any], as_of: date, fields: list[str],
                        limit: int, offset: int, count_only: bool = False,
                        order: list[tuple[str, bool]] | None = None) -> CompiledQuery:
        snap = vs.snapshot
        measure = vs.measures[0]
        if not isinstance(measure.expression, AggExpr):
            raise TelaError(ErrorCode.SPEC_INVALID, "This figure combines several measures and can't be listed "
                            "as records.")
        p = Params()
        aliases, joins, _ = self._join_plan(snap, vs)
        root = vs.root.key

        def col(entity: str, field_key: str) -> exp.Expression:
            if "." in field_key:
                entity, field_key = field_key.split(".", 1)
            ent = snap.entity(entity)
            assert ent is not None
            f = ent.field(field_key)
            if f is None:
                raise TelaError(ErrorCode.SPEC_INVALID, "Unknown field.", {"admin_detail": {"field": field_key}})
            return _fcol(aliases[entity], f)

        conds: list[exp.Expression] = []
        m_expr = measure.expression
        if m_expr.where is not None:
            conds.append(self._cond(m_expr.where, root, col, p, as_of))
        if (m_expr.field is not None or m_expr.value is not None) and m_expr.agg != "count":
            conds.append(exp.Not(this=exp.Is(this=self._agg_input(m_expr, root, col, p, as_of),
                                             expression=exp.Null())))
        where = self._where(vs, col, p, as_of)
        if where is not None:
            conds.append(where)
        by_output = {b.output: b for b in vs.breakdowns}
        for output, value in marks.items():
            b = by_output.get(output)
            if b is None:
                raise TelaError(ErrorCode.SPEC_INVALID, "That mark is not part of this widget.")
            if b.personal is not None:
                raise TelaError(ErrorCode.SPEC_INVALID, "Records can't be listed for a personal grouping yet.")
            if b.field.computed and not b.grain:
                e = self._dim_expr(b.field.computed, b.field.entity, col, p, as_of)
                conds.append(exp.Is(this=e, expression=exp.Null()) if value is None
                             else exp.In(this=e, expressions=[p.add(str(value))]))
                continue
            c = col(b.field.entity, b.field.field.key)
            if value is None or (b.field.not_set and str(value) == b.field.not_set and not b.grain):
                conds.append(exp.Is(this=c, expression=exp.Null()))
            elif b.grain:
                start = date.fromisoformat(str(value)[:10])
                conds.append(exp.and_(exp.GTE(this=c, expression=p.add(start)),
                                      exp.LT(this=c.copy(), expression=p.add(_bucket_end(start, b.grain))), copy=False))
            else:
                raws = [raw for raw, lbl in b.field.value_labels if lbl == str(value)] + [value]
                conds.append(exp.In(this=c, expressions=[p.add(v) for v in dict.fromkeys(raws)]))
        q = exp.select().from_(table_of(vs.root).as_(aliases[root]))
        for j in joins:
            q = q.join(j)
        if conds:
            q = q.where(_and(conds))
        if count_only:
            pk = [col(root, k) for k in vs.root.primary_key]
            inner = q.select(*(c.as_(f"__pk{i}") for i, c in enumerate(pk))).distinct()
            outer = exp.select(exp.Count(this=exp.Star()).as_("n")).from_(inner.subquery("records"))
            meta = [ColumnMeta("n", "measure", "Records", "records", "integer")]
            return CompiledQuery(outer, p, meta, {v: k for k, v in aliases.items()}, False, self.dialect, {})
        columns: list[ColumnMeta] = []
        for fk in fields:
            f = vs.root.field(fk)
            assert f is not None
            q = q.select(col(root, fk).as_(fk))
            columns.append(ColumnMeta(fk, "dimension", f.label, f"field:{root}.{fk}", f.data_type,
                                      field=f"{root}.{fk}"))
        # Blanks last whichever way a detail table sorts; the key keeps pages stable.
        first = [exp.Ordered(this=exp.column(k), desc=d, nulls_first=False) for k, d in order or [] if k in fields]
        q = q.distinct().order_by(*first, *(exp.Ordered(this=exp.column(k), desc=False) for k in vs.root.primary_key
                                            if k in fields)).limit(limit).offset(offset)
        q = order_outside(q, self.dialect)
        return CompiledQuery(q, p, columns, {v: k for k, v in aliases.items()}, False, self.dialect, {})
