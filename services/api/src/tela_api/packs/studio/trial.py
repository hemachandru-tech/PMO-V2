"""Try a pack measure on a connection (US-232): the draft is matched to the connection's schema as an install would
(the saved field mapping included), bound, and the one measure runs over every row (``QueryService.trial``, the
US-199 trial figure: aggregates only). Nothing is saved; a measure whose fields the source lacks is named with its
gaps instead of run.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field, ValidationError

from tela_api.audit import service as audit
from tela_api.core.db import tenant_tx
from tela_api.identity.principal import Principal
from tela_api.model.catalog import stamp_source_types
from tela_api.model.store import build_snapshot
from tela_api.packs.manifest import bind, match, source_manifest
from tela_api.packs.router import _catalog, _model_and_base, saved_mapping
from tela_api.packs.studio import store
from tela_api.packs.studio.deps import AUTHOR, rid
from tela_api.query.service import QueryContext, QueryService
from tela_common.errors import ErrorCode, TelaError
from tela_common.ids import new_id

router = APIRouter(prefix="/api/v1/packs", tags=["packs"])


class TryIn(BaseModel):
    connection_id: str = Field(min_length=36, max_length=36)
    measure: str = Field(min_length=1, max_length=80)


@router.post("/{pack_id}/try")
async def try_measure(pack_id: str, body: TryIn, request: Request, principal: Principal = AUTHOR) -> dict[str, Any]:
    service: QueryService = request.app.state.query_service
    async with tenant_tx(principal.tenant_id) as conn:
        row = await store.load(conn, pack_id)
        docs = await store.documents(conn, row.id)
        try:
            manifest, _, _ = source_manifest(store.source(row, docs))
        except (ValueError, ValidationError) as exc:
            raise TelaError(ErrorCode.VALIDATION_ERROR, "Fix the pack's errors before trying it.") from exc
        if not any(m["key"] == body.measure for m in manifest.measures):
            raise TelaError(ErrorCode.NOT_FOUND, "The pack has no measure with that key.")
        catalog = await _catalog(conn, body.connection_id)
        _, base = await _model_and_base(conn, body.connection_id)
        result = match(manifest, catalog, model=base, mapping=await saved_mapping(conn, body.connection_id,
                                                                                  row.pack_key))
        try:
            connection = await service._connection(conn, body.connection_id)
        except TelaError as exc:
            raise TelaError(ErrorCode.CONNECTION_NOT_READY, "This data source can't be queried right now.") from exc
    binding = bind(manifest, result)
    item = next((i for i in binding.items if i["kind"] == "measure" and i["key"] == body.measure), None)
    measures = [m for m in binding.definition["measures"] if m["key"] == body.measure]
    if not measures or not binding.definition["entities"]:
        return {"measure": body.measure, "available": False, "value": None,
                "gaps": list(item["gaps"]) if item else ["the pack's entities are not found on this source"],
                "confidence": round(result.confidence, 3)}
    definition = {**binding.definition, "measures": measures, "dimensions": []}
    try:
        snap = build_snapshot(definition, version_id=new_id(), connection_id=body.connection_id, version_no=None,
                              engine=connection["engine"])
    except ValueError as exc:
        raise TelaError(ErrorCode.VALIDATION_ERROR, f"The measure doesn't bind on this source: {exc}") from exc
    stamp_source_types(snap, {c["qualified_name"].lower(): c["columns"] for c in catalog})
    _, figures = await service.trial(snapshot=snap, connection=connection, principal=principal,
                                     ctx=QueryContext(surface="precheck", request_id=rid(request), use_cache=False),
                                     fields=[], measures=True)
    value = figures.get(body.measure)
    async with tenant_tx(principal.tenant_id) as conn:
        await audit.record(conn, tenant_id=principal.tenant_id, action="pack.measure_tried", object_type="pack",
                           object_id=row.id, outcome="success", actor_id=principal.user_id,
                           correlation_id=rid(request),
                           details={"pack": row.pack_key, "measure": body.measure, "connection": body.connection_id})
    return {"measure": body.measure, "available": value is not None, "value": value,
            "gaps": [] if value is not None else ["the source could not compute it"],
            "confidence": round(result.confidence, 3)}
