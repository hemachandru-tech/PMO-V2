"""US-259 / US-257b units: Direct SQL graded by rows, a run's mode applied to copies of the sources, follow-ups."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest

from tela_api.ask import modes
from tela_api.ask.frame import QuestionFrame
from tela_api.ask.orchestrator import direct_question
from tela_api.knowledge.gold import in_mode, run_mode_problem
from tela_api.knowledge.scoring import direct_match, frame_match, says_something


def test_a_figure_matches_whatever_its_type() -> None:
    assert direct_match([[100]], [[Decimal("100")]], shape="number")
    assert not direct_match([[100]], [[99]], shape="number")


def test_nothing_in_the_expected_rows_proves_nothing() -> None:
    assert not direct_match([], [], shape="number")
    assert not direct_match([[0]], [[0]], shape="number")
    assert not says_something([[None], [0]]) and says_something([["Apollo"]])


def test_breakdown_rows_compare_as_sets_of_values_in_any_column_order() -> None:
    expected = [["KSA", 4], ["UAE", 7]]
    assert direct_match(expected, [[7, "UAE"], [4, "KSA"]], shape="breakdown")
    assert not direct_match(expected, [["UAE", 7], ["KSA", 5]], shape="breakdown")
    assert not direct_match(expected, [["UAE", 7]], shape="breakdown")


def test_order_counts_only_when_the_spec_sorts_and_limits() -> None:
    expected = [["UAE", 7], ["KSA", 4]]
    assert direct_match(expected, [["KSA", 4], ["UAE", 7]], shape="ranking")
    assert not direct_match(expected, [["KSA", 4], ["UAE", 7]], shape="ranking", ordered=True)


def test_a_period_at_midnight_is_its_date() -> None:
    assert direct_match([[date(2026, 1, 1), 3], [date(2026, 2, 1), 5]],
                        [[datetime(2026, 1, 1), 3], [datetime(2026, 2, 1), 5]], shape="trend")


def test_a_list_is_the_same_records_whatever_columns_it_shows() -> None:
    # The spec lists records as a count by name: every name with its count, those counted 0 aren't listed.
    expected = [["Apollo", 1], ["Borealis", 1], ["Cygnus", 0]]
    assert direct_match(expected, [["Region 1", "Apollo"], ["Region 2", "Borealis"]], shape="list_of_records")
    assert not direct_match(expected, [["Apollo"], ["Borealis"], ["Cygnus"]], shape="list_of_records")
    assert not direct_match(expected, [["Apollo"]], shape="list_of_records")


def test_a_run_mode_must_be_built_and_direct_sql_needs_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    assert run_mode_problem("certified", True) is None and run_mode_problem("direct_sql", False) is None
    assert "plan-only" in (run_mode_problem("direct_sql", True) or "")
    monkeypatch.setattr(modes, "BUILT", frozenset({"certified"}))
    assert "isn't available yet" in (run_mode_problem("fields", False) or "")


def _src(engine: str, name: str) -> Any:
    return SimpleNamespace(connection={"engine": engine, "descriptor": {}, "name": name}, ask_mode="certified",
                           mode_note=None)


def test_a_run_mode_applies_to_copies_and_leaves_out_sources_that_cannot_answer() -> None:
    pg, mongo = _src("postgres", "TPG"), _src("mongodb", "Logs")
    got, why = in_mode({"a": pg, "b": mongo}, "direct_sql")
    assert got["a"].ask_mode == "direct_sql" and got["a"] is not pg and pg.ask_mode == "certified"
    assert "b" not in got and why["b"].startswith("Direct SQL can't answer on Logs")


def test_a_follow_up_carries_the_earlier_question_only_when_the_frame_says_so() -> None:
    refine = QuestionFrame(shape="list_of_records", follow_up="refine")
    assert "earlier question: “list projects”" in direct_question("only UAE", "list projects", refine)
    assert direct_question("how many projects", "list projects", QuestionFrame(shape="number")) == \
        "how many projects"
    assert direct_question("only UAE", None, refine) == "only UAE"


def test_a_fields_figure_or_list_is_graded_by_value_other_shapes_as_always() -> None:
    assert frame_match([[34]], [[34]], shape="number") and not frame_match([[34]], [[35]], shape="number")
    assert frame_match([["Apollo", 1], ["Cygnus", 0]], [["Apollo", "P-1", 1]], shape="list_of_records")
    assert not frame_match([["KSA", 4]], [["KSA", 4]], shape="breakdown")  # one row proves nothing, as before
    assert frame_match([["KSA", 4], ["UAE", 7]], [["UAE", 7], ["KSA", 4]], shape="breakdown")
