"""Local Ollama runtime: chat with structured output and embeddings (dev default; Q-016 / DEC-10)."""

from __future__ import annotations

import asyncio
import random
from typing import Any

import httpx

from tela_common.errors import ErrorCode, TelaError
from tela_gateway.providers.base import ProviderResult


class OllamaProvider:
    name = "ollama"

    def __init__(self, base_url: str, *, timeout: float = 30.0, http: httpx.AsyncClient | None = None) -> None:
        self._http = http or httpx.AsyncClient(base_url=base_url, timeout=timeout)

    async def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        for attempt in range(2):  # one retry on 5xx / 429 with jitter
            try:
                resp = await self._http.post(path, json=body)
            except httpx.TimeoutException as exc:
                raise TelaError(ErrorCode.LLM_TIMEOUT, "The language model did not answer in time.") from exc
            except httpx.HTTPError as exc:
                raise TelaError(ErrorCode.PLANNER_UNAVAILABLE, "The language model is unavailable.") from exc
            if resp.status_code in (429, 500, 502, 503, 504) and attempt == 0:
                await asyncio.sleep(0.5 + random.random())  # noqa: S311 - jitter, not crypto
                continue
            if resp.status_code >= 400:
                raise TelaError(ErrorCode.PLANNER_UNAVAILABLE, "The language model is unavailable.",
                                {"status": resp.status_code})
            data: dict[str, Any] = resp.json()
            return data
        raise TelaError(ErrorCode.PLANNER_UNAVAILABLE, "The language model is unavailable.")

    async def generate(
        self, *, model: str, messages: list[dict[str, str]], json_schema: dict[str, Any] | None, max_tokens: int,
        temperature: float | None = None, think: bool = False,
    ) -> ProviderResult:
        body: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": False,
            # False is accepted by every model; True is refused by one that cannot think (llama3.2).
            **({} if think else {"think": False}),
            "options": {"temperature": temperature or 0, "num_predict": max_tokens},
        }
        if json_schema is not None:
            body["format"] = json_schema
        data = await self._post("/api/chat", body)
        return ProviderResult(
            text=data.get("message", {}).get("content", ""),
            prompt_tokens=int(data.get("prompt_eval_count") or 0),
            completion_tokens=int(data.get("eval_count") or 0),
            model=model,
        )

    async def embed(self, *, model: str, texts: list[str], dim: int | None = None) -> list[list[float]]:
        data = await self._post("/api/embed", {"model": model, "input": texts})
        vectors: list[list[float]] = data.get("embeddings", [])
        return vectors
