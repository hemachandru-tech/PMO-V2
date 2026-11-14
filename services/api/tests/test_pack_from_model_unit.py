"""A pack from an approved model, without a database (ENH-025, US-236): what is taken, what is left out and why."""

from __future__ import annotations

from typing import Any

from tela_api.packs.manifest import PackManifest
from tela_api.packs.source import DbPackSource
from tela_api.packs.studio.from_model import Built, Model, _cut_indexed, _refs, manifest_from_model, pack_snapshot
from tela_api.packs.studio.validate import validate

COUNT = {"agg": "count"}


def _definition() -> dict[str, Any]:
    return {
        "entities": [
            {"key": "projects", "label": "Projects", "source_object": "public.projects", "primary_key": ["project_id"],
             "display_fields": ["name"], "description": "One row per project", "source_checksum": "abc",
             "fields": [
                 {"key": "project_id", "source_column": "project_id", "label": "Project id", "data_type": "text",
                  "semantic_type": "identifier"},
                 {"key": "name", "source_column": "project_name", "label": "Name", "data_type": "text"},
                 {"key": "manager", "source_column": "pm_name", "label": "Manager", "data_type": "text",
                  "semantic_type": "person_name", "sensitivity": "personal"},
                 {"key": "status", "source_column": "status", "label": "Status", "data_type": "text",
                  "source_type": "text"},
                 {"key": "notes", "source_column": "notes", "label": "Notes", "data_type": "text",
                  "is_queryable": False},
                 {"key": "region", "source_column": "region", "label": "Region", "data_type": "text"},
             ]},
            {"key": "risks", "label": "Risks", "source_object": "public.risks", "primary_key": ["risk_id"],
             "fields": [
                 {"key": "risk_id", "source_column": "risk_id", "label": "Risk id", "data_type": "text"},
                 {"key": "project_id", "source_column": "project_id", "label": "Project", "data_type": "text"},
             ]},
        ],
        "links": [
            {"from_entity": "risks", "to_entity": "projects", "from_fields": ["project_id"],
             "to_fields": ["project_id"], "cardinality": "many_to_one", "origin": "pack", "accepted": True,
             "inherits_scope": True},
            {"from_entity": "projects", "to_entity": "risks", "from_fields": ["name"], "to_fields": ["risk_id"],
             "cardinality": "one_to_many", "origin": "proposed", "accepted": False},
        ],
        "measures": [
            {"key": "open_projects", "label": "Open projects", "entity": "projects", "plain_rule": "Status Active.",
             "expression": {"agg": "count", "where": {"field": "status", "op": "=", "value": "Active"}}},
            {"key": "old_count", "label": "Old count", "entity": "projects", "plain_rule": "Every project.",
             "expression": COUNT, "status": "deprecated"},
            {"key": "total_risks", "label": "Risks", "entity": "risks", "plain_rule": "Every risk.",
             "expression": COUNT},
        ],
        "dimensions": [{"key": "region", "label": "Region", "entity": "projects", "field": "region"}],
        "settings": {"reporting_date": "2026-09-30"},
    }


def _model(pack: DbPackSource | None = None) -> Model:
    return Model("c1", "TPG", "postgres", "v1", 3, "m1", _definition(), pack)


def _template() -> DbPackSource:
    head = {"pack_key": "pmo", "signature": {"primary_entity": "projects"}, "entities": [
        {"key": "projects", "label": "Projects", "object_candidates": ["projects", "tpg_project"],
         "synonyms": ["initiatives"], "primary_key": ["project_id"], "fields": [
             {"key": "status", "label": "Status", "data_type": "text", "columns": ["status", "statecodename"],
              "values": ["Active", "On Hold"], "synonyms": ["state"]}]}]}
    return DbPackSource("p1", "pmo", "1.0.0", None, "released", head, {})


def test_entities_fields_links_and_measures() -> None:
    built = manifest_from_model(_model(_template()), key="acme", title="Acme", version="0.1.0",
                                released_at="2026-11-06T00:00:00Z")
    head = built.head
    PackManifest.model_validate(head)
    assert validate(head, {}).errors == []
    projects = head["entities"][0]
    # Source names first, then the template pack's candidates; its synonyms and values come along.
    assert projects["object_candidates"] == ["projects", "tpg_project"] and projects["synonyms"] == ["initiatives"]
    fields = {f["key"]: f for f in projects["fields"]}
    assert fields["status"]["columns"] == ["status", "statecodename"]
    assert fields["status"]["values"] == ["Active", "On Hold"] and fields["status"]["synonyms"] == ["state"]
    assert fields["name"]["columns"] == ["project_name"] and fields["project_id"]["required"]
    assert fields["manager"]["sensitivity"] == "personal" and fields["manager"]["semantic_type"] == "person_name"
    assert "notes" not in fields  # hidden from queries and read by nothing
    assert "description" not in projects and "source_checksum" not in projects
    assert head["links"] == [{"from_entity": "risks", "to_entity": "projects", "from_fields": ["project_id"],
                              "to_fields": ["project_id"], "cardinality": "many_to_one", "inherits_scope": True}]
    assert [m["key"] for m in head["measures"]] == ["open_projects", "total_risks"]
    assert head["signature"] == {"primary_entity": "projects", "min_confidence": 0.6}
    assert {(x["kind"], x["key"]) for x in built.left_out} == {
        ("measure", "old_count"), ("field", "projects.notes"), ("link", "projects → risks")}


def test_uncertified_measure_taken_when_chosen_and_primary_without_a_template() -> None:
    built = manifest_from_model(_model(), key="acme", title="Acme", version="0.1.0",
                                released_at="2026-11-06T00:00:00Z", measures=["old_count"])
    head = built.head
    assert [m["key"] for m in head["measures"]] == ["open_projects", "old_count", "total_risks"]
    assert head["measures"][1]["status"] == "deprecated"  # still uncertified in the pack
    assert head["signature"]["primary_entity"] == "projects"  # the entity with most measures
    assert head["entities"][0]["object_candidates"] == ["projects"]


def test_a_hidden_field_a_rule_reads_is_kept() -> None:
    model = _model()
    model.definition["measures"].append({"key": "with_notes", "label": "With notes", "entity": "projects",
                                         "plain_rule": "Notes set.", "expression": {
                                             "agg": "count", "where": {"field": "notes", "op": "not_null"}}})
    built = manifest_from_model(model, key="acme", title="Acme", version="0.1.0", released_at="x")
    assert "notes" in {f["key"] for f in built.head["entities"][0]["fields"]}


def test_knowledge_is_cut_to_what_the_pack_has() -> None:
    model = _model()
    built = manifest_from_model(model, key="acme", title="Acme", version="0.1.0", released_at="x")
    snap = pack_snapshot(model, built.head)
    glossary = {"version": "0.2.0", "status": "draft", "terms": [
        {"term": "PM", "means": "project manager", "explain": "The manager.", "fields": ["projects.manager"]},
        {"term": "S2", "means": "planning stage", "explain": "Stage two.", "fields": ["projects.stage_end"]},
        {"term": "active", "means": "open", "explain": "Open ones.", "reading": "open_projects",
         "instead_of": ["old_count"]},
    ]}
    cut = _cut_indexed("glossary", glossary, snap, built)
    assert cut is not None
    assert [t["term"] for t in cut["terms"]] == ["PM", "active"] and cut["terms"][1]["instead_of"] == []
    rules = {"version": "0.1.0", "rules": [
        {"id": "r1", "title": "One", "rule": "About open projects.", "measures": ["open_projects"]},
        {"id": "r2", "title": "Two", "rule": "About the old count.", "measures": ["old_count"]}]}
    cut = _cut_indexed("dashboard_rules", rules, snap, built)
    assert cut is not None and [r["id"] for r in cut["rules"]] == ["r1"]
    left = {(x["kind"], x["key"]) for x in built.left_out}
    assert {("glossary", "S2"), ("dashboard_rule", "r2")} <= left


def test_refs_of_a_query_spec() -> None:
    spec = {"measure": {"ref": "measure:a"}, "breakdowns": [{"ref": "dimension:b"}],
            "filters": [{"ref": "dimension:c", "op": "in", "values": ["x"]}], "time": {"ref": "dimension:d"}}
    assert _refs(spec) == {"measure:a", "dimension:b", "dimension:c", "dimension:d"}
    assert Built({}, {}).left_out == []
