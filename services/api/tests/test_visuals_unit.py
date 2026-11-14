"""Heat grid, meter, ordinal columns and figure tone without a database (US-157; RULE-008).

Results are written by hand in the shape the query service returns; the snapshot is the PMO pack bound to the
synthetic fixture (as in the planner's offline suite).
"""

from __future__ import annotations

from typing import Any

import pytest
from test_planner_gold_unit import SNAP

from tela_api.content.common import guard_visual, visual_options
from tela_api.content.viewer import figure_tone
from tela_api.query.viz import visualise
from tela_common.errors import TelaError


def dim(key: str) -> dict[str, Any]:
    d = SNAP.dimension(key)
    assert d is not None
    return {"name": key, "kind": "dimension", "label": d.label, "ref": f"dimension:{key}", "data_type": "text"}


def meas(key: str) -> dict[str, Any]:
    m = SNAP.measure(key)
    assert m is not None
    return {"name": key, "kind": "measure", "label": m.label, "ref": f"measure:{key}", "data_type": "integer",
            "format": m.format}


REGION_BY_STATUS = {
    "columns": [{"name": "region", "kind": "dimension", "label": "Region", "ref": "dimension:region",
                 "data_type": "text"}, dim("reported_status"), meas("open_projects")],
    "rows": [[r, s, n] for r, k in (("North", 1), ("South", 2), ("East", 3))
             for s, n in (("Green", 4 * k), ("Amber", k), ("Red", 1), (None, 2))],
}


def test_heat_grid_puts_status_on_the_columns_with_tones() -> None:
    flipped = {"columns": [REGION_BY_STATUS["columns"][1], REGION_BY_STATUS["columns"][0],
                           REGION_BY_STATUS["columns"][2]],
               "rows": [[s, r, n] for r, s, n in REGION_BY_STATUS["rows"]]}
    viz = visualise(flipped, SNAP, "heat_grid")
    spec = viz["chart_spec"]
    assert viz["visual"] == "heat_grid" and viz["note"] is None
    assert (spec["x"], spec["y"], spec["ramp"]) == ("region", "reported_status", "rag")
    assert spec["tones"] == {"Green": "good", "Amber": "warning", "Red": "critical"}  # Not set stays neutral
    # Status stacks are still the default for category x status; the heat grid is a suitable alternative.
    default = visualise(REGION_BY_STATUS, SNAP)
    assert default["visual"] == "stacked_status" and "heat_grid" in default["alternatives"]
    assert default["chart_spec"]["tones"]["Red"] == "critical"


def test_heat_grid_refused_over_one_breakdown() -> None:
    one = {"columns": [dim("reported_status"), meas("open_projects")], "rows": [["Green", 3], ["Red", 1]]}
    with pytest.raises(TelaError) as err:
        guard_visual(one, SNAP, "heat_grid")
    assert err.value.details["reason"].startswith("Heat grid needs two breakdowns")
    assert "heat_grid" in {d["visual"] for d in visual_options(one, SNAP)["disabled"]}


def test_meter_figures_come_from_the_server() -> None:
    result = {"columns": [meas("budget_code_set"), meas("open_projects")], "rows": [[31, 111]]}
    viz = visualise(result, SNAP, "meter")
    m = viz["chart_spec"]["meter"]
    assert viz["visual"] == "meter"
    assert m == {"part": 31, "whole": 111, "percent": 27.9,
                 "note": f"{SNAP.measure('budget_code_set').label} out of {SNAP.measure('open_projects').label.lower()}"}
    assert visualise(result, SNAP, "meter", {"type": "meter", "note": "Open projects with a budget code"}
                     )["chart_spec"]["meter"]["note"] == "Open projects with a budget code"
    # A part bigger than its whole is not a meter; nor is a percent beside a count.
    upside = {**result, "rows": [[120, 111]]}
    assert visualise(upside, SNAP, "meter")["visual"] != "meter"
    mixed = {"columns": [meas("budget_code_compliance_pct"), meas("open_projects")], "rows": [[27.9, 111]]}
    assert "meter" in {d["visual"] for d in visualise(mixed, SNAP)["disabled"]}
    assert visualise({**result, "rows": [[0, 0]]}, SNAP, "meter")["chart_spec"]["meter"]["percent"] is None


def test_banded_breakdown_is_ordered_columns() -> None:
    d = SNAP.dimension("lateness_bucket")
    assert d is not None and d.bands
    result = {"columns": [dim("lateness_bucket"), meas("open_projects")],
              "rows": [[label, i + 1] for i, label in enumerate(d.labels()[:5])]}
    viz = visualise(result, SNAP)
    assert viz["visual"] == "columns" and viz["chart_spec"]["ramp"] == "ordinal"


@pytest.mark.parametrize(("value", "tone", "expected"), [
    (12, {"warning": 5, "critical": 10}, ("critical", "Critical at or above 10")),
    (10, {"warning": 5, "critical": 10}, ("critical", "Critical at or above 10")),
    (7, {"warning": 5, "critical": 10}, ("warning", "Warning at or above 5")),
    (4, {"warning": 5, "critical": 10}, ("good", "Good below 5")),
    (70, {"warning": 80, "critical": 60, "higher_is_worse": False}, ("warning", "Warning at or below 80")),
    (95.5, {"warning": 80, "higher_is_worse": False}, ("good", "Good above 80")),
    (3, {"critical": 2}, ("critical", "Critical at or above 2")),
])
def test_figure_tone(value: float, tone: dict[str, Any], expected: tuple[str, str]) -> None:
    out = figure_tone(value, tone)
    assert out is not None and (out["tone"], out["rule"]) == expected and out["label"] == expected[0].capitalize()


def test_no_threshold_no_tone() -> None:
    assert figure_tone(3, None) is None and figure_tone(3, {"higher_is_worse": True}) is None


def test_table_in_colour_carries_status_tones_and_is_table_only() -> None:
    """ENH-020: a table widget may be shown in colour; the API sends the tone of each status word."""
    from tela_api.content.common import stored_visual
    from tela_query_spec.spec import Visual

    plain = visualise(REGION_BY_STATUS, SNAP, "table", {"type": "table"})["chart_spec"]
    assert plain["type"] == "table" and plain["colour"] is False
    on = stored_visual({"type": "table"}, "table", colour=True, sent={"colour"})
    assert on == {"type": "table", "colour": True}
    spec = visualise(REGION_BY_STATUS, SNAP, "table", on)["chart_spec"]
    assert spec["colour"] is True and spec["tones"] == {"Green": "good", "Amber": "warning", "Red": "critical"}
    assert stored_visual(on, "table", colour=False, sent={"colour"}) == {"type": "table"}  # off clears it
    assert stored_visual(on, "ranked_bar") == {"type": "ranked_bar"}  # a new chart type drops it
    with pytest.raises(TelaError):
        stored_visual({"type": "donut"}, "donut", colour=True, sent={"colour"})
    with pytest.raises(ValueError):
        Visual(type="donut", colour=True)


def test_card_chart_colours_and_controls_belong_to_the_widget() -> None:
    """ENH-023: the card colour, chart colours and widget filters survive a new chart type when it can show them;
    the run's chart spec carries them only where they fit."""
    from tela_api.content.common import stored_visual
    from tela_query_spec.spec import ChartColours, Visual

    colours = ChartColours(scheme="warm", overrides={"KSA": "status-danger"})
    bars = stored_visual({"type": "ranked_bar"}, "ranked_bar", card="accent", colours=colours,
                         controls=["dimension:region"], sent={"card", "colours", "controls"})
    assert bars == {"type": "ranked_bar", "card": "accent", "controls": ["dimension:region"],
                    "colours": {"scheme": "warm", "overrides": {"KSA": "status-danger"}}}
    # A new chart type keeps them; a table has no series colours, so they go; a figure may take a filled card.
    assert stored_visual(bars, "columns")["colours"]["scheme"] == "warm"
    table = stored_visual(bars, "table")
    assert table == {"type": "table", "card": "accent", "controls": ["dimension:region"]}
    figure = stored_visual({"type": "figure"}, "figure", card="brand", sent={"card"})
    assert figure == {"type": "figure", "card": "brand"}
    assert "card" not in stored_visual(figure, "ranked_bar")  # brand fills only a figure's card
    assert stored_visual(figure, "figure", card="default", sent={"card"}) == {"type": "figure"}
    with pytest.raises(TelaError):
        stored_visual({"type": "donut"}, "donut", card="dark", sent={"card"})
    with pytest.raises(TelaError):
        stored_visual({"type": "figure"}, "figure", colours=colours, sent={"colours"})
    with pytest.raises(ValueError):
        Visual(type="donut", card="brand")
    with pytest.raises(ValueError):
        Visual(type="table", colours={"scheme": "warm"})  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        Visual(type="table", controls=["measure:open_projects"])
    with pytest.raises(ValueError):
        ChartColours(overrides={"KSA": "#ff0000"})  # type: ignore[dict-item]

    spec = visualise(REGION_BY_STATUS, SNAP, "stacked_status", {**bars, "type": "stacked_status"})["chart_spec"]
    assert spec["card"] == "accent" and spec["colours"] == {"scheme": "warm", "overrides": {"KSA": "status-danger"}}
    as_table = visualise(REGION_BY_STATUS, SNAP, "table", {**bars, "type": "table"})["chart_spec"]
    assert as_table["card"] == "accent" and as_table["colours"] is None
    plain = visualise(REGION_BY_STATUS, SNAP, "table", {"type": "table"})["chart_spec"]
    assert plain["card"] is None and plain["colours"] is None
