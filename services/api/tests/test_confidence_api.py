"""Voting, back-translation and confidence in Ask (US-182; FEAT-064 / FEAT-019), with a scripted AG-03 (FakeGateway).

Acceptance: AG-03 plans three samples at a moderate temperature and the majority is answered with its confidence; when
the samples disagree or the plan's plain words miss the question, Ask asks one question with the readings and runs the
one chosen; the person's verbosity moves the threshold; the approved ambiguity library reads "late projects" as
overdue go-lives.
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
from test_ask_api import ask, by_event, parse_sse
from test_planner_gold_unit import SNAP

from tela_api.knowledge.guidance import glossary_chunks, load_glossary

pytestmark = [needs_db, pytest.mark.integration]

MISS = "zorbly glimps of the quantum widgets"  # names no measure: the rules find nothing, AG-03 plans
BY_REGION = {"measure": "measure:open_projects", "breakdowns": ["dimension:region"]}
BY_PROGRAMME = {"measure": "measure:open_projects", "breakdowns": ["dimension:programme"]}
TOTAL = {"measure": "measure:total_projects"}


@pytest.fixture
async def world(stack: httpx.AsyncClient, owner_conn: psycopg.Connection, make_token: Callable[..., str],
                tpg_source: dict[str, Any]) -> World:
    w = await setup_world(stack, owner_conn, make_token, tpg_source)
    # The pack glossary with its ambiguity library, approved by an Admin (FEAT-016).
    doc = owner_conn.execute(
        "INSERT INTO rag.knowledge_documents (id, tenant_id, connection_id, source_kind, title, content_hash, "
        "review_status, index_status) VALUES (gen_random_uuid(), %s, %s, 'glossary', 'Glossary', 'g', 'approved', "
        "'indexed') RETURNING id", (w.tenant_id, w.connection_id)).fetchone()
    assert doc is not None
    for i, c in enumerate(glossary_chunks(load_glossary("pmo_tpg"), SNAP)):
        owner_conn.execute("INSERT INTO rag.knowledge_chunks (id, tenant_id, document_id, chunk_no, content, metadata) "
                           "VALUES (gen_random_uuid(), %s, %s, %s, %s, %s)",
                           (w.tenant_id, doc[0], i, c["content"], json.dumps(c["metadata"])))
    return w


def samples(*plans: dict[str, Any]) -> Callable[[str, list[Any]], Any]:
    """AG-03: no candidate pick, then the plans in turn, one per sample (the last one repeated)."""
    queue = list(plans)

    def reply(task: str, messages: list[dict[str, Any]]) -> Any:
        if "choosing candidates" in messages[0]["content"]:
            return {}
        return queue.pop(0) if len(queue) > 1 else queue[0]
    return reply


def plan_calls(fake: FakeGateway) -> list[dict[str, Any]]:
    return [c for c in fake.calls if c["agent"] == "AG-03" and "choosing candidates" not in c["messages"][0]["content"]]


async def choose(api: httpx.AsyncClient, token: str, turn_id: str, option: str, events: list[Any]) -> list[Any]:
    """Answer the question and read the stream on from the clarify event."""
    resp = await api.post(f"/api/v1/ask/turns/{turn_id}/clarification", headers=h(token), json={"option_id": option})
    assert resp.status_code == 202, resp.text
    after = {"Last-Event-ID": str(max(e[0] for e in events))}
    return parse_sse((await api.get(resp.json()["stream_url"], headers=h(token, **after))).text)


async def test_agreeing_samples_are_answered_with_their_confidence(stack: httpx.AsyncClient, world: World) -> None:
    fake = FakeGateway({"AG-03": samples({**BY_REGION, "confidence": 0.9,
                                          "reasoning_summary": "Open projects split by region."})})
    stack.app.state.gateway = fake.client()  # type: ignore[attr-defined]
    _, events = await ask(stack, world.tokens["admin"], MISS)
    ev = by_event(events)
    assert ev["plan"]["planner"] == "llm" and ev["plan"]["spec"]["breakdowns"] == [{"ref": "dimension:region"}]
    calls = plan_calls(fake)
    assert len(calls) == 3 and {c["temperature"] for c in calls} == {0.6}
    conf = ev["complete"]["confidence"]
    assert conf["score"] == pytest.approx(0.98) and conf["threshold"] == 0.5 and conf["verbosity"] == "balanced"
    assert conf["signals"]["agreement"] == 1.0 and conf["signals"]["samples"] == 3
    assert conf["signals"]["back_translation"] == "Open projects by Region" and conf["signals"]["back_check"] is True
    assert conf["reasoning"] == "Open projects split by region." and conf["why"] == []


async def test_disagreeing_samples_ask_which_reading_and_run_the_chosen_one(
        stack: httpx.AsyncClient, world: World, owner_conn: psycopg.Connection) -> None:
    fake = FakeGateway({"AG-03": samples(BY_REGION, BY_PROGRAMME, TOTAL)})
    stack.app.state.gateway = fake.client()  # type: ignore[attr-defined]
    admin = world.tokens["admin"]
    turn_id, events = await ask(stack, admin, MISS)
    ev = by_event(events)
    assert [e[1] for e in events][-1] == "clarify" and "plan" not in ev
    q = ev["clarify"]
    assert q["kind"] == "reading" and q["question"] == "I can read this more than one way. Which did you mean?"
    assert [o["label"] for o in q["options"]] == ["Open projects by Region", "Open projects by Programme", "Projects"]
    assert q["why"] == ["1 of 3 plans agreed"] and "readings" not in q  # the specs stay on the turn
    stored = owner_conn.execute("SELECT clarification, planner FROM chat.turns WHERE id = %s", (turn_id,)).fetchone()
    assert stored is not None and len(stored[0]["readings"]) == 3 and stored[1] == "llm"

    resumed = by_event(await choose(stack, admin, turn_id, "b", events))
    assert resumed["plan"]["spec"]["breakdowns"] == [{"ref": "dimension:programme"}]
    assert resumed["plan"]["interpreted"] == ["You chose: Open projects by Programme"]
    assert resumed["plan"]["planner"] == "llm" and "complete" in resumed
    assert len(plan_calls(fake)) == 3  # the chosen reading runs as stored: no new plan


async def test_a_plan_whose_words_miss_the_question_is_checked_with_the_person(
        stack: httpx.AsyncClient, world: World) -> None:
    # AG-03 adds a filter the question never said: the back-translation catches it.
    fake = FakeGateway({"AG-03": samples({"measure": "measure:open_projects",
                                          "filters": [{"ref": "dimension:region", "values": ["KSA"]}]})})
    stack.app.state.gateway = fake.client()  # type: ignore[attr-defined]
    admin = world.tokens["admin"]
    _, events = await ask(stack, admin, MISS)
    q = by_event(events)["clarify"]
    assert q["why"] == ["the plan filters on KSA, which the question doesn't say"]
    assert q["options"][0]["label"] == "Open projects, Region: KSA" and 2 <= len(q["options"]) <= 4

    # Assume first: answered, with the low confidence and why on the card.
    resp = await stack.put("/api/v1/me/preferences", headers=h(admin), json={"ask_verbosity": "assume_first"})
    assert resp.status_code == 200, resp.text
    _, events = await ask(stack, admin, MISS)
    done = by_event(events)["complete"]
    assert done["confidence"]["score"] == 0.4 and done["confidence"]["verbosity"] == "assume_first"
    assert done["confidence"]["why"] == ["the plan filters on KSA, which the question doesn't say"]


async def test_traps_read_as_the_pack_says_and_ask_freely_asks(stack: httpx.AsyncClient, world: World) -> None:
    creator = world.tokens["creator"]
    _, events = await ask(stack, creator, "How many late projects?")
    ev = by_event(events)
    assert ev["plan"]["planner"] == "rules" and ev["plan"]["spec"]["measure"]["ref"] == "measure:overdue_go_lives"
    assert "Read “late projects” as Overdue go-lives" in ev["complete"]["interpreted"]
    assert ev["complete"]["confidence"]["score"] == 0.6

    resp = await stack.put("/api/v1/me/preferences", headers=h(creator), json={"ask_verbosity": "ask_freely"})
    assert resp.json()["ask_verbosity"] == "ask_freely"
    turn_id, events = await ask(stack, creator, "How many late projects?")
    q = by_event(events)["clarify"]
    assert q["why"] == ["“late projects” has more than one meaning"]
    labels = [o["label"] for o in q["options"]]
    assert labels[0] == "Overdue go-lives" and "Overdue more than 90 days" in labels
    resumed = by_event(await choose(stack, creator, turn_id, chr(97 + labels.index("Overdue more than 90 days")),
                                     events))
    assert resumed["plan"]["spec"]["measure"]["ref"] == "measure:overdue_over_90" and "complete" in resumed
    assert resumed["plan"]["planner"] == "rules"
