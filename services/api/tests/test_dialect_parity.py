"""Dialect parity of the query engine (US-113, US-114, US-109, US-117; TEST-078).

Every PMO pack measure — alone, by region, by month over a relative range, with filters, a personal
grouping and band, through the fan-out link, as a record list and as filter values — is compiled for
each SQL dialect, scope-injected, re-checked by ``assert_scoped`` and by the runner's own statement
guard, then run on that engine through its adapter against the same synthetic extract. Ad-hoc rules (US-163:
``today``, nested ``any`` inside ``all``, field-to-field, literal dates) go through the same path. Results must
equal PostgreSQL's. Engines without a container URL are skipped; PostgreSQL is the reference.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "connector-runner" / "tests"))
import tpg_synthetic
from sources import (
    RO_PASSWORD,
    MemoryStore,
    file_info,
    seed_mongo,
    seed_mysql,
    seed_postgres,
    seed_sqlserver,
    tpg_workbook,
    url,
)

from tela_api.identity.scope import EntityScope, ResolvedCondition, ResolvedScope
from tela_api.packs.manifest import DEFAULT_PACKS_DIR, bind, load_manifest, match
from tela_api.query.asof import pin_dates
from tela_api.query.compilers import compile_spec, dialect_for, field_query, records_compiler
from tela_api.query.dataverse import WebApiCompiled, assert_webapi_scoped
from tela_api.query.duckdb_post import apply_post
from tela_api.query.limits import Limits as ApiLimits
from tela_api.query.limits import apply_limit
from tela_api.query.mongo import MongoCompiled, assert_mongo_scoped
from tela_api.query.scope_injector import assert_scoped, inject
from tela_api.query.validator import FieldRef, SpecValidator
from tela_common.errors import ErrorCode, TelaError
from tela_query_spec.expr import AggExpr
from tela_query_spec.model import ModelSnapshot
from tela_query_spec.spec import parse_spec
from tela_runner.adapters.base import ConnectionInfo, Limits
from tela_runner.mongo_guard import check_pipeline
from tela_runner.sql_guard import check_statement
from tela_runner.webapi_guard import check_webapi

pytestmark = pytest.mark.integration
AS_OF = tpg_synthetic.AS_OF
TODAY = AS_OF + timedelta(days=12)  # today differs from the configured reporting date


def snapshot(schema: str) -> ModelSnapshot:
    manifest, _, _ = load_manifest(DEFAULT_PACKS_DIR / "pmo" / "manifest.yaml")
    projects, requests = tpg_synthetic.generate()
    catalog = [{"qualified_name": f"{schema}.{name}", "columns": [{"name": k, "data_type": "text"} for k in rows[0]]}
               for name, rows in (("projects", projects), ("project_requests", requests))]
    binding = bind(manifest, match(manifest, catalog), reporting_date=AS_OF.isoformat())
    return ModelSnapshot.model_validate({"model_version_id": "v", "connection_id": "c", **binding.definition})


def scope(snap: ModelSnapshot, cio: str | None = None) -> ResolvedScope:
    entities = {e.key: EntityScope(all_rows=True) for e in snap.entities}
    if cio:
        entities["projects"] = EntityScope(alternatives=[(ResolvedCondition("cio", "in", (cio, "Karim Haddad")),)])
    return ResolvedScope("c", entities, frozenset(), frozenset())


def _info(engine: str, admin: dict[str, Any], schema: str) -> ConnectionInfo:
    if engine == "mongodb":
        return ConnectionInfo("parity-mongodb", engine, {
            "uri": f"mongodb://{admin['host']}:{admin['port']}/?authSource=tpg", "database": "tpg",
            "tls": {"mode": "disable"}, "read_preference": "primary"}, {"username": "tela_ro", "password": RO_PASSWORD})
    tls = {"trust_server_certificate": True} if engine == "sqlserver" else {"mode": "disable"}
    return ConnectionInfo(f"parity-{engine}", engine, {"host": admin["host"], "port": admin["port"],
                                                       "database": "tpg", "tls": tls, "approved_schemas": [schema]},
                          {"username": "tela_ro", "password": RO_PASSWORD})


ENGINES: dict[str, tuple[str, str, Callable[[dict[str, Any]], None], str]] = {
    # engine: (url key, schema, seed, adapter import path)
    "postgres": ("postgres", "public", seed_postgres, "tela_runner.adapters.postgres:PostgresAdapter"),
    "mysql": ("mysql", "tpg", seed_mysql, "tela_runner.adapters.mysql:MySqlAdapter"),
    "sqlserver": ("mssql", "dbo", seed_sqlserver, "tela_runner.adapters.sqlserver:SqlServerAdapter"),
    "mongodb": ("mongo", "tpg", seed_mongo, "tela_runner.adapters.mongodb:MongoAdapter"),
}


class Source:
    def __init__(self, engine: str) -> None:
        if engine == "excel":  # an uploaded workbook through DuckDB; no container needed
            from tela_runner.adapters.files import FileAdapter

            store = MemoryStore()
            self.adapter, self.engine, self.dialect, self.schema = FileAdapter(store=store), engine, "duckdb", "main"
            self.info = file_info(store, tpg_workbook())
            self.snap = snapshot(self.schema)
            self.allowed = {e.source_object.lower() for e in self.snap.entities}
            return
        key, self.schema, seed, path = ENGINES[engine]
        admin = url(key)
        if admin is None:
            pytest.skip(f"TELA_TEST_{key.upper()}_URL not set")
        seed(admin)
        module, cls = path.split(":")
        self.adapter = getattr(__import__(module, fromlist=[cls]), cls)(allow_insecure_tls=True)
        self.engine, self.dialect = engine, dialect_for(engine)
        self.info = _info(engine, admin, self.schema)
        self.snap = snapshot(self.schema)
        self.allowed = {e.source_object.lower() for e in self.snap.entities}

    async def run(self, compiled: Any, sc: ResolvedScope, *, limit: int | None = None) -> list[tuple[Any, ...]]:
        if isinstance(compiled, MongoCompiled):
            statement = compiled.render(self.snap, sc, limit or 10_001)
            assert_mongo_scoped(statement, self.snap, sc)
            check_pipeline(statement, self.allowed)
            result = await self.adapter.execute(self.info, statement, {}, Limits(), "parity")
            return [tuple(_norm(v) for v in row) for row in _post(compiled, result.rows)]
        tree = inject(compiled.tree, self.snap, sc, compiled.params)
        tree, _, _ = apply_limit(tree, ApiLimits(), limit)
        sql = tree.sql(dialect=self.dialect)
        assert_scoped(sql, self.dialect, self.snap, sc, compiled.params.values)
        check_statement(sql, self.dialect, self.allowed)
        result = await self.adapter.execute(self.info, sql, compiled.params.values, Limits(), "parity")
        return [tuple(_norm(v) for v in row) for row in result.rows]


def _post(compiled: Any, rows: list[Any]) -> list[Any]:
    """Window operations on MongoDB and the Dataverse Web API: the DuckDB post-step, as QueryService runs it."""
    if compiled.duckdb_post is None or not rows:
        return rows
    sql, extra = compiled.duckdb_post
    return apply_post([list(r) for r in rows], compiled.columns, sql, extra)[0]


def _norm(v: Any) -> Any:
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, int | float | Decimal):
        return round(float(v), 4)
    if isinstance(v, str) and len(v) >= 10 and v[4] == "-" and v[7] == "-":
        return v[:10]  # dates and datetimes compare on the calendar date
    return v


def cases(snap: ModelSnapshot) -> list[tuple[str, dict[str, Any], bool]]:
    """(label, spec body, ordered?)."""
    out: list[tuple[str, dict[str, Any], bool]] = []
    for m in snap.measures:
        ref = {"ref": f"measure:{m.key}"}
        out.append((f"{m.key}", {"measure": ref}, True))
        if m.entity == "projects":
            out.append((f"{m.key} by region", {"measure": ref, "breakdowns": [{"ref": "dimension:region"}],
                                               "sort": [{"by": "dimension:region", "dir": "asc"}]}, True))
            out.append((f"{m.key} by month", {"measure": ref, "time": {
                "ref": "dimension:planned_deployment_date", "grain": "month",
                "range": {"relative": "next_90_days"}}}, True))
    projects = {"ref": "measure:" + next(m.key for m in snap.measures if m.entity == "projects")}
    out += [
        ("filters", {"measure": projects, "breakdowns": [{"ref": "dimension:priority"}],
                     "filters": [{"ref": "dimension:region", "op": "in", "values": ["KSA", "Jordan"]},
                                 {"ref": "dimension:priority", "op": "!=", "values": ["Low"]}],
                     "sort": [{"by": "dimension:priority", "dir": "asc"}]}, True),
        ("between dates", {"measure": projects, "time": {"ref": "dimension:planned_deployment_date",
                                                        "range": {"from": "2026-07-01", "to": "2026-12-31"}}}, True),
        ("grouping", {"measure": projects, "breakdowns": [{"ref": "personal:area"}], "personal_fields": [
            {"key": "area", "kind": "grouping", "source": "dimension:region",
             "groups": [{"label": "Gulf", "values": ["ksa", "GCC - UAE"]}]}]}, False),
        ("band", {"measure": projects, "breakdowns": [{"ref": "personal:done"}], "personal_fields": [
            {"key": "done", "kind": "band", "source": "field:projects.percent_complete",
             "bands": [{"label": "Early", "lower": 0, "upper": 50}, {"label": "Late", "lower": 50, "upper": 100}],
             "closing_max": 100}]}, False),
        ("top 3", {"measure": projects, "breakdowns": [{"ref": "dimension:cio"}],
                   "sort": [{"by": "measure", "dir": "desc"}, {"by": "dimension:cio", "dir": "asc"}],
                   "limit": 3}, True),
    ]
    def adhoc(where: Any, **kw: Any) -> dict[str, Any]:
        return {"adhoc": {"entity": "projects", "agg": "count", "label": "Free-hand", "plain_rule": "Free-hand",
                          "where": where, **kw}}

    past_plan = {"all": [
        {"any": [{"field": "actual_project_start_date", "op": "not_null"}, {"field": "on_hold_date", "op": "not_null"},
                 {"field": "first_baselined_deployment_date", "op": "not_null"}]},
        {"any": [{"field": "planned_deployment_date", "op": "is_null"},
                 {"field": "planned_deployment_date", "op": "<", "value": {"today": True}}]}]}
    out += [
        ("adhoc past plan", {"measure": adhoc(past_plan)}, True),
        ("adhoc past plan by region", {"measure": adhoc(past_plan), "breakdowns": [{"ref": "dimension:region"}],
                                       "sort": [{"by": "dimension:region", "dir": "asc"}]}, True),
        ("adhoc due soon", {"measure": adhoc({"all": [
            {"field": "planned_deployment_date", "op": ">=", "value": {"today": True, "offset_days": -30}},
            {"field": "planned_deployment_date", "op": "<", "value": "2027-01-01"},
            {"not": {"field": "current_state", "op": "in", "values": ["Cancelled", "Closed"]}}]})}, True),
        ("adhoc over budget sum", {"measure": adhoc(
            {"field": "cost_total_usd", "op": ">", "other_field": "approved_budget_value_usd"},
            agg="sum", field="approved_budget_value_usd")}, True),
    ]
    requests = next((m for m in snap.measures if m.entity == "project_requests"), None)
    if requests is not None:  # fan-out: requests broken down by a field of the projects that link to them
        out.append(("fan-out", {"measure": {"ref": f"measure:{requests.key}"},
                                "breakdowns": [{"ref": "field:projects.region"}]}, False))
        out.append(("fan-out share", {"measure": {"ref": f"measure:{requests.key}"},
                                      "breakdowns": [{"ref": "field:projects.region"}],
                                      "share_of_total": [f"measure:{requests.key}"]}, False))
    # US-176: several measures, derived figures (divide-by-zero guarded) and shares of the total.
    forecast, budget = "measure:forecast_cost_total", "measure:approved_budget_total"
    counts = [f"measure:{m.key}" for m in snap.measures
              if m.entity == "projects" and isinstance(m.expression, AggExpr) and m.expression.agg == "count"]
    out += [
        ("money pair with derived by region", {
            "measures": [{"ref": forecast}, {"ref": budget}], "breakdowns": [{"ref": "dimension:region"}],
            "derived": [{"key": "pct_of", "op": "percent_of", "of": forecast, "to": budget},
                        {"key": "over", "op": "percent_change", "of": forecast, "to": budget},
                        {"key": "gap", "op": "difference", "of": forecast, "to": budget}],
            "sort": [{"by": "dimension:region", "dir": "asc"}]}, True),
        ("four counts with ratio, no breakdown", {
            "measures": [{"ref": r} for r in counts[:4]],
            "derived": [{"key": "per", "op": "ratio", "of": counts[1], "to": counts[0]}]}, True),
        ("share of total, top 3 by share", {
            "measure": {"ref": counts[0]}, "breakdowns": [{"ref": "dimension:cio"}], "share_of_total": [counts[0]],
            "sort": [{"by": f"share:{counts[0].split(':')[1]}", "dir": "desc"}, {"by": "dimension:cio", "dir": "asc"}],
            "limit": 3}, True),
        ("shares and ratio by priority and month", {
            "measures": [{"ref": counts[0]}, {"ref": counts[1]}],
            "breakdowns": [{"ref": "dimension:priority"}], "share_of_total": [counts[0], counts[1]],
            "derived": [{"key": "per", "op": "ratio", "of": counts[1], "to": counts[0]}],
            "time": {"ref": "dimension:planned_deployment_date", "grain": "month", "range": {"relative": "next_90_days"}},
            "sort": [{"by": "dimension:priority", "dir": "asc"}, {"by": "dimension:planned_deployment_date", "dir": "asc"}]},
         True),
    ]
    # US-178: period compare (SQL engines only; document compilers don't handle CTEs with FULL JOIN)
    out += [
        ("period compare by month", {
            "measure": projects, "compare": {"kind": "previous_period"},
            "time": {"ref": "dimension:planned_deployment_date", "grain": "month",
                     "range": {"from": "2026-07-01", "to": "2026-12-31"}}}, True),
        ("period compare by quarter with breakdown", {
            "measure": projects, "compare": {"kind": "previous_period"},
            "breakdowns": [{"ref": "dimension:region"}],
            "time": {"ref": "dimension:planned_deployment_date", "grain": "quarter",
                     "range": {"from": "2026-01-01", "to": "2026-12-31"}},
            "sort": [{"by": "dimension:region", "dir": "asc"},
                     {"by": "dimension:planned_deployment_date", "dir": "asc"}]}, True),
        ("same period last year by month", {
            "measure": projects, "compare": {"kind": "same_period_last_year"},
            "time": {"ref": "dimension:planned_deployment_date", "grain": "month",
                     "range": {"from": "2026-01-01", "to": "2026-06-30"}}}, True),
        # BUG-006: windows with data on both sides, so each period must meet its counterpart (H2 2026 → H1 2027).
        ("period compare by month, both windows filled", {
            "measure": projects, "compare": {"kind": "previous_period"}, "breakdowns": [{"ref": "dimension:region"}],
            "time": {"ref": "dimension:planned_deployment_date", "grain": "month",
                     "range": {"from": "2027-01-01", "to": "2027-06-30"}}}, False),
        ("same quarter last year, both windows filled", {
            "measure": projects, "compare": {"kind": "same_period_last_year"},
            "time": {"ref": "dimension:planned_deployment_date", "grain": "quarter",
                     "range": {"from": "2027-01-01", "to": "2027-12-31"}}}, False),
    ]
    # US-178: exists_in (semi-join) — only when the snapshot has risks
    if snap.entity("risks") is not None:
        out += [
            ("exists_in risks", {
                "measure": projects,
                "exists_in": [{"entity": "risks",
                               "filters": [{"ref": "dimension:risk_category", "op": "=", "values": ["Technical"]}]}]}, True),
            ("exists_in risks by region", {
                "measure": projects, "breakdowns": [{"ref": "dimension:region"}],
                "exists_in": [{"entity": "risks",
                               "filters": [{"ref": "dimension:risk_category", "op": "=", "values": ["Technical"]}]}],
                "sort": [{"by": "dimension:region", "dir": "asc"}]}, True),
        ]
    # US-177: window operations — SQL in the engine; MongoDB and the Dataverse Web API through the DuckDB post-step.
    forecast_by_project = {"measure": {"ref": forecast},
                           "breakdowns": [{"ref": "dimension:programme"}, {"ref": "field:projects.name"}]}
    by_month_2026 = {"ref": "dimension:planned_deployment_date", "grain": "month",
                     "range": {"from": "2026-01-01", "to": "2026-12-31"}}
    out += [
        ("top 3 projects per programme", {**forecast_by_project,
                                          "top_n_per_group": {"by": "dimension:programme", "n": 3}}, False),
        ("running total by month per region", {"measure": projects, "breakdowns": [{"ref": "dimension:region"}],
                                               "time": by_month_2026, "running_total": {}}, False),
        ("3-month moving average by month", {"measure": projects, "time": by_month_2026,
                                             "moving_avg": {"window": 3}}, False),
        ("rank by cio", {"measure": projects, "breakdowns": [{"ref": "dimension:cio"}], "rank": {}}, False),
        ("quartiles by cio", {"measure": projects, "breakdowns": [{"ref": "dimension:cio"}], "bucket": {"k": 4}},
         False),
        ("p85 forecast per programme", {**forecast_by_project,
                                        "percentile": {"p": "p85", "per": "dimension:programme"}}, False),
        ("programmes with more than 5", {"measure": projects, "breakdowns": [{"ref": "dimension:programme"}],
                                         "having": {"op": ">", "value": 5}}, False),
    ]
    return out


@pytest.fixture(scope="module")
async def reference() -> Source:
    src = Source("postgres")
    yield src
    await src.adapter.close()


@pytest.fixture(scope="module", params=["mysql", "sqlserver", "excel", "mongodb"])
async def target(request: pytest.FixtureRequest) -> Source:
    src = Source(request.param)
    yield src
    await src.adapter.close()


def _compile(src: Source, body: dict[str, Any]) -> tuple[Any, Any]:
    spec, _ = parse_spec({"model_version_id": "v", "connection_id": "c", **body})
    vs = pin_dates(SpecValidator(src.snap, freehand=True).validate(spec, connection_id="c"), as_of=AS_OF, today=TODAY)
    return compile_spec(getattr(src, "connection", src.engine), vs, as_of=AS_OF), vs


async def test_every_measure_and_shape_matches_postgres(reference: Source, target: Source) -> None:
    mismatches = []
    for label, body, ordered in cases(reference.snap):
        for sc_label, cio in (("all rows", None), ("CIO scope", "Nomsa Osei")):
            want_q, _ = _compile(reference, body)
            try:
                got_q, _ = _compile(target, body)
            except TelaError as exc:  # US-156: medians on PostgreSQL and DuckDB only; MongoDB has none of it
                assert exc.code == ErrorCode.MEASURE_UNAVAILABLE and target.engine in ("mysql", "sqlserver", "mongodb"), label
                continue
            want = await reference.run(want_q, scope(reference.snap, cio), limit=body.get("limit"))
            got = await target.run(got_q, scope(target.snap, cio), limit=body.get("limit"))
            if not ordered:
                want, got = sorted(want, key=repr), sorted(got, key=repr)
            if want != got:
                mismatches.append((label, sc_label, want[:5], got[:5]))
    assert mismatches == []


async def _helpers(src: Source) -> list[list[tuple[Any, ...]]]:
    measure = next(m for m in src.snap.measures if m.entity == "projects" and m.expression.agg == "count")
    _, vs = _compile(src, {"measure": {"ref": f"measure:{measure.key}"}, "breakdowns": [{"ref": "dimension:region"}]})
    rc = records_compiler(getattr(src, "connection", src.engine))
    ent = src.snap.entity("projects")
    assert ent is not None
    region, modified = ent.field("region"), ent.field("modified_on")
    assert region is not None and modified is not None
    queries = [
        (rc.compile_records(vs, marks={"region": "KSA"}, as_of=AS_OF,
                            fields=["project_id", "name", "planned_deployment_date"], limit=5, offset=5), 5),
        (rc.compile_records(vs, marks={"region": "KSA"}, as_of=AS_OF, fields=[], limit=1, offset=0,
                            count_only=True), None),
        (field_query(getattr(src, "connection", src.engine), src.snap, FieldRef("projects", region, "Region"),
                     "distinct", limit=50), None),
        (field_query(getattr(src, "connection", src.engine), src.snap, FieldRef("projects", modified, "Modified"),
                     "max"), None),
        # The field profile AG-03 reads (US-175): rows with a value, the earliest value, the most frequent values.
        (field_query(getattr(src, "connection", src.engine), src.snap, FieldRef("projects", region, "Region"),
                     "filled"), None),
        (field_query(getattr(src, "connection", src.engine), src.snap, FieldRef("projects", modified, "Modified"),
                     "min"), None),
        (field_query(getattr(src, "connection", src.engine), src.snap, FieldRef("projects", region, "Region"),
                     "top", limit=5), 5),
    ]
    return [await src.run(q, scope(src.snap), limit=n) for q, n in queries]


async def test_records_and_filter_values_match_postgres(reference: Source, target: Source) -> None:
    want = await _helpers(reference)
    assert len(want[0]) == 5 and want[1][0][0] > 5 and ("KSA",) in want[2]
    assert await _helpers(target) == want


def test_as_of_is_a_date() -> None:
    assert isinstance(AS_OF, date)


# ----------------------------------------------------------------------------- Dataverse (US-110, US-111)


def dataverse_snapshot() -> ModelSnapshot:
    """The PMO pack bound to the fake environment's logical names (fields without one stay unbound)."""
    manifest, _, _ = load_manifest(DEFAULT_PACKS_DIR / "pmo" / "manifest.yaml")
    catalog = [{"qualified_name": f"dbo.{logical}", "columns": [{"name": c} for c, _ in
                                                                   tpg_synthetic.dataverse_columns(table)]}
               for table, (logical, _) in tpg_synthetic.DATAVERSE_TABLES.items()]
    binding = bind(manifest, match(manifest, catalog), reporting_date=AS_OF.isoformat())
    return ModelSnapshot.model_validate({"model_version_id": "v", "connection_id": "c", **binding.definition})


class DataverseSource(Source):
    def __init__(self, mode: str, fake: Any, tds: dict[str, Any] | None) -> None:
        import os

        from sources import dataverse_info, seed_dataverse_tds

        from tela_runner.adapters.dataverse import DataverseAdapter

        os.environ["TELA_DATAVERSE_AUTHORITY_ROUTE"] = fake.url
        if mode == "tds":
            if tds is None:
                pytest.skip("TELA_TEST_MSSQL_URL not set")
            seed_dataverse_tds(tds)
        self.adapter = DataverseAdapter(allow_insecure_tls=True)
        self.engine, self.dialect, self.schema = "dataverse", "tsql", "dbo"
        self.info = dataverse_info(fake, mode="tds" if mode == "tds" else "webapi", tds=tds if mode == "tds" else None,
                                   fmt="fetchxml" if mode == "fetchxml" else "odata")
        self.snap = dataverse_snapshot()
        self.allowed = {e.source_object.lower() for e in self.snap.entities}
        self.connection: dict[str, Any] = {"engine": "dataverse", "descriptor": self.info.descriptor}

    async def load_columns(self) -> None:
        catalog = await self.adapter.introspect(self.info)
        self.connection["_columns"] = {o.qualified_name.lower(): {c["name"]: c for c in o.columns}
                                       for o in catalog.objects}

    async def run(self, compiled: Any, sc: ResolvedScope, *, limit: int | None = None) -> list[tuple[Any, ...]]:
        if isinstance(compiled, WebApiCompiled):
            statement = compiled.render(self.snap, sc, limit or 10_001)
            assert_webapi_scoped(statement, self.snap, sc, self.connection["_columns"])
            check_webapi(statement, self.allowed)
            result = await self.adapter.execute(self.info, statement, {}, Limits(), "parity")
            return [tuple(_norm(v) for v in row) for row in _post(compiled, result.rows)]
        return await super().run(compiled, sc, limit=limit)


@pytest.fixture(scope="module")
def fake_dataverse() -> Any:
    from sources import FakeDataverse

    f = FakeDataverse()
    yield f
    f.stop()


@pytest.fixture(scope="module", params=["tds", "odata", "fetchxml"])
async def dataverse(request: pytest.FixtureRequest, fake_dataverse: Any) -> DataverseSource:
    src = DataverseSource(request.param, fake_dataverse, url("mssql"))
    await src.load_columns()
    yield src
    await src.adapter.close()


MIN_SHAPES = {"tds": 110, "odata": 60, "fetchxml": 60}  # shapes that must run (others: unbound or unavailable)


def aligned(reference: Source, dv: ModelSnapshot) -> Source:
    """PostgreSQL with Dataverse's bindings: the pack has no Dataverse column for some optional calculator inputs
    (percent complete, actual start, issues KPI), which then count as blank, so the health score uses fewer
    factors there. The reference leaves the same inputs out (and keeps only the measures and dimensions Dataverse
    binds, which no longer resolve without them)."""
    import copy

    from tela_query_spec.expr import CALCULATORS

    optional = {f for c in CALCULATORS.values() for f in c["optional"]}
    d = reference.snap.model_dump()
    for ent in d["entities"]:
        de = dv.entity(ent["key"])
        if ent["key"] == "projects" and de is not None:
            ent["fields"] = [f for f in ent["fields"] if f["key"] not in optional or de.field(f["key"]) is not None]
    d["measures"] = [m for m in d["measures"] if any(x.key == m["key"] for x in dv.measures)]
    d["dimensions"] = [m for m in d["dimensions"] if any(x.key == m["key"] for x in dv.dimensions)]
    src = copy.copy(reference)
    src.snap = ModelSnapshot.model_validate(d)
    return src


async def test_dataverse_matches_postgres(reference: Source, dataverse: DataverseSource) -> None:
    """Every shape the PMO pack can bind on Dataverse returns PostgreSQL's answer; in Web API mode the shapes it
    cannot express are Unavailable, never approximated."""
    from tela_common.errors import ErrorCode, TelaError

    mismatches, ran, unavailable = [], 0, 0
    ref = aligned(reference, dataverse.snap)
    for label, body, ordered in cases(reference.snap):
        for sc_label, cio in (("all rows", None), ("CIO scope", "Nomsa Osei")):
            try:
                got_q, _ = _compile(dataverse, body)
            except TelaError as exc:
                assert exc.code in (ErrorCode.SPEC_INVALID, ErrorCode.MEASURE_UNAVAILABLE), (label, exc.code)
                unavailable += exc.code == ErrorCode.MEASURE_UNAVAILABLE
                continue
            want_q, _ = _compile(ref, body)
            want = await ref.run(want_q, scope(ref.snap, cio), limit=body.get("limit"))
            got = await dataverse.run(got_q, scope(dataverse.snap, cio), limit=body.get("limit"))
            if not ordered:
                want, got = sorted(want, key=repr), sorted(got, key=repr)
            ran += 1
            if want != got:
                mismatches.append((label, sc_label, want[:5], got[:5]))
    assert mismatches == []
    mode = dataverse.info.descriptor["webapi_format"] if dataverse.info.descriptor["mode"] == "webapi" else "tds"
    print(f"dataverse {mode}: {ran} shapes ran, {unavailable} unavailable")
    assert ran >= MIN_SHAPES[mode], (ran, unavailable)
    if mode != "tds":
        assert unavailable > 0  # arithmetic measures and period grains are Unavailable in Web API mode


async def test_dataverse_helpers_match_postgres(reference: Source, dataverse: DataverseSource) -> None:
    want, got = await _helpers(reference), await _helpers(dataverse)
    assert len(got[0]) == len(want[0]) == 5  # record pages (ids differ: GUIDs on Dataverse)
    assert got[1:] == want[1:]  # record count, scoped filter values, newest modified date
