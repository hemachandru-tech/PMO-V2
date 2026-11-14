"""Dataverse adapter against the fake environment (US-110, US-111; TEST-014, TEST-067, TEST-086, TEST-087).

The fake (tests/fakes/dataverse_fake.py) runs on a local port: Entra client credentials through MSAL (routed
to the fake in development only), the application user's role privileges, EntityDefinitions metadata and
Web API reads with paging and 429 throttling. The TDS path needs ``TELA_TEST_MSSQL_URL`` (a SQL Server
container standing in for the TDS endpoint). Not a substitute for the US-058 spike on a real environment.
"""

from __future__ import annotations

import json
import os
import struct
from typing import Any

import pytest
from sources import DV_WRITER, FakeDataverse, dataverse_info, seed_dataverse_tds, url

from tela_common.errors import ErrorCode, TelaError
from tela_runner.adapters.base import Limits
from tela_runner.adapters.dataverse import SQL_COPT_SS_ACCESS_TOKEN, token_attribute, unsafe_privileges
from tela_runner.webapi_guard import check_webapi

ALLOWED = {"dbo.tpg_project", "dbo.tpg_projectrequest"}


def test_token_is_injected_as_utf16_with_a_length_prefix() -> None:
    attr = token_attribute("eyJ0eXAi.abc")
    raw = attr[SQL_COPT_SS_ACCESS_TOKEN]
    (length,) = struct.unpack("<I", raw[:4])
    assert length == len("eyJ0eXAi.abc") * 2 and raw[4:].decode("utf-16-le") == "eyJ0eXAi.abc"


def test_privilege_check() -> None:
    roles = [{"roleid": "r1", "name": "Tela Reader"}, {"roleid": "r2", "name": "System Administrator"}]
    reasons = unsafe_privileges(roles, {"r1": ["prvReadtpg_project", "prvAppendTotpg_project"], "r2": []})
    assert reasons == ["role Tela Reader grants prvAppendTotpg_project",
                       "application user has the System Administrator role"]
    assert unsafe_privileges([{"roleid": "r1", "name": "R"}], {"r1": ["prvReadtpg_project"]}) == []


def stmt(query: dict[str, str], **extra: Any) -> str:
    return json.dumps({"entity": "dbo.tpg_project", "entity_set": "tpg_projects", "format": "odata", "query": query,
                       "columns": ["n"], **extra})


@pytest.mark.parametrize("bad", [
    stmt({"$apply": "groupby((tpgmea_region))"}),  # scope filter not first
    stmt({"$select": "tpg_title"}),  # no scope filter
    stmt({"$apply": "filter(statecode eq 0)", "$expand": "x"}),
    stmt({"$filter": "(x) and tpgmea_region eq 'KSA'", "$apply": "filter(x)"}),
    stmt({"$filter": "(tpg_projectid ne null)", "$select": "Microsoft.Dynamics.CRM.WhoAmI()"}),
    json.dumps({"entity": "dbo.systemuser", "entity_set": "systemusers", "query": {"$filter": "(x)"}, "columns": []}),
    stmt({"fetchXml": "<fetch><entity name='tpg_project'><link-entity name='systemuser'/><filter/></entity></fetch>"}),
    stmt({"fetchXml": "<fetch><entity name='systemuser'><filter/></entity></fetch>"}),
    stmt({"fetchXml": "<!DOCTYPE x [<!ENTITY a 'b'>]><fetch/>"}),
    stmt({"fetchXml": "<fetch><entity name='tpg_project'><attribute name='a'/></entity></fetch>"}),
    "not json",
])
def test_webapi_guard_refuses(bad: str) -> None:
    with pytest.raises(TelaError):
        check_webapi(bad, ALLOWED)


def test_webapi_guard_accepts_compiled_shapes() -> None:
    check_webapi(stmt({"$apply": "filter(tpg_projectid ne null)/groupby((tpgmea_region),aggregate($count as m0))"}),
                 ALLOWED)
    check_webapi(stmt({"fetchXml": "<fetch aggregate=\"true\"><entity name=\"tpg_project\"><filter type=\"and\">"
                                   "<condition attribute=\"tpg_projectid\" operator=\"not-null\"/></filter>"
                                   "<attribute name=\"tpg_projectid\" aggregate=\"count\" alias=\"m0\"/></entity>"
                                   "</fetch>"}), ALLOWED)


# ----------------------------------------------------------------------------- live fake


@pytest.fixture(scope="module")
def fake() -> Any:
    f = FakeDataverse()
    yield f
    f.stop()


@pytest.fixture
async def adapter(fake: FakeDataverse) -> Any:
    from tela_runner.adapters.dataverse import DataverseAdapter

    os.environ["TELA_DATAVERSE_AUTHORITY_ROUTE"] = fake.url
    a = DataverseAdapter(allow_insecure_tls=True)
    yield a
    await a.close()


@pytest.mark.integration
async def test_read_only_verdicts_and_sign_in(adapter: Any, fake: FakeDataverse) -> None:
    safe = await adapter.verify_read_only(dataverse_info(fake, mode="webapi"), force=True)
    assert safe.safe, safe.reasons
    writer = await adapter.verify_read_only(dataverse_info(fake, mode="webapi", app=DV_WRITER), force=True)
    assert not writer.safe and "role Basic User grants prvWritetpg_project" in writer.reasons
    bad = await adapter.test_connection(dataverse_info(fake, mode="webapi", app=(DV_WRITER[0], "wrong")))
    assert bad.reachable and not bad.authenticated and "Entra ID refused" in (bad.error or "")
    ok = await adapter.test_connection(dataverse_info(fake, mode="webapi"))
    assert ok.reachable and ok.authenticated and ok.read_only.safe and "WEBAPI" in (ok.server_version or "")


@pytest.mark.integration
async def test_tds_off_offers_web_api(adapter: Any, fake: FakeDataverse) -> None:
    result = await adapter.test_connection(dataverse_info(fake, mode="tds"))  # 127.0.0.1:1433 answers nothing
    assert result.extra == {"suggest_mode": "webapi"} and not result.read_only.safe
    assert "Web API mode" in (result.error or "")


@pytest.mark.integration
async def test_metadata_introspection(adapter: Any, fake: FakeDataverse) -> None:
    catalog = await adapter.introspect(dataverse_info(fake, mode="webapi"))
    by = {o.qualified_name: o for o in catalog.objects}
    project = by["dbo.tpg_project"]
    cols = {c["name"]: c for c in project.columns}
    assert project.primary_key == ["tpg_projectid"] and project.kind == "dataverse_table"
    assert cols["tpgmea_planneddeploymentdate"]["type"] == "date" and cols["modifiedon"]["type"] == "datetime"
    assert cols["statecode"]["type"] == "int" and cols["statecodename"]["virtual_of"] == "statecode"
    assert cols["statuscodename"]["options"]["3"] == "On Hold"
    with pytest.raises(TelaError):
        await adapter.introspect(type(dataverse_info(fake))("c", "dataverse", {**dataverse_info(fake).descriptor,
                                                                              "tables": []}, {}))


@pytest.mark.integration
async def test_webapi_reads_paging_labels_groups_and_throttling(adapter: Any, fake: FakeDataverse) -> None:
    info = dataverse_info(fake, mode="webapi")
    grouped = json.dumps({
        "entity": "dbo.tpg_project", "entity_set": "ignored", "format": "odata",
        "query": {"$apply": "filter(tpg_projectid ne null)/filter(statecode eq 0)/groupby((statuscode),"
                            "aggregate($count as m0))"},
        "groups_query": {"$apply": "filter(tpg_projectid ne null)/groupby((statuscode))"},
        "groups_keys": ["state"], "group_fill": [0], "columns": ["state", "n"],
        "sources": {"state": "statuscode", "n": "m0"},
        "labels": {"state": {"1": "Project Created", "2": "Active", "3": "On Hold", "4": "Completed", "5": "Dropped",
                             "6": "Cancelled", "7": "Archived", "8": "Expired"}},
        "post": {"sort": [["n", True]]}})
    result = await adapter.execute(info, grouped, {}, Limits(), "d1")
    rows = {r[0]: r[1] for r in result.rows}
    assert rows["Project Created"] == 49 and rows["Active"] == 13 and rows["On Hold"] == 3
    assert rows["Completed"] == 0  # a group with no matching rows still shows 0, as SQL does
    assert result.rows[0] == ["Project Created", 49]
    page = json.dumps({"entity": "dbo.tpg_project", "entity_set": "x", "format": "odata",
                       "query": {"$select": "tpgmea_projectnumber", "$filter": "(tpg_projectid ne null)",
                                 "$orderby": "tpgmea_projectnumber asc"}, "columns": ["n"],
                       "sources": {"n": "tpgmea_projectnumber"}, "post": {"skip": 30, "top": 40}})
    paged = await adapter.execute(info, page, {}, Limits(), "d2")
    assert len(paged.rows) == 40 and paged.rows[0] == ["PRJ-031"]
    fake.throttle(2)  # every second read is throttled once: retried after Retry-After
    try:
        again = await adapter.execute(info, grouped, {}, Limits(), "d3")
        assert {r[0]: r[1] for r in again.rows} == rows
        fake.throttle(1)  # every read throttled: two retries, then MSG-025
        with pytest.raises(TelaError) as err:
            await adapter.execute(info, grouped, {}, Limits(), "d4")
        assert err.value.code == ErrorCode.SOURCE_BUSY
    finally:
        fake.throttle(0)
    assert fake.module.STATE["writes"] == 0  # the adapter never sent anything but GET


@pytest.mark.integration
async def test_tds_through_the_stand_in(adapter: Any, fake: FakeDataverse) -> None:
    admin = url("mssql")
    if admin is None:
        pytest.skip("TELA_TEST_MSSQL_URL not set")
    seed_dataverse_tds(admin)
    info = dataverse_info(fake, mode="tds", tds=admin)
    result = await adapter.test_connection(info)
    assert result.reachable and result.authenticated and result.read_only.safe, result
    bound = await adapter.execute(info, "SELECT count(*) AS n FROM dbo.tpg_project WHERE statecodename = :p0",
                                  {"p0": "Active"}, Limits(), "t2")
    assert bound.rows == [[65]]
    with pytest.raises(TelaError) as err:
        await adapter.execute(info, "UPDATE dbo.tpg_project SET tpg_title = 'x'", {}, Limits(), "t3")
    assert err.value.code == ErrorCode.READ_ONLY_VIOLATION
