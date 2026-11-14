"""Structured JSON logging with redaction.

Logs must never contain prompts, result rows, SQL parameter values, tokens or
passwords (technical_document §Error handling, logging and observability;
RULE-021). Redaction is applied to keys and to string values.
"""

from __future__ import annotations

import hashlib
import logging
import re
import sys
from collections.abc import Mapping, MutableMapping
from typing import Any

import structlog

from tela_common.observability import add_trace_ids

REDACTED = "[REDACTED]"

# Keys whose values are never logged, whatever they contain.
_SENSITIVE_KEYS = re.compile(
    r"(pass(word)?|secret|token|api[_-]?key|authorization|cookie|credential|private[_-]?key"
    r"|prompt|completion|rows|params|sql_params|dsn)",
    re.IGNORECASE,
)

# Numeric metric keys that contain a sensitive word but are safe (e.g. prompt_tokens).
_SAFE_KEYS = re.compile(r"(_tokens|_count|_ms|_bytes)$", re.IGNORECASE)

# Value patterns that look like secrets (subset of the gateway leak scanner).
_SECRET_VALUE_PATTERNS = [
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}"),
    re.compile(r"sk-[A-Za-z0-9]{20,}"),
    re.compile(r"AIza[0-9A-Za-z_\-]{35}"),
    re.compile(r"(postgres(ql)?|mysql|mongodb(\+srv)?|sqlserver|redis)://[^:\s/]+:[^@\s]+@"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}"),  # JWT
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-]{16,}"),
]


def hash_identifier(value: str | None) -> str | None:
    """One-way hash for user ids in logs (``user_id(hashed)``)."""
    if value is None:
        return None
    return hashlib.sha256(value.encode()).hexdigest()[:16]


def _is_sensitive_key(key: str) -> bool:
    return bool(_SENSITIVE_KEYS.search(key)) and not _SAFE_KEYS.search(key)


def _redact_value(value: Any) -> Any:
    if isinstance(value, str):
        for pattern in _SECRET_VALUE_PATTERNS:
            if pattern.search(value):
                return REDACTED
        return value
    if isinstance(value, Mapping):
        return {k: (REDACTED if _is_sensitive_key(str(k)) else _redact_value(v)) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_redact_value(v) for v in value]
    return value


def redact(event: MutableMapping[str, Any]) -> MutableMapping[str, Any]:
    """Return the event dict with sensitive keys and secret-looking values removed."""
    for key in list(event.keys()):
        if key in ("event", "level", "timestamp", "service"):
            continue
        if _is_sensitive_key(key):
            event[key] = REDACTED
        else:
            event[key] = _redact_value(event[key])
    return event


def _redact_processor(_: Any, __: str, event_dict: MutableMapping[str, Any]) -> MutableMapping[str, Any]:
    return redact(event_dict)


def configure_logging(service: str, level: str = "INFO") -> None:
    """Configure structlog for JSON output with the standard Tela fields."""
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=level.upper())

    def add_service(_: Any, __: str, event_dict: MutableMapping[str, Any]) -> MutableMapping[str, Any]:
        event_dict.setdefault("service", service)
        return event_dict

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True, key="ts"),
            add_service,
            add_trace_ids,
            _redact_processor,
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.getLevelName(level.upper())),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str | None = None) -> Any:
    return structlog.get_logger(name)
