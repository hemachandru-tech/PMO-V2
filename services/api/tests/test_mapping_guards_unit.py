"""Mapping guards at install and approval (US-199; FEAT-067): the EXEC-115 mapping is stopped, no database."""

from __future__ import annotations

from typing import Any

import pytest
from pmo_oracle import open_by_status

from tela_api.model import guards
from tela_api.model.store import build_snapshot
from tela_api.packs.manifest import PackManifest, bind, discover, load_manifest, match
from tela_api.packs.mapping import PackMapping, conflicts, pack_values, profile_definition, step

_TYPES = {"text": "varchar", "integer": "int", "decimal": "decimal(12,2)", "date": "date", "datetime": "datetime",
          "boolean": "tinyint(1)"}


@pytest.fixture(scope="module")
def manifest() -> PackManifest:
    """The PMO pack with open = Status Active, as 2.1.0 had RULE-026 (DEC-24): Status is filtered by its values."""
    pack = load_manifest(next(p for p in discover() if p.parent.name == "pmo"))[0]
    return PackManifest.model_validate(open_by_status(pack.model_dump()))


def _mysql(manifest: PackManifest) -> list[dict[str, Any]]:
    """The TPG MySQL source of EXEC-115: every pack name as is, except the record state is ``record_status``."""
    out = []
    for e in manifest.entities:
        cols = [{"name": f.columns[0], "type": _TYPES[f.data_type]} for f in e.fields]
        if e.key == "projects":
            cols = [{**c, "name": "record_status"} if c["name"] == "status" else c for c in cols]
        out.append({"qualified_name": f"tpg.{e.object_candidates[0]}", "columns": cols})
    return out


def _profile(*values: tuple[str, int]) -> list[dict[str, Any]]:
    return [{"value": v, "rows": n} for v, n in values]


CURRENT_STATE = _profile(("Active", 279), ("Completed", 120), ("On Hold", 79), ("Project Created", 73))
RECORD_STATUS = _profile(("Active", 420), ("Inactive", 131))


def test_exec115_mapping_is_blocked_with_the_reason(manifest: PackManifest) -> None:
    catalog = _mysql(manifest)
    r = match(manifest, catalog, mapping=PackMapping(fields={"projects.status": "current_state"}))
    proj = r.entities["projects"]
    # The exact-name match no longer gives current_state to Current state too: it is taken by the Admin's choice.
    assert proj.mapping["status"] == "current_state" and "current_state" not in proj.mapping
    assert proj.how["current_state"] == "column_taken"
    issues = conflicts(manifest, r)
    assert [i["ref"] for i in issues] == ["field:projects.current_state"]
    assert "current_state" in issues[0]["message"] and "Status" in issues[0]["message"]
    rows = {f["key"]: f for e in step(manifest, catalog, r, PackMapping())["entities"] if e["key"] == "projects"
            for f in e["fields"]}
    assert rows["current_state"]["how"] == "column_taken"


def test_two_fields_mapped_to_one_column_by_hand(manifest: PackManifest) -> None:
    r = match(manifest, _mysql(manifest), mapping=PackMapping(fields={"projects.status": "current_state",
                                                                       "projects.current_state": "current_state"}))
    issues = conflicts(manifest, r)
    assert len(issues) == 1 and "Status and Current state both read current_state" in issues[0]["message"]


def test_record_status_passes(manifest: PackManifest) -> None:
    r = match(manifest, _mysql(manifest), mapping=PackMapping(fields={"projects.status": "record_status"}))
    assert r.entities["projects"].mapping["current_state"] == "current_state"
    assert conflicts(manifest, r) == [] and r.matched
    # With nothing mapped, the record state is just missing (no guess onto current_state).
    plain = match(manifest, _mysql(manifest))
    assert "status" not in plain.entities["projects"].mapping and conflicts(manifest, plain) == []


def test_values_missing_from_the_column_are_warned(manifest: PackManifest) -> None:
    expected = pack_values(manifest, rules_only=True)
    assert expected["projects.status"] == ["Active", "Inactive"]
    wrong = guards.value_warnings(expected, {"projects.status": CURRENT_STATE},
                                  {"projects.status": ("Status", "current_state")}, {})
    assert len(wrong) == 1 and wrong[0]["missing"] == ["Inactive"]
    assert "Status: the pack's rules use Active, Inactive; current_state has Active (279), Completed (120)" \
        in wrong[0]["issue"]
    assert guards.value_warnings(expected, {"projects.status": RECORD_STATUS},
                                 {"projects.status": ("Status", "record_status")}, {}) == []
    # A source value mapped to the pack's value counts; a free-text column (50+ values) is not checked.
    assert guards.value_warnings(expected, {"projects.status": CURRENT_STATE},
                                 {"projects.status": ("Status", "current_state")},
                                 {"projects.status": {"Completed": "Inactive"}}) == []
    many = _profile(*((f"v{i}", 1) for i in range(guards.TOP_VALUES)))
    assert guards.value_warnings(expected, {"projects.status": many},
                                 {"projects.status": ("Status", "current_state")}, {}) == []


def test_profile_definition_reads_mapped_columns_even_when_a_required_field_is_missing(
        manifest: PackManifest) -> None:
    r = match(manifest, _mysql(manifest), mapping=PackMapping(fields={"projects.status": "current_state"}))
    definition = profile_definition(manifest, r)
    assert definition is not None
    proj = next(e for e in definition["entities"] if e["key"] == "projects")
    assert {"key": "status", "source_column": "current_state", "label": "Status", "data_type": "text"} \
        in proj["fields"]
    build_snapshot(definition, version_id="v", connection_id="c", version_no=None, engine="mysql")


def test_zero_and_same_figure_measures_listed() -> None:
    measures = [
        {"key": "open_projects", "label": "Open projects", "entity": "projects", "format": "integer",
         "expression": {"agg": "count", "filter": {"field": "status", "op": "=", "value": "Active"}}},
        {"key": "in_delivery", "label": "Projects in delivery", "entity": "projects", "format": "integer",
         "expression": {"agg": "count", "filter": {"field": "current_state", "op": "=", "value": "Active"}}},
        {"key": "closed_projects", "label": "Closed projects", "entity": "projects", "format": "integer",
         "expression": {"agg": "count", "filter": {"field": "status", "op": "=", "value": "Inactive"}}},
        {"key": "risks", "label": "Open risks", "entity": "risks", "format": "integer",
         "expression": {"agg": "count"}},
        {"key": "broken", "label": "Broken", "entity": "projects", "format": "integer", "expression": {"agg": "count"}},
    ]
    out = guards.measure_warnings(measures, {"open_projects": 279, "in_delivery": "279", "closed_projects": 0,
                                             "risks": 279, "broken": None})
    assert [(w["kind"], w["ref"]) for w in out] == [("zero", "measure:closed_projects"),
                                                     ("same_figure", "measure:open_projects")]
    assert out[1]["measures"] == ["open_projects", "in_delivery"]  # other tables are not compared
    assert "Open projects, Projects in delivery all return 279" in out[1]["issue"]


def test_approval_checks_on_the_broken_draft(manifest: PackManifest) -> None:
    """The EXEC-115 draft (model v2): Status and Current state both on current_state."""
    r = match(manifest, _mysql(manifest), mapping=PackMapping(fields={"projects.status": "current_state",
                                                                       "projects.current_state": "current_state"}))
    definition = bind(manifest, r).definition
    blocking = guards.shared_columns(definition)
    assert [i["ref"] for i in blocking] == ["field:projects.status"]
    assert "Status and Current state both read current_state in tpg.projects" in blocking[0]["issue"]
    snap = build_snapshot(definition, version_id="v", connection_id="c", version_no=None, engine="mysql")
    assert "projects.status" in guards.filter_fields(snap)
    warned = guards.filter_warnings(snap, {"projects.status": CURRENT_STATE, "projects.current_state": CURRENT_STATE})
    assert "measure:inactive_projects" in {w["ref"] for w in warned}  # Inactive is not a value of current_state
    assert "measure:open_projects" not in {w["ref"] for w in warned}  # Active is
    good = bind(manifest, match(manifest, _mysql(manifest),
                                mapping=PackMapping(fields={"projects.status": "record_status"}))).definition
    assert guards.shared_columns(good) == []


def test_a_pack_with_both_lookup_columns_keeps_each_on_its_own(manifest: PackManifest) -> None:
    """A pack built from a Dataverse model (US-236) has ``createdby`` and ``createdbyname`` as fields of their own:
    the label-column rule (BUG-021) must not move ``createdby`` onto ``createdbyname``, or every lookup blocks
    install as two fields on one column (EXEC-167)."""
    from tela_api.packs.manifest import PackField

    proj = next(e for e in manifest.entities if e.key == "projects")
    extra = [PackField(key="createdby", label="Createdby", data_type="text", columns=["createdby"]),
             PackField(key="createdbyname", label="Createdbyname", data_type="text", columns=["createdbyname"]),
             PackField(key="owner", label="Owner", data_type="text", columns=["ownerid"])]  # no owneridname field
    ent = proj.model_copy(update={"fields": [*proj.fields, *extra]})
    pack = manifest.model_copy(update={"entities": [ent if e.key == "projects" else e for e in manifest.entities]})
    catalog = _mysql(manifest)
    for item in catalog:
        if item["qualified_name"] == f"tpg.{proj.object_candidates[0]}":
            item["columns"] += [{"name": "createdby", "type": "uniqueidentifier"}, {"name": "createdbyname", "type": "nvarchar"},
                                {"name": "ownerid", "type": "uniqueidentifier"}, {"name": "owneridname", "type": "nvarchar"}]
    r = match(pack, catalog)
    m = r.entities["projects"]
    assert m.mapping["createdby"] == "createdby" and m.how["createdby"] == "exact"
    assert m.mapping["createdbyname"] == "createdbyname"
    # A lookup whose label column no pack field names still reads the label (BUG-021 unchanged).
    assert m.mapping["owner"] == "owneridname" and m.how["owner"] == "dataverse_label"
    assert [i for i in conflicts(pack, r) if "projects." in i["ref"] and "created" in i["ref"]] == []
