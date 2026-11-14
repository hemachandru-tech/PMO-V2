"""Answer feedback (ENH-013, US-183; FEAT-064 / FEAT-018).

* ``GET/PUT/DELETE /ask/turns/{id}/feedback`` (``ask.use``) — the author's thumbs up or down on their own answer;
  one per person and turn, a second vote replaces the first. A down-vote may say why: ``wrong`` or
  ``not_what_i_asked`` ("This isn't what I asked"), with a comment of up to 500 characters. The question and the
  Query Spec are copied from the turn — never rows, headlines or numbers. A vote an Admin already resolved is final.
* ``GET /ask/feedback`` (``knowledge.manage``) — the Admin queue: open down-votes first by default, with counts.
* ``PATCH /ask/feedback/{id}`` (``knowledge.manage``) — ``fixed`` with the gold example made from it (the screen
  saves it through ``POST /knowledge/gold-examples`` with the turn id, so it reaches the suite and the few-shots
  through the usual review), or ``dismissed`` with an optional note. The voter is told when theirs is fixed.

Audit: ``answer.feedback``, ``answer.feedback_resolved``.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import text

from tela_api.audit import service as audit
from tela_api.content.common import notify
from tela_api.core.db import fetch_all, fetch_one, tenant_tx
from tela_api.identity.deps import require
from tela_api.identity.permissions import Perm
from tela_api.identity.principal import Principal
from tela_common.errors import ErrorCode, TelaError
from tela_common.ids import new_id

router = APIRouter(prefix="/api/v1/ask", tags=["ask"])

_SELECT = """
    SELECT f.id, f.turn_id, f.user_id, u.display_name AS user_name, f.vote, f.reason, f.comment, f.question, f.spec,
           f.connection_id, c.name AS connection_name, f.status, f.gold_example_id, f.resolved_by,
           r.display_name AS resolved_by_name, f.resolved_at, f.resolution_note, f.created_at, f.updated_at
    FROM chat.answer_feedback f
    JOIN iam.users u ON u.id = f.user_id
    LEFT JOIN conn.connections c ON c.id = f.connection_id
    LEFT JOIN iam.users r ON r.id = f.resolved_by
"""


def _rid(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def feedback_out(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(row["id"]), "turn_id": str(row["turn_id"]),
        "user": {"id": str(row["user_id"]), "display_name": row["user_name"]},
        "vote": row["vote"], "reason": row["reason"], "comment": row["comment"], "question": row["question"],
        "spec": row["spec"],
        "connection": {"id": str(row["connection_id"]), "name": row["connection_name"]}
        if row["connection_id"] else None,
        "status": row["status"],
        "gold_example_id": str(row["gold_example_id"]) if row["gold_example_id"] else None,
        "resolved_by": {"id": str(row["resolved_by"]), "display_name": row["resolved_by_name"]}
        if row["resolved_by"] else None,
        "resolved_at": _iso(row["resolved_at"]), "resolution_note": row["resolution_note"],
        "created_at": _iso(row["created_at"]), "updated_at": _iso(row["updated_at"]),
    }


async def _own_turn(conn: Any, principal: Principal, turn_id: str) -> dict[str, Any]:
    try:
        turn = await fetch_one(conn, "SELECT id, user_id, question, spec, connection_id, status FROM chat.turns "
                                     "WHERE id = CAST(:id AS uuid)", {"id": turn_id})
    except Exception:  # noqa: BLE001 - a malformed id is not found
        turn = None
    if turn is None or str(turn["user_id"]) != principal.user_id:
        raise TelaError(ErrorCode.NOT_FOUND, "That answer was not found.")
    return dict(turn)


async def _mine(conn: Any, principal: Principal, turn_id: str) -> dict[str, Any] | None:
    row = await fetch_one(conn, _SELECT + " WHERE f.turn_id = CAST(:t AS uuid) AND f.user_id = CAST(:u AS uuid)",
                          {"t": turn_id, "u": principal.user_id})
    return dict(row) if row else None


class FeedbackIn(BaseModel):
    vote: Literal["up", "down"]
    reason: Literal["wrong", "not_what_i_asked"] | None = None
    comment: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def _down_only(self) -> FeedbackIn:
        if self.comment is not None:
            self.comment = " ".join(self.comment.split()) or None
        if self.vote == "up" and (self.reason or self.comment):
            raise ValueError("Only a thumbs down takes a reason or a comment.")
        return self


@router.get("/turns/{turn_id}/feedback")
async def get_feedback(turn_id: str, principal: Principal = Depends(require(Perm.ASK_USE))) -> dict[str, Any]:
    async with tenant_tx(principal.tenant_id) as conn:
        await _own_turn(conn, principal, turn_id)
        row = await _mine(conn, principal, turn_id)
    return {"feedback": _voter_out(row) if row else None}


def _voter_out(row: dict[str, Any]) -> dict[str, Any]:
    """What the voter sees of their own vote: no Admin names or notes."""
    return {k: v for k, v in feedback_out(row).items() if k in ("id", "turn_id", "vote", "reason", "comment",
                                                                 "status", "created_at", "updated_at")}


@router.put("/turns/{turn_id}/feedback")
async def put_feedback(turn_id: str, body: FeedbackIn, request: Request,
                       principal: Principal = Depends(require(Perm.ASK_USE))) -> dict[str, Any]:
    async with tenant_tx(principal.tenant_id) as conn:
        turn = await _own_turn(conn, principal, turn_id)
        if turn["status"] != "complete":
            raise TelaError(ErrorCode.CONFLICT, "Only a finished answer can be rated.")
        old = await _mine(conn, principal, turn_id)
        if old is not None and old["status"] in ("fixed", "dismissed"):
            raise TelaError(ErrorCode.CONFLICT, "An Admin has already looked at your feedback on this answer.")
        status = "none" if body.vote == "up" else "open"
        params = {"id": old["id"] if old else new_id(), "t": principal.tenant_id, "turn": turn_id,
                  "u": principal.user_id, "vote": body.vote, "reason": body.reason, "comment": body.comment,
                  "q": turn["question"], "spec": json.dumps(turn["spec"]) if turn["spec"] is not None else None,
                  "c": str(turn["connection_id"]) if turn["connection_id"] else None, "st": status}
        await conn.execute(
            text("INSERT INTO chat.answer_feedback (id, tenant_id, turn_id, user_id, vote, reason, comment, question, "
                 "spec, connection_id, status) VALUES (:id, :t, CAST(:turn AS uuid), CAST(:u AS uuid), :vote, "
                 ":reason, :comment, :q, CAST(:spec AS jsonb), CAST(:c AS uuid), :st) "
                 "ON CONFLICT (turn_id, user_id) DO UPDATE SET vote = EXCLUDED.vote, reason = EXCLUDED.reason, "
                 "comment = EXCLUDED.comment, status = EXCLUDED.status, spec = EXCLUDED.spec"), params)
        await audit.record(conn, tenant_id=principal.tenant_id, action="answer.feedback", object_type="answer",
                           object_id=turn_id, outcome="success", actor_id=principal.user_id,
                           correlation_id=_rid(request), details={"vote": body.vote, "reason": body.reason})
        row = await _mine(conn, principal, turn_id)
    assert row is not None
    return {"feedback": _voter_out(row)}


@router.delete("/turns/{turn_id}/feedback", status_code=204)
async def delete_feedback(turn_id: str, principal: Principal = Depends(require(Perm.ASK_USE))) -> Response:
    async with tenant_tx(principal.tenant_id) as conn:
        await _own_turn(conn, principal, turn_id)
        old = await _mine(conn, principal, turn_id)
        if old is not None and old["status"] in ("fixed", "dismissed"):
            raise TelaError(ErrorCode.CONFLICT, "An Admin has already looked at your feedback on this answer.")
        await conn.execute(text("DELETE FROM chat.answer_feedback WHERE turn_id = CAST(:t AS uuid) AND "
                                "user_id = CAST(:u AS uuid)"), {"t": turn_id, "u": principal.user_id})
    return Response(status_code=204)


@router.get("/feedback")
async def list_feedback(status: Literal["open", "fixed", "dismissed", "all"] = Query(default="open"),
                        principal: Principal = Depends(require(Perm.KNOWLEDGE_MANAGE))) -> dict[str, Any]:
    async with tenant_tx(principal.tenant_id) as conn:
        rows = await fetch_all(
            conn, _SELECT + " WHERE f.vote = 'down' AND (:s = 'all' OR f.status = :s) "
                            "ORDER BY (f.status = 'open') DESC, f.created_at DESC LIMIT 500", {"s": status})
        counts = await fetch_all(conn, "SELECT status, count(*) AS n FROM chat.answer_feedback GROUP BY status")
    by = {r["status"]: int(r["n"]) for r in counts}
    return {"items": [feedback_out(dict(r)) for r in rows],
            "counts": {"open": by.get("open", 0), "fixed": by.get("fixed", 0), "dismissed": by.get("dismissed", 0),
                       "up": by.get("none", 0)}}


class ResolveIn(BaseModel):
    status: Literal["fixed", "dismissed"]
    gold_example_id: str | None = Field(default=None, max_length=64)
    note: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def _gold_for_fixed(self) -> ResolveIn:
        if self.status == "fixed" and not self.gold_example_id:
            raise ValueError("A fixed answer names the gold example made from it.")
        return self


@router.patch("/feedback/{feedback_id}")
async def resolve_feedback(feedback_id: str, body: ResolveIn, request: Request,
                           principal: Principal = Depends(require(Perm.KNOWLEDGE_MANAGE))) -> dict[str, Any]:
    async with tenant_tx(principal.tenant_id) as conn:
        try:
            row = await fetch_one(conn, _SELECT + " WHERE f.id = CAST(:id AS uuid) FOR UPDATE OF f",
                                  {"id": feedback_id})
        except Exception:  # noqa: BLE001 - a malformed id is not found
            row = None
        if row is None or row["vote"] != "down":
            raise TelaError(ErrorCode.NOT_FOUND, "That feedback was not found.")
        if row["status"] != "open":
            raise TelaError(ErrorCode.CONFLICT, "This feedback has already been resolved.")
        if body.status == "fixed":
            try:
                gold = await fetch_one(conn, "SELECT id, status FROM rag.gold_examples WHERE id = CAST(:g AS uuid)",
                                       {"g": body.gold_example_id})
            except Exception:  # noqa: BLE001
                gold = None
            if gold is None or gold["status"] == "rejected":
                raise TelaError(ErrorCode.VALIDATION_ERROR, "Save the corrected gold example first.")
        await conn.execute(
            text("UPDATE chat.answer_feedback SET status = :st, gold_example_id = CAST(:g AS uuid), resolved_by = "
                 "CAST(:u AS uuid), resolved_at = now(), resolution_note = :note WHERE id = CAST(:id AS uuid)"),
            {"st": body.status, "g": body.gold_example_id if body.status == "fixed" else None,
             "u": principal.user_id, "note": " ".join((body.note or "").split()) or None, "id": feedback_id})
        await audit.record(conn, tenant_id=principal.tenant_id, action="answer.feedback_resolved",
                           object_type="answer", object_id=str(row["turn_id"]), outcome="success",
                           actor_id=principal.user_id, correlation_id=_rid(request),
                           details={"feedback_id": feedback_id, "status": body.status,
                                    "gold_example_id": body.gold_example_id})
        if body.status == "fixed" and str(row["user_id"]) != principal.user_id:
            await notify(conn, principal.tenant_id, [str(row["user_id"])], kind="answer.feedback_fixed",
                         title="Your flagged answer was fixed",
                         body=f"“{row['question'][:200]}” now has a corrected answer that Ask learns from.",
                         by=principal.user_id)
        out = await fetch_one(conn, _SELECT + " WHERE f.id = CAST(:id AS uuid)", {"id": feedback_id})
    assert out is not None
    return feedback_out(dict(out))
