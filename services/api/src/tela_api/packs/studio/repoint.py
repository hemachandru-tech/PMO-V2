"""New pack from a template, re-pointed to a schema (US-235; FEAT-075): Create → *Clone a pack* + a connection.

``POST /packs/{id}/repoint`` matches a pack the tenant can see (PMO, or one of its own) to a connection's catalogue
as an install would: the name rules, the Admin's mapping (or the one saved for that pack and connection), the US-199
guards and the US-200 value suggestions, returned as the mapping step ``PackMappingStep`` shows. On a released pack
the US-201 AI suggestions come from ``/starter-packs/{id}/ai-suggestions``. It also lists the names the clone will add
as candidates, and the items whose fields stay unmapped, by kind with the reason and what removing each takes with it.

``POST /packs`` with ``method: clone`` and ``options.connection_id`` creates the clone (``create.clone`` calls
``repoint``): the confirmed source names are **added** to the clone's ``object_candidates`` and field ``columns``,
so the clone matches this source by name next time; the items the Admin removes go (``refactor.remove``), the rest
stay and are Unavailable where the source lacks them; then the renames apply (``refactor.rename``).
"""

from __future__ import annotations

import copy
from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy.ext.asyncio import AsyncConnection

from tela_api.core.db import tenant_tx
from tela_api.identity.principal import Principal
from tela_api.packs.apply import plan
from tela_api.packs.manifest import Binding, PackManifest, PackMatch, bind, match, source_manifest
from tela_api.packs.mapping import PackMapping, check, conflicts, step
from tela_api.packs.router import (
    _ai_available,
    _catalog,
    _model_and_base,
    _Prepared,
    _source_refs,
    _suggest,
    _value_checks,
    saved_mapping,
)
from tela_api.packs.studio import store
from tela_api.packs.studio.deps import AUTHOR, rid
from tela_api.packs.studio.refactor import KINDS, Renames, closure, remove, rename
from tela_api.query.service import QueryService
from tela_common.errors import ErrorCode, TelaError

router = APIRouter(prefix="/api/v1/packs", tags=["packs"])

# How a name was found when the clone needs it as a candidate: the pack's own names and label columns don't.
_CONFIRMED = ("mapped", "similar_name", "model_label")
_FIELD_REASON = {"not_in_source": "Marked as not in this source.",
                 "column_taken": "Its column is mapped to another field."}


class RepointIn(BaseModel):
    connection_id: str = Field(min_length=36, max_length=36)
    mapping: PackMapping | None = None


class CloneOptions(BaseModel):
    """``options`` of a re-pointed clone: the connection, the mapping as confirmed, the items to remove (``kind:key``,
    the rest stay Unavailable) and the renames."""

    model_config = ConfigDict(extra="forbid")

    connection_id: str = Field(min_length=36, max_length=36)
    mapping: PackMapping = Field(default_factory=PackMapping)
    remove: list[str] = Field(default_factory=list, max_length=2000)
    renames: Renames = Field(default_factory=Renames)


def add_candidates(head: dict[str, Any], result: PackMatch) -> tuple[dict[str, Any], list[dict[str, str]]]:
    """Add the source names the match confirmed (the Admin's choices and the similar-name and model-label guesses)
    to the manifest's candidates, after the pack's own; returns the manifest and what was added."""
    added: list[dict[str, str]] = []
    for e in head.get("entities") or []:
        em = result.entities.get(e["key"])
        if em is None or em.object is None:
            continue
        name = em.object.rpartition(".")[2]
        if em.object_how in _CONFIRMED and name.lower() not in {c.lower() for c in e["object_candidates"]}:
            e["object_candidates"] = [*e["object_candidates"], name]
            added.append({"ref": f"entity:{e['key']}", "kind": "entity", "name": name})
        for f in e.get("fields") or []:
            col = em.mapping.get(f["key"])
            if col and em.how.get(f["key"]) in _CONFIRMED and col.lower() not in {c.lower() for c in f["columns"]}:
                f["columns"] = [*f["columns"], col]
                added.append({"ref": f"field:{e['key']}.{f['key']}", "kind": "field", "name": col})
    return head, added


def unmapped(manifest: PackManifest, result: PackMatch, binding: Binding, head: dict[str, Any]) -> list[dict[str, Any]]:
    """The items the source can't serve, grouped by kind (entity, field, measure, dimension, report), each with the
    reason; a table or field also names the measures and dimensions removing it takes with it. A missing table is
    one item (not each of its fields); reports the pack itself marks unavailable are not listed."""
    items: list[dict[str, Any]] = []

    def add(kind: str, key: str, title: str, reason: str) -> None:
        ref = f"{kind}:{key}"
        item: dict[str, Any] = {"ref": ref, "kind": kind, "key": key, "title": title, "reason": reason}
        if kind in ("entity", "field"):
            item["removes_also"] = sorted(r for r in closure(head, {ref}) - {ref} if not r.startswith("field:"))
        items.append(item)

    for ent in manifest.entities:
        em = result.entities[ent.key]
        if em.object is None:
            add("entity", ent.key, ent.label, "Marked as not in this source." if em.object_how == "not_in_source"
                else "No table found on this source.")
            continue
        for fk in em.missing:
            f = next(x for x in ent.fields if x.key == fk)
            reason = _FIELD_REASON.get(em.how.get(fk, ""), "No column found on this source.")
            if f.required:
                reason += f" It is required: without it {ent.label} isn't available."
            add("field", f"{ent.key}.{fk}", f"{ent.label}: {f.label}", reason)
    pack_gaps = set(manifest.unavailable_reports)
    for it in binding.items:
        if it["availability"] == "unavailable" and not (it["kind"] == "report" and it["key"] in pack_gaps):
            add(it["kind"], it["key"], it["title"], f"Needs {', '.join(it['gaps'])}.")
    items.sort(key=lambda i: KINDS.index(i["kind"]))
    return items


def _manifest(row: store.PackRow, docs: dict[str, Any]) -> PackManifest:
    try:
        return source_manifest(store.source(row, docs))[0]
    except (ValueError, ValidationError) as exc:
        raise TelaError(ErrorCode.VALIDATION_ERROR, "Fix the pack's errors before cloning it onto a source.") from exc


async def _matched(conn: AsyncConnection, row: store.PackRow, manifest: PackManifest, connection_id: str,
                   mapping: PackMapping | None) -> tuple[list[dict[str, Any]], dict[str, Any] | None, dict[str, Any],
                                                         PackMapping, PackMatch]:
    """(catalogue, model, its definition, the mapping used, the match); a mapping that doesn't fit is a 400."""
    catalog = await _catalog(conn, connection_id)
    model, base = await _model_and_base(conn, connection_id)
    chosen = mapping if mapping is not None else await saved_mapping(conn, connection_id, row.pack_key)
    result = match(manifest, catalog, model=base, mapping=chosen)
    issues = check(manifest, catalog, chosen, result)
    if issues:
        raise TelaError(ErrorCode.VALIDATION_ERROR, "Some of the mapping does not fit this source.", {"issues": issues})
    return catalog, model, base, chosen, result


@router.post("/{pack_id}/repoint")
async def preview_repoint(pack_id: str, body: RepointIn, request: Request,
                          principal: Principal = AUTHOR) -> dict[str, Any]:
    """The mapping step, the candidates the clone adds and the unmapped items for cloning this pack onto a
    connection. Nothing is saved."""
    service: QueryService = request.app.state.query_service
    async with tenant_tx(principal.tenant_id) as conn:
        row = await store.load(conn, pack_id)
        docs = await store.documents(conn, row.id)
        manifest = _manifest(row, docs)
        catalog, model, base, chosen, result = await _matched(conn, row, manifest, body.connection_id, body.mapping)
        refs = await _source_refs(conn, model)
        ai = row.status == "released" and await _ai_available(conn, request)
        try:
            connection: dict[str, Any] | None = await service._connection(conn, body.connection_id)
        except TelaError:
            connection = None
    binding = bind(manifest, result)
    p = _Prepared(pack={"id": row.id, "pack_key": row.pack_key, "pack_version": row.pack_version}, manifest=manifest,
                  catalog=catalog, result=result, mapping=chosen, model=model, binding=binding,
                  plan=plan(base, binding, manifest, set()), conflicts=conflicts(manifest, result),
                  connection=connection)
    profiles, warnings = await _value_checks(service, p, principal, rid(request))
    suggested = await _suggest(service, p, principal, rid(request), profiles)
    head = store.as_written(row.manifest, docs)
    _, added = add_candidates(copy.deepcopy(head), result)
    return {"pack_id": row.id, "pack": row.pack_key, "version": row.pack_version, "title": row.title,
            "confidence": round(result.confidence, 3), "matched": result.matched,
            "mapping": chosen.model_dump(),
            "mapping_step": step(manifest, catalog, result, chosen, refs, profiles, suggested),
            "checks": {"blocking": p.conflicts, "warnings": warnings, "values_read": bool(profiles),
                       "suggestions": len(suggested), "ai_available": ai},
            "candidates": added, "unmapped": unmapped(manifest, result, binding, head)}


async def repoint(conn: AsyncConnection, row: store.PackRow, head: dict[str, Any], docs: dict[str, Any],
                  options: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """A clone's manifest and documents re-pointed onto ``options.connection_id``: candidates added, items removed,
    renames applied (``head`` and ``docs`` are the clone's copies, changed in place)."""
    try:
        opts = CloneOptions.model_validate(options)
    except ValidationError as exc:
        raise TelaError(ErrorCode.VALIDATION_ERROR, "The clone's options are not valid.",
                        {"errors": [{"path": ".".join(str(p) for p in e["loc"]), "message": e["msg"]}
                                    for e in exc.errors()]}) from exc
    manifest = _manifest(row, docs)
    _, _, _, _, result = await _matched(conn, row, manifest, opts.connection_id, opts.mapping)
    shared = conflicts(manifest, result)
    if shared:
        raise TelaError(ErrorCode.VALIDATION_ERROR, "Two pack fields read one column. Fix the mapping first.",
                        {"issues": shared})
    head, _ = add_candidates(head, result)
    try:
        head, docs = remove(head, docs, set(opts.remove))
        if not opts.renames.empty():
            head, docs = rename(head, docs, opts.renames)
    except ValueError as exc:
        raise TelaError(ErrorCode.VALIDATION_ERROR, str(exc)) from exc
    return head, docs
