"""Applying a starter pack onto an existing model (US-153; BUG-004): the merge plan, no database."""

from __future__ import annotations

import copy
from typing import Any

import pytest

from tela_api.packs.apply import plan
from tela_api.packs.manifest import Binding, PackManifest, bind, discover, load_manifest, match


@pytest.fixture(scope="module")
def pack() -> tuple[PackManifest, Binding]:
    path = next(p for p in discover() if p.parent.name == "pmo")
    manifest, _raw, _sha = load_manifest(path)
    catalog = [{"qualified_name": f"public.{e.object_candidates[0]}",
                "columns": [{"name": f.columns[0]} for f in e.fields]} for e in manifest.entities]
    return manifest, bind(manifest, match(manifest, catalog), reporting_date="2026-10-01")


def _empty() -> dict[str, Any]:
    return {"entities": [], "links": [], "measures": [], "dimensions": [], "settings": {}}


def test_empty_model_gets_the_whole_pack(pack: tuple[PackManifest, Binding]) -> None:
    manifest, binding = pack
    p = plan(_empty(), binding, manifest)
    assert p.clashes == [] and p.same_source == []
    assert p.added["measures"] == len(binding.definition["measures"])
    assert {e["key"] for e in p.definition["entities"]} == {e["key"] for e in binding.definition["entities"]}
    assert p.definition["settings"]["reporting_date"] == "2026-10-01"
    assert p.out()["reports"]["available"] == sum(1 for i in binding.items
                                                 if i["kind"] == "report" and i["availability"] == "available")


def test_admins_own_items_and_wording_are_kept(pack: tuple[PackManifest, Binding]) -> None:
    manifest, binding = pack
    base = copy.deepcopy(binding.definition)
    projects = next(e for e in base["entities"] if e["key"] == "projects")
    projects["label"] = "Our projects"
    projects["fields"][0]["label"] = "Our label"
    projects["fields"].append({"key": "my_note", "source_column": "my_note", "label": "Note", "data_type": "string"})
    base["measures"] = [m for m in base["measures"] if m["key"] != "open_projects"]
    base["measures"].append({"key": "my_count", "label": "My count", "entity": "projects", "plain_rule": "All.",
                             "expression": {"agg": "count"}})
    base["settings"] = {"currency": "USD"}

    p = plan(base, binding, manifest)
    ent = next(e for e in p.definition["entities"] if e["key"] == "projects")
    assert ent["label"] == "Our projects" and ent["fields"][0]["label"] == "Our label"
    assert any(f["key"] == "my_note" for f in ent["fields"])
    keys = {m["key"] for m in p.definition["measures"]}
    assert {"my_count", "open_projects"} <= keys
    assert p.added["measures"] == 1 and p.clashes == []
    assert p.definition["settings"] == {"currency": "USD", "reporting_date": "2026-10-01"}


def test_clashes_take_the_pack_unless_kept(pack: tuple[PackManifest, Binding]) -> None:
    manifest, binding = pack
    base = copy.deepcopy(binding.definition)
    mine = next(m for m in base["measures"] if m["key"] == "open_projects")
    mine["plain_rule"] = "Our own rule."
    mine["expression"] = {"agg": "count"}

    taken = plan(base, binding, manifest)
    assert [c["ref"] for c in taken.clashes] == ["measure:open_projects"]
    assert taken.clashes[0]["choice"] == "pack" and taken.clashes[0]["mine"] == "Our own rule."
    assert next(m for m in taken.definition["measures"] if m["key"] == "open_projects")["plain_rule"] != "Our own rule."

    kept = plan(base, binding, manifest, {"measure:open_projects"})
    assert kept.clashes[0]["choice"] == "mine"
    assert next(m for m in kept.definition["measures"] if m["key"] == "open_projects")["plain_rule"] == "Our own rule."


def test_keeping_a_field_drops_the_pack_items_that_need_it(pack: tuple[PackManifest, Binding]) -> None:
    manifest, binding = pack
    base = copy.deepcopy(binding.definition)
    projects = next(e for e in base["entities"] if e["key"] == "projects")
    projects["fields"] = [f for f in projects["fields"] if f["key"] != "planned_deployment_date"]
    projects["fields"].append({"key": "planned_deployment_date", "source_column": "go_live_text",
                               "label": "Go-live", "data_type": "string"})

    p = plan(base, binding, manifest, {"field:projects.planned_deployment_date"})
    assert "field:projects.planned_deployment_date" in [c["ref"] for c in p.clashes]
    # Pack measures that need the field were in the model already, so they stay the Admin's; take them out first.
    base["measures"] = []
    p = plan(base, binding, manifest, {"field:projects.planned_deployment_date"})
    keys = {m["key"] for m in p.definition["measures"]}
    assert "overdue_go_lives" not in keys
    gone = {i["key"]: i for i in p.items if i["availability"] == "unavailable"}
    assert "overdue_go_lives" in gone and gone["overdue_go_lives"]["gaps"]
    # US-155: a report keeps the widgets the merged model can run and names the others; it is unavailable only
    # when none is left (every Delayed projects widget needs the planned date).
    reports = {i["key"]: i for i in p.items if i["kind"] == "report"}
    assert reports["projects_overview"]["availability"] == "available"
    assert "measure overdue_go_lives" in reports["projects_overview"]["gaps"]
    assert "delayed_projects" in gone and "measure overdue_go_lives" in gone["delayed_projects"]["gaps"]


def test_entity_on_a_different_table_is_a_clash_and_same_table_is_noted(pack: tuple[PackManifest, Binding]
                                                                         ) -> None:
    manifest, binding = pack
    other = {"key": "projects", "label": "Projects", "source_object": "public.something_else", "primary_key": ["id"],
             "fields": [{"key": "id", "source_column": "id", "label": "Id", "data_type": "string"}]}
    p = plan({**_empty(), "entities": [other]}, binding, manifest, {"entity:projects"})
    assert p.clashes[0]["ref"] == "entity:projects" and p.clashes[0]["choice"] == "mine"
    assert next(e for e in p.definition["entities"] if e["key"] == "projects")["source_object"] == "public.something_else"
    assert any(i["key"] == "open_projects" and i["availability"] == "unavailable" for i in p.items)

    projects = next(e for e in binding.definition["entities"] if e["key"] == "projects")
    alias = {**copy.deepcopy(projects), "key": "tpg_project"}
    p = plan({**_empty(), "entities": [alias]}, binding, manifest)
    assert p.same_source == [{"pack_entity": "projects", "model_entity": "tpg_project",
                              "source_object": projects["source_object"]}]


def test_a_discovered_field_on_a_pack_column_is_folded_into_the_pack_field(pack: tuple[PackManifest, Binding]
                                                                            ) -> None:
    """BUG-016: a model discovered from the source names fields after their columns; applying the pack must not add
    its field beside the discovered one on the same column (US-199's guard then blocks approval)."""
    from tela_api.model.guards import shared_columns

    manifest, binding = pack
    base = copy.deepcopy(binding.definition)
    projects = next(e for e in base["entities"] if e["key"] == "projects")
    status = next(f for f in projects["fields"] if f["key"] == "status")
    projects["fields"] = [f for f in projects["fields"] if f["key"] != "status"]
    discovered = {"key": "col_status", "source_column": status["source_column"].upper(), "label": "Col status",
                  "data_type": status["data_type"], "description": "From the source."}
    projects["fields"].append(discovered)
    projects["display_fields"] = ["col_status"]
    base["measures"], base["dimensions"] = [], []

    p = plan(base, binding, manifest)
    ent = next(e for e in p.definition["entities"] if e["key"] == "projects")
    keys = [f["key"] for f in ent["fields"]]
    assert "status" in keys and "col_status" not in keys
    assert next(f for f in ent["fields"] if f["key"] == "status")["description"] == "From the source."
    assert "status" in ent["display_fields"] and "col_status" not in ent["display_fields"]
    assert p.folded == [{"entity": "projects", "column": discovered["source_column"], "mine": "col_status",
                         "mine_label": "Col status", "pack": "status", "pack_label": status["label"]}]
    assert p.same_column == [] and shared_columns(p.definition) == []
    assert "description" not in next(f for f in next(e for e in binding.definition["entities"]
                                                      if e["key"] == "projects")["fields"] if f["key"] == "status")

    # Re-applying onto a draft that already holds both (the EXEC-115 / BUG-016 drafts) cleans it up too.
    both = copy.deepcopy(binding.definition)
    next(e for e in both["entities"] if e["key"] == "projects")["fields"].append({**discovered, "description": ""})
    assert shared_columns(both) and shared_columns(plan(both, binding, manifest).definition) == []


def test_a_discovered_field_the_model_uses_is_kept_and_named(pack: tuple[PackManifest, Binding]) -> None:
    manifest, binding = pack
    base = copy.deepcopy(binding.definition)
    projects = next(e for e in base["entities"] if e["key"] == "projects")
    status = next(f for f in projects["fields"] if f["key"] == "status")
    projects["fields"].append({"key": "col_status", "source_column": status["source_column"], "label": "Col status",
                               "data_type": status["data_type"]})
    base["measures"].append({"key": "my_active", "label": "My active", "entity": "projects", "plain_rule": "Active.",
                             "expression": {"agg": "count", "where": {"field": "col_status", "op": "=",
                                                                     "value": "Active"}}})

    p = plan(base, binding, manifest)
    ent = next(e for e in p.definition["entities"] if e["key"] == "projects")
    assert {"status", "col_status"} <= {f["key"] for f in ent["fields"]}
    assert [(s["mine"], s["pack"]) for s in p.same_column] == [("col_status", "status")] and p.folded == []
    assert p.out()["same_column"] == p.same_column
