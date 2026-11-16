"""A question asked on a dashboard carries the dashboard's widgets in words (Direct SQL mode)."""

from __future__ import annotations

from tela_api.ask.dashboard_context import describe_spec
from tela_query_spec.model import ModelSnapshot


def snap() -> ModelSnapshot:
    def f(c: str, t: str = "text") -> dict:
        return {"key": c, "source_column": c, "label": c.replace("_", " ").title(), "data_type": t}
    return ModelSnapshot.model_validate({
        "model_version_id": "v", "connection_id": "c", "engine": "mysql",
        "entities": [{"key": "orders", "label": "Orders", "source_object": "shop.orders", "primary_key": ["id"],
                      "fields": [f("id", "integer"), f("status"), f("created_at", "date")]}],
        "measures": [{"key": "orders_count", "label": "Orders", "entity": "orders", "expression": {"agg": "count"},
                      "plain_rule": "Every order."}],
        "dimensions": [{"key": "status", "label": "Order status", "entity": "orders", "field": "status"}]})


def test_a_widget_is_described_by_its_measure_rule_group_bys_filters_and_top_n() -> None:
    spec = {"measure": {"ref": "measure:orders_count"}, "breakdowns": [{"ref": "dimension:status"}],
            "filters": [{"ref": "field:orders.status", "op": "!=", "values": ["Closed"]}], "limit": 5}
    assert describe_spec(spec, snap()) == ("Orders (Every order.); by Order status; where Orders Status != Closed; "
                                           "top 5")


def test_a_trend_names_its_date_and_grain() -> None:
    spec = {"measure": {"ref": "measure:orders_count"}, "time": {"ref": "field:orders.created_at", "grain": "month"}}
    assert describe_spec(spec, snap()) == "Orders (Every order.); over Orders Created At per month"


def test_a_spec_without_a_measure_says_nothing() -> None:
    assert describe_spec({}, snap()) == ""
