"""Who may see or change authored content (FEAT-021, FEAT-024, FEAT-038, FEAT-041, FEAT-042).

* Starter reports belong to the pack (no owner): everyone may read them and run their widgets; nobody edits them.
* Reports, library widgets and dashboard drafts belong to their owner. Others see them only through a
  view share (DATA-034), and always inside **their own** scope: a share never widens row access.
* Published dashboards are seen through their **live version**: its frozen content, never the draft.
  A widget on a live version runs its frozen spec, so later edits in the library do not leak to readers.
* Anything else is a 404 with no metadata (api_catalog §Conventions).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import AsyncConnection

from tela_api.core.db import fetch_all, fetch_one
from tela_api.identity.permissions import Perm
from tela_api.identity.principal import Principal
from tela_common.errors import ErrorCode, TelaError

NO_REPORT = "That report does not exist or is not shared with you."
NO_DASHBOARD = "You don't have access to this dashboard."
NO_WIDGET = "That widget does not exist or is not shared with you."

LIVE_VERSIONS = """
    SELECT v.id, v.dashboard_id, v.version_no, v.frozen_content FROM content.dashboard_versions v
    JOIN content.dashboards d ON d.live_version_id = v.id AND d.status = 'published' AND d.deleted_at IS NULL
    WHERE v.state = 'live' AND EXISTS (
      SELECT 1 FROM content.publications p WHERE p.dashboard_version_id = v.id
      AND (p.all_roles OR p.role_id = ANY(CAST(:roles AS uuid[]))))
"""
_ITEM_PATH = "$.reports[*].sections[*].items[*] ? (@.widget_id == $w)"


async def _one(conn: AsyncConnection, sql: str, params: dict[str, Any]) -> dict[str, Any] | None:
    try:
        return await fetch_one(conn, sql, params)
    except Exception:  # noqa: BLE001 - a malformed id is simply "not found", never a leak
        return None


async def _shared(conn: AsyncConnection, principal: Principal, object_type: str, object_id: str) -> bool:
    row = await _one(conn, "SELECT 1 AS ok FROM content.shares WHERE object_type = :o AND "
                           "object_id = CAST(:id AS uuid) AND grantee_user_id = CAST(:u AS uuid)",
                     {"o": object_type, "id": object_id, "u": principal.user_id})
    return row is not None


async def own_report(conn: AsyncConnection, principal: Principal, report_id: str, *,
                     lock: bool = False) -> dict[str, Any]:
    sql = "SELECT * FROM content.reports WHERE id = CAST(:id AS uuid) AND deleted_at IS NULL"
    row = await _one(conn, sql + (" FOR UPDATE" if lock else ""), {"id": report_id})
    if row is None or str(row["owner_id"]) != principal.user_id:
        raise TelaError(ErrorCode.NOT_FOUND, NO_REPORT)
    return row


async def readable_report(conn: AsyncConnection, principal: Principal, report_id: str) -> dict[str, Any]:
    """A starter report, the owner, a view share on the report, or a view share on a dashboard that contains it."""
    row = await _one(conn, "SELECT * FROM content.reports WHERE id = CAST(:id AS uuid) AND deleted_at IS NULL",
                     {"id": report_id})
    if row is None:
        raise TelaError(ErrorCode.NOT_FOUND, NO_REPORT)
    if (row["origin"] == "starter" or str(row["owner_id"]) == principal.user_id
            or await _shared(conn, principal, "report", report_id)):
        return row
    via = await _one(conn, "SELECT 1 AS ok FROM content.dashboard_reports dr JOIN content.shares s ON "
                           "s.object_type = 'dashboard' AND s.object_id = dr.dashboard_id WHERE dr.report_id = :r "
                           "AND s.grantee_user_id = CAST(:u AS uuid)", {"r": row["id"], "u": principal.user_id})
    if via is None:
        raise TelaError(ErrorCode.NOT_FOUND, NO_REPORT)
    return row


async def own_dashboard(conn: AsyncConnection, principal: Principal, dashboard_id: str, *,
                        publisher_ok: bool = False, lock: bool = False) -> dict[str, Any]:
    """The owner; with ``publisher_ok`` also an Admin who holds ``dashboards.publish`` (DEC-05)."""
    sql = "SELECT * FROM content.dashboards WHERE id = CAST(:id AS uuid) AND kind = 'user' AND deleted_at IS NULL"
    row = await _one(conn, sql + (" FOR UPDATE" if lock else ""), {"id": dashboard_id})
    if row is None:
        raise TelaError(ErrorCode.NOT_FOUND, NO_DASHBOARD)
    if str(row["owner_id"]) == principal.user_id:
        return row
    if publisher_ok and principal.has(Perm.DASHBOARDS_PUBLISH):
        return row
    raise TelaError(ErrorCode.NOT_FOUND, NO_DASHBOARD)


async def readable_dashboard_draft(conn: AsyncConnection, principal: Principal, dashboard_id: str) -> dict[str, Any]:
    row = await _one(conn, "SELECT * FROM content.dashboards WHERE id = CAST(:id AS uuid) AND kind = 'user' "
                           "AND deleted_at IS NULL", {"id": dashboard_id})
    if row is None:
        raise TelaError(ErrorCode.NOT_FOUND, NO_DASHBOARD)
    if (str(row["owner_id"]) == principal.user_id or principal.has(Perm.DASHBOARDS_PUBLISH)
            or await _shared(conn, principal, "dashboard", dashboard_id)):
        return row
    raise TelaError(ErrorCode.NOT_FOUND, NO_DASHBOARD)


async def live_version(conn: AsyncConnection, principal: Principal, dashboard_id: str) -> dict[str, Any] | None:
    return await _one(conn, LIVE_VERSIONS + " AND v.dashboard_id = CAST(:d AS uuid)",
                      {"roles": list(principal.role_ids), "d": dashboard_id})


def frozen_items(frozen: dict[str, Any]) -> list[dict[str, Any]]:
    return [i for r in frozen.get("reports", []) for s in r.get("sections", []) for i in s.get("items", [])]


@dataclass
class ResolvedWidget:
    id: str
    title: str
    spec: dict[str, Any]
    visual: str
    follow_page_filters: bool
    badge: str
    source: str  # starter | library | frozen
    model_version_id: str
    connection_id: str
    owner_id: str | None = None
    hidden_for_roles: list[str] = field(default_factory=list)
    dashboard_version_id: str | None = None
    own_filters: list[dict[str, Any]] = field(default_factory=list)  # the Creator's fixed filters (ENH-023 US-221)

    def hidden_for(self, principal: Principal) -> bool:
        return bool(set(self.hidden_for_roles) & set(principal.role_ids))


def _from_frozen(item: dict[str, Any], version_id: str) -> ResolvedWidget:
    return ResolvedWidget(id=item["widget_id"], title=item["title"], spec=item["spec"], visual=item["visual"],
                          follow_page_filters=item.get("follow_page_filters", True),
                          badge=item.get("badge", "certified"),
                          source="frozen", model_version_id=item["spec"]["model_version_id"],
                          connection_id=item["spec"]["connection_id"],
                          hidden_for_roles=list(item.get("hidden_for_roles", [])), dashboard_version_id=version_id,
                          own_filters=list(item.get("own_filters") or []))


async def resolve_widget(conn: AsyncConnection, principal: Principal, widget_id: str, *,
                         dashboard_version_id: str | None = None) -> ResolvedWidget:
    if dashboard_version_id:
        v = await _one(conn, LIVE_VERSIONS + " AND v.id = CAST(:v AS uuid)",
                       {"roles": list(principal.role_ids), "v": dashboard_version_id})
        item = next((i for i in frozen_items(v["frozen_content"]) if i.get("widget_id") == widget_id),
                    None) if v else None
        if v is None or item is None:
            raise TelaError(ErrorCode.NOT_FOUND, NO_WIDGET)
        return _from_frozen(item, str(v["id"]))

    w = await _one(conn, "SELECT w.*, t.dashboard_id FROM content.widgets w LEFT JOIN content.dashboard_tabs t "
                         "ON t.id = w.dashboard_tab_id WHERE w.id = CAST(:id AS uuid) AND w.deleted_at IS NULL",
                   {"id": widget_id})
    if w is not None and w["dashboard_tab_id"] is not None:
        visible = await _one(conn, "SELECT 1 AS ok FROM content.dashboards d WHERE d.id = :d AND "
                                   "d.status = 'published' AND EXISTS (SELECT 1 FROM content.publications p WHERE "
                                   "p.dashboard_id = d.id AND "
                                   "(p.all_roles OR p.role_id = ANY(CAST(:roles AS uuid[]))))",
                             {"d": w["dashboard_id"], "roles": list(principal.role_ids)})
        if visible is None:
            raise TelaError(ErrorCode.NOT_FOUND, NO_WIDGET)
        return ResolvedWidget(id=widget_id, title=w["title"], spec=w["spec"], visual=w["visual"],
                              follow_page_filters=w["follow_page_filters"], badge=w["certification_state"],
                              source="starter", model_version_id=str(w["model_version_id"]),
                              connection_id=str(w["connection_id"]), own_filters=list(w.get("own_filters") or []))
    if w is not None and w["owner_id"] is None and w["dashboard_tab_id"] is None:
        starter = await _one(conn, "SELECT 1 AS ok FROM content.report_items i JOIN content.reports r ON r.id = "
                                   "i.report_id WHERE i.widget_id = :w AND r.origin = 'starter' "
                                   "AND r.deleted_at IS NULL",
                             {"w": w["id"]})
        if starter is not None:
            return ResolvedWidget(id=widget_id, title=w["title"], spec=w["spec"], visual=w["visual"],
                                  follow_page_filters=w["follow_page_filters"], badge=w["certification_state"],
                                  source="starter", model_version_id=str(w["model_version_id"]),
                                  connection_id=str(w["connection_id"]), own_filters=list(w.get("own_filters") or []))
    if w is not None and await _library_access(conn, principal, w):
        return ResolvedWidget(id=widget_id, title=w["title"], spec=w["spec"], visual=w["visual"],
                              follow_page_filters=w["follow_page_filters"], badge=w["certification_state"],
                              source="library", model_version_id=str(w["model_version_id"]),
                              connection_id=str(w["connection_id"]), own_filters=list(w.get("own_filters") or []),
                              owner_id=str(w["owner_id"]))
    # A reader of a published dashboard: the frozen spec of the live version that shows this widget.
    rows = await fetch_all(conn, LIVE_VERSIONS + " AND jsonb_path_exists(v.frozen_content, CAST(:path AS jsonpath), "
                                                 "jsonb_build_object('w', CAST(:w AS text))) LIMIT 1",
                           {"roles": list(principal.role_ids), "path": _ITEM_PATH, "w": widget_id})
    for v in rows:
        item = next(i for i in frozen_items(v["frozen_content"]) if i.get("widget_id") == widget_id)
        return _from_frozen(item, str(v["id"]))
    raise TelaError(ErrorCode.NOT_FOUND, NO_WIDGET)


async def _library_access(conn: AsyncConnection, principal: Principal, w: dict[str, Any]) -> bool:
    if w["owner_id"] is not None and str(w["owner_id"]) == principal.user_id:
        return True
    row = await _one(
        conn,
        """
        SELECT 1 AS ok FROM content.shares s WHERE s.grantee_user_id = CAST(:u AS uuid) AND (
          (s.object_type = 'widget' AND s.object_id = :w)
          OR (s.object_type = 'report' AND s.object_id IN (
                SELECT report_id FROM content.report_items WHERE widget_id = :w))
          OR (s.object_type = 'dashboard' AND s.object_id IN (
                SELECT dr.dashboard_id FROM content.dashboard_reports dr JOIN content.report_items i
                ON i.report_id = dr.report_id WHERE i.widget_id = :w)))
        LIMIT 1
        """, {"u": principal.user_id, "w": w["id"]})
    return row is not None
