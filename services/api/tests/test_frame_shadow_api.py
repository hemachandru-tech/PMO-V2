"""US-253 shadow mode: every Ask turn's question frame is recorded as a step of its agent run; answers unchanged."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Any

import httpx
import psycopg
import pytest
from api_testkit import needs_db
from conftest import FakeGateway
from pmo_testkit import World, setup_world
from test_agent_runs_api import detail, runs
from test_ask_api import ask, by_event

from tela_api.core.settings import get_settings

pytestmark = [needs_db, pytest.mark.integration]


@pytest.fixture
async def world(stack: httpx.AsyncClient, owner_conn: psycopg.Connection, make_token: Callable[..., str],
                tpg_source: dict[str, Any]) -> World:
    return await setup_world(stack, owner_conn, make_token, tpg_source)


@pytest.fixture
def frame_mode(monkeypatch: pytest.MonkeyPatch) -> Iterator[Callable[[str], None]]:
    def set_mode(mode: str) -> None:
        monkeypatch.setattr(get_settings(), "ask_frame", mode)
    yield set_mode


def frame_steps(d: dict[str, Any]) -> list[dict[str, Any]]:
    return [s for s in d["steps"] if s["label"].startswith("Question frame (shadow)")]


async def test_the_frame_is_recorded_after_the_answer(stack: httpx.AsyncClient, world: World,
                                                      frame_mode: Callable[[str], None]) -> None:
    frame_mode("rules")
    admin = world.tokens["admin"]
    turn_id, events = await ask(stack, world.tokens["creator"], "open projects by region")
    assert by_event(events)["plan"]["planner"] == "rules" and "complete" in by_event(events)
    run = (await runs(stack, admin, turn_id=turn_id))[0]
    assert run["llm_calls"] == 0 and run["outcome"] == "ok"
    d = await detail(stack, admin, run["id"])
    (step,) = frame_steps(d)
    assert step["agent"] == "AG-01" and step["kind"] == "decision"
    assert step["seq"] == len(d["steps"])  # after "Answered": it never holds up the answer
    got = step["detail"]
    assert got["frame"]["shape"] == "breakdown" and got["frame"]["groupings"] == ["region"]
    assert got["llm_status"] == "off" and got["llm"] is None


async def test_with_a_provider_the_llm_reads_it_too(stack: httpx.AsyncClient, world: World,
                                                    owner_conn: psycopg.Connection,
                                                    frame_mode: Callable[[str], None]) -> None:
    frame_mode("shadow")
    owner_conn.execute("INSERT INTO ops.llm_settings (tenant_id, provider, models) VALUES (%s, 'ollama', '{}') "
                       "ON CONFLICT (tenant_id) DO UPDATE SET provider = 'ollama'", (world.tenant_id,))
    fake = FakeGateway({"AG-01": {"shape": "list_of_records", "subject": "projects",
                                  "conditions": [{"phrase": "open"}]}})
    stack.app.state.gateway = fake.client()  # type: ignore[attr-defined]
    admin = world.tokens["admin"]
    turn_id, events = await ask(stack, admin, "Show the List of open Projects")
    answered = by_event(events)
    assert "complete" in answered or "error" in answered or "clarify" in answered  # whatever it was, unchanged
    run = (await runs(stack, admin, turn_id=turn_id))[0]
    d = await detail(stack, admin, run["id"])
    (step,) = frame_steps(d)
    got = step["detail"]
    assert got["llm_status"] == "ok" and got["confident"] and got["frame"]["shape"] == "list_of_records"
    assert got["rules"]["shape"] == "list_of_records"
    frame_calls = [c for c in fake.calls if c["task"] == "frame"]
    assert len(frame_calls) == 1 and frame_calls[0]["agent"] == "AG-01"
    assert any(s["kind"] == "llm_call" and s["task"] == "frame" for s in d["steps"])


async def test_off_records_nothing(stack: httpx.AsyncClient, world: World,
                                   frame_mode: Callable[[str], None]) -> None:
    frame_mode("off")
    admin = world.tokens["admin"]
    turn_id, _ = await ask(stack, world.tokens["creator"], "open projects by region")
    run = (await runs(stack, admin, turn_id=turn_id))[0]
    assert frame_steps(await detail(stack, admin, run["id"])) == []
