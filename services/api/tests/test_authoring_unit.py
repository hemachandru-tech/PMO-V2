"""Pure authoring rules: grid layout, filter reach, chat-edit targets and refine parsing (no database)."""

from __future__ import annotations

from typing import Any

import pytest

from tela_api.ask.refine import apply_ops, chips_for, is_rule_change, previous_period_spec
from tela_api.content.chat_edit import Unresolved, apply, find_target
from tela_api.content.dashboards import reach
from tela_api.content.layout import Box, compact, place, problems, settle
from tela_common.errors import TelaError
from tela_query_spec.model import ModelSnapshot

SNAP = ModelSnapshot.model_validate({
    "model_version_id": "v1", "connection_id": "c1", "version_no": 1,
    "entities": [
        {"key": "projects", "label": "Projects", "source_object": "public.projects", "primary_key": ["id"],
         "fields": [{"key": "id", "label": "Id", "data_type": "integer", "source_column": "id"},
                    {"key": "region", "label": "Region", "data_type": "text", "source_column": "region"},
                    {"key": "go_live", "label": "Go-live", "data_type": "date", "source_column": "go_live"}]},
        {"key": "tickets", "label": "Tickets", "source_object": "public.tickets", "primary_key": ["id"],
         "fields": [{"key": "id", "label": "Id", "data_type": "integer", "source_column": "id"},
                    {"key": "queue", "label": "Queue", "data_type": "text", "source_column": "queue"}]},
    ],
    "measures": [{"key": "open_projects", "label": "Open projects", "entity": "projects", "plain_rule": "Open.",
                  "expression": {"agg": "count"}},
                 {"key": "tickets", "label": "Tickets", "entity": "tickets", "plain_rule": "All.",
                  "expression": {"agg": "count"}}],
    "dimensions": [{"key": "region", "label": "Region", "entity": "projects", "field": "region"},
                   {"key": "go_live", "label": "Go-live", "entity": "projects", "field": "go_live",
                    "is_temporal": True},
                   {"key": "queue", "label": "Queue", "entity": "tickets", "field": "queue"}],
})
SPEC = {"model_version_id": "v1", "connection_id": "c1", "measure": {"ref": "measure:open_projects"},
        "breakdowns": [{"ref": "dimension:region"}], "filters": []}


def test_grid_rules() -> None:
    assert problems([Box("a", 0, 0, 6, 4), Box("b", 3, 2, 6, 4)]) == ["a overlaps b"]
    assert problems([Box("a", 8, 0, 6, 4)]) == ["a: x + w must be at most 12"]
    assert problems([Box("a", 0, 0, 1, 4)])
    assert [(b.id, b.y) for b in compact([Box("a", 0, 7, 6, 4), Box("b", 6, 3, 6, 2)])] == [("b", 0), ("a", 0)]
    assert place([Box("a", 0, 0, 6, 4)], 3, 2) == (6, 0)
    assert place([Box("a", 0, 0, 12, 4)], 3, 2) == (0, 4)
    settled = {b.id: (b.x, b.y) for b in settle([Box("a", 0, 0, 12, 4), Box("b", 0, 0, 6, 4)], first="b")}
    assert settled == {"b": (0, 0), "a": (0, 4)}


def frozen(filters: list[dict[str, Any]]) -> dict[str, Any]:
    def w(wid: str, measure: str) -> dict[str, Any]:
        return {"kind": "widget", "item_id": f"i-{wid}", "widget_id": wid, "title": wid,
                "spec": {**SPEC, "measure": {"ref": f"measure:{measure}"}, "breakdowns": []}}
    return {"shared_filters": filters, "reports": [{"title": "R", "sections": [{"items": [
        w("p", "open_projects"), w("t", "tickets")]}]}]}


def test_filter_reach_and_exclusion() -> None:
    out = reach(frozen([{"ref": "dimension:region"}]), {"v1": SNAP})[0]
    assert out["reaches_all"] is False
    t = next(w for w in out["widgets"] if w["widget_id"] == "t")
    assert t["reached"] is False and t["message"] == "Not affected by Region"
    excluded = reach(frozen([{"ref": "dimension:region", "exclude_widget_ids": ["t"]}]), {"v1": SNAP})[0]
    assert excluded["reaches_all"] is True


def test_refine_ops_carry_filters_forward() -> None:
    spec = {**SPEC, "filters": [{"ref": "dimension:region", "op": "=", "values": ["KSA"]}]}
    out, said = apply_ops(spec, [{"op": "set_limit", "value": 5}, {"op": "set_visual", "value": "columns"}], SNAP)
    assert out["filters"] == spec["filters"] and out["limit"] == 5 and said == ["Top 5", "Shown as columns"]
    out, _ = apply_ops(out, [{"op": "set_time", "grain": "month"}], SNAP)
    assert out["time"] == {"ref": "dimension:go_live", "grain": "month"}
    with pytest.raises(TelaError):
        apply_ops(SPEC, [{"op": "compare_previous"}], SNAP)
    with pytest.raises(TelaError):
        apply_ops(SPEC, [{"op": "drop_table"}], SNAP)
    labels = [c["label"] for c in chips_for(out, SNAP)]
    assert labels == ["Open projects", "Region", "Go-live by month", "Region: KSA", "top 5"]


def test_rule_change_detection() -> None:
    assert is_rule_change("count cancelled projects too")
    assert is_rule_change("treat on hold as active")
    assert is_rule_change("don't count closed ones")
    assert not is_rule_change("only KSA as columns")
    assert not is_rule_change("top 5 by region")


def test_previous_period() -> None:
    from datetime import date

    spec = {**SPEC, "time": {"ref": "dimension:go_live", "range": {"relative": "last_30_days"}}}
    out = previous_period_spec(spec, date(2026, 9, 30))
    assert out is not None and out[0]["time"]["range"] == {"from": "2026-08-02", "to": "2026-08-31"}
    assert previous_period_spec(SPEC, date(2026, 9, 30)) is None


def state() -> dict[str, Any]:
    items = [{"id": f"i{n}", "section_id": "s", "kind": "widget", "widget_id": f"w{n}", "x": x, "y": 0, "w": 6,
              "h": 4, "text": None} for n, x in ((1, 0), (2, 6))]
    items.append({"id": "i3", "section_id": "s", "kind": "widget", "widget_id": "w3", "x": 0, "y": 4, "w": 3, "h": 2,
                  "text": None})
    return {"title": "R", "page_filters": [], "sections": [{"id": "s", "title": "S"}], "items": items,
            "widgets": {"w1": {"title": "Open by region", "visual": "ranked_bar", "spec": SPEC},
                        "w2": {"title": "Open by state", "visual": "ranked_bar", "spec": SPEC},
                        "w3": {"title": "Open projects", "visual": "figure", "spec": SPEC}}}


def test_chat_targets_and_apply() -> None:
    st = state()
    with pytest.raises(Unresolved) as u:
        find_target(st, "open by")
    assert {o["title"] for o in u.value.question["options"]} == {"Open by region", "Open by state"}
    assert find_target(st, "open by region")["id"] == "i1"  # type: ignore[index]
    assert find_target(st, "budget") is None
    out = apply(st, [{"op": "resize", "item_id": "i1", "to": {"w": 12, "h": 4}},
                     {"op": "move", "item_id": "i3", "to": {"section_id": "s", "x": 0, "y": -1}}])
    pos = {i["id"]: (i["x"], i["y"], i["w"]) for i in out["items"]}
    assert pos["i3"] == (0, 0, 3) and pos["i1"][2] == 12
    assert not problems([Box(i["id"], i["x"], i["y"], i["w"], i["h"]) for i in out["items"]])
    assert st["items"][0]["w"] == 6  # the original is untouched (preview works on a copy)


# --------------------------------------------------------------------------- custom SQL validator (US-079)

from tela_api.identity.scope import EntityScope, ResolvedCondition, ResolvedScope  # noqa: E402
from tela_api.query import custom_sql  # noqa: E402

SQL_SNAP = ModelSnapshot.model_validate({
    "model_version_id": "v1", "connection_id": "c1",
    "entities": [{"key": "projects", "label": "Projects", "source_object": "public.projects", "primary_key": ["id"],
                  "fields": [{"key": "id", "label": "Id", "data_type": "integer", "source_column": "id"},
                             {"key": "region", "label": "Region", "data_type": "text", "source_column": "region"},
                             {"key": "budget", "label": "Budget", "data_type": "decimal",
                              "source_column": "approved_budget"}]}],
    "dimensions": [{"key": "region", "label": "Region", "entity": "projects", "field": "region"}],
})
SQL_SCOPE = ResolvedScope("c1", {"projects": EntityScope(alternatives=[(ResolvedCondition("region", "in",
                                                                                         ("KSA",)),)])},
                          frozenset({"projects.budget"}), frozenset())


def test_custom_sql_refuses_the_adversarial_corpus() -> None:
    from security_corpus import malicious_statements

    refused = 0
    for sql in malicious_statements():
        with pytest.raises(custom_sql.CheckFailed):
            custom_sql.validate(sql, engine="postgres", descriptor=None, snap=SQL_SNAP, scope=SQL_SCOPE)
        refused += 1
    assert refused > 500


def test_custom_sql_scope_and_page_filters() -> None:
    p = custom_sql.validate("SELECT region, COUNT(*) AS n FROM projects WHERE :page_filters GROUP BY region",
                            engine="postgres", descriptor=None, snap=SQL_SNAP, scope=SQL_SCOPE)
    assert p.has_placeholder and not p.warnings and [c["ok"] for c in p.checks] == [True] * 10
    cq, applied, skipped = custom_sql.compiled(p, [{"ref": "dimension:region", "op": "=", "values": ["KSA"]},
                                                   {"ref": "dimension:priority", "op": "=", "values": ["High"]}],
                                               SQL_SNAP)
    assert applied == ["dimension:region"] and skipped == ["dimension:priority"]
    assert '"projects"."region" = %(p0)s' in cq.tree.sql(dialect="postgres") and cq.params.values == {"p0": "KSA"}
    none = custom_sql.compiled(p, [], SQL_SNAP)[0].tree.sql(dialect="postgres")
    assert "1 = 1" in none
    with pytest.raises(custom_sql.CheckFailed) as hidden:
        custom_sql.validate("SELECT SUM(approved_budget) FROM projects", engine="postgres", descriptor=None,
                            snap=SQL_SNAP, scope=SQL_SCOPE)
    assert hidden.value.step == "visible_fields"
    with pytest.raises(custom_sql.CheckFailed) as mongo:
        custom_sql.validate("SELECT 1", engine="mongodb", descriptor=None, snap=SQL_SNAP, scope=SQL_SCOPE)
    assert mongo.value.error.code == "engine_not_supported"


@pytest.mark.parametrize("dialect_engine", ["postgres", "sqlserver"])
def test_a_schema_qualified_column_points_at_the_scoped_alias(dialect_engine: str) -> None:
    """Run 01a1ce49: ``dbo.t.col`` named the table the scope injector replaces, so the database couldn't bind it."""
    from tela_api.query.scope_injector import inject

    p = custom_sql.validate("SELECT DISTINCT public.projects.region AS r FROM public.projects",
                            engine=dialect_engine, descriptor=None, snap=SQL_SNAP, scope=SQL_SCOPE)
    scoped = inject(p.tree.copy(), SQL_SNAP, SQL_SCOPE, custom_sql.Params("t")).sql(dialect=p.dialect)
    assert "public.projects.region" not in scoped.replace('"', "").replace("[", "").replace("]", "")
    assert scoped.replace('"', "").replace("[", "").replace("]", "").startswith("SELECT DISTINCT projects.region AS r")


def test_check_tokens_bind_user_connection_and_statement() -> None:
    tok = custom_sql.issue_token(b"k", user_id="u", connection_id="c", ast_hash="h")
    custom_sql.verify_token(b"k", tok, user_id="u", connection_id="c", ast_hash="h")
    for kw in ({"user_id": "x"}, {"connection_id": "x"}, {"ast_hash": "x"}):
        with pytest.raises(TelaError):
            custom_sql.verify_token(b"k", tok, **{"user_id": "u", "connection_id": "c", "ast_hash": "h", **kw})
    with pytest.raises(TelaError):
        custom_sql.verify_token(b"other", tok, user_id="u", connection_id="c", ast_hash="h")


def test_personal_measure_needs_an_approved_link() -> None:
    from tela_api.query.personal import with_personal
    from tela_query_spec.spec import parse_spec

    spec, _ = parse_spec({**SPEC, "measure": {"ref": "measure:pm_x"}, "breakdowns": [], "personal_measures": [
        {"key": "pm_x", "label": "X", "entity": "projects", "agg": "count", "linked_to": "tickets"}]})
    with pytest.raises(TelaError) as no_link:
        with_personal(spec, SNAP)
    assert "no approved link" in no_link.value.message


def test_catalog_issues_permits_datetime_source_for_date_fields() -> None:
    from tela_api.model.catalog import catalog_issues

    catalog = {
        "public.projects": [
            {"name": "id", "type": "integer"},
            {"name": "region", "type": "varchar(64)"},
            {"name": "go_live", "type": "timestamp with time zone"},
        ],
        "public.tickets": [
            {"name": "id", "type": "integer"},
            {"name": "queue", "type": "varchar(64)"},
        ],
    }
    # SNAP has go_live declared as data_type: date, while catalog has timestamp with time zone (datetime)
    issues = catalog_issues(SNAP, catalog)
    assert issues == []



def test_dashboard_filters_reach_the_answer_measure() -> None:
    """US-189: Ask on a dashboard applies only the page filters the answer's measure reaches, ad-hoc rules included."""
    from tela_api.ask.orchestrator import page_filter_notes
    from tela_api.content.viewer import merge_page_filters
    from tela_query_spec.spec import Filter

    page = [Filter(ref="dimension:region", op="=", values=["KSA"]), Filter(ref="dimension:queue", op="=", values=["IT"])]
    out, applied, skipped = merge_page_filters(SPEC, SNAP, page, True)
    assert applied == ["dimension:region"] and skipped == ["dimension:queue"]
    assert out["filters"] == [{"ref": "dimension:region", "op": "=", "values": ["KSA"]}] and SPEC["filters"] == []
    listed = {k: v for k, v in SPEC.items() if k != "measure"} | {"measures": [{"ref": "measure:tickets"}]}
    assert merge_page_filters(listed, SNAP, page, True)[1] == ["dimension:queue"]
    adhoc = {**SPEC, "measure": {"adhoc": {"entity": "tickets", "agg": "count", "label": "T", "plain_rule": "All."}}}
    assert merge_page_filters(adhoc, SNAP, page, True)[1:] == (["dimension:queue"], ["dimension:region"])
    assert page_filter_notes(applied, skipped, SNAP) == [
        "Dashboard filters: Region", "Dashboard filters not applied (they don't reach this measure): Queue"]
    assert page_filter_notes([], [], SNAP) == []
