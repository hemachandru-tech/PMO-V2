"""ENH-022 Wave 2 (FEAT-072; no database): names read across word order, confidence that weighs how each filter was
found, and the zero-result check before answering.

* US-216 — a measure's synonym or a glossary term said in another order ("project will be active" for "active
  projects") names the measure, so its words filter nothing; a value several fields of one table hold ("amber") is
  asked, not picked; a filter that restates the measure's own rule in its own words is reported and offered without.
* US-217 — only exact and approved-synonym links (or a value the person chose) are certain: a partial, spelling or
  meaning link, or a sentence value, caps the score below "balanced" unless the question names the field.
* US-218 — a count whose filters leave nothing is probed without the filter it is least sure of; data without it
  re-plans without that filter and asks. A true zero with exact filters still answers 0.
"""

from __future__ import annotations

import asyncio
from typing import Any

import duckdb
import pytest
import yaml
from test_gold_scoring_unit import db, run, snapshot  # noqa: F401 - pytest fixtures
from test_noise_set_unit import REAL_INDEX
from test_planner_gold_unit import KNOWN, SNAP, plan

from tela_api.ask import confidence, sanity
from tela_api.ask.freehand import expand
from tela_api.ask.linking import rank_measures
from tela_api.ask.planner import LinkedValue, Plan, Trap, find_trap, norm, span_of
from tela_api.knowledge.values import link
from tela_api.packs.manifest import DEFAULT_PACKS_DIR
from tela_query_spec.model import ModelSnapshot

EXEC139 = "How many number of project will be active in UAE region"
BALANCED = confidence.threshold(None)
HOLD = "Training material will be prepared once the build i"
UAE = {"ref": "dimension:region", "op": "=", "values": ["GCC - UAE"]}


def checked(question: str) -> tuple[Plan, Plan]:
    """The rules plan with its checks as ``Orchestrator._rules`` runs them, then Ask's decision at "balanced"."""
    p = plan(question, index=REAL_INDEX)
    linked = link(question, REAL_INDEX, known=KNOWN).links
    confidence.check_rules(p, question, SNAP, SNAP.measures, SNAP.dimensions, linked,
                           rank_measures(question, SNAP, SNAP.measures))
    return p, confidence.decide(p, question, SNAP, SNAP.measures, None)


def traps() -> list[Trap]:
    terms = yaml.safe_load((DEFAULT_PACKS_DIR / "pmo" / "glossary.yaml").read_text())["terms"]
    return [Trap(t["term"].lower(), t["reading"], tuple(t.get("instead_of") or ())) for t in terms if t.get("reading")]


# --------------------------------------------------------------------------- US-216 word order


def test_exec139_reads_active_projects_across_word_order() -> None:
    p, decided = checked(EXEC139)
    assert p.spec is not None and p.spec["measure"]["ref"] == "measure:open_projects"
    assert p.spec["filters"] == [UAE]  # no Current state: Active on top of Open projects
    assert p.confidence >= BALANCED and decided.status == "ok"


def test_singular_and_any_order_name_the_measure_not_a_filter() -> None:
    for q in ("How many active project do we have", "How many projects are active", "Open projects in UAE"):
        p, _ = checked(q)
        assert p.spec is not None and p.spec["measure"]["ref"] == "measure:open_projects", q
        assert all(f["ref"] != "dimension:current_state" for f in p.spec["filters"]), q
        assert p.confidence == 1.0, (q, p.why)


def test_span_needs_the_words_side_by_side_once_grammar_is_dropped() -> None:
    assert span_of("active projects", norm(EXEC139)) is not None
    assert span_of("late projects", "how many projects are late") is not None
    assert span_of("late projects", "projects deployed late") is None  # another word between: not the term
    assert span_of("cost", "what does it cost") is None  # one word: an exact phrase only


def test_traps_said_in_any_order_but_an_exact_phrase_wins() -> None:
    t = traps()

    def read(q: str) -> str | None:
        found = find_trap(norm(q), t, SNAP.measures, SNAP)
        return found.reading if found else None

    assert read("project will be active in UAE") == "open_projects"
    assert read("how many projects are late") == "overdue_go_lives"
    assert read("requests that are active") == "open_demand"
    assert read("projects deployed late") is None
    assert read("projects with active requests") is None  # "active requests" is said in a row: not "active projects"


def test_glossary_terms_expand_across_word_order() -> None:
    terms = {"active projects": "open projects", "s2": "planning stage"}
    assert expand(norm(EXEC139), terms) == ("how many number of open projects will be in uae region",
                                            ["active projects read as open projects"])
    assert expand("projects past s2", terms) == ("projects past planning stage", ["S2 read as planning stage"])


def test_a_value_several_fields_hold_is_asked_not_picked() -> None:
    p, decided = checked("How many amber projects")
    assert p.confidence < BALANCED and any("is a value of" in w for w in p.why), p.why
    assert decided.status == "clarify"
    labels = " | ".join(o["label"] for o in decided.question["options"])
    assert "Reported status" in labels and "Schedule KPI" in labels, labels
    named, _ = checked("How many projects are reported Amber?")  # the field is named: no question
    assert named.confidence >= BALANCED, named.why


def test_a_filter_restating_the_rule_is_reported_and_offered_without() -> None:
    p, decided = checked("Open projects that are active in KSA")
    assert "Current state: Active narrows Open projects, whose rule already counts Active" in p.interpreted
    assert p.confidence < BALANCED and decided.status == "clarify"
    specs = decided.question["readings"]
    assert any(all(f["ref"] != "dimension:current_state" for f in s["filters"]) for s in specs)
    # the field named outright is the person's own criterion, not the measure restated (EXEC-115)
    stated, _ = checked("Open projects in current state Active in KSA")
    assert not any("narrows" in i for i in stated.interpreted)


# --------------------------------------------------------------------------- US-217 how each link was found


def _exec139_before() -> tuple[Plan, list[LinkedValue]]:
    """EXEC-139's plan before US-213/214: "will" linked part of a hold-reason sentence; "active" a state."""
    spec = {"measure": {"ref": "measure:open_projects"}, "breakdowns": [],
            "filters": [{"ref": "dimension:current_state", "op": "=", "values": ["Active"]},
                        {"ref": "dimension:hold_reason", "op": "=", "values": [HOLD]}, UAE]}
    linked = [LinkedValue("hold_reason", HOLD, HOLD, "will", "partial", 0.8),
              LinkedValue("current_state", "Active", "Active", "active", "exact"),
              LinkedValue("status", "Active", "Active", "active", "exact"),
              LinkedValue("region", "GCC - UAE", "GCC - UAE", "uae", "synonym", 0.95)]
    return Plan("ok", spec=spec, chips=[], measures_considered=["open_projects"]), linked


def test_exec139_plan_before_wave1_scores_below_balanced_and_asks() -> None:
    p, linked = _exec139_before()
    confidence.check_rules(p, EXEC139, SNAP, SNAP.measures, SNAP.dimensions, linked,
                           rank_measures(EXEC139, SNAP, SNAP.measures))
    assert p.confidence < BALANCED
    assert f"“will” matched part of hold reason “{HOLD}”" in p.why
    assert (p.signals or {})["weak_refs"]["dimension:hold_reason"] == 0.5  # partial and a sentence: the weakest
    assert confidence.decide(p, EXEC139, SNAP, SNAP.measures, None).status == "clarify"


def test_guessed_links_ask_unless_the_field_is_named() -> None:
    # A given name is a whole word of a short name: certain on its own field, asked when two fields hold it.
    assert checked("Open projects for Nomsa")[0].confidence == 1.0
    two, _ = checked("Open projects of Lina")
    assert two.confidence < BALANCED and "“lina” is a value of Delivery manager and Project manager" in two.why
    named, _ = checked("Open projects where Lina is the project manager")
    assert named.confidence >= BALANCED, named.why
    sentence = LinkedValue("hold_reason", HOLD, HOLD, "will", "partial", 0.8)
    spec = {"measure": {"ref": "measure:open_projects"}, "breakdowns": [],
            "filters": [{"ref": "dimension:hold_reason", "op": "=", "values": [HOLD]}]}
    assert f"“will” matched part of hold reason “{HOLD}”" in confidence.link_doubts(EXEC139, spec, SNAP,
                                                                                    [sentence]).why
    slip, _ = checked("Open projects in jordon")
    assert slip.confidence < BALANCED and "“jordon” was matched to Jordan by spelling" in slip.why


def test_a_free_text_value_the_question_never_says_is_doubted() -> None:
    spec = {"measure": {"ref": "measure:open_projects"}, "breakdowns": [],
            "filters": [{"ref": "dimension:hold_reason", "op": "=", "values": [HOLD]}]}
    doubts = confidence.link_doubts("How many open projects are waiting", spec, SNAP, [])
    assert doubts.weak == {"dimension:hold_reason": confidence.FREE_TEXT_SCORE}
    assert any("holds free text" in w for w in doubts.why)


# --------------------------------------------------------------------------- US-218 zero-result check


def runner(con: duckdb.DuckDBPyConnection, snap: ModelSnapshot, calls: list[dict[str, Any]]) -> sanity.Run:
    async def go(spec: dict[str, Any]) -> dict[str, Any]:
        calls.append(spec)
        rows = run(con, snap, spec)
        return {"columns": [{"kind": "measure", "label": "x", "format": None}], "rows": [list(r) for r in rows]}
    return go


def test_exec139_zero_is_caught_and_replanned(db: duckdb.DuckDBPyConnection,  # noqa: F811
                                              snapshot: ModelSnapshot) -> None:  # noqa: F811
    p, linked = _exec139_before()
    assert p.spec is not None
    p.spec["filters"] = [f for f in p.spec["filters"] if f["ref"] != "dimension:current_state"]
    weak = confidence.link_doubts(EXEC139, p.spec, SNAP, linked).weak
    calls: list[dict[str, Any]] = []
    note = asyncio.run(sanity.zero_result(p.spec, runner(db, snapshot, calls), SNAP, weak, label="Open projects",
                                          kind=None))
    assert note is not None and note["ref"] == "dimension:hold_reason" and note["numbers"]["without"] > 0
    assert len(calls) == 2  # the plan's own total (the answer's, cached) and one without the filter
    confidence.zero_result(p, note, SNAP)
    assert p.spec["filters"] == [UAE] and p.readings[0]["filters"][0]["ref"] == "dimension:hold_reason"
    assert p.confidence < BALANCED and p.interpreted[-1].startswith("Left out Hold reason:")
    decided = confidence.decide(p, EXEC139, SNAP, SNAP.measures, None)
    assert decided.status == "clarify" and decided.question["readings"][0]["filters"] == [UAE]
    assumed = confidence.decide(p, EXEC139, SNAP, SNAP.measures, "assume_first")
    assert assumed.status == "ok" and assumed.spec is not None and assumed.spec["filters"] == [UAE]


def test_a_true_zero_still_answers(db: duckdb.DuckDBPyConnection, snapshot: ModelSnapshot) -> None:  # noqa: F811
    spec = {"measure": {"ref": "measure:open_projects"}, "breakdowns": [],
            "filters": [{"ref": "dimension:region", "op": "=", "values": ["KSA"]},
                        {"ref": "dimension:reported_status", "op": "=", "values": ["Red"]}]}
    calls: list[dict[str, Any]] = []
    total = run(db, snapshot, {**spec})
    assert total and (total[0][0] or 0) == 0, "the fixture has no Red open projects in KSA"
    # every filter found exactly: nothing is weak, nothing is probed
    weak = confidence.link_doubts("Red open projects in KSA", spec, SNAP,
                                  link("Red open projects in KSA", REAL_INDEX, known=KNOWN).links).weak
    assert weak == {}
    assert asyncio.run(sanity.zero_result(spec, runner(db, snapshot, calls), SNAP, weak, label="Open projects",
                                          kind=None)) is None
    assert calls == []
    # a weak filter whose removal still leaves nothing is a true zero too
    nothing = {**spec, "filters": [*spec["filters"], {"ref": "dimension:hold_reason", "op": "=", "values": [HOLD]}]}
    assert asyncio.run(sanity.zero_result(nothing, runner(db, snapshot, calls), SNAP,
                                          {"dimension:hold_reason": 0.5}, label="Open projects", kind=None)) is None


@pytest.mark.parametrize("value", [1, None])
def test_no_zero_no_probe(value: int | None) -> None:
    calls: list[dict[str, Any]] = []

    async def go(spec: dict[str, Any]) -> dict[str, Any]:
        calls.append(spec)
        return {"columns": [{"kind": "measure"}], "rows": [[value]] if value else []}

    spec = {"measure": {"ref": "measure:open_projects"}, "filters": [UAE]}
    note = asyncio.run(sanity.zero_result(spec, go, SNAP, {"dimension:region": 0.8}, label="Open projects", kind=None))
    assert note is None and len(calls) == (1 if value else 2)
