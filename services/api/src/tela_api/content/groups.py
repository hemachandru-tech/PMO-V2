"""Dashboard groups for the sidebar (ENH-009, US-169; FEAT-061).

* Groups are shared by the whole tenant (W1). Anyone with ``dashboards.group`` (Admin and Creator) creates them;
  a Creator changes only the groups they created, an Admin (``dashboards.publish``, DEC-05) changes any group and
  is the only one who reorders the groups, since the order is everyone's (W2).
* A group never widens access (W3). Each person sees, in each group, only the dashboards they can already open:
  published to one of their roles (starter dashboards an Admin hid from all their reports excepted), their own
  drafts, drafts shared with them, and for Admins any draft. A group with nothing to show them is left out.
  Group names are not secret; dashboard titles are, so an editor learns only how many items they cannot see, and
  saving a group's items keeps those items untouched.
* A dashboard can sit in several groups (W4). Every change is audited.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from tela_api.audit import service as audit
from tela_api.content.viewer import visible_dashboards
from tela_api.core.db import fetch_all, fetch_one, tenant_tx
from tela_api.home.router import hidden_starter_dashboards
from tela_api.identity.deps import require
from tela_api.identity.permissions import Perm
from tela_api.identity.principal import Principal
from tela_common.errors import ErrorCode, TelaError
from tela_common.ids import new_id

router = APIRouter(prefix="/api/v1", tags=["dashboard-groups"])
NO_GROUP = "That dashboard group does not exist."
NOT_YOURS = "Only the person who created this group or an Admin can change it."
MAX_ITEMS = 100
_ICON = r"^[a-z][a-z0-9-]{0,39}$"


class DashboardGroupIn(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    icon: str | None = Field(default=None, pattern=_ICON)

    @field_validator("name")
    @classmethod
    def _trim(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Give the group a name.")
        return v.strip()


class DashboardGroupPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=60)
    icon: str | None = Field(default=None, pattern=_ICON)
    clear_icon: bool = False

    @field_validator("name")
    @classmethod
    def _trim(cls, v: str | None) -> str | None:
        if v is not None and not v.strip():
            raise ValueError("Give the group a name.")
        return v.strip() if v is not None else None


class DashboardGroupItemsIn(BaseModel):
    dashboard_ids: list[str] = Field(max_length=MAX_ITEMS)


class DashboardGroupOrderIn(BaseModel):
    group_ids: list[str] = Field(max_length=500)


class DashboardGroupMembershipIn(BaseModel):
    group_ids: list[str] = Field(max_length=500)


def _rid(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


def _is_admin(principal: Principal) -> bool:
    return principal.has(Perm.DASHBOARDS_PUBLISH)


async def readable_dashboards(conn: AsyncConnection, principal: Principal) -> dict[str, dict[str, Any]]:
    """Every dashboard this person can open, with the link the sidebar should use (published view first)."""
    out: dict[str, dict[str, Any]] = {}
    published = [d for d in await visible_dashboards(conn, principal) if d["deleted_at"] is None]
    hidden = await hidden_starter_dashboards(conn, principal, published)
    for d in published:
        if str(d["id"]) not in hidden:
            out[str(d["id"])] = {"id": str(d["id"]), "title": d["title"], "kind": d["kind"],
                                 "link": f"/dashboards/{d['id']}", "published": True}
    drafts = await fetch_all(
        conn,
        """
        SELECT d.id, d.title, d.kind FROM content.dashboards d
        WHERE d.kind = 'user' AND d.deleted_at IS NULL AND (:admin OR d.owner_id = CAST(:u AS uuid) OR EXISTS (
          SELECT 1 FROM content.shares s WHERE s.object_type = 'dashboard' AND s.object_id = d.id
          AND s.grantee_user_id = CAST(:u AS uuid)))
        """, {"u": principal.user_id, "admin": _is_admin(principal)})
    for d in drafts:
        out.setdefault(str(d["id"]), {"id": str(d["id"]), "title": d["title"], "kind": d["kind"],
                                      "link": f"/dashboards/{d['id']}/draft", "published": False})
    return out


async def _groups(conn: AsyncConnection) -> list[dict[str, Any]]:
    return await fetch_all(
        conn, "SELECT g.*, u.display_name AS creator FROM content.dashboard_groups g JOIN iam.users u "
              "ON u.id = g.created_by WHERE g.deleted_at IS NULL ORDER BY g.position, lower(g.name)")


async def _items(conn: AsyncConnection, group_ids: list[str]) -> dict[str, list[str]]:
    rows = await fetch_all(
        conn, "SELECT i.group_id, i.dashboard_id FROM content.dashboard_group_items i JOIN content.dashboards d "
              "ON d.id = i.dashboard_id AND d.deleted_at IS NULL WHERE i.group_id = ANY(CAST(:g AS uuid[])) "
              "ORDER BY i.group_id, i.position", {"g": group_ids})
    out: dict[str, list[str]] = {g: [] for g in group_ids}
    for r in rows:
        out[str(r["group_id"])].append(str(r["dashboard_id"]))
    return out


def _out(g: dict[str, Any], items: list[str], readable: dict[str, dict[str, Any]],
         principal: Principal) -> dict[str, Any]:
    shown = [readable[i] for i in items if i in readable]
    return {"id": str(g["id"]), "name": g["name"], "icon": g["icon"], "position": g["position"],
            "created_by": {"id": str(g["created_by"]), "name": g["creator"]},
            "can_edit": _is_admin(principal) or str(g["created_by"]) == principal.user_id,
            "dashboards": shown, "hidden_count": len(items) - len(shown)}


async def _one(conn: AsyncConnection, principal: Principal, group_id: str, *, edit: bool) -> dict[str, Any]:
    try:
        g = await fetch_one(conn, "SELECT g.*, u.display_name AS creator FROM content.dashboard_groups g JOIN "
                                  "iam.users u ON u.id = g.created_by WHERE g.id = CAST(:id AS uuid) AND "
                                  "g.deleted_at IS NULL FOR UPDATE OF g", {"id": group_id})
    except Exception:  # noqa: BLE001 - a malformed id is simply "not found"
        g = None
    if g is None:
        raise TelaError(ErrorCode.NOT_FOUND, NO_GROUP)
    if edit and not (_is_admin(principal) or str(g["created_by"]) == principal.user_id):
        raise TelaError(ErrorCode.FORBIDDEN, NOT_YOURS)
    return g


async def _name_free(conn: AsyncConnection, name: str, group_id: str | None = None) -> None:
    clash = await fetch_one(conn, "SELECT 1 AS x FROM content.dashboard_groups WHERE deleted_at IS NULL AND "
                                  "lower(btrim(name)) = lower(btrim(:n)) AND id IS DISTINCT FROM CAST(:id AS uuid)",
                            {"n": name, "id": group_id})
    if clash:
        raise TelaError(ErrorCode.CONFLICT, f"A group called “{name}” already exists.")


async def _one_out(conn: AsyncConnection, principal: Principal, group_id: str) -> dict[str, Any]:
    g = await _one(conn, principal, group_id, edit=False)
    items = await _items(conn, [group_id])
    return _out(g, items[group_id], await readable_dashboards(conn, principal), principal)


async def _record(conn: AsyncConnection, principal: Principal, request: Request, action: str, group_id: str,
                  details: dict[str, Any]) -> None:
    await audit.record(conn, tenant_id=principal.tenant_id, action=f"dashboard_group.{action}",
                       object_type="dashboard_group", object_id=group_id, outcome="success",
                       actor_id=principal.user_id, correlation_id=_rid(request), details=details)


@router.get("/me/dashboard-groups")
async def my_groups(principal: Principal = Depends(require(Perm.DASHBOARDS_VIEW))) -> dict[str, Any]:
    """The sidebar's groups: only dashboards this person can open, and only groups with at least one of them."""
    async with tenant_tx(principal.tenant_id) as conn:
        groups = await _groups(conn)
        items = await _items(conn, [str(g["id"]) for g in groups])
        readable = await readable_dashboards(conn, principal)
    out = []
    for g in groups:
        shown = [readable[i] for i in items[str(g["id"])] if i in readable]
        if shown:
            out.append({"id": str(g["id"]), "name": g["name"], "icon": g["icon"], "dashboards": shown})
    return {"items": out}


@router.get("/dashboard-groups")
async def list_groups(principal: Principal = Depends(require(Perm.DASHBOARDS_GROUP))) -> dict[str, Any]:
    async with tenant_tx(principal.tenant_id) as conn:
        groups = await _groups(conn)
        items = await _items(conn, [str(g["id"]) for g in groups])
        readable = await readable_dashboards(conn, principal)
    return {"items": [_out(g, items[str(g["id"])], readable, principal) for g in groups],
            "can_reorder": _is_admin(principal)}


@router.post("/dashboard-groups", status_code=201)
async def create_group(body: DashboardGroupIn, request: Request,
                       principal: Principal = Depends(require(Perm.DASHBOARDS_GROUP))) -> dict[str, Any]:
    async with tenant_tx(principal.tenant_id) as conn:
        await _name_free(conn, body.name)
        gid = new_id()
        await conn.execute(
            text("INSERT INTO content.dashboard_groups (id, tenant_id, name, icon, position, created_by) "
                 "SELECT :id, :t, :n, :i, coalesce(max(position) + 1, 0), :u FROM content.dashboard_groups "
                 "WHERE deleted_at IS NULL"),
            {"id": gid, "t": principal.tenant_id, "n": body.name, "i": body.icon, "u": principal.user_id})
        await _record(conn, principal, request, "created", gid, {"name": body.name})
        return await _one_out(conn, principal, gid)


@router.patch("/dashboard-groups/{group_id}")
async def update_group(group_id: str, body: DashboardGroupPatch, request: Request,
                       principal: Principal = Depends(require(Perm.DASHBOARDS_GROUP))) -> dict[str, Any]:
    async with tenant_tx(principal.tenant_id) as conn:
        g = await _one(conn, principal, group_id, edit=True)
        changes: dict[str, Any] = {}
        if body.name is not None and body.name != g["name"]:
            await _name_free(conn, body.name, group_id)
            changes["name"] = body.name
        if body.clear_icon:
            changes["icon"] = None
        elif body.icon is not None and body.icon != g["icon"]:
            changes["icon"] = body.icon
        if changes:
            sets = ", ".join(f"{k} = :{k}" for k in changes)
            await conn.execute(text(f"UPDATE content.dashboard_groups SET {sets} WHERE id = :id"),  # noqa: S608
                               {**changes, "id": group_id})
            await _record(conn, principal, request, "updated", group_id,
                          {"from": g["name"], **changes})
        return await _one_out(conn, principal, group_id)


@router.delete("/dashboard-groups/{group_id}", status_code=204)
async def delete_group(group_id: str, request: Request,
                       principal: Principal = Depends(require(Perm.DASHBOARDS_GROUP))) -> Response:
    async with tenant_tx(principal.tenant_id) as conn:
        g = await _one(conn, principal, group_id, edit=True)
        await conn.execute(text("UPDATE content.dashboard_groups SET deleted_at = now() WHERE id = :id"),
                           {"id": group_id})
        await conn.execute(text("DELETE FROM content.dashboard_group_items WHERE group_id = :id"), {"id": group_id})
        await _record(conn, principal, request, "deleted", group_id, {"name": g["name"]})
    return Response(status_code=204)


@router.put("/dashboard-groups/order")
async def reorder_groups(body: DashboardGroupOrderIn, request: Request,
                         principal: Principal = Depends(require(Perm.DASHBOARDS_GROUP))) -> dict[str, Any]:
    if not _is_admin(principal):
        raise TelaError(ErrorCode.FORBIDDEN, "Only an Admin can change the order of the groups.")
    async with tenant_tx(principal.tenant_id) as conn:
        current = [str(g["id"]) for g in await _groups(conn)]
        if len(set(body.group_ids)) != len(body.group_ids) or set(body.group_ids) != set(current):
            raise TelaError(ErrorCode.VALIDATION_ERROR, "Send every group exactly once, in the new order.")
        for pos, gid in enumerate(body.group_ids):
            await conn.execute(text("UPDATE content.dashboard_groups SET position = :p WHERE id = :id"),
                               {"p": pos, "id": gid})
        await audit.record(conn, tenant_id=principal.tenant_id, action="dashboard_group.reordered",
                           object_type="dashboard_group", outcome="success", actor_id=principal.user_id,
                           correlation_id=_rid(request), details={"groups": len(body.group_ids)})
    return await list_groups(principal)


async def _save_items(conn: AsyncConnection, principal: Principal, group_id: str, wanted: list[str],
                      readable: dict[str, dict[str, Any]]) -> tuple[list[str], list[str]]:
    """Set the readable part of a group to ``wanted`` (in order); items the caller cannot open stay, after them."""
    before = (await _items(conn, [group_id]))[group_id]
    kept = [i for i in before if i not in readable]
    after = wanted + kept
    if len(after) > MAX_ITEMS:
        raise TelaError(ErrorCode.VALIDATION_ERROR, f"A group holds at most {MAX_ITEMS} dashboards.")
    await conn.execute(text("DELETE FROM content.dashboard_group_items WHERE group_id = :g "
                            "AND NOT (dashboard_id = ANY(CAST(:keep AS uuid[])))"), {"g": group_id, "keep": kept})
    for pos, did in enumerate(after):
        await conn.execute(
            text("INSERT INTO content.dashboard_group_items (tenant_id, group_id, dashboard_id, position, added_by) "
                 "VALUES (:t, :g, :d, :p, :u) ON CONFLICT (group_id, dashboard_id) DO UPDATE SET position = :p"),
            {"t": principal.tenant_id, "g": group_id, "d": did, "p": pos, "u": principal.user_id})
    return before, after


@router.put("/dashboard-groups/{group_id}/items")
async def set_items(group_id: str, body: DashboardGroupItemsIn, request: Request,
                    principal: Principal = Depends(require(Perm.DASHBOARDS_GROUP))) -> dict[str, Any]:
    if len(set(body.dashboard_ids)) != len(body.dashboard_ids):
        raise TelaError(ErrorCode.VALIDATION_ERROR, "A dashboard can be in a group only once.")
    async with tenant_tx(principal.tenant_id) as conn:
        await _one(conn, principal, group_id, edit=True)
        readable = await readable_dashboards(conn, principal)
        if any(d not in readable for d in body.dashboard_ids):
            raise TelaError(ErrorCode.NOT_FOUND, "One of those dashboards does not exist or you can't open it.")
        before, after = await _save_items(conn, principal, group_id, body.dashboard_ids, readable)
        if before != after:
            await _record(conn, principal, request, "items_changed", group_id,
                          {"added": len(set(after) - set(before)), "removed": len(set(before) - set(after)),
                           "dashboards": len(after)})
        return await _one_out(conn, principal, group_id)


@router.put("/dashboards/{dashboard_id}/groups")
async def set_dashboard_groups(dashboard_id: str, body: DashboardGroupMembershipIn, request: Request,
                               principal: Principal = Depends(require(Perm.DASHBOARDS_GROUP))) -> dict[str, Any]:
    """"Add to group…" from a dashboard: it ends up in exactly ``group_ids`` among the groups the caller may change;
    groups the caller may not change are left as they are."""
    async with tenant_tx(principal.tenant_id) as conn:
        readable = await readable_dashboards(conn, principal)
        if dashboard_id not in readable:
            raise TelaError(ErrorCode.NOT_FOUND, "That dashboard does not exist or you can't open it.")
        groups = {str(g["id"]): g for g in await _groups(conn)}
        if any(g not in groups for g in body.group_ids):
            raise TelaError(ErrorCode.NOT_FOUND, NO_GROUP)
        editable = {gid for gid, g in groups.items() if _is_admin(principal) or str(g["created_by"]) ==
                    principal.user_id}
        if any(g not in editable for g in body.group_ids):
            raise TelaError(ErrorCode.FORBIDDEN, NOT_YOURS)
        items = await _items(conn, list(groups))
        for gid in sorted(editable):
            mine = [i for i in items[gid] if i in readable]
            has = dashboard_id in mine
            if (gid in body.group_ids) == has:
                continue
            await _one(conn, principal, gid, edit=True)  # locks the row
            wanted = [*mine, dashboard_id] if not has else [i for i in mine if i != dashboard_id]
            await _save_items(conn, principal, gid, wanted, readable)
            await _record(conn, principal, request, "items_changed", gid,
                          {"added": 0 if has else 1, "removed": 1 if has else 0, "dashboards": len(wanted)})
        return {"dashboard_id": dashboard_id, "group_ids": [g for g in groups if g in body.group_ids or (
            g not in editable and dashboard_id in items[g])]}
