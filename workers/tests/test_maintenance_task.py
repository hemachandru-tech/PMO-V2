"""Hourly scim_reconcile (US-119): calls the API; transport failures are retried with backoff."""

from __future__ import annotations

from typing import Any

import pytest

from tela_worker.tasks import maintenance


class FakeApi:
    calls: list[str] = []
    fail = False

    def post(self, path: str, json: Any = None) -> dict[str, Any]:
        FakeApi.calls.append(path)
        if FakeApi.fail:
            raise ConnectionError("api down")
        return {"tenants": 1, "ok": 1, "drift": 0, "failed": 0, "busy": 0}

    def close(self) -> None:
        pass


def test_scim_reconcile_calls_the_api(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(maintenance, "InternalApi", FakeApi)
    FakeApi.calls, FakeApi.fail = [], False
    assert maintenance.scim_reconcile.apply().get()["ok"] == 1
    assert FakeApi.calls == ["/api/v1/internal/scim/reconcile"]


def test_scim_reconcile_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(maintenance, "InternalApi", FakeApi)
    FakeApi.calls, FakeApi.fail = [], True
    result = maintenance.scim_reconcile.apply()
    assert result.failed() and len(FakeApi.calls) == 3  # first try plus two retries


def test_agent_runs_sweep_calls_the_api(monkeypatch: pytest.MonkeyPatch) -> None:
    """ENH-024 (US-229): the daily job asks the API to clear old agent run text and delete runs past 90 days."""
    monkeypatch.setattr(maintenance, "InternalApi", FakeApi)
    FakeApi.calls, FakeApi.fail = [], False
    maintenance.agent_runs_sweep.apply().get()
    assert FakeApi.calls == ["/api/v1/internal/agent-runs/sweep"]
