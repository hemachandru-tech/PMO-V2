"""Questions that say "not", "no", "without", "excluding" … are answered that way round (BUG-026; no database).

Live (EXEC-174 review): "Count the projects (any state) with no budget code by CIO" answered "… has the most with a
budget code (45 of 86)" — the opposite. Acceptance: a value after a negation is left out (`!=` / `not_in`), and so
are the values listed after it; "other than" is not the value "Other"; a measure asked the other way round is its
base count with the rule's values left out when its rule is listed values on one field, and is otherwise not
answered (never the opposite); a measure whose own name holds the negation, and plain questions, are as before.
"""

from __future__ import annotations

import pytest
from test_planner_gold_unit import plan

from tela_api.ask.planner import negated_values


def spec_of(question: str) -> dict:
    p = plan(question)
    assert p.status == "ok" and p.spec is not None, (p.status, p.reason, p.question)
    return p.spec


def filters(question: str) -> list[tuple[str, str, list[str]]]:
    return [(f["ref"].split(":", 1)[1], f["op"], f["values"]) for f in spec_of(question)["filters"]]


@pytest.mark.parametrize(("question", "expected"), [
    ("How many projects are not in KSA?", [("region", "!=", ["KSA"])]),
    ("How many open projects excluding CAPEX?", [("budget_type", "!=", ["CAPEX"])]),
    ("How many non-CAPEX projects?", [("budget_type", "!=", ["CAPEX"])]),
    ("Open projects other than Active", [("current_state", "!=", ["Active"])]),
    ("Open projects apart from Active", [("current_state", "!=", ["Active"])]),
])
def test_a_value_after_a_negation_is_left_out(question: str, expected: list[tuple[str, str, list[str]]]) -> None:
    assert filters(question) == expected


def test_values_listed_after_a_negation_are_left_out_too() -> None:
    assert filters("How many projects are not in KSA or Jordan?") == [("region", "not_in", ["Jordan", "KSA"])]
    assert filters("How many projects in Jordan, excluding active?") == [
        ("region", "=", ["Jordan"]), ("current_state", "!=", ["Active"])]
    assert filters("How many projects are in KSA but not in Jordan?") == [
        ("region", "=", ["KSA"]), ("region", "!=", ["Jordan"])]


def test_negated_values_follow_the_list() -> None:
    text = "projects in ksa except active or on hold"
    spans = [(text.index("ksa"), text.index("ksa") + 3), (text.index("active"), text.index("active") + 6),
             (text.index("on hold"), text.index("on hold") + 7)]
    assert negated_values(text, spans) == [False, True, True]


def test_a_measure_asked_the_other_way_round_is_its_base_count_without_the_rule() -> None:
    p = plan("How many projects are not on hold?")
    assert p.status == "ok" and p.spec is not None
    assert p.spec["measure"]["ref"] == "measure:total_projects"
    assert p.spec["filters"] == [{"ref": "dimension:current_state", "op": "!=", "values": ["On Hold"]}]
    assert "Read “not on hold” as Projects with current state not On Hold" in p.interpreted
    assert {"kind": "filter", "label": "Current state: not On Hold"} in p.chips


@pytest.mark.parametrize("question", [
    "Count the projects (any state) with no budget code by CIO",
    "How many projects without a budget code?",
])
def test_an_opposite_the_rules_cannot_count_is_not_answered(question: str) -> None:
    """"Budget code is set" has no listed values to leave out: Ask says so instead of counting the opposite."""
    p = plan(question)
    assert p.status == "unsupported" and p.spec is None
    assert p.reason is not None and "“With a budget code” counts the opposite" in p.reason


@pytest.mark.parametrize(("question", "measure"), [
    ("How many projects are not yet started?", "project_created"),  # the measure's own name holds the negation
    ("Requests with no budget yet", "requests_no_budget"),
    ("How many projects in KSA?", "total_projects"),
])
def test_names_that_hold_a_negation_and_plain_questions_are_as_before(question: str, measure: str) -> None:
    spec = spec_of(question)
    assert spec["measure"]["ref"] == f"measure:{measure}"
    assert all(f["op"] in ("=", "in") for f in spec["filters"])
