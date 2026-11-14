"""Gold runs per Ask answer mode (ENH-028 US-259; FEAT-078 / TEST).

Acceptance: a run queued in a mode answers every example in that mode on the same questions, whatever the source is
set to (which is left unchanged), and becomes that mode's score on Connections > Ask; Direct SQL is graded by its rows
against the expected spec's, with the kind of answer, and a wrong Direct SQL answer counts as confident-wrong; a
Direct SQL run can't be plan-only; a run queued without a mode follows each source's mode and is stamped with it.
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
from test_gold_api import GOLD, gold, run_queued
from test_knowledge_api import bag, index_model

pytestmark = [needs_db, pytest.mark.integration]
OPEN = "current_state IN ('Project Created', 'Active', 'On Hold')"
SQL = {  # what AG-10 writes for each question; the breakdown forgets the open condition (a wrong answer)
    "How many projects are open?": f"SELECT COUNT(*) AS n FROM projects WHERE {OPEN}",
    "Which projects are open?": f"SELECT name, region FROM projects WHERE {OPEN}",
    "Open projects by region": "SELECT region, COUNT(*) AS n FROM projects GROUP BY region",
}


@pytest.fixture
async def world(stack: httpx.AsyncClient, owner_conn: psycopg.Connection, make_token: Callable[..., str],
                tpg_source: dict[str, Any], signing_keys: dict[str, Any]) -> World:
    w = await setup_world(stack, owner_conn, make_token, tpg_source)
    stack.app.state.gateway = FakeGateway(embedder=bag).client()  # type: ignore[attr-defined]
    await index_model(stack, w, signing_keys)
    admin = w.tokens["admin"]
    open_ = {"measure": {"ref": "measure:open_projects"}}
    for question, spec in (("How many projects are open?", open_),
                           ("Which projects are open?", {**open_, "breakdowns": [{"ref": "field:projects.name"}]}),
                           ("Open projects by region", {**open_, "breakdowns": [{"ref": "dimension:region"}]})):
        resp = await stack.post(GOLD, headers=h(admin), json=gold(w, question, spec, tags=["modes"]))
        assert resp.status_code == 201, resp.text
    return w


def ag10(task: str, messages: list[dict[str, Any]]) -> dict[str, Any]:
    question = messages[-1]["content"].split("\n")[0]
    return {"sql": SQL[question], "explanation": "One sentence."}


async def scores(api: httpx.AsyncClient, world: World) -> dict[str, Any]:
    resp = await api.get(f"/api/v1/connections/{world.connection_id}/ask-mode", headers=h(world.tokens["admin"]))
    assert resp.status_code == 200, resp.text
    return {c["mode"]: c["score"] for c in resp.json()["choices"]}


async def test_a_direct_sql_run_is_graded_by_its_rows_and_scored_for_the_mode(
        stack: httpx.AsyncClient, world: World, owner_conn: psycopg.Connection,
        signing_keys: dict[str, Any]) -> None:
    admin = world.tokens["admin"]
    fake = FakeGateway({"AG-10": ag10}, embedder=bag)
    stack.app.state.gateway = fake.client()  # type: ignore[attr-defined]
    resp = await stack.post(f"{GOLD}/evaluate", headers=h(admin), json={"ask_mode": "direct_sql"})
    assert resp.status_code == 202, resp.text
    run_id = resp.json()["run_id"]
    assert (await run_queued(stack, world, signing_keys, run_id))["status"] == "done"
    run = (await stack.get(f"/api/v1/knowledge/eval-runs/{run_id}", headers=h(admin))).json()
    assert run["ask_mode"] == "direct_sql" and run["planner"] == {"direct_sql": 3}
    by_q = {r["question"]: r for r in run["results"]}
    for right in ("How many projects are open?", "Which projects are open?"):
        assert by_q[right]["passed"] and by_q[right]["exec_match"] and not by_q[right]["confident_wrong"], {k: by_q[right][k] for k in ("diff", "error", "shape", "status", "exec_match")}
    wrong = by_q["Open projects by region"]
    assert not wrong["passed"] and wrong["confident_wrong"] and wrong["status"] == "ok"
    assert wrong["diff"][0]["path"] == "result" and "GROUP BY region" in wrong["error"]
    assert run["passed"] == 2 and run["total"] == 3
    assert len([c for c in fake.calls if c["agent"] == "AG-10"]) == 3
    # The source is still answered in its own mode; the run is Direct SQL's score.
    mode = owner_conn.execute("SELECT ask_mode FROM conn.connections WHERE id = %s", (world.connection_id,))
    assert mode.fetchone()[0] == "certified"
    got = (await scores(stack, world))["direct_sql"]
    assert got["run_id"] == run_id and got["total"] == 3 and got["right"] == 2 and got["confident_wrong"] == 1
    # The query log keeps gold runs out of Ask's figures.
    surfaces = owner_conn.execute("SELECT DISTINCT surface FROM ops.query_executions WHERE tenant_id = %s",
                                  (world.tenant_id,)).fetchall()
    assert ("evaluation",) in surfaces and ("ask",) not in surfaces


async def test_a_direct_sql_run_cannot_be_plan_only(stack: httpx.AsyncClient, world: World) -> None:
    resp = await stack.post(f"{GOLD}/evaluate", headers=h(world.tokens["admin"]),
                            json={"ask_mode": "direct_sql", "plan_only": True})
    assert resp.status_code == 400 and "plan-only" in resp.json()["error"]["message"]
    resp = await stack.post(f"{GOLD}/evaluate", headers=h(world.tokens["admin"]), json={"ask_mode": "guess"})
    assert resp.status_code in (400, 422)


async def test_without_a_mode_the_run_follows_the_source(stack: httpx.AsyncClient, world: World,
                                                         owner_conn: psycopg.Connection,
                                                         signing_keys: dict[str, Any]) -> None:
    owner_conn.execute("UPDATE conn.connections SET ask_mode = 'direct_sql' WHERE id = %s", (world.connection_id,))
    stack.app.state.gateway = FakeGateway({"AG-10": ag10}, embedder=bag).client()  # type: ignore[attr-defined]
    admin = world.tokens["admin"]
    run_id = (await stack.post(f"{GOLD}/evaluate", headers=h(admin), json={})).json()["run_id"]
    assert owner_conn.execute("SELECT ask_mode FROM rag.eval_runs WHERE id = %s", (run_id,)).fetchone()[0] is None
    assert (await run_queued(stack, world, signing_keys, run_id))["status"] == "done"
    run = (await stack.get(f"/api/v1/knowledge/eval-runs/{run_id}", headers=h(admin))).json()
    assert run["ask_mode"] == "direct_sql" and run["passed"] == 2  # the source's own mode, stamped
    # A certified run in the same place stays Certified's score; both are shown.
    stack.app.state.gateway = FakeGateway(embedder=bag).client()  # type: ignore[attr-defined]
    run2 = (await stack.post(f"{GOLD}/evaluate", headers=h(admin), json={"ask_mode": "certified"})).json()["run_id"]
    assert (await run_queued(stack, world, signing_keys, run2))["status"] == "done"
    got = await scores(stack, world)
    assert got["certified"]["run_id"] == run2 and got["direct_sql"]["run_id"] == run_id
