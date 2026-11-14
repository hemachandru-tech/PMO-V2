"""Anthropic Claude (DEC-01): Messages API with structured JSON output; no embeddings API."""

from __future__ import annotations

from typing import Any

import anthropic

from tela_common.errors import ErrorCode, TelaError
from tela_gateway.providers.base import ProviderResult, closed_schema, split_system, timed_out, unavailable

# Thinking is always on for these models and counts against max_tokens, so short tasks get headroom.
MIN_MAX_TOKENS = 2048
# Models that take the server-side refusal fallback ("default" routes by refusal category).
FALLBACK_MODELS = {"claude-fable-5-1", "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5"}
FALLBACK_BETA = "server-side-fallback-2026-07-01"


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, api_key: str, *, timeout: float = 30.0, client: Any = None) -> None:
        self._client = client or anthropic.AsyncAnthropic(api_key=api_key, timeout=timeout, max_retries=1)

    async def generate(
        self, *, model: str, messages: list[dict[str, str]], json_schema: dict[str, Any] | None, max_tokens: int,
        temperature: float | None = None,
    ) -> ProviderResult:
        # ``temperature`` is not sent: thinking is always on for these models, which needs the default (US-182).
        system, turns = split_system(messages)
        output_config: dict[str, Any] = {"effort": "low"}  # short, bounded agent tasks
        if json_schema is not None:
            output_config["format"] = {"type": "json_schema", "schema": closed_schema(json_schema)}
        body: dict[str, Any] = {"model": model, "max_tokens": max(max_tokens, MIN_MAX_TOKENS),
                                "messages": turns, "output_config": output_config}
        if system:
            body["system"] = system
        try:
            if model in FALLBACK_MODELS:
                resp = await self._client.beta.messages.create(**body, betas=[FALLBACK_BETA], fallbacks="default")
            else:
                resp = await self._client.messages.create(**body)
        except anthropic.APITimeoutError as exc:
            raise timed_out() from exc
        except anthropic.APIStatusError as exc:
            raise unavailable(exc.status_code) from exc
        except anthropic.APIConnectionError as exc:
            raise unavailable() from exc
        if resp.stop_reason == "refusal":
            raise TelaError(ErrorCode.INVALID_OUTPUT, "The model declined to answer.")
        text = "".join(b.text for b in resp.content if b.type == "text")
        return ProviderResult(text=text, prompt_tokens=int(resp.usage.input_tokens or 0),
                              completion_tokens=int(resp.usage.output_tokens or 0), model=resp.model or model)

    async def embed(self, *, model: str, texts: list[str], dim: int | None = None) -> list[list[float]]:
        raise TelaError(ErrorCode.PROVIDER_NOT_ALLOWED, "Anthropic has no embeddings service; choose another one.")
