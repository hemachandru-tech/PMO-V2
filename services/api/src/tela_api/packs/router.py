"""Starter pack detection and install (API-015; FEAT-010; US-036).

* ``GET /starter-packs/match?connection_id=`` scores the introspected catalogue
  against every loaded pack: confidence, matched / missing / extra columns and
  the field mapping (physical extract names or Dataverse logical names).
* ``POST /starter-packs/{pack_id}/install`` builds a **draft** model with the
  pack's entities, links, certified measures and dimensions bound to the names
  found, and records starter items (measures, dimensions, reports) with their
  availability. Nothing is live until the Admin approves the model.
* ``POST /starter-packs/{pack_id}/preview`` shows what an install would do. On a connection that already has a
  model the install *merges* the pack into the model draft (``packs/apply.py``; US-153, BUG-004): the Admin's own
  items stay and clashes take the pack's certified form unless listed in ``keep_mine``.
* Names that differ (US-154): matching also uses the model's labels, Dataverse label columns and similar names;
  the preview returns a mapping step and takes the Admin's ``mapping`` (pack field → column or "not in this source",
  source value → pack value). Install saves it per connection and pack (``model.pack_field_mappings``); re-apply
  and pack updates reuse it.
* Mapping guards (US-199; ``model/guards.py``): the preview lists pack fields that read one column (install refuses
  them) and shows each status-like field's column values beside the pack's, with a warning when the pack's values
  are missing. ``POST /starter-packs/{pack_id}/check`` runs a trial figure of each bound measure and lists those that
  return 0 or the same figure as another measure with a different rule.
* Value-based suggestions (US-200): for a pack field with known values that the name rules left unmapped, or whose
  column's values don't fit, the preview reads the table's other text columns and suggests the one whose values match
  the pack's, with the evidence and a confidence. Nothing is applied until the Admin picks it.
* LLM suggestions (US-201; ``packs/ai_suggest.py``): ``POST /starter-packs/{pack_id}/ai-suggestions`` asks the
  tenant's model (AG-04) about the fields names and values leave open, one call per pack table, and returns a column
  (or none) with a confidence and a reason, flagged where US-199's guards would refuse it. Nothing is applied; with no
  model set up it answers ``available: false`` and the step works as before (the preview says ``ai_available``).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import text

from tela_api.audit import service as audit
from tela_api.core.db import fetch_all, fetch_one, tenant_tx
from tela_api.identity.deps import require
from tela_api.identity.permissions import Perm
from tela_api.identity.principal import Principal
from tela_api.llm import trace
from tela_api.llm.client import AgentFailed, GatewayClient, run_agent
from tela_api.llm.planner_fit import planner_provider
from tela_api.model import guards
from tela_api.model.catalog import stamp_source_types
from tela_api.model.router import _model, _snapshot_or_issues, ensure_draft, insert_questions
from tela_api.model.store import build_snapshot
from tela_api.packs import ai_suggest
from tela_api.packs.apply import ApplyPlan, plan
from tela_api.packs.manifest import Binding, PackManifest, PackMatch, bind, match
from tela_api.packs.mapping import (
    PackMapping,
    candidate_columns,
    candidate_definition,
    check,
    conflicts,
    pack_values,
    profile_definition,
    step,
    suggestions,
    wanted_suggestions,
)
from tela_api.query.service import QueryContext, QueryService
from tela_common.errors import ErrorCode, TelaError
from tela_common.ids import new_id

router = APIRouter(prefix="/api/v1/starter-packs", tags=["starter-packs"])


async def _catalog(conn: Any, connection_id: str) -> list[dict[str, Any]]:
    rows = await fetch_all(
        conn, "SELECT qualified_name, columns FROM conn.connection_objects WHERE connection_id = CAST(:c AS uuid) "
              "AND removed_at IS NULL AND readable IS NOT FALSE", {"c": connection_id})  # unreadable: as if absent
    if not rows:
        raise TelaError(ErrorCode.CONNECTION_NOT_READY, "Read the schema (introspect) first.")
    return rows


@router.get("")
async def list_packs(principal: Principal = Depends(require(Perm.MODEL_APPROVE))) -> dict[str, Any]:
    async with tenant_tx(principal.tenant_id) as conn:
        rows = await fetch_all(conn, "SELECT id, pack_key, pack_version, title, released_at FROM model.starter_packs "
                                     "WHERE status = 'released' ORDER BY pack_key, pack_version")
    return {"items": [{**r, "id": str(r["id"])} for r in rows]}


async def _model_and_base(conn: Any, connection_id: str) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """The connection's model (or None) and its draft (else approved) definition."""
    existing = await fetch_one(conn, "SELECT id FROM model.data_models WHERE connection_id = CAST(:c AS uuid)",
                               {"c": connection_id})
    model = await _model(conn, str(existing["id"])) if existing else None
    base: dict[str, Any] = {"entities": [], "links": [], "measures": [], "dimensions": [], "settings": {}}
    if model is not None:
        version = model["draft_version_id"] or model["current_version_id"]
        row = await fetch_one(conn, "SELECT definition FROM model.model_versions WHERE id = :id", {"id": version}) \
            if version else None
        base = row["definition"] if row else base
    return model, base


async def saved_mapping(conn: Any, connection_id: str, pack_key: str, *, resolved: bool = False) -> PackMapping:
    """The Admin's saved mapping for this connection and pack; with ``resolved``, on top of the names the last
    install bound (so a pack update binds the same columns)."""
    row = await fetch_one(conn, "SELECT mapping, resolved FROM model.pack_field_mappings WHERE "
                                "connection_id = CAST(:c AS uuid) AND pack_key = :k",
                          {"c": connection_id, "k": pack_key})
    if row is None:
        return PackMapping()
    mine = PackMapping.model_validate(row["mapping"] or {})
    if not resolved:
        return mine
    return PackMapping.model_validate({"entities": {}, "fields": {}, **(row["resolved"] or {})}).merged(mine)


async def _source_refs(conn: Any, model: dict[str, Any] | None) -> dict[tuple[str, str], Any]:
    """(source object, column) → a field of the approved model, so the mapping step can list source values."""
    if model is None or not model["current_version_id"]:
        return {}
    row = await fetch_one(conn, "SELECT definition FROM model.model_versions WHERE id = :id",
                          {"id": model["current_version_id"]})
    refs: dict[tuple[str, str], Any] = {}
    for e in (row["definition"] if row else {}).get("entities", []):
        for f in e.get("fields", []):
            refs.setdefault((str(e["source_object"]), str(f["source_column"])),
                            {"model_version_id": str(model["current_version_id"]),
                             "ref": f"field:{e['key']}.{f['key']}"})
    return refs


@router.get("/match")
async def match_packs(connection_id: str,
                      principal: Principal = Depends(require(Perm.MODEL_APPROVE))) -> dict[str, Any]:
    async with tenant_tx(principal.tenant_id) as conn:
        catalog = await _catalog(conn, connection_id)
        packs = await fetch_all(conn, "SELECT id, pack_key, pack_version, title, manifest FROM model.starter_packs "
                                      "WHERE status = 'released'")
        _model_row, base = await _model_and_base(conn, connection_id)
        mappings = {p["pack_key"]: await saved_mapping(conn, connection_id, p["pack_key"]) for p in packs}
    matches = []
    for p in packs:
        result = match(PackManifest.model_validate(p["manifest"]), catalog, model=base, mapping=mappings[p["pack_key"]])
        matches.append({"pack_id": str(p["id"]), "pack_key": p["pack_key"], "version": p["pack_version"],
                        "title": p["title"], **result.out()})
    matches.sort(key=lambda m: m["confidence"], reverse=True)
    return {"matches": matches}


class InstallIn(BaseModel):
    connection_id: str
    reporting_date: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    keep_mine: list[str] = Field(default_factory=list, max_length=500)
    mapping: PackMapping | None = None  # replaces the saved mapping (US-154)


class PreviewIn(BaseModel):
    connection_id: str
    mapping: PackMapping | None = None


@dataclass
class _Prepared:
    pack: dict[str, Any]
    manifest: PackManifest
    catalog: list[dict[str, Any]]
    result: PackMatch
    mapping: PackMapping
    model: dict[str, Any] | None
    binding: Binding
    plan: ApplyPlan
    conflicts: list[dict[str, str]]
    connection: dict[str, Any] | None  # for the guards' reads; None when the source can't be queried now


async def _prepare(conn: Any, service: QueryService, pack_id: str, connection_id: str, reporting_date: str | None,
                   keep_mine: list[str], mapping: PackMapping | None, *, require_match: bool) -> _Prepared:
    """The pack, its match (with the Admin's mapping), the existing model (or None) and the merge plan."""
    pack = await fetch_one(conn, "SELECT id, pack_key, pack_version, manifest FROM model.starter_packs "
                                 "WHERE id = CAST(:id AS uuid) AND status = 'released'", {"id": pack_id})
    if pack is None:
        raise TelaError(ErrorCode.NOT_FOUND, "Starter pack not found.")
    manifest = PackManifest.model_validate(pack["manifest"])
    catalog = await _catalog(conn, connection_id)
    model, base = await _model_and_base(conn, connection_id)
    chosen = mapping if mapping is not None else await saved_mapping(conn, connection_id, pack["pack_key"])
    result = match(manifest, catalog, model=base, mapping=chosen)
    issues = check(manifest, catalog, chosen, result)
    if issues:
        raise TelaError(ErrorCode.VALIDATION_ERROR, "Some of the mapping does not fit this source.",
                        {"issues": issues})
    shared = conflicts(manifest, result)
    if require_match and shared:
        raise TelaError(ErrorCode.VALIDATION_ERROR, "Two pack fields read one column. Fix the mapping first.",
                        {"issues": shared})
    if require_match and not result.matched:
        raise TelaError(ErrorCode.PACK_NOT_MATCHED, "This database does not look like the pack's source. Map the "
                                                     "pack's required fields, or use the model assistant instead.",
                        result.out())
    if model is not None and model["pending_pack_id"]:
        raise TelaError(ErrorCode.CONFLICT, "A pack update is waiting for this model's approval. Approve or "
                                            "discard the draft first.")
    binding = bind(manifest, result, reporting_date=reporting_date)
    try:
        connection: dict[str, Any] | None = await service._connection(conn, connection_id)
    except TelaError:
        connection = None
    return _Prepared(pack=pack, manifest=manifest, catalog=catalog, result=result, mapping=chosen, model=model,
                     binding=binding, plan=plan(base, binding, manifest, set(keep_mine)), conflicts=shared,
                     connection=connection)


def _trial_snapshot(p: _Prepared, definition: dict[str, Any]) -> Any:
    """A snapshot of a definition about to be bound, typed from the catalogue (None when it is not valid)."""
    assert p.connection is not None
    try:
        snap = build_snapshot({"links": [], "measures": [], "dimensions": [], "settings": {}, **definition},
                              version_id=new_id(), connection_id=str(p.connection["id"]), version_no=None,
                              engine=p.connection["engine"])
    except ValueError:
        return None
    stamp_source_types(snap, {c["qualified_name"].lower(): c["columns"] for c in p.catalog})
    return snap


async def _value_checks(service: QueryService, p: _Prepared, principal: Principal,
                        request_id: str | None) -> tuple[dict[str, list[dict[str, Any]] | None], list[dict[str, Any]]]:
    """US-199: the mapped columns' most frequent values for each status-like pack field, and a warning for each
    field whose rule values are not in its column."""
    expected = pack_values(p.manifest, rules_only=True)
    shown = pack_values(p.manifest)
    definition = profile_definition(p.manifest, p.result) if p.connection else None
    snap = _trial_snapshot(p, definition) if definition else None
    if snap is None or p.connection is None:
        return {}, []
    text_fields = {f"{e.key}.{f.key}" for e in snap.entities for f in e.fields if f.data_type == "text"}
    refs = sorted(r for r in shown if r in text_fields)
    ctx = QueryContext(surface="precheck", request_id=request_id, use_cache=False)
    profiles, _ = await service.trial(snapshot=snap, connection=p.connection, principal=principal, ctx=ctx,
                                      fields=refs, measures=False, top=guards.TOP_VALUES)
    columns = {f"{e.key}.{f.key}": (f.label, f.source_column) for e in snap.entities for f in e.fields}
    return profiles, guards.value_warnings(expected, profiles, columns, p.result.values)


async def _suggest(service: QueryService, p: _Prepared, principal: Principal, request_id: str | None,
                   profiles: dict[str, list[dict[str, Any]] | None]) -> dict[str, dict[str, Any]]:
    """US-200: columns suggested by their values for the fields the names left unmapped or whose values don't fit.
    Reads the candidate columns only when such a field exists (a source that maps by name reads nothing more)."""
    wanted = wanted_suggestions(p.manifest, p.result, p.mapping, profiles)
    if not wanted or p.connection is None:
        return {}
    definition, refs = candidate_definition(p.manifest, p.result,
                                            candidate_columns(p.manifest, p.catalog, p.result, wanted))
    snap = _trial_snapshot(p, definition) if definition else None
    if snap is None:
        return {}
    ctx = QueryContext(surface="precheck", request_id=request_id, use_cache=False)
    read, _ = await service.trial(snapshot=snap, connection=p.connection, principal=principal, ctx=ctx,
                                  fields=sorted(refs), measures=False, top=guards.TOP_VALUES)
    return suggestions(p.manifest, p.result, p.mapping, wanted, {refs[r]: v for r, v in read.items()})


@router.post("/{pack_id}/preview")
async def preview_pack(pack_id: str, body: PreviewIn, request: Request,
                       principal: Principal = Depends(require(Perm.MODEL_APPROVE))) -> dict[str, Any]:
    service: QueryService = request.app.state.query_service
    async with tenant_tx(principal.tenant_id) as conn:
        p = await _prepare(conn, service, pack_id, body.connection_id, None, [], body.mapping, require_match=False)
        refs = await _source_refs(conn, p.model)
        ai_available = await _ai_available(conn, request)
    rid = getattr(request.state, "request_id", None)
    profiles, warnings = await _value_checks(service, p, principal, rid)
    suggested = await _suggest(service, p, principal, rid, profiles)
    model = p.model
    return {"pack_id": str(p.pack["id"]), "pack": p.pack["pack_key"], "version": p.pack["pack_version"],
            "confidence": round(p.result.confidence, 3), "matched": p.result.matched,
            "model_exists": model is not None,
            "installed_pack_id": str(model["installed_pack_id"]) if model and model["installed_pack_id"] else None,
            "mapping": p.mapping.model_dump(),
            "mapping_step": step(p.manifest, p.catalog, p.result, p.mapping, refs, profiles, suggested),
            "checks": {"blocking": p.conflicts, "warnings": warnings, "values_read": bool(profiles),
                       "suggestions": len(suggested), "ai_available": ai_available},
            **p.plan.out()}


@router.post("/{pack_id}/check")
async def check_pack(pack_id: str, body: PreviewIn, request: Request,
                     principal: Principal = Depends(require(Perm.MODEL_APPROVE))) -> dict[str, Any]:
    """Measures after mapping (US-199): a trial figure of each bound pack measure on the real data, and the ones that
    return 0 or the same figure as another measure with a different rule. Nothing is saved."""
    service: QueryService = request.app.state.query_service
    rid = getattr(request.state, "request_id", None)
    async with tenant_tx(principal.tenant_id) as conn:
        p = await _prepare(conn, service, pack_id, body.connection_id, None, [], body.mapping, require_match=False)
    if p.conflicts:
        raise TelaError(ErrorCode.VALIDATION_ERROR, "Two pack fields read one column. Fix the mapping first.",
                        {"issues": p.conflicts})
    if p.connection is None:
        raise TelaError(ErrorCode.CONNECTION_NOT_READY, "This data source can't be queried right now.")
    snap = _trial_snapshot(p, p.binding.definition) if p.binding.definition["entities"] else None
    if snap is None:
        raise TelaError(ErrorCode.PACK_NOT_MATCHED, "Map the pack's required fields first.")
    _, figures = await service.trial(snapshot=snap, connection=p.connection, principal=principal,
                                     ctx=QueryContext(surface="precheck", request_id=rid, use_cache=False),
                                     fields=[], measures=True)
    measures = p.binding.definition["measures"]
    warnings = guards.measure_warnings(measures, figures)
    async with tenant_tx(principal.tenant_id) as conn:
        await audit.record(conn, tenant_id=principal.tenant_id, action="starter_pack.checked",
                           object_type="connection", object_id=body.connection_id, outcome="success",
                           actor_id=principal.user_id, correlation_id=rid,
                           details={"pack": p.pack["pack_key"], "version": p.pack["pack_version"],
                                    "measures": len(measures), "warnings": len(warnings)})
    return {"measures": [{"key": m["key"], "label": m["label"], "value": figures.get(m["key"])} for m in measures],
            "warnings": warnings}


async def _ai_available(conn: Any, request: Request) -> bool:
    """Whether a model is set up for AG-04: a gateway and a provider (the tenant's, else the deployment's)."""
    gateway: GatewayClient | None = getattr(request.app.state, "gateway", None)
    return gateway is not None and await planner_provider(conn, gateway) is not None


@router.post("/{pack_id}/ai-suggestions")
async def ai_suggestions(pack_id: str, body: PreviewIn, request: Request,
                         principal: Principal = Depends(require(Perm.MODEL_APPROVE))) -> dict[str, Any]:
    """US-201: the tenant's model suggests a column, with a reason, for each field names and values leave open; one
    call per pack table; flagged where US-199's guards would refuse it. Nothing is saved or applied."""
    async with trace.agent_run(principal.tenant_id, principal.user_id, "pack_mapping", title=f"Map {pack_id}",
                               object_ref=f"connection:{body.connection_id}", agent="AG-04"):
        return await _ai_suggestions(pack_id, body, request, principal)


async def _ai_suggestions(pack_id: str, body: PreviewIn, request: Request, principal: Principal) -> dict[str, Any]:
    service: QueryService = request.app.state.query_service
    gateway: GatewayClient | None = getattr(request.app.state, "gateway", None)
    rid = getattr(request.state, "request_id", None)
    async with tenant_tx(principal.tenant_id) as conn:
        p = await _prepare(conn, service, pack_id, body.connection_id, None, [], body.mapping, require_match=False)
        available = await _ai_available(conn, request)
    if not available:
        return {"available": False, "message": "No AI model is set up. Map the fields by name and values, or set "
                                               "up a model under AI settings.", "suggestions": {}, "tables": []}
    profiles, _ = await _value_checks(service, p, principal, rid)
    value_suggested = await _suggest(service, p, principal, rid, profiles)
    value_wanted = wanted_suggestions(p.manifest, p.result, p.mapping, profiles)
    asked = ai_suggest.wanted(p.manifest, p.result, value_wanted, value_suggested)
    offered = {e: ai_suggest.offered(p.catalog, p.result, e, fields) for e, fields in asked.items()}
    samples: dict[str, dict[str, list[dict[str, Any]] | None]] = {e: {} for e in asked}
    readable: dict[str, list[str]] = {}  # text columns to sample, on tables whose key is mapped
    for e, cols in offered.items():
        pent = p.manifest.entity(e)
        names = ai_suggest.sampled(cols)
        if pent and names and all(k in p.result.entities[e].mapping for k in pent.primary_key):
            readable[e] = names
    definition, refs = candidate_definition(p.manifest, p.result, readable)
    snap = _trial_snapshot(p, definition) if definition and p.connection else None
    if snap is not None and p.connection is not None:
        read, _ = await service.trial(snapshot=snap, connection=p.connection, principal=principal,
                                      ctx=QueryContext(surface="precheck", request_id=rid, use_cache=False),
                                      fields=sorted(refs), measures=False, top=guards.TOP_VALUES)
        for r, v in read.items():
            ent_key, col = refs[r]
            samples[ent_key][col] = v
    out: dict[str, dict[str, Any]] = {}
    tables: list[dict[str, Any]] = []
    dropped = 0
    for ent_key, fields in asked.items():
        em = p.result.entities[ent_key]
        user = ai_suggest.prompt(p.manifest, ent_key, fields, offered[ent_key], samples[ent_key], em.mapping,
                                 value_suggested)
        try:
            reply = await run_agent(gateway, ai_suggest.AiMapReply, tenant_id=principal.tenant_id,
                                    user_id=principal.user_id, agent=ai_suggest.AGENT, task=ai_suggest.TASK,
                                    system=ai_suggest.SYSTEM, user=user)
        except AgentFailed as exc:
            tables.append({"entity": ent_key, "asked": len(fields), "status": "failed", "code": exc.code})
            continue
        got, n = ai_suggest.accept(p.manifest, p.catalog, p.result, ent_key, fields, reply, samples[ent_key])
        await trace.step("validate", f"{ent_key}: {len(got)} suggestion(s) kept, {n} dropped", agent="AG-04",
                         detail={"entity": ent_key, "asked": len(fields), "kept": len(got), "dropped": n})
        out.update(got)
        dropped += n
        tables.append({"entity": ent_key, "asked": len(fields), "status": "ok", "suggested": len(got)})
    async with tenant_tx(principal.tenant_id) as conn:
        await audit.record(conn, tenant_id=principal.tenant_id, action="starter_pack.ai_suggested",
                           object_type="connection", object_id=body.connection_id, outcome="success",
                           actor_id=principal.user_id, correlation_id=rid,
                           details={"pack": p.pack["pack_key"], "asked": sum(len(f) for f in asked.values()),
                                    "suggested": sum(1 for s in out.values() if s["column"]),
                                    "flagged": sum(1 for s in out.values() if s["flags"]), "dropped": dropped,
                                    "failed": [t["entity"] for t in tables if t["status"] == "failed"]})
    return {"available": True, "message": None, "suggestions": out, "tables": tables, "dropped": dropped}


@router.post("/{pack_id}/install", status_code=202)
async def install_pack(pack_id: str, body: InstallIn, request: Request,
                       principal: Principal = Depends(require(Perm.MODEL_APPROVE))) -> dict[str, Any]:
    async with tenant_tx(principal.tenant_id) as conn:
        p = await _prepare(conn, request.app.state.query_service, pack_id, body.connection_id, body.reporting_date,
                           body.keep_mine, body.mapping, require_match=True)
        pack, result, model, binding, apply_plan = p.pack, p.result, p.model, p.binding, p.plan
        await conn.execute(
            text("""
                INSERT INTO model.pack_field_mappings (tenant_id, connection_id, pack_key, mapping, resolved,
                  updated_by)
                VALUES (:t, CAST(:c AS uuid), :k, CAST(:m AS jsonb), CAST(:r AS jsonb), :by)
                ON CONFLICT (tenant_id, connection_id, pack_key) DO UPDATE SET mapping = EXCLUDED.mapping,
                  resolved = EXCLUDED.resolved, updated_by = EXCLUDED.updated_by, updated_at = now()
            """),
            {"t": principal.tenant_id, "c": body.connection_id, "k": pack["pack_key"],
             "m": json.dumps(p.mapping.model_dump()),
             "r": json.dumps({"entities": {k: e.object for k, e in result.entities.items() if e.object},
                              "fields": {f"{k}.{fk}": col for k, e in result.entities.items()
                                         for fk, col in e.mapping.items()}}),
             "by": principal.user_id})
        if model is None:
            model_id, draft_id = new_id(), new_id()
            await conn.execute(
                text("INSERT INTO model.data_models (id, tenant_id, connection_id, draft_version_id, "
                     "installed_pack_id, created_by) VALUES (:id, :t, CAST(:c AS uuid), :d, :p, :by)"),
                {"id": model_id, "t": principal.tenant_id, "c": body.connection_id, "d": draft_id, "p": pack["id"],
                 "by": principal.user_id},
            )
            await conn.execute(
                text("INSERT INTO model.model_versions (id, tenant_id, data_model_id, status, definition, created_by) "
                     "VALUES (:id, :t, :m, 'draft', CAST(:def AS jsonb), :by)"),
                {"id": draft_id, "t": principal.tenant_id, "m": model_id, "def": json.dumps(binding.definition),
                 "by": principal.user_id},
            )
            items = binding.items
        else:
            # Merge into the Admin's model (US-153): their items and open questions stay.
            model_id = str(model["id"])
            draft = await ensure_draft(conn, model, principal)
            draft_id = str(draft["id"])
            snap, issues = _snapshot_or_issues(apply_plan.definition, model=model, version_id=draft_id)
            if snap is None:
                raise TelaError(ErrorCode.MODEL_INVALID, "The merged model has problems to fix first.",
                                {"issues": issues})
            stored = snap.model_dump(mode="json", exclude={"model_version_id", "connection_id", "version_no",
                                                           "engine"}, by_alias=True)
            await conn.execute(
                text("UPDATE model.model_versions SET definition = CAST(:d AS jsonb), "
                     "draft_revision = draft_revision + 1 WHERE id = :id"),
                {"d": json.dumps(stored), "id": draft["id"]})
            await conn.execute(text("UPDATE model.data_models SET installed_pack_id = :p WHERE id = :id"),
                               {"p": pack["id"], "id": model_id})
            items = apply_plan.items
        asked = await fetch_one(conn, "SELECT 1 FROM model.model_questions WHERE model_version_id = :v AND "
                                      "status = 'open' AND question LIKE 'Date-relative PMO rules%'", {"v": draft_id})
        if not body.reporting_date and not asked and not apply_plan.definition["settings"].get("reporting_date"):
            await insert_questions(conn, principal.tenant_id, draft_id, [{
                "question": "Date-relative PMO rules use today's date. Set a fixed reporting date instead?",
                "options": ["Use today", "Set a fixed date"], "blocking": False}], principal.user_id)
        for item in items:
            await conn.execute(
                text("""
                    INSERT INTO model.starter_pack_items (id, tenant_id, starter_pack_id, connection_id, item_kind,
                      item_key, title, availability, gaps, created_by)
                    VALUES (:id, :t, :p, CAST(:c AS uuid), :k, :key, :title, :a, CAST(:g AS jsonb), :by)
                    ON CONFLICT (tenant_id, connection_id, starter_pack_id, item_kind, item_key) DO UPDATE SET
                      title = EXCLUDED.title, availability = EXCLUDED.availability, gaps = EXCLUDED.gaps
                """),
                {"id": new_id(), "t": principal.tenant_id, "p": pack["id"], "c": body.connection_id,
                 "k": item["kind"], "key": item["key"], "title": item["title"], "a": item["availability"],
                 "g": json.dumps(item["gaps"]), "by": principal.user_id},
            )
        await audit.record(
            conn, tenant_id=principal.tenant_id, action="starter_pack.installed", object_type="data_model",
            object_id=model_id, outcome="success", actor_id=principal.user_id,
            correlation_id=getattr(request.state, "request_id", None),
            details={"pack": pack["pack_key"], "version": pack["pack_version"],
                     "confidence": round(result.confidence, 3),
                     "unavailable": sum(1 for i in items if i["availability"] == "unavailable"),
                     "mode": "new" if model is None else "merge",
                     "mapped": len(p.mapping.fields) + len(p.mapping.entities), "value_maps": len(p.mapping.values),
                     "clashes": [{"ref": c["ref"], "choice": c["choice"]} for c in apply_plan.clashes]},
        )
    # The pack's context documents join this connection's knowledge, waiting for review (US-233).
    from tela_api.packs.studio.attachments import copy_to_connection  # the Studio imports this module

    documents = await copy_to_connection(request, principal, str(pack["id"]), body.connection_id)
    unavailable = [i for i in items if i["availability"] == "unavailable"]
    return {
        "model_id": model_id, "draft_version_id": draft_id, "pack": pack["pack_key"], "version": pack["pack_version"],
        "confidence": round(result.confidence, 3),
        "mode": "new" if model is None else "merge",
        "installed": {"entities": len(binding.definition["entities"]), "measures": len(binding.definition["measures"]),
                      "dimensions": len(binding.definition["dimensions"]),
                      "reports": sum(1 for i in items if i["kind"] == "report" and i["availability"] == "available")},
        "added": apply_plan.added if model is not None else None,
        "clashes": apply_plan.clashes if model is not None else [],
        "unavailable": unavailable,
        "documents": documents,
        "next_step": "Review the draft model and approve it. Nothing is live until approval.",
    }


@router.get("/items")
async def list_items(connection_id: str, principal: Principal = Depends(require(Perm.MODEL_EDIT))) -> dict[str, Any]:
    async with tenant_tx(principal.tenant_id) as conn:
        rows = await fetch_all(
            conn, "SELECT item_kind, item_key, title, availability, gaps, state FROM model.starter_pack_items "
                  "WHERE connection_id = CAST(:c AS uuid) ORDER BY item_kind, item_key", {"c": connection_id})
    return {"items": rows}
