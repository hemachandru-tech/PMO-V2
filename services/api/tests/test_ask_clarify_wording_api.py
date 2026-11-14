"""AG-02 with LLM review on (ENH-026, US-241; DEC-28): the model back-check runs on every plan, and one ``clarify``
call words the reading question, with a scripted AG-02.

Acceptance: labels reworded, the readings' specs identical; a label failing the wording check keeps the rules' label;
a back-check mismatch caps the score at 0.4 (EXEC-171), on a rules plan and on AG-03's; review off makes no AG-02 call.
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
from pmo_testkit import World, h
from test_ask_api import ask, by_event
from test_ask_review_api import LATE, review_on, reviewer
from test_confidence_api import (
    BY_PROGRAMME,
    BY_REGION,
    MISS,
    TOTAL,
    choose,
    samples,
    world,  # noqa: F401 - the fixture with the approved pack glossary
)

pytestmark = [needs_db, pytest.mark.integration]

CORRECT = {"verdict": "correct", "replacement": {"measure": "measure:open_projects"},
           "reason": "late could mean projects past their end date"}
RULES_LABELS = ["Overdue go-lives", "Open projects"]
MISSING = "the question asks about projects, not go-lives"


def ag02(*, question: str = "", labels: list[str] | None = None,
         answers: bool = True) -> Callable[[str, list[Any]], Any]:
    """AG-02: the wording review replaces the draft's first labels with ``labels`` (the rest kept) and its question
    with ``question``; the back-check says whether the plan answers the question."""
    def reply(task: str, messages: list[dict[str, Any]]) -> Any:
        if "You word a clarifying question" not in messages[0]["content"]:
            return {"answers": True} if answers else {"answers": False, "missing": MISSING}
        if labels is None:
            return {"verdict": "confirm"}
        draft = json.loads(messages[1]["content"])["draft"]
        return {"verdict": "correct", "reason": "plainer",
                "replacement": {"question": question or draft["question"],
                                "labels": [*labels, *draft["labels"][len(labels):]]}}
    return reply


def calls(fake: FakeGateway, marker: str) -> list[dict[str, Any]]:
    return [c for c in fake.calls if c["agent"] == "AG-02" and marker in c["messages"][0]["content"]]


async def test_labels_are_reworded_and_the_readings_kept(stack: httpx.AsyncClient, world: World,  # noqa: F811
                                                         owner_conn: psycopg.Connection) -> None:
    review_on(owner_conn, world)
    worded = ["Projects whose go-live date has passed", "Every open project"]
    fake = FakeGateway({"AG-03": reviewer(CORRECT),
                        "AG-02": ag02(question="Which late projects do you mean?", labels=worded)})
    stack.app.state.gateway = fake.client()  # type: ignore[attr-defined]
    creator = world.tokens["creator"]
    turn_id, events = await ask(stack, creator, LATE)
    q = by_event(events)["clarify"]
    assert q["question"] == "Which late projects do you mean?"
    assert [o["label"] for o in q["options"]][:2] == worded
    assert [o["value"] for o in q["options"]][:2] == ["reading:0", "reading:1"]
    assert q["worded"] == "llm" and q["rules_labels"][:2] == RULES_LABELS
    sent = calls(fake, "You word a clarifying question")
    assert len(sent) == 1 and sent[0]["task"] == "clarify"
    stored = owner_conn.execute("SELECT clarification FROM chat.turns WHERE id = %s", (turn_id,)).fetchone()
    assert stored is not None
    assert [r["measure"]["ref"] for r in stored[0]["readings"]][:2] == ["measure:overdue_go_lives",
                                                                       "measure:open_projects"]

    resumed = by_event(await choose(stack, creator, turn_id, "b", events))
    assert resumed["plan"]["spec"]["measure"]["ref"] == "measure:open_projects"  # the reading, not the words
    assert resumed["plan"]["interpreted"] == ["You chose: Every open project"]


async def test_a_label_failing_the_wording_check_keeps_the_rules_label(
        stack: httpx.AsyncClient, world: World, owner_conn: psycopg.Connection) -> None:  # noqa: F811
    review_on(owner_conn, world)
    worded = ["Go-lives that are overdue", "Open projects and their actual spend"]
    fake = FakeGateway({"AG-03": reviewer(CORRECT), "AG-02": ag02(question="Which did you mean?", labels=worded)})
    stack.app.state.gateway = fake.client()  # type: ignore[attr-defined]
    _, events = await ask(stack, world.tokens["creator"], LATE)
    q = by_event(events)["clarify"]
    assert [o["label"] for o in q["options"]][:2] == ["Go-lives that are overdue", "Open projects"]
    assert q["question"] == "Which did you mean?"


async def test_a_wrong_count_of_labels_keeps_the_rules_wording(stack: httpx.AsyncClient, world: World,  # noqa: F811
                                                               owner_conn: psycopg.Connection) -> None:
    review_on(owner_conn, world)
    def one_label(task: str, messages: list[dict[str, Any]]) -> Any:
        return {"verdict": "correct", "replacement": {"question": "Which?", "labels": ["Only one label"]}}
    fake = FakeGateway({"AG-03": reviewer(CORRECT), "AG-02": one_label})
    stack.app.state.gateway = fake.client()  # type: ignore[attr-defined]
    _, events = await ask(stack, world.tokens["creator"], LATE)
    q = by_event(events)["clarify"]
    assert q["question"] == "I can read this more than one way. Which did you mean?"
    assert [o["label"] for o in q["options"]][:2] == RULES_LABELS and "worded" not in q


async def test_the_back_check_runs_on_a_rules_plan_and_caps_a_mismatch(
        stack: httpx.AsyncClient, world: World, owner_conn: psycopg.Connection) -> None:  # noqa: F811
    review_on(owner_conn, world)
    fake = FakeGateway({"AG-03": reviewer({"verdict": "confirm"}), "AG-02": ag02(answers=False)})
    stack.app.state.gateway = fake.client()  # type: ignore[attr-defined]
    creator = world.tokens["creator"]
    _, events = await ask(stack, creator, LATE)
    q = by_event(events)["clarify"]
    assert MISSING in q["why"] and len(q["options"]) >= 2
    sent = calls(fake, "You check a query plan")
    assert len(sent) == 1 and sent[0]["task"] == "clarify" and "Overdue go-lives" in sent[0]["messages"][1]["content"]

    resp = await stack.put("/api/v1/me/preferences", headers=h(creator), json={"ask_verbosity": "assume_first"})
    assert resp.status_code == 200, resp.text
    _, events = await ask(stack, creator, LATE)
    done = by_event(events)["complete"]
    assert done["confidence"]["score"] == 0.4  # the review's lift to 0.7 doesn't undo the cap
    assert done["confidence"]["signals"]["back_check"] is False
    assert done["confidence"]["signals"]["back_check_model"] == MISSING


async def test_the_back_check_runs_on_an_llm_plan_with_review_on(stack: httpx.AsyncClient, world: World,  # noqa: F811
                                                                  owner_conn: psycopg.Connection) -> None:
    review_on(owner_conn, world)
    fake = FakeGateway({"AG-03": samples({**BY_REGION, "confidence": 0.9}), "AG-02": ag02(answers=False)})
    stack.app.state.gateway = fake.client()  # type: ignore[attr-defined]
    _, events = await ask(stack, world.tokens["admin"], MISS)
    q = by_event(events)["clarify"]
    assert MISSING in q["why"]
    assert len(calls(fake, "You check a query plan")) == 1


async def test_review_off_makes_no_ag02_call(stack: httpx.AsyncClient, world: World,  # noqa: F811
                                             owner_conn: psycopg.Connection) -> None:
    review_on(owner_conn, world, on=False)
    fake = FakeGateway({"AG-03": samples(BY_REGION, BY_PROGRAMME, TOTAL), "AG-02": ag02(answers=False)})
    stack.app.state.gateway = fake.client()  # type: ignore[attr-defined]
    _, events = await ask(stack, world.tokens["admin"], MISS)
    q = by_event(events)["clarify"]  # the samples disagree: asked with the rules' wording
    assert q["question"] == "I can read this more than one way. Which did you mean?" and "worded" not in q
    assert [c for c in fake.calls if c["agent"] == "AG-02"] == []
