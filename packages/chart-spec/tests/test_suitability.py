"""RULE-008 decision table (business_rules §4.1)."""

from __future__ import annotations

from tela_chart_spec import REASONS, ResultShape, choose_visual, grid_axes, status_tone, suitability, to_chart_spec


def ok(shape: ResultShape) -> set[str]:
    return {s["visual"] for s in suitability(shape) if s["suitable"]}


def test_single_value_is_a_figure() -> None:
    shape = ResultShape(row_count=1, measures=["n"])
    assert ok(shape) == {"figure", "table"} and choose_visual(shape)[0] == "figure"


def test_category_shapes() -> None:
    six = ResultShape(row_count=6, categories=["region"], measures=["n"], distinct_counts={"region": 6})
    assert ok(six) >= {"ranked_bar", "donut", "table"} and "trend" not in ok(six)
    nine = ResultShape(row_count=9, categories=["region"], measures=["n"], distinct_counts={"region": 9})
    assert "donut" not in ok(nine)
    ratio = six.model_copy(update={"additive": False})
    assert "donut" not in ok(ratio)
    paired = ResultShape(row_count=4, categories=["p"], measures=["a", "b"], measure_formats=["currency", "currency"])
    assert choose_visual(paired)[0] == "paired_bars"


def test_dates_and_substitutions() -> None:
    months = ResultShape(row_count=6, dates=["period"], measures=["n"])
    assert choose_visual(months)[0] == "trend"
    two = ResultShape(row_count=2, dates=["period"], measures=["n"])
    assert choose_visual(two)[0] == "columns"
    visual, _, note = choose_visual(months, requested="donut")
    assert visual == "trend" and note is not None and note.startswith("Donut needs parts of a whole")


def test_status_stacks() -> None:
    shape = ResultShape(row_count=12, categories=["region", "status"], statuses=["status"], measures=["n"],
                        distinct_counts={"status": 3})
    assert "stacked_status" in ok(shape)


# --------------------------------------------------------------------------- US-157 heat grid, meter, ramps


def grid(rows: int = 5, cols: int = 3, status: bool = True) -> ResultShape:
    return ResultShape(row_count=rows * cols, categories=["region", "status"], statuses=["status"] if status else [],
                       measures=["n"], distinct_counts={"region": rows, "status": cols})


def test_heat_grid_needs_two_breakdowns_within_limits() -> None:
    assert "heat_grid" in ok(grid())
    assert "heat_grid" not in ok(ResultShape(row_count=6, categories=["region"], measures=["n"],
                                             distinct_counts={"region": 6}))
    assert "heat_grid" not in ok(grid(rows=26, cols=3))  # too many rows
    assert "heat_grid" in ok(grid(rows=5, cols=13, status=False))  # the smaller side goes on the columns
    assert "heat_grid" not in ok(grid(rows=20, cols=14, status=False))  # 14 columns even so
    two_measures = grid().model_copy(update={"measures": ["a", "b"], "measure_formats": ["integer", "integer"]})
    assert "heat_grid" not in ok(two_measures)
    reasons = {s["visual"]: s["reason"] for s in suitability(grid(rows=30))}
    assert reasons["heat_grid"] == REASONS["heat_grid"]


def test_heat_grid_axes_and_ramp() -> None:
    # A status breakdown goes on the columns with the RAG colours, whichever order it was asked in.
    flipped = grid().model_copy(update={"categories": ["status", "region"]})
    spec = to_chart_spec("heat_grid", flipped, {}, {})
    assert (spec.x, spec.y, spec.ramp) == ("region", "status", "rag")
    assert grid_axes(flipped) == ("region", "status")
    plain = ResultShape(row_count=20, categories=["portfolio", "priority"], measures=["n"],
                        distinct_counts={"portfolio": 10, "priority": 4})
    spec = to_chart_spec("heat_grid", plain, {}, {})
    assert (spec.x, spec.y, spec.ramp) == ("portfolio", "priority", "sequential")
    # Two plain breakdowns used to fall back to a table; the heat grid now carries them.
    assert choose_visual(plain)[0] == "heat_grid"
    # Status stacks stay the default for category x status; the heat grid is offered beside them.
    visual, alternatives, _ = choose_visual(grid())
    assert visual == "stacked_status" and "heat_grid" in alternatives


def test_meter_is_a_part_within_its_whole() -> None:
    meter = ResultShape(row_count=1, measures=["with_code", "open"], measure_formats=["integer", "integer"],
                        part_within_whole=True)
    # waffle: US-188; ranked bars: one bar per measure (EXEC-173)
    assert ok(meter) == {"meter", "waffle", "ranked_bar", "table"} and choose_visual(meter)[0] == "meter"
    assert "meter" not in ok(meter.model_copy(update={"part_within_whole": False}))
    assert "meter" not in ok(meter.model_copy(update={"measure_formats": ["percent", "percent"]}))
    assert "meter" not in ok(meter.model_copy(update={"measure_formats": ["integer", "currency"]}))
    visual, _, note = choose_visual(meter.model_copy(update={"part_within_whole": False}), requested="meter")
    assert visual == "table" and note is not None and note.startswith("Meter needs a part and its whole")


def test_one_row_of_measures_may_be_bars_one_per_measure() -> None:
    """EXEC-173: five costs and no breakdown, "as a bar": ranked bars with the measures as the bars, when asked."""
    costs = ResultShape(row_count=1, measures=[f"c{i}" for i in range(5)], measure_formats=["currency"] * 5)
    assert "ranked_bar" in ok(costs)
    assert choose_visual(costs)[0] == "table"  # never the default
    assert choose_visual(costs, requested="ranked_bar")[:3:2] == ("ranked_bar", None)
    chart = to_chart_spec("ranked_bar", costs, {}, {})
    assert chart.x is None and chart.series == costs.measures
    assert "ranked_bar" not in ok(costs.model_copy(update={"measure_formats": ["currency"] * 4 + ["integer"]}))
    assert "ranked_bar" not in ok(costs.model_copy(update={"measures": ["c0"], "measure_formats": ["currency"]}))
    seven = ResultShape(row_count=1, measures=[f"c{i}" for i in range(7)], measure_formats=["currency"] * 7)
    assert "ranked_bar" not in ok(seven)
    assert "ranked_bar" not in ok(costs.model_copy(update={"row_count": 3, "categories": ["region"]}))


def test_ordered_columns_get_the_ordinal_ramp() -> None:
    bands = ResultShape(row_count=5, categories=["days_late"], ordered=["days_late"], measures=["n"],
                        distinct_counts={"days_late": 5})
    assert choose_visual(bands)[0] == "columns"
    assert to_chart_spec("columns", bands, {}, {}).ramp == "ordinal"
    months = ResultShape(row_count=6, dates=["period"], measures=["n"])
    assert to_chart_spec("columns", months, {}, {}).ramp is None


def test_status_tones_are_words_not_colours() -> None:
    assert [status_tone(v) for v in ("Green", " amber ", "Yellow", "RED", "On track", None, "Not set", "Closed")] == [
        "good", "warning", "warning", "critical", "good", "neutral", "neutral", "neutral"]
