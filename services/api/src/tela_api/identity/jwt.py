"""Access-token validation (FEAT-001, REQ-002, NFR-004).

* Keys come only from the JWKS of the *configured* issuer — never from a URL
  taken from the token (S03 §10).
* Checks signature, ``iss``, ``aud``, ``exp``/``nbf`` and that it is an access
  token (ID tokens are rejected).
* JWKS is cached (default 10 minutes) and refreshed once on an unknown ``kid``,
  at most every 30 seconds.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any

import httpx
import jwt
from jwt import PyJWK

from tela_api.core.metrics import AUTH_FAILURES
from tela_common.errors import ErrorCode, TelaError


@dataclass
class _JwksCache:
    keys: dict[str, PyJWK] = field(default_factory=dict)
    fetched_at: float = 0.0
    last_forced: float = 0.0


class JwtVerifier:
    def __init__(
        self,
        *,
        issuer: str,
        audience: str,
        jwks_url: str,
        allowed_algs: list[str],
        access_token_types: list[str],
        cache_seconds: int = 600,
        http: httpx.AsyncClient | None = None,
        leeway_seconds: int = 30,
    ) -> None:
        self.issuer = issuer
        self.audience = audience
        self.jwks_url = jwks_url
        self.allowed_algs = allowed_algs
        self.access_token_types = access_token_types
        self.cache_seconds = cache_seconds
        self.leeway = leeway_seconds
        self._http = http or httpx.AsyncClient(timeout=5.0)
        self._cache = _JwksCache()
        self._lock = asyncio.Lock()

    async def _fetch_jwks(self) -> None:
        last_error: Exception | None = None
        for _ in range(3):  # 2 retries (technical_document §Third-party integrations)
            try:
                resp = await self._http.get(self.jwks_url)
                resp.raise_for_status()
                data = resp.json()
                keys: dict[str, PyJWK] = {}
                for jwk in data.get("keys", []):
                    if jwk.get("use", "sig") != "sig" or "kid" not in jwk:
                        continue
                    try:
                        keys[jwk["kid"]] = PyJWK(jwk)
                    except jwt.PyJWKError:
                        continue
                self._cache = _JwksCache(keys=keys, fetched_at=time.monotonic(), last_forced=self._cache.last_forced)
                return
            except (httpx.HTTPError, ValueError) as exc:
                last_error = exc
                await asyncio.sleep(0.2)
        raise TelaError(ErrorCode.TOKEN_INVALID, "Sign-in keys are unavailable; try again shortly.") from last_error

    async def _key_for(self, kid: str) -> PyJWK:
        now = time.monotonic()
        async with self._lock:
            if not self._cache.keys or now - self._cache.fetched_at > self.cache_seconds:
                await self._fetch_jwks()
            if kid not in self._cache.keys and now - self._cache.last_forced > 30:
                self._cache.last_forced = now
                await self._fetch_jwks()
        key = self._cache.keys.get(kid)
        if key is None:
            raise self._fail("unknown_kid")
        return key

    @staticmethod
    def _fail(reason: str) -> TelaError:
        AUTH_FAILURES.labels(reason=reason).inc()
        message = "Your session is not valid. Please sign in again."
        return TelaError(ErrorCode.TOKEN_INVALID, message, {"reason": reason})

    async def verify(self, token: str) -> dict[str, Any]:
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError as exc:
            raise self._fail("malformed") from exc
        alg = header.get("alg")
        if alg not in self.allowed_algs:
            raise self._fail("alg_not_allowed")
        kid = header.get("kid")
        if not kid:
            raise self._fail("missing_kid")
        key = await self._key_for(kid)
        try:
            claims: dict[str, Any] = jwt.decode(
                token,
                key=key,
                algorithms=[alg],
                audience=self.audience,
                issuer=self.issuer,
                leeway=self.leeway,
                options={"require": ["exp", "iat", "iss", "sub"]},
            )
        except jwt.ExpiredSignatureError as exc:
            raise self._fail("expired") from exc
        except jwt.InvalidAudienceError as exc:
            raise self._fail("audience") from exc
        except jwt.InvalidIssuerError as exc:
            raise self._fail("issuer") from exc
        except jwt.PyJWTError as exc:
            raise self._fail("invalid") from exc

        token_type = claims.get("typ") or header.get("typ")
        if token_type not in self.access_token_types:
            raise self._fail("not_access_token")
        return claims

    async def aclose(self) -> None:
        await self._http.aclose()
