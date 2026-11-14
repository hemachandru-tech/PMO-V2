"""Planner model fit (US-191; DEC-23): the schema goes to the gateway, a failed answer is retried at most once (never
for a vote sample among several), voting runs one sample on a local Ollama, and each option has its models."""

from __future__ import annotations

from typing import Any

import pytest
from conftest import FakeGateway
from pydantic import BaseModel

from tela_api.llm import planner_fit
from tela_api.llm.client import AgentFailed, run_agent


class Out(BaseModel):
    measure: str


def call(fake: FakeGateway, **kw: Any) -> Any:
    return run_agent(fake.client(), Out, tenant_id="t", user_id="u", agent="AG-03", task="plan", system="s",
                     user="q", **kw)


async def test_the_schema_goes_to_the_gateway() -> None:
    fake = FakeGateway({"AG-03": {"measure": "measure:open_projects"}})
    assert (await call(fake)).measure == "measure:open_projects"
    assert fake.calls[0]["json_schema"] == Out.model_json_schema()


async def test_a_schema_failure_is_fixed_once_with_the_answer_and_the_error() -> None:
    replies = iter([{"__invalid__": '{"measure": 5}'}, {"measure": "measure:open_projects"}])
    fake = FakeGateway({"AG-03": lambda task, messages: next(replies)})
    assert (await call(fake)).measure == "measure:open_projects"
    fix = fake.calls[1]["messages"]
    assert fix[-2] == {"role": "assistant", "content": '{"measure": 5}'}
    assert fix[-1]["content"] == "That did not validate: measure: not a string Fix it."


@pytest.mark.parametrize(("retries", "calls"), [(1, 2), (0, 1)])
async def test_failed_answers_are_retried_at_most_retries_times(retries: int, calls: int) -> None:
    fake = FakeGateway({"AG-03": {"__invalid__": "{}"}})
    with pytest.raises(AgentFailed) as exc:
        await call(fake, retries=retries)
    assert exc.value.code == "invalid_output" and len(fake.calls) == calls


async def test_an_answer_the_model_check_rejects_is_also_retried_once() -> None:
    fake = FakeGateway({"AG-03": {"measure": 5}})  # passes the fake gateway, fails Pydantic
    with pytest.raises(AgentFailed) as exc:
        await call(fake)
    assert exc.value.code == "agent_failed" and len(fake.calls) == 2


def test_voting_runs_one_sample_on_a_local_model() -> None:
    assert planner_fit.vote_samples("ollama") == 1
    assert planner_fit.vote_samples("anthropic") == 3
    assert planner_fit.vote_samples(None) == 3


def test_each_option_has_a_recommended_model_and_a_minimum() -> None:
    assert planner_fit.option("ollama") == "local" and planner_fit.option("gemini") == "hosted"
    assert planner_fit.option(None) is None
    local, hosted = planner_fit.recommendation("local"), planner_fit.recommendation("hosted")
    assert local["recommended"] and local["minimum"] and hosted["recommended"] and hosted["minimum"]
    assert planner_fit.recommendation(None) == {"recommended": None, "minimum": None}


async def test_the_gateway_catalog_is_kept_for_a_minute() -> None:
    fake = FakeGateway(default_provider="ollama")
    client = fake.client()
    seen: list[int] = []
    orig = client.providers

    async def counting() -> dict[str, Any]:
        seen.append(1)
        return await orig()
    client.providers = counting  # type: ignore[method-assign]
    assert (await client.catalog())["default_provider"] == "ollama"
    await client.catalog()
    assert len(seen) == 1
    await client.catalog(ttl_s=0)
    assert len(seen) == 2
