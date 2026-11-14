"""Stable error codes shared by every service (api_catalog.md, Error code catalogue)."""

from __future__ import annotations

from enum import StrEnum
from typing import Any


class ErrorCode(StrEnum):
    TOKEN_INVALID = "token_invalid"  # noqa: S105 - an error code, not a secret
    FORBIDDEN = "forbidden"
    NOT_FOUND = "not_found"
    VALIDATION_ERROR = "validation_error"
    PRECONDITION_FAILED = "precondition_failed"
    RATE_LIMITED = "rate_limited"
    NO_ACCESS = "no_access"
    SCOPE_UNRESOLVED = "scope_unresolved"
    SCOPE_REWRITE_FAILED = "scope_rewrite_failed"
    UNKNOWN_GROUP = "unknown_group"
    PENDING_APPROVAL = "pending_approval"
    FIELD_NOT_IN_MODEL = "field_not_in_model"
    PREDICATE_INVALID = "predicate_invalid"
    SECRETS_UNAVAILABLE = "secrets_unavailable"
    STORAGE_UNAVAILABLE = "storage_unavailable"
    LEAK_BLOCKED = "leak_blocked"
    PROVIDER_NOT_ALLOWED = "provider_not_allowed"
    BUDGET_EXCEEDED = "budget_exceeded"
    PLANNER_UNAVAILABLE = "planner_unavailable"
    LLM_TIMEOUT = "llm_timeout"
    INVALID_OUTPUT = "invalid_output"
    SIGNATURE_INVALID = "signature_invalid"
    CONFLICT = "conflict"
    # Query engine and connector runner (P1)
    SPEC_INVALID = "spec_invalid"
    ONE_DATABASE_PER_WIDGET = "one_database_per_widget"
    MODEL_NOT_APPROVED = "model_not_approved"
    SQL_PARSE_ERROR = "sql_parse_error"
    SQL_MULTIPLE_STATEMENTS = "sql_multiple_statements"
    SQL_NOT_SELECT = "sql_not_select"
    SQL_FORBIDDEN_NODE = "sql_forbidden_node"
    SQL_OBJECT_NOT_ALLOWED = "sql_object_not_allowed"
    SQL_FIELD_HIDDEN = "sql_field_hidden"
    SQL_PLACEHOLDER_MISPLACED = "sql_placeholder_misplaced"
    ENGINE_NOT_SUPPORTED = "engine_not_supported"
    QUERY_TIMEOUT = "query_timeout"
    QUERY_FAILED = "query_failed"
    SCHEMA_CHANGED = "schema_changed"
    SOURCE_UNREACHABLE = "source_unreachable"
    SOURCE_BUSY = "source_busy"  # MSG-025: throttled by the source (Dataverse 429) after the allowed retries
    MEASURE_UNAVAILABLE = "measure_unavailable"  # no shape for this measure on the source (Dataverse Web API)
    READ_ONLY_VIOLATION = "read_only_violation"
    PAYLOAD_TOO_LARGE = "payload_too_large"
    CONNECTION_NOT_READY = "connection_not_ready"
    OPEN_QUESTIONS_UNRESOLVED = "open_questions_unresolved"
    UNAPPROVED_LINKS_IN_USE = "unapproved_links_in_use"
    MODEL_INVALID = "model_invalid"
    PACK_NOT_MATCHED = "pack_not_matched"
    CONFIGURATION_ERROR = "configuration_error"
    # Authoring and publishing (P2)
    VISUAL_NOT_SUITABLE = "visual_not_suitable"
    PRECHECK_FAILED = "precheck_failed"
    UNCERTIFIED_WIDGET = "uncertified_widget"
    ALREADY_DECIDED = "already_decided"
    DEPENDENCY_PENDING = "dependency_pending"
    INTERNAL = "internal_error"


HTTP_STATUS: dict[ErrorCode, int] = {
    ErrorCode.TOKEN_INVALID: 401,
    ErrorCode.FORBIDDEN: 403,
    ErrorCode.NO_ACCESS: 403,
    ErrorCode.SCOPE_UNRESOLVED: 403,
    ErrorCode.NOT_FOUND: 404,
    ErrorCode.VALIDATION_ERROR: 400,
    ErrorCode.UNKNOWN_GROUP: 400,
    ErrorCode.FIELD_NOT_IN_MODEL: 400,
    ErrorCode.PREDICATE_INVALID: 400,
    ErrorCode.PENDING_APPROVAL: 409,
    ErrorCode.PRECONDITION_FAILED: 412,
    ErrorCode.SCOPE_REWRITE_FAILED: 422,
    ErrorCode.LEAK_BLOCKED: 422,
    ErrorCode.INVALID_OUTPUT: 422,
    ErrorCode.PROVIDER_NOT_ALLOWED: 403,
    ErrorCode.BUDGET_EXCEEDED: 429,
    ErrorCode.RATE_LIMITED: 429,
    ErrorCode.SECRETS_UNAVAILABLE: 503,
    ErrorCode.STORAGE_UNAVAILABLE: 503,
    ErrorCode.PLANNER_UNAVAILABLE: 503,
    ErrorCode.LLM_TIMEOUT: 503,
    ErrorCode.SIGNATURE_INVALID: 500,
    ErrorCode.CONFLICT: 409,
    ErrorCode.SPEC_INVALID: 422,
    ErrorCode.ONE_DATABASE_PER_WIDGET: 422,
    ErrorCode.MODEL_NOT_APPROVED: 409,
    ErrorCode.SQL_PARSE_ERROR: 422,
    ErrorCode.SQL_MULTIPLE_STATEMENTS: 422,
    ErrorCode.SQL_NOT_SELECT: 422,
    ErrorCode.SQL_FORBIDDEN_NODE: 422,
    ErrorCode.SQL_OBJECT_NOT_ALLOWED: 422,
    ErrorCode.SQL_FIELD_HIDDEN: 422,
    ErrorCode.SQL_PLACEHOLDER_MISPLACED: 422,
    ErrorCode.ENGINE_NOT_SUPPORTED: 422,
    ErrorCode.QUERY_TIMEOUT: 504,
    ErrorCode.QUERY_FAILED: 422,
    ErrorCode.SCHEMA_CHANGED: 409,
    ErrorCode.SOURCE_UNREACHABLE: 503,
    ErrorCode.READ_ONLY_VIOLATION: 503,
    ErrorCode.PAYLOAD_TOO_LARGE: 422,
    ErrorCode.SOURCE_BUSY: 503,
    ErrorCode.MEASURE_UNAVAILABLE: 422,
    ErrorCode.CONNECTION_NOT_READY: 409,
    ErrorCode.OPEN_QUESTIONS_UNRESOLVED: 409,
    ErrorCode.UNAPPROVED_LINKS_IN_USE: 409,
    ErrorCode.MODEL_INVALID: 422,
    ErrorCode.PACK_NOT_MATCHED: 409,
    ErrorCode.CONFIGURATION_ERROR: 422,
    ErrorCode.VISUAL_NOT_SUITABLE: 422,
    ErrorCode.PRECHECK_FAILED: 409,
    ErrorCode.UNCERTIFIED_WIDGET: 409,
    ErrorCode.ALREADY_DECIDED: 409,
    ErrorCode.DEPENDENCY_PENDING: 409,
    ErrorCode.INTERNAL: 500,
}


class TelaError(Exception):
    """Domain error carrying a stable code and a user-safe message."""

    def __init__(self, code: ErrorCode, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}

    @property
    def status(self) -> int:
        return HTTP_STATUS.get(self.code, 500)
