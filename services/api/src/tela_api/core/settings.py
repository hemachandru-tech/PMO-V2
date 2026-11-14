"""Service configuration (technical_document §Configuration and environment variables).

Secrets are never passed as plain environment variables in deployed
environments: ``*_FILE`` variables point at files mounted by the orchestrator.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from tela_common.objectstore import parse_sse


def _read_file(path: str | None) -> str | None:
    if not path:
        return None
    return Path(path).read_text().strip()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="TELA_", extra="ignore")

    env: str = "local"
    log_level: str = "INFO"

    database_url: str | None = None
    database_url_file: str | None = None
    db_pool_size: int = 10

    redis_url: str | None = None
    redis_url_file: str | None = None

    # OIDC (Keycloak brokering Entra ID). The issuer is trusted configuration;
    # keys are never fetched from a URL taken from the token (S03 §10).
    oidc_issuer: str = "http://localhost:8080/realms/tela"
    oidc_audience: str = "tela-api"
    oidc_jwks_url: str | None = None  # internal URL when it differs from the public issuer
    oidc_allowed_algs: list[str] = Field(default_factory=lambda: ["RS256", "ES256"])
    oidc_access_token_types: list[str] = Field(default_factory=lambda: ["Bearer", "at+jwt"])
    oidc_groups_claim: str = "groups"
    jwks_cache_seconds: int = 600

    principal_cache_seconds: int = 60  # short, per the grant-cache lesson (S16 RISK-003)

    # SCIM provisioning (US-118; API-003): requests per minute per tenant. Ending a person's identity-provider
    # sessions on suspension needs a Keycloak client with the realm-management "manage-users" role; without it
    # Tela still refuses their tokens (sessions_revoked_at) and the task is marked skipped.
    scim_rate_per_minute: int = 600
    idp_admin_url: str | None = None  # internal Keycloak base URL when it differs from the issuer's
    idp_admin_client_id: str | None = None
    idp_admin_client_secret: str | None = None
    idp_admin_client_secret_file: str | None = None

    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173"])

    secrets_backend: str = "file"  # openbao | vault | azure_keyvault | file (dev only)
    secrets_addr: str | None = None

    otel_exporter_otlp_endpoint: str | None = None

    # Connector runner (US-021, US-023). The API signs execution envelopes with an Ed25519 private key;
    # the runner holds only the public key. Service tokens (HMAC) authenticate the transport.
    runner_url: str = "http://connector-runner:8100"
    gateway_url: str = "http://llm-gateway:8200"
    llm_timeout_seconds: float = 120.0  # local CPU models are slow; agents always have a fallback
    # DEC-01: aggregates (totals and counts, never rows) may reach a model when the tenant also allows it.
    llm_allow_aggregates: bool = True
    service_signing_key: str | None = None
    service_signing_key_file: str | None = None
    execution_signing_key_file: str | None = None
    execution_signing_key_pem: str | None = None

    query_max_rows: int = 10_000  # RULE-006
    query_timeout_seconds: int = 20
    cache_ttl_seconds: int = 900  # ADR-010, DEC-02
    enum_check_max_values: int = 200  # filter values are checked against scoped distinct values up to this size
    store_statement_text: bool = False  # query_executions.statement_text only when the tenant allows it
    packs_dir: str | None = None  # packs/ manifests (tela-admin load-packs)

    # Object storage (COMP-014) for exports and uploads. Unset endpoint = the S3 default (AWS) in other profiles.
    object_store_endpoint: str | None = None
    object_store_bucket: str = "tela"
    object_store_access_key: str | None = None
    object_store_secret_key: str | None = None
    object_store_secret_key_file: str | None = None
    # Server-side encryption: AES256 | aws:kms (+ key id) | none — none only in local development, where the
    # SeaweedFS gateway has no encryption. Puts fail when the store does not confirm the requested encryption.
    object_store_sse: str = "AES256"
    object_store_kms_key_id: str | None = None

    # Exports (US-095; DEC-15 working position): 10 per hour per person, 15-minute signed links, files kept 24 h,
    # at most this many rows across all sheets; each widget is still capped at query_max_rows (RULE-006).
    export_rate_per_hour: int = 10
    export_link_minutes: int = 15
    export_retention_hours: int = 24
    export_max_rows: int = 100_000
    knowledge_max_upload_mb: int = 25  # API-049 proposed limit
    # Retrieval (US-174): RRF top ``pool`` → gateway reranker → top 8 scoring at least ``floor`` (sigmoid, 0 to 1).
    # The reranker is off unless the gateway has ``TELA_RERANK_URL`` (US-197: it lowered recall, EXEC-106).
    # Lexical ranking is the weighted tsvector with word rarity, or ParadeDB BM25 when ``pg_search`` is installed.
    knowledge_rerank_pool: int = 30
    knowledge_rerank_floor: float = 0.02
    knowledge_lexical: Literal["weighted", "pg_search"] = "weighted"
    # Dimension value index (US-173): at most this many values per dimension (a dimension with more is free text and
    # is not indexed); a word the model doesn't know links to a value by meaning only at this cosine similarity.
    value_index_max_values: int = 1000
    value_link_vector_min: float = 0.85
    # Direct SQL as an Ask answer mode (DEC-30, US-256/257): False takes it off the Connections > Ask choices and
    # answers a source set to it with certified measures.
    ask_direct_sql: bool = True
    # Confidence (US-182): AG-03 plans this many samples at this temperature and keeps the majority (1 = no vote);
    # a small model also checks the plan's plain words against the question when ``ask_backcheck_model`` is on.
    ask_vote_samples: int = Field(default=3, ge=1, le=5)
    ask_vote_temperature: float = Field(default=0.6, ge=0, le=1)
    ask_backcheck_model: bool = False
    # Ask asks which reading was meant below the person's threshold (US-182). Lowered by 0.1 from 0.35/0.6/0.85 so
    # Ask answers more and asks less (EXEC-171); TELA_ASK_CONFIDENCE_THRESHOLDS='{"balanced": 0.55}' tunes one level.
    ask_confidence_thresholds: dict[str, float] = Field(
        default_factory=lambda: {"assume_first": 0.25, "balanced": 0.5, "ask_freely": 0.75})
    # Question frame (US-253, ENH-028 W3), shadow mode: after every Ask turn the frame is built and recorded as a step
    # of the turn's agent run; answers are unchanged. ``shadow`` = rules + one LLM ``frame`` call (when the tenant has
    # a provider), ``rules`` = no LLM call, ``off`` = nothing. The LLM call waits at most ``ask_frame_timeout_s``.
    ask_frame: Literal["off", "rules", "shadow"] = "shadow"
    ask_frame_timeout_s: float = Field(default=30.0, gt=0, le=300)
    # LLM review (US-239, DEC-28): an agent waits this long for the LLM to review its rules draft; past it the draft
    # stands. Local qwen3:8b took a median 73 s to plan a question (EXEC-113), so local review is off by default.
    llm_review_timeout_s: float = Field(default=60.0, gt=0, le=600)
    # Agents kept out of LLM review whatever the tenant's switch says (DEC-28 point 3, US-246 final gate, EXEC-169):
    # AG-05 report plans stayed 15 of 20 right on qwen3:8b with review on, at ≈ 28 s more a plan; AG-06 chat edits rose
    # from 13 to 19 of 20 right but made a confident-wrong preview the rules don't make. Set
    # TELA_LLM_REVIEW_OFF_AGENTS='[]' to measure them again (e.g. the hosted gate pair).
    llm_review_off_agents: list[str] = Field(default_factory=lambda: ["AG-05", "AG-06"])
    # Planner model fit (US-191, DEC-23): voting runs one sample on providers that run samples one after another
    # (local Ollama: three samples took three times as long, EXEC-102); the recommended and minimum AG-03 model per
    # option (local, hosted) from EXEC-109: on the 20 US-190 LLM-path questions llama3.2:3b planned none right, qwen3:8b
    # with thinking 5 (2 confident-wrong); hosted is not yet measured (no hosted key on dev), so its models are
    # provisional. AI settings warn when at least ``min_calls`` AG-03 calls of the current model in ``days`` days
    # include this share of schema failures.
    ask_vote_serial_providers: list[str] = Field(default_factory=lambda: ["ollama", "local"])
    planner_recommended: dict[str, str] = Field(default_factory=lambda: {"local": "qwen3:8b",
                                                                         "hosted": "claude-sonnet-5-5"})
    planner_minimum: dict[str, str] = Field(default_factory=lambda: {"local": "qwen3:8b",
                                                                     "hosted": "claude-haiku-5-5"})
    planner_health_days: int = Field(default=7, ge=1, le=90)
    planner_health_min_calls: int = Field(default=10, ge=1)
    planner_schema_warn_rate: float = Field(default=0.2, ge=0, le=1)
    file_source_max_upload_mb: int = 50  # Excel/CSV sources (DEC-07, MSG-027)
    file_scan_url: str | None = None  # optional malware scanner for uploads; answers {"clean": bool}

    @model_validator(mode="after")
    def _resolve_files(self) -> Settings:
        if not self.database_url:
            self.database_url = _read_file(self.database_url_file)
        if not self.redis_url:
            self.redis_url = _read_file(self.redis_url_file)
        if not self.service_signing_key:
            self.service_signing_key = _read_file(self.service_signing_key_file)
        if not self.execution_signing_key_pem:
            self.execution_signing_key_pem = _read_file(self.execution_signing_key_file)
        if not self.object_store_secret_key:
            self.object_store_secret_key = _read_file(self.object_store_secret_key_file)
        if not self.idp_admin_client_secret:
            self.idp_admin_client_secret = _read_file(self.idp_admin_client_secret_file)
        if self.env not in ("local", "dev", "test") and self.secrets_backend == "file":
            raise ValueError("the 'file' secrets backend is for local development only")
        self.object_store_sse = parse_sse(self.object_store_sse, self.env)
        return self

    @property
    def jwks_url(self) -> str:
        return self.oidc_jwks_url or f"{self.oidc_issuer.rstrip('/')}/protocol/openid-connect/certs"

    @property
    def async_database_url(self) -> str:
        if not self.database_url:
            raise RuntimeError("TELA_DATABASE_URL or TELA_DATABASE_URL_FILE is required")
        url = self.database_url
        for prefix in ("postgresql+psycopg://", "postgresql://", "postgres://"):
            if url.startswith(prefix):
                return "postgresql+asyncpg://" + url[len(prefix):]
        return url


@lru_cache
def get_settings() -> Settings:
    return Settings()
