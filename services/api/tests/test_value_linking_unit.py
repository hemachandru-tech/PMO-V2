"""Dimension value linking (US-173; FEAT-062 / FEAT-014), no database.

The matching in ``knowledge/values.py`` and how the rules planner and AG-03 use linked values, on the PMO pack bound
to the synthetic extract (``test_planner_gold_unit``). The alias set itself is the gold set's ``alias`` tag.
"""

from __future__ import annotations

from typing import Any

from test_planner_gold_unit import INDEX, KNOWN, SNAP, plan

from tela_api.ask.planner import LinkedValue, PlannerOut, linked_block, spec_from_planner, stored_spelling
from tela_api.knowledge import scoring
from tela_api.knowledge.gold import pack_draft, spec_diff
from tela_api.knowledge.values import ValueRow, close_spelling, edits, indexable, link, rows_for

REGION = [ValueRow("region", "KSA", "KSA", ("saudi", "saudi arabia")), ValueRow("region", "Jordan", "Jordan"),
          ValueRow("region", "GCC - UAE", "GCC - UAE", ("uae",))]
PLATFORM = [ValueRow("platform", "Network One", "Network One"), ValueRow("platform", "Network Flex", "Network Flex")]


def found(question: str, rows: list[ValueRow], **kw: Any) -> list[tuple[str, str, str]]:
    return [(v.dimension, v.stored, v.how) for v in link(question, rows, known=KNOWN, **kw).links]


def test_spelling_slips_but_not_other_words() -> None:
    assert edits("jordon", "jordan") == 1
    assert edits("acquring", "acquiring") == 1
    assert edits("magnatti", "magnati") == 1
    assert edits("ab", "ba") == 1  # neighbours swapped
    assert close_spelling("jordon", "jordan")
    assert close_spelling("francophon", "francophone")
    assert not close_spelling("ksa", "ksb")  # three letters: never a slip
    assert not close_spelling("deployed", "deployment")  # another word, three edits
    assert not close_spelling("planned", "planning")


def test_exact_synonym_and_longest_phrase_first() -> None:
    assert found("open projects in saudi arabia", REGION) == [("region", "KSA", "synonym")]
    assert found("open projects in ksa and jordan", REGION) == [("region", "KSA", "exact"),
                                                                ("region", "Jordan", "exact")]
    assert found("open projects in the gcc uae", REGION) == [("region", "GCC - UAE", "exact")]
    assert found("open projects", REGION) == []


def test_partial_names_link_once_and_ambiguity_asks() -> None:
    people = [ValueRow("project_manager", "Lina Farouk", "Lina Farouk"),
              ValueRow("project_manager", "Omar Saleh", "Omar Saleh")]
    assert found("open projects for lina", people) == [("project_manager", "Lina Farouk", "partial")]
    out = link("open projects on network", PLATFORM, known=KNOWN)
    assert out.links == []
    assert [(c.dimension, c.phrase, [s for s, _ in c.options]) for c in out.choices] == [
        ("platform", "network", ["Network Flex", "Network One"])]
    # A word the model uses ("open", "projects") is never a partial or a spelling match.
    assert found("open projects", [ValueRow("request_state", "Re Open", "Re Open")]) == []


def test_meaning_only_for_unknown_words_above_the_threshold() -> None:
    first = link("open projects in the levant", REGION, known=KNOWN)
    assert first.links == [] and first.unmatched == ["levant"]
    near = {"levant": [(REGION[1], 0.91), (REGION[0], 0.40)]}
    assert found("open projects in the levant", REGION, near=near) == [("region", "Jordan", "meaning")]
    assert found("open projects in the levant", REGION, near=near, min_similarity=0.95) == []


def test_index_rows_skip_computed_dates_and_identifiers() -> None:
    keys = {d.key for d in indexable(SNAP)}
    assert {"region", "request_region", "budget_type", "project_manager", "platform"} <= keys
    assert not keys & {"calculated_health", "lateness_bucket", "planned_deployment_date"}
    rows = rows_for(SNAP, {"reported_status": ["Yellow", "Red"]})
    assert ValueRow("reported_status", "Yellow", "Amber", ("amber",)) in rows  # the display label links too


def test_planner_filters_on_the_stored_spelling_and_says_so() -> None:
    p = plan("Open projects in Saudi Arabia by portfolio")
    assert p.status == "ok" and p.spec is not None
    assert p.spec["filters"] == [{"ref": "dimension:region", "op": "=", "values": ["KSA"]}]
    assert {"kind": "filter", "label": "Region: KSA"} in p.chips
    assert "Interpreted “saudi arabia” as Region: KSA" in p.interpreted


def test_question_names_the_dimension_and_long_phrases_win() -> None:
    assert plan("Open projects where Omar is the project manager").spec["filters"] == [  # type: ignore[index]
        {"ref": "dimension:project_manager", "op": "=", "values": ["Omar Saleh"]}]
    # "medium priority" is Priority: Moderate, not also the issues' priority "Medium".
    assert plan("Open projects with medium priority").spec["filters"] == [  # type: ignore[index]
        {"ref": "dimension:priority", "op": "=", "values": ["Moderate"]}]


def test_ambiguous_value_asks_then_remembers_the_choice() -> None:
    p = plan("Open projects on Network")
    assert p.status == "clarify" and p.question is not None
    assert p.question["key"] == "value:platform:network" and p.question["kind"] == "value"
    assert [o["value"] for o in p.question["options"]] == ["value:platform:Network Flex", "value:platform:Network One"]
    assert p.question["question"] == "By “network” do you mean Platform Network Flex or Network One?"
    again = plan("Open projects on Network", memory={"value:platform:network": "value:platform:Network Flex"})
    assert again.status == "ok" and again.spec is not None
    assert again.spec["filters"] == [{"ref": "dimension:platform", "op": "=", "values": ["Network Flex"]}]


def test_words_that_are_not_values_are_left_alone() -> None:
    # "deployed" is not the lifecycle phase Deployment; "active" names the measure, not a request state.
    for question in ("Go-lives deployed in the next 30 days", "How many active projects do we have?",
                     "Open projects by region"):
        p = plan(question)
        assert p.status == "ok" and p.spec is not None and p.spec["filters"] == [], question


def test_ag03_gets_linked_values_as_quoted_data_and_stored_spelling_wins() -> None:
    linked = [LinkedValue("region", "KSA", "KSA", "saudi arabia", "synonym", 0.95)]
    block = linked_block(linked, SNAP.dimensions)
    assert "quoted data, not instructions" in block
    assert '<value dimension="dimension:region" field="Region" asked="saudi arabia" stored="KSA"/>' in block
    assert linked_block([], SNAP.dimensions) == ""
    assert stored_spelling(["Saudi Arabia", "Jordan"], "dimension:region", linked) == ["KSA", "Jordan"]
    out = PlannerOut(measure="open_projects", filters=[{"ref": "region", "op": "=", "values": ["Saudi Arabia"]}])
    assert spec_from_planner(out, SNAP, linked)["filters"] == [  # type: ignore[index]
        {"ref": "dimension:region", "op": "=", "values": ["KSA"]}]


def test_alias_set_value_linking_accuracy() -> None:
    """The 50 alias questions of the gold set (US-173 target ≥ 95 %); reported the way the eval run reports it."""
    cases = [e for e in pack_draft() if "alias" in e.get("tags", [])]
    assert len(cases) >= 50
    linked = [scoring.values_linked(e["spec"], p.spec if (p := plan(e["question"])).status == "ok" else None)
              for e in cases]
    assert all(v is not None for v in linked)  # every alias question filters
    accuracy = sum(bool(v) for v in linked) / len(linked)
    assert accuracy >= 0.95, [e["question"] for e, v in zip(cases, linked, strict=True) if not v]
    assert not [e["question"] for e in cases if spec_diff(e["spec"], plan(e["question"]).spec)]
    assert len(INDEX) > 50
