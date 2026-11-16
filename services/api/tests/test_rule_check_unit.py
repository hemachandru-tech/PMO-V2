"""Drafted SQL checked against the model's business rules (always, population, display, named terms)."""

from __future__ import annotations

from tela_api.ask.rule_check import repair_prompt, rule_gaps
from tela_query_spec.model import ModelSnapshot


def snap() -> ModelSnapshot:
    def f(c: str) -> dict:
        return {"key": c, "source_column": c, "label": c, "data_type": "text"}
    return ModelSnapshot.model_validate({
        "model_version_id": "v", "connection_id": "c", "engine": "mysql",
        "entities": [
            {"key": "orders", "label": "Orders", "source_object": "shop.orders", "primary_key": ["id"],
             "fields": [f(c) for c in ("id", "status_name", "portfolio_name", "colour", "cio_name")]},
            {"key": "tasks", "label": "Tasks", "source_object": "shop.tasks", "primary_key": ["id"],
             "fields": [f(c) for c in ("id", "order_id", "is_critical")]}],
        "links": [{"from_entity": "tasks", "to_entity": "orders", "from_fields": ["order_id"], "to_fields": ["id"],
                   "cardinality": "many_to_one"}],
        "rules": [
            {"key": "scope", "term": "Default scope", "entity": "orders", "always": True,
             "sql": "portfolio_name <> 'Test' OR portfolio_name IS NULL"},
            {"key": "open", "term": "Open orders", "entity": "orders", "kind": "population",
             "sql": "status_name NOT IN ('Closed') OR status_name IS NULL"},
            {"key": "amber", "term": "Amber", "entity": "orders", "kind": "display",
             "sql": "CASE WHEN colour = 'Yellow' THEN 'Amber' ELSE colour END"},
            {"key": "crit", "term": "Critical path", "entity": "tasks", "sql": "is_critical = 'Yes'"}]})


def terms(sql: str, q: str) -> list[str]:
    s = snap()
    return [g.rule.term for g in rule_gaps(sql, s, q, list(s.rules), "mysql")]


SCOPE = "(portfolio_name <> 'Test' OR portfolio_name IS NULL)"


def test_a_count_by_cio_without_the_population_is_sent_back() -> None:
    assert terms(f"SELECT cio_name, COUNT(*) FROM shop.orders WHERE {SCOPE} GROUP BY cio_name",
                 "How are orders distributed by CIO?") == ["Open orders"]


def test_a_status_the_question_names_keeps_the_population_out() -> None:
    assert terms(f"SELECT COUNT(*) FROM shop.orders WHERE {SCOPE} AND status_name = 'On Hold'",
                 "How many orders are on hold?") == []


def test_a_missing_always_rule_and_a_bare_display_column_are_named() -> None:
    got = terms("SELECT id, colour FROM shop.orders WHERE status_name <> 'Closed'", "List orders with colour")
    assert got == ["Default scope", "Amber"]


def test_a_term_the_question_names_brings_its_table() -> None:
    got = terms(f"SELECT COUNT(*) FROM shop.orders WHERE {SCOPE} AND status_name = 'Active'",
                "How many active orders are in Critical Path Orders?")
    assert got == ["Critical path"]
    ok = terms(f"SELECT COUNT(DISTINCT o.id) FROM shop.orders o JOIN shop.tasks t ON t.order_id = o.id "
               f"WHERE {SCOPE} AND o.status_name = 'Active' AND t.is_critical = 'Yes'",
               "How many active orders are in Critical Path Orders?")
    assert ok == []


def test_the_repair_names_each_rule_with_its_condition_and_the_data_date() -> None:
    s = snap()
    gaps = rule_gaps("SELECT COUNT(*) FROM shop.orders", s, "How many orders?", list(s.rules), "mysql")
    text = repair_prompt("How many orders?", "SELECT COUNT(*) FROM shop.orders", gaps, s, "2026-10-25")
    assert "Default scope [shop.orders]" in text and "Open orders [shop.orders]" in text and "status_name NOT IN" in text


def test_an_always_rule_written_as_two_filters_counts_and_does_not_hide_the_population() -> None:
    s = ModelSnapshot.model_validate({
        "model_version_id": "v", "connection_id": "c", "engine": "mysql",
        "entities": [{"key": "orders", "label": "Orders", "source_object": "shop.orders", "primary_key": ["id"],
                      "fields": [{"key": c, "source_column": c, "label": c, "data_type": "text"}
                                 for c in ("id", "state", "dropped_on", "closed_on", "cio")]}],
        "rules": [
            {"key": "cut", "term": "Cut-off", "entity": "orders", "always": True,
             "sql": "(state <> 'Dropped' OR dropped_on >= '2026-01-01') AND (state <> 'Done' OR closed_on >= '2026-01-01')"},
            {"key": "open", "term": "Open orders", "entity": "orders", "kind": "population",
             "sql": "state NOT IN ('Done', 'Dropped')"},
            {"key": "name", "term": "Order", "entity": "orders", "kind": "expression", "sql": "id"}]})
    sql = ("SELECT cio, COUNT(*) FROM shop.orders WHERE (state <> 'Dropped' OR dropped_on >= '2026-01-01') "
           "AND (state <> 'Done' OR closed_on >= '2026-01-01') GROUP BY cio")
    assert [g.rule.term for g in rule_gaps(sql, s, "How are orders spread by CIO? Order list", list(s.rules), "mysql")] \
        == ["Open orders"]


def test_a_plain_status_filter_is_the_questions_own_even_when_a_scope_rule_uses_that_column() -> None:
    s = ModelSnapshot.model_validate({
        "model_version_id": "v", "connection_id": "c", "engine": "mysql",
        "entities": [{"key": "orders", "label": "Orders", "source_object": "shop.orders", "primary_key": ["id"],
                      "fields": [{"key": c, "source_column": c, "label": c, "data_type": "text"}
                                 for c in ("id", "state", "dropped_on", "closed_on", "cio")]}],
        "rules": [
            {"key": "cut", "term": "Cut-off", "entity": "orders", "always": True,
             "sql": "(state <> 'Dropped' OR dropped_on >= '2026-01-01') AND (state <> 'Done' OR closed_on >= '2026-01-01')"},
            {"key": "open", "term": "Open orders", "entity": "orders", "kind": "population",
             "sql": "state NOT IN ('Done', 'Dropped')"}]})
    cut = "(state <> 'Dropped' OR dropped_on >= '2026-01-01') AND (state <> 'Done' OR closed_on >= '2026-01-01')"
    for own in ("state = 'Active'", "state NOT IN ('Done', 'Dropped')"):
        sql = f"SELECT COUNT(*) FROM shop.orders WHERE {cut} AND {own}"
        assert rule_gaps(sql, s, "How many orders?", list(s.rules), "mysql") == []


def test_one_word_of_another_tables_rule_does_not_pull_that_table_in() -> None:
    s = snap()
    rules = [*s.rules, s.rules[0].model_copy(update={"key": "act", "term": "active", "entity": "tasks",
                                                       "always": False, "sql": "is_critical = 'Active'"})]
    sql = f"SELECT COUNT(*) FROM shop.orders WHERE {SCOPE} AND status_name = 'Active'"
    assert [g.rule.term for g in rule_gaps(sql, s, "How many active orders?", rules, "mysql")] == []


def test_a_table_written_as_one_quoted_name_with_a_dot_is_recognised() -> None:
    sql = f"SELECT cio_name, COUNT(*) FROM `shop.orders` WHERE {SCOPE} GROUP BY cio_name"
    assert terms(sql, "How are orders distributed by CIO?") == ["Open orders"]


def test_only_relabelling_display_rules_are_enforced() -> None:
    from tela_api.ask.rule_check import relabels
    assert relabels("CASE WHEN colour = 'Yellow' THEN 'Amber' ELSE colour END", "mysql")
    assert relabels("CASE colour WHEN 'Yellow' THEN 'Amber' ELSE colour END", "mysql")
    assert relabels("COALESCE(cio_name, '(not set)')", "mysql")
    assert not relabels("CASE WHEN region IN ('A', 'B') THEN 'Group' ELSE region END", "mysql")
    assert not relabels("CASE WHEN DATEDIFF({data_date}, edited) BETWEEN 0 AND 7 THEN '0-7' END", "mysql")


def test_a_question_for_two_figures_needs_two_aggregates() -> None:
    from tela_api.ask.rule_check import figures_missing
    q = "How many active coaches and how many active students are there?"
    assert figures_missing(q, "SELECT COUNT(*) FROM u WHERE t = 'coach'", "mysql")
    assert figures_missing(q, "SELECT SUM(t = 'coach') AS c, SUM(t = 'student') AS s FROM u", "mysql") is None
    assert figures_missing("How many coaches are there?", "SELECT COUNT(*) FROM u", "mysql") is None
    assert figures_missing(q, "SELECT t, COUNT(*) FROM u GROUP BY t", "mysql") is None  # a breakdown answers both
