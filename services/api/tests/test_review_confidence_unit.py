"""How AG-03's review of a rules plan moves its confidence (ENH-026, US-240; DEC-28)."""

from __future__ import annotations

from typing import Any

from test_planner_gold_unit import SNAP

from tela_api.ask import confidence
from tela_api.ask.planner import Plan

OVERDUE: dict[str, Any] = {"measure": {"ref": "measure:overdue_go_lives"}}
OPEN: dict[str, Any] = {"measure": {"ref": "measure:open_projects"}}


def plan(score: float) -> Plan:
    return Plan("ok", spec=dict(OVERDUE), confidence=score)


def test_confirm_lifts_one_step_never_to_certainty() -> None:
    p = plan(0.7)
    assert confidence.apply_review(p, "confirm", [], SNAP) == "confirm"
    assert p.confidence == 0.8 and p.signals == {"review": "confirm"} and p.readings == []
    high = plan(0.9)
    confidence.apply_review(high, "confirm", [], SNAP)
    assert high.confidence == 0.95
    sure = plan(1.0)  # a rules plan with no doubt keeps its score
    confidence.apply_review(sure, "confirm", [], SNAP)
    assert sure.confidence == 1.0


def test_a_different_reading_caps_and_is_offered() -> None:
    p = plan(1.0)
    assert confidence.apply_review(p, "correct", [OPEN], SNAP, "late may mean open") == "correct"
    assert p.confidence == confidence.REVIEW_CAP < confidence.threshold("balanced")
    assert p.readings == [OPEN] and p.spec == OVERDUE  # never swapped
    assert p.why == ["the planner read it differently"]
    assert p.interpreted == ["The planner read this as: Open projects"]
    assert p.signals == {"review": "correct", "review_reason": "late may mean open"}
    assert [confidence.describe(s, SNAP) for s in confidence.readings(p, SNAP, SNAP.measures)] == \
        ["Overdue go-lives", "Open projects"]


def test_the_same_meaning_counts_as_a_confirm() -> None:
    p = plan(0.7)
    assert confidence.apply_review(p, "correct", [dict(OVERDUE)], SNAP) == "confirm"
    assert p.confidence == 0.8 and p.readings == []


def test_ask_offers_the_options_without_a_note() -> None:
    p = plan(0.7)
    assert confidence.apply_review(p, "ask", [dict(OVERDUE), OPEN], SNAP) == "ask"
    assert p.confidence == confidence.REVIEW_CAP and p.readings == [OPEN] and p.interpreted == []
    assert p.why == ["the planner says it can be read more than one way"]


def test_fallback_only_records_the_signal() -> None:
    p = plan(0.7)
    assert confidence.apply_review(p, "fallback", [], SNAP, "the review took too long") == "fallback"
    assert p.confidence == 0.7 and p.signals == {"review": "fallback", "review_reason": "the review took too long"}


# --------------------------------------------------------------------------- AG-02 (US-241)

CLAR = confidence.clarification("How many late projects?", [OVERDUE, OPEN], SNAP, [])


def worded(question: str, *labels: str) -> dict[str, Any]:
    return confidence.reworded(CLAR, confidence.ClarifyWording(question=question, labels=list(labels)),
                               lambda said: "spend" not in said)


def test_reworded_labels_keep_ids_values_and_readings() -> None:
    out = worded("Which late projects?", "Go-lives past their date", "All open projects")
    assert out["question"] == "Which late projects?" and out["worded"] == "llm"
    assert [o["label"] for o in out["options"]] == ["Go-lives past their date", "All open projects"]
    assert [(o["id"], o["value"]) for o in out["options"]] == [(o["id"], o["value"]) for o in CLAR["options"]]
    assert out["readings"] is CLAR["readings"] and out["rules_labels"] == [o["label"] for o in CLAR["options"]]


def test_a_bad_label_or_question_keeps_the_rules_wording() -> None:
    rules = [o["label"] for o in CLAR["options"]]
    out = worded("", "Go-lives with actual spend", "x" * (confidence.MAX_LABEL + 1))
    assert out is CLAR  # nothing usable: unchanged
    same = worded("Which?", "Same words", "same words")  # two options must not read alike
    assert [o["label"] for o in same["options"]] == rules and same["question"] == "Which?"
    assert worded("Which?", "Only one") is CLAR
    model = Plan("ok", spec=dict(OVERDUE), confidence=0.8, why=[])
    confidence.model_mismatch(model, "the question asks about projects")
    assert model.confidence == confidence.MISMATCH_CAP and model.signals == {"back_check": False,
                                                         "back_check_model": "the question asks about projects"}
