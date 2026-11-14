"""Dashboards: compose, preview as role, pre-publish checks, publish, versions and restore
(API-037 to API-041; FEAT-027, FEAT-038 to FEAT-040; RULE-009, RULE-015, RULE-016, RULE-024; US-073, US-088,
US-090, US-091, US-093).

* A dashboard draft is an ordered list of the owner's reports (possibly from different connections) shown as
  tabs or grouped, with shared filters taken from model dimensions. Each shared filter shows which widgets it
  reaches; a widget it cannot reach must be explicitly excluded before publishing.
* Publishing (Admin only, DEC-05) runs the six RULE-024 checks for every target role against the draft as it
  is frozen. If any fails nothing changes: the live version stays live. If all pass, an immutable version
  vN+1 is stored, the previous live version becomes Superseded and the audience is notified.
* Restore vK creates vN+1 with vK's content after re-running the checks (RULE-016). Unpublish withdraws the
  live version.
* Preview as role renders the draft under the role's scope and hidden fields. Row counts are shown to
  Admins only; a Creator sees which widgets show or hide and why, never more data than they could (A1).
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, Header, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from tela_api import outbox
from tela_api.audit import service as audit
from tela_api.content.access import own_dashboard, readable_dashboard_draft
from tela_api.content.common import (
    canonical,
    is_custom,
    notify,
    principal_for_role,
    principal_for_user,
    remember,
    replay,
    roles_by_ref,
    run_spec,
    users_with_permission,
    users_with_roles,
)
from tela_api.content.render import freeze_dashboard, render_frozen, snapshots_for, widget_items
from tela_api.content.reports import report_state
from tela_api.content.starters import starter_reports
from tela_api.core.db import fetch_all, fetch_one, tenant_tx
from tela_api.governance.certification import enqueue_for_widgets
from tela_api.identity.deps import require
from tela_api.identity.permissions import Perm
from tela_api.identity.principal import Principal
from tela_api.identity.scope import resolve_scope
from tela_api.model.store import current_version_id
from tela_api.query.service import QueryContext, QueryService
from tela_common.errors import ErrorCode, TelaError
from tela_common.ids import new_id
from tela_query_spec.model import ModelSnapshot
from tela_query_spec.spec import ref_key

router = APIRouter(prefix="/api/v1", tags=["dashboards"])
PUBLISH_ROUTE = "POST /dashboards/publish"
ONLY_ADMINS = "Only an Admin can publish to roles. Use Request publish to ask one."
CHECK_LABELS = {
    "all_widgets_certified": "Every widget is certified",
    "returns_data_for_each_role": "Every widget returns data for each role",
    "field_visibility": "No widget depends on a field hidden for a role",
    "shared_filters_reach_every_widget": "Shared filters reach every widget",
    "model_version_current": "Widgets use the current model version",
    "publish_permission": "You can publish to roles",
}


def _service(request: Request) -> QueryService:
    service: QueryService = request.app.state.query_service
    return service


def _rid(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


# --------------------------------------------------------------------------- reach (FEAT-038)


def reach(frozen: dict[str, Any], snaps: dict[str, ModelSnapshot]) -> list[dict[str, Any]]:
    out = []
    for f in frozen.get("shared_filters", []):
        excluded = set(f.get("exclude_widget_ids", []))
        label = next((s.dimension(ref_key(f["ref"])).label for s in snaps.values()
                      if s.dimension(ref_key(f["ref"])) is not None), ref_key(f["ref"]).replace("_", " ").capitalize())
        widgets = []
        for r in frozen["reports"]:
            for s in r["sections"]:
                for i in s["items"]:
                    if i["kind"] != "widget":
                        continue
                    snap = snaps[i["spec"]["model_version_id"]]
                    dim = snap.dimension(ref_key(f["ref"]))
                    if is_custom(i["spec"]):
                        # RULE-007: custom SQL follows page filters through :page_filters on the tables it reads.
                        ok = dim is not None and i["spec"].get("has_page_filters", False) and \
                            dim.entity in i["spec"].get("entities", [])
                    else:
                        m = snap.measure(ref_key(i["spec"]["measure"]["ref"]))
                        ok = dim is not None and m is not None and snap.join_path(m.entity, dim.entity) is not None
                    widgets.append({"widget_id": i["widget_id"], "item_id": i["item_id"], "title": i["title"],
                                    "report": r["title"], "reached": ok, "excluded": i["widget_id"] in excluded,
                                    "message": None if ok else f"Not affected by {label}"})
        out.append({"ref": f["ref"], "label": label, "widgets": widgets,
                    "reaches_all": all(w["reached"] or w["excluded"] for w in widgets)})
    return out


def _definitions(spec: dict[str, Any]) -> list[dict[str, str]]:
    refs = list(spec.get("definition_refs", []))
    if is_custom(spec) and not any(r.get("kind") == "custom_sql" for r in refs):
        refs.append({"kind": "custom_sql", "id": spec["custom_sql_id"]})
    return refs


def _entities_of(spec: dict[str, Any], snap: ModelSnapshot) -> list[str]:
    if is_custom(spec):
        return list(spec.get("entities", []))
    m = snap.measure(ref_key(spec["measure"]["ref"]))
    return [m.entity] if m else []


# --------------------------------------------------------------------------- prechecks (RULE-024)


async def _run_for(request: Request, p: Principal, spec: dict[str, Any]) -> tuple[str, dict[str, Any] | None,
                                                                                    TelaError | None]:
    try:
        res = await run_spec(_service(request), spec, p, QueryContext(surface="preview", request_id=_rid(request)))
        return ("hidden" if res.get("hidden") else "empty" if res["row_count"] == 0 else "ok"), res, None
    except TelaError as exc:
        return "error", None, exc


def _scope_reason(exc: TelaError, role_name: str) -> tuple[str, bool]:
    """(message, depends_on_person). A role whose policy reads a personal attribute cannot be previewed as a role."""
    attr = exc.details.get("missing_attribute")
    if attr:
        return f"Depends on each person's {attr} — preview as a named person to see rows.", True
    if exc.code == ErrorCode.SCOPE_UNRESOLVED:
        return f"No data scope is set up for {role_name}.", False
    return exc.message, False


async def run_prechecks(request: Request, principal: Principal, frozen: dict[str, Any],
                        roles: list[dict[str, Any]], *, version_check_blocks: bool = True) -> dict[str, Any]:
    async with tenant_tx(principal.tenant_id) as conn:
        snaps = await snapshots_for(conn, principal.tenant_id, frozen)
        role_principals = [(r, await principal_for_role(conn, principal, r)) for r in roles]
        current = {cid: await current_version_id(conn, cid) for cid in {s.connection_id for s in snaps.values()}}
    items = widget_items(frozen)
    report_of = {i["item_id"]: r["title"] for r in frozen["reports"] for s in r["sections"] for i in s["items"]}
    report_id_of = {i["item_id"]: r["id"] for r in frozen["reports"] for s in r["sections"] for i in s["items"]}
    checks: dict[str, list[dict[str, Any]]] = {k: [] for k in CHECK_LABELS}
    notes: list[str] = []

    for i in items:
        if i.get("badge", "certified") != "certified":
            checks["all_widgets_certified"].append({
                "widget_id": i["widget_id"], "item_id": i["item_id"], "title": i["title"],
                "report": report_of[i["item_id"]], "report_id": report_id_of[i["item_id"]],
                "message": "Uses custom SQL" if is_custom(i["spec"]) else "Uses a personal definition",
                "fixes": [{"action": "request_certification", "items": _definitions(i["spec"])},
                          {"action": "remove_widget"}]})
        snap = snaps[i["spec"]["model_version_id"]]
        if current.get(snap.connection_id) != snap.model_version_id:
            checks["model_version_current"].append({
                "widget_id": i["widget_id"], "item_id": i["item_id"], "title": i["title"],
                "report": report_of[i["item_id"]], "report_id": report_id_of[i["item_id"]],
                "message": f"Pinned to model v{snap.version_no}; a newer version is approved",
                "fixes": [{"action": "upgrade_widget"}]})
    for f in reach(frozen, snaps):
        for w in f["widgets"]:
            if not w["reached"] and not w["excluded"]:
                checks["shared_filters_reach_every_widget"].append({
                    "widget_id": w["widget_id"], "item_id": w["item_id"], "title": w["title"], "report": w["report"],
                    "report_id": report_id_of.get(w["item_id"]),
                    "filter": f["ref"], "message": w["message"],
                    "fixes": [{"action": "exclude_widget", "filter": f["ref"]},
                              {"action": "map_filter", "available": False,
                               "note": "Mapping a filter across sources is not available yet"}]})
    for role, rp in role_principals:
        for i in items:
            if role["id"] in i.get("hidden_for_roles", []) or str(role["id"]) in i.get("hidden_for_roles", []):
                continue
            status, _res, exc = await _run_for(request, rp, {k: v for k, v in i["spec"].items() if k != "compare"})
            base = {"widget_id": i["widget_id"], "item_id": i["item_id"], "title": i["title"],
                    "report": report_of[i["item_id"]], "report_id": report_id_of[i["item_id"]], "role": role["key"],
                    "role_name": role["name"]}
            if status == "hidden":
                checks["field_visibility"].append({**base, "message": f"Uses a field hidden for {role['name']}",
                                                   "fixes": [{"action": "hide_for_role", "role_id": str(role["id"])},
                                                             {"action": "remove_widget"}]})
            elif status == "empty" and not i.get("empty_expected"):
                checks["returns_data_for_each_role"].append({
                    **base, "message": f"Returns no rows for {role['name']}",
                    "fixes": [{"action": "mark_empty_expected"}, {"action": "remove_widget"},
                              {"action": "remove_role", "role": role["key"]}]})
            elif status == "error" and exc is not None:
                message, per_person = _scope_reason(exc, role["name"])
                if per_person:
                    note = f"{role['name']}: {message}"
                    if note not in notes:
                        notes.append(note)
                    continue
                checks["returns_data_for_each_role"].append({
                    **base, "message": message, "fixes": [{"action": "remove_widget"},
                                                          {"action": "remove_role", "role": role["key"]}]})
    if not principal.has(Perm.DASHBOARDS_PUBLISH):
        checks["publish_permission"].append({"message": ONLY_ADMINS, "fixes": [{"action": "request_publish"}]})
    if not version_check_blocks and checks["model_version_current"]:
        notes += [f"{x['title']}: {x['message']} — restored as published." for x in checks["model_version_current"]]
        checks["model_version_current"] = []
    if not roles:
        checks["returns_data_for_each_role"].append({"message": "Choose at least one role to publish to.",
                                                     "fixes": []})
    out = [{"check": k, "label": CHECK_LABELS[k], "passed": not v, "items": v} for k, v in checks.items()]
    return {"passed": all(c["passed"] for c in out), "checks": out, "notes": notes,
            "roles": [{"id": str(r["id"]), "key": r["key"], "name": r["name"]} for r in roles]}


# --------------------------------------------------------------------------- CRUD (API-037)


class SharedFilter(BaseModel):
    ref: str = Field(pattern=r"^dimension:[a-z_][a-z0-9_]{0,62}$")
    exclude_widget_ids: list[str] = Field(default_factory=list, max_length=100)


class DashboardIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    report_ids: list[str] = Field(default_factory=list, max_length=20)
    layout: str = Field(default="tabs", pattern="^(tabs|grouped)$")
    shared_filters: list[SharedFilter] = Field(default_factory=list, max_length=10)


class DashboardPatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    layout: str | None = Field(default=None, pattern="^(tabs|grouped)$")
    shared_filters: list[SharedFilter] | None = Field(default=None, max_length=10)


class OrderItem(BaseModel):
    report_id: str
    group_title: str | None = Field(default=None, max_length=120)


class OrderIn(BaseModel):
    items: list[OrderItem] = Field(max_length=20)


async def _set_reports(conn: AsyncConnection, principal: Principal, d: dict[str, Any], items: list[OrderItem]) -> None:
    ids = [i.report_id for i in items]
    if len(set(ids)) != len(ids):
        raise TelaError(ErrorCode.VALIDATION_ERROR, "A report can be on a dashboard once.")
    for rid in ids:
        try:
            r = await fetch_one(conn, "SELECT owner_id, origin FROM content.reports WHERE id = CAST(:r AS uuid) AND "
                                      "deleted_at IS NULL", {"r": rid})
        except Exception:  # noqa: BLE001
            r = None
        if r is None or (r["origin"] != "starter" and str(r["owner_id"]) != str(d["owner_id"])):
            raise TelaError(ErrorCode.NOT_FOUND,
                            "A dashboard can only contain its owner's reports and starter reports.")
    await conn.execute(text("DELETE FROM content.dashboard_reports WHERE dashboard_id = :d"), {"d": d["id"]})
    for pos, i in enumerate(items):
        await conn.execute(
            text("INSERT INTO content.dashboard_reports (tenant_id, dashboard_id, report_id, position, group_title) "
                 "VALUES (:t, :d, CAST(:r AS uuid), :p, :g)"),
            {"t": principal.tenant_id, "d": d["id"], "r": i.report_id, "p": pos, "g": i.group_title})


async def _detail(conn: AsyncConnection, principal: Principal, d: dict[str, Any]) -> dict[str, Any]:
    frozen = await freeze_dashboard(conn, d)
    snaps = await snapshots_for(conn, principal.tenant_id, frozen)
    live = await fetch_one(conn, "SELECT v.id, v.version_no, v.target_role_ids, v.published_at, u.display_name FROM "
                                 "content.dashboard_versions v JOIN iam.users u ON u.id = v.published_by WHERE "
                                 "v.id = :v", {"v": d["live_version_id"]}) if d["live_version_id"] else None
    roles = await fetch_all(conn, "SELECT id, key, name FROM iam.roles WHERE id = ANY(CAST(:r AS uuid[]))",
                            {"r": [str(x) for x in live["target_role_ids"]] if live else []})
    owner = await fetch_one(conn, "SELECT display_name FROM iam.users WHERE id = :u", {"u": d["owner_id"]})
    connections = await fetch_all(conn, "SELECT id, name FROM conn.connections WHERE id = ANY(CAST(:c AS uuid[]))",
                                  {"c": sorted({s.connection_id for s in snaps.values()})})
    dims: dict[str, dict[str, Any]] = {}
    for s in snaps.values():
        for dim in s.dimensions:
            if not dim.is_temporal:
                dims.setdefault(dim.key, {"ref": f"dimension:{dim.key}", "label": dim.label})
    live_frozen = await _live_frozen(conn, d) if live else {}
    changed = live is not None and canonical(live_frozen) != canonical(frozen)
    in_draft = {r["id"] for r in frozen["reports"]}
    starters = await starter_reports(conn, sorted(in_draft | {r["id"] for r in live_frozen.get("reports", [])}))
    involved = str(d["owner_id"]) == principal.user_id or principal.has(Perm.DASHBOARDS_PUBLISH)
    latest = await _requests(conn, "r.dashboard_id = :d ORDER BY r.created_at DESC LIMIT 1",
                             {"d": d["id"]}) if involved else []
    return {
        "id": str(d["id"]), "title": d["title"], "layout": d["layout"], "status": d["status"],
        "owner": {"id": str(d["owner_id"]), "name": owner["display_name"] if owner else None},
        "can_edit": str(d["owner_id"]) == principal.user_id, "can_publish": principal.has(Perm.DASHBOARDS_PUBLISH),
        "shared_filters": d["shared_filters"], "reach": reach(frozen, snaps),
        "available_filters": sorted(dims.values(), key=lambda x: x["label"]),
        "reports": [{"id": r["id"], "title": r["title"], "group_title": r["group_title"],
                     "widgets": sum(1 for s in r["sections"] for i in s["items"] if i["kind"] == "widget"),
                     "starter": r["id"] in starters}
                    for r in frozen["reports"]],
        # RULE-017: starter reports an Admin deleted that the live version still shows ("republish to apply").
        "starter_removed": [{"id": r["id"], "title": r["title"]} for r in live_frozen.get("reports", [])
                            if starters.get(r["id"]) and r["id"] not in in_draft],
        "connections": [{"id": str(c["id"]), "name": c["name"]} for c in connections],
        "live": {"version_id": str(live["id"]), "version": live["version_no"], "published_at": live["published_at"],
                 "published_by": live["display_name"],
                 "roles": [{"id": str(r["id"]), "key": r["key"], "name": r["name"]} for r in roles]} if live else None,
        "draft_differs_from_live": changed, "revision": d["draft_revision"],
        "publish_request": latest[0] if latest else None,
    }


async def _requests(conn: AsyncConnection, where: str, params: dict[str, Any]) -> list[dict[str, Any]]:
    """Publish requests (US-142) with names resolved; ``where`` is a fixed clause from this module."""
    rows = await fetch_all(
        conn, "SELECT r.*, d.title, u.display_name AS requester, dc.display_name AS decider, v.version_no "
              "FROM content.publish_requests r JOIN content.dashboards d ON d.id = r.dashboard_id "
              "JOIN iam.users u ON u.id = r.requested_by LEFT JOIN iam.users dc ON dc.id = r.decided_by "
              "LEFT JOIN content.dashboard_versions v ON v.id = r.version_id WHERE " + where, params)
    role_ids = sorted({str(x) for r in rows for x in r["target_role_ids"]})
    roles = {str(r["id"]): r for r in await fetch_all(
        conn, "SELECT id, key, name FROM iam.roles WHERE id = ANY(CAST(:r AS uuid[]))", {"r": role_ids})}
    return [{"id": str(r["id"]), "dashboard": {"id": str(r["dashboard_id"]), "title": r["title"]},
             "requested_by": {"id": str(r["requested_by"]), "name": r["requester"]},
             "roles": [{"id": k, "key": roles[k]["key"], "name": roles[k]["name"]}
                       for k in (str(x) for x in r["target_role_ids"]) if k in roles],
             "note": r["note"], "status": r["status"], "created_at": r["created_at"],
             "decided_by": r["decider"], "decided_at": r["decided_at"], "decision_note": r["decision_note"],
             "version": r["version_no"]} for r in rows]


async def _live_frozen(conn: AsyncConnection, d: dict[str, Any]) -> dict[str, Any]:
    row = await fetch_one(conn, "SELECT frozen_content FROM content.dashboard_versions WHERE id = :v",
                          {"v": d["live_version_id"]})
    return row["frozen_content"] if row else {}


@router.get("/dashboards")
async def list_dashboards(all: bool = False,  # noqa: A002 - query parameter name
                          principal: Principal = Depends(require(Perm.DASHBOARDS_VIEW))) -> dict[str, Any]:
    show_all = all and principal.has(Perm.DASHBOARDS_PUBLISH)
    async with tenant_tx(principal.tenant_id) as conn:
        rows = await fetch_all(
            conn,
            """
            SELECT d.id, d.title, d.layout, d.status, d.updated_at, d.owner_id, u.display_name AS owner_name,
              v.version_no AS live_version,
              (SELECT count(*) FROM content.dashboard_reports dr WHERE dr.dashboard_id = d.id) AS reports
            FROM content.dashboards d JOIN iam.users u ON u.id = d.owner_id
            LEFT JOIN content.dashboard_versions v ON v.id = d.live_version_id
            WHERE d.kind = 'user' AND d.deleted_at IS NULL AND (:all OR d.owner_id = CAST(:u AS uuid) OR EXISTS (
              SELECT 1 FROM content.shares s WHERE s.object_type = 'dashboard' AND s.object_id = d.id
              AND s.grantee_user_id = CAST(:u AS uuid)))
            ORDER BY d.updated_at DESC LIMIT 200
            """, {"u": principal.user_id, "all": show_all})
    return {"items": [{**r, "id": str(r["id"]), "owner_id": str(r["owner_id"]),
                       "mine": str(r["owner_id"]) == principal.user_id} for r in rows]}


@router.post("/dashboards", status_code=201)
async def create_dashboard(body: DashboardIn, request: Request,
                           principal: Principal = Depends(require(Perm.DASHBOARDS_CREATE))) -> dict[str, Any]:
    async with tenant_tx(principal.tenant_id) as conn:
        did = new_id()
        await conn.execute(
            text("INSERT INTO content.dashboards (id, tenant_id, title, kind, layout, shared_filters, status, "
                 "version_no, owner_id, created_by) VALUES (:id, :t, :title, 'user', :l, CAST(:f AS jsonb), 'draft', "
                 "0, :u, :u)"),
            {"id": did, "t": principal.tenant_id, "title": body.title.strip(), "l": body.layout,
             "f": json.dumps([f.model_dump() for f in body.shared_filters]), "u": principal.user_id})
        d = await own_dashboard(conn, principal, did, lock=True)
        await _set_reports(conn, principal, d, [OrderItem(report_id=r) for r in body.report_ids])
        await audit.record(conn, tenant_id=principal.tenant_id, action="dashboard.created", object_type="dashboard",
                           object_id=did, outcome="success", actor_id=principal.user_id, correlation_id=_rid(request))
        return await _detail(conn, principal, d)


@router.get("/dashboards/{dashboard_id}")
async def get_dashboard(dashboard_id: str,
                        principal: Principal = Depends(require(Perm.DASHBOARDS_VIEW))) -> dict[str, Any]:
    async with tenant_tx(principal.tenant_id) as conn:
        d = await readable_dashboard_draft(conn, principal, dashboard_id)
        return await _detail(conn, principal, d)


@router.get("/dashboards/{dashboard_id}/view")
async def view_draft(dashboard_id: str, request: Request,
                     principal: Principal = Depends(require(Perm.DASHBOARDS_VIEW))) -> dict[str, Any]:
    """The draft as the viewer renders it, for the owner, share recipients and publishers (own scope)."""
    async with tenant_tx(principal.tenant_id) as conn:
        d = await readable_dashboard_draft(conn, principal, dashboard_id)
        frozen = await freeze_dashboard(conn, d)
        return await render_frozen(conn, principal, d, frozen, version_id=None, version_no=None, draft=True)


@router.patch("/dashboards/{dashboard_id}")
async def patch_dashboard(dashboard_id: str, body: DashboardPatch, request: Request,
                          principal: Principal = Depends(require(Perm.DASHBOARDS_CREATE))) -> dict[str, Any]:
    async with tenant_tx(principal.tenant_id) as conn:
        d = await own_dashboard(conn, principal, dashboard_id, publisher_ok=True, lock=True)
        if body.title is not None:
            await conn.execute(text("UPDATE content.dashboards SET title = :t WHERE id = :id"),
                               {"t": body.title.strip(), "id": d["id"]})
        if body.layout is not None:
            await conn.execute(text("UPDATE content.dashboards SET layout = :l WHERE id = :id"),
                               {"l": body.layout, "id": d["id"]})
        if body.shared_filters is not None:
            refs = [f.ref for f in body.shared_filters]
            if len(set(refs)) != len(refs):
                raise TelaError(ErrorCode.VALIDATION_ERROR, "Each shared filter can be added once.")
            frozen = await freeze_dashboard(conn, d)
            snaps = await snapshots_for(conn, principal.tenant_id, frozen)
            unknown = [r for r in refs if not any(s.dimension(ref_key(r)) for s in snaps.values())]
            if unknown and snaps:
                raise TelaError(ErrorCode.VALIDATION_ERROR, "Shared filters must be dimensions of the reports' "
                                "models.", {"unknown": unknown})
            await conn.execute(text("UPDATE content.dashboards SET shared_filters = CAST(:f AS jsonb) WHERE id = :id"),
                               {"f": json.dumps([f.model_dump() for f in body.shared_filters]), "id": d["id"]})
        await conn.execute(text("UPDATE content.dashboards SET draft_revision = draft_revision + 1 WHERE id = :id"),
                           {"id": d["id"]})
        d = await own_dashboard(conn, principal, dashboard_id, publisher_ok=True)
        return await _detail(conn, principal, d)


@router.put("/dashboards/{dashboard_id}/reports")
async def order_reports(dashboard_id: str, body: OrderIn,
                        principal: Principal = Depends(require(Perm.DASHBOARDS_CREATE))) -> dict[str, Any]:
    async with tenant_tx(principal.tenant_id) as conn:
        d = await own_dashboard(conn, principal, dashboard_id, lock=True)
        await _set_reports(conn, principal, d, body.items)
        await conn.execute(text("UPDATE content.dashboards SET draft_revision = draft_revision + 1 WHERE id = :id"),
                           {"id": d["id"]})
        return await _detail(conn, principal, d)


@router.delete("/dashboards/{dashboard_id}", status_code=204)
async def delete_dashboard(dashboard_id: str, request: Request,
                           principal: Principal = Depends(require(Perm.DASHBOARDS_CREATE))) -> Response:
    async with tenant_tx(principal.tenant_id) as conn:
        d = await own_dashboard(conn, principal, dashboard_id, lock=True)
        if d["live_version_id"]:
            await _withdraw(conn, d)
        await conn.execute(text("UPDATE content.dashboards SET deleted_at = now(), status = 'deleted' WHERE id = :id"),
                           {"id": d["id"]})
        await audit.record(conn, tenant_id=principal.tenant_id, action="dashboard.deleted", object_type="dashboard",
                           object_id=dashboard_id, outcome="success", actor_id=principal.user_id,
                           correlation_id=_rid(request))
    return Response(status_code=204)


# --------------------------------------------------------------------------- preview as role (API-038)


class PreviewAs(BaseModel):
    role: str | None = Field(default=None, max_length=64)
    user_id: str | None = Field(default=None, max_length=64)


class PreviewIn(BaseModel):
    as_: PreviewAs = Field(alias="as")


async def preview_frozen(request: Request, principal: Principal, frozen: dict[str, Any], body: PreviewIn,
                         object_type: str, object_id: str, *, shared_with: set[str] | None = None) -> dict[str, Any]:
    if bool(body.as_.role) == bool(body.as_.user_id):
        raise TelaError(ErrorCode.VALIDATION_ERROR, "Preview as one role or one named person.")
    admin = principal.has(Perm.DASHBOARDS_PUBLISH)
    async with tenant_tx(principal.tenant_id) as conn:
        if body.as_.role:
            role = (await roles_by_ref(conn, [body.as_.role]))[0]
            p = await principal_for_role(conn, principal, role)
            who = {"role": role["key"], "name": role["name"]}
        else:
            if not admin and body.as_.user_id not in (shared_with or set()):
                raise TelaError(ErrorCode.FORBIDDEN, "You can preview as a person you shared this with.")
            p = await principal_for_user(conn, principal, str(body.as_.user_id))
            who = {"user_id": body.as_.user_id, "name": p.display_name}
        snaps = await snapshots_for(conn, principal.tenant_id, frozen)
        scopes: dict[str, Any] = {}
        for cid in {s.connection_id for s in snaps.values()}:
            try:
                scopes[cid] = await resolve_scope(conn, p, cid)
            except TelaError as exc:
                scopes[cid] = exc
        await audit.record(conn, tenant_id=principal.tenant_id, action="preview.as_role", object_type=object_type,
                           object_id=object_id, outcome="success", actor_id=principal.user_id,
                           correlation_id=_rid(request), details={"as": who})
    widgets = []
    for i in widget_items(frozen):
        base = {"widget_id": i["widget_id"], "item_id": i["item_id"], "title": i["title"]}
        if set(p.role_ids) & {str(x) for x in i.get("hidden_for_roles", [])}:
            widgets.append({**base, "shown": False, "reason": "Hidden for this role on this dashboard"})
            continue
        status, res, err = await _run_for(request, p, {k: v for k, v in i["spec"].items() if k != "compare"})
        if status == "error" and err is not None:
            widgets.append({**base, "shown": False, "reason": _scope_reason(err, who["name"])[0]})
        elif status == "hidden":
            widgets.append({**base, "shown": False, "reason": f"Uses fields hidden for {who['name']}"})
        else:
            row = {**base, "shown": True, "reason": "No rows in this scope" if status == "empty" else None}
            if admin and res is not None:
                row["rows"] = res["row_count"]
            widgets.append(row)
    rows_in_scope: dict[str, Any] = {}
    if admin:
        for snap in snaps.values():
            scope = scopes.get(snap.connection_id)
            if isinstance(scope, TelaError) or scope is None:
                continue
            for entity in sorted({e for i in widget_items(frozen) if i["spec"]["model_version_id"] ==
                                  snap.model_version_id for e in _entities_of(i["spec"], snap)}):
                if entity not in scope.entities:
                    rows_in_scope[entity] = 0
                    continue
                rows_in_scope[entity] = await _service(request).rows_in_scope(
                    snapshot=snap, scope=scope, connection_id=snap.connection_id, entity=entity, principal=p,
                    ctx=QueryContext(surface="preview", request_id=_rid(request)))
    scope_text = "; ".join(s.summary if not isinstance(s, TelaError) else _scope_reason(s, who["name"])[0]
                           for s in scopes.values())
    return {"as": who, "scope_text": scope_text, "rows_in_scope": rows_in_scope if admin else None,
            "counts_note": None if admin else "Row counts are shown to Admins only.", "widgets": widgets}


@router.post("/dashboards/{dashboard_id}/preview")
async def preview_dashboard(dashboard_id: str, body: PreviewIn, request: Request,
                            principal: Principal = Depends(require(Perm.DASHBOARDS_CREATE))) -> dict[str, Any]:
    async with tenant_tx(principal.tenant_id) as conn:
        d = await own_dashboard(conn, principal, dashboard_id, publisher_ok=True)
        frozen = await freeze_dashboard(conn, d)
        shared = {str(r["grantee_user_id"]) for r in await fetch_all(
            conn, "SELECT grantee_user_id FROM content.shares WHERE object_type = 'dashboard' AND object_id = :d",
            {"d": d["id"]})}
    return await preview_frozen(request, principal, frozen, body, "dashboard", dashboard_id, shared_with=shared)


@router.post("/reports/{report_id}/preview")
async def preview_report(report_id: str, body: PreviewIn, request: Request,
                         principal: Principal = Depends(require(Perm.REPORTS_CREATE))) -> dict[str, Any]:
    from tela_api.content.access import own_report

    async with tenant_tx(principal.tenant_id) as conn:
        r = await own_report(conn, principal, report_id)
        state = await report_state(conn, r["id"])
        shared = {str(x["grantee_user_id"]) for x in await fetch_all(
            conn, "SELECT grantee_user_id FROM content.shares WHERE object_type = 'report' AND object_id = :r",
            {"r": r["id"]})}
    frozen = {"reports": [{"id": report_id, "title": state["title"], "sections": [
        {"id": s["id"], "title": s["title"], "kind": s["kind"], "collapsed": s["collapsed"], "items": [
            {"kind": i["kind"], "item_id": i["id"], "widget_id": i["widget_id"],
             "hidden_for_roles": i["hidden_for_roles"],
             **({"title": state["widgets"][i["widget_id"]]["title"], "spec": state["widgets"][i["widget_id"]]["spec"]}
                if i["kind"] == "widget" else {})}
            for i in state["items"] if i["section_id"] == s["id"]]} for s in state["sections"]]}]}
    return await preview_frozen(request, principal, frozen, body, "report", report_id, shared_with=shared)


# --------------------------------------------------------------------------- prechecks and publish (API-039/040)


class PrecheckIn(BaseModel):
    target_roles: list[str] = Field(default_factory=list, max_length=20)


class PublishIn(PrecheckIn):
    note: str | None = Field(default=None, max_length=500)


async def _target_roles(conn: AsyncConnection, d: dict[str, Any], refs: list[str]) -> list[dict[str, Any]]:
    if refs:
        return await roles_by_ref(conn, refs)
    live = await fetch_one(conn, "SELECT target_role_ids FROM content.dashboard_versions WHERE id = :v",
                           {"v": d["live_version_id"]}) if d["live_version_id"] else None
    return await roles_by_ref(conn, [str(r) for r in live["target_role_ids"]]) if live else []


@router.post("/dashboards/{dashboard_id}/prechecks")
async def prechecks(dashboard_id: str, body: PrecheckIn, request: Request,
                    principal: Principal = Depends(require(Perm.DASHBOARDS_CREATE))) -> dict[str, Any]:
    async with tenant_tx(principal.tenant_id) as conn:
        d = await own_dashboard(conn, principal, dashboard_id, publisher_ok=True)
        frozen = await freeze_dashboard(conn, d)
        roles = await _target_roles(conn, d, body.target_roles)
    return await run_prechecks(request, principal, frozen, roles)


def _summary(prev: dict[str, Any] | None, frozen: dict[str, Any]) -> str:
    n_r, n_w = len(frozen["reports"]), len(widget_items(frozen))
    text_ = f"{n_r} report{'s' if n_r != 1 else ''}, {n_w} widget{'s' if n_w != 1 else ''}"
    if prev:
        before = {i["widget_id"] for i in widget_items(prev)}
        after = {i["widget_id"] for i in widget_items(frozen)}
        added, removed = len(after - before), len(before - after)
        if added or removed:
            text_ += f" ({'+' + str(added) if added else ''}{', ' if added and removed else ''}" \
                     f"{'-' + str(removed) if removed else ''} widgets)"
        elif canonical(prev) != canonical(frozen):
            text_ += " (layout or settings changed)"
        else:
            text_ += " (no changes)"
    return text_


async def _go_live(conn: AsyncConnection, principal: Principal, d: dict[str, Any], frozen: dict[str, Any],
                   roles: list[dict[str, Any]], checks: dict[str, Any], note: str | None,
                   restored_from: dict[str, Any] | None = None) -> dict[str, Any]:
    prev = await fetch_one(conn, "SELECT id, frozen_content FROM content.dashboard_versions WHERE id = :v",
                           {"v": d["live_version_id"]}) if d["live_version_id"] else None
    n = await fetch_one(conn, "SELECT coalesce(max(version_no), 0) + 1 AS n FROM content.dashboard_versions WHERE "
                              "dashboard_id = :d", {"d": d["id"]})
    version_no = int(n["n"]) if n else 1
    vid = new_id()
    if prev is not None:
        await conn.execute(text("UPDATE content.dashboard_versions SET state = 'superseded' WHERE id = :v"),
                           {"v": prev["id"]})
    summary = (f"Restored v{restored_from['version_no']}: " if restored_from else "") + _summary(
        prev["frozen_content"] if prev else None, frozen)
    await conn.execute(
        text("INSERT INTO content.dashboard_versions (id, tenant_id, dashboard_id, version_no, frozen_content, "
             "precheck_result, target_role_ids, note, change_summary, state, restored_from_id, published_by) VALUES "
             "(:id, :t, :d, :n, CAST(:f AS jsonb), CAST(:c AS jsonb), CAST(:r AS uuid[]), :note, :s, 'live', "
             "CAST(:rf AS uuid), :u)"),
        {"id": vid, "t": principal.tenant_id, "d": d["id"], "n": version_no, "f": canonical(frozen),
         "c": canonical(checks), "r": [str(r["id"]) for r in roles], "note": note, "s": summary,
         "rf": str(restored_from["id"]) if restored_from else None, "u": principal.user_id})
    await conn.execute(text("UPDATE content.dashboards SET live_version_id = :v, status = 'published', version_no = :n "
                            "WHERE id = :d"), {"v": vid, "n": version_no, "d": d["id"]})
    await conn.execute(text("DELETE FROM content.publications WHERE dashboard_id = :d"), {"d": d["id"]})
    for r in roles:
        await conn.execute(
            text("INSERT INTO content.publications (id, tenant_id, dashboard_id, dashboard_version_id, role_id, "
                 "published_by) VALUES (:id, :t, :d, :v, :r, :u)"),
            {"id": new_id(), "t": principal.tenant_id, "d": d["id"], "v": vid, "r": r["id"], "u": principal.user_id})
    if restored_from is None:
        for r in frozen["reports"]:
            state = await report_state(conn, r["id"])
            await conn.execute(text("UPDATE content.reports SET published_snapshot = CAST(:s AS jsonb) WHERE id = "
                                    "CAST(:id AS uuid) AND origin <> 'starter'"),
                               {"s": canonical(state), "id": r["id"]})
    audience = [u for u in await users_with_roles(conn, [str(r["id"]) for r in roles]) if u != principal.user_id]
    await notify(conn, principal.tenant_id, audience, kind="dashboard.published",
                 title=f"{frozen['title']} was published to your role",
                 body=note or f"Version {version_no} is now live.", link=f"/dashboards/{d['id']}", by=principal.user_id)
    await outbox.emit(conn, tenant_id=principal.tenant_id, topic="dashboard.published", aggregate_id=str(d["id"]),
                      payload={"version_id": vid, "version": version_no})
    return {"dashboard_version_id": vid, "version": version_no, "live": True, "change_summary": summary,
            "notified": len(audience), "roles": [{"id": str(r["id"]), "key": r["key"], "name": r["name"]}
                                                 for r in roles]}


@router.post("/dashboards/{dashboard_id}/publish", status_code=201)
async def publish(dashboard_id: str, body: PublishIn, request: Request,
                  idempotency_key: str | None = Header(default=None),
                  principal: Principal = Depends(require(Perm.DASHBOARDS_CREATE))) -> dict[str, Any]:
    if not principal.has(Perm.DASHBOARDS_PUBLISH):
        raise TelaError(ErrorCode.FORBIDDEN, ONLY_ADMINS)
    route = f"{PUBLISH_ROUTE}/{dashboard_id}"
    async with tenant_tx(principal.tenant_id) as conn:
        if (prior := await replay(conn, principal, route, idempotency_key)) is not None:
            return prior
        d = await own_dashboard(conn, principal, dashboard_id, publisher_ok=True)
        frozen = await freeze_dashboard(conn, d)
        roles = await _target_roles(conn, d, body.target_roles)
    if not frozen["reports"]:
        raise TelaError(ErrorCode.CONFLICT, "Add at least one report before publishing.")
    checks = await run_prechecks(request, principal, frozen, roles)
    if not checks["passed"]:
        async with tenant_tx(principal.tenant_id) as conn:
            if any(c["check"] == "all_widgets_certified" and not c["passed"] for c in checks["checks"]):
                await enqueue_for_widgets(conn, principal, [i["spec"] for i in widget_items(frozen)],
                                          trigger="publish_bound")
            await audit.record(conn, tenant_id=principal.tenant_id, action="dashboard.publish_blocked",
                               object_type="dashboard", object_id=dashboard_id, outcome="deny",
                               actor_id=principal.user_id, correlation_id=_rid(request),
                               details={"failed": [c["check"] for c in checks["checks"] if not c["passed"]]})
        raise TelaError(ErrorCode.PRECHECK_FAILED, "Publishing is blocked until every check passes. The live version "
                        "is unchanged.", checks)
    async with tenant_tx(principal.tenant_id) as conn:
        d = await own_dashboard(conn, principal, dashboard_id, publisher_ok=True, lock=True)
        if canonical(await freeze_dashboard(conn, d)) != canonical(frozen):
            raise TelaError(ErrorCode.CONFLICT, "The dashboard changed while it was being checked. Publish again.")
        out = await _go_live(conn, principal, d, frozen, roles, checks, body.note)
        closed = await fetch_one(
            conn, "UPDATE content.publish_requests SET status = 'published', decided_by = :u, decided_at = now(), "
                  "version_id = :v WHERE dashboard_id = :d AND status = 'open' RETURNING id, requested_by",
            {"u": principal.user_id, "v": out["dashboard_version_id"], "d": d["id"]})
        if closed is not None and str(closed["requested_by"]) != principal.user_id:
            await notify(conn, principal.tenant_id, [str(closed["requested_by"])], kind="publish.approved",
                         title=f"{d['title']} was published",
                         body=f"{principal.display_name} published version {out['version']} to "
                              f"{', '.join(r['name'] for r in roles)}.",
                         link=f"/dashboards/{dashboard_id}/versions", by=principal.user_id)
        out["request_id"] = str(closed["id"]) if closed else None
        await audit.record(conn, tenant_id=principal.tenant_id, action="dashboard.published", object_type="dashboard",
                           object_id=dashboard_id, outcome="success", actor_id=principal.user_id,
                           correlation_id=_rid(request),
                           details={"version": out["version"], "version_id": out["dashboard_version_id"],
                                    "roles": [r["key"] for r in roles], "note": body.note})
        await remember(conn, principal, route, idempotency_key, 201, out)
    return out


@router.post("/dashboards/{dashboard_id}/request-publish", status_code=202)
async def request_publish(dashboard_id: str, body: PublishIn, request: Request,
                          principal: Principal = Depends(require(Perm.DASHBOARDS_CREATE))) -> dict[str, Any]:
    async with tenant_tx(principal.tenant_id) as conn:
        d = await own_dashboard(conn, principal, dashboard_id)
        roles = await roles_by_ref(conn, body.target_roles) if body.target_roles else []
        queued = await enqueue_for_widgets(conn, principal, [i["spec"] for i in widget_items(
            await freeze_dashboard(conn, d))], trigger="publish_bound")
        admins = [u for u in await users_with_permission(conn, Perm.DASHBOARDS_PUBLISH) if u != principal.user_id]
        names = ", ".join(r["name"] for r in roles) or "its audience"
        # One open request per dashboard: asking again replaces it.
        await conn.execute(text("UPDATE content.publish_requests SET status = 'withdrawn' WHERE dashboard_id = :d "
                                "AND status = 'open'"), {"d": d["id"]})
        rid = new_id()
        await conn.execute(
            text("INSERT INTO content.publish_requests (id, tenant_id, dashboard_id, requested_by, target_role_ids, "
                 "note) VALUES (:id, :t, :d, :u, CAST(:r AS uuid[]), :n)"),
            {"id": rid, "t": principal.tenant_id, "d": d["id"], "u": principal.user_id,
             "r": [str(r["id"]) for r in roles], "n": (body.note or "").strip() or None})
        sent = await notify(conn, principal.tenant_id, admins, kind="publish.requested",
                            title=f"{principal.display_name} asks to publish {d['title']}",
                            body=(body.note or "") + f" Roles: {names}.",
                            link=f"/dashboards/{dashboard_id}/publish?request={rid}", by=principal.user_id)
        await audit.record(conn, tenant_id=principal.tenant_id, action="dashboard.publish_requested",
                           object_type="dashboard", object_id=dashboard_id, outcome="success",
                           actor_id=principal.user_id, correlation_id=_rid(request),
                           details={"roles": [r["key"] for r in roles], "request_id": rid})
    return {"requested": True, "request_id": rid, "notified": sent, "certification_requests": queued}


@router.get("/publish-requests")
async def publish_requests(status: str = "open",
                           principal: Principal = Depends(require(Perm.DASHBOARDS_PUBLISH))) -> dict[str, Any]:
    """Govern → Publish requests (US-142): open requests first-in first-out; decided ones newest first."""
    if status not in {"open", "decided"}:
        raise TelaError(ErrorCode.VALIDATION_ERROR, "Status is open or decided.")
    async with tenant_tx(principal.tenant_id) as conn:
        if status == "open":
            items = await _requests(conn, "r.status = 'open' AND d.deleted_at IS NULL ORDER BY r.created_at", {})
        else:
            items = await _requests(conn, "r.status IN ('published', 'declined') ORDER BY r.decided_at DESC "
                                          "LIMIT 200", {})
    return {"items": items}


class DeclineIn(BaseModel):
    note: str = Field(min_length=1, max_length=500)


@router.post("/publish-requests/{request_id}/decline")
async def decline_request(request_id: str, body: DeclineIn, request: Request,
                          principal: Principal = Depends(require(Perm.DASHBOARDS_PUBLISH))) -> dict[str, Any]:
    note = body.note.strip()
    if not note:
        raise TelaError(ErrorCode.VALIDATION_ERROR, "Tell the requester why.", {"field": "note"})
    async with tenant_tx(principal.tenant_id) as conn:
        try:
            row = await fetch_one(conn, "SELECT r.*, d.title FROM content.publish_requests r JOIN content.dashboards d "
                                        "ON d.id = r.dashboard_id WHERE r.id = CAST(:id AS uuid) FOR UPDATE OF r",
                                  {"id": request_id})
        except Exception:  # noqa: BLE001
            row = None
        if row is None:
            raise TelaError(ErrorCode.NOT_FOUND, "That publish request does not exist.")
        if row["status"] != "open":
            raise TelaError(ErrorCode.CONFLICT, "That request has already been handled.")
        await conn.execute(text("UPDATE content.publish_requests SET status = 'declined', decided_by = :u, "
                                "decided_at = now(), decision_note = :n WHERE id = :id"),
                           {"u": principal.user_id, "n": note, "id": row["id"]})
        await notify(conn, principal.tenant_id, [str(row["requested_by"])], kind="publish.declined",
                     title=f"{principal.display_name} returned {row['title']} to you", body=note,
                     link=f"/dashboards/{row['dashboard_id']}/edit", by=principal.user_id)
        await audit.record(conn, tenant_id=principal.tenant_id, action="dashboard.publish_declined",
                           object_type="dashboard", object_id=str(row["dashboard_id"]), outcome="success",
                           actor_id=principal.user_id, correlation_id=_rid(request),
                           details={"request_id": request_id})
        return (await _requests(conn, "r.id = :id", {"id": row["id"]}))[0]


# --------------------------------------------------------------------------- versions (API-041)


@router.get("/dashboards/{dashboard_id}/versions")
async def versions(dashboard_id: str,
                   principal: Principal = Depends(require(Perm.DASHBOARDS_CREATE))) -> dict[str, Any]:
    async with tenant_tx(principal.tenant_id) as conn:
        d = await own_dashboard(conn, principal, dashboard_id, publisher_ok=True)
        rows = await fetch_all(
            conn, "SELECT v.id, v.version_no, v.state, v.note, v.change_summary, v.published_at, v.target_role_ids, "
                  "u.display_name AS published_by, rf.version_no AS restored_from, rq.display_name AS requested_by "
                  "FROM content.dashboard_versions v JOIN iam.users u ON u.id = v.published_by "
                  "LEFT JOIN content.dashboard_versions rf ON rf.id = v.restored_from_id "
                  "LEFT JOIN content.publish_requests pr ON pr.version_id = v.id "
                  "LEFT JOIN iam.users rq ON rq.id = pr.requested_by "
                  "WHERE v.dashboard_id = :d ORDER BY v.version_no DESC", {"d": d["id"]})
        roles = {str(r["id"]): r["name"] for r in await fetch_all(conn, "SELECT id, name FROM iam.roles")}
    return {"items": [{"id": str(r["id"]), "version": r["version_no"], "state": r["state"], "note": r["note"],
                       "change_summary": r["change_summary"], "published_at": r["published_at"],
                       "published_by": r["published_by"], "restored_from": r["restored_from"],
                       "requested_by": r["requested_by"],
                       "roles": [roles.get(str(x), str(x)) for x in r["target_role_ids"]]} for r in rows],
            "can_restore": principal.has(Perm.DASHBOARDS_PUBLISH)}


class RestoreIn(BaseModel):
    note: str | None = Field(default=None, max_length=500)


@router.post("/dashboards/{dashboard_id}/versions/{version}/restore", status_code=201)
async def restore(dashboard_id: str, version: int, body: RestoreIn, request: Request,
                  principal: Principal = Depends(require(Perm.DASHBOARDS_PUBLISH))) -> dict[str, Any]:
    async with tenant_tx(principal.tenant_id) as conn:
        d = await own_dashboard(conn, principal, dashboard_id, publisher_ok=True)
        old = await fetch_one(conn, "SELECT * FROM content.dashboard_versions WHERE dashboard_id = :d AND "
                                    "version_no = :n", {"d": d["id"], "n": version})
        if old is None:
            raise TelaError(ErrorCode.NOT_FOUND, f"Version {version} does not exist.")
        if old["state"] == "live":
            raise TelaError(ErrorCode.CONFLICT, f"Version {version} is already live.")
        roles = await roles_by_ref(conn, [str(r) for r in old["target_role_ids"]])
    frozen = old["frozen_content"]
    checks = await run_prechecks(request, principal, frozen, roles, version_check_blocks=False)
    if not checks["passed"]:
        raise TelaError(ErrorCode.PRECHECK_FAILED, f"Version {version} can't be restored until every check passes.",
                        checks)
    async with tenant_tx(principal.tenant_id) as conn:
        d = await own_dashboard(conn, principal, dashboard_id, publisher_ok=True, lock=True)
        out = await _go_live(conn, principal, d, frozen, roles, checks, body.note or f"Restored version {version}",
                             restored_from=old)
        await audit.record(conn, tenant_id=principal.tenant_id, action="dashboard.restored", object_type="dashboard",
                           object_id=dashboard_id, outcome="success", actor_id=principal.user_id,
                           correlation_id=_rid(request),
                           details={"restored": version, "version": out["version"], "notes": checks["notes"]})
    return {**out, "notes": checks["notes"]}


async def _withdraw(conn: AsyncConnection, d: dict[str, Any]) -> None:
    await conn.execute(text("UPDATE content.dashboard_versions SET state = 'withdrawn' WHERE id = :v"),
                       {"v": d["live_version_id"]})
    await conn.execute(text("DELETE FROM content.publications WHERE dashboard_id = :d"), {"d": d["id"]})
    await conn.execute(text("UPDATE content.dashboards SET live_version_id = NULL, status = 'draft' WHERE id = :d"),
                       {"d": d["id"]})


@router.post("/dashboards/{dashboard_id}/unpublish")
async def unpublish(dashboard_id: str, request: Request,
                    principal: Principal = Depends(require(Perm.DASHBOARDS_PUBLISH))) -> dict[str, Any]:
    async with tenant_tx(principal.tenant_id) as conn:
        d = await own_dashboard(conn, principal, dashboard_id, publisher_ok=True, lock=True)
        if not d["live_version_id"]:
            raise TelaError(ErrorCode.CONFLICT, "This dashboard is not published.")
        await _withdraw(conn, d)
        await audit.record(conn, tenant_id=principal.tenant_id, action="dashboard.unpublished",
                           object_type="dashboard", object_id=dashboard_id, outcome="success",
                           actor_id=principal.user_id, correlation_id=_rid(request))
    return {"withdrawn": True}
