"""Model snapshot loading (US-031, US-035).

Approved versions are immutable, so their parsed snapshots are cached in
process by (tenant, version id). Drafts are always read fresh.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncConnection

from tela_api.core.db import fetch_one
from tela_common.errors import ErrorCode, TelaError
from tela_query_spec.model import ModelSnapshot

_cache: dict[tuple[str, str], ModelSnapshot] = {}


def build_snapshot(definition: dict[str, Any], *, version_id: str, connection_id: str,
                   version_no: int | None, engine: str) -> ModelSnapshot:
    return ModelSnapshot.model_validate({
        **definition, "model_version_id": version_id, "connection_id": connection_id, "version_no": version_no,
        "engine": engine,
    })


async def load_snapshot(conn: AsyncConnection, tenant_id: str, version_id: str, *,
                        allow_draft: bool = False) -> ModelSnapshot:
    cached = _cache.get((tenant_id, version_id))
    if cached is not None:
        return cached
    row = await fetch_one(
        conn,
        """
        SELECT v.id, v.status, v.version_no, v.definition, m.connection_id, c.engine
        FROM model.model_versions v
        JOIN model.data_models m ON m.id = v.data_model_id
        JOIN conn.connections c ON c.id = m.connection_id
        WHERE v.id = CAST(:id AS uuid)
        """,
        {"id": version_id},
    )
    if row is None:
        raise TelaError(ErrorCode.NOT_FOUND, "That model version does not exist.")
    if row["status"] == "draft" and not allow_draft:
        raise TelaError(ErrorCode.MODEL_NOT_APPROVED, "This model has not been approved yet.")
    snap = build_snapshot(row["definition"], version_id=str(row["id"]), connection_id=str(row["connection_id"]),
                          version_no=row["version_no"], engine=row["engine"])
    if row["status"] != "draft":
        _cache[(tenant_id, version_id)] = snap
    return snap


async def current_version_id(conn: AsyncConnection, connection_id: str) -> str | None:
    row = await fetch_one(
        conn, "SELECT current_version_id FROM model.data_models WHERE connection_id = CAST(:c AS uuid)",
        {"c": connection_id},
    )
    return str(row["current_version_id"]) if row and row["current_version_id"] else None
