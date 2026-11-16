"""Ask answer mode per connection (ENH-028 US-256; DEC-29, DEC-30), live on the TPG fixture.

Acceptance: a connection carries ``ask_mode`` (``certified`` by default); Connections > Ask lists the four modes with
one line each, whether each can be chosen and its latest gold score; choosing a mode is audited and refused when the
mode's planner isn't built or the source can't take it (Direct SQL on MongoDB); the orchestrator picks the planner by
the source's mode and records the mode on the turn, its agent run and the Ask events.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx
import psycopg
import pytest
from api_testkit import needs_db
from conftest import FakeGateway
from pmo_testkit import World, h, setup_world
from test_agent_runs_api import runs
from test_ask_api import ask, by_event

from tela_api.ask import modes
from tela_api.core.settings import get_settings
from tela_common.ids import new_id

pytestmark = [needs_db, pytest.mark.integration]


@pytest.fixture
async def world(stack: httpx.AsyncClient, owner_conn: psycopg.Connection, make_token: Callable[..., str],
                tpg_source: dict[str, Any]) -> World:
    return await setup_world(stack, owner_conn, make_token, tpg_source)


def url(world: World) -> str:
    return f"/api/v1/connections/{world.connection_id}/ask-mode"


async def get_mode(api: httpx.AsyncClient, world: World) -> dict[str, Any]:
    resp = await api.get(url(world), headers=h(world.tokens["admin"]))
    assert resp.status_code == 200, resp.text
    out: dict[str, Any] = resp.json()
    return out


async def set_mode(api: httpx.AsyncClient, world: World, mode: str) -> httpx.Response:
    return await api.put(url(world), headers=h(world.tokens["admin"]), json={"mode": mode})


async def audit(api: httpx.AsyncClient, world: World) -> list[dict[str, Any]]:
    resp = await api.get("/api/v1/admin/audit", headers=h(world.tokens["admin"]),
                         params={"action": "connection.ask_mode_changed"})
    assert resp.status_code == 200, resp.text
    items: list[dict[str, Any]] = resp.json()["items"]
    return items


async def test_certified_is_the_default_and_the_four_modes_are_listed(stack: httpx.AsyncClient,
                                                                       world: World) -> None:
    got = await get_mode(stack, world)
    assert got["mode"] == "certified" and got["answers_with"] == "certified" and got["fallback_reason"] is None
    by_mode = {c["mode"]: c for c in got["choices"]}
    assert list(by_mode) == ["certified", "fields", "hybrid", "direct_sql"]
    assert all(c["label"] and c["note"] for c in got["choices"])
    assert by_mode["certified"]["default"] and by_mode["certified"]["unavailable"] is None
    assert by_mode["direct_sql"]["unavailable"] is None  # PostgreSQL is a SQL source
    assert by_mode["fields"]["unavailable"] is None  # Plan B (US-254)
    assert by_mode["hybrid"]["unavailable"] is None  # US-255
    assert all(c["score"] is None for c in got["choices"])  # no gold run yet
    conn = await stack.get(f"/api/v1/connections/{world.connection_id}", headers=h(world.tokens["admin"]))
    assert conn.json()["ask_mode"] == "certified"
    # Admins only.
    for who in ("creator", "viewer"):
        assert (await stack.get(url(world), headers=h(world.tokens[who]))).status_code == 403
        assert (await stack.put(url(world), headers=h(world.tokens[who]),
                                json={"mode": "direct_sql"})).status_code == 403


async def test_changing_the_mode_is_audited_and_unbuilt_or_unsuited_modes_are_refused(
        stack: httpx.AsyncClient, world: World, owner_conn: psycopg.Connection,
        monkeypatch: pytest.MonkeyPatch) -> None:
    resp = await set_mode(stack, world, "direct_sql")
    assert resp.status_code == 200, resp.text
    assert resp.json()["mode"] == "direct_sql" and resp.json()["answers_with"] == "direct_sql"
    [event] = await audit(stack, world)
    assert event["details"] == {"from": "certified", "to": "direct_sql"}
    severity = owner_conn.execute("SELECT severity FROM ops.audit_events WHERE action = "
                                  "'connection.ask_mode_changed'").fetchone()
    assert severity is not None and severity[0] == "high"  # Direct SQL is switched on: a high-severity change
    assert (await set_mode(stack, world, "direct_sql")).status_code == 200
    assert len(await audit(stack, world)) == 1  # choosing the same mode again changes nothing

    assert (await set_mode(stack, world, "nonsense")).status_code in (400, 422)
    monkeypatch.setattr(modes, "BUILT", modes.BUILT - {"hybrid"})  # a mode not built (all are, since US-255)
    assert (await set_mode(stack, world, "hybrid")).status_code in (400, 422)
    monkeypatch.undo()
    monkeypatch.setattr(get_settings(), "ask_direct_sql", False)  # DEC-30 withdrawn
    got = await get_mode(stack, world)
    assert got["answers_with"] == "certified" and "switched off" in got["fallback_reason"]
    monkeypatch.setattr(get_settings(), "ask_direct_sql", True)

    assert (await set_mode(stack, world, "certified")).status_code == 200
    assert [e["details"]["to"] for e in await audit(stack, world)] == ["certified", "direct_sql"]
    # MongoDB is not a SQL source: Direct SQL is refused (and listed as unavailable).
    owner_conn.execute("UPDATE conn.connections SET engine = 'mongodb' WHERE id = %s", (world.connection_id,))
    resp = await set_mode(stack, world, "direct_sql")
    assert resp.status_code == 400 and "SQL source" in resp.json()["error"]["message"]
    assert {c["mode"]: c for c in (await get_mode(stack, world))["choices"]}["direct_sql"]["unavailable"]


async def test_each_mode_shows_its_latest_gold_run(stack: httpx.AsyncClient, world: World,
                                                   owner_conn: psycopg.Connection) -> None:
    admin = owner_conn.execute("SELECT id FROM iam.users WHERE display_name = 'Ada Admin'").fetchone()
    assert admin is not None

    def gold_run(mode: str, finished: str, results: list[tuple[bool, str, bool, int]]) -> None:
        run_id = new_id()
        owner_conn.execute(
            "INSERT INTO rag.eval_runs (id, tenant_id, connection_id, trigger, threshold, run_as, status, ask_mode, "
            "plan_only, finished_at) VALUES (%s, %s, %s, 'manual', 0.9, %s, 'done', %s, true, %s)",
            (run_id, world.tenant_id, world.connection_id, admin[0], mode, finished))
        for passed, status, wrong_sure, ms in results:
            owner_conn.execute(
                "INSERT INTO rag.eval_results (id, tenant_id, run_id, question, passed, status, confident_wrong, "
                "latency_ms) VALUES (%s, %s, %s, 'q', %s, %s, %s, %s)",
                (new_id(), world.tenant_id, run_id, passed, status, wrong_sure, ms))

    gold_run("certified", "2026-11-01", [(True, "ok", False, 100)])  # older: not shown
    gold_run("certified", "2026-11-10", [(True, "ok", False, 1000), (False, "ok", True, 3000),
                                         (False, "clarify", False, 2000), (False, "unsupported", False, 500)])
    gold_run("direct_sql", "2026-11-11", [(True, "ok", False, 9000)])
    by_mode = {c["mode"]: c for c in (await get_mode(stack, world))["choices"]}
    score = by_mode["certified"]["score"]
    assert score["total"] == 4 and score["right"] == 1 and score["confident_wrong"] == 1
    assert score["clarified"] == 1 and score["refused"] == 1 and score["median_ms"] == 1000
    assert score["run_at"].startswith("2026-11-10") and score["plan_only"] is True
    assert by_mode["direct_sql"]["score"]["right"] == 1 and by_mode["fields"]["score"] is None


async def test_the_source_mode_picks_the_planner_and_is_recorded(stack: httpx.AsyncClient, world: World,
                                                                 owner_conn: psycopg.Connection,
                                                                 monkeypatch: pytest.MonkeyPatch) -> None:
    admin = world.tokens["admin"]
    # Certified: today's planner, the mode on the intent, the turn and the agent run.
    turn_id, events = await ask(stack, admin, "open projects by region")
    ev = by_event(events)
    assert ev["intent"]["ask_mode"] == "certified" and ev["plan"]["planner"] == "rules" and "complete" in ev
    assert (await runs(stack, admin, turn_id=turn_id))[0]["ask_mode"] == "certified"
    turn = await stack.get(f"/api/v1/ask/turns/{turn_id}", headers=h(admin))
    assert turn.json()["ask_mode"] == "certified"

    # Direct SQL: AG-10 writes the query, the Custom SQL pipeline runs it; badge and SQL on the answer.
    assert (await set_mode(stack, world, "direct_sql")).status_code == 200
    statement = "SELECT name, region FROM projects WHERE status = 'Active'"
    fake = FakeGateway({"AG-10": {"sql": statement, "explanation": "Active projects and their region."}})
    stack.app.state.gateway = fake.client()  # type: ignore[attr-defined]
    turn_id, events = await ask(stack, admin, "list of active projects")
    ev = by_event(events)
    assert ev["intent"]["ask_mode"] == "direct_sql" and ev["plan"]["planner"] == "direct_sql"
    done = ev["complete"]
    assert done["badge"] == "custom_sql" and done["sql"] == statement and done["certified"] is False
    assert done["publishable"] is False and done["can_pin"] is True and done["ask_mode"] == "direct_sql"  # US-260
    assert done["row_count"] == len(done["rows"]) > 0 and done["columns"]
    assert [c["task"] for c in fake.calls if c["agent"] == "AG-10"] == ["sql_draft"]
    assert (await runs(stack, admin, turn_id=turn_id))[0]["ask_mode"] == "direct_sql"
    turn = (await stack.get(f"/api/v1/ask/turns/{turn_id}", headers=h(admin))).json()
    assert turn["status"] == "complete" and turn["planner"] == "direct_sql" and turn["ask_mode"] == "direct_sql"

    # A refused draft ends the turn with the reason and the refused SQL, never another mode's answer.
    # (A new question: the same words again would reuse the SQL that passed last time, from the answer memory.)
    stack.app.state.gateway = FakeGateway({"AG-10": {"sql": "DELETE FROM projects", "explanation": "x"}}).client()  # type: ignore[attr-defined]
    _, events = await ask(stack, admin, "list of projects on hold")
    err = by_event(events)["error"]
    assert err["code"] == "direct_sql_refused" and err["sql"] == "DELETE FROM projects" and "complete" not in by_event(events)

    # Fields + definitions: Plan B (US-254) plans the question.
    assert (await set_mode(stack, world, "fields")).status_code == 200
    turn_id, events = await ask(stack, admin, "Show the List of open Projects")
    ev = by_event(events)
    assert ev["intent"]["ask_mode"] == "fields" and ev["plan"]["planner"] == "fields", ev.get("error")
    assert ev["complete"]["ask_mode"] == "fields"
    assert (await runs(stack, admin, turn_id=turn_id))[0]["ask_mode"] == "fields"

    # A stored mode whose planner isn't built answers with certified measures and says so.
    monkeypatch.setattr(modes, "BUILT", modes.BUILT - {"hybrid"})  # every mode is built since US-255: one taken away
    owner_conn.execute("UPDATE conn.connections SET ask_mode = 'hybrid' WHERE id = %s", (world.connection_id,))
    _, events = await ask(stack, admin, "open projects by region")
    ev = by_event(events)
    assert ev["intent"]["ask_mode"] == "certified" and "Hybrid" in ev["intent"]["ask_mode_note"]
    assert ev["plan"]["planner"] == "rules" and "complete" in ev
