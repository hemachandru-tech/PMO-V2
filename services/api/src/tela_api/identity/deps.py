"""FastAPI dependencies: request id → principal → permission (technical_document §API implementation)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from fastapi import Depends, Request

from tela_api.identity.jwt import JwtVerifier
from tela_api.identity.permissions import Perm
from tela_api.identity.principal import Principal, PrincipalResolver
from tela_common.errors import ErrorCode, TelaError


def _verifier(request: Request) -> JwtVerifier:
    verifier: JwtVerifier = request.app.state.jwt_verifier
    return verifier


def _resolver(request: Request) -> PrincipalResolver:
    resolver: PrincipalResolver = request.app.state.principal_resolver
    return resolver


def _bearer(request: Request) -> str:
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise TelaError(ErrorCode.TOKEN_INVALID, "Sign in to continue.", {"reason": "missing_token"})
    return token.strip()


async def get_principal(request: Request) -> Principal:
    cached = getattr(request.state, "principal", None)
    if cached is not None:
        return cached  # type: ignore[no-any-return]
    claims = await _verifier(request).verify(_bearer(request))
    principal = await _resolver(request).resolve(
        claims,
        request_id=getattr(request.state, "request_id", None),
        client_ip=request.client.host if request.client else None,
    )
    request.state.principal = principal
    return principal


def require(*permissions: Perm) -> Callable[..., Awaitable[Principal]]:
    """Route guard: every listed permission key is required."""

    async def _dep(principal: Principal = Depends(get_principal)) -> Principal:
        missing = [p for p in permissions if not principal.has(p)]
        if missing:
            raise TelaError(ErrorCode.FORBIDDEN, "You don't have permission to do that.", {"missing": missing})
        return principal

    return _dep
