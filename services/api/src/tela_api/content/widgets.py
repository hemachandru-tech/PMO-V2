"""Widget library, Pin and suitable visuals (API-021, API-023; FEAT-021, FEAT-026; US-062, US-071).

* Pin turns an answer into a live widget: it stores the spec, connection and model version — never
  numbers. The chosen visual must pass the chart integrity guard; an unsuitable one is refused with the
  reason and one alternative (``visual_not_suitable``).
* The same definition pinned again reuses the library widget (``def_hash``); pinning it onto a report
  that already shows it is refused with MSG-023.
* Default sizes: figure 3 x 2, table 12 x 5, everything else 6 x 4, at the first free slot of the section.
* Viewers can ask about a widget but cannot pin: the API returns 403 (``widgets.create``).
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, Header, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import text

from tela_api.ask.refine import ChipOp, apply_ops
from tela_api.audit import service as audit
from tela_api.content.common import (
    MSG_FREEHAND_HELD,
    check_controls,
    def_hash,
    guard_visual,
    is_freehand,
    remember,
    replay,
    stored_visual,
    visual_options,
)
from tela_api.content.reports import first_section, mutate, place_widget
from tela_api.core.db import fetch_all, fetch_one, tenant_tx
from tela_api.governance import freehand_cert
from tela_api.governance.certification import enqueue, load_item
from tela_api.identity.deps import require
from tela_api.identity.permissions import Perm
from tela_api.identity.principal import Principal
from tela_api.model.store import current_version_id, load_snapshot
from tela_api.query.service import QueryContext, QueryService
from tela_common.errors import ErrorCode, TelaError
from tela_common.ids import new_id
from tela_query_spec.spec import MAX_CONTROLS, CardTone, ChartColours, FigureTone, parse_spec

router = APIRouter(prefix="/api/v1", tags=["widgets"])
PIN_ROUTE = "POST /widgets"


def _service(request: Request) -> QueryService:
    service: QueryService = request.app.state.query_service
    return service


def _rid(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


class Target(BaseModel):
    report_id: str
    section_id: str | None = None


class PinIn(BaseModel):
    from_turn_id: str
    title: str = Field(min_length=1, max_length=200)
    visual: str = Field(min_length=1, max_length=40)
    target: Target | None = None
    follow_page_filters: bool = True
    ops: list[ChipOp] | None = Field(default=None, max_length=4)  # breakdown / date change that makes the chart fit


def widget_out(w: dict[str, Any], usage: int = 0) -> dict[str, Any]:
    return {"id": str(w["id"]), "title": w["title"], "visual": w["visual"], "badges": [w["certification_state"]],
            "spec": w["spec"], "question": w.get("question"), "model_version_id": str(w["model_version_id"]),
            "connection_id": str(w["connection_id"]), "follow_page_filters": w["follow_page_filters"],
            "own_filters": w.get("own_filters") or [], "used_on_reports": usage, "updated_at": w.get("updated_at")}


@router.get("/widgets")
async def library(principal: Principal = Depends(require(Perm.WIDGETS_CREATE))) -> dict[str, Any]:
    async with tenant_tx(principal.tenant_id) as conn:
        rows = await fetch_all(
            conn, "SELECT w.*, (SELECT count(DISTINCT i.report_id) FROM content.report_items i JOIN content.reports r "
                  "ON r.id = i.report_id AND r.deleted_at IS NULL WHERE i.widget_id = w.id) AS usage FROM "
                  "content.widgets w WHERE w.owner_id = :u AND w.deleted_at IS NULL AND w.dashboard_tab_id IS NULL "
                  "ORDER BY w.updated_at DESC LIMIT 500", {"u": principal.user_id})
    return {"items": [widget_out(r, r["usage"]) for r in rows]}


@router.post("/widgets", status_code=201)
async def pin(body: PinIn, request: Request, response: Response,
              idempotency_key: str | None = Header(default=None),
              principal: Principal = Depends(require(Perm.WIDGETS_CREATE))) -> dict[str, Any]:
    async with tenant_tx(principal.tenant_id) as conn:
        if (prior := await replay(conn, principal, PIN_ROUTE, idempotency_key)) is not None:
            return prior
        try:
            turn = await fetch_one(conn, "SELECT * FROM chat.turns WHERE id = CAST(:id AS uuid) AND user_id = :u",
                                   {"id": body.from_turn_id, "u": principal.user_id})
        except Exception:  # noqa: BLE001
            turn = None
        if turn is None:
            raise TelaError(ErrorCode.NOT_FOUND, "That answer does not exist.")
        if turn["status"] != "complete" or not turn["spec"]:
            raise TelaError(ErrorCode.CONFLICT, "Only a finished answer with a chart or figure can be pinned.")
        snap = await load_snapshot(conn, principal.tenant_id, str(turn["model_version_id"]))
    spec = {k: v for k, v in turn["spec"].items() if k != "compare"}
    freehand_rule = is_freehand(spec)
    if freehand_rule and body.target is not None:
        raise TelaError(ErrorCode.CONFLICT, MSG_FREEHAND_HELD)  # US-166: the library first, a report once certified
    if body.ops:
        spec, _ = apply_ops(spec, [o.model_dump(exclude_none=True) for o in body.ops], snap)
    if body.follow_page_filters is False:
        spec["follow_page_filters"] = False
    result = await _service(request).execute(spec, principal, QueryContext(surface="preview", request_id=_rid(request)))
    guard_visual(result, snap, body.visual)
    spec["visual"] = {"type": body.visual}
    h = def_hash(spec)

    async with tenant_tx(principal.tenant_id) as conn:
        existing = await fetch_one(conn, "SELECT * FROM content.widgets WHERE owner_id = :u AND def_hash = :h AND "
                                         "deleted_at IS NULL", {"u": principal.user_id, "h": h})
        refs = spec.get("definition_refs", [])
        state = "certified"
        for ref in refs:
            row = await fetch_one(conn, "SELECT cert_status FROM content.personal_fields WHERE id = CAST(:id AS uuid)",
                                  {"id": ref["id"]})
            if row is None or row["cert_status"] not in ("certified", "merged"):
                state = "personal"
        held: str | None = None
        if freehand_rule and existing is None:
            # US-166: the rule is kept as the owner's free-hand definition and sent to an Admin at once.
            held = await freehand_cert.hold(conn, principal, spec=spec, question=turn["question"],
                                            connection_id=str(turn["connection_id"]),
                                            model_version_id=str(turn["model_version_id"]))
            refs, state = [{"kind": freehand_cert.KIND, "id": held}], "pending_certification"
        if existing is None:
            wid = new_id()
            await conn.execute(
                text("INSERT INTO content.widgets (id, tenant_id, title, spec, visual, follow_page_filters, "
                     "model_version_id, connection_id, certification_state, owner_id, question, source_kind, def_hash, "
                     "from_turn_id, definition_refs, created_by) VALUES (:id, :t, :title, CAST(:s AS jsonb), :v, :f, "
                     ":mv, :c, :state, :u, :q, 'spec', :h, :turn, CAST(:refs AS jsonb), :u)"),
                {"id": wid, "t": principal.tenant_id, "title": body.title.strip(), "s": json.dumps(spec),
                 "v": body.visual, "f": body.follow_page_filters, "mv": turn["model_version_id"],
                 "c": turn["connection_id"], "u": principal.user_id, "q": turn["question"], "h": h,
                 "turn": turn["id"], "state": state, "refs": json.dumps(refs)})
        else:
            wid = str(existing["id"])
        request_id = None
        if held is not None:
            item = await load_item(conn, freehand_cert.KIND, held)
            request_id, _ = await enqueue(conn, principal, freehand_cert.KIND, item, trigger="manual",
                                          note=f"Free-hand question: {turn['question'][:400]}")
        await audit.record(conn, tenant_id=principal.tenant_id, action="widget.pinned", object_type="widget",
                           object_id=wid, outcome="success", actor_id=principal.user_id, correlation_id=_rid(request),
                           details={"reused": existing is not None, "visual": body.visual,
                                    "report_id": body.target.report_id if body.target else None})
        w = await fetch_one(conn, "SELECT * FROM content.widgets WHERE id = :id", {"id": wid})
        assert w is not None
    out: dict[str, Any] = {**widget_out(w), "reused": existing is not None, "placement": None, "report": None,
                           **({"certification_request_id": request_id} if request_id else {})}

    if body.target is not None:
        target = body.target

        async def fn(conn: Any, report: dict[str, Any]) -> dict[str, Any]:
            on_report = await fetch_one(conn, "SELECT 1 AS ok FROM content.report_items WHERE report_id = :r AND "
                                              "widget_id = :w", {"r": report["id"], "w": wid})
            if on_report:
                raise TelaError(ErrorCode.CONFLICT, "This widget is already on this report.")  # MSG-023
            section = await first_section(conn, report, target.section_id)
            item_id = await place_widget(conn, report, section, wid, body.visual)
            row = await fetch_one(conn, "SELECT grid_x, grid_y, grid_w, grid_h FROM content.report_items WHERE id = "
                                        ":i", {"i": item_id})
            assert row is not None
            return {"item_id": item_id, "section_id": str(section["id"]), "x": row["grid_x"], "y": row["grid_y"],
                    "w": row["grid_w"], "h": row["grid_h"]}

        report, placement = await mutate(principal, target.report_id, source="canvas",
                                         summary=f"Pinned {body.title.strip()}", fn=fn, request_id=_rid(request))
        out.update(placement=placement, report={"id": report["id"], "title": report["title"],
                                                "revision": report["revision"]})
    async with tenant_tx(principal.tenant_id) as conn:
        await remember(conn, principal, PIN_ROUTE, idempotency_key, 201, out)
    return out


@router.get("/widgets/{widget_id}")
async def get_widget(widget_id: str, principal: Principal = Depends(require(Perm.WIDGETS_CREATE))) -> dict[str, Any]:
    async with tenant_tx(principal.tenant_id) as conn:
        w = await _own(conn, principal, widget_id)
    return widget_out(w)


async def _own(conn: Any, principal: Principal, widget_id: str) -> dict[str, Any]:
    try:
        w = await fetch_one(conn, "SELECT * FROM content.widgets WHERE id = CAST(:id AS uuid) AND owner_id = :u AND "
                                  "deleted_at IS NULL", {"id": widget_id, "u": principal.user_id})
    except Exception:  # noqa: BLE001
        w = None
    if w is None:
        raise TelaError(ErrorCode.NOT_FOUND, "That widget is not in your library.")
    return w


class WidgetPatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    visual: str | None = Field(default=None, max_length=40)
    follow_page_filters: bool | None = None
    # US-157: a figure's tone threshold and a meter's note, kept in ``spec.visual``; null clears them.
    tone: FigureTone | None = None
    note: str | None = Field(default=None, max_length=200)
    colour: bool | None = None  # a table in colour (ENH-020)
    card: CardTone | None = None  # ENH-023: card colour, chart colours, widget filters for readers
    colours: ChartColours | None = None
    controls: list[str] | None = Field(default=None, max_length=MAX_CONTROLS)


@router.patch("/widgets/{widget_id}")
async def patch_widget(widget_id: str, body: WidgetPatch, request: Request,
                       principal: Principal = Depends(require(Perm.WIDGETS_CREATE))) -> dict[str, Any]:
    async with tenant_tx(principal.tenant_id) as conn:
        w = await _own(conn, principal, widget_id)
        snap = await load_snapshot(conn, principal.tenant_id, str(w["model_version_id"]))
    spec = dict(w["spec"])
    if body.visual is not None:
        result = await _service(request).execute(spec, principal, QueryContext(surface="preview",
                                                                              request_id=_rid(request)))
        guard_visual(result, snap, body.visual)
    spec["visual"] = stored_visual(spec.get("visual"), body.visual or w["visual"], tone=body.tone, note=body.note,
                                   colour=body.colour, card=body.card, colours=body.colours, controls=body.controls,
                                   sent=body.model_fields_set)
    if body.controls:
        check_controls(body.controls, spec, snap)
    async with tenant_tx(principal.tenant_id) as conn:
        await conn.execute(
            text("UPDATE content.widgets SET title = coalesce(:t, title), visual = coalesce(:v, visual), "
                 "follow_page_filters = coalesce(:f, follow_page_filters), spec = CAST(:s AS jsonb) WHERE id = :id"),
            {"t": body.title.strip() if body.title else None, "v": body.visual, "f": body.follow_page_filters,
             "s": json.dumps(spec), "id": w["id"]})
        w = await _own(conn, principal, widget_id)
    return widget_out(w)


@router.delete("/widgets/{widget_id}", status_code=204)
async def delete_widget(widget_id: str, request: Request,
                        principal: Principal = Depends(require(Perm.WIDGETS_CREATE))) -> Response:
    async with tenant_tx(principal.tenant_id) as conn:
        w = await _own(conn, principal, widget_id)
        used = await fetch_all(conn, "SELECT DISTINCT r.title FROM content.report_items i JOIN content.reports r ON "
                                     "r.id = i.report_id AND r.deleted_at IS NULL WHERE i.widget_id = :w",
                               {"w": w["id"]})
        if used:
            raise TelaError(ErrorCode.CONFLICT, f"This widget is on {used[0]['title']}. Remove it from the report "
                            "first.", {"reports": [u["title"] for u in used]})
        await conn.execute(text("UPDATE content.widgets SET deleted_at = now() WHERE id = :id"), {"id": w["id"]})
        await audit.record(conn, tenant_id=principal.tenant_id, action="widget.deleted", object_type="widget",
                           object_id=widget_id, outcome="success", actor_id=principal.user_id,
                           correlation_id=_rid(request))
    return Response(status_code=204)


@router.post("/widgets/{widget_id}/upgrade")
async def upgrade(widget_id: str, request: Request,
                  principal: Principal = Depends(require(Perm.WIDGETS_CREATE))) -> dict[str, Any]:
    """Pre-publish fix "Upgrade widget" (RULE-015): re-pin to the current approved model if the spec still holds."""
    async with tenant_tx(principal.tenant_id) as conn:
        w = await _own(conn, principal, widget_id)
        current = await current_version_id(conn, str(w["connection_id"]))
    if current is None:
        raise TelaError(ErrorCode.MODEL_NOT_APPROVED, "This data source has no approved model.")
    if current == str(w["model_version_id"]):
        return {**widget_out(w), "upgraded": False}
    spec = {**w["spec"], "model_version_id": current}
    await _service(request).prepare(spec, principal, QueryContext(surface="preview", request_id=_rid(request)))
    async with tenant_tx(principal.tenant_id) as conn:
        await conn.execute(text("UPDATE content.widgets SET spec = CAST(:s AS jsonb), model_version_id = :v, "
                                "def_hash = :h WHERE id = :id"),
                           {"s": json.dumps(spec), "v": current, "h": def_hash(spec), "id": w["id"]})
        await audit.record(conn, tenant_id=principal.tenant_id, action="widget.upgraded", object_type="widget",
                           object_id=widget_id, outcome="success", actor_id=principal.user_id,
                           correlation_id=_rid(request), details={"from": str(w["model_version_id"]), "to": current})
        w = await _own(conn, principal, widget_id)
    return {**widget_out(w), "upgraded": True}


class SuitableIn(BaseModel):
    spec: dict[str, Any]


@router.post("/visuals/suitable")
async def suitable(body: SuitableIn, request: Request,
                   principal: Principal = Depends(require(Perm.ASK_USE))) -> dict[str, Any]:
    try:
        spec, _ = parse_spec(body.spec)
    except ValueError as exc:
        raise TelaError(ErrorCode.SPEC_INVALID, "The question could not be understood as a valid query.") from exc
    async with tenant_tx(principal.tenant_id) as conn:
        snap = await load_snapshot(conn, principal.tenant_id, spec.model_version_id)
    result = await _service(request).execute(body.spec, principal, QueryContext(surface="preview",
                                                                               request_id=_rid(request)))
    if result.get("hidden"):
        return {"suitable": [], "disabled": [], "recommended": None, "hidden": True}
    return visual_options(result, snap, body.spec)
