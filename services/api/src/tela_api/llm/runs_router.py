"""Agent runs for Admins (ENH-024, US-227 to US-229; DEC-25).

* ``GET /admin/agent-runs`` — runs newest first with an opaque cursor; filters: agent, kind, outcome, person, has a
  model call, date range, turn, words of the title. ``/facets`` counts runs per agent, kind and outcome.
* ``GET /admin/agent-runs/{id}`` — the run and its steps, without LLM text (``has_text`` says which steps hold it).
  A run still going has ``outcome = running`` and the steps so far (the screen polls it, US-228).
* ``GET /admin/agent-runs/{id}/steps/{seq}/text`` — what went to the model (masked, as sent) and its raw reply;
  every read is audited (``agent_run.text_viewed``).
* ``GET /admin/agent-runs/{id}/export`` — the run as JSON with its text, for a bug report (``agent_run.exported``).
* ``GET``/``PUT /admin/agent-runs/settings`` — keep LLM text or not, and for how many days
  (``agent_run.settings_changed``); ``POST /admin/agent-runs/purge-text`` clears all kept text now
  (``agent_run.text_purged``).
* ``POST /internal/agent-runs/sweep`` — the daily job: text cleared after the tenant's days, runs deleted after 90.

Admins only (``settings.manage``, like AI settings); row-level security keeps each tenant's runs to itself.
"""

from __future__ import annotations

import base64
import json
from datetime import datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import text

from tela_api.audit import service as audit
from tela_api.content.exports import require_worker
from tela_api.core.db import fetch_all, fetch_one, tenant_tx
from tela_api.identity.deps import require
from tela_api.identity.permissions import Perm
from tela_api.identity.principal import Principal
from tela_common.errors import ErrorCode, TelaError

router = APIRouter(prefix="/api/v1/admin/agent-runs", tags=["agent-runs"])
internal = APIRouter(prefix="/api/v1/internal/agent-runs", tags=["internal"], include_in_schema=False)

AGENTS = {"AG-01": "Intent router", "AG-02": "Clarifier", "AG-03": "Query planner", "AG-04": "Model assistant",
          "AG-05": "Report planner", "AG-06": "Canvas editor", "AG-07": "Visual advisor", "AG-08": "Narrator",
          "AG-09": "Certification reviewer", "AG-10": "SQL drafter"}
KINDS = ("ask", "model_assist", "pack_mapping", "certification", "custom_sql", "report_plan", "canvas_edit", "agent")
OUTCOMES = ("running", "ok", "fallback", "clarify", "refused", "error")
RUN_DAYS = 90  # runs and steps kept (DATA-046's window); text goes sooner, after the tenant's days
ADMIN = Perm.SETTINGS_MANAGE


def _rid(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


def _encode(r: dict[str, Any]) -> str:
    return base64.urlsafe_b64encode(f"{r['started_at'].isoformat()}|{r['id']}".encode()).decode()


def _decode(value: str) -> tuple[datetime, str]:
    try:
        at, _, rid = base64.urlsafe_b64decode(value.encode()).decode().partition("|")
        return datetime.fromisoformat(at), rid
    except (ValueError, UnicodeDecodeError) as exc:
        raise TelaError(ErrorCode.VALIDATION_ERROR, "That page cursor is not valid.") from exc


def _filters(agent: str | None, kind: str | None, outcome: str | None, user: str | None, llm: bool | None,
             start: datetime | None, end: datetime | None, turn_id: str | None, q: str | None,
             skip: str = "") -> tuple[str, dict[str, Any]]:
    where: list[str] = ["true"]
    params: dict[str, Any] = {}
    if agent and skip != "agent":
        where.append(":agent = ANY(r.agents)")
        params["agent"] = agent
    if kind and skip != "kind":
        where.append("r.kind = :kind")
        params["kind"] = kind
    if outcome and skip != "outcome":
        where.append("r.outcome = :outcome")
        params["outcome"] = outcome
    if user:
        where.append("(u.display_name ILIKE :user OR CAST(r.user_id AS text) = :user_id)")
        params.update(user=f"%{user}%", user_id=user)
    if llm is not None:
        where.append("r.llm_calls > 0" if llm else "r.llm_calls = 0")
    if start:
        where.append("r.started_at >= :start")
        params["start"] = start
    if end:
        where.append("r.started_at < :end")
        params["end"] = end
    if turn_id:
        where.append("CAST(r.turn_id AS text) = :turn")
        params["turn"] = turn_id
    if q:
        where.append("r.title ILIKE :q")
        params["q"] = f"%{q}%"
    return " AND ".join(where), params


_RUNS = ("SELECT r.*, u.display_name AS user_name FROM ops.agent_runs r LEFT JOIN iam.users u ON u.id = r.user_id "
         "WHERE ")


def _run(r: dict[str, Any]) -> dict[str, Any]:
    return {"id": str(r["id"]), "kind": r["kind"], "title": r["title"], "agents": list(r["agents"] or []),
            "turn_id": str(r["turn_id"]) if r["turn_id"] else None, "object_ref": r["object_ref"],
            "user": {"id": str(r["user_id"]) if r["user_id"] else None, "name": r["user_name"]},
            "outcome": r["outcome"], "llm_calls": r["llm_calls"], "prompt_tokens": r["prompt_tokens"],
            "completion_tokens": r["completion_tokens"], "started_at": r["started_at"], "ended_at": r["ended_at"],
            "duration_ms": r["duration_ms"], "text_kept": r["capture_text"],
            "ask_mode": r.get("ask_mode")}


def _step(s: dict[str, Any]) -> dict[str, Any]:
    return {"seq": s["seq"], "agent": s["agent"], "kind": s["kind"], "label": s["label"], "status": s["status"],
            "started_at": s["started_at"], "duration_ms": s["duration_ms"], "detail": s["detail"],
            "task": s["task"], "provider": s["provider"], "model": s["model"], "prompt_tokens": s["prompt_tokens"],
            "completion_tokens": s["completion_tokens"], "has_text": bool(s["has_text"]),
            "text_cleared": s["text_cleared_at"] is not None, "text_cut": s["text_cut"]}


@router.get("")
async def list_runs(agent: str | None = None, kind: str | None = None, outcome: str | None = None,
                    user: str | None = None, llm: bool | None = None,
                    start: datetime | None = Query(default=None, alias="from"),
                    end: datetime | None = Query(default=None, alias="to"),
                    turn_id: str | None = None, q: str | None = Query(default=None, max_length=200),
                    cursor: str | None = None, limit: int = Query(default=50, ge=1, le=100),
                    principal: Principal = Depends(require(ADMIN))) -> dict[str, Any]:
    where, params = _filters(agent, kind, outcome, user, llm, start, end, turn_id, q)
    if cursor:
        at, rid = _decode(cursor)
        where += " AND (r.started_at, r.id) < (:at, CAST(:rid AS uuid))"
        params.update(at=at, rid=rid)
    async with tenant_tx(principal.tenant_id) as conn:
        rows = await fetch_all(conn, _RUNS + where + " ORDER BY r.started_at DESC, r.id DESC LIMIT :lim",
                               {**params, "lim": limit + 1})
    more = len(rows) > limit
    rows = rows[:limit]
    return {"items": [_run(r) for r in rows], "next_cursor": _encode(rows[-1]) if more and rows else None,
            "agents": AGENTS}


@router.get("/facets")
async def facets(agent: str | None = None, kind: str | None = None, outcome: str | None = None,
                 user: str | None = None, llm: bool | None = None,
                 start: datetime | None = Query(default=None, alias="from"),
                 end: datetime | None = Query(default=None, alias="to"),
                 turn_id: str | None = None, q: str | None = Query(default=None, max_length=200),
                 principal: Principal = Depends(require(ADMIN))) -> dict[str, Any]:
    """Runs per agent, kind and outcome; each counted under every other filter but its own."""
    out: dict[str, dict[str, int]] = {}
    base = "FROM ops.agent_runs r LEFT JOIN iam.users u ON u.id = r.user_id WHERE "
    async with tenant_tx(principal.tenant_id) as conn:
        for name, expr in (("agent", "unnest(r.agents)"), ("kind", "r.kind"), ("outcome", "r.outcome")):
            where, params = _filters(agent, kind, outcome, user, llm, start, end, turn_id, q, skip=name)
            rows = await fetch_all(conn, f"SELECT {expr} AS v, count(*) AS n {base}{where} GROUP BY 1 "
                                         "ORDER BY 1", params)
            out[name] = {str(r["v"]): int(r["n"]) for r in rows}
    return out


class TraceSettingsIn(BaseModel):
    capture_text: bool
    text_days: int = Field(ge=1, le=90)


async def _settings(conn: Any) -> dict[str, Any]:
    row = await fetch_one(conn, "SELECT capture_text, text_days, updated_at FROM ops.agent_trace_settings")
    return dict(row) if row else {"capture_text": True, "text_days": 14, "updated_at": None}


@router.get("/settings")
async def get_settings(principal: Principal = Depends(require(ADMIN))) -> dict[str, Any]:
    async with tenant_tx(principal.tenant_id) as conn:
        return {**await _settings(conn), "run_days": RUN_DAYS}


@router.put("/settings")
async def put_settings(body: TraceSettingsIn, request: Request,
                       principal: Principal = Depends(require(ADMIN))) -> dict[str, Any]:
    async with tenant_tx(principal.tenant_id) as conn:
        before = await _settings(conn)
        await conn.execute(text(
            "INSERT INTO ops.agent_trace_settings (tenant_id, capture_text, text_days, updated_by) "
            "VALUES (:t, :c, :d, CAST(:u AS uuid)) ON CONFLICT (tenant_id) DO UPDATE SET capture_text = "
            "EXCLUDED.capture_text, text_days = EXCLUDED.text_days, updated_by = EXCLUDED.updated_by, "
            "updated_at = now()"),
            {"t": principal.tenant_id, "c": body.capture_text, "d": body.text_days, "u": principal.user_id})
        await audit.record(conn, tenant_id=principal.tenant_id, action="agent_run.settings_changed",
                           object_type="agent_trace_settings", outcome="success", actor_id=principal.user_id,
                           correlation_id=_rid(request),
                           details={"capture_text": [before["capture_text"], body.capture_text],
                                    "text_days": [before["text_days"], body.text_days]})
        return {**await _settings(conn), "run_days": RUN_DAYS}


@router.post("/purge-text")
async def purge_text(request: Request, principal: Principal = Depends(require(ADMIN))) -> dict[str, int]:
    async with tenant_tx(principal.tenant_id) as conn:
        n = await clear_text(conn, None)
        await audit.record(conn, tenant_id=principal.tenant_id, action="agent_run.text_purged",
                           object_type="agent_trace_settings", outcome="success", actor_id=principal.user_id,
                           correlation_id=_rid(request), details={"steps": n})
    return {"steps": n}


async def _one(conn: Any, run_id: str) -> dict[str, Any]:
    try:
        UUID(run_id)
    except ValueError as exc:
        raise TelaError(ErrorCode.NOT_FOUND, "That agent run was not found.") from exc
    row = await fetch_one(conn, _RUNS + "r.id = CAST(:id AS uuid)", {"id": run_id})
    if row is None:
        raise TelaError(ErrorCode.NOT_FOUND, "That agent run was not found.")
    return row


_STEPS = ("SELECT seq, agent, kind, label, status, started_at, duration_ms, detail, task, provider, model, "
          "prompt_tokens, completion_tokens, text_cut, text_cleared_at, "
          "(prompt_text IS NOT NULL OR reply_text IS NOT NULL) AS has_text FROM ops.agent_steps "
          "WHERE run_id = CAST(:id AS uuid) ORDER BY seq")


@router.get("/{run_id}")
async def get_run(run_id: str, principal: Principal = Depends(require(ADMIN))) -> dict[str, Any]:
    async with tenant_tx(principal.tenant_id) as conn:
        run = await _one(conn, run_id)
        steps = await fetch_all(conn, _STEPS, {"id": run_id})
        related = await fetch_all(conn, "SELECT id, kind, title, started_at FROM ops.agent_runs WHERE turn_id = "
                                        ":turn AND id <> :id ORDER BY started_at", {"turn": run["turn_id"],
                                                                                   "id": run["id"]}) \
            if run["turn_id"] else []
    return {**_run(run), "steps": [_step(s) for s in steps], "agent_names": AGENTS,
            "related": [{"id": str(r["id"]), "kind": r["kind"], "title": r["title"], "started_at": r["started_at"]}
                        for r in related]}


def _text(s: dict[str, Any]) -> dict[str, Any]:
    return {"seq": s["seq"], "agent": s["agent"], "task": s["task"], "provider": s["provider"], "model": s["model"],
            "prompt": s["prompt_text"], "reply": s["reply_text"], "cut": s["text_cut"],
            "cleared_at": s["text_cleared_at"]}


@router.get("/{run_id}/steps/{seq}/text")
async def step_text(run_id: str, seq: int, request: Request,
                    principal: Principal = Depends(require(ADMIN))) -> dict[str, Any]:
    async with tenant_tx(principal.tenant_id) as conn:
        await _one(conn, run_id)
        row = await fetch_one(conn, "SELECT seq, agent, task, provider, model, prompt_text, reply_text, text_cut, "
                                    "text_cleared_at FROM ops.agent_steps WHERE run_id = CAST(:id AS uuid) AND "
                                    "seq = :seq", {"id": run_id, "seq": seq})
        if row is None:
            raise TelaError(ErrorCode.NOT_FOUND, "That step was not found.")
        await audit.record(conn, tenant_id=principal.tenant_id, action="agent_run.text_viewed",
                           object_type="agent_run", object_id=run_id, outcome="success",
                           actor_id=principal.user_id, correlation_id=_rid(request),
                           details={"seq": seq, "agent": row["agent"], "task": row["task"]})
    return _text(row)


@router.get("/{run_id}/export")
async def export_run(run_id: str, request: Request, principal: Principal = Depends(require(ADMIN))) -> dict[str, Any]:
    async with tenant_tx(principal.tenant_id) as conn:
        run = await _one(conn, run_id)
        steps = await fetch_all(conn, _STEPS, {"id": run_id})
        texts = {r["seq"]: r for r in await fetch_all(
            conn, "SELECT seq, agent, task, provider, model, prompt_text, reply_text, text_cut, text_cleared_at "
                  "FROM ops.agent_steps WHERE run_id = CAST(:id AS uuid) AND (prompt_text IS NOT NULL OR "
                  "reply_text IS NOT NULL)", {"id": run_id})}
        await audit.record(conn, tenant_id=principal.tenant_id, action="agent_run.exported",
                           object_type="agent_run", object_id=run_id, outcome="success",
                           actor_id=principal.user_id, correlation_id=_rid(request),
                           details={"steps": len(steps), "with_text": len(texts)})
    out: dict[str, Any] = json.loads(json.dumps({
        "run": _run(run), "agent_names": AGENTS,
        "steps": [{**_step(s), "text": _text(texts[s["seq"]]) if s["seq"] in texts else None} for s in steps]},
        default=str))
    return out


async def clear_text(conn: Any, days: int | None) -> int:
    """Clear kept LLM text (all of it, or older than ``days``); the step and its metadata stay."""
    res = await conn.execute(text(
        "UPDATE ops.agent_steps SET prompt_text = NULL, reply_text = NULL, text_cleared_at = now() "
        "WHERE (prompt_text IS NOT NULL OR reply_text IS NOT NULL) AND (CAST(:d AS integer) IS NULL OR "
        "started_at < now() - make_interval(days => CAST(:d AS integer)))"), {"d": days})
    return int(res.rowcount or 0)


async def sweep_tenant(conn: Any) -> dict[str, int]:
    settings = await _settings(conn)
    cleared = await clear_text(conn, int(settings["text_days"]))
    res = await conn.execute(text("DELETE FROM ops.agent_runs WHERE started_at < now() - make_interval(days => :d)"),
                             {"d": RUN_DAYS})
    return {"cleared": cleared, "deleted": int(res.rowcount or 0)}


@internal.post("/sweep")
async def sweep(_: dict[str, Any] = Depends(require_worker)) -> dict[str, int]:
    async with tenant_tx(None) as conn:
        tenants = [str(r["tenant_id"]) for r in await fetch_all(conn, "SELECT tenant_id FROM "
                                                                      "ops.f_agent_run_tenants()")]
    cleared = deleted = 0
    for tenant_id in tenants:
        async with tenant_tx(tenant_id) as conn:
            out = await sweep_tenant(conn)
        cleared += out["cleared"]
        deleted += out["deleted"]
    return {"tenants": len(tenants), "cleared": cleared, "deleted": deleted}

