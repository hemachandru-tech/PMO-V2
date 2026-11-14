"""A question that states a measure's own rule is that measure (US-202, DEC-24; no database).

Acceptance: with RULE-026 on the current state, EXEC-115's question — the open states listed in words, On Hold among
them though it also names the Projects on hold measure — plans as Open projects with no filter; states that narrow
the rule stay a filter; a model whose rule reads Status keeps the US-204 route. A left-out value is found among the
scoped values of the field, so the question names the field ("current state …"), as US-205's check needs.
"""

from __future__ import annotations

import pytest
from test_answer_sanity_unit import EXEC115
from test_planner_gold_unit import INDEX, KNOWN, RECORD_SNAP, SNAP, plan

from tela_api.ask import confidence, stated
from tela_api.ask.linking import rank_measures
from tela_api.knowledge.values import link

ONE_STATE = {"ref": "dimension:current_state", "op": "=", "values": ["Active"]}


def test_exec115_is_open_projects_with_no_filter() -> None:
    p = plan(EXEC115)
    assert p.status == "ok" and p.spec is not None
    assert p.spec["measure"]["ref"] == "measure:open_projects" and p.spec["filters"] == []
    assert "the current state listed is the rule of Open projects" in p.interpreted
    assert not [c for c in p.chips if c["kind"] == "filter"]


@pytest.mark.parametrize("question", [
    "open projects where the current state is Active, On Hold or Project Created",
    "How many open projects are in current state On Hold, Project Created or Active?",
])
def test_the_states_in_any_order_are_the_rule(question: str) -> None:
    p = plan(question)
    assert p.status == "ok" and p.spec is not None and p.spec["filters"] == []


@pytest.mark.parametrize(("question", "values"), [
    ("How many open projects (current state Project Created or Active) are there?", {"Active", "Project Created"}),
    ("How many open projects are active?", {"Active"}),
])
def test_states_that_narrow_the_rule_stay_a_filter(question: str, values: set[str]) -> None:
    p = plan(question)
    assert p.status == "ok" and p.spec is not None and p.spec["measure"]["ref"] == "measure:open_projects"
    states = [f for f in p.spec["filters"] if f["ref"] == "dimension:current_state"]
    assert {v for f in states for v in f["values"]} == values


def test_a_rule_on_another_field_is_not_restated() -> None:
    """Open projects counted by Status (pack 2.1.0): the listed states are criteria, for US-204's route."""
    measure = RECORD_SNAP.measure("open_projects")
    assert measure is not None
    spec = {"measure": {"ref": "measure:open_projects"}, "filters": [ONE_STATE]}
    assert stated.restated_rule(spec, EXEC115, RECORD_SNAP, measure, {}) is None
    p = plan(EXEC115, snap=RECORD_SNAP)
    assert p.spec is not None and p.spec["filters"] != []


def test_the_restated_filter_is_found_with_the_left_out_value() -> None:
    measure = SNAP.measure("open_projects")
    assert measure is not None
    two = {"ref": "dimension:current_state", "op": "in", "values": ["Active", "Project Created"]}
    spec = {"measure": {"ref": "measure:open_projects"}, "filters": [two]}
    states = {"current_state": ["Active", "Completed", "On Hold", "Project Created"]}
    assert stated.restated_rule(spec, EXEC115, SNAP, measure, states) is two
    assert stated.restated_rule(spec, EXEC115, SNAP, measure, {}) is None  # On Hold not listed: it narrows


def test_the_back_check_takes_the_stated_rule_as_said() -> None:
    """The rule's values (and their links to other fields: request state On Hold) are not "named but unused"."""
    p = plan(EXEC115)
    linked = link(EXEC115, INDEX, known=KNOWN)
    confidence.check_rules(p, EXEC115, SNAP, SNAP.measures, SNAP.dimensions, linked.links,
                           rank_measures(EXEC115, SNAP, SNAP.measures))
    assert p.confidence == 1.0 and p.why == []
    assert confidence.decide(p, EXEC115, SNAP, SNAP.measures, None).status == "ok"


def test_a_value_the_rule_leaves_out_is_still_named() -> None:
    """"open projects in Project Created" planned without its filter: one state narrows the rule, so it is named."""
    q = "How many open projects are in Project Created?"
    spec = {"measure": {"ref": "measure:open_projects"}, "breakdowns": [], "filters": []}
    linked = [v for v in link(q, INDEX, known=KNOWN).links if v.dimension == "current_state"]
    back = confidence.back_translate(q, spec, SNAP, SNAP.measures, SNAP.dimensions, linked)
    assert back.missing == ["the question names Project Created"]


def test_a_filter_its_measure_leaves_out_is_a_mismatch() -> None:
    """EXEC-115's misreading ("open project" singular): Projects on hold with current state Active or Project Created
    counts no project, so the back-check fails and Ask asks instead of answering 0 at full confidence."""
    q = "How many open project (current state Project Created, Active or On Hold) are there"
    spec = {"measure": {"ref": "measure:on_hold"}, "breakdowns": [], "filters": [
        {"ref": "dimension:current_state", "op": "in", "values": ["Active", "Project Created"]}]}
    back = confidence.back_translate(q, spec, SNAP, SNAP.measures, SNAP.dimensions, [])
    assert "Projects on hold leaves out Active, Project Created" in back.missing
    one = {**spec, "filters": [{"ref": "dimension:current_state", "op": "in", "values": ["On Hold", "Active"]}]}
    assert not [m for m in confidence.back_translate(q, one, SNAP, SNAP.measures, SNAP.dimensions, []).missing
                if "leaves out" in m]  # On Hold is kept: a narrowing, not a contradiction
