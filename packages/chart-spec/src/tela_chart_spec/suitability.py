"""Visual suitability decision table (business_rules §4.1, RULE-008).

Only visuals that suit the result shape are offered; unsuitable ones carry the reason shown to the
user. A request for an unsuitable visual is substituted (donut on dates → columns, donut with more
than 7 slices → ranked bars, trend with fewer than 3 points → columns, figure over many rows → table).

US-157 adds the heat grid (two breakdowns by one measure; a status breakdown goes on the columns and takes the RAG
colours, anything else a single-hue ramp) and the meter (a part and its whole: "31 of 111 (28 %)"). Ordered
breakdowns on columns get the ordinal ramp. Colours are never the only signal: every mark carries its value.

US-176 adds paired measures and percentages. A derived figure or a share of the total is a *companion* of the
measure it is worked out from: it is shown as that series' label and in the table, never drawn as a bar of its
own, so the rows below count series only. Bars and lines share one axis, so several series need one unit (paired
bars, trend). A ratio or percentage on its own (no companion of a shown series) is not additive: no donut and no
stacked status bars; a share of the total is additive and may be a donut.

US-187 adds the PMO visual catalogue wave 1: dumbbell (two measures of one unit per item, the gap between them),
waterfall / bridge (parts building a total, or the change from one total to another by item), bullet (a figure
against its target band), Pareto (ranked bars with the cumulative share), XmR control chart (8+ periods with the
natural process limits), Gantt (an item with a start and an end date, ≤ 40 rows), funnel (stages that only fall)
and small multiples (one small trend per item, shared axis, ≤ 12 panels). The figures each one needs — running
totals, cumulative shares, limits, drop-offs, bands — are worked out by the API (``query/viz.py``), not the browser.

US-188 adds wave 2, for distributions, flows and sets: calendar heatmap (one figure per day over 3-24 months),
sankey (flows through 2-4 breakdowns), UpSet (which yes/no flags occur together, ≤ 6 sets), beeswarm (one dot per
item, ≤ 500), ridgeline / raincloud (the spread of items in 3-8 groups), time-to-event curve (Kaplan-Meier from a
start and an end date per item), bump chart (rank of ≤ 10 items over time), treemap (parts of a whole in one or two
levels) and waffle (100 cells: parts of a whole, or a part and its whole). "Item" is a breakdown with one row per
value. The estimates (densities, quartiles, survival, ranks, cells) are worked out by the API as well.

EXEC-173: one row of 2 to 6 measures in one unit and no breakdown ("internal, external … and software cost, as a
bar") may be ranked bars, one bar per measure; the browser turns the row into one row per measure. It is drawn only
when asked for: the default for such a row stays the table.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

Visual = Literal["figure", "ranked_bar", "columns", "trend", "paired_bars", "stacked_status", "donut", "heat_grid",
                 "meter", "dumbbell", "waterfall", "bullet", "pareto", "xmr", "gantt", "funnel", "small_multiples",
                 "calendar", "sankey", "upset", "beeswarm", "ridgeline", "survival", "bump", "treemap", "waffle",
                 "table"]
VISUALS: tuple[Visual, ...] = ("figure", "ranked_bar", "columns", "trend", "paired_bars", "stacked_status",
                               "donut", "heat_grid", "meter", "dumbbell", "waterfall", "bullet", "pareto", "xmr",
                               "gantt", "funnel", "small_multiples", "calendar", "sankey", "upset", "beeswarm",
                               "ridgeline", "survival", "bump", "treemap", "waffle", "table")
Ramp = Literal["rag", "sequential", "ordinal"]
Tone = Literal["good", "warning", "critical", "neutral"]
DONUT_MAX_SLICES = 7
RANKED_MAX = 25
STATUS_MAX = 6
GRID_MAX_ROWS = 25
GRID_MAX_COLUMNS = 12
WATERFALL_MAX = 12  # steps between the start and the total
PARETO_MIN = 3
XMR_MIN_PERIODS = 8
GANTT_MAX_ROWS = 40
FUNNEL_STAGES = (3, 8)
PANELS_MAX = 12
CALENDAR_MONTHS = (3, 24)
SANKEY_LEVELS = (2, 4)
SANKEY_MAX_ROWS = 100
SANKEY_MAX_NODES = 12  # values per breakdown
UPSET_SETS = (2, 6)
POINTS = (5, 500)  # beeswarm and ridgeline items
SWARM_GROUPS = 8
RIDGE_GROUPS = (3, 8)
RIDGE_MIN_PER_GROUP = 5
SURVIVAL = (10, 2000)
SURVIVAL_GROUPS = 6
BUMP_ITEMS = (2, 10)
BUMP_MIN_PERIODS = 3
TREEMAP_MAX_ROWS = 200
WAFFLE_MAX_PARTS = 6
MEASURE_BARS = (2, 6)  # EXEC-173: one bar per measure of a one-row result

REASONS = {
    "figure": "Figure needs a single value",
    "ranked_bar": "Ranked bars need one category and one measure, or one row of 2 to 6 measures in one unit",
    "columns": "Columns need a date or ordered breakdown",
    "trend": "Trend needs a date breakdown, and one unit for several lines",
    "paired_bars": "Paired bars need two measures with the same unit",
    "stacked_status": "Stacked status bars need a status breakdown",
    "donut": "Donut needs parts of a whole (≤ 7 slices, additive values)",
    "heat_grid": "Heat grid needs two breakdowns and one measure (≤ 25 rows, ≤ 12 columns)",
    "meter": "Meter needs a part and its whole: two counts, the first within the second",
    "dumbbell": "Dumbbell needs one category and two measures with the same unit (≤ 25 items)",
    "waterfall": "Waterfall needs one category and additive values (≤ 12 steps; two measures make a bridge)",
    "bullet": "Bullet needs one measure with a target (≤ 25 items)",
    "pareto": "Pareto needs one category and additive, non-negative values (3 to 25 items)",
    "xmr": "Control chart needs a date breakdown with 8 or more periods and one measure",
    "gantt": "Gantt needs one category with a start and an end date (≤ 40 rows)",
    "funnel": "Funnel needs 3 to 8 stages in order whose counts only fall",
    "small_multiples": "Small multiples need a date and one category with 2 to 12 items, and one measure",
    "calendar": "Calendar heatmap needs a day breakdown over 3 to 24 months and one measure",
    "sankey": "Sankey needs 2 to 4 breakdowns (≤ 12 values each) and additive, non-negative values",
    "upset": "UpSet needs 2 to 6 yes/no breakdowns and a count",
    "beeswarm": "Beeswarm needs one row per item (5 to 500) and one measure, in at most 8 groups",
    "ridgeline": "Ridgeline needs one row per item and a group with 3 to 8 values, 5 or more items each",
    "survival": "Time-to-event needs one row per item with a start and an end date (10 or more), in at most 6 groups",
    "bump": "Bump chart needs a date with 3 or more periods and one category with 2 to 10 items",
    "treemap": "Treemap needs one or two breakdowns and additive, non-negative values (≤ 200 rows)",
    "waffle": "Waffle needs parts of a whole (≤ 6 parts, additive values) or a part and its whole",
}

# Status words → tone (RAG colours with the word, never colour alone). Anything else, and "Not set", is neutral.
TONE_WORDS: dict[str, Tone] = {
    "green": "good", "on track": "good", "passed": "good", "ok": "good",
    "amber": "warning", "yellow": "warning", "at risk": "warning",
    "red": "critical", "off track": "critical", "failed": "critical",
}


def status_tone(value: object) -> Tone:
    return TONE_WORDS.get(str(value).strip().lower(), "neutral") if value is not None else "neutral"


class ResultShape(BaseModel):
    """What the decision table needs to know about a result."""

    row_count: int
    categories: list[str] = Field(default_factory=list)  # categorical dimension columns
    dates: list[str] = Field(default_factory=list)  # date / period columns
    statuses: list[str] = Field(default_factory=list)  # status dimension columns (also categorical)
    ordered: list[str] = Field(default_factory=list)  # categorical columns with a custom order
    measures: list[str] = Field(default_factory=list)
    measure_formats: list[str] = Field(default_factory=list)
    additive: bool = True
    non_negative: bool = True
    distinct_counts: dict[str, int] = Field(default_factory=dict)  # per dimension column
    part_within_whole: bool = False  # one row, two measures, 0 ≤ first ≤ second (the meter)
    # US-176: derived figure or share column → the measure column it accompanies (label, not a series)
    companions: dict[str, str] = Field(default_factory=dict)
    has_target: bool = False  # US-187: the measure has a target (the widget's tone or the pack's threshold)
    descending: bool = False  # US-187: stages that only fall (one row of measures, or an ordered breakdown)
    flags: list[str] = Field(default_factory=list)  # US-188: categorical columns holding yes/no values only
    daily: bool = False  # US-188: the date column holds days, not months or weeks
    months: int = 0  # US-188: calendar months the dates span (first to last, inclusive)

    @property
    def series(self) -> list[str]:
        return [m for m in self.measures if m not in self.companions]

    @property
    def series_formats(self) -> list[str]:
        return [f for m, f in zip(self.measures, self.measure_formats, strict=False) if m not in self.companions]

    def item_and_group(self) -> tuple[str | None, str | None]:
        """(item, group) of a per-item result (US-188): the item has one row per value; the group is the other
        breakdown, if any. (None, None) if no breakdown has one row per value."""
        items = [c for c in self.categories if self.distinct_counts.get(c) == self.row_count]
        if not items or len(self.categories) > 2:
            return None, None
        item = items[0]
        return item, next((c for c in self.categories if c != item), None)


def suitability(shape: ResultShape) -> list[dict[str, Any]]:
    cats = [c for c in shape.categories if c not in shape.statuses]
    n_dims = len(shape.categories) + len(shape.dates)
    n_meas = len(shape.series)
    units = set(shape.series_formats)
    first_cat = shape.categories[0] if shape.categories else None
    out = []
    for v in VISUALS:
        ok = True
        if v == "figure":
            ok = shape.row_count == 1 and n_meas == 1 and n_dims == 0
        elif v == "ranked_bar":
            ok = (len(shape.categories) == 1 and not shape.dates and n_meas == 1) or measure_bars(shape)
        elif v == "columns":
            ok = n_meas == 1 and ((len(shape.dates) == 1 and not shape.categories)
                                  or (len(shape.ordered) == 1 and n_dims == 1))
        elif v == "trend":
            ok = (len(shape.dates) == 1 and not shape.categories and 1 <= n_meas <= 4 and shape.row_count >= 3
                  and len(units) <= 1)
        elif v == "paired_bars":
            ok = len(shape.categories) == 1 and not shape.dates and n_meas == 2 and len(units) == 1
        elif v == "stacked_status":
            ok = (len(cats) == 1 and len(shape.statuses) == 1 and not shape.dates and n_meas == 1
                  and shape.additive and shape.distinct_counts.get(shape.statuses[0], 0) <= STATUS_MAX)
        elif v == "donut":
            ok = (len(shape.categories) == 1 and not shape.dates and n_meas == 1 and shape.additive
                  and shape.non_negative and first_cat is not None
                  and shape.distinct_counts.get(first_cat, shape.row_count) <= DONUT_MAX_SLICES)
        elif v == "heat_grid":
            ok = len(shape.categories) == 2 and not shape.dates and n_meas == 1 and shape.row_count >= 2
            if ok:
                rows_dim, cols_dim = grid_axes(shape)
                ok = (shape.distinct_counts.get(rows_dim, 0) <= GRID_MAX_ROWS
                      and shape.distinct_counts.get(cols_dim, 0) <= GRID_MAX_COLUMNS)
        elif v == "meter":
            ok = (shape.row_count == 1 and n_dims == 0 and len(shape.measures) == 2 and shape.part_within_whole
                  and len(set(shape.measure_formats)) == 1 and shape.measure_formats[0] in ("integer", "currency"))
        elif v == "dumbbell":
            ok = (len(shape.categories) == 1 and not shape.dates and n_meas == 2 and len(units) == 1
                  and shape.row_count <= RANKED_MAX)
        elif v == "waterfall":
            ok = (len(shape.categories) == 1 and not shape.dates and shape.additive and len(units) == 1
                  and n_meas in (1, 2) and 2 <= shape.row_count <= WATERFALL_MAX)
        elif v == "bullet":
            ok = (shape.has_target and n_meas == 1 and not shape.dates and len(shape.categories) <= 1
                  and (shape.row_count == 1 if not shape.categories else shape.row_count <= RANKED_MAX))
        elif v == "pareto":
            ok = (len(shape.categories) == 1 and not shape.ordered and not shape.dates and n_meas == 1
                  and shape.additive and shape.non_negative and PARETO_MIN <= shape.row_count <= RANKED_MAX)
        elif v == "xmr":
            ok = (len(shape.dates) == 1 and not shape.categories and n_meas == 1
                  and shape.row_count >= XMR_MIN_PERIODS)
        elif v == "gantt":
            ok = len(shape.categories) == 1 and len(shape.dates) == 2 and 1 <= shape.row_count <= GANTT_MAX_ROWS
        elif v == "funnel":
            lo, hi = FUNNEL_STAGES
            stages = (n_dims == 0 and shape.row_count == 1 and lo <= n_meas <= hi and len(units) == 1
                      and shape.series_formats[0] in ("integer", "currency"))
            ordered = (len(shape.ordered) == 1 and n_dims == 1 and n_meas == 1 and lo <= shape.row_count <= hi)
            # stages are never summed, so a distinct count may be a stage
            ok = (stages or ordered) and shape.non_negative and shape.descending
        elif v == "small_multiples":
            panel = shape.categories[0] if len(shape.categories) == 1 else None
            ok = (len(shape.dates) == 1 and panel is not None and n_meas == 1
                  and 2 <= shape.distinct_counts.get(panel, 0) <= PANELS_MAX)
        elif v in WAVE_2:
            ok = WAVE_2[v](shape, n_meas, len(units))
        out.append({"visual": v, "suitable": ok, "reason": None if ok or v == "table" else REASONS[v]})
    return out


def choose_visual(shape: ResultShape, requested: str | None = None) -> tuple[str, list[str], str | None]:
    """(visual, suitable alternatives, substitution note). A requested unsuitable visual is substituted."""
    table = {s["visual"]: s for s in suitability(shape)}
    suitable = [v for v in VISUALS if table[v]["suitable"]]
    note = None
    if requested and requested in table:
        if table[requested]["suitable"]:
            return requested, [v for v in suitable if v != requested], None
        note = f"{table[requested]['reason']} — showing {_default(shape, table)} instead."
    chosen = _default(shape, table)
    return chosen, [v for v in suitable if v != chosen], note


def _default(shape: ResultShape, table: dict[str, dict[str, Any]]) -> str:
    # US-188: an all-yes/no result reads as UpSet, flows through 3-4 breakdowns as a sankey (both were tables)
    sankey = "sankey" if len(shape.categories) >= 3 else None
    for v in ("figure", "meter", "trend", "columns", "paired_bars", "stacked_status", "upset", "heat_grid",
              "small_multiples", "gantt", "ranked_bar", sankey):
        if v is None or (v == "ranked_bar" and measure_bars(shape)):  # measures as bars only when asked for
            continue
        if table[v]["suitable"]:
            return v
    if shape.row_count == 1 and len(shape.series) == 1:
        return "figure"
    return "table"


class ChartSpec(BaseModel):
    """Renderer-neutral chart description; the web app maps it to ECharts."""

    type: Visual
    x: str | None = None
    series: list[str] = Field(default_factory=list)
    stack: str | None = None
    top_n: int | None = None
    labels: dict[str, str] = Field(default_factory=dict)
    formats: dict[str, str] = Field(default_factory=dict)
    y: str | None = None  # heat grid: the breakdown on the columns (x is on the rows)
    ramp: Ramp | None = None  # heat grid cells, ordered columns
    tones: dict[str, Tone] = Field(default_factory=dict)  # status value → tone, for RAG columns and status stacks
    meter: dict[str, Any] | None = None  # {part, whole, percent, note}, figures from the server (COMP-001)
    companions: dict[str, str] = Field(default_factory=dict)  # US-176: label column → the series it labels
    # US-187: figures the API works out for the visual (waterfall steps, Pareto shares, XmR limits, bullet bands,
    # funnel drop-offs, Gantt dates, panels), with ``table``: the same figures as a table for Excel and print.
    figures: dict[str, Any] | None = None
    colour: bool = False  # ENH-020: a table in colour (status words take ``tones``, figures a single-hue ramp)
    card: str | None = None  # ENH-023 US-223: the widget card's colour (a palette swatch name)
    colours: dict[str, Any] | None = None  # ENH-023 US-224: {scheme, overrides: {label: swatch}}


def measure_bars(shape: ResultShape) -> bool:
    """One row of 2 to 6 measures in one unit and no breakdown: one bar per measure (EXEC-173)."""
    lo, hi = MEASURE_BARS
    return (shape.row_count == 1 and not shape.categories and not shape.dates and lo <= len(shape.series) <= hi
            and len(set(shape.series_formats)) == 1)


def grid_axes(shape: ResultShape) -> tuple[str, str]:
    """(rows, columns) of a heat grid: a status breakdown goes on the columns, else the one with fewer values."""
    a, b = shape.categories[0], shape.categories[1]
    if b in shape.statuses or (a not in shape.statuses
                               and shape.distinct_counts.get(b, 0) <= shape.distinct_counts.get(a, 0)):
        return a, b
    return b, a


def to_chart_spec(visual: str, shape: ResultShape, labels: dict[str, str], formats: dict[str, str]) -> ChartSpec:
    x: str | None = (shape.dates or shape.categories or [None])[0]  # type: ignore[list-item]
    stack = y = None
    ramp: Ramp | None = None
    if visual == "stacked_status" and shape.statuses:
        stack = shape.statuses[0]
        x = next((c for c in shape.categories if c != stack), x)
    if visual == "heat_grid" and len(shape.categories) == 2:
        x, y = grid_axes(shape)
        ramp = "rag" if y in shape.statuses else "sequential"
    if visual == "columns" and not shape.dates and x in shape.ordered:
        ramp = "ordinal"
    if visual == "gantt":  # the item on the rows; the start and end dates are in the figures
        x = shape.categories[0]
    if visual in ("small_multiples", "bump"):  # the date along each panel or line, one panel or line per item
        y = shape.categories[0]
    if visual in ("beeswarm", "ridgeline", "survival"):  # x: the item; y: the group, if any
        item, group = shape.item_and_group()
        x, y = item or x, group
    if visual == "treemap" and len(shape.categories) == 2:  # x: the outer level (fewer values); y: the inner
        a, b = shape.categories
        x, y = (a, b) if shape.distinct_counts.get(a, 0) <= shape.distinct_counts.get(b, 0) else (b, a)
    top_n = RANKED_MAX if visual == "ranked_bar" and shape.row_count > RANKED_MAX else None
    series = shape.series if visual != "table" else list(shape.measures)
    companions = {c: m for c, m in shape.companions.items() if m in series} if visual != "table" else {}
    return ChartSpec(type=visual, x=x, series=series, stack=stack, top_n=top_n,  # type: ignore[arg-type]
                     labels=labels, formats=formats, y=y, ramp=ramp, companions=companions)


# --------------------------------------------------------------------------- US-188 wave 2 rows


def _calendar(shape: ResultShape, n_meas: int, units: int) -> bool:
    lo, hi = CALENDAR_MONTHS
    return len(shape.dates) == 1 and not shape.categories and n_meas == 1 and shape.daily and lo <= shape.months <= hi


def _sankey(shape: ResultShape, n_meas: int, units: int) -> bool:
    lo, hi = SANKEY_LEVELS
    return (lo <= len(shape.categories) <= hi and not shape.dates and n_meas == 1 and shape.additive
            and shape.non_negative and shape.row_count <= SANKEY_MAX_ROWS
            and all(shape.distinct_counts.get(c, 0) <= SANKEY_MAX_NODES for c in shape.categories))


def _upset(shape: ResultShape, n_meas: int, units: int) -> bool:
    lo, hi = UPSET_SETS
    return (lo <= len(shape.flags) <= hi and set(shape.flags) == set(shape.categories) and not shape.dates
            and n_meas == 1 and shape.additive and shape.non_negative)


def _per_item(shape: ResultShape, n_meas: int) -> tuple[str | None, str | None]:
    lo, hi = POINTS
    if shape.dates or n_meas != 1 or not lo <= shape.row_count <= hi:
        return None, None
    return shape.item_and_group()


def _beeswarm(shape: ResultShape, n_meas: int, units: int) -> bool:
    item, group = _per_item(shape, n_meas)
    return item is not None and (group is None or shape.distinct_counts.get(group, 0) <= SWARM_GROUPS)


def _ridgeline(shape: ResultShape, n_meas: int, units: int) -> bool:
    item, group = _per_item(shape, n_meas)
    lo, hi = RIDGE_GROUPS
    groups = shape.distinct_counts.get(group, 0) if group else 0
    return item is not None and lo <= groups <= hi and shape.row_count >= RIDGE_MIN_PER_GROUP * groups


def _survival(shape: ResultShape, n_meas: int, units: int) -> bool:
    lo, hi = SURVIVAL
    if len(shape.dates) != 2 or n_meas > 1 or not lo <= shape.row_count <= hi or not 1 <= len(shape.categories) <= 2:
        return False
    item, group = shape.item_and_group()
    return item is not None and (group is None or shape.distinct_counts.get(group, 0) <= SURVIVAL_GROUPS)


def _bump(shape: ResultShape, n_meas: int, units: int) -> bool:
    lo, hi = BUMP_ITEMS
    item = shape.categories[0] if len(shape.categories) == 1 else None
    return (len(shape.dates) == 1 and item is not None and n_meas == 1
            and lo <= shape.distinct_counts.get(item, 0) <= hi
            and shape.distinct_counts.get(shape.dates[0], 0) >= BUMP_MIN_PERIODS)


def _treemap(shape: ResultShape, n_meas: int, units: int) -> bool:
    return (1 <= len(shape.categories) <= 2 and not shape.dates and n_meas == 1 and shape.additive
            and shape.non_negative and 2 <= shape.row_count <= TREEMAP_MAX_ROWS)


def _waffle(shape: ResultShape, n_meas: int, units: int) -> bool:
    first = shape.categories[0] if len(shape.categories) == 1 else None
    parts = (first is not None and not shape.dates and n_meas == 1 and shape.additive and shape.non_negative
             and 2 <= shape.distinct_counts.get(first, 0) <= WAFFLE_MAX_PARTS)
    return parts or (shape.part_within_whole and len(shape.measures) == 2 and not shape.categories)


WAVE_2: dict[str, Any] = {"calendar": _calendar, "sankey": _sankey, "upset": _upset, "beeswarm": _beeswarm,
                          "ridgeline": _ridgeline, "survival": _survival, "bump": _bump, "treemap": _treemap,
                          "waffle": _waffle}
