"""Current user and logout (API-001, API-002)."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from tela_api.audit import service as audit
from tela_api.core.db import fetch_all, fetch_one, tenant_tx
from tela_api.identity.deps import get_principal
from tela_api.identity.principal import Principal, PrincipalResolver
from tela_api.identity.scope import resolve_scope
from tela_common.errors import TelaError

router = APIRouter(prefix="/api/v1", tags=["identity"])


class ScopeSummary(BaseModel):
    connection_id: str
    connection_name: str
    text: str


class Preferences(BaseModel):
    """Interface preferences remembered per user (US-134). Unknown keys are refused."""

    model_config = ConfigDict(extra="forbid")

    theme: Literal["system", "light", "dark"] = "system"
    # US-182: how readily Ask asks a clarifying question — assume first, balanced, or ask freely.
    ask_verbosity: Literal["assume_first", "balanced", "ask_freely"] = "balanced"
    # ENH-021: the person's own layout; "default" follows the company's (``branding.layout``).
    layout: Literal["default", "classic", "executive", "command", "aurora"] = "default"


class GrantSummary(BaseModel):
    """A permission given to this person on one connection (US-161), on top of their roles."""

    permission: str
    connection_id: str
    expires_at: datetime


class MeResponse(BaseModel):
    user_id: str
    display_name: str
    email: str | None
    tenant_id: str
    roles: list[str]
    level: str
    audience_roles: list[str]
    permissions: list[str]
    grants: list[GrantSummary] = []
    scope_summary: list[ScopeSummary]
    locale: str | None
    time_zone: str | None
    preferences: Preferences


@router.get("/me", response_model=MeResponse)
async def me(principal: Principal = Depends(get_principal)) -> MeResponse:
    summaries: list[ScopeSummary] = []
    async with tenant_tx(principal.tenant_id) as conn:
        connections = await fetch_all(
            conn,
            """
            SELECT DISTINCT c.id, c.name FROM conn.connections c
            JOIN iam.scope_policies p ON p.connection_id = c.id
            WHERE c.deleted_at IS NULL AND p.is_active AND p.role_id = ANY(CAST(:roles AS uuid[]))
            ORDER BY c.name
            """,
            {"roles": list(principal.role_ids)},
        )
        for c in connections:
            try:
                scope = await resolve_scope(conn, principal, str(c["id"]))
                text = scope.summary
            except TelaError:  # an unresolvable scope is shown, not raised, on /me
                text = "Your data scope could not be determined — ask your PMO admin."
            summaries.append(ScopeSummary(connection_id=str(c["id"]), connection_name=c["name"], text=text))
        prefs = await _load_preferences(conn, principal.user_id)

    return MeResponse(
        user_id=principal.user_id,
        display_name=principal.display_name,
        email=principal.email,
        tenant_id=principal.tenant_id,
        roles=[principal.level],
        level=principal.level,
        audience_roles=list(principal.audience_roles),
        permissions=sorted(principal.permissions),
        grants=[GrantSummary(permission=g.permission, connection_id=g.connection_id, expires_at=g.expires_at)
                for g in principal.grants if g.live()],
        scope_summary=summaries,
        locale=principal.locale,
        time_zone=principal.time_zone,
        preferences=prefs,
    )


async def _load_preferences(conn: AsyncConnection, user_id: str) -> Preferences:
    row = await fetch_one(conn, "SELECT preferences FROM iam.users WHERE id = CAST(:id AS uuid)", {"id": user_id})
    stored = (row or {}).get("preferences") or {}
    # Keep only keys this version knows, so an old value never breaks sign-in.
    return Preferences.model_validate({k: v for k, v in stored.items() if k in Preferences.model_fields})


@router.put("/me/preferences", response_model=Preferences)
async def put_preferences(body: Preferences, principal: Principal = Depends(get_principal)) -> Preferences:
    """Saves the signed-in user's interface preferences (theme, Ask verbosity). Only the keys sent change. Needs no
    permission: it is the user's own setting."""
    async with tenant_tx(principal.tenant_id) as conn:
        await conn.execute(
            text("UPDATE iam.users SET preferences = preferences || CAST(:p AS jsonb) WHERE id = CAST(:id AS uuid)"),
            {"p": json.dumps(body.model_dump(exclude_unset=True)), "id": principal.user_id},
        )
        return await _load_preferences(conn, principal.user_id)


@router.post("/auth/logout", status_code=204)
async def logout(request: Request, principal: Principal = Depends(get_principal)) -> Response:
    resolver: PrincipalResolver = request.app.state.principal_resolver
    resolver.invalidate(user_id=principal.user_id)
    async with tenant_tx(principal.tenant_id) as conn:
        await audit.record(
            conn, tenant_id=principal.tenant_id, action="auth.logout", object_type="user",
            object_id=principal.user_id, outcome="success", actor_id=principal.user_id,
            correlation_id=getattr(request.state, "request_id", None),
        )
    return Response(status_code=204)
