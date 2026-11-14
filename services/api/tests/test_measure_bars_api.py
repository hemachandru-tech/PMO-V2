"""One row of several measures as ranked bars, and pin offers that keep every measure (EXEC-173).

Acceptance: "internal, external, contingency, hardware and software cost … as a bar" answers as ranked bars (one bar
per measure, the browser draws them) and pins as ranked bars; the pin options offer no one-measure chart "by" a
breakdown for it, only a table by a breakdown; a one-measure answer keeps its fixes.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx
import psycopg
import pytest
from api_testkit import needs_db
from pmo_testkit import World, h, setup_world
from test_ask_api import ask, by_event
from test_authoring_api import new_report, pin

pytestmark = [needs_db, pytest.mark.integration]

COSTS = "Total internal, external, contingency, hardware and software cost of CAPEX projects, as a bar."


@pytest.fixture
async def world(stack: httpx.AsyncClient, owner_conn: psycopg.Connection, make_token: Callable[..., str],
                tpg_source: dict[str, Any]) -> World:
    return await setup_world(stack, owner_conn, make_token, tpg_source)


async def test_five_costs_are_bars_and_pin_as_bars(stack: httpx.AsyncClient, world: World) -> None:
    token = world.tokens["creator"]
    turn_id, events = await ask(stack, token, COSTS)
    ev = by_event(events)
    spec = ev["complete"]["spec"]
    assert len(spec["extra_measures"]) == 4
    assert ev["viz"]["visual"] == "ranked_bar" and ev["viz"]["note"] is None and ev["viz"]["chart_spec"]["x"] is None
    assert len(ev["viz"]["chart_spec"]["series"]) == 5

    options = (await stack.post("/api/v1/visuals/suitable", headers=h(token), json={"spec": spec})).json()
    assert "ranked_bar" in options["suitable"] and options["recommended"] == "table"
    fixes: dict[str, Any] = options["fixes"]
    assert set(fixes) <= {"table", "trend"} and "table" in fixes
    assert [op["op"] for op in fixes["table"]["ops"]] == ["add_breakdown"]

    report = await new_report(stack, token, "Costs")
    ok = await pin(stack, token, turn_id, "CAPEX costs", "ranked_bar", report["id"])
    assert ok.status_code == 201, ok.text


async def test_a_single_measure_keeps_its_fixes(stack: httpx.AsyncClient, world: World) -> None:
    token = world.tokens["creator"]
    _, events = await ask(stack, token, "How many open projects?")
    spec = by_event(events)["complete"]["spec"]
    fixes = (await stack.post("/api/v1/visuals/suitable", headers=h(token), json={"spec": spec})).json()["fixes"]
    assert {"ranked_bar", "table", "donut"} <= set(fixes)
