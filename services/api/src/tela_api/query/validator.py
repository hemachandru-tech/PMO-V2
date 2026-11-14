"""Spec validation against the pinned model version (US-018; RULE-003, RULE-006, RULE-008, RULE-014).

Every reference must exist in the model snapshot the spec pins, every entity
it touches must be reachable from the measure's entity through accepted links,
the limit must be within the connection cap, and the spec must read exactly one
connection. Fields hidden for the principal never compile: the result is
flagged ``hidden`` and no query runs (US-020).

Rejections carry a user-readable message; offending names go in
``admin_detail``, which only Admins see.

An ad-hoc rule (US-163, ``measure.adhoc``) is checked here too: only with a
live ``ask.freehand`` grant on the connection (``freehand=True``), over fields
that are in the model, queryable and not hidden for the person (a hidden one
is refused, not flagged), with linked fields reached through accepted links
only, plain comparisons only, and limits on fields, nesting and alternatives.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from dataclasses import field as dc_field
from typing import Any, Literal

from tela_common.errors import ErrorCode, TelaError
from tela_query_spec.expr import (
    AggExpr,
    All,
    Any_,
    AsOf,
    Compare,
    InSet,
    Not,
    NullCheck,
    Today,
    condition_depth,
    condition_fields,
    is_additive,
    measure_fields,
    or_branches,
    split_ref,
)
from tela_query_spec.model import (
    NUMERIC_TYPES,
    TEMPORAL_TYPES,
    Dimension,
    Measure,
    ModelEntity,
    ModelField,
    ModelSnapshot,
)
from tela_query_spec.spec import (
    ADHOC_KEY,
    MAX_ADHOC_BRANCHES,
    MAX_ADHOC_DEPTH,
    MAX_ADHOC_FIELDS,
    PERCENTILE_VALUES,
    AdhocMeasure,
    PersonalField,
    QuerySpec,
    ref_key,
    ref_kind,
    share_key,
)

HIDDEN_MESSAGE = "Hidden for your role"
NO_FREEHAND = ("Free-hand questions on this data need access from your Admin. Ask with a certified measure, or "
               "request free-hand access.")


@dataclass(frozen=True)
class FieldRef:
    """A resolved model field: entity key + field definition."""

    entity: str
    field: ModelField
    label: str
    value_labels: tuple[tuple[str, str], ...] = ()
    is_temporal: bool = False
    is_status: bool = False
    sort_order: tuple[str, ...] | None = None
    # The model dimension behind the reference, when it computes its values or labels blanks (US-156).
    dimension: Dimension | None = dc_field(default=None, compare=False, hash=False)

    @property
    def qualified(self) -> str:
        return f"{self.entity}.{self.field.key}"

    @property
    def computed(self) -> Dimension | None:
        return self.dimension if self.dimension is not None and self.dimension.computed else None

    @property
    def not_set(self) -> str | None:
        return self.dimension.not_set if self.dimension is not None else None


@dataclass(frozen=True)
class ResolvedBreakdown:
    ref: str
    output: str  # result column name
    field: FieldRef
    personal: PersonalField | None = None
    grain: str | None = None


@dataclass(frozen=True)
class ResolvedExistsIn:
    """A validated semi-join filter (US-178): the linked entity and its resolved filter conditions."""
    entity: str
    entity_obj: ModelEntity
    root_fields: list[str]  # fields on root for join
    linked_fields: list[str]  # fields on linked entity for join
    filters: list[tuple[FieldRef, Any]]  # (resolved field, Filter)
    where: Any = None  # the certified count measure's own rule (``measure``), on the linked entity
    measure_label: str | None = None


@dataclass(frozen=True)
class ResolvedCohort:
    """A validated cohort breakdown (US-178): date bucket of a linked entity."""
    ref: str
    field: FieldRef
    grain: str
    agg: str  # min | max
    entity: str
    root_fields: list[str]
    linked_fields: list[str]


@dataclass(frozen=True)
class ResolvedWindowOp:
    """A validated window operation (US-177): the kind, the resolved measure key, optional partition ref,
    and the parameters specific to each operation type."""

    kind: str  # top_n_per_group | running_total | moving_avg | rank | bucket | percentile | having
    measure_key: str  # the measure column this operates on
    partition_ref: str | None = None  # breakdown output name for PARTITION BY (None = whole result)
    params: dict[str, Any] = field(default_factory=dict)  # n, window, k, p, op, value, order, labels


@dataclass(frozen=True)
class Extra:
    """A result column worked out from the measures' columns (US-176): a derived figure (``op`` over ``of`` and
    ``to``) or a measure's share of the total (``op`` = ``share``, ``of`` the measure)."""

    key: str
    op: str  # ratio | difference | percent_of | percent_change | share
    of: str  # measure key
    to: str | None
    label: str
    format: str
    plain_rule: str
    additive: bool = False

    @property
    def ref(self) -> str:
        return f"share:{self.of}" if self.op == "share" else f"derived:{self.key}"


@dataclass
class ValidatedSpec:
    spec: QuerySpec
    snapshot: ModelSnapshot
    root: ModelEntity
    measures: list[Any]  # tela_query_spec.model.Measure, primary first
    breakdowns: list[ResolvedBreakdown]
    filters: list[tuple[FieldRef, Any]]  # (field, tela_query_spec.spec.Filter)
    time: tuple[FieldRef, Any] | None
    entities: set[str]
    hidden: bool = False
    hidden_refs: list[str] = field(default_factory=list)
    limit: int | None = None
    certified: bool = True  # False when the main measure is an ad-hoc rule (US-163)
    today: Any = None  # the date ``today`` was pinned to for this run, when the spec uses it (cache key)
    extras: list[Extra] = field(default_factory=list)  # derived figures, then shares of the total (US-176)
    exists_in: list[ResolvedExistsIn] = field(default_factory=list)  # semi-join filters (US-178)
    cohort: ResolvedCohort | None = None  # cohort breakdown (US-178)
    window_ops: list[ResolvedWindowOp] = field(default_factory=list)  # window operations (US-177)


# Derived figures (US-176): default label and plain rule, from the two measures' labels.
DERIVED_WORDS = {
    "ratio": ("{a} per {b}", "{a} divided by {b}. Blank when {b} is zero or blank."),
    "difference": ("{a} minus {b}", "{a} minus {b}."),
    "percent_of": ("{a} as % of {b}", "{a} as a percentage of {b}. Blank when {b} is zero or blank."),
    "percent_change": ("{a} over {b} (%)", "How far {a} is above (or below) {b}, as a percentage of {b}. Blank when "
                       "{b} is zero or blank."),
}


DERIVED_FORMAT = {"ratio": "decimal", "percent_of": "percent", "percent_change": "percent"}  # difference: the unit


def _reject(message: str, **admin: Any) -> TelaError:
    return TelaError(ErrorCode.SPEC_INVALID, message, {"admin_detail": admin} if admin else {})


class SpecValidator:
    def __init__(
        self,
        snapshot: ModelSnapshot,
        *,
        hidden_fields: Iterable[str] = (),
        row_cap: int = 10_000,
        enum_values: Callable[[FieldRef], list[str] | None] | None = None,
        freehand: bool = False,
    ) -> None:
        self.snapshot = snapshot
        self.hidden = frozenset(hidden_fields)
        self.row_cap = row_cap
        self.enum_values = enum_values
        self.freehand = freehand  # a live ``ask.freehand`` grant on the spec's connection (US-163)

    # ------------------------------------------------------------------ resolution

    def _field(self, ref: str, personal: dict[str, PersonalField]) -> tuple[FieldRef, PersonalField | None]:
        kind, key = ref_kind(ref), ref_key(ref)
        if kind == "dimension":
            dim = self.snapshot.dimension(key)
            if dim is None:
                raise _reject("That breakdown or filter is not in the approved model.", ref=ref)
            ent = self.snapshot.entity(dim.entity)
            assert ent is not None
            f = ent.field(dim.field)
            assert f is not None
            order = dim.sort_order
            if dim.computed:
                # Computed values are labels: a text field anchored on the dimension's field (hiding, catalogue).
                f = f.model_copy(update={"data_type": "text", "label": dim.label})
                order = order or (dim.labels() if dim.bands else None)
            return FieldRef(dim.entity, f, dim.label, tuple(sorted(dim.value_labels.items())), dim.is_temporal,
                            dim.is_status, tuple(order) if order else None,
                            dim if (dim.computed or dim.not_set) else None), None
        if kind == "field":
            ent_key, field_key = key.split(".", 1)
            ent = self.snapshot.entity(ent_key)
            f = ent.field(field_key) if ent else None
            if f is None or not f.is_queryable:
                raise _reject("That field is not in the approved model.", ref=ref)
            return FieldRef(ent_key, f, f.label, is_temporal=f.data_type in ("date", "datetime")), None
        if kind == "personal":
            pf = personal.get(key)
            if pf is None:
                raise _reject("That personal field is not defined in this question.", ref=ref)
            base, _ = self._field(pf.source, {})
            if pf.kind == "band" and base.field.data_type not in ("integer", "decimal"):
                raise _reject("Bands need a numeric field.", ref=ref, source=pf.source)
            return base, pf
        raise _reject("Unsupported reference.", ref=ref)

    def _reachable(self, root: str, entity: str, ref: str) -> None:
        if self.snapshot.join_path(root, entity) is None:
            raise _reject(
                "That breakdown is not linked to this measure in the approved model.",
                ref=ref, root=root, entity=entity,
            )

    # ------------------------------------------------------------------ validate

    def validate(self, spec: QuerySpec, *, connection_id: str | None = None) -> ValidatedSpec:
        snap = self.snapshot
        if spec.model_version_id != snap.model_version_id:
            raise _reject("This question was built on a different model version.",
                          pinned=spec.model_version_id, loaded=snap.model_version_id)
        if spec.connection_id != snap.connection_id or (connection_id and connection_id != spec.connection_id):
            raise TelaError(ErrorCode.ONE_DATABASE_PER_WIDGET, "A widget reads one database only.")

        measures = []
        for mref in spec.all_measure_refs():
            if mref == spec.measure.ref and spec.adhoc is not None:
                measures.append(self._adhoc(spec.adhoc))
                continue
            m = snap.measure(ref_key(mref))
            if m is None or m.status != "certified":
                raise _reject("That measure is not in the approved model.", ref=mref)
            measures.append(m)
        root_key = measures[0].entity
        root = snap.entity(root_key)
        assert root is not None
        for m in measures[1:]:
            if m.entity != root_key:
                raise _reject("Extra measures must count the same thing as the main measure.",
                              ref=m.key, entity=m.entity, root=root_key)

        hidden_refs: list[str] = []
        entities = {root_key}
        for m in measures:
            for fkey in measure_fields(m.expression) | ({m.currency_field} if m.currency_field else set()):
                ent_key, field_key = split_ref(m.entity, fkey)
                if ent_key != root_key:  # a field of a linked entity (US-156) joins that entity
                    self._reachable(root_key, ent_key, f"measure:{m.key}")
                    entities.add(ent_key)
                if f"{ent_key}.{field_key}" in self.hidden:
                    hidden_refs.append(f"measure:{m.key}")

        personal = {p.key: p for p in spec.personal_fields}
        breakdowns: list[ResolvedBreakdown] = []
        outputs: set[str] = set()
        for b in spec.breakdowns:
            fref, pf = self._field(b.ref, personal)
            self._reachable(root_key, fref.entity, b.ref)
            entities.add(fref.entity)
            out = ref_key(b.ref).replace(".", "__")
            if out in outputs:
                raise _reject("The same breakdown is listed twice.", ref=b.ref)
            outputs.add(out)
            breakdowns.append(ResolvedBreakdown(b.ref, out, fref, pf))
            if self._is_hidden(fref):
                hidden_refs.append(b.ref)

        time_ref = None
        if spec.time is not None:
            fref, _ = self._field(spec.time.ref, personal)
            if fref.field.data_type not in ("date", "datetime"):
                raise _reject("Trends need a date field.", ref=spec.time.ref)
            self._reachable(root_key, fref.entity, spec.time.ref)
            entities.add(fref.entity)
            time_ref = (fref, spec.time)
            if fref.qualified in self.hidden:
                hidden_refs.append(spec.time.ref)
            if spec.time.grain:
                breakdowns.append(ResolvedBreakdown(spec.time.ref, "period", fref, None, spec.time.grain))

        filters: list[tuple[FieldRef, Any]] = []
        for flt in spec.filters:
            fref, pf = self._field(flt.ref, personal)
            if pf is not None:
                raise _reject("Filter on the underlying field rather than a personal field.", ref=flt.ref)
            self._reachable(root_key, fref.entity, flt.ref)
            if self._is_hidden(fref):
                hidden_refs.append(flt.ref)
            self._check_filter_values(fref, flt)
            entities.add(fref.entity)
            filters.append((fref, flt))

        # Semi-join filters (US-178)
        resolved_exists: list[ResolvedExistsIn] = []
        for ei in spec.exists_in:
            ent = snap.entity(ei.entity)
            if ent is None:
                raise _reject("That entity is not in the approved model.", entity=ei.entity)
            path = snap.join_path(root_key, ei.entity)
            if path is None:
                raise _reject("That entity is not linked to this measure in the approved model.",
                              entity=ei.entity, root=root_key)
            if len(path) != 1:  # the EXISTS joins on one link (a reading asked of another table may be further away)
                raise _reject("A semi-join needs an entity linked directly to this measure's.",
                              entity=ei.entity, root=root_key)
            # Find the direct link fields for the EXISTS subquery join
            link, forward = path[-1]
            root_fk = link.from_fields if forward else link.to_fields
            linked_fk = link.to_fields if forward else link.from_fields
            ei_filters: list[tuple[FieldRef, Any]] = []
            for flt in ei.filters:
                fref, pf = self._field(flt.ref, personal)
                if pf is not None:
                    raise _reject("Filter on the underlying field rather than a personal field.", ref=flt.ref)
                if fref.entity != ei.entity:
                    raise _reject("A semi-join filter must be on the linked entity.", ref=flt.ref, entity=ei.entity)
                ei_filters.append((fref, flt))
            where, mlabel = None, None
            if ei.measure is not None:
                lm = snap.measure(ei.measure.split(":", 1)[1])
                expr = getattr(lm, "expression", None)
                if lm is None or lm.status != "certified" or lm.entity != ei.entity:
                    raise _reject("A semi-join measure must be a certified measure of the linked entity.",
                                  measure=ei.measure, entity=ei.entity)
                if getattr(expr, "agg", None) not in ("count", "count_distinct"):
                    raise _reject("A semi-join measure must count rows of the linked entity.", measure=ei.measure)
                where, mlabel = getattr(expr, "where", None), lm.label
            entities.add(ei.entity)
            resolved_exists.append(ResolvedExistsIn(ei.entity, ent, list(root_fk), list(linked_fk), ei_filters,
                                                    where, mlabel))

        # Cohort breakdown (US-178)
        resolved_cohort: ResolvedCohort | None = None
        if spec.cohort is not None:
            fref, _ = self._field(spec.cohort.ref, personal)
            if fref.field.data_type not in ("date", "datetime"):
                raise _reject("A cohort needs a date field.", ref=spec.cohort.ref)
            if fref.entity == root_key:
                raise _reject("A cohort groups by a date on a linked entity, not the measure's own.",
                              ref=spec.cohort.ref, entity=fref.entity)
            path = snap.join_path(root_key, fref.entity)
            if path is None:
                raise _reject("That entity is not linked to this measure in the approved model.",
                              ref=spec.cohort.ref, entity=fref.entity)
            link, forward = path[-1]
            root_fk = link.from_fields if forward else link.to_fields
            linked_fk = link.to_fields if forward else link.from_fields
            entities.add(fref.entity)
            resolved_cohort = ResolvedCohort(spec.cohort.ref, fref, spec.cohort.grain, spec.cohort.agg,
                                             fref.entity, list(root_fk), list(linked_fk))
            breakdowns.append(ResolvedBreakdown(spec.cohort.ref, "cohort", fref, None, spec.cohort.grain))

        extras = self._extras(spec, measures, breakdowns)
        for s in spec.sort:
            if s.by == "measure" or s.by in spec.all_measure_refs() or s.by in {e.ref for e in extras}:
                continue
            if s.by not in {b.ref for b in spec.breakdowns} and not (spec.time and s.by == spec.time.ref):
                raise _reject("Sort by the measure or one of the breakdowns.", sort=s.by)

        cap = self.row_cap
        if spec.limit is not None and spec.limit > cap:
            raise _reject(f"Ask for at most {cap:,} rows.", limit=spec.limit, cap=cap)

        # Window operations (US-177)
        window_ops = self._window_ops(spec, measures, breakdowns)

        return ValidatedSpec(
            spec=spec, snapshot=snap, root=root, measures=measures, breakdowns=breakdowns, filters=filters,
            time=time_ref, entities=entities, hidden=bool(hidden_refs), hidden_refs=sorted(set(hidden_refs)),
            limit=spec.limit, certified=spec.adhoc is None, extras=extras,
            exists_in=resolved_exists, cohort=resolved_cohort, window_ops=window_ops,
        )

    # ------------------------------------------------------------------ derived figures and shares (US-176)

    @staticmethod
    def _extras(spec: QuerySpec, measures: list[Measure], breakdowns: list[ResolvedBreakdown]) -> list[Extra]:
        by_ref = {f"measure:{m.key}": m for m in measures}
        out: list[Extra] = []
        for d in spec.derived:
            a, b = by_ref[d.of], by_ref[d.to]
            same_unit = a.format == b.format
            if d.op == "difference" and not same_unit:
                raise _reject(f"'{a.label}' and '{b.label}' are in different units, so one can't be taken from "
                              "the other.", derived=d.key, formats=[a.format, b.format])
            if d.op in ("percent_of", "percent_change") and not same_unit:
                raise _reject(f"'{a.label}' can't be a percentage of '{b.label}': they are in different units.",
                              derived=d.key, formats=[a.format, b.format])
            label, rule = DERIVED_WORDS[d.op]
            out.append(Extra(
                d.key, d.op, a.key, b.key, d.label or label.format(a=a.label, b=b.label),
                DERIVED_FORMAT.get(d.op, a.format),
                rule.format(a=a.label, b=b.label),
                additive=d.op == "difference" and is_additive(a.expression) and is_additive(b.expression)))
        for r in spec.share_of_total:
            m = by_ref[r]
            if not breakdowns:
                raise _reject("A share of the total needs a breakdown to share out.", share=r)
            if not is_additive(m.expression):
                raise _reject(f"'{m.label}' doesn't add up across groups, so it has no share of a total.", share=r)
            out.append(Extra(share_key(r), "share", m.key, None, f"{m.label}: share of total (%)", "percent",
                             f"{m.label} in each row as a percentage of its total over all rows of the answer. "
                             "Blank when the total is zero.", additive=True))
        return out

    # ------------------------------------------------------------------ window operations (US-177)

    @staticmethod
    def _window_ops(spec: QuerySpec, measures: list[Any],
                    breakdowns: list[ResolvedBreakdown]) -> list[ResolvedWindowOp]:
        """Resolve and validate window operations on the spec."""
        ops: list[ResolvedWindowOp] = []
        measure_keys = {f"measure:{m.key}": m.key for m in measures}
        breakdown_outputs = {b.ref: b.output for b in breakdowns}
        has_grain = any(b.grain for b in breakdowns)
        along = next((b.output for b in breakdowns if b.grain), None)  # the time grain a window slides along
        # Without `per`, a running total or moving average restarts in each group of the other breakdowns.
        others = [b.output for b in breakdowns if not b.grain]
        primary_key: str = measures[0].key

        def resolve_measure(ref: str | None) -> str:
            if ref is None:
                return primary_key
            if ref not in measure_keys:
                raise _reject("That window operation names a measure that is not in this answer.", ref=ref)
            return str(measure_keys[ref])

        def resolve_partition(ref: str | None) -> str | None:
            if ref is None:
                return None
            if ref not in breakdown_outputs:
                raise _reject("That window operation partitions by a breakdown that is not in this answer.", ref=ref)
            return breakdown_outputs[ref]

        if spec.top_n_per_group is not None:
            t = spec.top_n_per_group
            if t.by not in breakdown_outputs:
                raise _reject("top_n_per_group.by must be one of the answer's breakdowns.", ref=t.by)
            ops.append(ResolvedWindowOp("top_n_per_group", primary_key,
                                         breakdown_outputs[t.by], {"n": t.n, "order": t.order}))

        if spec.running_total is not None:
            rt = spec.running_total
            if not has_grain:
                raise _reject("A running total needs a time grain to accumulate over.")
            mkey = resolve_measure(rt.measure)
            m_obj = next((m for m in measures if m.key == mkey), None)
            if m_obj is not None and not is_additive(m_obj.expression):
                raise _reject(f"A running total of '{m_obj.label}' doesn't add up — an average or a "
                              "percentage can't be accumulated.")
            ops.append(ResolvedWindowOp("running_total", mkey, resolve_partition(rt.per),
                                         {"along": along, "groups": others if rt.per is None else []}))

        if spec.moving_avg is not None:
            ma = spec.moving_avg
            if not has_grain:
                raise _reject("A moving average needs a time grain to slide over.")
            mkey = resolve_measure(ma.measure)
            m_obj = next((m for m in measures if m.key == mkey), None)
            if m_obj is not None and not is_additive(m_obj.expression):
                raise _reject(f"A moving average of '{m_obj.label}' would average averages or percentages "
                              "and mislead. Ask for the figure by period instead.")
            ops.append(ResolvedWindowOp("moving_avg", mkey, resolve_partition(ma.per),
                                         {"window": ma.window, "along": along,
                                          "groups": others if ma.per is None else []}))

        if spec.rank is not None:
            rk = spec.rank
            mkey = resolve_measure(rk.measure)
            ops.append(ResolvedWindowOp("rank", mkey, resolve_partition(rk.per),
                                         {"order": rk.order}))

        if spec.bucket is not None:
            bk = spec.bucket
            mkey = resolve_measure(bk.measure)
            labels = _bucket_labels(bk.k, bk.order)
            ops.append(ResolvedWindowOp("bucket", mkey, resolve_partition(bk.per),
                                         {"k": bk.k, "order": bk.order, "labels": labels}))

        if spec.percentile is not None:
            pc = spec.percentile
            mkey = resolve_measure(pc.measure)
            ops.append(ResolvedWindowOp("percentile", mkey, resolve_partition(pc.per),
                                         {"p": pc.p, "q": PERCENTILE_VALUES[pc.p]}))

        if spec.having is not None:
            hv = spec.having
            mkey = resolve_measure(hv.measure)
            if not breakdowns:
                raise _reject("A having filter needs a breakdown to group by.")
            ops.append(ResolvedWindowOp("having", mkey, params={"op": hv.op, "value": hv.value}))

        return ops

    # ------------------------------------------------------------------ ad-hoc rules (US-163)

    def _adhoc(self, a: AdhocMeasure) -> Measure:
        if not self.freehand:
            raise TelaError(ErrorCode.FORBIDDEN, NO_FREEHAND, {"reason": "freehand_not_granted"})
        if self.snapshot.entity(a.entity) is None:
            raise _reject("That rule counts something that is not in the approved model.", entity=a.entity)
        refs = condition_fields(a.where) | ({a.field} if a.field else set())
        if len(refs) > MAX_ADHOC_FIELDS:
            raise _reject(f"A free-hand rule can use at most {MAX_ADHOC_FIELDS} fields.", fields=len(refs))
        if condition_depth(a.where) > MAX_ADHOC_DEPTH:
            raise _reject("That rule is nested too deeply. Split it into simpler questions.",
                          depth=condition_depth(a.where))
        if or_branches(a.where) > MAX_ADHOC_BRANCHES:
            raise _reject(f"A free-hand rule can have at most {MAX_ADHOC_BRANCHES} alternatives.",
                          branches=or_branches(a.where))
        fields = {r: self._adhoc_field(a.entity, r) for r in refs}
        if a.field is not None:
            dtype = fields[a.field].data_type
            if a.agg in ("sum", "avg", "min", "max") and dtype not in NUMERIC_TYPES:
                raise _reject(f"'{a.agg}' needs a number field.", field=a.field)
        if a.where is not None:
            self._adhoc_condition(a.where, fields)
        fmt: Literal["integer", "decimal"] = "integer" if a.agg in ("count", "count_distinct") else "decimal"
        return Measure(key=ADHOC_KEY, label=a.label, entity=a.entity, plain_rule=a.plain_rule, format=fmt,
                       expression=AggExpr(agg=a.agg, field=a.field, where=a.where), status="adhoc",
                       blank_handling="Blank values are not counted.")

    def _adhoc_field(self, entity: str, ref: str) -> ModelField:
        ent_key, field_key = split_ref(entity, ref)
        ent = self.snapshot.entity(ent_key)
        f = ent.field(field_key) if ent else None
        if f is None or not f.is_queryable:
            raise _reject("That rule uses a field that is not in the approved model.", field=ref)
        if f"{ent_key}.{field_key}" in self.hidden:
            raise TelaError(ErrorCode.FORBIDDEN, f"That rule uses a field that is {HIDDEN_MESSAGE.lower()}.",
                            {"reason": "hidden_field"})
        if ent_key != entity:
            self._reachable(entity, ent_key, ref)
        return f

    def _adhoc_condition(self, cond: Any, fields: dict[str, ModelField]) -> None:
        if isinstance(cond, All | Any_):
            for c in cond.all if isinstance(cond, All) else cond.any:
                self._adhoc_condition(c, fields)
            return
        if isinstance(cond, Not):
            self._adhoc_condition(cond.not_, fields)
            return
        if isinstance(cond, NullCheck):
            return
        if isinstance(cond, Compare):
            f = fields[cond.field]
            if cond.other_field is not None:
                if _family(fields[cond.other_field].data_type) != _family(f.data_type):
                    raise _reject(f"'{f.label}' can't be compared with '{fields[cond.other_field].label}'.",
                                  field=cond.field, other=cond.other_field)
                return
            self._adhoc_value(f, cond.value, cond.field)
            return
        if isinstance(cond, InSet):
            f = fields[cond.field]
            if f.data_type in TEMPORAL_TYPES:
                raise _reject(f"Compare '{f.label}' with a date rather than a list.", field=cond.field)
            for v in cond.values:
                self._adhoc_value(f, v, cond.field)
            return
        raise _reject("Free-hand rules compare fields with values; calculated values need a certified measure.",
                      condition=type(cond).__name__)

    @staticmethod
    def _adhoc_value(f: ModelField, value: Any, ref: str) -> None:
        dtype = f.data_type
        if isinstance(value, AsOf | Today):
            if dtype not in TEMPORAL_TYPES:
                raise _reject(f"'{f.label}' is not a date.", field=ref)
            return
        if dtype in NUMERIC_TYPES and (isinstance(value, bool) or not isinstance(value, int | float)):
            raise _reject(f"'{f.label}' needs a number.", field=ref)
        if dtype in TEMPORAL_TYPES:
            from datetime import date

            try:
                date.fromisoformat(str(value)[:10])
            except ValueError as exc:
                raise _reject(f"'{value}' is not a date.", field=ref) from exc
            if not isinstance(value, str):
                raise _reject(f"'{f.label}' needs a date.", field=ref)
        if dtype == "boolean" and not isinstance(value, bool):
            raise _reject(f"'{f.label}' is yes or no.", field=ref)

    def _is_hidden(self, fref: FieldRef) -> bool:
        fields = fref.computed.fields() if fref.computed else {fref.field.key}
        return any(f"{fref.entity}.{k}" in self.hidden for k in fields)

    def _check_filter_values(self, fref: FieldRef, flt: Any) -> None:
        if fref.computed:
            allowed = list(fref.sort_order or fref.computed.labels())
            if flt.op not in ("=", "!=", "in", "not_in") or (allowed and any(str(v) not in allowed
                                                                              for v in flt.values)):
                raise _reject(f"'{fref.label}' takes one of: {', '.join(allowed)}.", ref=flt.ref)
            return
        dtype = fref.field.data_type
        for v in flt.values:
            if dtype in ("integer", "decimal") and (isinstance(v, bool) or not isinstance(v, int | float)):
                raise _reject(f"'{fref.label}' needs numbers.", ref=flt.ref)
            if dtype in ("date", "datetime") and not isinstance(v, str):
                raise _reject(f"'{fref.label}' needs dates.", ref=flt.ref)
        if dtype in ("date", "datetime"):
            from datetime import date

            for v in flt.values:
                try:
                    date.fromisoformat(str(v)[:10])
                except ValueError as exc:
                    raise _reject(f"'{v}' is not a date.", ref=flt.ref) from exc
        # Enum-like dimensions: values must exist in the scoped distinct values (S30 enum-fabrication lesson).
        if self.enum_values is not None and dtype == "text" and flt.op in ("=", "!=", "in", "not_in"):
            known = self.enum_values(fref)
            if known is not None:
                reverse = {lbl: raw for raw, lbl in fref.value_labels}
                unknown = [v for v in flt.values if str(v) not in known and reverse.get(str(v)) not in known
                           and str(v) != fref.not_set]
                if unknown:
                    raise _reject(
                        f"No {fref.label} called {', '.join(map(str, unknown[:3]))} in your data.",
                        ref=flt.ref, unknown=[str(u) for u in unknown[:20]],
                    )


def _family(dtype: str) -> str:
    return "number" if dtype in NUMERIC_TYPES else "date" if dtype in TEMPORAL_TYPES else dtype


def _bucket_labels(k: int, order: str) -> list[str]:
    """Human-readable labels for NTILE buckets: "Top quartile", "2nd quartile", … for k=4; "Top 20%",
    "2nd 20%", … for other k."""
    if k == 4:
        names = ["Top quartile", "2nd quartile", "3rd quartile", "Bottom quartile"]
        return names if order == "desc" else list(reversed(names))
    pct = 100 // k
    labels = []
    for i in range(k):
        if i == 0:
            labels.append(f"Top {pct}%")
        elif i == k - 1:
            labels.append(f"Bottom {pct}%")
        else:
            labels.append(f"{_ordinal(i + 1)} {pct}%")
    return labels if order == "desc" else list(reversed(labels))


def _ordinal(n: int) -> str:
    if 10 <= n % 100 <= 20:
        return f"{n}th"
    return f"{n}{'st' if n % 10 == 1 else 'nd' if n % 10 == 2 else 'rd' if n % 10 == 3 else 'th'}"
