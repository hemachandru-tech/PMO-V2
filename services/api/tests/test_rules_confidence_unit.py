"""Confidence and back-check on rules plans (ENH-016, US-193; FEAT-066 / FEAT-064), without a database.

Acceptance: the rules planner's plans get AG-03's back-translation and new signals — the margin over the next linked
measure, words of the question the plan doesn't use (a money word with a count, "percentage" with a count, an open
qualifier ignored, a breakdown or a number left out) and how much of the question it covers. A mismatch caps the score;
below the person's threshold the plan becomes one question with the top readings (US-182), or one reading answered with
a note. The EXEC-102 paraphrases that rules answered wrong at confidence 1.0 now ask; the original gold set is unchanged.
"""

from __future__ import annotations

from typing import Any

import pytest
from test_paraphrase_set_unit import EXAMPLES, ORIGINALS
from test_period_compare_unit import snapshot, spec  # noqa: F401 - pytest fixture
from test_planner_gold_unit import INDEX, KNOWN, SNAP, plan

from tela_api.ask import confidence
from tela_api.ask.linking import rank_measures
from tela_api.ask.planner import Plan
from tela_api.knowledge import scoring
from tela_api.knowledge.gold import spec_diff
from tela_api.knowledge.values import link
from tela_api.query.validator import SpecValidator
from tela_common.errors import TelaError
from tela_query_spec.model import ModelSnapshot

BY_QUESTION = {e["question"]: e for e in EXAMPLES}
# The EXEC-102 paraphrases the rules path answered with a wrong figure at confidence 1.0 offline before US-193.
EXEC102_CONFIDENT_WRONG = [
    "Split the live projects by geography",
    "How much money has been sanctioned for the open projects?",
    "Live projects split by urgency",
    "Which 5 regions have the most live projects?",
    "Pending requests by stage",
    "What percentage of projects have a budget code?",
    "What share of requests turn into projects, by quarter raised?",
    "Late governance approvals per request CIO",
    "What percentage of contract value was delivered on time?",
    "Which delivery managers have projects with no status reported?",
]
# Still answered with confidence: nothing in their words says the plan is wrong (EXEC102_CONFIDENT_WRONG_CEILING).
KNOWN_MISSES = ["Where are we over or under budget, by region?", "Which CIOs have their projects properly baselined?"]


def checked(question: str, verbosity: str | None = None) -> tuple[Plan, Plan]:
    """The rules plan with its US-193 checks, then Ask's decision at the person's threshold."""
    p = plan(question)
    linked = link(question, INDEX, known=KNOWN)
    confidence.check_rules(p, question, SNAP, SNAP.measures, SNAP.dimensions, linked.links,
                           rank_measures(question, SNAP, SNAP.measures))
    return p, confidence.decide(p, question, SNAP, SNAP.measures, verbosity)


def spec_of(**kw: Any) -> dict[str, Any]:
    return {"measure": {"ref": f"measure:{kw['measure']}"}, "breakdowns": [{"ref": f"dimension:{d}"}
                                                                        for d in kw.get("by", [])], "filters": []}


# --------------------------------------------------------------------------- the EXEC-102 cases


@pytest.mark.parametrize("question", EXEC102_CONFIDENT_WRONG)
def test_exec102_confident_wrong_cases_ask_or_answer_right(question: str) -> None:
    expected = BY_QUESTION[question]["spec"]
    p, decided = checked(question)
    if decided.status == "ok":
        assert not spec_diff(expected, decided.spec), (question, decided.spec)
        return
    assert decided.status == "clarify", (question, decided.status)
    assert p.confidence < confidence.threshold(None) and p.why, question
    assert len(decided.question["options"]) >= 2 and decided.question["kind"] == "reading"


def test_most_exec102_questions_offer_the_right_reading() -> None:
    """One click answers: the expected measure is among the readings for most of them (US-195 widens the options)."""
    offered = [q for q in EXEC102_CONFIDENT_WRONG
               if scoring.offered_right(BY_QUESTION[q]["spec"], checked(q)[1].question)]
    assert len(offered) >= 7, offered


@pytest.mark.parametrize("question", KNOWN_MISSES)
def test_known_misses_are_paraphrases_still_answered(question: str) -> None:
    p, decided = checked(question)
    assert decided.status == "ok" and p.confidence >= confidence.threshold(None)


def test_originals_are_unchanged_and_never_asked() -> None:
    """W4: no over-clarification. Every original gold question keeps its plan and stays above even "ask freely"
    except where its own plan already was below it."""
    asked = []
    for e in ORIGINALS:
        before = plan(e["question"])
        p, decided = checked(e["question"])
        if decided.status != "ok" or decided.spec != before.spec:
            asked.append(e["question"])
        assert p.confidence >= confidence.threshold(None), (e["question"], p.why)
    assert not asked


# --------------------------------------------------------------------------- signals


def test_a_money_word_with_a_count_is_a_mismatch() -> None:
    p, decided = checked("How much money has been sanctioned for the open projects?")
    assert p.spec["measure"]["ref"] == "measure:open_projects"
    assert any("“money” asks for an amount" in w for w in p.why)
    assert p.signals["back_check"] is False and p.confidence <= confidence.MISMATCH_CAP
    assert decided.status == "clarify"


def test_a_percentage_with_a_count_offers_the_percent_measure() -> None:
    p, decided = checked("What percentage of projects have a budget code?")
    assert any("“percentage” asks for a percentage" in w for w in p.why)
    refs = [r["measure"]["ref"] for r in decided.question["readings"]]
    assert refs[0] == p.spec["measure"]["ref"] and "measure:budget_code_compliance_pct" in refs


def test_an_ignored_open_qualifier_and_breakdown_are_named() -> None:
    p, _ = checked("Pending requests by stage")
    assert any("“pending” is not in the plan" in w for w in p.why)
    assert any("“by stage” is not in the plan" in w for w in p.why)


def test_a_number_the_plan_leaves_out_is_a_mismatch() -> None:
    p, _ = checked("Which 5 regions have the most live projects?")
    assert "“5” is not in the plan" in p.why


def test_a_threshold_or_a_ratio_is_not_a_percentage_ask() -> None:
    for q in ("Projects with forecast more than 15% over budget by portfolio",
              "Overdue go-lives as % of in delivery by region", "Ratio of projects on hold to open projects by region"):
        assert confidence.unused_words(q, plan(q).spec, SNAP) == [], q


def test_values_that_name_the_measure_or_a_plan_field_are_not_missing() -> None:
    """The shared back-check: "On Hold" names Projects on hold, "Deployment" the planned deployment date."""
    for q in ("Projects on hold by region", "Trend of projects by planned deployment month",
              "How many projects have insufficient data for calculated health?"):
        p = plan(q)
        back = confidence.back_translate(q, p.spec, SNAP, SNAP.measures, SNAP.dimensions,
                                         link(q, INDEX, known=KNOWN).links)
        assert back.ok, (q, back.missing)


def test_coverage_counts_the_words_the_plan_accounts_for() -> None:
    q = "Late governance approvals per request CIO"
    p = plan(q)
    covered, left = confidence.coverage(q, confidence.plan_words(p.spec, SNAP, p, question=q))
    assert covered < confidence.COVERAGE_LOW and {"governance", "approvals"} <= set(left)
    q = "Open projects by region"
    p = plan(q)
    assert confidence.coverage(q, confidence.plan_words(p.spec, SNAP, p, question=q)) == (1.0, [])


def test_margin_is_the_lead_over_the_next_linked_measure() -> None:
    spec = spec_of(measure="open_projects")
    assert confidence.margin(spec, [(0.8, "open_projects", "meaning"), (0.75, "total_projects", "words")]) == 0.05
    # A measure named outright, or none ranked, has no margin.
    assert confidence.margin(spec, [(1.1, "open_projects", "named"), (1.05, "total_projects", "words")]) is None
    assert confidence.margin(spec, []) is None


def test_a_near_tie_caps_below_ask_freely_only() -> None:
    p = Plan("ok", spec=spec_of(measure="open_projects", by=["region"]), confidence=1.0)
    confidence.check_rules(p, "Open projects by region", SNAP, SNAP.measures, SNAP.dimensions, [],
                           [(0.8, "open_projects", "words"), (0.75, "total_projects", "words")])
    assert p.confidence == confidence.MARGIN_CAP and "another measure matches nearly as well" in p.why
    assert confidence.threshold("ask_freely") > p.confidence >= confidence.threshold("balanced")
    assert p.alternatives == ["total_projects"]


def test_one_reading_is_answered_with_a_note() -> None:
    p = Plan("ok", spec=spec_of(measure="open_projects"), confidence=1.0)
    confidence.check_rules(p, "Open projects by geography", SNAP, SNAP.measures, SNAP.dimensions, [], [])
    assert p.confidence <= confidence.MISMATCH_CAP and not p.alternatives
    decided = confidence.decide(p, "Open projects by geography", SNAP, SNAP.measures, None)
    assert decided.status == "ok" and confidence.MSG_UNSURE in decided.interpreted


def test_free_hand_and_multi_step_plans_are_not_rechecked() -> None:
    p = Plan("ok", spec=spec_of(measure="open_projects"), freehand=True)
    confidence.check_rules(p, "Pending requests by stage", SNAP, SNAP.measures, SNAP.dimensions)
    assert p.confidence == 1.0 and p.signals is None
    q = "Portfolios with above-average slip"
    multi = plan(q)
    before = multi.confidence
    confidence.check_rules(multi, q, SNAP, SNAP.measures, SNAP.dimensions)
    assert multi.spec is None or not multi.spec.get("steps") or multi.confidence == before


def test_assume_first_still_answers() -> None:
    _, decided = checked("Pending requests by stage", "assume_first")
    assert decided.status == "ok"


def test_a_semi_join_two_links_away_is_refused_not_compiled(snapshot: ModelSnapshot) -> None:
    """A reading asked of another table can keep a semi-join the new measure reaches only through a third table; the
    validator refuses it (so ``_decide`` drops the reading) rather than the compiler failing on it."""
    far = next((m, x) for m in snapshot.measures for x in snapshot.measures
               if m.status == x.status == "certified" and len(snapshot.join_path(m.entity, x.entity) or []) == 2)
    with pytest.raises(TelaError, match="linked directly"):
        SpecValidator(snapshot).validate(spec({"measure": {"ref": f"measure:{far[0].key}"}, "exists_in": [
            {"entity": far[1].entity, "measure": f"measure:{far[1].key}"}]}, snapshot))


def test_a_compare_or_a_kind_word_inside_a_value_is_not_unused() -> None:
    for q in ("Open projects by quarter vs previous period", "Open projects on hold for no funding"):
        p = plan(q)
        assert confidence.unused_words(q, p.spec, SNAP) == [], q
