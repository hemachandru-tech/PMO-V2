"""Excel and CSV through DuckDB (US-108, US-109; TEST-013, TEST-085; RULE-001, RULE-006).

Profiling (sheets, header rows, types, merged cells, mixed-type and formula-only columns), the Admin's
settings overriding inference, and the execution guarantees: read-only database, no external access,
interrupt at the timeout, row and payload caps, checksum verification.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from sources import MemoryStore, file_info, tpg_csv, tpg_workbook

from tela_common.errors import ErrorCode, TelaError
from tela_runner.adapters.base import Limits
from tela_runner.adapters.files import FileAdapter, identifier


@pytest.fixture
def store() -> MemoryStore:
    return MemoryStore()


@pytest.fixture
async def adapter(store: MemoryStore, tmp_path: Path) -> Any:
    a = FileAdapter(store=store, cache_dir=str(tmp_path / "cache"))
    yield a
    await a.close()


def test_identifiers_are_safe_and_unique() -> None:
    taken: set[str] = set()
    assert identifier("Planned go-live", taken) == "planned_go_live"
    assert identifier("Planned go live", taken) == "planned_go_live_2"
    assert identifier("2026 budget", taken) == "c_2026_budget"
    assert identifier('x"; DROP TABLE y; --', taken) == "x_drop_table_y"


async def test_workbook_profile_flags_and_types(adapter: FileAdapter, store: MemoryStore) -> None:
    info = file_info(store, tpg_workbook(extras=True))
    catalog = await adapter.introspect(info)
    names = {o.qualified_name for o in catalog.objects}
    assert names == {"main.projects", "main.project_requests", "main.notes"}
    projects = next(o for o in catalog.objects if o.qualified_name == "main.projects")
    types = {c["name"]: c["type"] for c in projects.columns}
    # Whole-number amounts are stored as integers in the workbook; a fractional column is a double.
    assert types["planned_deployment_date"] == "date" and types["approved_budget_value_usd"] == "bigint"
    assert types["approved_pcr_counter"] == "bigint" and types["modified_on"] == "timestamp"
    assert projects.row_estimate == 100 and projects.kind == "sheet"
    notes = next(t for t in catalog.profile["sheets"] if t["sheet"] == "Notes")
    assert notes["header_row"] == 2 and notes["merged_cells"] == 1
    flags = {c["header"]: c["flags"] for c in notes["columns"]}
    assert flags["Mixed"] == ["mixed_types"] and "formula_only" in flags["Formula"] and flags["Score"] == []
    notes_obj = next(o for o in catalog.objects if o.qualified_name == "main.notes")
    assert {c["name"]: c["type"] for c in notes_obj.columns}["score"] == "double"
    assert "Project id" in flags  # headers kept for the confirmation screen; no cell values are returned
    assert "P0001" not in str(catalog.profile)


async def test_settings_override_inference(adapter: FileAdapter, store: MemoryStore) -> None:
    sheets = [{"sheet": "projects", "header_row": 1, "columns": [
        {"header": "approved_pcr_counter", "type": "text"}, {"header": "cio", "personal": True}]},
              {"sheet": "project_requests", "include": False}]
    info = file_info(store, tpg_workbook(), sheets=sheets)
    catalog = await adapter.introspect(info)
    assert {o.qualified_name for o in catalog.objects} == {"main.projects"}
    cols = {c["name"]: c for c in catalog.objects[0].columns}
    assert cols["approved_pcr_counter"]["type"] == "varchar" and cols["cio"]["personal"] is True


async def test_queries_caps_and_types(adapter: FileAdapter, store: MemoryStore) -> None:
    info = file_info(store, tpg_workbook())
    typed = await adapter.execute(info, "SELECT count(*) AS n, max(planned_deployment_date) AS d, "
                                        "sum(approved_budget_value_usd) AS s FROM main.projects WHERE status = $p0",
                                  {"p0": "Active"}, Limits(), "e1")
    assert [c["type"] for c in typed.columns] == ["integer", "date", "integer"] and typed.rows[0][0] == 65
    over = await adapter.execute(info, "SELECT project_id FROM main.projects", {}, Limits(max_rows=40), "e2")
    assert len(over.rows) == 40 and over.truncated
    with pytest.raises(TelaError) as err:
        await adapter.execute(info, "SELECT * FROM main.projects", {}, Limits(max_bytes=2000), "e3")
    assert err.value.code == ErrorCode.PAYLOAD_TOO_LARGE
    tricky = await adapter.execute(info, "SELECT count(*) FROM main.projects WHERE name = $p0",
                                   {"p0": "x' OR '1'='1"}, Limits(), "e4")
    assert tricky.rows == [[0]]


async def test_read_only_no_external_access_and_timeout(adapter: FileAdapter, store: MemoryStore,
                                                        tmp_path: Path) -> None:
    info = file_info(store, tpg_workbook())
    for sql in ("INSERT INTO main.projects (project_id) VALUES ('X')", "DROP TABLE main.projects",
                "CREATE TABLE main.x AS SELECT 1 AS a"):
        with pytest.raises(TelaError) as err:
            await adapter.execute(info, sql, {}, Limits(), "w")
        assert err.value.code in (ErrorCode.READ_ONLY_VIOLATION, ErrorCode.QUERY_FAILED), sql
    secret = tmp_path / "secret.csv"
    secret.write_text("a\n1\n")
    for sql in (f"SELECT * FROM read_csv('{secret}')", "INSTALL httpfs", "SET enable_external_access = true",
                f"COPY main.projects TO '{tmp_path / 'out.csv'}'", f"ATTACH '{tmp_path / 'x.db'}'"):
        with pytest.raises(TelaError):
            await adapter.execute(info, sql, {}, Limits(), "x")
    assert not (tmp_path / "out.csv").exists() and not (tmp_path / "x.db").exists()
    with pytest.raises(TelaError) as err:
        await adapter.execute(info, "SELECT count(*) FROM main.projects a, main.projects b, main.projects c, "
                                    "main.projects d, main.projects e", {}, Limits(timeout_ms=300), "t")
    assert err.value.code == ErrorCode.QUERY_TIMEOUT


async def test_checksum_mismatch_and_missing_file(adapter: FileAdapter, store: MemoryStore) -> None:
    info = file_info(store, tpg_workbook())
    store.objects[info.descriptor["file"]["object_key"]] = b"tampered"
    with pytest.raises(TelaError) as err:
        await adapter.introspect(info)
    assert err.value.code == ErrorCode.SIGNATURE_INVALID
    result = await adapter.test_connection(type(info)(info.connection_id, "excel", {"file": {}}, {}))
    assert not result.reachable


async def test_csv_source(adapter: FileAdapter, store: MemoryStore) -> None:
    info = file_info(store, tpg_csv(), "csv", filename="TPG projects.csv")
    catalog = await adapter.introspect(info)
    assert [o.qualified_name for o in catalog.objects] == ["main.tpg_projects"]
    types = {c["name"]: c["type"] for c in catalog.objects[0].columns}
    assert types["planned_deployment_date"] == "date" and types["percent_complete"] == "double"
    rows = await adapter.execute(info, "SELECT count(*) FROM main.tpg_projects WHERE current_state = $p0",
                                 {"p0": "Active"}, Limits(), "c")
    assert rows.rows == [[13]]
