"""Common provider interface: generate(messages, json_schema, max_tokens, temperature) -> (text, usage)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from tela_common.errors import ErrorCode, TelaError


@dataclass(frozen=True)
class ProviderResult:
    text: str
    prompt_tokens: int
    completion_tokens: int
    model: str


@dataclass(frozen=True)
class Credentials:
    """What a keyed provider needs: the tenant's key (from the secrets store) and, for Azure, its endpoint."""

    api_key: str
    endpoint: str | None = None


class Provider(Protocol):
    name: str

    async def generate(
        self,
        *,
        model: str,
        messages: list[dict[str, str]],
        json_schema: dict[str, Any] | None,
        max_tokens: int,
        temperature: float | None = None,  # None: deterministic (0) where the provider allows it
    ) -> ProviderResult: ...

    async def embed(self, *, model: str, texts: list[str], dim: int | None = None) -> list[list[float]]: ...


def split_system(messages: list[dict[str, str]]) -> tuple[str, list[dict[str, str]]]:
    """Providers that take the system prompt apart (Anthropic, Gemini): joined system text + the other turns."""
    system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
    return system, [m for m in messages if m["role"] != "system"]


def closed_schema(schema: Any) -> Any:
    """A copy of ``schema`` with ``additionalProperties: false`` on every object (structured-output engines need it)."""
    if isinstance(schema, list):
        return [closed_schema(s) for s in schema]
    if not isinstance(schema, dict):
        return schema
    out = {k: closed_schema(v) for k, v in schema.items()}
    if out.get("type") == "object" or "properties" in out:
        out.setdefault("additionalProperties", False)
    return out


TRANSIENT = frozenset({429, 500, 502, 503, 504})  # rate limit or a passing provider fault: worth another try
RETRY_DELAYS_S = (2.0, 5.0)  # waits before the second and third try


def unavailable(status: int | None = None) -> TelaError:
    if status in (401, 403):
        return TelaError(ErrorCode.PLANNER_UNAVAILABLE, "The model provider refused the API key.", {"status": status})
    details = {"status": status} if status else {}
    if status in (400, 404):  # the provider read the request and said no: a wrong model name is the usual cause
        return TelaError(ErrorCode.PLANNER_UNAVAILABLE, "The model provider rejected the request (HTTP "
                         f"{status}): check the model name (its id, not its display name).", details)
    if status == 429:
        return TelaError(ErrorCode.PLANNER_UNAVAILABLE, "The model provider is rate-limiting this key (HTTP 429).",
                         details)
    return TelaError(ErrorCode.PLANNER_UNAVAILABLE, "The language model is unavailable.", details)


async def with_retries(call: Any, *, delays: tuple[float, ...] = RETRY_DELAYS_S, sleep: Any = None) -> Any:
    """Run ``call()`` (a coroutine factory); on a transient provider error (rate limit, 5xx) wait and try again,
    up to ``len(delays)`` more times. Anything else, or the last failure, is raised as it is."""
    import asyncio
    pause = sleep or asyncio.sleep
    for i in range(len(delays) + 1):
        try:
            return await call()
        except TelaError as exc:
            status = (exc.details or {}).get("status")
            if exc.code != ErrorCode.PLANNER_UNAVAILABLE or status not in TRANSIENT or i == len(delays):
                raise
            await pause(delays[i])
    raise AssertionError("unreachable")


def timed_out() -> TelaError:
    return TelaError(ErrorCode.LLM_TIMEOUT, "The language model did not answer in time.")
