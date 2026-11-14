"""Window operations through Ask, end to end (US-177; FEAT-063; ENH-012).

The rules planner turns the question into a window spec, the API scopes and runs it on the live source, and the
answer is checked against the independent oracle (``pmo_oracle``) for the Creator's scope (Gulf regions, RULE-004).
"""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal
from typing import Any

import httpx
import pmo_oracle as oracle
import psycopg
import pytest
from api_testkit import needs_db
from pmo_testkit import World, setup_world
from test_ask_api import ask, by_event

pytestmark = [needs_db, pytest.mark.integration]

CREATOR_REGIONS = {"KSA", "GCC - UAE"}


@pytest.fixture
async def world(stack: httpx.AsyncClient, owner_conn: psycopg.Connection, make_token: Callable[..., str],
                tpg_source: dict[str, Any]) -> World:
    return await setup_world(stack, owner_conn, make_token, tpg_source)


def _answer(events: list[Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    ev = by_event(events)
    assert "error" not in ev, ev.get("error")
    done = ev["complete"]
    names = [c["name"] for c in done["columns"]]
    return ev, [dict(zip(names, r, strict=True)) for r in done["rows"]]


def _in_scope() -> list[dict[str, Any]]:
    return [p for p in oracle.PROJECTS if p["region"] in CREATOR_REGIONS]


async def test_top_3_projects_per_programme(stack: httpx.AsyncClient, world: World) -> None:
    _, events = await ask(stack, world.tokens["creator"], "top 3 projects per programme by forecast cost")
    ev, rows = _answer(events)
    assert ev["plan"]["planner"] == "rules"
    assert ev["plan"]["spec"]["top_n_per_group"] == {"by": "dimension:programme", "n": 3, "order": "desc"}
    # Oracle: forecast (EAC) per project over comparable open projects (RULE-037), top 3 in each programme.
    totals: dict[tuple[str, str], Decimal | None] = {}
    for p in _in_scope():
        key = (p["program"], p["name"])
        cost = p["cost_total_usd"] if oracle._comparable(p) else None
        if cost is not None:
            totals[key] = (totals.get(key) or Decimal(0)) + cost
        else:
            totals.setdefault(key, None)
    want: dict[str, list[float | None]] = {}
    for (prog, _), v in totals.items():
        want.setdefault(prog, []).append(None if v is None else float(v))
    for prog, vals in want.items():
        want[prog] = sorted(vals, key=lambda x: (x is None, -(x or 0)))[:3]
    got: dict[str, list[float | None]] = {}
    for r in rows:
        v = r["forecast_cost_total"]
        got.setdefault(r["programme"], []).append(None if v is None else float(v))
    assert set(got) == set(want)
    assert got == want
    assert len(rows) == sum(len(v) for v in want.values()) < len(totals)  # the cut is per programme
    assert all("projects__name" in r for r in rows)


async def test_running_total_of_open_projects_by_month(stack: httpx.AsyncClient, world: World) -> None:
    _, events = await ask(stack, world.tokens["creator"], "running total of open projects by month")
    ev, rows = _answer(events)
    assert ev["plan"]["planner"] == "rules" and ev["plan"]["spec"]["running_total"] == {}
    assert ev["viz"]["visual"] in ("trend", "line", "columns")
    period = next(c["name"] for c in by_event(events)["complete"]["columns"] if c["kind"] == "period")
    # Oracle: open projects (RULE-026: status Active) per planned deployment month, accumulated in month order.
    per_month: dict[str | None, int] = {}
    for p in _in_scope():
        d = p["planned_deployment_date"]
        month = d.replace(day=1).isoformat() if d else None
        per_month[month] = per_month.get(month, 0) + (1 if p["status"] == "Active" else 0)
    got = {(str(r[period])[:10] if r[period] else None): (r["open_projects"], r["open_projects__cumsum"])
           for r in rows}
    assert {k: v[0] for k, v in got.items()} == per_month
    running = 0
    for month in sorted(per_month, key=lambda m: (m is None, m or "")):  # empty dates come last on every engine
        running += per_month[month]
        assert got[month][1] == running, month
    rt = next(c for c in by_event(events)["complete"]["columns"] if c["name"] == "open_projects__cumsum")
    assert rt["of"] == "open_projects" and "running total" in rt["label"]
