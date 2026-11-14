"""Gateway configuration (DEC-01).

The data policy approves Anthropic, OpenAI, Azure OpenAI, Gemini and a local Ollama, and allows aggregates
(totals and counts, never rows) to reach a model. A deployment still enables nothing until it lists providers in
``TELA_LLM_ALLOWED_PROVIDERS`` and an Admin chooses one in AI settings.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class GatewaySettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="TELA_", extra="ignore")

    env: str = "local"
    otel_exporter_otlp_endpoint: str | None = None
    log_level: str = "INFO"

    database_url: str | None = None
    database_url_file: str | None = None

    service_signing_key_file: str | None = None
    service_signing_key: str | None = None  # tests / local only

    # Provider allow-list (approved under DEC-01). Empty = LLM disabled.
    llm_provider: str | None = None
    llm_allowed_providers: list[str] = Field(default_factory=list)
    # Aggregates to AG-08 narration (RULE-019); the tenant's own toggle must also be on.
    llm_allow_aggregates: bool = True
    llm_model_default: str | None = None
    llm_model_by_task: dict[str, str] = Field(default_factory=dict)
    llm_timeout_seconds: float = 30.0
    # Providers whose decoding is held to the answer's JSON schema (US-191: llama3.2:3b sent free text without it).
    # Hosted strict modes refuse open objects and recursive schemas, so they get the schema only in the prompt;
    # every answer is still checked against it here and a failure is recorded as invalid_output.
    llm_constrained_providers: list[str] = Field(default_factory=lambda: ["ollama"])
    llm_budget_tokens_monthly: int = 0  # 0 = unlimited (must be set before prod)
    llm_user_requests_per_minute: int = 30
    llm_allowed_tasks: list[str] = Field(
        default_factory=lambda: ["intent", "clarify", "plan", "plan_review", "model_assist", "map_suggest",
                                 "report_plan", "canvas_edit", "visual_advice", "narrate", "explain", "cert_review",
                                 "sql_draft", "pack_author", "frame"]
    )

    ollama_url: str = "http://ollama:11434"
    # Thinking models (qwen3) reason before answering: ~330 more tokens a reply. US-191 measured planning with
    # qwen3:8b: thinking on gave 5/20 right and 2 confident-wrong against 2/20 and 10 with it off, at twice the time,
    # so it is on for planning only, on models that can think (a model that cannot refuses think: true).
    # ``plan_review`` (AG-03 reviewing the rules' plan) does not think: with thinking its 4k-token prompt took
    # 21-76 s on qwen3:8b and 13 of 30 reviews passed the 60 s review timeout; without it 5-16 s, same verdicts
    # (EXEC-180).
    ollama_think_tasks: list[str] = Field(default_factory=lambda: ["plan"])
    ollama_think_models: list[str] = Field(default_factory=lambda: ["qwen3", "deepseek-r1", "gpt-oss"])
    azure_openai_api_version: str = "2024-10-21"
    # Tenant provider keys (read-only AppRole, deploy/openbao/policies/tela-gateway.hcl). None = no keyed providers.
    secrets_backend: str | None = None
    secrets_addr: str | None = None

    # Deployment default; a tenant may choose an embedding model of its own provider (same dimension).
    embed_provider: str = "ollama"
    embed_model: str = "mxbai-embed-large"
    embed_dim: int = 1024

    # Local cross-encoder for knowledge retrieval (US-174): a text-embeddings-inference /rerank endpoint. Unset = off,
    # and retrieval keeps plain reciprocal rank fusion. Keep it unset (US-197, EXEC-106: lower recall, slow on CPU).
    rerank_url: str | None = None
    rerank_model: str = "BAAI/bge-reranker-v2-m3"
    rerank_timeout_seconds: float = 10.0

    @model_validator(mode="after")
    def _resolve(self) -> GatewaySettings:
        if not self.database_url and self.database_url_file:
            self.database_url = Path(self.database_url_file).read_text().strip()
        if not self.service_signing_key and self.service_signing_key_file:
            self.service_signing_key = Path(self.service_signing_key_file).read_text().strip()
        self.rerank_url = self.rerank_url or None  # compose passes an empty string when it is not set
        return self

    @property
    def async_database_url(self) -> str | None:
        if not self.database_url:
            return None
        url = self.database_url
        for prefix in ("postgresql+psycopg://", "postgresql://"):
            if url.startswith(prefix):
                return "postgresql+asyncpg://" + url[len(prefix):]
        return url


@lru_cache
def get_settings() -> GatewaySettings:
    return GatewaySettings()
