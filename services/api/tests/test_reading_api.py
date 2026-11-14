"""Reading: Home, starter dashboards, widget runs, definitions and drill (US-044, US-052, US-054, US-055).

Acceptance: TEST-019 (Home shelves), TEST-050 (filters reach every widget on every tab, locked scope chip,
as-of), TEST-051 (one failing widget does not fail the page), TEST-052 (definition popover; query for
Creators only), TEST-053 (drill scoped, hidden fields absent, paginated).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx
import pmo_oracle as oracle
import psycopg
import pytest
from api_testkit import needs_db
from pmo_testkit import World, h, setup_world
from test_starter_pack2_unit import PROJECT_TABS

pytestmark = [needs_db, pytest.mark.integration]
GULF = {"KSA", "GCC - UAE"}


@pytest.fixture
async def world(stack: httpx.AsyncClient, owner_conn: psycopg.Connection, make_token: Callable[..., str],
                tpg_source: dict[str, Any]) -> World:
    return await setup_world(stack, owner_conn, make_token, tpg_source)


async def dashboards(api: httpx.AsyncClient, token: str) -> dict[str, Any]:
    items = (await api.get("/api/v1/published", headers=h(token))).json()["items"]
    return {d["title"]: d for d in items}


async def test_starter_dashboards_and_home(stack: httpx.AsyncClient, world: World) -> None:
    approved = world.extra.get("install")
    assert approved is not None
    ds = await dashboards(stack, world.tokens["nomsa"])
    assert set(ds) == {"PMO — Projects", "PMO — Requests"}
    projects = ds["PMO — Projects"]
    assert projects["badges"] == ["certified", "starter"] and projects["count"] == {"tabs": 26, "widgets": 203}

    home = (await stack.get("/api/v1/home", headers=h(world.tokens["nomsa"]))).json()
    assert {t["title"] for t in home["shelves"]["starter"]} == set(ds)
    assert {t["owner"] for t in home["shelves"]["starter"]} == {"Tela starter"}
    assert home["shelves"]["published_to_my_role"] == [] and "mine" not in home["shelves"]
    assert home["empty_text"]["published_to_my_role"] == "Nothing published to your role yet."
    assert home["greeting"]["name"] == "Nomsa Osei" and "Own CIO" in home["greeting"]["scopes"][0]["text"]
    creator_home = (await stack.get("/api/v1/home", headers=h(world.tokens["creator"]))).json()
    assert creator_home["shelves"]["mine"] == [] and creator_home["can_create"] is True
    # Suggested questions come from certified measures (US-138), at most six.
    asks = home["suggested_questions"]
    assert 0 < len(asks) <= 6 and len(set(asks)) == len(asks)


async def test_filters_reach_every_tab(stack: httpx.AsyncClient, world: World) -> None:
    token = world.tokens["creator"]
    d = (await dashboards(stack, token))["PMO — Projects"]
    view = (await stack.get(f"/api/v1/published/{d['id']}", headers=h(token))).json()
    assert [t["title"] for t in view["tabs"]] == PROJECT_TABS
    assert "Gulf regions" in view["locked_scope_chip"] and view["scope_message"].startswith("This is your data scope")
    assert {f["ref"] for f in view["filters"]} >= {"dimension:region", "dimension:priority"}
    page = [{"ref": "dimension:region", "op": "=", "values": ["KSA"]}]
    for tab in view["tabs"]:
        for w in tab["widgets"]:
            resp = await stack.post(f"/api/v1/widgets/{w['id']}/run", headers=h(token), json={"page_filters": page})
            assert resp.status_code == 200, (w["title"], resp.text)
            body = resp.json()
            assert body["filters_applied"] == ["dimension:region"] and body["as_of"]
            assert body["viz"]["visual"] is not None
            for col_i, col in enumerate(body["columns"]):
                if col["name"] == "region":
                    assert {r[col_i] for r in body["rows"]} <= {"KSA"}
    cio_tab = next(t for t in view["tabs"] if t["title"] == "CIO & Portfolio")["widgets"]
    open_figure = next(w for w in cio_tab if w["title"] == "Open projects" and w["visual"] == "figure")
    one = (await stack.post(f"/api/v1/widgets/{open_figure['id']}/run", headers=h(token),
                            json={"page_filters": page})).json()
    assert one["rows"] == [[oracle.count("open_projects", regions={"KSA"})]]
    assert one["viz"]["visual"] == "figure"
    # KPI extras (US-139): no time range, so no change; a monthly sparkline by the measure's date, same scope.
    k = one["kpi"]
    assert k["value"] == one["rows"][0][0] and k["change"] is None
    spark = k["sparkline"]
    assert spark["grain"] == "month" and spark["by"] == "Planned deployment date" and len(spark["points"]) >= 3
    assert spark["range"] == "last_12_months"
    monthly_trend = next(w for t in view["tabs"] for w in t["widgets"] if w["title"] == "Planned go-lives by month")
    assert (await stack.post(f"/api/v1/widgets/{monthly_trend['id']}/run", headers=h(token),
                             json={})).json()["kpi"] is None


async def test_requests_widgets_ignore_unreachable_filters(stack: httpx.AsyncClient, world: World) -> None:
    token = world.tokens["admin"]
    d = (await dashboards(stack, token))["PMO — Requests"]
    view = (await stack.get(f"/api/v1/published/{d['id']}", headers=h(token))).json()
    w = view["tabs"][0]["widgets"][0]
    resp = (await stack.post(f"/api/v1/widgets/{w['id']}/run", headers=h(token), json={"page_filters": [
        {"ref": "dimension:request_region", "op": "=", "values": ["KSA"]}]})).json()
    assert resp["filters_applied"] == ["dimension:request_region"]


async def test_hidden_widgets_and_failures(stack: httpx.AsyncClient, world: World) -> None:
    admin, nomsa = world.tokens["admin"], world.tokens["nomsa"]
    await stack.put("/api/v1/admin/field-policies", headers=h(admin), json={
        "connection_id": world.connection_id,
        "rules": [{"field_ref": "field:projects.approved_budget_value_usd", "hidden_for": ["cio"]}]})
    d = (await dashboards(stack, nomsa))["PMO — Projects"]
    view = (await stack.get(f"/api/v1/published/{d['id']}", headers=h(nomsa))).json()
    finance = next(t for t in view["tabs"] if t["title"] == "CAPEX budget vs forecast")
    hidden = [w for w in finance["widgets"] if w["hidden"]]
    assert hidden and all(w["hidden_message"] == "Hidden for your role" for w in hidden)
    run = (await stack.post(f"/api/v1/widgets/{hidden[0]['id']}/run", headers=h(nomsa), json={})).json()
    assert run["hidden"] is True and run["rows"] == []
    # A widget whose source is down fails alone (TEST-051): the page and other dashboards still load.
    await stack.post(f"/api/v1/connections/{world.connection_id}/disable", headers=h(admin))
    first = view["tabs"][0]["widgets"][0]
    down = await stack.post(f"/api/v1/widgets/{first['id']}/run", headers=h(nomsa), json={})
    assert down.status_code == 503 and down.json()["error"]["code"] == "source_unreachable"
    assert (await stack.get(f"/api/v1/published/{d['id']}", headers=h(nomsa))).status_code == 200


async def test_no_access_leaks_nothing(stack: httpx.AsyncClient, world: World, owner_conn: psycopg.Connection) -> None:
    d = (await dashboards(stack, world.tokens["admin"]))["PMO — Projects"]
    owner_conn.execute("DELETE FROM content.publications WHERE dashboard_id = %s", (d["id"],))
    owner_conn.execute("INSERT INTO content.publications (id, tenant_id, dashboard_id, role_id) SELECT "
                       "gen_random_uuid(), tenant_id, id, '00000000-0000-7000-8000-00000000a001' FROM content.dashboards "
                       "WHERE id = %s", (d["id"],))  # published to Admin only
    resp = await stack.get(f"/api/v1/published/{d['id']}", headers=h(world.tokens["nomsa"]))
    assert resp.status_code == 404 and resp.json()["error"]["message"] == "You don't have access to this dashboard."
    assert d["title"] not in resp.text
    assert (await stack.get(f"/api/v1/published/{d['id']}", headers=h(world.tokens["admin"]))).status_code == 200
    assert (await stack.get("/api/v1/published/not-a-uuid", headers=h(world.tokens["admin"]))).status_code == 404


async def test_definition_popover(stack: httpx.AsyncClient, world: World) -> None:
    url = f"/api/v1/definitions/measure:overdue_go_lives?connection_id={world.connection_id}"
    viewer = (await stack.get(url, headers=h(world.tokens["nomsa"]))).json()
    assert viewer["label"] == "Overdue go-lives" and viewer["rule_ref"] == "RULE-028"
    assert "Planned deployment date" in viewer["fields_used"] and viewer["blank_handling"]
    assert viewer["certified"]["model_version"] == 1 and viewer["badge_text"].startswith("✓ Certified")
    assert viewer["in_scope_text"].startswith("Within your scope:") and viewer["can_show_query"] is False
    creator = (await stack.get(url, headers=h(world.tokens["creator"]))).json()
    assert creator["can_show_query"] is True
    spec = world.spec("overdue_go_lives", breakdowns=[{"ref": "dimension:region"}])
    q = (await stack.post("/api/v1/query/explain", headers=h(world.tokens["creator"]), json={"spec": spec})).json()
    assert "WHERE" in q["sql"] and {s["entity"] for s in q["scope"]} == {"projects"}
    assert "Gulf regions" not in q["sql"] and "region" in q["scope"][0]["predicate"]
    denied = await stack.post("/api/v1/query/explain", headers=h(world.tokens["nomsa"]), json={"spec": spec})
    assert denied.status_code == 403


async def test_drill_is_scoped_paged_and_masks_hidden(stack: httpx.AsyncClient, world: World) -> None:
    admin, creator = world.tokens["admin"], world.tokens["creator"]
    await stack.put("/api/v1/admin/field-policies", headers=h(admin), json={
        "connection_id": world.connection_id,
        "rules": [{"field_ref": "field:projects.delivery_manager", "hidden_for": ["creator"]}]})
    spec = world.spec("total_projects", breakdowns=[{"ref": "dimension:region"}])
    resp = await stack.post("/api/v1/query/records", headers=h(creator), json={"spec": spec, "mark": {"region": "KSA"}})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    names = [c["name"] for c in body["columns"]]
    assert "delivery_manager" not in names and {"project_number", "region"} <= set(names)
    ksa = [p for p in oracle.PROJECTS if p["region"] == "KSA"]
    assert body["total"] == len(ksa) and {r[names.index("region")] for r in body["rows"]} == {"KSA"}
    # Outside her scope: the mark is not part of her chart.
    out = await stack.post("/api/v1/query/records", headers=h(creator), json={"spec": spec, "mark": {"region": "DPO"}})
    assert out.status_code == 400
    # Paging: 100 per page.
    all_spec = world.spec("total_projects")
    page1 = (await stack.post("/api/v1/query/records", headers=h(admin), json={"spec": all_spec})).json()
    assert page1["total"] == 100 and len(page1["rows"]) == 100 and page1["next_offset"] is None
    page = (await stack.post("/api/v1/query/records", headers=h(admin), json={"spec": all_spec, "offset": 90})).json()
    assert len(page["rows"]) == 10
    overdue = world.spec("overdue_go_lives")
    recs = (await stack.post("/api/v1/query/records", headers=h(admin), json={"spec": overdue})).json()
    assert recs["total"] == oracle.count("overdue_go_lives")


async def test_widget_drill_and_period_marks(stack: httpx.AsyncClient, world: World) -> None:
    token = world.tokens["admin"]
    d = (await dashboards(stack, token))["PMO — Projects"]
    view = (await stack.get(f"/api/v1/published/{d['id']}", headers=h(token))).json()
    delivery = next(t for t in view["tabs"] if t["title"] == "Flow metrics")
    monthly = next(w for w in delivery["widgets"] if w["title"] == "Planned go-lives by month")
    run = (await stack.post(f"/api/v1/widgets/{monthly['id']}/run", headers=h(token), json={})).json()
    period, count = run["rows"][0]
    drill = (await stack.post(f"/api/v1/widgets/{monthly['id']}/drill", headers=h(token),
                              json={"mark": {"period": period}})).json()
    assert drill["total"] == count
