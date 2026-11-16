"""Dashboard planned by the LLM: the catalogue comes from the model only, and the LLM's keys and values are checked."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from tela_api.content.dashboard_ai import Catalogue, Checker, FilterOut, KpiOut, ReportOut, WidgetOut, _filler
from tela_query_spec.model import ModelSnapshot


def snap() -> ModelSnapshot:
    def f(c: str, t: str = "text", **kw: object) -> dict:
        return {"key": c, "source_column": c, "label": c.replace("_", " ").title(), "data_type": t, **kw}
    return ModelSnapshot.model_validate({
        "model_version_id": "v", "connection_id": "c", "engine": "mysql",
        "entities": [
            {"key": "orders", "label": "Orders", "source_object": "shop.orders", "primary_key": ["id"],
             "fields": [f("id", "integer"), f("status", values=["Open", "Closed", "On Hold"]),
                        f("colour", values=["Green", "Yellow", "Red"]), f("created_at", "date"),
                        f("clip_id", values=["a1", "a2"]), f("note", semantic_type="free_text"),
                        f("email", sensitivity="personal", values=["x@y"])]},
            {"key": "tasks", "label": "Tasks", "source_object": "shop.tasks", "primary_key": ["id"],
             "fields": [f("id", "integer"), f("order_id", "integer"), f("kind", values=["Build", "Test"])]},
            {"key": "lonely", "label": "Lonely", "source_object": "shop.lonely", "primary_key": ["id"],
             "fields": [f("id", "integer"), f("tag", values=["p", "q"])]}],
        "links": [{"from_entity": "tasks", "to_entity": "orders", "from_fields": ["order_id"], "to_fields": ["id"],
                   "cardinality": "many_to_one"}],
        "measures": [
            {"key": "orders_count", "label": "Orders", "entity": "orders", "expression": {"agg": "count"},
             "plain_rule": "Every order."},
            {"key": "tasks_count", "label": "Tasks", "entity": "tasks", "expression": {"agg": "count"},
             "plain_rule": "Every task."}],
        "dimensions": [{"key": "rag", "label": "RAG", "entity": "orders", "field": "colour", "is_status": True,
                        "value_labels": {"Yellow": "Amber"}}],
        "rules": [{"key": "open", "term": "Open orders", "entity": "orders", "kind": "population",
                   "sql": "status <> 'Closed'"}],
        "settings": {"reporting_date": "2026-10-25"}})


def source() -> SimpleNamespace:
    s = snap()
    scope = SimpleNamespace(entities={"orders": 1, "tasks": 1, "lonely": 1}, hidden_fields=frozenset())
    return SimpleNamespace(snap=s, scope=scope, measures=list(s.measures), dims=list(s.dimensions),
                           connection={"id": "c", "name": "shop"})


class Service:
    async def prepare(self, spec: dict, principal: object, ctx: object) -> None:
        return None


def checker() -> Checker:
    return Checker(Catalogue(source()), Service(), SimpleNamespace())  # type: ignore[arg-type]


def test_the_catalogue_comes_from_the_model_and_leaves_out_keys_personal_and_free_text() -> None:
    cat = Catalogue(source())
    assert set(cat.measures) == {"orders_count", "tasks_count"}
    keys = set(cat.group_bys)
    assert {"rag", "orders.status", "orders.created_at", "tasks.kind", "lonely.tag"} <= keys
    assert not keys & {"orders.colour", "orders.id", "tasks.order_id", "orders.clip_id", "orders.note",
                       "orders.email"}
    assert cat.reach["tasks"] == ["orders", "tasks"]
    prompt = cat.prompt()
    assert prompt["data_date"] == "2026-10-25"
    assert next(g for g in prompt["group_bys"] if g["key"] == "rag")["values"] == ["Green", "Amber", "Red"]
    assert prompt["rules"][0]["term"] == "Open orders"


def test_an_invented_measure_or_group_by_is_left_out_with_the_reason() -> None:
    c = checker()
    assert asyncio.run(c.widget("P", WidgetOut(title="x", measure="revenue_total"), kpi=False)) is None
    assert asyncio.run(c.widget("P", WidgetOut(title="y", measure="orders_count", group_by=["region"]),
                                kpi=False)) is None
    assert [d["reason"] for d in c.dropped] == ["“revenue_total” is not a certified measure you can use",
                                                "“region” is not a group-by in the model"]


def test_a_group_by_the_measure_cannot_reach_is_left_out() -> None:
    c = checker()
    assert asyncio.run(c.widget("P", WidgetOut(title="z", measure="orders_count", group_by=["lonely.tag"]),
                                kpi=False)) is None
    assert "not linked" in c.dropped[0]["reason"]


def test_filter_values_must_be_known_and_take_the_stored_spelling() -> None:
    c = checker()
    w = asyncio.run(c.widget("P", KpiOut(title="Amber orders", measure="orders_count",
                                         filters=[FilterOut(by="rag", values=["amber"])]), kpi=True))
    assert w is not None and w["spec"]["filters"] == [{"ref": "dimension:rag", "op": "=", "values": ["Yellow"]}]
    assert w["visual"] == "figure"
    bad = asyncio.run(c.widget("P", KpiOut(measure="orders_count", filters=[FilterOut(by="orders.status",
                                                                                      values=["Pending"])]), kpi=True))
    assert bad is None and "“Pending” is not a value of Status" in c.dropped[-1]["reason"]


def test_a_date_group_by_becomes_a_monthly_trend_and_top_n_sorts() -> None:
    c = checker()
    t = asyncio.run(c.widget("P", WidgetOut(title="t", measure="orders_count", group_by=["orders.created_at"]),
                             kpi=False))
    assert t is not None and t["spec"]["time"] == {"ref": "field:orders.created_at", "grain": "month"}
    r = asyncio.run(c.widget("P", WidgetOut(title="r", measure="tasks_count", group_by=["rag"], top=5),
                             kpi=False))
    assert r is not None and r["spec"]["limit"] == 5 and r["spec"]["breakdowns"] == [{"ref": "dimension:rag"}]


def test_fill_ins_use_reachable_group_bys_and_skip_what_is_planned() -> None:
    cat = Catalogue(source())
    c = checker()
    kept = [asyncio.run(c.widget("P", WidgetOut(title="a", measure="tasks_count", group_by=["tasks.kind"]),
                                 kpi=False))]
    out = _filler(cat, ReportOut(title="Tasks", purpose="work on tasks"), kept, 3)
    assert out and all(cat.reachable(cat.measures[w.measure].entity, cat.group_bys[(w.group_by or [w.time.by])[0]])
                       for w in out if w.group_by or w.time)
    assert not any(w.measure == "tasks_count" and w.group_by == ["tasks.kind"] for w in out)


def test_a_median_measure_is_left_out_before_planning_where_the_database_cannot_compute_it() -> None:
    src = source()
    data = src.snap.model_dump()
    data["measures"].append({"key": "age_median", "label": "Median age", "entity": "orders",
                             "expression": {"agg": "median", "field": "id"}, "plain_rule": "Median."})
    for engine, kept in (("mysql", False), ("postgres", True)):
        s = ModelSnapshot.model_validate({**data, "engine": engine})
        src2 = SimpleNamespace(snap=s, scope=src.scope, measures=list(s.measures), dims=list(s.dimensions),
                               connection={"id": "c", "name": "shop", "engine": engine})
        cat = Catalogue(src2)
        assert ("age_median" in cat.measures) is kept
        assert ("age_median" in cat.unsupported) is not kept
