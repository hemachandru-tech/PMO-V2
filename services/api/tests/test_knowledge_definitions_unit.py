"""Business definitions from knowledge (ENH-017, US-203; FEAT-067 / FEAT-014 / FEAT-062; no database).

Reading definitions from Markdown (a heading over "An X is …", ``Term: meaning`` lines, table rows), turning them into
a rule on the model, text only when the model lacks a field or value, the certified measure of the same term and
whether its rule is the definition's, the definition's figure, and AG-03's context: approved definitions first and
never cut. The review gate, trial counts and the conflict with Open projects run live in
``test_knowledge_definitions_api``.
"""

from __future__ import annotations

from test_planner_gold_unit import RECORD_SNAP as SNAP

from tela_api.knowledge import definitions as D
from tela_api.knowledge.retrieval import Hit, context_block
from tela_common.chunking import chunk_text

DOC = """# PMO definitions

## Open project
An open project is a project whose current state is Project Created, Active or On Hold.

## Terms
- **Closed project**: current state in Completed, Cancelled.
- Go-live means the deployment of a project to production.
- Gated project: a project whose stage gate is G3 or G4.
- Parked project = current state other than Active.

| Term | Meaning |
| --- | --- |
| Paused project | Current state On Hold |

## Book of work
Projects with status Active.
"""
OPEN = {"entity": "projects", "field": "current_state", "op": "in", "values": ["Project Created", "Active", "On Hold"]}


def chunks(md: str) -> list[dict[str, object]]:
    return [{"id": None, "chunk_no": i, "content": c.content, "metadata": c.metadata}
            for i, c in enumerate(chunk_text(md))]


def read(md: str = DOC) -> dict[str, D.Definition | None]:
    return {p.term: D.interpret(p, SNAP) for p in D.read_chunks(chunks(md))}


def test_definitions_are_read_from_a_markdown_section() -> None:
    found = read()
    assert list(found) == ["Open project", "Closed project", "Go-live", "Gated project", "Parked project",
                           "Paused project", "Book of work"]
    # A heading with "An open project is …" under it: the rule, and the certified measure of the same term.
    open_ = found["Open project"]
    assert open_ is not None and open_.rule == OPEN and open_.expressible and open_.gaps == []
    assert open_.measure_ref == "measure:open_projects"
    closed = found["Closed project"]  # a "Term: meaning" line: the term is not a measure's name
    assert closed is not None and closed.rule == {"entity": "projects", "field": "current_state", "op": "in",
                                                  "values": ["Completed", "Cancelled"]}
    parked = found["Parked project"]  # "other than" is not in
    assert parked is not None and parked.rule == {"entity": "projects", "field": "current_state", "op": "not_in",
                                                  "values": ["Active"]}
    paused = found["Paused project"]  # a table row, the values straight after the field
    assert paused is not None and paused.rule and paused.rule["values"] == ["On Hold"]
    book = found["Book of work"]  # the entity named in the meaning: "projects with status Active"
    assert book is not None and book.rule == {"entity": "projects", "field": "status", "op": "in", "values": ["Active"]}
    assert found["Go-live"] is None  # names no field, value or measure: not a definition of the model


def test_passage_and_heading_are_kept() -> None:
    [p, *_] = D.read_chunks(chunks(DOC))
    assert p.heading == "Open project" and p.restated and p.chunk_no == 0
    assert p.passage == "An open project is a project whose current state is Project Created, Active or On Hold."


def test_text_only_when_the_model_lacks_a_field() -> None:
    gated = read()["Gated project"]
    assert gated is not None and gated.rule is None and not gated.expressible
    assert gated.gaps == ["The model has no field “stage gate” on Projects."]


def test_values_take_their_stored_spelling_and_a_missing_one_is_a_gap() -> None:
    [p, *_] = D.read_chunks(chunks(DOC))
    known = {"projects.current_state": {"project created": "Project Created", "active": "Active",
                                        "on hold": "On Hold", "completed": "Completed"}}
    d = D.interpret(p, SNAP, known)
    assert d is not None and d.expressible and d.rule == OPEN
    shouty = D.Passage(term="Open project", meaning="current state in ACTIVE, on hold, Parked", chunk_no=0,
                       passage="…", restated=True)
    d = D.interpret(shouty, SNAP, known)
    assert d is not None and d.rule and d.rule["values"] == ["Active", "On Hold", "Parked"]
    assert d.gaps == ["Current state has no value “Parked”."] and not d.expressible


def test_terms_match_measures_by_name_only() -> None:
    assert D.term_key("Open Projects") == "open project"
    assert D.measure_for("Open project", SNAP).key == "open_projects"  # type: ignore[union-attr]
    assert D.measure_for("active projects", SNAP).key == "open_projects"  # type: ignore[union-attr]  # a synonym
    assert D.measure_for("Stuck request", SNAP) is None  # not a measure's name: never a loose match


def test_the_measures_rule_is_compared_with_the_definition() -> None:
    open_projects = SNAP.measure("open_projects")
    assert open_projects is not None
    assert not D.same_rule(open_projects, OPEN)  # Status is Active, not Current state in …
    assert D.same_rule(open_projects, {"entity": "projects", "field": "status", "op": "in", "values": ["active"]})
    assert D.rule_words(OPEN, SNAP) == "Projects where Current state is one of Project Created, Active, On Hold"


def test_the_definitions_figure_replaces_the_measures_rule() -> None:
    spec = D.definition_spec(OPEN, SNAP.measure("open_projects"), SNAP, "Open project")
    adhoc = spec["measure"]["adhoc"]
    assert spec["measure"]["ref"] == "measure:_adhoc" and adhoc["agg"] == "count" and adhoc["entity"] == "projects"
    assert adhoc["where"] == {"field": "current_state", "op": "in", "values": OPEN["values"]}
    assert adhoc["plain_rule"].startswith("Projects where Current state")


def row(**over: object) -> dict[str, object]:
    return {"id": "d1", "term": "Open project", "meaning": "current state in Project Created, Active, On Hold",
            "rule": OPEN, "expressible": True, "gaps": [], "measure_ref": "measure:open_projects", "chunk_id": "c1",
            "document_id": "doc1", "document_title": "PMO definitions", "checks": None, **over}


def test_a_question_uses_the_term_or_its_measures_name() -> None:
    r = row()
    assert D.uses_term("How many open projects are there by region?", r, SNAP)
    assert D.uses_term("Show the active projects", r, SNAP)  # the measure's synonym
    assert not D.uses_term("How many projects are on hold?", r, SNAP)
    assert D.visible(r, {"projects": {"current_state"}}) and not D.visible(r, {"projects": {"status"}})


def test_definitions_open_ag03_context_and_are_never_cut() -> None:
    conflict = {"conflict": True, "measure": {"label": "Open projects", "rule": "Status is Active"}}
    text = D.definition_text(row(checks=conflict), SNAP)
    assert text.splitlines() == [
        "Open project means: current state in Project Created, Active, On Hold",
        "Rule: count Projects where Current state is one of Project Created, Active, On Hold.",
        "Differs from the certified measure Open projects (Status is Active).",
        "Approved definition, from “PMO definitions”."]
    defn = Hit(chunk_id="c1", document_id="doc1", kind=D.KIND, title="Definition: Open project", content=text,
               score=1.0, metadata={"definition_id": "d1"})
    long_doc = [Hit(chunk_id=f"x{i}", document_id="big", kind="uploaded_doc", title="Handbook",
                    content="open projects " * 200, score=1.0) for i in range(6)]
    block = context_block(long_doc, definitions=[defn, defn])
    assert block.index("## Business definitions") < block.index("## Documents")
    assert block.count('title="Definition: Open project"') == 1 and "Rule: count Projects where" in block
    assert "Text only" in D.definition_text(row(rule=None, expressible=False,
                                                gaps=["The model has no field “stage gate” on Projects."]), SNAP)
    assert context_block([], definitions=[defn]).count("## Business definitions") == 1


def test_a_stored_definition_is_read_again_on_a_new_model() -> None:
    d = D.reread(row(), SNAP)
    assert d.expressible and d.measure_ref == "measure:open_projects"
    gone = D.reread(row(rule={**OPEN, "field": "stage_gate"}), SNAP)
    assert not gone.expressible and gone.gaps == ["The model has no field “stage gate” on Projects."]
