"""Report blueprints and starter dashboards authored in a pack (ENH-025, US-234; FEAT-075), on the TPG fixture.

A tenant draft cloned from PMO: its blueprints all pass; edits need ``If-Match`` and are refused with each problem's
path; a system pack is read-only; only ``packs.author`` may author; another tenant never sees the draft. The live
preview lays a blueprint out on the connection's approved model and runs it. "Save as blueprint" of a report built
from the PMO projects overview gives back that blueprint's widgets, which install as the same starter report
widgets; a custom SQL widget is named and left out. A dashboard saved as a starter dashboard keeps its tabs in order.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import httpx
import psycopg
import pytest
from api_testkit import OTHER_ISSUER, bootstrap, needs_db
from pmo_testkit import World, h, setup_world

from tela_api.core.db import tenant_tx
from tela_api.packs.manifest import source_manifest
from tela_api.packs.source import db_source, document_sha
from tela_common.ids import new_id

pytestmark = [needs_db, pytest.mark.integration]
KEY = "acme_pmo"


@pytest.fixture
async def world(stack: httpx.AsyncClient, owner_conn: psycopg.Connection, make_token: Callable[..., str],
                tpg_source: dict[str, Any]) -> World:
    return await setup_world(stack, owner_conn, make_token, tpg_source)


def _clone(owner: psycopg.Connection, tenant: str, *, empty_blueprints: bool = False) -> str:
    """A tenant draft with PMO's manifest and documents under its own key (what US-232's Clone makes)."""
    sys_id, head = owner.execute("SELECT id, manifest FROM model.starter_packs WHERE pack_key = 'pmo_tpg' AND "
                                 "tenant_id IS NULL").fetchone()
    head = {**head, "pack_key": KEY, "pack_version": "0.1.0", "title": "Acme PMO"}
    docs = dict(owner.execute("SELECT kind, body FROM model.pack_documents WHERE pack_id = %s", (sys_id,)).fetchall())
    if empty_blueprints:
        docs["report_blueprints"] = {**docs["report_blueprints"], "blueprints": []}
        head["dashboards"] = {k: {**d, "reports": []} for k, d in head["dashboards"].items()}
    pid = new_id()
    owner.execute("INSERT INTO model.starter_packs (id, tenant_id, pack_key, pack_version, title, detection_signature, "
                  "manifest, manifest_sha256, released_at, status, based_on_pack_id) VALUES (%s, %s, %s, '0.1.0', "
                  "'Acme PMO', %s, %s, %s, now(), 'draft', %s)",
                  (pid, tenant, KEY, json.dumps(head["signature"]), json.dumps(head), document_sha(head), sys_id))
    for kind, body in docs.items():
        owner.execute("INSERT INTO model.pack_documents (id, pack_id, tenant_id, kind, body, sha256) VALUES "
                      "(%s, %s, %s, %s, %s, %s)", (new_id(), pid, tenant, kind, json.dumps(body), document_sha(body)))
    return pid


def _get(api: httpx.AsyncClient, token: str, pack_id: str) -> Any:
    return api.get(f"/api/v1/packs/{pack_id}/reports", headers=h(token))


async def test_edit_blueprints_and_dashboards_of_a_draft(stack: httpx.AsyncClient, world: World,
                                                         owner_conn: psycopg.Connection) -> None:
    admin = world.tokens["admin"]
    pid = _clone(owner_conn, world.tenant_id)
    got = await _get(stack, admin, pid)
    assert got.status_code == 200, got.text
    body = got.json()
    assert body["problems"] == {} and not body["pack"]["read_only"] and got.headers["ETag"] == body["etag"]
    assert [d["key"] for d in body["dashboards"]] == ["projects", "requests"]
    assert len(body["blueprints"]) > 30 and {m["key"] for m in body["names"]["measures"]} >= {"open_projects"}
    bp = next(b for b in body["blueprints"] if b["id"] == "projects_overview")
    new = {**bp, "id": "gulf_overview", "title": "Gulf overview", "tab": "Gulf",
           "filters": [{"ref": "region", "op": "in", "values": ["KSA", "GCC - UAE"]}]}
    url = f"/api/v1/packs/{pid}/reports/blueprints/gulf_overview"
    # If-Match is required and must be current.
    assert (await stack.put(url, headers=h(admin), json={"blueprint": new})).status_code == 412
    assert (await stack.put(url, headers=h(admin, **{"If-Match": '"stale"'}),
                            json={"blueprint": new})).status_code == 412
    # A blueprint naming what the pack lacks is refused with the part's path.
    bad = await stack.put(url, headers=h(admin, **{"If-Match": body["etag"]}),
                          json={"blueprint": {**new, "kpis": ["no_such_measure"]}})
    assert bad.status_code == 400, bad.text
    assert bad.json()["error"]["details"]["problems"][0]["path"] == "kpis.0"
    saved = await stack.put(url, headers=h(admin, **{"If-Match": body["etag"]}),
                            json={"blueprint": new, "tones": {"in_delivery": {"warning": 5, "critical": 10}}})
    assert saved.status_code == 200, saved.text
    out = saved.json()
    projects = next(d for d in out["dashboards"] if d["key"] == "projects")
    assert projects["reports"][-1] == "gulf_overview" and out["tones"]["in_delivery"] == {"warning": 5, "critical": 10}
    # The dashboard: its tabs in a new order and its shared filters.
    tabs = ["gulf_overview", *[r for r in projects["reports"] if r != "gulf_overview"]]
    dash = await stack.put(f"/api/v1/packs/{pid}/reports/dashboards/projects",
                           headers=h(admin, **{"If-Match": out["etag"]}),
                           json={"title": "Acme — Projects", "reports": tabs, "filters": ["region", "cio"]})
    assert dash.status_code == 200, dash.text
    # What install reads: the stored draft gives the new starter report as the dashboard's first tab.
    async with tenant_tx(world.tenant_id) as conn:
        src = await db_source(conn, pid)
    assert src is not None
    manifest, _, _ = source_manifest(src)
    assert manifest.dashboards["projects"]["reports"][0] == "gulf_overview"
    rep = manifest.reports["gulf_overview"]
    assert rep["title"] == "Projects — Gulf overview" and rep["tab"] == "Gulf"
    assert all({"ref": "region", "op": "in", "values": ["KSA", "GCC - UAE"]} in w["filters"] for w in rep["widgets"])
    assert next(w for w in rep["widgets"] if w["measure"] == "in_delivery")["tone"] == {"warning": 5, "critical": 10}
    # Delete the blueprint: it leaves its tab.
    etag = dash.json()["etag"]
    gone = await stack.delete(url, headers=h(admin, **{"If-Match": etag}))
    assert gone.status_code == 200 and "gulf_overview" not in next(
        d for d in gone.json()["dashboards"] if d["key"] == "projects")["reports"]
    events = {r[0] for r in owner_conn.execute("SELECT action FROM ops.audit_events WHERE object_id = %s", (pid,))}
    assert events >= {"pack.blueprint_saved", "pack.dashboard_saved", "pack.blueprint_deleted"}

    # A system pack is read-only; a Creator may not author; another tenant never sees the draft.
    sys_id = str(owner_conn.execute("SELECT id FROM model.starter_packs WHERE pack_key = 'pmo_tpg'").fetchone()[0])
    sys_body = (await _get(stack, admin, sys_id)).json()
    assert sys_body["pack"]["read_only"] and sys_body["pack"]["system"]
    ro = await stack.put(f"/api/v1/packs/{sys_id}/reports/blueprints/x1",
                         headers=h(admin, **{"If-Match": sys_body["etag"]}), json={"blueprint": new})
    assert ro.status_code == 409 and "clone it" in ro.json()["error"]["message"]
    assert (await _get(stack, world.tokens["creator"], pid)).status_code == 403
    globex = bootstrap(owner_conn, "globex", OTHER_ISSUER, {"globex-admins": "admin"})
    theirs = _clone(owner_conn, globex)
    assert (await _get(stack, admin, theirs)).status_code == 404
    assert (await stack.put(f"/api/v1/packs/{theirs}/reports/blueprints/x1", headers=h(admin, **{"If-Match": "x"}),
                            json={"blueprint": new})).status_code == 404


async def test_preview_lays_out_and_runs_a_blueprint(stack: httpx.AsyncClient, world: World,
                                                     owner_conn: psycopg.Connection) -> None:
    admin = world.tokens["admin"]
    pid = _clone(owner_conn, world.tenant_id)
    body = (await _get(stack, admin, pid)).json()
    bp = next(b for b in body["blueprints"] if b["id"] == "projects_overview")
    bp = {**bp, "widgets": [*bp["widgets"], {"title": "Made up", "measure": "no_such_measure",
                                              "visual": "ranked_bar"}]}
    resp = await stack.post(f"/api/v1/packs/{pid}/reports/preview", headers=h(admin),
                            json={"blueprint": bp, "connection_id": world.connection_id})
    assert resp.status_code == 200, resp.text
    out = resp.json()
    assert out["problems"] and out["problems"][0]["path"] == "widgets.2"  # shown, and still previewed
    headline, section = out["sections"]
    assert [w["title"] for w in headline["widgets"]][:2] == ["Projects in delivery", "Overdue go-lives"]
    assert section["blueprint"]["id"] == "projects_overview" and section["footnote"]
    refs = {w["ref"] for s in out["sections"] for w in s["widgets"]}
    assert set(out["results"]) == refs and {p["widget_ref"] for p in out["layout_preview"]} == refs
    figure = out["results"][headline["widgets"][0]["ref"]]
    assert figure["rows"] and figure["rows"][0][0] > 0
    table = next(w for w in section["widgets"] if w["visual"] == "table")
    assert table["spec"]["visual"]["detail"]["columns"][0] == "project_number"
    assert any(g["request_part"] == "Made up" for g in out["gaps"])


def _key(w: dict[str, Any]) -> tuple[Any, ...]:
    return (w["title"], w["measure"], tuple(w.get("extra_measures", [])), tuple(w.get("breakdowns", [])),
            w["visual"], json.dumps(w.get("detail"), sort_keys=True), json.dumps(w.get("filters"), sort_keys=True))


async def test_save_report_and_dashboard_as_blueprints(stack: httpx.AsyncClient, world: World,
                                                       owner_conn: psycopg.Connection) -> None:
    admin = world.tokens["admin"]
    # A report built by the planner from the PMO projects overview, plus a custom SQL widget.
    plan = (await stack.post("/api/v1/reports/plan", headers=h(admin),
                             json={"request": "Projects overview", "connection_id": world.connection_id})).json()
    assert [s["key"] for s in plan["sections"]] == ["headline", "bp_projects_overview"], plan["sections"]
    built = await stack.post(f"/api/v1/reports/plans/{plan['plan_id']}/build",
                             headers=h(admin, **{"Idempotency-Key": "bp1"}))
    assert built.status_code == 201, built.text
    report_id = built.json()["report_id"]
    section = owner_conn.execute("SELECT id FROM content.report_sections WHERE report_id = %s ORDER BY position "
                                 "DESC LIMIT 1", (report_id,)).fetchone()[0]
    user_id = owner_conn.execute("SELECT owner_id FROM content.reports WHERE id = %s", (report_id,)).fetchone()[0]
    wid = new_id()
    owner_conn.execute("INSERT INTO content.widgets (id, tenant_id, title, spec, visual, model_version_id, "
                       "connection_id, certification_state, owner_id, created_by) VALUES (%s, %s, 'My SQL', %s, "
                       "'table', %s, %s, 'custom_sql', %s, %s)",
                       (wid, world.tenant_id, json.dumps({"kind": "custom_sql", "custom_sql_id": new_id(), "sql": "SELECT 1",
                                                         "model_version_id": world.version_id,
                                                         "connection_id": world.connection_id,
                                                         "entities": ["projects"]}),
                        world.version_id, world.connection_id, user_id, user_id))
    owner_conn.execute("INSERT INTO content.report_items (id, tenant_id, report_id, section_id, item_kind, widget_id, "
                       "grid_x, grid_y, grid_w, grid_h) VALUES (%s, %s, %s, %s, 'widget', %s, 0, 99, 6, 4)",
                       (new_id(), world.tenant_id, report_id, section, wid))

    pid = _clone(owner_conn, world.tenant_id, empty_blueprints=True)
    etag = (await _get(stack, admin, pid)).json()["etag"]
    shown = await stack.post(f"/api/v1/packs/{pid}/reports/from-report", headers=h(admin),
                             json={"report_id": report_id, "dashboard": "projects", "id": "overview_copy",
                                   "save": False})
    assert shown.status_code == 200 and not shown.json()["saved"], shown.text
    assert shown.json()["left_out"] == [{"title": "My SQL", "reason": "custom SQL"}]
    saved = await stack.post(f"/api/v1/packs/{pid}/reports/from-report", headers=h(admin, **{"If-Match": etag}),
                             json={"report_id": report_id, "dashboard": "projects", "id": "overview_copy"})
    assert saved.status_code == 200, saved.text
    raw = saved.json()["blueprint"]
    assert raw["kpis"] == ["in_delivery", "overdue_go_lives", "net_forecast_variance", "key_priorities_attention"]
    assert next(d for d in saved.json()["reports"]["dashboards"] if d["key"] == "projects")["reports"] == \
        ["overview_copy"]

    # On install the saved blueprint gives the same starter report widgets as PMO's own blueprint (tones aside).
    async with tenant_tx(world.tenant_id) as conn:
        draft = await db_source(conn, pid)
        system = await db_source(conn, str(owner_conn.execute(
            "SELECT id FROM model.starter_packs WHERE pack_key = 'pmo_tpg'").fetchone()[0]))
    assert draft is not None and system is not None
    mine, _, _ = source_manifest(draft)
    theirs, _, _ = source_manifest(system)
    assert [_key(w) for w in mine.reports["overview_copy"]["widgets"]] == \
        [_key(w) for w in theirs.reports["projects_overview"]["widgets"]]
    assert mine.reports["overview_copy"]["footnote"] == theirs.reports["projects_overview"]["footnote"]

    # A dashboard with that report: a starter dashboard of the same tabs and its shared filters.
    dash = await stack.post("/api/v1/dashboards", headers=h(admin), json={
        "title": "Ops review", "report_ids": [report_id], "shared_filters": [{"ref": "dimension:region"}]})
    assert dash.status_code == 201, dash.text
    etag = saved.json()["reports"]["etag"]
    made = await stack.post(f"/api/v1/packs/{pid}/reports/from-dashboard", headers=h(admin, **{"If-Match": etag}),
                            json={"dashboard_id": dash.json()["id"]})
    assert made.status_code == 200, made.text
    out = made.json()
    bid = out["blueprints"][0]["id"]
    assert out["dashboard"] == {"key": "ops_review", "title": "Ops review", "reports": [bid], "filters": ["region"]}
    assert {"title": "My SQL", "reason": "custom SQL", "report": plan["title"]} in out["left_out"]
    stored = next(d for d in out["reports"]["dashboards"] if d["key"] == "ops_review")
    assert stored["reports"] == [bid] and stored["filters"] == ["region"]
