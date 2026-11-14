"""Home suggested questions (US-138) on a model built from a raw schema — pure unit tests, no database."""

from __future__ import annotations

from tela_api.home.router import suggestions
from tela_query_spec.model import ModelSnapshot


def _entity(key: str, *fields: str) -> dict:
    return {"key": key, "label": key.title(), "source_object": f"dataverse.{key}", "primary_key": ["id"],
            "fields": [{"key": f, "source_column": f, "label": f, "data_type": "text"} for f in ("id", *fields)]}


def _count(entity: str) -> dict:
    return {"key": f"{entity}_count", "label": f"Number of {entity}", "entity": entity, "expression": {"agg": "count"},
            "plain_rule": "Counts rows."}


# Alphabetical like a baseline model: a reference table (account) sorts ahead of the business tables.
SNAP = ModelSnapshot.model_validate({
    "model_version_id": "v", "connection_id": "c",
    "entities": [_entity("account"), _entity("project", "account_id", "status", "region"),
                 _entity("risk", "project_id", "category")],
    "links": [{"from_entity": "project", "to_entity": "account", "from_fields": ["account_id"], "to_fields": ["id"],
               "cardinality": "many_to_one"},
              {"from_entity": "risk", "to_entity": "project", "from_fields": ["project_id"], "to_fields": ["id"],
               "cardinality": "many_to_one"}],
    "measures": [_count("account"), _count("project"), _count("risk")],
    "dimensions": [{"key": "status", "label": "Status", "entity": "project", "field": "status", "is_status": True},
                   {"key": "region", "label": "Region", "entity": "project", "field": "region"},
                   {"key": "risk_category", "label": "Risk category", "entity": "risk", "field": "category"}],
})


def test_business_tables_lead_and_each_measure_uses_its_own_dimensions() -> None:
    assert suggestions(SNAP, frozenset()) == [
        "Number of project by status", "Number of risk by risk category", "Number of account"]


def test_a_dimension_reached_only_by_fanning_out_is_not_offered() -> None:
    # Accounts reach project dimensions only one-to-many (one account, many projects): counts would multiply.
    only_account = ModelSnapshot.model_validate({**SNAP.model_dump(by_alias=True), "measures": [_count("account")]})
    assert suggestions(only_account, frozenset()) == ["Number of account"]


def test_a_many_to_one_dimension_is_offered_when_the_entity_has_none() -> None:
    risk_only_dims = ModelSnapshot.model_validate({**SNAP.model_dump(by_alias=True),
                                                   "measures": [_count("risk")], "dimensions": [
        {"key": "status", "label": "Status", "entity": "project", "field": "status", "is_status": True}]})
    assert suggestions(risk_only_dims, frozenset()) == ["Number of risk by status"]


def test_hidden_dimensions_are_skipped() -> None:
    assert suggestions(SNAP, frozenset({"project.status"}))[0] == "Number of project by region"
