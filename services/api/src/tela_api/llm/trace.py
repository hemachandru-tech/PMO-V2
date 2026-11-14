"""Agent run log (ENH-024, US-225 to US-228; DEC-25).

A run is one piece of agent work: an Ask turn, a model assistant proposal, a pack mapping suggestion, a
certification rephrase, a custom SQL draft, a report plan or a canvas edit. ``agent_run`` opens it and holds it in a
context variable, so ``step`` can be called from anywhere below without passing it on; a run opened while another is
open joins the outer one (AG-07 inside an Ask turn is part of that turn). ``run_agent`` opens a run of kind
``agent`` when no caller did, so no model call goes unrecorded.

Each step is written when it ends (the Admin screen shows a run while it happens, US-228). An ``llm_call`` step
holds the text the gateway sent after masking and the raw reply, when the tenant keeps LLM text: stored only in
``ops.agent_steps``, never logged (the log redaction rules and TEST-080 are unchanged).

Writing the log never fails or holds up the work: a failed write is counted and logged without its content.
"""

from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

from prometheus_client import Counter
from sqlalchemy import text

from tela_api.core.db import fetch_one, tenant_tx
from tela_common.ids import new_id
from tela_common.logging import get_logger

log = get_logger(__name__)
WRITE_FAILURES = Counter("tela_agent_trace_write_failures_total", "Agent run log writes that failed", ["what"])

TEXT_CAP = 64_000  # characters kept of each prompt message and of the reply (W4)
DETAIL_CAP = 8_000  # characters of a step's JSON detail
DROPPED = frozenset({"rows", "data", "records", "series", "result", "sample", "samples", "values"})
KINDS = frozenset({"rule", "retrieve", "llm_call", "validate", "retry", "fallback", "decision", "query", "error",
                   "done"})
REFUSALS = frozenset({"unsupported_question", "write_refused", "build_not_available", "no_source",
                      "freehand_not_allowed"})

_current: ContextVar[Run | None] = ContextVar("agent_run", default=None)
_paused: ContextVar[bool] = ContextVar("agent_run_paused", default=False)


@dataclass
class Run:
    id: str
    tenant_id: str
    kind: str
    capture_text: bool
    started: float = field(default_factory=time.monotonic)
    agents: list[str] = field(default_factory=list)
    seq: int = 0
    outcome: str | None = None
    llm_calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    fell_back: bool = False

    def next_seq(self) -> int:
        self.seq += 1
        return self.seq

    def joined(self, agent: str | None) -> bool:
        """Note an agent taking part; True when it is new to the run."""
        if agent and agent not in self.agents:
            self.agents.append(agent)
            return True
        return False


def current() -> Run | None:
    return _current.get()


@contextmanager
def paused() -> Iterator[None]:
    """No runs are opened inside (gold-set evaluation plans hundreds of questions)."""
    token = _paused.set(True)
    try:
        yield
    finally:
        _paused.reset(token)


def compact(data: Any) -> dict[str, Any]:
    """A step's detail without result rows or values, and short enough to list."""
    if not isinstance(data, dict):
        return {"value": data} if data is not None else {}
    kept = {k: v for k, v in data.items() if k not in DROPPED and k != "ts"}
    raw = json.dumps(kept, default=str)
    if len(raw) <= DETAIL_CAP:
        out: dict[str, Any] = json.loads(raw)
        return out
    return {"cut": True, "keys": sorted(kept)[:40], "text": raw[:DETAIL_CAP]}


async def _settings(conn: Any) -> bool:
    row = await fetch_one(conn, "SELECT capture_text FROM ops.agent_trace_settings")
    return bool(row["capture_text"]) if row else True


@asynccontextmanager
async def agent_run(tenant_id: str, user_id: str | None, kind: str, *, title: str = "", turn_id: str | None = None,
                    object_ref: str | None = None, agent: str | None = None) -> AsyncIterator[Run | None]:
    """Open a run (or join the one already open). Yields None when the log could not be started."""
    if _paused.get():
        yield None
        return
    outer = _current.get()
    if outer is not None:
        if outer.joined(agent):
            await _write_agents(outer)
        yield outer
        return
    run: Run | None = None
    try:
        async with tenant_tx(tenant_id) as conn:
            run = Run(id=new_id(), tenant_id=tenant_id, kind=kind, capture_text=await _settings(conn))
            run.joined(agent)
            await conn.execute(text(
                "INSERT INTO ops.agent_runs (id, tenant_id, kind, title, agents, turn_id, object_ref, user_id, "
                "capture_text) VALUES (:id, :t, :k, :title, :agents, CAST(:turn AS uuid), :obj, CAST(:u AS uuid), "
                ":cap)"),
                {"id": run.id, "t": tenant_id, "k": kind, "title": title[:500], "agents": run.agents,
                 "turn": turn_id, "obj": object_ref, "u": user_id, "cap": run.capture_text})
    except Exception as exc:  # noqa: BLE001 - the log never stops the work
        WRITE_FAILURES.labels(what="run").inc()
        log.warning("agent_trace.write_failed", what="run", error_class=type(exc).__name__)
        run = None
    token = _current.set(run) if run is not None else None
    try:
        yield run
    except BaseException:
        if run is not None:
            run.outcome = "error"
        raise
    finally:
        if token is not None:
            _current.reset(token)
        if run is not None:
            await _close(run)


async def _write_agents(run: Run) -> None:
    try:
        async with tenant_tx(run.tenant_id) as conn:
            await conn.execute(text("UPDATE ops.agent_runs SET agents = :a WHERE id = :id"),
                               {"a": run.agents, "id": run.id})
    except Exception as exc:  # noqa: BLE001 - the log never stops the work
        WRITE_FAILURES.labels(what="run").inc()
        log.warning("agent_trace.write_failed", what="agents", error_class=type(exc).__name__)


async def _close(run: Run) -> None:
    outcome = run.outcome or "ok"
    if outcome == "ok" and run.fell_back:
        outcome = "fallback"
    try:
        async with tenant_tx(run.tenant_id) as conn:
            await conn.execute(text(
                "UPDATE ops.agent_runs SET outcome = :o, agents = :a, llm_calls = :n, prompt_tokens = :pt, "
                "completion_tokens = :ct, ended_at = now(), duration_ms = :ms WHERE id = :id"),
                {"o": outcome, "a": run.agents, "n": run.llm_calls, "pt": run.prompt_tokens,
                 "ct": run.completion_tokens, "ms": int((time.monotonic() - run.started) * 1000), "id": run.id})
    except Exception as exc:  # noqa: BLE001 - the log never stops the work
        WRITE_FAILURES.labels(what="run").inc()
        log.warning("agent_trace.write_failed", what="close", error_class=type(exc).__name__)


async def set_title(title: str) -> None:
    run = _current.get()
    if run is None or not title:
        return
    try:
        async with tenant_tx(run.tenant_id) as conn:
            await conn.execute(text("UPDATE ops.agent_runs SET title = :t WHERE id = :id"),
                               {"t": title[:500], "id": run.id})
    except Exception as exc:  # noqa: BLE001 - the log never stops the work
        WRITE_FAILURES.labels(what="run").inc()
        log.warning("agent_trace.write_failed", what="title", error_class=type(exc).__name__)


async def set_ask_mode(mode: str) -> None:
    """The Ask answer mode the open run answers in (US-256)."""
    run = _current.get()
    if run is None:
        return
    try:
        async with tenant_tx(run.tenant_id) as conn:
            await conn.execute(text("UPDATE ops.agent_runs SET ask_mode = :m WHERE id = :id"),
                               {"m": mode, "id": run.id})
    except Exception as exc:  # noqa: BLE001 - the log never stops the work
        WRITE_FAILURES.labels(what="run").inc()
        log.warning("agent_trace.write_failed", what="ask_mode", error_class=type(exc).__name__)


def set_outcome(outcome: str) -> None:
    run = _current.get()
    if run is not None:
        run.outcome = outcome


async def step(kind: str, label: str, *, agent: str | None = None, detail: Any = None, status: str = "ok",
               duration_ms: int | None = None, llm: dict[str, Any] | None = None, conn: Any = None) -> None:
    """Record one step of the open run (nothing when no run is open).

    ``llm``: an ``llm_call`` step's task, provider, model, usage and the gateway's ``trace`` (masked messages, raw
    reply), kept only when the run captures text.
    """
    run = _current.get()
    if run is None:
        return
    assert kind in KINDS, kind
    new_agent = run.joined(agent)
    if kind == "fallback":
        run.fell_back = True
    seq = run.next_seq()
    params: dict[str, Any] = {
        "run": run.id, "seq": seq, "t": run.tenant_id, "agent": agent, "kind": kind, "label": label[:300],
        "status": status, "ms": duration_ms, "detail": json.dumps(compact(detail), default=str),
        "task": None, "provider": None, "model": None, "pt": None, "ct": None, "prompt": None, "reply": None,
        "cut": False,
    }
    if llm is not None:
        usage = llm.get("usage") or {}
        run.llm_calls += 1
        run.prompt_tokens += int(usage.get("prompt_tokens") or 0)
        run.completion_tokens += int(usage.get("completion_tokens") or 0)
        params.update(task=llm.get("task"), provider=llm.get("provider"), model=llm.get("model"),
                      pt=usage.get("prompt_tokens"), ct=usage.get("completion_tokens"))
        trace = llm.get("trace") or {}
        if run.capture_text and trace:
            messages, cut = [], bool(trace.get("cut"))
            for m in trace.get("messages") or []:
                content = str(m.get("content", ""))
                cut = cut or len(content) > TEXT_CAP
                messages.append({"role": m.get("role"), "content": content[:TEXT_CAP]})
            reply = trace.get("reply")
            if reply is not None:
                cut = cut or len(str(reply)) > TEXT_CAP
                reply = str(reply)[:TEXT_CAP]
            params.update(prompt=json.dumps(messages), reply=reply, cut=cut)
    if conn is not None:  # inside the caller's transaction (Ask's stage events): no extra round trip
        try:
            async with conn.begin_nested():
                await _insert(conn, params, run if new_agent else None)
        except Exception as exc:  # noqa: BLE001 - the log never stops the work
            WRITE_FAILURES.labels(what="step").inc()
            log.warning("agent_trace.write_failed", what="step", error_class=type(exc).__name__)
        return
    try:
        async with tenant_tx(run.tenant_id) as tx:
            await _insert(tx, params, run if new_agent else None)
    except Exception as exc:  # noqa: BLE001 - the log never stops the work
        WRITE_FAILURES.labels(what="step").inc()
        log.warning("agent_trace.write_failed", what="step", error_class=type(exc).__name__)


async def _insert(conn: Any, params: dict[str, Any], run: Run | None) -> None:
    """Write one step; ``run`` when the step brought a new agent into it."""
    await conn.execute(text(
        "INSERT INTO ops.agent_steps (run_id, seq, tenant_id, agent, kind, label, status, duration_ms, "
        "detail, task, provider, model, prompt_tokens, completion_tokens, prompt_text, reply_text, "
        "text_cut) VALUES (:run, :seq, :t, :agent, :kind, :label, :status, :ms, CAST(:detail AS jsonb), "
        ":task, :provider, :model, :pt, :ct, CAST(:prompt AS jsonb), :reply, :cut)"), params)
    if run is not None:
        await conn.execute(text("UPDATE ops.agent_runs SET agents = :a WHERE id = :id"),
                           {"a": run.agents, "id": run.id})


# Ask's stage events (``Orchestrator.emit``) as steps: event → (agent, kind, label).
ASK_EVENTS: dict[str, tuple[str | None, str, str]] = {
    "intent": ("AG-01", "decision", "Routed the question"),
    "retrieval": ("AG-03", "retrieve", "Retrieved knowledge and candidates"),
    "clarify": ("AG-02", "decision", "Asked a clarifying question"),
    "plan": ("AG-03", "decision", "Planned the query"),
    "compile": (None, "rule", "Compiled the query"),
    "validate": (None, "validate", "Checked the query (allow-list, scope, limits)"),
    "execute": (None, "query", "Ran the query"),
    "viz": ("AG-07", "decision", "Chose the visual"),
    "narrative": ("AG-08", "decision", "Wrote the headline"),
    "error": (None, "error", "Stopped"),
    "complete": (None, "done", "Answered"),
}


async def ask_event(event: str, data: dict[str, Any], conn: Any = None) -> None:
    """Record an Ask stage event as a step and settle the run's outcome from it."""
    agent, kind, label = ASK_EVENTS.get(event, (None, "decision", event))
    status = "failed" if event == "error" else "ok"
    if event == "error":
        code = str(data.get("code") or "")
        set_outcome("refused" if code in REFUSALS else "error")
        label = f"Stopped: {code}" if code else label
    elif event == "clarify":
        set_outcome("clarify")
    elif event == "intent" and data.get("intent"):
        label = f"Routed the question: {data['intent']}"
    elif event == "plan" and data.get("planner"):
        label = f"Planned the query ({data['planner']})"
    await step(kind, label, agent=agent, detail=data, status=status, conn=conn)
