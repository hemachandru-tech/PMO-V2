"""MongoDB adapter and pipeline guard (US-112; TEST-015, TEST-084; RULE-001, RULE-006).

Guard units run everywhere; the adapter suite needs ``TELA_TEST_MONGO_URL`` (scripts/conformance.sh up).
"""

from __future__ import annotations

from typing import Any

import pytest
from bson import json_util
from sources import RO_PASSWORD, RW_PASSWORD, seed_mongo, url

from tela_common.errors import ErrorCode, TelaError
from tela_runner.adapters.base import ConnectionInfo, Limits
from tela_runner.mongo_guard import check_pipeline

ALLOWED = {"tpg.projects", "tpg.project_requests", "tpg.projects#milestones"}


def stmt(pipeline: list[dict[str, Any]], collection: str = "tpg.projects", columns: list[str] | None = None,
         empty_row: list[Any] | None = None) -> str:
    return str(json_util.dumps({"collection": collection, "pipeline": pipeline, "columns": columns or ["n"],
                                "empty_row": empty_row}))


SCOPE = {"$match": {"cio": {"$in": ["Nomsa Osei"]}}}
REFUSED = {
    "out": [SCOPE, {"$out": "stolen"}],
    "merge": [SCOPE, {"$merge": {"into": "x"}}],
    "where in match": [SCOPE, {"$match": {"$where": "sleep(1000)"}}],
    "function in expr": [SCOPE, {"$addFields": {"x": {"$function": {"body": "1", "args": [], "lang": "js"}}}}],
    "accumulator": [SCOPE, {"$group": {"_id": None, "x": {"$accumulator": {}}}}],
    "scope not first": [{"$group": {"_id": None, "n": {"$sum": 1}}}],
    "lookup without pipeline": [SCOPE, {"$lookup": {"from": "project_requests", "localField": "a",
                                                     "foreignField": "b", "as": "j"}}],
    "lookup to ungoverned": [SCOPE, {"$lookup": {"from": "users", "pipeline": [{"$match": {}}], "as": "j"}}],
    "lookup across databases": [SCOPE, {"$lookup": {"from": {"db": "admin", "coll": "system.users"},
                                                    "pipeline": [{"$match": {}}], "as": "j"}}],
    "nested pipeline unscoped": [SCOPE, {"$lookup": {"from": "project_requests",
                                                     "pipeline": [{"$project": {"a": 1}}], "as": "j"}}],
    "nested out": [SCOPE, {"$lookup": {"from": "project_requests",
                                       "pipeline": [{"$match": {}}, {"$out": "x"}], "as": "j"}}],
    "union": [SCOPE, {"$unionWith": "users"}],
    "graph lookup": [SCOPE, {"$graphLookup": {"from": "projects"}}],
    "facet unscoped": [SCOPE, {"$facet": {"a": [{"$count": "n"}]}}],
    "facet out": [SCOPE, {"$facet": {"a": [{"$match": {}}, {"$merge": "x"}]}}],
    "current op": [{"$currentOp": {}}],
    "collStats": [SCOPE, {"$collStats": {}}],
    "unknown stage": [SCOPE, {"$densify": {}}],
    "two keys": [SCOPE, {"$match": {}, "$limit": 1}],
}


@pytest.mark.parametrize("label", sorted(REFUSED))
def test_guard_refuses(label: str) -> None:
    with pytest.raises(TelaError) as err:
        check_pipeline(stmt(REFUSED[label]), ALLOWED)
    assert err.value.code.startswith("sql_"), label


def test_guard_refuses_other_collections_and_junk() -> None:
    for bad in (stmt([SCOPE], "tpg.users"), stmt([SCOPE], "admin.system.users"), "not json", "[]",
                json_util.dumps({"collection": "tpg.projects", "pipeline": [SCOPE]})):
        with pytest.raises(TelaError):
            check_pipeline(bad, ALLOWED)


def test_guard_accepts_compiled_shapes() -> None:
    ok = [SCOPE, {"$project": {"cio": 1, "region": 1}},
          {"$lookup": {"from": "project_requests", "localField": "project_request_id",
                       "foreignField": "project_request_id", "pipeline": [{"$match": {}}, {"$project": {"a": 1}}],
                       "as": "__j1"}},
          {"$unwind": {"path": "$__j1", "preserveNullAndEmptyArrays": True}},
          {"$match": {"region": {"$in": ["KSA", "$where"]}}},  # a value that looks like an operator is data
          {"$group": {"_id": {"b0": "$region"}, "n": {"$sum": 1}}}, {"$sort": {"n": -1}}, {"$limit": 10001}]
    assert check_pipeline(stmt(ok), ALLOWED)["collection"] == "tpg.projects"


# ----------------------------------------------------------------------------- live adapter


@pytest.fixture(scope="module")
def admin() -> dict[str, Any]:
    a = url("mongo")
    if a is None:
        pytest.skip("TELA_TEST_MONGO_URL not set")
    seed_mongo(a)
    return a


def info(admin: dict[str, Any], user: str = "tela_ro", password: str = RO_PASSWORD) -> ConnectionInfo:
    return ConnectionInfo("01900000-0000-7000-8000-0000000000f1", "mongodb",
                          {"uri": f"mongodb://{admin['host']}:{admin['port']}/?authSource=tpg", "database": "tpg",
                           "tls": {"mode": "disable"}, "read_preference": "primary"},
                          {"username": user, "password": password})


@pytest.fixture
async def adapter() -> Any:
    from tela_runner.adapters.mongodb import MongoAdapter

    a = MongoAdapter(allow_insecure_tls=True)
    yield a
    await a.close()


@pytest.mark.integration
async def test_read_only_verdicts(adapter: Any, admin: dict[str, Any]) -> None:
    safe = await adapter.verify_read_only(info(admin), force=True)
    assert safe.safe, safe.reasons
    rw = await adapter.verify_read_only(info(admin, "tela_rw", RW_PASSWORD), force=True)
    assert not rw.safe and any("insert on tpg." in r for r in rw.reasons), rw.reasons
    dba = await adapter.verify_read_only(info(admin, "tela_dba_rw", RW_PASSWORD), force=True)
    assert not dba.safe and any("createIndex" in r or "dropCollection" in r for r in dba.reasons), dba.reasons
    bad = await adapter.test_connection(info(admin, "tela_ro", "wrong"))
    assert bad.reachable and not bad.authenticated
    down = await adapter.test_connection(ConnectionInfo("c", "mongodb", {"uri": "mongodb://127.0.0.1:1",
                                                                          "database": "tpg",
                                                                          "tls": {"mode": "disable"}}, {}))
    assert not down.reachable
    with pytest.raises(TelaError):
        adapter._options(ConnectionInfo("c", "mongodb", {"uri": "mongodb://u:p@h/", "database": "tpg"}, {}))


@pytest.mark.integration
async def test_execution_caps_timeout_and_empty_row(adapter: Any, admin: dict[str, Any]) -> None:
    ro = info(admin)
    count = stmt([{"$match": {"status": "Active"}}, {"$count": "n"}])
    assert (await adapter.execute(ro, count, {}, Limits(), "m1")).rows == [[65]]
    none = stmt([{"$match": {"status": "Nope"}}, {"$count": "n"}], empty_row=[0])
    assert (await adapter.execute(ro, none, {}, Limits(), "m2")).rows == [[0]]
    ids = stmt([{"$match": {}}, {"$project": {"_id": 0, "project_id": 1}}], columns=["project_id"])
    over = await adapter.execute(ro, ids, {}, Limits(max_rows=40), "m3")
    assert len(over.rows) == 40 and over.truncated
    with pytest.raises(TelaError) as err:
        await adapter.execute(ro, stmt([{"$match": {}}], columns=["name", "region", "cio", "planned_deployment_date"]),
                              {}, Limits(max_bytes=500), "m4")
    assert err.value.code == ErrorCode.PAYLOAD_TOO_LARGE
    slow = stmt([{"$match": {}}, {"$addFields": {"x": {"$reduce": {"input": {"$range": [0, 3_000_000]},
                                                                   "initialValue": 0,
                                                                   "in": {"$add": ["$$value", "$$this"]}}}}},
                 {"$group": {"_id": None, "n": {"$sum": "$x"}}}])
    with pytest.raises(TelaError) as err:
        await adapter.execute(ro, slow, {}, Limits(timeout_ms=200), "m5")
    assert err.value.code == ErrorCode.QUERY_TIMEOUT
    typed = await adapter.execute(ro, stmt([{"$match": {"status": "Active"}}, {"$group": {
        "_id": None, "d": {"$max": "$planned_deployment_date"}, "s": {"$sum": "$approved_budget_value_usd"}}}],
        columns=["d", "s"]), {}, Limits(), "m6")
    assert isinstance(typed.rows[0][0], str) and len(typed.rows[0][0]) == 10 and isinstance(typed.rows[0][1], int)


@pytest.mark.integration
async def test_sampled_catalogue(adapter: Any, admin: dict[str, Any]) -> None:
    catalog = await adapter.introspect(info(admin))
    by = {o.qualified_name: o for o in catalog.objects}
    assert {"tpg.projects", "tpg.project_requests", "tpg.projects#milestones"} <= set(by)
    projects = {c["name"]: c for c in by["tpg.projects"].columns}
    assert projects["planned_deployment_date"]["type"] == "date"
    assert projects["approved_budget_value_usd"]["type"] == "decimal"
    assert projects["team.sponsor"]["type"] == "string" and "milestones" not in projects
    assert projects["actual_deployment_date"]["nullable"] and projects["actual_deployment_date"]["presence"] < 1
    assert by["tpg.projects"].primary_key == ["_id"]
    child = by["tpg.projects#milestones"]
    assert child.primary_key == ["_parent_id", "_index"] and {"name", "planned", "done"} <= {
        c["name"] for c in child.columns}
    assert "Project 1" not in str(catalog)  # sampled values are never returned


@pytest.mark.integration
def test_the_source_itself_refuses_writes_from_the_runtime_identity(admin: dict[str, Any]) -> None:
    """NFR-001 / TEST-084: the read-role account cannot insert, update, drop or run $out, guards aside."""
    from pymongo import MongoClient
    from pymongo.errors import OperationFailure

    client = MongoClient(host=admin["host"], port=admin["port"], username="tela_ro", password=RO_PASSWORD,
                         authSource="tpg")
    db = client["tpg"]
    try:
        for attempt in (lambda: db.projects.insert_one({"project_id": "W1"}),
                        lambda: db.projects.update_many({}, {"$set": {"name": "x"}}),
                        lambda: db.projects.drop_indexes(),
                        lambda: list(db.projects.aggregate([{"$match": {}}, {"$out": "stolen"}]))):
            with pytest.raises(OperationFailure) as err:
                attempt()
            assert err.value.code == 13  # Unauthorized
    finally:
        client.close()
    assert "stolen" not in MongoClient(host=admin["host"], port=admin["port"], username=admin["user"],
                                       password=admin["password"])["tpg"].list_collection_names()
