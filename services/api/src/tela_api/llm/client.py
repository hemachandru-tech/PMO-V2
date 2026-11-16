"""Gateway client and agent harness (technical_document §Agent implementation).

Agents never call providers: every call goes through the LLM gateway, which applies policy, masking,
leak scanning and the metadata ledger. The harness asks for JSON matching a schema, validates it with
Pydantic, retries **once** with the validation error appended, then gives up with ``agent_failed``. The schema also
goes to the gateway, which checks every answer against it (and records failures in the ledger) and holds a local
model's decoding to it (US-191).
Callers always have a deterministic fallback, so a missing or failing model never blocks the user.
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

import httpx
from pydantic import BaseModel, ValidationError

from tela_api.agents import prompts as agent_prompts
from tela_api.llm import trace
from tela_common import service_auth
from tela_common.errors import ErrorCode, TelaError
from tela_common.logging import get_logger
from tela_common.observability import propagation_headers

log = get_logger(__name__)
GATEWAY_AUDIENCE = "tela-llm-gateway"
# Model calls made inside ``counting()``: a gold run's LLM calls per question (US-246).
_calls: ContextVar[list[int] | None] = ContextVar("tela_llm_calls", default=None)


@contextmanager
def counting() -> Iterator[list[int]]:
    """Count the model calls (repairs included) made within the block; read ``[0]`` after it."""
    box = [0]
    token = _calls.set(box)
    try:
        yield box
    finally:
        _calls.reset(token)


class AgentFailed(Exception):
    """The model was unavailable or its output stayed invalid after one repair round."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class GatewayClient:
    def __init__(self, base_url: str, service_key: bytes, http: httpx.AsyncClient | None = None,
                 timeout_s: float = 90.0) -> None:
        self.base_url = base_url.rstrip("/")
        self._key = service_key
        self._http = http or httpx.AsyncClient(timeout=timeout_s)
        self._catalog: tuple[float, dict[str, Any]] | None = None

    async def aclose(self) -> None:
        await self._http.aclose()

    def _headers(self) -> dict[str, str]:
        token = service_auth.issue(self._key, sub="tela-api", aud=GATEWAY_AUDIENCE, ttl_s=60)
        return propagation_headers({"Authorization": f"Bearer {token}"})

    async def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        try:
            resp = await self._http.post(f"{self.base_url}{path}", json=body, headers=self._headers())
        except httpx.TimeoutException as exc:
            raise TelaError(ErrorCode.LLM_TIMEOUT, "The language model took too long to answer.") from exc
        except httpx.HTTPError as exc:
            raise TelaError(ErrorCode.PLANNER_UNAVAILABLE, "The language model is not reachable.") from exc
        if resp.status_code >= 400:
            try:
                err = resp.json()["error"]
                raise TelaError(ErrorCode(err["code"]), err.get("message") or "The model call failed.",
                                err.get("details") or {})
            except (ValueError, KeyError) as exc:
                raise TelaError(ErrorCode.PLANNER_UNAVAILABLE, "The language model call failed.") from exc
        data: dict[str, Any] = resp.json()
        return data

    async def providers(self) -> dict[str, Any]:
        try:
            resp = await self._http.get(f"{self.base_url}/providers", headers=self._headers())
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise TelaError(ErrorCode.PLANNER_UNAVAILABLE, "The LLM gateway is not reachable.") from exc
        data: dict[str, Any] = resp.json()
        return data

    async def catalog(self, ttl_s: float = 60.0) -> dict[str, Any]:
        """``providers()`` kept for ``ttl_s`` seconds: Ask reads the default provider on every planned question."""
        if self._catalog is None or time.monotonic() - self._catalog[0] > ttl_s:
            self._catalog = (time.monotonic(), await self.providers())
        return self._catalog[1]

    async def embed(self, *, tenant_id: str, texts: list[str]) -> dict[str, Any]:
        """Embeddings through the gateway (local Ollama only, DEC-10): labels and definitions, never row values."""
        return await self._post("/embed", {"tenant_id": tenant_id, "texts": texts})

    async def rerank(self, *, tenant_id: str, query: str, texts: list[str]) -> dict[str, Any]:
        """Cross-encoder scores for retrieved knowledge (US-174); ``scores`` is null when no reranker is deployed."""
        return await self._post("/rerank", {"tenant_id": tenant_id, "query": query, "texts": texts})

    async def generate(self, *, tenant_id: str, user_id: str | None, agent: str, task: str,
                       messages: list[dict[str, str]], json_schema: dict[str, Any] | None = None,
                       turn_id: str | None = None, known_names: list[str] | None = None,
                       max_tokens: int = 1500, temperature: float | None = None,
                       capture: bool = False) -> dict[str, Any]:
        """``capture``: the gateway returns the masked messages it sent and the raw reply under ``trace`` (also on
        a refusal, in the error's details) for the agent run log (US-226)."""
        body: dict[str, Any] = {
            "tenant_id": tenant_id, "user_id": user_id, "turn_id": turn_id, "agent": agent, "task": task,
            "messages": messages, "json_schema": json_schema, "max_tokens": max_tokens,
            "known_names": known_names or [],
        }
        if capture:
            body["capture"] = True
        if temperature is not None:  # sampled answers for voting (US-182); deterministic otherwise
            body["temperature"] = temperature
        return await self._post("/generate", body)


async def run_agent[T: BaseModel](client: GatewayClient | None, model: type[T], *, tenant_id: str, user_id: str | None,
                    agent: str, task: str, system: str, user: str, turn_id: str | None = None,
                    known_names: list[str] | None = None, temperature: float | None = None, retries: int = 1,
                    max_tokens: int = 1500) -> T:
    """Call an agent and return a validated object, or raise AgentFailed. An answer that fails the schema is sent back
    for a fix at most ``retries`` times (0 for a vote sample: the other samples stand in for it)."""
    async with trace.agent_run(tenant_id, user_id, "agent", title=f"{agent} {task}", turn_id=turn_id, agent=agent):
        try:
            return await _run_agent(client, model, tenant_id=tenant_id, user_id=user_id, agent=agent, task=task,
                                    system=system, user=user, turn_id=turn_id, known_names=known_names,
                                    temperature=temperature, retries=retries, max_tokens=max_tokens)
        except AgentFailed as exc:
            await trace.step("fallback", f"{agent} gave no usable answer ({exc.code}); the caller falls back",
                             agent=agent, status="failed", detail={"code": exc.code, "task": task})
            raise


def _llm(task: str, out: dict[str, Any]) -> dict[str, Any]:
    return {"task": task, "provider": out.get("provider"), "model": out.get("model"), "usage": out.get("usage"),
            "trace": out.get("trace")}


async def _run_agent[T: BaseModel](client: GatewayClient | None, model: type[T], *, tenant_id: str,
                                   user_id: str | None, agent: str, task: str, system: str, user: str,
                                   turn_id: str | None, known_names: list[str] | None, temperature: float | None,
                                   retries: int, max_tokens: int = 1500) -> T:
    if client is None:
        raise AgentFailed("planner_unavailable", "No language model is configured.")
    run = trace.current()
    capture = run is not None and run.capture_text
    schema = model.model_json_schema()
    system, tuned = await agent_prompts.apply(tenant_id, agent, system)  # an Admin's prompts and skills (US-264/265)
    tuned_detail = {"tuned": tuned} if tuned["prompts"] or tuned["skills"] else {}
    messages = [{"role": "system", "content": system + "\nAnswer with JSON only, matching this schema:\n"
                 + json.dumps(schema)}, {"role": "user", "content": user}]
    for attempt in range(retries + 1):
        last = attempt == retries
        label = f"{agent} {task}" + (f", fix {attempt}" if attempt else "")
        began = time.monotonic()
        if (box := _calls.get()) is not None:
            box[0] += 1
        try:
            out = await client.generate(tenant_id=tenant_id, user_id=user_id, agent=agent, task=task,
                                        messages=messages, json_schema=schema, turn_id=turn_id,
                                        known_names=known_names, temperature=temperature, capture=capture,
                                        max_tokens=max_tokens)
        except TelaError as exc:
            took = int((time.monotonic() - began) * 1000)
            agent_prompts.record(agent=agent, task=task, tuned=tuned, ok=False, reply=exc.message, ms=took,
                                 code=str(exc.code))
            got = exc.details.get("trace") if isinstance(exc.details, dict) else None
            await trace.step("llm_call", label, agent=agent, duration_ms=took,
                             status="blocked" if exc.code == ErrorCode.LEAK_BLOCKED else "failed",
                             detail={"code": str(exc.code), "message": exc.message,
                                     "reasons": exc.details.get("reasons"), "error": exc.details.get("error"),
                                     "temperature": temperature},
                             llm={"task": task, "provider": (got or {}).get("provider"),
                                  "model": (got or {}).get("model"), "usage": (got or {}).get("usage"),
                                  "trace": got})
            if exc.code != ErrorCode.INVALID_OUTPUT or last:
                raise AgentFailed(str(exc.code), exc.message) from exc
            log.info("agent.repair", agent=agent, attempt=attempt)
            await trace.step("retry", f"{agent} asked to fix its answer", agent=agent)
            if "text" in exc.details:  # the gateway's schema check failed: show the answer and what was wrong
                messages = [*messages, {"role": "assistant", "content": str(exc.details["text"])},
                            {"role": "user", "content": f"That did not validate: {exc.details.get('error')} Fix it."}]
            else:
                messages = [*messages, {"role": "user", "content": "The previous answer was not valid JSON for the "
                                                                   "schema. Try again."}]
            continue
        agent_prompts.record(agent=agent, task=task, tuned=tuned, ok=True, reply=out.get("text"),
                             ms=int((time.monotonic() - began) * 1000), model=out.get("model"))
        await trace.step("llm_call", label, agent=agent, duration_ms=int((time.monotonic() - began) * 1000),
                         detail={"latency_ms": out.get("latency_ms"), "temperature": temperature, **tuned_detail},
                         llm=_llm(task, out))
        try:
            return model.model_validate(out.get("json") if out.get("json") is not None else json.loads(out["text"]))
        except (ValidationError, ValueError) as exc:
            await trace.step("validate", f"{agent} answer did not match {model.__name__}", agent=agent,
                             status="failed", detail={"error": str(exc)[:1500]})
            if last:
                break
            log.info("agent.repair", agent=agent, attempt=attempt)
            await trace.step("retry", f"{agent} asked to fix its answer", agent=agent)
            messages = [*messages, {"role": "assistant", "content": out.get("text", "")[:4000]},
                        {"role": "user", "content": "That did not validate: " + str(exc)[:1500] + " Fix it."}]
    raise AgentFailed("agent_failed", "The model's answer did not match the expected format.")
