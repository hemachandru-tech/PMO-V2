"""P3 sources on the running Compose stack (core + dev profiles): the governed read path per engine.

For each sample source (MySQL, SQL Server, an uploaded workbook, MongoDB, and Dataverse fakes) the journey is the one the
PostgreSQL sample takes in ``test_p1_read_path``: create the connection through the API, test it (reachable,
signed in, read-only), read the schema, install and approve the PMO pack, add scope policies, then check the
reference counts for the Admin and the CIO's scoped counts, a drill list and filter values — all through the
real runner, OpenBao credentials and signed envelopes. Idempotent.

    TELA_SMOKE=1 uv run pytest -q tests/smoke/test_p3_sources.py
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest
from test_p1_read_path import Api, _pw, apis  # noqa: F401 - shared fixture

pytestmark = pytest.mark.skipif(
    not __import__("os").environ.get("TELA_SMOKE"), reason="set TELA_SMOKE=1 with the stack running")

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "packs" / "pmo" / "fixtures"))
import tpg_synthetic  # noqa: E402

PROJECTS, REQUESTS = tpg_synthetic.generate()

DATAVERSE = {"engine": "dataverse", "environment_url": "http://dataverse-fake:8099",
             "tenant_id": "00000000-0000-4000-8000-000000000001", "client_id": "00000000-0000-4000-8000-0000000000a1",
             "client_secret": "fake-reader-secret", "tables": ["tpg_project", "tpg_projectrequest"]}

SOURCES: dict[str, dict[str, Any]] = {
    "mysql": {"name": "Sample TPG on MySQL (dev)", "engine": "mysql", "host": "sample-mysql", "database": "tpg",
              "username": "tela_ro", "password_file": "sample_mysql_ro_password", "tls": {"mode": "disable"}},
    "sqlserver": {"name": "Sample TPG on SQL Server (dev)", "engine": "sqlserver", "host": "sample-mssql",
                  "database": "tpg", "username": "tela_ro", "password_file": "sample_mssql_ro_password",
                  "tls": {"trust_server_certificate": True}},
    "excel": {"name": "Sample TPG workbook (dev)", "upload": "TPG extract.xlsx"},
    "mongodb": {"name": "Sample TPG on MongoDB (dev)", "engine": "mongodb",
                "uri": "mongodb://sample-mongo:27017/?authSource=tpg", "database": "tpg", "username": "tela_ro",
                "password_file": "sample_mongo_ro_password", "tls": {"mode": "disable"}, "read_preference": "primary"},
    # The Dataverse fake (DEC-09 pending): Web API mode, and TDS mode through the SQL Server stand-in.
    "dataverse-webapi": {**DATAVERSE, "name": "Sample TPG on Dataverse fake, Web API (dev)", "mode": "webapi"},
    "dataverse-tds": {**DATAVERSE, "name": "Sample TPG on Dataverse fake, TDS (dev)", "mode": "tds",
                      "dev_tds": "sample-mssql,1433", "tds_username": "tela_ro",
                      "tds_password_file": "sample_mssql_ro_password"},
}


def _workbook() -> bytes:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "services" / "connector-runner" / "tests"))
    from sources import tpg_workbook

    return tpg_workbook()


def connect(admin: Api, source: dict[str, Any]) -> dict[str, str]:
    """Create (or reuse) the connection, test, introspect, install the newest PMO pack, approve, add policies."""
    items = admin.call("GET", "/connections").json()["items"]
    conn = next((c for c in items if c["name"] == source["name"]), None)
    if conn is None and "upload" in source:
        resp = admin.call("POST", "/connections/files", data={"name": source["name"]}, files={"file": (
            source["upload"], _workbook(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})
        assert resp.status_code == 201, resp.text
        cid = resp.json()["connection_id"]
    elif conn is None:
        body = {k: v for k, v in source.items() if not k.endswith("_file")}
        if "password_file" in source:
            body["password"] = _pw(source["password_file"])
        if "tds_password_file" in source:
            body["tds_password"] = _pw(source["tds_password_file"])
        resp = admin.call("POST", "/connections", json=body)
        assert resp.status_code == 201, resp.text
        cid = resp.json()["connection_id"]
    else:
        cid = conn["id"]
    test = admin.call("POST", f"/connections/{cid}/test").json()
    assert test["status"] == "connected" and test["read_only"]["verdict"] == "safe", test
    intro = admin.call("POST", f"/connections/{cid}/introspect").json()
    assert intro["status"] == "connected", intro
    models = admin.call("GET", f"/models?connection_id={cid}").json()["items"]
    if not models or not models[0]["current_version_id"]:
        packs = admin.call("GET", "/starter-packs").json()["items"]
        newest = max((p for p in packs if p["pack_key"] == "pmo_tpg"),
                     key=lambda p: tuple(int(x) for x in p["pack_version"].split(".")))
        install = admin.call("POST", f"/starter-packs/{newest['id']}/install",
                             json={"connection_id": cid, "reporting_date": "2026-09-30"})
        assert install.status_code == 202, install.text
        model_id = install.json()["model_id"]
        draft = admin.call("GET", f"/models/{model_id}/draft").json()
        approved = admin.call("POST", f"/models/{model_id}/approve", json={"draft_etag": draft["draft_etag"]})
        assert approved.status_code == 201, approved.text
        version_id = approved.json()["model_version_id"]
    else:
        version_id = models[0]["current_version_id"]
    existing = {(p["entity"], p["role"]) for p in
                admin.call("GET", f"/admin/scope-policies?connection_id={cid}").json()["items"]}
    for entity in ("projects", "project_requests"):
        for role, body in (("admin", {"all_rows": True, "description": "Admin: all rows"}),
                           ("cio", {"predicates": [{"field": "cio", "op": "=", "attr": "cio"}],
                                    "description": "Own CIO"})):
            if (entity, role) not in existing:
                resp = admin.call("POST", "/admin/scope-policies",
                                  json={"connection_id": cid, "entity": entity, "role": role, **body})
                assert resp.status_code == 201, resp.text
    return {"cid": cid, "version_id": version_id}


@pytest.fixture(scope="module", params=sorted(SOURCES))
def world(request: pytest.FixtureRequest, apis: dict[str, Api]) -> dict[str, str]:
    return connect(apis["admin"], SOURCES[request.param])


def _run(api: Api, world: dict[str, str], measure: str, **extra: Any) -> Any:
    spec = {"model_version_id": world["version_id"], "connection_id": world["cid"],
            "measure": {"ref": f"measure:{measure}"}, **extra}
    resp = api.call("POST", "/query/run", json={"spec": spec, "use_cache": False})
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_reference_counts(apis: dict[str, Api], world: dict[str, str]) -> None:
    for measure, expected in (("total_projects", 100), ("open_projects", 65), ("in_delivery", 13),
                              ("on_hold", 3), ("project_created", 49)):
        assert _run(apis["admin"], world, measure)["rows"] == [[expected]], measure


def test_cio_scope_drill_and_filter_values(apis: dict[str, Api], world: dict[str, str]) -> None:
    mine = sum(1 for p in PROJECTS if p["cio"] == "Nomsa Osei" and p["status"] == "Active")
    result = _run(apis["nomsa"], world, "open_projects")
    assert result["rows"] == [[mine]] and result["scope"]["locked"]
    by_region = _run(apis["nomsa"], world, "open_projects", breakdowns=[{"ref": "dimension:region"}])
    expected = {}
    for p in PROJECTS:
        if p["cio"] == "Nomsa Osei" and p["status"] == "Active":
            expected[p["region"]] = expected.get(p["region"], 0) + 1
    assert {r[0]: r[1] for r in by_region["rows"]} == expected
    spec = {"model_version_id": world["version_id"], "connection_id": world["cid"],
            "measure": {"ref": "measure:open_projects"}, "breakdowns": [{"ref": "dimension:region"}]}
    region = by_region["rows"][0][0]
    drill = apis["nomsa"].call("POST", "/query/records", json={"spec": spec, "mark": {"region": region}})
    assert drill.status_code == 200, drill.text
    assert drill.json()["total"] == expected[region]
    values = apis["nomsa"].call("POST", "/query/filter-values", json={
        "connection_id": world["cid"], "model_version_id": world["version_id"], "ref": "dimension:region"})
    assert values.status_code == 200, values.text
    listed = {v["value"] if isinstance(v, dict) else v for v in values.json()["values"]}
    assert set(expected) <= listed
