"""Agent run log (ENH-024, US-225 to US-229; DEC-25)."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import httpx
import psycopg
import pytest
from api_testkit import needs_db
from conftest import FakeGateway
from pmo_testkit import World, h, setup_world
from test_ask_api import ask, by_event

from tela_common import service_auth

pytestmark = [needs_db, pytest.mark.integration]


@pytest.fixture
async def world(stack: httpx.AsyncClient, owner_conn: psycopg.Connection, make_token: Callable[..., str],
                tpg_source: dict[str, Any]) -> World:
    return await setup_world(stack, owner_conn, make_token, tpg_source)


async def runs(api: httpx.AsyncClient, token: str, **params: Any) -> list[dict[str, Any]]:
    resp = await api.get("/api/v1/admin/agent-runs", headers=h(token), params=params)
    assert resp.status_code == 200, resp.text
    items: list[dict[str, Any]] = resp.json()["items"]
    return items


async def detail(api: httpx.AsyncClient, token: str, run_id: str) -> dict[str, Any]:
    resp = await api.get(f"/api/v1/admin/agent-runs/{run_id}", headers=h(token))
    assert resp.status_code == 200, resp.text
    out: dict[str, Any] = resp.json()
    return out


async def test_a_rules_answer_is_one_run_with_its_steps_and_no_model_call(stack: httpx.AsyncClient,
                                                                         world: World) -> None:
    admin = world.tokens["admin"]
    turn_id, events = await ask(stack, world.tokens["creator"], "open projects by region")
    assert by_event(events)["plan"]["planner"] == "rules"
    found = await runs(stack, admin, turn_id=turn_id)
    assert len(found) == 1
    run = found[0]
    assert run["kind"] == "ask" and run["title"] == "open projects by region" and run["outcome"] == "ok"
    assert run["llm_calls"] == 0 and run["user"]["name"] == "Cleo Creator" and run["ended_at"] is not None
    assert {"AG-01", "AG-03", "AG-07", "AG-08"} <= set(run["agents"])
    d = await detail(stack, admin, run["id"])
    kinds = [s["kind"] for s in d["steps"]]
    assert kinds[0] == "decision" and kinds[-2] == "done" and "llm_call" not in kinds
    assert d["steps"][-1]["label"].startswith("Question frame (shadow)")  # US-253: recorded after the answer
    assert [s["seq"] for s in d["steps"]] == list(range(1, len(d["steps"]) + 1))
    labels = [s["label"] for s in d["steps"]]
    assert "Routed the question: ask" in labels and "Rules planner: ok" in labels and "Ran the query" in labels
    assert "Planned by rules: ok" in labels
    query = next(s for s in d["steps"] if s["kind"] == "query")
    assert "rows" not in query["detail"] and query["detail"]["row_count"] >= 1  # no result rows in a step
    assert not any(s["has_text"] for s in d["steps"])
    # Readers of the log are Admins only.
    assert (await stack.get("/api/v1/admin/agent-runs", headers=h(world.tokens["creator"]))).status_code == 403


async def test_model_calls_keep_what_was_sent_and_the_raw_reply_and_reading_it_is_audited(
        stack: httpx.AsyncClient, world: World, owner_conn: psycopg.Connection) -> None:
    admin = world.tokens["admin"]
    fake = FakeGateway({"AG-03": {"measure": "measure:schedule_risk", "breakdowns": ["dimension:region"]}})
    stack.app.state.gateway = fake.client()  # type: ignore[attr-defined]
    turn_id, events = await ask(stack, admin, "where are we slipping")
    assert by_event(events)["plan"]["planner"] == "llm"
    assert all(c["capture"] for c in fake.calls)  # the gateway is asked for the text it sent
    run = (await runs(stack, admin, turn_id=turn_id))[0]
    assert run["llm_calls"] == 4 and run["prompt_tokens"] == 160 and run["completion_tokens"] == 36
    d = await detail(stack, admin, run["id"])
    calls = [s for s in d["steps"] if s["kind"] == "llm_call"]
    assert len(calls) == 4 and all(s["agent"] == "AG-03" and s["has_text"] for s in calls)
    assert calls[0]["task"] == "plan" and calls[0]["model"] == "llama3.2:3b" and calls[0]["provider"] == "ollama"
    resp = await stack.get(f"/api/v1/admin/agent-runs/{run['id']}/steps/{calls[1]['seq']}/text", headers=h(admin))
    assert resp.status_code == 200, resp.text
    got = resp.json()
    assert [m["role"] for m in got["prompt"]] == ["system", "user"] and "where are we slipping" in got["prompt"][1][
        "content"]
    assert json.loads(got["reply"]) == {"measure": "measure:schedule_risk", "breakdowns": ["dimension:region"]}
    assert got["prompt"] == fake.calls[1]["messages"]
    audited = owner_conn.execute("SELECT object_id, details FROM ops.audit_events WHERE action = "
                                 "'agent_run.text_viewed'").fetchall()
    assert audited == [(run["id"], {"seq": calls[1]["seq"], "agent": "AG-03", "task": "plan"})]
    # The text never reaches the ledger, the audit details or the turn's own events.
    stored = json.dumps(owner_conn.execute("SELECT json_agg(e) FROM ops.audit_events e").fetchone()[0])
    assert "where are we slipping" not in stored.replace(run["id"], "")
    # Filters: by agent, by a model call, by words.
    assert [r["id"] for r in await runs(stack, admin, agent="AG-03", llm=True)] == [run["id"]]
    assert run["id"] in [r["id"] for r in await runs(stack, admin, q="slipping")]
    facets = (await stack.get("/api/v1/admin/agent-runs/facets", headers=h(admin))).json()
    assert facets["agent"]["AG-03"] >= 1 and facets["kind"]["ask"] >= 1


async def test_a_failing_model_is_a_fallback_and_turning_capture_off_keeps_no_text(stack: httpx.AsyncClient,
                                                                                   world: World) -> None:
    admin = world.tokens["admin"]
    stack.app.state.gateway = FakeGateway(fail="planner_unavailable").client()  # type: ignore[attr-defined]
    turn_id, _ = await ask(stack, admin, "where are we slipping")
    run = (await runs(stack, admin, turn_id=turn_id))[0]
    d = await detail(stack, admin, run["id"])
    assert any(s["kind"] == "llm_call" and s["status"] == "failed" for s in d["steps"])
    assert any(s["kind"] == "fallback" for s in d["steps"])
    assert run["outcome"] in ("fallback", "refused", "error")

    put = await stack.put("/api/v1/admin/agent-runs/settings", headers=h(admin),
                          json={"capture_text": False, "text_days": 7})
    assert put.status_code == 200 and put.json() == {**put.json(), "capture_text": False, "text_days": 7,
                                                     "run_days": 90}
    fake = FakeGateway({"AG-03": {"measure": "measure:schedule_risk", "breakdowns": ["dimension:region"]}})
    stack.app.state.gateway = fake.client()  # type: ignore[attr-defined]
    turn_id, _ = await ask(stack, admin, "where are we slipping")
    assert not any(c["capture"] for c in fake.calls)
    run = (await runs(stack, admin, turn_id=turn_id))[0]
    d = await detail(stack, admin, run["id"])
    calls = [s for s in d["steps"] if s["kind"] == "llm_call"]
    assert calls and not any(s["has_text"] for s in calls) and calls[0]["prompt_tokens"] == 40
    assert d["text_kept"] is False


async def test_runs_outside_ask_rules_agents_and_the_sweep(stack: httpx.AsyncClient, world: World,
                                                           owner_conn: psycopg.Connection,
                                                           signing_keys: dict[str, Any]) -> None:
    admin = world.tokens["admin"]
    # AG-05 (rules only): one report_plan run with its rule step.
    resp = await stack.post("/api/v1/reports/plan", headers=h(world.tokens["creator"]),
                            json={"request": "A delivery overview of open projects by region"})
    assert resp.status_code == 200, resp.text
    run = (await runs(stack, admin, kind="report_plan"))[0]
    assert run["agents"] == ["AG-05"] and run["llm_calls"] == 0 and run["outcome"] == "ok"
    d = await detail(stack, admin, run["id"])
    assert [s["kind"] for s in d["steps"]] == ["rule"] and d["steps"][0]["label"].startswith("Planned ")

    # Text export is audited; purge clears text and keeps the steps.
    fake = FakeGateway({"AG-03": {"measure": "measure:schedule_risk", "breakdowns": ["dimension:region"]}})
    stack.app.state.gateway = fake.client()  # type: ignore[attr-defined]
    turn_id, _ = await ask(stack, admin, "where are we slipping")
    run = (await runs(stack, admin, turn_id=turn_id))[0]
    exported = (await stack.get(f"/api/v1/admin/agent-runs/{run['id']}/export", headers=h(admin))).json()
    assert any(s["text"] and s["text"]["reply"] for s in exported["steps"])
    purge = await stack.post("/api/v1/admin/agent-runs/purge-text", headers=h(admin))
    assert purge.status_code == 200 and purge.json()["steps"] == 4
    d = await detail(stack, admin, run["id"])
    assert not any(s["has_text"] for s in d["steps"]) and any(s["text_cleared"] for s in d["steps"])
    actions = {r[0] for r in owner_conn.execute("SELECT action FROM ops.audit_events WHERE action LIKE "
                                                "'agent_run.%'").fetchall()}
    assert actions == {"agent_run.exported", "agent_run.text_purged"}

    # The daily sweep: runs older than 90 days go; text goes after the tenant's days.
    owner_conn.execute("UPDATE ops.agent_runs SET started_at = now() - interval '100 days' WHERE id = %s",
                       (run["id"],))
    token = service_auth.issue(signing_keys["service"].encode(), sub="tela-worker", aud="tela-api-internal",
                               ttl_s=60)
    assert (await stack.post("/api/v1/internal/agent-runs/sweep")).status_code == 401
    swept = await stack.post("/api/v1/internal/agent-runs/sweep", headers={"Authorization": f"Bearer {token}"})
    assert swept.status_code == 200 and swept.json()["deleted"] == 1
    assert (await stack.get(f"/api/v1/admin/agent-runs/{run['id']}", headers=h(admin))).status_code == 404
    assert (await stack.get("/api/v1/admin/agent-runs/not-a-run", headers=h(admin))).status_code == 404


async def test_a_running_run_shows_its_steps_so_far_and_other_tenants_runs_stay_hidden(
        stack: httpx.AsyncClient, world: World, owner_conn: psycopg.Connection) -> None:
    """US-228: a run still going is listed with the steps written so far; RLS keeps a tenant's runs to itself."""
    from api_testkit import OTHER_ISSUER, bootstrap
    from pmo_testkit import GROUPS

    from tela_common.ids import new_id

    admin = world.tokens["admin"]
    tenant = owner_conn.execute("SELECT id FROM iam.tenants WHERE slug = 'acme'").fetchone()[0]
    running = new_id()
    owner_conn.execute("INSERT INTO ops.agent_runs (id, tenant_id, kind, title, agents) VALUES (%s, %s, 'ask', "
                       "'still going', '{AG-01,AG-03}')", (running, tenant))
    owner_conn.execute("INSERT INTO ops.agent_steps (run_id, seq, tenant_id, agent, kind, label) VALUES "
                       "(%s, 1, %s, 'AG-01', 'decision', 'Routed the question: ask')", (running, tenant))
    d = await detail(stack, admin, running)
    assert d["outcome"] == "running" and d["ended_at"] is None and [s["seq"] for s in d["steps"]] == [1]
    assert running in [r["id"] for r in await runs(stack, admin, outcome="running")]

    other = bootstrap(owner_conn, "globex", OTHER_ISSUER, GROUPS)
    theirs = new_id()
    owner_conn.execute("INSERT INTO ops.agent_runs (id, tenant_id, kind, title) VALUES (%s, %s, 'ask', 'globex "
                       "secret')", (theirs, other))
    assert theirs not in [r["id"] for r in await runs(stack, admin)]
    assert (await stack.get(f"/api/v1/admin/agent-runs/{theirs}", headers=h(admin))).status_code == 404
    assert (await stack.get(f"/api/v1/admin/agent-runs/{theirs}/steps/1/text", headers=h(admin))).status_code == 404


async def test_ag10_sees_only_the_tables_the_question_points_to(stack: httpx.AsyncClient, world: World) -> None:
    """EXEC-146 follow-up: AG-10 got every column of every table (over 64,000 characters on Dataverse) and a local
    model timed out; now its prompt lists the tables the question points to and says how many it left out."""
    admin = world.tokens["admin"]
    fake = FakeGateway({"AG-10": {"sql": "SELECT 1", "explanation": ""}})
    stack.app.state.gateway = fake.client()  # type: ignore[attr-defined]
    resp = await stack.post("/api/v1/custom-sql/draft", headers=h(admin),
                            json={"connection_id": world.connection_id, "question": "open projects by region"})
    assert resp.status_code == 200, resp.text
    system = next(c for c in fake.calls if c["agent"] == "AG-10")["messages"][0]["content"]
    block = system.split("Tables:\n", 1)[1].split("\n\nCertified measures", 1)[0]
    tables = [ln for ln in block.splitlines() if ln.startswith("- ") and not ln.startswith("- link:")]
    assert tables and all("project" in ln.split(":", 1)[0] or "region" in ln.split(":", 1)[0] for ln in tables), tables
    assert "other tables are not listed" in system and "SQL dialect: postgres" in system
    assert "Answer with an aggregate query" in system and "Certified measures (their rules):" in system
    run = (await runs(stack, admin, kind="custom_sql"))[0]
    assert run["agents"] == ["AG-10"] and run["llm_calls"] == 1
