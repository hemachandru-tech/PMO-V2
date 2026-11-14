"""Transactional outbox writer (US-009; ADR-011; DATA-050).

Rows are inserted in the same transaction as the state change; the worker's
``outbox_relay`` task publishes them.
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection


async def emit(
    conn: AsyncConnection, *, tenant_id: str, topic: str, aggregate_id: str, payload: dict[str, Any]
) -> None:
    await conn.execute(
        text(
            "INSERT INTO ops.outbox_events (tenant_id, topic, aggregate_id, payload) "
            "VALUES (:tenant_id, :topic, :aggregate_id, CAST(:payload AS jsonb))"
        ),
        {"tenant_id": tenant_id, "topic": topic, "aggregate_id": aggregate_id, "payload": json.dumps(payload)},
    )
