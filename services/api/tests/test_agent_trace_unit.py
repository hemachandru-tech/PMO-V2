"""The agent run log never stops the work (ENH-024, US-225 W4)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import pytest

from tela_api.llm import trace


@asynccontextmanager
async def broken(_: Any) -> AsyncIterator[Any]:
    raise ConnectionError("database down")
    yield  # pragma: no cover


async def test_a_failed_log_write_never_fails_the_work(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(trace, "tenant_tx", broken)
    async with trace.agent_run("t1", "u1", "ask", agent="AG-01") as run:
        assert run is None and trace.current() is None
        await trace.step("decision", "Routed the question")  # no run: nothing to write, nothing raised
        done = 42
    assert done == 42


async def test_steps_are_numbered_and_join_the_outer_run(monkeypatch: pytest.MonkeyPatch) -> None:
    written: list[dict[str, Any]] = []

    class Conn:
        async def execute(self, _sql: Any, params: dict[str, Any] | None = None) -> Any:
            written.append(params or {})

    @asynccontextmanager
    async def fake(_: Any) -> AsyncIterator[Any]:
        yield Conn()

    async def settings(_: Any) -> bool:
        return True

    monkeypatch.setattr(trace, "tenant_tx", fake)
    monkeypatch.setattr(trace, "_settings", settings)
    async with trace.agent_run("t1", "u1", "ask", agent="AG-01") as outer:
        async with trace.agent_run("t1", "u1", "agent", agent="AG-07") as inner:
            assert inner is outer
            await trace.step("llm_call", "AG-07 visual_advice", agent="AG-07", llm={
                "task": "visual_advice", "usage": {"prompt_tokens": 10, "completion_tokens": 3},
                "trace": {"messages": [{"role": "user", "content": "x" * 70_000}], "reply": "{}"}})
        await trace.step("query", "Ran the query", detail={"row_count": 3, "rows": [[1, 2]]})
    assert outer is not None and outer.agents == ["AG-01", "AG-07"] and outer.llm_calls == 1
    steps = [p for p in written if "seq" in p]
    assert [s["seq"] for s in steps] == [1, 2]
    assert steps[0]["cut"] is True and len(steps[0]["prompt"]) < 70_000
    assert "rows" not in steps[1]["detail"] and '"row_count": 3' in steps[1]["detail"]
    close = written[-1]
    assert close["o"] == "ok" and close["n"] == 1 and close["pt"] == 10


async def test_paused_opens_no_run(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(trace, "tenant_tx", broken)
    with trace.paused():
        async with trace.agent_run("t1", "u1", "ask") as run:
            assert run is None


async def test_an_ask_error_is_a_failed_step(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[dict[str, Any]] = []

    async def record(kind: str, label: str, **kw: Any) -> None:
        seen.append({"kind": kind, "label": label, **kw})

    monkeypatch.setattr(trace, "step", record)
    await trace.ask_event("error", {"code": "secrets_unavailable", "message": "x"})
    assert seen[0]["kind"] == "error" and seen[0]["status"] == "failed"
    assert seen[0]["label"] == "Stopped: secrets_unavailable"
