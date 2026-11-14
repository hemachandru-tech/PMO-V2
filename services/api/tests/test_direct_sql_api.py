"""Direct SQL in Ask (ENH-028 US-257a; DEC-30), live on the TPG fixture, not yet routed from Ask (US-257b).

Acceptance: AG-10's SQL runs through the Custom SQL pipeline under the person's row scope (a scoped viewer sees only
their rows); a write statement and a hidden field are refused at once, never repaired, and audited as security
events; any other failed check, the frame's shape included, gets one repair call, then a refusal; a source that is not
SQL is refused before any model call; approved named conditions reach AG-10 as SQL; every answer carries the Custom
SQL badge and its SQL and is not publishable until certified.
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
from test_knowledge_api import index_model
from test_knowledge_definitions_api import OPEN_STATES

from tela_api.ask import direct_sql
from tela_api.identity.principal import Principal

pytestmark = [needs_db, pytest.mark.integration]
NOMSA = "Nomsa Osei"  # the CIO viewer: own CIO's projects only


@pytest.fixture
async def world(stack: httpx.AsyncClient, owner_conn: psycopg.Connection, make_token: Callable[..., str],
                tpg_source: dict[str, Any]) -> World:
    return await setup_world(stack, owner_conn, make_token, tpg_source)


async def principal(api: httpx.AsyncClient, token: str) -> Principal:
    state = api.app.state  # type: ignore[attr-defined]
    claims = await state.jwt_verifier.verify(token)
    p: Principal = await state.principal_resolver.resolve(claims, request_id=None, client_ip=None)
    return p


async def answer(api: httpx.AsyncClient, world: World, token: str, question: str,
                 reply: Any) -> tuple[direct_sql.DirectAnswer, list[dict[str, Any]]]:
    fake = FakeGateway({"AG-10": reply})
    state = api.app.state  # type: ignore[attr-defined]
    got = await direct_sql.answer(state.query_service, fake.client(), await principal(api, token),
                                  connection_id=world.connection_id, question=question)
    return got, [c for c in fake.calls if c["agent"] == "AG-10"]


def sql(text: str) -> dict[str, str]:
    return {"sql": text, "explanation": "One sentence."}


async def audit(api: httpx.AsyncClient, world: World, action: str, **params: Any) -> list[dict[str, Any]]:
    resp = await api.get("/api/v1/admin/audit", headers=h(world.tokens["admin"]), params={"action": action, **params})
    assert resp.status_code == 200, resp.text
    items: list[dict[str, Any]] = resp.json()["items"]
    return items


async def test_a_scoped_viewer_gets_only_their_rows_with_the_badge_and_the_sql(
        stack: httpx.AsyncClient, world: World) -> None:
    statement = "SELECT name, region FROM projects WHERE status = 'Active'"
    got, calls = await answer(stack, world, world.tokens["nomsa"], "list of active projects", sql(statement))
    assert got.status == "ok" and got.shape == "list_of_records" and not got.repaired, got
    mine = [p for p in oracle.PROJECTS if p["status"] == "Active" and p["cio"] == NOMSA]
    assert got.result is not None and got.result["row_count"] == len(mine) < 65  # scope injected, not all 65
    assert sorted(r[0] for r in got.result["rows"]) == sorted(p["name"] for p in mine)
    out = got.out()
    assert out["badge"] == "custom_sql" and out["sql"] == statement and out["certified"] is False
    assert out["publishable"] is False and out["scope"]["locked"]
    assert [c["name"] for c in out["checks"]][-3:] == ["scope_applied", "limits", "answer_shape"]
    [call] = calls
    system = call["messages"][0]["content"]
    assert call["task"] == "sql_draft" and "Answer with a list of records" in system and "no :page_filters" in system
    [event] = await audit(stack, world, "custom_sql.asked")
    assert event["details"]["surface"] == "ask_direct_sql" and event["details"]["row_count"] == len(mine)


async def test_approved_named_conditions_reach_ag10_as_sql(stack: httpx.AsyncClient, world: World,
                                                           signing_keys: dict[str, Any]) -> None:
    await index_model(stack, world, signing_keys)  # the certified count measures' conditions, approved (US-252)
    statement = "SELECT COUNT(*) AS n FROM projects WHERE current_state IN ('Project Created', 'Active', 'On Hold')"
    got, [call] = await answer(stack, world, world.tokens["admin"], "How many open projects are there?",
                               sql(statement))
    assert got.status == "ok" and got.shape == "number"
    open_ = next(c for c in got.conditions if c["term"] == "Open projects")
    # Pack 2.2 (DEC-24): open = current state.
    assert open_["sql"] == "public.projects.current_state IN ('Project Created', 'Active', 'On Hold')", open_
    assert f"Agreed terms (use these conditions exactly):\n- Open projects: WHERE {open_['sql']}" in \
        call["messages"][0]["content"]
    assert got.result is not None and got.result["rows"] == [[sum(p["current_state"] in OPEN_STATES
                                                                  for p in oracle.PROJECTS)]]


async def test_a_write_statement_is_refused_at_once_and_audited(stack: httpx.AsyncClient, world: World) -> None:
    got, calls = await answer(stack, world, world.tokens["nomsa"], "how many projects",
                              sql("DELETE FROM projects"))
    assert got.status == "refused" and got.reason == "check_failed" and not got.repaired
    assert len(calls) == 1 and got.result is None and got.sql == "DELETE FROM projects"  # shown, never run
    assert got.checks[-1] == {**got.checks[-1], "ok": False, "code": "sql_not_select"}
    [event] = await audit(stack, world, "custom_sql.rejected", security="true")
    assert event["details"]["surface"] == "ask_direct_sql" and event["details"]["code"] == "sql_not_select"
    assert len(oracle.PROJECTS) == 100  # nothing was deleted (the source login is read-only too)


async def test_a_hidden_field_is_refused_and_never_offered(stack: httpx.AsyncClient, world: World) -> None:
    await stack.put("/api/v1/admin/field-policies", headers=h(world.tokens["admin"]), json={
        "connection_id": world.connection_id,
        "rules": [{"field_ref": "field:projects.approved_budget_value_usd", "hidden_for": ["viewer"]}]})
    got, calls = await answer(stack, world, world.tokens["nomsa"], "total approved budget",
                              sql("SELECT SUM(approved_budget_value_usd) AS b FROM projects"))
    assert got.status == "refused" and len(calls) == 1 and got.checks[-1]["code"] == "sql_field_hidden"
    projects = next(ln for ln in calls[0]["messages"][0]["content"].splitlines() if ln.startswith("- public.projects:"))
    assert "approved_budget_value_usd" not in projects  # left out of the schema AG-10 sees
    assert (await audit(stack, world, "custom_sql.rejected", security="true"))[0]["details"]["code"] == \
        "sql_field_hidden"


async def test_a_failed_check_gets_one_repair(stack: httpx.AsyncClient, world: World) -> None:
    # 01a1ca5c: a count for a list fails the shape check; the repair gives one row per record.
    replies = iter([sql("SELECT COUNT(*) AS n FROM projects WHERE status = 'Active'"),
                    sql("SELECT name FROM projects WHERE status = 'Active'")])
    got, calls = await answer(stack, world, world.tokens["admin"], "Show the List of open Projects",
                              lambda task, messages: next(replies))
    assert got.status == "ok" and got.repaired and len(calls) == 2
    assert got.result is not None and got.result["row_count"] == sum(p["status"] == "Active" for p in oracle.PROJECTS)
    repair = calls[1]["messages"][-1]["content"]
    assert "Your previous SQL:\nSELECT COUNT(*)" in repair and "failed the check (answer_shape)" in repair
    assert [e["details"]["attempt"] for e in await audit(stack, world, "custom_sql.rejected")] == [1]


async def test_a_second_failure_is_refused(stack: httpx.AsyncClient, world: World) -> None:
    got, calls = await answer(stack, world, world.tokens["admin"], "how many projects",
                              sql("SELECT COUNT(*) AS n FROM projects WHERE no_such_column = 1"))
    assert got.status == "refused" and got.reason == "check_failed" and got.repaired and len(calls) == 2
    assert "failed the check" in (got.message or "") and got.result is None
    assert [e["details"]["attempt"] for e in await audit(stack, world, "custom_sql.rejected")] == [2, 1]


async def test_a_source_that_is_not_sql_is_refused_before_any_call(
        stack: httpx.AsyncClient, world: World, owner_conn: psycopg.Connection) -> None:
    owner_conn.execute("UPDATE conn.connections SET engine = 'mongodb' WHERE id = %s", (world.connection_id,))
    got, calls = await answer(stack, world, world.tokens["admin"], "how many projects", sql("SELECT 1"))
    assert got.status == "refused" and got.reason == "engine" and calls == []
    assert got.out()["badge"] == "custom_sql"


async def test_values_the_data_doesnt_hold_go_back_once_with_the_real_ones(stack: httpx.AsyncClient,
                                                                           world: World) -> None:
    """Run 01a1ce59: a filter on a value that isn't there answered "No rows match" as if that were the answer."""
    replies = iter([sql("SELECT name FROM projects WHERE status = 'Open'"),
                    sql("SELECT name FROM projects WHERE status = 'Active'")])
    got, calls = await answer(stack, world, world.tokens["admin"], "list of open projects",
                              lambda task, messages: next(replies))
    assert got.status == "ok" and got.repaired and len(calls) == 2 and not got.value_notes, got
    assert got.sql == "SELECT name FROM projects WHERE status = 'Active'"
    assert got.result is not None and got.result["row_count"] == 65
    repair = calls[1]["messages"][-1]["content"]
    assert "returned no rows, because the data doesn't hold these filter values:\n- status has no 'Open'" in repair
    assert "'Active'" in repair and "'Inactive'" in repair  # the column's real values


async def test_a_failed_value_repair_keeps_the_empty_answer_and_says_why(stack: httpx.AsyncClient,
                                                                         world: World) -> None:
    got, calls = await answer(stack, world, world.tokens["admin"], "list of open projects",
                              sql("SELECT name FROM projects WHERE status IN ('Open', 'Active') AND region = 'Mars'"))
    assert got.status == "ok" and len(calls) == 2 and got.result is not None and got.result["row_count"] == 0
    status, region = got.value_notes  # 'Active' is held; 'Open' and 'Mars' are not
    assert status.startswith("status has no 'Open'; its values include ") and "'Active'" in status
    assert region.startswith("region has no 'Mars'; its values include ")
    assert got.out()["value_notes"] == got.value_notes


async def test_the_value_lookup_sees_only_the_persons_rows(stack: httpx.AsyncClient, world: World) -> None:
    other = "Karim Haddad"  # a CIO whose projects the CIO viewer can't see
    got, calls = await answer(stack, world, world.tokens["nomsa"], "list of Karim's projects",
                              sql(f"SELECT name FROM projects WHERE cio = '{other}'"))
    assert got.result is not None and got.result["row_count"] == 0
    assert calls[1]["messages"][-1]["content"].count("cio has no 'Karim Haddad'; its values include 'Nomsa Osei'.") == 1
    assert got.value_notes == ["cio has no 'Karim Haddad'; its values include 'Nomsa Osei'."]
