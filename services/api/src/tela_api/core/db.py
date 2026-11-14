"""Async database access with tenant context (US-005, US-006).

Every unit of work runs in a transaction that first sets the transaction-local
``app.tenant_id`` (``set_config(..., true)`` is equivalent to ``SET LOCAL``), so
PostgreSQL row-level security only exposes that tenant's rows. This is safe
with PgBouncer in transaction mode.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine

from tela_api.core.settings import Settings
from tela_common.observability import instrument_engine

_engine: AsyncEngine | None = None


def init_engine(settings: Settings) -> AsyncEngine:
    global _engine
    if _engine is None:
        _engine = create_async_engine(
            settings.async_database_url,
            pool_size=settings.db_pool_size,
            max_overflow=settings.db_pool_size,
            pool_pre_ping=True,
        )
        instrument_engine(_engine)
    return _engine


def get_engine() -> AsyncEngine:
    if _engine is None:
        raise RuntimeError("database engine not initialised")
    return _engine


async def dispose_engine() -> None:
    global _engine
    if _engine is not None:
        await _engine.dispose()
        _engine = None


@asynccontextmanager
async def tenant_tx(tenant_id: str | None) -> AsyncIterator[AsyncConnection]:
    """Open a transaction scoped to one tenant (or none, for pre-tenant lookups)."""
    async with get_engine().begin() as conn:
        if tenant_id is not None:
            await conn.execute(text("SELECT set_config('app.tenant_id', :tid, true)"), {"tid": str(tenant_id)})
        yield conn


async def fetch_one(conn: AsyncConnection, sql: str, params: dict[str, Any] | None = None) -> dict[str, Any] | None:
    row = (await conn.execute(text(sql), params or {})).mappings().first()
    return dict(row) if row else None


async def fetch_all(conn: AsyncConnection, sql: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    return [dict(r) for r in (await conn.execute(text(sql), params or {})).mappings().all()]
