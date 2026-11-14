"""Several measures, derived figures and shares of the total without a source (US-176; FEAT-063; RULE-008).

The spec rules (``measures`` list, derived figures over two of the answer's certified measures, shares), the
validator's unit and shape checks, the SQL compiler on DuckDB over the synthetic extract (derived columns are
checked against the measure columns of the same answer, shares against the full breakdown, zero divisors give a
blank), the RULE-008 rows for paired measures and percentages, and how Ask plans these questions (rules planner
and AG-03 output). Engine parity for the same shapes is in ``test_dialect_parity``.
"""

from __future__ import annotations

from typing import Any

import duckdb
import pytest
import tpg_synthetic
from pydantic import ValidationError
from sqlglot import exp
from test_planner_gold_unit import SNAP, plan
from test_pmo_catalogue_unit import _catalog

from tela_api.ask.planner import PlannerOut, measure_pair, spec_from_planner
from tela_api.packs.manifest import bind, discover, load_manifest, match
from tela_api.query.compiler import EXTRA_ALIAS, SqlCompiler, extra_expr, extra_value
from tela_api.query.limits import Limits, apply_limit
from tela_api.query.validator import Extra, SpecValidator
from tela_api.query.viz import visualise
from tela_common.errors import TelaError
from tela_query_spec.model import ModelSnapshot
from tela_query_spec.spec import parse_spec

AS_OF = tpg_synthetic.AS_OF
FORECAST, BUDGET = "measure:forecast_cost_total", "measure:approved_budget_total"
OPEN, DELIVERY, HOLD = "measure:open_projects", "measure:in_delivery", "measure:on_hold"


@pytest.fixture(scope="module")
def snapshot() -> ModelSnapshot:
    manifest = load_manifest(next(p for p in discover() if p.parent.name == "pmo"))[0]
    binding = bind(manifest, match(manifest, _catalog()), reporting_date=AS_OF.isoformat())
    return ModelSnapshot.model_validate({"model_version_id": "v1", "connection_id": "c1", "engine": "excel",
                                         **binding.definition})


@pytest.fixture(scope="module")
def db() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    con.execute("\n".join(line for line in tpg_synthetic.DDL.splitlines() if not line.startswith("DROP TABLE")))
    for table, cols, rows in tpg_synthetic.tables():
        con.executemany(f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                        [tuple(r[c] for c in cols) for r in rows])
    return con


def spec(body: dict[str, Any], snap: ModelSnapshot | None = None) -> Any:
    ids = {"model_version_id": snap.model_version_id, "connection_id": snap.connection_id} if snap else {
        "model_version_id": "v1", "connection_id": "c1"}
    return parse_spec({**ids, **body})[0]


def run(db: duckdb.DuckDBPyConnection, snap: ModelSnapshot, body: dict[str, Any]) -> tuple[list[str], list[tuple]]:
    vs = SpecValidator(snap).validate(spec(body))
    compiled = SqlCompiler("duckdb").compile(vs, as_of=AS_OF)
    tree, _, _ = apply_limit(compiled.tree, Limits(), body.get("limit"))
    rows = db.execute(tree.sql(dialect="duckdb"), compiled.params.values).fetchall()
    return [c.name for c in compiled.columns], rows


# --------------------------------------------------------------------------- the spec


def test_measures_list_is_measure_with_extra_measures() -> None:
    s = spec({"measures": [{"ref": FORECAST}, {"ref": BUDGET}]})
    assert s.measure.ref == FORECAST and [m.ref for m in s.extra_measures] == [BUDGET]
    assert s.all_measure_refs() == [FORECAST, BUDGET]


@pytest.mark.parametrize(("body", "message"), [
    ({"measures": [{"ref": f"measure:m{i}"} for i in range(7)]}, "at most 6"),
    ({"measures": [{"ref": FORECAST}], "measure": {"ref": BUDGET}}, "not both"),
    ({"measures": []}, "at least one"),
    ({"measures": [{"ref": FORECAST}, {"ref": FORECAST}]}, "listed twice"),
    ({"measures": [{"ref": FORECAST}, {"ref": BUDGET}],
      "derived": [{"key": "x", "op": "ratio", "of": FORECAST, "to": OPEN}]}, "not one of this answer's measures"),
    ({"measures": [{"ref": FORECAST}, {"ref": BUDGET}],
      "derived": [{"key": "x", "op": "ratio", "of": FORECAST, "to": FORECAST}]}, "two different measures"),
    ({"measures": [{"ref": FORECAST}, {"ref": BUDGET}],
      "derived": [{"key": "forecast_cost_total", "op": "ratio", "of": FORECAST, "to": BUDGET}]}, "its own key"),
    ({"measures": [{"ref": FORECAST}, {"ref": BUDGET}],
      "derived": [{"key": "a", "op": "ratio", "of": FORECAST, "to": BUDGET},
                  {"key": "a", "op": "difference", "of": FORECAST, "to": BUDGET}]}, "its own key"),
    ({"measures": [{"ref": FORECAST}, {"ref": BUDGET}],
      "derived": [{"key": "x__share", "op": "ratio", "of": FORECAST, "to": BUDGET}]}, "invalid derived key"),
    ({"measures": [{"ref": FORECAST}, {"ref": BUDGET}],
      "derived": [{"key": "x", "op": "ratio", "of": "dimension:region", "to": BUDGET}]}, "worked out from measures"),
    ({"measure": {"ref": FORECAST}, "share_of_total": [BUDGET]}, "not one of this answer's measures"),
    ({"measure": {"ref": FORECAST}, "share_of_total": [FORECAST, FORECAST]}, "lists a measure twice"),
    ({"measure": {"adhoc": {"entity": "projects", "agg": "count", "label": "x", "plain_rule": "x"}},
      "extra_measures": [{"ref": OPEN}], "derived": [{"key": "x", "op": "ratio", "of": "measure:_adhoc", "to": OPEN}]},
     "certified measures"),
])
def test_spec_rules(body: dict[str, Any], message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        spec(body)


# --------------------------------------------------------------------------- the validator


def test_validator_labels_formats_and_sort(snapshot: ModelSnapshot) -> None:
    vs = SpecValidator(snapshot).validate(spec({
        "measures": [{"ref": FORECAST}, {"ref": BUDGET}, {"ref": OPEN}], "breakdowns": [{"ref": "dimension:region"}],
        "derived": [{"key": "pct", "op": "percent_of", "of": FORECAST, "to": BUDGET},
                    {"key": "gap", "op": "difference", "of": FORECAST, "to": BUDGET},
                    {"key": "per", "op": "ratio", "of": FORECAST, "to": OPEN, "label": "Forecast per project"}],
        "share_of_total": [OPEN], "sort": [{"by": "derived:pct"}, {"by": "share:open_projects", "dir": "asc"}]}))
    got = {e.key: (e.op, e.label, e.format, e.ref, e.additive) for e in vs.extras}
    assert got == {
        "pct": ("percent_of", "Forecast cost (EAC, USD) as % of Approved budget (USD)", "percent", "derived:pct",
                False),
        "gap": ("difference", "Forecast cost (EAC, USD) minus Approved budget (USD)", "currency", "derived:gap", True),
        "per": ("ratio", "Forecast per project", "decimal", "derived:per", False),
        "open_projects__share": ("share", "Open projects: share of total (%)", "percent", "share:open_projects", True),
    }
    assert "zero" in vs.extras[0].plain_rule


@pytest.mark.parametrize(("body", "message"), [
    ({"measures": [{"ref": FORECAST}, {"ref": OPEN}],
      "derived": [{"key": "x", "op": "difference", "of": FORECAST, "to": OPEN}]}, "different units"),
    ({"measures": [{"ref": FORECAST}, {"ref": OPEN}],
      "derived": [{"key": "x", "op": "percent_of", "of": FORECAST, "to": OPEN}]}, "different units"),
    ({"measure": {"ref": OPEN}, "share_of_total": [OPEN]}, "needs a breakdown"),
    ({"measure": {"ref": "measure:reported_green_pct"}, "breakdowns": [{"ref": "dimension:region"}],
      "share_of_total": ["measure:reported_green_pct"]}, "doesn't add up"),
    ({"measure": {"ref": OPEN}, "breakdowns": [{"ref": "dimension:region"}],
      "sort": [{"by": "derived:nothing"}]}, "Sort by the measure"),
    ({"measures": [{"ref": OPEN}, {"ref": "measure:total_requests"}]}, "same thing"),
])
def test_validator_refuses(snapshot: ModelSnapshot, body: dict[str, Any], message: str) -> None:
    with pytest.raises(TelaError, match=message):
        SpecValidator(snapshot).validate(spec(body))


# --------------------------------------------------------------------------- the compiler on DuckDB


def _close(a: Any, b: Any) -> bool:
    return (a is None and b is None) or (a is not None and b is not None and abs(float(a) - float(b)) < 1e-6)


def test_derived_figures_match_their_measures(db: duckdb.DuckDBPyConnection, snapshot: ModelSnapshot) -> None:
    names, rows = run(db, snapshot, {
        "measures": [{"ref": FORECAST}, {"ref": BUDGET}], "breakdowns": [{"ref": "dimension:region"}],
        "derived": [{"key": "pct_of", "op": "percent_of", "of": FORECAST, "to": BUDGET},
                    {"key": "over", "op": "percent_change", "of": FORECAST, "to": BUDGET},
                    {"key": "gap", "op": "difference", "of": FORECAST, "to": BUDGET}]})
    assert names == ["region", "forecast_cost_total", "approved_budget_total", "pct_of", "over", "gap"]
    alone = dict(run(db, snapshot, {"measure": {"ref": FORECAST}, "breakdowns": [{"ref": "dimension:region"}]})[1])
    assert len(rows) >= 3
    for region, f, b, pct, over, gap in rows:
        assert _close(alone[region], f)
        assert _close(pct, None if not b or f is None else f / b * 100)
        assert _close(over, None if not b or f is None else (f - b) / b * 100)
        assert _close(gap, None if f is None or b is None else f - b)


def test_four_measures_and_a_ratio_in_one_row(db: duckdb.DuckDBPyConnection, snapshot: ModelSnapshot) -> None:
    names, rows = run(db, snapshot, {
        "measures": [{"ref": OPEN}, {"ref": DELIVERY}, {"ref": HOLD}, {"ref": "measure:total_projects"}],
        "derived": [{"key": "in_delivery_rate", "op": "percent_of", "of": DELIVERY, "to": OPEN}]})
    assert len(rows) == 1 and len(names) == 5
    o, d, h, t, rate = rows[0]
    assert t >= o >= d and h <= o
    assert _close(rate, d / o * 100)


def test_share_of_total_is_over_every_group_before_top_n(db: duckdb.DuckDBPyConnection,
                                                         snapshot: ModelSnapshot) -> None:
    body = {"measure": {"ref": OPEN}, "breakdowns": [{"ref": "dimension:cio"}], "share_of_total": [OPEN]}
    names, everyone = run(db, snapshot, body)
    assert names == ["cio", "open_projects", "open_projects__share"]
    total = sum(r[1] for r in everyone)
    assert _close(sum(r[2] for r in everyone), 100)
    for _, n, share in everyone:
        assert _close(share, n / total * 100)
    _, top = run(db, snapshot, {**body, "sort": [{"by": "share:open_projects"}, {"by": "dimension:cio", "dir": "asc"}],
                                "limit": 3})
    assert len(top) == 3
    assert [r[2] for r in top] == sorted((r[2] for r in everyone), reverse=True)[:3]  # not 100 % over three rows


def test_shares_through_a_fan_out_link(db: duckdb.DuckDBPyConnection, snapshot: ModelSnapshot) -> None:
    requests = next(m for m in snapshot.measures if m.entity == "project_requests")
    ref = f"measure:{requests.key}"
    _, rows = run(db, snapshot, {"measure": {"ref": ref}, "breakdowns": [{"ref": "field:projects.region"}],
                                 "share_of_total": [ref]})
    total = sum(r[1] for r in rows)
    assert total and all(_close(s, n / total * 100) for _, n, s in rows)


def test_zero_or_blank_divisor_is_blank_never_an_error() -> None:
    cols = {k: exp.column(k, table=EXTRA_ALIAS) for k in ("a", "b")}
    exprs = [extra_expr(Extra(op, op, "a", "b", op, "decimal", ""), cols).as_(op)
             for op in ("ratio", "difference", "percent_of", "percent_change")]
    exprs.append(extra_expr(Extra("s", "share", "a", None, "s", "percent", ""), cols).as_("s"))
    q = exp.select(*exprs).from_("(VALUES (4, 0), (6, NULL), (NULL, 2), (3, 2)) AS tela_d(a, b)")
    rows = [tuple(None if v is None else round(v, 4) for v in r)
            for r in duckdb.connect().execute(q.sql(dialect="duckdb")).fetchall()]
    assert rows == [(None, 4, None, None, 30.7692), (None, None, None, None, 46.1538), (None, None, None, None, None),
                    (1.5, 1, 150.0, 50.0, 23.0769)]


def test_extra_value_matches_sql_for_a_figure_over_no_rows() -> None:
    def e(op: str) -> Extra:
        return Extra(op, op, "a", "b", op, "decimal", "")

    assert [extra_value(e(op), {"a": 0, "b": 0}) for op in ("ratio", "difference", "percent_of",
                                                             "percent_change")] == [None, 0, None, None]
    assert extra_value(e("percent_change"), {"a": 3, "b": 2}) == 50
    assert extra_value(e("ratio"), {"a": None, "b": 2}) is None


# --------------------------------------------------------------------------- RULE-008: paired measures, percentages


def _col(name: str, ref: str, fmt: str, of: str | None = None) -> dict[str, Any]:
    out = {"name": name, "kind": "measure", "label": name, "ref": ref, "data_type": "decimal", "format": fmt}
    return {**out, "of": of} if of else out


REGION = {"name": "region", "kind": "dimension", "label": "Region", "ref": "dimension:region", "data_type": "text"}


def test_pair_with_a_percentage_is_paired_bars_labelled_by_the_percentage() -> None:
    result = {"columns": [REGION, _col("forecast_cost_total", FORECAST, "currency"),
                          _col("approved_budget_total", BUDGET, "currency"),
                          _col("pct", "derived:pct", "percent", of="forecast_cost_total")],
              "rows": [["KSA", 120.0, 100.0, 120.0], ["Jordan", 80.0, 100.0, 80.0], ["UAE", 50.0, 0, None]]}
    out = visualise(result, SNAP)
    assert out["visual"] == "paired_bars"
    assert out["chart_spec"]["series"] == ["forecast_cost_total", "approved_budget_total"]
    assert out["chart_spec"]["companions"] == {"pct": "forecast_cost_total"}
    assert visualise(result, SNAP, "table")["chart_spec"]["series"] == [  # the table shows every column
        "forecast_cost_total", "approved_budget_total", "pct"]


def test_share_companion_keeps_donut_and_ranked_bars() -> None:
    result = {"columns": [REGION, _col("open_projects", OPEN, "integer"),
                          _col("open_projects__share", "share:open_projects", "percent", of="open_projects")],
              "rows": [["KSA", 6, 50.0], ["Jordan", 4, 33.3], ["UAE", 2, 16.7]]}
    out = visualise(result, SNAP)
    assert out["visual"] == "ranked_bar" and "donut" in out["alternatives"]
    assert out["chart_spec"]["series"] == ["open_projects"]
    assert out["chart_spec"]["companions"] == {"open_projects__share": "open_projects"}


def test_different_units_never_share_an_axis() -> None:
    period = {"name": "period", "kind": "period", "label": "Month", "ref": "dimension:planned_deployment_date",
              "data_type": "date"}
    rows = [[f"2026-0{i}-01", i * 10.0, i] for i in range(1, 5)]
    mixed = {"columns": [period, _col("forecast_cost_total", FORECAST, "currency"), _col("open_projects", OPEN,
                                                                                         "integer")], "rows": rows}
    out = visualise(mixed, SNAP, "trend")
    assert out["visual"] == "table" and "one unit" in out["note"]
    same = {**mixed, "columns": [period, _col("forecast_cost_total", FORECAST, "currency"),
                                 _col("approved_budget_total", BUDGET, "currency")]}
    assert visualise(same, SNAP)["visual"] == "trend"
    by_region = {"columns": [REGION, *mixed["columns"][1:]], "rows": [["KSA", 1.0, 2], ["UAE", 3.0, 4]]}
    assert "paired_bars" not in visualise(by_region, SNAP)["alternatives"]


# --------------------------------------------------------------------------- Ask: rules planner and AG-03


@pytest.mark.parametrize(("question", "a", "b", "op"), [
    ("Forecast cost as % of approved budget by CIO", FORECAST, BUDGET, "percent_of"),
    ("forecast cost over approved budget % by region", FORECAST, BUDGET, "percent_change"),
    ("ratio of projects on hold to open projects by region", HOLD, OPEN, "ratio"),
    ("difference between forecast cost and approved budget by programme", FORECAST, BUDGET, "difference"),
    ("Forecast cost vs approved budget by region", FORECAST, BUDGET, None),
])
def test_rules_planner_reads_two_measures(question: str, a: str, b: str, op: str | None) -> None:
    out = plan(question)
    assert out.status == "ok", out.reason
    assert out.spec is not None
    assert out.spec["measure"]["ref"] == a and out.spec["extra_measures"] == [{"ref": b}]
    assert [d["op"] for d in out.spec.get("derived", [])] == ([op] if op else [])
    assert out.spec["breakdowns"], "the breakdown is still read"
    SpecValidator(SNAP).validate(spec(out.spec, SNAP))


def test_rules_planner_share_of_total() -> None:
    out = plan("open projects by region as a share of total")
    assert out.spec is not None and out.spec["share_of_total"] == [OPEN]
    assert any(c["kind"] == "share" for c in out.chips)
    assert "share_of_total" not in (plan("how many open projects as a share of total").spec or {})  # no breakdown


def test_one_measure_questions_are_unchanged() -> None:
    for q in ("open projects by region and priority", "projects on hold in KSA and Jordan",
              "Forecast variance (%) by CIO"):
        out = plan(q)
        assert out.spec is not None and "extra_measures" not in out.spec and "derived" not in out.spec, q
    assert measure_pair("open projects by region and priority", SNAP.measures) is None


def test_ag03_output_keeps_only_valid_extras() -> None:
    out = PlannerOut(measure="forecast_cost_total", breakdowns=["region"],
                     extra_measures=["approved_budget_total", "no_such_measure", "total_requests"],
                     derived=[{"op": "percent_of", "of": "forecast_cost_total", "to": "approved_budget_total"},
                              {"op": "ratio", "of": "forecast_cost_total", "to": "total_requests"},  # not kept
                              {"op": "sum", "of": "forecast_cost_total", "to": "approved_budget_total"}],
                     share_of_total=True)
    s = spec_from_planner(out, SNAP)
    assert s is not None
    assert s["extra_measures"] == [{"ref": BUDGET}]  # unknown and other-entity measures dropped
    assert s["derived"] == [{"key": "derived_1", "op": "percent_of", "of": FORECAST, "to": BUDGET}]
    assert s["share_of_total"] == [FORECAST]
    SpecValidator(SNAP).validate(spec(s, SNAP))
    lone = spec_from_planner(PlannerOut(measure="open_projects", share_of_total=True), SNAP)
    assert lone is not None and "share_of_total" not in lone  # nothing to share out
    units = spec_from_planner(PlannerOut(measure="forecast_cost_total", extra_measures=["open_projects"], derived=[
        {"op": "difference", "of": "forecast_cost_total", "to": "open_projects"},
        {"op": "ratio", "of": "measure:forecast_cost_total", "to": "measure:open_projects"}]), SNAP)
    assert units is not None and [d["op"] for d in units["derived"]] == ["ratio"]  # no difference across units



def test_refining_away_a_measure_drops_its_figures() -> None:
    """Removing or swapping a measure (Refine, follow-ups) takes its derived figures and share with it."""
    from tela_api.ask.refine import apply_ops
    from tela_api.ask.rewrite import _swap_measure

    base = {"measure": {"ref": FORECAST}, "extra_measures": [{"ref": BUDGET}], "breakdowns": [{"ref": "dimension:region"}],
            "derived": [{"key": "pct", "op": "percent_of", "of": FORECAST, "to": BUDGET}], "share_of_total": [BUDGET]}
    out, said = apply_ops(base, [{"op": "remove_measure", "ref": BUDGET}], SNAP)
    assert out["derived"] == [] and out["share_of_total"] == [] and said == ["Removed Approved budget (USD)"]
    SpecValidator(SNAP).validate(spec(out, SNAP))
    kept, _ = apply_ops(base, [{"op": "add_measure", "ref": OPEN}], SNAP)
    assert kept["derived"] == base["derived"] and kept["share_of_total"] == [BUDGET]
    measure = SNAP.measure("open_projects")
    assert measure is not None
    swapped = _swap_measure(base, measure, SNAP)
    assert "derived" not in swapped and "share_of_total" not in swapped
