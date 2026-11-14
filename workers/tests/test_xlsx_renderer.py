"""Excel renderer (US-097; TEST-054): a sheet per visible widget plus Definitions, filters and date in the file."""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

from openpyxl import load_workbook

from tela_worker.export import xlsx

PAYLOAD = json.loads((Path(__file__).parent / "fixtures" / "export_payload.json").read_text())


def workbook(payload: dict[str, Any] = PAYLOAD) -> Any:
    return load_workbook(io.BytesIO(xlsx.render_xlsx(payload)))


def test_one_sheet_per_widget_plus_definitions() -> None:
    wb = workbook()
    assert wb.sheetnames == ["1 Open projects", "2 Approved budget", "3 Open projects by region", "4 Late projects",
                             "5 Broken widget", "Definitions"]
    ws = wb["3 Open projects by region"]
    assert ws["A1"].value == "Open projects by region"
    assert ws["A2"].value == "PMO — Projects · Overview · Version 3"
    assert ws["A3"].value == "Filters: Region = KSA"
    assert ws["A4"].value == "Scope: projects: Own CIO · Data as of 2026-10-21 06:00 · Exported 2026-10-21 14:05 +03"
    assert [c.value for c in ws[6]] == ["Region", "Open projects"]
    assert [[c.value for c in r] for r in ws.iter_rows(min_row=7, max_row=10)] == [
        ["KSA", 18], ["GCC - UAE", 12], ["Egypt", 7], [None, 5]]
    assert ws["B7"].number_format == "#,##0" and ws.freeze_panes == "A7"
    # Every row of a long table, not the 40 the PDF prints.
    assert wb["4 Late projects"].max_row == 6 + 60


def test_figures_keep_values_and_formats() -> None:
    wb = workbook()
    budget = wb["2 Approved budget"]
    assert budget["A7"].value == 12500000.5 and budget["A7"].number_format == '"$"#,##0'
    kpi = wb["1 Open projects"]
    assert kpi["A7"].value == 42
    assert kpi["A9"].value == "Change vs Q2 2026: +3 (+7.7%)"
    assert wb["5 Broken widget"]["A6"].value == "The data source took too long to answer."


def test_definitions_sheet() -> None:
    ws = workbook()["Definitions"]
    assert ws["A3"].value == "Filters: Region = KSA"
    assert ws["A5"].value == "1 widget is hidden for your role and not included."
    header = [c.value for c in ws[6]]
    assert header == xlsx.DEFINITION_COLUMNS
    rows = [[c.value for c in r] for r in ws.iter_rows(min_row=7)]
    assert [r[1] for r in rows] == ["Open projects", "Approved budget", "Open projects by region", "Late projects",
                                    "Broken widget"]
    first = dict(zip(header, rows[0], strict=True))
    assert first["Rule"] == "Projects whose state is not Closed or Cancelled." and first["Badges"] == "Certified"
    assert first["Filters applied"] == "Region" and first["Sheet"] == "1 Open projects"
    assert rows[4][-1] == "The data source took too long to answer."


def test_text_that_looks_like_a_formula_stays_text() -> None:
    evil = json.loads(json.dumps(PAYLOAD))
    w = evil["tabs"][1]["sections"][0]["items"][0]
    w["rows"] = [['=HYPERLINK("http://x","click")', "+cmd", 1], ["-2+3", "@SUM(A1)", 2]]
    w["title"] = "Late: projects [all]?"
    wb = workbook(evil)
    ws = wb["4 Late  projects  all"]
    for cell in (ws["A7"], ws["B7"], ws["A8"], ws["B8"]):
        assert cell.data_type == "s", cell.value
    assert ws["A7"].value.startswith("=HYPERLINK")


def test_sheet_names_are_unique_and_short() -> None:
    used: set[str] = set()
    long = "A very long widget title that goes on and on"
    names = [xlsx.sheet_title(1, long, used), xlsx.sheet_title(1, long, used)]
    assert all(len(n) <= 31 for n in names) and names[0] != names[1] and names[1].endswith("(2)")


def _with(widget: dict[str, Any]) -> dict[str, Any]:
    tab = {"title": "Governance", "sections": [{"title": None, "kind": "body", "items": [widget]}]}
    return {**PAYLOAD, "tabs": [tab]}


def grid_row(ws: Any) -> int:
    return next(r for r in range(6, ws.max_row + 1) if str(ws[f"A{r}"].value or "").startswith("Region by "))


STATUS_COLS = [{"name": "region", "kind": "dimension", "label": "Region"},
               {"name": "reported_status", "kind": "dimension", "label": "Reported status"},
               {"name": "open_projects", "kind": "measure", "label": "Open projects", "format": "integer"}]


def test_heat_grid_cross_tab_with_rag_fills() -> None:
    rows = [["KSA", "Green", 9], ["KSA", "Red", 2], ["Egypt", "Green", 4], ["Egypt", None, 1]]
    w = {"kind": "widget", "id": "h1", "title": "Region by status", "visual": "heat_grid", "columns": STATUS_COLS,
         "rows": rows, "row_count": 4, "badges": [],
         "viz": {"visual": "heat_grid", "chart_spec": {"type": "heat_grid", "x": "region", "y": "reported_status",
                                                       "series": ["open_projects"], "ramp": "rag",
                                                       "tones": {"Green": "good", "Red": "critical"}}}}
    ws = workbook(_with(w))["1 Region by status"]
    top = grid_row(ws)  # two rows under the long table (header row 6, four rows)
    assert top == 13
    grid = [[c.value for c in r] for r in ws.iter_rows(min_row=top, max_row=top + 2, max_col=4)]
    assert grid == [["Region by Reported status", "Green", "Red", "Not set"], ["KSA", 9, 2, None],
                    ["Egypt", 4, None, 1]]
    assert ws["B14"].fill.fgColor.rgb == "00E4F3E8" and ws["C14"].fill.fgColor.rgb == "00FBE8F1"
    assert ws["D15"].fill.fill_type is None  # Not set: no colour, the number says it


def test_heat_grid_ramp_and_meter_and_tone_lines() -> None:
    cols = [{**STATUS_COLS[0]}, {"name": "priority", "kind": "dimension", "label": "Priority"}, STATUS_COLS[2]]
    rows = [["KSA", "P1", 1], ["KSA", "P2", 10], ["Egypt", "P1", 5]]
    w = {"kind": "widget", "id": "h2", "title": "Region by priority", "visual": "heat_grid", "columns": cols,
         "rows": rows, "row_count": 3, "badges": [],
         "viz": {"visual": "heat_grid", "chart_spec": {"type": "heat_grid", "x": "region", "y": "priority",
                                                       "series": ["open_projects"], "ramp": "sequential"}}}
    ws = workbook(_with(w))["1 Region by priority"]
    ksa = grid_row(ws) + 1
    assert ws[f"B{ksa}"].fill.fgColor.rgb == "00D6EBF3" and ws[f"C{ksa}"].fill.fgColor.rgb == "001B4F66"
    assert ws[f"C{ksa}"].font.color.rgb == "00FFFFFF"
    meter = {"kind": "widget", "id": "m1", "title": "Budget code", "visual": "meter", "badges": [], "row_count": 1,
             "columns": [{"name": "a", "kind": "measure", "label": "With budget code", "format": "integer"},
                         {"name": "b", "kind": "measure", "label": "Open projects", "format": "integer"}],
             "rows": [[31, 111]],
             "viz": {"visual": "meter", "chart_spec": {"type": "meter", "meter": {
                 "part": 31, "whole": 111, "percent": 27.9, "note": "Open projects with a budget code"}}}}
    assert workbook(_with(meter))["1 Budget code"]["A9"].value == "Open projects with a budget code: 31 of 111 (27.9%)"
    figure = {**PAYLOAD["tabs"][0]["sections"][0]["items"][0],
              "kpi": {"value": 12, "format": "integer", "change": None, "sparkline": None,
                      "tone": {"tone": "critical", "label": "Critical", "rule": "Critical at or above 10"},
                      "definition": "Projects not yet closed."}}
    ws = workbook(_with(figure))["1 Open projects"]
    assert [ws["A9"].value, ws["A10"].value] == ["Critical: Critical at or above 10", "Projects not yet closed."]


def test_reporting_date_export_name_and_footnotes() -> None:
    """US-158: "Data as of" the reporting date (with the refresh time), a detail table's sheet takes its export name,
    and the report's footnotes sit under each of its tables."""
    payload = json.loads(json.dumps(PAYLOAD))
    payload["reporting_date"] = "30 Sep 2026"
    tab = payload["tabs"][0]
    tab["footnotes"] = ["Open projects = Project Created, Active and On Hold."]
    late = next(w for t in payload["tabs"] for s in t["sections"] for w in s["items"] if w.get("title") == "Late projects")
    late["export_name"] = "Overdue projects"
    wb = workbook(payload)
    assert "4 Overdue projects" in wb.sheetnames
    ws = wb["1 Open projects"]
    assert ws["A4"].value == ("Scope: projects: Own CIO · Data as of 30 Sep 2026 · Refreshed 2026-10-21 06:00 · "
                              "Exported 2026-10-21 14:05 +03")
    assert "Open projects = Project Created, Active and On Hold." in [c.value for c in ws["A"]]


def test_visual_figures_table_and_gantt_note() -> None:
    """US-187: the figures the API worked out for a visual go below the data, formatted like it."""
    cols = [{"name": "region", "kind": "dimension", "label": "Region"},
            {"name": "open_projects", "kind": "measure", "label": "Open projects", "format": "integer"}]
    table = {"title": "Pareto", "columns": [{"name": "item", "label": "Region", "kind": "dimension", "format": None},
                                            {"name": "value", "label": "Open projects", "kind": "measure",
                                             "format": "integer"},
                                            {"name": "cumulative", "label": "Cumulative share", "kind": "measure",
                                             "format": "percent"}],
             "rows": [["South", 50, 62.5], ["North", 30, 100]]}
    w = {"kind": "widget", "id": "p1", "title": "Open by region", "visual": "pareto", "columns": cols,
         "rows": [["North", 30], ["South", 50]], "row_count": 2, "badges": [],
         "viz": {"visual": "pareto", "chart_spec": {"type": "pareto", "x": "region", "series": ["open_projects"],
                                                    "figures": {"table": table}}}}
    ws = workbook(_with(w))["1 Open by region"]
    values = [[c.value for c in r] for r in ws.iter_rows(min_row=1, max_col=3)]
    at = values.index(["Pareto", None, None]) + 1
    assert values[at] == ["Region", "Open projects", "Cumulative share"]
    assert values[at + 1] == ["South", 50, 62.5] and ws.cell(row=at + 3, column=3).number_format == \
        xlsx.NUMBER_FORMATS["percent"]
    gantt = {**w, "visual": "gantt", "viz": {"visual": "gantt", "chart_spec": {
        "type": "gantt", "x": "region", "figures": {"start": "s", "end": "e", "unplanned": 2}}}}
    ws = workbook(_with(gantt))["1 Open by region"]
    assert any(str(c.value).startswith("2 rows have no start or end date") for c in ws["A"])


def test_survival_note_for_items_left_out() -> None:
    """US-188: items the time-to-event curve leaves out are counted in a note, with the day open items count to."""
    cols = [{"name": "project", "kind": "dimension", "label": "Project"}]
    w = {"kind": "widget", "id": "s1", "title": "Time to go-live", "visual": "survival", "columns": cols,
         "rows": [["A"]], "row_count": 1, "badges": [],
         "viz": {"visual": "survival", "chart_spec": {"type": "survival", "x": "project", "figures": {
             "left_out": 1, "as_of": "2026-10-29", "table": {"title": "Time to event", "columns": [], "rows": []}}}}}
    ws = workbook(_with(w))["1 Time to go-live"]
    assert any(str(c.value).startswith("1 row has no start date") and "2026-10-29" in str(c.value) for c in ws["A"])
