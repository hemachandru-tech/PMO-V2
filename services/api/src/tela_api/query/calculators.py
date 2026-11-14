"""Pack calculators compiled to SQL (US-156; RULE-033 - RULE-035).

A calculator is a record-level rule a pack declares (``tela_query_spec.expr.CALCULATORS``). Here each one is
built as a sqlglot expression over the row's columns so measures, conditions and breakdowns can use it like any
other value. ``pmo_health_v1`` mirrors ``tela_api.packs.pmo_health.calculate`` step for step (the parity test
runs both over the synthetic extract). Fields missing from the model count as blank, as in the Python version.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from sqlglot import exp

from tela_common.errors import ErrorCode, TelaError

Col = Callable[[str], exp.Expression | None]  # field key -> column, None when the model lacks the field
Days = Callable[[exp.Expression, exp.Expression], exp.Expression]  # (from, to) -> whole days to - from


def num(v: float) -> exp.Expression:
    return exp.Literal.number(v)


def double(e: exp.Expression) -> exp.Expression:
    return exp.Cast(this=e, to=exp.DataType.build("double"))


def ratio(left: exp.Expression, right: exp.Expression) -> exp.Expression:
    """left / right as a fraction (never integer division); NULL on a zero divisor."""
    return exp.paren(exp.Div(this=double(left), expression=exp.Nullif(this=right, expression=num(0))), copy=False)


def case(ifs: list[tuple[exp.Expression, exp.Expression]], default: exp.Expression | None = None) -> exp.Case:
    return exp.Case(ifs=[exp.If(this=c, true=v) for c, v in ifs], default=default)


def is_null(e: exp.Expression) -> exp.Expression:
    return exp.Is(this=e.copy(), expression=exp.Null())


def _or(*conds: exp.Expression) -> exp.Expression:
    out = conds[0]
    for c in conds[1:]:
        out = exp.or_(out, c, copy=False)
    return exp.paren(out, copy=False)


def _and(*conds: exp.Expression) -> exp.Expression:
    out = conds[0]
    for c in conds[1:]:
        out = exp.and_(out, c, copy=False)
    return exp.paren(out, copy=False)


def _steps(value: exp.Expression, steps: list[tuple[str, float, float]], last: float) -> exp.Case:
    """CASE over thresholds in order: (op, threshold, score)."""
    ops = {"<=": exp.LTE, ">=": exp.GTE}
    return case([(ops[op](this=value.copy(), expression=num(t)), num(s)) for op, t, s in steps], num(last))


WEIGHTS = {"schedule": 0.35, "cost": 0.25, "progress": 0.25, "issues": 0.15}


def pmo_health(output: str, col: Col, days: Days, as_of: exp.Expression, text: Callable[[str], exp.Expression]
               ) -> exp.Expression:
    def c(key: str, kind: str = "double") -> exp.Expression:
        """The field, or a typed blank when the model lacks it (an untyped NULL is ambiguous in functions)."""
        e = col(key)
        return e if e is not None else exp.Cast(this=exp.Null(), to=exp.DataType.build(kind))

    def d(key: str) -> exp.Expression:
        e = col(key)
        return exp.Cast(this=e if e is not None else exp.Null(), to=exp.DataType.build("date"))

    planned, baseline, actual = d("planned_deployment_date"), d("first_baselined_deployment_date"), \
        d("actual_deployment_date")
    reference = exp.Coalesce(this=baseline.copy(), expressions=[planned.copy()])
    earliest = case([(exp.LT(this=planned.copy(), expression=reference.copy()), planned.copy())], reference.copy())
    slip = case([
        (exp.Not(this=is_null(actual)), days(reference.copy(), actual.copy())),
        (_and(exp.Not(this=is_null(planned)), exp.LT(this=planned.copy(), expression=as_of.copy())),
         days(earliest, as_of.copy())),
    ], days(reference.copy(), exp.Coalesce(this=planned.copy(), expressions=[reference.copy()])))
    schedule = case([(_and(is_null(planned), is_null(baseline)), exp.Null())],
                    _steps(slip, [("<=", 0, 100), ("<=", 15, 75), ("<=", 30, 50), ("<=", 60, 25)], 0))

    budget, cost = c("approved_budget_value_usd"), c("cost_total_usd")
    variance = exp.Mul(this=ratio(exp.Sub(this=cost.copy(), expression=budget.copy()), budget.copy()),
                       expression=num(100))
    cost_score = case([(_or(is_null(budget), is_null(cost), exp.LTE(this=budget.copy(), expression=num(0)),
                            exp.LT(this=cost.copy(), expression=num(0))), exp.Null())],
                      _steps(variance, [("<=", 0, 100), ("<=", 10, 75), ("<=", 25, 50), ("<=", 50, 25)], 0))

    pct = c("percent_complete")
    start = exp.Coalesce(this=d("actual_project_start_date"), expressions=[d("created_on")])
    total = days(start.copy(), planned.copy())
    elapsed = exp.Mul(this=ratio(days(start.copy(), as_of.copy()), total.copy()), expression=num(100))
    expected = case([(exp.LT(this=elapsed.copy(), expression=num(0)), num(0)),
                     (exp.GT(this=elapsed.copy(), expression=num(100)), num(100))], elapsed.copy())
    gap = exp.Sub(this=pct.copy(), expression=expected)
    progress = case([(_or(is_null(pct), exp.LT(this=pct.copy(), expression=num(0)),
                          exp.GT(this=pct.copy(), expression=num(100)), is_null(start), is_null(planned),
                          exp.LTE(this=total.copy(), expression=num(0))), exp.Null())],
                    _steps(gap, [(">=", -5, 100), (">=", -15, 75), (">=", -25, 50), (">=", -50, 25)], 0))

    kpi = c("issue_kpi", "varchar")
    kpi_norm = exp.Lower(this=exp.Trim(this=kpi))
    issues = case([(exp.EQ(this=kpi_norm.copy(), expression=text("green")), num(100)),
                   (exp.In(this=kpi_norm.copy(), expressions=[text("yellow"), text("amber")]), num(50)),
                   (exp.EQ(this=kpi_norm.copy(), expression=text("red")), num(0))])

    comps = {"schedule": schedule, "cost": cost_score, "progress": progress, "issues": issues}
    coverage: exp.Expression = num(0)
    weighted: exp.Expression = num(0)
    for k, s in comps.items():
        w = num(WEIGHTS[k])
        coverage = exp.Add(this=coverage, expression=case([(exp.Not(this=is_null(s)), w)], num(0)))
        weighted = exp.Add(this=weighted, expression=case([(exp.Not(this=is_null(s)), exp.Mul(
            this=s.copy(), expression=w.copy()))], num(0)))
    coverage = exp.paren(coverage, copy=False)
    enough = exp.GTE(this=coverage.copy(), expression=num(0.6))
    score = case([(enough, ratio(exp.paren(weighted, copy=False), coverage.copy()))], exp.Null())
    if output == "coverage":
        return coverage
    if output == "score":
        return score
    if output != "status":
        raise TelaError(ErrorCode.SPEC_INVALID, "Unknown calculator output.")

    def zero(e: exp.Expression) -> exp.Expression:
        return exp.EQ(this=e.copy(), expression=num(0))

    return case([
        (exp.Not(this=enough.copy()), text("Insufficient data")),
        (zero(schedule), text("Red")),
        (zero(issues), text("Red")),
        (zero(cost_score), text("Red")),
        (_and(exp.EQ(this=cost_score.copy(), expression=num(25)), exp.GTE(this=score.copy(), expression=num(75))),
         text("Amber")),
        (exp.GTE(this=score.copy(), expression=num(75)), text("Green")),
        (exp.GTE(this=score.copy(), expression=num(50)), text("Amber")),
    ], text("Red"))


BUILDERS: dict[str, Callable[..., exp.Expression]] = {"pmo_health_v1": pmo_health}


def build(name: str, output: str, **kw: Any) -> exp.Expression:
    builder = BUILDERS.get(name)
    if builder is None:
        raise TelaError(ErrorCode.SPEC_INVALID, "Unknown calculator.", {"admin_detail": {"calculator": name}})
    return builder(output, **kw)
