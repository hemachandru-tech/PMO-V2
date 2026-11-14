"""Date fields over date-time columns (SQL Server and Dataverse keep date-only columns as date-times).

Approval accepts a ``date`` field on a ``datetime`` column and stamps the column's type on the field
(``source_type``); queries then read the column as its day: the SQL compiler casts it to a date (run here on
DuckDB with rows at several times of day) and the Dataverse Web API turns a day into the range [day, day + 1).
Fields without a stamped source type (versions approved before) compile as before.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any

import duckdb
import pytest
from test_dialect_parity import AS_OF, dataverse_snapshot, scope

from tela_api.model.catalog import catalog_issues, stamp_source_types
from tela_api.query.compiler import SqlCompiler
from tela_api.query.compilers import compile_spec
from tela_api.query.dataverse import And, Cmp, Or, day_cmp
from tela_api.query.validator import SpecValidator
from tela_query_spec.model import ModelSnapshot
from tela_query_spec.spec import parse_spec

CATALOG = {"dbo.tpg_project": [{"name": "id", "type": "int"}, {"name": "planned", "type": "datetime2"},
                               {"name": "closed", "type": "bit"}]}


def snap(source_type: str | None = "datetime") -> ModelSnapshot:
    return ModelSnapshot.model_validate({
        "model_version_id": "v1", "connection_id": "c1",
        "entities": [{"key": "projects", "label": "Projects", "source_object": "dbo.tpg_project",
                      "primary_key": ["id"],
                      "fields": [{"key": "id", "label": "Id", "data_type": "integer", "source_column": "id"},
                                 {"key": "planned", "label": "Planned", "data_type": "date",
                                  "source_column": "planned", "source_type": source_type}]}],
        "measures": [{"key": "n", "label": "N", "entity": "projects", "plain_rule": "All.",
                      "expression": {"agg": "count"}},
                     {"key": "late", "label": "Late", "entity": "projects", "plain_rule": "After 29 Oct.",
                      "expression": {"agg": "count", "where": {"field": "planned", "op": ">", "value": "2026-10-29"}}}],
        "dimensions": [{"key": "planned", "label": "Planned", "entity": "projects", "field": "planned",
                        "is_temporal": True}],
    })


@pytest.fixture(scope="module")
def db() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    con.execute("CREATE SCHEMA dbo; CREATE TABLE dbo.tpg_project (id INTEGER, planned TIMESTAMP)")
    con.executemany("INSERT INTO dbo.tpg_project VALUES (?, ?)", [
        (1, datetime(2026, 10, 28, 23, 0)), (2, datetime(2026, 10, 29, 0, 0)), (3, datetime(2026, 10, 29, 14, 30)),
        (4, datetime(2026, 10, 30, 9, 0)), (5, None)])
    return con


def run(db: duckdb.DuckDBPyConnection, s: ModelSnapshot, body: dict[str, Any]) -> list[tuple[Any, ...]]:
    spec, _ = parse_spec({"model_version_id": "v1", "connection_id": "c1", **body})
    compiled = SqlCompiler("duckdb").compile(SpecValidator(s).validate(spec), as_of=AS_OF)
    return sorted(db.execute(compiled.tree.sql(dialect="duckdb"), compiled.params.values).fetchall(),
                  key=lambda r: tuple((x is None, x) for x in r))


def count(db: duckdb.DuckDBPyConnection, s: ModelSnapshot, op: str, values: list[str]) -> int:
    rows = run(db, s, {"measure": {"ref": "measure:n"},
                       "filters": [{"ref": "dimension:planned", "op": op, "values": values}]})
    return int(rows[0][0])


def test_approval_accepts_and_stamps_a_date_field_over_a_datetime_column() -> None:
    s = snap(None)
    assert catalog_issues(s, CATALOG) == []
    stamp_source_types(s, CATALOG)
    planned = s.entity("projects").field("planned")  # type: ignore[union-attr]
    assert planned is not None and planned.source_type == "datetime" and planned.reads_day
    # a bit column is still no date
    wrong = snap(None)
    wrong.entity("projects").field("planned").source_column = "closed"  # type: ignore[union-attr]
    assert catalog_issues(wrong, CATALOG) == [
        {"ref": "field:projects.planned", "issue": "declared date but the source column is boolean"}]


def test_sql_filters_match_every_time_of_the_day(db: duckdb.DuckDBPyConnection) -> None:
    s = snap()
    assert count(db, s, "=", ["2026-10-29"]) == 2  # midnight and 14:30
    assert count(db, s, "!=", ["2026-10-29"]) == 2  # never NULL
    assert count(db, s, "in", ["2026-10-28", "2026-10-30"]) == 2
    assert count(db, s, "<=", ["2026-10-29"]) == 3
    assert count(db, s, ">", ["2026-10-29"]) == 1
    assert count(db, s, "between", ["2026-10-29", "2026-10-29"]) == 2
    rows = run(db, s, {"measure": {"ref": "measure:late"}})
    assert rows == [(1,)]


def test_sql_groups_by_day(db: duckdb.DuckDBPyConnection) -> None:
    rows = run(db, snap(), {"measure": {"ref": "measure:n"}, "breakdowns": [{"ref": "dimension:planned"}]})
    assert rows == [(date(2026, 10, 28), 1), (date(2026, 10, 29), 2), (date(2026, 10, 30), 1), (None, 1)]


def test_unstamped_fields_compile_as_before(db: duckdb.DuckDBPyConnection) -> None:
    spec, _ = parse_spec({"model_version_id": "v1", "connection_id": "c1", "measure": {"ref": "measure:n"},
                          "filters": [{"ref": "dimension:planned", "op": "=", "values": ["2026-10-29"]}]})
    sql = SqlCompiler("duckdb").compile(SpecValidator(snap(None)).validate(spec), as_of=AS_OF).tree.sql("duckdb")
    assert "CAST" not in sql.upper()
    assert count(db, snap(None), "=", ["2026-10-29"]) == 1  # only the midnight row: why the cast is needed


def test_webapi_day_is_a_half_open_range() -> None:
    d, nxt = date(2026, 10, 29), date(2026, 10, 30)
    assert day_cmp("c", "eq", "2026-10-29") == And((Cmp("c", "ge", d), Cmp("c", "lt", nxt)))
    assert day_cmp("c", "ne", "2026-10-29") == Or((Cmp("c", "lt", d), Cmp("c", "ge", nxt)))
    assert day_cmp("c", "le", "2026-10-29") == Cmp("c", "lt", nxt)
    assert day_cmp("c", "gt", "2026-10-29") == Cmp("c", "ge", nxt)
    assert day_cmp("c", "ge", "2026-10-29") == Cmp("c", "ge", d)
    assert day_cmp("c", "lt", "2026-10-29") == Cmp("c", "lt", d)


def test_webapi_filter_on_a_stamped_pmo_date() -> None:
    dv = dataverse_snapshot()
    planned = dv.entity("projects").field("planned_deployment_date")  # type: ignore[union-attr]
    assert planned is not None
    planned.source_type = "datetime"
    spec, _ = parse_spec({"model_version_id": "v", "connection_id": "c", "measure": {"ref": "measure:total_projects"},
                          "filters": [{"ref": "dimension:planned_deployment_date", "op": "=",
                                       "values": ["2026-10-29"]}]})
    conn = {"engine": "dataverse", "descriptor": {"mode": "webapi", "webapi_format": "odata"}, "_columns": {}}
    compiled = compile_spec(conn, SpecValidator(dv).validate(spec, connection_id="c"), as_of=AS_OF)
    apply = json.loads(compiled.render(dv, scope(dv), 10))["query"]["$apply"]
    col = planned.source_column
    assert f"{col} ge 2026-10-29" in apply and f"{col} lt 2026-10-30" in apply


def test_approval_names_a_table_the_source_account_may_not_read() -> None:
    issues = catalog_issues(snap(), CATALOG, {"dbo.tpg_project"})
    assert issues == [{"ref": "entity:projects", "issue": "the source account may not read dbo.tpg_project: give its "
                       "role Read on this table, or remove Projects from the model"}]
