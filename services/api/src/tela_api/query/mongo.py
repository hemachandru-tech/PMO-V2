"""Spec → MongoDB aggregation pipeline (US-112; ADR-014; RISK-005; spike US-059).

``MongoCompiler`` produces the same answers as ``SqlCompiler`` for the same validated spec:

* **Scope first.** The pipeline starts with a scope marker for the root entity that ``render`` expands to the
  scope ``$match`` and a ``$project`` of the visible fields (hidden fields never leave the first stages). An
  approved link compiles to ``$lookup`` whose nested pipeline starts with the linked entity's scope marker.
* **Grain (RULE-014).** A link that fans out is ``$unwind``-ed and then regrouped on the root key (one row
  per root and breakdown values, like the SQL ``tela_base``) before measures aggregate.
* **SQL semantics.** Missing fields count as NULL; conditions use three-valued logic (``x != 'A'`` and
  ``NOT (...)`` are unknown for NULL, as in SQL); ``SUM`` of no values is NULL; a figure over no rows is one
  row (count 0); NULLs sort last ascending and first descending, as PostgreSQL does.
* **Values are data.** Every value in an expression is wrapped in ``$literal``, so a filter value such as
  ``"$secret"`` is never read as a field path. Dates are BSON dates (midnight UTC for a ``date`` field).

The statement sent to the runner is Extended JSON: ``{"collection", "pipeline", "columns", "empty_row"}``.
``assert_mongo_scoped`` re-parses it and checks, without trusting the compiler, that every governed
collection is read through its expected scope ``$match`` and visible-field ``$project``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import Any

from tela_api.identity.scope import ResolvedCondition, ResolvedScope
from tela_api.query.compiler import (
    ColumnMeta,
    Params,
    _bucket_end,
    extra_columns,
    extra_value,
    require_basic,
    resolve_range,
)
from tela_api.query.scope_injector import _fail, visible_columns
from tela_api.query.validator import Extra, FieldRef, ResolvedBreakdown, ValidatedSpec
from tela_common.errors import ErrorCode, TelaError
from tela_query_spec.expr import (
    AggExpr,
    All,
    Any_,
    Arith,
    AsOf,
    Compare,
    InSet,
    MeasureExpr,
    Not,
    NullCheck,
    Today,
    measure_aggs,
)
from tela_query_spec.model import ModelEntity, ModelSnapshot, step_fans_out
from tela_query_spec.spec import Filter, PersonalField

SCOPE = "__tela_scope__"
CHILD = "#"  # ``db.collection#array`` is a child entity: one row per element of ``array``
MSG_UNSUPPORTED = "This question can't be answered from this MongoDB source yet."


def split_object(source_object: str) -> tuple[str, str, str | None]:
    """``db.collection[#array]`` → (database, collection, array path or None). Database names have no dots."""
    head, _, array = source_object.partition(CHILD)
    database, _, collection = head.partition(".")
    return database, collection, array or None


def bson_value(v: Any, data_type: str | None = None) -> Any:
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=UTC)
    if isinstance(v, date):
        return datetime.combine(v, time(), tzinfo=UTC)
    if isinstance(v, str) and data_type in ("date", "datetime"):
        return datetime.combine(date.fromisoformat(v[:10]), time(), tzinfo=UTC)
    if isinstance(v, Decimal):
        from bson.decimal128 import Decimal128

        return Decimal128(v)
    return v


def lit(v: Any, data_type: str | None = None) -> dict[str, Any]:
    return {"$literal": bson_value(v, data_type)}


def _div(a: Any, b: Any) -> dict[str, Any]:
    """a / b, blank when b is zero or blank or a is blank (as SQL's NULLIF)."""
    blank = {"$or": [{"$eq": [{"$ifNull": [b, 0]}, 0]}, {"$eq": [{"$ifNull": [a, None]}, None]}]}
    return {"$cond": [blank, None, {"$divide": [a, b]}]}


def _extra(e: Extra) -> dict[str, Any]:
    """A derived figure or share (US-176) over the grouped document's measure fields."""
    a = f"${e.of}"
    if e.op == "share":
        return {"$multiply": [_div(a, f"$__t_{e.of}"), 100]}
    b = f"${e.to}"
    if e.op == "ratio":
        return _div(a, b)
    if e.op == "difference":
        return {"$subtract": [a, b]}
    if e.op == "percent_of":
        return {"$multiply": [_div(a, b), 100]}
    return {"$multiply": [_div({"$subtract": [a, b]}, b), 100]}


def scope_query(entity: ModelEntity, scope: ResolvedScope) -> dict[str, Any]:
    """The scope predicate of one entity in MongoDB query language (RULE-004)."""
    es = scope.for_entity(entity.key)  # raises scope_unresolved when no policy covers the entity
    if es.via is not None:  # scope through a parent link needs a semi-join this engine path doesn't build (US-155)
        raise _fail("scope_via_link_unsupported", entity=entity.key)
    if es.all_rows:
        return {}
    if not es.alternatives:
        raise _fail("empty_scope", entity=entity.key)

    def cond(c: ResolvedCondition) -> dict[str, Any]:
        f = entity.field(c.field)
        if f is None:
            raise _fail("scope_field_not_in_model", entity=entity.key, field=c.field)
        vals = [bson_value(v, f.data_type) for v in c.values]
        if c.op == "is null":
            return {f.source_column: None}
        if c.op == "=":
            return {f.source_column: {"$eq": vals[0]}}
        if c.op == "in":
            return {f.source_column: {"$in": vals}}
        if c.op == "not in":
            return {f.source_column: {"$nin": [*vals, None]}}
        raise _fail("scope_operator", op=c.op)

    groups = [{"$and": [cond(c) for c in alt]} for alt in es.alternatives]
    return groups[0] if len(groups) == 1 else {"$or": groups}


def scope_stages(entity: ModelEntity, snapshot: ModelSnapshot, scope: ResolvedScope) -> list[dict[str, Any]]:
    # An inclusion projection keeps the document _id (an identifier, needed to regroup and to unwind arrays).
    project = dict.fromkeys(visible_columns(entity, snapshot, scope.hidden_fields), 1)
    return [{"$match": scope_query(entity, scope)}, {"$project": project}]


@dataclass
class MongoCompiled:
    """Shape-compatible with ``CompiledQuery`` for the shared execution path; ``render`` builds the statement."""

    root: ModelEntity
    stages: list[dict[str, Any]]
    columns: list[ColumnMeta]
    entities: dict[str, str]
    fan_out: bool
    used_fields: dict[str, set[str]]
    empty_row: list[Any] | None = None  # a figure over no rows still returns one row (as SQL does)
    fixed_limit: int | None = None  # records pages carry their own $skip/$limit
    dialect: str = "mongodb"
    params: Params = field(default_factory=Params)
    tree: None = None
    # DuckDB post-step for window operations (US-177): (sql, extra_columns) or None
    duckdb_post: tuple[str, list[ColumnMeta]] | None = None

    def render(self, snapshot: ModelSnapshot, scope: ResolvedScope, limit: int | None) -> str:
        from bson import json_util

        def expand(stages: list[dict[str, Any]]) -> list[dict[str, Any]]:
            out: list[dict[str, Any]] = []
            for st in stages:
                if SCOPE in st:
                    ent = snapshot.entity(st[SCOPE])
                    assert ent is not None
                    out += scope_stages(ent, snapshot, scope)
                    continue
                if "$lookup" in st and "pipeline" in st["$lookup"]:
                    st = {"$lookup": {**st["$lookup"], "pipeline": expand(st["$lookup"]["pipeline"])}}
                out.append(st)
            return out

        pipeline = expand(self.stages)
        if limit is not None and self.fixed_limit is None:
            pipeline.append({"$limit": max(int(limit), 1)})
        database, collection, _ = split_object(self.root.source_object)
        statement = {"collection": f"{database}.{collection}", "pipeline": pipeline,
                     "columns": [c.name for c in self.columns], "empty_row": self.empty_row}
        return str(json_util.dumps(statement, json_options=json_util.CANONICAL_JSON_OPTIONS))


# ---------------------------------------------------------------------------- the compiler


class MongoCompiler:
    def __init__(self) -> None:
        self.dialect = "mongodb"

    # ------------------------------------------------------------------ paths and joins

    def _plan(self, vs: ValidatedSpec) -> tuple[dict[str, str], list[dict[str, Any]], bool]:
        """(entity → path prefix, $lookup/$unwind stages, fans out?)."""
        snap = vs.snapshot
        root = vs.root.key
        prefixes = {root: ""}
        stages: list[dict[str, Any]] = []
        fan_out = False
        for target in sorted(vs.entities - {root}):
            path = snap.join_path(root, target)
            if path is None:
                raise TelaError(ErrorCode.SPEC_INVALID, "No approved link to that data.")
            here = root
            for link, forward in path:
                there = link.to_entity if forward else link.from_entity
                fan_out = fan_out or step_fans_out(link, forward)
                if there in prefixes:
                    here = there
                    continue
                here_ent, there_ent = snap.entity(here), snap.entity(there)
                assert here_ent is not None and there_ent is not None
                if split_object(there_ent.source_object)[2]:
                    raise TelaError(ErrorCode.SPEC_INVALID, MSG_UNSUPPORTED,
                                    {"admin_detail": {"reason": "link to an array entity"}})
                if split_object(there_ent.source_object)[0] != split_object(vs.root.source_object)[0]:
                    raise TelaError(ErrorCode.SPEC_INVALID, MSG_UNSUPPORTED,
                                    {"admin_detail": {"reason": "link across databases"}})
                alias = f"__j{len(prefixes)}"
                prefixes[there] = alias + "."
                here_fields = link.from_fields if forward else link.to_fields
                there_fields = link.to_fields if forward else link.from_fields
                pairs = [(prefixes[here] + self._src(here_ent, hf), self._src(there_ent, tf))
                         for hf, tf in zip(here_fields, there_fields, strict=True)]
                coll = split_object(there_ent.source_object)[1]
                lookup: dict[str, Any]
                if len(pairs) == 1:
                    lookup = {"from": coll, "localField": pairs[0][0], "foreignField": pairs[0][1],
                              "pipeline": [{SCOPE: there}], "as": alias}
                else:
                    let = {f"k{i}": f"${local}" for i, (local, _) in enumerate(pairs)}
                    match = {"$expr": {"$and": [{"$eq": [f"${foreign}", f"$$k{i}"]}
                                                for i, (_, foreign) in enumerate(pairs)]}}
                    lookup = {"from": coll, "let": let, "pipeline": [{SCOPE: there}, {"$match": match}], "as": alias}
                stages += [{"$lookup": lookup}, {"$unwind": {"path": f"${alias}", "preserveNullAndEmptyArrays": True}}]
                here = there
        return prefixes, stages, fan_out

    @staticmethod
    def _src(ent: ModelEntity, field_key: str) -> str:
        f = ent.field(field_key)
        if f is None:
            raise TelaError(ErrorCode.SPEC_INVALID, "Unknown field.", {"admin_detail": {"field": field_key}})
        return str(f.source_column)

    def _root_prefix(self, vs: ValidatedSpec) -> list[dict[str, Any]]:
        """The scope of the root entity first; an array entity unwinds its parent (scoped, if modelled)."""
        database, collection, array = split_object(vs.root.source_object)
        if array is None:
            return [{SCOPE: vs.root.key}]
        parent = next((e for e in vs.snapshot.entities if e.source_object == f"{database}.{collection}"), None)
        head: list[dict[str, Any]] = [{SCOPE: parent.key}] if parent else [{"$match": {}}]
        return [*head, {"$unwind": {"path": f"${array}", "includeArrayIndex": "_index"}},
                {"$replaceRoot": {"newRoot": {"$mergeObjects": [f"${array}", {"_parent_id": "$_id",
                                                                             "_index": "$_index"}]}}},
                {SCOPE: vs.root.key}]

    # ------------------------------------------------------------------ conditions

    @staticmethod
    def _f(path: str) -> dict[str, Any]:
        return {"$ifNull": [f"${path}", None]}  # a missing field is NULL, as in SQL

    def _x3(self, cond: Any, path: Any, ent: ModelEntity, as_of: date) -> Any:
        """Three-valued SQL logic as an aggregation expression: true, false or null (unknown)."""
        if isinstance(cond, Compare):
            f = ent.field(cond.field)
            assert f is not None
            left = self._f(path(cond.field))
            if cond.other_field is not None:
                right: Any = self._f(path(cond.other_field))
            elif isinstance(cond.value, AsOf):
                right = lit(as_of + timedelta(days=cond.value.asof_offset_days), "date")
            elif isinstance(cond.value, Today):
                raise TelaError(ErrorCode.SPEC_INVALID, "Today was not fixed for this run.")
            else:
                right = lit(cond.value, f.data_type)
            op = {"=": "$eq", "!=": "$ne", "<": "$lt", "<=": "$lte", ">": "$gt", ">=": "$gte"}[cond.op]
            return {"$cond": [{"$or": [{"$eq": [left, None]}, {"$eq": [right, None]}]}, None, {op: [left, right]}]}
        if isinstance(cond, InSet):
            f = ent.field(cond.field)
            assert f is not None
            x = self._f(path(cond.field))
            member: Any = {"$in": [x, lit([bson_value(v, f.data_type) for v in cond.values])]}
            if cond.op == "not_in":
                member = {"$not": [member]}
            return {"$cond": [{"$eq": [x, None]}, None, member]}
        if isinstance(cond, NullCheck):
            check = {"$eq": [self._f(path(cond.field)), None]}
            return check if cond.op == "is_null" else {"$not": [check]}
        if isinstance(cond, All):
            parts = [self._x3(c, path, ent, as_of) for c in cond.all]
            return {"$switch": {"branches": [{"case": {"$in": [False, parts]}, "then": False},
                                             {"case": {"$in": [None, parts]}, "then": None}], "default": True}}
        if isinstance(cond, Any_):
            parts = [self._x3(c, path, ent, as_of) for c in cond.any]
            return {"$switch": {"branches": [{"case": {"$in": [True, parts]}, "then": True},
                                             {"case": {"$in": [None, parts]}, "then": None}], "default": False}}
        if isinstance(cond, Not):
            inner = self._x3(cond.not_, path, ent, as_of)
            return {"$cond": [{"$eq": [inner, None]}, None, {"$not": [inner]}]}
        raise TelaError(ErrorCode.SPEC_INVALID, "Unsupported condition.")

    def _is_true(self, cond: Any, path: Any, ent: ModelEntity, as_of: date) -> dict[str, Any]:
        return {"$eq": [self._x3(cond, path, ent, as_of), True]}

    def _filter(self, path: str, fref: FieldRef, flt: Filter) -> dict[str, Any]:
        """A filter in query language with SQL's NULL behaviour (``!=`` and ``not in`` never match NULL)."""
        values: list[Any] = list(flt.values)
        op: str = flt.op
        if fref.value_labels and op in ("=", "!=", "in", "not_in"):
            labels = dict(fref.value_labels)
            values = list(dict.fromkeys([*values, *(raw for raw, lbl in labels.items() if lbl in map(str, values))]))
            op = {"=": "in", "!=": "not_in"}.get(op, op)
        dt = fref.field.data_type
        vals = [bson_value(date.fromisoformat(str(v)[:10]) if dt in ("date", "datetime") else v, dt) for v in values]
        if op == "in":
            return {path: {"$in": vals}}
        if op == "not_in":
            return {path: {"$nin": [*vals, None]}}
        if op == "between":
            return {path: {"$gte": vals[0], "$lte": vals[1]}}
        if op == "is_null":
            return {path: None}
        if op == "not_null":
            return {path: {"$ne": None}}
        if op == "!=":
            return {path: {"$nin": [vals[0], None]}}
        mop = {"=": "$eq", "<": "$lt", "<=": "$lte", ">": "$gt", ">=": "$gte"}[op]
        return {path: {mop: vals[0]}}

    def _where(self, vs: ValidatedSpec, path_of: Any, as_of: date) -> list[dict[str, Any]]:
        conds = [self._filter(path_of(fref.entity, fref.field.key), fref, flt) for fref, flt in vs.filters]
        if vs.time is not None:
            fref, tspec = vs.time
            if tspec.range is not None:
                start, end = resolve_range(tspec.range, as_of)
                p = path_of(fref.entity, fref.field.key)
                bounds: dict[str, Any] = {}
                if start is not None:
                    bounds["$gte"] = bson_value(start)
                if end is not None:
                    if fref.field.data_type == "datetime":
                        bounds["$lt"] = bson_value(end + timedelta(days=1))
                    else:
                        bounds["$lte"] = bson_value(end)
                conds.append({p: bounds})
        return [{"$match": {"$and": conds}}] if conds else []

    # ------------------------------------------------------------------ breakdowns

    def _breakdown(self, b: ResolvedBreakdown, path: str) -> Any:
        x = self._f(path)
        if b.grain:
            return {"$dateTrunc": {"date": x, "unit": b.grain, "startOfWeek": "monday"}}
        if b.personal is not None:
            return self._personal(b.personal, x, b.field)
        if b.field.value_labels:
            dt = b.field.field.data_type
            return {"$switch": {"branches": [{"case": {"$eq": [x, lit(raw, dt)]}, "then": lit(lbl)}
                                             for raw, lbl in b.field.value_labels], "default": x}}
        return x

    @staticmethod
    def _personal(pf: PersonalField, x: Any, fref: FieldRef) -> Any:
        if pf.kind == "grouping":
            text = x if fref.field.data_type == "text" else {"$toString": x}
            trimmed = {"$trim": {"input": text}}
            norm = trimmed if pf.case_sensitive else {"$toLower": trimmed}
            branches = [{"case": {"$or": [{"$eq": [x, None]}, {"$eq": [trimmed, ""]}]}, "then": lit(pf.not_set)}]
            for g in pf.groups:
                vals = [str(v) if pf.case_sensitive else str(v).strip().lower() for v in g.values]
                branches.append({"case": {"$in": [norm, lit(vals)]}, "then": lit(g.label)})
            return {"$switch": {"branches": branches, "default": lit(pf.catch_all)}}
        branches = [{"case": {"$eq": [x, None]}, "then": lit("Not set")},
                    {"case": {"$lt": [x, lit(pf.bands[0].lower)]}, "then": lit(f"Below {pf.bands[0].lower:g}")}]
        for i, band in enumerate(pf.bands):
            last = i == len(pf.bands) - 1
            cond: list[Any] = [{"$gte": [x, lit(band.lower)]}]
            if band.upper is not None:
                closing = last and pf.closing_max is not None and pf.closing_max == band.upper
                cond.append({"$lte" if closing else "$lt": [x, lit(band.upper)]})
            branches.append({"case": {"$and": cond}, "then": lit(band.label)})
        last_upper = pf.bands[-1].upper
        return {"$switch": {"branches": branches,
                            "default": lit(f"Above {last_upper:g}" if last_upper is not None else "Not set")}}

    # ------------------------------------------------------------------ measures

    def _accumulators(self, leaves: list[tuple[str, AggExpr, Any, Any]]) -> dict[str, Any]:
        """``leaves`` = (name, agg, value expression or None, condition expression or None)."""
        acc: dict[str, Any] = {}
        for name, agg, value, cond in leaves:
            nonnull = {"$ne": [value, None]} if value is not None else None
            gate = [c for c in (cond, nonnull) if c is not None]
            if agg.agg == "count":
                acc[name] = {"$sum": {"$cond": [{"$and": gate}, 1, 0]}} if gate else {"$sum": 1}
            elif agg.agg == "count_distinct":
                acc[name] = {"$addToSet": {"$cond": [{"$and": gate}, value, "$$REMOVE"]}}
            else:
                v = {"$cond": [cond, value, None]} if cond is not None else value
                op = {"sum": "$sum", "avg": "$avg", "min": "$min", "max": "$max"}[agg.agg]
                acc[name] = {op: v}
                if agg.agg == "sum":  # SUM of no values is NULL in SQL, 0 in MongoDB
                    acc[f"{name}_n"] = {"$sum": {"$cond": [{"$and": gate}, 1, 0]}}
        return acc

    @staticmethod
    def _leaf_out(name: str, agg: AggExpr) -> Any:
        if agg.agg == "count_distinct":
            return {"$size": f"${name}"}
        if agg.agg == "sum":
            return {"$cond": [{"$eq": [f"${name}_n", 0]}, None, f"${name}"]}
        return f"${name}"

    def _measure(self, expr: MeasureExpr | float, leaf: Any) -> Any:
        if isinstance(expr, AggExpr):
            return leaf(expr)
        if not isinstance(expr, Arith):
            return expr
        left, right = self._measure(expr.left, leaf), self._measure(expr.right, leaf)
        if expr.op == "div":
            out: Any = {"$cond": [{"$or": [{"$eq": [right, None]}, {"$eq": [right, 0]}, {"$eq": [left, None]}]},
                                  None, {"$divide": [left, right]}]}
        else:
            out = {{"add": "$add", "sub": "$subtract", "mul": "$multiply"}[expr.op]: [left, right]}
        if expr.scale is not None:
            out = {"$multiply": [out, expr.scale]}
        return out

    # ------------------------------------------------------------------ aggregate queries

    def compile(self, vs: ValidatedSpec, *, as_of: date) -> MongoCompiled:
        require_basic(vs, "mongodb")
        snap = vs.snapshot
        root = vs.root.key
        prefixes, joins, fan_out = self._plan(vs)
        used: dict[str, set[str]] = {}

        def path_of(entity: str, field_key: str) -> str:
            ent = snap.entity(entity)
            assert ent is not None
            src = self._src(ent, field_key)
            used.setdefault(entity, set()).add(src)
            return prefixes[entity] + src

        for m in vs.measures[:1]:
            if m.currency_field:
                raise TelaError(ErrorCode.SPEC_INVALID, MSG_UNSUPPORTED, {"admin_detail": {"reason": "currency"}})
        breakdowns = list(vs.breakdowns)
        stages: list[dict[str, Any]] = [*self._root_prefix(vs), *joins, *self._where(vs, path_of, as_of)]
        group_id = {f"b{i}": self._breakdown(b, path_of(b.field.entity, b.field.field.key))
                    for i, b in enumerate(breakdowns)} or None
        def rpath(k: str) -> str:
            return path_of(root, k)

        leaves_by_measure: list[list[tuple[str, AggExpr]]] = []
        if fan_out:
            pk = {f"k{i}": self._f(rpath(k)) for i, k in enumerate(vs.root.primary_key)}
            first: dict[str, Any] = {}
            plans: list[tuple[str, AggExpr, Any, Any]] = []
            for mi, m in enumerate(vs.measures):
                names = []
                for ai, agg in enumerate(measure_aggs(m.expression)):
                    name = f"__a{mi}_{ai}"
                    names.append((name, agg))
                    value: Any = None
                    cond: Any = None
                    if agg.field:
                        first[f"{name}_v"] = {"$first": self._f(rpath(agg.field))}
                        value = f"${name}_v"
                    if agg.where is not None:
                        first[f"{name}_w"] = {"$first": self._is_true(agg.where, rpath, vs.root, as_of)}
                        cond = {"$eq": [f"${name}_w", True]}
                    plans.append((name, agg, value, cond))
                leaves_by_measure.append(names)
            stages.append({"$group": {"_id": {**pk, **(group_id or {})}, **first}})
            outer_id = {k: f"$_id.{k}" for k in (group_id or {})} or None
            stages.append({"$group": {"_id": outer_id, **self._accumulators(plans)}})
        else:
            plans = []
            for mi, m in enumerate(vs.measures):
                names = []
                for ai, agg in enumerate(measure_aggs(m.expression)):
                    name = f"__a{mi}_{ai}"
                    names.append((name, agg))
                    value = self._f(rpath(agg.field)) if agg.field else None
                    cond = self._is_true(agg.where, rpath, vs.root, as_of) if agg.where is not None else None
                    plans.append((name, agg, value, cond))
                leaves_by_measure.append(names)
            stages.append({"$group": {"_id": group_id, **self._accumulators(plans)}})
        project: dict[str, Any] = {"_id": 0}
        for i, b in enumerate(breakdowns):
            project[b.output] = f"$_id.b{i}"
        for mi, m in enumerate(vs.measures):
            it = iter(leaves_by_measure[mi])
            project[m.key] = self._measure(m.expression, lambda a, it=it: self._leaf_out(*next(it)))
        stages.append({"$project": project})
        stages += self._extras(vs)
        stages += self._order(vs, breakdowns)
        columns = [self._column_meta(b) for b in breakdowns] + [
            ColumnMeta(m.key, "measure", m.label, f"measure:{m.key}", "decimal", m.format) for m in vs.measures
        ] + extra_columns(vs)
        empty = None
        if not breakdowns:
            empty = [0 if isinstance(m.expression, AggExpr) and m.expression.agg in ("count", "count_distinct")
                     else None for m in vs.measures]
            empty += [extra_value(e, dict(zip((m.key for m in vs.measures), empty, strict=True)))
                      for e in vs.extras]
        entities = {(prefixes[k].rstrip(".") or "t0"): k for k in prefixes}
        compiled = MongoCompiled(vs.root, stages, columns, entities, fan_out, used, empty)
        if vs.window_ops:
            from tela_api.query.duckdb_post import build_post_sql
            post = build_post_sql(vs, columns)
            if post is not None:
                compiled.duckdb_post = post
        return compiled

    @staticmethod
    def _extras(vs: ValidatedSpec) -> list[dict[str, Any]]:
        """Derived figures and shares of the total (US-176) over the grouped documents. A share's total is summed
        over every group by pushing them into one document and unwinding it again (no ``$setWindowFields``)."""
        if not vs.extras:
            return []
        stages: list[dict[str, Any]] = []
        shares = [e for e in vs.extras if e.op == "share"]
        if shares:
            totals = {f"__t_{e.of}": {"$sum": f"${e.of}"} for e in shares}
            stages += [{"$group": {"_id": None, "__rows": {"$push": "$$ROOT"}, **totals}},
                       {"$unwind": "$__rows"},
                       {"$replaceRoot": {"newRoot": {"$mergeObjects": [
                           "$__rows", {f"__t_{e.of}": f"$__t_{e.of}" for e in shares}]}}}]
        add = {e.key: _extra(e) for e in vs.extras}
        stages.append({"$addFields": add})
        if shares:
            stages.append({"$project": dict.fromkeys((f"__t_{e.of}" for e in shares), 0)})
        return stages

    def _order(self, vs: ValidatedSpec, breakdowns: list[ResolvedBreakdown]) -> list[dict[str, Any]]:
        keys: list[tuple[Any, bool]] = []  # (expression over output fields, descending)
        if not vs.spec.sort:
            if any(b.grain for b in breakdowns):
                keys.append(("$period", False))
            elif breakdowns:
                keys.append((f"${vs.measures[0].key}", True))
        for s in vs.spec.sort:
            desc = s.dir == "desc"
            if s.by == "measure":
                keys.append((f"${vs.measures[0].key}", desc))
            elif s.by.startswith("measure:"):
                keys.append((f"${s.by.split(':', 1)[1]}", desc))
            elif (extra := next((e for e in vs.extras if e.ref == s.by), None)) is not None:
                keys.append((f"${extra.key}", desc))
            else:
                b = next(b for b in breakdowns if b.ref == s.by)
                if b.field.sort_order and not b.grain and not b.personal:
                    order = {"$switch": {"branches": [{"case": {"$eq": [f"${b.output}", lit(v)]}, "then": i}
                                                      for i, v in enumerate(b.field.sort_order)],
                                         "default": len(b.field.sort_order)}}
                    keys.append((order, desc))
                else:
                    keys.append((f"${b.output}", desc))
        if not keys:
            return []
        add: dict[str, Any] = {}
        sort: dict[str, int] = {}
        for i, (expr, desc) in enumerate(keys):
            add[f"__s{i}"] = expr
            add[f"__n{i}"] = {"$cond": [{"$eq": [{"$ifNull": [expr, None]}, None]}, 1, 0]}
            sort[f"__n{i}"] = -1 if desc else 1  # PostgreSQL: NULLS LAST ascending, NULLS FIRST descending
            sort[f"__s{i}"] = -1 if desc else 1
        drop = dict.fromkeys(add, 0)
        return [{"$addFields": add}, {"$sort": sort}, {"$project": drop}]

    @staticmethod
    def _column_meta(b: ResolvedBreakdown) -> ColumnMeta:
        kind = "period" if b.grain else "dimension"
        label = b.personal.key if b.personal else b.field.label
        return ColumnMeta(b.output, kind, label, b.ref, "date" if b.grain else b.field.field.data_type,
                          None, dict(b.field.value_labels), b.field.sort_order, b.field.qualified)

    # ------------------------------------------------------------------ records (FEAT-044) and helpers

    def compile_records(self, vs: ValidatedSpec, *, marks: dict[str, Any], as_of: date, fields: list[str],
                        limit: int, offset: int, count_only: bool = False) -> MongoCompiled:
        require_basic(vs, "mongodb")
        snap = vs.snapshot
        measure = vs.measures[0]
        if not isinstance(measure.expression, AggExpr):
            raise TelaError(ErrorCode.SPEC_INVALID, "This figure combines several measures and can't be listed "
                            "as records.")
        root = vs.root.key
        prefixes, joins, fan_out = self._plan(vs)

        def path_of(entity: str, field_key: str) -> str:
            ent = snap.entity(entity)
            assert ent is not None
            return prefixes[entity] + self._src(ent, field_key)

        def rpath(k: str) -> str:
            return path_of(root, k)

        stages: list[dict[str, Any]] = [*self._root_prefix(vs), *joins, *self._where(vs, path_of, as_of)]
        conds: list[dict[str, Any]] = []
        if measure.expression.where is not None:
            conds.append({"$expr": self._is_true(measure.expression.where, rpath, vs.root, as_of)})
        if measure.expression.field is not None and measure.expression.agg != "count":
            conds.append({rpath(measure.expression.field): {"$ne": None}})
        by_output = {b.output: b for b in vs.breakdowns}
        for output, value in marks.items():
            b = by_output.get(output)
            if b is None:
                raise TelaError(ErrorCode.SPEC_INVALID, "That mark is not part of this widget.")
            if b.personal is not None:
                raise TelaError(ErrorCode.SPEC_INVALID, "Records can't be listed for a personal grouping yet.")
            p = path_of(b.field.entity, b.field.field.key)
            dt = b.field.field.data_type
            if value is None:
                conds.append({p: None})
            elif b.grain:
                start = date.fromisoformat(str(value)[:10])
                conds.append({p: {"$gte": bson_value(start), "$lt": bson_value(_bucket_end(start, b.grain))}})
            else:
                raws = [raw for raw, lbl in b.field.value_labels if lbl == str(value)] + [value]
                conds.append({p: {"$in": [bson_value(v, dt) for v in dict.fromkeys(raws)]}})
        if conds:
            stages.append({"$match": {"$and": conds}})
        pk = vs.root.primary_key
        if count_only:
            stages += [{"$group": {"_id": {f"k{i}": self._f(rpath(k)) for i, k in enumerate(pk)}}},
                       {"$count": "n"}]
            meta = [ColumnMeta("n", "measure", "Records", "records", "integer")]
            return MongoCompiled(vs.root, stages, meta, {"t0": root}, False, {}, [0], fixed_limit=1)
        values = {fk: self._f(rpath(fk)) for fk in fields}
        if fan_out:
            stages += [{"$group": {"_id": {f"k{i}": self._f(rpath(k)) for i, k in enumerate(pk)},
                                   **{fk: {"$first": v} for fk, v in values.items()}}},
                       {"$project": {"_id": 0, **dict.fromkeys(fields, 1)}}]
        else:
            stages.append({"$project": {"_id": 0, **values}})
        sort = {k: 1 for k in pk if k in fields}
        if sort:
            stages.append({"$sort": sort})
        stages += [{"$skip": max(offset, 0)}, {"$limit": max(limit, 1)}]
        columns = []
        for fk in fields:
            f = vs.root.field(fk)
            assert f is not None
            columns.append(ColumnMeta(fk, "dimension", f.label, f"field:{root}.{fk}", f.data_type,
                                      field=f"{root}.{fk}"))
        return MongoCompiled(vs.root, stages, columns, {"t0": root}, False, {}, None, fixed_limit=limit)


def compile_field_query(snapshot: ModelSnapshot, fref: FieldRef, kind: str, *,
                        limit: int | None = None) -> MongoCompiled:
    """Scoped distinct values (filter options), the newest value (freshness), a row count of one entity, or the
    field profile (US-175: rows with a value, the earliest value, the most frequent values with their counts)."""
    ent = snapshot.entity(fref.entity)
    assert ent is not None
    path = fref.field.source_column
    x = {"$ifNull": [f"${path}", None]}
    compiler = MongoCompiler()
    fake = type("VS", (), {"root": ent, "snapshot": snapshot})()
    stages: list[dict[str, Any]] = compiler._root_prefix(fake)
    empty: list[Any] | None = None
    if kind == "distinct":
        stages += [{"$match": {path: {"$ne": None}}}, {"$group": {"_id": x}},
                   {"$project": {"_id": 0, "value": "$_id"}}, {"$sort": {"value": 1}}]
        if limit is not None:
            stages.append({"$limit": limit})
    elif kind in ("max", "min"):
        stages += [{"$group": {"_id": None, "value": {f"${kind}": x}}}, {"$project": {"_id": 0, "value": 1}}]
        empty = [None]
    elif kind == "count":
        stages += [{"$count": "value"}]
        empty = [0]
    elif kind == "filled":
        stages += [{"$match": {path: {"$ne": None}}}, {"$count": "value"}]
        empty = [0]
    elif kind == "top":
        stages += [{"$match": {path: {"$ne": None}}}, {"$group": {"_id": x, "n": {"$sum": 1}}},
                   {"$project": {"_id": 0, "value": "$_id", "n": 1}}, {"$sort": {"n": -1, "value": 1}}]
        if limit is not None:
            stages.append({"$limit": limit})
    else:
        raise ValueError(kind)
    meta = [ColumnMeta("value", "dimension", fref.label, f"field:{fref.qualified}", fref.field.data_type,
                       field=fref.qualified)]
    if kind == "top":
        meta.append(ColumnMeta("n", "measure", "Rows", "rows", "integer"))
    return MongoCompiled(ent, stages, meta, {"t0": ent.key}, False, {ent.key: {path}}, empty,
                         fixed_limit=limit if kind in ("distinct", "top") else None)


# ---------------------------------------------------------------------------- independent check


def assert_mongo_scoped(statement: str, snapshot: ModelSnapshot, scope: ResolvedScope) -> None:
    """Re-parse the statement and check every governed collection is read through its scope (RULE-004)."""
    from bson import json_util

    try:
        doc = json_util.loads(statement)
    except Exception as exc:
        raise _fail("reparse_failed") from exc
    governed: dict[str, list[ModelEntity]] = {}
    for e in snapshot.entities:
        database, collection, _ = split_object(e.source_object)
        governed.setdefault(f"{database}.{collection}", []).append(e)
    coll = doc.get("collection")
    if coll not in governed:
        raise _fail("unscoped_reference", object=coll)
    database = coll.split(".", 1)[0]

    def expected(ent: ModelEntity) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = json_util.loads(json_util.dumps(
            scope_stages(ent, snapshot, scope), json_options=json_util.CANONICAL_JSON_OPTIONS))
        return out

    def check(pipeline: list[dict[str, Any]], candidates: list[ModelEntity], where: str) -> None:
        if not any(pipeline[:2] == expected(e) for e in candidates):
            raise _fail("scope_predicate_mismatch", object=where)
        for st in pipeline:
            if "$lookup" in st:
                target = f"{database}.{st['$lookup'].get('from')}"
                if target not in governed or "pipeline" not in st["$lookup"]:
                    raise _fail("unscoped_reference", object=target)
                targets = [e for e in governed[target] if not split_object(e.source_object)[2]]
                check(st["$lookup"]["pipeline"], targets, target)
            if any(k in st for k in ("$unionWith", "$graphLookup", "$facet")):
                raise _fail("unscoped_reference", object=where)

    pipeline = doc.get("pipeline") or []
    if not pipeline:
        raise _fail("no_governed_reference")
    roots = [e for e in governed[coll] if not split_object(e.source_object)[2]]
    children = [e for e in governed[coll] if split_object(e.source_object)[2]]
    idx = next((i for i, st in enumerate(pipeline) if "$replaceRoot" in st), None)
    child = next((c for c in children if idx is not None and pipeline[idx + 1: idx + 3] == expected(c)), None)
    if child is not None:
        # An array entity: the parent's scope (or no filter when the parent is not modelled), then its own.
        assert idx is not None
        if roots:
            check(pipeline[:idx], roots, coll)
        elif pipeline[0] != {"$match": {}}:
            raise _fail("scope_predicate_mismatch", object=coll)
        check(pipeline[idx + 1:], [child], child.source_object)
        return
    if children and idx is not None:
        raise _fail("scope_predicate_mismatch", object=coll)  # unwound an array without its scope
    check(pipeline, roots, coll)
