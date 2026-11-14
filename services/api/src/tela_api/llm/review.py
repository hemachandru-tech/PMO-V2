"""LLM review of an agent's rules draft (ENH-026, US-239; FEAT-076; DEC-28).

Every agent keeps its rules: they run first and make a draft. With the tenant's "LLM review" switch on, the draft goes
to the LLM with the request, the candidates the rules considered and the knowledge already retrieved, and the LLM
replies with a verdict:

* ``confirm`` — the draft is right;
* ``correct`` — a replacement in the agent's own typed output;
* ``ask`` — 2 to 4 readings, when the request can fairly be read more than one way.

The caller's ``check`` (spec and scope validation, RULE-008, wording, layout guards …) runs on whatever the LLM
returns. A failed, slow, refused or invalid reply, or a replacement the check refuses, is ``fallback``: the draft
stands, so a review never makes a result worse than the rules alone. What a verdict does to the result is the caller's
choice under DEC-28: a correction never replaces the draft silently.

The review prompt carries the draft and its context only: never result rows (``narrate`` is the one task that may
carry aggregates, DEC-01 / RULE-019).
"""

from __future__ import annotations

import asyncio
import inspect
import json
import time
from collections.abc import Awaitable, Callable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Literal

from prometheus_client import Counter
from pydantic import BaseModel, Field

from tela_api.core.settings import get_settings
from tela_api.llm import planner_fit, trace
from tela_api.llm.client import AgentFailed, GatewayClient, run_agent
from tela_common.logging import get_logger

log = get_logger(__name__)
REVIEWS = Counter("tela_llm_reviews_total", "LLM reviews of a rules draft by verdict", ["agent", "verdict"])

Verdict = Literal["off", "confirm", "correct", "ask", "fallback"]
# Keys that would hold result rows: dropped from the draft and context before they reach the prompt.
NO_ROWS = frozenset({"rows", "records", "series", "result", "sample", "samples", "data"})
MAX_OPTIONS = 4
# A gold run measuring the review (US-246) forces it on or off whatever the tenant's switch says; None follows it.
_forced: ContextVar[bool | None] = ContextVar("tela_llm_review_forced", default=None)

FRAME = (
    "You review a draft that Tela's rules made for this request. Reply with verdict \"confirm\" when the draft does "
    "what the request asks, even if you would have worded it differently. Reply \"correct\" with a replacement only "
    "when the draft is wrong for the request. Reply \"ask\" with 2 to 4 options only when the request can fairly be "
    "read more than one way. Use only the names, keys and values given in the draft and context; never invent one. "
    "Give a short reason in plain words."
)


class ReviewOut[T: BaseModel](BaseModel):
    verdict: Literal["confirm", "correct", "ask"]
    replacement: T | None = None
    options: list[T] = Field(default_factory=list, max_length=MAX_OPTIONS)
    reason: str = Field(default="", max_length=400)


@dataclass
class Review[T: BaseModel]:
    verdict: Verdict
    replacement: T | None = None
    options: list[T] = field(default_factory=list)
    reason: str = ""

    @property
    def reviewed(self) -> bool:
        """The LLM's verdict was usable (confirm, correct or ask)."""
        return self.verdict in ("confirm", "correct", "ask")


def review_default(provider: str | None) -> bool:
    """DEC-28 point 3: review on for a hosted provider, off for a local one (each review adds a model call)."""
    return planner_fit.option(provider) == "hosted"


def review_on(settings: Mapping[str, Any]) -> bool:
    """Whether agents send their rules draft for review: the switch is on (or forced on) and a provider is set."""
    on = _forced.get()
    return bool((settings.get("llm_review") if on is None else on) and settings.get("provider"))


# The agents that send a rules draft for review (US-239 to US-245); the Agents screen offers the on/off for these.
REVIEW_AGENTS = ("AG-01", "AG-02", "AG-03", "AG-04", "AG-05", "AG-06", "AG-08")


def agent_default_on(agent: str) -> bool:
    """The deployment's default for one agent: on unless ``TELA_LLM_REVIEW_OFF_AGENTS`` lists it."""
    return agent not in get_settings().llm_review_off_agents


def agent_choice(settings: Mapping[str, Any], agent: str) -> bool | None:
    """The Admin's choice for one agent on the Agents screen (US-268), None when it follows the default."""
    got = (settings.get("review_agents") or {}).get(agent)
    return got if isinstance(got, bool) else None


def agent_on(settings: Mapping[str, Any], agent: str) -> bool:
    """Whether this agent reviews when the tenant's switch is on: the Admin's choice, else the deployment default."""
    choice = agent_choice(settings, agent)
    return agent_default_on(agent) if choice is None else choice


def agent_review_on(settings: Mapping[str, Any], agent: str) -> bool:
    """The tenant's switch is on (or forced on) and this agent is on."""
    return review_on(settings) and agent_on(settings, agent)


@contextmanager
def forced(on: bool | None) -> Iterator[None]:
    """Within the block, review is on or off whatever the tenant's switch says (US-246 gold gate); None changes
    nothing."""
    token = _forced.set(on)
    try:
        yield
    finally:
        _forced.reset(token)


def without_rows(data: Any) -> Any:
    """``data`` with every key that could hold result rows removed, at any depth."""
    if isinstance(data, BaseModel):
        data = data.model_dump(mode="json")
    if isinstance(data, Mapping):
        return {k: without_rows(v) for k, v in data.items() if k not in NO_ROWS}
    if isinstance(data, list | tuple):
        return [without_rows(v) for v in data]
    return data


Check = Callable[[Any], Awaitable[list[str]] | list[str]]


async def _errors(check: Check | None, item: Any) -> list[str]:
    if check is None:
        return []
    out = check(item)
    return list(await out) if inspect.isawaitable(out) else list(out)


async def review[T: BaseModel](gateway: GatewayClient | None, out_model: type[T], *, settings: Mapping[str, Any],
                               tenant_id: str, user_id: str | None, agent: str, task: str, system: str,
                               request: str, draft: Any, context: Any = None, check: Check | None = None,
                               turn_id: str | None = None, known_names: list[str] | None = None) -> Review[T]:
    """Send the rules ``draft`` for review and return the verdict; ``off`` when the switch is off (no call).

    ``draft`` is what the rules produced, in plain words or as the agent's output; ``check`` returns the errors that
    make a replacement or an option unusable (empty when it passes). ``off`` too when the agent is off on the Agents
    screen, or by the deployment default (``llm_review_off_agents``) when the Admin hasn't chosen."""
    if gateway is None or not agent_review_on(settings, agent):
        return Review("off")
    began = time.monotonic()
    draft, context = without_rows(draft), without_rows(context)
    await trace.step("rule", f"{agent} rules draft sent for LLM review", agent=agent,
                     detail={"task": task, "draft": draft})
    user = json.dumps({"request": request, "draft": draft, "context": context}, default=str, ensure_ascii=False)
    timeout = get_settings().llm_review_timeout_s
    try:
        out = await asyncio.wait_for(
            run_agent(gateway, ReviewOut[out_model], tenant_id=tenant_id, user_id=user_id,  # type: ignore[valid-type]
                      agent=agent, task=task, system=system + "\n\n" + FRAME, user=user, turn_id=turn_id,
                      known_names=known_names),
            timeout)
    except (AgentFailed, TimeoutError) as exc:
        reason = "the review took too long" if isinstance(exc, TimeoutError) else f"no usable review ({exc.code})"
        return await _done(agent, Review("fallback", reason=reason), began)

    result: Review[T] = Review(out.verdict, reason=out.reason)
    if out.verdict == "correct":
        if out.replacement is None:
            return await _done(agent, Review("fallback", reason="a correction without a replacement"), began)
        if errors := await _errors(check, out.replacement):
            await trace.step("validate", f"{agent} correction refused by the checks", agent=agent, status="failed",
                             detail={"errors": errors[:5]})
            return await _done(agent, Review("fallback", reason="the correction failed the checks: " + errors[0]),
                               began)
        result.replacement = out.replacement
    elif out.verdict == "ask":
        kept = [o for o in out.options if not await _errors(check, o)]
        if len(kept) < 2:
            return await _done(agent, Review("fallback", reason="fewer than two usable options"), began)
        result.options = kept[:MAX_OPTIONS]
    return await _done(agent, result, began)


async def _done(agent: str, result: Review[Any], began: float) -> Review[Any]:
    REVIEWS.labels(agent=agent, verdict=result.verdict).inc()
    label = {"confirm": "LLM review: confirmed", "correct": "LLM review: corrected", "ask": "LLM review: asked",
             "fallback": "LLM review: fallback, the rules draft stands"}[result.verdict]
    await trace.step("decision", label, agent=agent, duration_ms=int((time.monotonic() - began) * 1000),
                     status="failed" if result.verdict == "fallback" else "ok",
                     detail={"verdict": result.verdict, "reason": result.reason, "options": len(result.options)})
    log.info("llm.review", agent=agent, verdict=result.verdict)
    return result
