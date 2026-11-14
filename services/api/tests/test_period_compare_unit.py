"""Period compare, semi-join and cohort without a source (US-178; FEAT-063; ENH-012).

The spec rules (``compare`` with ``same_period_last_year``, ``exists_in`` semi-join, ``cohort`` breakdown),
the validator's checks, the SQL compiler on DuckDB over the synthetic extract (period compare joins two
time windows on the grain with delta and % change columns; exists_in emits an EXISTS subquery; cohort
maps root rows to a date bucket of a linked entity), and RULE-008 rows. Engine parity for the same shapes
is in ``test_dialect_parity``.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import duckdb
import pytest
import tpg_synthetic
from pydantic import ValidationError
from test_pmo_catalogue_unit import _catalog

from tela_api.ask.refine import (
    apply_ops,
    chips_for,
    parse_instruction,
    previous_period_spec,
    same_period_last_year_spec,
)
from tela_api.packs.manifest import bind, discover, load_manifest, match
from tela_api.query.compiler import SqlCompiler, _year_ago
from tela_api.query.limits import Limits, apply_limit
from tela_api.query.validator import SpecValidator
from tela_api.query.viz import result_shape, visualise
from tela_common.errors import TelaError
from tela_query_spec.model import ModelSnapshot
from tela_query_spec.spec import QuerySpec, parse_spec

AS_OF = tpg_synthetic.AS_OF
OPEN = "measure:open_projects"
FORECAST = "measure:forecast_cost_total"


@pytest.fixture(scope="module")
def snapshot() -> ModelSnapshot:
    manifest = load_manifest(next(p for p in discover() if p.parent.name == "pmo"))[0]
    binding = bind(manifest, match(manifest, _catalog()), reporting_date=AS_OF.isoformat())
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


def spec(body: dict[str, Any], snap: ModelSnapshot | None = None) -> QuerySpec:
    ids = {"model_version_id": snap.model_version_id, "connection_id": snap.connection_id} if snap else {
        "model_version_id": "v1", "connection_id": "c1"}
    return parse_spec({**ids, **body})[0]


def run(db: duckdb.DuckDBPyConnection, snap: ModelSnapshot, body: dict[str, Any]) -> tuple[list[dict], list[tuple]]:
    vs = SpecValidator(snap).validate(spec(body, snap))
    compiled = SqlCompiler("duckdb").compile(vs, as_of=AS_OF)
    tree, _, _ = apply_limit(compiled.tree, Limits(), body.get("limit"))
    sql = tree.sql(dialect="duckdb")
    rows = db.execute(sql, compiled.params.values).fetchall()
    cols = [{"name": c.name, "kind": c.kind, "label": c.label, "ref": c.ref, "format": c.format,
             "of": c.of, "data_type": c.data_type} for c in compiled.columns]
    return cols, rows


# --------------------------------------------------------------------------- spec parsing


class TestCompare:
    def test_previous_period(self) -> None:
        s = spec({"measure": {"ref": OPEN}, "compare": {"kind": "previous_period"},
                  "time": {"ref": "dimension:planned_deployment_date", "grain": "month",
                           "range": {"relative": "this_year"}}})
        assert s.compare is not None and s.compare.kind == "previous_period"

    def test_same_period_last_year(self) -> None:
        s = spec({"measure": {"ref": OPEN}, "compare": {"kind": "same_period_last_year"},
                  "time": {"ref": "dimension:planned_deployment_date", "grain": "month",
                           "range": {"relative": "this_year"}}})
        assert s.compare is not None and s.compare.kind == "same_period_last_year"


class TestExistsIn:
    def test_parse(self) -> None:
        s = spec({"measure": {"ref": OPEN}, "exists_in": [
            {"entity": "risks", "filters": [{"ref": "dimension:risk_category", "op": "=", "values": ["Technical"]}]}]})
        assert len(s.exists_in) == 1
        assert s.exists_in[0].entity == "risks"

    def test_no_filters_rejected(self) -> None:
        with pytest.raises(ValidationError, match="at least"):
            spec({"measure": {"ref": OPEN}, "exists_in": [{"entity": "risks", "filters": []}]})


class TestCohort:
    def test_parse(self) -> None:
        s = spec({"measure": {"ref": OPEN}, "cohort": {
            "ref": "field:risks.due_date", "grain": "quarter"}})
        assert s.cohort is not None
        assert s.cohort.grain == "quarter" and s.cohort.agg == "min"

    def test_max_agg(self) -> None:
        s = spec({"measure": {"ref": OPEN}, "cohort": {
            "ref": "field:risks.due_date", "grain": "month", "agg": "max"}})
        assert s.cohort.agg == "max"


# --------------------------------------------------------------------------- year-ago helper


class TestYearAgo:
    def test_normal(self) -> None:
        assert _year_ago(date(2026, 6, 15)) == date(2025, 6, 15)

    def test_leap(self) -> None:
        assert _year_ago(date(2024, 2, 29)) == date(2023, 2, 28)


# --------------------------------------------------------------------------- period compare spec helpers


class TestPreviousPeriodSpec:
    def test_shifts_time_range(self) -> None:
        s = {"time": {"ref": "dimension:planned_deployment_date", "range": {"relative": "this_quarter"}}}
        result = previous_period_spec(s, AS_OF)
        assert result is not None
        prev, _label = result
        assert "range" in prev["time"]
        assert "compare" not in prev

    def test_no_range_returns_none(self) -> None:
        s = {"time": {"ref": "dimension:planned_deployment_date", "grain": "month"}}
        assert previous_period_spec(s, AS_OF) is None


class TestSamePeriodLastYear:
    def test_shifts_by_one_year(self) -> None:
        s = {"time": {"ref": "dimension:planned_deployment_date", "range": {"from": "2026-01-01", "to": "2026-03-31"}}}
        result = same_period_last_year_spec(s, AS_OF)
        assert result is not None
        prev, label = result
        assert prev["time"]["range"]["from"] == "2025-01-01"
        assert prev["time"]["range"]["to"] == "2025-03-31"
        assert "2025" in label


# --------------------------------------------------------------------------- refine ops


class TestRefineOps:
    def test_compare_last_year(self, snapshot: ModelSnapshot) -> None:
        base = {"model_version_id": "v1", "connection_id": "c1", "measure": {"ref": OPEN},
                "time": {"ref": "dimension:planned_deployment_date", "grain": "month",
                         "range": {"relative": "this_year"}}, "breakdowns": []}
        out, said = apply_ops(base, [{"op": "compare_last_year"}], snapshot)
        assert out["compare"] == {"kind": "same_period_last_year"}
        assert "last year" in said[0].lower()

    def test_compare_last_year_needs_range(self, snapshot: ModelSnapshot) -> None:
        base = {"model_version_id": "v1", "connection_id": "c1", "measure": {"ref": OPEN},
                "time": {"ref": "dimension:planned_deployment_date", "grain": "month"}, "breakdowns": []}
        with pytest.raises(TelaError):
            apply_ops(base, [{"op": "compare_last_year"}], snapshot)

    def test_parse_yoy(self, snapshot: ModelSnapshot) -> None:
        base = {"measure": {"ref": OPEN},
                "time": {"ref": "dimension:planned_deployment_date", "grain": "month",
                         "range": {"relative": "this_year"}}}
        ops = parse_instruction("year over year", base, snapshot,
                                measures=snapshot.measures, dims=snapshot.dimensions, values={})
        assert any(o["op"] == "compare_last_year" for o in ops)


# --------------------------------------------------------------------------- chips


class TestChips:
    def test_same_period_last_year_chip(self, snapshot: ModelSnapshot) -> None:
        s = {"measure": {"ref": OPEN}, "compare": {"kind": "same_period_last_year"},
             "time": {"ref": "dimension:planned_deployment_date", "grain": "month",
                      "range": {"relative": "this_year"}}, "breakdowns": [], "filters": []}
        chips = chips_for(s, snapshot)
        cmp = next((c for c in chips if c["kind"] == "compare"), None)
        assert cmp is not None and "last year" in cmp["label"]


# --------------------------------------------------------------------------- SQL compilation: period compare


class TestPeriodCompareCompiler:
    def test_previous_period_by_month(self, snapshot: ModelSnapshot, db: duckdb.DuckDBPyConnection) -> None:
        body = {"measure": {"ref": OPEN}, "compare": {"kind": "previous_period"},
                "time": {"ref": "dimension:planned_deployment_date", "grain": "month",
                         "range": {"from": "2026-07-01", "to": "2026-12-31"}}}
        cols, rows = run(db, snapshot, body)
        col_names = [c["name"] for c in cols]
        assert "period" in col_names
        # Delta columns exist
        m_key = next(c["name"] for c in cols if c["kind"] == "measure" and "__" not in c["name"])
        assert f"{m_key}__prev" in col_names
        assert f"{m_key}__delta" in col_names
        assert f"{m_key}__delta_pct" in col_names
        # Rows have the expected column count
        assert all(len(r) == len(cols) for r in rows)

    def test_same_period_last_year(self, snapshot: ModelSnapshot, db: duckdb.DuckDBPyConnection) -> None:
        body = {"measure": {"ref": OPEN}, "compare": {"kind": "same_period_last_year"},
                "time": {"ref": "dimension:planned_deployment_date", "grain": "quarter",
                         "range": {"from": "2026-01-01", "to": "2026-12-31"}}}
        cols, rows = run(db, snapshot, body)
        col_names = [c["name"] for c in cols]
        m_key = next(c["name"] for c in cols if c["kind"] == "measure" and "__" not in c["name"])
        assert f"{m_key}__prev" in col_names
        assert len(rows) >= 1

    def test_compare_with_breakdown(self, snapshot: ModelSnapshot, db: duckdb.DuckDBPyConnection) -> None:
        body = {"measure": {"ref": OPEN}, "compare": {"kind": "previous_period"},
                "breakdowns": [{"ref": "dimension:region"}],
                "time": {"ref": "dimension:planned_deployment_date", "grain": "month",
                         "range": {"from": "2026-07-01", "to": "2026-12-31"}}}
        cols, _rows = run(db, snapshot, body)
        col_names = [c["name"] for c in cols]
        assert "region" in col_names and "period" in col_names
        m_key = next(c["name"] for c in cols if c["kind"] == "measure" and "__" not in c["name"])
        assert f"{m_key}__delta" in col_names

    @staticmethod
    def _direct(db: duckdb.DuckDBPyConnection, grain: str, start: str, end: str) -> dict[date, int]:
        """Projects per period counted straight from the fixture (planned dates span 2026 and 2027)."""
        return dict(db.execute(
            f"SELECT CAST(date_trunc('{grain}', planned_deployment_date) AS DATE), count(*) FROM projects "
            "WHERE planned_deployment_date BETWEEN ? AND ? GROUP BY 1",
            [start, end]).fetchall())

    @pytest.mark.parametrize(("kind", "grain", "rng", "prev_rng", "back"), [
        ("previous_period", "month", ("2027-01-01", "2027-06-30"), ("2026-07-01", "2026-12-31"), 6),
        ("same_period_last_year", "quarter", ("2027-01-01", "2027-12-31"), ("2026-01-01", "2026-12-31"), 12),
        ("previous_period", "quarter", ("2027-01-01", "2027-12-31"), ("2026-01-01", "2026-12-31"), 12),
    ])
    def test_each_period_is_compared_with_its_counterpart(
            self, snapshot: ModelSnapshot, db: duckdb.DuckDBPyConnection, kind: str, grain: str,
            rng: tuple[str, str], prev_rng: tuple[str, str], back: int) -> None:
        """BUG-006: rows are the current window's periods only, each with the matching earlier period."""
        from tela_api.query.compiler import _add_months

        body = {"measure": {"ref": "measure:total_projects"}, "compare": {"kind": kind},
                "time": {"ref": "dimension:planned_deployment_date", "grain": grain,
                         "range": {"from": rng[0], "to": rng[1]}}}
        cols, rows = run(db, snapshot, body)
        names = [c["name"] for c in cols]
        m = next(c["name"] for c in cols if c["kind"] == "measure" and "__" not in c["name"])
        cur, prev = self._direct(db, grain, *rng), self._direct(db, grain, *prev_rng)
        assert cur and prev
        got = {r[names.index("period")]: r for r in rows}
        assert set(got) == set(cur) | {_add_months(p, back) for p in prev}
        assert all(date.fromisoformat(rng[0]) <= p <= date.fromisoformat(rng[1]) for p in got)
        for p, r in got.items():
            now, before = cur.get(p), prev.get(_add_months(p, -back))
            assert r[names.index(m)] == now and r[names.index(f"{m}__prev")] == before, p
            assert r[names.index(f"{m}__delta")] == (now or 0) - (before or 0)
            if before:  # one decimal place, not a whole number (DECIMAL without a scale on MySQL / SQL Server)
                assert float(r[names.index(f"{m}__delta_pct")]) == round(((now or 0) - before) / before * 100, 1)

    def test_previous_window(self) -> None:
        from tela_api.query.compiler import previous_window

        d = date.fromisoformat
        assert previous_window(d("2026-07-01"), d("2026-12-31"), "month") == (d("2026-01-01"), d("2026-06-30"))
        assert previous_window(d("2026-01-01"), d("2026-10-29"), "month") == (d("2025-03-01"), d("2025-12-29"))
        assert previous_window(d("2026-04-01"), d("2026-06-30"), "quarter") == (d("2026-01-01"), d("2026-03-30"))
        assert previous_window(d("2026-07-01"), d("2026-12-31"), None) == (d("2025-12-29"), d("2026-06-30"))

    def test_compare_figure_only_no_grain(self, snapshot: ModelSnapshot) -> None:
        """Without grain, compare is not handled by the compiler (orchestrator does it)."""
        body = {"measure": {"ref": OPEN}, "compare": {"kind": "previous_period"},
                "time": {"ref": "dimension:planned_deployment_date",
                         "range": {"from": "2026-07-01", "to": "2026-12-31"}}}
        vs = SpecValidator(snapshot).validate(spec(body, snapshot))
        compiled = SqlCompiler("duckdb").compile(vs, as_of=AS_OF)
        col_names = [c.name for c in compiled.columns]
        # No delta columns: the orchestrator handles figure-level compare
        assert not any("__delta" in n for n in col_names)


# --------------------------------------------------------------------------- SQL compilation: exists_in


class TestExistsInCompiler:
    def test_exists_in_risks_by_category(self, snapshot: ModelSnapshot, db: duckdb.DuckDBPyConnection) -> None:
        # Projects with at least one Technical risk
        body = {"measure": {"ref": OPEN},
                "exists_in": [{"entity": "risks",
                               "filters": [{"ref": "dimension:risk_category", "op": "=", "values": ["Technical"]}]}]}
        _cols, rows = run(db, snapshot, body)
        assert len(rows) == 1  # one row = the figure
        figure = rows[0][0]
        assert isinstance(figure, int)

        # Without exists_in, the figure should be >= the semi-joined one
        all_body = {"measure": {"ref": OPEN}}
        _, all_rows = run(db, snapshot, all_body)
        assert all_rows[0][0] >= figure

    def test_exists_in_with_breakdown(self, snapshot: ModelSnapshot, db: duckdb.DuckDBPyConnection) -> None:
        body = {"measure": {"ref": OPEN},
                "breakdowns": [{"ref": "dimension:region"}],
                "exists_in": [{"entity": "risks",
                               "filters": [{"ref": "dimension:risk_category", "op": "=", "values": ["Technical"]}]}]}
        cols, rows = run(db, snapshot, body)
        assert len(cols) == 2  # region + measure
        assert all(len(r) == 2 for r in rows)


# --------------------------------------------------------------------------- SQL compilation: cohort


class TestCohortCompiler:
    def test_cohort_by_risk_due_date(self, snapshot: ModelSnapshot, db: duckdb.DuckDBPyConnection) -> None:
        body = {"measure": {"ref": OPEN}, "cohort": {
            "ref": "field:risks.due_date", "grain": "quarter"}}
        cols, rows = run(db, snapshot, body)
        col_names = [c["name"] for c in cols]
        assert "cohort" in col_names
        # Each row has a cohort date and a measure
        cohort_i = col_names.index("cohort")
        for r in rows:
            assert r[cohort_i] is None or hasattr(r[cohort_i], "year")  # date or None (LEFT JOIN)


# --------------------------------------------------------------------------- RULE-008 for period compare


class TestSuitability:
    def test_period_compare_shape(self, snapshot: ModelSnapshot, db: duckdb.DuckDBPyConnection) -> None:
        body = {"measure": {"ref": OPEN}, "compare": {"kind": "previous_period"},
                "time": {"ref": "dimension:planned_deployment_date", "grain": "month",
                         "range": {"from": "2026-07-01", "to": "2026-12-31"}}}
        cols, rows = run(db, snapshot, body)
        result = {"columns": cols, "rows": [list(r) for r in rows], "row_count": len(rows),
                  "truncated": False, "hidden": False}
        shape = result_shape(result, snapshot)
        # Period compare: the delta columns are companions, so only 1 series
        assert len(shape.series) == 1
        assert len(shape.companions) >= 3  # __prev, __delta, __delta_pct
        vis = visualise(result, snapshot)
        assert vis["visual"] is not None


# --------------------------------------------------------------------------- wave-2 merge: compare asked in the question


def test_rules_planner_reads_a_compare_asked_in_the_question() -> None:
    from test_planner_gold_unit import plan

    from tela_api.ask.planner import compare_words

    p = plan("open projects this quarter compared with the previous period")
    assert p.status == "ok" and p.spec is not None
    assert p.spec["time"]["range"] == {"relative": "this_quarter"} and "grain" not in p.spec["time"]
    assert p.spec["compare"] == {"kind": "previous_period"}
    assert {"kind": "compare", "label": "vs previous period"} in p.chips
    yoy = plan("open projects by region this quarter vs same period last year")
    assert yoy.spec is not None and yoy.spec["compare"] == {"kind": "same_period_last_year"}
    assert yoy.spec["time"]["range"] == {"relative": "this_quarter"} and "grain" not in yoy.spec["time"]
    # "vs last month" is the comparison, not the range
    lm = plan("open projects this month vs last month")
    assert lm.spec is not None and lm.spec["time"]["range"] == {"relative": "this_month"}
    assert lm.spec["compare"] == {"kind": "previous_period"}
    # no range to compare: said, not guessed
    none = plan("open projects compared with the previous period")
    assert none.spec is not None and "compare" not in none.spec
    assert any("pick a time range" in i for i in none.interpreted)
    assert compare_words("open projects vs closed projects")[0] is None  # two measures, not a comparison


def test_compare_window_and_period_names() -> None:
    from tela_api.ask.refine import compare_title, compare_window, current_label

    spec = {"measure": {"ref": "measure:open_projects"},
            "time": {"ref": "dimension:planned_deployment_date", "range": {"relative": "this_quarter"}}}
    prev = compare_window({**spec, "compare": {"kind": "previous_period"}}, AS_OF)
    last_year = compare_window({**spec, "compare": {"kind": "same_period_last_year"}}, AS_OF)
    assert prev is not None and last_year is not None and prev[1] != last_year[1]
    assert last_year[0]["time"]["range"] != prev[0]["time"]["range"]
    assert compare_title({**spec, "compare": {"kind": "same_period_last_year"}}) == "Same period last year"
    assert current_label(spec) == "this quarter"
    assert current_label({"measure": spec["measure"]}) == "this period"


# --------------------------------------------------------------------------- semi-join on a certified measure


class TestExistsInMeasure:
    """"Open projects with an open high risk": the linked entity's certified count measure is the EXISTS rule."""

    def test_matches_the_rows(self, snapshot: ModelSnapshot, db: duckdb.DuckDBPyConnection) -> None:
        body = {"measure": {"ref": OPEN},
                "exists_in": [{"entity": "risks", "measure": "measure:high_exposure_risks"}]}
        _, rows = run(db, snapshot, body)
        expected = db.execute(
            "SELECT count(*) FROM projects p WHERE p.status = 'Active' AND EXISTS (SELECT 1 FROM risks r "
            "WHERE r.project_id = p.project_id AND (r.state IS NULL OR r.state NOT IN ('Closed')) "
            "AND r.exposure >= 15)").fetchone()[0]
        assert rows[0][0] == expected > 0
        _, opens = run(db, snapshot, {"measure": {"ref": OPEN},
                                      "exists_in": [{"entity": "risks", "measure": "measure:open_risks"}]})
        assert opens[0][0] >= expected

    def test_refusals(self, snapshot: ModelSnapshot, db: duckdb.DuckDBPyConnection) -> None:
        with pytest.raises(ValidationError, match="filter or a measure"):
            spec({"measure": {"ref": OPEN}, "exists_in": [{"entity": "risks"}]})
        for bad in ("measure:open_issues", "measure:risk_cost_exposure"):  # other entity; not a count
            with pytest.raises(TelaError):
                run(db, snapshot, {"measure": {"ref": OPEN},
                                   "exists_in": [{"entity": "risks", "measure": bad}]})

    def test_document_and_web_api_sources_refuse(self, snapshot: ModelSnapshot) -> None:
        from tela_api.query.compilers import compile_spec

        vs = SpecValidator(snapshot).validate(spec({"measure": {"ref": OPEN}, "exists_in": [
            {"entity": "risks", "measure": "measure:high_exposure_risks"}]}, snapshot))
        with pytest.raises(TelaError, match="linked records"):
            compile_spec({"engine": "mongodb"}, vs, as_of=AS_OF)

    def test_document_and_web_api_sources_refuse_a_grained_compare(self, snapshot: ModelSnapshot) -> None:
        from tela_api.query.compilers import compile_spec
        from tela_common.errors import ErrorCode

        time = {"ref": "dimension:planned_deployment_date", "grain": "month",
                "range": {"from": "2026-07-01", "to": "2026-12-31"}}
        vs = SpecValidator(snapshot).validate(spec({"measure": {"ref": OPEN}, "compare": {"kind": "previous_period"},
                                                    "time": time}, snapshot))
        webapi = {"engine": "dataverse", "descriptor": {"mode": "webapi"}}
        for conn in ({"engine": "mongodb"}, webapi):
            with pytest.raises(TelaError, match="earlier period") as exc:
                compile_spec(conn, vs, as_of=AS_OF)
            assert exc.value.code == ErrorCode.MEASURE_UNAVAILABLE
        compile_spec("postgres", vs, as_of=AS_OF)  # SQL engines join the two windows
        plain = SpecValidator(snapshot).validate(spec({"measure": {"ref": OPEN}, "time": time}, snapshot))
        compile_spec({"engine": "mongodb"}, plain, as_of=AS_OF)  # no compare: still answered


def test_tsql_average_is_not_integer_division(snapshot: ModelSnapshot) -> None:
    vs = SpecValidator(snapshot).validate(spec({"measure": {"ref": "measure:slip_vs_baseline_avg"}}, snapshot))
    sql = SqlCompiler("tsql").compile(vs, as_of=AS_OF).tree.sql(dialect="tsql")
    assert "AVG(CAST(" in sql and "AS FLOAT)" in sql
    pg = SqlCompiler("postgres").compile(vs, as_of=AS_OF).tree.sql(dialect="postgres")
    assert "AVG(CAST(" not in pg


def test_rules_planner_reads_link_phrases() -> None:
    from test_planner_gold_unit import plan

    def read(q: str) -> tuple[str, Any, list[Any]]:
        p = plan(q)
        assert p.status == "ok" and p.spec is not None, q
        return p.spec["measure"]["ref"], p.spec.get("exists_in"), p.spec["filters"]

    risk = [{"entity": "risks", "measure": "measure:high_exposure_risks"}]
    assert read("open projects with an open high risk") == ("measure:open_projects", risk, [])  # not Priority: High
    assert read("open projects with a risk past its due date")[1] == [
        {"entity": "risks", "measure": "measure:overdue_risks"}]
    assert read("open projects in KSA with an open issue")[1:] == (
        [{"entity": "issues", "measure": "measure:open_issues"}],
        [{"ref": "dimension:region", "op": "=", "values": ["KSA"]}])
    assert read("open projects with a pending PCR")[1] == [{"entity": "change_requests",
                                                           "measure": "measure:pcrs_pending"}]
    by_region = plan("open projects by region with an overdue action")
    assert by_region.spec is not None and by_region.spec["breakdowns"] == [{"ref": "dimension:region"}]
    assert {"kind": "filter", "label": "With overdue action items", "ref": "measure:overdue_actions"} in by_region.chips
    # A measure named by the phrase itself, and a value of the root, stay as before.
    assert read("projects with a PCR") == ("measure:projects_with_pcr", None, [])
    assert read("open projects with high priority")[1:] == (
        None, [{"ref": "dimension:priority", "op": "=", "values": ["High"]}])


async def test_figure_compare_with_an_empty_earlier_period(snapshot: ModelSnapshot) -> None:
    """BUG-005: SUM over an earlier period with no rows is NULL; a sum or count compares with 0, an average says
    why there is no comparison instead of dropping it."""
    from types import SimpleNamespace

    from tela_api.ask.orchestrator import Orchestrator

    class Service:
        async def execute(self, *_: Any, **__: Any) -> dict[str, Any]:
            return {"columns": [{"name": "m", "kind": "measure", "label": "M", "format": "currency"}], "rows": [[None]]}

    orch = Orchestrator(Service(), None)  # type: ignore[arg-type]
    src = SimpleNamespace(snap=snapshot)
    who = SimpleNamespace(time_zone=None)
    time = {"ref": "dimension:planned_deployment_date", "range": {"from": "2026-01-01", "to": "2026-10-29"}}
    spec = {"measure": {"ref": FORECAST}, "compare": {"kind": "same_period_last_year"}, "time": time}
    out = await orch._compare(who, "t", src, spec, {"value": 280402.0, "format": "currency"})  # type: ignore[arg-type]
    assert out is not None and out["previous"] == 0 and out["change"] == 280402.0 and out["change_pct"] is None
    assert "none recorded" in out["text"] and "2025" in out["previous_range"]
    avg = {**spec, "measure": {"ref": "measure:slip_vs_baseline_avg"}}
    note = await orch._compare(who, "t", src, avg, {"value": 3.5})  # type: ignore[arg-type]
    assert note is not None and "no data" in note["_note"]
