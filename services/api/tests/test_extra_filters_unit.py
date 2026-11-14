"""Words that match a stored value are not always a filter (BUG-025; no database).

Live (EXEC-174 diagnosis): "Calculated health of open projects … schedule 35% … cost 25% (estimate vs approved
budget) … Green, Amber, Red" was answered "Open projects: 0" — Risk category Schedule, PCR change type Cost, Closure
approval Approved, the three colours and "% complete" as Completed all became filters. Acceptance: a value of a table
with many rows per row of the measure's, or a yes/no value, filters only when the question names its field or table;
a value said inside another measure's label names that figure; every value of a field is no filter; a loose link adds
nothing to a field whose values the question lists; short and number words of synonyms and names link nothing. Values
the question means ("schedule risk", "approved change requests", "CAPEX", "reported Amber") still filter.
"""

from __future__ import annotations

import pytest
from test_planner_gold_unit import INDEX, KNOWN, plan

from tela_api.knowledge.values import link

HEALTH = ("Calculated health of open projects (current state Project Created, Active or On Hold) as a donut: Calculated "
          "health (PMO Intelligence model): schedule 35% (slip against the first baseline), cost 25% (estimate vs "
          "approved budget), progress 25% (% complete vs time elapsed), issues 15% (Issue KPI). A component with no "
          "data is left out; under 60% of the weight present = 'Insufficient data'. Score ≥ 75 Green, ≥ 50 Amber, "
          "else Red; a zero on schedule, cost or issues forces Red.")


def filters(question: str) -> dict[str, list[str]]:
    p = plan(question)
    assert p.status == "ok" and p.spec is not None, (p.status, p.reason, p.question)
    return {f["ref"].split(":", 1)[1]: f["values"] for f in p.spec["filters"]}


def test_a_formula_in_words_adds_no_filters() -> None:
    p = plan(HEALTH)
    assert p.status == "ok" and p.spec is not None
    assert p.spec["measure"]["ref"] == "measure:open_projects"
    assert p.spec["filters"] == [] and not p.spec.get("exists_in")
    assert "“schedule” was not read as Risk category: it is a value of risks, which the question doesn't name" \
        in p.interpreted
    assert any(line.endswith("are every value of Schedule KPI: not a filter") for line in p.interpreted)


@pytest.mark.parametrize(("question", "kept"), [
    ("Approved budget value and cost total of open CAPEX projects for each business unit/department",
     {"budget_type": ["CAPEX"]}),
    ("How many cost change requests are pending?", {}),
])  # "with no budget code" is now refused, not answered the opposite way (BUG-026, test_negation_unit)
def test_values_of_unnamed_fields_are_left_out(question: str, kept: dict[str, list[str]]) -> None:
    assert filters(question) == kept


@pytest.mark.parametrize(("question", "kept"), [
    ("How many open projects have a schedule risk?", {"risk_category": ["Schedule"]}),
    ("How many approved change requests?", {"request_state": ["Approved"]}),
    ("How many projects are reported Amber?", {"reported_status": ["Yellow"]}),
    ("How many CAPEX projects are there?", {"budget_type": ["CAPEX"]}),
])
def test_values_the_question_means_still_filter(question: str, kept: dict[str, list[str]]) -> None:
    assert filters(question) == kept


def test_a_listed_field_takes_no_loose_link() -> None:
    """"% complete" spelled near Completed is not added to the current states the question lists."""
    assert "Completed" not in filters("Open projects (current state Active or On Hold) by % complete").get(
        "current_state", [])


@pytest.mark.parametrize(("question", "word"), [
    ("A component with no data is left out", "out"),  # of the synonym "close out" (Closure)
    ("Go-lives one to two months out", "one"),  # of the name "Network One"
])
def test_short_and_number_words_link_nothing(question: str, word: str) -> None:
    assert word not in {v.phrase for v in link(question, INDEX, known=KNOWN).links}


def test_a_short_word_of_a_name_still_links() -> None:
    assert ("region", "GCC - UAE") in {(v.dimension, v.stored) for v in link("projects in uae", INDEX,
                                                                              known=KNOWN).links}


def test_live_shape_values_from_the_index_only() -> None:
    """Live (EXEC-174): the states and colours came from the value index, not the scoped lists, and a loose
    "complete" → Completed (one option in scope) rode along; Amber reached Yellow as a synonym."""
    from test_planner_gold_unit import SNAP

    from tela_api.ask.linking import rank_measures
    from tela_api.ask.planner import LinkedValue, plan_question

    linking = link(HEALTH, INDEX, known=KNOWN)
    loose = LinkedValue("current_state", "Completed", "Completed", "complete", "partial", 0.8)
    p = plan_question(HEALTH, SNAP, visible_measures=SNAP.measures, visible_dims=SNAP.dimensions,
                      values={"reported_status": ["Amber", "Green", "Red"]},
                      linked=[*linking.links, loose], choices=[],
                      linked_measures=rank_measures(HEALTH, SNAP, SNAP.measures))
    assert p.status == "ok" and p.spec is not None
    refs = {f["ref"]: f["values"] for f in p.spec["filters"]}
    assert "Completed" not in refs.get("dimension:current_state", [])
    assert "dimension:reported_status" not in refs
