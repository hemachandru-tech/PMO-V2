"""Starter content management (API-047; FEAT-046; RULE-017; US-098).

Admin (``starter.manage``):

* ``GET /admin/starter`` — every starter report with its state (Active, Hidden, Deleted), availability and gaps,
  visibility per role, the number of user copies and the dashboards that include it. Models approved before
  starter reports were content get theirs built here on first open.
* ``POST /admin/starter/{item_id}/visibility`` — ``{"hide_from_roles": [...]}`` sets the roles it is hidden from
  (keys or ids), ``{"show_to_all": true}`` clears them. Hidden is about discovery: the tab leaves the starter
  dashboard and the composer shelf for those roles; dashboards that already include it are unchanged.
* ``DELETE /admin/starter/{item_id}`` — ``?dry_run=true`` returns the effects only. Deleting (RULE-017): user
  copies stay and keep working; every dashboard that includes it gets a new draft without it while its live version
  stays (with a Deleted badge) until republished, and the owners are notified; the starter dashboard loses the tab.
* ``POST /admin/starter/{item_id}/restore`` — rebuilds the report from the installed pack version.

Everyone (REQ-114): ``GET /starter-reports`` lists the starter reports the person may use, and Creators copy one
with ``POST /reports/{report_id}/copy``. The copy is theirs and independent of later pack updates.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from tela_api.audit import service as audit
from tela_api.content.access import readable_report
from tela_api.content.common import notify, roles_by_ref
from tela_api.content.reports import _insert_item, create_report, report_out, report_state
from tela_api.content.starters import (
    backfill_starter_reports,
    build_starter_reports,
    dashboards_including,
    drop_from_dashboards,
    hidden_report_keys,
    refresh_starter_dashboards,
)
from tela_api.core.db import fetch_all, fetch_one, tenant_tx
from tela_api.identity.deps import require
from tela_api.identity.permissions import Perm
from tela_api.identity.principal import Principal
from tela_api.model.store import current_version_id
from tela_common.errors import ErrorCode, TelaError
from tela_common.ids import new_id

router = APIRouter(prefix="/api/v1", tags=["starter-content"])
NO_ITEM = "That starter report does not exist."

_ITEMS = """
    SELECT i.id, i.item_key, i.title, i.availability, i.gaps, i.state, i.hidden_from_role_ids, i.content_id,
      i.connection_id, c.name AS connection_name, p.pack_key, p.pack_version, i.updated_at,
      (SELECT count(*) FROM content.report_items ri WHERE ri.report_id = i.content_id AND ri.item_kind = 'widget')
        AS widgets,
      (SELECT count(*) FROM content.reports r WHERE r.copied_from_id = i.content_id AND r.deleted_at IS NULL)
        AS copies,
      (SELECT count(*) FROM content.dashboard_reports dr JOIN content.dashboards d ON d.id = dr.dashboard_id
        AND d.deleted_at IS NULL WHERE dr.report_id = i.content_id) AS dashboards
    FROM model.starter_pack_items i
    JOIN model.starter_packs p ON p.id = i.starter_pack_id
    JOIN model.data_models dm ON dm.connection_id = i.connection_id AND dm.installed_pack_id = i.starter_pack_id
    JOIN conn.connections c ON c.id = i.connection_id AND c.deleted_at IS NULL
    WHERE i.item_kind = 'report'
"""


def _rid(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


def _state(row: dict[str, Any]) -> str:
    if row["state"] == "deleted":
        return "deleted"
    return "hidden" if row["hidden_from_role_ids"] else "active"


def _item_out(row: dict[str, Any], roles: list[dict[str, Any]]) -> dict[str, Any]:
    hidden = {str(r) for r in row["hidden_from_role_ids"] or []}
    return {
        "id": str(row["id"]), "key": row["item_key"], "title": row["title"], "state": _state(row),
        "availability": row["availability"], "gaps": row["gaps"],
        "report_id": str(row["content_id"]) if row["content_id"] else None,
        "connection": {"id": str(row["connection_id"]), "name": row["connection_name"]},
        "pack": {"key": row["pack_key"], "version": row["pack_version"]},
        "widgets": row["widgets"], "copies": row["copies"], "dashboards": row["dashboards"],
        "visibility": [{"role_id": str(r["id"]), "key": r["key"], "name": r["name"], "visible": str(r["id"]) not in
                        hidden} for r in roles],
        "updated_at": row["updated_at"],
    }


async def _roles(conn: AsyncConnection) -> list[dict[str, Any]]:
    return await fetch_all(conn, "SELECT id, key, name, kind FROM iam.roles ORDER BY kind, name")


async def _item(conn: AsyncConnection, item_id: str, *, lock: bool = False) -> dict[str, Any]:
    try:
        row = await fetch_one(conn, _ITEMS + " AND i.id = CAST(:id AS uuid)", {"id": item_id})
    except Exception:  # noqa: BLE001 - a malformed id is just "not found"
        row = None
    if row is None:
        raise TelaError(ErrorCode.NOT_FOUND, NO_ITEM)
    if lock:
        await conn.execute(text("SELECT 1 FROM model.starter_pack_items WHERE id = :id FOR UPDATE"), {"id": row["id"]})
    return row


async def _one_out(conn: AsyncConnection, item_id: str) -> dict[str, Any]:
    return _item_out(await _item(conn, item_id), await _roles(conn))


# --------------------------------------------------------------------------- list and visibility


@router.get("/admin/starter")
async def list_starter(principal: Principal = Depends(require(Perm.STARTER_MANAGE))) -> dict[str, Any]:
    async with tenant_tx(principal.tenant_id) as conn:
        await backfill_starter_reports(conn, tenant_id=principal.tenant_id, by=principal.user_id)
        rows = await fetch_all(conn, _ITEMS + " ORDER BY c.name, i.availability, i.title")
        roles = await _roles(conn)
    return {"items": [_item_out(r, roles) for r in rows],
            "roles": [{"id": str(r["id"]), "key": r["key"], "name": r["name"]} for r in roles]}


class VisibilityIn(BaseModel):
    hide_from_roles: list[str] | None = Field(default=None, max_length=50)
    show_to_all: bool | None = None

    @model_validator(mode="after")
    def one_of(self) -> VisibilityIn:
        if (self.hide_from_roles is None) == (self.show_to_all is None) or self.show_to_all is False:
            raise ValueError("Send hide_from_roles or show_to_all: true.")
        return self


@router.post("/admin/starter/{item_id}/visibility")
async def set_visibility(item_id: str, body: VisibilityIn, request: Request,
                         principal: Principal = Depends(require(Perm.STARTER_MANAGE))) -> dict[str, Any]:
    async with tenant_tx(principal.tenant_id) as conn:
        item = await _item(conn, item_id, lock=True)
        if item["state"] == "deleted":
            raise TelaError(ErrorCode.CONFLICT, "Restore this starter report before changing who sees it.")
        roles = [] if body.show_to_all else await roles_by_ref(conn, body.hide_from_roles or [])
        await conn.execute(text("UPDATE model.starter_pack_items SET hidden_from_role_ids = CAST(:r AS uuid[]), "
                                "updated_at = now() WHERE id = :id"),
                           {"r": [str(r["id"]) for r in roles], "id": item["id"]})
        await audit.record(conn, tenant_id=principal.tenant_id, action="starter.visibility_changed",
                           object_type="starter_item", object_id=str(item["id"]), outcome="success",
                           actor_id=principal.user_id, correlation_id=_rid(request),
                           details={"report": item["item_key"], "hidden_from": [r["key"] for r in roles]})
        return await _one_out(conn, item_id)


# --------------------------------------------------------------------------- delete and restore (RULE-017)


async def _effects(conn: AsyncConnection, item: dict[str, Any]) -> dict[str, Any]:
    rid = item["content_id"]
    dashboards = await dashboards_including(conn, rid) if rid else []
    starter = await fetch_all(
        conn, "SELECT DISTINCT d.id, d.title FROM content.dashboard_tabs t JOIN content.dashboards d ON d.id = "
              "t.dashboard_id WHERE d.kind = 'starter' AND d.status = 'published' AND d.connection_id = :c AND "
              "t.report_key = :k ORDER BY d.title", {"c": item["connection_id"], "k": item["item_key"]})

    def dash(d: dict[str, Any]) -> dict[str, Any]:
        return {"dashboard_id": str(d["id"]), "title": d["title"], "owner": d["owner_name"],
                "live_version_stays": d["live_version"]}

    return {
        "user_copies_unaffected": item["copies"],
        "dashboards_getting_new_draft": [dash(d) for d in dashboards if d["live_version"] is not None],
        "drafts_without_it": [dash(d) for d in dashboards if d["live_version"] is None],
        "starter_dashboards": [{"dashboard_id": str(d["id"]), "title": d["title"]} for d in starter],
        "_owners": {str(d["owner_id"]): [x["title"] for x in dashboards if x["owner_id"] == d["owner_id"]]
                    for d in dashboards},
        "_dashboard_ids": [str(d["id"]) for d in dashboards],
    }


def _public(effects: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in effects.items() if not k.startswith("_")}


@router.delete("/admin/starter/{item_id}")
async def delete_starter(item_id: str, request: Request, dry_run: bool = False,
                         principal: Principal = Depends(require(Perm.STARTER_MANAGE))) -> dict[str, Any]:
    async with tenant_tx(principal.tenant_id) as conn:
        item = await _item(conn, item_id, lock=not dry_run)
        if item["availability"] != "available" or item["content_id"] is None:
            raise TelaError(ErrorCode.CONFLICT, "This starter report is not installed, so there is nothing to delete.")
        if item["state"] == "deleted":
            raise TelaError(ErrorCode.CONFLICT, "This starter report is already deleted. Restore it from the pack.")
        effects = await _effects(conn, item)
        if dry_run:
            return {"dry_run": True, "effects": _public(effects)}
        rid = item["content_id"]
        await conn.execute(text("UPDATE model.starter_pack_items SET state = 'deleted', updated_at = now() "
                                "WHERE id = :id"), {"id": item["id"]})
        await conn.execute(text("UPDATE content.reports SET deleted_at = now() WHERE id = :r"), {"r": rid})
        await drop_from_dashboards(conn, rid, effects["_dashboard_ids"])
        await refresh_starter_dashboards(conn, tenant_id=principal.tenant_id,
                                         connection_id=str(item["connection_id"]), by=principal.user_id)
        notified = 0
        for owner, titles in effects["_owners"].items():
            if owner == principal.user_id:
                continue
            notified += await notify(
                conn, principal.tenant_id, [owner], kind="starter.deleted",
                title=f"Starter report {item['title']} was removed from {', '.join(titles)}"[:200],
                body="Your draft no longer includes it. The live version stays as it is until you republish.",
                link=f"/dashboards/{effects['_dashboard_ids'][0]}/edit" if len(titles) == 1 else "/dashboards",
                by=principal.user_id)
        public = _public(effects)
        await audit.record(conn, tenant_id=principal.tenant_id, action="starter.deleted", object_type="starter_item",
                           object_id=str(item["id"]), outcome="success", actor_id=principal.user_id,
                           correlation_id=_rid(request), severity="high",
                           details={"report": item["item_key"], "user_copies": public["user_copies_unaffected"],
                                    "dashboards_with_new_draft": len(public["dashboards_getting_new_draft"]),
                                    "drafts_changed": len(public["drafts_without_it"])})
        return {"dry_run": False, "effects": public, "notified": notified, "item": await _one_out(conn, item_id)}


@router.post("/admin/starter/{item_id}/restore")
async def restore_starter(item_id: str, request: Request,
                          principal: Principal = Depends(require(Perm.STARTER_MANAGE))) -> dict[str, Any]:
    async with tenant_tx(principal.tenant_id) as conn:
        item = await _item(conn, item_id, lock=True)
        if item["state"] != "deleted":
            raise TelaError(ErrorCode.CONFLICT, "Only a deleted starter report can be restored.")
        cid = str(item["connection_id"])
        version_id = await current_version_id(conn, cid)
        if version_id is None:
            raise TelaError(ErrorCode.CONFLICT, "Approve the data model first; starter reports run on it.")
        await conn.execute(text("UPDATE model.starter_pack_items SET state = 'active', updated_at = now() "
                                "WHERE id = :id"), {"id": item["id"]})
        await build_starter_reports(conn, tenant_id=principal.tenant_id, connection_id=cid, version_id=version_id,
                                    by=principal.user_id, keys={item["item_key"]})
        await refresh_starter_dashboards(conn, tenant_id=principal.tenant_id, connection_id=cid, by=principal.user_id)
        await audit.record(conn, tenant_id=principal.tenant_id, action="starter.restored", object_type="starter_item",
                           object_id=str(item["id"]), outcome="success", actor_id=principal.user_id,
                           correlation_id=_rid(request),
                           details={"report": item["item_key"], "pack_version": item["pack_version"]})
        return {"item": await _one_out(conn, item_id)}


# --------------------------------------------------------------------------- use and copy (REQ-114)


@router.get("/starter-reports")
async def starter_reports_for_me(principal: Principal = Depends(require(Perm.DASHBOARDS_VIEW))) -> dict[str, Any]:
    """Active starter reports not hidden from the person's roles: the composer shelf and the copy source."""
    async with tenant_tx(principal.tenant_id) as conn:
        rows = await fetch_all(conn, _ITEMS + " AND i.state = 'active' AND i.availability = 'available' AND "
                                              "i.content_id IS NOT NULL ORDER BY c.name, i.title")
        hidden = await hidden_report_keys(conn, principal)
        mine = {str(r["copied_from_id"]) for r in await fetch_all(
            conn, "SELECT copied_from_id FROM content.reports WHERE owner_id = :u AND copied_from_id IS NOT NULL AND "
                  "deleted_at IS NULL", {"u": principal.user_id})}
    return {"items": [{"id": str(r["content_id"]), "title": r["title"], "key": r["item_key"], "widgets": r["widgets"],
                       "connection": {"id": str(r["connection_id"]), "name": r["connection_name"]},
                       "copied": str(r["content_id"]) in mine}
                      for r in rows if (str(r["connection_id"]), r["item_key"]) not in hidden]}


class CopyIn(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)


@router.post("/reports/{report_id}/copy", status_code=201)
async def copy_report(report_id: str, request: Request, response: Response, body: CopyIn | None = None,
                      principal: Principal = Depends(require(Perm.REPORTS_CREATE))) -> dict[str, Any]:
    async with tenant_tx(principal.tenant_id) as conn:
        source = await readable_report(conn, principal, report_id)
        if source["origin"] != "starter":
            raise TelaError(ErrorCode.CONFLICT, "Only starter reports can be copied.")
        state = await report_state(conn, source["id"])
        title = (body.title.strip() if body and body.title else f"{state['title']} (copy)")[:200]
        copy = await create_report(conn, principal, title, state["page_filters"], with_section=False)
        await conn.execute(text("UPDATE content.reports SET origin = 'copy', copied_from_id = :s WHERE id = :id"),
                           {"s": source["id"], "id": copy["id"]})
        sections: dict[str, str] = {}
        for s in state["sections"]:
            sections[s["id"]] = new_id()
            await conn.execute(
                text("INSERT INTO content.report_sections (id, tenant_id, report_id, title, kind, position, collapsed) "
                     "VALUES (:id, :t, :r, :title, :k, :p, :c)"),
                {"id": sections[s["id"]], "t": principal.tenant_id, "r": copy["id"], "title": s["title"],
                 "k": s["kind"], "p": s["position"], "c": s["collapsed"]})
        for i in state["items"]:
            new_item = {**i, "id": new_id(), "section_id": sections[i["section_id"]]}
            if i["widget_id"]:
                # The copy's own widget: same certified spec, owned by the person, never touched by pack updates.
                wid = new_id()
                await conn.execute(
                    text("INSERT INTO content.widgets (id, tenant_id, title, spec, visual, follow_page_filters, "
                         "model_version_id, connection_id, certification_state, owner_id, source_kind, own_filters, "
                         "created_by) SELECT :new, tenant_id, title, spec, visual, follow_page_filters, "
                         "model_version_id, connection_id, certification_state, :u, source_kind, own_filters, :u "
                         "FROM content.widgets WHERE id = CAST(:old AS uuid)"),
                    {"new": wid, "u": principal.user_id, "old": i["widget_id"]})
                new_item["widget_id"] = wid
            await _insert_item(conn, copy, new_item)
        await conn.execute(text("UPDATE model.starter_pack_items SET copy_count = copy_count + 1 "
                                "WHERE content_id = :s"), {"s": source["id"]})
        await audit.record(conn, tenant_id=principal.tenant_id, action="report.copied", object_type="report",
                           object_id=str(copy["id"]), outcome="success", actor_id=principal.user_id,
                           correlation_id=_rid(request), details={"from_starter": str(source["id"])})
        fresh = await fetch_one(conn, "SELECT * FROM content.reports WHERE id = :id", {"id": copy["id"]})
        assert fresh is not None
        out = await report_out(conn, principal, fresh)
    response.headers["ETag"] = f'"{out["revision"]}"'
    return out
