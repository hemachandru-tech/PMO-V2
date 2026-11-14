"""Chart and table words in Ask (BUG-017; no database): a question asking for a table or a chart gets one, however
it is phrased ("show me a table of …", "bar chart of …", "… as a chart"), and words that only look like one don't.

Plans with the PMO pack bound to the synthetic TPG fixture, as ``test_planner_gold_unit`` does.
"""

from __future__ import annotations

import pytest
from test_planner_gold_unit import plan

from tela_api.ask.planner import norm, requested_visual


@pytest.mark.parametrize(("question", "visual"), [
    ("Show open projects by status as a table", "table"),
    ("open projects by status in table", "table"),
    ("Show me a table of open projects by status", "table"),
    ("Open projects by status table", "table"),
    ("Open projects by status in table format", "table"),
    ("Open projects by status in tabular form", "table"),
    ("Bar chart of open projects by status", "ranked_bar"),
    ("Show projects by status as a bar chart", "ranked_bar"),
    ("Pie chart of projects by status", "donut"),
    ("Give me a donut of projects by status", "donut"),
    ("Show open projects by status in a chart", "chart"),
    ("Give me a chart of projects by status", "chart"),
    ("Projects by manager graph", "chart"),
    ("How many open projects?", None),
    ("Which projects are in line with budget?", None),
    ("List open projects", None),
])
def test_the_visual_a_question_asks_for(question: str, visual: str | None) -> None:
    assert requested_visual(norm(question)) == visual


@pytest.mark.parametrize(("question", "visual"), [
    ("Show me a table of open projects by status", "table"),
    ("Open projects by status in table format", "table"),
    ("How many open projects as a table?", "table"),
    ("Bar chart of open projects by status", "ranked_bar"),
    ("Pie chart of open projects by status", "donut"),
])
def test_the_plan_carries_the_asked_visual(question: str, visual: str) -> None:
    p = plan(question)
    assert p.status == "ok" and p.spec is not None, (question, p.status, p.question)
    assert p.spec["visual"] == {"type": visual}
    assert p.spec["breakdowns"] or "by status" not in question


def test_any_chart_is_one_that_suits_the_answer() -> None:
    p = plan("Show open projects by status as a chart")
    assert p.status == "ok" and p.spec is not None
    assert p.spec["visual"]["type"] not in ("table", "figure")
    # One figure has nothing to chart: the answer stays a figure.
    p = plan("Show the number of open projects as a chart")
    assert p.status == "ok" and p.spec is not None and "visual" not in p.spec


@pytest.mark.parametrize(("follow_up", "visual"), [
    ("now as a table", "table"), ("show it in table format", "table"), ("make it a pie chart", "donut"),
    ("show this as a chart", "chart"),
])
def test_a_follow_up_changes_the_visual(follow_up: str, visual: str) -> None:
    from test_planner_gold_unit import SNAP

    from tela_api.ask.refine import parse_instruction

    spec = plan("Open projects by status").spec
    assert spec is not None
    ops = parse_instruction(follow_up, spec, SNAP, measures=SNAP.measures, dims=SNAP.dimensions, values={})
    set_visual = [o["value"] for o in ops if o["op"] == "set_visual"]
    assert len(set_visual) == 1
    assert set_visual[0] == visual if visual != "chart" else set_visual[0] not in ("table", "figure")
