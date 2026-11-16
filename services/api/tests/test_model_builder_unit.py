"""Model builder (column choice → documents → LLM model → preview) and the rules Direct SQL is given."""

from __future__ import annotations

from tela_api.content.sql_context import rule_columns, schema_context, selected_rules
from tela_api.model.builder import (
    RuleIn,
    _dictionary,
    _rule_parts,
    apply_selection,
    check_rule,
    place_rules,
)
from tela_query_spec.model import ModelSnapshot

COLUMNS = {"id", "status_name", "status", "region_name", "portfolio_name", "go_live", "deployed_on"}


def definition() -> dict:
    fields = [{"key": c, "source_column": c, "label": c, "data_type": "text"} for c in sorted(COLUMNS)]
    return {"entities": [{"key": "orders", "label": "Orders", "source_object": "shop.orders", "primary_key": ["id"],
                          "fields": fields}],
            "links": [], "measures": [], "dimensions": [], "settings": {}}


def catalog() -> dict:
    return {"shop.orders": [{"name": c} for c in sorted(COLUMNS)]}


def test_a_column_dictionary_is_told_apart_from_rules() -> None:
    dictionary = "\n".join(["| Column | Meaning |", "|---|---|"] + [f"| `{c}` | something |" for c in sorted(COLUMNS)])
    rules = "\n".join(["| Records | Open when |", "|---|---|", "| Orders (`status_name`) | not Closed |",
                       "- An order is late when go_live is past."])
    assert _dictionary(dictionary, COLUMNS)
    assert not _dictionary(rules, COLUMNS)


def test_rule_parts_leave_dictionaries_out_and_keep_rule_sections() -> None:
    doc = {"title": "Rules", "text": "# Glossary\n" + "\n".join(f"| `{c}` | x |" for c in sorted(COLUMNS))
           + "\n# Open\nAn order is open when status_name is not Closed.\n"}
    parts = _rule_parts([doc], COLUMNS)
    assert len(parts) == 1 and "open when" in parts[0] and "`go_live`" not in parts[0]


def test_a_rule_naming_an_unknown_column_is_kept_for_review() -> None:
    ent = definition()["entities"][0]
    cols, problem = check_rule("status_name <> 'Closed' AND colour = 'Red'", ent, COLUMNS, "mysql")
    assert problem and "colour" in problem
    cols, problem = check_rule("deployed_on IS NULL AND go_live < {data_date}", ent, COLUMNS, "mysql")
    assert problem is None and cols == {"deployed_on", "go_live"}
    assert check_rule("status_name <> ", ent, COLUMNS, "mysql")[1] == "The condition is not valid SQL."


def test_placed_rules_are_checked_and_ask_for_the_columns_they_need() -> None:
    d = definition()
    d["entities"][0]["fields"][[f["key"] for f in d["entities"][0]["fields"]].index("go_live")]["is_queryable"] = False
    rules, needs = place_rules([
        RuleIn(term="Open order", synonyms=["active orders"], meaning="Not Closed.", table="orders",
               sql="status_name IS NULL OR status_name <> 'Closed'"),
        RuleIn(term="Late", table="shop.orders", sql="deployed_on IS NULL AND go_live < {data_date}"),
        RuleIn(term="Open order", table="orders", sql="status_name = 'Open'"),  # a repeat: the first stands
        RuleIn(term="Colour", table="orders", sql="colour = 'Red'"),
        RuleIn(term="Nowhere", table="missing_table", sql="x = 1"),
    ], d, catalog(), "document", "mysql")
    assert [r["term"] for r in rules] == ["Open order", "Late", "Colour", "Nowhere"]
    assert [r["status"] for r in rules] == ["ok", "ok", "needs_review", "needs_review"]
    assert needs == {"orders": ["go_live"]}


def test_unpicked_columns_are_hidden_unless_the_model_needs_them() -> None:
    d = definition()
    d["dimensions"] = [{"key": "region", "label": "Region", "entity": "orders", "field": "region_name"}]
    counts = apply_selection(d, {"shop.orders": {"status_name"}})
    shown = {f["source_column"] for f in d["entities"][0]["fields"] if f["is_queryable"]}
    assert shown == {"id", "status_name", "region_name"} and counts == {"shown": 3, "hidden": 4}


def snapshot(rules: list[dict], settings: dict | None = None) -> ModelSnapshot:
    d = definition()
    for f in d["entities"][0]["fields"]:
        f.pop("is_queryable", None)
    return ModelSnapshot.model_validate({**d, "model_version_id": "v1", "connection_id": "c1", "engine": "mysql",
                                         "rules": rules, "settings": settings or {}})


RULES = [
    {"key": "orders_scope", "term": "Default scope", "meaning": "Leave out the Test portfolio.", "entity": "orders",
     "sql": "portfolio_name IS NULL OR portfolio_name <> 'Test'", "always": True},
    {"key": "orders_open", "term": "Open order", "synonyms": ["active orders"], "meaning": "Not Closed.",
     "entity": "orders", "sql": "status_name IS NULL OR status_name <> 'Closed'"},
    {"key": "orders_late", "term": "Late order", "meaning": "Past go-live, not deployed.", "entity": "orders",
     "sql": "deployed_on IS NULL AND go_live < {data_date}"},
    {"key": "orders_bad", "term": "Open order bad", "entity": "orders", "sql": "colour = 1", "status": "needs_review"},
]


def test_direct_sql_gets_the_always_rules_and_the_rules_the_question_names() -> None:
    s = snapshot(RULES)
    picked = [r.term for r in selected_rules(s, {"orders"}, "How many active orders by region?")]
    # "always" first, then the ones the question names; an untouched rule ("Late order") and one held for review
    # stay out, so the prompt holds fewer, relevant rules
    assert picked[:2] == ["Default scope", "Open order"] and "Late order" not in picked and "Open order bad" not in picked
    assert rule_columns(s, s.entities[0]) >= {"portfolio_name", "status_name", "go_live", "deployed_on"}


def test_the_context_carries_the_data_date_and_the_rules_never_review_ones() -> None:
    s = snapshot(RULES, {"reporting_date": "2026-10-25"})
    out = schema_context(s, frozenset(), "How many late orders?", dialect="mysql", as_of="2026-10-25")
    assert "Today for this data (data date) is 2026-10-25" in out
    assert "Late order" in out and "Default scope" in out and "colour = 1" not in out


def test_the_data_date_placeholder_is_checked_and_written_in_quoted_or_not() -> None:
    from tela_api.model.builder import bind_date, normalise_date
    ent = definition()["entities"][0]
    for sql in ("go_live < '{data_date}'", "go_live < {data_date}"):
        assert check_rule(sql, ent, COLUMNS, "mysql")[1] is None
        assert normalise_date(sql) == "go_live < {data_date}"
        assert bind_date(sql, "2026-10-25") == "go_live < '2026-10-25'"
    s = snapshot(RULES, {"reporting_date": "2026-10-25"})
    out = schema_context(s, frozenset(), "How many late orders?", dialect="mysql", as_of="2026-10-25")
    assert "go_live < '2026-10-25'" in out and "{data_date}" not in out


def test_a_definition_the_document_overrides_is_dropped() -> None:
    rules, _ = place_rules([
        RuleIn(term="Open order", table="orders", sql="status_name <> 'Closed'"),
        RuleIn(term="Open order (old list)", table="orders", sql="status_name IN ('New')", overridden=True),
    ], definition(), catalog(), "document", "mysql")
    assert [r["term"] for r in rules] == ["Open order"]


def test_a_rule_phrase_the_question_uses_brings_its_table() -> None:
    from tela_api.content.sql_context import relevant_tables
    d = definition()
    d["entities"].append({"key": "tasks", "label": "Tasks", "source_object": "shop.tasks", "primary_key": ["id"],
                          "fields": [{"key": "id", "source_column": "id", "label": "id", "data_type": "text"},
                                     {"key": "is_critical", "source_column": "is_critical", "label": "Critical",
                                      "data_type": "text"}]})
    for f in d["entities"][0]["fields"]:
        f.pop("is_queryable", None)
    s = ModelSnapshot.model_validate({**d, "model_version_id": "v1", "connection_id": "c1", "engine": "mysql",
                                      "rules": [{"key": "crit", "term": "Critical path", "entity": "tasks",
                                                 "sql": "is_critical = 'Yes'"}]})
    assert "tasks" in relevant_tables(s, "How many orders are in Critical Path Orders?")


def test_the_database_reason_goes_back_for_a_repair() -> None:
    from tela_api.ask.direct_sql import run_error_text
    from tela_common.errors import ErrorCode, TelaError
    exc = TelaError(ErrorCode.SOURCE_UNREACHABLE, "The data source could not run this query.",
                    {"admin_detail": {"error": "1055: Expression #1 of SELECT list is not in GROUP BY"}})
    assert "1055" in run_error_text(exc)


def test_always_empty_columns_are_hidden_and_leave_the_record_columns() -> None:
    from tela_api.model.builder import hide_empty
    d = definition()
    d["entities"][0]["display_fields"] = ["region_name", "status_name"]
    hide_empty(d, {"orders": {"region_name", "id"}})
    f = {x["source_column"]: x for x in d["entities"][0]["fields"]}
    assert f["region_name"]["is_queryable"] is False and "Always empty" in f["region_name"]["description"]
    assert f["id"].get("is_queryable", True) and d["entities"][0]["display_fields"] == ["status_name"]


def test_an_empty_column_a_measure_uses_stays_visible() -> None:
    from tela_api.model.builder import hide_empty
    d = definition()
    d["measures"] = [{"key": "m", "label": "M", "entity": "orders", "plain_rule": "x",
                      "expression": {"agg": "count", "where": {"field": "orders.region_name", "op": "=", "value": "A"}}}]
    hide_empty(d, {"orders": {"region_name", "go_live"}})
    f = {x["source_column"]: x for x in d["entities"][0]["fields"]}
    assert f["region_name"].get("is_queryable", True) and f["go_live"]["is_queryable"] is False


def test_population_and_display_rules_are_sent_and_labelled() -> None:
    rules = [*RULES,
             {"key": "orders_pop", "term": "Orders in the book", "entity": "orders", "kind": "population",
              "sql": "status_name IS NULL OR status_name <> 'Closed'"},
             {"key": "orders_disp", "term": "Status shown", "entity": "orders", "kind": "display",
              "sql": "CASE status_name WHEN 'Yellow' THEN 'Amber' ELSE status_name END"}]
    s = snapshot(rules)
    out = schema_context(s, frozenset(), "How many orders by region?", dialect="mysql", as_of="2026-10-25")
    assert "(population: apply it when" in out and "(display: when you show this column" in out
    assert "Never compare a DATE or DATETIME column with ''" in out


def test_an_agreed_term_a_business_rule_defines_is_left_out() -> None:
    from tela_api.ask.direct_sql import without_ruled_terms
    s = snapshot(RULES)
    hints = [{"term": "Open orders", "sql": "x"}, {"term": "Big orders", "sql": "y"}]
    assert [h["term"] for h in without_ruled_terms(s, hints, "How many open orders?")] == ["Big orders"]


def test_positive_values_are_the_ones_a_rule_must_find_in_the_data() -> None:
    from tela_api.model.builder import positive_values
    pv = positive_values("TRIM(status_name) = 'Open' AND region_name IN ('A', 'B') AND status <> 'X' "
                         "AND NOT (portfolio_name = 'Test') AND go_live < {data_date}", "mysql")
    assert pv == {"status_name": {"Open"}, "region_name": {"A", "B"}}


def test_one_population_per_table_the_first_checked_one() -> None:
    from tela_api.model.builder import settle_populations
    rules = [{"entity": "orders", "kind": "population", "status": "needs_review"},
             {"entity": "orders", "kind": "population", "status": "ok"},
             {"entity": "orders", "kind": "population", "status": "ok"}]
    settle_populations(rules)
    assert [r["kind"] for r in rules] == ["population", "population", "condition"]


def test_the_first_definition_of_a_term_stands_and_inherits_the_population_role() -> None:
    rules, _ = place_rules([
        RuleIn(term="Open orders", table="orders", sql="status_name NOT IN ('Closed')"),
        RuleIn(term="Open order", table="orders", sql="status_name IN ('New')", kind="population"),
    ], definition(), catalog(), "document", "mysql")
    assert len(rules) == 1 and rules[0]["sql"] == "status_name NOT IN ('Closed')" and rules[0]["kind"] == "population"


def test_terms_that_differ_only_by_the_table_name_are_one_term() -> None:
    rules, _ = place_rules([
        RuleIn(term="Open", table="orders", sql="status_name NOT IN ('Closed')"),
        RuleIn(term="Open orders", table="orders", sql="status_name IN ('New')", kind="population"),
        RuleIn(term="Nothing", table="orders", sql="FALSE", always=True),
    ], definition(), catalog(), "document", "mysql")
    assert [(r["term"], r["kind"], r["status"]) for r in rules] == [
        ("Open", "population", "ok"), ("Nothing", "condition", "needs_review")]


def test_two_reads_merge_keeping_the_checked_rule_and_every_term() -> None:
    from tela_api.model.builder import _rule_parts, merge_passes
    d = definition()
    first, _ = place_rules([RuleIn(term="Amber", table=None, sql="CASE WHEN status_name = 'Y' THEN 'A' END",
                                   kind="display"),
                            RuleIn(term="Open orders", table="orders", sql="status_name <> 'Closed'")],
                           d, catalog(), "document", "mysql")
    second, _ = place_rules([RuleIn(term="Amber", table="orders", sql="CASE WHEN status_name = 'Y' THEN 'A' END",
                                    kind="display"),
                             RuleIn(term="Open", table="orders", sql="status_name IN ('New')", kind="population"),
                             RuleIn(term="Late", table="orders", sql="go_live < {data_date}")],
                            d, catalog(), "document", "mysql")
    merged = merge_passes([first, second], d)
    by_term = {r["term"]: r for r in merged}
    assert set(by_term) == {"Amber", "Open orders", "Late"}
    assert by_term["Amber"]["status"] == "ok" and by_term["Amber"]["entity"] == "orders"
    assert by_term["Open orders"]["sql"] == "status_name <> 'Closed'" and by_term["Open orders"]["kind"] == "population"
    assert len({r["key"] for r in merged}) == len(merged)
    doc = {"title": "Rules", "text": "\n".join(f"# S{i}\n" + "x " * 400 for i in range(6))}
    assert len(_rule_parts([doc], COLUMNS, 7000)) != len(_rule_parts([doc], COLUMNS, 2000))
