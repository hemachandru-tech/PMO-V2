"""US-258: an answer checked against its question frame, and the one confidence formula of the frame modes.
The PMO pack's model and the rules' frame; no database, no LLM."""

from __future__ import annotations

import pytest
from test_fields_planner_unit import SNAP, fields, ok, plan_b

from tela_api.ask import confidence, verify
from tela_api.ask.frame import QuestionFrame, rules_frame
from tela_api.ask.fields_planner import subjects

LIST_01A1CA5C = "Show the List of open Projects"


def frame(question: str) -> QuestionFrame:
    return rules_frame(question, subjects=subjects(SNAP))


# --------------------------------------------------------------------------- the structural check


def test_the_01a1ca5c_plan_one_number_for_a_list_fails() -> None:
    """Run 01a1ca5c: "Show the List of open Projects" answered "Open projects: 103", certified at 1.0."""
    f = frame(LIST_01A1CA5C)
    assert f.shape == "list_of_records"
    slots = [{"slot": "condition", "phrase": "open", "ref": "condition:open projects"}]
    (fail,) = verify.check(f, {"measure": {"ref": "measure:open_projects"}}, slots, snap=SNAP)
    assert (fail.slot, fail.wanted, fail.got) == ("shape", "list_of_records", "number")
    assert fail.why == "asked for a list of the records, the plan gives one number"


def test_plan_b_s_list_passes_and_keeps_its_confidence() -> None:
    p = plan_b(LIST_01A1CA5C)
    ok(p)
    assert fields(p)["check"] == {"passed": True, "failures": [], "left_out": []}
    assert fields(p)["certified"] and p.confidence == 1.0


def test_a_condition_that_is_not_a_filter_fails() -> None:
    f = frame("How many open projects in UAE")
    slots = [{"slot": "condition", "phrase": c.phrase, "ref": "x"} for c in f.conditions][:1]
    got = verify.check(f, {"measure": {"ref": "measure:total_projects"}}, slots, snap=SNAP)
    assert [x.slot for x in got] == ["condition", "condition"]  # one frame condition unread, and no rule at all
    assert verify.check(f, {"measure": {"ref": "measure:open_projects"}},
                        [{"slot": "condition", "phrase": c.phrase, "ref": "x"} for c in f.conditions], snap=SNAP) == []


def test_every_grouping_is_a_breakdown() -> None:
    f = frame("open projects by region")
    slots = [{"slot": "condition", "phrase": c.phrase} for c in f.conditions]
    grouped = [*slots, {"slot": "grouping", "phrase": "region", "ref": "dimension:region"}]
    spec = {"measure": {"ref": "measure:open_projects"}, "breakdowns": [{"ref": "dimension:region"}]}
    assert verify.check(f, spec, grouped, snap=SNAP) == []
    lost = verify.check(f, {**spec, "breakdowns": [{"ref": "dimension:portfolio"}]}, grouped, snap=SNAP)
    assert [(x.slot, x.phrase) for x in lost] == [("grouping", "region")]


def test_time_is_applied() -> None:
    f = frame("How many projects created this month")
    assert f.time is not None and f.time.range == "this_month"
    spec = {"measure": {"ref": "measure:total_projects"}}
    assert [x.slot for x in verify.check(f, spec, [], snap=SNAP)] == ["time"]
    timed = {**spec, "time": {"ref": "field:projects.created_on", "range": {"relative": "this_month"}}}
    assert verify.check(f, timed, [], snap=SNAP) == []
    trend = frame("projects created by month")
    assert [x.slot for x in verify.check(trend, spec, [], snap=SNAP, as_time=trend.groupings)] == ["shape", "time"]


def test_unused_words_fail() -> None:
    f = frame("How many projects")
    (fail,) = verify.check(f, {"measure": {"ref": "measure:total_projects"}}, [], unread=["zorbs"], snap=SNAP)
    assert (fail.slot, fail.phrase) == ("words", "zorbs")


def test_values_side_by_side_answer_a_comparison() -> None:
    f = QuestionFrame(shape="comparison", subject="projects", groupings=["region"])
    spec = {"measure": {"ref": "measure:total_projects"}, "breakdowns": [{"ref": "dimension:region"}]}
    assert verify.check(f, spec, [{"slot": "grouping", "phrase": "region", "ref": "dimension:region"}],
                        snap=SNAP) == []


# --------------------------------------------------------------------------- Ask asks about the failed slot


def test_a_failed_shape_asks_which_kind_then_answers_or_refuses() -> None:
    """A number with a grouping compiles to a breakdown: Ask asks which kind was meant (it never answers one for
    the other); the kind the template gives is answered, the other refused."""
    f = QuestionFrame(shape="number", subject="projects", groupings=["region"])
    p = plan_b("projects region", frame=f)
    assert p.status == "clarify" and p.question is not None
    assert p.question["key"] == "fields:shape:projects" and p.question["kind"] == "field"
    assert [(o["value"], o["label"]) for o in p.question["options"]] == [
        ("shape:number", "One number"), ("shape:breakdown", "A breakdown")]
    assert not fields(p)["certified"] and fields(p)["check"]["failures"][0]["slot"] == "shape"
    chose = plan_b("projects region", frame=f, memory={p.question["key"]: "shape:breakdown"})
    assert ok(chose)["breakdowns"] == [{"ref": "dimension:region"}] and fields(chose)["check"]["passed"]
    again = plan_b("projects region", frame=f, memory={p.question["key"]: "shape:number"})
    assert again.status == "unsupported" and "one number" in (again.reason or "")


# --------------------------------------------------------------------------- one confidence formula


@pytest.mark.parametrize(("scores", "passed", "want"), [
    ([1.0, 1.0, 1.0], True, 1.0),
    ([1.0, 0.95, 0.8], True, 0.76),
    ([0.6, 0.8], True, 0.48),  # no cap, no floor: the product itself
    ([1.0, 1.0], False, 0.0),  # a failed check
    ([], True, 1.0),
])
def test_frame_score_is_the_product_of_the_slots_times_the_check(scores: list[float], passed: bool,
                                                                  want: float) -> None:
    assert confidence.frame_score(scores, passed) == want


def test_a_meaning_match_scores_its_link_and_nothing_else() -> None:
    from tela_api.ask.planner import LinkedValue

    p = plan_b("How many projects in saudi", linked=[LinkedValue("region", "KSA", "KSA", "saudi", "meaning", 0.55)])
    ok(p)
    assert p.confidence == 0.55 and fields(p)["check"]["passed"] and not fields(p)["certified"]
