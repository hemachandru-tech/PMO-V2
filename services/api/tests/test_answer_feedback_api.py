"""Result sanity checks and answer feedback (US-183; FEAT-064 / FEAT-018), live.

Sanity notes reach the card's data notes and only offer a refinement; thumbs up/down and "This isn't what I asked"
on one's own answers; down-votes (question and spec, never rows) wait in the Admin queue; fixing one names the gold
example made from it.
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
from test_ask_api import ask, by_event
from test_authoring_api import stream
from test_knowledge_api import bag

pytestmark = [needs_db, pytest.mark.integration]
QUEUE = "/api/v1/ask/feedback"
GOLD = "/api/v1/knowledge/gold-examples"


@pytest.fixture
async def world(stack: httpx.AsyncClient, owner_conn: psycopg.Connection, make_token: Callable[..., str],
                tpg_source: dict[str, Any]) -> World:
    w = await setup_world(stack, owner_conn, make_token, tpg_source)
    stack.app.state.gateway = FakeGateway(embedder=bag).client()  # type: ignore[attr-defined]
    return w


def fb(turn_id: str) -> str:
    return f"/api/v1/ask/turns/{turn_id}/feedback"


async def refine(api: httpx.AsyncClient, token: str, turn_id: str, ops: list[dict[str, Any]]) -> dict[str, Any]:
    resp = await api.post(f"/api/v1/ask/turns/{turn_id}/refine", headers=h(token), json={"chip_ops": ops})
    assert resp.status_code == 202, resp.text
    return await stream(api, token, resp.json())


async def test_sanity_notes_reach_the_card(stack: httpx.AsyncClient, world: World) -> None:
    token = world.tokens["creator"]
    turn_id, events = await ask(stack, token, "Open projects by region")
    assert by_event(events)["complete"]["spec"]["breakdowns"] == [{"ref": "dimension:region"}]
    # Two filters that each match but never together (no KSA project has Karim Haddad as CIO): the note names the
    # first whose removal brings data back and offers to remove it.
    ev = await refine(stack, token, turn_id, [{"op": "add_filter", "ref": "dimension:region", "values": ["KSA"]},
                                             {"op": "add_filter", "ref": "dimension:cio", "values": ["Karim Haddad"]}])
    assert ev["complete"]["rows"] == []
    [note] = [n for n in ev["narrative"]["notes"] if n.get("kind") == "empty"]
    assert "Region: KSA" in note["text"] and note["fact"].startswith("s")
    assert note["action"]["ops"] == [{"op": "remove_filter", "ref": "dimension:region"}]
    # The offered refinement is one the API accepts; nothing was rerun in the answer's place.
    again = await refine(stack, token, ev["complete"]["turn_id"], note["action"]["ops"])
    assert again["complete"]["rows"]
    # A breakdown filtered down to one member: one row, and the filter on that dimension is offered.
    ev = await refine(stack, token, turn_id, [{"op": "add_filter", "ref": "dimension:region", "values": ["KSA"]}])
    assert len(ev["complete"]["rows"]) == 1
    kinds = [n.get("kind") for n in ev["narrative"]["notes"]]
    assert "single_row" in kinds
    # Groups add up to the root total here: no fan-out or excluded-rows note.
    assert not {"fan_out", "excluded"} & set(kinds)


async def test_votes_queue_and_fix(stack: httpx.AsyncClient, world: World, owner_conn: psycopg.Connection) -> None:
    creator, admin = world.tokens["creator"], world.tokens["admin"]
    turn_id, events = await ask(stack, creator, "How many projects are on hold?")
    spec = by_event(events)["complete"]["spec"]
    assert (await stack.get(fb(turn_id), headers=h(creator))).json() == {"feedback": None}

    up = await stack.put(fb(turn_id), headers=h(creator), json={"vote": "up"})
    assert up.status_code == 200, up.text
    assert up.json()["feedback"]["vote"] == "up" and up.json()["feedback"]["status"] == "none"
    bad = await stack.put(fb(turn_id), headers=h(creator), json={"vote": "up", "reason": "wrong"})
    assert bad.status_code == 400
    # Only the author rates an answer; others see it as not found.
    assert (await stack.put(fb(turn_id), headers=h(admin), json={"vote": "down"})).status_code == 404
    assert (await stack.put(fb("not-a-uuid"), headers=h(creator), json={"vote": "down"})).status_code == 404
    # Up-votes never reach the queue.
    assert (await stack.get(QUEUE, headers=h(admin))).json()["items"] == []

    down = await stack.put(fb(turn_id), headers=h(creator), json={
        "vote": "down", "reason": "not_what_i_asked", "comment": "  I meant  paused projects  "})
    assert down.status_code == 200
    mine = down.json()["feedback"]
    assert mine["status"] == "open" and mine["reason"] == "not_what_i_asked" and mine["comment"] == "I meant paused projects"
    assert (await stack.get(fb(turn_id), headers=h(creator))).json()["feedback"]["id"] == mine["id"]

    # The Admin queue: knowledge.manage only; question and spec, never rows.
    assert (await stack.get(QUEUE, headers=h(creator))).status_code == 403
    queue = (await stack.get(QUEUE, headers=h(admin))).json()
    [item] = queue["items"]
    assert queue["counts"]["open"] == 1
    assert item["question"] == "How many projects are on hold?" and item["spec"] == spec
    assert item["user"]["display_name"] == "Cleo Creator" and item["connection"]["id"] == world.connection_id
    assert "rows" not in item and "headline" not in item
    stored = owner_conn.execute("SELECT to_jsonb(f) FROM chat.answer_feedback f WHERE id = %s", (item["id"],)).fetchone()[0]
    assert set(stored) == {"id", "tenant_id", "turn_id", "user_id", "vote", "reason", "comment", "question", "spec",
                           "connection_id", "status", "gold_example_id", "resolved_by", "resolved_at",
                           "resolution_note", "created_at", "updated_at"}

    # Fixing needs the corrected gold example, saved from the answer through the usual endpoint.
    no_gold = await stack.patch(f"{QUEUE}/{item['id']}", headers=h(admin), json={"status": "fixed"})
    assert no_gold.status_code == 400
    corrected = {**{k: v for k, v in spec.items() if k not in ("model_version_id", "connection_id")},
                 "measure": {"ref": "measure:open_projects"}}
    g = await stack.post(GOLD, headers=h(admin), json={"question": item["question"], "expected_spec": corrected,
                                                       "connection_id": world.connection_id, "turn_id": turn_id})
    assert g.status_code == 201, g.text
    assert g.json()["status"] == "approved" and g.json()["source_turn_id"] == turn_id
    fixed = await stack.patch(f"{QUEUE}/{item['id']}", headers=h(admin), json={"status": "fixed",
                                                                                "gold_example_id": g.json()["id"]})
    assert fixed.status_code == 200, fixed.text
    assert fixed.json()["status"] == "fixed" and fixed.json()["gold_example_id"] == g.json()["id"]
    assert fixed.json()["resolved_by"]["display_name"] == "Ada Admin"
    assert owner_conn.execute("SELECT count(*) FROM ops.notifications WHERE kind = 'answer.feedback_fixed'"
                              ).fetchone()[0] == 1
    # Resolved feedback is final for the voter and the Admin.
    assert (await stack.put(fb(turn_id), headers=h(creator), json={"vote": "up"})).status_code == 409
    assert (await stack.delete(fb(turn_id), headers=h(creator))).status_code == 409
    again = await stack.patch(f"{QUEUE}/{item['id']}", headers=h(admin), json={"status": "dismissed"})
    assert again.status_code == 409
    assert (await stack.get(QUEUE, headers=h(admin))).json()["items"] == []
    assert [i["status"] for i in (await stack.get(QUEUE, params={"status": "all"}, headers=h(admin))).json()["items"]
            ] == ["fixed"]

    # A second down-vote is dismissed with a note; a vote can be taken back while open.
    t2, _ = await ask(stack, creator, "Open projects by region")
    await stack.put(fb(t2), headers=h(creator), json={"vote": "down", "reason": "wrong"})
    [item2] = (await stack.get(QUEUE, headers=h(admin))).json()["items"]
    out = await stack.patch(f"{QUEUE}/{item2['id']}", headers=h(admin), json={"status": "dismissed",
                                                                               "note": "The number is right."})
    assert out.json()["status"] == "dismissed" and out.json()["resolution_note"] == "The number is right."
    t3, _ = await ask(stack, creator, "How many projects are on hold?")
    await stack.put(fb(t3), headers=h(creator), json={"vote": "down"})
    assert (await stack.delete(fb(t3), headers=h(creator))).status_code == 204
    assert (await stack.get(fb(t3), headers=h(creator))).json() == {"feedback": None}
    actions = {r[0] for r in owner_conn.execute(
        "SELECT action FROM ops.audit_events WHERE action LIKE 'answer.feedback%'").fetchall()}
    assert actions == {"answer.feedback", "answer.feedback_resolved"}
