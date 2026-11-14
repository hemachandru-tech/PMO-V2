"""OpenAI and Azure OpenAI (DEC-01): chat completions with a JSON schema, and embeddings.

On Azure the "model" is the tenant's deployment name and the endpoint is the tenant's resource
(``https://<resource>.openai.azure.com``), which also fixes the region.
"""

from __future__ import annotations

from typing import Any

import openai

from tela_common.errors import ErrorCode, TelaError
from tela_gateway.providers.base import ProviderResult, timed_out, unavailable


class OpenAIProvider:
    name = "openai"

    def __init__(self, api_key: str, *, timeout: float = 30.0, endpoint: str | None = None,
                 api_version: str | None = None, client: Any = None) -> None:
        if client is not None:
            self._client = client
        elif endpoint:
            self.name = "azure_openai"
            self._client = openai.AsyncAzureOpenAI(api_key=api_key, azure_endpoint=endpoint, api_version=api_version,
                                                   timeout=timeout, max_retries=1)
        else:
            self._client = openai.AsyncOpenAI(api_key=api_key, timeout=timeout, max_retries=1)

    async def _call(self, fn: Any, **kwargs: Any) -> Any:
        try:
            return await fn(**kwargs)
        except openai.APITimeoutError as exc:
            raise timed_out() from exc
        except openai.APIStatusError as exc:
            raise unavailable(exc.status_code) from exc
        except openai.APIConnectionError as exc:
            raise unavailable() from exc

    async def generate(
        self, *, model: str, messages: list[dict[str, str]], json_schema: dict[str, Any] | None, max_tokens: int,
        temperature: float | None = None,
    ) -> ProviderResult:
        body: dict[str, Any] = {"model": model, "messages": messages, "max_completion_tokens": max_tokens}
        if temperature is not None:  # sampled answers (AG-03 voting, US-182); the provider default otherwise
            body["temperature"] = temperature
        if json_schema is not None:
            # Not strict: strict mode would make every property required. The gateway validates the answer anyway.
            body["response_format"] = {"type": "json_schema",
                                       "json_schema": {"name": "answer", "schema": json_schema, "strict": False}}
        resp = await self._call(self._client.chat.completions.create, **body)
        choice = resp.choices[0] if resp.choices else None
        if choice is None or choice.finish_reason == "content_filter":
            raise TelaError(ErrorCode.INVALID_OUTPUT, "The model declined to answer.")
        usage = resp.usage
        return ProviderResult(text=choice.message.content or "",
                              prompt_tokens=int(getattr(usage, "prompt_tokens", 0) or 0),
                              completion_tokens=int(getattr(usage, "completion_tokens", 0) or 0),
                              model=resp.model or model)

    async def embed(self, *, model: str, texts: list[str], dim: int | None = None) -> list[list[float]]:
        body: dict[str, Any] = {"model": model, "input": texts}
        if dim:
            body["dimensions"] = dim  # text-embedding-3 models shorten to the index size
        resp = await self._call(self._client.embeddings.create, **body)
        return [list(d.embedding) for d in sorted(resp.data, key=lambda d: d.index)]
