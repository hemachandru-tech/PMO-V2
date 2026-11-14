"""Excel renderer (US-097; FEAT-045; REQ-107; TEST-054, TEST-062).

One sheet per visible widget — a short header (dashboard, tab, filters, scope, data date, export time) above the
widget's table with its column headers — and a Definitions sheet listing each widget's measure rule, badges, the
filters that did and did not reach it, and the scope. Widgets hidden for the person never reach the payload, so
they have no sheet and no definition row (TEST-062).

A heat grid (US-157) also gets its cross-tab under the table — rows by columns, each cell filled with the grid's
colour (RAG fills for status columns, the ocean ramp otherwise) and always holding its number. A meter and a toned
figure add a line in words ("31 of 111 (27.9%)", "Critical at or above 10").

Values are written as values, never formulas: a text cell starting with ``=``, ``+``, ``-`` or ``@`` stays text
(spreadsheet formula injection). Numbers keep their raw value with a number format, so totals still work.
"""

from __future__ import annotations

import io
import re
from datetime import date, datetime
from typing import Any

from openpyxl import Workbook
from openpyxl.cell.cell import Cell
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

from tela_worker.export import renderer

NUMBER_FORMATS = {"currency": '"$"#,##0', "percent": '0.00"%"', "integer": "#,##0"}
FORMULA_START = ("=", "+", "-", "@", "\t", "\r")
BAD_SHEET_CHARS = re.compile(r"[\[\]:*?/\\]")
HEADER_FILL = PatternFill("solid", fgColor="E8EEF2")
BOLD = Font(bold=True)
MUTED = Font(color="4B5563", size=9)
TITLE = Font(bold=True, size=13)
# The light theme's status-*-soft and seq-1…5 tokens (apps/web/src/design/tokens.json).
TONE_FILLS = {"good": "E4F3E8", "warning": "FCF1DF", "critical": "FBE8F1"}
RAMP = ["D6EBF3", "9CCFE2", "4FA8C7", "287A9A", "1B4F66"]
WHITE = Font(color="FFFFFF")


def set_value(cell: Cell, value: Any) -> None:
    if isinstance(value, str):
        cell.value = value
        if value.startswith(FORMULA_START):
            cell.data_type = "s"  # text, never evaluated
        return
    cell.value = value


def cell_value(value: Any, column: dict[str, Any]) -> Any:
    if value is None:
        return None
    kind = column.get("data_type")
    if kind in ("date", "timestamp", "datetime") and isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return value
        return parsed.date() if kind == "date" else parsed.replace(tzinfo=None)
    if isinstance(value, bool | int | float | date):
        return value
    return str(value)


def number_format(column: dict[str, Any], value: Any) -> str | None:
    if column.get("format") in NUMBER_FORMATS:
        return NUMBER_FORMATS[column["format"]]
    if isinstance(value, float):
        return "#,##0.00"
    if isinstance(value, int) and not isinstance(value, bool):
        return "#,##0"
    if isinstance(value, datetime):
        return "yyyy-mm-dd hh:mm"
    if isinstance(value, date):
        return "yyyy-mm-dd"
    return None


def sheet_title(n: int, title: str, used: set[str]) -> str:
    base = BAD_SHEET_CHARS.sub(" ", f"{n} {title}").strip().strip("'")[:31] or f"Widget {n}"
    name, k = base, 2
    while name.lower() in used:
        suffix = f" ({k})"
        name, k = base[: 31 - len(suffix)] + suffix, k + 1
    used.add(name.lower())
    return name


def _context(ws: Worksheet, payload: dict[str, Any], heading: str, tab: str | None) -> int:
    d = payload["dashboard"]
    ws.cell(row=1, column=1, value=heading).font = TITLE
    lines = [
        f"{d['title']}" + (f" · {tab}" if tab else "") + (f" · Version {d['version']}" if d.get("version") else ""),
        f"Filters: {payload['filters_text']}",
        f"Scope: {payload['scope']} · " + (
            f"Data as of {payload['reporting_date']} · Refreshed {payload['as_of']}" if payload.get("reporting_date")
            else f"Data as of {payload['as_of']}") + f" · Exported {payload['exported_at']}",
    ]
    for i, line in enumerate(lines, start=2):
        c = ws.cell(row=i, column=1)
        set_value(c, line)
        c.font = MUTED
    return len(lines) + 3  # first table row, after a blank line


def _table(ws: Worksheet, start: int, columns: list[dict[str, Any]], rows: list[list[Any]]) -> int:
    for ci, col in enumerate(columns, start=1):
        c = ws.cell(row=start, column=ci)
        set_value(c, col.get("label") or col.get("name"))
        c.font, c.fill = BOLD, HEADER_FILL
        c.alignment = Alignment(horizontal="right" if col.get("kind") == "measure" else "left")
    widths = [len(str(col.get("label") or col.get("name") or "")) for col in columns]
    for ri, row in enumerate(rows, start=start + 1):
        for ci, col in enumerate(columns, start=1):
            v = cell_value(row[ci - 1] if ci - 1 < len(row) else None, col)
            c = ws.cell(row=ri, column=ci)
            set_value(c, v)
            if (fmt := number_format(col, v)) is not None:
                c.number_format = fmt
            widths[ci - 1] = max(widths[ci - 1], min(60, len(str(v)) if v is not None else 0))
    for ci, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(ci)].width = max(10, min(60, w + 2))
    ws.freeze_panes = ws.cell(row=start + 1, column=1)
    if columns:
        ws.auto_filter.ref = f"A{start}:{get_column_letter(len(columns))}{start + max(len(rows), 1)}"
    return start + len(rows) + 1


def widget_sheet(ws: Worksheet, payload: dict[str, Any], tab: str, w: dict[str, Any]) -> None:
    row = _context(ws, payload, w["title"], tab)
    if w.get("error"):
        set_value(ws.cell(row=row, column=1), w["error"])
        ws.column_dimensions["A"].width = 80
        return
    end = _table(ws, row, w.get("columns", []), w.get("rows", []))
    spec = (w.get("viz") or {}).get("chart_spec") or {}
    if spec.get("type") == "heat_grid" and spec.get("y"):
        end = _grid(ws, end + 2, spec, w.get("columns", []), w.get("rows", []))
    figures = spec.get("figures") or {}
    if figures.get("table"):  # US-187: the visual's own figures (steps, cumulative shares, limits, bands, stages)
        end = _figures(ws, end + 2, figures["table"])
    notes = []
    if spec.get("type") == "gantt" and figures.get("unplanned"):
        n = figures["unplanned"]
        notes.append(f"{n} {'row has' if n == 1 else 'rows have'} no start or end date, or end before they start: "
                     "listed above, not drawn on the timeline.")
    if spec.get("type") == "survival" and figures.get("left_out"):  # US-188
        n = figures["left_out"]
        notes.append(f"{n} {'row has' if n == 1 else 'rows have'} no start date, or end before they start: "
                     "left out of the time-to-event curve. Items without an end date count as still open on "
                     f"{figures.get('as_of')}.")
    if (m := spec.get("meter")) and spec.get("type") == "meter":
        pct = f" ({m['percent']}%)" if m.get("percent") is not None else ""
        notes.append(f"{m['note']}: {m['part']:,} of {m['whole']:,}{pct}")
    if (kpi := w.get("kpi")) and kpi.get("tone"):
        notes.append(f"{kpi['tone']['label']}: {kpi['tone']['rule']}")
    if kpi and kpi.get("definition"):
        notes.append(kpi["definition"])
    if kpi and kpi.get("change"):
        ch = kpi["change"]
        pct = f" ({ch['change_pct']:+}%)" if ch.get("change_pct") is not None else ""
        notes.append(f"Change vs {ch['previous_range']}: {ch['change']:+}{pct}")
    if w.get("truncated"):
        notes.extend(w.get("notes") or ["Only the first rows are included."])
    if w.get("filters_not_applied"):
        notes.append("Not affected by " + ", ".join(w["filters_not_applied"]))
    notes.extend(w.get("footnotes", []))
    for i, note in enumerate(notes, start=end + 1):
        c = ws.cell(row=i, column=1)
        set_value(c, note)
        c.font = MUTED


def _figures(ws: Worksheet, start: int, table: dict[str, Any]) -> int:
    """A small titled table below the data: the figures the API worked out for the visual (US-187)."""
    head = ws.cell(row=start, column=1)
    set_value(head, table.get("title") or "Figures")
    head.font = BOLD
    columns, rows = table.get("columns", []), table.get("rows", [])
    for ci, col in enumerate(columns, start=1):
        c = ws.cell(row=start + 1, column=ci)
        set_value(c, col.get("label") or col.get("name"))
        c.font, c.fill = BOLD, HEADER_FILL
    for ri, row in enumerate(rows, start=start + 2):
        for ci, col in enumerate(columns, start=1):
            v = cell_value(row[ci - 1] if ci - 1 < len(row) else None, col)
            c = ws.cell(row=ri, column=ci)
            set_value(c, v)
            if (fmt := number_format(col, v)) is not None:
                c.number_format = fmt
    return start + len(rows) + 2


def _grid(ws: Worksheet, start: int, spec: dict[str, Any], columns: list[dict[str, Any]],
          rows: list[list[Any]]) -> int:
    names = [c["name"] for c in columns]
    if spec["x"] not in names or spec["y"] not in names or not spec.get("series"):
        return start
    xi, yi, mi = names.index(spec["x"]), names.index(spec["y"]), names.index(spec["series"][0])
    xs = list(dict.fromkeys("Not set" if r[xi] is None else str(r[xi]) for r in rows))
    ys = list(dict.fromkeys("Not set" if r[yi] is None else str(r[yi]) for r in rows))
    cells = {("Not set" if r[xi] is None else str(r[xi]), "Not set" if r[yi] is None else str(r[yi])): r[mi]
             for r in rows}
    nums = [v for v in cells.values() if isinstance(v, int | float)]
    lo, hi = (min(nums), max(nums)) if nums else (0, 0)
    head = ws.cell(row=start, column=1)
    set_value(head, f"{columns[xi].get('label')} by {columns[yi].get('label')}")
    head.font, head.fill = BOLD, HEADER_FILL
    for ci, y in enumerate(ys, start=2):
        c = ws.cell(row=start, column=ci)
        set_value(c, y)
        c.font, c.fill = BOLD, HEADER_FILL
    for ri, x in enumerate(xs, start=start + 1):
        set_value(ws.cell(row=ri, column=1), x)
        for ci, y in enumerate(ys, start=2):
            v = cells.get((x, y))
            c = ws.cell(row=ri, column=ci)
            set_value(c, cell_value(v, columns[mi]))
            if (fmt := number_format(columns[mi], v)) is not None:
                c.number_format = fmt
            if not isinstance(v, int | float):
                continue
            if spec.get("ramp") == "rag":
                if (tone := (spec.get("tones") or {}).get(y)) in TONE_FILLS:
                    c.fill = PatternFill("solid", fgColor=TONE_FILLS[tone])
                continue
            step = 0 if hi == lo else min(len(RAMP) - 1, int((v - lo) / (hi - lo) * len(RAMP)))
            c.fill = PatternFill("solid", fgColor=RAMP[step])
            if step >= 3:
                c.font = WHITE
    return start + len(xs) + 1


DEFINITION_COLUMNS = ["Sheet", "Widget", "Tab", "Measure", "Rule", "Rule ref", "Blank values", "Badges",
                      "Filters applied", "Filters not applied", "Rows", "Note"]


def definitions_sheet(ws: Worksheet, payload: dict[str, Any], entries: list[tuple[str, str, dict[str, Any]]]) -> None:
    row = _context(ws, payload, "Definitions", None)
    if payload.get("excluded_widgets"):
        n = payload["excluded_widgets"]
        c = ws.cell(row=row - 1, column=1)
        set_value(c, f"{n} {'widget is' if n == 1 else 'widgets are'} hidden for your role and not included.")
        c.font = MUTED
    rows = []
    for sheet, tab, w in entries:
        d = w.get("definition") or {}
        badges = ", ".join(b.replace("_", " ").capitalize() for b in w.get("badges", []))
        applied = ", ".join(w.get("filters_applied", [])) or None
        missed = ", ".join(w.get("filters_not_applied", [])) or None
        rows.append([sheet, w["title"], tab, d.get("label"), d.get("rule_text"), d.get("rule_ref"),
                     d.get("blank_handling"), badges, applied, missed, w.get("row_count"),
                     w.get("error") or ("Truncated" if w.get("truncated") else None)])
    _table(ws, row, [{"label": c, "kind": "measure" if c == "Rows" else "text"} for c in DEFINITION_COLUMNS], rows)
    for letter in ("E", "G"):
        ws.column_dimensions[letter].width = 60
        for cells in ws[f"{letter}{row + 1}:{letter}{row + len(rows)}"]:
            for c in cells:
                c.alignment = Alignment(wrap_text=True, vertical="top")


@renderer("xlsx")
def render_xlsx(payload: dict[str, Any]) -> bytes:
    wb = Workbook()
    first = wb.active
    assert first is not None
    used: set[str] = {"definitions"}
    entries: list[tuple[str, str, dict[str, Any]]] = []
    n = 0
    for tab in payload["tabs"]:
        # The report's footnotes (population, data limits; US-158) go under every one of its tables.
        footnotes = [*tab.get("footnotes", []), *(i["text"] for s in tab["sections"] for i in s["items"]
                                                  if i["kind"] == "footnote")]
        for s in tab["sections"]:
            for w in s["items"]:
                if w["kind"] != "widget":
                    continue
                w = {**w, "footnotes": footnotes}
                n += 1
                name = sheet_title(n, w.get("export_name") or w["title"], used)  # a detail table's export name
                ws = first if n == 1 else wb.create_sheet()
                ws.title = name
                widget_sheet(ws, payload, tab["title"], w)
                entries.append((name, tab["title"], w))
    defs = first if n == 0 else wb.create_sheet()
    defs.title = "Definitions"
    definitions_sheet(defs, payload, entries)
    wb.properties.title = payload["dashboard"]["title"]
    wb.properties.creator = "Tela"
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
