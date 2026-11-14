"""PMO starter pack end to end: detection, install, approval and reconciliation (US-036 - US-040; TEST-012, TEST-060)."""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal
from typing import Any

import httpx
import pmo_oracle as oracle
import psycopg
import pytest
from api_testkit import needs_db
from pmo_testkit import AS_OF, World, as_map, create_connection, h, load_packs, run, setup_world, value

pytestmark = [needs_db, pytest.mark.integration]


@pytest.fixture
async def world(stack: httpx.AsyncClient, owner_conn: psycopg.Connection, make_token: Callable[..., str],
                tpg_source: dict[str, Any]) -> World:
    return await setup_world(stack, owner_conn, make_token, tpg_source)


async def test_pack_detected_and_installed_as_draft_only(
    stack: httpx.AsyncClient, owner_conn: psycopg.Connection, make_token: Callable[..., str],
    tpg_source: dict[str, Any],
) -> None:
    w = await setup_world(stack, owner_conn, make_token, tpg_source, approve=False)
    admin = w.tokens["admin"]
    import os

    load_packs(os.environ["TELA_TEST_OWNER_DATABASE_URL"])
    resp = await stack.get(f"/api/v1/starter-packs/match?connection_id={w.connection_id}", headers=h(admin))
    assert resp.status_code == 200, resp.text
    m = resp.json()["matches"][0]
    assert m["pack_key"] == "pmo_tpg" and m["matched"] and m["confidence"] > 0.9
    assert m["field_mapping"]["projects.planned_deployment_date"] == "planned_deployment_date"
    assert m["missing_objects"] == []

    install = await stack.post(f"/api/v1/starter-packs/{m['pack_id']}/install", headers=h(admin),
                               json={"connection_id": w.connection_id, "reporting_date": AS_OF})
    assert install.status_code == 202, install.text
    body = install.json()
    assert body["installed"]["measures"] >= 20 and body["installed"]["reports"] == 38
    unavailable = {i["key"] for i in body["unavailable"] if i["kind"] == "report"}
    # Pack 2.0 builds RAID, timesheets, critical tasks and status reporting from TPG (US-155); four gaps stay.
    assert unavailable == {"portfolio_trends", "schedule_compliance", "mandatory_fields", "milestone_slippage"}
    assert all(i["gaps"] for i in body["unavailable"])

    # Nothing is live until approval: there is no approved version to query yet.
    catalog = await stack.get(f"/api/v1/query/catalog?connection_id={w.connection_id}", headers=h(admin))
    assert catalog.status_code == 409 and catalog.json()["error"]["code"] == "model_not_approved"
    draft = (await stack.get(f"/api/v1/models/{body['model_id']}/draft", headers=h(admin))).json()
    assert draft["issues"] == [] and draft["unaccepted_links"] == []
    assert any(not q["blocking"] for q in draft["open_questions"]) is False  # reporting date given: no question

    # Viewers and creators cannot install packs.
    denied = await stack.post(f"/api/v1/starter-packs/{m['pack_id']}/install", headers=h(w.tokens["creator"]),
                              json={"connection_id": w.connection_id})
    assert denied.status_code == 403


async def test_partial_match_installs_unavailable_items_with_named_gaps(
    stack: httpx.AsyncClient, owner_conn: psycopg.Connection, make_token: Callable[..., str],
    tpg_source: dict[str, Any],
) -> None:
    from conftest import _dsn_for

    with psycopg.connect(_dsn_for(tpg_source["database"]), autocommit=True) as src:
        src.execute("DROP SCHEMA IF EXISTS partial CASCADE")
        src.execute("CREATE SCHEMA partial")
        src.execute("CREATE TABLE partial.projects AS SELECT project_id, project_number, name, status, "
                    "current_state, planned_deployment_date, actual_deployment_date, region, cio FROM public.projects")
        src.execute("ALTER TABLE partial.projects ADD PRIMARY KEY (project_id)")
        src.execute("GRANT USAGE ON SCHEMA partial TO tela_ro")
        src.execute("GRANT SELECT ON ALL TABLES IN SCHEMA partial TO tela_ro")
    w = await setup_world(stack, owner_conn, make_token, tpg_source, approve=False)
    admin = w.tokens["admin"]
    cid = await create_connection(stack, admin, tpg_source, name="Partial extract", schemas=["partial"])
    assert (await stack.post(f"/api/v1/connections/{cid}/test", headers=h(admin))).json()["status"] == "connected"
    assert (await stack.post(f"/api/v1/connections/{cid}/introspect", headers=h(admin))).status_code == 202
    import os

    load_packs(os.environ["TELA_TEST_OWNER_DATABASE_URL"])
    m = (await stack.get(f"/api/v1/starter-packs/match?connection_id={cid}", headers=h(admin))).json()["matches"][0]
    assert m["matched"] and m["confidence"] < 0.5 and "project_requests" in m["missing_objects"]
    install = (await stack.post(f"/api/v1/starter-packs/{m['pack_id']}/install", headers=h(admin),
                                json={"connection_id": cid, "reporting_date": AS_OF})).json()
    gaps = {i["key"]: i["gaps"] for i in install["unavailable"]}
    assert "Projects: Forecast cost (EAC, USD)" in gaps["forecast_cost_total"]
    assert gaps["open_demand"] == ["project_requests (whole table)"]
    # US-155: a report keeps what it can show; one with nothing left (the requests' screens) is unavailable.
    assert "requests_overview" in gaps and "projects_overview" not in gaps
    items = (await stack.get(f"/api/v1/starter-packs/items?connection_id={cid}", headers=h(admin))).json()["items"]
    assert {i["item_key"] for i in items if i["availability"] == "available" and i["item_kind"] == "measure"} >= {
        "open_projects", "overdue_go_lives", "in_delivery"}

    # The partial draft approves: only the available measures are in the model.
    draft = await stack.get(f"/api/v1/models/{install['model_id']}/draft", headers=h(admin))
    resp = await stack.post(f"/api/v1/models/{install['model_id']}/approve", headers=h(admin),
                            json={"draft_etag": draft.json()["draft_etag"]})
    assert resp.status_code == 201, resp.text


async def test_reference_counts_reconcile(stack: httpx.AsyncClient, world: World) -> None:
    admin = world.tokens["admin"]
    assert value(await run(stack, admin, world.spec("total_projects"))) == 100
    assert value(await run(stack, admin, world.spec("open_projects"))) == 65
    assert value(await run(stack, admin, world.spec("inactive_projects"))) == 35
    assert value(await run(stack, admin, world.spec("in_delivery"))) == 13
    assert value(await run(stack, admin, world.spec("on_hold"))) == 3
    assert value(await run(stack, admin, world.spec("project_created"))) == 49
    for measure in [*oracle.RULES, *oracle.REQUEST_RULES]:
        got = value(await run(stack, admin, world.spec(measure)))
        assert got == oracle.count(measure), measure


async def test_finance_exposure_reconciles(stack: httpx.AsyncClient, world: World) -> None:
    admin = world.tokens["admin"]
    want = oracle.finance()
    assert Decimal(str(value(await run(stack, admin, world.spec("approved_budget_total"))))) == want["approved"]
    assert Decimal(str(value(await run(stack, admin, world.spec("forecast_cost_total"))))) == want["forecast"]
    assert Decimal(str(value(await run(stack, admin, world.spec("budget_variance"))))) == want["variance"]
    pct = value(await run(stack, admin, world.spec("budget_variance_pct")))
    assert round(float(pct), 2) == round(float(want["variance_pct"]), 2)
    assert value(await run(stack, admin, world.spec("projects_excluded"))) == want["excluded"]


async def test_several_measures_derived_and_shares_reconcile(stack: httpx.AsyncClient, world: World) -> None:
    """US-176 through the query path: one answer with two measures and three derived figures reconciles with the
    finance oracle; shares of the total add up to 100 and say which measure they belong to."""
    admin = world.tokens["admin"]
    f, b = "measure:forecast_cost_total", "measure:approved_budget_total"
    resp = await run(stack, admin, world.spec("forecast_cost_total", extra_measures=[{"ref": b}], derived=[
        {"key": "pct_of", "op": "percent_of", "of": f, "to": b}, {"key": "over", "op": "percent_change", "of": f, "to": b},
        {"key": "gap", "op": "difference", "of": f, "to": b}]))
    assert resp.status_code == 200, resp.text
    body, want = resp.json(), oracle.finance()
    assert [c["name"] for c in body["columns"]] == ["forecast_cost_total", "approved_budget_total", "pct_of", "over",
                                                    "gap"]
    assert [c.get("of") for c in body["columns"]] == [None, None, "forecast_cost_total", "forecast_cost_total",
                                                      "forecast_cost_total"]
    forecast, approved, pct, over, gap = body["rows"][0]
    assert Decimal(str(forecast)) == want["forecast"] and Decimal(str(approved)) == want["approved"]
    assert round(float(pct), 4) == round(float(want["forecast"] / want["approved"] * 100), 4)
    assert round(float(over), 4) == round(float(want["variance_pct"]), 4)
    assert Decimal(str(gap)) == want["variance"]
    assert {d["ref"]: d["format"] for d in body["definitions"]} == {
        f: "currency", b: "currency", "derived:pct_of": "percent", "derived:over": "percent", "derived:gap": "currency"}

    shares = await run(stack, admin, world.spec("open_projects", breakdowns=[{"ref": "dimension:region"}],
                                                share_of_total=["measure:open_projects"]))
    assert shares.status_code == 200, shares.text
    by_region = oracle.by("open_projects", "region")
    total = sum(by_region.values())
    for region, n, share in shares.json()["rows"]:
        assert n == by_region[region] and round(share, 6) == round(n / total * 100, 6)

    refused = await run(stack, admin, world.spec("forecast_cost_total", extra_measures=[{"ref": "measure:open_projects"}],
                                                 derived=[{"key": "x", "op": "difference", "of": f,
                                                           "to": "measure:open_projects"}]))
    assert refused.status_code == 422 and "different units" in refused.json()["error"]["message"], refused.text


async def test_breakdowns_reconcile_and_status_labels(stack: httpx.AsyncClient, world: World) -> None:
    admin = world.tokens["admin"]
    by_region = as_map(await run(stack, admin, world.spec("open_projects", breakdowns=[{"ref": "dimension:region"}])))
    assert by_region == dict(oracle.by("open_projects", "region").items())
    # Reported status shows Yellow as Amber (value mapping in the model).
    status = await run(stack, admin, world.spec("total_projects", breakdowns=[{"ref": "dimension:reported_status"}]))
    labels = {r[0] for r in status.json()["rows"]}
    assert labels == {"Green", "Amber", "Red", "Not set"}  # a blank reported status shows as Not set (US-156)
    amber = as_map(status)["Amber"]
    assert amber == sum(1 for p in oracle.PROJECTS if p["overall_kpi"] == "Yellow")
    # Filtering on the display label matches the raw value.
    filtered = await run(stack, admin, world.spec("total_projects", filters=[
        {"ref": "dimension:reported_status", "op": "=", "values": ["Amber"]}]))
    assert value(filtered) == amber


async def test_every_available_starter_report_widget_runs(stack: httpx.AsyncClient, world: World) -> None:
    from tela_api.packs.manifest import DEFAULT_PACKS_DIR, load_manifest, widget_spec

    manifest, _, _ = load_manifest(DEFAULT_PACKS_DIR / "pmo" / "manifest.yaml")
    ran = 0
    for key, rep in manifest.reports.items():
        for i in range(len(rep["widgets"])):
            spec = widget_spec(manifest, key, i, model_version_id=world.version_id or "",
                               connection_id=world.connection_id)
            resp = await run(stack, world.tokens["admin"], spec)
            assert resp.status_code == 200, (key, i, resp.text)
            assert resp.json()["rows"], (key, i)
            ran += 1
    assert ran >= 35


async def test_catalogue_measures_reconcile_on_postgres(stack: httpx.AsyncClient, world: World) -> None:
    """US-156: every catalogue measure through the API (scope, PostgreSQL) equals the oracle; banded and
    computed dimensions run, filter and drill."""
    admin = world.tokens["admin"]
    for measure, want in oracle.CATALOGUE.items():
        got = value(await run(stack, admin, world.spec(measure)))
        if want is None or got is None:
            assert got is None and want is None, measure
        else:
            assert abs(float(got) - float(want)) <= 1e-6 * max(1.0, abs(float(want))), (measure, got, want)
    health = as_map(await run(stack, admin, world.spec("open_projects",
                                                       breakdowns=[{"ref": "dimension:calculated_health"}])))
    assert health.get("Green", 0) == oracle.CATALOGUE["health_green"]
    assert health.get("Insufficient data", 0) == oracle.CATALOGUE["health_insufficient"]
    late = await run(stack, admin, world.spec("in_delivery", filters=[
        {"ref": "dimension:lateness_bucket", "op": "=", "values": ["More than 90 days"]}]))
    assert value(late) == oracle.CATALOGUE["overdue_over_90"]
    options = await stack.post("/api/v1/query/filter-values", headers=h(admin), json={
        "connection_id": world.connection_id, "model_version_id": world.version_id,
        "ref": "dimension:on_hold_aging"})
    assert options.status_code == 200, options.text
    assert options.json()["values"][0] == "0\u201330 days" and options.json()["values"][-1] == "Not recorded"
    # The CIO persona sees health of their own projects only (scope applies to calculated values too).
    nomsa = as_map(await run(stack, world.tokens["nomsa"], world.spec(
        "open_projects", breakdowns=[{"ref": "dimension:calculated_health"}])))
    mine = [p for p in oracle.PROJECTS if p["status"] == "Active" and p["cio"] == "Nomsa Osei"]
    assert sum(nomsa.values()) == len(mine)


def test_health_examples_on_fixture_rows() -> None:
    from tela_api.packs.pmo_health import calculate

    active = [p for p in oracle.PROJECTS if p["current_state"] == "Active"]
    example, override = active[8], active[9]
    h1 = calculate({k: (float(v) if isinstance(v, Decimal) else v) for k, v in example.items()}, oracle.AS_OF)
    assert h1.display_score == 50.0 and h1.status == "Amber"
    h2 = calculate({k: (float(v) if isinstance(v, Decimal) else v) for k, v in override.items()}, oracle.AS_OF)
    assert h2.status == "Red" and h2.override_applied and h2.reason == "Cost score is 0 (severe overrun)"


async def test_pack_applied_to_a_model_the_admin_built(
    stack: httpx.AsyncClient, owner_conn: psycopg.Connection, make_token: Callable[..., str],
    tpg_source: dict[str, Any],
) -> None:
    """US-153 / BUG-004: the pack merges into a hand-built model; nothing the Admin made is lost."""
    import os

    w = await setup_world(stack, owner_conn, make_token, tpg_source, approve=False)
    admin = w.tokens["admin"]
    model_id = (await stack.post("/api/v1/models", headers=h(admin),
                                 json={"connection_id": w.connection_id})).json()["model_id"]
    draft = (await stack.get(f"/api/v1/models/{model_id}/draft", headers=h(admin))).json()
    own = {"key": "my_count", "label": "My count", "entity": "projects", "plain_rule": "Every project.",
           "expression": {"agg": "count"}}
    patched = await stack.patch(f"/api/v1/models/{model_id}/draft", headers=h(admin, **{"If-Match": draft["draft_etag"]}),
                                json={"measures": [own]})
    assert patched.status_code == 200, patched.text
    questions_before = {q["id"] for q in patched.json()["open_questions"]}
    assert questions_before

    load_packs(os.environ["TELA_TEST_OWNER_DATABASE_URL"])
    m = (await stack.get(f"/api/v1/starter-packs/match?connection_id={w.connection_id}",
                         headers=h(admin))).json()["matches"][0]
    preview = await stack.post(f"/api/v1/starter-packs/{m['pack_id']}/preview", headers=h(admin),
                               json={"connection_id": w.connection_id})
    assert preview.status_code == 200, preview.text
    p = preview.json()
    assert p["model_exists"] and p["installed_pack_id"] is None
    assert p["kept_own"]["measures"] == 1 and p["added"]["measures"] >= 20
    assert p["reports"]["available"] == 38
    denied = await stack.post(f"/api/v1/starter-packs/{m['pack_id']}/preview", headers=h(w.tokens["creator"]),
                              json={"connection_id": w.connection_id})
    assert denied.status_code == 403
    # The preview writes nothing.
    unchanged = (await stack.get(f"/api/v1/models/{model_id}/draft", headers=h(admin))).json()
    assert [x["key"] for x in unchanged["measures"]] == ["my_count"]

    keep = [c["ref"] for c in p["clashes"]][:1]
    install = await stack.post(f"/api/v1/starter-packs/{m['pack_id']}/install", headers=h(admin),
                               json={"connection_id": w.connection_id, "reporting_date": AS_OF, "keep_mine": keep})
    assert install.status_code == 202, install.text
    body = install.json()
    assert body["mode"] == "merge" and body["model_id"] == model_id
    assert all(c["choice"] == ("mine" if c["ref"] in keep else "pack") for c in body["clashes"])

    draft = (await stack.get(f"/api/v1/models/{model_id}/draft", headers=h(admin))).json()
    keys = {x["key"] for x in draft["measures"]}
    assert "my_count" in keys and "open_projects" in keys
    assert questions_before <= {q["id"] for q in draft["open_questions"]}  # the Admin's questions stay open
    assert draft["settings"]["reporting_date"] == AS_OF
    audit = owner_conn.execute("SELECT details->>'mode' FROM ops.audit_events WHERE action = "
                               "'starter_pack.installed' ORDER BY id DESC LIMIT 1").fetchone()
    assert audit == ("merge",)

    # Applying again changes nothing the Admin owns and adds nothing twice.
    again = (await stack.post(f"/api/v1/starter-packs/{m['pack_id']}/preview", headers=h(admin),
                              json={"connection_id": w.connection_id})).json()
    assert again["installed_pack_id"] == m["pack_id"] and again["added"]["measures"] == 0

    # Approval builds the starter reports and dashboards, as for a fresh install.
    for q in draft["open_questions"]:
        await stack.post(f"/api/v1/models/{model_id}/questions/{q['id']}/answer", headers=h(admin),
                         json={"answer": q["options"][0] if q["options"] else "Yes"})
    draft = (await stack.get(f"/api/v1/models/{model_id}/draft", headers=h(admin))).json()
    links = [{**ln, "accepted": True} for ln in draft["links"]]
    draft = (await stack.patch(f"/api/v1/models/{model_id}/draft", headers=h(admin, **{"If-Match": draft["draft_etag"]}),
                               json={"links": links})).json()
    approved = await stack.post(f"/api/v1/models/{model_id}/approve", headers=h(admin),
                                json={"draft_etag": draft["draft_etag"]})
    assert approved.status_code == 201, approved.text
    starters = owner_conn.execute("SELECT count(*) FROM content.reports WHERE origin = 'starter'").fetchone()
    assert starters is not None and starters[0] == 38


async def test_pack_mapped_to_a_source_with_other_names(
    stack: httpx.AsyncClient, owner_conn: psycopg.Connection, make_token: Callable[..., str],
    tpg_source: dict[str, Any],
) -> None:
    """US-154: Dataverse-style names are found, a required field is mapped by hand, a status value is mapped,
    the mapping is saved per connection, and the mapped measure runs on the source."""
    import os

    from conftest import RO_USER, SOURCE_DB, _dsn_for

    with psycopg.connect(_dsn_for(SOURCE_DB), autocommit=True) as src:
        src.execute("DROP SCHEMA IF EXISTS dv CASCADE")
        src.execute("CREATE SCHEMA dv")
        src.execute("""
            CREATE TABLE dv.tpg_project AS SELECT project_id AS tpg_projectid, name AS tpg_projectname,
              status AS statecodename, current_state AS statuscodename, cio AS tpgmea_customcioname,
              0 AS tpgmea_region, region AS tpgmea_regionname,
              CASE WHEN schedule_kpi IN ('Yellow', 'Amber') THEN 'Y' ELSE schedule_kpi END AS tpg_schedulekpiname,
              1::bigint AS versionnumber, planned_deployment_date AS tpgmea_golivedate, priority AS tpgmea_priorityname,
              platform AS tpgmea_platformname, overall_kpi AS tpg_overallkpiname, cost_kpi AS tpg_costkpiname,
              delivery_manager AS tpgmea_deliverymanagername, modified_on AS modifiedon,
              actual_deployment_date AS tpgmea_actualdeploymentdate
            FROM public.projects""")
        src.execute(f"GRANT USAGE ON SCHEMA dv TO {RO_USER}")
        src.execute(f"GRANT SELECT ON ALL TABLES IN SCHEMA dv TO {RO_USER}")
        expected = src.execute("SELECT count(*) FROM public.projects WHERE status = 'Active' AND "
                               "schedule_kpi IN ('Red', 'Yellow', 'Amber')").fetchone()

    w = await setup_world(stack, owner_conn, make_token, tpg_source, approve=False)
    admin = w.tokens["admin"]
    cid = await create_connection(stack, admin, tpg_source, name="TPG Dataverse names", schemas=["dv"])
    assert (await stack.post(f"/api/v1/connections/{cid}/test", headers=h(admin))).status_code == 200
    assert (await stack.post(f"/api/v1/connections/{cid}/introspect", headers=h(admin))).status_code == 202
    load_packs(os.environ["TELA_TEST_OWNER_DATABASE_URL"])

    m = next(x for x in (await stack.get(f"/api/v1/starter-packs/match?connection_id={cid}",
                                         headers=h(admin))).json()["matches"] if x["pack_key"] == "pmo_tpg")
    assert not m["matched"]  # the planned deployment date (required) has a name pack 2.0 does not know
    found = m["entities"]["projects"]["found_by"]
    assert found["region"] == "dataverse_label" and found["name"] == "similar_name"
    assert m["field_mapping"]["projects.region"] == "tpgmea_regionname"

    url = f"/api/v1/starter-packs/{m['pack_id']}"
    first = (await stack.post(f"{url}/preview", headers=h(admin), json={"connection_id": cid})).json()
    assert first["matched"] is False
    proj = next(e for e in first["mapping_step"]["entities"] if e["key"] == "projects")
    planned = next(f for f in proj["fields"] if f["key"] == "planned_deployment_date")
    assert planned["how"] == "missing" and planned["required"]
    assert "tpgmea_golivedate" in {c["name"] for c in proj["columns"]}
    sched = next(f for f in proj["fields"] if f["key"] == "schedule_kpi")
    assert "Amber" in sched["values"] and sched["value_map"] == {}
    refused = await stack.post(f"{url}/install", headers=h(admin), json={"connection_id": cid})
    assert refused.status_code == 409 and refused.json()["error"]["code"] == "pack_not_matched"

    wrong = await stack.post(f"{url}/preview", headers=h(admin), json={"connection_id": cid, "mapping": {
        "fields": {"projects.planned_deployment_date": "tpgmea_regionname"}}})
    assert wrong.status_code == 400 and wrong.json()["error"]["code"] == "validation_error"

    mapping = {"fields": {"projects.planned_deployment_date": "tpgmea_golivedate",
                          "projects.portfolio": None},
               "values": {"projects.schedule_kpi": {"Y": "Amber"}}}
    mapped = (await stack.post(f"{url}/preview", headers=h(admin),
                               json={"connection_id": cid, "mapping": mapping})).json()
    assert mapped["matched"] is True
    gaps = {i["key"]: i["gaps"] for i in mapped["unavailable"]}
    assert "overdue_go_lives" not in gaps and "schedule_risk" not in gaps
    assert any("Portfolio" in g for gs in gaps.values() for g in gs)  # "not in this source" names the gap

    install = await stack.post(f"{url}/install", headers=h(admin),
                               json={"connection_id": cid, "reporting_date": AS_OF, "mapping": mapping})
    assert install.status_code == 202, install.text
    saved = owner_conn.execute("SELECT mapping, resolved FROM model.pack_field_mappings WHERE connection_id = %s",
                               (cid,)).fetchone()
    assert saved is not None and saved[0]["values"] == {"projects.schedule_kpi": {"Y": "Amber"}}
    assert saved[1]["fields"]["projects.planned_deployment_date"] == "tpgmea_golivedate"
    # The saved mapping is reused: the match and the preview find it without being told again.
    again = next(x for x in (await stack.get(f"/api/v1/starter-packs/match?connection_id={cid}",
                                             headers=h(admin))).json()["matches"] if x["pack_key"] == "pmo_tpg")
    assert again["matched"] and again["entities"]["projects"]["found_by"]["planned_deployment_date"] == "mapped"
    reused = (await stack.post(f"{url}/preview", headers=h(admin), json={"connection_id": cid})).json()
    assert reused["mapping"]["fields"]["projects.portfolio"] is None

    model_id = install.json()["model_id"]
    draft = (await stack.get(f"/api/v1/models/{model_id}/draft", headers=h(admin))).json()
    dim = next(d for d in draft["dimensions"] if d["key"] == "schedule_status")
    assert dim["value_labels"]["Y"] == "Amber"
    for q in draft["open_questions"]:
        await stack.post(f"/api/v1/models/{model_id}/questions/{q['id']}/answer", headers=h(admin),
                         json={"answer": q["options"][0] if q["options"] else "Yes"})
    draft = (await stack.get(f"/api/v1/models/{model_id}/draft", headers=h(admin))).json()
    approved = await stack.post(f"/api/v1/models/{model_id}/approve", headers=h(admin),
                                json={"draft_etag": draft["draft_etag"]})
    assert approved.status_code == 201, approved.text
    resp = await stack.post("/api/v1/admin/scope-policies", headers=h(admin), json={
        "connection_id": cid, "entity": "projects", "role": "admin", "all_rows": True, "description": "All rows"})
    assert resp.status_code == 201, resp.text
    resp = await run(stack, admin, {"model_version_id": approved.json()["model_version_id"], "connection_id": cid,
                                    "measure": {"ref": "measure:schedule_risk"}})
    assert resp.status_code == 200, resp.text
    assert expected is not None and value(resp) == expected[0]
