"""A Direct SQL answer's actions (ENH-028 US-260): the same ones as any Ask answer.

Acceptance: the answer is drawn as a chart (a ``viz`` event with the other suitable charts) and can be pinned (the
SQL saved as a Custom SQL widget, still kept off a published dashboard until certified); it can be rated; and "Save
as gold example" puts it in the curation queue as a gold-example proposal with its SQL as evidence, which an Admin
approves once they have written the spec. Only a finished Direct SQL answer of one's own goes to the queue.
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

from tela_api.core.settings import get_settings

pytestmark = [needs_db, pytest.mark.integration]

BY_REGION = "SELECT region, COUNT(*) AS projects FROM projects GROUP BY region"
FROM_ANSWER = "/api/v1/knowledge/curation/from-answer"


@pytest.fixture
async def world(stack: httpx.AsyncClient, owner_conn: psycopg.Connection, make_token: Callable[..., str],
                tpg_source: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> World:
    w = await setup_world(stack, owner_conn, make_token, tpg_source)
    owner_conn.execute("UPDATE conn.connections SET ask_mode = 'direct_sql' WHERE id = %s", (w.connection_id,))
    monkeypatch.setattr(get_settings(), "ask_frame", "rules")
    fake = FakeGateway({"AG-10": {"sql": BY_REGION, "explanation": "Projects in each region."}})
    stack.app.state.gateway = fake.client()  # type: ignore[attr-defined]
    return w


async def test_the_answer_is_charted_pinned_and_rated(stack: httpx.AsyncClient, world: World,
                                                      owner_conn: psycopg.Connection) -> None:
    creator = world.tokens["creator"]
    turn_id, events = await ask(stack, creator, "projects by region")
    ev = by_event(events)
    done = ev["complete"]
    assert done["ask_mode"] == "direct_sql" and done["sql"] == BY_REGION, ev.get("error")
    assert done["can_pin"] is True and done["publishable"] is False and done["certified"] is False
    viz = ev["viz"]
    assert viz["visual"] and viz["chart_spec"]["type"] == viz["visual"]
    assert viz["visual"] in viz["visuals"]["suitable"] and len(viz["visuals"]["suitable"]) > 1
    assert set(viz["charts"]) == set(viz["visuals"]["suitable"])
    assert all(spec["type"] == v for v, spec in viz["charts"].items())
    stored = owner_conn.execute("SELECT visual FROM chat.turns WHERE id = %s", (turn_id,)).fetchone()
    assert stored == (viz["visual"],)

    # Pin: the card checks the SQL again and saves it as a Custom SQL widget.
    check = await stack.post("/api/v1/custom-sql/check", headers=h(creator),
                             json={"connection_id": world.connection_id, "sql": done["sql"]})
    assert check.status_code == 200, check.text
    pin = await stack.post("/api/v1/widgets/custom-sql", headers=h(creator), json={
        "connection_id": world.connection_id, "sql": done["sql"], "check_token": check.json()["check_token"],
        "title": "Projects by region", "visual": viz["visual"], "drafted_by_ai": True})
    assert pin.status_code == 201, pin.text
    widget = owner_conn.execute("SELECT certification_state, source_kind, visual FROM content.widgets WHERE id = %s",
                                (pin.json()["widget_id"],)).fetchone()
    assert widget == ("custom_sql", "custom_sql", viz["visual"])

    # Helpful / Not helpful / This isn't what I asked.
    vote = await stack.put(f"/api/v1/ask/turns/{turn_id}/feedback", headers=h(creator),
                           json={"vote": "down", "reason": "not_what_i_asked", "comment": "I meant active ones"})
    assert vote.status_code == 200 and vote.json()["feedback"]["status"] == "open", vote.text


async def test_save_as_gold_example_goes_to_the_curation_queue(stack: httpx.AsyncClient, world: World,
                                                                owner_conn: psycopg.Connection) -> None:
    creator, admin = world.tokens["creator"], world.tokens["admin"]
    turn_id, events = await ask(stack, creator, "projects by region")
    assert "complete" in by_event(events), by_event(events).get("error")

    made = await stack.post(FROM_ANSWER, headers=h(creator), json={"turn_id": turn_id})
    assert made.status_code == 201, made.text
    proposal_id = made.json()["proposal_id"]
    again = await stack.post(FROM_ANSWER, headers=h(creator), json={"turn_id": turn_id})
    assert again.status_code == 201 and again.json()["proposal_id"] == proposal_id
    evidence = owner_conn.execute("SELECT source, turn_id::text, detail FROM rag.curation_evidence WHERE "
                                  "proposal_id = %s", (proposal_id,)).fetchall()
    assert evidence == [("direct_sql", turn_id, f"SQL: {BY_REGION}")]
    notified = owner_conn.execute("SELECT count(*) FROM ops.notifications WHERE kind = 'gold_example.suggested'"
                                  ).fetchone()[0]
    assert notified >= 1

    queue = (await stack.get("/api/v1/knowledge/curation", headers=h(admin))).json()
    [p] = [p for p in queue["items"] if p["id"] == proposal_id]
    assert p["kind"] == "gold_example" and p["spec"] is None and p["question"] == "projects by region"
    assert p["questions"][0]["source"] == "direct_sql" and p["questions"][0]["detail"].startswith("SQL: SELECT")
    assert queue["counts"]["pending_by_source"]["direct_sql"] == 1

    # The Admin writes the spec; approving makes the gold example.
    no_spec = await stack.post(f"/api/v1/knowledge/curation/{proposal_id}/approve", headers=h(admin), json={})
    assert no_spec.status_code == 400 and "spec" in no_spec.text
    ok = await stack.post(f"/api/v1/knowledge/curation/{proposal_id}/approve", headers=h(admin), json={
        "expected_spec": {"measure": {"ref": "measure:total_projects"}, "breakdowns": [{"ref": "dimension:region"}]}})
    assert ok.status_code == 200, ok.text
    gold = owner_conn.execute("SELECT status, source_turn_id::text FROM rag.gold_examples WHERE id = %s",
                              (ok.json()["applied"]["gold_example_id"],)).fetchone()
    assert gold == ("approved", turn_id)
    twice = await stack.post(FROM_ANSWER, headers=h(creator), json={"turn_id": turn_id})
    assert twice.status_code == 409  # a gold example already


async def test_only_your_own_finished_direct_sql_answer(stack: httpx.AsyncClient, world: World,
                                                        owner_conn: psycopg.Connection) -> None:
    creator, viewer = world.tokens["creator"], world.tokens["viewer"]
    turn_id, _ = await ask(stack, creator, "projects by region")
    assert (await stack.post(FROM_ANSWER, headers=h(viewer), json={"turn_id": turn_id})).status_code == 403
    missing = await stack.post(FROM_ANSWER, headers=h(creator), json={"turn_id": "not-a-turn"})
    assert missing.status_code == 404
    owner_conn.execute("UPDATE conn.connections SET ask_mode = 'certified' WHERE id = %s", (world.connection_id,))
    certified, events = await ask(stack, creator, "How many projects are there?")
    assert by_event(events)["complete"].get("ask_mode") != "direct_sql"
    other = await stack.post(FROM_ANSWER, headers=h(creator), json={"turn_id": certified})
    assert other.status_code == 409 and "Direct SQL" in other.text
