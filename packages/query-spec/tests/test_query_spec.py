"""Query Spec contract (US-017)."""

from __future__ import annotations

import json

import pytest

from tela_query_spec import QuerySpec, parse_spec
from tela_query_spec.expr import AggExpr, Arith, is_additive, measure_fields
from tela_query_spec.model import ModelSnapshot
from tela_query_spec.schema import schemas

BASE = {"model_version_id": "mv", "connection_id": "c", "measure": {"ref": "measure:open_projects"}}


def test_minimal_spec_and_defaults() -> None:
    spec, stripped = parse_spec(BASE)
    assert spec.spec_version == "1" and spec.follow_page_filters and stripped == []


def test_client_scope_keys_are_stripped() -> None:
    spec, stripped = parse_spec({**BASE, "scope": {"all_rows": True}, "scope_hash": "x", "sql": "SELECT 1"})
    assert stripped == ["scope", "scope_hash", "sql"] and "scope" not in spec.model_dump()


@pytest.mark.parametrize("bad", [
    {"measure": {"ref": "dimension:region"}},
    {"breakdowns": [{"ref": "measure:x"}]},
    {"breakdowns": [{"ref": "dimension:Region"}]},
    {"filters": [{"ref": "dimension:region", "op": "=", "values": []}]},
    {"filters": [{"ref": "dimension:region", "op": "between", "values": [1]}]},
    {"filters": [{"ref": "dimension:region", "op": "is_null", "values": ["x"]}]},
    {"limit": 0}, {"limit": 10_001},
    {"time": {"ref": "dimension:d", "range": {"relative": "last_month", "from": "2026-01-01"}}},
    {"spec_version": "2"},
])
def test_invalid_specs(bad: dict) -> None:
    with pytest.raises(ValueError):
        parse_spec({**BASE, **bad})


def test_expression_ast() -> None:
    expr = Arith.model_validate({"op": "div", "scale": 100, "left": {"agg": "sum", "field": "cost"},
                                 "right": {"agg": "sum", "field": "budget", "where": {
                                     "all": [{"field": "status", "op": "=", "value": "Active"},
                                             {"not": {"field": "x", "op": "is_null"}}]}}})
    assert measure_fields(expr) == {"cost", "budget", "status", "x"}
    assert not is_additive(expr) and is_additive(AggExpr(agg="count"))
    with pytest.raises(ValueError):
        AggExpr.model_validate({"agg": "sum"})
    with pytest.raises(ValueError):
        AggExpr.model_validate({"agg": "count", "where": {"field": "a; DROP", "op": "=", "value": 1}})


def test_snapshot_integrity() -> None:
    ent = {"key": "p", "label": "P", "source_object": "public.p", "primary_key": ["id"],
           "fields": [{"key": "id", "source_column": "id", "label": "Id", "data_type": "text"}]}
    ModelSnapshot.model_validate({"model_version_id": "v", "connection_id": "c", "entities": [ent]})
    with pytest.raises(ValueError, match="unknown fields"):
        ModelSnapshot.model_validate({"model_version_id": "v", "connection_id": "c", "entities": [ent], "measures": [
            {"key": "m", "label": "M", "entity": "p", "plain_rule": "x", "expression": {"agg": "sum", "field": "x"}}]})


def test_json_schema_for_generated_types() -> None:
    out = schemas()
    assert "QuerySpec" in out and "ModelSnapshot" in out
    json.dumps(out)
    assert QuerySpec.model_json_schema()["properties"]["spec_version"]["const"] == "1"
