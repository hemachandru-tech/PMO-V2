"""Admin console overview (US-143; FEAT-056): counts across data, governance and the platform in one call."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx
import psycopg
import pytest
from api_testkit import add_connection, needs_db
from pmo_testkit import World, h, setup_world

from tela_common.ids import new_id

pytestmark = [needs_db, pytest.mark.integration]


@pytest.fixture
async def world(stack: httpx.AsyncClient, owner_conn: psycopg.Connection, make_token: Callable[..., str],
                tpg_source: dict[str, Any]) -> World:
    return await setup_world(stack, owner_conn, make_token, tpg_source, policies=False)


async def overview(api: httpx.AsyncClient, token: str, **params: str) -> dict[str, Any]:
    resp = await api.get("/api/v1/admin/overview", headers=h(token), params=params)
    assert resp.status_code == 200, resp.text
    return dict(resp.json())


async def test_admin_overview_counts(stack: httpx.AsyncClient, world: World, owner_conn: psycopg.Connection) -> None:
    admin = world.tokens["admin"]
    o = await overview(stack, admin, tz="Asia/Kolkata")
    assert o["tz"] == "Asia/Kolkata"
    assert o["connections"]["total"] == 1 and o["connections"]["healthy"] == 1 and not o["connections"]["attention"]
    assert o["models"]["total"] == 1 and o["models"]["items"][0]["version_no"] == 1
    assert o["certification"]["pending"] == 0 and o["publish_requests"]["open"] == 0
    assert len(o["ai"]["daily_calls"]) == 14
    intro = next(j for j in o["jobs"]["items"] if j["key"] == "introspection")
    assert intro["runs"] >= 1 and intro["failed"] == 0 and o["jobs"]["failed"] == 0
    baseline_calls = o["ai"]["today"]["calls"]

    # Something needs attention in each area.
    broken = add_connection(owner_conn, world.tenant_id, "Dataverse sandbox")
    owner_conn.execute("UPDATE conn.connections SET status = 'error' WHERE id = %s", (broken,))
    owner_conn.execute(
        "INSERT INTO ops.llm_invocations (id, tenant_id, agent, task, provider, model, payload_hash, scanner_verdict, "
        "prompt_tokens, completion_tokens, status) VALUES (%s, %s, 'AG-01', 'ask', 'ollama', 'llama3.2:3b', 'h', "
        "'blocked', 0, 0, 'blocked')", (new_id(), world.tenant_id))
    owner_conn.execute(
        "INSERT INTO ops.audit_events (tenant_id, action, object_type, outcome, severity, correlation_id) "
        "VALUES (%s, 'custom_sql.rejected', 'connection', 'deny', 'security', 'r1')", (world.tenant_id,))
    owner_conn.execute(
        "INSERT INTO ops.audit_events (tenant_id, action, object_type, object_id, outcome, correlation_id) "
        "VALUES (%s, 'connection.introspected', 'connection', %s, 'failure', 'r2')", (world.tenant_id, broken))
    await stack.get("/api/v1/me", headers=h(world.tokens["creator"]))  # signs the Creator in
    dash = new_id()
    owner_conn.execute("INSERT INTO content.dashboards (id, tenant_id, title, kind, status, connection_id, "
                       "model_version_id, owner_id) SELECT %s, %s, 'Ops', 'user', 'draft', %s, %s, id FROM iam.users "
                       "WHERE display_name = 'Cleo Creator'", (dash, world.tenant_id, world.connection_id,
                                                               world.version_id))
    owner_conn.execute("INSERT INTO content.publish_requests (id, tenant_id, dashboard_id, requested_by) SELECT %s, %s, "
                       "%s, id FROM iam.users WHERE display_name = 'Cleo Creator'", (new_id(), world.tenant_id, dash))

    o = await overview(stack, admin)
    assert o["connections"]["total"] == 2 and o["connections"]["healthy"] == 1
    assert o["connections"]["attention"] == [{"id": broken, "name": "Dataverse sandbox", "status": "error"}]
    assert o["ai"]["today"]["calls"] == baseline_calls + 1 and o["ai"]["today"]["blocked"] >= 1
    assert o["security"]["total"] == 1 and o["security"]["by_action"] == {"custom_sql.rejected": 1}
    assert o["security"]["latest"]["action"] == "custom_sql.rejected"
    intro = next(j for j in o["jobs"]["items"] if j["key"] == "introspection")
    assert intro["failed"] == 1 and intro["last_failure"]["label"] == "Dataverse sandbox" and o["jobs"]["failed"] == 1
    assert o["publish_requests"]["open"] == 1 and o["publish_requests"]["oldest_by"] == "Cleo Creator"

    # Only Admin permissions see it; a bad time zone is refused.
    for who in ("creator", "viewer"):
        resp = await stack.get("/api/v1/admin/overview", headers=h(world.tokens[who]))
        assert resp.status_code == 403
    bad = await stack.get("/api/v1/admin/overview", headers=h(admin), params={"tz": "Mars/Olympus"})
    assert bad.status_code == 400 and bad.json()["error"]["code"] == "validation_error"
