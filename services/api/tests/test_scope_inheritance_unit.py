"""Scope through a parent link (US-155; RULE-004, RULE-005), no database server.

A project's PCRs, status reports, risks … have no CIO or region of their own: the pack's links mark them
``inherits_scope``, so a row is visible only when its project is in the person's scope. The scope injector writes
that as a semi-join on the project table wrapped in the project's own scope; the independent verifier checks the
nested reference like any other governed table, and fails closed on tampering.
"""
# ruff: noqa: F811  (the pack 2.0 fixtures are imported, then requested by name)

from __future__ import annotations

from typing import Any

import duckdb
import pmo_oracle as oracle
import pytest
from test_pmo_catalogue_unit import AS_OF
from test_starter_pack2_unit import db, definition, manifest, snapshot  # noqa: F401  (shared fixtures)

from tela_api.identity.scope import ResolvedScope, combine_policies
from tela_api.query.compiler import SqlCompiler
from tela_api.query.mongo import scope_query
from tela_api.query.scope_injector import assert_scoped, inject
from tela_api.query.validator import SpecValidator
from tela_common.errors import ErrorCode, TelaError
from tela_query_spec.model import ModelLink, ModelSnapshot
from tela_query_spec.spec import parse_spec

CIO = [{"field": "cio", "op": "=", "attr": "cio"}]


def _pol(entity: str, preds: list[dict[str, Any]] | None = None, role: str = "cio") -> dict[str, Any]:
    return {"role_key": role, "entity_key": entity, "all_rows": preds is None, "predicate": preds or [],
            "description": f"{role} on {entity}"}


def _scope(snap: ModelSnapshot, policies: list[dict[str, Any]], cio: str = "Nomsa Osei") -> ResolvedScope:
    return combine_policies(connection_id="c1", policies=policies, field_rules=[],
                            scope_attributes={"cio": {"cio": [cio]}},
                            links=[ln.model_dump() for ln in snap.links])


def _run(db: duckdb.DuckDBPyConnection, snap: ModelSnapshot, scope: ResolvedScope, measure: str,
         **kw: Any) -> tuple[list[tuple[Any, ...]], str]:
    spec = parse_spec({"model_version_id": "v1", "connection_id": "c1", "measure": {"ref": f"measure:{measure}"},
                       **kw})[0]
    vs = SpecValidator(snap, hidden_fields=scope.hidden_fields).validate(spec)
    cq = SqlCompiler("duckdb").compile(vs, as_of=AS_OF)
    tree = inject(cq.tree, snap, scope, cq.params)
    pg = tree.sql(dialect="postgres")
    assert_scoped(pg, "postgres", snap, scope, cq.params.values)
    return db.execute(tree.sql(dialect="duckdb"), cq.params.values).fetchall(), pg


def _own(cio: str, table: str, keep: Any = lambda r: True) -> int:
    projects = {p["project_id"] for p in oracle.PROJECTS if p["cio"] == cio}
    return sum(r["project_id"] in projects and keep(r) for r in oracle.ACTIVITY[table])


def test_the_pack_marks_every_activity_link(snapshot: ModelSnapshot) -> None:
    passing = {ln.from_entity for ln in snapshot.links if ln.inherits_scope}
    assert passing == {"change_requests", "status_reports", "risks", "issues", "action_items", "decisions",
                       "timesheets", "project_tasks"}
    with pytest.raises(ValueError):
        ModelLink(from_entity="a", to_entity="b", from_fields=["x"], to_fields=["y"], cardinality="one_to_many",
                  inherits_scope=True)


def test_a_cio_sees_the_activity_of_own_projects_only(db: duckdb.DuckDBPyConnection, snapshot: ModelSnapshot) -> None:
    for cio in ("Nomsa Osei", "Karim Haddad"):
        scope = _scope(snapshot, [_pol("projects", CIO)], cio)
        assert scope.for_entity("risks").via is not None
        rows, sql = _run(db, snapshot, scope, "total_pcrs")
        assert rows[0][0] == _own(cio, "change_requests") > 0
        assert 'IN (SELECT "project_id" FROM main.projects WHERE "cio" = ' in sql
        rows, _ = _run(db, snapshot, scope, "open_risks")
        assert rows[0][0] == _own(cio, "risks", lambda r: r["state"] != "Closed")
    # Joined to the project for a breakdown, both references stay scoped.
    scope = _scope(snapshot, [_pol("projects", CIO)])
    rows, _ = _run(db, snapshot, scope, "hours_logged", breakdowns=[{"ref": "dimension:cio"}])
    assert {r[0] for r in rows} == {"Nomsa Osei"}


def test_all_rows_on_the_parent_passes_all_rows_on(db: duckdb.DuckDBPyConnection, snapshot: ModelSnapshot) -> None:
    scope = _scope(snapshot, [_pol("projects", role="admin")])
    risks = scope.for_entity("risks")
    assert risks.all_rows and risks.via is None and "through its projects" in risks.descriptions
    rows, _ = _run(db, snapshot, scope, "total_pcrs")
    assert rows[0][0] == oracle.CATALOGUE["total_pcrs"]


def test_own_policy_and_parent_scope_both_apply(db: duckdb.DuckDBPyConnection, snapshot: ModelSnapshot) -> None:
    scope = _scope(snapshot, [_pol("projects", CIO),
                              _pol("risks", [{"field": "category", "op": "in", "values": ["Schedule"]}])])
    rows, _ = _run(db, snapshot, scope, "open_risks")
    assert rows[0][0] == _own("Nomsa Osei", "risks", lambda r: r["state"] != "Closed" and r["category"] == "Schedule")


def test_no_scope_on_the_parent_denies_the_child(snapshot: ModelSnapshot) -> None:
    scope = _scope(snapshot, [_pol("project_requests", CIO), _pol("risks", role="admin")])
    for entity in ("risks", "change_requests"):
        with pytest.raises(TelaError) as err:
            scope.for_entity(entity)
        assert err.value.code == ErrorCode.SCOPE_UNRESOLVED
    # A cycle (or a chain deeper than three) fails closed too.
    loop = [{"from_entity": "a", "to_entity": "b", "from_fields": ["x"], "to_fields": ["y"],
             "cardinality": "many_to_one", "inherits_scope": True},
            {"from_entity": "b", "to_entity": "a", "from_fields": ["y"], "to_fields": ["x"],
             "cardinality": "many_to_one", "inherits_scope": True}]
    looped = combine_policies(connection_id="c1", policies=[_pol("a", role="admin"), _pol("b", role="admin")],
                              field_rules=[], scope_attributes={}, links=loop)
    assert looped.entities == {}


def test_the_verifier_catches_a_dropped_or_widened_parent_scope(db: duckdb.DuckDBPyConnection,
                                                                snapshot: ModelSnapshot) -> None:
    scope = _scope(snapshot, [_pol("projects", CIO)])
    spec = parse_spec({"model_version_id": "v1", "connection_id": "c1", "measure": {"ref": "measure:total_pcrs"}})[0]
    cq = SqlCompiler("postgres").compile(SpecValidator(snapshot).validate(spec), as_of=AS_OF)
    sql = inject(cq.tree, snapshot, scope, cq.params).sql(dialect="postgres")
    params = cq.params.values
    assert_scoped(sql, "postgres", snapshot, scope, params)
    cond = sql[sql.index('"project_id" IN (SELECT'):sql.index(") AS t0")]
    for tampered in (
        sql.replace(cond, "TRUE"),  # the semi-join removed
        sql.replace('FROM main.projects WHERE "cio" =', 'FROM main.projects WHERE TRUE OR "cio" ='),
        sql.replace('IN (SELECT "project_id" FROM main.projects WHERE', 'IN (SELECT "project_id" FROM main.projects '
                    'AS x JOIN main.risks AS y ON TRUE WHERE'),
    ):
        with pytest.raises(TelaError) as err:
            assert_scoped(tampered, "postgres", snapshot, scope, params)
        assert err.value.code == ErrorCode.SCOPE_REWRITE_FAILED
    # Another CIO's values in the parameters do not pass either.
    with pytest.raises(TelaError):
        assert_scoped(sql, "postgres", snapshot, _scope(snapshot, [_pol("projects", CIO)], "Karim Haddad"), params)


def test_engines_without_a_semi_join_fail_closed(snapshot: ModelSnapshot) -> None:
    scope = _scope(snapshot, [_pol("projects", CIO)])
    risks = snapshot.entity("risks")
    assert risks is not None
    with pytest.raises(TelaError) as err:
        scope_query(risks, scope)
    assert err.value.code == ErrorCode.SCOPE_REWRITE_FAILED
