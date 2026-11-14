"""Voting, back-translation and confidence (US-182; FEAT-064 / FEAT-019), without a database.

Acceptance: AG-03's samples are grouped by meaning and the majority kept; the chosen spec in plain words is checked
against the question; one score (model, vote, linking, back-check) against the person's threshold decides between
answering and one question with the top 2-4 readings; the ambiguity library ("late projects", "cost", "active",
"pending change requests") feeds the rules planner and the glossary.
"""

from __future__ import annotations

from typing import Any

import pytest
from test_planner_gold_unit import KNOWN, SNAP

from tela_api.ask import confidence
from tela_api.ask.freehand import glossary_terms
from tela_api.ask.planner import TRAP_CONFIDENCE, LinkedValue, Plan, PlannerOut, Trap, plan_question, spec_from_planner
from tela_api.core.settings import get_settings
from tela_api.knowledge.gold import comparable
from tela_api.knowledge.guidance import glossary_chunks, load_glossary

TRAPS = [Trap(t.term.lower(), t.reading, tuple(t.instead_of)) for t in load_glossary("pmo_tpg") if t.reading]
OPEN_BY_REGION = {"measure": "measure:open_projects", "breakdowns": ["dimension:region"]}


def out(**kw: Any) -> PlannerOut:
    return PlannerOut.model_validate(kw)


def to_spec(o: PlannerOut) -> dict[str, Any] | None:
    return spec_from_planner(o, SNAP)


def rules(question: str, traps: list[Trap] | None = None, linked: list[LinkedValue] | None = None) -> Plan:
    return plan_question(question, SNAP, visible_measures=SNAP.measures, visible_dims=SNAP.dimensions, values={},
                         memory={}, traps=TRAPS if traps is None else traps, linked=linked)


# --------------------------------------------------------------------------- voting


def test_samples_that_mean_the_same_are_one_reading() -> None:
    a = out(measure="measure:open_projects", filters=[{"ref": "dimension:region", "op": "in", "values": ["KSA", "Jordan"]}])
    b = out(measure="open_projects", filters=[{"ref": "region", "op": "in", "values": ["Jordan", "KSA"]}])
    vote = confidence.tally([a, b, a], to_spec)
    assert len(vote.readings) == 1 and vote.agreement == 1.0 and vote.top.votes == 3
    assert comparable(to_spec(a) or {}) == comparable(to_spec(b) or {})


def test_the_majority_wins_and_disagreement_is_kept_as_readings() -> None:
    by_region = out(**OPEN_BY_REGION)
    by_programme = out(measure="measure:open_projects", breakdowns=["dimension:programme"])
    vote = confidence.tally([by_programme, by_region, by_region], to_spec)
    assert vote.top.out == by_region and vote.agreement == pytest.approx(2 / 3)
    assert vote.others() == [to_spec(by_programme)]
    # Ties keep sample order; "can't answer" is a reading too and can win.
    tie = confidence.tally([by_programme, by_region], to_spec)
    assert tie.top.out == by_programme and tie.agreement == 0.5
    none = out(unsupported=True, missing_data=["budget"])
    assert confidence.tally([none, by_region, none], to_spec).top.key == "unsupported"


def test_window_operations_count_in_the_meaning() -> None:
    plain = to_spec(out(**OPEN_BY_REGION)) or {}
    ranked = to_spec(out(**OPEN_BY_REGION, rank=True)) or {}
    assert ranked.get("rank") == {} and comparable(plain) != comparable(ranked)


# --------------------------------------------------------------------------- back-translation


def test_back_translation_passes_a_plan_that_answers_the_question() -> None:
    spec = to_spec(out(**OPEN_BY_REGION)) or {}
    check = confidence.back_translate("Open projects by region", spec, SNAP, SNAP.measures, SNAP.dimensions)
    assert check.ok and check.score == 1.0 and check.words == "Open projects by Region"
    # A question that names nothing the model knows can't count against the plan.
    assert confidence.back_translate("zorbly glimps", spec, SNAP, SNAP.measures, SNAP.dimensions).ok


def test_back_translation_finds_what_the_plan_gets_wrong() -> None:
    measures, dims = SNAP.measures, SNAP.dimensions
    no_breakdown = to_spec(out(measure="measure:open_projects")) or {}
    check = confidence.back_translate("Open projects by region", no_breakdown, SNAP, measures, dims)
    assert check.missing == ["the question names Region"] and check.score == 0.5
    other = to_spec(out(measure="measure:total_projects", breakdowns=["dimension:region"])) or {}
    assert confidence.back_translate("Open projects by region", other, SNAP, measures, dims).missing == [
        "the question names Open projects"]
    unsaid = to_spec(out(measure="measure:open_projects", filters=[{"ref": "dimension:region", "values": ["KSA"]}])) or {}
    assert confidence.back_translate("How many open projects?", unsaid, SNAP, measures, dims).missing == [
        "the plan filters on KSA, which the question doesn't say"]
    # Said through a synonym the value index linked ("Saudi" → KSA): not an added filter.
    saudi = [LinkedValue("region", "KSA", "KSA", "saudi", "synonym", 0.95)]
    assert confidence.back_translate("How many open projects in Saudi?", unsaid, SNAP, measures, dims, saudi).ok
    # A linked value the plan leaves out.
    assert confidence.back_translate("How many open projects in Saudi?", no_breakdown, SNAP, measures, dims,
                                     saudi).missing == ["the question names KSA"]


# --------------------------------------------------------------------------- one score and the threshold


def test_one_score_from_model_vote_linking_and_back_check() -> None:
    good = confidence.BackCheck("Open projects by Region")
    assert confidence.combine(model=0.9, agreement=1.0, linking=1.0, back=good) == pytest.approx(0.98)
    assert confidence.combine(model=None, agreement=1.0, linking=1.0, back=good) == pytest.approx(0.95)
    two_of_three = confidence.combine(model=None, agreement=2 / 3, linking=1.0, back=good)
    assert two_of_three == pytest.approx(0.2 * 0.75 + 0.4 * 2 / 3 + 0.15 + 0.25, abs=1e-3)
    assert confidence.threshold("ask_freely") < two_of_three  # EXEC-171: 2 of 3 answers at every level
    # No majority caps the score like a back-check mismatch.
    assert confidence.combine(model=0.9, agreement=1 / 3, linking=1.0, back=good) == confidence.MISMATCH_CAP
    # A mismatch caps the score: below balanced, above assume-first.
    bad = confidence.BackCheck("Open projects", ["the question names Region"])
    capped = confidence.combine(model=1.0, agreement=1.0, linking=1.0, back=bad)
    assert capped == confidence.MISMATCH_CAP
    assert confidence.threshold("assume_first") < capped < confidence.threshold("balanced")
    assert confidence.threshold(None) == confidence.threshold("balanced") < confidence.threshold("ask_freely")
    linked = [LinkedValue("region", "KSA", "KSA", "ksaa", "spelling", 0.7)]
    spec = to_spec(out(measure="measure:open_projects", filters=[{"ref": "dimension:region", "values": ["KSA"]}])) or {}
    assert confidence.linking_score(spec, linked) == 0.7 and confidence.linking_score(spec, []) == 1.0


def _unsure_plan() -> Plan:
    spec = to_spec(out(**OPEN_BY_REGION)) or {}
    other = to_spec(out(measure="measure:open_projects", breakdowns=["dimension:programme"])) or {}
    return Plan("ok", spec=spec, confidence=0.7, readings=[other], why=["the next measure is close"],
                measures_considered=["measure:open_projects"])


@pytest.mark.parametrize(("verbosity", "asks"), [("assume_first", False), ("balanced", False), ("ask_freely", True)])
def test_verbosity_moves_the_threshold(verbosity: str, asks: bool) -> None:
    decided = confidence.decide(_unsure_plan(), "open projects by area", SNAP, SNAP.measures, verbosity)
    assert (decided.status == "clarify") is asks


def test_thresholds_are_a_setting(monkeypatch: pytest.MonkeyPatch) -> None:
    """EXEC-171: 0.25 / 0.5 / 0.75 by default; one level can be tuned without the others."""
    assert [confidence.threshold(v) for v in ("assume_first", "balanced", "ask_freely")] == [0.25, 0.5, 0.75]
    monkeypatch.setenv("TELA_ASK_CONFIDENCE_THRESHOLDS", '{"balanced": 0.6}')
    get_settings.cache_clear()
    assert confidence.threshold(None) == 0.6 and confidence.threshold("ask_freely") == 0.75
    assert confidence.decide(_unsure_plan(), "open projects by area", SNAP, SNAP.measures, None).status == "ok"
    monkeypatch.setenv("TELA_ASK_CONFIDENCE_THRESHOLDS", '{"balanced": 0.75}')
    get_settings.cache_clear()
    assert confidence.decide(_unsure_plan(), "open projects by area", SNAP, SNAP.measures, None).status == "clarify"
    monkeypatch.delenv("TELA_ASK_CONFIDENCE_THRESHOLDS")
    get_settings.cache_clear()


def test_caps_stay_below_balanced_and_above_assume_first() -> None:
    """A plan a check flags still asks under "balanced" with the lower thresholds (EXEC-171)."""
    for cap in (confidence.MISMATCH_CAP, confidence.COVERAGE_CAP, confidence.WEAK_LINK_CAP, confidence.NO_EFFECT_CAP,
                confidence.REVIEW_CAP):
        assert confidence.threshold("assume_first") < cap < confidence.threshold("balanced")
    assert confidence.threshold("balanced") < confidence.MARGIN_CAP < confidence.threshold("ask_freely")


def test_below_the_threshold_one_question_with_the_readings() -> None:
    plan = _unsure_plan()
    plan.confidence = 0.4
    decided = confidence.decide(plan, "open projects by area", SNAP, SNAP.measures, "balanced")
    assert decided.status == "clarify" and decided.question is not None
    q = decided.question
    assert q["kind"] == "reading" and q["question"] == confidence.MSG_ASK and q["why"] == ["the next measure is close"]
    assert [o["label"] for o in q["options"]] == ["Open projects by Region", "Open projects by Programme"]
    assert [o["value"] for o in q["options"]] == ["reading:0", "reading:1"] and len(q["readings"]) == 2
    # The answer picks a stored reading; nothing the client sends becomes a spec.
    chosen = confidence.chosen({**q, "answer": "b"}, SNAP)
    assert chosen is not None and chosen.spec == q["readings"][1]
    assert chosen.interpreted == ["You chose: Open projects by Programme"]
    assert confidence.chosen({**q, "answer": "z"}, SNAP) is None and confidence.chosen(q, SNAP) is None
    assert confidence.chosen({"key": "money:cost", "options": [], "answer": "a"}, SNAP) is None


def test_one_reading_is_answered_with_a_note() -> None:
    plan = Plan("ok", spec=to_spec(out(**OPEN_BY_REGION)), confidence=0.3)
    decided = confidence.decide(plan, "open projects by region", SNAP, SNAP.measures, "balanced")
    assert decided.status == "ok" and decided.interpreted == [confidence.MSG_UNSURE]


def test_at_most_four_distinct_readings() -> None:
    spec = to_spec(out(measure="measure:open_projects")) or {}
    plan = Plan("ok", spec=spec, confidence=0.2, readings=[spec, spec],
                alternatives=["total_projects", "inactive_projects", "overdue_go_lives", "health_red", "nope"])
    specs = confidence.readings(plan, SNAP, SNAP.measures)
    assert [s["measure"]["ref"] for s in specs] == ["measure:open_projects", "measure:total_projects",
                                                    "measure:inactive_projects", "measure:overdue_go_lives"]


# --------------------------------------------------------------------------- ambiguity library


@pytest.mark.parametrize(("question", "reading", "before"), [
    ("How many late projects?", "overdue_go_lives", "total_projects"),
    ("Total cost by region", "forecast_cost_total", None),  # was: "which money measure?"
    ("Active requests", "open_demand", "total_requests"),
    ("Pending change requests", "pcrs_pending", "change_request_count"),
    ("How many active projects?", "open_projects", "open_projects"),
])
def test_traps_are_read_as_the_pack_says(question: str, reading: str, before: str | None) -> None:
    without = rules(question, traps=[])
    assert (without.spec or {}).get("measure", {}).get("ref") == (f"measure:{before}" if before else None)
    plan = rules(question)
    assert plan.status == "ok" and plan.spec is not None and plan.spec["measure"]["ref"] == f"measure:{reading}"
    assert plan.confidence == TRAP_CONFIDENCE and plan.alternatives
    assert plan.interpreted[0].startswith("Read “") and plan.spec["filters"] == []


@pytest.mark.parametrize("question", ["Late critical tasks", "Forecast cost: internal by region",
                                      "Estimated cost of requests", "Open projects by region"])
def test_a_measure_named_outright_is_not_a_trap(question: str) -> None:
    plan = rules(question)
    assert plan.confidence == 1.0 and plan.alternatives == [] and not plan.interpreted


def test_a_trap_asks_only_when_the_person_asks_freely() -> None:
    plan = rules("How many late projects?")
    assert confidence.decide(plan, "How many late projects?", SNAP, SNAP.measures, "balanced").status == "ok"
    asked = confidence.decide(rules("How many late projects?"), "How many late projects?", SNAP, SNAP.measures,
                              "ask_freely")
    assert asked.status == "clarify" and asked.question is not None
    assert [o["label"] for o in asked.question["options"]] == [
        "Overdue go-lives", "Deployed more than 60 days late", "Overdue more than 90 days", "Projects"]
    assert asked.question["why"] == ["“late projects” has more than one meaning"]


def test_a_spelling_link_lowers_the_rules_confidence() -> None:
    linked = [LinkedValue("region", "KSA", "KSA", "ksaa", "spelling", 0.7)]
    plan = plan_question("open projects in ksaa", SNAP, visible_measures=SNAP.measures, visible_dims=SNAP.dimensions,
                         values={"region": ["KSA"]}, memory={}, linked=linked)
    assert plan.spec is not None and plan.spec["filters"][0]["values"] == ["KSA"]
    assert plan.confidence == 0.7 and plan.why == ["“ksaa” was matched to KSA by spelling"]


def test_traps_reach_the_glossary_but_never_replace_words() -> None:
    chunks = glossary_chunks(load_glossary("pmo_tpg"), SNAP)
    traps = [c for c in chunks if c["metadata"].get("reading")]
    assert {c["metadata"]["term"] for c in traps} == {t.phrase for t in TRAPS} == {
        "late projects", "cost", "active projects", "active requests", "pending change requests"}
    late = next(c for c in traps if c["metadata"]["term"] == "late projects")
    assert late["content"].startswith("“late projects” is read as Overdue go-lives, not Deployed more than 60 days late")
    # Free-hand reads "X means: Y" lines as words to replace; a trap is not one ("cost" would become "forecast cost").
    assert glossary_terms([c["content"] for c in traps]) == {}
    assert "s2" in glossary_terms([c["content"] for c in chunks])
    assert KNOWN  # the gold fixtures loaded
