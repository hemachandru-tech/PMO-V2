"""Edit a pack draft and check it (US-232).

``PATCH /packs/{id}`` replaces whole manifest sections (``entities``, ``measures`` …) on the revision named in
``If-Match``; a stale revision is refused (412) so two Admins never overwrite each other. A draft with schema errors
is not saved (422, with each error's path); warnings are saved and returned. ``POST /packs/{id}/validate`` checks
the stored draft, or the stored draft with some sections replaced (a side-panel form checks an item before saving).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Header, Request, Response
from pydantic import BaseModel, Field

from tela_api.audit import service as audit
from tela_api.core.db import tenant_tx
from tela_api.identity.principal import Principal
from tela_api.packs.studio import store
from tela_api.packs.studio.deps import AUTHOR, rid
from tela_api.packs.studio.validate import validate
from tela_common.errors import ErrorCode, TelaError

router = APIRouter(prefix="/api/v1/packs", tags=["packs"])


class SectionsIn(BaseModel):
    """Manifest sections to replace, each as written in manifest.yaml (``pack_version``, ``title``, ``signature``,
    ``entities``, ``links``, ``measures``, ``dimensions``, ``calculators``, ``reports``, ``dashboards``,
    ``unavailable_reports``)."""

    sections: dict[str, Any] = Field(default_factory=dict)


def _merged(row: store.PackRow, sections: dict[str, Any]) -> dict[str, Any]:
    unknown = sorted(set(sections) - set(store.SECTIONS))
    if unknown:
        raise TelaError(ErrorCode.VALIDATION_ERROR, f"These can't be changed here: {', '.join(unknown)}.",
                        {"errors": [{"path": k, "message": "Not an editable section."} for k in unknown],
                         "warnings": []})
    return {**row.manifest, **sections}


@router.patch("/{pack_id}")
async def update_draft(pack_id: str, body: SectionsIn, request: Request, response: Response,
                       if_match: str | None = Header(default=None), principal: Principal = AUTHOR) -> dict[str, Any]:
    async with tenant_tx(principal.tenant_id) as conn:
        row = await store.load(conn, pack_id, lock=True)
        store.require_editable(row)
        store.require_revision(row, if_match)
        head = _merged(row, body.sections)
        report = validate(head, await store.documents(conn, row.id))
        if report.errors:
            raise TelaError(ErrorCode.VALIDATION_ERROR, "Fix the errors before saving.", report.out())
        await store.save_manifest(conn, row, head)
        await audit.record(conn, tenant_id=principal.tenant_id, action="pack.draft_saved", object_type="pack",
                           object_id=row.id, outcome="success", actor_id=principal.user_id,
                           correlation_id=rid(request),
                           details={"pack": row.pack_key, "version": head["pack_version"],
                                    "sections": sorted(body.sections)})
        saved = await store.load(conn, row.id)
    response.headers["ETag"] = saved.etag
    return {**store.summary(saved), "manifest": store.ordered(saved.manifest), "checks": report.out()}


@router.post("/{pack_id}/validate")
async def validate_draft(pack_id: str, body: SectionsIn | None = None, principal: Principal = AUTHOR) -> dict[str, Any]:
    async with tenant_tx(principal.tenant_id) as conn:
        row = await store.load(conn, pack_id)
        docs = await store.documents(conn, row.id)
    head = _merged(row, body.sections) if body and body.sections else row.manifest
    return validate(head, docs).out()
