"""Starter content management (EPIC-11 part 1: US-098; FEAT-046; API-047; RULE-017).

Acceptance: TEST-055 — a starter report with 3 user copies that is included in a live dashboard is deleted:
the copies keep working, the live dashboard is unchanged (with a Deleted badge), a draft without the report
exists, and Restore from pack brings the report back. Also REQ-111 (hide from a role) and REQ-114 (copy).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx
import psycopg
import pytest
from api_testkit import needs_db
from pmo_testkit import World, h, setup_world

from tela_api.packs.manifest import discover, load_manifest

pytestmark = [needs_db, pytest.mark.integration]
DELIVERY = "delayed_projects"
REQUEST_REPORTS = set(load_manifest(next(p for p in discover() if p.parent.name == "pmo"))[0]
                      .dashboards["requests"]["reports"])


@pytest.fixture
async def world(stack: httpx.AsyncClient, owner_conn: psycopg.Connection, make_token: Callable[..., str],
                tpg_source: dict[str, Any]) -> World:
    return await setup_world(stack, owner_conn, make_token, tpg_source)


async def starter_items(api: httpx.AsyncClient, token: str) -> dict[str, dict[str, Any]]:
    resp = await api.get("/api/v1/admin/starter", headers=h(token))
    assert resp.status_code == 200, resp.text
    return {i["key"]: i for i in resp.json()["items"]}


async def starter_tabs(api: httpx.AsyncClient, token: str, title: str) -> list[str]:
    tiles = (await api.get("/api/v1/published", headers=h(token))).json()["items"]
    tile = next((t for t in tiles if t["title"] == title), None)
    if tile is None:
        return []
    resp = await api.get(f"/api/v1/published/{tile['id']}", headers=h(token))
    return [t["title"] for t in resp.json()["tabs"]] if resp.status_code == 200 else []


async def runs(api: httpx.AsyncClient, token: str, report: dict[str, Any]) -> bool:
    widget = next(i for s in report["sections"] for i in s["items"] if i["kind"] == "widget")
    resp = await api.post(f"/api/v1/widgets/{widget['widget_id']}/run", headers=h(token), json={})
    return resp.status_code == 200 and resp.json().get("status", "ok") != "error"


async def test_delete_effects_and_restore(stack: httpx.AsyncClient, world: World) -> None:
    admin, creator, viewer = world.tokens["admin"], world.tokens["creator"], world.tokens["viewer"]
    items = await starter_items(stack, admin)
    assert (await stack.get("/api/v1/admin/starter", headers=h(creator))).status_code == 403
    delivery = items[DELIVERY]
    assert delivery["state"] == "active" and delivery["availability"] == "available" and delivery["report_id"]
    assert delivery["widgets"] > 0 and delivery["copies"] == 0
    assert all(v["visible"] for v in delivery["visibility"])
    assert any(i["availability"] == "unavailable" and i["gaps"] for i in items.values())
    rid = delivery["report_id"]

    # Everyone reads a starter report; nobody edits it.
    report = (await stack.get(f"/api/v1/reports/{rid}", headers=h(viewer))).json()
    assert report["origin"] == "starter" and not report["can_edit"] and report["owner"]["name"] == "Tela starter"
    assert (await stack.patch(f"/api/v1/reports/{rid}", headers=h(admin), json={"title": "Mine"})).status_code == 404
    shelf = (await stack.get("/api/v1/starter-reports", headers=h(creator))).json()["items"]
    assert any(s["id"] == rid and not s["copied"] for s in shelf)

    # Three user copies (REQ-114); Viewers cannot copy.
    assert (await stack.post(f"/api/v1/reports/{rid}/copy", headers=h(viewer))).status_code == 403
    copies = []
    for token in (creator, creator, admin):
        resp = await stack.post(f"/api/v1/reports/{rid}/copy", headers=h(token))
        assert resp.status_code == 201, resp.text
        copies.append((token, resp.json()))
    first = copies[0][1]
    assert first["origin"] == "copy" and first["copied_from_id"] == rid and first["can_edit"]
    assert first["title"].endswith("(copy)")
    assert (await stack.post(f"/api/v1/reports/{first['id']}/copy", headers=h(creator))).status_code == 409
    assert (await starter_items(stack, admin))[DELIVERY]["copies"] == 3
    assert next(s for s in (await stack.get("/api/v1/starter-reports", headers=h(creator))).json()["items"]
                if s["id"] == rid)["copied"]

    # The starter report on a Creator's dashboard, published live to the Creator role.
    created = await stack.post("/api/v1/dashboards", headers=h(creator), json={
        "title": "Gulf delivery", "report_ids": [rid], "layout": "tabs"})
    assert created.status_code == 201, created.text
    d = created.json()
    assert d["reports"][0]["starter"]
    v1 = await stack.post(f"/api/v1/dashboards/{d['id']}/publish", headers=h(admin), json={"target_roles": ["creator"]})
    assert v1.status_code == 201, v1.text
    live = (await stack.get(f"/api/v1/published/{d['id']}", headers=h(creator))).json()
    assert live["tabs"][0]["badges"] == ["starter"]

    # Dry run explains the effects and changes nothing.
    dry = await stack.delete(f"/api/v1/admin/starter/{delivery['id']}?dry_run=true", headers=h(admin))
    assert dry.status_code == 200, dry.text
    effects = dry.json()["effects"]
    assert effects["user_copies_unaffected"] == 3
    assert [(x["dashboard_id"], x["live_version_stays"]) for x in effects["dashboards_getting_new_draft"]] == [
        (d["id"], 1)]
    assert [x["title"] for x in effects["starter_dashboards"]] == ["PMO — Projects"]
    assert (await starter_items(stack, admin))[DELIVERY]["state"] == "active"
    assert "Delayed projects" in await starter_tabs(stack, viewer, "PMO — Projects")

    deleted = await stack.delete(f"/api/v1/admin/starter/{delivery['id']}", headers=h(admin))
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["notified"] == 1 and deleted.json()["item"]["state"] == "deleted"
    again = await stack.delete(f"/api/v1/admin/starter/{delivery['id']}", headers=h(admin))
    assert again.status_code == 409

    # Copies keep working.
    for token, copy in copies:
        mine = (await stack.get(f"/api/v1/reports/{copy['id']}", headers=h(token))).json()
        assert mine["can_edit"] and await runs(stack, token, mine)
    # The live version is unchanged and shows the Deleted badge; the draft no longer has the report.
    still = (await stack.get(f"/api/v1/published/{d['id']}", headers=h(creator))).json()
    assert still["version"] == 1 and [t["title"] for t in still["tabs"]] == [t["title"] for t in live["tabs"]]
    assert still["tabs"][0]["badges"] == ["starter", "deleted"]
    assert (await stack.post(f"/api/v1/widgets/{still['tabs'][0]['widgets'][0]['id']}/run", headers=h(creator),
                             json={"context": {"dashboard_version_id": still["version_id"]}})).status_code == 200
    draft = (await stack.get(f"/api/v1/dashboards/{d['id']}", headers=h(creator))).json()
    assert draft["reports"] == [] and draft["draft_differs_from_live"]
    assert draft["starter_removed"] == [{"id": rid, "title": delivery["title"]}]
    note = (await stack.get("/api/v1/notifications?kind=starter.deleted", headers=h(creator))).json()["items"]
    assert len(note) == 1 and "republish" in note[0]["body"]
    # Gone from the starter dashboard, the shelf and direct reads.
    assert "Delayed projects" not in await starter_tabs(stack, viewer, "PMO — Projects")
    assert all(s["id"] != rid for s in (await stack.get("/api/v1/starter-reports", headers=h(creator))).json()["items"])
    assert (await stack.get(f"/api/v1/reports/{rid}", headers=h(viewer))).status_code == 404
    assert (await stack.post(f"/api/v1/reports/{rid}/copy", headers=h(creator))).status_code == 404
    readd = await stack.put(f"/api/v1/dashboards/{d['id']}/reports", headers=h(creator),
                            json={"items": [{"report_id": rid}]})
    assert readd.status_code == 404
    audit = (await stack.get("/api/v1/admin/audit?action=starter.deleted", headers=h(admin))).json()["items"]
    assert audit and audit[0]["details"]["user_copies"] == 3

    # Restore from pack brings it back; copies are untouched.
    restored = await stack.post(f"/api/v1/admin/starter/{delivery['id']}/restore", headers=h(admin))
    assert restored.status_code == 200, restored.text
    assert restored.json()["item"]["state"] == "active" and restored.json()["item"]["report_id"] == rid
    assert restored.json()["item"]["copies"] == 3
    assert (await stack.post(f"/api/v1/admin/starter/{delivery['id']}/restore", headers=h(admin))).status_code == 409
    assert "Delayed projects" in await starter_tabs(stack, viewer, "PMO — Projects")
    back = (await stack.get(f"/api/v1/reports/{rid}", headers=h(viewer))).json()
    assert sum(1 for s in back["sections"] for i in s["items"] if i["kind"] == "widget") == delivery["widgets"]
    assert await runs(stack, creator, back)  # the test Viewer has no data scope
    assert (await stack.get(f"/api/v1/published/{d['id']}", headers=h(creator))).json()["tabs"][0]["badges"] == [
        "starter"]


async def test_hide_from_role_and_show_to_all(stack: httpx.AsyncClient, world: World) -> None:
    admin, creator, viewer = world.tokens["admin"], world.tokens["creator"], world.tokens["viewer"]
    items = await starter_items(stack, admin)
    delivery = items[DELIVERY]
    bad = await stack.post(f"/api/v1/admin/starter/{delivery['id']}/visibility", headers=h(admin), json={})
    assert bad.status_code == 400
    unknown = await stack.post(f"/api/v1/admin/starter/{delivery['id']}/visibility", headers=h(admin),
                               json={"hide_from_roles": ["nobody"]})
    assert unknown.status_code == 400

    hidden = await stack.post(f"/api/v1/admin/starter/{delivery['id']}/visibility", headers=h(admin),
                              json={"hide_from_roles": ["viewer"]})
    assert hidden.status_code == 200, hidden.text
    assert hidden.json()["state"] == "hidden"
    assert {v["key"]: v["visible"] for v in hidden.json()["visibility"]}["viewer"] is False
    assert "Delayed projects" not in await starter_tabs(stack, viewer, "PMO — Projects")
    assert "Delayed projects" in await starter_tabs(stack, creator, "PMO — Projects")
    assert all(s["id"] != delivery["report_id"] for s in
               (await stack.get("/api/v1/starter-reports", headers=h(viewer))).json()["items"])

    # A starter dashboard whose every report is hidden leaves the person's Home.
    for key, item in items.items():
        if key in REQUEST_REPORTS and item["availability"] == "available":
            resp = await stack.post(f"/api/v1/admin/starter/{item['id']}/visibility", headers=h(admin),
                                    json={"hide_from_roles": ["viewer"]})
            assert resp.status_code == 200, resp.text
    home = (await stack.get("/api/v1/home", headers=h(viewer))).json()["shelves"]["starter"]
    assert [t["title"] for t in home] == ["PMO — Projects"]
    assert "PMO — Requests" in [t["title"] for t in
                                (await stack.get("/api/v1/home", headers=h(creator))).json()["shelves"]["starter"]]

    shown = await stack.post(f"/api/v1/admin/starter/{delivery['id']}/visibility", headers=h(admin),
                             json={"show_to_all": True})
    assert shown.json()["state"] == "active" and all(v["visible"] for v in shown.json()["visibility"])
    assert "Delayed projects" in await starter_tabs(stack, viewer, "PMO — Projects")


async def test_backfill_for_models_approved_before_starter_reports(stack: httpx.AsyncClient, world: World,
                                                                  owner_conn: psycopg.Connection) -> None:
    admin = world.tokens["admin"]
    owner_conn.execute("UPDATE model.starter_pack_items SET content_id = NULL WHERE item_kind = 'report'")
    owner_conn.execute("UPDATE content.reports SET deleted_at = now() WHERE origin = 'starter'")
    assert (await stack.get("/api/v1/starter-reports", headers=h(admin))).json()["items"] == []
    items = await starter_items(stack, admin)
    available = [i for i in items.values() if i["availability"] == "available"]
    assert available and all(i["report_id"] and i["widgets"] > 0 for i in available)
    assert len((await stack.get("/api/v1/starter-reports", headers=h(admin))).json()["items"]) == len(available)


async def test_items_of_another_pack_version_are_ignored(stack: httpx.AsyncClient, world: World,
                                                         owner_conn: psycopg.Connection) -> None:
    """Rows left by an install of another version of the pack never show up or keep a deleted report on a tab."""
    admin, viewer = world.tokens["admin"], world.tokens["viewer"]
    owner_conn.execute(
        "INSERT INTO model.starter_packs (id, pack_key, pack_version, title, detection_signature, manifest, "
        "manifest_sha256, released_at) SELECT gen_random_uuid(), pack_key, '9.9.9', title, detection_signature, "
        "manifest, manifest_sha256, released_at FROM model.starter_packs "
        "WHERE pack_key = 'pmo_tpg' LIMIT 1")
    owner_conn.execute(
        "INSERT INTO model.starter_pack_items (id, tenant_id, starter_pack_id, connection_id, item_kind, item_key, "
        "title, availability, gaps) SELECT gen_random_uuid(), i.tenant_id, p.id, i.connection_id, i.item_kind, "
        "i.item_key, i.title, i.availability, i.gaps FROM model.starter_pack_items i, model.starter_packs p "
        "WHERE p.pack_version = '9.9.9'")
    resp = await stack.get("/api/v1/admin/starter", headers=h(admin))
    keys = [i["key"] for i in resp.json()["items"]]
    assert len(keys) == len(set(keys)) and {i["pack"]["version"] for i in resp.json()["items"]} != {"9.9.9"}
    delivery = next(i for i in resp.json()["items"] if i["key"] == DELIVERY)
    assert (await stack.delete(f"/api/v1/admin/starter/{delivery['id']}", headers=h(admin))).status_code == 200
    assert "Delayed projects" not in await starter_tabs(stack, viewer, "PMO — Projects")


async def test_starter_reports_carry_text_blocks_and_detail_tables(stack: httpx.AsyncClient, world: World) -> None:
    """US-158: the pack's footnotes and callouts reach the starter dashboards and the pack-owned reports (and their
    copies), the dashboard says "Data as of" the reporting date, and a starter detail table lists records."""
    admin, creator = world.tokens["admin"], world.tokens["creator"]
    tiles = (await stack.get("/api/v1/published", headers=h(creator))).json()["items"]
    tile = next(t for t in tiles if t["title"] == "PMO — Projects")
    view = (await stack.get(f"/api/v1/published/{tile['id']}", headers=h(creator))).json()
    assert view["reporting_date"] == "2026-09-30"
    tabs = {t["title"]: t for t in view["tabs"]}
    assert all(t["footnotes"] for t in tabs.values())
    capex = tabs["CAPEX budget vs forecast"]
    assert capex["callouts"] == [{"kind": "callout", "title": "Forecasts are estimates", "level": "info",
                                  "text": capex["callouts"][0]["text"]}]
    assert "not money spent" in capex["footnotes"][0]
    table = next(w for w in tabs["Delayed projects"]["widgets"] if w["title"] == "Delayed projects")
    run = (await stack.post(f"/api/v1/widgets/{table['id']}/run", headers=h(creator), json={})).json()
    assert run["detail"]["columns"][:2] == ["project_number", "name"]
    assert run["detail"]["export_name"] == "Delayed projects" and run["row_count"] == len(run["rows"]) > 0
    names = [c["name"] for c in run["columns"]]
    dates = [r[names.index("planned_deployment_date")] for r in run["rows"]]
    assert dates == sorted(dates)  # the pack's default sort: oldest planned go-live first

    # The pack-owned report: callout first, footnote last; a copy keeps both.
    rid = (await starter_items(stack, admin))["capex_budget"]["report_id"]
    report = (await stack.get(f"/api/v1/reports/{rid}", headers=h(creator))).json()
    flat = [i for s in report["sections"] for i in s["items"]]
    assert report["sections"][0]["items"][0]["kind"] == "callout"
    assert [i["kind"] for i in report["sections"][-1]["items"]][-1] == "footnote"
    assert report["reporting_date"] == "2026-09-30"
    copy = await stack.post(f"/api/v1/reports/{rid}/copy", headers=h(creator))
    assert copy.status_code == 201, copy.text
    copied = [i for s in copy.json()["sections"] for i in s["items"]]
    assert [(i["kind"], i.get("title"), i.get("text")) for i in copied if i["kind"] != "widget"] == \
        [(i["kind"], i.get("title"), i.get("text")) for i in flat if i["kind"] != "widget"]


async def test_pack_two_builds_both_dashboards_with_the_catalogue_tabs(stack: httpx.AsyncClient, world: World,
                                                                      owner_conn: psycopg.Connection) -> None:
    """US-155: approval builds PMO — Projects (26 tabs) and PMO — Requests (12 tabs) in the catalogue's order, each
    tab in the catalogue layout (KPI strip, visuals, detail table, footnote); the shared filters are the catalogue's
    six (requests: no portfolio); the four data-gap screens stay unavailable with their gaps."""
    from test_starter_pack2_unit import PROJECT_TABS, REQUEST_TABS

    admin = world.tokens["admin"]
    tiles = {t["title"]: t for t in (await stack.get("/api/v1/published", headers=h(admin))).json()["items"]}
    projects = (await stack.get(f"/api/v1/published/{tiles['PMO — Projects']['id']}", headers=h(admin))).json()
    requests = (await stack.get(f"/api/v1/published/{tiles['PMO — Requests']['id']}", headers=h(admin))).json()
    assert [t["title"] for t in projects["tabs"]] == PROJECT_TABS
    assert [t["title"] for t in requests["tabs"]] == REQUEST_TABS
    assert {f["ref"] for f in projects["filters"]} == {f"dimension:{k}" for k in (
        "cio", "region", "portfolio", "project_manager", "priority", "platform")}
    assert not any("portfolio" in f["ref"] for f in requests["filters"])
    for tab in projects["tabs"] + requests["tabs"]:
        figures = [w for w in tab["widgets"] if w["visual"] == "figure"]
        assert 2 <= len(figures) <= 5 and all(w["placement"]["y"] == 0 for w in figures), tab["title"]
        assert sum(w["placement"]["w"] for w in figures) == 12, tab["title"]
        assert tab["footnotes"] and "Not shown on this source" not in tab["footnotes"][0], tab["title"]
    overview = requests["tabs"][0]
    assert overview["callouts"][0]["title"] == "No portfolio on requests"
    items = await starter_items(stack, admin)
    gaps = {k: i for k, i in items.items() if i["availability"] == "unavailable"}
    assert set(gaps) == {"portfolio_trends", "schedule_compliance", "mandatory_fields", "milestone_slippage"}
    # An activity tab runs for the Admin (all rows on every pack entity): PCRs reconcile with the oracle.
    import pmo_oracle as oracle

    pcr = next(t for t in projects["tabs"] if t["title"] == "PCR compliance")
    first = next(w for w in pcr["widgets"] if w["title"] == "PCRs")
    run = (await stack.post(f"/api/v1/widgets/{first['id']}/run", headers=h(admin), json={})).json()
    assert run["rows"] == [[oracle.CATALOGUE["total_pcrs"]]]
    # A CIO sees the PCRs of own projects only: the PCRs follow their project's scope (inherits_scope link).
    nomsa = {p["project_id"] for p in oracle.PROJECTS if p["cio"] == "Nomsa Osei"}
    own = sum(c["project_id"] in nomsa for c in oracle.ACTIVITY["change_requests"])
    run = (await stack.post(f"/api/v1/widgets/{first['id']}/run", headers=h(world.tokens["nomsa"]), json={})).json()
    assert run["rows"] == [[own]] and 0 < own < oracle.CATALOGUE["total_pcrs"]
