"""Packs list, one pack, its versions and documents; create and delete a draft (US-232; API-0xx ``/api/v1/packs``).

Everyone with ``packs.author`` (Admin, DEC-26) sees the system packs (read-only; clone to edit) and the tenant's
own packs with every version and where it is installed.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Header, Request, Response
from pydantic import BaseModel, Field

from tela_api.audit import service as audit
from tela_api.core.db import tenant_tx
from tela_api.identity.principal import Principal
from tela_api.packs.source import KINDS
from tela_api.packs.studio import attachments, store
from tela_api.packs.studio.create import CREATORS, PLANNED, CreateArgs
from tela_api.packs.studio.deps import AUTHOR, rid
from tela_api.packs.studio.validate import validate
from tela_common.errors import ErrorCode, TelaError

router = APIRouter(prefix="/api/v1/packs", tags=["packs"])
def _vkey(version: str) -> tuple[int, ...]:
    return tuple(int(p) for p in version.split("."))


@router.get("")
async def list_packs(principal: Principal = AUTHOR) -> dict[str, Any]:
    """Every pack the tenant can see, one entry per pack key, newest version first."""
    async with tenant_tx(principal.tenant_id) as conn:
        rows = await store.all_packs(conn)
        where = await store.installs(conn, [r.id for r in rows])
    packs: dict[tuple[str, str], dict[str, Any]] = {}
    for r in rows:
        entry = packs.setdefault((r.tenant_id or "", r.pack_key), {
            "pack_key": r.pack_key, "owner": "system" if r.system else "tenant", "versions": []})
        entry["versions"].append(store.summary(r, where.get(r.id)))
    items = []
    for entry in packs.values():
        entry["versions"].sort(key=lambda v: _vkey(v["version"]), reverse=True)
        latest = entry["versions"][0]
        items.append({**entry, "title": latest["title"], "latest": latest,
                      "installed": sum(len(v["installed_on"]) for v in entry["versions"])})
    items.sort(key=lambda p: (p["owner"] != "system", p["title"].lower()))
    return {"items": items, "create": {"methods": sorted(CREATORS), "planned": PLANNED}}


@router.get("/{pack_id}")
async def get_pack(pack_id: str, response: Response, principal: Principal = AUTHOR) -> dict[str, Any]:
    """One pack version: its manifest (sections in file order), its knowledge documents' sizes and the checks."""
    async with tenant_tx(principal.tenant_id) as conn:
        row = await store.load(conn, pack_id)
        docs = await store.documents(conn, row.id)
        where = await store.installs(conn, [row.id])
    response.headers["ETag"] = row.etag
    return {**store.summary(row, where.get(row.id)), "manifest": store.ordered(row.manifest),
            "documents": {k: _size(v) for k, v in docs.items()}, "checks": validate(row.manifest, docs).out()}


def _size(body: Any) -> int:
    """Items in a knowledge document (the length of its main list)."""
    if isinstance(body, dict):
        lists = [v for v in body.values() if isinstance(v, list)]
        return max((len(v) for v in lists), default=0)
    return 0


@router.get("/{pack_id}/versions")
async def list_versions(pack_id: str, principal: Principal = AUTHOR) -> dict[str, Any]:
    """Every version of this pack (same key and owner), newest first, with where each is installed."""
    async with tenant_tx(principal.tenant_id) as conn:
        row = await store.load(conn, pack_id)
        rows = [r for r in await store.all_packs(conn) if r.pack_key == row.pack_key and r.tenant_id == row.tenant_id]
        where = await store.installs(conn, [r.id for r in rows])
    versions = [store.summary(r, where.get(r.id)) for r in rows]
    versions.sort(key=lambda v: _vkey(v["version"]), reverse=True)
    return {"pack_key": row.pack_key, "items": versions}


@router.get("/{pack_id}/documents/{kind}")
async def get_document(pack_id: str, kind: str, principal: Principal = AUTHOR) -> dict[str, Any]:
    if kind not in KINDS:
        raise TelaError(ErrorCode.NOT_FOUND, "Unknown knowledge document.")
    async with tenant_tx(principal.tenant_id) as conn:
        row = await store.load(conn, pack_id)
        docs = await store.documents(conn, row.id)
    if kind not in docs:
        raise TelaError(ErrorCode.NOT_FOUND, "This pack has no document of that kind.")
    return {"kind": kind, "body": docs[kind]}


class CreateIn(BaseModel):
    method: str = Field(default="blank", max_length=20)
    pack_key: str = Field(pattern=r"^[a-z][a-z0-9_]{1,40}$")
    title: str = Field(min_length=1, max_length=120)
    version: str = Field(default="0.1.0", pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")
    from_pack_id: str | None = None
    options: dict[str, Any] = Field(default_factory=dict)


@router.post("", status_code=201)
async def create_pack(body: CreateIn, request: Request, response: Response,
                      principal: Principal = AUTHOR) -> dict[str, Any]:
    """A new draft: blank, a copy of a pack the tenant can see (``clone``) or from an approved model (``model``,
    US-236, with ``options``); what the method could not take is listed in ``left_out``."""
    creator = CREATORS.get(body.method)
    if creator is None:
        story = PLANNED.get(body.method)
        raise TelaError(ErrorCode.VALIDATION_ERROR, f"Creating a pack this way comes with {story}." if story else
                        f"Unknown way to create a pack: {body.method}.")
    async with tenant_tx(principal.tenant_id) as conn:
        if await store.key_taken(conn, body.pack_key):
            raise TelaError(ErrorCode.CONFLICT, f"A pack with the key {body.pack_key} already exists. Pick another "
                                                "key.", {"field": "pack_key"})
        draft = await creator(conn, principal, CreateArgs(body.pack_key, body.title, body.version, body.from_pack_id,
                                                          body.options))
        report = validate(draft.head, draft.documents)
        if report.errors:
            raise TelaError(ErrorCode.VALIDATION_ERROR, "The new pack doesn't pass its checks.", report.out())
        pack_id = await store.insert(conn, principal, draft.head, draft.documents, based_on=draft.based_on)
        if draft.based_on:  # its context documents too (US-233)
            await attachments.copy_to_pack(conn, principal, draft.based_on, pack_id)
        await audit.record(conn, tenant_id=principal.tenant_id, action="pack.created", object_type="pack",
                           object_id=pack_id, outcome="success", actor_id=principal.user_id,
                           correlation_id=rid(request),
                           details={"pack": body.pack_key, "version": body.version, "method": body.method,
                                    **({"from": draft.based_on} if draft.based_on else {}),
                                    **({"source": draft.source} if draft.source else {}),
                                    **({"left_out": len(draft.left_out)} if draft.left_out else {})})
        row = await store.load(conn, pack_id)
    response.headers["ETag"] = row.etag
    return {**store.summary(row), "checks": report.out(), "left_out": draft.left_out,
            **({"source": draft.source} if draft.source else {})}


@router.delete("/{pack_id}", status_code=204)
async def delete_pack(pack_id: str, request: Request, if_match: str | None = Header(default=None),
                      principal: Principal = AUTHOR) -> Response:
    """Delete a draft version (a released version is retired, never deleted)."""
    async with tenant_tx(principal.tenant_id) as conn:
        row = await store.load(conn, pack_id, lock=True)
        store.require_editable(row)
        store.require_revision(row, if_match)
        await store.delete(conn, row)
        await audit.record(conn, tenant_id=principal.tenant_id, action="pack.deleted", object_type="pack",
                           object_id=row.id, outcome="success", actor_id=principal.user_id,
                           correlation_id=rid(request), details={"pack": row.pack_key, "version": row.pack_version})
    return Response(status_code=204)
