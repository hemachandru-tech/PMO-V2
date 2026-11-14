"""Ad-hoc rules through the governed read path (ENH-008, US-163; FEAT-060).

A free-hand rule runs only with a live ``ask.freehand`` grant on that connection, under the person's scope and
field policies like any certified measure, is labelled not certified, marks the grant used and is audited as
``query.freehand`` (refusals too). Expected numbers come from the synthetic extract (``pmo_oracle.PROJECTS``).
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from typing import Any

import httpx
import pmo_oracle as oracle
import psycopg
import pytest
from api_testkit import needs_db
from pmo_testkit import World, h, run, setup_world, value

pytestmark = [needs_db, pytest.mark.integration]

GRANTS = "/api/v1/admin/permission-grants"
# At least one stage date is set, and the planned deployment date is blank or before today (the ENH-008 question).
PAST_PLAN = {"all": [
    {"any": [{"field": "actual_project_start_date", "op": "not_null"}, {"field": "on_hold_date", "op": "not_null"},
             {"field": "first_baselined_deployment_date", "op": "not_null"}]},
    {"any": [{"field": "planned_deployment_date", "op": "is_null"},
             {"field": "planned_deployment_date", "op": "<", "value": {"today": True}}]},
]}


def _past_plan(p: dict[str, Any], today: date) -> bool:
    started = any(p[k] is not None for k in ("actual_project_start_date", "on_hold_date",
                                             "first_baselined_deployment_date"))
    return started and (p["planned_deployment_date"] is None or p["planned_deployment_date"] < today)


def _expected(cio: str | None = None) -> int:
    today = datetime.now(UTC).date()  # test users have no time zone: UTC
    return sum(1 for p in oracle.PROJECTS if _past_plan(p, today) and (cio is None or p["cio"] == cio))


@pytest.fixture
async def world(stack: httpx.AsyncClient, owner_conn: psycopg.Connection, make_token: Callable[..., str],
                tpg_source: dict[str, Any]) -> World:
    w = await setup_world(stack, owner_conn, make_token, tpg_source)
    for persona in ("nomsa", "karim", "admin"):
        me = await stack.get("/api/v1/me", headers=h(w.tokens[persona]))
        assert me.status_code == 200, me.text
        w.extra[f"{persona}_id"] = me.json()["user_id"]
    return w


def _adhoc(w: World, where: Any = PAST_PLAN, **kw: Any) -> dict[str, Any]:
    adhoc = {"entity": "projects", "agg": "count", "label": "Past the Plan stage (free-hand)",
             "plain_rule": "Projects with a stage date set whose planned deployment date is blank or before today",
             "where": where, **kw}
    return {"model_version_id": w.version_id, "connection_id": w.connection_id, "measure": {"adhoc": adhoc}}


async def _grant(stack: httpx.AsyncClient, w: World, persona: str, *, seconds: float = 90 * 86400) -> str:
    resp = await stack.post(GRANTS, headers=h(w.tokens["admin"]), json={
        "user_id": w.extra[f"{persona}_id"], "connection_id": w.connection_id, "reason": "Plan stage review",
        "expires_at": (datetime.now(UTC) + timedelta(seconds=seconds)).isoformat()})
    assert resp.status_code == 201, resp.text
    return str(resp.json()["id"])


def _audits(owner: psycopg.Connection) -> list[tuple[str, str]]:
    return [(r[0], r[1]) for r in owner.execute(
        "SELECT outcome, u.display_name FROM ops.audit_events a JOIN iam.users u ON u.id = a.actor_id "
        "WHERE a.action = 'query.freehand' ORDER BY a.occurred_at")]


async def test_no_grant_is_refused_and_audited(stack: httpx.AsyncClient, world: World,
                                               owner_conn: psycopg.Connection) -> None:
    resp = await run(stack, world.tokens["nomsa"], _adhoc(world))
    assert resp.status_code == 403, resp.text
    assert resp.json()["error"]["code"] == "forbidden"
    assert "request free-hand access" in resp.json()["error"]["message"]
    assert _audits(owner_conn) == [("deny", "Nomsa Osei")]
    # Nothing reached the source.
    assert owner_conn.execute("SELECT count(*) FROM ops.query_executions WHERE user_id = %s",
                              (world.extra["nomsa_id"],)).fetchone() == (0,)
    # An Admin has no free-hand access by role either.
    assert (await run(stack, world.tokens["admin"], _adhoc(world))).status_code == 403


async def test_a_grant_runs_the_rule_in_scope_not_certified(stack: httpx.AsyncClient, world: World,
                                                            owner_conn: psycopg.Connection) -> None:
    await _grant(stack, world, "nomsa")
    resp = await run(stack, world.tokens["nomsa"], _adhoc(world))
    body = resp.json()
    assert value(resp) == _expected(cio="Nomsa Osei")
    assert body["certified"] is False and body["badge"] == "not_certified"
    assert body["definitions"][0]["ref"] == "measure:_adhoc"
    assert body["definitions"][0]["plain_rule"].startswith("Projects with a stage date set")
    assert body["scope"]["locked"] is True
    # The grant is per connection and per person: Karim has none.
    assert (await run(stack, world.tokens["karim"], _adhoc(world))).status_code == 403
    await _grant(stack, world, "karim")
    assert value(await run(stack, world.tokens["karim"], _adhoc(world))) == _expected(cio="Karim Haddad")

    events = owner_conn.execute(
        "SELECT outcome, details FROM ops.audit_events WHERE action = 'query.freehand' AND outcome = 'allow' "
        "ORDER BY occurred_at").fetchall()
    assert len(events) == 2
    assert events[0][1]["label"] == "Past the Plan stage (free-hand)" and events[0][1]["rule"]["all"][1]["any"][1]["value"] == {"today": True, "offset_days": 0}
    used = owner_conn.execute("SELECT last_used_at FROM iam.permission_grants WHERE user_id = %s",
                              (world.extra["nomsa_id"],)).fetchone()
    assert used is not None and used[0] is not None
    # Certified answers are unchanged and still say so.
    certified = await run(stack, world.tokens["nomsa"], world.spec("open_projects"))
    assert certified.json()["certified"] is True and certified.json()["badge"] == "certified"


async def test_removed_and_expired_grants_are_refused(stack: httpx.AsyncClient, world: World) -> None:
    grant = await _grant(stack, world, "nomsa")
    assert (await run(stack, world.tokens["nomsa"], _adhoc(world))).status_code == 200
    removed = await stack.request("DELETE", f"{GRANTS}/{grant}", headers=h(world.tokens["admin"]),
                                  json={"reason": "Review finished"})
    assert removed.status_code in (200, 204), removed.text
    assert (await run(stack, world.tokens["nomsa"], _adhoc(world))).status_code == 403

    await _grant(stack, world, "karim", seconds=2)
    assert (await run(stack, world.tokens["karim"], _adhoc(world))).status_code == 200
    await asyncio.sleep(2.5)  # the end date is checked at use, no job needed
    assert (await run(stack, world.tokens["karim"], _adhoc(world))).status_code == 403


async def test_hidden_fields_are_refused_in_a_rule(stack: httpx.AsyncClient, world: World) -> None:
    await _grant(stack, world, "nomsa")
    put = await stack.put("/api/v1/admin/field-policies", headers=h(world.tokens["admin"]), json={
        "connection_id": world.connection_id,
        "rules": [{"field_ref": "field:projects.approved_budget_value_usd", "hidden_for": ["cio"]}]})
    assert put.status_code == 200, put.text
    for spec in (_adhoc(world, {"field": "approved_budget_value_usd", "op": ">", "value": 1_000_000}),
                 _adhoc(world, None, agg="sum", field="approved_budget_value_usd")):
        resp = await run(stack, world.tokens["nomsa"], spec)
        assert resp.status_code == 403, resp.text
        assert "budget" not in resp.json()["error"]["message"].lower()


async def test_rules_are_values_never_sql(stack: httpx.AsyncClient, world: World) -> None:
    await _grant(stack, world, "nomsa")
    sneaky = "Nomsa Osei'; DROP TABLE projects; --"
    resp = await run(stack, world.tokens["nomsa"], _adhoc(world, {"field": "cio", "op": "=", "value": sneaky}))
    assert value(resp) == 0
    for bad in ({"field": "cio; DROP TABLE projects", "op": "not_null"},
                {"sql": "SELECT 1"},
                {"field": "salary", "op": ">", "value": 1}):
        resp = await run(stack, world.tokens["nomsa"], _adhoc(world, bad))
        assert resp.status_code == 422, resp.text
        assert "admin_detail" not in resp.json()["error"].get("details", {})
    # Still counted from the full extract afterwards.
    assert value(await run(stack, world.tokens["nomsa"], _adhoc(world))) == _expected(cio="Nomsa Osei")
