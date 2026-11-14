"""BUG-018 and BUG-021, no database.

* BUG-018: tables read from the source after the model was built are listed as "not in the model" and can be added
  to the draft the way the baseline would have (entity, count measure, foreign-key links to tables already there).
* BUG-021: any source column can be mapped onto a text pack field (e.g. a Dataverse yes/no column onto "Key priority
  project"); queries read a yes/no column as "Yes" / "No" and a number or date column as text.
"""

from __future__ import annotations

from typing import Any

import duckdb
import pytest
from test_dialect_parity import AS_OF

from tela_api.model.catalog import catalog_issues, extend_definition, stamp_source_types, unmodelled_objects
from tela_api.packs.manifest import PackManifest, discover, load_manifest, match
from tela_api.packs.mapping import PackMapping, can_map, check
from tela_api.query.compiler import SqlCompiler
from tela_api.query.validator import SpecValidator
from tela_query_spec.model import ModelSnapshot
from tela_query_spec.spec import parse_spec

# --------------------------------------------------------------------------- BUG-018


def _obj(name: str, cols: list[str], pk: list[str] | None = None, fks: list[dict[str, Any]] | None = None
         ) -> dict[str, Any]:
    return {"qualified_name": name, "columns": [{"name": c, "type": "integer" if c.endswith("id") else "text"}
                                                for c in cols],
            "keys": {"primary": pk or [cols[0]], "foreign": fks or []}}


def test_new_tables_are_listed_and_added_beside_the_admins_model() -> None:
    projects = _obj("dbo.projects", ["id", "name"])
    risks = _obj("dbo.risks", ["id", "project_id", "title"],
                 fks=[{"columns": ["project_id"], "ref_object": "dbo.projects", "ref_columns": ["id"]}])
    # an existing entity already uses the key "risks" (on another table): the new one gets a numbered key
    model = {"entities": [{"key": "projects", "label": "Projects", "source_object": "dbo.projects",
                           "primary_key": ["id"], "fields": [
                               {"key": "id", "source_column": "id", "label": "Id", "data_type": "integer"},
                               {"key": "project_name", "source_column": "name", "label": "Project",
                                "data_type": "text"}]},
                          {"key": "risks", "label": "Old risks", "source_object": "dbo.old_risks",
                           "primary_key": ["id"], "fields": [
                               {"key": "id", "source_column": "id", "label": "Id", "data_type": "integer"}]}],
             "links": [], "measures": [{"key": "mine", "label": "Mine", "entity": "projects",
                                        "expression": {"agg": "count"}, "plain_rule": "All."}],
             "dimensions": [], "settings": {"x": "y"}}
    catalog = [projects, risks, _obj("dbo.old_risks", ["id"])]
    assert unmodelled_objects(model, catalog) == ["dbo.risks"]

    out, questions, added = extend_definition(model, [risks], catalog)
    assert added == ["risks_2"]
    assert [e["key"] for e in out["entities"]] == ["projects", "risks", "risks_2"]
    assert out["entities"][0] == model["entities"][0]  # the Admin's wording is kept
    assert out["settings"] == {"x": "y"} and out["measures"][0]["key"] == "mine"
    assert any(m["key"] == "risks_2_count" and m["entity"] == "risks_2" for m in out["measures"])
    assert out["links"] == [{"from_entity": "risks_2", "to_entity": "projects", "from_fields": ["project_id"],
                             "to_fields": ["id"], "cardinality": "many_to_one", "origin": "foreign_key",
                             "accepted": False}]
    assert all((q.get("target_ref") or {}).get("entity") in (None, "risks_2") for q in questions)
    assert unmodelled_objects(out, catalog) == []
    # adding it again changes nothing
    again, _, none = extend_definition(out, [risks], catalog)
    assert none == [] and again["entities"] == out["entities"] and again["links"] == out["links"]


# --------------------------------------------------------------------------- BUG-021


@pytest.fixture(scope="module")
def manifest() -> PackManifest:
    return load_manifest(next(p for p in discover() if p.parent.name == "pmo"))[0]


def test_any_column_can_stand_for_a_text_field_but_not_for_a_number() -> None:
    for t in ("bit", "boolean", "integer", "bigint", "decimal(18,2)", "datetime2", "ntext", None):
        assert can_map("text", None, t)
    assert not can_map("integer", None, "nvarchar(100)")
    assert not can_map("date", None, "bit")
    assert can_map("decimal", None, "money")


def _source(label: bool) -> list[dict[str, Any]]:
    cols = [{"name": "tpg_projectid", "type": "uuid"}, {"name": "tpg_projectname", "type": "nvarchar"},
            {"name": "tpgmea_keypriorityproject", "type": "bit"}]
    if label:
        cols.append({"name": "tpgmea_keypriorityprojectname", "type": "nvarchar"})
    return [{"qualified_name": "dataverse.tpg_project", "columns": cols}]


def test_a_boolean_column_maps_onto_key_priority_project(manifest: PackManifest) -> None:
    mapping = PackMapping(fields={"projects.key_priority_project": "tpgmea_keypriorityproject"})
    r = match(manifest, _source(False), mapping=mapping)
    assert r.entities["projects"].mapping["key_priority_project"] == "tpgmea_keypriorityproject"
    assert not [i for i in check(manifest, _source(False), mapping, r) if "key_priority" in i["ref"]]
    # Dataverse keeps the yes/no label beside it: a manual pick reads the label column
    r = match(manifest, _source(True), mapping=mapping)
    assert r.entities["projects"].mapping["key_priority_project"] == "tpgmea_keypriorityprojectname"
    # a number field still needs a number
    bad = PackMapping(fields={"projects.approved_pcr_counter": "tpg_projectname"})
    r = match(manifest, _source(False), mapping=bad)
    assert any("needs integer" in i["message"] for i in check(manifest, _source(False), bad, r))


def _snap() -> ModelSnapshot:
    return ModelSnapshot.model_validate({
        "model_version_id": "v1", "connection_id": "c1",
        "entities": [{"key": "projects", "label": "Projects", "source_object": "dbo.projects", "primary_key": ["id"],
                      "fields": [{"key": "id", "label": "Id", "data_type": "integer", "source_column": "id"},
                                 {"key": "kpp", "label": "Key priority project", "data_type": "text",
                                  "source_column": "kpp"},
                                 {"key": "phase", "label": "Phase", "data_type": "text", "source_column": "phase"}]}],
        "measures": [{"key": "n", "label": "N", "entity": "projects", "plain_rule": "All.",
                      "expression": {"agg": "count"}},
                     {"key": "key_n", "label": "Key", "entity": "projects", "plain_rule": "Key priority.",
                      "expression": {"agg": "count", "where": {"field": "kpp", "op": "=", "value": "Yes"}}}],
        "dimensions": [{"key": "kpp", "label": "Key priority project", "entity": "projects", "field": "kpp"},
                       {"key": "phase", "label": "Phase", "entity": "projects", "field": "phase"}],
    })


def test_text_fields_over_boolean_and_integer_columns_query_as_text() -> None:
    catalog = {"dbo.projects": [{"name": "id", "type": "int"}, {"name": "kpp", "type": "bit"},
                                {"name": "phase", "type": "int"}]}
    s = _snap()
    assert catalog_issues(s, catalog) == []
    stamp_source_types(s, catalog)
    assert s.entity("projects").field("kpp").reads_text  # type: ignore[union-attr]
    con = duckdb.connect()
    con.execute("CREATE SCHEMA dbo; CREATE TABLE dbo.projects (id INTEGER, kpp BOOLEAN, phase INTEGER)")
    con.executemany("INSERT INTO dbo.projects VALUES (?, ?, ?)",
                    [(1, True, 1), (2, True, 2), (3, False, 2), (4, None, None)])

    def run(body: dict[str, Any]) -> list[tuple[Any, ...]]:
        spec, _ = parse_spec({"model_version_id": "v1", "connection_id": "c1", **body})
        compiled = SqlCompiler("duckdb").compile(SpecValidator(s).validate(spec), as_of=AS_OF)
        return sorted(con.execute(compiled.tree.sql(dialect="duckdb"), compiled.params.values).fetchall(),
                      key=lambda r: tuple((x is None, x) for x in r))

    assert run({"measure": {"ref": "measure:key_n"}}) == [(2,)]
    assert run({"measure": {"ref": "measure:n"}, "breakdowns": [{"ref": "dimension:kpp"}]}) == [
        ("No", 1), ("Yes", 2), (None, 1)]
    assert run({"measure": {"ref": "measure:n"},
                "filters": [{"ref": "dimension:phase", "op": "=", "values": ["2"]}]}) == [(2,)]
    for dialect in ("tsql", "mysql", "postgres"):
        spec, _ = parse_spec({"model_version_id": "v1", "connection_id": "c1", "measure": {"ref": "measure:key_n"}})
        sql = SqlCompiler(dialect).compile(SpecValidator(s).validate(spec), as_of=AS_OF).sql()
        assert "'Yes'" in sql and "CASE WHEN" in sql
