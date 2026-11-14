"""Spec → Dataverse Web API requests: OData ``$apply`` / ``$filter`` or FetchXML (US-111; API-067; ADR-013).

Used when a Dataverse connection runs in Web API mode (the TDS endpoint is off). The Web API answers only some
shapes; others are **Unavailable** (``measure_unavailable``) rather than approximated:

* supported: one measure that is a single aggregate (count, count distinct, sum, average, min, max) with its
  condition, broken down by plain fields of its own table, with filters, a time range, sorting and top N;
  record lists, filter values, newest value and row counts;
* unavailable: arithmetic measures, extra measures, links to other tables, period grains, personal groupings
  and bands, label-mapped breakdowns, multi-currency, field-to-field comparisons.

**Scope first.** The scope is always the first ``filter()`` of ``$apply``, the first group of ``$filter`` or the
first ``<filter>`` of FetchXML; ``assert_webapi_scoped`` re-renders it and compares, independently of the
compiler. An "all rows" scope is the always-true ``<primary key> ne null``.

**SQL semantics.** Conditions are compiled to negation normal form (FetchXML has no NOT): ``!=`` and
``not in`` carry an explicit "is not null" guard, as SQL never matches NULL there. When a measure's condition
filters rows before grouping, a second groups query lists every group so groups without matching rows still
show 0 (count) or empty (other aggregates), as in SQL. Sorting (NULLs last ascending) and top N happen in the
runner on the grouped rows.

**Choices.** A choice column ``x`` is offered to the model as its label column ``xname`` (as the TDS endpoint
does). Conditions on ``xname`` compile to the choice codes of ``x``; grouping or listing ``xname`` reads ``x``
and the runner maps codes back to labels.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any
from xml.sax.saxutils import escape, quoteattr

from tela_api.identity.scope import ResolvedCondition, ResolvedScope
from tela_api.query.compiler import ColumnMeta, Params, _bucket_end, require_basic, resolve_range
from tela_api.query.scope_injector import _fail
from tela_api.query.validator import FieldRef, ValidatedSpec
from tela_common.errors import ErrorCode, TelaError
from tela_query_spec.expr import AggExpr, All, Any_, AsOf, Compare, InSet, Not, NullCheck, Today
from tela_query_spec.model import ModelEntity, ModelSnapshot
from tela_query_spec.spec import Filter

MSG_UNAVAILABLE = "This figure isn't available from this Dataverse source in Web API mode."


def unavailable(reason: str) -> TelaError:
    return TelaError(ErrorCode.MEASURE_UNAVAILABLE, MSG_UNAVAILABLE, {"admin_detail": {"reason": reason}})


# ----------------------------------------------------------------------------- condition IR (negation normal form)


@dataclass(frozen=True)
class Cmp:
    prop: str
    op: str  # eq ne gt ge lt le
    value: Any


@dataclass(frozen=True)
class Null:
    prop: str
    is_null: bool


@dataclass(frozen=True)
class In:
    prop: str
    values: tuple[Any, ...]
    negate: bool = False


@dataclass(frozen=True)
class And:
    items: tuple[Any, ...]


@dataclass(frozen=True)
class Or:
    items: tuple[Any, ...]


NEGATE = {"eq": "ne", "ne": "eq", "lt": "ge", "le": "gt", "gt": "le", "ge": "lt"}
OPS = {"=": "eq", "!=": "ne", "<": "lt", "<=": "le", ">": "gt", ">=": "ge"}


def cmp(prop: str, op: str, value: Any) -> Any:
    """A comparison with SQL's NULL behaviour: ``ne`` never matches NULL."""
    if value is None:
        return Null(prop, op == "eq")
    if op == "ne":
        return And((Cmp(prop, "ne", value), Null(prop, False)))
    return Cmp(prop, op, value)


def day_cmp(prop: str, op: str, value: Any) -> Any:
    """A comparison with a day on a date-time column (a date field over it, ``ModelField.reads_day``): the day is
    the half-open range [day, day + 1)."""
    if value is None:
        return cmp(prop, op, value)
    day = date.fromisoformat(str(value)[:10])
    nxt = day + timedelta(days=1)
    if op == "eq":
        return And((Cmp(prop, "ge", day), Cmp(prop, "lt", nxt)))
    if op == "ne":
        return Or((Cmp(prop, "lt", day), Cmp(prop, "ge", nxt)))
    return Cmp(prop, *{"lt": ("lt", day), "le": ("lt", nxt), "gt": ("ge", nxt), "ge": ("ge", day)}[op])


@dataclass
class Columns:
    """Source column metadata of one table: types, choice options, label columns (from the catalogue)."""

    meta: dict[str, dict[str, Any]] = field(default_factory=dict)

    def base(self, col: str) -> tuple[str, dict[str, str] | None]:
        m = self.meta.get(col) or {}
        if m.get("virtual_of"):
            return str(m["virtual_of"]), dict(m.get("options") or {})
        return col, None

    def type_of(self, col: str) -> str:
        return str((self.meta.get(col) or {}).get("type") or "nvarchar")


def _choice(cols: Columns, col: str, op: str, values: list[Any]) -> Any:
    """Conditions on a label column become conditions on the choice codes."""
    base, options = cols.base(col)
    if options is None:
        if op in ("eq", "in"):
            return cmp(col, "eq", values[0]) if op == "eq" else In(col, tuple(values))
        return And((In(col, tuple(values), True), Null(col, False))) if op == "not_in" else cmp(col, op, values[0])
    codes = tuple(int(code) for code, label in options.items() if label in {str(v) for v in values})
    if op in ("eq", "in"):
        return In(base, codes or (-(2**31),))  # no such label: a code no record has
    if op in ("ne", "not_in"):
        return And((In(base, codes, True), Null(base, False))) if codes else Null(base, False)
    raise unavailable("ordering comparison on a choice label")


# ----------------------------------------------------------------------------- rendering


def _odata_value(v: Any, sql_type: str) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int | float):
        return repr(v) if isinstance(v, float) else str(v)
    if isinstance(v, datetime):
        return v.strftime("%Y-%m-%dT%H:%M:%SZ")
    if isinstance(v, date):
        return v.isoformat() if sql_type == "date" else f"{v.isoformat()}T00:00:00Z"
    if sql_type == "uniqueidentifier":
        return str(v)
    return "'" + str(v).replace("'", "''") + "'"


def odata(c: Any, cols: Columns) -> str:
    if isinstance(c, Cmp):
        return f"{c.prop} {c.op} {_odata_value(c.value, cols.type_of(c.prop))}"
    if isinstance(c, Null):
        return f"{c.prop} {'eq' if c.is_null else 'ne'} null"
    if isinstance(c, In):
        if not c.values:
            return f"{c.prop} eq null and {c.prop} ne null" if not c.negate else f"{c.prop} ne null"
        parts = [f"{c.prop} {'ne' if c.negate else 'eq'} {_odata_value(v, cols.type_of(c.prop))}" for v in c.values]
        return "(" + (" and " if c.negate else " or ").join(parts) + ")"
    if isinstance(c, And):
        return "(" + " and ".join(odata(i, cols) for i in c.items) + ")"
    if isinstance(c, Or):
        return "(" + " or ".join(odata(i, cols) for i in c.items) + ")"
    raise TypeError(c)


def _fetch_value(v: Any, sql_type: str) -> str:
    if isinstance(v, bool):
        return "1" if v else "0"
    if isinstance(v, datetime):
        return v.strftime("%Y-%m-%dT%H:%M:%SZ")
    if isinstance(v, date):
        return v.isoformat() if sql_type == "date" else f"{v.isoformat()}T00:00:00Z"
    return str(v)


def fetch(c: Any, cols: Columns) -> str:
    if isinstance(c, Cmp):
        return (f"<condition attribute={quoteattr(c.prop)} operator={quoteattr(c.op)} "
                f"value={quoteattr(_fetch_value(c.value, cols.type_of(c.prop)))}/>")
    if isinstance(c, Null):
        return f"<condition attribute={quoteattr(c.prop)} operator=\"{'null' if c.is_null else 'not-null'}\"/>"
    if isinstance(c, In):
        values = "".join(f"<value>{escape(_fetch_value(v, cols.type_of(c.prop)))}</value>" for v in c.values)
        return (f"<condition attribute={quoteattr(c.prop)} operator=\"{'not-in' if c.negate else 'in'}\">"
                f"{values}</condition>")
    if isinstance(c, And | Or):
        kind = "and" if isinstance(c, And) else "or"
        return f"<filter type=\"{kind}\">" + "".join(fetch(i, cols) for i in c.items) + "</filter>"
    raise TypeError(c)


def scope_ir(entity: ModelEntity, scope: ResolvedScope, cols: Columns) -> Any:
    es = scope.for_entity(entity.key)
    if es.via is not None:  # scope through a parent link needs a semi-join this engine path doesn't build (US-155)
        raise _fail("scope_via_link_unsupported", entity=entity.key)
    if es.all_rows:
        pk = entity.field(entity.primary_key[0])
        assert pk is not None
        return Null(pk.source_column, False)  # always true for a primary key
    if not es.alternatives:
        raise _fail("empty_scope", entity=entity.key)

    def one(c: ResolvedCondition) -> Any:
        f = entity.field(c.field)
        if f is None:
            raise _fail("scope_field_not_in_model", entity=entity.key, field=c.field)
        col = f.source_column
        if c.op == "is null":
            return Null(cols.base(col)[0], True)
        op = {"=": "eq", "in": "in", "not in": "not_in"}.get(c.op)
        if op is None:
            raise _fail("scope_operator", op=c.op)
        return _choice(cols, col, op, list(c.values))

    groups = [And(tuple(one(c) for c in alt)) for alt in es.alternatives]
    return groups[0] if len(groups) == 1 else Or(tuple(groups))


# ----------------------------------------------------------------------------- compiled statement


@dataclass
class WebApiCompiled:
    root: ModelEntity
    kind: str  # aggregate | records | count | distinct | max | min | top
    columns: list[ColumnMeta]
    conds: list[Any]
    cols: Columns
    entity_set: str
    fmt: str = "odata"
    groupby: list[str] = field(default_factory=list)
    aggregates: list[tuple[str, str | None, str]] = field(default_factory=list)  # (fn, prop, alias)
    select: list[str] = field(default_factory=list)
    sources: dict[str, str] = field(default_factory=dict)
    labels: dict[str, dict[str, str]] = field(default_factory=dict)
    sort: list[tuple[str, bool]] = field(default_factory=list)
    skip: int = 0
    top: int | None = None
    empty_row: list[Any] | None = None
    groups_conds: list[Any] | None = None  # conditions of the groups query (scope excluded)
    group_fill: list[Any] = field(default_factory=list)
    fixed_limit: int | None = None
    entities: dict[str, str] = field(default_factory=dict)
    used_fields: dict[str, set[str]] = field(default_factory=dict)
    fan_out: bool = False
    dialect: str = "webapi"
    params: Params = field(default_factory=Params)
    tree: None = None
    # DuckDB post-step for window operations (US-177): (sql, extra_columns) or None
    duckdb_post: tuple[str, list[ColumnMeta]] | None = None

    def _query(self, scope_c: Any, conds: list[Any], *, groupby: list[str], aggregates: list[tuple[str, str | None,
               str]], select: list[str], top: int | None) -> dict[str, str]:
        logical = self.root.source_object.rpartition(".")[2]
        if self.fmt == "fetchxml":
            agg = bool(groupby or aggregates)
            attrs = "".join(f"<attribute name={quoteattr(g)} groupby=\"true\" alias={quoteattr(g)}/>" for g in groupby)
            for fn, prop, alias in aggregates:
                pk = self.root.field(self.root.primary_key[0])
                assert pk is not None
                name = prop or pk.source_column
                fx = {"count": "count", "countdistinct": "countcolumn", "sum": "sum", "average": "avg",
                      "min": "min", "max": "max"}[fn]
                distinct = ' distinct="true"' if fn == "countdistinct" else ""
                attrs += f"<attribute name={quoteattr(name)} aggregate=\"{fx}\" alias={quoteattr(alias)}{distinct}/>"
            attrs += "".join(f"<attribute name={quoteattr(s)}/>" for s in select)
            filters = "<filter type=\"and\">" + fetch(scope_c, self.cols) + "".join(
                fetch(c, self.cols) for c in conds) + "</filter>"
            order = ""
            if not agg:
                pk = self.root.field(self.root.primary_key[0])
                assert pk is not None
                order = f"<order attribute={quoteattr(pk.source_column)}/>"
            head = '<fetch aggregate="true">' if agg else (f'<fetch top="{top}">' if top else "<fetch>")
            return {"fetchXml": f"{head}<entity name={quoteattr(logical)}>{filters}{attrs}{order}</entity></fetch>"}
        if groupby or aggregates:
            steps = [f"filter({odata(scope_c, self.cols)})", *(f"filter({odata(c, self.cols)})" for c in conds)]
            items = [f"$count as {a}" if fn == "count" and prop is None else f"{prop} with {fn} as {a}"
                     for fn, prop, a in aggregates]
            if groupby:
                steps.append(f"groupby(({','.join(groupby)})" + (f",aggregate({','.join(items)}))" if items else ")"))
            else:
                steps.append(f"aggregate({','.join(items)})")
            return {"$apply": "/".join(steps)}
        q = {"$select": ",".join(select), "$filter": " and ".join(
            f"({odata(c, self.cols)})" for c in [scope_c, *conds])}
        pk = self.root.field(self.root.primary_key[0])
        assert pk is not None
        q["$orderby"] = f"{pk.source_column} asc"
        if top:
            q["$top"] = str(top)
        return q

    def render(self, snapshot: ModelSnapshot, scope: ResolvedScope, limit: int | None) -> str:
        scope_c = scope_ir(self.root, scope, self.cols)
        post_top = self.top if self.kind in ("aggregate", "records", "top") else (
            limit if self.kind == "distinct" else None)
        query_top = self.skip + int(self.top or 0) if self.kind == "records" else None
        query = self._query(scope_c, self.conds, groupby=self.groupby, aggregates=self.aggregates,
                            select=self.select, top=query_top)
        st: dict[str, Any] = {"entity": self.root.source_object, "entity_set": self.entity_set, "format": self.fmt,
                              "query": query, "columns": [c.name for c in self.columns], "sources": self.sources,
                              "labels": self.labels, "empty_row": self.empty_row,
                              "post": {"sort": [[c, d] for c, d in self.sort], "skip": self.skip, "top": post_top}}
        if self.groups_conds is not None:
            st["groups_query"] = self._query(scope_c, self.groups_conds, groupby=self.groupby, aggregates=[],
                                             select=[], top=None)
            st["groups_keys"] = [c.name for c in self.columns[: len(self.groupby)]]
            st["group_fill"] = self.group_fill
        return json.dumps(st, sort_keys=True, default=str)


# ----------------------------------------------------------------------------- the compiler


class WebApiCompiler:
    def __init__(self, columns: dict[str, dict[str, dict[str, Any]]], entity_sets: dict[str, str],
                 fmt: str = "odata") -> None:
        self.columns = columns  # lower-case source object -> column metadata
        self.entity_sets = entity_sets
        self.fmt = fmt

    def _cols(self, ent: ModelEntity) -> Columns:
        return Columns(self.columns.get(ent.source_object.lower(), {}))

    def _entity_set(self, ent: ModelEntity) -> str:
        logical = ent.source_object.rpartition(".")[2]
        return self.entity_sets.get(logical) or f"{logical}s"

    # ------------------------------------------------------------------ conditions

    def _nnf(self, cond: Any, ent: ModelEntity, cols: Columns, as_of: date, neg: bool = False) -> Any:
        if isinstance(cond, Compare):
            f = ent.field(cond.field)
            assert f is not None
            if cond.other_field is not None:
                raise unavailable("field-to-field comparison")
            if isinstance(cond.value, Today):
                raise TelaError(ErrorCode.SPEC_INVALID, "Today was not fixed for this run.")
            value = (as_of + timedelta(days=cond.value.asof_offset_days)) if isinstance(cond.value, AsOf) \
                else cond.value
            op = OPS[cond.op]
            if f.reads_day:
                return And((day_cmp(f.source_column, NEGATE[op], value), Null(f.source_column, False))) if neg \
                    else day_cmp(f.source_column, op, value)
            if neg:
                guarded = _choice(cols, f.source_column, NEGATE[op], [value]) if cols.base(f.source_column)[1] \
                    is not None else cmp(f.source_column, NEGATE[op], value)
                return And((guarded, Null(cols.base(f.source_column)[0], False)))
            if cols.base(f.source_column)[1] is not None:
                return _choice(cols, f.source_column, op, [value])
            return cmp(f.source_column, op, value)
        if isinstance(cond, InSet):
            f = ent.field(cond.field)
            assert f is not None
            positive = (cond.op == "in") != neg
            if f.reads_day:
                days = tuple(day_cmp(f.source_column, "eq" if positive else "ne", v) for v in cond.values)
                return Or(days) if positive else And((*days, Null(f.source_column, False)))
            return _choice(cols, f.source_column, "in" if positive else "not_in", list(cond.values))
        if isinstance(cond, NullCheck):
            f = ent.field(cond.field)
            assert f is not None
            return Null(cols.base(f.source_column)[0], (cond.op == "is_null") != neg)
        if isinstance(cond, All):
            items = tuple(self._nnf(c, ent, cols, as_of, neg) for c in cond.all)
            return Or(items) if neg else And(items)
        if isinstance(cond, Any_):
            items = tuple(self._nnf(c, ent, cols, as_of, neg) for c in cond.any)
            return And(items) if neg else Or(items)
        if isinstance(cond, Not):
            return self._nnf(cond.not_, ent, cols, as_of, not neg)
        raise unavailable("condition")

    def _filter(self, fref: FieldRef, flt: Filter, cols: Columns) -> Any:
        col = fref.field.source_column
        values: list[Any] = list(flt.values)
        op: str = flt.op
        if fref.value_labels and op in ("=", "!=", "in", "not_in"):
            labels = dict(fref.value_labels)
            values = list(dict.fromkeys([*values, *(raw for raw, lbl in labels.items() if lbl in map(str, values))]))
            op = {"=": "in", "!=": "not_in"}.get(op, op)
        if fref.field.data_type in ("date", "datetime"):
            values = [date.fromisoformat(str(v)[:10]) for v in values]
        if fref.field.reads_day and op not in ("is_null", "not_null"):
            if op == "between":
                return And((day_cmp(col, "ge", values[0]), day_cmp(col, "le", values[1])))
            if op in ("in", "not_in"):
                days = tuple(day_cmp(col, "eq" if op == "in" else "ne", v) for v in values)
                return Or(days) if op == "in" else And((*days, Null(col, False)))
            return And((day_cmp(col, "ne", values[0]), Null(col, False))) if op == "!=" \
                else day_cmp(col, OPS[op], values[0])
        if op in ("in", "not_in", "=", "!="):
            return _choice(cols, col, {"=": "eq", "!=": "ne"}.get(op, op), values)
        if op == "between":
            return And((cmp(col, "ge", values[0]), cmp(col, "le", values[1])))
        if op in ("is_null", "not_null"):
            return Null(cols.base(col)[0], op == "is_null")
        if cols.base(col)[1] is not None:
            raise unavailable("ordering comparison on a choice label")
        return cmp(col, OPS[op], values[0])

    def _where(self, vs: ValidatedSpec, cols: Columns, as_of: date) -> list[Any]:
        conds = [self._filter(fref, flt, cols) for fref, flt in vs.filters]
        if vs.time is not None:
            fref, tspec = vs.time
            if tspec.range is not None:
                start, end = resolve_range(tspec.range, as_of)
                col = fref.field.source_column
                if start is not None:
                    conds.append(cmp(col, "ge", start))
                if end is not None:
                    moment = fref.field.data_type == "datetime" or fref.field.reads_day
                    conds.append(cmp(col, "lt", end + timedelta(days=1)) if moment else cmp(col, "le", end))
        return conds

    def _check(self, vs: ValidatedSpec) -> None:
        if vs.entities - {vs.root.key}:
            raise unavailable("link to another table")
        if len(vs.measures) != 1:
            raise unavailable("several measures")
        if vs.extras:  # US-176: a share's total needs every group; Web API mode doesn't approximate it
            raise unavailable("share of total")
        m = vs.measures[0]
        if not isinstance(m.expression, AggExpr):
            raise unavailable("arithmetic measure")
        if m.currency_field:
            raise unavailable("currency")
        for b in vs.breakdowns:
            if b.grain or b.personal or b.field.value_labels:
                raise unavailable("grain, grouping or label breakdown")

    # ------------------------------------------------------------------ aggregate queries

    def compile(self, vs: ValidatedSpec, *, as_of: date) -> WebApiCompiled:
        require_basic(vs, "dataverse_webapi")
        self._check(vs)
        root, cols = vs.root, self._cols(vs.root)
        m = vs.measures[0]
        agg: AggExpr = m.expression
        conds = self._where(vs, cols, as_of)
        filters = list(conds)
        if agg.where is not None:
            conds.append(self._nnf(agg.where, root, cols, as_of))
        prop = None
        if agg.field is not None:
            f = root.field(agg.field)
            assert f is not None
            prop = cols.base(f.source_column)[0]
            if agg.agg == "count":
                conds.append(Null(prop, False))
        fn = {"count": "count", "count_distinct": "countdistinct", "sum": "sum", "avg": "average", "min": "min",
              "max": "max"}[agg.agg]
        groupby, sources, labels = [], {}, {}
        for b in vs.breakdowns:
            base, options = cols.base(b.field.field.source_column)
            groupby.append(base)
            sources[b.output] = base
            if options is not None:
                labels[b.output] = options
        sources[m.key] = "m0"
        sort = self._sort(vs)
        columns = [ColumnMeta(b.output, "dimension", b.field.label, b.ref, b.field.field.data_type, None,
                              dict(b.field.value_labels), b.field.sort_order, b.field.qualified)
                   for b in vs.breakdowns] + [ColumnMeta(m.key, "measure", m.label, f"measure:{m.key}", "decimal",
                                                         m.format)]
        fill = [0 if agg.agg in ("count", "count_distinct") else None]
        compiled = WebApiCompiled(root, "aggregate", columns, conds, cols, self._entity_set(root), self.fmt,
                                  groupby=groupby, aggregates=[(fn, prop if fn != "count" else None, "m0")],
                                  sources=sources, labels=labels, sort=sort, top=vs.spec.limit,
                                  empty_row=fill if not groupby else None,
                                  groups_conds=filters if groupby and len(conds) > len(filters) else None,
                                  group_fill=fill, entities={"t0": root.key})
        if vs.window_ops:
            from tela_api.query.duckdb_post import build_post_sql
            post = build_post_sql(vs, columns)
            if post is not None:
                compiled.duckdb_post = post
        return compiled

    @staticmethod
    def _sort(vs: ValidatedSpec) -> list[tuple[str, bool]]:
        m = vs.measures[0]
        if not vs.spec.sort:
            return [(m.key, True)] if vs.breakdowns else []
        out = []
        for s in vs.spec.sort:
            if s.by == "measure" or s.by.startswith("measure:"):
                out.append((m.key, s.dir == "desc"))
            else:
                b = next(b for b in vs.breakdowns if b.ref == s.by)
                if b.field.sort_order:
                    raise unavailable("custom sort order")
                out.append((b.output, s.dir == "desc"))
        return out

    # ------------------------------------------------------------------ records and helpers

    def compile_records(self, vs: ValidatedSpec, *, marks: dict[str, Any], as_of: date, fields: list[str],
                        limit: int, offset: int, count_only: bool = False) -> WebApiCompiled:
        require_basic(vs, "dataverse_webapi")
        if vs.entities - {vs.root.key}:
            raise unavailable("link to another table")
        m = vs.measures[0]
        if not isinstance(m.expression, AggExpr):
            raise TelaError(ErrorCode.SPEC_INVALID, "This figure combines several measures and can't be listed "
                            "as records.")
        root, cols = vs.root, self._cols(vs.root)
        conds = self._where(vs, cols, as_of)
        if m.expression.where is not None:
            conds.append(self._nnf(m.expression.where, root, cols, as_of))
        if m.expression.field is not None and m.expression.agg != "count":
            f = root.field(m.expression.field)
            assert f is not None
            conds.append(Null(cols.base(f.source_column)[0], False))
        by_output = {b.output: b for b in vs.breakdowns}
        for output, value in marks.items():
            b = by_output.get(output)
            if b is None:
                raise TelaError(ErrorCode.SPEC_INVALID, "That mark is not part of this widget.")
            if b.personal is not None:
                raise TelaError(ErrorCode.SPEC_INVALID, "Records can't be listed for a personal grouping yet.")
            col = b.field.field.source_column
            if value is None:
                conds.append(Null(cols.base(col)[0], True))
            elif b.grain:
                start = date.fromisoformat(str(value)[:10])
                conds += [cmp(col, "ge", start), cmp(col, "lt", _bucket_end(start, b.grain))]
            else:
                raws = [raw for raw, lbl in b.field.value_labels if lbl == str(value)] + [value]
                conds.append(_choice(cols, col, "in", list(dict.fromkeys(raws))))
        if count_only:
            meta = [ColumnMeta("n", "measure", "Records", "records", "integer")]
            return WebApiCompiled(root, "count", meta, conds, cols, self._entity_set(root), self.fmt,
                                  aggregates=[("count", None, "n")], sources={"n": "n"}, empty_row=[0],
                                  fixed_limit=1, entities={"t0": root.key})
        select, sources, labels, columns = [], {}, {}, []
        for fk in fields:
            f = root.field(fk)
            assert f is not None
            base, options = cols.base(f.source_column)
            select.append(base)
            sources[fk] = base
            if options is not None:
                labels[fk] = options
            columns.append(ColumnMeta(fk, "dimension", f.label, f"field:{root.key}.{fk}", f.data_type,
                                      field=f"{root.key}.{fk}"))
        return WebApiCompiled(root, "records", columns, conds, cols, self._entity_set(root), self.fmt,
                              select=list(dict.fromkeys(select)), sources=sources, labels=labels, skip=offset,
                              top=limit, fixed_limit=limit, entities={"t0": root.key})

    def compile_field_query(self, snapshot: ModelSnapshot, fref: FieldRef, kind: str, *,
                            limit: int | None = None) -> WebApiCompiled:
        ent = snapshot.entity(fref.entity)
        assert ent is not None
        cols = self._cols(ent)
        base, options = cols.base(fref.field.source_column)
        meta = [ColumnMeta("value", "dimension", fref.label, f"field:{fref.qualified}", fref.field.data_type,
                           field=fref.qualified)]
        common: dict[str, Any] = {
            "entities": {"t0": ent.key}, "sources": {"value": base if kind == "distinct" else "value"},
            "labels": {"value": options} if options is not None and kind == "distinct" else {}}
        if kind == "distinct":
            return WebApiCompiled(ent, "distinct", meta, [Null(base, False)], cols, self._entity_set(ent), self.fmt,
                                  groupby=[base], sort=[("value", False)], top=limit, **common)
        if kind in ("max", "min"):
            return WebApiCompiled(ent, kind, meta, [], cols, self._entity_set(ent), self.fmt,
                                  aggregates=[(kind, base, "value")], empty_row=[None], **common)
        if kind == "top":  # the field profile's most frequent values (US-175)
            return WebApiCompiled(ent, "top", [*meta, ColumnMeta("n", "measure", "Rows", "rows", "integer")],
                                  [Null(base, False)], cols, self._entity_set(ent), self.fmt, groupby=[base],
                                  aggregates=[("count", None, "n")], sort=[("n", True), ("value", False)], top=limit,
                                  fixed_limit=limit, **{**common, "sources": {"value": base, "n": "n"},
                                                        "labels": {"value": options} if options is not None else {}})
        if kind == "count":
            return WebApiCompiled(ent, "count", meta, [], cols, self._entity_set(ent), self.fmt,
                                  aggregates=[("count", None, "value")], empty_row=[0], **common)
        if kind == "filled":  # rows with a value: the field profile AG-03 reads (US-175)
            return WebApiCompiled(ent, "count", meta, [Null(base, False)], cols, self._entity_set(ent), self.fmt,
                                  aggregates=[("count", None, "value")], empty_row=[0], **common)
        raise ValueError(kind)


# ----------------------------------------------------------------------------- independent check


def assert_webapi_scoped(statement: str, snapshot: ModelSnapshot, scope: ResolvedScope,
                         columns: dict[str, dict[str, dict[str, Any]]]) -> None:
    """Re-render the expected scope and check it leads every query in the statement (RULE-004)."""
    try:
        st = json.loads(statement)
    except ValueError as exc:
        raise _fail("reparse_failed") from exc
    ent = next((e for e in snapshot.entities if e.source_object.lower() == str(st.get("entity") or "").lower()),
               None)
    if ent is None:
        raise _fail("unscoped_reference", object=st.get("entity"))
    cols = Columns(columns.get(ent.source_object.lower(), {}))
    expected = scope_ir(ent, scope, cols)
    for q in (st["query"], st.get("groups_query")):
        if q is None:
            continue
        if "fetchXml" in q:
            head = f"<entity name={quoteattr(ent.source_object.rpartition('.')[2])}>"
            if head + "<filter type=\"and\">" + fetch(expected, cols) not in q["fetchXml"]:
                raise _fail("scope_predicate_mismatch", object=ent.source_object)
        elif "$apply" in q:
            if not q["$apply"].startswith(f"filter({odata(expected, cols)})"):
                raise _fail("scope_predicate_mismatch", object=ent.source_object)
        elif not q.get("$filter", "").startswith(f"({odata(expected, cols)})"):
            raise _fail("scope_predicate_mismatch", object=ent.source_object)
