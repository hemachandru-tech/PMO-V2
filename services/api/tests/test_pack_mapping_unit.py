"""Mapping a starter pack to a source with other names (US-154; FEAT-059): matching and binding, no database."""

from __future__ import annotations

from typing import Any

import pytest

from tela_api.packs.manifest import PackManifest, bind, discover, load_manifest, match
from tela_api.packs.mapping import PackMapping, check, pack_values, rewrite_values, step


@pytest.fixture(scope="module")
def manifest() -> PackManifest:
    return load_manifest(next(p for p in discover() if p.parent.name == "pmo"))[0]


def _col(name: str, type_: str = "text") -> dict[str, str]:
    return {"name": name, "type": type_}


def _dataverse() -> list[dict[str, Any]]:
    """A TPG-like Dataverse export: publisher prefixes, choice/lookup codes next to their labels, a plural table."""
    return [
        {"qualified_name": "dataverse.tpg_project", "columns": [
            _col("tpg_projectid", "uuid"), _col("tpg_projectname"), _col("statecodename"), _col("statuscodename"),
            _col("versionnumber", "bigint"),  # a Dataverse system column: publisher prefixes apply
            _col("tpgmea_region", "integer"), _col("tpgmea_regionname"),
            _col("tpgmea_customcio", "uuid"), _col("tpgmea_customcioname"),
            _col("tpg_portfolio", "uuid"), _col("tpg_portfolioname"),
            _col("tpg_overallkpi", "integer"), _col("tpg_overallkpiname"),
            _col("tpg_schedulekpi", "integer"), _col("tpg_schedulekpiname"),
            _col("tpgmea_priority", "integer"),  # a choice code without its label column: not a text field
            _col("tpgmea_platformname"),  # a label column exported without its code column
            _col("tpgmea_golivedate", "timestamp with time zone"),  # a go-live column the pack doesn't name
            _col("tpgmea_projectputonholddate", "timestamp with time zone"),
            _col("tpgmea_approvedpcrcounter", "integer"),
            _col("tpg_projectrequest", "uuid")]},
        {"qualified_name": "dataverse.tpg_projectrequests", "columns": [
            _col("tpg_projectrequestsid", "uuid"), _col("tpg_title"), _col("statuscodename"),
            _col("tpgmea_region", "integer"), _col("tpgmea_regionname")]},
    ]


def test_dataverse_names_are_found_without_a_mapping(manifest: PackManifest) -> None:
    r = match(manifest, _dataverse())
    proj, req = r.entities["projects"], r.entities["project_requests"]
    assert proj.object == "dataverse.tpg_project" and proj.object_how == "exact"
    assert req.object == "dataverse.tpg_projectrequests" and req.object_how == "exact"  # named by pack 2.0
    assert proj.mapping["region"] == "tpgmea_regionname" and proj.how["region"] == "dataverse_label"
    assert proj.mapping["cio"] == "tpgmea_customcioname" and proj.how["cio"] == "dataverse_label"
    assert proj.mapping["portfolio"] == "tpg_portfolioname" and proj.how["portfolio"] == "similar_name"
    assert proj.mapping["name"] == "tpg_projectname"  # entity word + field word
    assert proj.mapping["approved_pcr_counter"] == "tpgmea_approvedpcrcounter"
    assert req.mapping["project_request_id"] == "tpg_projectrequestsid"  # <table>id primary key
    assert proj.mapping["platform"] == "tpgmea_platformname" and proj.how["platform"] == "dataverse_label"
    assert "priority" not in proj.mapping  # integer choice code does not fit a text field
    assert "planned_deployment_date" in proj.missing_required and not r.matched
    assert 0 < proj.confidence < 1


def test_model_labels_find_a_field(manifest: PackManifest) -> None:
    model = {"entities": [{"key": "tpg_project", "label": "Projects", "source_object": "dataverse.tpg_project",
                           "fields": [{"key": "go_live", "label": "Planned deployment date",
                                       "source_column": "tpgmea_golivedate"}]}]}
    r = match(manifest, _dataverse(), model=model)
    proj = r.entities["projects"]
    assert proj.mapping["planned_deployment_date"] == "tpgmea_golivedate"
    assert proj.how["planned_deployment_date"] == "model_label"
    assert r.matched


def test_admin_mapping_and_not_in_source(manifest: PackManifest) -> None:
    mapping = PackMapping(fields={"projects.planned_deployment_date": "tpgmea_golivedate",
                                  "projects.on_hold_date": "tpgmea_projectputonholddate",
                                  "projects.region": None})
    r = match(manifest, _dataverse(), mapping=mapping)
    proj = r.entities["projects"]
    assert r.matched and proj.how["planned_deployment_date"] == "mapped"
    assert "region" not in proj.mapping and proj.how["region"] == "not_in_source"
    assert check(manifest, _dataverse(), mapping, r) == []
    items = {(i["kind"], i["key"]): i for i in bind(manifest, r).items}
    by_region = [i for i in items.values() if i["kind"] == "dimension" and i["availability"] == "unavailable"
                 and any("Region" in g for g in i["gaps"])]
    assert by_region, "items needing a field marked 'not in this source' are Unavailable with that gap"
    shown = step(manifest, _dataverse(), r, mapping)
    row = next(f for e in shown["entities"] if e["key"] == "projects" for f in e["fields"] if f["key"] == "region")
    assert row["how"] == "not_in_source" and row["column"] is None
    assert any(c["name"] == "tpgmea_regionname" for c in shown["entities"][0]["columns"])


def test_mapping_check_names_bad_choices(manifest: PackManifest) -> None:
    bad = PackMapping(entities={"nope": "x"}, fields={"projects.planned_deployment_date": "tpgmea_regionname",
                                                      "projects.region": "no_such_column",
                                                      "projects.unknown": "tpg_title"})
    r = match(manifest, _dataverse(), mapping=bad)
    refs = {i["ref"]: i["message"] for i in check(manifest, _dataverse(), bad, r)}
    assert "entity:nope" in refs and "field:projects.unknown" in refs
    assert "needs date" in refs["field:projects.planned_deployment_date"]
    assert "not a column" in refs["field:projects.region"]


def test_value_mapping_rewrites_rules_and_labels(manifest: PackManifest) -> None:
    assert {"Green", "Amber", "Red"} <= set(pack_values(manifest)["projects.overall_kpi"])
    vm = {"G": "Green", "Y": "Amber", "R": "Red", "": "Not set"}
    mapping = PackMapping(fields={"projects.planned_deployment_date": "tpgmea_golivedate"},
                          values={"projects.schedule_kpi": vm, "projects.overall_kpi": vm})
    r = match(manifest, _dataverse(), mapping=mapping)
    b = bind(manifest, r)
    dims = {d["key"]: d for d in b.definition["dimensions"]}
    assert dims["schedule_status"]["value_labels"] == {"G": "Green", "Y": "Amber", "R": "Red", "": "Not set",
                                                      "Yellow": "Amber"}
    sched = next(m for m in b.definition["measures"] if "schedule_kpi" in str(m["expression"]))
    cond = next(c for c in sched["expression"]["where"]["all"] if c["field"] == "schedule_kpi")
    assert cond == {"field": "schedule_kpi", "op": "in", "values": ["Red", "Yellow", "Amber", "R", "Y"]}


def test_rewrite_handles_equal_blank_and_negation() -> None:
    vm = {"kpi": {"Yellow": "Amber", "": "Amber"}}
    eq = rewrite_values({"field": "kpi", "op": "=", "value": "Amber"}, vm)
    assert eq == {"any": [{"field": "kpi", "op": "in", "values": ["Amber", "Yellow", ""]},
                          {"field": "kpi", "op": "is_null"}]}
    ne = rewrite_values({"agg": "count", "where": {"field": "kpi", "op": "!=", "value": "Amber"}}, vm)
    assert ne["where"] == {"field": "kpi", "op": "not_in", "values": ["Amber", "Yellow", ""]}
    untouched = {"field": "kpi", "op": "=", "value": "Green"}
    assert rewrite_values(untouched, vm) == untouched


def test_prefixes_are_only_set_aside_on_a_dataverse_source(manifest: PackManifest) -> None:
    other = [{"qualified_name": "tpg.projects", "columns": [
        _col("project_id"), _col("variant_status"), _col("record_status"), _col("current_state")]}]
    r = match(manifest, other)
    assert "status" not in r.entities["projects"].mapping  # variant_status is not "status"
    mapped = match(manifest, other, mapping=PackMapping(fields={"projects.status": "record_status"}))
    assert mapped.entities["projects"].mapping["status"] == "record_status"


def test_exact_names_still_win(manifest: PackManifest) -> None:
    catalog = [{"qualified_name": f"public.{e.object_candidates[0]}",
                "columns": [{"name": f.columns[0]} for f in e.fields]} for e in manifest.entities]
    r = match(manifest, catalog)
    assert r.matched and r.confidence == 1.0
    assert all(h == "exact" for e in r.entities.values() for h in e.how.values())
