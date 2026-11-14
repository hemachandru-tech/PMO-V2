"""Clarification options from linking and retrieval, offline (ENH-016, US-195; FEAT-066 / FEAT-019; no database).

When Ask asks, the right measure should be among the options: linking's candidates (words and meaning) and the
measures whose rule the question's date words describe, on the question's own table first. The US-190 set is scored
as the live suite asks it (``confidence.decide`` after the rules plan): whether the right measure was offered.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

import pytest
from test_paraphrase_set_unit import ORIGINALS, PARAPHRASES, lexical_hits
from test_planner_gold_unit import SNAP, plan

from tela_api.ask import confidence, meaning
from tela_api.ask.options import named_entities, past_date_measures, reading_measures
from tela_api.ask.planner import Plan
from tela_api.knowledge import scoring

DEPLOYMENT = "How many projects are past their deployment date?"
TIE_KEY = "measure:overdue_issues|overdue_risks"


def _option_measures(p: Plan) -> list[str]:
    assert p.question is not None
    return [o.get("measure") or s["measure"]["ref"] for o, s in zip(
        p.question["options"], p.question.get("readings") or [{}] * len(p.question["options"]), strict=True)]


@pytest.mark.parametrize("retrieval", [False, True])
def test_past_their_deployment_date_offers_overdue_go_lives(retrieval: bool) -> None:
    """EXEC-102 offered "Risks / Issues past their due date": a tie on words, about tables the question doesn't
    name. Now Overdue go-lives (its rule: planned deployment date before the reporting date) comes first."""
    meant = meaning.from_hits(lexical_hits(DEPLOYMENT)) if retrieval else None
    p = plan(DEPLOYMENT, meaning=meant)
    assert p.status == "clarify" and p.question is not None and p.question["key"] == TIE_KEY
    offered = _option_measures(p)
    assert offered[0] == "measure:overdue_go_lives"
    assert not {"measure:overdue_risks", "measure:overdue_issues"} & set(offered)
    assert all(SNAP.measure(r.split(":", 1)[1]).entity == "projects" for r in offered)  # type: ignore[union-attr]
    assert scoring.offered_right({"measure": {"ref": "measure:overdue_go_lives"}}, p.question) is True


def test_the_choice_answers_the_question_next_time() -> None:
    p = plan(DEPLOYMENT, memory={TIE_KEY: "measure:overdue_go_lives"})
    assert p.status == "ok" and p.spec is not None and p.spec["measure"] == {"ref": "measure:overdue_go_lives"}


def test_a_named_table_keeps_its_own_measures() -> None:
    p = plan("How many risks are past their due date?")
    assert p.status == "ok" and p.spec is not None and p.spec["measure"] == {"ref": "measure:overdue_risks"}
    assert past_date_measures("Risks past their due date", SNAP, SNAP.measures)[0].key == "overdue_risks"


def test_past_dates_read_the_rule_not_the_name() -> None:
    """A lateness word and a date named by its field's words or a dimension over it ("Go-live horizon"); counts
    before maxima and percentages for "how many", the broadest rule first."""
    assert [m.key for m in past_date_measures("How many projects have missed their go-live date?", SNAP,
                                              SNAP.measures)][:2] == ["overdue_go_lives", "overdue_over_90"]
    found = [m.key for m in past_date_measures(DEPLOYMENT, SNAP, SNAP.measures)]
    assert found[0] == "overdue_go_lives" and found[-1] == "north_star_on_time_tcv_pct"
    assert past_date_measures("Projects by deployment date", SNAP, SNAP.measures) == []  # nothing said to be past
    assert past_date_measures("How many projects are past due?", SNAP, SNAP.measures) == []  # no date named


def test_options_are_on_the_question_s_own_table() -> None:
    assert named_entities("Open projects and their risks", SNAP) == {"projects", "risks"}
    # Another table only when named ...
    assert reading_measures("Overdue risks and projects", SNAP, SNAP.measures,
                            ["overdue_risks", "overdue_issues", "open_projects"]) == ["overdue_risks", "open_projects"]
    # ... or to make two options; generic totals last; nothing named: the first candidate's table first.
    assert reading_measures("How many projects?", SNAP, SNAP.measures, ["total_projects", "overdue_risks"]) == [
        "total_projects", "overdue_risks"]
    assert reading_measures("Which are overdue?", SNAP, SNAP.measures, ["overdue_risks", "overdue_issues"]) == [
        "overdue_risks", "overdue_issues"]
    assert reading_measures("How many projects are open?", SNAP, SNAP.measures,
                            ["total_projects", "open_projects", "overdue_risks"],
                            exclude=["open_projects"]) == ["total_projects", "overdue_risks"]


def test_a_reading_of_an_unnamed_table_is_dropped() -> None:
    """A plan matched by meaning on another table ("live projects" → PCRs changing the go-live) is not offered
    while two readings of the named table remain."""
    p = Plan("ok", spec={"measure": {"ref": "measure:pcrs_pending"}, "breakdowns": [], "filters": []},
             confidence=confidence.COVERAGE_CAP, alternatives=["open_projects", "total_projects"])
    got = [s["measure"]["ref"] for s in confidence.readings(p, SNAP, SNAP.measures, "Live projects by region")]
    assert got == ["measure:open_projects", "measure:total_projects"]
    assert confidence.readings(p, SNAP, SNAP.measures)[0]["measure"]["ref"] == "measure:pcrs_pending"
    decided = confidence.decide(p, "Live projects by region", SNAP, SNAP.measures, None)
    assert decided.status == "clarify" and "measure:pcrs_pending" not in {
        s["measure"]["ref"] for s in decided.question["readings"]}  # type: ignore[index]


def _asked(examples: list[dict[str, Any]], retrieval: bool) -> Counter[str]:
    out: Counter[str] = Counter()
    for e in examples:
        q = e["question"]
        p = confidence.decide(plan(q, meaning=meaning.from_hits(lexical_hits(q)) if retrieval else None), q, SNAP,
                              SNAP.measures, None)
        out[p.status if p.status != "clarify" else
            "asked_right" if scoring.offered_right(e["spec"], p.question) else "asked_missing"] += 1
    return out


@pytest.mark.parametrize(("retrieval", "right", "missing"), [(False, 5, 3), (True, 12, 9)])
def test_paraphrases_asked_offer_the_right_measure(retrieval: bool, right: int, missing: int) -> None:
    """US-190 offline, as asked: before US-195 words alone 4 right / 3 missing, with the retrieval stand-in 5 / 16.
    The original set is never asked. US-213 (EXEC-140) moved two: "How much internal effort is requested per
    region?" was answered wrong and now asks (missing +1); "… look like they will miss their schedule?" asked only
    because "will" was unknown, and is now answered below the threshold with the unsure note (right -1, retrieval)."""
    orig, para = _asked(ORIGINALS, retrieval), _asked(PARAPHRASES, retrieval)
    print(f"\n{'retrieval' if retrieval else 'words'}: originals {dict(orig)} · paraphrases {dict(para)}")
    assert orig == Counter({"ok": len(ORIGINALS)})
    assert para["asked_right"] >= right and para["asked_missing"] <= missing
