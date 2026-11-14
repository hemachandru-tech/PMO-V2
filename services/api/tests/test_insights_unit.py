"""Insight engine (US-185; FEAT-065), no database server: each detector, the shape gate, ranking and grounding."""

from __future__ import annotations

from typing import Any

import pytest

from tela_api.ask import insights
from tela_api.ask.narrator import numbers_in

M = {"name": "m", "kind": "measure", "label": "Forecast cost", "format": "currency"}
N = {"name": "m", "kind": "measure", "label": "Open projects", "format": "integer"}
REGION = {"name": "region", "kind": "dimension", "label": "Region"}
MONTH = {"name": "month", "kind": "period", "label": "Month"}


def result(cols: list[dict[str, Any]], rows: list[list[Any]], **extra: Any) -> dict[str, Any]:
    return {"columns": cols, "rows": rows, "row_count": len(rows), "truncated": False, **extra}


def months(n: int) -> list[str]:
    return [f"2026-{i:02d}-01" for i in range(1, n + 1)]


def types(found: list[dict[str, Any]]) -> list[str]:
    return [f["type"] for f in found]


def grounded(f: dict[str, Any], kind: str | None) -> bool:
    allowed = insights.numbers_of(f, kind)
    return all(n in allowed or n.lstrip("-") in allowed for n in numbers_in(f["text"]))


# --------------------------------------------------------------------------- concentration


def test_concentration_top_share_and_pareto_point() -> None:
    items = [("KSA", 700.0), ("UAE", 150.0), ("Egypt", 50.0), ("Oman", 40.0), ("Qatar", 30.0), ("Bahrain", 30.0)]
    [f] = insights.concentration(items, "Forecast cost", "currency")
    assert f["numbers"]["top_share_pct"] == 90.0 and f["numbers"]["pareto_items"] == 2
    assert f["numbers"]["lead_share_pct"] == 70.0 and f["members"] == ["KSA", "UAE", "Egypt"]
    assert "2 of 6 make up 80%" in f["text"] and grounded(f, "currency")


def test_concentration_names_the_few_items_that_hold_everything() -> None:
    items = [("KSA", 2.0), ("UAE", 1.0), ("Egypt", 0.0), ("Oman", 0.0), ("Qatar", 0.0)]
    [f] = insights.concentration(items, "Overdue go-lives", "integer")
    assert f["text"] == "Only 2 of 5 have any overdue go-lives: KSA and UAE."
    assert f["members"] == ["KSA", "UAE"] and f["numbers"]["nonzero_items"] == 2 and grounded(f, "integer")


def test_concentration_skips_even_spread_negatives_and_short_lists() -> None:
    even = [(c, 100.0) for c in "ABCDEF"]
    assert insights.concentration(even, "X", None) == []
    assert insights.concentration([("A", 5.0), ("B", 1.0), ("C", 1.0)], "X", None) == []
    assert insights.concentration([("A", 50.0), ("B", -1.0), ("C", 1.0), ("D", 1.0)], "X", None) == []


# --------------------------------------------------------------------------- outliers


def test_outlier_robust_z_flags_the_extreme_item_only() -> None:
    items = [("A", 10.0), ("B", 11.0), ("C", 12.0), ("D", 10.0), ("E", 11.0), ("F", 95.0)]
    [f] = insights.outliers(items, "Overdue tasks", "integer")
    assert f["members"] == ["F"] and f["numbers"]["direction"] == "above" and f["numbers"]["z"] > 3
    assert f["numbers"]["median"] == 11.0 and grounded(f, "integer")


def test_outlier_needs_six_items_and_spread() -> None:
    assert insights.outliers([("A", 1.0), ("B", 1.0), ("C", 1.0), ("D", 1.0), ("E", 50.0)], "X", None) == []
    assert insights.outliers([(c, 4.0) for c in "ABCDEF"], "X", None) == []
    # MAD is zero but the mean absolute deviation is not: still found
    [f] = insights.outliers([("A", 4.0), ("B", 4.0), ("C", 4.0), ("D", 4.0), ("E", 4.0), ("F", 40.0)], "X", None)
    assert f["members"] == ["F"]


# --------------------------------------------------------------------------- change and trend


def test_change_names_both_periods_with_delta_and_percent() -> None:
    [f] = insights.change(120.0, 100.0, "2026-06-01", "2026-05-01", "Open projects", "integer")
    assert f["numbers"] == {"current": 120.0, "previous": 100.0, "delta": 20.0, "delta_pct": 20.0,
                            "period": "2026-06-01", "previous_period": "2026-05-01"}
    assert "up 20 (+20.0%) on 2026-05-01" in f["text"] and grounded(f, "integer")
    [down] = insights.change(80.0, 100.0, "June", "May", "Open projects", "integer")
    assert "down 20 (-20.0%)" in down["text"] and grounded(down, "integer")
    [zero] = insights.change(5.0, 0.0, "June", "May", "Open projects", "integer")
    assert zero["numbers"]["delta_pct"] is None


def test_trend_slope_sign_and_xmr_break() -> None:
    rising = list(zip(months(8), [10.0, 12, 13, 15, 16, 18, 19, 21], strict=True))
    found = insights.trend(rising, "Open projects", "integer")
    assert types(found) == ["trend"] and found[0]["numbers"]["direction"] == "rising"
    assert found[0]["numbers"]["r2"] > 0.9 and grounded(found[0], "integer")

    flat_then_spike = list(zip(months(8), [20.0, 21, 19, 20, 22, 20, 21, 60], strict=True))
    brk = [f for f in insights.trend(flat_then_spike, "Open projects", "integer") if f["type"] == "break"]
    assert brk and brk[0]["members"] == ["2026-08-01"] and brk[0]["numbers"]["direction"] == "above"
    assert grounded(brk[0], "integer")


def test_trend_run_of_eight_on_one_side_is_a_shift() -> None:
    values = [53.0, 47, 54, 46, 53, 47, 45, 47, 44, 46, 45, 47, 44, 46]  # noisy: no point past the limits
    found = insights.trend(list(zip([f"P{i:02d}" for i in range(14)], values, strict=True)), "X", None)
    [shift] = [f for f in found if f["type"] == "break"]
    assert shift["numbers"]["run"] == 8 and shift["numbers"]["direction"] == "below" and shift["members"] == ["P06"]
    assert grounded(shift, None)


def test_trend_needs_six_periods() -> None:
    assert insights.trend(list(zip(months(5), [1.0, 2, 3, 4, 5], strict=True)), "X", None) == []


# --------------------------------------------------------------------------- drivers


def test_driver_contribution_of_each_member_to_the_delta() -> None:
    before = {"KSA": 100.0, "UAE": 50.0, "Egypt": 50.0}
    now = {"KSA": 160.0, "UAE": 55.0, "Egypt": 45.0}
    [f] = insights.drivers(now, before, "June", "May", "Forecast cost", "currency")
    assert f["numbers"]["total_delta"] == 60.0 and f["members"][0] == "KSA"
    assert f["numbers"]["contributions"][0] == {"member": "KSA", "delta": 60.0, "share_pct": 100.0}
    assert grounded(f, "currency")
    assert insights.drivers({"A": 1.0, "B": 1.0}, {"A": 1.0, "B": 1.0}, "June", "May", "X", None) == []


# --------------------------------------------------------------------------- distribution and gap


def test_distribution_percentiles_spread_and_skew() -> None:
    items = [(f"P{i}", float(v)) for i, v in enumerate([5, 6, 7, 7, 8, 9, 10, 30, 45, 60])]
    [f] = insights.distribution(items, "Days late", "integer")
    assert f["numbers"]["p50"] == 8.5 and f["numbers"]["shape"] == "skewed high"
    assert f["numbers"]["p85"] == round(insights.quantile([v for _, v in items], 0.85), 2) == 39.75
    assert grounded(f, "integer")
    assert insights.distribution(items[:7], "Days late", "integer") == []


def test_gap_against_pack_threshold_both_directions() -> None:
    overdue = {"warning": 1, "critical": 10}
    [crit] = insights.gap(12, overdue, "Overdue go-lives", "integer")
    assert crit["numbers"]["band"] == "critical" and crit["priority"] == 0.9 and grounded(crit, "integer")
    [ok] = insights.gap(0, overdue, "Overdue go-lives", "integer")
    assert ok["numbers"]["band"] == "within target" and ok["priority"] < 0.2
    compliance = {"warning": 90, "critical": 75, "higher_is_worse": False}
    [warn] = insights.gap(82.5, compliance, "Baselining compliance", "percent")
    assert warn["numbers"]["band"] == "warning" and warn["numbers"]["gap"] == -7.5 and grounded(warn, "percent")


def test_members_past_threshold_in_a_breakdown() -> None:
    items = [("KSA", 12.0), ("UAE", 3.0), ("Egypt", 0.0), ("Oman", 0.0)]
    [f] = insights.members_past(items, {"warning": 1, "critical": 10}, "Overdue go-lives", "integer", "Region")
    assert f["text"] == ("Of 4 region values, 1 at the critical level of 10 and 1 at the warning level of 1 "
                         "for overdue go-lives.")
    assert f["numbers"]["critical_items"] == 1 and f["numbers"]["warning_items"] == 1
    assert f["members"] == ["KSA", "UAE"] and grounded(f, "integer")


# --------------------------------------------------------------------------- data note


def test_data_note_not_set_share_and_truncation() -> None:
    items = [("KSA", 60.0), ("UAE", 20.0), ("Not set", 20.0)]
    [f] = insights.data_note(items, "Open projects", "integer", True, "Region", False, 3)
    assert f["numbers"] == {"value": 20.0, "total": 100.0, "share_pct": 20.0} and grounded(f, "integer")
    [g] = insights.data_note(items, "Avg days", "decimal", False, "Region", False, 3)
    assert g["numbers"] == {} and "Not set" in g["text"]
    [t] = insights.data_note([("KSA", 1.0)], "X", None, True, "Region", True, 5000)
    assert t["numbers"] == {"rows": 5000} and grounded(t, None)


# --------------------------------------------------------------------------- the engine: shapes, ranking


def test_detect_breakdown_runs_breakdown_detectors_only() -> None:
    rows = [["KSA", 700.0], ["UAE", 150.0], ["Egypt", 50.0], ["Oman", 40.0], ["Qatar", 30.0], [None, 30.0]]
    found = insights.detect(result([REGION, M], rows), additive=True, agg="sum")
    assert set(types(found)) <= {"concentration", "outlier", "data_note"}
    assert "concentration" in types(found) and "data_note" in types(found)
    assert [f["id"] for f in found] == [f"f{i}" for i in range(1, len(found) + 1)]
    assert all("_about" not in f for f in found)
    assert [f["priority"] for f in found] == sorted((f["priority"] for f in found), reverse=True)
    assert all(grounded(f, "currency") for f in found)


def test_detect_restated_lead_is_dropped() -> None:
    # KSA is both the concentration lead and the outlier: one fact covers it
    rows = [["KSA", 900.0], ["UAE", 10.0], ["Egypt", 11.0], ["Oman", 12.0], ["Qatar", 10.0], ["Bahrain", 11.0]]
    found = insights.detect(result([REGION, M], rows), additive=True, agg="sum")
    assert sum(1 for f in found if "KSA" in f["members"][:1]) == 1


def test_detect_time_series_change_trend_and_note() -> None:
    rows = [[p, v] for p, v in zip(months(8), [10, 12, 13, 15, 16, 18, 19, 21], strict=True)]
    found = insights.detect(result([MONTH, N], rows), additive=True, agg="count")
    assert {"change", "trend"} <= set(types(found)) and "concentration" not in types(found)
    change = next(f for f in found if f["type"] == "change")
    assert change["numbers"]["period"] == "2026-08-01" and change["numbers"]["previous_period"] == "2026-07-01"


def test_detect_period_and_dimension_finds_the_driver() -> None:
    rows = [["2026-05-01", "KSA", 100], ["2026-05-01", "UAE", 50], ["2026-06-01", "KSA", 160],
            ["2026-06-01", "UAE", 52]]
    found = insights.detect(result([MONTH, REGION, N], rows), additive=True, agg="count")
    assert {"change", "driver"} <= set(types(found))
    driver = next(f for f in found if f["type"] == "driver")
    assert driver["members"][0] == "KSA" and driver["numbers"]["total_delta"] == 62
    # not additive: contributions don't add up, so no driver
    assert "driver" not in types(insights.detect(result([MONTH, REGION, N], rows), additive=False, agg="avg"))


def test_detect_single_value_gap_and_compare() -> None:
    res = result([N], [[12]])
    cmp = {"current": 12, "previous": 8, "previous_range": "1 May - 31 May 2026"}
    found = insights.detect(res, additive=True, agg="count", tone={"warning": 1, "critical": 10}, comparison=cmp)
    assert sorted(types(found)) == ["change", "gap"]
    change = next(f for f in found if f["type"] == "change")
    assert change["numbers"]["previous_period"] == "1 May - 31 May 2026" and change["numbers"]["delta_pct"] == 50.0


def test_detect_breakdown_with_previous_period_result_gives_drivers() -> None:
    now = result([REGION, N], [["KSA", 30], ["UAE", 10], ["Egypt", 9], ["Oman", 8]])
    prev = result([REGION, N], [["KSA", 10], ["UAE", 10], ["Egypt", 10], ["Oman", 8]])
    cmp = {"current": 57, "previous": 38, "previous_range": "May 2026"}
    found = insights.detect(now, additive=True, agg="count", comparison=cmp, previous=prev)
    driver = next(f for f in found if f["type"] == "driver")
    assert driver["members"][0] == "KSA" and driver["numbers"]["total_delta"] == 19


def test_detect_threshold_on_total_for_counts_and_per_member_for_shares() -> None:
    tone = {"warning": 90, "critical": 75, "higher_is_worse": False}
    pct = {**M, "label": "Baselining compliance", "format": "percent"}
    rows = [["KSA", 70.0], ["UAE", 95.0], ["Egypt", 88.0], ["Oman", 99.0]]
    found = insights.detect(result([REGION, pct], rows), additive=False, agg="avg", tone=tone, keep=10)
    [g] = [f for f in found if f["type"] == "gap"]
    assert g["numbers"]["critical_items"] == 1 and g["numbers"]["warning_items"] == 1
    counts = result([REGION, N], [["KSA", 8], ["UAE", 4], ["Egypt", 0], ["Oman", 0]])
    [t] = [f for f in insights.detect(counts, additive=True, agg="count", tone={"warning": 1, "critical": 10},
                                      keep=10) if f["type"] == "gap"]
    assert t["numbers"]["value"] == 12 and t["numbers"]["band"] == "critical"


def test_detect_distribution_only_for_non_additive_aggregates() -> None:
    rows = [[f"P{i}", float(v)] for i, v in enumerate([5, 6, 7, 7, 8, 9, 10, 30, 45, 60])]
    assert "distribution" in types(insights.detect(result([REGION, M], rows), additive=False, agg="median",
                                                   keep=10))
    assert "distribution" not in types(insights.detect(result([REGION, M], rows), additive=True, agg="sum",
                                                       keep=10))


@pytest.mark.parametrize("res", [
    result([REGION, M], []),
    result([REGION], [["KSA"]]),
    result([REGION, M], [["KSA", 1.0]], hidden=True),
])
def test_detect_nothing_without_measure_rows_or_when_hidden(res: dict[str, Any]) -> None:
    assert insights.detect(res, additive=True) == []


def test_rank_keeps_top_four() -> None:
    found = [insights._fact("x", p / 10, "t", {}, [], [("x", str(p))]) for p in range(1, 8)]
    kept = insights.rank(found)
    assert [f["priority"] for f in kept] == [0.7, 0.6, 0.5, 0.4] and [f["id"] for f in kept] == ["f1", "f2", "f3",
                                                                                                    "f4"]
