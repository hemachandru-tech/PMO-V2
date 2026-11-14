"""Agents screen API (ENH-030, US-264 to US-266; FEAT-080).

* ``GET /admin/agents`` — every agent with its prompts (how many edited), skills on and last 7 days of model calls;
  the shared prompts (the LLM review frame).
* ``GET /admin/agents/{code}`` — one agent: details, the knowledge it reads, each prompt (default, current text,
  version) and its skills.
* ``PUT /admin/agents/{code}/prompts/{key}`` — save a new version of a prompt; ``DELETE`` goes back to the default (a
  version with no text). ``GET …/versions`` lists the history, newest first.
* ``POST /admin/agents/{code}/skills``, ``PUT``/``DELETE …/skills/{id}`` — the agent's skillset.
* ``POST /admin/agents/{code}/try`` — one question with the saved and the draft prompt or skill, side by side (US-267).
* ``PUT /admin/agents/{code}/review`` — LLM review on or off for this agent, or back to the deployment default
  (US-268). The tenant's LLM review switch in AI settings still has to be on.

Admins only (``settings.manage``); every change is audited and takes effect on the next call (``prompts.invalidate``).
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import text

from tela_api.agents import catalog, prompts
from tela_api.audit import service as audit
from tela_api.core.db import fetch_all, fetch_one, tenant_tx
from tela_api.identity.deps import require
from tela_api.identity.permissions import Perm
from tela_api.identity.principal import Principal
from tela_api.llm import review as llm_review
from tela_common.errors import ErrorCode, TelaError
from tela_common.ids import new_id
from tela_common.logging import get_logger

log = get_logger(__name__)
router = APIRouter(prefix="/api/v1/admin/agents", tags=["agents"])
ADMIN = Perm.SETTINGS_MANAGE
STATS_DAYS = 7


class PromptIn(BaseModel):
    text: str = Field(min_length=1, max_length=prompts.PROMPT_MAX)
    note: str = Field(default="", max_length=500)


class ResetIn(BaseModel):
    note: str = Field(default="", max_length=500)


class ReviewIn(BaseModel):
    on: bool | None  # None: follow the deployment default


class SkillIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    body: str = Field(min_length=1, max_length=prompts.SKILL_MAX)
    enabled: bool = True


def _rid(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


def _agent(code: str) -> catalog.Agent:
    found = catalog.BY_CODE.get(code.upper())
    if found is None:
        raise TelaError(ErrorCode.NOT_FOUND, "That agent was not found.")
    return found


def _prompt(code: str, key: str) -> catalog.PromptDef:
    p = catalog.BY_KEY.get(key)
    if p is None or (p.agent != code and not (p.agent == "shared" and code == "shared")):
        raise TelaError(ErrorCode.NOT_FOUND, "That prompt was not found for this agent.")
    if not p.editable:
        raise TelaError(ErrorCode.VALIDATION_ERROR, "This prompt is built from a template and can't be edited here.")
    return p


async def _current(conn: Any) -> dict[str, dict[str, Any]]:
    rows = await fetch_all(conn, """
        SELECT DISTINCT ON (v.prompt_key) v.prompt_key, v.version, v.body, v.note, v.created_at,
               u.display_name AS by_name
        FROM ops.agent_prompt_versions v LEFT JOIN iam.users u ON u.id = v.created_by
        ORDER BY v.prompt_key, v.version DESC""")
    return {r["prompt_key"]: r for r in rows}


async def _stats(conn: Any, code: str | None = None) -> dict[str, dict[str, Any]]:
    rows = await fetch_all(conn, """
        SELECT agent, count(DISTINCT run_id) AS runs,
               count(*) FILTER (WHERE kind = 'llm_call') AS llm_calls,
               count(*) FILTER (WHERE kind = 'llm_call' AND status <> 'ok') AS failed,
               count(*) FILTER (WHERE kind = 'fallback') AS fallbacks,
               round(avg(duration_ms) FILTER (WHERE kind = 'llm_call'))::int AS avg_ms,
               coalesce(sum(prompt_tokens), 0)::int + coalesce(sum(completion_tokens), 0)::int AS tokens
        FROM ops.agent_steps
        WHERE agent IS NOT NULL AND started_at > now() - make_interval(days => :days)
          AND (CAST(:code AS text) IS NULL OR agent = :code)
        GROUP BY agent""", {"code": code, "days": STATS_DAYS})
    return {r["agent"]: r for r in rows}


def _empty_stats() -> dict[str, Any]:
    return {"runs": 0, "llm_calls": 0, "failed": 0, "fallbacks": 0, "avg_ms": None, "tokens": 0}


def _prompt_out(p: catalog.PromptDef, cur: dict[str, Any] | None, full: bool) -> dict[str, Any]:
    edited = bool(cur and cur["body"] is not None) and p.editable
    out: dict[str, Any] = {
        "key": p.key, "agent": p.agent, "task": p.task, "title": p.title, "used_for": p.used_for,
        "editable": p.editable, "note": p.note, "edited": edited,
        "version": cur["version"] if cur else 0,
        "updated_at": cur["created_at"] if cur else None, "updated_by": cur["by_name"] if cur else None,
        "change_note": cur["note"] if cur else "",
    }
    if full:
        default = catalog.default(p.key)
        out["default"] = default.strip()
        out["text"] = cur["body"] if edited and cur else default.strip()
    return out


def _skill_out(r: dict[str, Any]) -> dict[str, Any]:
    return {"id": str(r["id"]), "name": r["name"], "body": r["body"], "enabled": r["enabled"],
            "updated_at": r["updated_at"], "updated_by": r.get("by_name")}


async def _llm(conn: Any) -> dict[str, Any]:
    row = await fetch_one(conn, "SELECT provider, llm_review, review_agents FROM ops.llm_settings")
    return dict(row) if row else {"provider": None, "llm_review": False, "review_agents": {}}


def _review_out(code: str, llm: dict[str, Any]) -> dict[str, Any]:
    """US-268: whether this agent reviews, the Admin's choice (None = default) and the tenant switch."""
    reviews = code in llm_review.REVIEW_AGENTS
    return {"reviews": reviews, "on": reviews and llm_review.agent_on(llm, code),
            "choice": llm_review.agent_choice(llm, code) if reviews else None,
            "default_on": reviews and llm_review.agent_default_on(code),
            "switch_on": llm_review.review_on(llm)}


def _agent_out(a: catalog.Agent, llm: dict[str, Any]) -> dict[str, Any]:
    review = _review_out(a.code, llm)
    return {"code": a.code, "name": a.name, "purpose": a.purpose, "role": a.role, "tasks": list(a.tasks),
            "review_off": review["reviews"] and not review["on"], "review": review}


@router.get("")
async def list_agents(principal: Principal = Depends(require(ADMIN))) -> dict[str, Any]:
    async with tenant_tx(principal.tenant_id) as conn:
        cur = await _current(conn)
        stats = await _stats(conn)
        skills = await fetch_all(conn, "SELECT agent, count(*) AS n, count(*) FILTER (WHERE enabled) AS on_n "
                                       "FROM ops.agent_skills GROUP BY agent")
        llm = await _llm(conn)
    by_agent = {r["agent"]: r for r in skills}
    items = []
    for a in catalog.AGENTS:
        ps = catalog.prompts_for(a.code)
        edited = sum(1 for p in ps if _prompt_out(p, cur.get(p.key), False)["edited"])
        items.append({**_agent_out(a, llm), "prompts": len(ps), "edited": edited,
                      "skills": int(by_agent.get(a.code, {}).get("n") or 0),
                      "skills_on": int(by_agent.get(a.code, {}).get("on_n") or 0),
                      "stats": stats.get(a.code) or _empty_stats()})
    shared = [_prompt_out(p, cur.get(p.key), True) for p in catalog.prompts_for("shared")]
    return {"items": items, "shared": shared, "stats_days": STATS_DAYS,
            "limits": {"prompt": prompts.PROMPT_MAX, "skill": prompts.SKILL_MAX, "skills_on": prompts.SKILLS_ON_MAX}}


async def _skills(conn: Any, code: str) -> list[dict[str, Any]]:
    rows = await fetch_all(conn, """
        SELECT s.*, u.display_name AS by_name FROM ops.agent_skills s LEFT JOIN iam.users u ON u.id = s.updated_by
        WHERE s.agent = :a ORDER BY s.position, lower(s.name)""", {"a": code})
    return [_skill_out(r) for r in rows]


@router.get("/{code}")
async def get_agent(code: str, principal: Principal = Depends(require(ADMIN))) -> dict[str, Any]:
    a = _agent(code)
    async with tenant_tx(principal.tenant_id) as conn:
        cur = await _current(conn)
        stats = (await _stats(conn, a.code)).get(a.code) or _empty_stats()
        skills = await _skills(conn, a.code)
        llm = await _llm(conn)
    return {**_agent_out(a, llm), "inputs": a.inputs, "output": a.output, "guard": a.guard,
            "knowledge": [{"label": k.label, "href": k.href} for k in a.knowledge],
            "prompts": [_prompt_out(p, cur.get(p.key), True) for p in catalog.prompts_for(a.code)],
            "shared": [_prompt_out(p, cur.get(p.key), True) for p in catalog.prompts_for("shared")],
            "skills": skills, "stats": stats, "stats_days": STATS_DAYS,
            "limits": {"prompt": prompts.PROMPT_MAX, "skill": prompts.SKILL_MAX, "skills_on": prompts.SKILLS_ON_MAX}}


async def _save_version(request: Request, principal: Principal, code: str, key: str, body: str | None,
                        note: str) -> dict[str, Any]:
    p = _prompt(code, key)
    async with tenant_tx(principal.tenant_id) as conn:
        await conn.execute(text("SELECT pg_advisory_xact_lock(hashtext(:k))"),
                           {"k": f"{principal.tenant_id}:{key}"})
        last = await fetch_one(conn, "SELECT version, body FROM ops.agent_prompt_versions WHERE prompt_key = :k "
                                     "ORDER BY version DESC LIMIT 1", {"k": key})
        before = last["body"] if last else None
        if body is not None and body.strip() == catalog.default(key).strip():
            body = None  # the default's own text: store it as "default", so a later code change still reaches it
        if (before is None and body is None) or (before is not None and body == before):
            cur = await _current(conn)
            return _prompt_out(p, cur.get(key), True)
        version = (last["version"] if last else 0) + 1
        await conn.execute(text(
            "INSERT INTO ops.agent_prompt_versions (id, tenant_id, prompt_key, version, body, note, created_by) "
            "VALUES (:id, :t, :k, :v, :b, :n, CAST(:u AS uuid))"),
            {"id": new_id(), "t": principal.tenant_id, "k": key, "v": version, "b": body, "n": note,
             "u": principal.user_id})
        await audit.record(conn, tenant_id=principal.tenant_id, action="agent_prompt.changed",
                           object_type="agent_prompt", object_id=key, outcome="success",
                           actor_id=principal.user_id, correlation_id=_rid(request),
                           details={"agent": p.agent, "version": version, "reset": body is None,
                                    "chars": len(body or ""), "note": note})
        cur = await _current(conn)
    prompts.invalidate(principal.tenant_id)
    return _prompt_out(p, cur.get(key), True)


@router.put("/{code}/review")
async def put_review(code: str, body: ReviewIn, request: Request,
                     principal: Principal = Depends(require(ADMIN))) -> dict[str, Any]:
    """US-268: LLM review on or off for one agent (``on: null`` follows the deployment default)."""
    a = _agent(code)
    if a.code not in llm_review.REVIEW_AGENTS:
        raise TelaError(ErrorCode.VALIDATION_ERROR, "This agent doesn't send its work for LLM review.")
    async with tenant_tx(principal.tenant_id) as conn:
        row = await fetch_one(conn, "SELECT review_agents FROM ops.llm_settings FOR UPDATE")
        if row is None:
            raise TelaError(ErrorCode.VALIDATION_ERROR, "Choose an AI provider in AI settings first.")
        before = llm_review.agent_choice(row, a.code)
        if before != body.on:
            await conn.execute(text(
                "UPDATE ops.llm_settings SET review_agents = CASE WHEN CAST(:on AS boolean) IS NULL "
                "THEN review_agents - :a ELSE review_agents || jsonb_build_object(:a, CAST(:on AS boolean)) END, "
                "updated_by = CAST(:u AS uuid), updated_at = now()"),
                {"a": a.code, "on": body.on, "u": principal.user_id})
            await audit.record(conn, tenant_id=principal.tenant_id, action="agent_review.changed",
                               object_type="agent", object_id=a.code, outcome="success",
                               actor_id=principal.user_id, correlation_id=_rid(request),
                               details={"before": before, "after": body.on,
                                        "default_on": llm_review.agent_default_on(a.code)})
        llm = await _llm(conn)
    return _review_out(a.code, llm)


@router.put("/{code}/prompts/{key}")
async def put_prompt(code: str, key: str, body: PromptIn, request: Request,
                     principal: Principal = Depends(require(ADMIN))) -> dict[str, Any]:
    if not body.text.strip():
        raise TelaError(ErrorCode.VALIDATION_ERROR, "A prompt can't be empty; use Reset to go back to the default.")
    return await _save_version(request, principal, code.upper() if code != "shared" else code, key, body.text,
                               body.note)


@router.delete("/{code}/prompts/{key}")
async def reset_prompt(code: str, key: str, request: Request,
                       principal: Principal = Depends(require(ADMIN))) -> dict[str, Any]:
    return await _save_version(request, principal, code.upper() if code != "shared" else code, key, None,
                               "Back to the default")


@router.get("/{code}/prompts/{key}/versions")
async def prompt_versions(code: str, key: str, principal: Principal = Depends(require(ADMIN))) -> dict[str, Any]:
    p = _prompt(code.upper() if code != "shared" else code, key)
    async with tenant_tx(principal.tenant_id) as conn:
        rows = await fetch_all(conn, """
            SELECT v.version, v.body, v.note, v.created_at, u.display_name AS by_name
            FROM ops.agent_prompt_versions v LEFT JOIN iam.users u ON u.id = v.created_by
            WHERE v.prompt_key = :k ORDER BY v.version DESC LIMIT 100""", {"k": key})
    return {"key": p.key, "default": catalog.default(p.key).strip(),
            "items": [{"version": r["version"], "text": r["body"], "is_default": r["body"] is None,
                       "note": r["note"], "created_at": r["created_at"], "by": r["by_name"]} for r in rows]}


async def _check_skills_size(conn: Any, code: str, skip_id: str | None, adding: int) -> None:
    row = await fetch_one(conn, "SELECT coalesce(sum(length(name) + length(body)), 0)::int AS n FROM "
                                "ops.agent_skills WHERE agent = :a AND enabled AND (CAST(:id AS uuid) IS NULL OR "
                                "id <> CAST(:id AS uuid))", {"a": code, "id": skip_id})
    if int(row["n"] if row else 0) + adding > prompts.SKILLS_ON_MAX:
        raise TelaError(ErrorCode.VALIDATION_ERROR,
                        f"This agent's skills that are on would pass {prompts.SKILLS_ON_MAX:,} characters. "
                        "Shorten a skill or turn one off.")


async def _audit_skill(conn: Any, request: Request, principal: Principal, code: str, skill_id: str, change: str,
                       name: str) -> None:
    await audit.record(conn, tenant_id=principal.tenant_id, action="agent_skill.changed", object_type="agent_skill",
                       object_id=skill_id, outcome="success", actor_id=principal.user_id,
                       correlation_id=_rid(request), details={"agent": code, "change": change, "name": name})


def _name_taken(exc: Exception) -> bool:
    return "ux_agent_skills_name" in str(exc)


@router.post("/{code}/skills", status_code=201)
async def add_skill(code: str, body: SkillIn, request: Request,
                    principal: Principal = Depends(require(ADMIN))) -> dict[str, Any]:
    a = _agent(code)
    sid = new_id()
    try:
        async with tenant_tx(principal.tenant_id) as conn:
            if body.enabled:
                await _check_skills_size(conn, a.code, None, len(body.name) + len(body.body))
            pos = await fetch_one(conn, "SELECT coalesce(max(position), 0) + 1 AS p FROM ops.agent_skills "
                                        "WHERE agent = :a", {"a": a.code})
            await conn.execute(text(
                "INSERT INTO ops.agent_skills (id, tenant_id, agent, name, body, enabled, position, created_by, "
                "updated_by) VALUES (:id, :t, :a, :n, :b, :e, :p, CAST(:u AS uuid), CAST(:u AS uuid))"),
                {"id": sid, "t": principal.tenant_id, "a": a.code, "n": body.name.strip(), "b": body.body,
                 "e": body.enabled, "p": pos["p"] if pos else 1, "u": principal.user_id})
            await _audit_skill(conn, request, principal, a.code, str(sid), "added", body.name.strip())
            skills = await _skills(conn, a.code)
    except Exception as exc:
        if _name_taken(exc):
            raise TelaError(ErrorCode.CONFLICT, "This agent already has a skill with that name.") from exc
        raise
    prompts.invalidate(principal.tenant_id)
    return next(s for s in skills if s["id"] == str(sid))


def _sid(skill_id: str) -> str:
    try:
        return str(UUID(skill_id))
    except ValueError as exc:
        raise TelaError(ErrorCode.NOT_FOUND, "That skill was not found.") from exc


@router.put("/{code}/skills/{skill_id}")
async def put_skill(code: str, skill_id: str, body: SkillIn, request: Request,
                    principal: Principal = Depends(require(ADMIN))) -> dict[str, Any]:
    a, sid = _agent(code), _sid(skill_id)
    try:
        async with tenant_tx(principal.tenant_id) as conn:
            if body.enabled:
                await _check_skills_size(conn, a.code, sid, len(body.name) + len(body.body))
            done = await conn.execute(text(
                "UPDATE ops.agent_skills SET name = :n, body = :b, enabled = :e, updated_by = CAST(:u AS uuid), "
                "updated_at = now() WHERE id = CAST(:id AS uuid) AND agent = :a"),
                {"n": body.name.strip(), "b": body.body, "e": body.enabled, "u": principal.user_id, "id": sid,
                 "a": a.code})
            if done.rowcount == 0:
                raise TelaError(ErrorCode.NOT_FOUND, "That skill was not found.")
            await _audit_skill(conn, request, principal, a.code, sid, "edited", body.name.strip())
            skills = await _skills(conn, a.code)
    except TelaError:
        raise
    except Exception as exc:
        if _name_taken(exc):
            raise TelaError(ErrorCode.CONFLICT, "This agent already has a skill with that name.") from exc
        raise
    prompts.invalidate(principal.tenant_id)
    return next(s for s in skills if s["id"] == sid)


@router.delete("/{code}/skills/{skill_id}", status_code=204)
async def delete_skill(code: str, skill_id: str, request: Request,
                       principal: Principal = Depends(require(ADMIN))) -> None:
    a, sid = _agent(code), _sid(skill_id)
    async with tenant_tx(principal.tenant_id) as conn:
        row = await fetch_one(conn, "DELETE FROM ops.agent_skills WHERE id = CAST(:id AS uuid) AND agent = :a "
                                    "RETURNING name", {"id": sid, "a": a.code})
        if row is None:
            raise TelaError(ErrorCode.NOT_FOUND, "That skill was not found.")
        await _audit_skill(conn, request, principal, a.code, sid, "deleted", row["name"])
    prompts.invalidate(principal.tenant_id)


# --------------------------------------------------------------------------- Try it (US-267)

TRY_KINDS = {"AG-01": "ask", "AG-02": "ask", "AG-03": "ask", "AG-10": "sql"}
TRY_PING_S = 10.0


class TryPrompt(BaseModel):
    key: str
    text: str = Field(min_length=1, max_length=prompts.PROMPT_MAX)


class TrySkill(BaseModel):
    id: str | None = None  # the saved skill this draft replaces; None: a new one
    name: str = Field(min_length=1, max_length=120)
    body: str = Field(min_length=1, max_length=prompts.SKILL_MAX)
    enabled: bool = True


class TryIn(BaseModel):
    question: str = Field(min_length=1, max_length=500)
    connection_id: str | None = None
    prompt: TryPrompt | None = None
    skill: TrySkill | None = None


def _sse(event: str, data: Any) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


async def _try_ask(request: Request, principal: Principal, src: Any, question: str) -> dict[str, Any]:
    from tela_api.ask.orchestrator import Orchestrator
    from tela_api.ask.rewrite import describe

    orch = Orchestrator(request.app.state.query_service, request.app.state.gateway)
    plan, used, _ = await orch.plan_for(principal, question, src, {})
    out: dict[str, Any] = {"status": plan.status, "planner": used, "confidence": round(float(plan.confidence), 3),
                           "reason": plan.reason, "reading": None, "spec": plan.spec, "clarify": None}
    if plan.spec is not None:
        try:
            out["reading"] = describe(plan.spec, src.snap)
        except Exception:  # noqa: BLE001 - the words are a help, the spec is shown anyway
            out["reading"] = None
    if plan.status == "clarify" and plan.question:
        out["clarify"] = {"question": plan.question.get("question"),
                          "options": [o.get("label") for o in plan.question.get("options") or []]}
    return out


async def _try_sql(request: Request, principal: Principal, src: Any, question: str) -> dict[str, Any]:
    from tela_api.content.custom_sql_api import DraftIn, _draft

    try:
        got = await _draft(DraftIn(connection_id=str(src.connection["id"]), question=question), request, principal)
    except TelaError as exc:
        return {"status": "unsupported", "planner": "rules", "reason": exc.message, "sql": None}
    return {"status": "ok", "planner": got.get("source"), "sql": got.get("sql"), "reason": got.get("explanation")}


async def _try_side(request: Request, principal: Principal, kind: str, src: Any, question: str,
                    draft: prompts.Draft | None) -> dict[str, Any]:
    from tela_api.llm import trace

    began = time.monotonic()
    with prompts.trying(draft), prompts.recording() as calls, trace.paused():
        try:
            out = await (_try_ask if kind == "ask" else _try_sql)(request, principal, src, question)
        except Exception as exc:  # noqa: BLE001 - one side failing is shown, not raised
            log.warning("agents.try_failed", error_class=type(exc).__name__)
            out = {"status": "error", "reason": "This run failed before it finished."}
    return {**out, "calls": calls, "took_ms": int((time.monotonic() - began) * 1000)}


@router.post("/{code}/try")
async def try_it(code: str, body: TryIn, request: Request,
                 principal: Principal = Depends(require(ADMIN))) -> StreamingResponse:
    """Run one question with the saved prompts and skills ("before"), then with the unsaved draft ("after"), shown side
    by side; nothing is saved and the runs stay out of the agent run log. Server-sent events: ``side`` (one per side as
    it finishes), then ``complete``; a comment every 10 s keeps the connection open while a slow model works."""
    a = _agent(code)
    kind = TRY_KINDS.get(a.code)
    if kind is None:
        raise TelaError(ErrorCode.VALIDATION_ERROR, "Try it runs Ask planning (AG-01, AG-02, AG-03) and SQL drafting "
                        "(AG-10). This agent's work can't be tried here yet.")
    if getattr(request.app.state, "gateway", None) is None:
        raise TelaError(ErrorCode.VALIDATION_ERROR, "No language model is set up, so there is nothing to try.")
    if body.prompt is None and body.skill is None:
        raise TelaError(ErrorCode.VALIDATION_ERROR, "Change a prompt or a skill to try it.")
    draft = prompts.Draft(agent=a.code)
    if body.prompt is not None:
        _prompt(a.code, body.prompt.key)
        draft.prompts[body.prompt.key] = body.prompt.text
    from tela_api.ask.orchestrator import Orchestrator

    async with tenant_tx(principal.tenant_id) as conn:
        if body.skill is not None:
            saved = await fetch_all(conn, "SELECT id, name, body FROM ops.agent_skills WHERE agent = :a AND enabled "
                                          "ORDER BY position, lower(name)", {"a": a.code})
            skills = [(r["name"], r["body"]) for r in saved if str(r["id"]) != (body.skill.id or "")]
            if body.skill.enabled:
                skills.append((body.skill.name.strip(), body.skill.body))
            draft.skills = skills
    sources = await Orchestrator(request.app.state.query_service, request.app.state.gateway).sources(
        principal, body.connection_id)
    if not sources:
        raise TelaError(ErrorCode.VALIDATION_ERROR, "No connection with an approved model is available to try on.")
    src = sources[0]
    question = body.question.strip()

    async def events() -> AsyncIterator[str]:
        yield _sse("start", {"connection": {"id": str(src.connection["id"]), "name": src.connection.get("name")},
                             "kind": kind})
        # One side after the other: a local model answers one call at a time, so two at once made the first wait
        # behind the second and time out (EXEC-216 live check).
        for side, d in (("before", None), ("after", draft)):
            task = asyncio.create_task(_try_side(request, principal, kind, src, question, d))
            try:
                while not task.done():
                    done, _ = await asyncio.wait({task}, timeout=TRY_PING_S)
                    if not done:
                        yield ": ping\n\n"
            finally:
                if not task.done():
                    task.cancel()
            yield _sse("side", {"side": side, **task.result()})
        yield _sse("complete", {})

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})
