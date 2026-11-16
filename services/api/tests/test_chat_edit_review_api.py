"""AG-06 canvas edits reviewed by the LLM (ENH-026, US-244; DEC-28), with a scripted AG-06 on the TPG fixture.

Acceptance: a phrasing the rules miss is applied (marked as the AI's, nothing changes until Apply, Apply is one
undoable step); an unknown widget id is refused; an unsuitable chart is still refused with its alternative; a target
the LLM and the rules read differently is the existing clarifying question; review off → today's ops and no call.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import psycopg
import pytest
from api_testkit import needs_db
from conftest import FakeGateway
from pmo_testkit import World, h
from test_ask_api import ask
from test_ask_review_api import review_on
from test_authoring_api import items, new_report, pin, world  # noqa: F401 - the TPG world

from tela_api.core.settings import get_settings

pytestmark = [needs_db, pytest.mark.integration]


@pytest.fixture(autouse=True)
def ag06_reviewed(monkeypatch: pytest.MonkeyPatch) -> None:
    """AG-06 is out of review by default (EXEC-169); these tests put it back to keep the merge covered."""
    monkeypatch.setattr(get_settings(), "llm_review_off_agents", [])


async def canvas(stack: httpx.AsyncClient, w: World) -> tuple[str, dict[str, str]]:
    """A report with three widgets; returns its id and each widget's canvas item id by title."""
    token = w.tokens["creator"]
    report = await new_report(stack, token)
    for question, title, visual in (("Open projects by region", "Open by region", "ranked_bar"),
                                    ("Open projects by current state", "Open by state", "ranked_bar"),
                                    ("How many open projects?", "Open projects", "figure")):
        turn, _ = await ask(stack, token, question)
        assert (await pin(stack, token, turn, title, visual, report["id"])).status_code == 201
    got = (await stack.get(f"/api/v1/reports/{report['id']}", headers=h(token))).json()
    return report["id"], {i["title"]: i["id"] for i in items(got)}


def editor(stack: httpx.AsyncClient, conn: psycopg.Connection, w: World, verdict: dict[str, Any] | None,
           on: bool = True) -> FakeGateway:
    review_on(conn, w, on)
    fake = FakeGateway({"AG-06": verdict} if verdict is not None else {})
    stack.app.state.gateway = fake.client()  # type: ignore[attr-defined]
    return fake


def calls(fake: FakeGateway) -> list[dict[str, Any]]:
    return [c for c in fake.calls if c["agent"] == "AG-06"]


async def edit(stack: httpx.AsyncClient, w: World, rid: str, instruction: str, **extra: Any) -> dict[str, Any]:
    resp = await stack.post(f"/api/v1/reports/{rid}/chat-edit", headers=h(w.tokens["creator"]),
                            json={"instruction": instruction, **extra})
    assert resp.status_code == 200, resp.text
    return dict(resp.json())


async def test_a_phrasing_the_rules_miss_is_applied(stack: httpx.AsyncClient, world: World,
                                                    owner_conn: psycopg.Connection) -> None:
    rid, ids = await canvas(stack, world)
    fake = editor(stack, owner_conn, world, {
        "verdict": "correct", "reason": "stretch across the page is full width",
        "replacement": {"ops": [{"op": "resize", "widget": ids["Open by region"], "width": 12}]}})
    preview = await edit(stack, world, rid, "stretch the regional breakdown across the whole page")
    [sent] = calls(fake)
    assert sent["task"] == "canvas_edit"
    body = json.loads(sent["messages"][1]["content"])
    assert body["draft"]["ops"] == [] and body["draft"]["refused"][0]["reason"].startswith("I couldn't turn")
    assert {x["id"]: x["title"] for x in body["context"]["widgets"]} == {v: k for k, v in ids.items()}
    assert any(d["key"] == "region" for d in body["context"]["dimensions"])

    assert preview["review"]["verdict"] == "correct" and preview["preview_id"]
    [change] = preview["changes"]  # the rules' refusal is replaced by the AI's reading
    assert change["op"] == "resize" and change["by"] == "ai" and change["to"]["w"] == 12
    assert preview["highlights"] == [ids["Open by region"]]
    token = world.tokens["creator"]
    before = (await stack.get(f"/api/v1/reports/{rid}", headers=h(token))).json()
    assert all(i["w"] != 12 for i in items(before))  # nothing changes before Apply
    applied = await stack.post(f"/api/v1/reports/{rid}/chat-edit/{preview['preview_id']}/apply", headers=h(token))
    assert applied.status_code == 200, applied.text
    assert {i["title"]: i["w"] for i in items(applied.json())}["Open by region"] == 12
    undone = (await stack.post(f"/api/v1/reports/{rid}/undo", headers=h(token))).json()  # one undoable step
    assert {i["title"]: i["w"] for i in items(undone)}["Open by region"] != 12


async def test_an_unknown_widget_id_is_refused(stack: httpx.AsyncClient, world: World,
                                               owner_conn: psycopg.Connection) -> None:
    rid, ids = await canvas(stack, world)
    editor(stack, owner_conn, world, {"verdict": "correct", "replacement": {"ops": [
        {"op": "remove", "widget": "w-not-here"}, {"op": "spin", "widget": ids["Open projects"]}]}})
    preview = await edit(stack, world, rid, "get rid of the budget gauge")
    assert preview["preview_id"] is None
    reasons = [c["reason"] for c in preview["changes"] if c["op"] == "refused"]
    assert any("isn't on this report (“w-not-here”)" in r for r in reasons)
    assert any("“spin” isn't a layout change" in r for r in reasons)
    assert not [c for c in preview["changes"] if c["op"] != "refused"]


async def test_an_unsuitable_chart_is_still_refused_with_its_alternative(
        stack: httpx.AsyncClient, world: World, owner_conn: psycopg.Connection) -> None:
    rid, ids = await canvas(stack, world)
    editor(stack, owner_conn, world, {"verdict": "correct", "replacement": {"ops": [
        {"op": "set_visual", "widget": ids["Open by state"], "visual": "trend"},
        {"op": "move", "widget": ids["Open projects"], "to": "top"}]}})
    preview = await edit(stack, world, rid, "draw the state one as a line graph and bump the projects count up first")
    by_op = {c["op"]: c for c in preview["changes"]}
    assert by_op["move"]["by"] == "ai" and preview["preview_id"]
    refused = by_op["refused"]
    assert refused["reason"].startswith("Trend needs a date") and refused["alternative"]
    assert refused["item_id"] == ids["Open by state"] and "set_visual" not in by_op


async def test_an_addition_on_a_widget_the_words_do_not_name_is_left_out(
        stack: httpx.AsyncClient, world: World, owner_conn: psycopg.Connection) -> None:
    """US-246 gate (ce13, ce19): the AI's extra ops on widgets the instruction never names are left out, with a note;
    its op on the named widget stands."""
    rid, ids = await canvas(stack, world)
    editor(stack, owner_conn, world, {"verdict": "correct", "replacement": {"ops": [
        {"op": "set_limit", "widget": ids["Open by region"], "limit": 5},
        {"op": "set_limit", "widget": ids["Open by state"], "limit": 5},
        {"op": "move", "widget": ids["Open by region"], "next_to": ids["Open projects"]}]}})
    preview = await edit(stack, world, rid, "only keep the five biggest regions")
    changed = [(c["op"], c["item_id"]) for c in preview["changes"] if c["op"] != "refused"]
    assert changed == [("set_limit", ids["Open by region"])] and preview["preview_id"]
    notes = preview["review"]["notes"]
    assert any("Show the top 5 in Open by state" in n and "doesn't ask for" in n for n in notes)
    assert any("Move Open by region" in n for n in notes)  # beside a widget the words don't name

    editor(stack, owner_conn, world, {"verdict": "correct", "replacement": {"ops": [
        {"op": "remove", "widget": ids["Open projects"]}, {"op": "add_filter", "dimension": "region"}]}})
    preview = await edit(stack, world, rid, "drop the budget table")
    assert preview["preview_id"] is None
    assert not [c for c in preview["changes"] if c["op"] != "refused"]
    assert len(preview["review"]["notes"]) == 2

    editor(stack, owner_conn, world, {"verdict": "correct", "replacement": {"ops": [
        {"op": "resize", "widget": ids["Open by region"], "width": 12},
        {"op": "move", "widget": ids["Open by region"], "to": "top"}]}})
    preview = await edit(stack, world, rid, "stretch the regional breakdown across the whole page")
    assert [c["op"] for c in preview["changes"] if c["op"] != "refused"] == ["resize"]  # ce11: no placement word
    assert any("Move Open by region" in n for n in preview["review"]["notes"])

    editor(stack, owner_conn, world, {"verdict": "correct", "replacement": {"ops": [
        {"op": "add_filter", "dimension": "region"}]}})
    preview = await edit(stack, world, rid, "let people narrow the page down by region")
    assert [c["op"] for c in preview["changes"] if c["op"] != "refused"] == ["add_filter"]


async def test_a_different_target_asks_which_widget(stack: httpx.AsyncClient, world: World,
                                                    owner_conn: psycopg.Connection) -> None:
    rid, ids = await canvas(stack, world)
    fake = editor(stack, owner_conn, world, {"verdict": "correct", "reason": "the state chart is the open one",
                                             "replacement": {"ops": [{"op": "remove",
                                                                      "widget": ids["Open by state"]}]}})
    preview = await edit(stack, world, rid, "remove open by region")
    q = preview["clarification"]
    assert q["question"] == "Which widget do you mean by “open by region”?" and preview["preview_id"] is None
    assert [o["title"] for o in q["options"]] == ["Open by region", "Open by state"]
    assert q["why"] == ["the state chart is the open one"]
    # The person chose: read by the rules, not reviewed again.
    chosen = await edit(stack, world, rid, "remove Open by state", answered=True)
    assert [(c["op"], c["item_id"]) for c in chosen["changes"]] == [("remove", ids["Open by state"])]
    assert chosen["review"] is None and len(calls(fake)) == 1


async def test_a_confirm_and_an_ask(stack: httpx.AsyncClient, world: World,
                                    owner_conn: psycopg.Connection) -> None:
    rid, ids = await canvas(stack, world)
    editor(stack, owner_conn, world, {"verdict": "confirm", "reason": "full width"})
    preview = await edit(stack, world, rid, "make open by region full width")
    assert preview["review"]["verdict"] == "confirm"
    assert [(c["op"], c.get("by")) for c in preview["changes"]] == [("resize", None)]

    editor(stack, owner_conn, world, {"verdict": "ask", "reason": "two charts could be meant", "options": [
        {"ops": [{"op": "set_limit", "widget": ids["Open by region"], "limit": 5}]},
        {"ops": [{"op": "set_limit", "widget": ids["Open by state"], "limit": 5}]}]})
    asked = await edit(stack, world, rid, "only the five biggest bars please")
    q = asked["clarification"]
    assert q["question"] == "Which change do you mean?"
    assert [o["instruction"] for o in q["options"]] == ["limit Open by region to top 5", "limit Open by state to top 5"]
    picked = await edit(stack, world, rid, q["options"][1]["instruction"], answered=True)
    assert [(c["op"], c["item_id"], c["to"]) for c in picked["changes"]] == [("set_limit", ids["Open by state"], 5)]


async def test_ag06_is_out_of_review_by_default(stack: httpx.AsyncClient, world: World,
                                               owner_conn: psycopg.Connection,
                                               monkeypatch: pytest.MonkeyPatch) -> None:
    """DEC-28 point 3 (US-246 final gate, EXEC-169): with the tenant's switch on, chat edits make no review call."""
    monkeypatch.setattr(get_settings(), "llm_review_off_agents", ["AG-05", "AG-06"])
    rid, ids = await canvas(stack, world)
    fake = editor(stack, owner_conn, world, {"verdict": "correct", "replacement": {"ops": [
        {"op": "resize", "widget": ids["Open by region"], "width": 12}]}})
    preview = await edit(stack, world, rid, "stretch the regional breakdown across the whole page")
    assert calls(fake) == [] and preview["review"] is None and preview["preview_id"] is None


async def test_review_off_keeps_todays_ops(stack: httpx.AsyncClient, world: World,
                                           owner_conn: psycopg.Connection) -> None:
    rid, ids = await canvas(stack, world)
    instruction = ("make open by region full width and show open by state as trend, then move open projects to "
                   "the top")
    fake = editor(stack, owner_conn, world, {"verdict": "correct", "replacement": {"ops": []}}, on=False)
    off = await edit(stack, world, rid, instruction)
    assert not calls(fake) and off["review"] is None
    assert [(c["op"], c.get("item_id")) for c in off["changes"]] == [
        ("resize", ids["Open by region"]), ("move", ids["Open projects"]), ("refused", ids["Open by state"])]
    assert all("by" not in c for c in off["changes"])

    stack.app.state.gateway = FakeGateway(fail="planner_unavailable").client()  # type: ignore[attr-defined]
    review_on(owner_conn, world)
    fallback = await edit(stack, world, rid, instruction)  # a failed review: the rules' reading stands
    assert fallback["review"]["verdict"] == "fallback"
    assert fallback["changes"] == off["changes"]
