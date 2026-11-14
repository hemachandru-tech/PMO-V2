"""Signed transport to the connector runner (US-021, US-023).

Every call carries a short-lived HMAC service token (who is calling) and an
Ed25519-signed envelope (what exactly may run). This module only moves signed
instructions; it never decides what to run. Query execution goes exclusively
through ``tela_api.query.runner`` (US-022).
"""

from __future__ import annotations

from typing import Any

import httpx

from tela_common import envelope as env
from tela_common import service_auth
from tela_common.errors import ErrorCode, TelaError
from tela_common.observability import propagation_headers

RUNNER_AUDIENCE = "tela-connector-runner"


class RunnerTransport:
    def __init__(self, base_url: str, service_key: bytes, signing_pem: bytes,
                 http: httpx.AsyncClient | None = None, timeout_s: float = 30.0) -> None:
        self.base_url = base_url.rstrip("/")
        self._service_key = service_key
        self._signing_key = env.load_private_key(signing_pem)
        self._http = http or httpx.AsyncClient(timeout=timeout_s)

    async def aclose(self) -> None:
        await self._http.aclose()

    async def post(self, op: str, payload: dict[str, Any], *, statement: str | None = None,
                   timeout_s: float | None = None) -> dict[str, Any]:
        envelope = env.sign(self._signing_key, {**payload, "op": op})
        token = service_auth.issue(self._service_key, sub="tela-api", aud=RUNNER_AUDIENCE, ttl_s=60)
        try:
            resp = await self._http.post(
                f"{self.base_url}/{op}", json={"envelope": envelope, "statement": statement},
                headers=propagation_headers({"Authorization": f"Bearer {token}"}),
                timeout=timeout_s if timeout_s is not None else httpx.USE_CLIENT_DEFAULT,
            )
        except httpx.TimeoutException as exc:
            raise TelaError(ErrorCode.QUERY_TIMEOUT, "The data source took too long to answer.") from exc
        except httpx.HTTPError as exc:
            raise TelaError(ErrorCode.SOURCE_UNREACHABLE, "The connector service is not reachable.") from exc
        if resp.status_code >= 400:
            try:
                err = resp.json()["error"]
                code = ErrorCode(err["code"])
                message = err.get("message") or "The data source refused the request."
            except (ValueError, KeyError):
                code, message = ErrorCode.SOURCE_UNREACHABLE, "The connector service failed."
            raise TelaError(code, message)
        data: dict[str, Any] = resp.json()
        return data
