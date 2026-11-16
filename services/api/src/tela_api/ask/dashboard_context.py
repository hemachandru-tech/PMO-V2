"""What a question asked on a dashboard is about (Direct SQL mode): the dashboard, its widgets, the filters the reader
has set and the widget asked about, as a short note for the SQL writer.

Without it a question in a dashboard's chat panel ("why is this so high?", "which of these are late?") was answered as
if no dashboard were open. The note names each widget by what it shows, written from the model (measure, group-bys,
filters) or, for a Custom SQL widget, its SQL, so a follow-up reads the same population the reader is looking at. It
holds no rows and nothing outside the dashboard; values in filters are the reader's own choices.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncConnection

from tela_api.core.db import fetch_all, fetch_one
from tela_query_spec.model import ModelSnapshot
from tela_query_spec.spec import ref_key

MAX_WIDGETS = 40
SQL_CHARS = 900


def _label(ref: str, snap: ModelSnapshot | None) -> str:
    kind, key = ref.split(":", 1) if ":" in ref else ("", ref)
    if snap is None:
        return key
    if kind == "measure" and (m := snap.measure(key)):
        return m.label
    if kind == "dimension" and (d := snap.dimension(key)):
        return d.label
    if kind == "field" and "." in key:
        ent, fld = key.split(".", 1)
        e = snap.entity(ent)
        f = e.field(fld) if e else None
        return f"{e.label} {f.label}" if e and f else key
    return key


def describe_spec(spec: dict[str, Any], snap: ModelSnapshot | None) -> str:
    """One widget in words from its spec: measure (with its rule), group-bys, trend, filters, top N."""
    if not spec.get("measure"):
        return ""
    m = snap.measure(ref_key(spec["measure"]["ref"])) if snap else None
    parts = [_label(spec["measure"]["ref"], snap) + (f" ({m.plain_rule})" if m and m.plain_rule else "")]
    if spec.get("extra_measures"):
        parts.append("with " + ", ".join(_label(x["ref"], snap) for x in spec["extra_measures"]))
    if spec.get("breakdowns"):
        parts.append("by " + ", ".join(_label(b["ref"], snap) for b in spec["breakdowns"]))
    if spec.get("time"):
        parts.append(f"over {_label(spec['time']['ref'], snap)} per {spec['time'].get('grain') or 'period'}")
    for f in spec.get("filters") or []:
        parts.append(f"where {_label(f['ref'], snap)} {f['op']} {', '.join(map(str, f.get('values') or []))}".strip())
    if spec.get("limit"):
        parts.append(f"top {spec['limit']}")
    return "; ".join(parts)


async def dashboard_note(conn: AsyncConnection, context: dict[str, Any], snap: ModelSnapshot | None,
                         connection_id: str) -> tuple[str | None, dict[str, Any] | None]:
    """(note for the SQL writer, summary for the answer's "context used") for a question asked on a dashboard, or
    (None, None) when the question isn't. Only widgets on ``connection_id`` are described."""
    did = context.get("dashboard_id")
    if not did:
        return None, None
    d = await fetch_one(conn, "SELECT id, title FROM content.dashboards WHERE id = CAST(:d AS uuid) AND "
                              "deleted_at IS NULL", {"d": str(did)})
    if d is None:
        return None, None
    rows = await fetch_all(
        conn,
        "SELECT r.title AS page, w.id, w.title, w.spec, w.source_kind, c.sql_text FROM content.dashboard_reports dr "
        "JOIN content.reports r ON r.id = dr.report_id JOIN content.report_items i ON i.report_id = r.id "
        "JOIN content.widgets w ON w.id = i.widget_id LEFT JOIN content.custom_sql_definitions c "
        "ON c.id = w.custom_sql_id WHERE dr.dashboard_id = :d AND w.deleted_at IS NULL "
        "AND w.connection_id = CAST(:c AS uuid) ORDER BY dr.position, i.grid_y, i.grid_x",
        {"d": d["id"], "c": connection_id})
    about = str(context.get("widget_id") or "")
    lines = [f"The user is looking at the dashboard “{d['title']}”. Its widgets on this database:"]
    focus = None
    for r in rows[:MAX_WIDGETS]:
        what = (f"SQL: {(r['sql_text'] or '')[:SQL_CHARS]}" if r["source_kind"] == "custom_sql"
                else describe_spec(r["spec"] or {}, snap))
        line = f"- [{r['page']}] {r['title']}: {what}"
        if str(r["id"]) == about:
            focus = f"The question is about the widget “{r['title']}” ({what}). Answer for exactly what it shows."
        lines.append(line)
    filters = context.get("page_filters") or []
    if filters:
        lines.append("Filters the user set on the dashboard (apply them to the answer when the columns are reachable): "
                     + "; ".join(f"{_label(f['ref'], snap)} {f.get('op', '=')} "
                                 f"{', '.join(map(str, f.get('values') or []))}" for f in filters))
    if focus:
        lines.append(focus)
    lines.append("If the question refers to “this”, “these”, a chart or a figure, read it as one of these widgets "
                 "and keep its population and filters.")
    summary = {"dashboard": d["title"], "widgets": len(rows), "page_filters": len(filters),
               "about_widget": next((r["title"] for r in rows if str(r["id"]) == about), None)}
    return "\n".join(lines), summary
