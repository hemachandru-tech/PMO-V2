"""RULE-008 rows for the visual catalogue wave 1 (US-187): each visual x the shapes it suits and the ones it
refuses, the defaults it takes and the axes it is drawn on."""

from __future__ import annotations

import pytest

from tela_chart_spec import REASONS, VISUALS, ResultShape, choose_visual, suitability, to_chart_spec

WAVE_1 = ("dumbbell", "waterfall", "bullet", "pareto", "xmr", "gantt", "funnel", "small_multiples")


def ok(shape: ResultShape) -> set[str]:
    return {s["visual"] for s in suitability(shape) if s["suitable"]}


def cats(n: int, measures: int = 1, fmt: str = "integer", **kw: object) -> ResultShape:
    return ResultShape(row_count=n, categories=["region"], measures=[f"m{i}" for i in range(measures)],
                       measure_formats=[fmt] * measures, distinct_counts={"region": n}, **kw)  # type: ignore[arg-type]


def months(n: int, **kw: object) -> ResultShape:
    return ResultShape(row_count=n, dates=["period"], measures=["n"], measure_formats=["integer"],
                       **kw)  # type: ignore[arg-type]


def test_every_new_visual_has_a_reason() -> None:
    assert set(WAVE_1) <= set(VISUALS) and all(REASONS[v] for v in WAVE_1)
    refused = {s["visual"]: s["reason"] for s in suitability(ResultShape(row_count=0))}
    assert all(refused[v] == REASONS[v] for v in WAVE_1)


# (visual, shape, suitable)
CASES = [
    ("dumbbell", cats(6, 2, "currency"), True),
    ("dumbbell", cats(6, 2).model_copy(update={"measure_formats": ["integer", "currency"]}), False),  # two units
    ("dumbbell", cats(26, 2), False),  # too many items
    ("dumbbell", cats(6, 1), False),
    ("waterfall", cats(5), True),  # parts build the total
    ("waterfall", cats(5, non_negative=False), True),  # variance by component may be negative
    ("waterfall", cats(5, 2, "currency"), True),  # bridge: budget → forecast by component
    ("waterfall", cats(13), False),
    ("waterfall", cats(5, additive=False), False),  # a ratio does not add up to a total
    ("waterfall", months(5), False),
    ("bullet", cats(6, has_target=True), True),
    ("bullet", ResultShape(row_count=1, measures=["pct"], measure_formats=["percent"], has_target=True), True),
    ("bullet", cats(6), False),  # no target
    ("bullet", cats(26, has_target=True), False),
    ("pareto", cats(8), True),
    ("pareto", cats(2), False),
    ("pareto", cats(8, non_negative=False), False),
    ("pareto", cats(8, additive=False), False),
    ("pareto", cats(8, ordered=["region"]), False),  # sorting would lose the order
    ("xmr", months(8), True),
    ("xmr", months(7), False),
    ("xmr", months(12).model_copy(update={"categories": ["region"]}), False),
    ("gantt", ResultShape(row_count=12, categories=["project"], dates=["start", "go_live"], measures=["n"]), True),
    ("gantt", ResultShape(row_count=41, categories=["project"], dates=["start", "go_live"], measures=["n"]), False),
    ("gantt", ResultShape(row_count=12, categories=["project"], dates=["start"], measures=["n"]), False),
    ("funnel", ResultShape(row_count=1, measures=["dou", "darb", "sow", "approved"], measure_formats=["integer"] * 4,
                           descending=True), True),
    ("funnel", ResultShape(row_count=1, measures=["dou", "darb", "sow", "approved"], measure_formats=["integer"] * 4,
                           descending=False), False),  # a stage that grows is not a funnel
    ("funnel", ResultShape(row_count=1, measures=["a", "b"], measure_formats=["integer"] * 2, descending=True), False),
    ("funnel", cats(5, ordered=["region"], descending=True), True),  # an ordered stage breakdown
    ("funnel", cats(5, descending=True), False),  # stages need an order
    ("funnel", ResultShape(row_count=1, measures=["a", "b", "c"], measure_formats=["integer"] * 3, descending=True,
                           additive=False), True),  # distinct counts: stages are never summed
    ("small_multiples", ResultShape(row_count=36, dates=["period"], categories=["region"], measures=["n"],
                                    distinct_counts={"region": 6}), True),
    ("small_multiples", ResultShape(row_count=78, dates=["period"], categories=["region"], measures=["n"],
                                    distinct_counts={"region": 13}), False),
    ("small_multiples", ResultShape(row_count=6, dates=["period"], categories=["region"], measures=["n"],
                                    distinct_counts={"region": 1}), False),
]


@pytest.mark.parametrize(("visual", "shape", "suitable"), CASES,
                         ids=[f"{v}-{i}-{'ok' if s else 'refused'}" for i, (v, _, s) in enumerate(CASES)])
def test_decision_table(visual: str, shape: ResultShape, suitable: bool) -> None:
    assert (visual in ok(shape)) is suitable
    chosen, _, note = choose_visual(shape, requested=visual)
    if suitable:
        assert chosen == visual and note is None
    else:
        assert chosen != visual and note is not None and note.startswith(REASONS[visual])


def test_defaults_fill_the_gaps_only() -> None:
    # Date x category had no chart (a table); it is now small multiples.
    panels = ResultShape(row_count=36, dates=["period"], categories=["region"], measures=["n"],
                         distinct_counts={"region": 6})
    assert choose_visual(panels)[0] == "small_multiples"
    # Item x start x end had none either: a Gantt.
    plan = ResultShape(row_count=12, categories=["project"], dates=["start", "go_live"], measures=["n"])
    assert choose_visual(plan)[0] == "gantt"
    # Everything else keeps its default and offers the new visual beside it.
    visual, alternatives, _ = choose_visual(months(12))
    assert visual == "trend" and "xmr" in alternatives
    visual, alternatives, _ = choose_visual(cats(6, 2, "currency"))
    assert visual == "paired_bars" and {"dumbbell", "waterfall"} <= set(alternatives)
    visual, alternatives, _ = choose_visual(cats(8, has_target=True))
    assert visual == "ranked_bar" and {"pareto", "bullet", "waterfall"} <= set(alternatives)
    one = ResultShape(row_count=1, measures=["pct"], measure_formats=["percent"], has_target=True)
    assert choose_visual(one)[0] == "figure" and "bullet" in choose_visual(one)[1]


def test_axes() -> None:
    plan = ResultShape(row_count=12, categories=["project"], dates=["start", "go_live"], measures=["n"])
    assert to_chart_spec("gantt", plan, {}, {}).x == "project"
    panels = ResultShape(row_count=36, dates=["period"], categories=["region"], measures=["n"],
                         distinct_counts={"region": 6})
    spec = to_chart_spec("small_multiples", panels, {}, {})
    assert (spec.x, spec.y, spec.series) == ("period", "region", ["n"])
    stages = ResultShape(row_count=1, measures=["dou", "darb", "sow"], measure_formats=["integer"] * 3,
                         descending=True)
    assert to_chart_spec("funnel", stages, {}, {}).x is None
