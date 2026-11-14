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


def unavailable(status: int | None = None) -> TelaError:
    if status in (401, 403):
        return TelaError(ErrorCode.PLANNER_UNAVAILABLE, "The model provider refused the API key.", {"status": status})
    details = {"status": status} if status else {}
    return TelaError(ErrorCode.PLANNER_UNAVAILABLE, "The language model is unavailable.", details)


def timed_out() -> TelaError:
    return TelaError(ErrorCode.LLM_TIMEOUT, "The language model did not answer in time.")
