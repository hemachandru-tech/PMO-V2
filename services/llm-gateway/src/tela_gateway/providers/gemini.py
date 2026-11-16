"""Google Gemini API (DEC-01): generateContent with a JSON schema, and embeddings."""

from __future__ import annotations

from typing import Any

import httpx
from google import genai
from google.genai import errors, types

from tela_common.errors import ErrorCode, TelaError
from tela_gateway.providers.base import ProviderResult, split_system, timed_out, unavailable, with_retries


class GeminiProvider:
    name = "gemini"

    def __init__(self, api_key: str, *, timeout: float = 30.0, client: Any = None) -> None:
        self._client = client or genai.Client(
            api_key=api_key, http_options=types.HttpOptions(timeout=int(timeout * 1000)))

    async def _call(self, fn: Any, **kwargs: Any) -> Any:
        """One provider call, tried again after a short wait on a rate limit or a passing 5xx (the Gemini client,
        unlike the OpenAI and Anthropic ones, does not retry by itself)."""
        return await with_retries(lambda: self._call_once(fn, **kwargs))

    async def _call_once(self, fn: Any, **kwargs: Any) -> Any:
        try:
            return await fn(**kwargs)
        except errors.APIError as exc:
            raise unavailable(getattr(exc, "code", None)) from exc
        except httpx.TimeoutException as exc:
            raise timed_out() from exc
        except httpx.HTTPError as exc:
            raise unavailable() from exc

    async def generate(
        self, *, model: str, messages: list[dict[str, str]], json_schema: dict[str, Any] | None, max_tokens: int,
        temperature: float | None = None,
    ) -> ProviderResult:
        system, turns = split_system(messages)
        contents = [types.Content(role="model" if m["role"] == "assistant" else "user",
                                  parts=[types.Part(text=m["content"])]) for m in turns]
        config_kwargs: dict[str, Any] = {
            "system_instruction": system or None,
            "temperature": temperature or 0,
            "max_output_tokens": max(max_tokens, 2048),
        }
        if "flash-lite" not in model.lower():
            config_kwargs["thinking_config"] = types.ThinkingConfig(thinking_budget=0)
        config = types.GenerateContentConfig(**config_kwargs)
        if json_schema is not None:
            config.response_mime_type = "application/json"
            config.response_json_schema = json_schema
        resp = await self._call(self._client.aio.models.generate_content, model=model, contents=contents,
                                config=config)
        blocked = getattr(resp, "prompt_feedback", None) and getattr(resp.prompt_feedback, "block_reason", None)
        if blocked:
            raise TelaError(ErrorCode.INVALID_OUTPUT, "The model declined to answer.")
        usage = resp.usage_metadata
        return ProviderResult(text=resp.text or "",
                              prompt_tokens=int(getattr(usage, "prompt_token_count", 0) or 0),
                              completion_tokens=int(getattr(usage, "candidates_token_count", 0) or 0),
                              model=model)

    async def embed(self, *, model: str, texts: list[str], dim: int | None = None) -> list[list[float]]:
        config = types.EmbedContentConfig(output_dimensionality=dim) if dim else None
        resp = await self._call(self._client.aio.models.embed_content, model=model, contents=texts, config=config)
        return [list(e.values or []) for e in resp.embeddings or []]
