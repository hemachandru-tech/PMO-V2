"""Visual catalogue wave 1 without a database (US-187; RULE-008; COMP-001): the figures each visual needs are
worked out here, never in the browser. Results are written by hand in the shape the query service returns; the
snapshot is the PMO pack bound to the synthetic fixture."""

from __future__ import annotations

from typing import Any

import pytest
from test_planner_gold_unit import SNAP
from test_visuals_unit import dim, meas

from tela_api.ask.planner import VISUAL_WORDS
from tela_api.content.common import VISUAL_NAMES, default_size, visual_options
from tela_api.query.viz import visualise
from tela_chart_spec import VISUALS
from tela_query_spec.spec import Visual


def period(name: str, label: str) -> dict[str, Any]:
    return {"name": name, "kind": "period", "label": label, "data_type": "date"}


BUDGET_BY_PORTFOLIO = {
    "columns": [dim("portfolio"), meas("approved_budget_total"), meas("forecast_cost_total")],
    "rows": [["Digital", 100, 130], ["Infra", 80, 70], ["Data", 50, 65]],
}


def test_every_visual_is_named_and_stored() -> None:
    assert set(VISUAL_NAMES) == set(VISUALS)
    for v in VISUALS:
        Visual(type=v)  # type: ignore[arg-type]
    assert VISUAL_WORDS["waterfall"] == "waterfall" and VISUAL_WORDS["control chart"] == "xmr"


def test_waterfall_bridge_from_budget_to_forecast() -> None:
    viz = visualise(BUDGET_BY_PORTFOLIO, SNAP, "waterfall")
    f = viz["chart_spec"]["figures"]
    assert viz["visual"] == "waterfall" and f["mode"] == "bridge"
    steps = [(s["label"], s["kind"], s["value"], s["low"], s["high"]) for s in f["steps"]]
    assert steps[0][1:] == ("start", 230, 0, 230)
    assert steps[1:4] == [("Digital", "up", 30, 230, 260), ("Infra", "down", -10, 250, 260),
                          ("Data", "up", 15, 250, 265)]
    assert steps[-1][1:] == ("total", 265, 0, 265)  # = the forecast total
    assert f["table"]["rows"][-1][1:] == [265, 265]


def test_waterfall_builds_a_total_from_parts() -> None:
    one = {"columns": BUDGET_BY_PORTFOLIO["columns"][:2], "rows": [r[:2] for r in BUDGET_BY_PORTFOLIO["rows"]]}
    f = visualise(one, SNAP, "waterfall")["chart_spec"]["figures"]
    assert f["mode"] == "build" and [s["high"] for s in f["steps"]] == [100, 180, 230, 230]


def test_dumbbell_gap_per_item() -> None:
    viz = visualise(BUDGET_BY_PORTFOLIO, SNAP, "dumbbell")
    assert viz["visual"] == "dumbbell" and viz["chart_spec"]["figures"] == {"gaps": [30, -10, 15], "order": [0, 2, 1]}


def test_pareto_cumulative_share_and_vital_few() -> None:
    res = {"columns": [dim("region"), meas("open_projects")],
           "rows": [["North", 10], ["South", 50], ["East", 25], ["West", 10], [None, 5]]}
    viz = visualise(res, SNAP, "pareto")
    f = viz["chart_spec"]["figures"]
    assert viz["visual"] == "pareto"
    assert f["labels"] == ["South", "East", "North", "West", "Not set"]
    assert f["cumulative_pct"] == [50, 75, 85, 95, 100] and f["vital_few"] == 3


def test_xmr_limits_and_points_beyond() -> None:
    values = [10, 12, 11, 13, 12, 11, 12, 30]
    res = {"columns": [period("period", "Month"), meas("open_projects")],
           "rows": [[f"2026-{i + 1:02d}", v] for i, v in enumerate(values)]}
    viz = visualise(res, SNAP, "xmr")
    f = viz["chart_spec"]["figures"]
    assert viz["visual"] == "xmr" and viz["note"] is None
    mean, mr_bar = 111 / 8, 26 / 7  # moving ranges 2, 1, 2, 1, 1, 1, 18
    assert f["mean"] == round(mean, 2) and f["mr_bar"] == round(mr_bar, 2)
    assert f["upper"] == pytest.approx(mean + 2.66 * mr_bar, abs=0.01)
    assert f["lower"] == pytest.approx(mean - 2.66 * mr_bar, abs=0.01) and f["beyond"] == ["2026-08"]
    seven = {**res, "rows": res["rows"][:7]}
    assert visualise(seven, SNAP, "xmr")["visual"] == "trend"  # 8 periods or more


def test_bullet_bands_from_widget_tone_or_pack_target() -> None:
    res = {"columns": [dim("region"), meas("baselining_compliance_pct")],
           "rows": [["North", 95.0], ["South", 80.0], ["East", 60.0]]}
    tone = {"warning": 90, "critical": 75, "higher_is_worse": False}
    assert visualise(res, SNAP, "bullet")["visual"] != "bullet"  # no target, no bullet
    viz = visualise(res, SNAP, "bullet", target=tone)
    f = viz["chart_spec"]["figures"]
    assert viz["visual"] == "bullet"
    assert [r["band"] for r in f["rows"]] == ["good", "warning", "critical"]
    assert (f["target"], f["critical"], f["max"]) == (90, 75, 100)
    # The widget's own tone wins over the pack's.
    widget = visualise(res, SNAP, "bullet", {"type": "bullet", "tone": {"warning": 70, "higher_is_worse": False}},
                       target=tone)
    assert [r["band"] for r in widget["chart_spec"]["figures"]["rows"]] == ["good", "good", "warning"]


def test_funnel_from_stage_measures() -> None:
    cols = [meas("total_projects"), meas("open_projects"), meas("in_delivery"), meas("completed_projects")]
    res = {"columns": cols, "rows": [[200, 120, 90, 30]]}
    viz = visualise(res, SNAP, "funnel")
    stages = viz["chart_spec"]["figures"]["stages"]
    assert viz["visual"] == "funnel"
    assert [(s["value"], s["pct_of_first"], s["drop_pct"]) for s in stages] == [
        (200, 100, None), (120, 60, 40), (90, 45, 25), (30, 15, 66.7)]
    rising = {"columns": cols, "rows": [[200, 120, 130, 30]]}
    assert "funnel" not in visualise(rising, SNAP)["alternatives"]


def test_gantt_dates_and_unplanned_rows() -> None:
    res = {"columns": [dim("programme"),
                       period("start", "Planned start"), period("go_live", "Planned go-live"),
                       meas("total_projects")],
           "rows": [["A", "2026-01-01", "2026-06-30", 1], ["B", "2026-03-01", None, 1],
                    ["C", "2026-05-01", "2026-04-01", 1]]}
    viz = visualise(res, SNAP)
    assert viz["visual"] == "gantt"
    assert viz["chart_spec"]["figures"] == {"start": "start", "end": "go_live", "unplanned": 2}
    assert default_size("gantt") == (12, 5)


def test_small_multiples_share_one_axis() -> None:
    res = {"columns": [period("period", "Month"), dim("region"), meas("open_projects")],
           "rows": [[f"2026-0{m}", r, n * m] for m in (1, 2, 3) for r, n in (("North", 1), ("South", 3))]}
    viz = visualise(res, SNAP)
    spec = viz["chart_spec"]
    assert viz["visual"] == "small_multiples" and (spec["x"], spec["y"]) == ("period", "region")
    assert spec["figures"] == {"panels": ["South", "North"], "max": 9, "min": 0}


def test_options_offer_the_new_visuals() -> None:
    options = visual_options(BUDGET_BY_PORTFOLIO, SNAP)
    assert {"dumbbell", "waterfall"} <= set(options["suitable"]) and options["recommended"] == "paired_bars"
    xmr = next(d for d in options["disabled"] if d["visual"] == "xmr")
    assert xmr["label"] == "Control chart" and xmr["reason"].startswith("Control chart needs")


def test_chat_edit_reads_the_new_visual_words() -> None:
    from test_authoring_unit import state

    from tela_api.content.chat_edit import parse

    for words, visual in (("show open by region as a control chart", "xmr"),
                          ("turn open by state into a pareto", "pareto"),
                          ("show open by region as a gantt chart", "gantt"),
                          ("show open by state as small multiples", "small_multiples")):
        ops = parse(words, state(), SNAP.dimensions)[0]
        assert [o["to"] for o in ops if o["op"] == "set_visual"] == [visual], words
