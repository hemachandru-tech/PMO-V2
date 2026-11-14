"""Freezing and rendering dashboards (FEAT-038, FEAT-040, FEAT-042; DATA-032; RULE-016).

``freeze_dashboard`` captures a dashboard draft as it is now: every report, section, text note and widget
with its spec and model version pin. Publishing stores exactly this as an immutable version; the same
structure renders drafts for owners, share recipients and publishers, so what is checked and previewed
is what goes live.

``render_frozen`` turns frozen content into the viewer's shape for one person: tabs (one per report) or
grouped sections (a starter report's tab carries the starter badge, and Deleted once an Admin deleted it
while a live version still shows it, RULE-017), the locked scope chip per source, honest "data as of", shared
filters the person may see, and widgets marked hidden when they use a field hidden for the person's role or
when the publisher chose "Hide for role".
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncConnection

from tela_api.content.common import is_custom, measure_ref
from tela_api.content.reports import TEXT_KINDS, report_state, text_fields
from tela_api.content.starters import starter_reports
from tela_api.core.db import fetch_all, fetch_one
from tela_api.identity.principal import Principal
from tela_api.identity.scope import resolve_scope
from tela_api.model.store import load_snapshot
from tela_api.query.asof import reporting_date_or_none
from tela_api.query.validator import HIDDEN_MESSAGE, SpecValidator
from tela_common.errors import TelaError
from tela_query_spec.model import ModelSnapshot
from tela_query_spec.spec import parse_spec, ref_key


async def freeze_dashboard(conn: AsyncConnection, dashboard: dict[str, Any]) -> dict[str, Any]:
    rows = await fetch_all(conn, "SELECT dr.report_id, dr.group_title, r.title FROM content.dashboard_reports dr JOIN "
                                 "content.reports r ON r.id = dr.report_id WHERE dr.dashboard_id = :d AND "
                                 "r.deleted_at IS NULL ORDER BY dr.position", {"d": dashboard["id"]})
    reports = []
    for r in rows:
        state = await report_state(conn, r["report_id"])
        sections = []
        for s in state["sections"]:
            items = []
            for i in state["items"]:
                if i["section_id"] != s["id"]:
                    continue
                base = {"kind": i["kind"], "item_id": i["id"], "x": i["x"], "y": i["y"], "w": i["w"], "h": i["h"],
                        "empty_expected": i["empty_expected"], "hidden_for_roles": i["hidden_for_roles"]}
                if i["kind"] in TEXT_KINDS:
                    items.append({**base, **text_fields(i)})
                    continue
                w = state["widgets"][i["widget_id"]]
                items.append({**base, "widget_id": i["widget_id"], "title": w["title"], "visual": w["visual"],
                              "spec": w["spec"], "follow_page_filters": w["follow_page_filters"],
                              "badge": w["badge"],
                              # Only when set, so a dashboard frozen before ENH-023 compares equal (US-221).
                              **({"own_filters": w["own_filters"]} if w.get("own_filters") else {})})
            sections.append({"id": s["id"], "title": s["title"], "kind": s["kind"], "collapsed": s["collapsed"],
                             "items": items})
        reports.append({"id": str(r["report_id"]), "title": state["title"], "group_title": r["group_title"],
                        "page_filters": state["page_filters"], "sections": sections})
    return {"title": dashboard["title"], "layout": dashboard["layout"],
            "shared_filters": dashboard["shared_filters"], "reports": reports}


def widget_items(frozen: dict[str, Any]) -> list[dict[str, Any]]:
    return [i for r in frozen.get("reports", []) for s in r["sections"] for i in s["items"] if i["kind"] == "widget"]


async def snapshots_for(conn: AsyncConnection, tenant_id: str, frozen: dict[str, Any]) -> dict[str, ModelSnapshot]:
    out: dict[str, ModelSnapshot] = {}
    for i in widget_items(frozen):
        v = i["spec"]["model_version_id"]
        if v not in out:
            out[v] = await load_snapshot(conn, tenant_id, v)
    return out


async def render_frozen(conn: AsyncConnection, principal: Principal, d: dict[str, Any], frozen: dict[str, Any], *,
                        version_id: str | None, version_no: int | None, draft: bool = False) -> dict[str, Any]:
    snaps = await snapshots_for(conn, principal.tenant_id, frozen)
    connections = sorted({s.connection_id for s in snaps.values()})
    hidden_by_conn: dict[str, frozenset[str]] = {}
    chips = []
    for cid in connections:
        name = await fetch_one(conn, "SELECT name FROM conn.connections WHERE id = :c", {"c": cid})
        try:
            scope = await resolve_scope(conn, principal, cid)
            hidden_by_conn[cid] = scope.hidden_fields
            text = scope.summary or "All rows"
        except TelaError:
            hidden_by_conn[cid] = frozenset()
            text = "No data scope is set up for your role"
        chips.append(f"{name['name'] if name else 'Source'}: {text}" if len(connections) > 1 else text)
    last = await fetch_one(conn, "SELECT max(created_at) AS at FROM ops.query_executions WHERE connection_id = "
                                 "ANY(CAST(:c AS uuid[])) AND outcome = 'ok' AND NOT cache_hit", {"c": connections})
    roles = set(principal.role_ids)

    def item_out(i: dict[str, Any]) -> dict[str, Any]:
        placement = {"x": i["x"], "y": i["y"], "w": i["w"], "h": i["h"]}
        if i["kind"] in TEXT_KINDS:
            # Frozen callouts carry title and level already (text_fields at freeze time).
            return {"kind": i["kind"], "item_id": i["item_id"], "placement": placement, "text": i.get("text") or "",
                    **({"title": i.get("title"), "level": i.get("level") or "info"} if i["kind"] == "callout" else {})}
        snap = snaps[i["spec"]["model_version_id"]]
        try:
            if is_custom(i["spec"]):
                raise ValueError("custom SQL is checked when it runs")
            hidden = SpecValidator(snap, hidden_fields=hidden_by_conn.get(snap.connection_id, frozenset())).validate(
                parse_spec(i["spec"])[0]).hidden
        except (TelaError, ValueError):
            hidden = False
        by_choice = bool(roles & set(i.get("hidden_for_roles", [])))
        return {"kind": "widget", "id": i["widget_id"], "item_id": i["item_id"], "title": i["title"],
                "visual": i["visual"], "placement": placement, "follow_page_filters": i["follow_page_filters"],
                "hidden": hidden or by_choice, "hidden_message": HIDDEN_MESSAGE if hidden or by_choice else None,
                "badges": [i.get("badge", "certified")], "measure": measure_ref(i["spec"]),
                "connection_id": snap.connection_id, "model_version_id": snap.model_version_id}

    filters = []
    for f in frozen.get("shared_filters", []):
        for snap in snaps.values():
            dim = snap.dimension(ref_key(f["ref"]))
            if dim is not None and f"{dim.entity}.{dim.field}" not in hidden_by_conn.get(snap.connection_id, ()):
                filters.append({"ref": f["ref"], "label": dim.label, "connection_id": snap.connection_id})
                break
    starters = await starter_reports(conn, [r["id"] for r in frozen.get("reports", [])])
    dates = {v: reporting_date_or_none(s.settings, principal.time_zone) for v, s in snaps.items()}
    tabs = []
    for r in frozen.get("reports", []):
        versions = {i["spec"]["model_version_id"] for s in r["sections"] for i in s["items"] if i["kind"] == "widget"}
        badges = (["starter", "deleted"] if starters[r["id"]] else ["starter"]) if r["id"] in starters else []
        sections = [{"id": s["id"], "title": s["title"], "kind": s["kind"], "collapsed": s["collapsed"],
                     "items": [item_out(i) for i in s["items"]]} for s in r["sections"]]
        tab_dates = sorted({rd for v in versions if (rd := dates.get(v)) is not None})
        tabs.append({"id": r["id"], "title": r["title"], "group_title": r.get("group_title"), "badges": badges,
                     "sections": sections, "reporting_date": tab_dates[0] if len(tab_dates) == 1 else None,
                     "widgets": [i for s in sections for i in s["items"] if i["kind"] == "widget"]})
    return {
        "id": str(d["id"]), "title": frozen.get("title") or d["title"], "kind": "user",
        "layout": frozen.get("layout", "tabs"), "version": version_no, "version_id": version_id, "draft": draft,
        "connection_id": connections[0] if len(connections) == 1 else None, "connection_ids": connections,
        "model_version_id": None, "locked_scope_chip": " · ".join(chips) or "All rows",
        "scope_message": "This is your data scope from your Microsoft groups. Filters can narrow it but not widen it.",
        "as_of": last["at"] if last else None, "filters": filters, "tabs": tabs,
        "reporting_date": all_dates[0] if len(all_dates := sorted(set(filter(None, dates.values())))) == 1 else None,
    }
