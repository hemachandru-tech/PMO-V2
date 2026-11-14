"""BUG-028, no database: columns added to a source table after the model was built are listed as "not in the
model" and join the entity as fields the way the baseline would have, leaving the Admin's fields untouched."""

from __future__ import annotations

from tela_api.model.catalog import extend_columns, unmodelled_columns


def _catalog() -> list[dict]:
    return [
        {"qualified_name": "dbo.tpg_project", "keys": {"primary": ["tpg_projectid"], "foreign": []},
         "columns": [{"name": "tpg_projectid", "type": "uniqueidentifier"}, {"name": "tpg_name", "type": "nvarchar"},
                     {"name": "tpg_budget", "type": "decimal(18,2)"}, {"name": "Tpg_Name2", "type": "int"},
                     {"name": "tpg_owner_email", "type": "nvarchar"}]},
        {"qualified_name": "dbo.tpg_risk", "keys": {"primary": ["tpg_riskid"], "foreign": []},
         "columns": [{"name": "tpg_riskid", "type": "uniqueidentifier"}]},
        {"qualified_name": "dbo.not_modelled", "keys": {"primary": ["id"], "foreign": []},
         "columns": [{"name": "id", "type": "int"}]},
    ]


def _model() -> dict:
    return {"entities": [
        {"key": "projects", "label": "Projects", "source_object": "dbo.tpg_project", "primary_key": ["id"],
         "fields": [{"key": "id", "source_column": "tpg_projectid", "label": "Id", "data_type": "text"},
                    # the Admin renamed this field; its key is what a new column's key would be
                    {"key": "tpg_name_2", "source_column": "TPG_NAME", "label": "Project", "data_type": "text"},
                    {"key": "tpg_name2", "source_column": "something_else", "label": "X", "data_type": "text"}]},
        {"key": "risks", "label": "Risks", "source_object": "dbo.tpg_risk", "primary_key": ["id"],
         "fields": [{"key": "id", "source_column": "tpg_riskid", "label": "Id", "data_type": "text"}]}],
        "links": [], "measures": [{"key": "mine", "label": "Mine", "entity": "projects",
                                   "expression": {"agg": "count"}, "plain_rule": "All."}],
        "dimensions": [], "settings": {"x": "y"}}


def test_new_columns_are_listed_per_entity_case_insensitively() -> None:
    assert unmodelled_columns(_model(), _catalog()) == [
        {"entity": "projects", "object": "dbo.tpg_project",
         "columns": ["tpg_budget", "Tpg_Name2", "tpg_owner_email"]}]


def test_chosen_columns_join_the_entity_as_baseline_fields() -> None:
    model = _model()
    out, questions, added = extend_columns(model, {"projects": ["TPG_BUDGET", "tpg_name2", "tpg_name"]}, _catalog())
    # tpg_name is already read (as TPG_NAME) and is skipped; Tpg_Name2's key is taken and gets a number
    assert added == ["projects.tpg_budget", "projects.tpg_name2_2"]
    fields = out["entities"][0]["fields"]
    assert fields[:3] == model["entities"][0]["fields"]  # the Admin's fields are kept as they were
    assert fields[3] == {"key": "tpg_budget", "source_column": "tpg_budget", "label": "Tpg budget",
                         "data_type": "decimal", "sensitivity": "internal", "semantic_type": "currency"}
    assert fields[4]["source_column"] == "Tpg_Name2" and fields[4]["data_type"] == "integer"
    assert out["entities"][1] == model["entities"][1]
    assert out["measures"] == model["measures"] and out["settings"] == {"x": "y"}
    assert [q["target_ref"] for q in questions] == [{"entity": "projects", "field": "tpg_budget"}]
    assert unmodelled_columns(out, _catalog()) == [
        {"entity": "projects", "object": "dbo.tpg_project", "columns": ["tpg_owner_email"]}]
    # adding them again changes nothing
    again, none_q, none = extend_columns(out, {"projects": ["tpg_budget"]}, _catalog())
    assert none == [] and none_q == [] and again["entities"] == out["entities"]


def test_personal_columns_get_the_baseline_question() -> None:
    out, questions, added = extend_columns(_model(), {"projects": ["tpg_owner_email"]}, _catalog())
    assert added == ["projects.tpg_owner_email"]
    assert out["entities"][0]["fields"][-1]["sensitivity"] == "personal"
    assert questions[0]["options"] == ["Personal", "Not personal"]
