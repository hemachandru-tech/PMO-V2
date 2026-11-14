"""Typed expression AST for measure rules and filters (ADR-001; DATA-019 ``expression``).

Measure rules are data, never SQL text. A condition compares a field of the
measure's entity with a literal, with the reporting date (``asOf``) shifted by
a number of days, or with another field. Conditions combine with all/any/not.
An aggregate expression is either one aggregate over the entity (optionally
filtered by a condition) or arithmetic over two aggregate expressions.

US-156 adds row values: whole days between two dates (``days_from``/``days_to``,
either side a field or the reporting date), arithmetic over fields and numbers
(``calc``) and a pack calculator output (``calculator``, e.g. PMO calculated
health). An aggregate may read a value instead of a field, a ``check`` condition (``of``)
compares a value with literals, and ``median``/``percentile`` aggregates exist.
A field key may name a field of a linked entity as ``<entity>.<field>``.

US-163 adds **today** (``{"today": true, "offset_days": n}``): the calendar date of the run in the tenant's time
zone, wherever the reporting date may stand. The service pins it once per run (``pin_today``) by turning it into
the reporting date shifted by the gap between the two, so every compiler reads only ``asOf``.
"""

from __future__ import annotations

import re
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

IDENT = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")
REF = re.compile(r"^([a-z_][a-z0-9_]{0,62}\.)?[a-z_][a-z0-9_]{0,62}$")

Scalar = str | int | float | bool


def check_ident(value: str) -> str:
    if not IDENT.match(value):
        raise ValueError(f"'{value}' is not a valid key (lowercase letters, digits and underscores)")
    return value


def check_ref(value: str) -> str:
    """A field of the measure's entity (``field``) or of a linked entity (``entity.field``)."""
    if not REF.match(value):
        raise ValueError(f"'{value}' is not a valid field reference")
    return value


def split_ref(entity: str, ref: str) -> tuple[str, str]:
    """(entity, field) of a field reference read from ``entity``'s measures."""
    head, _, tail = ref.partition(".")
    return (head, tail) if tail else (entity, head)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class AsOf(_Strict):
    """The reporting date shifted by ``offset_days`` (RULE-018 ``asOf``)."""

    asof_offset_days: int = Field(ge=-36_600, le=36_600)


class Today(_Strict):
    """Today's date shifted by ``offset_days`` (US-163), fixed once per run."""

    today: Literal[True]
    offset_days: int = Field(default=0, ge=-36_600, le=36_600)


CompareOp = Literal["=", "!=", "<", "<=", ">", ">="]
SetOp = Literal["in", "not_in"]
NullOp = Literal["is_null", "not_null"]


class Compare(_Strict):
    field: str
    op: CompareOp
    value: Scalar | AsOf | Today | None = None
    other_field: str | None = None

    _f = field_validator("field", "other_field")(lambda v: v if v is None else check_ref(v))

    @model_validator(mode="after")
    def _one_rhs(self) -> Compare:
        if (self.value is None) == (self.other_field is None):
            raise ValueError("give exactly one of 'value' or 'other_field'")
        return self


class InSet(_Strict):
    field: str
    op: SetOp
    values: list[Scalar] = Field(min_length=1, max_length=500)

    _f = field_validator("field")(check_ref)


class NullCheck(_Strict):
    field: str
    op: NullOp

    _f = field_validator("field")(check_ref)


# ---------------------------------------------------------------------------- row values (US-156)


class DaysBetween(_Strict):
    """Whole days from ``days_from`` to ``days_to`` (to - from); each side is a date field or the reporting date
    shifted. Date-times count by their date. NULL when either date is blank."""

    days_from: str | AsOf | Today
    days_to: str | AsOf | Today

    _f = field_validator("days_from", "days_to")(lambda v: check_ref(v) if isinstance(v, str) else v)


class Calc(_Strict):
    """Row arithmetic over fields, numbers and values; ``div`` is NULL on a zero divisor."""

    calc: Literal["add", "sub", "mul", "div"]
    left: Operand
    right: Operand

    _f = field_validator("left", "right")(lambda v: check_ref(v) if isinstance(v, str) else v)


class Calculator(_Strict):
    """One output of a record-level pack calculator (e.g. ``pmo_health_v1`` score or status)."""

    calculator: str
    output: str

    _k = field_validator("calculator", "output")(check_ident)

    @model_validator(mode="after")
    def _known(self) -> Calculator:
        spec = CALCULATORS.get(self.calculator)
        if spec is None:
            raise ValueError(f"unknown calculator '{self.calculator}'")
        if self.output not in spec["outputs"]:
            raise ValueError(f"calculator '{self.calculator}' has no output '{self.output}'")
        return self


Value = Annotated[DaysBetween | Calc | Calculator, Field(union_mode="left_to_right")]
Operand = Annotated[DaysBetween | Calc | Calculator | float | str, Field(union_mode="left_to_right")]

# Calculators compiled by the query engine (record-level rules a pack declares). ``required`` fields must be in the
# model for the output to exist; ``optional`` ones count as blank when missing. Outputs: number or text.
CALCULATORS: dict[str, dict[str, Any]] = {
    "pmo_health_v1": {
        "outputs": {"score": "decimal", "status": "text", "coverage": "decimal"},
        "required": ["planned_deployment_date"],
        "optional": ["first_baselined_deployment_date", "actual_deployment_date", "approved_budget_value_usd",
                     "cost_total_usd", "percent_complete", "actual_project_start_date", "created_on", "issue_kpi"],
    },
}


class Check(_Strict):
    """Compare a row value with literals, e.g. days late > 60 or calculated health in (Red, Amber)."""

    of: Value
    op: CompareOp | SetOp
    value: Scalar | None = None
    values: list[Scalar] | None = Field(default=None, min_length=1, max_length=50)

    @model_validator(mode="after")
    def _rhs(self) -> Check:
        if self.op in ("in", "not_in") and (self.values is None or self.value is not None):
            raise ValueError("'in' and 'not_in' take 'values'")
        if self.op not in ("in", "not_in") and (self.value is None or self.values is not None):
            raise ValueError(f"'{self.op}' takes one 'value'")
        return self


class All(_Strict):
    all: list[Condition] = Field(min_length=1, max_length=50)


class Any_(_Strict):
    any: list[Condition] = Field(min_length=1, max_length=50)


class Not(_Strict):
    not_: Condition = Field(alias="not")

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)


Condition = Annotated[Compare | InSet | NullCheck | Check | All | Any_ | Not, Field(union_mode="left_to_right")]

Agg = Literal["count", "count_distinct", "sum", "avg", "min", "max", "median", "percentile"]


class AggExpr(_Strict):
    """One aggregate over the measure's entity. ``count`` without a field counts rows. ``value`` aggregates a row
    value instead of a field; ``percentile`` takes ``p`` (0-1, continuous, e.g. 0.85)."""

    agg: Agg
    field: str | None = None
    value: Value | None = None
    p: float | None = Field(default=None, gt=0, lt=1)
    where: Condition | None = None

    _f = field_validator("field")(lambda v: v if v is None else check_ref(v))

    @model_validator(mode="after")
    def _field_needed(self) -> AggExpr:
        if self.field is not None and self.value is not None:
            raise ValueError("give 'field' or 'value', not both")
        if self.agg != "count" and self.field is None and self.value is None:
            raise ValueError(f"'{self.agg}' needs a field")
        if (self.agg == "percentile") != (self.p is not None):
            raise ValueError("'percentile' needs 'p' (and only it takes one)")
        return self


class Arith(_Strict):
    """Arithmetic over aggregates, e.g. variance = forecast - approved; ``div`` is NULL-safe on zero. A side may be
    a plain number, e.g. go-lives in 12 months divided by 12."""

    op: Literal["add", "sub", "mul", "div"]
    left: MeasureExpr | float
    right: MeasureExpr | float
    scale: float | None = None  # multiply the result, e.g. 100 for a percentage

    @model_validator(mode="after")
    def _one_aggregate(self) -> Arith:
        if not isinstance(self.left, AggExpr | Arith) and not isinstance(self.right, AggExpr | Arith):
            raise ValueError("arithmetic needs at least one aggregate")
        return self


MeasureExpr = Annotated[AggExpr | Arith, Field(union_mode="left_to_right")]

Calc.model_rebuild()
Check.model_rebuild()
All.model_rebuild()
Any_.model_rebuild()
Not.model_rebuild()
AggExpr.model_rebuild()
Arith.model_rebuild()


def value_fields(value: Any) -> set[str]:
    """Every field reference a row value reads (a calculator's required inputs; optional ones may be missing)."""
    if isinstance(value, str):
        return {value}
    if isinstance(value, DaysBetween):
        return {v for v in (value.days_from, value.days_to) if isinstance(v, str)}
    if isinstance(value, Calc):
        return value_fields(value.left) | value_fields(value.right)
    if isinstance(value, Calculator):
        return set(CALCULATORS[value.calculator]["required"])
    return set()


def condition_fields(cond: Condition | None) -> set[str]:
    """Every field key a condition reads."""
    if cond is None:
        return set()
    if isinstance(cond, Check):
        return value_fields(cond.of)
    if isinstance(cond, Compare):
        return {cond.field} | ({cond.other_field} if cond.other_field else set())
    if isinstance(cond, InSet | NullCheck):
        return {cond.field}
    if isinstance(cond, All):
        return set().union(*(condition_fields(c) for c in cond.all))
    if isinstance(cond, Any_):
        return set().union(*(condition_fields(c) for c in cond.any))
    return condition_fields(cond.not_)


def measure_fields(expr: MeasureExpr | float) -> set[str]:
    """Every field reference a measure reads: ``field`` on its own entity, ``entity.field`` on a linked one."""
    if isinstance(expr, AggExpr):
        return ({expr.field} if expr.field else set()) | value_fields(expr.value) | condition_fields(expr.where)
    if isinstance(expr, Arith):
        return measure_fields(expr.left) | measure_fields(expr.right)
    return set()


def measure_refs(entity: str, expr: MeasureExpr) -> set[str]:
    """Qualified ``entity.field`` names a measure of ``entity`` reads."""
    return {".".join(split_ref(entity, r)) for r in measure_fields(expr)}


def measure_aggs(expr: MeasureExpr | float) -> list[AggExpr]:
    if isinstance(expr, AggExpr):
        return [expr]
    if isinstance(expr, Arith):
        return measure_aggs(expr.left) + measure_aggs(expr.right)
    return []


def _cond_extended(cond: Any) -> bool:
    if cond is None:
        return False
    if isinstance(cond, Check):
        return True
    if isinstance(cond, Compare):
        return "." in cond.field or "." in (cond.other_field or "")
    if isinstance(cond, InSet | NullCheck):
        return "." in cond.field
    if isinstance(cond, All):
        return any(_cond_extended(c) for c in cond.all)
    if isinstance(cond, Any_):
        return any(_cond_extended(c) for c in cond.any)
    return _cond_extended(cond.not_)


def uses_extended(expr: MeasureExpr | float) -> bool:
    """True when a measure needs the US-156 features (row values, medians, linked fields, number operands), which
    only the SQL engines compile; document and Web API sources answer Unavailable."""
    if isinstance(expr, AggExpr):
        return (expr.value is not None or expr.agg in ("median", "percentile") or "." in (expr.field or "")
                or _cond_extended(expr.where))
    if isinstance(expr, Arith):
        return (not isinstance(expr.left, AggExpr | Arith) or not isinstance(expr.right, AggExpr | Arith)
                or uses_extended(expr.left) or uses_extended(expr.right))
    return False


def is_additive(expr: MeasureExpr | float) -> bool:
    """Counts and sums are additive (RULE-008 donut / stacked bars); averages and ratios are not."""
    if isinstance(expr, AggExpr):
        return expr.agg in ("count", "sum")
    if isinstance(expr, Arith):
        return expr.op in ("add", "sub") and is_additive(expr.left) and is_additive(expr.right)
    return False


# ---------------------------------------------------------------------------- today (US-163)


def walk(node: Any, fn: Any) -> Any:
    """Rebuild ``node`` with ``fn`` applied to every sub-node (``fn`` returns the node itself to keep it)."""
    out = fn(node)
    if out is not node:
        return out
    if isinstance(node, BaseModel):
        updates = {name: new for name in type(node).model_fields
                   if (new := walk(getattr(node, name), fn)) is not getattr(node, name)}
        return node.model_copy(update=updates) if updates else node
    if isinstance(node, list):
        items = [walk(x, fn) for x in node]
        return items if any(a is not b for a, b in zip(items, node, strict=True)) else node
    return node


def uses_today(node: Any) -> bool:
    found: list[bool] = []

    def spot(n: Any) -> Any:
        if isinstance(n, Today):
            found.append(True)
        return n

    walk(node, spot)
    return bool(found)


def pin_today(node: Any, shift_days: int) -> Any:
    """``node`` with every ``today`` turned into the reporting date shifted by ``shift_days`` (today - reporting
    date) plus its own offset: compilers only know ``asOf``."""
    return walk(node, lambda n: AsOf(asof_offset_days=n.offset_days + shift_days) if isinstance(n, Today) else n)


def condition_depth(cond: Condition | None) -> int:
    """Nesting of all / any / not (a single comparison is 1)."""
    if cond is None:
        return 0
    if isinstance(cond, All | Any_):
        return 1 + max(condition_depth(c) for c in (cond.all if isinstance(cond, All) else cond.any))
    if isinstance(cond, Not):
        return 1 + condition_depth(cond.not_)
    return 1


def or_branches(cond: Condition | None) -> int:
    """Alternatives across every ``any`` in the condition."""
    if isinstance(cond, All):
        return sum(or_branches(c) for c in cond.all)
    if isinstance(cond, Any_):
        return len(cond.any) + sum(or_branches(c) for c in cond.any)
    if isinstance(cond, Not):
        return or_branches(cond.not_)
    return 0
