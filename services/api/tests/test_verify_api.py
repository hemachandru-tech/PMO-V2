"""US-258 through Ask: in the frame modes (Fields, Hybrid) the AG-01 intent review, AG-03 plan review and AG-02
back-check are off — the frame's structural check replaces them — while the Certified mode keeps ``llm_review``; a
plan that fails the check is asked about, and answered uncertified once the person says to leave the word out."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx
import psycopg
import pytest
from api_testkit import needs_db
from conftest import FakeGateway
from pmo_testkit import World, setup_world
from test_agent_runs_api import detail, runs
from test_ask_api import ask, by_event
from test_ask_review_api import review_on
from test_confidence_api import choose
from test_hybrid_api import set_mode

from tela_api.core.settings import get_settings

pytestmark = [needs_db, pytest.mark.integration]

REVIEWS = {"intent", "plan_review", "clarify"}  # AG-01 route review, AG-03 plan review, AG-02 back-check / wording


@pytest.fixture
async def world(stack: httpx.AsyncClient, owner_conn: psycopg.Connection, make_token: Callable[..., str],
                tpg_source: dict[str, Any]) -> World:
    return await setup_world(stack, owner_conn, make_token, tpg_source)


FRAME = {"shape": "number", "subject": "projects", "conditions": [{"phrase": "open"}]}


def reviewers(ag01: dict[str, Any] | None = None) -> FakeGateway:
    """Every reviewer confirms, so a review that runs shows up as a call and nothing else changes; AG-01 reads the
    frame (``ag01``) in the frame modes."""
    yes = {"verdict": "confirm", "reason": "fine"}
    return FakeGateway({"AG-01": ag01 or yes, "AG-02": {"ok": True, "missing": ""}, "AG-03": yes})


@pytest.mark.parametrize("mode", ["fields", "hybrid"])
async def test_reviews_are_off_in_the_frame_modes(stack: httpx.AsyncClient, world: World,
                                                  owner_conn: psycopg.Connection, monkeypatch: pytest.MonkeyPatch,
                                                  mode: str) -> None:
    review_on(owner_conn, world)
    await set_mode(stack, world, mode)
    fake = reviewers(FRAME)
    stack.app.state.gateway = fake.client()  # type: ignore[attr-defined]
    turn_id, events = await ask(stack, world.tokens["admin"], "How many open projects")
    ev = by_event(events)
    assert ev["complete"]["rows"] == [[65]] and ev["complete"]["certified"]
    assert not [c for c in fake.calls if c["task"] in REVIEWS or c["agent"] == "AG-02"], fake.calls
    assert {c["task"] for c in fake.calls if c["agent"] in ("AG-01", "AG-03")} <= {"frame"}  # the frame, once
    assert len([c for c in fake.calls if c["task"] == "frame"]) == 1
    d = await detail(stack, world.tokens["admin"], (await runs(stack, world.tokens["admin"], turn_id=turn_id))[0]["id"])
    assert "Checked against the question: passed" in [s["label"] for s in d["steps"]]


async def test_llm_review_keeps_working_in_the_certified_mode(stack: httpx.AsyncClient, world: World,
                                                              owner_conn: psycopg.Connection) -> None:
    review_on(owner_conn, world)
    fake = reviewers()
    stack.app.state.gateway = fake.client()  # type: ignore[attr-defined]
    _, events = await ask(stack, world.tokens["admin"], "How many open projects")
    assert "complete" in by_event(events)
    assert {"intent", "plan_review"} <= {c["task"] for c in fake.calls}


async def test_a_failed_check_asks_about_the_slot(stack: httpx.AsyncClient, world: World,
                                                  monkeypatch: pytest.MonkeyPatch) -> None:
    await set_mode(stack, world, "fields")
    monkeypatch.setattr(get_settings(), "ask_frame", "rules")
    token = world.tokens["admin"]
    turn_id, events = await ask(stack, token, "How many projects flibbertigibbet")
    q = by_event(events)["clarify"]
    assert q["kind"] == "field" and q["key"] == "fields:check_words:flibbertigibbet"
    assert [o["label"] for o in q["options"]] == ["Answer without “flibbertigibbet”"]
    d = await detail(stack, token, (await runs(stack, token, turn_id=turn_id))[0]["id"])
    step = next(s for s in d["steps"] if s["label"].startswith("Checked against the question"))
    assert step["label"] == "Checked against the question: words failed, asking" and step["status"] == "failed"
    done = by_event(await choose(stack, token, turn_id, "1", events))["complete"]
    assert done["rows"] == [[100]] and not done["certified"] and done["badge"] == "not_certified"
    assert done["fields"]["not_certified_because"] == "“flibbertigibbet” was left out, as you chose"
