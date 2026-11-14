"""Direct SQL in Ask (ENH-028 US-257a): the pieces that need no database — named conditions written as SQL, the
frame's shape checked against a statement's columns, and AG-10's prompt per shape."""

from __future__ import annotations

from typing import Any

import pytest
from sqlglot import parse_one

from tela_api.ask import direct_sql
from tela_api.ask.direct_sql import DirectAnswer, condition_hints, condition_sql, shape_problem, system_prompt
from tela_api.ask.frame import SHAPES, rules_frame
from tela_api.content.sql_context import AG10_SYSTEM
from tela_api.knowledge.conditions import parse
from tela_query_spec.model import ModelSnapshot


def field(key: str, data_type: str = "text") -> dict[str, Any]:
    return {"key": key, "label": key.replace("_", " ").title(), "data_type": data_type, "source_column": key}


def snapshot() -> ModelSnapshot:
    return ModelSnapshot.model_validate({
        "model_version_id": "v1", "connection_id": "c1", "engine": "postgres",
        "entities": [{"key": "projects", "label": "Projects", "source_object": "public.projects",
                      "primary_key": ["project_id"],
                      "fields": [field("project_id", "integer"), field("name"), field("status"), field("region"),
                                 field("budget", "decimal"), field("go_live", "date")]}],
        "measures": [{"key": "open_projects", "label": "Open projects", "entity": "projects", "plain_rule": "Active.",
                      "expression": {"agg": "count", "where": {"field": "status", "op": "=", "value": "Active"}}}],
        "dimensions": [{"key": "region", "label": "Region", "entity": "projects", "field": "region"}],
    })


def cond_row(term: str, condition: dict[str, Any], **kw: Any) -> dict[str, Any]:
    return {"term": term, "term_key": term.lower(), "synonyms": [], "entity": "projects", "condition": condition,
            "status": "approved", "source": "definition_sheet", "measure_ref": None, "conflict": None,
            "prefer": None, **kw}


@pytest.mark.parametrize(("cond", "sql"), [
    ({"field": "status", "op": "=", "value": "Active"}, "public.projects.status = 'Active'"),
    ({"field": "status", "op": "!=", "value": "O'Neil"}, "public.projects.status <> 'O''Neil'"),
    ({"field": "region", "op": "not_in", "values": ["KSA", "UAE"]}, "public.projects.region NOT IN ('KSA', 'UAE')"),
    ({"field": "budget", "op": ">", "value": 1000000}, "public.projects.budget > 1000000"),
    ({"field": "go_live", "op": "not_null"}, "public.projects.go_live IS NOT NULL"),
    ({"any": [{"field": "status", "op": "=", "value": "Active"}, {"not": {"field": "region", "op": "is_null"}}]},
     "(public.projects.status = 'Active' OR NOT (public.projects.region IS NULL))"),
])
def test_a_named_condition_is_written_as_sql(cond: dict[str, Any], sql: str) -> None:
    assert condition_sql(parse(cond), "projects", snapshot(), frozenset()) == sql


def test_a_condition_sql_cannot_say_plainly_is_left_to_its_words() -> None:
    snap = snapshot()
    asof = parse({"field": "go_live", "op": "<", "value": {"asof_offset_days": 0}})
    assert condition_sql(asof, "projects", snap, frozenset()) is None
    assert condition_sql(parse({"field": "status", "op": "=", "value": "Active"}), "projects", snap,
                         frozenset({"projects.status"})) is None  # hidden from the person


def test_hints_follow_the_order_of_authority_and_hide_what_the_person_cant_see() -> None:
    snap = snapshot()
    rows = [cond_row("Late", {"field": "go_live", "op": "<", "value": {"asof_offset_days": 0}}),
            cond_row("Big", {"field": "budget", "op": ">", "value": 1000000}),
            cond_row("Open", {"field": "status", "op": "in", "values": ["Active", "On Hold"]},
                     measure_ref="measure:open_projects", conflict={"rows": [3, 5]}),
            cond_row("Gulf", {"field": "region", "op": "in", "values": ["KSA"]})]
    hints = {h["term"]: h for h in condition_hints("list of open late projects", rows, snap, frozenset())}
    assert set(hints) == {"Open", "Late"}  # only the terms the question uses
    # A conflict the Admin hasn't settled: the certified measure's rule answers meanwhile.
    assert hints["Open"]["use"] == "measure" and hints["Open"]["conflict"]
    assert hints["Open"]["sql"] == "public.projects.status = 'Active'"
    assert hints["Late"]["sql"] is None and hints["Late"]["rule"].startswith("Projects where")
    assert condition_hints("big projects", rows, snap, frozenset({"projects.budget"})) == []


@pytest.mark.parametrize(("shape", "sql", "ok"), [
    ("list_of_records", "SELECT name, region FROM projects", True),
    ("list_of_records", "SELECT COUNT(*) AS n FROM projects", False),  # 01a1ca5c: a count for a list
    ("list_of_records", "SELECT region, COUNT(*) FROM projects GROUP BY region", False),
    ("record_detail", "SELECT name, status, go_live FROM projects WHERE name = 'Atlas'", True),
    ("number", "SELECT COUNT(*) AS n FROM projects", True),
    ("number", "SELECT name FROM projects", False),
    ("number", "SELECT region, COUNT(*) FROM projects GROUP BY region", False),
    ("breakdown", "SELECT region, COUNT(*) AS n FROM projects GROUP BY region", True),
    ("breakdown", "SELECT COUNT(*) FROM projects", False),
    ("ranking", "SELECT region, SUM(budget) AS b FROM projects GROUP BY region ORDER BY b DESC LIMIT 5", True),
    ("trend", "SELECT DATE_TRUNC('month', go_live) AS m, COUNT(*) FROM projects GROUP BY 1", True),
    ("share", "SELECT 100.0 * SUM(CASE WHEN status = 'Active' THEN 1 ELSE 0 END) / COUNT(*) AS pct FROM projects",
     True),
    ("comparison", "SELECT name FROM projects", False),
])
def test_the_frame_shape_is_checked_against_the_columns(shape: str, sql: str, ok: bool) -> None:
    problem = shape_problem(parse_one(sql, read="postgres"), shape)
    assert (problem is None) == ok, problem
    if not ok:
        assert problem and problem.startswith("The question asks for")


def test_every_answerable_shape_has_a_prompt_rule() -> None:
    assert set(direct_sql.SHAPE_RULES) == set(SHAPES) - {"definition"}


def test_the_prompt_asks_for_the_frames_kind_of_answer_and_the_agreed_terms() -> None:
    snap = snapshot()
    hints = condition_hints("list of open projects", [cond_row("Open", {"field": "status", "op": "=",
                                                                         "value": "Active"})], snap, frozenset())
    frame = rules_frame("list of open projects")
    assert frame.shape == "list_of_records"
    system = system_prompt(snap, frozenset({"projects.budget"}), "list of open projects", frame, hints)
    assert "Answer with a list of records" in system and "no aggregates" in system
    assert "Answer with an aggregate query" not in system and "no :page_filters" in system
    assert "Agreed terms (use these conditions exactly):\n- Open: WHERE public.projects.status = 'Active'" in system
    assert "SQL dialect: postgres" in system and "budget" not in system  # hidden field left out
    count = system_prompt(snap, frozenset(), "how many open projects", rules_frame("how many open projects"), [])
    assert "Answer with one figure" in count
    # The Custom SQL drafter keeps its own instructions (moved, not changed).
    assert AG10_SYSTEM.startswith("You are Tela's SQL drafter (AG-10).") and ":page_filters" in AG10_SYSTEM
    assert "Answer with an aggregate query" in AG10_SYSTEM


def test_an_answer_always_carries_the_badge_and_its_sql_and_is_never_publishable() -> None:
    out = DirectAnswer(status="ok", shape="number", sql="SELECT COUNT(*) FROM projects",
                       result={"columns": [], "rows": [[1]] * 300, "row_count": 300, "truncated": False}).out()
    assert out["badge"] == "custom_sql" and out["badges"] == ["custom_sql"] and out["sql"]
    assert out["certified"] is False and out["publishable"] is False and "published dashboard" in out["publish_note"]
    assert len(out["rows"]) == direct_sql.PREVIEW_ROWS and out["row_count"] == 300


# --------------------------------------------------------------------------- filter values (run 01a1ce59)


def prepared(sql: str) -> Any:
    from tela_api.identity.scope import EntityScope, ResolvedScope
    from tela_api.query import custom_sql

    scope = ResolvedScope("c1", {"projects": EntityScope(all_rows=True)}, frozenset(), frozenset())
    return custom_sql.validate(sql, engine="postgres", descriptor=None, snap=snapshot(), scope=scope)


def test_the_filters_a_query_compares_columns_with_are_found_and_exclusions_left_out() -> None:
    p = prepared("SELECT region, COUNT(*) AS n FROM public.projects WHERE public.projects.status IN ('Open', 'Active') "
                 "AND 3 = budget AND NOT region = 'KSA' AND region <> 'UAE' AND name NOT IN ('x') GROUP BY region")
    got = {(f.column, tuple(f.values)) for f in direct_sql.filters(p.tree, p.aliases)}
    assert got == {("status", ("Open", "Active")), ("budget", (3,))}
    assert direct_sql.filters(prepared("SELECT name FROM projects").tree, {}) == []


@pytest.mark.parametrize(("a", "b", "same"), [(1, "1", True), (1, 1.0, True), ("1.50", 1.5, True), ("Red", " red ", True),
                                              (True, "true", True), (1, 2, False), ("Red", "Amber", False)])
def test_values_compare_as_numbers_or_without_case(a: Any, b: Any, same: bool) -> None:
    assert (direct_sql.value_key(a) == direct_sql.value_key(b)) is same


def test_the_lookups_ask_which_values_are_held_and_for_a_sample() -> None:
    p = prepared("SELECT name FROM projects WHERE status IN ('Open', 'Active')")
    [f] = direct_sql.filters(p.tree, p.aliases)
    assert direct_sql.probe_sql(f, "postgres", among=True) == \
        "SELECT DISTINCT \"status\" FROM public.projects WHERE \"status\" IN ('Open', 'Active') LIMIT 25"
    assert direct_sql.probe_sql(f, "tsql", among=False) == "SELECT DISTINCT TOP 25 [status] FROM public.projects"


async def test_missing_values_go_back_to_the_model_with_the_real_ones() -> None:
    p = prepared("SELECT name FROM projects WHERE status IN ('Open', 'Active') AND region = 'KSA'")
    asked: list[str] = []

    async def run(sql: str) -> dict[str, Any]:
        asked.append(sql)
        if "IN ('Open', 'Active')" in sql:
            return {"rows": [["Active"]]}  # 'Open' isn't held
        if "IN ('KSA')" in sql:
            return {"rows": [["KSA"]]}
        return {"rows": [["Active"], ["On Hold"], [None]]}

    [m] = await direct_sql.missing_values(run, p)
    assert (m.column, m.values, m.known) == ("status", ["Open"], ["Active", "On Hold"])
    assert m.note() == "status has no 'Open'; its values include 'Active', 'On Hold'."
    assert len(asked) == 3  # status held + sample, region held
    prompt = direct_sql.value_repair_prompt("open projects", "SELECT 1", [m])
    assert "returned no rows, because the data doesn't hold these filter values:\n- status has no 'Open'" in prompt


async def test_a_failed_lookup_only_skips_its_note() -> None:
    from tela_common.errors import ErrorCode, TelaError

    async def run(sql: str) -> dict[str, Any]:
        raise TelaError(ErrorCode.QUERY_FAILED, "no")

    assert await direct_sql.missing_values(run, prepared("SELECT name FROM projects WHERE status = 'x'")) == []
