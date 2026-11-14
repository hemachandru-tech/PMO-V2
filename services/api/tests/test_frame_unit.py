"""US-253: the question frame — rules reading, LLM reading, their cross-check (shadow mode)."""

from __future__ import annotations

from typing import Any

import pytest
from conftest import FakeGateway

from tela_api.ask.frame import (
    FRAME_SYSTEM,
    QuestionFrame,
    build,
    combine,
    rules_frame,
)


def shape(q: str, **kw: Any) -> str:
    return rules_frame(q, **kw).shape


@pytest.mark.parametrize(("question", "expected"), [
    # the agent run 01a1ca5c question: a list, not one number
    ("Show the List of open Projects", "list_of_records"),
    ("list of open projects", "list_of_records"),
    ("Which projects are late?", "list_of_records"),
    ("names of projects with a high risk", "list_of_records"),
    ("show the open projects", "list_of_records"),
    ("How many open projects in UAE", "number"),
    ("list the number of open projects", "number"),
    ("total budget of open projects", "number"),
    ("how much did we spend in 2025", "number"),
    ("count of risks by severity and status", "breakdown"),
    ("show budget by region as a bar chart", "breakdown"),
    ("top 5 programmes by cost", "ranking"),
    ("which project has the highest budget", "ranking"),
    ("open projects by month", "trend"),
    ("monthly trend of closed projects", "trend"),
    ("compare open projects this year vs last year", "comparison"),
    ("share of projects that are late", "share"),
    ("distribution of project budgets", "distribution"),
    ("details of project Apollo", "record_detail"),
    ("what does open project mean", "definition"),
])
def test_rules_read_the_shape(question: str, expected: str) -> None:
    assert shape(question) == expected


def test_list_of_open_projects_keeps_every_word() -> None:
    f = rules_frame("Show the List of open Projects")
    assert f.subject == "projects" and [c.phrase for c in f.conditions] == ["open"]
    assert "list" in f.words_used and f.words_unused == []


def test_count_with_a_place_and_a_qualifier() -> None:
    f = rules_frame("How many open projects in UAE")
    assert f.shape == "number" and f.subject == "projects" and f.measure_phrase is None
    assert {(c.value or c.phrase) for c in f.conditions} == {"open", "uae"}
    assert not any(c.negated for c in f.conditions)


def test_measure_phrase_grouping_and_subject() -> None:
    f = rules_frame("total budget of open projects by region")
    assert (f.shape, f.measure_phrase, f.subject, f.groupings) == ("breakdown", "total budget", "projects",
                                                                   ["region"])


@pytest.mark.parametrize(("question", "values"), [
    ("projects not on hold", {"on hold"}),
    ("show projects excluding KSA or UAE", {"ksa", "uae"}),
    ("projects other than those in delivery", {"delivery"}),
    ("which projects are not on hold", {"on hold"}),
])
def test_negation(question: str, values: set[str]) -> None:
    f = rules_frame(question)
    negated = {c.value for c in f.conditions if c.negated}
    assert negated == values and all(c.op == "!=" for c in f.conditions if c.negated)


def test_numeric_conditions() -> None:
    f = rules_frame("projects with budget over 1000000")
    (c,) = f.conditions
    assert (c.field_hint, c.op, c.value) == ("budget", ">", "1000000")
    f = rules_frame("risks with score at least 15")
    assert [(c.op, c.value) for c in f.conditions] == [(">=", "15")] and f.order is None


def test_time_range_grain_and_date_field() -> None:
    t = rules_frame("projects closed last month").time
    assert t is not None and (t.range, t.field_hint, t.grain) == ("last_month", "closed", None)
    t = rules_frame("open projects by month").time
    assert t is not None and t.grain == "month"
    t = rules_frame("how much did we spend in 2025").time
    assert t is not None and t.range == "2025"


def test_order() -> None:
    o = rules_frame("top 5 programmes by cost").order
    assert o is not None and (o.by, o.dir, o.limit) == ("cost", "desc", 5)
    o = rules_frame("which project has the highest budget").order
    assert o is not None and (o.by, o.dir, o.limit) == ("budget", "desc", 1)
    o = rules_frame("bottom 3 regions by open risks").order
    assert o is not None and (o.dir, o.limit) == ("asc", 3)


def test_follow_up_only_with_a_previous_question() -> None:
    assert rules_frame("only UAE").follow_up == "none"
    assert rules_frame("only UAE", follow_up_possible=True).follow_up == "refine"
    assert rules_frame("as a chart", follow_up_possible=True).follow_up == "refine"
    assert rules_frame("what about risks?", follow_up_possible=True).follow_up == "replace"
    assert rules_frame("how many open projects", follow_up_possible=True).follow_up == "none"


def test_unread_words_are_listed_not_dropped() -> None:
    f = rules_frame("how many projects zork")
    assert f.words_unused == [] or "zork" in f.words_unused or f.subject == "zork"
    f = rules_frame("which projects are late quickly blorp")
    assert "projects" in f.words_used
    assert set(f.words_used) | set(f.words_unused) >= {"projects", "late", "quickly", "blorp"}


def test_known_subjects_pick_the_subject() -> None:
    f = rules_frame("details of project Apollo", subjects={"projects"})
    assert f.subject == "project" and "apollo" in f.words_unused


def test_visual_words() -> None:
    f = rules_frame("show budget by region as a bar chart")
    assert f.visual == "ranked_bar" and "chart" in f.words_used


def test_the_llm_prompt_names_no_catalogue_and_stays_small() -> None:
    assert len(FRAME_SYSTEM.split()) < 420  # ≈ 400 tokens with the schema left out
    assert "measure:" not in FRAME_SYSTEM and "dimension:" not in FRAME_SYSTEM


def _llm(**kw: Any) -> QuestionFrame:
    return QuestionFrame(**{"shape": "list_of_records", "subject": "projects", **kw})


def test_agreement_on_shape_and_conditions_is_confident() -> None:
    rules = rules_frame("list of open projects in UAE")
    llm = _llm(conditions=[{"phrase": "open"}, {"phrase": "in UAE", "op": "=", "value": "UAE"}])
    got = combine(rules, llm)
    assert got["confident"] and got["uncertain"] == []


def test_disagreement_marks_the_slot_uncertain() -> None:
    rules = rules_frame("Show the List of open Projects")
    got = combine(rules, _llm(shape="number", conditions=[{"phrase": "open"}]))
    assert not got["confident"] and got["uncertain"] == ["shape"]
    got = combine(rules, _llm(conditions=[{"phrase": "not open", "value": "open", "negated": True}]))
    assert not got["confident"] and "conditions" in got["uncertain"]


def test_condition_split_does_not_matter() -> None:
    rules = rules_frame("which projects are open high risk")
    llm = _llm(conditions=[{"phrase": "open"}, {"phrase": "high risk"}])
    assert combine(rules, llm)["agree"]["conditions"]


FRAME_REPLY = {"shape": "list_of_records", "subject": "projects", "conditions": [{"phrase": "open"}]}


async def test_build_with_the_llm() -> None:
    fake = FakeGateway({"AG-01": FRAME_REPLY})
    got = await build(fake.client(), tenant_id="t", user_id=None, question="Show the List of open Projects",
                      previous=None, turn_id=None, use_llm=True)
    assert got["llm_status"] == "ok" and got["confident"] and got["frame"].shape == "list_of_records"
    (call,) = fake.calls
    assert (call["agent"], call["task"]) == ("AG-01", "frame")
    assert call["messages"][1]["content"] == "Show the List of open Projects"  # the question only, no catalogue


async def test_follow_up_sends_the_previous_question() -> None:
    fake = FakeGateway({"AG-01": {**FRAME_REPLY, "follow_up": "refine"}})
    got = await build(fake.client(), tenant_id="t", user_id=None, question="only UAE",
                      previous="list of open projects", turn_id=None, use_llm=True)
    assert "Previous question: list of open projects" in fake.calls[0]["messages"][1]["content"]
    assert got["frame"].follow_up == "refine" and got["rules"].follow_up == "refine"


async def test_llm_failure_leaves_the_rules_frame() -> None:
    fake = FakeGateway(fail="planner_unavailable")
    got = await build(fake.client(), tenant_id="t", user_id=None, question="list of open projects",
                      previous=None, turn_id=None, use_llm=True)
    assert got["llm"] is None and got["llm_status"].startswith("failed")
    assert got["frame"].shape == "list_of_records" and not got["confident"] and got["uncertain"] == ["llm"]


async def test_bad_llm_answer_is_fixed_once_then_dropped() -> None:
    fake = FakeGateway({"AG-01": {"shape": "pie"}})
    got = await build(fake.client(), tenant_id="t", user_id=None, question="list of open projects",
                      previous=None, turn_id=None, use_llm=True)
    assert len(fake.calls) == 2 and got["llm"] is None and got["frame"].shape == "list_of_records"


async def test_rules_only_makes_no_call() -> None:
    fake = FakeGateway({"AG-01": FRAME_REPLY})
    got = await build(fake.client(), tenant_id="t", user_id=None, question="list of open projects",
                      previous=None, turn_id=None, use_llm=False)
    assert fake.calls == [] and got["llm_status"] == "off"
