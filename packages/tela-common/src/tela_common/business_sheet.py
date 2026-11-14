"""Business-context spreadsheet (ENH-028, US-251; FEAT-078 / FEAT-014).

An Admin uploads one ``.xlsx`` per source saying what its business terms mean, one term per row, under a fixed header
row: ``term | meaning | table | field | operator | values | notes``. The API checks the header on upload and serves
the template; the worker reads the rows into one chunk each (so they are scanned, embedded and reviewed like any
document) with the row's cells in the chunk's metadata; the API proposes each row as a definition (US-203 queue).

``openpyxl`` is imported only here and only when a sheet is read or written (the ``sheet`` extra).
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from typing import Any

CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
MAGIC = b"PK\x03\x04"
HEADER = ("term", "meaning", "table", "field", "operator", "values", "notes")
OPERATORS = ("=", "≠", "in", "not_in", "blank", "not_blank", "<", ">", "between", "before_today", "after_today")
MAX_ROWS = 2000
MAX_CELL = 600
HELP = {
    "term": "The business term, as people say it: Open project, Late go-live.",
    "meaning": "What it means, in words (shown to Ask and to reviewers).",
    "table": "The table it is about (its name in Tela or in the source), e.g. Projects.",
    "field": "The field the rule reads (its name in Tela or the source column), e.g. Current state.",
    "operator": "One of: " + ", ".join(OPERATORS) + ". Leave blank to keep the term as text only.",
    "values": "The values, separated by ; (between: two values; blank, not_blank, before_today, after_today: none).",
    "notes": "Anything else for the reviewer. Not used by Ask.",
}


class SheetError(ValueError):
    """The file is not a business-context sheet: ``code`` says why."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class SheetRow:
    row: int  # the spreadsheet row number (2 = the first under the header)
    term: str
    meaning: str
    table: str
    field: str
    operator: str
    values: list[str]
    notes: str

    def cells(self) -> dict[str, Any]:
        return {"row": self.row, "term": self.term, "meaning": self.meaning, "table": self.table,
                "field": self.field, "operator": self.operator, "values": self.values, "notes": self.notes}

    def text(self) -> str:
        """The row as one passage: what reviewers read and search finds."""
        rule = " ".join(p for p in (self.table, self.field, self.operator, "; ".join(self.values)) if p)
        out = f"{self.term}: {self.meaning}" if self.meaning else self.term
        if rule:
            out += f"\nRule: {rule}"
        if self.notes:
            out += f"\nNotes: {self.notes}"
        return out


def _cell(v: Any, *, lines: bool = False) -> str:
    if v is None:
        return ""
    if lines and isinstance(v, str):  # values may be one per line
        return "\n".join(" ".join(ln.split()) for ln in v.replace("\r", "\n").split("\n"))[:MAX_CELL]
    if hasattr(v, "isoformat"):
        return str(v.isoformat())[:10] if getattr(v, "hour", 0) == 0 and getattr(v, "minute", 0) == 0 else \
            str(v.isoformat())
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return " ".join(str(v).split())[:MAX_CELL]


def split_values(raw: str) -> list[str]:
    """"Project Created; Active; On Hold" (or one per line, or by commas when there is no semicolon)."""
    raw = raw.replace("\r", "\n")
    sep = ";" if ";" in raw else "\n" if "\n" in raw else ","
    return list(dict.fromkeys(v.strip().strip("\"'") for v in raw.split(sep) if v.strip()))


def read(data: bytes) -> list[SheetRow]:
    """The rows of a business-context sheet (its first sheet), header checked. Raises ``SheetError``."""
    import zipfile

    from openpyxl import load_workbook
    from openpyxl.utils.exceptions import InvalidFileException

    if not data.startswith(MAGIC):
        raise SheetError("unreadable_document", "The file isn't an Excel workbook (.xlsx).")
    try:
        wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except (InvalidFileException, zipfile.BadZipFile, KeyError, ValueError, OSError) as exc:
        raise SheetError("unreadable_document", "The workbook couldn't be read. Check it opens in Excel.") from exc
    try:
        ws = wb.worksheets[0]
        rows = ws.iter_rows(values_only=True)
        head = next(rows, None)
        got = tuple(_cell(c).lower().replace(" ", "_") for c in (head or ())[:len(HEADER)])
        if got != HEADER:
            raise SheetError("bad_template", "The first row must be the template's header: "
                                             + ", ".join(HEADER) + ". Download the template and keep its header.")
        out: list[SheetRow] = []
        for n, values in enumerate(rows, start=2):
            cells = [_cell(c, lines=i == 5) for i, c in enumerate((values or ())[:len(HEADER)])] + [""] * len(HEADER)
            term, meaning, table, field, op, vals, notes = cells[:len(HEADER)]
            if not any((term, meaning, table, field, op, vals, notes)):
                continue
            if len(out) >= MAX_ROWS:
                raise SheetError("too_many_chunks", f"The sheet has more than {MAX_ROWS} terms. Split it.")
            out.append(SheetRow(row=n, term=term[:80], meaning=meaning, table=table, field=field,
                                operator=op.strip().lower().replace(" ", "_"), values=split_values(vals),
                                notes=notes))
        if not out:
            raise SheetError("no_text", "The sheet has no terms under its header.")
        return out
    finally:
        wb.close()


def template() -> bytes:
    """The empty template: one sheet, the header row (frozen, with a note on each column), an operator list."""
    from openpyxl import Workbook
    from openpyxl.comments import Comment
    from openpyxl.styles import Font
    from openpyxl.worksheet.datavalidation import DataValidation

    wb = Workbook()
    ws = wb.active
    ws.title = "Business context"
    ws.append(list(HEADER))
    for i, name in enumerate(HEADER, start=1):
        cell = ws.cell(row=1, column=i)
        cell.font = Font(bold=True)
        cell.comment = Comment(HELP[name], "Tela")
        ws.column_dimensions[cell.column_letter].width = 40 if name in ("meaning", "values", "notes") else 20
    ws.freeze_panes = "A2"
    dv = DataValidation(type="list", formula1='"' + ",".join(OPERATORS) + '"', allow_blank=True)
    dv.error, dv.errorTitle = "Pick an operator from the list.", "Operator"
    ws.add_data_validation(dv)
    dv.add("E2:E2001")
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
