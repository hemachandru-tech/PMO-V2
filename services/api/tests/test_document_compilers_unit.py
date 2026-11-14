"""MongoDB and Dataverse Web API compilers without a source (US-112, US-111): every PMO pack shape compiles,
the independent scope checks accept what the compilers emit and refuse tampered statements, and the Web API
reports Unavailable instead of approximating."""

from __future__ import annotations

import json
from typing import Any

import pytest
from bson import json_util
from test_dialect_parity import AS_OF, TODAY, cases, dataverse_snapshot, scope, snapshot

from tela_api.query.asof import pin_dates
from tela_api.query.compilers import compile_spec, field_query, records_compiler
from tela_api.query.dataverse import WebApiCompiled, assert_webapi_scoped
from tela_api.query.mongo import MongoCompiled, assert_mongo_scoped
from tela_api.query.validator import FieldRef, SpecValidator
from tela_common.errors import ErrorCode, TelaError
from tela_query_spec.expr import uses_extended
from tela_query_spec.spec import parse_spec
from tela_runner.mongo_guard import check_pipeline
from tela_runner.webapi_guard import check_webapi

MONGO = snapshot("tpg")
DV = dataverse_snapshot()
CHOICES = {"dbo.tpg_project": {
    "statecode": {"name": "statecode", "type": "int", "options": {"0": "Active", "1": "Inactive"}},
    "statecodename": {"name": "statecodename", "type": "nvarchar", "virtual_of": "statecode",
                      "options": {"0": "Active", "1": "Inactive"}},
    "statuscodename": {"name": "statuscodename", "type": "nvarchar", "virtual_of": "statuscode",
                       "options": {"1": "Project Created", "2": "Active", "3": "On Hold", "4": "Completed"}},
    "tpg_projectid": {"name": "tpg_projectid", "type": "uniqueidentifier"},
    "tpgmea_planneddeploymentdate": {"name": "tpgmea_planneddeploymentdate", "type": "date"},
    "modifiedon": {"name": "modifiedon", "type": "datetime"}}}


def vs(snap: Any, body: dict[str, Any]) -> Any:
    spec, _ = parse_spec({"model_version_id": "v", "connection_id": "c", **body})
    # Free-hand access so the shared cases' ad-hoc rules (US-163) compile too; ``today`` pinned like the service.
    return pin_dates(SpecValidator(snap, freehand=True).validate(spec, connection_id="c"), as_of=AS_OF, today=TODAY)


def webapi(fmt: str) -> dict[str, Any]:
    return {"engine": "dataverse", "descriptor": {"mode": "webapi", "webapi_format": fmt}, "_columns": CHOICES}


@pytest.mark.parametrize("cio", [None, "Nomsa Osei"])
def test_every_shape_compiles_to_a_scoped_guarded_pipeline(cio: str | None) -> None:
    sc = scope(MONGO, cio)
    allowed = {e.source_object.lower() for e in MONGO.entities}
    for label, body, _ in cases(MONGO):
        v = vs(MONGO, body)
        # US-156 rules and US-178's grained compare: SQL engines only
        if any(uses_extended(m.expression) for m in v.measures) or body.get("compare"):
            with pytest.raises(TelaError) as err:
                compile_spec("mongodb", v, as_of=AS_OF)
            assert err.value.code == ErrorCode.MEASURE_UNAVAILABLE, label
            continue
        compiled = compile_spec("mongodb", v, as_of=AS_OF)
        assert isinstance(compiled, MongoCompiled), label
        statement = compiled.render(MONGO, sc, body.get("limit") or 10_001)
        assert_mongo_scoped(statement, MONGO, sc)
        check_pipeline(statement, allowed)
        scoped = cio is not None and compiled.root.key == "projects"
        assert json_util.loads(statement)["pipeline"][0] == {"$match": {"$and": [
            {"cio": {"$in": ["Nomsa Osei", "Karim Haddad"]}}]} if scoped else {}}, label


def test_mongo_records_values_and_tampering() -> None:
    sc = scope(MONGO, "Nomsa Osei")
    v = vs(MONGO, {"measure": {"ref": "measure:open_projects"}, "breakdowns": [{"ref": "dimension:region"}]})
    rc = records_compiler("mongodb")
    page = rc.compile_records(v, marks={"region": "KSA"}, as_of=AS_OF, fields=["project_id", "name"], limit=5,
                              offset=10)
    count = rc.compile_records(v, marks={"region": None}, as_of=AS_OF, fields=[], limit=1, offset=0, count_only=True)
    ent = MONGO.entity("projects")
    assert ent is not None
    for q in (page, count, field_query("mongodb", MONGO, FieldRef("projects", ent.field("region"), "R"), "distinct",
                                       limit=20),
              field_query("mongodb", MONGO, FieldRef("projects", ent.field("modified_on"), "M"), "max"),
              field_query("mongodb", MONGO, FieldRef("projects", ent.field("project_id"), "P"), "count"),
              # The field profile AG-03 reads (US-175).
              field_query("mongodb", MONGO, FieldRef("projects", ent.field("region"), "R"), "filled"),
              field_query("mongodb", MONGO, FieldRef("projects", ent.field("modified_on"), "M"), "min"),
              field_query("mongodb", MONGO, FieldRef("projects", ent.field("region"), "R"), "top", limit=5)):
        assert_mongo_scoped(q.render(MONGO, sc, 50), MONGO, sc)
    top = json_util.loads(field_query("mongodb", MONGO, FieldRef("projects", ent.field("region"), "R"), "top",
                                      limit=5).render(MONGO, sc, 5))["pipeline"]
    assert {"$sort": {"n": -1, "value": 1}} in top and {"$limit": 5} in top
    doc = json_util.loads(page.render(MONGO, sc, 5))
    assert {"$skip": 10} in doc["pipeline"] and {"$limit": 5} in doc["pipeline"]
    doc["pipeline"][0] = {"$match": {}}  # the scope removed
    with pytest.raises(TelaError) as err:
        assert_mongo_scoped(json_util.dumps(doc), MONGO, sc)
    assert err.value.code == ErrorCode.SCOPE_REWRITE_FAILED
    fan = compile_spec("mongodb", vs(MONGO, {"measure": {"ref": "measure:total_requests"},
                                            "breakdowns": [{"ref": "field:projects.region"}]}), as_of=AS_OF)
    doc = json_util.loads(fan.render(MONGO, sc, 10))
    lookup = next(st for st in doc["pipeline"] if "$lookup" in st)
    lookup["$lookup"]["pipeline"][0] = {"$match": {}}  # the linked collection's scope removed
    with pytest.raises(TelaError):
        assert_mongo_scoped(json_util.dumps(doc), MONGO, sc)
    with pytest.raises(TelaError):
        assert_mongo_scoped(json_util.dumps({"collection": "tpg.users", "pipeline": [{"$match": {}}]}), MONGO, sc)


@pytest.mark.parametrize("fmt", ["odata", "fetchxml"])
def test_webapi_shapes_compile_or_are_unavailable(fmt: str) -> None:
    sc = scope(DV, "Nomsa Osei")
    ran = unavailable = 0
    for label, body, _ in cases(MONGO):
        try:
            compiled = compile_spec(webapi(fmt), vs(DV, body), as_of=AS_OF)
        except TelaError as exc:
            assert exc.code in (ErrorCode.SPEC_INVALID, ErrorCode.MEASURE_UNAVAILABLE), label
            unavailable += exc.code == ErrorCode.MEASURE_UNAVAILABLE
            continue
        assert isinstance(compiled, WebApiCompiled)
        statement = compiled.render(DV, sc, body.get("limit") or 10_001)
        assert_webapi_scoped(statement, DV, sc, CHOICES)
        check_webapi(statement, {e.source_object.lower() for e in DV.entities})
        ran += 1
    assert ran >= 30 and unavailable > 0


def test_webapi_choice_labels_null_guards_and_tampering() -> None:
    sc = scope(DV)
    open_ = compile_spec(webapi("odata"), vs(DV, {"measure": {"ref": "measure:open_projects"},
                                                  "breakdowns": [{"ref": "dimension:current_state"}]}), as_of=AS_OF)
    st = json.loads(open_.render(DV, sc, 10))
    apply = st["query"]["$apply"]
    assert apply.startswith("filter(tpg_projectid ne null)/")  # RULE-026 on the current state (US-202, DEC-24)
    assert "filter((statuscode eq 1 or statuscode eq 2 or statuscode eq 3))" in apply
    assert "groupby((statuscode)" in apply and st["labels"]["current_state"]["3"] == "On Hold"
    assert st["groups_query"] and st["group_fill"] == [0]  # groups without open projects still show 0
    v = vs(DV, {"measure": {"ref": "measure:total_projects"},
                "filters": [{"ref": "dimension:region", "op": "!=", "values": ["KSA"]}]})
    odata = json.loads(compile_spec(webapi("odata"), v, as_of=AS_OF).render(DV, sc, 10))["query"]["$apply"]
    assert "tpgmea_region ne 'KSA' and tpgmea_region ne null" in odata  # SQL: != never matches NULL
    fetch = json.loads(compile_spec(webapi("fetchxml"), v, as_of=AS_OF).render(DV, sc, 10))["query"]["fetchXml"]
    assert 'operator="not-null"' in fetch and fetch.startswith('<fetch aggregate="true">')
    rc = records_compiler(webapi("odata"))
    page = rc.compile_records(vs(DV, {"measure": {"ref": "measure:open_projects"},
                                      "breakdowns": [{"ref": "dimension:region"}]}),
                              marks={"region": "KSA"}, as_of=AS_OF, fields=["project_id", "status"], limit=5, offset=5)
    rec = json.loads(page.render(DV, sc, 5))
    assert rec["query"]["$filter"].startswith("(tpg_projectid ne null)") and rec["query"]["$top"] == "10"
    rec["query"]["$filter"] = "(tpgmea_region eq 'KSA')"  # the scope removed
    with pytest.raises(TelaError):
        assert_webapi_scoped(json.dumps(rec), DV, sc, CHOICES)
    region = DV.entity("projects").field("region")  # type: ignore[union-attr]
    planned = DV.entity("projects").field("planned_deployment_date")  # type: ignore[union-attr]
    for fmt in ("odata", "fetchxml"):  # the field profile AG-03 reads (US-175)
        for q in (field_query(webapi(fmt), DV, FieldRef("projects", region, "Region"), "top", limit=5),
                  field_query(webapi(fmt), DV, FieldRef("projects", planned, "Planned"), "min"),
                  field_query(webapi(fmt), DV, FieldRef("projects", region, "Region"), "filled")):
            statement = q.render(DV, sc, 5)
            assert_webapi_scoped(statement, DV, sc, CHOICES)
            check_webapi(statement, {e.source_object.lower() for e in DV.entities})
    top = json.loads(field_query(webapi("odata"), DV, FieldRef("projects", region, "Region"), "top", limit=5)
                     .render(DV, sc, 5))
    assert "groupby((tpgmea_region),aggregate($count as n))" in top["query"]["$apply"]
    assert top["post"] == {"sort": [["n", True], ["value", False]], "skip": 0, "top": 5}
    filled = json.loads(field_query(webapi("odata"), DV, FieldRef("projects", region, "Region"), "filled")
                        .render(DV, sc, 5))
    assert "tpgmea_region ne null" in json.dumps(filled["query"])
    with pytest.raises(ValueError, match="nonsense"):
        field_query(webapi("odata"), DV, FieldRef("projects", region, "Region"), "nonsense")
    with pytest.raises(TelaError) as err:
        compile_spec(webapi("odata"), vs(DV, {"measure": {"ref": "measure:open_projects"}, "time": {
            "ref": "dimension:planned_deployment_date", "grain": "month"}}), as_of=AS_OF)
    assert err.value.code == ErrorCode.MEASURE_UNAVAILABLE
