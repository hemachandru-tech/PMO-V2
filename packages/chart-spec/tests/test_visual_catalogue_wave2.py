"""RULE-008 rows for the visual catalogue wave 2 (US-188): each visual x the shapes it suits and the ones it
refuses, the defaults it takes and the axes it is drawn on."""

from __future__ import annotations

import pytest

from tela_chart_spec import REASONS, VISUALS, ResultShape, choose_visual, suitability, to_chart_spec

WAVE_2 = ("calendar", "sankey", "upset", "beeswarm", "ridgeline", "survival", "bump", "treemap", "waffle")


def ok(shape: ResultShape) -> set[str]:
    return {s["visual"] for s in suitability(shape) if s["suitable"]}


def days(months: int, **kw: object) -> ResultShape:
    return ResultShape(row_count=months * 30, dates=["day"], measures=["n"], measure_formats=["integer"],
                       daily=True, months=months, **kw)  # type: ignore[arg-type]


def flows(*counts: int, rows: int = 20, **kw: object) -> ResultShape:
    names = ["gate", "stage", "outcome", "region", "cio"][:len(counts)]
    return ResultShape(row_count=rows, categories=names, measures=["n"], measure_formats=["integer"],
                       distinct_counts=dict(zip(names, counts, strict=True)), **kw)  # type: ignore[arg-type]


def flags(n: int, **kw: object) -> ResultShape:
    names = [f"gap_{i}" for i in range(n)]
    return ResultShape(row_count=2 ** n, categories=names, flags=names, measures=["n"], measure_formats=["integer"],
                       distinct_counts=dict.fromkeys(names, 2), **kw)  # type: ignore[arg-type]


def items(n: int, groups: int = 0, **kw: object) -> ResultShape:
    cats = ["project", "portfolio"] if groups else ["project"]
    counts = {"project": n, **({"portfolio": groups} if groups else {})}
    return ResultShape(row_count=n, categories=cats, measures=["budget"], measure_formats=["currency"],
                       distinct_counts=counts, **kw)  # type: ignore[arg-type]


def spans(n: int, groups: int = 0, **kw: object) -> ResultShape:
    return items(n, groups).model_copy(update={"dates": ["start", "go_live"], "measures": [], "measure_formats": [],
                                               **kw})


def ranks(items_: int, periods: int) -> ResultShape:
    return ResultShape(row_count=items_ * periods, dates=["period"], categories=["region"], measures=["n"],
                       measure_formats=["integer"], distinct_counts={"region": items_, "period": periods})


def parts(n: int, **kw: object) -> ResultShape:
    return ResultShape(row_count=n, categories=["region"], measures=["n"], measure_formats=["integer"],
                       distinct_counts={"region": n}, **kw)  # type: ignore[arg-type]


def test_every_new_visual_has_a_reason() -> None:
    assert set(WAVE_2) <= set(VISUALS) and all(REASONS[v] for v in WAVE_2)
    refused = {s["visual"]: s["reason"] for s in suitability(ResultShape(row_count=0))}
    assert all(refused[v] == REASONS[v] for v in WAVE_2)


# (visual, shape, suitable)
CASES = [
    ("calendar", days(3), True),
    ("calendar", days(24), True),
    ("calendar", days(2), False),  # too short for a calendar: a trend reads better
    ("calendar", days(25), False),
    ("calendar", days(6).model_copy(update={"daily": False}), False),  # months, not days
    ("calendar", days(6).model_copy(update={"categories": ["region"]}), False),
    ("sankey", flows(3, 4), True),
    ("sankey", flows(3, 4, 3), True),  # requests → gates → outcome
    ("sankey", flows(3, 4, 3, 2), True),
    ("sankey", flows(3), False),  # one breakdown is not a flow
    ("sankey", flows(3, 4, 3, 2, 2), False),
    ("sankey", flows(3, 13), False),  # too many values on one level
    ("sankey", flows(3, 4, rows=101), False),
    ("sankey", flows(3, 4, additive=False), False),
    ("sankey", flows(3, 4, non_negative=False), False),
    ("upset", flags(3), True),
    ("upset", flags(6), True),
    ("upset", flags(7), False),
    ("upset", flags(1), False),
    ("upset", flags(3).model_copy(update={"categories": ["gap_0", "gap_1", "gap_2", "region"]}), False),  # not a set
    ("beeswarm", items(40), True),
    ("beeswarm", items(40, groups=4), True),
    ("beeswarm", items(40, groups=9), False),
    ("beeswarm", items(501), False),
    ("beeswarm", items(4), False),
    ("beeswarm", items(40).model_copy(update={"distinct_counts": {"project": 12}}), False),  # not one row per item
    ("ridgeline", items(40, groups=4), True),
    ("ridgeline", items(40, groups=2), False),  # too few groups: a beeswarm reads better
    ("ridgeline", items(80, groups=9), False),
    ("ridgeline", items(14, groups=3), False),  # fewer than 5 items per group
    ("ridgeline", items(40), False),
    ("survival", spans(30), True),
    ("survival", spans(30, groups=3), True),
    ("survival", spans(30, groups=7), False),
    ("survival", spans(9), False),
    ("survival", spans(30, dates=["start"]), False),
    ("bump", ranks(6, 4), True),
    ("bump", ranks(11, 4), False),
    ("bump", ranks(6, 2), False),
    ("bump", ranks(1, 6), False),
    ("treemap", parts(8), True),
    ("treemap", flows(3, 12, rows=30), True),  # portfolio → programme
    ("treemap", parts(8, additive=False), False),
    ("treemap", parts(8, non_negative=False), False),
    ("treemap", flows(3, 4, 3), False),
    ("treemap", parts(201), False),
    ("waffle", parts(3), True),
    ("waffle", parts(6), True),
    ("waffle", parts(7), False),
    ("waffle", parts(3, additive=False), False),
    ("waffle", ResultShape(row_count=1, measures=["set", "scope"], measure_formats=["integer"] * 2,
                           part_within_whole=True), True),  # a part and its whole
    ("waffle", ResultShape(row_count=1, measures=["a", "b"], measure_formats=["integer"] * 2), False),
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
    # Yes/no breakdowns with a count had no chart beyond a heat grid of two (and a table past two): UpSet.
    assert choose_visual(flags(2))[0] == "upset" and choose_visual(flags(4))[0] == "upset"
    # Three or four breakdowns were a table: a sankey.
    assert choose_visual(flows(3, 4, 3))[0] == "sankey"
    # Everything else keeps its default and offers the new visual beside it.
    visual, alternatives, _ = choose_visual(flows(3, 4))
    assert visual == "heat_grid" and {"sankey", "treemap"} <= set(alternatives)
    visual, alternatives, _ = choose_visual(days(6))
    assert visual == "trend" and "calendar" in alternatives
    visual, alternatives, _ = choose_visual(ranks(6, 4))
    assert visual == "small_multiples" and "bump" in alternatives
    visual, alternatives, _ = choose_visual(parts(5))
    assert visual == "ranked_bar" and {"treemap", "waffle", "beeswarm"} <= set(alternatives)
    assert choose_visual(spans(30))[0] == "gantt" and "survival" in choose_visual(spans(30))[1]
    assert choose_visual(items(40, groups=4))[0] == "table"
    meter = ResultShape(row_count=1, measures=["set", "scope"], measure_formats=["integer"] * 2,
                        part_within_whole=True)
    assert choose_visual(meter)[0] == "meter" and "waffle" in choose_visual(meter)[1]


def test_axes() -> None:
    spec = to_chart_spec("ridgeline", items(40, groups=4), {}, {})
    assert (spec.x, spec.y, spec.series) == ("project", "portfolio", ["budget"])
    assert (to_chart_spec("beeswarm", items(40), {}, {}).x, to_chart_spec("beeswarm", items(40), {}, {}).y) == (
        "project", None)
    assert to_chart_spec("survival", spans(30, groups=3), {}, {}).y == "portfolio"
    spec = to_chart_spec("bump", ranks(6, 4), {}, {})
    assert (spec.x, spec.y) == ("period", "region")
    spec = to_chart_spec("treemap", flows(3, 12, rows=30), {}, {})
    assert (spec.x, spec.y) == ("gate", "stage")  # the level with fewer values is the outer one
    assert to_chart_spec("calendar", days(6), {}, {}).x == "day"
