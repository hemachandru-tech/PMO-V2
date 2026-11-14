"""Knowledge-led planning route (ENH-017, US-204; FEAT-067 / FEAT-066 / FEAT-062), without a database server.

Acceptance: a question that states its own criteria on a field the matched measure's rule reads is counted by them —
the table's certified count with the stated filter, every listed value kept — instead of the criteria stacked on the
rule; the same question without criteria keeps the certified measure; an approved definition the question uses is
applied the same way and cited; plain questions (the US-190 originals and paraphrases) plan exactly as before (W4, W5).
"""

from __future__ import annotations

from typing import Any

import pytest
from test_answer_sanity_unit import EXEC115
from test_paraphrase_set_unit import EXAMPLES, ORIGINALS
from test_planner_gold_unit import INDEX, KNOWN, ROWS
from test_planner_gold_unit import RECORD_SNAP as SNAP

from tela_api.ask import confidence, stated
from tela_api.ask.linking import rank_measures
from tela_api.ask.orchestrator import value_dims
from tela_api.ask.planner import Plan, plan_question
from tela_api.ask.rewrite import describe
from tela_api.knowledge.values import link
from tela_query_spec.model import ModelSnapshot

OPEN_STATES = ["Project Created", "Active", "On Hold"]
STATED = "Projects, Current state: Active, Project Created, On Hold"


def _exec115() -> ModelSnapshot:
    """The model with the pack's Status read from ``current_state``, as on TPG MySQL (EXEC-115)."""
    data = SNAP.model_dump()
    projects = next(e for e in data["entities"] if e["key"] == "projects")
    column = next(f["source_column"] for f in projects["fields"] if f["key"] == "current_state")
    next(f for f in projects["fields"] if f["key"] == "status")["source_column"] = column
    return ModelSnapshot.model_validate(data)


EXEC115_SNAP = _exec115()


def values_for(question: str, snap: ModelSnapshot, p: Plan) -> dict[str, list[str]]:
    """The scoped values ``Orchestrator._values`` lists for the plan's measure (display labels)."""
    if p.status != "ok" or p.spec is None:
        return {}
    m = snap.measure(p.spec["measure"]["ref"].split(":", 1)[1])
    assert m is not None
    return {d.key: sorted({d.value_labels.get(str(r[d.field]), str(r[d.field])) for r in ROWS[d.entity]
                           if r.get(d.field) is not None}) for d in value_dims(question, snap, snap.dimensions, m)}


def routed(question: str, snap: ModelSnapshot = SNAP, rows: list[dict[str, Any]] | None = None) -> tuple[Plan, Plan]:
    """The rules plan with its checks, then the route, as ``Orchestrator._rules`` runs them: (rules plan, final)."""
    ranked = rank_measures(question, snap, snap.measures)
    kw: dict[str, Any] = {"visible_measures": snap.measures, "visible_dims": snap.dimensions,
                          "linked_measures": ranked}
    linking = link(question, INDEX, known=KNOWN)
    p = plan_question(question, snap, values={}, **kw)
    values = values_for(question, snap, p)
    if p.status == "ok":
        p = plan_question(question, snap, values=values, linked=linking.links, choices=linking.choices, **kw)
    confidence.check_rules(p, question, snap, snap.measures, snap.dimensions, linking.links, ranked, values)
    led = stated.from_question(p, question, snap, snap.measures, values) or \
        stated.from_definition(p, question, snap, snap.measures, snap.dimensions, rows or [])
    if led is None:
        return p, p
    stated.check(led, question, snap, snap.measures, snap.dimensions, linking.links, ranked, values)
    return p, led


def definition(term: str = "Open project", values: list[str] | None = None, *, measure: str | None = "open_projects",
               expressible: bool = True, conflict: bool = True) -> dict[str, Any]:
    """An approved definition row as ``definitions.approved`` returns it."""
    return {"id": "d1", "term": term, "meaning": f"An {term.lower()} is a project whose current state is …",
            "rule": {"entity": "projects", "field": "current_state", "op": "in", "values": values or OPEN_STATES},
            "expressible": expressible, "gaps": [] if expressible else ["The model has no field “stage gate”."],
            "measure_ref": f"measure:{measure}" if measure else None, "chunk_id": "c1", "document_id": "doc1",
            "document_title": "PMO definitions", "passage": "An open project is …",
            "checks": {"conflict": conflict}}


# --------------------------------------------------------------------------- stated criteria win


def test_exec115_question_is_counted_by_the_stated_states() -> None:
    """EXEC-115: Status read from current state, so the stated states are on the rule's field and replace it."""
    rules, p = routed(EXEC115, EXEC115_SNAP)
    assert describe(rules.spec, EXEC115_SNAP) == "Open projects, Current state: Active, Project Created"
    assert p.counted_by is not None and p.counted_by.source == "question"
    assert describe(p.spec, EXEC115_SNAP) == STATED  # 431 on TPG MySQL
    assert p.interpreted[0] == "Counted by your definition: Current state in Active, Project Created, On Hold"
    assert p.confidence >= confidence.threshold(None), p.why
    # The certified measure's own figure, beside the answer, without the stated filter.
    assert p.beside is not None and p.beside["spec"]["measure"] == {"ref": "measure:open_projects"}
    assert p.beside["spec"]["filters"] == [] and p.beside["own_filters"] and p.beside["when_differs"]
    decided = confidence.decide(p, EXEC115, EXEC115_SNAP, EXEC115_SNAP.measures, None)
    assert decided.status == "ok" and decided.spec == p.spec


def test_every_listed_value_stays_in_the_filter() -> None:
    """"On Hold" is not read as the Projects on hold measure: it is one of the listed states."""
    _, p = routed(EXEC115, EXEC115_SNAP)
    states = next(f for f in p.spec["filters"] if f["ref"] == "dimension:current_state")
    assert states["op"] == "in" and set(states["values"]) == set(OPEN_STATES)
    assert p.spec["measure"] == {"ref": "measure:total_projects"} and "measure:on_hold" not in str(p.spec)


@pytest.mark.parametrize("snap", [SNAP, EXEC115_SNAP], ids=["right_mapping", "exec115_mapping"])
def test_the_same_question_without_criteria_keeps_the_certified_measure(snap: ModelSnapshot) -> None:
    rules, p = routed("How many open projects are there?", snap)
    assert p is rules and p.counted_by is None and p.spec["measure"] == {"ref": "measure:open_projects"}


def test_criteria_on_another_field_are_stacked_as_before() -> None:
    """The right mapping: the rule reads Status, the question Current state — a filter on the measure, as before."""
    rules, p = routed(EXEC115, SNAP)
    assert p is rules and p.counted_by is None and p.spec["measure"] == {"ref": "measure:open_projects"}


def test_criteria_that_only_narrow_the_rule_keep_the_measure() -> None:
    """"Open projects that are active" (status Active): the rule allows every stated value."""
    rules, p = routed("How many open projects with status Active are there?")
    assert p is rules and p.counted_by is None


def test_other_filters_are_kept_on_both_figures() -> None:
    q = "How many open projects (current state Project Created, Active or On Hold) are there in Digital?"
    _, p = routed(q, EXEC115_SNAP)
    assert p.counted_by is not None
    refs = {f["ref"] for f in p.spec["filters"]}
    assert {"dimension:current_state", "dimension:portfolio"} <= refs
    assert [f["ref"] for f in p.beside["spec"]["filters"]] == ["dimension:portfolio"]


def test_no_base_measure_keeps_the_rules_plan() -> None:
    """Without a certified count of the table, nothing certified expresses the stated reading: the rules' plan stays
    (the checks then ask, US-205)."""
    measures = [m for m in EXEC115_SNAP.measures if m.key != "total_projects"]
    p = plan_question(EXEC115, EXEC115_SNAP, values=values_for(EXEC115, EXEC115_SNAP, routed(EXEC115, EXEC115_SNAP)[0]),
                      visible_measures=measures, visible_dims=EXEC115_SNAP.dimensions,
                      linked_measures=rank_measures(EXEC115, EXEC115_SNAP, measures))
    assert p.status == "ok"
    assert stated.from_question(p, EXEC115, EXEC115_SNAP, measures, values_for(EXEC115, EXEC115_SNAP, p)) is None


# --------------------------------------------------------------------------- approved definitions


def test_an_approved_definition_is_applied_and_cited() -> None:
    q = "How many open projects are there?"
    rules, p = routed(q, SNAP, [definition()])
    assert rules.spec["measure"] == {"ref": "measure:open_projects"}
    assert p.counted_by is not None and p.counted_by.source == "definition" and p.counted_by.badge == \
        "knowledge_definition"
    assert describe(p.spec, SNAP) == "Projects, Current state: Project Created, Active, On Hold"
    assert p.interpreted[0] == ("Counted by the approved definition “Open project”: Current state in Project "
                                "Created, Active, On Hold")
    assert p.counted_by.cites() == ["chunk:c1"]
    public = p.counted_by.public()
    assert public["definition"]["term"] == "Open project" and public["definition"]["document"]["title"] == \
        "PMO definitions"
    assert public["measure"]["label"] == "Open projects"
    assert p.confidence >= confidence.threshold(None), p.why
    assert p.beside["spec"]["measure"] == {"ref": "measure:open_projects"}


def test_definitions_that_do_not_apply() -> None:
    q = "How many open projects are there?"
    # The question doesn't use the term; the definition is the measure's own rule; text only.
    assert routed("How many projects are there?", SNAP, [definition()])[1].counted_by is None
    same = definition(values=["Active"])
    same["rule"] = {"entity": "projects", "field": "status", "op": "in", "values": ["Active"]}
    assert routed(q, SNAP, [same])[1].counted_by is None
    text_only = definition(expressible=False)
    assert routed(q, SNAP, [text_only])[1].counted_by is None
    assert stated.unapplied([text_only, definition()], q, SNAP) == [text_only]


def test_stated_criteria_win_over_a_definition() -> None:
    _, p = routed(EXEC115, EXEC115_SNAP, [definition(values=["Active"])])
    assert p.counted_by.source == "question" and describe(p.spec, EXEC115_SNAP) == STATED


def test_a_definition_with_no_measure_replaces_nothing() -> None:
    """No certified measure to replace: the rules refuse, and AG-03 plans it with the definition in its context."""
    q = "How many quantum widgets are there?"
    rules, p = routed(q, SNAP, [definition("Quantum widget", ["On Hold"], measure=None)])
    assert rules.status == "unsupported" and p is rules


def test_a_conflicting_definition_not_applied_is_named() -> None:
    """US-205: the plan answers by a measure an approved definition of the question's term disagrees with."""
    q = "How many open projects are there?"
    rules, _ = routed(q, SNAP)
    row = definition(expressible=False)
    assert stated.conflicting(rules, [row], q, SNAP) is row
    assert stated.conflicting(rules, [definition(expressible=False, conflict=False)], q, SNAP) is None
    _, led = routed(q, SNAP, [definition()])
    assert stated.conflicting(led, [definition()], q, SNAP) is None  # counted by it


# --------------------------------------------------------------------------- measured (W5)


# Questions that state their own criteria, on the EXEC-115 model: (question, reading after US-204).
STATED_SET = [
    (EXEC115, STATED),
    ("How many open projects (current state Project Created or Active) are there?",
     "Projects, Current state: Active, Project Created"),
    ("Count the open projects with current state On Hold or Active", "Projects, Current state: Active, On Hold"),
    ("open projects where the current state is Active, On Hold or Project Created",
     "Projects, Current state: Active, Project Created, On Hold"),
    ("How many open projects are there?", "Open projects"),
    ("Open projects in Digital", "Open projects, Portfolio: Digital"),
]


@pytest.mark.parametrize(("question", "reading"), STATED_SET)
def test_stated_criteria_set(question: str, reading: str) -> None:
    _, p = routed(question, EXEC115_SNAP)
    assert p.status == "ok" and describe(p.spec, EXEC115_SNAP) == reading


@pytest.mark.parametrize("which", ["originals", "paraphrases"])
def test_the_route_changes_no_gold_plan(which: str) -> None:
    """US-190 offline harness: no original or paraphrase states criteria on its measure's rule field, so every one
    plans, scores and decides as before (W4: plain questions keep the rules). EXEC-115's question and its noise
    (US-202) state them by design."""
    questions = [e["question"] for e in (ORIGINALS if which == "originals" else EXAMPLES)
                 if "exec115" not in (e.get("tags") or []) and e.get("noise_of") != EXEC115]
    assert [q for q in questions if routed(q)[1].counted_by is not None] == []
