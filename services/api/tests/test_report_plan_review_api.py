"""AG-05 report plan reviewed by the LLM (ENH-026, US-243; DEC-28), with a scripted AG-05 on the TPG fixture.

Acceptance: an added widget on a certified measure is kept; an invented measure key is dropped with a note; the "Not
included" gaps are the rules'; the title passes the wording check; "Show the rules plan" restores the rules plan and
"Show the AI plan" brings the refinement back; plan editing in words gets the same review; review off returns today's
plan byte for byte and makes no AG-05 call.
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
from test_ask_review_api import review_on

from tela_api.content import plan_gold
from tela_api.core.settings import get_settings

pytestmark = [needs_db, pytest.mark.integration]


@pytest.fixture(autouse=True)
def ag05_reviewed(monkeypatch: pytest.MonkeyPatch) -> None:
    """AG-05 is out of review by default (EXEC-169); these tests put it back to keep the review covered."""
    monkeypatch.setattr(get_settings(), "llm_review_off_agents", [])


REQUEST = "Delivery report with overdue go-lives by region, and milestone slippage"


@pytest.fixture
async def world(stack: httpx.AsyncClient, owner_conn: psycopg.Connection, make_token: Callable[..., str],
                tpg_source: dict[str, Any]) -> World:
    return await setup_world(stack, owner_conn, make_token, tpg_source)


def planner(reply: Any) -> FakeGateway:
    """AG-05 replies ``reply`` to its review; any other agent gets nothing."""
    return FakeGateway({"AG-05": reply})


def calls(fake: FakeGateway) -> list[dict[str, Any]]:
    return [c for c in fake.calls if c["agent"] == "AG-05"]


async def plan(api: httpx.AsyncClient, token: str, request: str = REQUEST) -> dict[str, Any]:
    resp = await api.post("/api/v1/reports/plan", headers=h(token), json={"request": request})
    assert resp.status_code == 200, resp.text
    return dict(resp.json())


def without_id(p: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in p.items() if k != "plan_id"}


def refs(p: dict[str, Any]) -> dict[str, str]:
    return {w["title"]: w["ref"] for s in p["sections"] for w in s["widgets"]}


async def test_review_off_is_todays_plan(stack: httpx.AsyncClient, world: World,
                                         owner_conn: psycopg.Connection) -> None:
    token = world.tokens["creator"]
    stack.app.state.gateway = None  # type: ignore[attr-defined]
    today = await plan(stack, token)
    fake = planner({"verdict": "correct", "replacement": {"title": "Something else"}})
    stack.app.state.gateway = fake.client()  # type: ignore[attr-defined]
    review_on(owner_conn, world, on=False)
    off = await plan(stack, token)
    assert json.dumps(without_id(off)) == json.dumps(without_id(today)) and "review" not in off
    assert calls(fake) == []


async def test_refined_plan_keeps_certified_adds_and_drops_invented_keys(stack: httpx.AsyncClient, world: World,
                                                                         owner_conn: psycopg.Connection) -> None:
    token = world.tokens["creator"]
    stack.app.state.gateway = None  # type: ignore[attr-defined]
    rules = await plan(stack, token)
    by_title = refs(rules)
    named = next(s for s in rules["sections"] if s["key"] == "named")
    dropped_ref = named["widgets"][0]["ref"]

    review_on(owner_conn, world)
    fake = planner({"verdict": "correct", "reason": "the request is about delivery by region", "replacement": {
        "title": "Delivery and actual spend by region",
        "section_order": ["bp_delayed_projects", "named"],
        "widgets": [
            {"action": "keep", "ref": by_title["Overdue go-lives"]},
            {"action": "remove", "ref": dropped_ref},
            {"action": "add", "section": "named", "measure": "open_projects", "breakdowns": ["region"],
             "title": "Open projects by region"},
            {"action": "add", "section": "headline", "measure": "actual_spend_total", "title": "Actual spend"},
            {"action": "add", "section": "named", "measure": "open_projects", "breakdowns": ["no_such_dimension"]},
            {"action": "replace", "ref": "w999", "measure": "open_projects"},
            {"action": "add", "measure": "milestone_slippage", "title": "Milestone slippage"},
        ]}})
    stack.app.state.gateway = fake.client()  # type: ignore[attr-defined]
    refined = await plan(stack, token)

    [sent] = calls(fake)
    assert sent["task"] == "report_plan"
    body = json.loads(sent["messages"][1]["content"])
    assert body["request"] == REQUEST and body["draft"]["gaps"] and "rows" not in json.dumps(body)
    assert {"key": "overdue_go_lives", "label": "Overdue go-lives"} in body["context"]["measures"]
    assert any(d["key"] == "region" for d in body["context"]["dimensions"]) and body["context"]["blueprints"]

    review = refined["review"]
    assert review["verdict"] == "correct" and review["showing"] == "ai" and "alternate" not in review
    titles = refs(refined)
    assert "Open projects by region" in titles and "Actual spend" not in titles
    assert "Added Open projects by region" in review["changes"] and named["widgets"][0]["title"] in review["removed"]
    added = next(w for s in refined["sections"] for w in s["widgets"] if w["title"] == "Open projects by region")
    assert added["certified"] and added["spec"]["measure"]["ref"] == "measure:open_projects"
    assert added["visual"] != "figure"  # plan_visual picks a chart for a breakdown (RULE-008)
    dropped = " ".join(review["dropped"])
    assert "actual_spend_total" in dropped and "no_such_dimension" in dropped and "w999" in dropped
    assert "milestone_slippage" in dropped
    # The "Not included" list is the rules' own; the title can't claim spend.
    assert refined["gaps"] == rules["gaps"] and any("milestone" in g["reason"].lower() for g in refined["gaps"])
    assert refined["title"] != "Delivery and actual spend by region" and "spend" not in refined["title"].lower()
    keys = [s["key"] for s in refined["sections"]]
    assert keys[0] == "headline" and keys.index("bp_delayed_projects") < keys.index("named")
    assert {b["widget_ref"] for b in refined["layout_preview"]} == set(titles.values())

    # "Show the rules plan" restores it; "Show the AI plan" brings the refinement back.
    url = f"/api/v1/reports/plans/{refined['plan_id']}/show"
    back = (await stack.post(url, headers=h(token), json={"show": "rules"})).json()
    assert refs(back) == by_title and back["title"] == rules["title"] and back["review"]["showing"] == "rules"
    stored = (await stack.get(f"/api/v1/reports/plans/{refined['plan_id']}", headers=h(token))).json()
    assert refs(stored) == by_title
    again = (await stack.post(url, headers=h(token), json={"show": "ai"})).json()
    assert refs(again) == titles and again["review"]["changes"] == review["changes"]

    built = await stack.post(f"/api/v1/reports/plans/{refined['plan_id']}/build", headers=h(token))
    assert built.status_code == 201, built.text
    assert "Open projects by region" in {r["title"] for r in built.json()["results"]}


async def test_ag05_is_out_of_review_by_default(stack: httpx.AsyncClient, world: World,
                                               owner_conn: psycopg.Connection,
                                               monkeypatch: pytest.MonkeyPatch) -> None:
    """DEC-28 point 3 (US-246 final gate, EXEC-169): with the tenant's switch on, a report plan makes no review call."""
    monkeypatch.setattr(get_settings(), "llm_review_off_agents", ["AG-05", "AG-06"])
    token = world.tokens["creator"]
    stack.app.state.gateway = None  # type: ignore[attr-defined]
    today = await plan(stack, token)
    fake = planner({"verdict": "correct", "replacement": {"title": "Something else"}})
    stack.app.state.gateway = fake.client()  # type: ignore[attr-defined]
    review_on(owner_conn, world)
    got = await plan(stack, token)
    assert json.dumps(without_id(got)) == json.dumps(without_id(today)) and "review" not in got
    assert calls(fake) == []


async def test_admin_turns_ag05_review_on_and_off_on_the_agents_screen(
        stack: httpx.AsyncClient, world: World, owner_conn: psycopg.Connection,
        monkeypatch: pytest.MonkeyPatch) -> None:
    """US-268: the Agents screen's choice beats the deployment default; null goes back to it; audited."""
    monkeypatch.setattr(get_settings(), "llm_review_off_agents", ["AG-05", "AG-06"])
    admin, token = world.tokens["admin"], world.tokens["creator"]
    url = "/api/v1/admin/agents/AG-05/review"
    # No AI settings row yet: nothing to switch.
    owner_conn.execute("DELETE FROM ops.llm_settings WHERE tenant_id = %s", (world.tenant_id,))
    assert (await stack.put(url, headers=h(admin), json={"on": True})).status_code == 400
    review_on(owner_conn, world)
    fake = planner({"verdict": "confirm", "reason": "fits"})
    stack.app.state.gateway = fake.client()  # type: ignore[attr-defined]
    ag05 = (await stack.get("/api/v1/admin/agents/AG-05", headers=h(admin))).json()
    assert ag05["review"] == {"reviews": True, "on": False, "choice": None, "default_on": False, "switch_on": True}
    await plan(stack, token)
    assert calls(fake) == []

    resp = await stack.put(url, headers=h(admin), json={"on": True})
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"reviews": True, "on": True, "choice": True, "default_on": False, "switch_on": True}
    got = await plan(stack, token)
    assert len(calls(fake)) == 1 and got["review"]["verdict"] == "confirm"
    listed = (await stack.get("/api/v1/admin/agents", headers=h(admin))).json()["items"]
    assert next(a for a in listed if a["code"] == "AG-05")["review_off"] is False

    # Off for AG-03 is kept beside AG-05's choice; back to the default for AG-05.
    assert (await stack.put("/api/v1/admin/agents/AG-03/review", headers=h(admin), json={"on": False})
            ).json()["on"] is False
    assert (await stack.put(url, headers=h(admin), json={"on": None})).json()["choice"] is None
    await plan(stack, token)
    assert len(calls(fake)) == 1
    stored = owner_conn.execute("SELECT review_agents FROM ops.llm_settings WHERE tenant_id = %s",
                                (world.tenant_id,)).fetchone()
    assert stored is not None and stored[0] == {"AG-03": False}
    rows = owner_conn.execute("SELECT object_id, details->>'after' FROM ops.audit_events "
                              "WHERE action = 'agent_review.changed' ORDER BY occurred_at").fetchall()
    assert [tuple(r) for r in rows] == [("AG-05", "true"), ("AG-03", "false"), ("AG-05", None)]

    # An agent that never reviews has no switch; only Admins may change it.
    assert (await stack.put("/api/v1/admin/agents/AG-10/review", headers=h(admin), json={"on": True})
            ).status_code == 400
    assert (await stack.put(url, headers=h(token), json={"on": True})).status_code == 403


async def test_confirm_and_fallback_keep_the_rules_plan(stack: httpx.AsyncClient, world: World,
                                                        owner_conn: psycopg.Connection) -> None:
    token = world.tokens["creator"]
    stack.app.state.gateway = None  # type: ignore[attr-defined]
    rules = await plan(stack, token)
    review_on(owner_conn, world)
    for reply, verdict in (({"verdict": "confirm", "reason": "fits"}, "confirm"), ({}, "fallback"),
                           ({"verdict": "correct", "replacement": {"widgets": [
                               {"action": "add", "measure": "made_up"}]}}, "correct")):
        stack.app.state.gateway = planner(reply).client()  # type: ignore[attr-defined]
        got = await plan(stack, token)
        assert got["review"]["verdict"] == verdict and got["review"]["showing"] == "rules"
        assert got["review"]["changes"] == [] and without_id({**got, "review": None}) == {**without_id(rules),
                                                                                           "review": None}
        resp = await stack.post(f"/api/v1/reports/plans/{got['plan_id']}/show", headers=h(token),
                                json={"show": "rules"})
        assert resp.status_code == 409


async def test_plan_editing_is_reviewed(stack: httpx.AsyncClient, world: World,
                                        owner_conn: psycopg.Connection) -> None:
    token = world.tokens["creator"]
    stack.app.state.gateway = None  # type: ignore[attr-defined]
    rules = await plan(stack, token)
    review_on(owner_conn, world)
    fake = planner({"verdict": "correct", "replacement": {"widgets": [
        {"action": "add", "section": "named", "measure": "open_projects", "breakdowns": ["priority"]}]}})
    stack.app.state.gateway = fake.client()  # type: ignore[attr-defined]
    edited = await stack.post(f"/api/v1/reports/plans/{rules['plan_id']}/edit", headers=h(token),
                              json={"instruction": "add a priority filter"})
    assert edited.status_code == 200, edited.text
    out = edited.json()
    [sent] = calls(fake)
    body = json.loads(sent["messages"][1]["content"])
    assert body["request"].startswith(REQUEST) and "Change: add a priority filter" in body["request"]
    assert any(f["ref"] == "dimension:priority" for f in out["page_filters"])
    assert "Open projects by priority" in out["diff"]["added"] and out["review"]["showing"] == "ai"
    back = (await stack.post(f"/api/v1/reports/plans/{rules['plan_id']}/show", headers=h(token),
                             json={"show": "rules"})).json()
    assert "Open projects by priority" not in refs(back) and back["diff"]["added"] == []
    assert any(f["ref"] == "dimension:priority" for f in back["page_filters"])  # the rules' edit stays


# The rules plan's score on the 20 report requests (US-246 final gate scores review off against on).
RULES_RIGHT = 15  # misses: rp02, rp04, rp12, rp19, rp20 (EXEC-163)


async def test_report_plan_gold_set_rules_baseline(stack: httpx.AsyncClient, world: World) -> None:
    cases = plan_gold.load()
    assert len(cases) == 20 and len({c["id"] for c in cases}) == 20
    stack.app.state.gateway = None  # type: ignore[attr-defined]
    misses = {}
    for case in cases:
        got = await plan(stack, world.tokens["creator"], case["request"])
        misses[case["id"]] = plan_gold.score(got, case["expect"])
    assert sum(not m for m in misses.values()) == RULES_RIGHT, misses


async def test_a_refinement_can_put_a_gold_miss_right(stack: httpx.AsyncClient, world: World,
                                                      owner_conn: psycopg.Connection) -> None:
    case = next(c for c in plan_gold.load() if c["id"] == "rp02")
    review_on(owner_conn, world)
    stack.app.state.gateway = planner({"verdict": "correct", "replacement": {"widgets": [  # type: ignore[attr-defined]
        {"action": "add", "section": "horizon", "measure": k} for k in ("go_lives_0_30", "go_lives_31_60",
                                                                        "go_lives_61_90")]}}).client()
    got = await plan(stack, world.tokens["creator"], case["request"])
    assert plan_gold.score(got, case["expect"]) == [] and got["review"]["showing"] == "ai"
