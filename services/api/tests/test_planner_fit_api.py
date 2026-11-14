"""Planner model fit in Ask and AI settings (US-191; DEC-23: Local and Hosted planning both offered).

Acceptance: a local Ollama plans one sample (it runs samples one after another), a hosted provider votes on three and a
failed sample is not retried; AI settings show the option with its recommended and minimum model, and warn when the
planner model fails the schema check too often (from ``ops.llm_invocations``).
"""

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
from test_confidence_api import BY_REGION, MISS, plan_calls, samples

pytestmark = [needs_db, pytest.mark.integration]


@pytest.fixture
async def world(stack: httpx.AsyncClient, owner_conn: psycopg.Connection, make_token: Callable[..., str],
                tpg_source: dict[str, Any]) -> World:
    return await setup_world(stack, owner_conn, make_token, tpg_source)


def choose_provider(conn: psycopg.Connection, w: World, provider: str, planner: str) -> None:
    conn.execute("INSERT INTO ops.llm_settings (tenant_id, provider, models) VALUES (%s, %s, %s)",
                 (w.tenant_id, provider, json.dumps({"planner": planner})))


async def test_a_local_planner_plans_one_sample(stack: httpx.AsyncClient, owner_conn: psycopg.Connection,
                                                world: World) -> None:
    choose_provider(owner_conn, world, "ollama", "qwen3:8b")
    fake = FakeGateway({"AG-03": samples({**BY_REGION, "confidence": 0.9})})
    stack.app.state.gateway = fake.client()  # type: ignore[attr-defined]
    _, events = await ask(stack, world.tokens["admin"], MISS)
    ev = by_event(events)
    assert ev["plan"]["planner"] == "llm"
    calls = plan_calls(fake)
    assert len(calls) == 1 and calls[0]["temperature"] is None and calls[0]["json_schema"]
    assert ev["complete"]["confidence"]["signals"]["samples"] == 1


async def test_the_deployment_default_counts_when_the_tenant_chose_none(stack: httpx.AsyncClient,
                                                                       world: World) -> None:
    fake = FakeGateway({"AG-03": samples(BY_REGION)}, default_provider="ollama")
    stack.app.state.gateway = fake.client()  # type: ignore[attr-defined]
    await ask(stack, world.tokens["admin"], MISS)
    assert len(plan_calls(fake)) == 1


async def test_a_hosted_planner_votes_and_a_failed_sample_is_not_retried(
        stack: httpx.AsyncClient, owner_conn: psycopg.Connection, world: World) -> None:
    choose_provider(owner_conn, world, "anthropic", "claude-sonnet-5-5")
    fake = FakeGateway({"AG-03": samples({"__invalid__": "{}"}, BY_REGION)})
    stack.app.state.gateway = fake.client()  # type: ignore[attr-defined]
    _, events = await ask(stack, world.tokens["admin"], MISS)
    ev = by_event(events)
    assert ev["plan"]["planner"] == "llm" and ev["plan"]["spec"]["breakdowns"] == [{"ref": "dimension:region"}]
    assert len(plan_calls(fake)) == 3  # three samples, the failed one not sent back
    assert ev["complete"]["confidence"]["signals"]["samples"] == 2


def ledger(conn: psycopg.Connection, w: World, model: str, statuses: list[str], provider: str = "ollama") -> None:
    for status in statuses:
        conn.execute("INSERT INTO ops.llm_invocations (id, tenant_id, agent, task, provider, model, payload_hash, "
                     "scanner_verdict, status) VALUES (gen_random_uuid(), %s, 'AG-03', 'plan', %s, %s, 'h', 'clean', "
                     "%s)", (w.tenant_id, provider, model, status))


async def test_settings_warn_when_the_planner_model_fails_the_schema_too_often(
        stack: httpx.AsyncClient, owner_conn: psycopg.Connection, world: World) -> None:
    admin = world.tokens["admin"]
    stack.app.state.gateway = FakeGateway().client()  # type: ignore[attr-defined]
    off = (await stack.get("/api/v1/admin/llm-settings", headers=h(admin))).json()["planner_health"]
    assert off["option"] is None and off["warn"] is False and off["recommended"] is None

    choose_provider(owner_conn, world, "ollama", "llama3.2:3b")
    ledger(owner_conn, world, "llama3.2:3b", ["ok"] * 6 + ["invalid_output"] * 3 + ["timeout", "blocked"])
    ledger(owner_conn, world, "qwen3:8b", ["invalid_output"] * 20)  # another model's calls don't count
    got = (await stack.get("/api/v1/admin/llm-settings", headers=h(admin))).json()["planner_health"]
    assert got["option"] == "local" and got["model"] == "llama3.2:3b"
    assert got["calls"] == 10 and got["schema_failures"] == 3 and got["timeouts"] == 1
    assert got["failure_rate"] == 0.3 and got["warn"] is True
    assert got["recommended"] == "qwen3:8b" and got["minimum"]

    owner_conn.execute("UPDATE ops.llm_settings SET models = %s", (json.dumps({"planner": "qwen3:8b"}),))
    owner_conn.execute("DELETE FROM ops.llm_invocations WHERE model = 'qwen3:8b'")
    ledger(owner_conn, world, "qwen3:8b", ["ok"] * 9 + ["invalid_output"])
    fine = (await stack.get("/api/v1/admin/llm-settings", headers=h(admin))).json()["planner_health"]
    assert fine["failure_rate"] == 0.1 and fine["warn"] is False

    owner_conn.execute("UPDATE ops.llm_settings SET provider = 'anthropic', models = %s",
                       (json.dumps({"planner": "claude-sonnet-5-5"}),))
    ledger(owner_conn, world, "claude-sonnet-5-5", ["invalid_output"] * 2, provider="anthropic")
    few = (await stack.get("/api/v1/admin/llm-settings", headers=h(admin))).json()["planner_health"]
    assert few["option"] == "hosted" and few["failure_rate"] == 1.0
    assert few["warn"] is False  # too few calls to judge
