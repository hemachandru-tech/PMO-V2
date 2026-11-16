"""AG-08 explains every definition with LLM review on, grounded on the measure's rule (ENH-026, US-245; DEC-28).

Acceptance: with no knowledge hits, AG-08 gets only the plain rule and blank handling as citable text; an explanation
citing them is kept and cites the measure, one citing anything else is dropped; review off makes no AG-08 call
without knowledge; ``narrate`` stays off while "send totals to the model" is off.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import httpx
import psycopg
import pytest
from api_testkit import needs_db
from conftest import FakeGateway
from pmo_testkit import World
from test_ask_api import ask, by_event, world  # noqa: F401 - the TPG world
from test_ask_review_api import review_on

from tela_api.ask.orchestrator import rule_refs

pytestmark = [needs_db, pytest.mark.integration]

QUESTION = "What does overdue go-lives mean?"


def explainer(sentences: Any) -> FakeGateway:
    """AG-08 ``explain`` replies with ``sentences`` (a callable gets the ids it was offered); everything else nothing."""
    def reply(task: str, messages: list[dict[str, Any]]) -> Any:
        if task != "explain":
            return {}
        offered = [p.split('"')[0] for p in messages[0]["content"].split('<knowledge id="')[1:]]
        return {"sentences": sentences(offered) if callable(sentences) else sentences}
    return FakeGateway({"AG-08": reply})


def explains(fake: FakeGateway) -> list[dict[str, Any]]:
    return [c for c in fake.calls if c["agent"] == "AG-08" and c["task"] == "explain"]


async def test_explain_without_knowledge_cites_the_rule_only(stack: httpx.AsyncClient, world: World,
                                                             owner_conn: psycopg.Connection) -> None:
    review_on(owner_conn, world)
    fake = explainer(lambda ids: [
        {"text": f"A go-live is overdue once its planned deployment date has passed [{ids[0]}].", "cites": [ids[0]]},
        {"text": "Projects without a planned date are left out.", "cites": [ids[1]]}])
    stack.app.state.gateway = fake.client()  # type: ignore[attr-defined]
    _, events = await ask(stack, world.tokens["creator"], QUESTION)
    ev = by_event(events)
    assert ev["retrieval"]["knowledge_refs"] == []  # nothing indexed: the rule is all AG-08 has
    [call] = explains(fake)
    system = call["messages"][0]["content"]
    measure = ev["complete"]["definition"]
    assert measure["rule_text"] in system and "## Certified definition" in system
    offered = [p.split('"')[0] for p in system.split('<knowledge id="')[1:]]
    assert offered == list(rule_refs(SimpleNamespace(key="overdue_go_lives")))  # type: ignore[arg-type]
    story = ev["narrative"]
    assert story["source"] == "llm" and story["grounded_on"] == "rule"
    assert story["sentences"][0]["text"] == "A go-live is overdue once its planned deployment date has passed."
    assert story["sentences"][0]["cites"] == story["sentences"][1]["cites"] == ["measure:overdue_go_lives"]
    assert story["sentences"][-1]["text"] == measure["blank_handling"]  # the certified text stays after it
    assert measure["citations"] == []  # no reference document to list


async def test_an_explanation_citing_anything_else_is_dropped(stack: httpx.AsyncClient, world: World,
                                                              owner_conn: psycopg.Connection) -> None:
    review_on(owner_conn, world)
    fake = explainer([{"text": "Overdue go-lives are late.", "cites": ["chunk:00000000-0000-4000-8000-00000000dead"]},
                      {"text": "They slip a lot.", "cites": []}])
    stack.app.state.gateway = fake.client()  # type: ignore[attr-defined]
    _, events = await ask(stack, world.tokens["creator"], QUESTION)
    story = by_event(events)["narrative"]
    assert len(explains(fake)) == 1
    assert story["source"] == "definition" and story["citation_check"] == "failed" and "late" not in str(story)


async def test_review_off_makes_no_explain_call_without_knowledge(stack: httpx.AsyncClient,
                                                                  world: World,
                                                                  owner_conn: psycopg.Connection) -> None:
    review_on(owner_conn, world, on=False)
    fake = explainer([{"text": "x", "cites": []}])
    stack.app.state.gateway = fake.client()  # type: ignore[attr-defined]
    _, events = await ask(stack, world.tokens["creator"], QUESTION)
    assert by_event(events)["narrative"]["source"] == "definition" and explains(fake) == []


async def test_narrate_stays_off_with_totals_off(stack: httpx.AsyncClient, world: World,
                                                 owner_conn: psycopg.Connection) -> None:
    review_on(owner_conn, world)
    owner_conn.execute("UPDATE ops.llm_settings SET allow_aggregates = false WHERE tenant_id = %s", (world.tenant_id,))
    fake = FakeGateway({"AG-08": {"headline": {"text": "x", "facts": []}, "sentences": []}})
    stack.app.state.gateway = fake.client()  # type: ignore[attr-defined]
    _, events = await ask(stack, world.tokens["creator"], "How many overdue go-lives are there?")
    assert "complete" in by_event(events)
    assert [c for c in fake.calls if c["task"] == "narrate"] == []
