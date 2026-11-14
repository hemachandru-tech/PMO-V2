"""Home shelves (API-056; FEAT-017; US-044).

Greeting with the person's scope on each source and an honest "data as of" (the latest source read,
not a cache hit), and four shelves filtered per person: Starter, Published to my role, Mine (Creators
and Admins only) and Shared with me. Starter items hidden from the person's role are left out.
Suggested questions (US-138) are built from certified measures the person can read, never from content.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncConnection

from tela_api.content.starters import hidden_report_keys
from tela_api.content.viewer import tile, tile_counts, visible_dashboards
from tela_api.core.db import fetch_all, fetch_one, tenant_tx
from tela_api.identity.deps import require
from tela_api.identity.permissions import Perm
from tela_api.identity.principal import Principal
from tela_api.identity.scope import resolve_scope
from tela_api.model.store import current_version_id, load_snapshot
from tela_common.errors import TelaError
from tela_query_spec.expr import measure_refs
from tela_query_spec.model import ModelSnapshot, step_fans_out

router = APIRouter(prefix="/api/v1", tags=["home"])
_REPORTS = ("SELECT r.id, r.title, r.owner_id, r.updated_at, r.published_snapshot IS NOT NULL AS published, "
            "(SELECT count(*) FROM content.report_items i WHERE i.report_id = r.id AND i.item_kind = 'widget') "
            "AS widgets FROM content.reports r")


SUGGESTIONS = 6


def _breaks_down(snap: ModelSnapshot, entity: str, dim: Any) -> bool:
    """The dimension sits on the measure's entity or is reached without fanning out (each row maps to one value)."""
    path = snap.join_path(entity, dim.entity)
    return path is not None and not any(step_fans_out(link, forward) for link, forward in path)


def suggestions(snap: ModelSnapshot, hidden: frozenset[str], limit: int = SUGGESTIONS) -> list[str]:
    """Questions from certified measures, taken in turn from each entity and each paired with a different dimension
    it can break down by (its own first, then status and ordered dimensions), so the list covers the model rather
    than one table. Entities with the most dimensions of their own come first: on a model built from a raw schema
    that puts the business tables ahead of reference tables.

    Measures and dimensions that use a field hidden from the person are left out, so every suggestion can be answered.
    """
    by_entity: dict[str, list[Any]] = {}
    for m in snap.measures:
        if m.status == "certified" and not any(r in hidden for r in measure_refs(m.entity, m.expression)):
            by_entity.setdefault(m.entity, []).append(m)
    dims = sorted((d for d in snap.dimensions if not d.is_temporal and f"{d.entity}.{d.field}" not in hidden),
                  key=lambda d: (not d.is_status, not d.sort_order))
    own = Counter(d.entity for d in dims)
    queues = sorted(by_entity.values(), key=lambda q: -own[q[0].entity])
    ordered = [q[i] for i in range(max(map(len, queues), default=0)) for q in queues if i < len(q)]
    out: list[str] = []
    used: set[str] = set()
    for m in ordered[:limit]:
        dim = next((d for d in sorted(dims, key=lambda d: d.entity != m.entity)
                    if d.key not in used and _breaks_down(snap, m.entity, d)), None)
        if dim is not None:
            used.add(dim.key)
        out.append(f"{m.label} by {dim.label.lower()}" if dim else m.label)
    return out


def _report_tile(r: dict[str, Any]) -> dict[str, Any]:
    return {"id": str(r["id"]), "type": "report", "title": r["title"], "badges": ["draft"] if not r["published"]
            else ["certified"], "version": None, "count": {"widgets": r["widgets"]}, "last_edited_at": r["updated_at"],
            "owner_id": r["owner_id"]}


def _dashboard_tile(d: dict[str, Any], counts: dict[str, Any]) -> dict[str, Any]:
    return {**tile(d, counts), "owner_id": d.get("owner_id")}


def _draft_tile(d: dict[str, Any], counts: dict[str, Any]) -> dict[str, Any]:
    out = _dashboard_tile(d, counts)
    out["badges"] = ["certified"] if d["live_version_id"] else ["draft"]
    out["version"] = d["version_no"] or None
    out["published"] = d["live_version_id"] is not None
    return out


async def _name_owners(conn: Any, tiles: list[dict[str, Any]]) -> None:
    """Swap owner ids for display names on every tile (US-138: cards show who owns them); starter content reads
    "Tela starter"."""
    ids = sorted({str(t["owner_id"]) for t in tiles if t.get("owner_id")})
    names = {str(r["id"]): r["display_name"] for r in await fetch_all(
        conn, "SELECT id, display_name FROM iam.users WHERE id = ANY(CAST(:ids AS uuid[]))", {"ids": ids})} \
        if ids else {}
    for t in tiles:
        owner = t.pop("owner_id", None)
        t["owner"] = "Tela starter" if "starter" in t["badges"] else names.get(str(owner)) if owner else None


async def hidden_starter_dashboards(conn: AsyncConnection, principal: Principal,
                                    dashboards: list[dict[str, Any]]) -> set[str]:
    """Starter dashboards whose every report an Admin hid from one of the person's roles (FEAT-046)."""
    keys = await hidden_report_keys(conn, principal)
    starters = [str(d["id"]) for d in dashboards if d["kind"] == "starter"]
    if not keys or not starters:
        return set()
    tabs = await fetch_all(conn, "SELECT t.dashboard_id, t.report_key, d.connection_id FROM content.dashboard_tabs t "
                                 "JOIN content.dashboards d ON d.id = t.dashboard_id "
                                 "WHERE t.dashboard_id = ANY(CAST(:d AS uuid[]))", {"d": starters})
    shown = {str(t["dashboard_id"]) for t in tabs if (str(t["connection_id"]), t["report_key"]) not in keys}
    return set(starters) - shown


@router.get("/home")
async def home(principal: Principal = Depends(require(Perm.DASHBOARDS_VIEW))) -> dict[str, Any]:
    async with tenant_tx(principal.tenant_id) as conn:
        dashboards = await visible_dashboards(conn, principal)
        hidden = await hidden_starter_dashboards(conn, principal, dashboards)
        counts = await tile_counts(conn, [str(d["id"]) for d in dashboards])
        sources = await fetch_all(
            conn, "SELECT c.id, c.name FROM conn.connections c JOIN model.data_models m ON m.connection_id = c.id "
                  "WHERE c.deleted_at IS NULL AND m.current_version_id IS NOT NULL ORDER BY c.name")
        scopes: list[dict[str, Any]] = []
        suggested: list[str] = []
        for s in sources:
            try:
                scope = await resolve_scope(conn, principal, str(s["id"]))
            except TelaError:
                continue
            if scope.entities:
                scopes.append({"connection_id": str(s["id"]), "connection_name": s["name"], "text": scope.summary})
                version_id = await current_version_id(conn, str(s["id"]))
                if version_id and len(suggested) < SUGGESTIONS:
                    snap = await load_snapshot(conn, principal.tenant_id, version_id)
                    suggested += suggestions(snap, scope.hidden_fields, SUGGESTIONS - len(suggested))
        last = await fetch_one(conn, "SELECT max(created_at) AS at FROM ops.query_executions WHERE outcome = 'ok' "
                                     "AND NOT cache_hit")
        mine_d = await fetch_all(conn, "SELECT * FROM content.dashboards WHERE kind = 'user' AND owner_id = :u "
                                       "AND deleted_at IS NULL ORDER BY updated_at DESC", {"u": principal.user_id}) \
            if principal.has(Perm.REPORTS_CREATE) else []
        mine_r = await fetch_all(conn, _REPORTS + " WHERE r.owner_id = :u AND r.deleted_at IS NULL ORDER BY "
                                       "r.updated_at DESC LIMIT 50", {"u": principal.user_id}) \
            if principal.has(Perm.REPORTS_CREATE) else []
        shared_r = await fetch_all(conn, _REPORTS + " JOIN content.shares s ON s.object_type = 'report' AND "
                                         "s.object_id = r.id WHERE s.grantee_user_id = :u AND r.deleted_at IS NULL "
                                         "ORDER BY s.created_at DESC", {"u": principal.user_id})
        shared_d = await fetch_all(conn, "SELECT d.* FROM content.dashboards d JOIN content.shares s ON s.object_type "
                                         "= 'dashboard' AND s.object_id = d.id WHERE s.grantee_user_id = :u AND "
                                         "d.deleted_at IS NULL ORDER BY s.created_at DESC", {"u": principal.user_id})
        draft_counts = await tile_counts(conn, [str(d["id"]) for d in mine_d + shared_d])
    starter = [_dashboard_tile(d, counts.get(str(d["id"]), {})) for d in dashboards
               if d["kind"] == "starter" and str(d["id"]) not in hidden]
    published = [_dashboard_tile(d, counts.get(str(d["id"]), {})) for d in dashboards if d["kind"] == "user"]
    shared = [_draft_tile(d, draft_counts.get(str(d["id"]), {})) for d in shared_d] + [_report_tile(r)
                                                                                         for r in shared_r]
    shelves: dict[str, Any] = {"starter": starter, "published_to_my_role": published, "shared_with_me": shared}
    if principal.has(Perm.REPORTS_CREATE):
        shelves["mine"] = [_draft_tile(d, draft_counts.get(str(d["id"]), {})) for d in mine_d] + \
            [_report_tile(r) for r in mine_r]
    async with tenant_tx(principal.tenant_id) as conn:
        await _name_owners(conn, [t for shelf in shelves.values() for t in shelf])
    return {
        "greeting": {"name": principal.display_name, "scopes": scopes, "as_of": last["at"] if last else None},
        "shelves": shelves,
        "empty_text": {"starter": "No starter content yet. An Admin installs it with a data source.",
                       "published_to_my_role": "Nothing published to your role yet.",
                       "mine": "Your drafts and reports will appear here.",
                       "shared_with_me": "Nothing has been shared with you yet."},
        "can_create": principal.has(Perm.REPORTS_CREATE),
        "suggested_questions": suggested if principal.has(Perm.ASK_USE) else [],
    }
