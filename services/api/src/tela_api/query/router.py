"""Query endpoints over the single scoped path (US-022; FEAT-003, FEAT-018 groundwork, FEAT-042 filter values).

* ``POST /query/run`` runs a Query Spec for the caller (exploratory preview; Ask
  and widgets reuse the same service in later epics).
* ``POST /query/filter-values`` lists filter options under the caller's scope.
* ``GET /query/catalog`` lists the certified measures and dimensions of the
  current approved model that the caller may use (hidden fields removed), plus
  the queryable fields of each entity and the approved links between entities,
  which the field builder offers for bands and cross-table measures (US-078),
  with source table and column names for the custom SQL editor (US-080).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from tela_api.core.db import tenant_tx
from tela_api.identity.deps import require
from tela_api.identity.permissions import Perm
from tela_api.identity.principal import Principal
from tela_api.identity.scope import resolve_scope
from tela_api.model.store import current_version_id, load_snapshot
from tela_api.query.service import QueryContext, QueryService
from tela_common.errors import ErrorCode, TelaError

router = APIRouter(prefix="/api/v1/query", tags=["query"])


def _service(request: Request) -> QueryService:
    service: QueryService = request.app.state.query_service
    return service


def _ctx(request: Request, surface: str) -> QueryContext:
    return QueryContext(surface=surface, request_id=getattr(request.state, "request_id", None))


class RunIn(BaseModel):
    spec: dict[str, Any]
    use_cache: bool = True


@router.post("/run")
async def run(body: RunIn, request: Request, principal: Principal = Depends(require(Perm.ASK_USE))) -> dict[str, Any]:
    ctx = _ctx(request, "preview")
    ctx.use_cache = body.use_cache
    return await _service(request).execute(body.spec, principal, ctx)


class FilterValuesIn(BaseModel):
    connection_id: str
    model_version_id: str
    ref: str = Field(pattern=r"^(dimension:[a-z_][a-z0-9_]*|field:[a-z_][a-z0-9_]*\.[a-z_][a-z0-9_]*)$")
    limit: int = Field(default=500, ge=1, le=1000)


@router.post("/filter-values")
async def filter_values(body: FilterValuesIn, request: Request,
                        principal: Principal = Depends(require(Perm.DASHBOARDS_VIEW))) -> dict[str, Any]:
    return await _service(request).filter_values(
        connection_id=body.connection_id, model_version_id=body.model_version_id, ref=body.ref,
        principal=principal, ctx=_ctx(request, "filter_values"), limit=body.limit)


@router.get("/catalog")
async def catalog(connection_id: str, principal: Principal = Depends(require(Perm.ASK_USE))) -> dict[str, Any]:
    async with tenant_tx(principal.tenant_id) as conn:
        version_id = await current_version_id(conn, connection_id)
        if version_id is None:
            raise TelaError(ErrorCode.MODEL_NOT_APPROVED, "This data source has no approved model yet.")
        snap = await load_snapshot(conn, principal.tenant_id, version_id)
        scope = await resolve_scope(conn, principal, connection_id)
    hidden = scope.hidden_fields

    def visible_measure(m: Any) -> bool:
        from tela_query_spec.expr import measure_refs

        return all(r not in hidden for r in measure_refs(m.entity, m.expression))

    return {
        "connection_id": connection_id, "model_version_id": version_id, "version_no": snap.version_no,
        "engine": snap.engine,
        "scope": {"summary": scope.summary, "locked": True},
        "measures": [{"ref": f"measure:{m.key}", "label": m.label, "plain_rule": m.plain_rule, "format": m.format,
                      "entity": m.entity, "synonyms": m.synonyms, "rule_ref": m.rule_ref}
                     for m in snap.measures if m.status == "certified" and visible_measure(m)],
        "dimensions": [{"ref": f"dimension:{d.key}", "label": d.label, "entity": d.entity, "temporal": d.is_temporal,
                        "synonyms": d.synonyms}
                       for d in snap.dimensions if f"{d.entity}.{d.field}" not in hidden],
        "entities": [{"key": e.key, "label": e.label, "table": e.source_object.rpartition(".")[2],
                      "fields": [{"ref": f"field:{e.key}.{f.key}", "key": f.key, "label": f.label,
                                  "data_type": f.data_type, "column": f.source_column}
                                 for f in e.fields if f.is_queryable and f"{e.key}.{f.key}" not in hidden]}
                     for e in snap.entities],
        "links": sorted({tuple(sorted((link.from_entity, link.to_entity))) for link in snap.links if link.accepted}),
    }
