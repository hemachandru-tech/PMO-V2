"""Business-context spreadsheet (ENH-028, US-251; FEAT-078 / FEAT-014; no database).

The template and its fixed header, reading rows (values split, operators as written), a wrong header or a file that
isn't a workbook, a row read as a definition on the model (table, field, operator, values checked; the closest
stored value suggested; text only with the reason when it fails), every sheet operator as a runnable condition, and
the worker's one chunk per term. Upload, review, approval into a named condition and the injection gate run live in
``test_business_sheet_api``.
"""

from __future__ import annotations

import io
from typing import Any

import pytest
from openpyxl import Workbook, load_workbook
from test_planner_gold_unit import RECORD_SNAP as SNAP

from tela_api.knowledge import conditions as C
from tela_api.knowledge import definitions as D
from tela_common import business_sheet as B
from tela_worker.knowledge import extract_chunks

HEADER = list(B.HEADER)
KNOWN = {"projects.current_state": {"project created": "Project Created", "active": "Active", "on hold": "On Hold",
                                    "completed": "Completed"}}


def sheet(*rows: list[Any], header: list[str] | None = None, sheets: int = 1) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.append(header or HEADER)
    for r in rows:
        ws.append(r)
    for n in range(1, sheets):
        wb.create_sheet(f"Other {n}").append(["ignored"])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def row(term: str, table: str = "", field: str = "", op: str = "", values: Any = "", meaning: str = "") -> dict[str, Any]:
    return {"term": term, "meaning": meaning, "table": table, "field": field, "operator": op,
            "values": B.split_values(values) if isinstance(values, str) else values, "notes": ""}


def test_template_is_one_sheet_with_the_fixed_header() -> None:
    wb = load_workbook(io.BytesIO(B.template()))
    assert len(wb.worksheets) == 1 and wb.active.freeze_panes == "A2"
    assert [c.value for c in wb.active[1]] == HEADER
    assert wb.active["E1"].comment is not None and "between" in wb.active["E1"].comment.text
    with pytest.raises(B.SheetError) as err:
        B.read(B.template())
    assert err.value.code == "no_text"  # the header alone is not a sheet of terms


def test_rows_are_read_as_written() -> None:
    rows = B.read(sheet(
        ["Open project", "Projects being worked on", "Projects", "Current state", "in",
         "Project Created; Active; On Hold", "From the PMO handbook"],
        [None] * 7,  # a blank row is skipped
        ["Late go-live", None, "projects", "planned_deployment_date", "Before today", None, None],
        ["Budget band", None, "Projects", "Approved budget", "between", "1000\n5000", None]))
    assert [r.row for r in rows] == [2, 4, 5]
    first = rows[0]
    assert (first.term, first.table, first.field, first.operator) == ("Open project", "Projects", "Current state", "in")
    assert first.values == ["Project Created", "Active", "On Hold"] and first.notes == "From the PMO handbook"
    assert first.text() == ("Open project: Projects being worked on\nRule: Projects Current state in Project Created; "
                            "Active; On Hold\nNotes: From the PMO handbook")
    assert rows[1].operator == "before_today" and rows[1].values == []
    assert rows[2].values == ["1000", "5000"]
    assert B.split_values("New, Active") == ["New", "Active"]  # commas when there is no semicolon


def test_a_wrong_header_or_a_non_workbook_is_refused() -> None:
    with pytest.raises(B.SheetError) as err:
        B.read(sheet(["Open project"], header=["Term", "Definition"]))
    assert err.value.code == "bad_template" and "term, meaning, table, field, operator, values, notes" in str(err.value)
    with pytest.raises(B.SheetError) as err:
        B.read(b"PK\x03\x04not really a zip")
    assert err.value.code == "unreadable_document"
    with pytest.raises(B.SheetError) as err:
        B.read(b"%PDF-1.7")
    assert err.value.code == "unreadable_document"
    # Header names are matched loosely on case and spaces; extra sheets are not read.
    assert B.read(sheet(["Open project"], header=[h.upper() for h in HEADER], sheets=2))[0].term == "Open project"


def test_a_row_becomes_a_definition_on_the_model() -> None:
    d = D.interpret_row(row("Open projects", "Projects", "Current state", "=", "project created; Active; on hold"),
                        SNAP, KNOWN)
    assert d is not None and d.expressible and d.measure_ref == "measure:open_projects"
    assert d.rule == {"entity": "projects", "field": "current_state", "op": "in",
                      "values": ["Project Created", "Active", "On Hold"]}  # stored spellings
    assert d.meaning == "Projects where Current state is one of Project Created, Active, On Hold"  # none given

    late = D.interpret_row(row("Late go-live", "projects", "Planned deployment date", "before_today"), SNAP, KNOWN)
    assert late is not None and late.expressible and late.rule == {
        "entity": "projects", "field": "planned_deployment_date", "op": "before_today", "values": []}

    closed = D.interpret_row(row("Closed project", "Projects", "Current state", "≠", "Active"), SNAP, KNOWN)
    assert closed is not None and closed.rule is not None and closed.rule["op"] == "not_in"


@pytest.mark.parametrize(("cells", "gap"), [
    (row("Gated project", "Projects", "Stage gate", "in", "G3"), "The model has no field “Stage gate” on Projects."),
    (row("Widget", "Widgets", "Colour", "in", "Red"), "The model has no table “Widgets”."),
    (row("Open project", "Projects", "Current state", "in", "Actve"),
     "Current state has no value “Actve” (closest: “Active”)."),
    (row("Open project", "Projects", "Current state", "roughly", "Active"), "“roughly” is not an operator Tela knows."),
    (row("Late", "Projects", "Current state", "before_today"),
     "Current state is not a date field; “before today” needs one."),
    (row("Big", "Projects", "Approved budget (USD)", "between", "1000"), "“between” takes two values."),
    (row("Big", "Projects", "Approved budget", ">", "lots"), "“lots” is not a number for Approved budget (USD)."),
    (row("Go-live", meaning="The deployment of a project to production."), "The sheet gives no rule for this term."),
])
def test_a_row_that_fails_stays_as_text_with_the_reason(cells: dict[str, Any], gap: str) -> None:
    d = D.interpret_row(cells, SNAP, KNOWN)
    assert d is not None and not d.expressible and gap in d.gaps


def test_every_sheet_operator_is_a_runnable_condition() -> None:
    rules = [{"entity": "projects", "field": "current_state", "op": "in", "values": ["Active"]},
             {"entity": "projects", "field": "current_state", "op": "not_in", "values": ["Active"]},
             {"entity": "projects", "field": "actual_deployment_date", "op": "blank", "values": []},
             {"entity": "projects", "field": "actual_deployment_date", "op": "not_blank", "values": []},
             {"entity": "projects", "field": "approved_budget_value_usd", "op": ">", "values": ["1000"]},
             {"entity": "projects", "field": "approved_budget_value_usd", "op": "between", "values": ["1000", "5000.5"]},
             {"entity": "projects", "field": "planned_deployment_date", "op": "<", "values": ["2026-01-01"]},
             {"entity": "projects", "field": "planned_deployment_date", "op": "before_today", "values": []},
             {"entity": "projects", "field": "planned_deployment_date", "op": "after_today", "values": []}]
    for r in rules:
        assert D.check_rule(r, SNAP) == [], r
        cond = C.from_rule(r)
        assert C.parse(cond) is not None and C.gaps(cond, "projects", SNAP) == [], r
        assert D.rule_words(r, SNAP)
    assert C.from_rule(rules[5]) == {"all": [{"field": "approved_budget_value_usd", "op": ">=", "value": 1000},
                                             {"field": "approved_budget_value_usd", "op": "<=", "value": 5000.5}]}
    assert C.from_rule(rules[7]) == {"field": "planned_deployment_date", "op": "<",
                                     "value": {"today": True, "offset_days": 0}}
    assert D.rule_words(rules[7], SNAP) == "Projects where Planned deployment date is before today"


def test_the_worker_reads_one_chunk_per_term() -> None:
    data = sheet(["Open project", "Being worked on", "Projects", "Current state", "in", "Active; On Hold", None],
                 ["Go-live", "Deployment to production", None, None, None, None, None])
    extractor, chunks = extract_chunks(data, B.CONTENT_TYPE)
    assert extractor == "openpyxl" and len(chunks) == 2
    assert chunks[0]["content"].startswith("Open project: Being worked on")
    assert chunks[0]["metadata"]["row"] == 2 and chunks[0]["metadata"]["sheet"]["values"] == ["Active", "On Hold"]
    from tela_worker.knowledge import ExtractionError
    with pytest.raises(ExtractionError) as err:
        extract_chunks(sheet(["x"], header=["nope"]), B.CONTENT_TYPE)
    assert err.value.code == "bad_template"
