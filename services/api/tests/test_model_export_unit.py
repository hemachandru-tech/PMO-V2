"""Approved models written to the export folder: v<N>.json, latest.json and manifest.json per connection."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from types import SimpleNamespace

from tela_api.model import export


def test_an_approved_model_is_written_with_its_manifest(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(export, "get_settings", lambda: SimpleNamespace(model_export_dir=str(tmp_path)))
    rows = iter([
        {"model_id": "m1", "connection": "ball report", "connection_id": "c1", "engine": "mysql", "version_id": "v3",
         "version_no": 3, "status": "approved", "approved_at": datetime(2026, 11, 15, tzinfo=UTC),
         "definition": {"entities": [{"key": "a"}], "rules": [], "measures": [{"key": "x"}]}},
        {"model_id": "m1", "connection": "ball report", "connection_id": "c1", "engine": "mysql", "version_id": "v2",
         "version_no": 2, "status": "superseded", "approved_at": None, "definition": {"entities": []}},
    ])

    async def fake_fetch_one(conn, sql, params):
        return next(rows)
    monkeypatch.setattr(export, "fetch_one", fake_fetch_one)
    first = asyncio.run(export.export_model(None, "m1"))
    older = asyncio.run(export.export_model(None, "m1", "v2"))
    folder = tmp_path / "ball-report"
    assert first["file"] == "v3.json" and first["latest"] and not older["latest"]
    assert json.loads((folder / "latest.json").read_text())["measures"] == [{"key": "x"}]  # still v3, not v2
    manifest = json.loads((folder / "manifest.json").read_text())
    assert manifest["latest_version"] == 3 and [v["version"] for v in manifest["versions"]] == [2, 3]


def test_export_is_off_without_a_folder(monkeypatch) -> None:
    monkeypatch.setattr(export, "get_settings", lambda: SimpleNamespace(model_export_dir=None))
    assert asyncio.run(export.export_model(None, "m1")) is None
