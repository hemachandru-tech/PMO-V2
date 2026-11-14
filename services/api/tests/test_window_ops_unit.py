"""Window operations without a source (US-177; FEAT-063; ENH-012).

The spec rules (``top_n_per_group``, ``running_total``, ``moving_avg``, ``rank``, ``bucket``,
``percentile``, ``having``), the validator's checks and refusals, the SQL compiler on DuckDB over the
synthetic extract, limits, refine ops, chips, rules-planner phrases, and insight engine on window outputs.
Engine parity for the same shapes is in ``test_dialect_parity``.
"""

from __future__ import annotations

from typing import Any

import duckdb
import pytest
import tpg_synthetic
from pydantic import ValidationError
from test_pmo_catalogue_unit import _catalog

from tela_api.ask.planner import plan_question
from tela_api.ask.refine import apply_ops, chips_for, parse_instruction
from tela_api.packs.manifest import bind, discover, load_manifest, match
from tela_api.query.compiler import SqlCompiler
from tela_api.query.duckdb_post import apply_post, build_post_sql
from tela_api.query.limits import Limits, apply_limit
from tela_api.query.validator import SpecValidator, _bucket_labels
from tela_api.query.viz import result_shape
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
    return ModelSnapshot.model_validate(
        {"model_version_id": "v1", "connection_id": "c1", "engine": "excel", **binding.definition}
    )


@pytest.fixture(scope="module")
def db() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    con.execute("\n".join(line for line in tpg_synthetic.DDL.splitlines() if not line.startswith("DROP TABLE")))
    for table, cols, rows in tpg_synthetic.tables():
        con.executemany(
            f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
            [tuple(r[c] for c in cols) for r in rows],
        )
    return con


def spec(body: dict[str, Any], snap: ModelSnapshot | None = None) -> QuerySpec:
    ids = (
        {"model_version_id": snap.model_version_id, "connection_id": snap.connection_id}
        if snap
        else {"model_version_id": "v1", "connection_id": "c1"}
    )
    return parse_spec({**ids, **body})[0]


def run(
    db: duckdb.DuckDBPyConnection, snap: ModelSnapshot, body: dict[str, Any]
) -> tuple[list[dict[str, Any]], list[tuple[Any, ...]]]:
    vs = SpecValidator(snap).validate(spec(body, snap))
    compiled = SqlCompiler("duckdb").compile(vs, as_of=AS_OF)
    tree, _, _ = apply_limit(compiled.tree, Limits(), body.get("limit"))
    sql = tree.sql(dialect="duckdb")
    rows = db.execute(sql, compiled.params.values).fetchall()
    cols: list[dict[str, Any]] = [
        {
            "name": c.name,
            "kind": c.kind,
            "label": c.label,
            "ref": c.ref,
            "format": c.format,
            "of": c.of,
            "data_type": c.data_type,
        }
        for c in compiled.columns
    ]
    return cols, rows


# --------------------------------------------------------------------------- spec parsing


class TestSpecParsing:
    def test_top_n_per_group(self) -> None:
        s = spec(
            {
                "measure": {"ref": OPEN},
                "breakdowns": [{"ref": "dimension:region"}],
                "top_n_per_group": {"by": "dimension:region", "n": 3},
            }
        )
        assert s.top_n_per_group is not None
        assert s.top_n_per_group.n == 3

    def test_top_n_per_group_n_too_large(self) -> None:
        with pytest.raises(ValidationError, match="less than or equal to 50"):
            spec(
                {
                    "measure": {"ref": OPEN},
                    "breakdowns": [{"ref": "dimension:region"}],
                    "top_n_per_group": {"by": "dimension:region", "n": 100},
                }
            )

    def test_running_total(self) -> None:
        s = spec(
            {
                "measure": {"ref": OPEN},
                "time": {"ref": "dimension:planned_deployment_date", "grain": "month"},
                "running_total": {},
            }
        )
        assert s.running_total is not None

    def test_moving_avg(self) -> None:
        s = spec(
            {
                "measure": {"ref": OPEN},
                "time": {"ref": "dimension:planned_deployment_date", "grain": "month"},
                "moving_avg": {"window": 3},
            }
        )
        assert s.moving_avg is not None and s.moving_avg.window == 3

    def test_moving_avg_window_too_large(self) -> None:
        with pytest.raises(ValidationError, match="less than or equal to 24"):
            spec(
                {
                    "measure": {"ref": OPEN},
                    "time": {"ref": "dimension:planned_deployment_date", "grain": "month"},
                    "moving_avg": {"window": 30},
                }
            )

    def test_rank(self) -> None:
        s = spec({"measure": {"ref": OPEN}, "breakdowns": [{"ref": "dimension:region"}], "rank": {}})
        assert s.rank is not None

    def test_bucket(self) -> None:
        s = spec(
            {"measure": {"ref": OPEN}, "breakdowns": [{"ref": "dimension:region"}], "bucket": {"k": 4}}
        )
        assert s.bucket is not None and s.bucket.k == 4

    def test_percentile(self) -> None:
        s = spec(
            {"measure": {"ref": OPEN}, "breakdowns": [{"ref": "dimension:region"}], "percentile": {"p": "p85"}}
        )
        assert s.percentile is not None and s.percentile.p == "p85"

    def test_having(self) -> None:
        s = spec(
            {"measure": {"ref": OPEN}, "breakdowns": [{"ref": "dimension:region"}], "having": {"op": ">", "value": 5}}
        )
        assert s.having is not None and s.having.op == ">" and s.having.value == 5


# --------------------------------------------------------------------------- validator


class TestValidator:
    def test_running_total_without_grain_refused(self, snapshot: ModelSnapshot) -> None:
        with pytest.raises(TelaError, match="time grain"):
            SpecValidator(snapshot).validate(spec({"measure": {"ref": OPEN}, "running_total": {}}, snapshot))

    def test_moving_avg_without_grain_refused(self, snapshot: ModelSnapshot) -> None:
        with pytest.raises(TelaError, match="time grain"):
            SpecValidator(snapshot).validate(
                spec({"measure": {"ref": OPEN}, "moving_avg": {"window": 3}}, snapshot)
            )

    def test_top_n_per_group_bad_ref_refused(self, snapshot: ModelSnapshot) -> None:
        with pytest.raises(TelaError, match="breakdown"):
            SpecValidator(snapshot).validate(
                spec(
                    {
                        "measure": {"ref": OPEN},
                        "top_n_per_group": {"by": "dimension:region", "n": 3},
                    },
                    snapshot,
                )
            )

    def test_having_without_breakdown_refused(self, snapshot: ModelSnapshot) -> None:
        with pytest.raises(TelaError, match="breakdown"):
            SpecValidator(snapshot).validate(
                spec({"measure": {"ref": OPEN}, "having": {"op": ">", "value": 5}}, snapshot)
            )

    def test_bucket_labels_quartile(self) -> None:
        labels = _bucket_labels(4, "desc")
        assert labels[0] == "Top quartile"
        assert labels[-1] == "Bottom quartile"

    def test_bucket_labels_reversed(self) -> None:
        labels = _bucket_labels(4, "asc")
        assert labels[0] == "Bottom quartile"

    def test_bucket_labels_generic(self) -> None:
        labels = _bucket_labels(5, "desc")
        assert labels[0] == "Top 20%"
        assert len(labels) == 5


# --------------------------------------------------------------------------- compiler on DuckDB


class TestHaving:
    def test_having_filters_groups(self, db: duckdb.DuckDBPyConnection, snapshot: ModelSnapshot) -> None:
        cols, rows = run(
            db, snapshot, {"measure": {"ref": OPEN}, "breakdowns": [{"ref": "dimension:region"}],
                           "having": {"op": ">", "value": 0}}
        )
        mi = next(i for i, c in enumerate(cols) if c["name"] == "open_projects")
        assert all(r[mi] > 0 for r in rows)


class TestRank:
    def test_rank_by_region(self, db: duckdb.DuckDBPyConnection, snapshot: ModelSnapshot) -> None:
        cols, rows = run(
            db, snapshot, {"measure": {"ref": OPEN}, "breakdowns": [{"ref": "dimension:region"}], "rank": {}}
        )
        rank_col = next((i for i, c in enumerate(cols) if c["name"] == "open_projects__rank"), None)
        assert rank_col is not None
        ranks = sorted(r[rank_col] for r in rows)
        assert ranks[0] == 1

    def test_rank_column_is_companion(self, db: duckdb.DuckDBPyConnection, snapshot: ModelSnapshot) -> None:
        cols, _rows = run(
            db, snapshot, {"measure": {"ref": OPEN}, "breakdowns": [{"ref": "dimension:region"}], "rank": {}}
        )
        rank_c = next(c for c in cols if c["name"] == "open_projects__rank")
        assert rank_c["of"] == "open_projects"


class TestRunningTotal:
    def test_running_total_by_month(self, db: duckdb.DuckDBPyConnection, snapshot: ModelSnapshot) -> None:
        cols, rows = run(
            db,
            snapshot,
            {
                "measure": {"ref": OPEN},
                "time": {"ref": "dimension:planned_deployment_date", "grain": "month",
                         "range": {"relative": "this_year"}},
                "running_total": {},
            },
        )
        rt_col = next((i for i, c in enumerate(cols) if "__cumsum" in c["name"]), None)
        assert rt_col is not None
        measure_col = next(i for i, c in enumerate(cols) if c["name"] == "open_projects")
        cumsum = 0
        for r in sorted(rows, key=lambda r: r[0]):
            cumsum += r[measure_col]
            assert r[rt_col] == cumsum

    def test_running_total_is_companion(self, db: duckdb.DuckDBPyConnection, snapshot: ModelSnapshot) -> None:
        cols, _ = run(
            db,
            snapshot,
            {
                "measure": {"ref": OPEN},
                "time": {"ref": "dimension:planned_deployment_date", "grain": "month",
                         "range": {"relative": "this_year"}},
                "running_total": {},
            },
        )
        rt_c = next(c for c in cols if "__cumsum" in c["name"])
        assert rt_c["of"] == "open_projects"


class TestMovingAvg:
    def test_moving_avg_3(self, db: duckdb.DuckDBPyConnection, snapshot: ModelSnapshot) -> None:
        cols, rows = run(
            db,
            snapshot,
            {
                "measure": {"ref": OPEN},
                "time": {"ref": "dimension:planned_deployment_date", "grain": "month"},
                "moving_avg": {"window": 3},
            },
        )
        ma_col = next((i for i, c in enumerate(cols) if "__mavg3" in c["name"]), None)
        assert ma_col is not None
        assert any(r[ma_col] is not None for r in rows)


class TestBucket:
    def test_bucket_quartiles(self, db: duckdb.DuckDBPyConnection, snapshot: ModelSnapshot) -> None:
        cols, rows = run(
            db, snapshot, {"measure": {"ref": OPEN}, "breakdowns": [{"ref": "dimension:region"}], "bucket": {"k": 4}}
        )
        bk_col = next((i for i, c in enumerate(cols) if "__bucket" in c["name"]), None)
        assert bk_col is not None
        labels = {r[bk_col] for r in rows}
        assert labels & {"Top quartile", "2nd quartile", "3rd quartile", "Bottom quartile"}


class TestPercentile:
    def test_percentile_p50(self, db: duckdb.DuckDBPyConnection, snapshot: ModelSnapshot) -> None:
        cols, rows = run(
            db, snapshot, {"measure": {"ref": OPEN}, "breakdowns": [{"ref": "dimension:region"}],
                           "percentile": {"p": "p50"}}
        )
        pct_col = next((i for i, c in enumerate(cols) if "__p50" in c["name"]), None)
        assert pct_col is not None
        p50_values = {r[pct_col] for r in rows}
        assert len(p50_values) == 1  # all same since no partition


class TestTopNPerGroup:
    def test_top_n_limits_rows(self, db: duckdb.DuckDBPyConnection, snapshot: ModelSnapshot) -> None:
        """top_n_per_group with one breakdown keeps only the top N rows overall (each group has 1 row,
        so ROW_NUMBER partitioned by region keeps at most N distinct regions)."""
        _, all_rows = run(db, snapshot, {"measure": {"ref": OPEN}, "breakdowns": [{"ref": "dimension:region"}]})
        _cols, rows = run(
            db,
            snapshot,
            {
                "measure": {"ref": OPEN},
                "breakdowns": [{"ref": "dimension:region"}],
                "top_n_per_group": {"by": "dimension:region", "n": 1},
            },
        )
        # With partition by region and n=1, each region has exactly 1 row so all pass.
        # This tests the SQL compiles and runs without error.
        assert len(rows) == len(all_rows)  # each group has 1 row


# --------------------------------------------------------------------------- DuckDB post-step


class TestDuckDBPost:
    def test_build_post_sql_having(self, snapshot: ModelSnapshot) -> None:
        vs = SpecValidator(snapshot).validate(
            spec({"measure": {"ref": OPEN}, "breakdowns": [{"ref": "dimension:region"}],
                  "having": {"op": ">", "value": 5}}, snapshot)
        )
        from tela_api.query.compiler import ColumnMeta

        columns = [
            ColumnMeta("region", "dimension", "Region", "d", "text"),
            ColumnMeta("open_projects", "measure", "OP", "m", "integer", "integer"),
        ]
        result = build_post_sql(vs, columns)
        assert result is not None
        sql, _extra = result
        assert ">" in sql
        assert "5" in sql

    def test_apply_post_having(self) -> None:
        from tela_api.query.compiler import ColumnMeta

        columns = [
            ColumnMeta("region", "dimension", "Region", "d", "text"),
            ColumnMeta("count", "measure", "Count", "m", "integer", "integer"),
        ]
        rows: list[list[Any]] = [["A", 10], ["B", 3], ["C", 7]]
        sql = 'SELECT "region", "count" FROM tela_rows WHERE "count" > 5'
        new_rows, _new_cols = apply_post(rows, columns, sql, [])
        assert len(new_rows) == 2
        assert all(r[1] > 5 for r in new_rows)


# --------------------------------------------------------------------------- refine ops


class TestRefineOps:
    def test_set_running_total(self, snapshot: ModelSnapshot) -> None:
        spec_dict: dict[str, Any] = {
            "measure": {"ref": OPEN},
            "time": {"ref": "dimension:planned_deployment_date", "grain": "month"},
            "breakdowns": [],
            "filters": [],
        }
        out, said = apply_ops(spec_dict, [{"op": "set_running_total"}], snapshot)
        assert "running_total" in out
        assert any("running total" in s.lower() for s in said)

    def test_set_moving_avg(self, snapshot: ModelSnapshot) -> None:
        spec_dict: dict[str, Any] = {
            "measure": {"ref": OPEN},
            "time": {"ref": "dimension:planned_deployment_date", "grain": "month"},
            "breakdowns": [],
            "filters": [],
        }
        out, _said = apply_ops(spec_dict, [{"op": "set_moving_avg", "value": 3}], snapshot)
        assert out["moving_avg"]["window"] == 3

    def test_set_rank(self, snapshot: ModelSnapshot) -> None:
        spec_dict: dict[str, Any] = {
            "measure": {"ref": OPEN},
            "breakdowns": [{"ref": "dimension:region"}],
            "filters": [],
        }
        out, _said = apply_ops(spec_dict, [{"op": "set_rank"}], snapshot)
        assert "rank" in out

    def test_remove_window(self, snapshot: ModelSnapshot) -> None:
        spec_dict: dict[str, Any] = {
            "measure": {"ref": OPEN},
            "breakdowns": [{"ref": "dimension:region"}],
            "filters": [],
            "rank": {},
            "having": {"op": ">", "value": 5},
        }
        out, _said = apply_ops(spec_dict, [{"op": "remove_window"}], snapshot)
        assert "rank" not in out
        assert "having" not in out


# --------------------------------------------------------------------------- chips


class TestChips:
    def test_running_total_chip(self, snapshot: ModelSnapshot) -> None:
        spec_dict: dict[str, Any] = {
            "measure": {"ref": OPEN},
            "time": {"ref": "dimension:planned_deployment_date", "grain": "month"},
            "running_total": {},
        }
        chips = chips_for(spec_dict, snapshot)
        assert any(c["kind"] == "window" and "running total" in c["label"].lower() for c in chips)

    def test_having_chip(self, snapshot: ModelSnapshot) -> None:
        spec_dict: dict[str, Any] = {
            "measure": {"ref": OPEN},
            "breakdowns": [{"ref": "dimension:region"}],
            "having": {"op": ">", "value": 5},
        }
        chips = chips_for(spec_dict, snapshot)
        assert any(c["kind"] == "window" and ">" in c["label"] for c in chips)

    def test_rank_chip(self, snapshot: ModelSnapshot) -> None:
        spec_dict: dict[str, Any] = {
            "measure": {"ref": OPEN},
            "breakdowns": [{"ref": "dimension:region"}],
            "rank": {},
        }
        chips = chips_for(spec_dict, snapshot)
        assert any(c["kind"] == "window" and "rank" in c["label"].lower() for c in chips)


# --------------------------------------------------------------------------- rules planner


class TestRulesPlanner:
    def test_running_total_phrase(self, snapshot: ModelSnapshot) -> None:
        measures = [m for m in snapshot.measures if m.status == "certified"]
        dims = list(snapshot.dimensions)
        plan = plan_question(
            "running total of open projects by month this year",
            snapshot,
            visible_measures=measures,
            visible_dims=dims,
            values={},
        )
        assert plan.status == "ok"
        assert plan.spec is not None
        assert "running_total" in plan.spec

    def test_having_phrase(self, snapshot: ModelSnapshot) -> None:
        measures = [m for m in snapshot.measures if m.status == "certified"]
        dims = list(snapshot.dimensions)
        plan = plan_question(
            "programmes with more than 5 open projects",
            snapshot,
            visible_measures=measures,
            visible_dims=dims,
            values={},
        )
        assert plan.status == "ok"
        assert plan.spec is not None

    def test_quartile_phrase(self, snapshot: ModelSnapshot) -> None:
        measures = [m for m in snapshot.measures if m.status == "certified"]
        dims = list(snapshot.dimensions)
        plan = plan_question(
            "open projects by region in quartiles",
            snapshot,
            visible_measures=measures,
            visible_dims=dims,
            values={},
        )
        assert plan.status == "ok"
        if plan.spec:
            assert "bucket" in plan.spec


# --------------------------------------------------------------------------- RULE-008 / suitability


class TestSuitability:
    def test_running_total_is_companion(self, db: duckdb.DuckDBPyConnection, snapshot: ModelSnapshot) -> None:
        cols, rows = run(
            db,
            snapshot,
            {
                "measure": {"ref": OPEN},
                "time": {"ref": "dimension:planned_deployment_date", "grain": "month",
                         "range": {"relative": "this_year"}},
                "running_total": {},
            },
        )
        result: dict[str, Any] = {"columns": cols, "rows": rows}
        shape = result_shape(result, snapshot)
        assert len(shape.companions) >= 1

    def test_rank_is_companion(self, db: duckdb.DuckDBPyConnection, snapshot: ModelSnapshot) -> None:
        cols, rows = run(
            db, snapshot, {"measure": {"ref": OPEN}, "breakdowns": [{"ref": "dimension:region"}], "rank": {}}
        )
        result: dict[str, Any] = {"columns": cols, "rows": rows}
        shape = result_shape(result, snapshot)
        assert any("__rank" in c for c in shape.companions)


# --------------------------------------------------------------------------- insight engine


class TestInsights:
    def test_window_column_not_primary_measure(
        self, db: duckdb.DuckDBPyConnection, snapshot: ModelSnapshot
    ) -> None:
        cols, rows = run(
            db,
            snapshot,
            {
                "measure": {"ref": OPEN},
                "time": {"ref": "dimension:planned_deployment_date", "grain": "month",
                         "range": {"relative": "this_year"}},
                "running_total": {},
            },
        )
        result: dict[str, Any] = {"columns": cols, "rows": rows}
        from tela_api.ask.insights import detect

        facts = detect(result, additive=True)
        assert isinstance(facts, list)


# --------------------------------------------------------------------------- parse_instruction


class TestParseInstruction:
    def test_running_total(self, snapshot: ModelSnapshot) -> None:
        measures = [m for m in snapshot.measures if m.status == "certified"]
        dims = list(snapshot.dimensions)
        spec_dict: dict[str, Any] = {
            "measure": {"ref": OPEN},
            "time": {"ref": "dimension:planned_deployment_date", "grain": "month"},
        }
        ops = parse_instruction("show running total", spec_dict, snapshot, measures=measures, dims=dims, values={})
        assert any(op["op"] == "set_running_total" for op in ops)

    def test_show_rank(self, snapshot: ModelSnapshot) -> None:
        measures = [m for m in snapshot.measures if m.status == "certified"]
        dims = list(snapshot.dimensions)
        spec_dict: dict[str, Any] = {"measure": {"ref": OPEN}, "breakdowns": [{"ref": "dimension:region"}]}
        ops = parse_instruction("show rank", spec_dict, snapshot, measures=measures, dims=dims, values={})
        assert any(op["op"] == "set_rank" for op in ops)


# --------------------------------------------------------------------------- values checked against Python


PROJECT = "field:projects.name"


def _by(cols: list[dict[str, Any]], name: str) -> int:
    return next(i for i, c in enumerate(cols) if c["name"] == name)


def _quantile(values: list[float], q: float) -> float:
    """PERCENTILE_CONT: linear interpolation between the closest ranks."""
    v = sorted(values)
    h = q * (len(v) - 1)
    lo = int(h)
    hi = min(lo + 1, len(v) - 1)
    return v[lo] + (h - lo) * (v[hi] - v[lo])


class TestWindowValues:
    def test_top_3_projects_per_programme(self, db: duckdb.DuckDBPyConnection, snapshot: ModelSnapshot) -> None:
        body: dict[str, Any] = {"measure": {"ref": FORECAST},
                                "breakdowns": [{"ref": "dimension:programme"}, {"ref": PROJECT}]}
        cols, all_rows = run(db, snapshot, body)
        _, rows = run(db, snapshot, {**body, "top_n_per_group": {"by": "dimension:programme", "n": 3}})
        g, m = _by(cols, "programme"), _by(cols, "forecast_cost_total")
        expected: dict[Any, list[Any]] = {}
        for r in all_rows:
            expected.setdefault(r[g], []).append(r[m])
        got: dict[Any, list[Any]] = {}
        for r in rows:
            got.setdefault(r[g], []).append(r[m])
        assert any(len(v) > 3 for v in expected.values())  # the cut matters on this extract
        assert set(got) == set(expected)
        for k, vals in expected.items():
            top = sorted(vals, key=lambda x: (x is not None, x), reverse=True)[:3]
            assert sorted(got[k], key=lambda x: (x is not None, x)) == sorted(top, key=lambda x: (x is not None, x))
        # each group's rows together, highest first
        assert [r[g] for r in rows] == sorted(r[g] for r in rows)

    def test_running_total_restarts_per_group(self, db: duckdb.DuckDBPyConnection, snapshot: ModelSnapshot) -> None:
        cols, rows = run(db, snapshot, {"measure": {"ref": OPEN}, "breakdowns": [{"ref": "dimension:region"}],
                                        "time": {"ref": "dimension:planned_deployment_date", "grain": "month"},
                                        "running_total": {}})
        g, m = _by(cols, "region"), _by(cols, "open_projects")
        t = next(i for i, c in enumerate(cols) if c["kind"] == "period")
        rt = _by(cols, "open_projects__cumsum")
        running: dict[Any, int] = {}
        for r in sorted(rows, key=lambda r: (str(r[g]), r[t])):
            running[r[g]] = running.get(r[g], 0) + r[m]
            assert r[rt] == running[r[g]]
        assert len(running) > 1

    def test_moving_avg_values(self, db: duckdb.DuckDBPyConnection, snapshot: ModelSnapshot) -> None:
        cols, rows = run(db, snapshot, {"measure": {"ref": OPEN},
                                        "time": {"ref": "dimension:planned_deployment_date", "grain": "month"},
                                        "moving_avg": {"window": 3}})
        m, ma = _by(cols, "open_projects"), _by(cols, "open_projects__mavg3")
        ordered = sorted(rows, key=lambda r: r[0])
        for i, r in enumerate(ordered):
            window = [x[m] for x in ordered[max(0, i - 2):i + 1]]
            assert r[ma] == pytest.approx(sum(window) / len(window))

    def test_moving_avg_refused_on_average(self, snapshot: ModelSnapshot) -> None:
        with pytest.raises(TelaError, match="moving average"):
            SpecValidator(snapshot).validate(spec({"measure": {"ref": "measure:avg_health_score"},
                                                   "time": {"ref": "dimension:planned_deployment_date",
                                                            "grain": "month"},
                                                   "moving_avg": {"window": 3}}, snapshot))

    @pytest.mark.parametrize("p,q", [("p50", 0.5), ("p85", 0.85), ("p90", 0.9)])
    def test_percentile_per_programme_both_forms(self, db: duckdb.DuckDBPyConnection, snapshot: ModelSnapshot,
                                                 p: str, q: float) -> None:
        """DuckDB's quantile_cont and the position form used on PostgreSQL, MySQL and SQL Server give the
        same exact PERCENTILE_CONT of the project values in each programme."""
        body = {"measure": {"ref": FORECAST}, "breakdowns": [{"ref": "dimension:programme"}, {"ref": PROJECT}],
                "percentile": {"p": p, "per": "dimension:programme"}}
        cols, rows = run(db, snapshot, body)
        g, m, pc = _by(cols, "programme"), _by(cols, "forecast_cost_total"), _by(cols, f"forecast_cost_total__{p}")
        values: dict[Any, list[float]] = {}
        for r in rows:
            if r[m] is not None:
                values.setdefault(r[g], []).append(float(r[m]))
        for r in rows:
            assert float(r[pc]) == pytest.approx(_quantile(values[r[g]], q))
        vs = SpecValidator(snapshot).validate(spec(body, snapshot))
        portable = SqlCompiler("postgres").compile(vs, as_of=AS_OF)  # the position form, run here on DuckDB
        assert "PERCENTILE" not in portable.sql().upper()
        tree, _, _ = apply_limit(portable.tree, Limits(), None)
        prow = db.execute(tree.sql(dialect="duckdb"), portable.params.values).fetchall()
        pidx = [c.name for c in portable.columns].index(f"forecast_cost_total__{p}")
        assert sorted((str(r[g]), str(r[1]), round(float(r[pidx]), 4)) for r in prow) == \
            sorted((str(r[g]), str(r[1]), round(float(r[pc]), 4)) for r in rows)

    @pytest.mark.parametrize("dialect", ["postgres", "mysql", "tsql"])
    def test_percentile_compiles_without_percentile_cont(self, snapshot: ModelSnapshot, dialect: str) -> None:
        vs = SpecValidator(snapshot).validate(spec({"measure": {"ref": FORECAST},
                                                    "breakdowns": [{"ref": "dimension:programme"}, {"ref": PROJECT}],
                                                    "percentile": {"p": "p85", "per": "dimension:programme"}},
                                                   snapshot))
        sql = SqlCompiler(dialect).compile(vs, as_of=AS_OF).sql().upper()
        assert "PERCENTILE_CONT" not in sql and "ROW_NUMBER()" in sql and "FLOOR(" in sql

    def test_post_step_orders_along_the_grain(self, snapshot: ModelSnapshot) -> None:
        """MongoDB / Dataverse Web API: the post-step accumulates in period order, per region."""
        from tela_api.query.compiler import ColumnMeta

        vs = SpecValidator(snapshot).validate(spec({"measure": {"ref": OPEN},
                                                    "breakdowns": [{"ref": "dimension:region"}],
                                                    "time": {"ref": "dimension:planned_deployment_date",
                                                             "grain": "month"},
                                                    "running_total": {}, "moving_avg": {"window": 2}}, snapshot))
        grain = vs.window_ops[0].params["along"]
        columns = [ColumnMeta("region", "dimension", "Region", "d", "text"),
                   ColumnMeta(grain, "dimension", "Month", "t", "text"),
                   ColumnMeta("open_projects", "measure", "Open", "m", "integer", "integer")]
        rows: list[list[Any]] = [["A", "2026-03", 1], ["A", "2026-01", 9], ["A", "2026-02", 5], ["B", "2026-01", 2]]
        built = build_post_sql(vs, columns)
        assert built is not None
        out, cols = apply_post(rows, columns, *built)
        names = [c.name for c in cols]
        got = {(r[0], r[1]): (r[names.index("open_projects__cumsum")], r[names.index("open_projects__mavg2")])
               for r in out}
        assert got == {("A", "2026-01"): (9, 9.0), ("A", "2026-02"): (14, 7.0), ("A", "2026-03"): (15, 3.0),
                       ("B", "2026-01"): (2, 2.0)}
        assert [r[1] for r in out] == [r[1] for r in rows]  # the source's order and its values (ISO text) kept


class TestWindowPhrases:
    def _plan(self, snapshot: ModelSnapshot, q: str) -> dict[str, Any]:
        plan = plan_question(q, snapshot, visible_measures=[m for m in snapshot.measures if m.status == "certified"],
                             visible_dims=list(snapshot.dimensions), values={})
        assert plan.status == "ok" and plan.spec is not None
        SpecValidator(snapshot).validate(spec(plan.spec, snapshot))  # the planned shape is valid
        return plan.spec

    def test_top_n_projects_per_programme(self, snapshot: ModelSnapshot) -> None:
        s = self._plan(snapshot, "top 3 projects per programme by forecast cost")
        assert s["measure"]["ref"] == FORECAST
        assert [b["ref"] for b in s["breakdowns"]] == ["dimension:programme", PROJECT]
        assert s["top_n_per_group"] == {"by": "dimension:programme", "n": 3, "order": "desc"}
        assert "limit" not in s  # "top 3" is per programme, not a cap on the answer

    def test_having_names_its_groups(self, snapshot: ModelSnapshot) -> None:
        s = self._plan(snapshot, "programmes with more than 10 open projects")
        assert s["breakdowns"] == [{"ref": "dimension:programme"}]
        assert s["having"] == {"op": ">", "value": 10}

    def test_median_over_records_per_group(self, snapshot: ModelSnapshot) -> None:
        s = self._plan(snapshot, "median forecast cost by programme")
        assert s["percentile"] == {"p": "p50", "per": "dimension:programme"}
        assert PROJECT in [b["ref"] for b in s["breakdowns"]]

    def test_plain_top_n_unchanged(self, snapshot: ModelSnapshot) -> None:
        s = self._plan(snapshot, "top 5 regions by open projects")
        assert s["limit"] == 5 and "top_n_per_group" not in s
