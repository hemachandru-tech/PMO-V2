"""Export render task (US-095): start → render → hand back; refusals are final, renderer failures are visible."""

from __future__ import annotations

from typing import Any

import pytest

from tela_worker.api import ApiRefused
from tela_worker.export import RENDERERS
from tela_worker.tasks.exports import run_export

PAYLOAD = {"job": {"id": "j1", "format": "xlsx"}, "file_name": "PMO-2026-10-21.xlsx", "row_count": 3,
           "excluded_widgets": 1, "tabs": []}


class FakeApi:
    def __init__(self, start: dict[str, Any] | Exception) -> None:
        self.start = start
        self.calls: list[tuple[str, Any]] = []

    def post(self, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        self.calls.append((path, body))
        if path.endswith("/start"):
            if isinstance(self.start, Exception):
                raise self.start
            return self.start
        return {"status": "failed"}

    def put_file(self, path: str, data: bytes, content_type: str, params: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((path, {"bytes": data, "type": content_type, **params}))
        return {"status": "succeeded"}

    def close(self) -> None:
        pass


@pytest.fixture
def fake_xlsx(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(RENDERERS, "xlsx", lambda payload: b"PK" + payload["file_name"].encode())


def test_renders_and_hands_the_file_back(fake_xlsx: None) -> None:
    api = FakeApi(PAYLOAD)
    assert run_export("t1", "j1", api) == "done"  # type: ignore[arg-type]
    path, sent = api.calls[-1]
    assert path == "/api/v1/internal/exports/j1/file"
    assert sent["bytes"] == b"PKPMO-2026-10-21.xlsx" and sent["tenant_id"] == "t1"
    assert sent["type"].endswith("spreadsheetml.sheet") and sent["row_count"] == 3 and sent["excluded_widgets"] == 1


def test_refusal_is_final() -> None:
    api = FakeApi(ApiRefused(403, "forbidden"))
    assert run_export("t1", "j1", api) == "refused"  # type: ignore[arg-type]
    assert [c[0] for c in api.calls] == ["/api/v1/internal/exports/j1/start"]


def test_renderer_failure_marks_the_job_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(_: dict[str, Any]) -> bytes:
        raise RuntimeError("chromium crashed")

    monkeypatch.setitem(RENDERERS, "xlsx", boom)
    api = FakeApi(PAYLOAD)
    assert run_export("t1", "j1", api) == "failed"  # type: ignore[arg-type]
    assert api.calls[-1] == ("/api/v1/internal/exports/j1/fail", {"tenant_id": "t1", "error_code": "render_failed"})
