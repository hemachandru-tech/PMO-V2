"""Query engine units: validator, compiler, scope injector, limits, cache keys (US-018 - US-021, US-026, US-027).

Acceptance: TEST-072 (CTE shadowing), TEST-073 (scope-keyed cache), TEST-074 - TEST-076 (scope cannot be
bypassed: property tests), TEST-081 - TEST-083 (compiler: fan-out, currency, groupings and bands).
"""

from __future__ import annotations

import os
from datetime import date
from typing import Any

import psycopg
import pytest
import sqlglot
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from tela_api.identity.scope import combine_policies
from tela_api.query.cache import cache_key
from tela_api.query.compiler import Params, SqlCompiler, resolve_range
from tela_api.query.limits import Limits, apply_limit
from tela_api.query.scope_injector import assert_scoped, inject
from tela_api.query.validator import SpecValidator
from tela_common.errors import ErrorCode, TelaError
from tela_query_spec.model import ModelSnapshot
from tela_query_spec.spec import parse_spec

AS_OF = date(2026, 9, 30)


def _f(key: str, dtype: str = "text", col: str | None = None, **kw: Any) -> dict[str, Any]:
    return {"key": key, "source_column": col or key, "label": key.title(), "data_type": dtype, **kw}


SNAP = ModelSnapshot.model_validate({
    "model_version_id": "mv1", "connection_id": "c1",
    "entities": [
        {"key": "projects", "label": "Projects", "source_object": "public.projects", "primary_key": ["project_id"],
         "fields": [_f("project_id"), _f("region"), _f("cio"), _f("status"), _f("budget", "decimal"),
                    _f("planned", "date"), _f("request_id")]},
        {"key": "requests", "label": "Requests", "source_object": "public.requests", "primary_key": ["request_id"],
         "fields": [_f("request_id"), _f("state"), _f("cio")]},
    ],
    "links": [{"from_entity": "projects", "to_entity": "requests", "from_fields": ["request_id"],
               "to_fields": ["request_id"], "cardinality": "many_to_one"}],
    "measures": [
        {"key": "n", "label": "N", "entity": "projects", "plain_rule": "x", "expression": {"agg": "count"}},
        {"key": "open_n", "label": "Open", "entity": "projects", "plain_rule": "x",
         "expression": {"agg": "count", "where": {"field": "status", "op": "=", "value": "Active"}}},
        {"key": "budget", "label": "Budget", "entity": "projects", "plain_rule": "x",
         "expression": {"agg": "sum", "field": "budget"}},
        {"key": "req_n", "label": "Requests", "entity": "requests", "plain_rule": "x", "expression": {"agg": "count"}},
    ],
    "dimensions": [
        {"key": "region", "label": "Region", "entity": "projects", "field": "region"},
        {"key": "state", "label": "State", "entity": "requests", "field": "state"},
        {"key": "planned", "label": "Planned", "entity": "projects", "field": "planned", "is_temporal": True},
    ],
})
POLICIES = [
    {"role_key": "cio", "entity_key": "projects", "all_rows": False,
     "predicate": [{"field": "cio", "op": "=", "attr": "cio"}], "description": "own"},
    {"role_key": "cio", "entity_key": "requests", "all_rows": False,
     "predicate": [{"field": "cio", "op": "=", "attr": "cio"}], "description": "own"},
]


def scope_for(cio: str = "Nomsa Osei", hidden: list[str] | None = None) -> Any:
    return combine_policies(connection_id="c1", policies=POLICIES,
                            field_rules=[{"model_field_key": f, "effect": "hide"} for f in hidden or []],
                            scope_attributes={"cio": {"cio": [cio]}})


def compile_sql(raw: dict[str, Any], scope: Any = None, snap: ModelSnapshot = SNAP) -> tuple[str, dict[str, Any]]:
    spec, _ = parse_spec({"model_version_id": snap.model_version_id, "connection_id": snap.connection_id, **raw})
    scope = scope or scope_for()
    vs = SpecValidator(snap, hidden_fields=scope.hidden_fields).validate(spec)
    cq = SqlCompiler().compile(vs, as_of=AS_OF)
    tree, _, _ = apply_limit(inject(cq.tree, snap, scope, cq.params), Limits(), spec.limit)
    sql = tree.sql(dialect="postgres")
    assert_scoped(sql, "postgres", snap, scope, cq.params.values)
    return sql, cq.params.values


# --------------------------------------------------------------------------- validator


def test_unknown_and_unreachable_references_are_refused() -> None:
    with pytest.raises(TelaError) as err:
        compile_sql({"measure": {"ref": "measure:nope"}})
    assert err.value.code == ErrorCode.SPEC_INVALID
    lonely = SNAP.model_copy(update={"links": []})
    with pytest.raises(TelaError):
        compile_sql({"measure": {"ref": "measure:n"}, "breakdowns": [{"ref": "dimension:state"}]}, snap=lonely)
    with pytest.raises(ValueError):
        parse_spec({"model_version_id": "mv1", "connection_id": "c1", "measure": {"ref": "measure:n"},
                    "breakdowns": [{"ref": "dimension:region) UNION SELECT"}]})


def test_unaccepted_links_are_not_join_paths() -> None:
    proposed = SNAP.model_copy(update={"links": [SNAP.links[0].model_copy(update={"accepted": False})]})
    with pytest.raises(TelaError):
        compile_sql({"measure": {"ref": "measure:n"}, "breakdowns": [{"ref": "dimension:state"}]}, snap=proposed)


def test_hidden_field_flags_instead_of_compiling() -> None:
    spec, _ = parse_spec({"model_version_id": "mv1", "connection_id": "c1", "measure": {"ref": "measure:budget"}})
    vs = SpecValidator(SNAP, hidden_fields={"projects.budget"}).validate(spec)
    assert vs.hidden and vs.hidden_refs == ["measure:budget"]


def test_enum_values_are_checked_against_scoped_values() -> None:
    spec, _ = parse_spec({"model_version_id": "mv1", "connection_id": "c1", "measure": {"ref": "measure:n"},
                          "filters": [{"ref": "dimension:region", "op": "=", "values": ["Atlantis"]}]})
    validator = SpecValidator(SNAP, enum_values=lambda f: ["KSA", "Jordan"])
    with pytest.raises(TelaError) as err:
        validator.validate(spec)
    assert "Atlantis" in err.value.message


# --------------------------------------------------------------------------- compiler shape and scope


def test_scope_wraps_every_governed_table_and_filters_stay_outside() -> None:
    sql, params = compile_sql({"measure": {"ref": "measure:open_n"}, "breakdowns": [{"ref": "dimension:state"}],
                               "filters": [{"ref": "dimension:region", "op": "in", "values": ["KSA"]}]})
    assert sql.count('FROM public.projects WHERE "cio" =') == 1
    assert sql.count('FROM public.requests WHERE "cio" =') == 1
    assert "Nomsa Osei" not in sql and "KSA" not in sql and "Active" not in sql  # values are bound
    assert sorted(v for v in params.values() if isinstance(v, str)) == ["Active", "KSA", "Nomsa Osei", "Nomsa Osei"]


def test_fan_out_uses_distinct_root_keys() -> None:
    sql, _ = compile_sql({"measure": {"ref": "measure:req_n"}, "breakdowns": [{"ref": "dimension:region"}]})
    assert sql.startswith("WITH tela_base AS (SELECT DISTINCT")
    plain, _ = compile_sql({"measure": {"ref": "measure:n"}, "breakdowns": [{"ref": "dimension:state"}]})
    assert "tela_base" not in plain  # many-to-one does not fan out


def test_assert_scoped_catches_tampering() -> None:
    scope = scope_for()
    sql, params = compile_sql({"measure": {"ref": "measure:n"}})
    for tampered in (
        sql.replace('WHERE "cio" = %(p0)s', 'WHERE "cio" = %(p0)s OR 1 = 1'),
        sql.replace('(SELECT "project_id", "region", "cio", "status", "budget", "planned", "request_id" FROM '
                    'public.projects WHERE "cio" = %(p0)s) AS t0', "public.projects AS t0"),
        "WITH projects AS (SELECT 1) " + sql,
        sql + " UNION ALL SELECT COUNT(*) FROM public.projects",
    ):
        with pytest.raises(TelaError) as err:
            assert_scoped(tampered, "postgres", SNAP, scope, params)
        assert err.value.code == ErrorCode.SCOPE_REWRITE_FAILED
    with pytest.raises(TelaError):
        assert_scoped(sql, "postgres", SNAP, scope, {**params, "p0": "Karim Haddad"})


def test_missing_scope_attribute_refuses() -> None:
    with pytest.raises(TelaError) as err:
        combine_policies(connection_id="c1", policies=POLICIES, field_rules=[], scope_attributes={})
    assert err.value.code == ErrorCode.SCOPE_UNRESOLVED


def test_hidden_columns_never_selected_in_scoped_tables() -> None:
    sql, _ = compile_sql({"measure": {"ref": "measure:n"}}, scope=scope_for(hidden=["projects.budget"]))
    assert '"budget"' not in sql


def test_limits_and_time_ranges() -> None:
    sql, _ = compile_sql({"measure": {"ref": "measure:n"}, "breakdowns": [{"ref": "dimension:region"}]})
    assert sql.endswith("LIMIT 10001")
    sql, _ = compile_sql({"measure": {"ref": "measure:n"}, "breakdowns": [{"ref": "dimension:region"}], "limit": 5})
    assert sql.endswith("LIMIT 5")
    tsql = sqlglot.transpile("SELECT a FROM t LIMIT 10001", read="postgres", write="tsql")[0]
    assert "TOP 10001" in tsql
    from tela_query_spec.spec import TimeRange

    assert resolve_range(TimeRange(relative="last_month"), AS_OF) == (date(2026, 8, 1), date(2026, 8, 31))
    assert resolve_range(TimeRange(relative="this_quarter"), AS_OF) == (date(2026, 7, 1), AS_OF)
    assert resolve_range(TimeRange(relative="last_30_days"), AS_OF) == (date(2026, 9, 1), AS_OF)
    assert resolve_range(TimeRange(relative="last_year"), AS_OF) == (date(2025, 1, 1), date(2025, 12, 31))
    assert resolve_range(TimeRange(**{"from": "2026-01-01", "to": "2026-02-01"}), AS_OF) == (
        date(2026, 1, 1), date(2026, 2, 1))


def test_cache_key_separates_scopes_and_versions() -> None:
    base = {"tenant_id": "t", "connection_id": "c", "model_version_id": "v1", "statement_hash": "s",
            "scope_hash": scope_for().scope_hash}
    k1 = cache_key(**base)
    assert k1 != cache_key(**{**base, "scope_hash": scope_for("Karim Haddad").scope_hash})
    assert k1 != cache_key(**{**base, "scope_hash": scope_for(hidden=["projects.budget"]).scope_hash})
    assert k1 != cache_key(**{**base, "model_version_id": "v2"})
    assert k1 != cache_key(**{**base, "as_of_bucket": "2026-10-01"})
    assert k1 == cache_key(**base)


# --------------------------------------------------------------------------- property tests (US-027)

refs = st.sampled_from(["dimension:region", "dimension:state", "dimension:planned", "field:projects.status",
                        "field:requests.cio"])
text_values = st.text(min_size=0, max_size=30)


@settings(max_examples=150, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(
    measure=st.sampled_from(["n", "open_n", "budget", "req_n"]),
    breakdowns=st.lists(st.sampled_from(["dimension:region", "dimension:state", "field:projects.status"]),
                        max_size=2, unique=True),
    filter_values=st.lists(text_values, min_size=1, max_size=4),
    cio=text_values.filter(lambda s: s != ""),
    limit=st.one_of(st.none(), st.integers(min_value=1, max_value=10_000)),
)
def test_property_every_compiled_query_is_scoped(measure: str, breakdowns: list[str], filter_values: list[str],
                                                 cio: str, limit: int | None) -> None:
    raw = {"measure": {"ref": f"measure:{measure}"}, "breakdowns": [{"ref": b} for b in breakdowns],
           "filters": [{"ref": "dimension:region", "op": "in", "values": filter_values}], "limit": limit}
    sql, params = compile_sql(raw, scope=scope_for(cio))
    tree = sqlglot.parse_one(sql, read="postgres")
    for table in tree.find_all(sqlglot.exp.Table):
        if table.name == "tela_base":
            continue
        select = table.parent.parent
        assert isinstance(select.parent, sqlglot.exp.Subquery) and select.args.get("where") is not None
    # Every user value is bound, never inlined.
    for v in [*filter_values, cio]:
        assert v in params.values()
    # One bound scope value per governed table (a filter value may equal it, hence >=).
    assert sum(1 for v in params.values() if v == cio) >= len([t for t in tree.find_all(sqlglot.exp.Table)
                                                                if t.name != "tela_base"])


@settings(max_examples=60, deadline=None)
@given(drop=st.integers(min_value=0, max_value=1))
def test_property_removing_any_scope_predicate_fails_closed(drop: int) -> None:
    sql, params = compile_sql({"measure": {"ref": "measure:n"}, "breakdowns": [{"ref": "dimension:state"}]})
    tree = sqlglot.parse_one(sql, read="postgres")
    scoped = list(tree.find_all(sqlglot.exp.Table))
    scoped[drop].parent.parent.set("where", None)
    with pytest.raises(TelaError):
        assert_scoped(tree.sql("postgres"), "postgres", SNAP, scope_for(), params)


# --------------------------------------------------------------------------- live semantics (TEST-081 - TEST-083)

OWNER = os.environ.get("TELA_TEST_OWNER_DATABASE_URL")
live = pytest.mark.skipif(not OWNER, reason="TELA_TEST_OWNER_DATABASE_URL not set")

FANOUT = ModelSnapshot.model_validate({
    "model_version_id": "mvf", "connection_id": "cf",
    "entities": [
        {"key": "txn", "label": "Transactions", "source_object": "fanout.txn", "primary_key": ["id"],
         "fields": [_f("id", "integer"), _f("amount", "decimal"), _f("currency"), _f("branch")]},
        {"key": "fee", "label": "Fees", "source_object": "fanout.fee", "primary_key": ["id"],
         "fields": [_f("id", "integer"), _f("txn_id", "integer"), _f("kind"), _f("fee", "decimal")]},
        {"key": "val", "label": "Values", "source_object": "fanout.val", "primary_key": ["id"],
         "fields": [_f("id", "integer"), _f("v"), _f("x", "decimal")]},
    ],
    "links": [{"from_entity": "txn", "to_entity": "fee", "from_fields": ["id"], "to_fields": ["txn_id"],
               "cardinality": "one_to_many"}],
    "measures": [
        {"key": "gross", "label": "Gross", "entity": "txn", "plain_rule": "x", "currency_field": "currency",
         "expression": {"agg": "sum", "field": "amount"}},
        {"key": "txns", "label": "Txns", "entity": "txn", "plain_rule": "x", "expression": {"agg": "count"}},
        {"key": "fees", "label": "Fees", "entity": "fee", "plain_rule": "x", "expression": {"agg": "sum", "field": "fee"}},
        {"key": "vals", "label": "Values", "entity": "val", "plain_rule": "x", "expression": {"agg": "count"}},
    ],
    "dimensions": [{"key": "kind", "label": "Fee kind", "entity": "fee", "field": "kind"},
                   {"key": "branch", "label": "Branch", "entity": "txn", "field": "branch"}],
})
ALL = combine_policies(connection_id="cf", policies=[
    {"role_key": "admin", "entity_key": e, "all_rows": True, "predicate": [], "description": "all"}
    for e in ("txn", "fee", "val")], field_rules=[], scope_attributes={})


@pytest.fixture(scope="module")
def fanout_db(tpg_source: dict[str, Any]) -> Any:
    from conftest import _dsn_for

    conn = psycopg.connect(_dsn_for(tpg_source["database"]), autocommit=True)
    conn.execute("DROP SCHEMA IF EXISTS fanout CASCADE")
    conn.execute("CREATE SCHEMA fanout")
    conn.execute("CREATE TABLE fanout.txn (id int PRIMARY KEY, amount numeric, currency text, branch text)")
    conn.execute("CREATE TABLE fanout.fee (id int PRIMARY KEY, txn_id int, kind text, fee numeric)")
    conn.execute("CREATE TABLE fanout.val (id int PRIMARY KEY, v text, x numeric)")
    # S27 RULE-007 example: one AED 1,000 transaction with three fee rows (42 in total).
    conn.execute("INSERT INTO fanout.txn VALUES (1, 1000, 'AED', 'Dubai'), (2, 10000, 'JOD', 'Amman'), "
                 "(3, 50000, 'AED', 'Dubai'), (4, 2000, 'USD', 'Dubai')")
    conn.execute("INSERT INTO fanout.fee VALUES (1, 1, 'card', 20), (2, 1, 'fx', 12), (3, 1, 'card', 10)")
    conn.execute("INSERT INTO fanout.val VALUES (1, 'A', 0), (2, 'B', 999999.99), (3, 'C', 1000000), "
                 "(4, 'D', 5000000), (5, NULL, NULL), (6, ' ', 7000000), (7, 'b', -1)")
    yield conn
    conn.close()


def _run_live(conn: Any, raw: dict[str, Any], snap: ModelSnapshot = FANOUT) -> list[tuple[Any, ...]]:
    spec, _ = parse_spec({"model_version_id": snap.model_version_id, "connection_id": snap.connection_id, **raw})
    vs = SpecValidator(snap).validate(spec)
    cq = SqlCompiler().compile(vs, as_of=AS_OF)
    tree, _, _ = apply_limit(inject(cq.tree, snap, ALL, cq.params), Limits(), spec.limit)
    sql = tree.sql(dialect="postgres")
    assert_scoped(sql, "postgres", snap, ALL, cq.params.values)
    return [tuple(r) for r in conn.execute(sql, cq.params.values).fetchall()]


@live
def test_fan_out_sum_is_not_multiplied(fanout_db: Any) -> None:
    rows = _run_live(fanout_db, {"measure": {"ref": "measure:gross"}, "breakdowns": [{"ref": "dimension:kind"}],
                                 "filters": [{"ref": "field:txn.currency", "op": "=", "values": ["AED"]},
                                             {"ref": "field:txn.id", "op": "=", "values": [1]}]})
    # Naive SUM over the join would say 3,000 for the one transaction.
    assert sorted(rows) == [("card", 1000), ("fx", 1000)]
    total = _run_live(fanout_db, {"measure": {"ref": "measure:txns"}, "breakdowns": [{"ref": "dimension:kind"}]})
    assert dict(total) == {"card": 1, "fx": 1, None: 3}
    fees = _run_live(fanout_db, {"measure": {"ref": "measure:fees"}})
    assert fees == [(42,)]


@live
def test_currency_is_never_summed_across(fanout_db: Any) -> None:
    rows = _run_live(fanout_db, {"measure": {"ref": "measure:gross"}})
    assert sorted((r[0], int(r[1])) for r in rows) == [("AED", 51000), ("JOD", 10000), ("USD", 2000)]
    single = _run_live(fanout_db, {"measure": {"ref": "measure:gross"},
                                   "filters": [{"ref": "field:txn.currency", "op": "=", "values": ["AED"]}]})
    assert single == [(51000,)]


@live
def test_grouping_catch_all_and_not_set(fanout_db: Any) -> None:
    rows = _run_live(fanout_db, {
        "measure": {"ref": "measure:vals"}, "breakdowns": [{"ref": "personal:core_growth"}],
        "personal_fields": [{"key": "core_growth", "kind": "grouping", "source": "field:val.v",
                             "groups": [{"label": "Core", "values": ["A", "B"]}, {"label": "Growth", "values": ["C"]}]}]})
    # RULE-012: A, B (and b, case-insensitive) → Core; C → Growth; D → Everything else; NULL and blank → Not set.
    assert dict(rows) == {"Core": 3, "Growth": 1, "Everything else": 1, "Not set": 2}


@live
def test_band_edges(fanout_db: Any) -> None:
    rows = _run_live(fanout_db, {
        "measure": {"ref": "measure:vals"}, "breakdowns": [{"ref": "personal:size"}],
        "personal_fields": [{"key": "size", "kind": "band", "source": "field:val.x", "bands": [
            {"label": "0-1M", "lower": 0, "upper": 1_000_000}, {"label": "1M-5M", "lower": 1_000_000, "upper": 5_000_000},
            {"label": "5M+", "lower": 5_000_000}]}]})
    # RULE-013: exactly 1M goes into 1M-5M; 5M opens the last band; below the first edge is named.
    assert dict(rows) == {"0-1M": 2, "1M-5M": 1, "5M+": 2, "Not set": 1, "Below 0": 1}
    with pytest.raises(ValueError, match="do not meet"):
        parse_spec({"model_version_id": "mvf", "connection_id": "cf", "measure": {"ref": "measure:vals"},
                    "personal_fields": [{"key": "s", "kind": "band", "source": "field:val.x", "bands": [
                        {"label": "a", "lower": 0, "upper": 10}, {"label": "b", "lower": 11, "upper": 20}]}]})
    with pytest.raises(ValueError, match="two groups"):
        parse_spec({"model_version_id": "mvf", "connection_id": "cf", "measure": {"ref": "measure:vals"},
                    "personal_fields": [{"key": "g", "kind": "grouping", "source": "field:val.v", "groups": [
                        {"label": "x", "values": ["A"]}, {"label": "y", "values": ["a"]}]}]})


def test_params_are_named_and_unique() -> None:
    p = Params()
    assert [p.add(1).name, p.add("x").name] == ["p0", "p1"] and p.values == {"p0": 1, "p1": "x"}
