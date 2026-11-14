"""Starter pack updates (API-048; FEAT-047; US-120; REQ-113; RULE-015, RULE-017).

* ``GET /admin/packs/updates`` — every model whose installed pack has a newer version in the catalogue, or one
  applied and waiting for model approval: what changes (reports, measures, dimensions, fields added, changed or
  removed), whether the model needs a draft, the dashboards that will get a new draft, and the user copies (never
  touched).
* ``POST /admin/packs/{pack_id}/update`` ``{"connection_id"}`` — applies that version to the connection's model.
  Changes to measures, dimensions, fields and links are merged into the model **draft**: what the pack changed is
  replaced, what it dropped is removed, what it added is added, and the model's own items (and edits to items the
  pack did not change) stay. The version then waits (``pending_pack_id``) until the draft is approved; approval
  switches the model to it and rebuilds the starter reports. With nothing for the model to approve, the switch
  happens at once. Only pack-owned starter reports are rebuilt, in place: dashboards that include one get it in
  their draft while their live version stays, and the owners are told. A report the new version drops leaves those
  drafts like a deleted starter. User copies are separate reports and are never touched.
* ``POST /internal/packs/update-check`` — the daily ``pack_update_check`` job: tells the Admins about each newer
  version once per model, with a change summary.
"""

from __future__ import annotations

import copy
import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from tela_api.audit import service as audit
from tela_api.content.common import notify, users_with_permission
from tela_api.content.exports import require_worker
from tela_api.content.starters import (
    build_starter_reports,
    dashboards_including,
    drop_from_dashboards,
    install_starter_dashboards,
    touch_dashboards,
)
from tela_api.core.db import fetch_all, fetch_one, tenant_tx
from tela_api.identity.deps import require
from tela_api.identity.permissions import Perm
from tela_api.identity.principal import Principal
from tela_api.model.router import ensure_draft
from tela_api.packs.manifest import Binding, PackManifest, bind, match
from tela_api.packs.mapping import PackMapping
from tela_api.packs.router import _catalog, saved_mapping
from tela_common.errors import ErrorCode, TelaError
from tela_common.ids import new_id

router = APIRouter(prefix="/api/v1/admin/packs", tags=["starter-packs"])
internal = APIRouter(prefix="/api/v1/internal/packs", tags=["internal"], include_in_schema=False)

_MODELS = """
    SELECT m.id, m.connection_id, m.current_version_id, m.draft_version_id, m.installed_pack_id, m.pending_pack_id,
      m.pack_update_notified_id, c.name AS connection_name, p.pack_key, p.pack_version, p.title, p.manifest
    FROM model.data_models m
    JOIN model.starter_packs p ON p.id = m.installed_pack_id
    JOIN conn.connections c ON c.id = m.connection_id AND c.deleted_at IS NULL
"""
_PACK = "SELECT id, pack_key, pack_version, title, released_at, manifest FROM model.starter_packs"


def version_key(version: str) -> tuple[int, ...]:
    return tuple(int(p) for p in version.split("."))


def _same(a: Any, b: Any) -> bool:
    return json.dumps(a, sort_keys=True, default=str) == json.dumps(b, sort_keys=True, default=str)


def _key(item: dict[str, Any]) -> str:
    return str(item["key"])


def _link_key(link: dict[str, Any]) -> str:
    return f"{link['from_entity']}>{link['to_entity']}:{','.join(link.get('from_fields', []))}"


# --------------------------------------------------------------------------- three-way merge (pure)


def merge_keyed(base: list[dict[str, Any]], old: list[dict[str, Any]], new: list[dict[str, Any]],
                key: Callable[[dict[str, Any]], str] = _key) -> list[dict[str, Any]]:
    """Apply the pack's change from ``old`` to ``new`` onto ``base`` (the model), item by item.

    What the pack added or changed takes the pack's form; what it dropped is removed; everything else keeps the
    model's own form, so the Admin's items and edits to items the pack did not change survive. An item the Admin
    removed stays removed unless the pack changed it.
    """
    om, nm = {key(i): i for i in old}, {key(i): i for i in new}
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in base:
        k = key(item)
        seen.add(k)
        if k in nm and (k not in om or not _same(om[k], nm[k])):
            out.append(nm[k])
        elif not (k in om and k not in nm):
            out.append(item)
    out += [item for k, item in nm.items() if k not in seen and (k not in om or not _same(om[k], item))]
    return out


def merge_definition(base: dict[str, Any], old: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
    """The model definition with the pack's change from ``old`` to ``new`` (both bindings) applied."""
    out = copy.deepcopy(base)
    out["measures"] = merge_keyed(base.get("measures", []), old["measures"], new["measures"])
    out["dimensions"] = merge_keyed(base.get("dimensions", []), old["dimensions"], new["dimensions"])
    out["links"] = merge_keyed(base.get("links", []), old["links"], new["links"], _link_key)
    oe, ne = {e["key"]: e for e in old["entities"]}, {e["key"]: e for e in new["entities"]}
    entities: list[dict[str, Any]] = []
    for ent in base.get("entities", []):
        k = ent["key"]
        if k in oe and k not in ne:
            continue
        if k not in ne:
            entities.append(ent)
            continue
        o, n = oe.get(k, {"fields": []}), ne[k]
        merged = {**ent, **{a: v for a, v in n.items() if a != "fields" and not _same(o.get(a), v)}}
        merged["fields"] = merge_keyed(ent.get("fields", []), o["fields"], n["fields"])
        entities.append(merged)
    have = {e["key"] for e in base.get("entities", [])}
    entities += [n for k, n in ne.items() if k not in have and (k not in oe or not _same(oe[k], n))]
    out["entities"] = entities
    return out


# --------------------------------------------------------------------------- what changes (pure)


def item_changes(old: dict[str, Any], new: dict[str, Any]) -> list[dict[str, Any]]:
    """Measures, dimensions, entities and fields the pack adds, changes or removes (between two bindings)."""
    out: list[dict[str, Any]] = []

    def compare(kind: str, before: dict[str, dict[str, Any]], after: dict[str, dict[str, Any]],
                title: Callable[[dict[str, Any]], str]) -> None:
        for k in sorted(before.keys() | after.keys()):
            change = ("added" if k not in before else "removed" if k not in after
                      else None if _same(before[k], after[k]) else "changed")
            if change:
                out.append({"kind": kind, "key": k, "title": title(after.get(k) or before[k]), "change": change})

    for kind in ("measure", "dimension"):
        compare(kind, {i["key"]: i for i in old[f"{kind}s"]}, {i["key"]: i for i in new[f"{kind}s"]},
                lambda i: str(i["label"]))
    oe, ne = {e["key"]: e for e in old["entities"]}, {e["key"]: e for e in new["entities"]}
    compare("entity", {k: {"label": e["label"]} for k, e in oe.items()},
            {k: {"label": e["label"]} for k, e in ne.items()}, lambda e: str(e["label"]))

    def fields(entities: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
        return {f"{k}.{f['key']}": {**f, "_entity": e["label"]} for k, e in entities.items() for f in e["fields"]
                if k in oe and k in ne}

    compare("field", fields(oe), fields(ne), lambda f: f"{f['_entity']}: {f['label']}")
    return out


def _report_refs(report: dict[str, Any]) -> set[str]:
    refs: set[str] = set()
    for w in report["widgets"]:
        refs |= {f"measure:{m}" for m in [w["measure"], *w.get("extra_measures", [])]}
        refs |= {f"dimension:{d}" for d in [*w.get("breakdowns", []), *(f["ref"] for f in w.get("filters", []))]}
        if w.get("time"):
            refs.add(f"dimension:{w['time']['ref']}")
    return refs


def report_changes(old_m: PackManifest, new_m: PackManifest, old_b: Binding, new_b: Binding,
                   items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Starter reports the update adds, rebuilds or drops (on this connection: only available ones count)."""
    def ok(b: Binding) -> set[str]:
        return {i["key"] for i in b.items if i["kind"] == "report" and i["availability"] == "available"}

    before, after = ok(old_b), ok(new_b)
    touched = {f"{c['kind']}:{c['key']}" for c in items if c["kind"] in ("measure", "dimension")}
    out = []
    for key in sorted(before | after):
        rep = new_m.reports.get(key) or old_m.reports[key]
        if key not in before:
            change = "added"
        elif key not in after:
            change = "removed"
        elif not _same(old_m.reports[key], new_m.reports[key]) or _report_refs(new_m.reports[key]) & touched:
            change = "changed"
        else:
            continue
        out.append({"kind": "report", "key": key, "title": rep["title"], "change": change})
    return out


def summarise(changes: list[dict[str, Any]]) -> str:
    def count(kind: str, change: str) -> int:
        return sum(1 for c in changes if c["kind"] == kind and c["change"] == change)

    parts = []
    for kind, label in (("report", "starter report"), ("measure", "measure"), ("dimension", "dimension"),
                        ("field", "field")):
        bits = [f"{n} {c}" for c in ("added", "changed", "removed") if (n := count(kind, c))]
        if bits:
            parts.append(f"{label}s: {', '.join(bits)}")
    return ("; ".join(parts) + ".") if parts else "No changes for this connection."


# --------------------------------------------------------------------------- plan


@dataclass
class Plan:
    model: dict[str, Any]
    target: dict[str, Any]
    changes: list[dict[str, Any]]
    merged: dict[str, Any] | None  # the model definition with the pack's changes; None when there is no model yet
    model_change: bool


async def _pack(conn: AsyncConnection, pack_id: str) -> dict[str, Any] | None:
    try:
        return await fetch_one(conn, _PACK + " WHERE id = CAST(:id AS uuid)", {"id": pack_id})
    except Exception:  # noqa: BLE001 - a malformed id is just "not found"
        return None


async def newest(conn: AsyncConnection, pack_key: str) -> dict[str, Any] | None:
    rows = await fetch_all(conn, _PACK + " WHERE pack_key = :k AND status = 'released'", {"k": pack_key})
    return max(rows, key=lambda r: version_key(r["pack_version"]), default=None)


def _bindings(catalog: list[dict[str, Any]], *manifests: PackManifest, mapping: PackMapping | None = None
              ) -> list[Binding]:
    return [bind(m, match(m, catalog, mapping=mapping)) for m in manifests]


async def plan_update(conn: AsyncConnection, model: dict[str, Any], target: dict[str, Any]) -> Plan:
    """What applying ``target`` to this model changes. Merges onto the draft (or the approved version)."""
    catalog = await _catalog(conn, str(model["connection_id"]))
    installed = PackManifest.model_validate(model["manifest"])
    new_m = PackManifest.model_validate(target["manifest"])
    # A version already waiting in the draft is the starting point of the merge, not the installed one.
    pending = await _pack(conn, str(model["pending_pack_id"])) if model["pending_pack_id"] else None
    start = PackManifest.model_validate(pending["manifest"]) if pending else installed
    # The names the install bound and the Admin's mapping (US-154), so every version binds the same columns.
    mapping = await saved_mapping(conn, str(model["connection_id"]), installed.pack_key, resolved=True)
    old_b, start_b, new_b = _bindings(catalog, installed, start, new_m, mapping=mapping)
    items = item_changes(old_b.definition, new_b.definition)
    changes = report_changes(installed, new_m, old_b, new_b, items) + items
    vid = model["draft_version_id"] or model["current_version_id"]
    base = await fetch_one(conn, "SELECT definition FROM model.model_versions WHERE id = :id", {"id": vid}) \
        if vid else None
    merged = merge_definition(base["definition"], start_b.definition, new_b.definition) if base else None
    model_change = (base is None or model["current_version_id"] is None
                    or not _same(base["definition"], merged) or pending is not None)
    return Plan(model, target, changes, merged, model_change)


async def _effects(conn: AsyncConnection, connection_id: str, installed_pack_id: str,
                   changes: list[dict[str, Any]]) -> dict[str, Any]:
    items = {r["item_key"]: r for r in await fetch_all(
        conn, "SELECT item_key, title, content_id FROM model.starter_pack_items WHERE connection_id = :c AND "
              "starter_pack_id = :p AND item_kind = 'report' AND state = 'active' AND content_id IS NOT NULL",
        {"c": connection_id, "p": installed_pack_id})}
    copies = await fetch_one(
        conn, "SELECT count(*) AS n FROM content.reports WHERE deleted_at IS NULL AND copied_from_id = ANY("
              "CAST(:ids AS uuid[]))", {"ids": [str(i["content_id"]) for i in items.values()]})
    dashboards = []
    for c in changes:
        if c["kind"] == "report" and c["change"] in ("changed", "removed") and c["key"] in items:
            for d in await dashboards_including(conn, str(items[c["key"]]["content_id"])):
                dashboards.append({"dashboard_id": str(d["id"]), "title": d["title"], "owner": d["owner_name"],
                                   "live_version_stays": d["live_version"], "report": c["title"],
                                   "change": c["change"]})
    return {"user_copies_affected": 0, "user_copies_unaffected": copies["n"] if copies else 0,
            "dashboards_getting_new_draft": dashboards}


# --------------------------------------------------------------------------- switch (apply or approval)


async def switch_pack(conn: AsyncConnection, *, tenant_id: str, model_id: str, to_pack_id: str, by: str,
                      version_id: str | None, request_id: str | None = None) -> dict[str, Any]:
    """Move a model's starter content to a newer pack version (the caller holds the model row lock).

    Carries each starter item's Admin settings (deleted, hidden from roles, its report) over to the new version,
    retires reports the version drops and rebuilds changed or new ones at ``version_id``; with ``version_id`` None
    (model approval) the approval rebuilds every starter report itself.
    """
    model = await fetch_one(conn, _MODELS + " WHERE m.id = :id", {"id": model_id})
    target = await _pack(conn, to_pack_id)
    assert model is not None and target is not None
    cid = str(model["connection_id"])
    installed, new_m = PackManifest.model_validate(model["manifest"]), PackManifest.model_validate(target["manifest"])
    mapping = await saved_mapping(conn, cid, installed.pack_key, resolved=True)
    old_b, new_b = _bindings(await _catalog(conn, cid), installed, new_m, mapping=mapping)
    changes = report_changes(installed, new_m, old_b, new_b, item_changes(old_b.definition, new_b.definition))
    previous = {(r["item_kind"], r["item_key"]): r for r in await fetch_all(
        conn, "SELECT id, item_kind, item_key, title, content_id, state, hidden_from_role_ids, copy_count FROM "
              "model.starter_pack_items WHERE connection_id = :c AND starter_pack_id = :p",
        {"c": cid, "p": model["installed_pack_id"]})}
    for item in new_b.items:
        prev = previous.get((item["kind"], item["key"]), {})
        await conn.execute(
            text("""
                INSERT INTO model.starter_pack_items (id, tenant_id, starter_pack_id, connection_id, item_kind,
                  item_key, title, availability, gaps, content_id, state, hidden_from_role_ids, copy_count, created_by)
                VALUES (:id, :t, :p, CAST(:c AS uuid), :k, :key, :title, :a, CAST(:g AS jsonb), :content, :s,
                  CAST(:hidden AS uuid[]), :copies, :by)
                ON CONFLICT (tenant_id, connection_id, starter_pack_id, item_kind, item_key) DO UPDATE SET
                  title = EXCLUDED.title, availability = EXCLUDED.availability, gaps = EXCLUDED.gaps,
                  content_id = EXCLUDED.content_id, state = EXCLUDED.state,
                  hidden_from_role_ids = EXCLUDED.hidden_from_role_ids, copy_count = EXCLUDED.copy_count,
                  updated_at = now()
            """),
            {"id": new_id(), "t": tenant_id, "p": target["id"], "c": cid, "k": item["kind"], "key": item["key"],
             "title": item["title"], "a": item["availability"], "g": json.dumps(item["gaps"]),
             "content": prev.get("content_id"), "s": prev.get("state", "active"),
             "hidden": [str(r) for r in prev.get("hidden_from_role_ids") or []], "copies": prev.get("copy_count", 0),
             "by": by})
    await conn.execute(text("UPDATE model.data_models SET installed_pack_id = :p, pending_pack_id = NULL, "
                            "pack_update_notified_id = :p WHERE id = :id"), {"p": target["id"], "id": model_id})

    notified = 0
    touched: list[str] = []
    for c in changes:
        was = previous.get(("report", c["key"]))
        if c["change"] == "added" or not was or not was["content_id"] or was["state"] != "active":
            continue
        rid = str(was["content_id"])
        dashboards = await dashboards_including(conn, rid)
        ids = [str(d["id"]) for d in dashboards]
        removed = c["change"] == "removed"
        if removed:
            # The new version no longer has it (or it lost a field here): retire it like a deleted starter.
            await conn.execute(text("UPDATE content.reports SET deleted_at = now() WHERE id = :r"), {"r": rid})
            await conn.execute(text("UPDATE model.starter_pack_items SET state = 'deleted' WHERE connection_id = :c "
                                    "AND starter_pack_id = :p AND item_kind = 'report' AND item_key = :k"),
                               {"c": cid, "p": target["id"], "k": c["key"]})
            await drop_from_dashboards(conn, rid, ids)
            body = ("The new pack version drops it, so your draft no longer includes it. The live version "
                    "stays as it is until you republish.")
        else:
            touched += ids
            body = "Your draft now shows the new version. The live version stays as it is until you republish."
        owners: dict[str, list[dict[str, Any]]] = {}
        for d in dashboards:
            owners.setdefault(str(d["owner_id"]), []).append(d)
        for owner, mine in owners.items():
            if owner == by:
                continue
            word = "removed" if removed else "updated"
            notified += await notify(
                conn, tenant_id, [owner], kind=f"starter.{word}",
                title=f"Starter report {c['title']} was {word} in {', '.join(d['title'] for d in mine)}"[:200],
                body=body, link=f"/dashboards/{mine[0]['id']}/edit" if len(mine) == 1 else "/dashboards", by=by)
    await touch_dashboards(conn, sorted(set(touched)))

    rebuilt: dict[str, str] = {}
    if version_id is not None:
        keys = {c["key"] for c in changes if c["change"] in ("added", "changed")}
        if keys:
            rebuilt = await build_starter_reports(conn, tenant_id=tenant_id, connection_id=cid,
                                                  version_id=version_id, by=by, keys=keys)
        await install_starter_dashboards(conn, tenant_id=tenant_id, connection_id=cid, version_id=version_id, by=by)
    result = {"from": model["pack_version"], "to": target["pack_version"], "reports": changes,
              "rebuilt": len(rebuilt), "owners_notified": notified}
    await audit.record(conn, tenant_id=tenant_id, action="starter_pack.updated", object_type="data_model",
                       object_id=model_id, outcome="success", actor_id=by, correlation_id=request_id,
                       details={"pack": model["pack_key"], "from": model["pack_version"], "to": target["pack_version"],
                                **{k: sum(1 for c in changes if c["change"] == k)
                                   for k in ("added", "changed", "removed")}, "owners_notified": notified})
    return result


# --------------------------------------------------------------------------- API-048


def _rid(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


@router.get("/updates")
async def list_updates(principal: Principal = Depends(require(Perm.STARTER_MANAGE))) -> dict[str, Any]:
    out = []
    async with tenant_tx(principal.tenant_id) as conn:
        for model in await fetch_all(conn, _MODELS + " ORDER BY c.name"):
            target = await newest(conn, model["pack_key"])
            pending = await _pack(conn, str(model["pending_pack_id"])) if model["pending_pack_id"] else None
            if target is None or version_key(target["pack_version"]) <= version_key(model["pack_version"]):
                continue
            awaiting = pending is not None and pending["id"] == target["id"]
            entry: dict[str, Any] = {
                "pack_id": str(target["id"]), "pack_key": target["pack_key"], "title": target["title"],
                "installed": model["pack_version"], "available": target["pack_version"],
                "released_at": target["released_at"], "model_id": str(model["id"]),
                "connection": {"id": str(model["connection_id"]), "name": model["connection_name"]},
                "status": "awaiting_approval" if awaiting else "available",
                "pending": {"version": pending["pack_version"], "draft_version_id": str(model["draft_version_id"])}
                if pending else None,
            }
            try:
                plan = await plan_update(conn, model, target)
            except TelaError as exc:
                out.append({**entry, "changes": [], "summary": exc.message, "model_draft_required": None,
                            "user_copies_affected": 0, "user_copies_unaffected": 0,
                            "dashboards_getting_new_draft": [], "error": exc.message})
                continue
            out.append({**entry, "changes": plan.changes, "summary": summarise(plan.changes),
                        "model_draft_required": plan.model_change,
                        **await _effects(conn, str(model["connection_id"]), str(model["installed_pack_id"]),
                                         plan.changes), "error": None})
    return {"items": out}


class UpdateIn(BaseModel):
    connection_id: str = Field(max_length=64)


@router.post("/{pack_id}/update")
async def apply_update(pack_id: str, body: UpdateIn, request: Request,
                       principal: Principal = Depends(require(Perm.STARTER_MANAGE))) -> dict[str, Any]:
    async with tenant_tx(principal.tenant_id) as conn:
        try:
            model = await fetch_one(conn, _MODELS + " WHERE m.connection_id = CAST(:c AS uuid) FOR UPDATE OF m",
                                    {"c": body.connection_id})
        except Exception:  # noqa: BLE001 - a malformed id is just "not found"
            model = None
        if model is None:
            raise TelaError(ErrorCode.NOT_FOUND, "That connection has no starter pack installed.")
        target = await _pack(conn, pack_id)
        if target is None or target["pack_key"] != model["pack_key"]:
            raise TelaError(ErrorCode.NOT_FOUND, "That pack version does not exist for this connection.")
        if version_key(target["pack_version"]) <= version_key(model["pack_version"]):
            raise TelaError(ErrorCode.CONFLICT, f"This connection already has version {model['pack_version']}.")
        if model["pending_pack_id"] is not None:
            pending = await _pack(conn, str(model["pending_pack_id"]))
            if pending and version_key(target["pack_version"]) <= version_key(pending["pack_version"]):
                raise TelaError(ErrorCode.CONFLICT, f"Version {pending['pack_version']} is already waiting in the "
                                                    "model draft. Approve the draft to finish the update.")
        plan = await plan_update(conn, model, target)
        effects = await _effects(conn, body.connection_id, str(model["installed_pack_id"]), plan.changes)
        if not plan.model_change:
            result = await switch_pack(conn, tenant_id=principal.tenant_id, model_id=str(model["id"]),
                                       to_pack_id=str(target["id"]), by=principal.user_id,
                                       version_id=str(model["current_version_id"]), request_id=_rid(request))
            return {"status": "applied", "summary": summarise(plan.changes), "changes": plan.changes, **effects,
                    **result}
        draft = await ensure_draft(conn, model, principal)
        await conn.execute(text("UPDATE model.model_versions SET definition = CAST(:d AS jsonb), "
                                "draft_revision = draft_revision + 1 WHERE id = :id"),
                           {"d": json.dumps(plan.merged), "id": draft["id"]})
        await conn.execute(text("UPDATE model.data_models SET pending_pack_id = :p, pack_update_notified_id = :p "
                                "WHERE id = :id"), {"p": target["id"], "id": model["id"]})
        await audit.record(conn, tenant_id=principal.tenant_id, action="starter_pack.update_staged",
                           object_type="data_model", object_id=str(model["id"]), outcome="success",
                           actor_id=principal.user_id, correlation_id=_rid(request),
                           details={"pack": model["pack_key"], "from": model["pack_version"],
                                    "to": target["pack_version"], "draft_version_id": str(draft["id"]),
                                    "changes": len(plan.changes)})
    return {"status": "awaiting_approval", "summary": summarise(plan.changes), "changes": plan.changes, **effects,
            "model_id": str(model["id"]), "draft_version_id": str(draft["id"]),
            "next_step": "Review and approve the model draft. Starter reports change when it is approved; "
                         "users' copies never do."}


# --------------------------------------------------------------------------- pack_update_check (daily)


@internal.post("/update-check")
async def update_check(_: dict[str, Any] = Depends(require_worker)) -> dict[str, int]:
    async with tenant_tx(None) as conn:
        tenants = [str(r["tenant_id"]) for r in await fetch_all(conn, "SELECT tenant_id FROM "
                                                                      "model.f_pack_update_tenants()")]
    models = notified = 0
    for tenant_id in tenants:
        async with tenant_tx(tenant_id) as conn:
            m, n = await notify_updates(conn, tenant_id)
        models += m
        notified += n
    return {"tenants": len(tenants), "models": models, "notified": notified}


async def notify_updates(conn: AsyncConnection, tenant_id: str, *, pack_key: str | None = None) -> tuple[int, int]:
    """Tell the tenant's Admins about each newer pack version once per model, with a change summary: (models,
    notifications). The daily check runs it for every tenant; a pack release (US-238) runs it for its own key."""
    admins = await users_with_permission(conn, Perm.STARTER_MANAGE)
    models = notified = 0
    for model in await fetch_all(conn, _MODELS + " ORDER BY c.name"):
        if pack_key is not None and model["pack_key"] != pack_key:
            continue
        target = await newest(conn, model["pack_key"])
        if (target is None or target["id"] == model["pack_update_notified_id"]
                or version_key(target["pack_version"]) <= version_key(model["pack_version"])):
            continue
        try:
            summary = summarise((await plan_update(conn, model, target)).changes)
        except TelaError:
            summary = "Read the connection's schema again to see what changes."
        notified += await notify(
            conn, tenant_id, admins, kind="pack.update_available",
            title=f"{target['title']} {target['pack_version']} is available for {model['connection_name']}",
            body=f"Installed: {model['pack_version']}. {summary} Updating changes starter reports only; "
                 "users' copies are never touched.", link="/admin/starter")
        await conn.execute(text("UPDATE model.data_models SET pack_update_notified_id = :p WHERE id = :id"),
                           {"p": target["id"], "id": model["id"]})
        models += 1
    return models, notified
