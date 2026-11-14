"""Starter pack updates (EPIC-11 part 2: US-120; FEAT-047; API-048; REQ-113).

Acceptance: TEST-056 — given a user copy of a starter report, when the Admin applies a pack update that changes
that starter report, the user copy is unchanged and the starter report reflects the new version. Also: the daily
check notifies once per version; a dashboard that includes the report gets a draft while its live version stays;
measure changes wait in a model draft and the starter reports follow on approval.
"""

from __future__ import annotations

import copy
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import psycopg
import pytest
from api_testkit import needs_db
from pmo_testkit import World, h, setup_world

from tela_api.packs.manifest import load_manifest
from tela_common import service_auth
from tela_common.ids import new_id

pytestmark = [needs_db, pytest.mark.integration]
MANIFEST = Path(__file__).resolve().parents[3] / "packs" / "pmo" / "manifest.yaml"
DELIVERY = "delayed_projects"
REMOVED = "capex_budget"


@pytest.fixture
async def world(stack: httpx.AsyncClient, owner_conn: psycopg.Connection, make_token: Callable[..., str],
                tpg_source: dict[str, Any]) -> World:
    return await setup_world(stack, owner_conn, make_token, tpg_source)


def release(owner: psycopg.Connection, version: str, change: Callable[[dict[str, Any]], None]) -> str:
    """Put a newer version of the PMO pack into the catalogue (what ``tela-admin load-packs`` does)."""
    raw = copy.deepcopy(load_manifest(MANIFEST)[1])  # as load-packs stores it: reports from the blueprints
    raw["pack_version"] = version
    change(raw)
    pack_id = new_id()
    owner.execute(
        "INSERT INTO model.starter_packs (id, pack_key, pack_version, title, detection_signature, manifest, "
        "manifest_sha256, released_at) VALUES (%s, %s, %s, %s, %s, %s, %s, now())",
        (pack_id, raw["pack_key"], version, raw["title"], json.dumps(raw["signature"]),
         json.dumps(raw, default=str), f"test-{version}"))
    return pack_id


def v130(raw: dict[str, Any]) -> None:
    """Report changes only: Delayed projects gains a widget, CAPEX budget leaves the pack."""
    raw["reports"][DELIVERY]["widgets"].append(
        {"title": "Open projects by platform", "measure": "open_projects", "breakdowns": ["platform"],
         "visual": "ranked_bar"})
    del raw["reports"][REMOVED]
    for dash in raw["dashboards"].values():
        dash["reports"] = [r for r in dash["reports"] if r != REMOVED]


def v140(raw: dict[str, Any]) -> None:
    """A measure is renamed and one is added (the model must approve them); Overview uses the new one."""
    v130(raw)
    next(m for m in raw["measures"] if m["key"] == "go_lives_0_30")["label"] = "Go-lives within 30 days"
    raw["measures"].append({"key": "all_project_records", "label": "All project records", "entity": "projects",
                            "plain_rule": "Every project record, open or closed.", "expression": {"agg": "count"}})
    raw["reports"]["projects_overview"]["widgets"].append(
        {"title": "All project records", "measure": "all_project_records", "visual": "figure"})


def widgets(report: dict[str, Any]) -> list[dict[str, Any]]:
    return [i for s in report["sections"] for i in s["items"] if i["kind"] == "widget"]


async def get(api: httpx.AsyncClient, token: str, path: str) -> Any:
    resp = await api.get(path, headers=h(token))
    assert resp.status_code == 200, resp.text
    return resp.json()


async def worker_check(api: httpx.AsyncClient, keys: dict[str, Any]) -> dict[str, Any]:
    token = service_auth.issue(keys["service"].encode(), sub="tela-worker", aud="tela-api-internal", ttl_s=60)
    resp = await api.post("/api/v1/internal/packs/update-check", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200, resp.text
    return dict(resp.json())


async def test_pack_update_leaves_copies_untouched(stack: httpx.AsyncClient, world: World,
                                                   owner_conn: psycopg.Connection,
                                                   signing_keys: dict[str, Any]) -> None:
    admin, creator = world.tokens["admin"], world.tokens["creator"]
    cid = world.connection_id
    items = {i["key"]: i for i in (await get(stack, admin, "/api/v1/admin/starter"))["items"]}
    rid = items[DELIVERY]["report_id"]
    before = await get(stack, creator, f"/api/v1/reports/{rid}")
    assert len(widgets(before)) == 8

    # A user copy, and a Creator's dashboard with the starter report, published live.
    copy = await stack.post(f"/api/v1/reports/{rid}/copy", headers=h(creator))
    assert copy.status_code == 201, copy.text
    copy_before = await get(stack, creator, f"/api/v1/reports/{copy.json()['id']}")
    created = await stack.post("/api/v1/dashboards", headers=h(creator), json={
        "title": "Gulf delivery", "report_ids": [rid], "layout": "tabs"})
    assert created.status_code == 201, created.text
    d = created.json()
    published = await stack.post(f"/api/v1/dashboards/{d['id']}/publish", headers=h(admin),
                                 json={"target_roles": ["creator"]})
    assert published.status_code == 201, published.text
    live = await get(stack, creator, f"/api/v1/published/{d['id']}")

    # Nothing newer yet.
    assert (await get(stack, admin, "/api/v1/admin/packs/updates"))["items"] == []
    assert (await worker_check(stack, signing_keys))["notified"] == 0
    assert (await stack.get("/api/v1/admin/packs/updates", headers=h(creator))).status_code == 403

    # 2.3.0 is released: the daily check tells the Admins once, with a change summary.
    pack_130 = release(owner_conn, "2.3.0", v130)
    first = await worker_check(stack, signing_keys)
    assert first["models"] == 1 and first["notified"] == 1
    assert (await worker_check(stack, signing_keys))["notified"] == 0
    note = (await get(stack, admin, "/api/v1/notifications?kind=pack.update_available"))["items"]
    assert len(note) == 1 and "2.3.0" in note[0]["title"] and "never touched" in note[0]["body"]
    assert "starter reports: 1 changed, 1 removed" in note[0]["body"]

    updates = (await get(stack, admin, "/api/v1/admin/packs/updates"))["items"]
    assert len(updates) == 1
    u = updates[0]
    assert (u["installed"], u["available"], u["status"], u["pack_id"]) == ("2.2.1", "2.3.0", "available", pack_130)
    assert u["model_draft_required"] is False and u["user_copies_affected"] == 0 and u["user_copies_unaffected"] == 1
    reports = {c["key"]: c["change"] for c in u["changes"] if c["kind"] == "report"}
    assert reports == {DELIVERY: "changed", REMOVED: "removed"}
    assert [(x["dashboard_id"], x["live_version_stays"]) for x in u["dashboards_getting_new_draft"]] == [(d["id"], 1)]

    wrong = await stack.post(f"/api/v1/admin/packs/{pack_130}/update", headers=h(creator), json={"connection_id": cid})
    assert wrong.status_code == 403
    applied = await stack.post(f"/api/v1/admin/packs/{pack_130}/update", headers=h(admin), json={"connection_id": cid})
    assert applied.status_code == 200, applied.text
    assert applied.json()["status"] == "applied" and applied.json()["to"] == "2.3.0"
    again = await stack.post(f"/api/v1/admin/packs/{pack_130}/update", headers=h(admin), json={"connection_id": cid})
    assert again.status_code == 409

    # TEST-056: the starter report reflects the new version (same report, rebuilt in place)...
    after = await get(stack, creator, f"/api/v1/reports/{rid}")
    assert len(widgets(after)) == 9 and "Open projects by platform" in json.dumps(after)
    # ...and the user copy is unchanged.
    copy_after = await get(stack, creator, f"/api/v1/reports/{copy.json()['id']}")
    assert [w["widget_id"] for w in widgets(copy_after)] == [w["widget_id"] for w in widgets(copy_before)]
    assert copy_after["revision"] == copy_before["revision"]
    # The live dashboard is unchanged; its draft has the new version and the owner was told.
    still = await get(stack, creator, f"/api/v1/published/{d['id']}")
    assert still["version"] == 1 and len(still["tabs"][0]["widgets"]) == len(live["tabs"][0]["widgets"])
    draft = await get(stack, creator, f"/api/v1/dashboards/{d['id']}")
    assert draft["draft_differs_from_live"] and draft["reports"][0]["widgets"] == 9
    told = (await get(stack, creator, "/api/v1/notifications?kind=starter.updated"))["items"]
    assert len(told) == 1 and "republish" in told[0]["body"]
    # CAPEX budget left the pack: gone from the starter list and the starter dashboard.
    items = {i["key"]: i for i in (await get(stack, admin, "/api/v1/admin/starter"))["items"]}
    assert REMOVED not in items and items[DELIVERY]["pack"]["version"] == "2.3.0"
    assert items[DELIVERY]["report_id"] == rid and items[DELIVERY]["copies"] == 1
    assert (await get(stack, admin, "/api/v1/admin/packs/updates"))["items"] == []

    # 2.4.0 changes measures: they wait in a model draft; nothing changes until it is approved.
    pack_140 = release(owner_conn, "2.4.0", v140)
    staged = await stack.post(f"/api/v1/admin/packs/{pack_140}/update", headers=h(admin), json={"connection_id": cid})
    assert staged.status_code == 200, staged.text
    assert staged.json()["status"] == "awaiting_approval"
    changes = {(c["kind"], c["key"]): c["change"] for c in staged.json()["changes"]}
    assert changes[("measure", "go_lives_0_30")] == "changed"
    assert changes[("measure", "all_project_records")] == "added"
    assert changes[("report", "projects_overview")] == "changed"
    assert (await stack.post(f"/api/v1/admin/packs/{pack_140}/update", headers=h(admin),
                             json={"connection_id": cid})).status_code == 409
    pending = (await get(stack, admin, "/api/v1/admin/packs/updates"))["items"]
    assert [(p["status"], p["pending"]["version"]) for p in pending] == [("awaiting_approval", "2.4.0")]
    model_draft = await get(stack, admin, f"/api/v1/models/{world.model_id}/draft")
    measures = {m["key"]: m for m in model_draft["measures"]}
    assert measures["go_lives_0_30"]["label"] == "Go-lives within 30 days" and "all_project_records" in measures
    overview_id = items["projects_overview"]["report_id"]
    assert len(widgets(await get(stack, admin, f"/api/v1/reports/{overview_id}"))) == 7

    approved = await stack.post(f"/api/v1/models/{world.model_id}/approve", headers=h(admin),
                                json={"draft_etag": model_draft["draft_etag"]})
    assert approved.status_code == 201, approved.text
    new_version = approved.json()["model_version_id"]
    overview = await get(stack, admin, f"/api/v1/reports/{overview_id}")
    assert len(widgets(overview)) == 8 and "All project records" in json.dumps(overview)
    items = {i["key"]: i for i in (await get(stack, admin, "/api/v1/admin/starter"))["items"]}
    assert items[DELIVERY]["pack"]["version"] == "2.4.0" and items[DELIVERY]["report_id"] == rid
    spec_versions = {r[0] for r in owner_conn.execute(
        "SELECT DISTINCT w.model_version_id::text FROM content.report_items i JOIN content.widgets w ON "
        "w.id = i.widget_id WHERE i.report_id = %s", (rid,)).fetchall()}
    assert spec_versions == {new_version}
    # The copy still runs on the version it was copied at and was never rebuilt.
    copy_final = await get(stack, creator, f"/api/v1/reports/{copy.json()['id']}")
    assert [w["widget_id"] for w in widgets(copy_final)] == [w["widget_id"] for w in widgets(copy_before)]
    audit = (await get(stack, admin, "/api/v1/admin/audit?action=starter_pack.updated"))["items"]
    assert {a["details"]["to"] for a in audit} == {"2.3.0", "2.4.0"}
