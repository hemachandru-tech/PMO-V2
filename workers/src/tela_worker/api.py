"""Calls from the worker to the API's internal endpoints (US-095).

The worker never reads source data or object storage itself: the API re-checks access, runs every query through
the one scoped path and stores files. Each call carries a 60-second HMAC service token (``sub`` tela-worker,
``aud`` tela-api-internal), the same scheme the API uses towards the runner and gateway.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import httpx

from tela_common import service_auth
from tela_common.observability import propagation_headers

AUDIENCE = "tela-api-internal"


class ApiRefused(Exception):
    """The API refused the call on purpose (no access, too large, already finished); it has recorded why."""

    def __init__(self, status: int, code: str) -> None:
        super().__init__(f"{status} {code}")
        self.status = status
        self.code = code


def _service_key() -> bytes:
    path = os.environ.get("TELA_SERVICE_SIGNING_KEY_FILE")
    key = Path(path).read_text().strip() if path else os.environ.get("TELA_SERVICE_SIGNING_KEY", "")
    if not key:
        raise RuntimeError("TELA_SERVICE_SIGNING_KEY or TELA_SERVICE_SIGNING_KEY_FILE is required")
    return key.encode()


class InternalApi:
    def __init__(self, base_url: str | None = None, key: bytes | None = None,
                 http: httpx.Client | None = None) -> None:
        self.base_url = (base_url or os.environ.get("TELA_API_URL", "http://api:8000")).rstrip("/")
        self._key = key or _service_key()
        self._http = http or httpx.Client(timeout=300)

    def _headers(self) -> dict[str, str]:
        token = service_auth.issue(self._key, sub="tela-worker", aud=AUDIENCE, ttl_s=60)
        return propagation_headers({"Authorization": f"Bearer {token}"})

    def _check(self, resp: httpx.Response) -> dict[str, Any]:
        if resp.status_code in (403, 404, 409, 422):
            try:
                code = str(resp.json()["error"]["code"])
            except (ValueError, KeyError, TypeError):
                code = "refused"
            raise ApiRefused(resp.status_code, code)
        resp.raise_for_status()
        data: dict[str, Any] = resp.json()
        return data

    def post(self, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        return self._check(self._http.post(f"{self.base_url}{path}", json=body or {}, headers=self._headers()))

    def get_file(self, path: str, params: dict[str, Any]) -> bytes:
        resp = self._http.get(f"{self.base_url}{path}", params=params, headers=self._headers())
        if resp.status_code in (403, 404, 409, 422):
            self._check(resp)
        resp.raise_for_status()
        return resp.content

    def put_file(self, path: str, data: bytes, content_type: str, params: dict[str, Any]) -> dict[str, Any]:
        headers = {**self._headers(), "Content-Type": content_type}
        return self._check(self._http.put(f"{self.base_url}{path}", content=data, params=params, headers=headers))

    def close(self) -> None:
        self._http.close()
