"""Ad-hoc rules in the query spec and the ``today`` value (ENH-008, US-163; FEAT-060), no database server.

The PMO pack is bound to the synthetic extract and ad-hoc rules are compiled for DuckDB and run on its rows in
memory; each result is compared with the same rule written in Python over the extract. Refusals (no grant, fields
outside the model or hidden, limits, condition types, value types, links) are checked on the validator alone.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import duckdb
import pytest
import tpg_synthetic
from pydantic import ValidationError

from tela_api.packs.manifest import bind, discover, load_manifest, match
from tela_api.query import dataverse, mongo
from tela_api.query.asof import pin_dates
from tela_api.query.compiler import SqlCompiler
from tela_api.query.validator import NO_FREEHAND, SpecValidator, ValidatedSpec
from tela_common.errors import ErrorCode, TelaError
from tela_query_spec.expr import AsOf, Compare, Today, pin_today
from tela_query_spec.model import Measure, ModelSnapshot
from tela_query_spec.spec import ADHOC_REF, parse_spec

AS_OF: date = tpg_synthetic.AS_OF
TODAY = AS_OF + timedelta(days=12)  # a reporting date is configured, so today is a different day
PROJECTS, _ = tpg_synthetic.generate()
TYPES = {"varchar": "text", "numeric": "numeric", "double": "double precision", "integer": "integer",
         "date": "date", "timestamp": "timestamp"}

# The problem question (ENH-008): at least one stage date is set, and the deployment date is blank or before today.
PROBLEM = {"all": [
    {"any": [{"field": "actual_project_start_date", "op": "not_null"},
             {"field": "on_hold_date", "op": "not_null"},
             {"field": "first_baselined_deployment_date", "op": "not_null"}]},
    {"any": [{"field": "planned_deployment_date", "op": "is_null"},
             {"field": "planned_deployment_date", "op": "<", "value": {"today": True}}]},
]}


def _catalog() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for table, cols in (("projects", tpg_synthetic.PROJECT_COLUMNS),
                        ("project_requests", tpg_synthetic.REQUEST_COLUMNS)):
        body = tpg_synthetic.DDL.split(f"CREATE TABLE {table} (")[1].split(");")[0]
        types = {line.split()[0]: line.split()[1].split("(")[0] for line in body.strip().splitlines()
                 if line.split() and line.split()[0] in cols}
        out.append({"qualified_name": f"main.{table}",
                    "columns": [{"name": c, "type": TYPES.get(types[c], types[c])} for c in cols]})
    return out


@pytest.fixture(scope="module")
def snapshot() -> ModelSnapshot:
    manifest = load_manifest(next(p for p in discover() if p.parent.name == "pmo"))[0]
    binding = bind(manifest, match(manifest, _catalog()), reporting_date=AS_OF.isoformat())
    return ModelSnapshot.model_validate({"model_version_id": "v1", "connection_id": "c1", "engine": "excel",
                                         **binding.definition})


@pytest.fixture(scope="module")
def db() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    con.execute(tpg_synthetic.DDL.replace("DROP TABLE IF EXISTS projects;", "").replace(
        "DROP TABLE IF EXISTS project_requests;", ""))
    projects, requests = tpg_synthetic.generate()
    for table, cols, rows in (("project_requests", tpg_synthetic.REQUEST_COLUMNS, requests),
                              ("projects", tpg_synthetic.PROJECT_COLUMNS, projects)):
        con.executemany(f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                        [tuple(r[c] for c in cols) for r in rows])
    return con


def _spec(snap: ModelSnapshot, where: Any = None, *, agg: str = "count", field: str | None = None,
          entity: str = "projects", **kw: Any) -> Any:
    adhoc = {"entity": entity, "agg": agg, "label": "Past plan (free-hand)", "plain_rule": "Rule in words",
             **({"field": field} if field else {}), **({"where": where} if where is not None else {})}
    return parse_spec({"model_version_id": snap.model_version_id, "connection_id": snap.connection_id,
                       "measure": {"adhoc": adhoc}, **kw})[0]


def _validate(snap: ModelSnapshot, spec: Any, *, freehand: bool = True, hidden: tuple[str, ...] = (),
              today: date = TODAY) -> ValidatedSpec:
    vs = SpecValidator(snap, freehand=freehand, hidden_fields=hidden).validate(spec)
    return pin_dates(vs, as_of=AS_OF, today=today)


def _run(db: duckdb.DuckDBPyConnection, vs: ValidatedSpec) -> list[tuple[Any, ...]]:
    compiled = SqlCompiler("duckdb").compile(vs, as_of=AS_OF)
    return db.execute(compiled.sql(), compiled.params.values).fetchall()


def _refused(snap: ModelSnapshot, spec: Any, code: str = ErrorCode.SPEC_INVALID, **kw: Any) -> TelaError:
    with pytest.raises(TelaError) as err:
        _validate(snap, spec, **kw)
    assert err.value.code == code, err.value.message
    return err.value


# --------------------------------------------------------------------------- spec shape


def test_adhoc_takes_the_reserved_ref_and_only_the_main_measure(snapshot: ModelSnapshot) -> None:
    spec = _spec(snapshot, PROBLEM)
    assert spec.measure.ref == ADHOC_REF and spec.adhoc is not None
    assert parse_spec(spec.model_dump(by_alias=True))[0] == spec  # round-trips as stored
    base = {"model_version_id": "v1", "connection_id": "c1"}
    with pytest.raises(ValidationError):
        parse_spec({**base, "measure": {"ref": ADHOC_REF}})
    with pytest.raises(ValidationError):
        parse_spec({**base, "measure": {"ref": "measure:total_projects"},
                    "extra_measures": [{"adhoc": {"entity": "projects", "agg": "count", "label": "x",
                                                  "plain_rule": "x"}}]})
    with pytest.raises(ValidationError):  # a rule is data, never SQL text
        parse_spec({**base, "measure": {"adhoc": {"entity": "projects", "agg": "count", "label": "x",
                                                  "plain_rule": "x", "sql": "SELECT 1"}}})
    with pytest.raises(ValidationError):
        _spec(snapshot, agg="sum")  # sum needs a field


def test_a_model_never_stores_an_adhoc_measure(snapshot: ModelSnapshot) -> None:
    data = snapshot.model_dump()
    data["measures"] = [*data["measures"], {**data["measures"][0], "key": "x_adhoc", "status": "adhoc"}]
    with pytest.raises(ValidationError, match="free-hand"):
        ModelSnapshot.model_validate(data)


# --------------------------------------------------------------------------- validator refusals


def test_needs_a_live_freehand_grant(snapshot: ModelSnapshot) -> None:
    err = _refused(snapshot, _spec(snapshot, PROBLEM), ErrorCode.FORBIDDEN, freehand=False)
    assert err.message == NO_FREEHAND
    # A certified measure never needs the grant.
    certified = parse_spec({"model_version_id": "v1", "connection_id": "c1",
                            "measure": {"ref": "measure:total_projects"}})[0]
    assert SpecValidator(snapshot).validate(certified).certified is True


def test_fields_must_be_in_the_model_and_visible(snapshot: ModelSnapshot) -> None:
    _refused(snapshot, _spec(snapshot, entity="budgets"))
    _refused(snapshot, _spec(snapshot, {"field": "salary", "op": ">", "value": 1}))
    err = _refused(snapshot, _spec(snapshot, {"field": "approved_budget_value_usd", "op": ">", "value": 1}),
                   ErrorCode.FORBIDDEN, hidden=("projects.approved_budget_value_usd",))
    assert "budget" not in err.message.lower()  # the refusal never names the hidden field
    _refused(snapshot, _spec(snapshot, agg="sum", field="approved_budget_value_usd"), ErrorCode.FORBIDDEN,
             hidden=("projects.approved_budget_value_usd",))


def test_limits_on_fields_nesting_and_alternatives(snapshot: ModelSnapshot) -> None:
    many = [k for k in tpg_synthetic.PROJECT_COLUMNS if k.endswith(("_kpi", "_usd"))][:9]
    _refused(snapshot, _spec(snapshot, {"all": [{"field": k, "op": "not_null"} for k in many]}))
    deep: dict[str, Any] = {"field": "cio", "op": "not_null"}
    for _ in range(4):
        deep = {"all": [deep]}
    _refused(snapshot, _spec(snapshot, deep))
    _refused(snapshot, _spec(snapshot, {"any": [{"field": "cio", "op": "=", "value": f"Person {i}"}
                                                for i in range(13)]}))


def test_plain_comparisons_only_and_values_of_the_right_type(snapshot: ModelSnapshot) -> None:
    calc = {"of": {"calculator": "pmo_health_v1", "output": "status"}, "op": "=", "value": "Red"}
    _refused(snapshot, _spec(snapshot, calc))
    _refused(snapshot, _spec(snapshot, {"field": "percent_complete", "op": ">", "value": "half"}))
    _refused(snapshot, _spec(snapshot, {"field": "planned_deployment_date", "op": "<", "value": 5}))
    _refused(snapshot, _spec(snapshot, {"field": "planned_deployment_date", "op": "<", "value": "soon"}))
    _refused(snapshot, _spec(snapshot, {"field": "cio", "op": "<", "value": {"today": True}}))
    _refused(snapshot, _spec(snapshot, {"field": "cio", "op": "=", "other_field": "percent_complete"}))
    _refused(snapshot, _spec(snapshot, {"field": "planned_deployment_date", "op": "in", "values": ["2026-01-01"]}))
    _refused(snapshot, _spec(snapshot, agg="avg", field="cio"))


def test_no_joins_beyond_the_models_links(snapshot: ModelSnapshot) -> None:
    linked = _spec(snapshot, {"field": "project_requests.project_request_id", "op": "not_null"})
    assert "project_requests" in _validate(snapshot, linked).entities
    unlinked = snapshot.model_copy(update={"links": []})
    _refused(unlinked, linked)


# --------------------------------------------------------------------------- results


def _python(rule: Any, today: date = TODAY) -> int:
    return sum(1 for p in PROJECTS if rule(p, today))


def test_the_problem_question(db: duckdb.DuckDBPyConnection, snapshot: ModelSnapshot) -> None:
    vs = _validate(snapshot, _spec(snapshot, PROBLEM))
    assert vs.certified is False and vs.today == TODAY
    assert vs.measures[0].status == "adhoc" and vs.measures[0].plain_rule == "Rule in words"

    def rule(p: dict[str, Any], today: date) -> bool:
        started = any(p[k] is not None for k in ("actual_project_start_date", "on_hold_date",
                                                 "first_baselined_deployment_date"))
        d = p["planned_deployment_date"]
        return started and (d is None or d < today)

    want = _python(rule)
    assert _run(db, vs) == [(want,)]
    # Today moves the answer; the reporting date does not stand in for it.
    assert want != _python(rule, AS_OF)
    assert _run(db, _validate(snapshot, _spec(snapshot, PROBLEM), today=AS_OF)) == [(_python(rule, AS_OF),)]


def test_breakdown_sum_other_field_and_literal_dates(db: duckdb.DuckDBPyConnection, snapshot: ModelSnapshot) -> None:
    where = {"all": [{"field": "cost_total_usd", "op": ">", "other_field": "approved_budget_value_usd"},
                     {"field": "planned_deployment_date", "op": ">=", "value": "2026-07-01"},
                     {"not": {"field": "current_state", "op": "in", "values": ["Cancelled", "Closed"]}}]}
    vs = _validate(snapshot, _spec(snapshot, where, agg="sum", field="approved_budget_value_usd",
                                   breakdowns=[{"ref": "dimension:region"}]))
    assert vs.today is None  # no today in the rule: the cache key is unchanged
    got = {r[0]: float(r[1]) for r in _run(db, vs)}
    want: dict[str, float] = {}
    for p in PROJECTS:
        d, cost, budget = p["planned_deployment_date"], p["cost_total_usd"], p["approved_budget_value_usd"]
        if cost is not None and budget is not None and cost > budget and d is not None \
                and d >= date(2026, 7, 1) and p["current_state"] not in ("Cancelled", "Closed"):
            want[p["region"]] = want.get(p["region"], 0.0) + float(budget)
    assert got == pytest.approx(want)


def test_today_with_an_offset_and_in_a_certified_measure(db: duckdb.DuckDBPyConnection,
                                                         snapshot: ModelSnapshot) -> None:
    soon = {"all": [{"field": "planned_deployment_date", "op": ">=", "value": {"today": True}},
                    {"field": "planned_deployment_date", "op": "<=", "value": {"today": True, "offset_days": 30}}]}
    want = sum(1 for p in PROJECTS if p["planned_deployment_date"] is not None
               and TODAY <= p["planned_deployment_date"] <= TODAY + timedelta(days=30))
    assert _run(db, _validate(snapshot, _spec(snapshot, soon))) == [(want,)]

    # The same rule as a certified measure of the model reads today too.
    certified = Measure.model_validate({"key": "due_in_30", "label": "Due in 30 days", "entity": "projects",
                                        "plain_rule": "Planned within 30 days of today",
                                        "expression": {"agg": "count", "where": soon}})
    snap = snapshot.model_copy(update={"measures": [*snapshot.measures, certified]})
    spec = parse_spec({"model_version_id": "v1", "connection_id": "c1", "measure": {"ref": "measure:due_in_30"}})[0]
    vs = pin_dates(SpecValidator(snap).validate(spec), as_of=AS_OF, today=TODAY)
    assert vs.certified is True and vs.today == TODAY
    assert _run(db, vs) == [(want,)]
    unpinned = SpecValidator(snap).validate(spec)
    with pytest.raises(TelaError, match="Today was not fixed"):
        SqlCompiler("duckdb").compile(unpinned, as_of=AS_OF)


def test_pin_today_is_a_shift_of_the_reporting_date() -> None:
    cond = Compare(field="d", op="<", value=Today(today=True, offset_days=-2))
    assert pin_today(cond, 12) == Compare(field="d", op="<", value=AsOf(asof_offset_days=10))
    assert pin_today(cond, 0).value == AsOf(asof_offset_days=-2)


def test_other_dialects_compile_the_rule(snapshot: ModelSnapshot) -> None:
    vs = _validate(snapshot, _spec(snapshot, PROBLEM, breakdowns=[{"ref": "dimension:region"}]))
    for dialect in ("postgres", "mysql", "tsql"):
        sql = SqlCompiler(dialect).compile(vs, as_of=AS_OF).sql()
        assert "OR" in sql and "IS NULL" in sql, dialect
    statement = mongo.MongoCompiler().compile(vs, as_of=AS_OF)
    assert statement.entities
    webapi = dataverse.WebApiCompiler({}, {}, "odata").compile(vs, as_of=AS_OF)
    assert webapi is not None
