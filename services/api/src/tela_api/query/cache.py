"""Scope-keyed result cache (US-026; ADR-010; RISK-015).

Key = sha256(tenant | connection | model_version | statement_hash | scope_hash
| page_filter_hash | as_of_bucket). The statement hash covers the compiled
text **and** its bound values, and the scope hash covers row scope and hidden
fields, so two people with different scopes can never share an entry.

Values live in Redis with a TTL. PostgreSQL keeps only index metadata
(``ops.result_cache_index``) for "data as of" and invalidation. Invalidation
runs on model approval, scope or field policy change, connection edit and
manual refresh.
"""

from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime, timedelta
from typing import Any, Protocol

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from tela_api.core.db import fetch_all


def cache_key(*, tenant_id: str, connection_id: str, model_version_id: str, statement_hash: str, scope_hash: str,
              page_filter_hash: str = "", as_of_bucket: str = "") -> str:
    raw = "|".join([tenant_id, connection_id, model_version_id, statement_hash, scope_hash, page_filter_hash,
                    as_of_bucket])
    return "tela:result:" + hashlib.sha256(raw.encode()).hexdigest()


class CacheBackend(Protocol):
    async def get(self, key: str) -> bytes | None: ...
    async def set(self, key: str, value: bytes, ttl_s: int) -> None: ...
    async def delete(self, *keys: str) -> None: ...


class RedisBackend:
    def __init__(self, redis: Any) -> None:
        self.redis = redis

    async def get(self, key: str) -> bytes | None:
        value: bytes | None = await self.redis.get(key)
        return value

    async def set(self, key: str, value: bytes, ttl_s: int) -> None:
        await self.redis.set(key, value, ex=ttl_s)

    async def delete(self, *keys: str) -> None:
        if keys:
            await self.redis.delete(*keys)


class MemoryBackend:
    """Process-local backend for tests and single-process development."""

    def __init__(self) -> None:
        self._data: dict[str, tuple[float, bytes]] = {}

    async def get(self, key: str) -> bytes | None:
        hit = self._data.get(key)
        if hit is None or hit[0] < time.monotonic():
            self._data.pop(key, None)
            return None
        return hit[1]

    async def set(self, key: str, value: bytes, ttl_s: int) -> None:
        self._data[key] = (time.monotonic() + ttl_s, value)

    async def delete(self, *keys: str) -> None:
        for k in keys:
            self._data.pop(k, None)


class ResultCache:
    def __init__(self, backend: CacheBackend) -> None:
        self.backend = backend

    async def get(self, key: str) -> dict[str, Any] | None:
        raw = await self.backend.get(key)
        return json.loads(raw) if raw else None

    async def put(self, conn: AsyncConnection, *, key: str, tenant_id: str, connection_id: str,
                  widget_id: str | None, as_of: datetime, ttl_s: int, value: dict[str, Any]) -> None:
        if ttl_s <= 0:
            return
        await self.backend.set(key, json.dumps(value, default=str).encode(), ttl_s)
        await conn.execute(
            text(
                "INSERT INTO ops.result_cache_index (cache_key, tenant_id, connection_id, widget_id, as_of, "
                "expires_at) VALUES (:k, :t, CAST(:c AS uuid), CAST(:w AS uuid), :a, :e) "
                "ON CONFLICT (cache_key) DO UPDATE SET as_of = EXCLUDED.as_of, expires_at = EXCLUDED.expires_at"
            ),
            {"k": key, "t": tenant_id, "c": connection_id, "w": widget_id, "a": as_of,
             "e": as_of + timedelta(seconds=ttl_s)},
        )

    async def invalidate_connection(self, conn: AsyncConnection, connection_id: str) -> int:
        rows = await fetch_all(
            conn, "DELETE FROM ops.result_cache_index WHERE connection_id = CAST(:c AS uuid) RETURNING cache_key",
            {"c": connection_id},
        )
        await self.backend.delete(*(r["cache_key"] for r in rows))
        return len(rows)

    async def invalidate_tenant(self, conn: AsyncConnection) -> int:
        rows = await fetch_all(conn, "DELETE FROM ops.result_cache_index RETURNING cache_key")
        await self.backend.delete(*(r["cache_key"] for r in rows))
        return len(rows)
