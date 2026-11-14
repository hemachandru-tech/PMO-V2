"""PMO catalogue measures and dimensions (US-156; FEAT-059), no database server.

The pack is bound to the synthetic extract, every measure is compiled for DuckDB and run on the extract's rows
in memory, and the result is compared with the independent oracle (``pmo_oracle.CATALOGUE``; calculated health
from the pack's Python calculator). The SQL shapes of the other dialects are checked without running them.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

import duckdb
import pmo_oracle as oracle
import pytest
import tpg_synthetic

from tela_api.packs.manifest import PackManifest, bind, discover, load_manifest, match
from tela_api.query.compiler import RecordCompiler, SqlCompiler, require_basic
from tela_api.query.validator import SpecValidator
from tela_common.errors import TelaError
from tela_query_spec.model import Dimension, ModelSnapshot
from tela_query_spec.spec import parse_spec

AS_OF: date = tpg_synthetic.AS_OF
TYPES = {"varchar": "text", "numeric": "numeric", "double": "double precision", "integer": "integer",
         "date": "date", "timestamp": "timestamp"}


def _catalog() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for table, cols, _ in tpg_synthetic.tables():
        body = tpg_synthetic.DDL.split(f"CREATE TABLE {table} (")[1].split(");")[0]
        types = {line.split()[0]: line.split()[1].split("(")[0] for line in body.strip().splitlines()
                 if line.split() and line.split()[0] in cols}
        out.append({"qualified_name": f"main.{table}",
                    "columns": [{"name": c, "type": TYPES.get(types[c], types[c])} for c in cols]})
    return out


@pytest.fixture(scope="module")
def manifest() -> PackManifest:
    return load_manifest(next(p for p in discover() if p.parent.name == "pmo"))[0]


@pytest.fixture(scope="module")
def snapshot(manifest: PackManifest) -> ModelSnapshot:
    result = match(manifest, _catalog())
    assert result.matched
    binding = bind(manifest, result, reporting_date=AS_OF.isoformat())
    gaps = {i["key"]: i["gaps"] for i in binding.items if i["availability"] == "unavailable"
            and i["kind"] in ("measure", "dimension")}
    assert gaps == {}, gaps
    return ModelSnapshot.model_validate({"model_version_id": "v1", "connection_id": "c1", "engine": "excel",
                                         **binding.definition})


@pytest.fixture(scope="module")
def db() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    con.execute("\n".join(line for line in tpg_synthetic.DDL.splitlines() if not line.startswith("DROP TABLE")))
    for table, cols, rows in tpg_synthetic.tables():
        con.executemany(f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                        [tuple(r[c] for c in cols) for r in rows])
    return con


def _spec(snap: ModelSnapshot, measure: str, **kw: Any) -> Any:
    return parse_spec({"model_version_id": snap.model_version_id, "connection_id": snap.connection_id,
                       "measure": {"ref": f"measure:{measure}"}, **kw})[0]


def _run(db: duckdb.DuckDBPyConnection, snap: ModelSnapshot, measure: str, **kw: Any) -> list[tuple[Any, ...]]:
    vs = SpecValidator(snap).validate(_spec(snap, measure, **kw))
    compiled = SqlCompiler("duckdb").compile(vs, as_of=AS_OF)
    return db.execute(compiled.sql(), compiled.params.values).fetchall()


def _close(got: Any, want: Any) -> bool:
    if want is None or got is None:
        return got is None and want is None
    g, w = float(got), float(want)
    return abs(g - w) <= 1e-6 * max(1.0, abs(w))


@pytest.mark.parametrize("measure", sorted(oracle.CATALOGUE))
def test_measure_matches_the_oracle(db: duckdb.DuckDBPyConnection, snapshot: ModelSnapshot, measure: str) -> None:
    rows = _run(db, snapshot, measure)
    assert _close(rows[0][-1], oracle.CATALOGUE[measure]), (measure, rows[0][-1], oracle.CATALOGUE[measure])


def test_every_new_measure_has_a_definition_and_an_oracle(manifest: PackManifest) -> None:
    assert all(m.get("definition") for m in manifest.measures)
    old = {"total_projects", "open_projects", "inactive_projects", "in_delivery", "on_hold", "project_created",
           "overdue_go_lives", "schedule_risk", "rag_contradictions", "stale_projects", "go_lives_0_30",
           "go_lives_31_60", "go_lives_61_90", "over_budget", "approved_budget_total", "forecast_cost_total",
           "budget_variance", "budget_variance_pct", "projects_excluded", "open_demand", "overdue_dou",
           "overdue_darb", "overdue_sow", "total_requests"}
    assert {m["key"] for m in manifest.measures} - old == set(oracle.CATALOGUE)


def test_calculated_health_matches_python_per_project(db: duckdb.DuckDBPyConnection, snapshot: ModelSnapshot) -> None:
    rows = dict(_run(db, snapshot, "open_projects", breakdowns=[{"ref": "dimension:calculated_health"}]))
    want: dict[str, int] = {}
    for p in oracle.PROJECTS:
        if p["status"] == "Active":
            s = oracle.health(p).status
            want[s] = want.get(s, 0) + 1
    assert rows == want


def _band(days: int | None, bands: list[tuple[str, float | None, float | None]], not_set: str, other: str) -> str:
    if days is None:
        return not_set
    for label, above, upto in bands:
        if (above is None or days > above) and (upto is None or days <= upto):
            return label
    return other


def _want(dim: Dimension, rows: list[dict[str, Any]], days: Any, keep: Any) -> dict[str, int]:
    bands = [(b.label, b.above, b.upto) for b in dim.bands or []]
    out: dict[str, int] = {}
    for r in rows:
        if keep(r):
            k = _band(days(r), bands, dim.not_set or "", dim.other)
            out[k] = out.get(k, 0) + 1
    return out


def test_banded_dimensions_use_the_reporting_date(db: duckdb.DuckDBPyConnection, snapshot: ModelSnapshot) -> None:
    P, R = oracle.PROJECTS, oracle.REQUESTS
    cases = [
        ("on_hold", "on_hold_aging", P, lambda p: (AS_OF - p["on_hold_date"]).days if p["on_hold_date"] else None,
         lambda p: p["status"] == "Active" and p["current_state"] == "On Hold"),
        ("in_delivery", "lateness_bucket", P, lambda p: (AS_OF - p["planned_deployment_date"]).days,
         lambda p: p["current_state"] == "Active"),
        ("open_projects", "go_live_horizon", P, lambda p: (p["planned_deployment_date"] - AS_OF).days,
         lambda p: p["status"] == "Active"),
        ("total_requests", "request_on_hold_aging", R,
         lambda r: (AS_OF - r["on_hold_date"]).days if r["on_hold_date"] else None, lambda r: True),
    ]
    for measure, dim_key, rows, days, keep in cases:
        dim = snapshot.dimension(dim_key)
        assert dim is not None
        got = {k: v for k, v in _run(db, snapshot, measure, breakdowns=[{"ref": f"dimension:{dim_key}"}]) if v}
        assert got == {k: v for k, v in _want(dim, rows, days, keep).items() if v}, dim_key
    # Boundaries: on hold 90 days is in the 61-90 band, 91 days "More than 90 days"; request 180 vs 181 days.
    hold = dict(_run(db, snapshot, "on_hold", breakdowns=[{"ref": "dimension:on_hold_aging"}]))
    assert hold["More than 90 days"] == 1 and hold["61\u201390 days"] == 1
    req = dict(_run(db, snapshot, "total_requests", breakdowns=[{"ref": "dimension:request_on_hold_aging"}]))
    assert req["More than 180 days"] == 2 and req["91\u2013180 days"] >= 1
    # Go-live horizon "Next 30 days" holds day 0 to day 30, as the certified go-lives measure (undeployed only).
    upcoming = _run(db, snapshot, "open_projects", filters=[
        {"ref": "dimension:go_live_horizon", "op": "=", "values": ["Next 30 days"]}])
    assert upcoming[0][0] == sum(1 for p in P if p["status"] == "Active"
                                 and 0 <= (p["planned_deployment_date"] - AS_OF).days <= 30)


def test_banded_filter_and_records(db: duckdb.DuckDBPyConnection, snapshot: ModelSnapshot) -> None:
    rows = _run(db, snapshot, "in_delivery", filters=[
        {"ref": "dimension:lateness_bucket", "op": "in", "values": ["More than 90 days"]}])
    assert rows[0][0] == 1
    with pytest.raises(TelaError):
        _run(db, snapshot, "in_delivery", filters=[
            {"ref": "dimension:lateness_bucket", "op": "=", "values": ["Very late"]}])
    vs = SpecValidator(snapshot).validate(_spec(snapshot, "in_delivery",
                                                breakdowns=[{"ref": "dimension:lateness_bucket"}]))
    rec = RecordCompiler("duckdb").compile_records(vs, marks={"lateness_bucket": "More than 90 days"}, as_of=AS_OF,
                                                   fields=["project_id"], limit=50, offset=0)
    assert len(db.execute(rec.sql(), rec.params.values).fetchall()) == 1


def test_not_set_is_a_label_and_a_filter(db: duckdb.DuckDBPyConnection, snapshot: ModelSnapshot) -> None:
    got = dict(_run(db, snapshot, "open_projects", breakdowns=[{"ref": "dimension:reported_status"}]))
    assert got["Not set"] == 1 and set(got) == {"Green", "Amber", "Red", "Not set"}
    only = _run(db, snapshot, "open_projects", filters=[
        {"ref": "dimension:reported_status", "op": "in", "values": ["Not set", "Red"]}])
    assert only[0][0] == got["Not set"] + got["Red"]
    without = _run(db, snapshot, "open_projects", filters=[
        {"ref": "dimension:reported_status", "op": "!=", "values": ["Not set"]}])
    assert without[0][0] == sum(got.values()) - 1


def test_variance_spread_matches_the_over_budget_measure(db: duckdb.DuckDBPyConnection,
                                                         snapshot: ModelSnapshot) -> None:
    spread = dict(_run(db, snapshot, "total_projects", breakdowns=[{"ref": "dimension:variance_spread"}]))
    over15 = sum(1 for p in oracle.PROJECTS if (p["approved_budget_value_usd"] or 0) > 0
                 and p["cost_total_usd"] is not None
                 and (p["cost_total_usd"] - p["approved_budget_value_usd"]) / p["approved_budget_value_usd"] * 100
                 > Decimal(15))
    assert spread["Over by 15 %+"] == over15 == oracle.CATALOGUE["forecast_over_budget_15"]


def test_other_dialects(snapshot: ModelSnapshot) -> None:
    vs = SpecValidator(snapshot).validate(_spec(snapshot, "slip_vs_baseline_median"))
    pg = SqlCompiler("postgres").compile(vs, as_of=AS_OF).sql()
    assert "PERCENTILE_CONT(0.5) WITHIN GROUP" in pg
    for dialect in ("mysql", "tsql"):
        with pytest.raises(TelaError) as err:
            SqlCompiler(dialect).compile(vs, as_of=AS_OF)
        assert err.value.code.value == "measure_unavailable"
    vs = SpecValidator(snapshot).validate(_spec(snapshot, "deployed_over_60_late",
                                                breakdowns=[{"ref": "dimension:on_hold_aging"}]))
    assert "DATEDIFF(DAY" in SqlCompiler("tsql").compile(vs, as_of=AS_OF).sql()
    assert "DATEDIFF(" in SqlCompiler("mysql").compile(vs, as_of=AS_OF).sql()
    assert "AS DATE) - CAST(" in SqlCompiler("postgres").compile(vs, as_of=AS_OF).sql()
    with pytest.raises(TelaError):
        require_basic(vs, "mongodb")
    basic = SpecValidator(snapshot).validate(_spec(snapshot, "open_projects"))
    require_basic(basic, "mongodb")


def test_linked_fields_join_the_other_entity(snapshot: ModelSnapshot) -> None:
    vs = SpecValidator(snapshot).validate(_spec(snapshot, "lead_time_median"))
    assert vs.entities == {"projects", "project_requests"}
    hidden = SpecValidator(snapshot, hidden_fields={"project_requests.created_on"}).validate(
        _spec(snapshot, "lead_time_median"))
    assert hidden.hidden and hidden.hidden_refs == ["measure:lead_time_median"]
