"""Personal fields: groupings, bands and cross-table measures (API-030, API-031; FEAT-028 to FEAT-030;
RULE-012 to RULE-014; US-074 to US-077).

* A personal field belongs to its Creator, is pinned to the approved model version it was built on and is
  applied at query level only (nothing is written to the source). Its badge is Personal until certified.
* Groupings: named groups of values, a renamable catch-all and "Not set" for blank values; matching ignores case
  and surrounding spaces; a value in two groups is rejected (RULE-012).
* Bands: strictly ascending edges, half-open ranges, an open top or an inclusive closing maximum, "Not set" and
  "Below <first edge>"; the edge rule is stated in words (RULE-013).
* Cross-table measures: one aggregate along approved links only, with a semantics sentence; no approved link
  is refused (MSG-026) and a measure never spans two connections (RULE-014).
* Preview counts per bucket run under the Creator's own scope and show "data as of".
* A field whose base dimension or link disappears from the approved model is marked Broken and its owner is
  notified once.
"""

from __future__ import annotations

import hashlib
import json
import re
from itertools import pairwise
from typing import Any, Literal

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from tela_api.audit import service as audit
from tela_api.content.common import canonical, notify
from tela_api.core.db import fetch_all, fetch_one, tenant_tx
from tela_api.identity.deps import require
from tela_api.identity.permissions import Perm
from tela_api.identity.principal import Principal
from tela_api.identity.scope import resolve_scope
from tela_api.model.store import current_version_id, load_snapshot
from tela_api.query.personal import NO_LINK
from tela_api.query.personal import plain_rule as measure_rule
from tela_api.query.service import QueryContext, QueryService
from tela_common.errors import ErrorCode, TelaError
from tela_common.ids import new_id
from tela_query_spec.expr import AggExpr
from tela_query_spec.model import ModelSnapshot
from tela_query_spec.spec import Band, Group, PersonalField, PersonalMeasure

router = APIRouter(prefix="/api/v1", tags=["fields"])
KINDS = ("grouping", "band", "join_measure")
NUMERIC_TYPES = ("integer", "decimal", "number", "numeric", "float", "currency")


def _service(request: Request) -> QueryService:
    service: QueryService = request.app.state.query_service
    return service


def _rid(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


def _fmt(x: float) -> str:
    if abs(x) >= 1_000_000 and x % 100_000 == 0:
        return f"{x / 1_000_000:g}M"
    if abs(x) >= 1_000 and x % 100 == 0:
        return f"{x / 1_000:g}K"
    return f"{x:,g}"


class GroupIn(BaseModel):
    label: str = Field(min_length=1, max_length=80)
    values: list[str | int | float] = Field(min_length=1, max_length=500)


class FieldIn(BaseModel):
    kind: Literal["grouping", "band", "join_measure"]
    name: str = Field(min_length=1, max_length=120)
    connection_id: str
    # grouping and band
    source_ref: str | None = Field(default=None, max_length=140)
    groups: list[GroupIn] = Field(default_factory=list, max_length=50)
    catch_all_label: str = Field(default="Everything else", min_length=1, max_length=80)
    not_set_label: str = Field(default="Not set", min_length=1, max_length=80)
    case_sensitive: bool = False
    edges: list[float] = Field(default_factory=list, max_length=51)
    labels: list[str] = Field(default_factory=list, max_length=50)
    open_ended_top: bool = True
    # join measure
    path: list[str] = Field(default_factory=list, max_length=5)
    agg: Literal["count", "count_distinct", "sum", "avg", "min", "max"] = "count_distinct"
    target: str | None = Field(default=None, max_length=140)


class PreviewIn(BaseModel):
    field_id: str | None = None
    field: FieldIn | None = None
    measure_ref: str | None = None


def _key(name: str) -> str:
    k = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_") or "field"
    return ("f_" + k if k[0].isdigit() else k)[:50]


def _source(snap: ModelSnapshot, ref: str) -> tuple[str, str, str, str]:
    """(entity, field, label, data_type) of a dimension:<key> or field:<entity>.<field> reference."""
    if ref.startswith("dimension:"):
        dim = snap.dimension(ref.split(":", 1)[1])
        if dim is None:
            raise TelaError(ErrorCode.VALIDATION_ERROR, "That dimension is not in the approved model.")
        ent = snap.entity(dim.entity)
        f = ent.field(dim.field) if ent else None
        return dim.entity, dim.field, dim.label, f.data_type if f else "text"
    if ref.startswith("field:") and "." in ref:
        entity, field = ref.split(":", 1)[1].split(".", 1)
        ent = snap.entity(entity)
        f = ent.field(field) if ent else None
        if f is None:
            raise TelaError(ErrorCode.VALIDATION_ERROR, "That field is not in the approved model.")
        return entity, field, f.label, f.data_type
    raise TelaError(ErrorCode.VALIDATION_ERROR, "Pick a dimension or field to build on.")


def build(body: FieldIn, snap: ModelSnapshot, hidden: frozenset[str], key: str) -> tuple[dict[str, Any], str]:
    """Validate and turn the request into a stored definition (PersonalField or PersonalMeasure) and plain rule."""
    if body.kind in ("grouping", "band"):
        entity, field, label, data_type = _source(snap, body.source_ref or "")
        if f"{entity}.{field}" in hidden:
            raise TelaError(ErrorCode.VALIDATION_ERROR, f"{label} is not available to your role.")
        if body.kind == "grouping":
            names = [g.label.strip() for g in body.groups]
            if not names:
                raise TelaError(ErrorCode.VALIDATION_ERROR, "Add at least one group.")
            reserved = {body.catch_all_label.strip().lower(), body.not_set_label.strip().lower()}
            if len({n.lower() for n in names}) != len(names) or reserved & {n.lower() for n in names}:
                raise TelaError(ErrorCode.VALIDATION_ERROR, "Group names must be different from each other and from "
                                "the catch-all and Not set names.")
            try:
                pf = PersonalField(key=key, kind="grouping", source=body.source_ref or "",
                                   groups=[Group(label=g.label.strip(), values=g.values) for g in body.groups],
                                   catch_all=body.catch_all_label.strip(), not_set=body.not_set_label.strip(),
                                   case_sensitive=body.case_sensitive)
            except ValidationError as exc:
                msg = exc.errors()[0]["msg"].removeprefix("Value error, ")
                raise TelaError(ErrorCode.VALIDATION_ERROR, msg[:1].upper() + msg[1:] + ".") from exc
            parts = [f"{g.label} ({', '.join(str(v) for v in g.values[:6])}{'…' if len(g.values) > 6 else ''})"
                     for g in pf.groups]
            rule = (f"Groups {label} into {', '.join(parts[:-1]) + ' and ' + parts[-1] if len(parts) > 1 else parts[0]}"
                    f". Any other value goes to {pf.catch_all}; blank values go to {pf.not_set}. Matching "
                    + ("is case-sensitive." if pf.case_sensitive else "ignores case and extra spaces."))
            return pf.model_dump(), rule
        if data_type not in NUMERIC_TYPES:
            raise TelaError(ErrorCode.VALIDATION_ERROR, f"Bands need a number; {label} is {data_type}.")
        edges = body.edges
        if len(edges) < 2:
            raise TelaError(ErrorCode.VALIDATION_ERROR, "Give at least two edges.")
        for a, b in pairwise(edges):
            if b <= a:
                kind = "overlap" if b < a else "repeat"
                raise TelaError(ErrorCode.VALIDATION_ERROR, f"Band edges {kind}: {_fmt(b)} must be above {_fmt(a)}. "
                                "Edges must go up strictly, with no gaps.")
        n = len(edges) if body.open_ended_top else len(edges) - 1
        labels = body.labels or [
            (f"{_fmt(edges[i])} - {_fmt(edges[i + 1])}" if i + 1 < len(edges) else f"{_fmt(edges[i])}+")
            for i in range(n)]
        if len(labels) != n:
            raise TelaError(ErrorCode.VALIDATION_ERROR, f"Give {n} band names for {len(edges)} edges.")
        bands = [Band(label=labels[i], lower=edges[i], upper=edges[i + 1] if i + 1 < len(edges) else None)
                 for i in range(n)]
        pf = PersonalField(key=key, kind="band", source=body.source_ref or "", bands=bands,
                           closing_max=None if body.open_ended_top else edges[-1])
        words = [f"{b.label}: {_fmt(b.lower)} up to {'and including ' if b.upper == pf.closing_max else ''}"
                 f"{_fmt(b.upper)}" if b.upper is not None else f"{b.label}: {_fmt(b.lower)} and above" for b in bands]
        rule = (f"Bands {label}. {'; '.join(words)}. The lower edge is included and the upper edge is not"
                f"{' (except the closing maximum)' if pf.closing_max is not None else ''}; exactly "
                f"{_fmt(edges[1])} goes into {bands[1].label if len(bands) > 1 else bands[0].label}. Blank values go "
                f"to Not set; values below {_fmt(edges[0])} go to Below {_fmt(edges[0])}"
                + (f"; values above {_fmt(edges[-1])} go to Above {_fmt(edges[-1])}." if pf.closing_max is not None
                   else "."))
        return pf.model_dump(), rule
    # Cross-table measure (RULE-014): the aggregate lives on the first entity of the path.
    path = body.path
    if not path:
        raise TelaError(ErrorCode.VALIDATION_ERROR, "Choose the data the measure counts.")
    for e in path:
        if snap.entity(e) is None:
            raise TelaError(ErrorCode.VALIDATION_ERROR, f"'{e}' is not in this data source. A measure reads one "
                            "database.")
    for here, there in pairwise(path):
        if snap.join_path(here, there) is None:
            raise TelaError(ErrorCode.VALIDATION_ERROR, NO_LINK.format(a=here, b=there))  # MSG-026
    target_field: str | None = None
    if body.target:
        entity, _, target_field = body.target.partition(".")
        if entity != path[0]:
            raise TelaError(ErrorCode.VALIDATION_ERROR, "The counted field must belong to the first data in the path.")
        if f"{entity}.{target_field}" in hidden:
            raise TelaError(ErrorCode.VALIDATION_ERROR, "That field is not available to your role.")
    try:
        AggExpr(agg=body.agg, field=target_field)
    except ValidationError as exc:
        raise TelaError(ErrorCode.VALIDATION_ERROR, f"'{body.agg}' needs a field to aggregate.") from exc
    pm = PersonalMeasure(key=f"pm_{key}"[:60], label=body.name.strip(), entity=path[0], agg=body.agg,
                         field=target_field,
                         linked_to=path[-1] if len(path) > 1 else None)
    from tela_api.query.personal import to_measure

    to_measure(pm, snap)
    return pm.model_dump(), measure_rule(pm, snap)


def definition_hash(kind: str, definition: dict[str, Any]) -> str:
    core = {k: v for k, v in definition.items() if k not in ("key", "label")}
    return hashlib.sha256(canonical({"kind": kind, **core}).encode()).hexdigest()


def spec_fragment(row: dict[str, Any]) -> dict[str, Any]:
    """What a spec needs to use this field, and how it is referenced."""
    d = row["definition"]
    if row["kind"] == "join_measure":
        return {"personal_measures": [d], "measure": {"ref": f"measure:{d['key']}"}}
    return {"personal_fields": [d], "breakdown": {"ref": f"personal:{d['key']}"}}


def field_out(row: dict[str, Any]) -> dict[str, Any]:
    d = row["definition"]
    return {"id": str(row["id"]), "kind": row["kind"], "name": row["label"], "key": row["key"],
            "connection_id": str(row["connection_id"]), "model_version_id": str(row["model_version_id"]),
            "definition": d, "plain_rule": row["plain_rule"], "badge": "certified" if row["cert_status"] == "certified"
            else "personal", "cert_status": row["cert_status"], "certified_as": row["certified_as"],
            "broken": row["broken_reason"] is not None, "broken_reason": row["broken_reason"],
            "spec_fragment": spec_fragment(row), "updated_at": row["updated_at"]}


def broken_against(snap: ModelSnapshot, kind: str, definition: dict[str, Any]) -> str | None:
    """Why a personal field would not work on this model version, or None. Also used for a draft's impact (US-144)."""
    try:
        if kind == "join_measure":
            pm = PersonalMeasure.model_validate(definition)
            if snap.entity(pm.entity) is None or (pm.linked_to and snap.join_path(pm.entity, pm.linked_to) is None):
                return "The data or link this measure uses is no longer in the approved model."
        else:
            _source(snap, definition["source"])
    except TelaError:
        return "The base field is no longer in the approved model."
    return None


async def _check_broken(conn: AsyncConnection, principal: Principal, row: dict[str, Any]) -> dict[str, Any]:
    """A field whose base was removed from the current approved model is Broken (US-075); notify once."""
    current = await current_version_id(conn, str(row["connection_id"]))
    reason = None
    if current is None:
        reason = "The data source has no approved model."
    elif current != str(row["model_version_id"]):
        reason = broken_against(await load_snapshot(conn, principal.tenant_id, current), row["kind"], row["definition"])
    if reason != row["broken_reason"]:
        await conn.execute(text("UPDATE content.personal_fields SET broken_reason = :r WHERE id = :id"),
                           {"r": reason, "id": row["id"]})
        if reason is not None:
            await notify(conn, principal.tenant_id, [str(row["owner_id"])], kind="definition.broken",
                         title=f"{row['label']} is broken", body=reason, link="/fields")
        row = {**row, "broken_reason": reason}
    return row


async def own_field(conn: AsyncConnection, principal: Principal, field_id: str) -> dict[str, Any]:
    try:
        row = await fetch_one(conn, "SELECT * FROM content.personal_fields WHERE id = CAST(:id AS uuid) AND "
                                    "kind <> 'freehand' AND deleted_at IS NULL", {"id": field_id})
    except Exception:  # noqa: BLE001
        row = None
    if row is None or str(row["owner_id"]) != principal.user_id:
        raise TelaError(ErrorCode.NOT_FOUND, "That field does not exist or is not yours.")
    return row


async def model_context(conn: AsyncConnection, principal: Principal, connection_id: str,
                   version_id: str | None = None) -> tuple[ModelSnapshot, frozenset[str]]:
    version_id = version_id or await current_version_id(conn, connection_id)
    if version_id is None:
        raise TelaError(ErrorCode.MODEL_NOT_APPROVED, "This data source has no approved model yet.")
    snap = await load_snapshot(conn, principal.tenant_id, version_id)
    if snap.connection_id != connection_id:
        raise TelaError(ErrorCode.NOT_FOUND, "That data source does not exist.")
    try:
        hidden = (await resolve_scope(conn, principal, connection_id)).hidden_fields
    except TelaError:
        hidden = frozenset()
    return snap, hidden


async def _unique_key(conn: AsyncConnection, principal: Principal, version_id: str, base: str,
                      exclude: str | None = None) -> str:
    taken = {r["key"] for r in await fetch_all(
        conn, "SELECT key FROM content.personal_fields WHERE owner_id = :u AND model_version_id = :v AND deleted_at "
              "IS NULL AND (CAST(:x AS uuid) IS NULL OR id <> CAST(:x AS uuid))",
        {"u": principal.user_id, "v": version_id, "x": exclude})}
    key, n = base, 2
    while key in taken:
        key, n = f"{base[:46]}_{n}", n + 1
    return key


@router.get("/fields")
async def list_fields(principal: Principal = Depends(require(Perm.FIELDS_CREATE))) -> dict[str, Any]:
    async with tenant_tx(principal.tenant_id) as conn:
        # Free-hand rules held for certification (US-166) are widgets' definitions, not fields.
        rows = await fetch_all(conn, "SELECT * FROM content.personal_fields WHERE owner_id = :u AND deleted_at IS NULL "
                                     "AND kind <> 'freehand' ORDER BY updated_at DESC", {"u": principal.user_id})
        rows = [await _check_broken(conn, principal, r) for r in rows]
    return {"items": [field_out(r) for r in rows]}


@router.post("/fields", status_code=201)
async def create_field(body: FieldIn, request: Request,
                       principal: Principal = Depends(require(Perm.FIELDS_CREATE))) -> dict[str, Any]:
    async with tenant_tx(principal.tenant_id) as conn:
        snap, hidden = await model_context(conn, principal, body.connection_id)
        clash = await fetch_one(conn, "SELECT 1 AS x FROM content.personal_fields WHERE owner_id = :u AND "
                                      "lower(label) = lower(:l) AND deleted_at IS NULL",
                                {"u": principal.user_id, "l": body.name.strip()})
        if clash:
            raise TelaError(ErrorCode.CONFLICT, f"You already have a field called {body.name.strip()}.")
        key = await _unique_key(conn, principal, snap.model_version_id, _key(body.name))
        definition, rule = build(body, snap, hidden, key)
        fid = new_id()
        await conn.execute(
            text("INSERT INTO content.personal_fields (id, tenant_id, owner_id, connection_id, model_version_id, kind, "
                 "key, label, definition, definition_hash, plain_rule) VALUES (:id, :t, :u, :c, :v, :k, :key, :l, "
                 "CAST(:d AS jsonb), :h, :r)"),
            {"id": fid, "t": principal.tenant_id, "u": principal.user_id, "c": body.connection_id,
             "v": snap.model_version_id, "k": body.kind, "key": key, "l": body.name.strip(),
             "d": json.dumps(definition), "h": definition_hash(body.kind, definition), "r": rule})
        await audit.record(conn, tenant_id=principal.tenant_id, action="field.created", object_type="personal_field",
                           object_id=fid, outcome="success", actor_id=principal.user_id,
                           correlation_id=_rid(request), details={"kind": body.kind})
        row = await own_field(conn, principal, fid)
    return field_out(row)


@router.get("/fields/{field_id}")
async def get_field(field_id: str, principal: Principal = Depends(require(Perm.FIELDS_CREATE))) -> dict[str, Any]:
    async with tenant_tx(principal.tenant_id) as conn:
        row = await _check_broken(conn, principal, await own_field(conn, principal, field_id))
    return field_out(row)


@router.patch("/fields/{field_id}")
async def update_field(field_id: str, body: FieldIn, request: Request,
                       principal: Principal = Depends(require(Perm.FIELDS_CREATE))) -> dict[str, Any]:
    """Edit in place (same kind). A pending certification request gets the new snapshot and is flagged changed."""
    from tela_api.governance.certification import on_item_changed

    async with tenant_tx(principal.tenant_id) as conn:
        row = await own_field(conn, principal, field_id)
        if body.kind != row["kind"]:
            raise TelaError(ErrorCode.VALIDATION_ERROR, "A field keeps its kind. Create a new field instead.")
        if row["cert_status"] in ("certified", "merged"):
            raise TelaError(ErrorCode.CONFLICT, "Certified fields can't be edited. Create a new personal field.")
        snap, hidden = await model_context(conn, principal, str(row["connection_id"]))
        key = row["key"] if snap.model_version_id == str(row["model_version_id"]) else \
            await _unique_key(conn, principal, snap.model_version_id, row["key"], exclude=field_id)
        definition, rule = build(body, snap, hidden, key)
        h = definition_hash(body.kind, definition)
        await conn.execute(
            text("UPDATE content.personal_fields SET label = :l, definition = CAST(:d AS jsonb), definition_hash = :h, "
                 "plain_rule = :r, model_version_id = :v, key = :key, broken_reason = NULL WHERE id = :id"),
            {"l": body.name.strip(), "d": json.dumps(definition), "h": h, "r": rule, "v": snap.model_version_id,
             "key": key, "id": row["id"]})
        if h != row["definition_hash"]:
            await on_item_changed(conn, principal, row["kind"], field_id)
        await audit.record(conn, tenant_id=principal.tenant_id, action="field.updated", object_type="personal_field",
                           object_id=field_id, outcome="success", actor_id=principal.user_id,
                           correlation_id=_rid(request))
        row = await own_field(conn, principal, field_id)
    return field_out(row)


@router.delete("/fields/{field_id}", status_code=204)
async def delete_field(field_id: str, request: Request,
                       principal: Principal = Depends(require(Perm.FIELDS_CREATE))) -> Response:
    async with tenant_tx(principal.tenant_id) as conn:
        row = await own_field(conn, principal, field_id)
        used = await fetch_all(conn, "SELECT title FROM content.widgets WHERE deleted_at IS NULL AND "
                                     "definition_refs @> "
                                     "CAST(:r AS jsonb)", {"r": json.dumps([{"id": field_id}])})
        if used:
            raise TelaError(ErrorCode.CONFLICT, f"{row['label']} is used by {used[0]['title']}. Remove it from those "
                            "widgets first.", {"widgets": [u["title"] for u in used]})
        await conn.execute(text("UPDATE content.personal_fields SET deleted_at = now() WHERE id = :id"),
                           {"id": row["id"]})
        await conn.execute(text("UPDATE gov.certification_requests SET status = 'withdrawn' WHERE item_id = :id AND "
                                "status = 'pending'"), {"id": row["id"]})
        await audit.record(conn, tenant_id=principal.tenant_id, action="field.deleted", object_type="personal_field",
                           object_id=field_id, outcome="success", actor_id=principal.user_id,
                           correlation_id=_rid(request))
    return Response(status_code=204)


async def preview_definition(request: Request, principal: Principal, kind: str, definition: dict[str, Any],
                             snap: ModelSnapshot, connection_id: str, measure_ref: str | None = None) -> dict[str, Any]:
    """Counts per bucket (grouping, band) or the value (measure) within this person's scope (API-031)."""
    base = {"model_version_id": snap.model_version_id, "connection_id": connection_id}
    if kind == "join_measure":
        spec = {**base, "measure": {"ref": f"measure:{definition['key']}"}, "personal_measures": [definition]}
    else:
        entity = _source(snap, definition["source"])[0]
        ref = measure_ref
        if ref is None:
            m = next((m for m in snap.measures if m.entity == entity and m.status == "certified"
                      and isinstance(m.expression, AggExpr) and m.expression.agg == "count"
                      and m.expression.where is None), None)
            if m is None:
                raise TelaError(ErrorCode.VALIDATION_ERROR, "Choose a measure to count with.")
            ref = f"measure:{m.key}"
        spec = {**base, "measure": {"ref": ref}, "personal_fields": [definition],
                "breakdowns": [{"ref": f"personal:{definition['key']}"}]}
    res = await _service(request).execute(spec, principal, QueryContext(surface="preview", request_id=_rid(request)))
    if res.get("hidden"):
        return {"hidden": True, "message": res.get("message"), "buckets": [], "as_of": None}
    out: dict[str, Any] = {"as_of": res.get("as_of"), "scope_text": res["scope"]["summary"],
                           "measure": res["definitions"][0]["label"] if res.get("definitions") else None}
    if kind == "join_measure":
        out["value"] = res["rows"][0][-1] if res["rows"] else 0
        out["semantics"] = res["definitions"][0]["plain_rule"] if res.get("definitions") else None
        return out
    out["buckets"] = [{"label": r[0], "count": r[-1]} for r in res["rows"]]
    return out


class WordsIn(BaseModel):
    text: str = Field(min_length=1, max_length=500)
    connection_id: str | None = None


@router.post("/fields/draft")
async def draft_in_words(body: WordsIn, principal: Principal = Depends(require(Perm.FIELDS_CREATE))) -> dict[str, Any]:
    """A field-builder draft from words, e.g. "group platforms into Core and Growth" (US-075). Nothing is saved."""
    from tela_api.content.field_words import draft_from_words

    if not body.connection_id:
        raise TelaError(ErrorCode.VALIDATION_ERROR, "Choose a data source.")
    async with tenant_tx(principal.tenant_id) as conn:
        snap, hidden = await model_context(conn, principal, body.connection_id)
    return draft_from_words(body.text, snap, hidden).as_json(body.connection_id)


@router.post("/fields/{field_id}/edit-in-words")
async def edit_in_words(field_id: str, body: WordsIn,
                        principal: Principal = Depends(require(Perm.FIELDS_CREATE))) -> dict[str, Any]:
    """Propose an edit to a band in words, with a before/after diff (US-076; FEAT-029 A2). Applying it is a PATCH
    with ``proposal``; the edges are validated here first, so a conflict names the edges before anything is shown."""
    from tela_api.content.field_words import band_diff, edit_bands_in_words

    async with tenant_tx(principal.tenant_id) as conn:
        row = await own_field(conn, principal, field_id)
        if row["kind"] != "band":
            raise TelaError(ErrorCode.VALIDATION_ERROR, "Editing in words works for bands. Change groups in the "
                            "builder.")
        snap, hidden = await model_context(conn, principal, str(row["connection_id"]))
    edit = edit_bands_in_words(body.text, row["definition"])
    proposal = FieldIn(kind="band", name=row["label"], connection_id=str(row["connection_id"]),
                       source_ref=row["definition"]["source"], edges=edit.edges, labels=edit.labels,
                       open_ended_top=edit.open_top)
    _, rule = build(proposal, snap, hidden, row["key"])
    return {"proposal": proposal.model_dump(exclude_defaults=False), "changes": edit.changes,
            "diff": band_diff(row["definition"], edit.edges, edit.labels, edit.open_top),
            "rule_before": row["plain_rule"], "rule_after": rule}


@router.post("/fields/preview")
async def preview(body: PreviewIn, request: Request,
                  principal: Principal = Depends(require(Perm.FIELDS_CREATE))) -> dict[str, Any]:
    async with tenant_tx(principal.tenant_id) as conn:
        if body.field_id:
            row = await own_field(conn, principal, body.field_id)
            snap, _ = await model_context(conn, principal, str(row["connection_id"]), str(row["model_version_id"]))
            kind, definition, rule, cid = row["kind"], row["definition"], row["plain_rule"], str(row["connection_id"])
        elif body.field:
            snap, hidden = await model_context(conn, principal, body.field.connection_id)
            kind, cid = body.field.kind, body.field.connection_id
            definition, rule = build(body.field, snap, hidden, "preview_field")
        else:
            raise TelaError(ErrorCode.VALIDATION_ERROR, "Send a field or a field id.")
    out = await preview_definition(request, principal, kind, definition, snap, cid, body.measure_ref)
    return {**out, "edge_rules": rule, "kind": kind}
