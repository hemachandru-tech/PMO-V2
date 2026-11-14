"""Breakdown and shape words (US-194; no database): "why", "how long", "how late" and "when" add the breakdown a
dimension's ``answers`` role names, and "each month" / "per quarter" read a grain on the measure's own date.

Plans with the PMO pack bound to the synthetic TPG fixture, as ``test_planner_gold_unit`` does.
"""

from __future__ import annotations

from typing import Any

import pytest
from test_planner_gold_unit import M, SNAP, plan

from tela_api.ask.linking import catalogue_block
from tela_api.ask.planner import plan_question

D = "dimension:"


def spec(question: str) -> dict[str, Any]:
    p = plan(question)
    assert p.status == "ok" and p.spec is not None, (question, p.status, p.question)
    return p.spec


def breakdowns(question: str) -> list[str]:
    return [b["ref"] for b in spec(question)["breakdowns"]]


@pytest.mark.parametrize(("question", "measure", "dim"), [
    # The story's three acceptance questions.
    ("Why are projects on hold?", "on_hold", "hold_reason"),
    ("How long have on-hold projects been parked?", "on_hold", "on_hold_aging"),
    ("How late are the overdue go-lives?", "overdue_go_lives", "lateness_bucket"),
    # Of two "how late" bands, the one over the measure's own fields; of two "how long", the one the words name.
    ("How late were the deployed projects?", "deployed_projects", "schedule_variance_bucket"),
    ("How old is the open demand?", "open_demand", "request_aging"),
    ("How long have requests been on hold?", "total_requests", "request_on_hold_aging"),
    # The measure's own entity first: requests' and PCRs' own reasons, not the projects' hold reason.
    ("Why are requests on hold?", "total_requests", "request_hold_reason"),
    ("Why were PCRs raised?", "total_pcrs", "pcr_reason"),
    ("When are the open projects going live?", "open_projects", "go_live_horizon"),
])
def test_shape_words_add_their_breakdown(question: str, measure: str, dim: str) -> None:
    s = spec(question)
    assert s["measure"] == {"ref": M + measure}
    assert s["breakdowns"][0] == {"ref": D + dim}


def test_the_shape_is_said_in_the_answer() -> None:
    p = plan("Why are projects on hold?")
    assert "“why” read as a breakdown by hold reason" in p.interpreted
    assert any(c["kind"] == "breakdown" and c["label"] == "Hold reason" for c in p.chips)


def test_a_shape_breakdown_comes_first_and_keeps_the_named_one() -> None:
    assert breakdowns("Why are projects on hold by region?") == [D + "hold_reason", D + "region"]
    # Asked for by name already: not added twice.
    assert breakdowns("Why are projects on hold, by hold reason?") == [D + "hold_reason"]


def test_no_shape_no_breakdown() -> None:
    assert breakdowns("How many projects are on hold?") == []
    assert breakdowns("How many overdue go-lives?") == []
    # "when" only opens a question; mid-sentence it is not a horizon.
    assert breakdowns("How many projects slipped when they were in delivery?") == []


def test_a_shape_with_no_dimension_the_measure_reaches_adds_nothing() -> None:
    dims = [d for d in SNAP.dimensions if "why" not in d.answers]
    p = plan_question("Why are projects on hold?", SNAP, values={}, visible_measures=SNAP.measures,
                      visible_dims=dims)
    assert p.status == "ok" and p.spec is not None and p.spec["breakdowns"] == []


@pytest.mark.parametrize(("question", "measure", "date", "grain"), [
    ("Overdue go-lives each month", "overdue_go_lives", "planned_deployment_date", "month"),
    # "quarter" is the grain, not the requests' date "Quarter raised".
    ("Open projects per quarter", "open_projects", "planned_deployment_date", "quarter"),
    # Requests are read on their own date, not the projects' go-live.
    ("How many requests each month?", "total_requests", "quarter_raised", "month"),
    ("Hours logged per month", "hours_logged", "timesheet_entered", "month"),
])
def test_each_month_is_a_grain_on_the_measures_own_date(question: str, measure: str, date: str, grain: str) -> None:
    s = spec(question)
    assert s["measure"] == {"ref": M + measure}
    assert s["time"] == {"ref": D + date, "grain": grain}


def test_ag03_sees_the_shapes_a_dimension_answers() -> None:
    block = catalogue_block([], [d for d in SNAP.dimensions if d.key in ("hold_reason", "region")])
    assert "- dimension:hold_reason — Hold reason (answers 'why')" in block
    assert "- dimension:region — Region\n" in block + "\n"
