"""Per-tenant LLM settings (FEAT-049, US-042), bounded by the deployment allow-list (DEC-01).

The API writes ``ops.llm_settings``; the gateway reads it (cached 30 s) to pick the provider, the
model per agent role, the token budget, the aggregates toggle, the provider key's ``secret_ref`` and the Azure
endpoint. A tenant can only choose a provider that the deployment allows (``TELA_LLM_ALLOWED_PROVIDERS``);
with no tenant row, the deployment defaults apply.
"""

from __future__ import annotations

import time
from typing import Any, Protocol

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

ROLE_BY_TASK = {
    "intent": "planner", "clarify": "planner", "plan": "planner", "plan_review": "planner", "model_assist": "planner",
    "map_suggest": "planner", "report_plan": "planner", "canvas_edit": "planner", "sql_draft": "planner",
    "pack_author": "planner", "frame": "planner",
    "narrate": "narrator", "explain": "narrator", "cert_review": "narrator", "visual_advice": "advisor",
}


class TenantSettingsSource(Protocol):
    async def get(self, tenant_id: str) -> dict[str, Any] | None: ...


class MemoryTenantSettings:
    def __init__(self, rows: dict[str, dict[str, Any]] | None = None) -> None:
        self.rows = rows or {}

    async def get(self, tenant_id: str) -> dict[str, Any] | None:
        return self.rows.get(tenant_id)


class DbTenantSettings:
    def __init__(self, engine: AsyncEngine, ttl_s: float = 30.0) -> None:
        self.engine = engine
        self.ttl_s = ttl_s
        self._cache: dict[str, tuple[float, dict[str, Any] | None]] = {}

    async def get(self, tenant_id: str) -> dict[str, Any] | None:
        hit = self._cache.get(tenant_id)
        if hit and time.monotonic() - hit[0] < self.ttl_s:
            return hit[1]
        async with self.engine.begin() as conn:
            await conn.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": tenant_id})
            row = (await conn.execute(text(
                "SELECT provider, models, monthly_token_budget, allow_aggregates, secret_ref, endpoint "
                "FROM ops.llm_settings WHERE tenant_id = :t"),
                {"t": tenant_id})).mappings().first()
        value = dict(row) if row else None
        self._cache[tenant_id] = (time.monotonic(), value)
        return value
