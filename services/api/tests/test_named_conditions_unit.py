"""Named conditions and the order of authority (ENH-028, US-252; FEAT-078 / FEAT-007; no database).

A certified count measure's condition and its name ("Open projects" → open on Projects), the gaps of a condition on a
model, the fields a person must see, conflict detection between a condition and the measure of the same term, the
order of authority (approved condition → certified measure → document text) and the definitions the knowledge
route may use once read through their conditions. Storing, carrying forward, the Admin's choice and row scope run
live in ``test_named_conditions_api``.
"""

from __future__ import annotations

from typing import Any

from test_planner_gold_unit import RECORD_SNAP as SNAP

from tela_api.knowledge import conditions as C

OPEN = {"field": "current_state", "op": "in", "values": ["Project Created", "Active", "On Hold"]}


def row(**kw: Any) -> dict[str, Any]:
    base = {"id": "c1", "term": "Open project", "term_key": "open project", "synonyms": ["Open projects"],
            "entity": "projects", "condition": OPEN, "source": "document", "definition_id": "d1",
            "measure_ref": "measure:open_projects", "status": "approved", "conflict": None, "prefer": None}
    return {**base, **kw}


CONFLICT = {"condition": {"rule": "…", "figure": 65}, "measure": {"ref": "measure:open_projects", "figure": 62}}


def test_count_measures_with_a_condition_propose_it() -> None:
    m = SNAP.measure("open_projects")
    assert m is not None and C.from_measure(m) == {"field": "status", "op": "=", "value": "Active"}
    assert C.qualifier(C.measure_term(m), m.entity, SNAP) == "open"
    delivery = SNAP.measure("in_delivery")
    assert delivery is not None and C.qualifier(C.measure_term(delivery), "projects", SNAP) == "in delivery"
    # A count with no condition, and an amount, propose nothing.
    assert all(C.from_measure(x) is None for x in SNAP.measures
               if x.key in ("total_projects", "cost_total") or x.status != "certified")


def test_a_condition_is_read_against_the_model() -> None:
    assert C.gaps(OPEN, "projects", SNAP) == []
    assert C.gaps({"field": "stage_gate", "op": "in", "values": ["G3"]}, "projects", SNAP) == \
        ["The model has no field “stage gate” on Projects."]
    assert C.gaps(OPEN, "widgets", SNAP) == ["The model has no widgets."]
    assert C.gaps({"nonsense": True}, "projects", SNAP) == ["The condition can't be read."]
    assert C.rule_text(OPEN, "projects", SNAP) == \
        "Projects where Current state is one of Project Created, Active, On Hold"
    m = SNAP.measure("overdue_go_lives")
    assert m is not None and C.parse(C.from_measure(m)) is not None  # dates and blanks round-trip


def test_hidden_fields_keep_a_condition_from_a_person() -> None:
    both = {"all": [OPEN, {"field": "region", "op": "=", "value": "KSA"}]}
    assert C.visible(row(), None)  # no limit
    assert C.visible(row(), {"projects": {"current_state", "name"}})
    assert not C.visible(row(), {"projects": {"name"}})  # current state is hidden from them
    assert not C.visible(row(condition=both), {"projects": {"current_state"}})  # every field it reads
    assert not C.visible(row(), {})  # no access to the entity


def test_same_rows_compares_rules_not_spelling() -> None:
    assert C.same_rows({"field": "status", "op": "=", "value": "Active"},
                       {"field": "status", "op": "in", "values": ["active"]})
    assert not C.same_rows({"field": "status", "op": "=", "value": "Active"}, OPEN)
    assert C.same_rows(OPEN, {**OPEN, "values": list(reversed(OPEN["values"]))})


def test_order_of_authority() -> None:
    q = "How many open projects are there?"
    # An approved condition with no conflict answers.
    [m] = C.authority(q, [row()])
    assert (m.use, m.conflict) == ("condition", False)
    # A conflict the Admin hasn't settled: the certified measure answers, the conflict is noted.
    [m] = C.authority(q, [row(conflict=CONFLICT)])
    assert (m.use, m.conflict, m.measure_ref) == ("measure", True, "measure:open_projects")
    assert C.authority(q, [row(conflict=CONFLICT, prefer="condition")])[0].use == "condition"
    assert C.authority(q, [row(conflict=CONFLICT, prefer="measure")])[0].use == "measure"
    # A measure's own condition is the measure.
    assert C.authority(q, [row(source="pack_measure", definition_id=None)])[0].use == "measure"
    # Document text only where nothing approved names the term; never counted.
    hints = [{"term": "Open project"}, {"term": "Go-live"}]
    out = C.authority("Open projects and go-lives", [row()], hints)
    assert [(x.term, x.use) for x in out] == [("Open project", "condition"), ("Go-live", "text")]
    # Proposed (carried without its field) and unrelated conditions don't count.
    assert C.authority(q, [row(status="proposed")]) == []
    assert C.authority("How many closed projects?", [row()]) == []
    # A synonym is the term.
    assert C.authority("Count the live portfolio", [row(synonyms=["live portfolio"])])[0].use == "condition"


def test_definitions_read_through_their_conditions() -> None:
    defs = [{"id": "d1", "term": "Open project", "expressible": True, "rule": {"entity": "projects", **OPEN},
             "checks": {"conflict": True, "figure": 65}},
            {"id": "d2", "term": "Gated project", "expressible": False, "rule": None, "checks": None},
            {"id": "d3", "term": "Paused project", "expressible": True, "rule": {}, "checks": None}]
    held = C.apply_to_definitions(defs, [row(conflict=CONFLICT)])
    assert [d["id"] for d in held] == ["d1", "d2", "d3"]
    assert held[0]["held"] and held[0]["checks"]["conflict"] and held[0]["checks"]["measure"]["figure"] == 62
    assert "held" not in held[1]  # text only: AG-03 reads it as before
    assert held[2]["held"]  # its term is stored once, by another definition
    chosen = C.apply_to_definitions(defs, [row(conflict=CONFLICT, prefer="condition")])
    assert not chosen[0]["held"] and chosen[0]["checks"]["conflict"]
    dropped = C.apply_to_definitions(defs, [row(conflict=CONFLICT, prefer="measure")])
    assert [d["id"] for d in dropped] == ["d2", "d3"]
    agreed = C.apply_to_definitions(defs, [row()])
    assert not agreed[0]["held"] and agreed[0]["checks"]["conflict"] is False
