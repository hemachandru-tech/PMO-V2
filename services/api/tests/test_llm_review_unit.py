"""LLM review of a rules draft (ENH-026, US-239; DEC-28): verdicts, fallbacks and what reaches the prompt."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import pytest
from conftest import FakeGateway
from pydantic import BaseModel

from tela_api.core.settings import get_settings
from tela_api.llm import review as rv
from tela_api.llm import trace

ON = {"provider": "anthropic", "llm_review": True}


class Pick(BaseModel):
    measure: str


@asynccontextmanager
async def no_log(_: Any) -> AsyncIterator[Any]:
    raise ConnectionError("no database in unit tests")
    yield  # pragma: no cover


@pytest.fixture(autouse=True)
def _no_trace_db(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(trace, "tenant_tx", no_log)


def known(item: Pick) -> list[str]:
    return [] if item.measure in ("open_projects", "closed_projects") else [f"unknown measure {item.measure}"]


async def call(fake: FakeGateway | None, settings: dict[str, Any] = ON, **kw: Any) -> rv.Review[Pick]:
    return await rv.review(fake.client() if fake else None, Pick, settings=settings, tenant_id="t1", user_id="u1",
                           agent="AG-03", task="plan", system="You plan questions.", request="open projects",
                           draft=kw.pop("draft", {"measure": "open_projects"}), check=kw.pop("check", known), **kw)


def test_defaults_follow_the_provider() -> None:
    assert rv.review_default("anthropic") is True and rv.review_default("azure_openai") is True
    assert rv.review_default("ollama") is False and rv.review_default(None) is False
    assert rv.review_on({"provider": "ollama", "llm_review": True}) is True
    assert rv.review_on({"provider": None, "llm_review": True}) is False  # no provider: rules only
    assert rv.review_on({"provider": "anthropic", "llm_review": False}) is False


async def test_switch_off_makes_no_call() -> None:
    fake = FakeGateway({"AG-03": {"verdict": "confirm"}})
    out = await call(fake, settings={"provider": "anthropic", "llm_review": False})
    assert out.verdict == "off" and not out.reviewed and fake.calls == []
    assert (await call(None)).verdict == "off"


async def test_confirm() -> None:
    fake = FakeGateway({"AG-03": {"verdict": "confirm", "reason": "matches"}})
    out = await call(fake)
    assert out.verdict == "confirm" and out.reviewed and out.reason == "matches"
    sent = fake.calls[0]
    assert sent["agent"] == "AG-03" and sent["task"] == "plan"
    assert "Reply with verdict" in sent["messages"][0]["content"]  # the shared review frame
    assert json.loads(sent["messages"][1]["content"])["draft"] == {"measure": "open_projects"}


async def test_correction_kept_only_when_it_passes_the_checks() -> None:
    good = await call(FakeGateway({"AG-03": {"verdict": "correct", "replacement": {"measure": "closed_projects"}}}))
    assert good.verdict == "correct" and good.replacement == Pick(measure="closed_projects")
    bad = await call(FakeGateway({"AG-03": {"verdict": "correct", "replacement": {"measure": "made_up"}}}))
    assert bad.verdict == "fallback" and bad.replacement is None and "made_up" in bad.reason
    empty = await call(FakeGateway({"AG-03": {"verdict": "correct"}}))
    assert empty.verdict == "fallback"


async def test_ask_needs_two_usable_options() -> None:
    two = await call(FakeGateway({"AG-03": {"verdict": "ask", "options": [
        {"measure": "open_projects"}, {"measure": "closed_projects"}, {"measure": "made_up"}]}}))
    assert two.verdict == "ask" and [o.measure for o in two.options] == ["open_projects", "closed_projects"]
    one = await call(FakeGateway({"AG-03": {"verdict": "ask", "options": [
        {"measure": "open_projects"}, {"measure": "made_up"}]}}))
    assert one.verdict == "fallback" and one.options == []


async def test_async_check() -> None:
    async def check(item: Pick) -> list[str]:
        return known(item)

    out = await call(FakeGateway({"AG-03": {"verdict": "correct", "replacement": {"measure": "made_up"}}}),
                     check=check)
    assert out.verdict == "fallback"


@pytest.mark.parametrize("fake", [FakeGateway(fail="planner_unavailable"), FakeGateway(fail="leak_blocked"),
                                  FakeGateway({"AG-03": {"verdict": "maybe"}})])
async def test_failures_fall_back(fake: FakeGateway) -> None:
    out = await call(fake)
    assert out.verdict == "fallback" and not out.reviewed


async def test_slow_review_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    async def slow(*_: Any, **__: Any) -> Any:
        await asyncio.sleep(5)

    monkeypatch.setattr(rv, "run_agent", slow)
    monkeypatch.setattr(get_settings(), "llm_review_timeout_s", 0.05)
    out = await call(FakeGateway())
    assert out.verdict == "fallback" and "too long" in out.reason


async def test_no_rows_reach_the_prompt() -> None:
    fake = FakeGateway({"AG-03": {"verdict": "confirm"}})
    await call(fake, draft={"measure": "open_projects", "filters": [{"ref": "status", "values": ["Active"]}],
                            "rows": [[1, "secret"]]},
               context={"result": {"data": [1]}, "candidates": ["open_projects"], "samples": ["x"]})
    sent = json.loads(fake.calls[0]["messages"][1]["content"])
    assert "rows" not in sent["draft"] and sent["draft"]["filters"][0]["values"] == ["Active"]
    assert sent["context"] == {"candidates": ["open_projects"]}
    assert "secret" not in fake.calls[0]["messages"][1]["content"]


def test_agent_choice_beats_the_deployment_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """US-268: an Admin's choice per agent wins; an agent left out follows ``llm_review_off_agents``."""
    monkeypatch.setattr(get_settings(), "llm_review_off_agents", ["AG-05", "AG-06"])
    base = {"provider": "anthropic", "llm_review": True}
    assert rv.agent_review_on(base, "AG-03") and not rv.agent_review_on(base, "AG-05")
    chosen = {**base, "review_agents": {"AG-05": True, "AG-03": False, "AG-06": "yes"}}
    assert rv.agent_review_on(chosen, "AG-05") and not rv.agent_review_on(chosen, "AG-03")
    assert rv.agent_choice(chosen, "AG-06") is None and not rv.agent_review_on(chosen, "AG-06")
    # The tenant switch still has to be on.
    assert not rv.agent_review_on({**chosen, "llm_review": False}, "AG-05")


async def test_an_agent_turned_off_makes_no_review_call(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "llm_review_off_agents", [])
    fake = FakeGateway({"AG-03": {"verdict": "confirm"}})
    settings = {"provider": "anthropic", "llm_review": True, "review_agents": {"AG-03": False}}
    got = await rv.review(fake.client(), Pick, settings=settings, tenant_id="t", user_id=None, agent="AG-03",
                          task="plan_review", system="s", request="q", draft={"key": "a"})
    assert got.verdict == "off" and fake.calls == []
