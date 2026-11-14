"""Chart enhancement suggestions on an Ask answer and on a draft report's widget (US-104, FEAT-016; AG-07).

Creators and Admins see suggestions on their own answers and drafts; Viewers don't (FEAT-016 roles).
``GET`` returns up to three checked suggestions (``ask/advisor.py``) or "Looks good as is". Applying one is a
refinement on an answer — ``POST /ask/turns/{id}/refine`` with the suggestion's ``ops`` as chip ops, which has
Undo — and one report change on a widget, which the report's Undo steps back. The apply endpoint takes the ops
back and checks them again (certified measures only, RULE-008 on the changed result) before saving.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import text

from tela_api.ask.advisor import Candidate, Context, advise, check
from tela_api.ask.orchestrator import Orchestrator, Source
from tela_api.ask.refine import ChipOp, apply_ops
from tela_api.ask.router import _own_turn
from tela_api.content.access import own_report
from tela_api.content.common import def_hash, is_custom
from tela_api.content.reports import SET_DEF_HASH, _item, item_widget, mutate
from tela_api.core.db import fetch_one, tenant_tx
from tela_api.identity.deps import require
from tela_api.identity.permissions import Perm
from tela_api.identity.principal import Principal
from tela_api.knowledge.patterns import ChartPattern, for_model, load_patterns, pack_source_for
from tela_api.llm.router import tenant_llm_settings
from tela_api.model.store import load_snapshot
from tela_api.query.service import QueryContext, QueryService
from tela_common.errors import ErrorCode, TelaError
from tela_query_spec.spec import ref_key

router = APIRouter(prefix="/api/v1", tags=["suggestions"])
CUSTOM_SQL = "Suggestions work on certified measures; this widget is custom SQL."


def _orchestrator(request: Request) -> Orchestrator:
    service: QueryService = request.app.state.query_service
    return Orchestrator(service, getattr(request.app.state, "gateway", None))


async def _patterns(principal: Principal, connection_id: str, snap: Any, packs_dir: str | None) -> list[ChartPattern]:
    async with tenant_tx(principal.tenant_id) as conn:
        dm = await fetch_one(conn, "SELECT id FROM model.data_models WHERE connection_id = CAST(:c AS uuid)",
                             {"c": connection_id})
        pack = await pack_source_for(conn, str(dm["id"]), packs_dir) if dm else None
    return for_model(load_patterns(pack), snap) if pack else []


async def _context(request: Request, principal: Principal, spec: dict[str, Any], visual: str,
                   ctx: QueryContext) -> tuple[Context, Source, Any]:
    orch = _orchestrator(request)
    sources = await orch.sources(principal, str(spec["connection_id"]))
    if not sources:
        raise TelaError(ErrorCode.NOT_FOUND, "That data source is not available to you.")
    async with tenant_tx(principal.tenant_id) as conn:
        snap = await load_snapshot(conn, principal.tenant_id, str(spec["model_version_id"]))
    src = Source(sources[0].connection, snap, sources[0].scope)  # the version the spec was made on
    service: QueryService = request.app.state.query_service

    async def run(changed: dict[str, Any]) -> dict[str, Any]:
        return dict(await service.execute(changed, principal, ctx))

    result = await run({k: v for k, v in spec.items() if k not in ("compare", "visual")})
    patterns = await _patterns(principal, str(spec["connection_id"]), snap, request.app.state.settings.packs_dir)
    return Context(spec=spec, visual=visual, result=result, snap=snap, measures=src.measures, dims=src.dims,
                   patterns=patterns), src, run


async def _advise(request: Request, principal: Principal, spec: dict[str, Any], visual: str,
                  ctx: QueryContext) -> dict[str, Any]:
    context, src, run = await _context(request, principal, spec, visual, ctx)
    orch = _orchestrator(request)
    async with tenant_tx(principal.tenant_id) as conn:
        settings = await tenant_llm_settings(conn)
    labels = [context.snap.measure(ref_key(spec["measure"]["ref"]))]
    query = " ".join([*(m.label for m in labels if m), *(b["ref"].split(":", 1)[1].replace("_", " ")
                                                         for b in spec.get("breakdowns", [])), visual])
    # The pack's chart patterns and its dashboard rules (US-159: Not set out of %, colour never alone …).
    knowledge = await orch.retrieve(principal, f"chart {query}", src, kinds=["chart_pattern", "dashboard_rule"])
    out = await advise(context, run, gateway=orch.gateway, use_llm=bool(settings.get("provider")),
                       hits=knowledge.hits, tenant_id=principal.tenant_id, user_id=principal.user_id)
    return {**out, "limited_context": knowledge.limited,
            "patterns": [{"ref": h.ref, "title": h.title, "pattern": h.metadata.get("pattern")}
                         for h in knowledge.hits if h.kind == "chart_pattern"],
            "rules": [{"ref": h.ref, "title": h.title, "rule": h.metadata.get("rule")}
                      for h in knowledge.hits if h.kind == "dashboard_rule"]}


# --------------------------------------------------------------------------- Ask answers


@router.get("/ask/turns/{turn_id}/suggestions")
async def turn_suggestions(turn_id: str, request: Request,
                           principal: Principal = Depends(require(Perm.WIDGETS_CREATE))) -> dict[str, Any]:
    async with tenant_tx(principal.tenant_id) as conn:
        turn = await _own_turn(conn, principal, turn_id)
    if turn["status"] != "complete" or not turn["spec"]:
        raise TelaError(ErrorCode.CONFLICT, "Suggestions need a finished answer.")
    spec = {**turn["spec"], "model_version_id": str(turn["spec"].get("model_version_id") or turn["model_version_id"]),
            "connection_id": str(turn["spec"].get("connection_id") or turn["connection_id"])}
    return await _advise(request, principal, spec, turn["visual"] or "table",
                         QueryContext(surface="ask", turn_id=turn_id))


# --------------------------------------------------------------------------- report widgets


async def _widget(principal: Principal, report_id: str, item_id: str) -> dict[str, Any]:
    async with tenant_tx(principal.tenant_id) as conn:
        report = await own_report(conn, principal, report_id)
        item = await _item(conn, report, item_id)
        if item["widget_id"] is None:
            raise TelaError(ErrorCode.VALIDATION_ERROR, "Text notes have no chart.")
        w = await fetch_one(conn, "SELECT * FROM content.widgets WHERE id = :w", {"w": item["widget_id"]})
    assert w is not None
    return w


@router.get("/reports/{report_id}/items/{item_id}/suggestions")
async def item_suggestions(report_id: str, item_id: str, request: Request,
                           principal: Principal = Depends(require(Perm.REPORTS_CREATE))) -> dict[str, Any]:
    w = await _widget(principal, report_id, item_id)
    if is_custom(w["spec"]):
        return {"suggestions": [], "message": CUSTOM_SQL, "source": "rules", "limited_context": False, "patterns": []}
    return await _advise(request, principal, dict(w["spec"]), w["visual"],
                         QueryContext(surface="preview", widget_id=str(w["id"])))


class ApplyIn(BaseModel):
    ops: list[ChipOp] = Field(min_length=1, max_length=4)
    title: str = Field(default="Chart suggestion", min_length=1, max_length=80)


@router.post("/reports/{report_id}/items/{item_id}/suggestions/apply")
async def apply_suggestion(report_id: str, item_id: str, body: ApplyIn, request: Request, response: Response,
                           principal: Principal = Depends(require(Perm.REPORTS_CREATE))) -> dict[str, Any]:
    """Apply a suggestion to a widget: checked again, then one report change (Undo steps back)."""
    w = await _widget(principal, report_id, item_id)
    if is_custom(w["spec"]):
        raise TelaError(ErrorCode.VALIDATION_ERROR, CUSTOM_SQL)
    ops = [{k: v for k, v in o.model_dump(exclude_none=True).items() if k in ("op", "value", "ref", "dir")}
           for o in body.ops]
    context, _, run = await _context(request, principal, dict(w["spec"]), w["visual"],
                                     QueryContext(surface="preview", widget_id=str(w["id"])))
    asked = next((str(o.get("value")) for o in ops if o.get("op") == "set_visual"), None)
    checked = await check(context, Candidate(body.title, body.title, ops, asked), run)
    if checked is None:
        raise TelaError(ErrorCode.VALIDATION_ERROR, "That suggestion no longer fits this widget. Open Suggestions "
                        "again for current ones.")
    spec, said = apply_ops(dict(w["spec"]), checked["ops"], context.snap)
    spec["visual"] = {"type": checked["visual"]}

    async def fn(conn: Any, report: dict[str, Any]) -> None:
        item = await _item(conn, report, item_id)
        await conn.execute(
            text(f"UPDATE content.widgets SET spec = CAST(:spec AS jsonb), {SET_DEF_HASH}, visual = :v "  # noqa: S608
                 "WHERE id = :id AND owner_id = CAST(:u AS uuid)"),
            {"spec": json.dumps(spec), "h": def_hash(spec), "v": checked["visual"],
             "id": await item_widget(conn, item, principal.user_id),
             "u": principal.user_id})

    out, _ = await mutate(principal, report_id, source="canvas", summary=f"Suggestion: {body.title} "
                          f"({'; '.join(said)})"[:300], fn=fn, response=response,
                          request_id=getattr(request.state, "request_id", None),
                          audit_action="report.suggestion_applied")
    return {**out, "applied": checked}
