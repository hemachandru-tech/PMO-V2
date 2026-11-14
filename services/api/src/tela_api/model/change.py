"""One governed change to an approved model, approved at once as its next version (US-166, US-198).

Certifying a free-hand rule adds a measure (US-166); approving a curated synonym adds a word to a measure (US-198).
Both go through the model's own gate: the change is written to the draft and the draft approved (``model.approve``:
checks, dry run at the source, freeze, re-indexing). A draft that holds other unapproved changes is left alone; on
any failure the draft is put back as it was.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from fastapi import Request
from sqlalchemy import text

from tela_api.core.db import fetch_one, tenant_tx
from tela_api.identity.permissions import Perm
from tela_api.identity.principal import Principal
from tela_common.errors import ErrorCode, TelaError

MSG_DRAFT_BUSY = "The model has unapproved changes in its draft. Approve or discard them first, then {what}."


async def apply_change(request: Request, principal: Principal, connection_id: str,
                       change: Callable[[dict[str, Any]], dict[str, Any]], *, note: str, what: str,
                       denied: str) -> dict[str, Any]:
    """``change`` maps the current definition to the new one (it may raise to refuse). Returns the approval's
    result (``model_version_id``, ``version``). ``what`` ends the busy-draft message; ``denied`` explains a missing
    ``model.approve``."""
    from tela_api.model.router import ApproveIn, _draft_etag, approve_draft, ensure_draft

    if not principal.has(Perm.MODEL_APPROVE):
        raise TelaError(ErrorCode.FORBIDDEN, denied)
    async with tenant_tx(principal.tenant_id) as conn:
        model = await fetch_one(conn, "SELECT * FROM model.data_models WHERE connection_id = :c",
                                {"c": connection_id})
        if model is None or model["current_version_id"] is None:
            raise TelaError(ErrorCode.MODEL_NOT_APPROVED, "This source has no approved model.")
        current = await fetch_one(conn, "SELECT definition FROM model.model_versions WHERE id = :id",
                                  {"id": model["current_version_id"]})
        assert current is not None
        definition = change(current["definition"])
        created = model["draft_version_id"] is None
        if not created:
            draft = await fetch_one(conn, "SELECT definition FROM model.model_versions WHERE id = :id",
                                    {"id": model["draft_version_id"]})
            if draft is None or draft["definition"] != current["definition"]:
                raise TelaError(ErrorCode.CONFLICT, MSG_DRAFT_BUSY.format(what=what))
        draft = await ensure_draft(conn, model, principal)
        before = draft["definition"]
        row = await fetch_one(conn, "UPDATE model.model_versions SET definition = CAST(:d AS jsonb), draft_revision = "
                                    "draft_revision + 1 WHERE id = :id RETURNING draft_revision",
                              {"d": json.dumps(definition), "id": draft["id"]})
        assert row is not None
    try:
        return await approve_draft(str(model["id"]), ApproveIn(draft_etag=_draft_etag(row["draft_revision"]),
                                                               note=note), request, principal)
    except TelaError:
        async with tenant_tx(principal.tenant_id) as conn:
            if created:
                await conn.execute(text("UPDATE model.data_models SET draft_version_id = NULL WHERE id = :m AND "
                                        "draft_version_id = :d"), {"m": model["id"], "d": draft["id"]})
                await conn.execute(text("DELETE FROM model.model_versions WHERE id = :d AND status = 'draft'"),
                                   {"d": draft["id"]})
            else:
                await conn.execute(text("UPDATE model.model_versions SET definition = CAST(:d AS jsonb), "
                                        "draft_revision = draft_revision + 1 WHERE id = :id AND status = 'draft'"),
                                   {"d": json.dumps(before), "id": draft["id"]})
        raise
