"""Ask endpoints (API-016 to API-018; FEAT-018, FEAT-019; US-046).

* ``POST /ask/turns`` validates the question (1 to 2,000 characters), applies the rate limit (30 per minute
  per user) and concurrency limits (2 per user, 8 per instance), records the turn and starts the
  orchestrator in the background. 202 with the stream URL.
* ``GET /ask/turns/{id}/stream`` relays ``chat.turn_events`` as SSE with a heartbeat every 15 s and
  resumes after ``Last-Event-ID``. It ends after ``complete``, ``error`` or ``clarify``.
* ``POST /ask/turns/{id}/clarification`` resumes a paused turn with the chosen interpretation and
  remembers it for the conversation (FEAT-019 A2).
* Turns belong to their author only. Rows are never stored: ``GET /ask/turns/{id}`` re-runs the spec
  through the cache and the scoped path.

Deviation: the orchestrator runs as an in-process background task, so a turn is tied to the API replica
that accepted it; the stream reads events from the database and works from any replica.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections import defaultdict, deque
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import text

from tela_api.ask.orchestrator import Orchestrator
from tela_api.ask.refine import (
    REDIRECT,
    RefineIn,
    apply_field,
    apply_ops,
    chip_ops_from,
    is_rule_change,
    parse_instruction,
)
from tela_api.content.common import log_change
from tela_api.core.db import fetch_all, fetch_one, tenant_tx
from tela_api.identity.deps import require
from tela_api.identity.permissions import Perm
from tela_api.identity.principal import Principal
from tela_api.knowledge.values import propose_synonym
from tela_api.model.store import load_snapshot
from tela_api.query.service import QueryContext, QueryService
from tela_common.errors import ErrorCode, TelaError
from tela_common.ids import new_id
from tela_query_spec.spec import ref_key

router = APIRouter(prefix="/api/v1/ask", tags=["ask"])
RATE_PER_MIN = 30
PER_USER = 2
PER_INSTANCE = 8
HEARTBEAT_S = 15.0
STREAM_MAX_S = 300.0
TERMINAL = {"complete", "error", "clarify"}

_calls: dict[str, deque[float]] = defaultdict(deque)
_running: dict[str, set[str]] = defaultdict(set)
_tasks: set[asyncio.Task[None]] = set()


def _orchestrator(request: Request) -> Orchestrator:
    service: QueryService = request.app.state.query_service
    return Orchestrator(service, getattr(request.app.state, "gateway", None))


def _admit(principal: Principal) -> None:
    now = time.monotonic()
    calls = _calls[principal.user_id]
    while calls and now - calls[0] > 60:
        calls.popleft()
    if len(calls) >= RATE_PER_MIN:
        raise TelaError(ErrorCode.RATE_LIMITED, "Too many questions in a minute. Try again shortly.")
    if len(_running[principal.user_id]) >= PER_USER or sum(len(v) for v in _running.values()) >= PER_INSTANCE:
        raise TelaError(ErrorCode.RATE_LIMITED, "Tela is still answering your previous questions. Try again shortly.")
    calls.append(now)


def _start(orchestrator: Orchestrator, principal: Principal, turn_id: str) -> None:
    _running[principal.user_id].add(turn_id)

    async def go() -> None:
        try:
            await orchestrator.run(principal, turn_id)
        finally:
            _running[principal.user_id].discard(turn_id)

    task = asyncio.create_task(go())
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


class AskContext(BaseModel):
    connection_id: str | None = None
    dashboard_id: str | None = None
    dashboard_version_id: str | None = None
    widget_id: str | None = None
    page_filters: list[dict[str, Any]] = Field(default_factory=list, max_length=20)


class TurnIn(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    conversation_id: str | None = None
    connection_id: str | None = None
    context: AskContext = Field(default_factory=AskContext)


async def _own_turn(conn: Any, principal: Principal, turn_id: str) -> dict[str, Any]:
    try:
        row = await fetch_one(conn, "SELECT * FROM chat.turns WHERE id = CAST(:id AS uuid) AND user_id = :u",
                              {"id": turn_id, "u": principal.user_id})
    except Exception:  # noqa: BLE001 - malformed ids are simply not found
        row = None
    if row is None:
        raise TelaError(ErrorCode.NOT_FOUND, "That question does not exist.")
    return row


@router.post("/turns", status_code=202)
async def create_turn(body: TurnIn, request: Request,
                      principal: Principal = Depends(require(Perm.ASK_USE))) -> dict[str, Any]:
    question = body.question.strip()
    if not question:
        raise TelaError(ErrorCode.VALIDATION_ERROR, "Type a question first.")
    _admit(principal)
    context = body.context.model_dump()
    context["connection_id"] = body.connection_id or body.context.connection_id
    async with tenant_tx(principal.tenant_id) as conn:
        conversation_id = body.conversation_id
        if conversation_id:
            own = await fetch_one(conn, "SELECT id FROM chat.conversations WHERE id = CAST(:id AS uuid) "
                                        "AND user_id = :u", {"id": conversation_id, "u": principal.user_id})
            if own is None:
                raise TelaError(ErrorCode.NOT_FOUND, "That conversation does not exist.")
        else:
            conversation_id = new_id()
            await conn.execute(
                text("INSERT INTO chat.conversations (id, tenant_id, user_id, title) VALUES (:id, :t, :u, :title)"),
                {"id": conversation_id, "t": principal.tenant_id, "u": principal.user_id, "title": question[:120]})
        turn_id = new_id()
        await conn.execute(
            text("INSERT INTO chat.turns (id, tenant_id, conversation_id, user_id, question, context) "
                 "VALUES (:id, :t, CAST(:c AS uuid), :u, :q, CAST(:ctx AS jsonb))"),
            {"id": turn_id, "t": principal.tenant_id, "c": conversation_id, "u": principal.user_id, "q": question,
             "ctx": json.dumps(context)})
        await conn.execute(text("UPDATE chat.conversations SET updated_at = now() WHERE id = CAST(:c AS uuid)"),
                           {"c": conversation_id})
    _start(_orchestrator(request), principal, turn_id)
    return {"turn_id": turn_id, "conversation_id": conversation_id,
            "stream_url": f"/api/v1/ask/turns/{turn_id}/stream"}


@router.get("/turns/{turn_id}/stream")
async def stream(turn_id: str, request: Request, last_event_id: str | None = Header(default=None),
                 principal: Principal = Depends(require(Perm.ASK_USE))) -> StreamingResponse:
    async with tenant_tx(principal.tenant_id) as conn:
        await _own_turn(conn, principal, turn_id)
    after = int(last_event_id) if last_event_id and last_event_id.isdigit() else 0

    async def events() -> AsyncIterator[str]:
        nonlocal after
        started = last_beat = time.monotonic()
        yield "retry: 2000\n\n"
        while time.monotonic() - started < STREAM_MAX_S:
            async with tenant_tx(principal.tenant_id) as conn:
                rows = await fetch_all(conn, "SELECT id, event, data FROM chat.turn_events WHERE turn_id = :t "
                                             "AND id > :a ORDER BY id", {"t": turn_id, "a": after})
                status = await fetch_one(conn, "SELECT status, planner FROM chat.turns WHERE id = :t", {"t": turn_id})
            for r in rows:
                after = r["id"]
                data = r["data"]
                if r["event"] == "complete" and data.get("spec") and not data.get("hidden"):
                    data = await _with_rows(request, principal, turn_id, data,
                                            fields=bool(status and status["planner"] in ("fields", "hybrid")))
                yield f"id: {r['id']}\nevent: {r['event']}\ndata: {json.dumps(data, default=str)}\n\n"
                # A clarify the person has since answered is not the end: replay what followed it.
                answered = r["event"] == "clarify" and status is not None and status["status"] in (
                    "pending", "running", "complete", "error")
                if r["event"] in TERMINAL and not answered:
                    return
            if not rows and status and status["status"] in ("complete", "error", "awaiting_clarification"):
                return
            if time.monotonic() - last_beat >= HEARTBEAT_S:
                last_beat = time.monotonic()
                yield ": heartbeat\n\n"
            await asyncio.sleep(0.15)

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})


async def _with_rows(request: Request, principal: Principal, turn_id: str, data: dict[str, Any], *,
                     fields: bool = False) -> dict[str, Any]:
    """Rows travel with the stream but are never stored on the turn: re-read through the scope-keyed cache.
    ``fields``: a Plan B answer (US-254), whose ad-hoc measure runs under the answer mode, not a free-hand grant."""
    service: QueryService = request.app.state.query_service
    try:
        result = await service.execute(data["spec"], principal, QueryContext(surface="ask", turn_id=turn_id,
                                                                             fields=fields))
    except TelaError as exc:
        return {**data, "columns": [], "rows": [], "result_error": {"code": exc.code, "message": exc.message}}
    return {**data, "columns": result["columns"], "rows": result["rows"], "as_of": result.get("as_of")}


async def _new_turn(conn: Any, principal: Principal, parent: dict[str, Any], question: str,
                    context: dict[str, Any]) -> str:
    turn_id = new_id()
    await conn.execute(
        text("INSERT INTO chat.turns (id, tenant_id, conversation_id, user_id, question, context) "
             "VALUES (:id, :t, :c, :u, :q, CAST(:ctx AS jsonb))"),
        {"id": turn_id, "t": principal.tenant_id, "c": parent["conversation_id"], "u": principal.user_id,
         "q": question[:2000], "ctx": json.dumps(context, default=str)})
    await conn.execute(text("UPDATE chat.conversations SET updated_at = now() WHERE id = :c"),
                       {"c": parent["conversation_id"]})
    return str(turn_id)


@router.post("/turns/{turn_id}/refine", status_code=202)
async def refine(turn_id: str, body: RefineIn, request: Request,
                 principal: Principal = Depends(require(Perm.ASK_USE))) -> dict[str, Any]:
    """Refine in words or chips (API-019): a new turn with the changed spec; the rule never changes."""
    async with tenant_tx(principal.tenant_id) as conn:
        turn = await _own_turn(conn, principal, turn_id)
        if turn["status"] != "complete" or not turn["spec"]:
            raise TelaError(ErrorCode.CONFLICT, "Only a finished answer can be refined.")
        snap = await load_snapshot(conn, principal.tenant_id, str(turn["model_version_id"]))
    measure = snap.measure(ref_key(turn["spec"]["measure"]["ref"]))
    if body.instruction and is_rule_change(body.instruction):
        return {"turn_id": None, "redirect": {"options": ["personal_field", "model_change"]},
                "message": REDIRECT.format(measure=measure.label if measure else "this measure")}
    ops = chip_ops_from(body)
    base_spec = turn["spec"]
    said_fields: list[str] = []
    for op in [o for o in ops if o.get("op") == "use_field"]:
        async with tenant_tx(principal.tenant_id) as conn:
            from tela_api.content.fields import own_field

            field = await own_field(conn, principal, str(op.get("value") or op.get("ref") or ""))
        if str(field["model_version_id"]) != str(base_spec["model_version_id"]) or field["broken_reason"]:
            raise TelaError(ErrorCode.VALIDATION_ERROR, f"{field['label']} was built on another model version. Edit "
                            "it to rebuild it on the current model.")
        base_spec, field_said = apply_field(base_spec, field)
        said_fields.append(field_said)
    ops = [o for o in ops if o.get("op") != "use_field"]
    if body.instruction:
        orch = _orchestrator(request)
        sources = await orch.sources(principal, str(turn["connection_id"]))
        if sources and measure is not None:
            values = await orch._values(principal, sources[0], measure, body.instruction)
            ops += parse_instruction(body.instruction, turn["spec"], snap, measures=sources[0].measures,
                                     dims=sources[0].dims, values=values)
    if not ops and not said_fields:
        raise TelaError(ErrorCode.VALIDATION_ERROR, "I couldn't turn that into a change. Try “top 5”, “as columns”, "
                        "“only KSA”, “by month” or “compare with the previous period”.")
    spec, said = apply_ops(base_spec, ops, snap)
    said = said_fields + said
    service: QueryService = request.app.state.query_service
    await service.prepare({k: v for k, v in spec.items() if k != "compare"}, principal,
                          QueryContext(surface="ask", request_id=getattr(request.state, "request_id", None)))
    _admit(principal)
    async with tenant_tx(principal.tenant_id) as conn:
        new = await _new_turn(conn, principal, turn, body.instruction or "; ".join(said), {
            "connection_id": str(turn["connection_id"]), "refined_from": turn_id, "preset_spec": spec,
            "changes": said, "ops": ops, "widget_id": (turn["context"] or {}).get("widget_id")})
        await log_change(conn, principal, object_type="turn_spec", object_id=new, source="refine",
                         before=turn["spec"], after=spec, summary="; ".join(said))
    _start(_orchestrator(request), principal, new)
    return {"turn_id": new, "conversation_id": str(turn["conversation_id"]), "changes": said,
            "stream_url": f"/api/v1/ask/turns/{new}/stream"}


@router.post("/turns/{turn_id}/undo", status_code=202)
async def undo(turn_id: str, request: Request,
               principal: Principal = Depends(require(Perm.ASK_USE))) -> dict[str, Any]:
    """Undo (API-020): a new turn with the previous spec; repeatable back to the first answer."""
    async with tenant_tx(principal.tenant_id) as conn:
        turn = await _own_turn(conn, principal, turn_id)
        prev_id = (turn["context"] or {}).get("refined_from")
        if not prev_id:
            raise TelaError(ErrorCode.CONFLICT, "Nothing to undo.")
        prev = await _own_turn(conn, principal, prev_id)
        if not prev["spec"]:
            raise TelaError(ErrorCode.CONFLICT, "Nothing to undo.")
    _admit(principal)
    async with tenant_tx(principal.tenant_id) as conn:
        new = await _new_turn(conn, principal, turn, f"Undo: {turn['question']}", {
            "connection_id": str(prev["connection_id"]), "preset_spec": prev["spec"], "undo_of": turn_id,
            "refined_from": (prev["context"] or {}).get("refined_from"),
            "changes": [f"Back to: {prev['question'][:200]}"]})
        await log_change(conn, principal, object_type="turn_spec", object_id=new, source="refine",
                         before=turn["spec"], after=prev["spec"], summary="Undo")
    _start(_orchestrator(request), principal, new)
    return {"turn_id": new, "conversation_id": str(turn["conversation_id"]),
            "stream_url": f"/api/v1/ask/turns/{new}/stream"}


class ClarificationIn(BaseModel):
    option_id: str | None = Field(default=None, max_length=4)
    free_text: str | None = Field(default=None, max_length=300)


@router.post("/turns/{turn_id}/clarification", status_code=202)
async def clarify(turn_id: str, body: ClarificationIn, request: Request,
                  principal: Principal = Depends(require(Perm.ASK_USE))) -> dict[str, Any]:
    async with tenant_tx(principal.tenant_id) as conn:
        turn = await _own_turn(conn, principal, turn_id)
        if turn["status"] != "awaiting_clarification" or not turn["clarification"]:
            raise TelaError(ErrorCode.CONFLICT, "This question is not waiting for an answer.")
        q = turn["clarification"]
        option = next((o for o in q["options"] if o["id"] == body.option_id), None)
        if option is None and body.free_text:
            needle = body.free_text.lower()
            option = next((o for o in q["options"] if needle in o["label"].lower()
                           or o["label"].lower() in needle), None)
        if option is None:
            raise TelaError(ErrorCode.VALIDATION_ERROR, "Pick one of the options.")
        if q.get("kind") != "intent":  # what a term means stays for the conversation; an intent is this turn's only
            await conn.execute(
                text("UPDATE chat.conversations SET memory = memory || CAST(:m AS jsonb) WHERE id = :c"),
                {"m": json.dumps({q["key"]: option.get("measure") or option["value"]}), "c": turn["conversation_id"]})
        if q.get("kind") == "value" and turn["connection_id"]:
            # "network" meant Network One: an Admin may make it a synonym for everyone (US-173, FEAT-014).
            _, dim, phrase = q["key"].split(":", 2)
            await propose_synonym(conn, principal, str(turn["connection_id"]), dim,
                                  option["value"].split(":", 2)[2], phrase)
        await conn.execute(text("UPDATE chat.turns SET status = 'pending', clarification = clarification || "
                                "CAST(:a AS jsonb) WHERE id = :id"),
                           {"a": json.dumps({"answer": option["id"]}), "id": turn["id"]})
    _admit(principal)
    _start(_orchestrator(request), principal, turn_id)
    return {"turn_id": turn_id, "stream_url": f"/api/v1/ask/turns/{turn_id}/stream", "chosen": option["label"]}


@router.get("/turns/{turn_id}")
async def get_turn(turn_id: str, request: Request,
                   principal: Principal = Depends(require(Perm.ASK_USE))) -> dict[str, Any]:
    async with tenant_tx(principal.tenant_id) as conn:
        turn = await _own_turn(conn, principal, turn_id)
    out = {k: turn[k] for k in ("question", "status", "intent", "spec", "chips", "visual", "narrative",
                                "clarification", "error", "planner", "ask_mode", "created_at")}
    out.update(id=str(turn["id"]), conversation_id=str(turn["conversation_id"]))
    if turn["status"] == "complete" and turn["spec"]:
        service: QueryService = request.app.state.query_service
        try:
            result = await service.execute(turn["spec"], principal, QueryContext(
                surface="ask", turn_id=turn_id, fields=turn["planner"] in ("fields", "hybrid")))
            out["result"] = {k: result[k] for k in ("columns", "rows", "truncated", "as_of", "scope", "definitions")}
        except TelaError as exc:
            out["result_error"] = {"code": exc.code, "message": exc.message}
    return out


@router.get("/conversations")
async def conversations(principal: Principal = Depends(require(Perm.ASK_USE))) -> dict[str, Any]:
    async with tenant_tx(principal.tenant_id) as conn:
        rows = await fetch_all(
            conn, "SELECT c.id, c.title, c.updated_at, count(t.id) AS turns FROM chat.conversations c LEFT JOIN "
                  "chat.turns t ON t.conversation_id = c.id WHERE c.user_id = :u GROUP BY c.id "
                  "ORDER BY c.updated_at DESC LIMIT 50", {"u": principal.user_id})
    return {"items": [{**r, "id": str(r["id"])} for r in rows]}


@router.get("/conversations/{conversation_id}")
async def conversation(conversation_id: str,
                       principal: Principal = Depends(require(Perm.ASK_USE))) -> dict[str, Any]:
    async with tenant_tx(principal.tenant_id) as conn:
        try:
            conv = await fetch_one(conn, "SELECT id, title FROM chat.conversations WHERE id = CAST(:id AS uuid) "
                                         "AND user_id = :u", {"id": conversation_id, "u": principal.user_id})
        except Exception:  # noqa: BLE001
            conv = None
        if conv is None:
            raise TelaError(ErrorCode.NOT_FOUND, "That conversation does not exist.")
        turns = await fetch_all(conn, "SELECT id, question, status, visual, narrative, error, created_at FROM "
                                      "chat.turns WHERE conversation_id = :c ORDER BY created_at", {"c": conv["id"]})
    return {"id": str(conv["id"]), "title": conv["title"], "turns": [{**t, "id": str(t["id"])} for t in turns]}
