"""Profile cards and checked rule pieces for Direct SQL: read from the whole table, generic for any database."""

from __future__ import annotations

import asyncio
from typing import Any

from tela_api.ask import column_cards as cc
from tela_api.ask.column_cards import Card, card_note, card_sql, column_cards, pin_today, rule_counts
from tela_api.content.sql_context import schema_context, selected_rules, term_score
from tela_common.errors import ErrorCode, TelaError
from tela_query_spec.model import ModelSnapshot


def snap() -> ModelSnapshot:
    def f(c: str, t: str = "text", label: str | None = None, **kw: object) -> dict:
        return {"key": c, "source_column": c, "label": label or c.replace("_", " ").title(), "data_type": t, **kw}
    return ModelSnapshot.model_validate({
        "model_version_id": "v1", "connection_id": "c", "engine": "mysql",
        "entities": [
            {"key": "orders", "label": "Orders", "source_object": "shop.orders", "primary_key": ["id"],
             "fields": [f("id", "integer"), f("status", values=["Open", "Closed"]), f("portfolio"),
                        f("manager", label="Manager"), f("manager_name", label="Manager"),
                        f("due", "date"), f("code")]},
            {"key": "changes", "label": "Changes", "source_object": "shop.changes", "primary_key": ["id"],
             "fields": [f("id", "integer"), f("order_id", "integer"), f("approval", values=["Approved", "No"])]}],
        "links": [{"from_entity": "changes", "to_entity": "orders", "from_fields": ["order_id"], "to_fields": ["id"],
                   "cardinality": "many_to_one"}],
        "rules": [
            {"key": "scope", "term": "Default scope", "entity": "orders", "always": True,
             "sql": "`portfolio` <> 'Internal' OR `portfolio` IS NULL"},
            {"key": "slip", "term": "Slip", "entity": "orders", "sql": "`due` < {data_date}"},
            {"key": "pcr", "term": "Approved PCR", "entity": "changes", "sql": "`approval` = 'Approved'"},
            {"key": "bad", "term": "Broken", "entity": "orders", "sql": "`nope` = 1"}],
        "settings": {"reporting_date": "2026-10-25"}})


class Run:
    """A fake scoped run: answers the card query, the top-values query and rule counts."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def __call__(self, sql: str) -> dict[str, Any]:
        self.calls.append(sql)
        if "`nope`" in sql:
            raise TelaError(ErrorCode.VALIDATION_ERROR, "unknown column")
        if "SUM(CASE WHEN" in sql:
            n = sql.count("SUM(CASE WHEN")
            return {"rows": [[111, *([95] * n)]]}
        if "GROUP BY" in sql:
            return {"rows": [["Open", 88], ["Closed", 23]]}
        if sql.startswith("SELECT COUNT(*) AS n"):
            # id unique; status 2 values; portfolio 2 of 111; manager 23; manager_name 108; due range; code empty
            return {"columns": [{"name": c} for c in (
                "n", "f0", "d0", "lo0", "hi0", "f1", "d1", "f2", "d2", "f3", "d3", "f4", "d4", "f5", "d5", "lo5",
                "hi5", "f6", "d6")],
                "rows": [[111, 111, 111, 1, 111, 111, 2, 2, 1, 23, 9, 108, 12, 100, 90, "2025-01-01",
                          "2027-03-31", 0, 0]]}
        return {"rows": []}


def setup_function() -> None:
    cc._cards.clear()
    cc._tops.clear()
    cc._rule_hits.clear()


def test_the_card_query_reads_counts_distinct_and_ranges_with_aggregates_only() -> None:
    e = snap().entities[0]
    sql = card_sql(e, [e.fields[0], e.fields[1], e.fields[5]], "mysql")
    assert sql.startswith("SELECT COUNT(*) AS n, COUNT(`id`) AS f0, COUNT(DISTINCT `id`) AS d0, MIN(`id`) AS lo0")
    assert "COUNT(DISTINCT `status`) AS d1" in sql and "MIN(`status`)" not in sql  # no text values in a range
    assert "MAX(`due`) AS hi2" in sql and "LIMIT" not in sql


def test_cards_say_how_full_unique_and_wide_each_column_is() -> None:
    s, run = snap(), Run()
    cols = [(s.entities[0], f) for f in s.entities[0].fields]
    notes = asyncio.run(column_cards(run, s, frozenset(), cols, "mysql", "c", show_values=True,
                                     valued={"orders.status"}))
    assert notes["orders.id"] == "filled in every row (111); unique per row; from 1 to 111"
    assert "filled in 2 of 111 rows (2%)" in notes["orders.portfolio"]
    assert "add OR portfolio IS NULL" in notes["orders.portfolio"]
    assert "most common: 'Open' 88, 'Closed' 23" in notes["orders.status"]
    assert "manager_name has the same label and is filled in 108 of 111 rows (97%)" in notes["orders.manager"]
    assert notes["orders.code"] == "blank in all 111 rows: holds no data"
    assert "from 2025-01-01 to 2027-03-31" in notes["orders.due"]
    again = asyncio.run(column_cards(run, s, frozenset(), cols, "mysql", "c", show_values=True,
                                     valued={"orders.status"}))
    assert again == notes and sum(1 for c in run.calls if c.startswith("SELECT COUNT(*) AS n")) == 1  # cached


def test_without_samples_a_card_keeps_counts_but_no_values_or_ranges() -> None:
    s = snap()
    cols = [(s.entities[0], f) for f in s.entities[0].fields]
    notes = asyncio.run(column_cards(Run(), s, frozenset(), cols, "mysql", "c", show_values=False,
                                     valued={"orders.status"}))
    assert "most common" not in notes["orders.status"] and "from" not in notes["orders.due"]
    assert notes["orders.status"] == "filled in every row (111); 2 distinct values"


def test_a_card_note_is_written_for_one_column() -> None:
    f = snap().entities[0].fields[2]
    assert card_note(f, Card(rows=10, filled=10, distinct=3), [], show_values=False) == \
        "filled in every row (10); 3 distinct values"


def test_rule_pieces_are_run_on_the_data_and_a_rule_that_cannot_run_gets_no_count() -> None:
    s, run = snap(), Run()
    got = asyncio.run(rule_counts(run, s, list(s.rules), "mysql", "c", "2026-10-25"))
    assert got == {"scope": (95, 111), "slip": (95, 111), "pcr": (95, 111)}
    assert any("'2026-10-25'" in c for c in run.calls)  # the data date is written into the rule
    before = len(run.calls)
    assert asyncio.run(rule_counts(run, s, list(s.rules), "mysql", "c", "2026-10-25")) == got
    assert len(run.calls) == before  # each rule is run once per model version


def test_the_rules_context_shows_each_rule_with_its_count() -> None:
    s = snap()
    text = schema_context(s, frozenset(), "how many orders slipped", dialect="mysql", as_of="2026-10-25",
                          cards={"orders.portfolio": "filled in 2 of 111 rows (2%)"},
                          rule_counts={"slip": (40, 111)})
    assert "- Slip [shop.orders]" in text and "(checked on the data: true for 40 of 111 rows)" in text
    assert "data: filled in 2 of 111 rows (2%)" in text


def test_word_forms_and_spelled_out_acronyms_find_the_business_rule() -> None:
    s = snap()
    assert [r.key for r in selected_rules(s, {"orders"}, "How many orders have slipped?") if r.key == "slip"]
    assert [r.key for r in selected_rules(s, {"orders", "changes"}, "orders without an approved Project Change "
                                          "Request")][-1] == "pcr"
    assert term_score("delayed delivery", ["Slip"], set()) == 0


def test_today_is_always_the_data_date() -> None:
    sql = pin_today("SELECT COUNT(*) FROM t WHERE d < CURRENT_DATE AND e < NOW() AND f < CURDATE()", "2026-10-25",
                    "mysql")
    assert "CURRENT_DATE" not in sql and "NOW()" not in sql and sql.count("CAST('2026-10-25' AS DATE)") == 3
    same = "SELECT 1 FROM t WHERE d < '2026-10-25'"
    assert pin_today(same, "2026-10-25", "mysql") is same
    assert pin_today("SELECT GETDATE()", "2026-10-25", "tsql") == "SELECT CAST('2026-10-25' AS DATE)"
