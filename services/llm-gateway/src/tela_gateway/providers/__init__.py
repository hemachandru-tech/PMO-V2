"""Provider adapters. Provider SDKs may only be imported inside this package (CI guard, US-004)."""

from __future__ import annotations

from tela_common.errors import ErrorCode, TelaError
from tela_gateway.providers.base import Credentials, Provider, ProviderResult
from tela_gateway.providers.ollama import OllamaProvider

__all__ = ["EMBEDDERS", "KEYED", "Credentials", "OllamaProvider", "Provider", "ProviderResult", "build_provider"]

# Approved under DEC-01. Keyed providers read the tenant's key from the secrets store.
KEYED = frozenset({"anthropic", "openai", "azure_openai", "gemini"})
EMBEDDERS = frozenset({"ollama", "openai", "azure_openai", "gemini"})


def build_provider(name: str, *, ollama_url: str, timeout: float, credentials: Credentials | None = None,
                   azure_api_version: str | None = None) -> Provider:
    if name == "ollama":
        return OllamaProvider(ollama_url, timeout=timeout)
    if name not in KEYED:
        raise ValueError(f"provider adapter '{name}' is not available")
    if credentials is None or not credentials.api_key:
        raise TelaError(ErrorCode.PLANNER_UNAVAILABLE, "No API key is set for this model provider.")
    # SDKs are imported lazily: a deployment that only allows Ollama never loads them.
    if name == "anthropic":
        from tela_gateway.providers.anthropic import AnthropicProvider

        return AnthropicProvider(credentials.api_key, timeout=timeout)
    if name == "gemini":
        from tela_gateway.providers.gemini import GeminiProvider

        return GeminiProvider(credentials.api_key, timeout=timeout)
    from tela_gateway.providers.openai import OpenAIProvider

    if name == "azure_openai":
        if not credentials.endpoint:
            raise TelaError(ErrorCode.PLANNER_UNAVAILABLE, "No Azure OpenAI endpoint is set.")
        return OpenAIProvider(credentials.api_key, timeout=timeout, endpoint=credentials.endpoint,
                              api_version=azure_api_version)
    return OpenAIProvider(credentials.api_key, timeout=timeout)
