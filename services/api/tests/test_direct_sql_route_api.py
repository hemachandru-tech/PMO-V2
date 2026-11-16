"""Direct SQL routed from Ask (ENH-028 US-257b): the question frame (US-253) reaches AG-10.

Acceptance: a source in ``direct_sql`` mode answers with the frame Ask builds (the LLM's reading when the tenant has a
provider, else the rules'), records it as a step of the turn's agent run (not again as a shadow step), asks AG-10 for
that frame's kind of answer, and carries a follow-up's earlier question in the words AG-10 gets.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx
import pmo_oracle as oracle
import psycopg
import pytest
from api_testkit import needs_db
from conftest import FakeGateway
from pmo_testkit import World, h, setup_world
from test_agent_runs_api import detail, runs
from test_ask_api import ask, by_event

from tela_api.core.settings import get_settings

pytestmark = [needs_db, pytest.mark.integration]


@pytest.fixture
async def world(stack: httpx.AsyncClient, owner_conn: psycopg.Connection, make_token: Callable[..., str],
                tpg_source: dict[str, Any]) -> World:
    w = await setup_world(stack, owner_conn, make_token, tpg_source)
    owner_conn.execute("UPDATE conn.connections SET ask_mode = 'direct_sql' WHERE id = %s", (w.connection_id,))
    return w


def ag10(fake: FakeGateway) -> list[dict[str, Any]]:
    return [c for c in fake.calls if c["agent"] == "AG-10"]


async def test_the_llm_frame_decides_the_kind_of_answer_ag10_is_asked_for(
        stack: httpx.AsyncClient, world: World, owner_conn: psycopg.Connection,
        monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "ask_frame", "shadow")
    owner_conn.execute("INSERT INTO ops.llm_settings (tenant_id, provider, models) VALUES (%s, 'ollama', '{}') "
                       "ON CONFLICT (tenant_id) DO UPDATE SET provider = 'ollama'", (world.tenant_id,))
    statement = "SELECT name, region FROM projects WHERE status = 'Active'"

    def reply(task: str, _messages: Any) -> dict[str, Any]:
        # The rules read "active projects" as one figure; the LLM reads a list of them.
        return {"shape": "list_of_records", "subject": "projects", "conditions": [{"phrase": "active"}]} \
            if task == "frame" else {}

    fake = FakeGateway({"AG-01": reply, "AG-10": {"sql": statement, "explanation": "The active projects."}})
    stack.app.state.gateway = fake.client()  # type: ignore[attr-defined]
    admin = world.tokens["admin"]
    turn_id, events = await ask(stack, admin, "active projects")
    ev = by_event(events)
    assert ev["plan"]["planner"] == "direct_sql" and ev["plan"]["shape"] == "list_of_records", ev.get("error")
    done = ev["complete"]
    assert done["shape"] == "list_of_records" and done["sql"] == statement and done["connection_id"]
    [call] = ag10(fake)
    assert "Answer with a list of records" in call["messages"][0]["content"]
    assert [c["task"] for c in fake.calls if c["agent"] == "AG-01"].count("frame") == 1  # built once, not again
    d = await detail(stack, admin, (await runs(stack, admin, turn_id=turn_id))[0]["id"])
    labels = [s["label"] for s in d["steps"]]
    assert "Question frame: list_of_records, uncertain: shape" in labels
    assert not any(label.startswith("Question frame (shadow)") for label in labels)
    step = next(s for s in d["steps"] if s["label"].startswith("Question frame:"))
    assert step["detail"]["rules"]["shape"] == "number" and step["detail"]["llm_status"] == "ok"


async def test_without_a_provider_the_rules_frame_is_used(stack: httpx.AsyncClient, world: World,
                                                          monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "ask_frame", "shadow")
    fake = FakeGateway({"AG-10": {"sql": "SELECT COUNT(*) AS n FROM projects", "explanation": "All projects."}})
    stack.app.state.gateway = fake.client()  # type: ignore[attr-defined]
    _, events = await ask(stack, world.tokens["admin"], "How many projects are there?")
    ev = by_event(events)
    assert ev["plan"]["shape"] == "number" and ev["complete"]["rows"] == [[len(oracle.PROJECTS)]], ev.get("error")
    assert [c["task"] for c in fake.calls if c["task"] == "frame"] == []
    assert "Answer with one figure" in ag10(fake)[0]["messages"][0]["content"]


async def test_a_follow_up_carries_the_earlier_question(stack: httpx.AsyncClient, world: World,
                                                        monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "ask_frame", "rules")
    fake = FakeGateway({"AG-10": {"sql": "SELECT name, region FROM projects WHERE status = 'Active'",
                                  "explanation": "The active projects."}})
    stack.app.state.gateway = fake.client()  # type: ignore[attr-defined]
    admin = world.tokens["admin"]
    first, _ = await ask(stack, admin, "list of active projects")
    conversation = (await stack.get(f"/api/v1/ask/turns/{first}", headers=h(admin))).json()["conversation_id"]
    _, events = await ask(stack, admin, "only in UAE", conversation_id=conversation)
    assert "complete" in by_event(events), by_event(events).get("error")
    assert by_event(events)["plan"]["shape"] == "list_of_records"  # still a list: read with the earlier question
    first_call, follow_up = ag10(fake)
    assert "earlier question" not in first_call["messages"][-1]["content"]
    assert "only in UAE" in follow_up["messages"][-1]["content"]
    # The earlier turn comes back with the SQL it ran, so the follow-up keeps its table and filter.
    note = follow_up["messages"][-1]["content"]
    assert "EARLIER IN THIS CONVERSATION" in note and "1. Question: list of active projects" in note
    assert "SQL: SELECT name, region FROM projects WHERE status = 'Active'" in note
