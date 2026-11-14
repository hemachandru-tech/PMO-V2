"""Curation queue from Ask's misses (ENH-016, US-198; FEAT-066 / FEAT-014; no database).

Proposals from each source — a refused question, a clarification choice, a repaired plan, a down-vote — and an
approved synonym feeding linking: the rules planner plans the question it once refused. The queue, the review gate
and the held-out exclusion run live in ``test_curation_api``.
"""

from __future__ import annotations

import pytest
import test_planner_gold_unit as offline
from test_planner_gold_unit import SNAP

from tela_api.knowledge import curation
from tela_api.knowledge.gold import pack_draft
from tela_common.errors import TelaError
from tela_query_spec.model import ModelSnapshot

ON_HOLD = {"model_version_id": "v1", "connection_id": "c1", "measure": {"ref": "measure:on_hold"},
           "breakdowns": [{"ref": "dimension:region"}]}


def test_phrase_is_the_words_the_model_does_not_know() -> None:
    assert curation.phrase_for("Show me stalled projects by region", SNAP) == "stalled projects"
    assert curation.phrase_for("How many inflight projects do we have?", SNAP) == "inflight projects"
    assert curation.phrase_for("How many open projects by region?", SNAP) is None  # every word known
    assert curation.phrase_for("Open projects per quarter in 2026", SNAP) is None  # grains and numbers are known
    # At most four words, from the first unknown one.
    assert curation.phrase_for("zorbly glimps quantum widgets frobs blarg projects", SNAP) == \
        "zorbly glimps quantum widgets"


def test_refusal_proposes_a_synonym_or_a_glossary_term() -> None:
    error = {"code": "unsupported_question", "did_you_mean": None}
    [p] = curation.from_refusal("Show me stalled projects by region", SNAP, error, ["total_projects", "nope"])
    assert (p.kind, p.key, p.phrase, p.measure_ref) == ("measure_synonym", "stalled projects", "stalled projects", None)
    assert p.options == ["measure:total_projects"]  # unknown measures dropped; the Admin picks the measure
    meant = {**error, "did_you_mean": {"label": "Projects on hold", "measure": "measure:on_hold"}}
    [p] = curation.from_refusal("Show me stalled projects by region", SNAP, meant, ["total_projects"])
    assert p.measure_ref == "measure:on_hold" and p.options == ["measure:on_hold", "measure:total_projects"]
    # An abbreviation in capitals is a glossary term; the Admin writes what it means.
    [g] = curation.from_refusal("How many RFQs are pending?", SNAP, error, [])
    assert (g.kind, g.phrase, g.key, g.meaning) == ("glossary_term", "RFQs", "rfqs", None)
    assert curation.from_refusal("How many open projects by region?", SNAP, error, []) == []  # nothing unknown


def test_clarification_choice_proposes_a_synonym() -> None:
    clar = {"key": "measure:on_hold|open_projects", "question": "Which measure do you mean?", "answer": "b",
            "options": [{"id": "a", "label": "Open projects", "measure": "measure:open_projects"},
                        {"id": "b", "label": "Projects on hold", "measure": "measure:on_hold"}]}
    [syn] = curation.from_clarification("Stalled projects by region", SNAP, clar, ON_HOLD)
    assert (syn.kind, syn.phrase, syn.measure_ref) == ("measure_synonym", "stalled projects", "measure:on_hold")
    # A reading choice (US-182): the chosen reading's measure.
    reading = {"kind": "reading", "answer": "b", "readings": [{"measure": {"ref": "measure:open_projects"}},
                                                              {"measure": {"ref": "measure:on_hold"}}],
               "options": [{"id": "a", "label": "x", "value": "reading:0"}, {"id": "b", "label": "y",
                                                                           "value": "reading:1"}]}
    assert curation.chosen_measure(reading, None) == "measure:on_hold"
    # Every word known: nothing to learn (Ask suggests the answered question as a gold example itself).
    assert curation.from_clarification("Open projects by region", SNAP, clar, ON_HOLD) == []


def test_repair_proposes_the_answered_spec_and_the_persons_words() -> None:
    gold, syn = curation.from_repair("How many stalled projects?", SNAP, ON_HOLD,
                                     [("unknown_measure", "measure:stalled_projects"), ("not_reachable", None)])
    assert gold.kind == "gold_example" and gold.spec is not None and gold.spec["measure"] == {"ref": "measure:on_hold"}
    assert (syn.phrase, syn.measure_ref) == ("stalled projects", "measure:on_hold")
    # A name AG-03 made up that the person never said teaches nothing.
    [only] = curation.from_repair("How many stalled projects?", SNAP, ON_HOLD,
                                  [("unknown_measure", "measure:paused_portfolio_items")])
    assert only.kind == "gold_example"
    # A name the model already has is not a new synonym.
    [only] = curation.from_repair("How many open projects?", SNAP, ON_HOLD, [("unknown_measure", "open_projects")])
    assert only.kind == "gold_example"


def test_downvote_proposes_a_gold_example_to_correct() -> None:
    [p] = curation.from_feedback("How many projects are paused?", ON_HOLD)
    assert p.kind == "gold_example" and p.needs_fix and "model_version_id" not in (p.spec or {})
    assert curation.from_feedback("How many projects are paused?", None) == []


def test_approved_synonym_feeds_linking(monkeypatch: pytest.MonkeyPatch) -> None:
    question = "Show me stalled projects by region"
    assert offline.plan(question).status == "unsupported"
    definition = SNAP.model_dump(mode="json")
    changed = curation.add_synonym(definition, "on_hold", "stalled projects")
    assert definition["measures"] != changed["measures"]  # the current definition is untouched
    monkeypatch.setattr(offline, "SNAP", ModelSnapshot.model_validate(changed))
    plan = offline.plan(question)
    assert plan.status == "ok" and plan.spec is not None
    assert plan.spec["measure"] == {"ref": "measure:on_hold"} and plan.spec["breakdowns"] == [
        {"ref": "dimension:region"}]
    # A wording a measure already has is refused, as is a measure the model doesn't have.
    with pytest.raises(TelaError, match="already names Projects on hold"):
        curation.add_synonym(changed, "open_projects", "Stalled projects")
    with pytest.raises(TelaError, match="already names Open projects"):
        curation.add_synonym(definition, "on_hold", "open projects")
    with pytest.raises(TelaError, match="certified measure"):
        curation.add_synonym(definition, "no_such_measure", "zorbly")


def test_held_out_paraphrases_exist_to_exclude() -> None:
    held = [ex for ex in pack_draft() if ex.get("held_out")]
    assert sum("paraphrase" in ex.get("tags", []) for ex in held) >= 100  # US-190's, beside US-173's alias set
