"""Custom SQL: check, run, draft with chat and save as widget (API-032; FEAT-031; US-079, US-080).

* ``/check`` runs the validation pipeline and returns every step; a pass returns a check token bound to the
  statement hash for five minutes. Failed safety steps are security audit events.
* ``/run`` needs that token and runs through the single scoped path (scope injected, verified, capped and
  signed; the runner re-verifies). It returns the first 100 rows, the row count, Truncated and a suggested visual.
* ``/draft`` (AG-10) proposes SQL from the approved schema only and never runs it. Without a usable model the
  draft comes from the rules planner, compiled to readable SQL.
* Saving as a widget needs a passing check in this session; the widget carries the Custom SQL badge until it is
  certified. A query without ``:page_filters`` cannot follow page filters (MSG-017).
* Rate limit: 20 calls a minute per person across check, run and draft.
"""

from __future__ import annotations

import json
import re
import time
from collections import defaultdict, deque
from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlglot import exp

from tela_api.ask.orchestrator import Orchestrator
from tela_api.ask.planner import plan_question
from tela_api.audit import service as audit
from tela_api.content.common import guard_visual, visual_options
from tela_api.content.fields import model_context
from tela_api.content.reports import first_section, mutate, place_widget
from tela_api.content.sql_context import AG10_SYSTEM, SqlDraftOut, schema_context
from tela_api.core.db import fetch_all, fetch_one, tenant_tx
from tela_api.identity.deps import require
from tela_api.identity.permissions import Perm
from tela_api.identity.principal import Principal
from tela_api.llm import trace
from tela_api.llm.client import AgentFailed, run_agent
from tela_api.query import custom_sql
from tela_api.query.compiler import DIALECTS, SqlCompiler
from tela_api.query.service import QueryContext, QueryService
from tela_api.query.viz import visualise
from tela_common.errors import ErrorCode, TelaError
from tela_common.ids import new_id

router = APIRouter(prefix="/api/v1", tags=["custom-sql"])
RATE_PER_MIN = 20
PREVIEW_ROWS = 100
_calls: dict[str, deque[float]] = defaultdict(deque)


def _service(request: Request) -> QueryService:
    service: QueryService = request.app.state.query_service
    return service


def _rid(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


def _key(request: Request) -> bytes:
    settings = request.app.state.settings
    key = settings.service_signing_key or ""
    if not key:
        key = getattr(request.app.state, "_custom_sql_key", "")
        if not key:
            import secrets

            key = secrets.token_hex(32)
            request.app.state._custom_sql_key = key
    return f"custom-sql:{key}".encode()


def _admit(principal: Principal) -> None:
    now = time.monotonic()
    calls = _calls[principal.user_id]
    while calls and now - calls[0] > 60:
        calls.popleft()
    if len(calls) >= RATE_PER_MIN:
        raise TelaError(ErrorCode.RATE_LIMITED, "Too many custom SQL checks in a minute. Try again shortly.")
    calls.append(now)


class SqlIn(BaseModel):
    connection_id: str
    sql: str = Field(min_length=1, max_length=20000)


class RunIn(SqlIn):
    check_token: str = Field(min_length=10, max_length=2000)
    page_filters: list[dict[str, Any]] = Field(default_factory=list, max_length=20)


class DraftIn(BaseModel):
    connection_id: str
    question: str = Field(min_length=1, max_length=1000)


class SaveIn(RunIn):
    title: str = Field(min_length=1, max_length=200)
    visual: str = Field(default="table", max_length=40)
    follow_page_filters: bool = True
    drafted_by_ai: bool = False
    report_id: str | None = None
    section_id: str | None = None


async def _check(request: Request, principal: Principal, connection_id: str, sql: str) -> tuple[Any, Any, Any]:
    try:
        return await _service(request).custom_sql_check(
            sql, connection_id=connection_id, principal=principal,
            ctx=QueryContext(surface="custom_sql_run", request_id=_rid(request)))
    except custom_sql.CheckFailed as failed:
        security = failed.error.code in custom_sql.SECURITY_CODES
        async with tenant_tx(principal.tenant_id) as conn:
            await audit.record(conn, tenant_id=principal.tenant_id, action="custom_sql.rejected",
                               object_type="connection", object_id=connection_id, outcome="deny",
                               actor_id=principal.user_id, correlation_id=_rid(request),
                               severity="security" if security else "normal",
                               details={"step": failed.step, "code": str(failed.error.code),
                                        "reason": failed.error.details.get("reason")})
        raise TelaError(failed.error.code, failed.error.message,
                        {"step": failed.step, "checks": failed.checks}) from failed


@router.post("/custom-sql/check")
async def check(body: SqlIn, request: Request,
                principal: Principal = Depends(require(Perm.CUSTOMSQL_USE))) -> dict[str, Any]:
    _admit(principal)
    prepared, snap, _ = await _check(request, principal, body.connection_id, body.sql)
    token = custom_sql.issue_token(_key(request), user_id=principal.user_id, connection_id=body.connection_id,
                                   ast_hash=prepared.ast_hash)
    return {"ok": True, "checks": prepared.checks, "check_token": token, "warnings": prepared.warnings,
            "follows_page_filters": prepared.has_placeholder, "model_version": snap.version_no,
            "expires_in_s": custom_sql.TOKEN_TTL_S}


async def _run(request: Request, principal: Principal, body: RunIn) -> tuple[dict[str, Any], Any, Any]:
    prepared, snap, _ = await _check(request, principal, body.connection_id, body.sql)
    custom_sql.verify_token(_key(request), body.check_token, user_id=principal.user_id,
                            connection_id=body.connection_id, ast_hash=prepared.ast_hash)
    started = time.monotonic()
    result = await _service(request).execute_custom(
        body.sql, connection_id=body.connection_id, model_version_id=snap.model_version_id, principal=principal,
        ctx=QueryContext(surface="custom_sql_run", request_id=_rid(request)), page_filters=body.page_filters)
    result["duration_ms"] = int((time.monotonic() - started) * 1000)
    return result, prepared, snap


@router.post("/custom-sql/run")
async def run(body: RunIn, request: Request,
              principal: Principal = Depends(require(Perm.CUSTOMSQL_USE))) -> dict[str, Any]:
    _admit(principal)
    result, _, snap = await _run(request, principal, body)
    viz = visualise(result, snap)
    return {**{k: result[k] for k in ("columns", "row_count", "truncated", "as_of", "duration_ms", "notes",
                                      "filters_applied", "filters_not_applied", "warnings", "scope")},
            "rows": result["rows"][:PREVIEW_ROWS], "badge": "custom_sql", "suggested_visual": viz["visual"],
            "visuals": visual_options(result, snap) if result["rows"] else None}


# --------------------------------------------------------------------------- AG-10 drafter


def _literal(v: Any) -> exp.Expression:
    if isinstance(v, bool):
        return exp.Boolean(this=v)
    if isinstance(v, int | float):
        return exp.Literal.number(v)
    return exp.Literal.string(str(v))


async def _rules_draft(request: Request, principal: Principal, connection_id: str, question: str) -> str | None:
    """Plan with the certified rules planner, then compile the spec to readable SQL with :page_filters."""
    orch = Orchestrator(_service(request), None)
    sources = await orch.sources(principal, connection_id)
    if not sources:
        return None
    src = sources[0]
    plan = plan_question(question, src.snap, visible_measures=src.measures, visible_dims=src.dims, values={})
    if plan.status != "ok" or plan.spec is None:
        return None
    spec = {"model_version_id": src.snap.model_version_id, "connection_id": connection_id,
            **{k: v for k, v in plan.spec.items() if k != "visual"}}
    pr = await _service(request).prepare(spec, principal, QueryContext(surface="custom_sql_run"))
    dialect = DIALECTS.get(src.connection["engine"], "postgres")
    cq = SqlCompiler(dialect).compile(pr.vs, as_of=pr.as_of_date)
    tree = cq.tree.transform(lambda n: _literal(cq.params.values.get(n.name)) if isinstance(n, exp.Placeholder)
                             else n)
    select = tree if isinstance(tree, exp.Select) else next(iter(tree.find_all(exp.Select)))
    target = next(iter(s for s in tree.find_all(exp.Select) if s.args.get("from")), select)
    target.where(exp.Anonymous(this=custom_sql.SENTINEL), append=True, copy=False)
    sql = tree.sql(dialect=dialect, pretty=True)
    return re.sub(rf"{custom_sql.SENTINEL}\(\)", ":page_filters", sql, flags=re.I)


@router.post("/custom-sql/draft")
async def draft(body: DraftIn, request: Request,
                principal: Principal = Depends(require(Perm.CUSTOMSQL_USE))) -> dict[str, Any]:
    _admit(principal)
    async with trace.agent_run(principal.tenant_id, principal.user_id, "custom_sql", title=body.question,
                               object_ref=f"connection:{body.connection_id}", agent="AG-10"):
        return await _draft(body, request, principal)


async def _draft(body: DraftIn, request: Request, principal: Principal) -> dict[str, Any]:
    sql, source, note = None, "rules", None
    gateway = getattr(request.app.state, "gateway", None)
    if gateway is not None:
        async with tenant_tx(principal.tenant_id) as conn:
            try:
                snap, hidden = await model_context(conn, principal, body.connection_id)
            except TelaError:
                snap = None
        if snap is not None:
            try:
                out = await run_agent(gateway, SqlDraftOut, tenant_id=principal.tenant_id, user_id=principal.user_id,
                                      agent="AG-10", task="sql_draft",
                                      system=AG10_SYSTEM + "\n\n" + schema_context(snap, hidden, body.question,
                                                                                    dialect=DIALECTS.get(snap.engine)),
                                      user=body.question)
                await _service(request).custom_sql_check(out.sql, connection_id=body.connection_id,
                                                         principal=principal,
                                                         ctx=QueryContext(surface="custom_sql_run"))
                sql, source, note = out.sql, "llm", out.explanation or None
                await trace.step("validate", "The drafted SQL passed the safety check", agent="AG-10")
            except (custom_sql.CheckFailed, TelaError) as exc:
                await trace.step("validate", "The drafted SQL failed the safety check", agent="AG-10",
                                 status="failed", detail={"error": str(exc)[:500]})
                sql = None
            except AgentFailed:
                sql = None
    if sql is None:
        sql = await _rules_draft(request, principal, body.connection_id, body.question)
        source = "rules"
        await trace.step("rule", "Drafted from a certified measure (rules)" if sql else "No rules draft either",
                         agent="AG-10", status="ok" if sql else "failed")
    if sql is None:
        trace.set_outcome("refused")
        raise TelaError(ErrorCode.VALIDATION_ERROR, "I couldn't draft SQL for that. Name a certified measure, for "
                        "example \"open projects by region\".")
    async with tenant_tx(principal.tenant_id) as conn:
        await audit.record(conn, tenant_id=principal.tenant_id, action="custom_sql.drafted", object_type="connection",
                           object_id=body.connection_id, outcome="success", actor_id=principal.user_id,
                           correlation_id=_rid(request), details={"source": source})
    return {"sql": sql, "source": source, "explanation": note, "drafted_by_ai": source == "llm",
            "note": "This is a draft. Check and run it before saving; it is never run automatically."}


# --------------------------------------------------------------------------- definitions and widgets


def widget_spec(definition_id: str, prepared: Any, snap: Any, sql: str, follow: bool) -> dict[str, Any]:
    return {"kind": "custom_sql", "custom_sql_id": definition_id, "sql": custom_sql.statement_text(sql),
            "dialect": prepared.dialect, "connection_id": snap.connection_id,
            "model_version_id": snap.model_version_id, "follow_page_filters": follow and prepared.has_placeholder,
            "has_page_filters": prepared.has_placeholder,
            "entities": sorted({e.key for e in prepared.aliases.values()})}


def definition_out(row: dict[str, Any]) -> dict[str, Any]:
    return {"id": str(row["id"]), "title": row["title"], "sql": row["sql_text"], "dialect": row["dialect"],
            "connection_id": str(row["connection_id"]), "model_version_id": str(row["model_version_id"]),
            "follows_page_filters": row["has_page_filters"], "drafted_by_ai": row["drafted_by_ai"],
            "cert_status": row["cert_status"], "badge": "certified" if row["cert_status"] == "certified"
            else "custom_sql", "last_check": row["last_check"], "updated_at": row["updated_at"]}


@router.post("/widgets/custom-sql", status_code=201)
async def save_widget(body: SaveIn, request: Request,
                      principal: Principal = Depends(require(Perm.CUSTOMSQL_USE))) -> dict[str, Any]:
    if not principal.has(Perm.WIDGETS_CREATE):
        raise TelaError(ErrorCode.FORBIDDEN, "Saving widgets needs Creator access.")
    _admit(principal)
    result, prepared, snap = await _run(request, principal, body)
    guard_visual(result, snap, body.visual)
    did, wid = new_id(), new_id()
    warnings = list(prepared.warnings)
    spec = widget_spec(did, prepared, snap, body.sql, body.follow_page_filters)
    last_check = {"ok": True, "checks": prepared.checks, "row_count": result["row_count"],
                  "duration_ms": result["duration_ms"], "truncated": result["truncated"]}
    async with tenant_tx(principal.tenant_id) as conn:
        await conn.execute(
            text("INSERT INTO content.custom_sql_definitions (id, tenant_id, owner_id, connection_id, "
                 "model_version_id, "
                 "title, dialect, sql_text, ast_hash, has_page_filters, last_check, drafted_by_ai) VALUES (:id, :t, "
                 ":u, :c, :v, :title, :d, :sql, :h, :pf, CAST(:lc AS jsonb), :ai)"),
            {"id": did, "t": principal.tenant_id, "u": principal.user_id, "c": body.connection_id,
             "v": snap.model_version_id, "title": body.title.strip(), "d": prepared.dialect,
             "sql": custom_sql.statement_text(body.sql), "h": prepared.ast_hash, "pf": prepared.has_placeholder,
             "lc": json.dumps(last_check, default=str), "ai": body.drafted_by_ai})
        await conn.execute(
            text("INSERT INTO content.widgets (id, tenant_id, title, spec, visual, follow_page_filters, "
                 "model_version_id, "
                 "connection_id, certification_state, owner_id, source_kind, custom_sql_id, "
                 "definition_refs, created_by) "
                 "VALUES (:id, :t, :title, CAST(:s AS jsonb), :v, :f, :mv, :c, 'custom_sql', :u, 'custom_sql', :d, "
                 "CAST(:refs AS jsonb), :u)"),
            {"id": wid, "t": principal.tenant_id, "title": body.title.strip(), "s": json.dumps(spec),
             "v": body.visual, "f": spec["follow_page_filters"], "mv": snap.model_version_id,
             "c": body.connection_id, "u": principal.user_id, "d": did,
             "refs": json.dumps([{"kind": "custom_sql", "id": did}])})
        await audit.record(conn, tenant_id=principal.tenant_id, action="custom_sql.saved", object_type="widget",
                           object_id=wid, outcome="success", actor_id=principal.user_id, correlation_id=_rid(request),
                           details={"custom_sql_id": did, "drafted_by_ai": body.drafted_by_ai})
    placement = None
    if body.report_id:
        async def fn(conn: Any, report: dict[str, Any]) -> str:
            section = await first_section(conn, report, body.section_id)
            return await place_widget(conn, report, section, wid, body.visual)

        _, item_id = await mutate(principal, body.report_id, source="canvas", summary=f"Added {body.title.strip()}",
                                  fn=fn, request_id=_rid(request))
        placement = {"report_id": body.report_id, "item_id": item_id}
    return {"widget_id": wid, "custom_sql_id": did, "badges": ["custom_sql"], "visual": body.visual,
            "follow_page_filters": spec["follow_page_filters"], "warnings": warnings, "placement": placement}


@router.get("/custom-sql")
async def list_definitions(principal: Principal = Depends(require(Perm.CUSTOMSQL_USE))) -> dict[str, Any]:
    async with tenant_tx(principal.tenant_id) as conn:
        rows = await fetch_all(conn, "SELECT * FROM content.custom_sql_definitions WHERE owner_id = :u AND deleted_at "
                                     "IS NULL ORDER BY updated_at DESC", {"u": principal.user_id})
    return {"items": [definition_out(r) for r in rows]}


async def own_definition(conn: Any, principal: Principal, definition_id: str) -> dict[str, Any]:
    try:
        row = await fetch_one(conn, "SELECT * FROM content.custom_sql_definitions WHERE id = CAST(:id AS uuid) AND "
                                    "deleted_at IS NULL", {"id": definition_id})
    except Exception:  # noqa: BLE001
        row = None
    if row is None or str(row["owner_id"]) != principal.user_id:
        raise TelaError(ErrorCode.NOT_FOUND, "That custom SQL does not exist or is not yours.")
    return row


@router.get("/custom-sql/{definition_id}")
async def get_definition(definition_id: str,
                         principal: Principal = Depends(require(Perm.CUSTOMSQL_USE))) -> dict[str, Any]:
    async with tenant_tx(principal.tenant_id) as conn:
        return definition_out(await own_definition(conn, principal, definition_id))


class EditIn(RunIn):
    title: str | None = Field(default=None, min_length=1, max_length=200)


@router.patch("/custom-sql/{definition_id}")
async def edit_definition(definition_id: str, body: EditIn, request: Request,
                          principal: Principal = Depends(require(Perm.CUSTOMSQL_USE))) -> dict[str, Any]:
    """Change the statement (after a passing check); widgets follow; a pending request is flagged changed."""
    from tela_api.governance.certification import on_item_changed

    _admit(principal)
    async with tenant_tx(principal.tenant_id) as conn:
        row = await own_definition(conn, principal, definition_id)
    if row["cert_status"] in ("certified", "merged"):
        raise TelaError(ErrorCode.CONFLICT, "Certified custom SQL can't be edited. Save a new widget instead.")
    if body.connection_id != str(row["connection_id"]):
        raise TelaError(ErrorCode.VALIDATION_ERROR, "Custom SQL keeps its data source.")
    result, prepared, snap = await _run(request, principal, body)
    async with tenant_tx(principal.tenant_id) as conn:
        await conn.execute(
            text("UPDATE content.custom_sql_definitions SET sql_text = :sql, ast_hash = :h, has_page_filters = :pf, "
                 "model_version_id = :v, title = coalesce(:title, title), last_check = CAST(:lc AS "
                 "jsonb) WHERE id = :id"),
            {"sql": custom_sql.statement_text(body.sql), "h": prepared.ast_hash, "pf": prepared.has_placeholder,
             "v": snap.model_version_id, "title": body.title, "id": row["id"],
             "lc": json.dumps({"ok": True, "checks": prepared.checks, "row_count": result["row_count"]}, default=str)})
        for w in await fetch_all(conn, "SELECT id, follow_page_filters FROM content.widgets WHERE custom_sql_id = :d",
                                 {"d": row["id"]}):
            spec = widget_spec(definition_id, prepared, snap, body.sql, w["follow_page_filters"])
            await conn.execute(text("UPDATE content.widgets SET spec = CAST(:s AS jsonb), model_version_id = :v, "
                                    "follow_page_filters = :f WHERE id = :id"),
                               {"s": json.dumps(spec), "v": snap.model_version_id, "f": spec["follow_page_filters"],
                                "id": w["id"]})
        if prepared.ast_hash != row["ast_hash"]:
            await on_item_changed(conn, principal, "custom_sql", definition_id)
        await audit.record(conn, tenant_id=principal.tenant_id, action="custom_sql.updated",
                           object_type="custom_sql", object_id=definition_id, outcome="success",
                           actor_id=principal.user_id, correlation_id=_rid(request))
        row = await own_definition(conn, principal, definition_id)
    return definition_out(row)
