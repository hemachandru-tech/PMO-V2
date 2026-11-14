"""US-255 Hybrid mode offline: Plan B that answers with a certified measure whenever the frame's entity, aggregation
and conditions equal one, and the gold suite's counterpart check (a B answer and its certified twin run once each,
any difference fails). The PMO pack's model on the synthetic TPG extract (DuckDB); no database, no LLM."""

from __future__ import annotations

from typing import Any

import duckdb
import pytest
from test_fields_planner_unit import OPEN, SNAP, fields, ok, plan_b, run
from test_gold_scoring_unit import db as db  # the extract in DuckDB (a pytest fixture)

from tela_api.ask.planner import Plan
from tela_api.knowledge import gold, scoring


def hybrid(question: str) -> Plan:
    return plan_b(question, hybrid=True)


def test_how_many_open_projects_uses_the_certified_measure(db: duckdb.DuckDBPyConnection) -> None:
    p = hybrid("How many open projects")
    spec = ok(p)
    assert spec["measure"] == {"ref": "measure:open_projects"}  # certified: the Certified mode's SQL
    assert fields(p)["certified"] and fields(p)["hybrid"] == "measure:open_projects"
    assert "counterpart" not in fields(p)
    assert "Answered with the certified measure Open projects" in p.interpreted
    assert run(db, SNAP, spec) == [(len(OPEN),)] == [(65,)]


def test_a_count_with_no_condition_is_the_total_measure() -> None:
    assert ok(hybrid("How many projects"))["measure"] == {"ref": "measure:total_projects"}


def test_a_list_of_open_projects_stays_plan_b_with_the_measure_as_its_counterpart(
        db: duckdb.DuckDBPyConnection) -> None:
    p = hybrid("list of open projects")
    spec = ok(p)
    assert scoring.answer_shape(spec) == "list_of_records" and spec["measure"]["ref"] == "measure:_adhoc"
    assert fields(p)["counterpart"] == "measure:open_projects" and "hybrid" not in fields(p)
    rows = run(db, SNAP, spec)
    other, how = scoring.counterpart_spec(spec, "measure:open_projects")
    assert how == "rows" and other["measure"] == {"ref": "measure:open_projects"} and not other["breakdowns"]
    counted = run(db, SNAP, other)
    assert len(rows) == counted[0][0] == 65  # as many rows as the measure counts
    assert scoring.counterpart_problem(how, rows, counted, "measure:open_projects") is None


def test_more_conditions_than_the_measure_stay_plan_b(db: duckdb.DuckDBPyConnection) -> None:
    p = hybrid("How many open projects in UAE")
    spec = ok(p)
    assert spec["measure"]["ref"] == "measure:_adhoc" and "hybrid" not in fields(p) and "counterpart" not in fields(p)
    assert run(db, SNAP, spec) == [(11,)]


def test_a_breakdown_of_the_measure_gives_plan_b_s_rows(db: duckdb.DuckDBPyConnection) -> None:
    h, b = ok(hybrid("open projects by region")), ok(plan_b("open projects by region"))
    assert h["measure"] == {"ref": "measure:open_projects"} and h["breakdowns"] == b["breakdowns"]
    assert scoring.same_result(run(db, SNAP, b), run(db, SNAP, h))
    p = plan_b("open projects by region")  # Plan B: the gold suite runs its certified twin beside it
    assert fields(p)["counterpart"] == "measure:open_projects"
    other, how = scoring.counterpart_spec(b, "measure:open_projects")
    assert how == "same" and other == h | {"measure": {"ref": "measure:open_projects"}}


def test_a_share_uses_the_measure_on_both_sides(db: duckdb.DuckDBPyConnection) -> None:
    spec = ok(hybrid("share of open projects by region"))
    assert spec["measure"] == {"ref": "measure:open_projects"} and spec["share_of_total"] == ["measure:open_projects"]
    run(db, SNAP, spec)


def test_a_ranking_of_records_has_no_counterpart() -> None:
    p = hybrid("top 3 projects by forecast cost")
    assert ok(p)["measure"]["ref"] == "measure:_adhoc" and "counterpart" not in fields(p)


def test_a_difference_is_a_failing_case() -> None:
    assert scoring.counterpart_problem("rows", [["a", 1], ["b", 1]], [[3]], "measure:x") == {
        "path": "hybrid_counterpart", "expected": "measure:x: 3", "actual": "2 rows"}
    assert scoring.counterpart_problem("same", [["KSA", 4]], [["KSA", 5]], "measure:x") is not None
    assert scoring.counterpart_problem("same", [["KSA", 4]], [["KSA", 4.0]], "measure:x") is None
    diff = [{"path": "hybrid_counterpart", "expected": "x", "actual": "y"}]
    assert scoring.classify(diff, expected={}, actual={}) == "result"


class _Service:
    """Answers the B spec with ``b`` rows and the certified measure with ``c``."""

    def __init__(self, b: list[Any], c: list[Any]) -> None:
        self.b, self.c, self.calls = b, c, []

    async def execute(self, spec: dict[str, Any], principal: Any, ctx: Any) -> dict[str, Any]:
        self.calls.append((spec["measure"]["ref"], ctx.fields))
        rows = self.c if spec["measure"]["ref"] != "measure:_adhoc" else self.b
        return {"rows": rows, "row_count": len(rows), "truncated": False}


class _Orch:
    def __init__(self, plan: Plan) -> None:
        self.plan = plan

    async def plan_for(self, *args: Any, **kwargs: Any) -> tuple[Plan, str, Any]:
        from tela_api.knowledge.retrieval import Retrieval

        return self.plan, "hybrid", Retrieval(hits=[], limited=False)


class _Src:
    class snap:  # noqa: N801 - stands in for a ModelSnapshot
        model_version_id = "v1"

    connection = {"id": "c1"}


@pytest.mark.parametrize(("counted", "passed"), [(2, True), (3, False)])
async def test_the_gold_suite_runs_both_once(counted: int, passed: bool) -> None:
    p = hybrid("list of open projects")
    spec = ok(p)
    ex = {"id": "g1", "question": "list of open projects", "tags": ["list"], "expected_spec": spec, "turns": [],
          "expected_result_fingerprint": None, "role_id": None}
    service = _Service([["A", 1], ["B", 1]], [[counted]])
    res = await gold.evaluate_one(service, _Orch(p), object(), "t1", ex, _Src())  # type: ignore[arg-type]
    assert service.calls == [("measure:_adhoc", True), ("measure:open_projects", False)]  # each once
    assert res["passed"] is passed
    if not passed:
        assert res["diff"][-1]["path"] == "hybrid_counterpart" and res["error_class"] == "result"
