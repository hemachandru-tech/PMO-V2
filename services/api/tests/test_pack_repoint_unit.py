"""New pack from a template, re-pointed to a schema (ENH-025, US-235; FEAT-075), no database.

A clone of PMO onto the TPG MySQL source (EXEC-115: the record state is ``record_status``) takes the confirmed source
names as candidates and then matches that source by name alone; one rename over the pack keeps every reference;
the items whose fields stay unmapped are listed by kind with the reason, and removing them leaves a valid pack.
"""

from __future__ import annotations

import copy
import json
from typing import Any

import pytest

from tela_api.knowledge.guidance import BlueprintsFile, GlossaryFile, RecipesFile
from tela_api.knowledge.patterns import ChartPattern
from tela_api.packs.manifest import PackManifest, bind, discover, match, source_manifest
from tela_api.packs.mapping import PackMapping
from tela_api.packs.source import KINDS, DbPackSource, FilePackSource
from tela_api.packs.studio.refactor import Renames, closure, references, remove, rename
from tela_api.packs.studio.repoint import add_candidates, unmapped
from tela_api.packs.studio.validate import validate
from tela_query_spec.expr import measure_refs
from tela_query_spec.model import Measure

_TYPES = {"text": "varchar", "integer": "int", "decimal": "decimal(12,2)", "date": "date", "datetime": "datetime",
          "boolean": "tinyint(1)"}
RENAMES = Renames.model_validate({
    "entities": {"projects": {"key": "initiatives", "label": "Initiatives"}},
    "fields": {"projects.status": {"key": "record_state", "label": "Record state"},
               "projects.project_request_id": {"key": "request_ref"}},
    "measures": {"open_projects": {"key": "live_projects", "label": "Live projects"},
                 "forecast_cost_total": {"label": "Forecast at completion"}},
})


@pytest.fixture(scope="module")
def pmo() -> tuple[dict[str, Any], dict[str, Any]]:
    src = FilePackSource(next(p for p in discover() if p.parent.name == "pmo").parent)
    return src.head(), {k: body for k in KINDS if (body := src.document(k)) is not None}


def _manifest(head: dict[str, Any], docs: dict[str, Any]) -> PackManifest:
    return source_manifest(DbPackSource("p", head["pack_key"], head["pack_version"], "t", "draft", head, docs))[0]


def _mysql(manifest: PackManifest, *, drop_tables: tuple[str, ...] = (),
           drop_columns: tuple[str, ...] = ()) -> list[dict[str, Any]]:
    """The TPG MySQL source of EXEC-115: every pack name as is, except the record state is ``record_status``."""
    out = []
    for e in manifest.entities:
        if e.key in drop_tables:
            continue
        cols = [{"name": f.columns[0], "type": _TYPES[f.data_type]} for f in e.fields
                if f"{e.key}.{f.key}" not in drop_columns]
        if e.key == "projects":
            cols = [{**c, "name": "record_status"} if c["name"] == "status" else c for c in cols]
        out.append({"qualified_name": f"tpg.{e.object_candidates[0]}", "columns": cols})
    return out


def _reads(manifest: PackManifest, result: Any, key_of: dict[str, str] | None = None) -> dict[str, set[str]]:
    """Measure key (as renamed back by ``key_of``) → the source columns its bound rule reads."""
    definition = bind(manifest, result).definition
    cols = {e["key"]: {f["key"]: f"{e['source_object']}.{f['source_column']}" for f in e["fields"]}
            for e in definition["entities"]}
    out = {}
    for m in definition["measures"]:
        refs = measure_refs(m["entity"], Measure.model_validate(m).expression)
        out[(key_of or {}).get(m["key"], m["key"])] = {cols[r.partition(".")[0]][r.partition(".")[2]] for r in refs}
    return out


# --------------------------------------------------------------------------- clone onto a connection


def test_clone_onto_tpg_mysql_adds_the_confirmed_names_as_candidates(pmo: tuple[dict[str, Any], dict[str, Any]]) -> None:
    head, docs = pmo
    manifest = _manifest(head, docs)
    catalog = _mysql(manifest)
    chosen = PackMapping(fields={"projects.status": "record_status"})
    before = match(manifest, catalog, mapping=chosen)
    clone, added = add_candidates(copy.deepcopy(head), before)
    assert added == [{"ref": "field:projects.status", "kind": "field", "name": "record_status"}]
    status = next(f for e in clone["entities"] if e["key"] == "projects" for f in e["fields"] if f["key"] == "status")
    assert status["columns"][-1] == "record_status" and status["columns"][0] == "status"  # added, never replacing

    # Next time the clone matches this source by name alone, binding the same columns: every mapped measure reads
    # what it read under the hand mapping, so its result is the same.
    again = match(_manifest(clone, docs), catalog)
    assert again.entities["projects"].mapping["status"] == "record_status"
    assert again.entities["projects"].how["status"] == "exact"
    assert _reads(_manifest(clone, docs), again) == _reads(manifest, before)
    assert not validate(clone, docs).errors


def test_exact_names_add_nothing(pmo: tuple[dict[str, Any], dict[str, Any]]) -> None:
    head, docs = pmo
    manifest = _manifest(head, docs)
    catalog = [{**t, "columns": [{**c, "name": "status"} if c["name"] == "record_status" else c for c in t["columns"]]}
               for t in _mysql(manifest)]
    clone, added = add_candidates(copy.deepcopy(head), match(manifest, catalog))
    assert added == [] and clone == head


# --------------------------------------------------------------------------- unmapped items


def test_unmapped_items_are_listed_by_kind_with_the_reason(pmo: tuple[dict[str, Any], dict[str, Any]]) -> None:
    head, docs = pmo
    manifest = _manifest(head, docs)
    catalog = _mysql(manifest, drop_tables=("timesheets",), drop_columns=("projects.cio",))
    result = match(manifest, catalog, mapping=PackMapping(fields={"projects.status": "record_status",
                                                                  "projects.region": None}))
    items = unmapped(manifest, result, bind(manifest, result), head)
    by_ref = {i["ref"]: i for i in items}
    kinds = [i["kind"] for i in items]
    assert kinds == sorted(kinds, key=["entity", "field", "measure", "dimension", "report"].index)  # grouped by kind

    assert by_ref["entity:timesheets"]["reason"] == "No table found on this source."
    assert by_ref["field:projects.cio"]["reason"] == "No column found on this source."
    assert by_ref["field:projects.region"]["reason"] == "Marked as not in this source."
    assert not [r for r in by_ref if r.startswith("field:timesheets.")]  # a missing table is one item, not its fields
    cio = by_ref["dimension:cio"]
    assert cio["kind"] == "dimension" and cio["reason"].startswith("Needs ") and "CIO" in cio["reason"]
    hours = [i for i in items if i["kind"] == "measure" and "timesheets (whole table)" in i["reason"]]
    assert hours, "measures on timesheets are listed with the missing table"
    # Removing a field takes what reads it with it: the preview says so.
    assert "dimension:cio" in by_ref["field:projects.cio"]["removes_also"]
    # Reports the pack itself marks unavailable (its data gaps) are not this source's doing: not listed.
    gaps = {f"report:{bp.id}" for bp in BlueprintsFile.model_validate(docs["report_blueprints"]).blueprints
            if bp.unavailable}
    assert gaps and not gaps & set(by_ref)
    # Everything bound is not listed.
    assert "measure:open_projects" not in by_ref and "field:projects.status" not in by_ref


def test_removing_unmapped_items_leaves_a_valid_pack(pmo: tuple[dict[str, Any], dict[str, Any]]) -> None:
    head, docs = pmo
    refs = {"entity:timesheets", "field:projects.cio"}
    gone = closure(head, refs)
    assert {"dimension:cio", "entity:timesheets"} <= gone
    assert any(r.startswith("measure:") for r in gone)
    new_head, new_docs = remove(copy.deepcopy(head), copy.deepcopy(docs), refs)
    report = validate(new_head, new_docs)
    assert not report.errors, report.errors[:3]
    assert len(report.warnings) <= len(validate(head, docs).warnings)
    left = references(new_head, new_docs)
    assert not [r for r in left if r.startswith(("entity:timesheets", "field:timesheets.", "field:projects.cio"))]
    assert not [r for r in left if r in gone]
    assert "cio" not in new_head["dashboards"]["projects"]["filters"]
    recipes = RecipesFile.model_validate(new_docs["analysis_recipes"])
    assert not [r for r in recipes.recipes if "cio" in r.keys("dimension")]
    # A blueprint is never left half-built: one whose figures went is unavailable, naming what it lost.
    for bp in BlueprintsFile.model_validate(new_docs["report_blueprints"]).blueprints:
        measures, dims = bp.refs()
        assert not ({f"measure:{m}" for m in measures} | {f"dimension:{d}" for d in dims}) & gone
    manifest = _manifest(new_head, new_docs)
    assert manifest.entity("timesheets") is None


def test_removing_a_key_field_is_refused(pmo: tuple[dict[str, Any], dict[str, Any]]) -> None:
    head, docs = pmo
    with pytest.raises(ValueError, match="key"):
        remove(copy.deepcopy(head), copy.deepcopy(docs), {"field:projects.project_id"})
    with pytest.raises(ValueError, match="no measure"):
        remove(copy.deepcopy(head), copy.deepcopy(docs), {"measure:nope"})


# --------------------------------------------------------------------------- rename


def test_a_rename_keeps_every_reference(pmo: tuple[dict[str, Any], dict[str, Any]]) -> None:
    head, docs = pmo
    before = references(head, docs)
    new_head, new_docs = rename(copy.deepcopy(head), copy.deepcopy(docs), RENAMES)

    # Every reference site is still there, pointing at the new key.
    def renamed(ref: str) -> str:
        kind, _, key = ref.partition(":")
        if kind == "measure":
            return f"measure:{'live_projects' if key == 'open_projects' else key}"
        if kind == "entity":
            return f"entity:{'initiatives' if key == 'projects' else key}"
        ent, _, fk = key.partition(".")
        fk = {"projects.status": "record_state", "projects.project_request_id": "request_ref"}.get(key, fk)
        return f"field:{'initiatives' if ent == 'projects' else ent}.{fk}"

    expected: dict[str, int] = {}
    for ref, n in before.items():
        expected[renamed(ref)] = expected.get(renamed(ref), 0) + n
    assert references(new_head, new_docs) == expected
    assert sum(before.values()) > 1000  # the whole pack is walked: manifest, blueprints, patterns, recipes, glossary

    # Nothing names the old keys any more (dashboards keep their own key "projects": it is not the entity).
    text = json.dumps([new_head, new_docs])
    for old in ('"open_projects"', '"measure:open_projects"', '"projects.', '"entity": "projects"',
                '"from_entity": "projects"', '"to_entity": "projects"', '"primary_entity": "projects"'):
        assert old not in text, old
    assert new_head["dashboards"]["projects"]["title"] == head["dashboards"]["projects"]["title"]

    # Labels follow; the checks find nothing new.
    m = {x["key"]: x for x in new_head["measures"]}
    assert m["live_projects"]["label"] == "Live projects" and m["forecast_cost_total"]["label"] == "Forecast at completion"
    ent = next(e for e in new_head["entities"] if e["key"] == "initiatives")
    assert ent["label"] == "Initiatives"
    assert next(f for f in ent["fields"] if f["key"] == "record_state")["label"] == "Record state"
    report = validate(new_head, new_docs)
    assert not report.errors and len(report.warnings) == len(validate(head, docs).warnings)

    # The knowledge documents read the new keys.
    glossary = GlossaryFile.model_validate(new_docs["glossary"])
    assert not [t for t in glossary.terms if any(f.startswith("projects.") for f in t.fields)]
    patterns = [ChartPattern.model_validate(p) for p in new_docs["chart_patterns"]["patterns"]]
    assert any("live_projects" in p.refs()[0] for p in patterns) or not any(
        "open_projects" in ChartPattern.model_validate(p).refs()[0] for p in docs["chart_patterns"]["patterns"])
    bps = BlueprintsFile.model_validate(new_docs["report_blueprints"])
    assert any("live_projects" in bp.refs()[0] for bp in bps.blueprints)

    # Bound on the same source the renamed pack reads the same columns for every measure.
    manifest, new_manifest = _manifest(head, docs), _manifest(new_head, new_docs)
    catalog = _mysql(manifest)
    mapping = PackMapping(fields={"projects.status": "record_status"})
    new_mapping = PackMapping(fields={"initiatives.record_state": "record_status"})
    back = {"live_projects": "open_projects"}
    assert _reads(new_manifest, match(new_manifest, catalog, mapping=new_mapping), back) == \
        _reads(manifest, match(manifest, catalog, mapping=mapping))
    assert len(new_manifest.reports) == len(manifest.reports)


def test_a_rename_refuses_what_would_break(pmo: tuple[dict[str, Any], dict[str, Any]]) -> None:
    head, docs = pmo

    def attempt(spec: dict[str, Any]) -> None:
        rename(copy.deepcopy(head), copy.deepcopy(docs), Renames.model_validate(spec))

    with pytest.raises(ValueError, match="already"):
        attempt({"measures": {"open_projects": {"key": "in_delivery"}}})
    with pytest.raises(ValueError, match="already"):
        attempt({"fields": {"projects.status": {"key": "current_state"}}})
    with pytest.raises(ValueError, match="no field"):
        attempt({"fields": {"projects.nope": {"key": "other"}}})
    with pytest.raises(ValueError, match="health"):  # the health calculator reads this field by its name
        attempt({"fields": {"projects.planned_deployment_date": {"key": "go_live"}}})
    with pytest.raises(ValueError):
        attempt({"entities": {"projects": {"key": "Not A Key"}}})
