"""Starter reports and dashboards from the installed pack (FEAT-046, FEAT-042; US-039, US-052, US-098).

On model approval, every available, active starter report of the pack becomes

* a pack-owned ``content.reports`` row (``origin = 'starter'``, no owner, so nobody edits it on the canvas)
  that Creators can put on their own dashboards or copy (a copy is theirs and never follows the pack), and
* a tab of a starter dashboard (PMO — Projects, PMO — Requests), published to every role.

Both are pinned to the approved version. Widgets store spec + model version only, never numbers (DATA-026).
A starter report holds the widgets the approved model can run; the others are named in its footnote (US-155).
Re-approval re-points the starters to the new version; user copies are never touched. A deleted starter
(RULE-017) leaves its starter dashboard at once; Restore from pack rebuilds it from the installed pack.
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from tela_api.core.db import fetch_all, fetch_one
from tela_api.identity.principal import Principal
from tela_api.model.store import current_version_id
from tela_api.packs.manifest import PackManifest, fit_widgets, left_out_note, spec_of
from tela_common.ids import new_id


def catalogue_layout(visuals: list[str], y0: int = 0) -> list[tuple[int, int, int, int]]:
    """(x, y, w, h) per widget in the report catalogue's layout (US-155): the figures share one 12-column strip,
    then the visuals two by two (6 + 6, a lone last one 12 wide), then the tables across the width."""
    place: dict[int, tuple[int, int, int, int]] = {}
    figures = [i for i, v in enumerate(visuals) if v == "figure"]
    charts = [i for i, v in enumerate(visuals) if v not in ("figure", "table")]
    y, x = y0, 0
    for k, i in enumerate(figures):
        w = 12 // len(figures) + (1 if k < 12 % len(figures) else 0)
        place[i], x = (x, y, w, 2), x + w
    y += 2 if figures else 0
    for k, i in enumerate(charts):
        lone = k == len(charts) - 1 and k % 2 == 0
        place[i] = ((k % 2) * 6, y, 12 if lone else 6, 4)
        y += 4 if lone or k % 2 else 0
    for i, v in enumerate(visuals):
        if v == "table":
            place[i], y = (0, y, 12, 5), y + 5
    return [place[i] for i in range(len(visuals))]


# Every query reads only the items of the pack version the connection's model has installed: rows left by an
# earlier install of another version of the pack are ignored.


async def _manifest(conn: AsyncConnection, connection_id: str) -> PackManifest | None:
    row = await fetch_one(
        conn, "SELECT p.manifest FROM model.data_models m JOIN model.starter_packs p ON p.id = m.installed_pack_id "
              "WHERE m.connection_id = CAST(:c AS uuid)", {"c": connection_id})
    return PackManifest.model_validate(row["manifest"]) if row else None


async def _definition(conn: AsyncConnection, version_id: str) -> dict[str, Any]:
    row = await fetch_one(conn, "SELECT definition FROM model.model_versions WHERE id = CAST(:v AS uuid)",
                          {"v": version_id})
    return dict(row["definition"]) if row else {}


def shown(report: dict[str, Any], definition: dict[str, Any]) -> tuple[list[int], dict[str, Any]]:
    """The widget indexes a starter report shows on this model, and the report fitted to it: detail tables without
    the columns the model lacks, the left-out widgets named at the end of its footnote."""
    fitted = fit_widgets(report, definition)
    keep = [i for i, w in enumerate(fitted) if w is not None]
    left = [report["widgets"][i]["title"] for i, w in enumerate(fitted) if w is None]
    report = {**report, "widgets": [w if w is not None else report["widgets"][i] for i, w in enumerate(fitted)]}
    if left:
        report["footnote"] = " ".join(t for t in (report.get("footnote"), left_out_note(left)) if t)
    return keep, report


async def build_starter_reports(conn: AsyncConnection, *, tenant_id: str, connection_id: str, version_id: str,
                                by: str, keys: set[str] | None = None) -> dict[str, str]:
    """Create or rebuild the pack-owned report of every available, active starter report (or only ``keys``).

    A rebuild replaces the report's sections, items and widgets in place, so dashboards that include it pick up
    the new version in their draft; live versions keep their frozen content.
    """
    manifest = await _manifest(conn, connection_id)
    if manifest is None:
        return {}
    definition = await _definition(conn, version_id)
    items = await fetch_all(
        conn, "SELECT id, item_key, content_id FROM model.starter_pack_items WHERE connection_id = CAST(:c AS uuid) "
              "AND starter_pack_id = (SELECT m.installed_pack_id FROM model.data_models m WHERE m.connection_id = "
              "CAST(:c AS uuid)) AND item_kind = 'report' AND availability = 'available' AND state = 'active' "
              "ORDER BY item_key",
        {"c": connection_id})
    out: dict[str, str] = {}
    for item in items:
        key = item["item_key"]
        report = manifest.reports.get(key)
        if report is None or (keys is not None and key not in keys):
            continue
        keep, report = shown(report, definition)
        rid = str(item["content_id"] or new_id())
        await conn.execute(
            text("""
                INSERT INTO content.reports (id, tenant_id, owner_id, title, origin, created_by)
                VALUES (CAST(:id AS uuid), :t, NULL, :title, 'starter', :by)
                ON CONFLICT (id) DO UPDATE SET title = EXCLUDED.title, deleted_at = NULL, updated_at = now(),
                  draft_revision = content.reports.draft_revision + 1
            """), {"id": rid, "t": tenant_id, "title": report["title"][:200], "by": by})
        old = [str(r["widget_id"]) for r in await fetch_all(
            conn, "SELECT widget_id FROM content.report_items WHERE report_id = CAST(:r AS uuid) AND widget_id IS NOT "
                  "NULL", {"r": rid})]
        await conn.execute(text("DELETE FROM content.report_items WHERE report_id = CAST(:r AS uuid)"), {"r": rid})
        await conn.execute(text("DELETE FROM content.report_sections WHERE report_id = CAST(:r AS uuid)"), {"r": rid})
        if old:
            await conn.execute(text("UPDATE content.widgets SET deleted_at = now() WHERE id = ANY(CAST(:w AS uuid[])) "
                                    "AND owner_id IS NULL AND dashboard_tab_id IS NULL"), {"w": old})
        widgets = [(i, report["widgets"][i]) for i in keep]
        groups = [("Headline", "headline", [(i, w) for i, w in widgets if w["visual"] == "figure"]),
                  ("Details", "normal", [(i, w) for i, w in widgets if w["visual"] != "figure"])]
        groups = [g for g in groups if g[2]]
        callouts, footnote = report.get("callouts", []), report.get("footnote")
        for position, (title, kind, group) in enumerate(groups):
            sid = new_id()
            await conn.execute(
                text("INSERT INTO content.report_sections (id, tenant_id, report_id, title, kind, position) "
                     "VALUES (:id, :t, CAST(:r AS uuid), :title, :k, :p)"),
                {"id": sid, "t": tenant_id, "r": rid, "title": title, "k": kind, "p": position})
            y = 0
            if position == 0:  # callouts open the report (US-158)
                for c in callouts:
                    await add_text_item(conn, tenant_id, rid, sid, "callout", c["text"], y, title=c.get("title"),
                                        level=c.get("level", "info"))
                    y += 2
            places = catalogue_layout([w["visual"] for _, w in group], y)
            for (_, w), (x, wy, width, height) in zip(group, places, strict=True):
                wid = new_id()
                spec = spec_of(w, model_version_id=version_id, connection_id=connection_id)
                await conn.execute(
                    text("INSERT INTO content.widgets (id, tenant_id, title, spec, visual, model_version_id, "
                         "connection_id, created_by) VALUES (:id, :t, :title, CAST(:s AS jsonb), :v, :mv, :c, :by)"),
                    {"id": wid, "t": tenant_id, "title": w["title"], "s": json.dumps(spec), "v": w["visual"],
                     "mv": version_id, "c": connection_id, "by": by})
                await conn.execute(
                    text("INSERT INTO content.report_items (id, tenant_id, report_id, section_id, item_kind, "
                         "widget_id, grid_x, grid_y, grid_w, grid_h) VALUES (:id, :t, CAST(:r AS uuid), :s, 'widget', "
                         ":w, :x, :y, :ww, :h)"),
                    {"id": new_id(), "t": tenant_id, "r": rid, "s": sid, "w": wid, "x": x, "y": wy, "ww": width,
                     "h": height})
            bottom = max((wy + h for _, wy, _, h in places), default=y)
            if footnote and position == len(groups) - 1:  # the footnote closes it
                await add_text_item(conn, tenant_id, rid, sid, "footnote", footnote, bottom)
        await conn.execute(text("UPDATE model.starter_pack_items SET content_id = CAST(:r AS uuid), updated_at = now() "
                                "WHERE id = :id"), {"r": rid, "id": item["id"]})
        out[key] = rid
    return out


async def add_text_item(conn: AsyncConnection, tenant_id: str, report_id: str, section_id: str, kind: str, body: str,
                        y: int, *, title: str | None = None, level: str | None = None) -> None:
    await conn.execute(
        text("INSERT INTO content.report_items (id, tenant_id, report_id, section_id, item_kind, text_content, "
             "text_title, text_tone, grid_x, grid_y, grid_w, grid_h) VALUES (:id, :t, CAST(:r AS uuid), :s, :k, :txt, "
             ":title, :level, 0, :y, 12, 2)"),
        {"id": new_id(), "t": tenant_id, "r": report_id, "s": section_id, "k": kind, "txt": body[:4000],
         "title": title, "level": level, "y": y})


def tab_texts(report: dict[str, Any]) -> list[dict[str, Any]]:
    """A starter tab's callouts and footnote, as the viewer shows them above and below its widgets (US-158)."""
    return [{"kind": "callout", "title": c.get("title"), "text": c["text"], "level": c.get("level", "info")}
            for c in report.get("callouts", [])] + \
        ([{"kind": "footnote", "text": report["footnote"]}] if report.get("footnote") else [])


async def backfill_starter_reports(conn: AsyncConnection, *, tenant_id: str, by: str) -> int:
    """Build the starter reports a model approved before US-098 never got (no ``content_id`` yet)."""
    rows = await fetch_all(
        conn, "SELECT DISTINCT i.connection_id, m.current_version_id FROM model.starter_pack_items i JOIN "
              "model.data_models m ON m.connection_id = i.connection_id AND m.installed_pack_id = i.starter_pack_id "
              "WHERE i.item_kind = 'report' AND "
              "i.availability = 'available' AND i.state = 'active' AND i.content_id IS NULL AND "
              "m.current_version_id IS NOT NULL")
    built = 0
    for r in rows:
        built += len(await build_starter_reports(conn, tenant_id=tenant_id, connection_id=str(r["connection_id"]),
                                                 version_id=str(r["current_version_id"]), by=by))
    return built


async def refresh_starter_dashboards(conn: AsyncConnection, *, tenant_id: str, connection_id: str,
                                     by: str) -> list[str]:
    """Rebuild the starter dashboards of a connection at its current version (after a delete or a restore)."""
    version_id = await current_version_id(conn, connection_id)
    if version_id is None:
        return []
    return await install_starter_dashboards(conn, tenant_id=tenant_id, connection_id=connection_id,
                                            version_id=version_id, by=by)


async def starter_reports(conn: AsyncConnection, report_ids: list[str]) -> dict[str, bool]:
    """Which of these reports are starter reports: report id → deleted (RULE-017 Deleted badge)."""
    if not report_ids:
        return {}
    rows = await fetch_all(conn, "SELECT id, deleted_at IS NOT NULL AS deleted FROM content.reports WHERE "
                                 "id = ANY(CAST(:r AS uuid[])) AND origin = 'starter'", {"r": report_ids})
    return {str(r["id"]): r["deleted"] for r in rows}


async def hidden_report_keys(conn: AsyncConnection, principal: Principal) -> set[tuple[str, str]]:
    """``(connection_id, report_key)`` of the starter reports an Admin hid from one of the person's roles."""
    rows = await fetch_all(
        conn, "SELECT i.connection_id, i.item_key FROM model.starter_pack_items i JOIN model.data_models m ON "
              "m.connection_id = i.connection_id AND m.installed_pack_id = i.starter_pack_id WHERE i.item_kind = "
              "'report' AND i.hidden_from_role_ids && CAST(:roles AS uuid[])", {"roles": list(principal.role_ids)})
    return {(str(r["connection_id"]), r["item_key"]) for r in rows}


async def install_starter_dashboards(conn: AsyncConnection, *, tenant_id: str, connection_id: str,
                                     version_id: str, by: str) -> list[str]:
    manifest = await _manifest(conn, connection_id)
    if manifest is None:
        return []
    available = {r["item_key"] for r in await fetch_all(
        conn, "SELECT item_key FROM model.starter_pack_items WHERE connection_id = :c AND starter_pack_id = "
              "(SELECT m.installed_pack_id FROM model.data_models m WHERE m.connection_id = :c) AND "
              "item_kind = 'report' "
              "AND availability = 'available' AND state = 'active'", {"c": connection_id})}
    dims = {r["item_key"] for r in await fetch_all(
        conn, "SELECT item_key FROM model.starter_pack_items WHERE connection_id = :c AND starter_pack_id = "
              "(SELECT m.installed_pack_id FROM model.data_models m WHERE m.connection_id = :c) AND item_kind = "
              "'dimension' "
              "AND availability = 'available'", {"c": connection_id})}
    definition = await _definition(conn, version_id)
    created: list[str] = []
    for key, dash in manifest.dashboards.items():
        reports = [r for r in dash["reports"] if r in available]
        if not reports:
            # Every report of this dashboard was deleted: it leaves Home until one is restored.
            await conn.execute(
                text("DELETE FROM content.publications WHERE dashboard_id IN (SELECT id FROM content.dashboards "
                     "WHERE connection_id = CAST(:c AS uuid) AND starter_key = :k)"), {"c": connection_id, "k": key})
            await conn.execute(
                text("UPDATE content.dashboards SET status = 'deleted' WHERE connection_id = CAST(:c AS uuid) AND "
                     "starter_key = :k"), {"c": connection_id, "k": key})
            continue
        filters = [{"ref": f"dimension:{d}"} for d in dash.get("filters", []) if d in dims]
        row = await fetch_one(
            conn,
            """
            INSERT INTO content.dashboards (id, tenant_id, title, kind, connection_id, model_version_id, starter_key,
              shared_filters, status, created_by)
            VALUES (:id, :t, :title, 'starter', :c, :v, :k, CAST(:f AS jsonb), 'published', :by)
            ON CONFLICT (tenant_id, connection_id, starter_key) DO UPDATE SET
              model_version_id = EXCLUDED.model_version_id, title = EXCLUDED.title,
              shared_filters = EXCLUDED.shared_filters, status = 'published',
              version_no = content.dashboards.version_no + 1
            RETURNING id
            """,
            {"id": new_id(), "t": tenant_id, "title": dash["title"], "c": connection_id, "v": version_id, "k": key,
             "f": json.dumps(filters), "by": by},
        )
        assert row is not None
        dashboard_id = str(row["id"])
        await conn.execute(text("DELETE FROM content.dashboard_tabs WHERE dashboard_id = :d"), {"d": dashboard_id})
        await conn.execute(text("DELETE FROM content.publications WHERE dashboard_id = :d"), {"d": dashboard_id})
        for pos, report_key in enumerate(reports):
            tab_id = new_id()
            keep, report = shown(manifest.reports[report_key], definition)
            title = report.get("tab") or report["title"].split("—", 1)[-1].strip()
            await conn.execute(
                text("INSERT INTO content.dashboard_tabs (id, tenant_id, dashboard_id, position, title, report_key, "
                     "texts) VALUES (:id, :t, :d, :p, :title, :k, CAST(:texts AS jsonb))"),
                {"id": tab_id, "t": tenant_id, "d": dashboard_id, "p": pos, "title": title, "k": report_key,
                 "texts": json.dumps(tab_texts(report))})
            places = catalogue_layout([report["widgets"][i]["visual"] for i in keep])
            for i, (x, y, width, height) in zip(keep, places, strict=True):
                w = report["widgets"][i]
                spec = spec_of(w, model_version_id=version_id, connection_id=connection_id)
                await conn.execute(
                    text("""
                        INSERT INTO content.widgets (id, tenant_id, dashboard_tab_id, position, title, spec, visual,
                          placement, model_version_id, connection_id, created_by)
                        VALUES (:id, :t, :tab, :p, :title, CAST(:s AS jsonb), :v, CAST(:pl AS jsonb), :mv, :c, :by)
                    """),
                    {"id": new_id(), "t": tenant_id, "tab": tab_id, "p": i, "title": w["title"],
                     "s": json.dumps(spec), "v": w["visual"],
                     "pl": json.dumps({"x": x, "y": y, "w": width, "h": height}), "mv": version_id,
                     "c": connection_id, "by": by})
        await conn.execute(
            text("INSERT INTO content.publications (id, tenant_id, dashboard_id, all_roles, published_by) "
                 "VALUES (:id, :t, :d, true, :by)"), {"id": new_id(), "t": tenant_id, "d": dashboard_id, "by": by})
        created.append(dashboard_id)
    return created


async def dashboards_including(conn: AsyncConnection, report_id: str) -> list[dict[str, Any]]:
    """User dashboards whose draft includes a starter report, with their owner and live version (if any)."""
    return await fetch_all(
        conn, "SELECT d.id, d.title, d.owner_id, u.display_name AS owner_name, v.version_no AS live_version FROM "
              "content.dashboard_reports dr JOIN content.dashboards d ON d.id = dr.dashboard_id AND d.deleted_at IS "
              "NULL AND d.kind = 'user' JOIN iam.users u ON u.id = d.owner_id LEFT JOIN content.dashboard_versions v "
              "ON v.id = d.live_version_id WHERE dr.report_id = :r ORDER BY d.title", {"r": report_id})


async def drop_from_dashboards(conn: AsyncConnection, report_id: str, dashboard_ids: list[str]) -> None:
    """Take a starter report out of these dashboards' drafts; their live versions keep it (RULE-017)."""
    for dashboard_id in dashboard_ids:
        await conn.execute(text("DELETE FROM content.dashboard_reports WHERE dashboard_id = :d AND report_id = :r"),
                           {"d": dashboard_id, "r": report_id})
        await conn.execute(
            text("UPDATE content.dashboard_reports dr SET position = o.n FROM (SELECT report_id, row_number() "
                 "OVER (ORDER BY position) - 1 AS n FROM content.dashboard_reports WHERE dashboard_id = :d) o "
                 "WHERE dr.dashboard_id = :d AND dr.report_id = o.report_id"), {"d": dashboard_id})
    await touch_dashboards(conn, dashboard_ids)


async def touch_dashboards(conn: AsyncConnection, dashboard_ids: list[str]) -> None:
    """A dashboard's draft changed under its owner (a starter report was rebuilt or removed)."""
    if dashboard_ids:
        await conn.execute(text("UPDATE content.dashboards SET draft_revision = draft_revision + 1, updated_at = "
                                "now() WHERE id = ANY(CAST(:d AS uuid[]))"), {"d": dashboard_ids})
