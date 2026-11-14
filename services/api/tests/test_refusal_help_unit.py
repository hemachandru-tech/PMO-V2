"""What a "couldn't match" answer offers (US-167; no database).

Uses the PMO pack bound to the synthetic TPG fixture, as ``test_planner_gold_unit.py`` does. Before US-167 the
suggestions were the first five measures of the model, so a model drafted from a Dataverse catalogue offered
"Number of action items, availability, business units, cios" for a question about projects.
"""

from __future__ import annotations

from test_planner_gold_unit import SNAP, plan

from tela_api.ask.planner import auto_count, refusal_help
from tela_query_spec.model import Measure, ModelSnapshot

PAST_PLAN = ("How many projects (any state) are past the Plan stage, meaning projects that have at least one stage "
             "planned end date and whose S2 plan planned end date is blank or before today?")


def _with_draft_counts() -> ModelSnapshot:
    """The pack plus the per-table row counts a model draft makes, listed first (alphabetical tables)."""
    counts = [Measure.model_validate({"key": f"{k}_count", "label": f"Number of {label}", "entity": "projects",
                                      "expression": {"agg": "count"}, "plain_rule": f"Counts every {label} record."})
              for k, label in (("action_item", "action items"), ("availability", "availability"),
                               ("business_unit", "business units"), ("cio", "cios"), ("projects_raw", "projects"))]
    return SNAP.model_copy(update={"measures": [*counts, *SNAP.measures]})


def test_the_problem_question_offers_past_planning_first() -> None:
    assert plan(PAST_PLAN).status == "unsupported"
    help_ = refusal_help(PAST_PLAN, SNAP, SNAP.measures, SNAP.dimensions)
    assert help_["examples"][0] == "Past planning"
    assert help_["did_you_mean"] == {"label": "Past planning", "measure": "measure:baselining_in_scope"}
    assert help_["understood"] == ["Projects"]
    assert {"meaning", "blank", "whose"} <= set(help_["unrecognised"]) and len(help_["unrecognised"]) <= 8
    assert "projects" not in help_["unrecognised"]


def test_draft_row_counts_never_come_before_pack_measures() -> None:
    snap = _with_draft_counts()
    assert [m.label for m in snap.measures[:4]] == ["Number of action items", "Number of availability",
                                                    "Number of business units", "Number of cios"]
    assert auto_count(snap.measures[0]) and not auto_count(SNAP.measure("baselining_in_scope"))  # type: ignore[arg-type]
    for question in (PAST_PLAN, "how many widgets are frobnicated"):
        examples = refusal_help(question, snap, snap.measures, snap.dimensions)["examples"]
        assert len(examples) == 5 and not [x for x in examples if x.startswith("Number of ")], (question, examples)


def test_suggestions_follow_the_table_the_question_names() -> None:
    help_ = refusal_help("requests waiting on discovery", SNAP, SNAP.measures, SNAP.dimensions)
    by_label = {m.label: m for m in SNAP.measures}
    assert all(by_label[x].entity == "project_requests" for x in help_["examples"]), help_["examples"]
    assert help_["understood"] == ["Project requests"]
    assert help_["did_you_mean"] is None  # "Requests" shares one word: a hint, not a meaning


def test_no_did_you_mean_without_a_close_name() -> None:
    help_ = refusal_help("how many widgets are frobnicated", SNAP, SNAP.measures, SNAP.dimensions)
    assert help_["did_you_mean"] is None and help_["understood"] == []
    assert help_["unrecognised"] == ["widgets", "frobnicated"]


def test_only_visible_measures_are_offered() -> None:
    visible = [m for m in SNAP.measures if m.key != "baselining_in_scope"]
    help_ = refusal_help(PAST_PLAN, SNAP, visible, SNAP.dimensions)
    assert "Past planning" not in help_["examples"] and help_["did_you_mean"] is None


def test_a_suggestion_answers_when_asked() -> None:
    """Each suggestion is sent back as the question, so it must plan to its own measure."""
    help_ = refusal_help(PAST_PLAN, SNAP, SNAP.measures, SNAP.dimensions)
    for label in help_["examples"]:
        p = plan(label)
        measure = SNAP.measure(p.spec["measure"]["ref"].split(":", 1)[1]) if p.status == "ok" and p.spec else None
        assert measure is not None and measure.label == label, (label, p.status, p.reason)
