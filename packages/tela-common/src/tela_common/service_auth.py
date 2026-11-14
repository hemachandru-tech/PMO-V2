"""Signed, short-lived service tokens for internal calls (API → gateway, API → runner).

Compose profile: HMAC-SHA256 compact tokens with a shared key mounted from a
file. Helm adds mTLS on top (technical_document §Security implementation).
Start-up refuses empty or placeholder keys.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Any

from tela_common.errors import ErrorCode, TelaError


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def issue(key: bytes, *, sub: str, aud: str, ttl_s: int = 60, claims: dict[str, Any] | None = None) -> str:
    now = int(time.time())
    payload = {"sub": sub, "aud": aud, "iat": now, "exp": now + ttl_s, **(claims or {})}
    body = _b64(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode())
    sig = _b64(hmac.new(key, body.encode(), hashlib.sha256).digest())
    return f"{body}.{sig}"


def verify(key: bytes, token: str, *, aud: str, leeway_s: int = 5) -> dict[str, Any]:
    try:
        body, sig = token.split(".", 1)
    except ValueError as exc:
        raise TelaError(ErrorCode.SIGNATURE_INVALID, "invalid service token") from exc
    expected = _b64(hmac.new(key, body.encode(), hashlib.sha256).digest())
    if not hmac.compare_digest(expected, sig):
        raise TelaError(ErrorCode.SIGNATURE_INVALID, "invalid service token")
    payload: dict[str, Any] = json.loads(_unb64(body))
    now = int(time.time())
    if payload.get("aud") != aud:
        raise TelaError(ErrorCode.SIGNATURE_INVALID, "service token audience mismatch")
    if int(payload.get("exp", 0)) + leeway_s < now:
        raise TelaError(ErrorCode.SIGNATURE_INVALID, "service token expired")
    return payload
