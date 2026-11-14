"""Reports, sections and items (API-024, API-027, API-029; FEAT-024, FEAT-027; US-065, US-069, US-072).

* A report is the Creator's **draft**: every change is saved at once (auto-save), bumps
  ``draft_revision`` (the ETag) and writes an undo-log entry with the whole report state before and
  after. Readers of a published dashboard keep seeing the frozen version until the next publish.
* ``PUT /layout`` needs ``If-Match`` (412 on a stale revision) and is validated on the 12-column grid.
* Undo steps back one change at a time; Undo all returns to the last published state, or to an empty
  report if it was never published. Both are themselves undoable entries in the log.
* Widget edits from the canvas toolbar (rename, chart type, filters, follow page filters) go through the
  chart integrity guard (RULE-008) and are part of the report state, so Undo restores them too.
"""

from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable
from typing import Any, Literal

from fastapi import APIRouter, Depends, Header, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from tela_api.ask.refine import ChipOp, apply_ops
from tela_api.audit import service as audit
from tela_api.content.access import own_report, readable_report
from tela_api.content.common import (
    MSG_FREEHAND_HELD,
    canonical,
    check_controls,
    def_hash,
    default_size,
    guard_visual,
    is_freehand,
    log_change,
    stored_visual,
)
from tela_api.content.layout import Box, compact, place, problems
from tela_api.core.db import fetch_all, fetch_one, tenant_tx
from tela_api.identity.deps import require
from tela_api.identity.permissions import Perm
from tela_api.identity.principal import Principal
from tela_api.model.store import current_version_id, load_snapshot
from tela_api.query.asof import reporting_date_or_none
from tela_api.query.service import QueryContext, QueryService
from tela_chart_spec import VISUALS
from tela_common.errors import ErrorCode, TelaError
from tela_common.ids import new_id
from tela_query_spec.model import ModelSnapshot
from tela_query_spec.spec import MAX_CONTROLS, CardTone, ChartColours, Detail, FigureTone, Filter, ref_key

router = APIRouter(prefix="/api/v1", tags=["reports"])
TAG = re.compile(r"<[^>]*>")
# Report text blocks (US-158): a text note anywhere on the grid, a callout (title + text, info or warning) and a
# footnote (population and data limits, at the end of its section).
TEXT_KINDS = ("text_note", "callout", "footnote")
TEXT_SIZES = {"text_note": (6, 2), "callout": (12, 2), "footnote": (12, 2)}


def _service(request: Request) -> QueryService:
    service: QueryService = request.app.state.query_service
    return service


def _rid(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


def clean_text(value: str) -> str:
    """Text notes are markdown-lite: tags are stripped here and the client renders plain text."""
    return TAG.sub("", value).strip()[:4000]


# --------------------------------------------------------------------------- state snapshots


async def report_state(conn: AsyncConnection, report_id: Any) -> dict[str, Any]:
    r = await fetch_one(conn, "SELECT title, page_filters FROM content.reports WHERE id = :id", {"id": report_id})
    assert r is not None
    sections = await fetch_all(conn, "SELECT id, title, kind, position, collapsed FROM content.report_sections "
                                     "WHERE report_id = :id ORDER BY position", {"id": report_id})
    items = await fetch_all(conn, "SELECT id, section_id, item_kind, widget_id, text_content, text_title, text_tone, "
                                  "grid_x, grid_y, grid_w, grid_h, empty_expected, hidden_for_roles FROM "
                                  "content.report_items "
                                  "WHERE report_id = :id ORDER BY grid_y, grid_x, id", {"id": report_id})
    wids = [str(i["widget_id"]) for i in items if i["widget_id"]]
    widgets = await fetch_all(conn, "SELECT id, title, visual, spec, follow_page_filters, own_filters, "
                                    "certification_state FROM content.widgets WHERE id = ANY(CAST(:w AS uuid[]))",
                              {"w": wids}) if wids else []
    return {
        "title": r["title"], "page_filters": r["page_filters"],
        "sections": [{"id": str(s["id"]), "title": s["title"], "kind": s["kind"], "position": s["position"],
                      "collapsed": s["collapsed"]} for s in sections],
        "items": [{"id": str(i["id"]), "section_id": str(i["section_id"]), "kind": i["item_kind"],
                   "widget_id": str(i["widget_id"]) if i["widget_id"] else None, "text": i["text_content"],
                   **({"text_title": i["text_title"], "level": i["text_tone"]} if i["item_kind"] == "callout" else {}),
                   "x": i["grid_x"], "y": i["grid_y"], "w": i["grid_w"], "h": i["grid_h"],
                   "empty_expected": i["empty_expected"],
                   "hidden_for_roles": [str(x) for x in i["hidden_for_roles"] or []]} for i in items],
        "widgets": {str(w["id"]): {"title": w["title"], "visual": w["visual"], "spec": w["spec"],
                                   "follow_page_filters": w["follow_page_filters"], "own_filters": w["own_filters"],
                                   "badge": w["certification_state"]} for w in widgets},
    }


async def restore_state(conn: AsyncConnection, report: dict[str, Any], state: dict[str, Any]) -> None:
    rid, owner = report["id"], report["owner_id"]
    await conn.execute(text("UPDATE content.reports SET title = :t, page_filters = CAST(:f AS jsonb) WHERE id = :id"),
                       {"t": state["title"], "f": json.dumps(state["page_filters"]), "id": rid})
    await conn.execute(text("DELETE FROM content.report_items WHERE report_id = :id"), {"id": rid})
    await conn.execute(text("DELETE FROM content.report_sections WHERE report_id = :id"), {"id": rid})
    for s in state["sections"]:
        await conn.execute(
            text("INSERT INTO content.report_sections (id, tenant_id, report_id, title, kind, position, collapsed) "
                 "VALUES (CAST(:id AS uuid), :t, :r, :title, :kind, :p, :c)"),
            {"id": s["id"], "t": report["tenant_id"], "r": rid, "title": s["title"], "kind": s["kind"],
             "p": s["position"], "c": s["collapsed"]})
    for wid, w in state["widgets"].items():
        await conn.execute(
            text("UPDATE content.widgets SET title = :title, visual = :v, spec = CAST(:s AS jsonb), "
                 "follow_page_filters = :f, own_filters = CAST(:o AS jsonb), deleted_at = NULL "
                 "WHERE id = CAST(:id AS uuid) AND owner_id = :owner"),
            {"title": w["title"], "v": w["visual"], "s": json.dumps(w["spec"]), "f": w["follow_page_filters"],
             "o": json.dumps(w["own_filters"]), "id": wid, "owner": owner})
    for i in state["items"]:
        await _insert_item(conn, report, i)


async def _insert_item(conn: AsyncConnection, report: dict[str, Any], i: dict[str, Any]) -> None:
    await conn.execute(
        text("INSERT INTO content.report_items (id, tenant_id, report_id, section_id, item_kind, widget_id, "
             "text_content, text_title, text_tone, grid_x, grid_y, grid_w, grid_h, empty_expected, "
             "hidden_for_roles) VALUES (CAST(:id AS uuid), :t, :r, CAST(:s AS uuid), :k, CAST(:w AS uuid), :txt, :tt, "
             ":tone, :x, :y, :ww, :h, :e, CAST(:hr AS uuid[]))"),
        {"id": i["id"], "t": report["tenant_id"], "r": report["id"], "s": i["section_id"], "k": i["kind"],
         "w": i.get("widget_id"), "txt": i.get("text"), "tt": i.get("text_title"), "tone": i.get("level"),
         "x": i["x"], "y": i["y"], "ww": i["w"], "h": i["h"],
         "e": i.get("empty_expected", False), "hr": i.get("hidden_for_roles", [])})


def text_fields(i: dict[str, Any]) -> dict[str, Any]:
    """The text of a note, callout or footnote as readers get it (canvas, frozen versions, viewer, exports)."""
    out = {"text": i.get("text") or ""}
    if i["kind"] == "callout":
        out.update(title=i.get("text_title"), level=i.get("level") or "info")
    return out


def empty_state(title: str) -> dict[str, Any]:
    return {"title": title, "page_filters": [], "widgets": {}, "items": [],
            "sections": [{"id": new_id(), "title": "Section 1", "kind": "normal", "position": 0, "collapsed": False}]}


# --------------------------------------------------------------------------- output


async def report_out(conn: AsyncConnection, principal: Principal, report: dict[str, Any]) -> dict[str, Any]:
    state = await report_state(conn, report["id"])
    can_edit = str(report["owner_id"]) == principal.user_id
    owner = await fetch_one(conn, "SELECT display_name FROM iam.users WHERE id = :u",
                            {"u": report["owner_id"]}) if report["owner_id"] else None
    dashboards = await fetch_all(conn, "SELECT d.id, d.title FROM content.dashboard_reports dr JOIN "
                                       "content.dashboards d ON d.id = dr.dashboard_id WHERE dr.report_id = :r AND "
                                       "d.deleted_at IS NULL",
                                 {"r": report["id"]})
    # "Data as of" (US-158): the reporting date of the report's model, when all its widgets share one.
    versions = {str(w["spec"]["model_version_id"]) for w in state["widgets"].values()
                if (w["spec"] or {}).get("model_version_id")}
    dates = set()
    for version in versions:
        snap = await load_snapshot(conn, principal.tenant_id, version)
        dates.add(reporting_date_or_none(snap.settings, principal.time_zone))
    undo = await fetch_one(conn, "SELECT summary FROM content.drafts_undo_log WHERE object_type = 'report' AND "
                                 "object_id = :r AND NOT undone ORDER BY revision DESC LIMIT 1", {"r": report["id"]})

    def item_out(i: dict[str, Any]) -> dict[str, Any]:
        base = {k: i[k] for k in ("id", "section_id", "kind", "x", "y", "w", "h", "empty_expected", "hidden_for_roles")}
        if i["kind"] in TEXT_KINDS:
            return {**base, **text_fields(i)}
        w = state["widgets"].get(i["widget_id"] or "", {})
        spec = w.get("spec", {})
        out = {**base, "widget_id": i["widget_id"], "title": w.get("title"), "visual": w.get("visual"),
               "badges": [w.get("badge", "certified")], "measure": spec.get("measure", {}).get("ref"),
               "follow_page_filters": w.get("follow_page_filters", True), "own_filters": w.get("own_filters", []),
               "connection_id": spec.get("connection_id"), "model_version_id": spec.get("model_version_id")}
        if can_edit:
            out["spec"] = spec
        return out

    return {
        "id": str(report["id"]), "title": state["title"], "page_filters": state["page_filters"],
        "revision": report["draft_revision"],
        "owner": {"id": str(report["owner_id"]), "name": owner["display_name"] if owner else None}
        if report["owner_id"] else {"id": None, "name": "Tela starter"},
        "origin": report["origin"],
        "copied_from_id": str(report["copied_from_id"]) if report["copied_from_id"] else None,
        "can_edit": can_edit, "published": report["published_snapshot"] is not None,
        "updated_at": report["updated_at"], "plan_id": str(report["plan_id"]) if report["plan_id"] else None,
        "in_dashboards": [{"id": str(d["id"]), "title": d["title"]} for d in dashboards],
        "reporting_date": next(iter(dates)) if len(dates) == 1 else None,
        "can_undo": undo is not None, "undo_summary": undo["summary"] if undo else None,
        "sections": [{**s, "items": [item_out(i) for i in state["items"] if i["section_id"] == s["id"]]}
                     for s in state["sections"]],
    }


async def _out(principal: Principal, report_id: str, response: Response | None = None) -> dict[str, Any]:
    async with tenant_tx(principal.tenant_id) as conn:
        report = await readable_report(conn, principal, report_id)
        out = await report_out(conn, principal, report)
    if response is not None:
        response.headers["ETag"] = f'"{out["revision"]}"'
    return out


Mutation = Callable[[AsyncConnection, dict[str, Any]], Awaitable[Any]]


async def mutate(principal: Principal, report_id: str, *, source: str, summary: str, fn: Mutation,
                 if_match: str | None = None, response: Response | None = None,
                 request_id: str | None = None, audit_action: str | None = None) -> tuple[dict[str, Any], Any]:
    """Run one auto-saved change: lock, check the revision, change, log before/after, bump the ETag."""
    async with tenant_tx(principal.tenant_id) as conn:
        report = await own_report(conn, principal, report_id, lock=True)
        if if_match is not None and if_match.strip().strip('"').removeprefix("W/").strip('"') != \
                str(report["draft_revision"]):
            raise TelaError(ErrorCode.PRECONDITION_FAILED, "This report changed since you loaded it. Reload and "
                            "try again.", {"revision": report["draft_revision"]})
        before = await report_state(conn, report["id"])
        result = await fn(conn, report)
        after = await report_state(conn, report["id"])
        if canonical(after) != canonical(before):
            await conn.execute(text("UPDATE content.reports SET draft_revision = draft_revision + 1 WHERE id = :id"),
                               {"id": report["id"]})
            await log_change(conn, principal, object_type="report", object_id=str(report["id"]), source=source,
                             before=before, after=after, summary=summary)
            if audit_action:
                await audit.record(conn, tenant_id=principal.tenant_id, action=audit_action, object_type="report",
                                   object_id=str(report["id"]), outcome="success", actor_id=principal.user_id,
                                   correlation_id=request_id, details={"summary": summary[:200]})
        fresh = await own_report(conn, principal, report_id)
        out = await report_out(conn, principal, fresh)
    if response is not None:
        response.headers["ETag"] = f'"{out["revision"]}"'
    return out, result


# --------------------------------------------------------------------------- reports CRUD (API-024)


class PageFilterDef(BaseModel):
    ref: str = Field(pattern=r"^dimension:[a-z_][a-z0-9_]{0,62}$")


class ReportIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    page_filters: list[PageFilterDef] = Field(default_factory=list, max_length=10)


class ReportPatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    page_filters: list[PageFilterDef] | None = Field(default=None, max_length=10)


async def create_report(conn: AsyncConnection, principal: Principal, title: str,
                        page_filters: list[dict[str, Any]] | None = None, plan_id: str | None = None,
                        with_section: bool = True) -> dict[str, Any]:
    rid = new_id()
    await conn.execute(
        text("INSERT INTO content.reports (id, tenant_id, owner_id, title, page_filters, plan_id, created_by) "
             "VALUES (:id, :t, :o, :title, CAST(:f AS jsonb), CAST(:p AS uuid), :o)"),
        {"id": rid, "t": principal.tenant_id, "o": principal.user_id, "title": title,
         "f": json.dumps(page_filters or []), "p": plan_id})
    if with_section:
        await add_section(conn, principal.tenant_id, rid, "Section 1")
    row = await fetch_one(conn, "SELECT * FROM content.reports WHERE id = :id", {"id": rid})
    assert row is not None
    return row


async def add_section(conn: AsyncConnection, tenant_id: str, report_id: Any, title: str,
                      kind: str = "normal") -> str:
    pos = await fetch_one(conn, "SELECT coalesce(max(position) + 1, 0) AS p FROM content.report_sections "
                                "WHERE report_id = :r", {"r": report_id})
    sid = new_id()
    await conn.execute(
        text("INSERT INTO content.report_sections (id, tenant_id, report_id, title, kind, position) "
             "VALUES (:id, :t, :r, :title, :k, :p)"),
        {"id": sid, "t": tenant_id, "r": report_id, "title": title[:120], "k": kind, "p": pos["p"] if pos else 0})
    return str(sid)


@router.get("/reports")
async def list_reports(principal: Principal = Depends(require(Perm.DASHBOARDS_VIEW))) -> dict[str, Any]:
    async with tenant_tx(principal.tenant_id) as conn:
        rows = await fetch_all(
            conn,
            """
            SELECT r.id, r.title, r.updated_at, r.owner_id, u.display_name AS owner_name,
              (SELECT count(*) FROM content.report_items i WHERE i.report_id = r.id
               AND i.item_kind = 'widget') AS widgets,
              r.published_snapshot IS NOT NULL AS published
            FROM content.reports r JOIN iam.users u ON u.id = r.owner_id
            WHERE r.deleted_at IS NULL AND (r.owner_id = CAST(:u AS uuid) OR EXISTS (
              SELECT 1 FROM content.shares s WHERE s.object_type = 'report' AND s.object_id = r.id
              AND s.grantee_user_id = CAST(:u AS uuid)))
            ORDER BY r.updated_at DESC LIMIT 200
            """, {"u": principal.user_id})
    return {"items": [{"id": str(r["id"]), "title": r["title"], "updated_at": r["updated_at"],
                       "widgets": r["widgets"], "published": r["published"],
                       "mine": str(r["owner_id"]) == principal.user_id, "owner_name": r["owner_name"]} for r in rows]}


@router.post("/reports", status_code=201)
async def post_report(body: ReportIn, request: Request, response: Response,
                      principal: Principal = Depends(require(Perm.REPORTS_CREATE))) -> dict[str, Any]:
    async with tenant_tx(principal.tenant_id) as conn:
        report = await create_report(conn, principal, body.title.strip(), [f.model_dump() for f in body.page_filters])
        await audit.record(conn, tenant_id=principal.tenant_id, action="report.created", object_type="report",
                           object_id=str(report["id"]), outcome="success", actor_id=principal.user_id,
                           correlation_id=_rid(request))
        out = await report_out(conn, principal, report)
    response.headers["ETag"] = f'"{out["revision"]}"'
    return out


@router.get("/reports/{report_id}")
async def get_report(report_id: str, response: Response,
                     principal: Principal = Depends(require(Perm.DASHBOARDS_VIEW))) -> dict[str, Any]:
    return await _out(principal, report_id, response)


@router.patch("/reports/{report_id}")
async def patch_report(report_id: str, body: ReportPatch, request: Request, response: Response,
                       if_match: str | None = Header(default=None),
                       principal: Principal = Depends(require(Perm.REPORTS_CREATE))) -> dict[str, Any]:
    async def fn(conn: AsyncConnection, report: dict[str, Any]) -> None:
        if body.title is not None:
            await conn.execute(text("UPDATE content.reports SET title = :t WHERE id = :id"),
                               {"t": body.title.strip(), "id": report["id"]})
        if body.page_filters is not None:
            await conn.execute(text("UPDATE content.reports SET page_filters = CAST(:f AS jsonb) WHERE id = :id"),
                               {"f": json.dumps([f.model_dump() for f in body.page_filters]), "id": report["id"]})

    summary = "Renamed the report" if body.title is not None and body.page_filters is None else "Changed page filters"
    out, _ = await mutate(principal, report_id, source="canvas", summary=summary, fn=fn, if_match=if_match,
                          response=response, request_id=_rid(request))
    return out


@router.delete("/reports/{report_id}", status_code=204)
async def delete_report(report_id: str, request: Request,
                        principal: Principal = Depends(require(Perm.REPORTS_CREATE))) -> Response:
    async with tenant_tx(principal.tenant_id) as conn:
        report = await own_report(conn, principal, report_id, lock=True)
        used = await fetch_all(conn, "SELECT d.title FROM content.dashboard_reports dr JOIN content.dashboards d ON "
                                     "d.id = dr.dashboard_id WHERE dr.report_id = :r AND d.deleted_at IS NULL",
                               {"r": report["id"]})
        if used:
            raise TelaError(ErrorCode.CONFLICT, f"This report is on {used[0]['title']}. Remove it from the dashboard "
                            "first.", {"dashboards": [u["title"] for u in used]})
        await conn.execute(text("UPDATE content.reports SET deleted_at = now() WHERE id = :id"), {"id": report["id"]})
        await audit.record(conn, tenant_id=principal.tenant_id, action="report.deleted", object_type="report",
                           object_id=report_id, outcome="success", actor_id=principal.user_id,
                           correlation_id=_rid(request))
    return Response(status_code=204)


# --------------------------------------------------------------------------- sections


class SectionIn(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    kind: str = Field(default="normal", pattern="^(normal|headline)$")


class SectionPatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=120)
    collapsed: bool | None = None
    position: int | None = Field(default=None, ge=0, le=100)


@router.post("/reports/{report_id}/sections", status_code=201)
async def post_section(report_id: str, body: SectionIn, request: Request, response: Response,
                       principal: Principal = Depends(require(Perm.REPORTS_CREATE))) -> dict[str, Any]:
    async def fn(conn: AsyncConnection, report: dict[str, Any]) -> str:
        return await add_section(conn, principal.tenant_id, report["id"], body.title.strip(), body.kind)

    out, sid = await mutate(principal, report_id, source="canvas", summary=f"Added section {body.title.strip()}",
                            fn=fn, response=response, request_id=_rid(request))
    return {**out, "section_id": sid}


async def _section(conn: AsyncConnection, report: dict[str, Any], section_id: str) -> dict[str, Any]:
    try:
        row = await fetch_one(conn, "SELECT * FROM content.report_sections WHERE id = CAST(:s AS uuid) "
                                    "AND report_id = :r", {"s": section_id, "r": report["id"]})
    except Exception:  # noqa: BLE001
        row = None
    if row is None:
        raise TelaError(ErrorCode.NOT_FOUND, "That section is not on this report.")
    return row


@router.patch("/reports/{report_id}/sections/{section_id}")
async def patch_section(report_id: str, section_id: str, body: SectionPatch, request: Request, response: Response,
                        principal: Principal = Depends(require(Perm.REPORTS_CREATE))) -> dict[str, Any]:
    async def fn(conn: AsyncConnection, report: dict[str, Any]) -> None:
        s = await _section(conn, report, section_id)
        if body.title is not None:
            await conn.execute(text("UPDATE content.report_sections SET title = :t WHERE id = :id"),
                               {"t": body.title.strip(), "id": s["id"]})
        if body.collapsed is not None:
            await conn.execute(text("UPDATE content.report_sections SET collapsed = :c WHERE id = :id"),
                               {"c": body.collapsed, "id": s["id"]})
        if body.position is not None:
            order = [r["id"] for r in await fetch_all(conn, "SELECT id FROM content.report_sections WHERE report_id = "
                                                            ":r ORDER BY position", {"r": report["id"]})]
            order.remove(s["id"])
            order.insert(min(body.position, len(order)), s["id"])
            for pos, sid in enumerate(order):
                await conn.execute(text("UPDATE content.report_sections SET position = :p WHERE id = :id"),
                                   {"p": pos, "id": sid})

    what = "Moved" if body.position is not None else "Renamed" if body.title else "Changed"
    out, _ = await mutate(principal, report_id, source="canvas", summary=f"{what} a section", fn=fn,
                          response=response, request_id=_rid(request))
    return out


@router.delete("/reports/{report_id}/sections/{section_id}")
async def delete_section(report_id: str, section_id: str, request: Request, response: Response,
                         principal: Principal = Depends(require(Perm.REPORTS_CREATE))) -> dict[str, Any]:
    async def fn(conn: AsyncConnection, report: dict[str, Any]) -> None:
        s = await _section(conn, report, section_id)
        count = await fetch_one(conn, "SELECT count(*) AS n FROM content.report_sections WHERE report_id = :r",
                                {"r": report["id"]})
        if count and count["n"] <= 1:
            raise TelaError(ErrorCode.CONFLICT, "A report needs at least one section.")
        await conn.execute(text("DELETE FROM content.report_sections WHERE id = :id"), {"id": s["id"]})
        rest = await fetch_all(conn, "SELECT id FROM content.report_sections WHERE report_id = :r ORDER BY position",
                               {"r": report["id"]})
        for pos, r in enumerate(rest):
            await conn.execute(text("UPDATE content.report_sections SET position = :p WHERE id = :id"),
                               {"p": pos, "id": r["id"]})

    out, _ = await mutate(principal, report_id, source="canvas", summary="Removed a section", fn=fn,
                          response=response, request_id=_rid(request))
    return out


# --------------------------------------------------------------------------- items


class ItemIn(BaseModel):
    kind: str = Field(pattern="^(widget|text_note|callout|footnote|measure)$")
    section_id: str | None = None
    widget_id: str | None = None
    measure: str | None = Field(default=None, pattern=r"^measure:[a-z_][a-z0-9_]{0,62}$")  # kind "measure"
    connection_id: str | None = None  # kind "measure"
    text: str | None = Field(default=None, max_length=4000)
    title: str | None = Field(default=None, max_length=200)  # a callout's title
    level: Literal["info", "warning"] | None = None  # a callout's level
    x: int | None = Field(default=None, ge=0, le=11)
    y: int | None = Field(default=None, ge=0)
    w: int | None = Field(default=None, ge=2, le=12)
    h: int | None = Field(default=None, ge=2, le=24)


class ItemPatch(BaseModel):
    text: str | None = Field(default=None, max_length=4000)
    title: str | None = Field(default=None, max_length=200)  # a widget's (not blank) or a callout's (blank clears it)
    visual: str | None = Field(default=None, max_length=40)
    follow_page_filters: bool | None = None
    filters: list[Filter] | None = Field(default=None, max_length=10)  # the widget's own filters (badge)
    limit: int | None = Field(default=None, ge=1, le=1000)
    ops: list[ChipOp] | None = Field(default=None, max_length=4)  # breakdown / date changes (chart-type fixes)
    empty_expected: bool | None = None
    hidden_for_roles: list[str] | None = Field(default=None, max_length=20)
    section_id: str | None = None
    tone: FigureTone | None = None  # a figure's threshold (US-157); null clears it
    note: str | None = Field(default=None, max_length=200)  # a meter's note
    detail: Detail | None = None  # a detail table's columns, sort and export name (US-158); null lists no records
    colour: bool | None = None  # a table in colour (ENH-020)
    card: CardTone | None = None  # the card's colour (ENH-023 US-223); null or "default" is the plain card
    colours: ChartColours | None = None  # the chart's colours (US-224); null goes back to the brand's
    controls: list[str] | None = Field(default=None, max_length=MAX_CONTROLS)  # widget filters for readers (US-221)
    level: Literal["info", "warning"] | None = None  # a callout's level


async def section_boxes(conn: AsyncConnection, section_id: Any, *, exclude: str | None = None) -> list[Box]:
    rows = await fetch_all(conn, "SELECT id, grid_x, grid_y, grid_w, grid_h FROM content.report_items "
                                 "WHERE section_id = :s", {"s": section_id})
    return [Box(str(r["id"]), r["grid_x"], r["grid_y"], r["grid_w"], r["grid_h"]) for r in rows
            if str(r["id"]) != exclude]


async def first_section(conn: AsyncConnection, report: dict[str, Any], section_id: str | None) -> dict[str, Any]:
    if section_id:
        return await _section(conn, report, section_id)
    row = await fetch_one(conn, "SELECT * FROM content.report_sections WHERE report_id = :r AND kind = 'normal' "
                                "ORDER BY position LIMIT 1", {"r": report["id"]}) or \
        await fetch_one(conn, "SELECT * FROM content.report_sections WHERE report_id = :r ORDER BY position LIMIT 1",
                        {"r": report["id"]})
    if row is None:
        sid = await add_section(conn, report["tenant_id"], report["id"], "Section 1")
        row = await _section(conn, report, sid)
    return row


async def place_widget(conn: AsyncConnection, report: dict[str, Any], section: dict[str, Any], widget_id: str,
                       visual: str, *, w: int | None = None, h: int | None = None, x: int | None = None,
                       y: int | None = None) -> str:
    held = await fetch_one(conn, "SELECT spec FROM content.widgets WHERE id = CAST(:id AS uuid)", {"id": widget_id})
    if held is not None and is_freehand(held["spec"]):
        raise TelaError(ErrorCode.CONFLICT, MSG_FREEHAND_HELD)  # US-166: not on a report until certified
    dw, dh = default_size(visual)
    w, h = w or dw, h or dh
    boxes = await section_boxes(conn, section["id"])
    if x is None or y is None or any(Box("n", x, y, w, h).hits(b) for b in boxes) or x + w > 12:
        x, y = place(boxes, w, h)
    item_id = new_id()
    await _insert_item(conn, report, {"id": item_id, "section_id": str(section["id"]), "kind": "widget",
                                      "widget_id": widget_id, "x": x, "y": y, "w": w, "h": h})
    return str(item_id)


async def _item(conn: AsyncConnection, report: dict[str, Any], item_id: str) -> dict[str, Any]:
    try:
        row = await fetch_one(conn, "SELECT * FROM content.report_items WHERE id = CAST(:i AS uuid) AND report_id = :r",
                              {"i": item_id, "r": report["id"]})
    except Exception:  # noqa: BLE001
        row = None
    if row is None:
        raise TelaError(ErrorCode.NOT_FOUND, "That item is not on this report.")
    return row


async def measure_spec(request: Request, principal: Principal, body: ItemIn) -> tuple[dict[str, Any], str]:
    """A certified measure dragged from the canvas library becomes a figure spec, run once in the Creator's scope."""
    if not body.measure or not body.connection_id:
        raise TelaError(ErrorCode.VALIDATION_ERROR, "Choose a certified measure and its data source.")
    async with tenant_tx(principal.tenant_id) as conn:
        try:
            version_id = await current_version_id(conn, body.connection_id)
        except Exception:  # noqa: BLE001
            version_id = None
        if version_id is None:
            raise TelaError(ErrorCode.MODEL_NOT_APPROVED, "This data source has no approved model yet.")
        snap = await load_snapshot(conn, principal.tenant_id, version_id)
    m = snap.measure(body.measure.split(":", 1)[1])
    if m is None or m.status != "certified":
        raise TelaError(ErrorCode.NOT_FOUND, "That certified measure is not in the current model.")
    spec = {"model_version_id": version_id, "connection_id": body.connection_id,
            "measure": {"ref": body.measure}, "visual": {"type": "figure"}}
    result = await _service(request).execute(spec, principal, QueryContext(surface="preview",
                                                                           request_id=_rid(request)))
    if result.get("hidden"):
        raise TelaError(ErrorCode.FORBIDDEN, "This measure uses fields that are hidden for your role.")
    return spec, m.label


@router.post("/reports/{report_id}/items", status_code=201)
async def post_item(report_id: str, body: ItemIn, request: Request, response: Response,
                    principal: Principal = Depends(require(Perm.REPORTS_CREATE))) -> dict[str, Any]:
    measure = await measure_spec(request, principal, body) if body.kind == "measure" else None

    async def fn(conn: AsyncConnection, report: dict[str, Any]) -> str:
        section = await first_section(conn, report, body.section_id)
        if measure is not None:
            spec, label = measure
            h = def_hash(spec)
            existing = await fetch_one(conn, "SELECT id FROM content.widgets WHERE owner_id = :u AND def_hash = :h "
                                             "AND deleted_at IS NULL", {"u": principal.user_id, "h": h})
            wid = str(existing["id"]) if existing else new_id()
            if existing is None:
                await conn.execute(
                    text("INSERT INTO content.widgets (id, tenant_id, title, spec, visual, model_version_id, "
                         "connection_id, certification_state, owner_id, def_hash, created_by) VALUES (:id, :t, "
                         ":title, CAST(:s AS jsonb), 'figure', :mv, :c, 'certified', :u, :h, :u)"),
                    {"id": wid, "t": principal.tenant_id, "title": label, "s": json.dumps(spec),
                     "mv": spec["model_version_id"], "c": spec["connection_id"], "u": principal.user_id, "h": h})
            elif await fetch_one(conn, "SELECT 1 AS ok FROM content.report_items WHERE report_id = :r AND "
                                       "widget_id = :w", {"r": report["id"], "w": wid}):
                raise TelaError(ErrorCode.CONFLICT, "This widget is already on this report.")  # MSG-023
            return await place_widget(conn, report, section, wid, "figure", w=body.w, h=body.h, x=body.x, y=body.y)
        if body.kind in TEXT_KINDS:
            note = clean_text(body.text or "")
            if not note:
                raise TelaError(ErrorCode.VALIDATION_ERROR, "Write the text first.")
            dw, dh = TEXT_SIZES[body.kind]
            w, th = body.w or dw, body.h or dh
            boxes = await section_boxes(conn, section["id"])
            if body.kind == "footnote":  # always under everything else in its section
                x, y = 0, max((b.y + b.h for b in boxes), default=0)
                w = 12
            else:
                x, y = (body.x, body.y) if body.x is not None and body.y is not None and body.x + w <= 12 and not any(
                    Box("n", body.x, body.y, w, th).hits(b) for b in boxes) else place(boxes, w, th)
            item_id = new_id()
            title = clean_text(body.title or "")[:200] or None
            await _insert_item(conn, report, {"id": item_id, "section_id": str(section["id"]), "kind": body.kind,
                                              "text": note, "x": x, "y": y, "w": w, "h": th,
                                              **({"text_title": title, "level": body.level or "info"}
                                                 if body.kind == "callout" else {})})
            return str(item_id)
        widget = await _library_widget(conn, principal, body.widget_id or "")
        on_report = await fetch_one(conn, "SELECT 1 AS ok FROM content.report_items WHERE report_id = :r AND "
                                          "widget_id = :w", {"r": report["id"], "w": widget["id"]})
        if on_report:
            raise TelaError(ErrorCode.CONFLICT, "This widget is already on this report.")  # MSG-023
        return await place_widget(conn, report, section, str(widget["id"]), widget["visual"], w=body.w, h=body.h,
                                  x=body.x, y=body.y)

    summary = {"text_note": "Added a text note", "callout": "Added a callout", "footnote": "Added a footnote",
               "measure": f"Added {measure[1] if measure else 'a measure'}"}.get(
        body.kind, "Added a widget from the library")
    out, item_id = await mutate(principal, report_id, source="canvas", summary=summary, fn=fn, response=response,
                                request_id=_rid(request))
    return {**out, "item_id": item_id}


async def _library_widget(conn: AsyncConnection, principal: Principal, widget_id: str) -> dict[str, Any]:
    try:
        row = await fetch_one(conn, "SELECT * FROM content.widgets WHERE id = CAST(:w AS uuid) AND owner_id = :u "
                                    "AND deleted_at IS NULL AND dashboard_tab_id IS NULL",
                              {"w": widget_id, "u": principal.user_id})
    except Exception:  # noqa: BLE001
        row = None
    if row is None:
        raise TelaError(ErrorCode.NOT_FOUND, "That widget is not in your library.")
    return row


# A widget's definition hash marks the library's one widget per definition; a report's own copy (BUG-020) or an edit
# that makes a widget match another of the owner's keeps no hash, as a duplicated item does.
SET_DEF_HASH = ("def_hash = CASE WHEN EXISTS (SELECT 1 FROM content.widgets o WHERE o.owner_id = CAST(:u AS uuid) "
                "AND o.def_hash = :h AND o.id <> :id) THEN NULL ELSE :h END")


async def item_widget(conn: AsyncConnection, item: dict[str, Any], user_id: str) -> Any:
    """The widget an edit made on this report item may change (BUG-020).

    A widget placed on another report (or twice on this one) is shared: the edit goes to a copy that only this item
    uses, so the other places keep their chart. The copy keeps the title and is otherwise identical; undo puts the
    item back on the shared widget."""
    shared = await fetch_one(conn, "SELECT 1 FROM content.report_items WHERE widget_id = :w AND id <> :i LIMIT 1",
                             {"w": item["widget_id"], "i": item["id"]})
    if shared is None:
        return item["widget_id"]
    wid = new_id()
    await conn.execute(
        text("INSERT INTO content.widgets (id, tenant_id, title, spec, visual, follow_page_filters, model_version_id, "
             "connection_id, certification_state, owner_id, question, source_kind, own_filters, created_by) "
             "SELECT :new, tenant_id, title, spec, visual, follow_page_filters, model_version_id, connection_id, "
             "certification_state, owner_id, question, source_kind, own_filters, :u FROM content.widgets "
             "WHERE id = :old AND owner_id = CAST(:u AS uuid)"),
        {"new": wid, "u": user_id, "old": item["widget_id"]})
    await conn.execute(text("UPDATE content.report_items SET widget_id = :w WHERE id = :id"),
                       {"w": wid, "id": item["id"]})
    return wid


@router.patch("/reports/{report_id}/items/{item_id}")
async def patch_item(report_id: str, item_id: str, body: ItemPatch, request: Request, response: Response,
                     principal: Principal = Depends(require(Perm.REPORTS_CREATE))) -> dict[str, Any]:
    changed_spec: dict[str, Any] | None = None
    sent = body.model_fields_set
    if body.visual is not None or body.filters is not None or body.limit is not None or body.ops or \
            {"tone", "note", "detail", "colour", "card", "colours", "controls"} & sent:
        # Guard first, outside the write transaction: the widget runs under the Creator's scope (cached).
        async with tenant_tx(principal.tenant_id) as conn:
            report = await own_report(conn, principal, report_id)
            item = await _item(conn, report, item_id)
            if item["widget_id"] is None:
                raise TelaError(ErrorCode.VALIDATION_ERROR, "Text blocks have no chart or filters.")
            w = await fetch_one(conn, "SELECT * FROM content.widgets WHERE id = :w", {"w": item["widget_id"]})
            assert w is not None
            snap = await load_snapshot(conn, principal.tenant_id, str(w["model_version_id"]))
        changed_spec = dict(w["spec"])
        if body.filters is not None:
            own = [f.model_dump() for f in body.filters]
            base = [f for f in changed_spec.get("filters", []) if f not in (w["own_filters"] or [])]
            changed_spec["filters"] = base + own
        if body.limit is not None:
            changed_spec["limit"] = body.limit
            changed_spec.setdefault("sort", [{"by": "measure", "dir": "desc"}])
        if body.ops:
            changed_spec, _ = apply_ops(changed_spec, [o.model_dump(exclude_none=True) for o in body.ops], snap)
        visual = body.visual or w["visual"]
        if visual in VISUAL_TYPES:
            changed_spec["visual"] = stored_visual(changed_spec.get("visual"), visual, tone=body.tone, note=body.note,
                                                   detail=body.detail, colour=body.colour, card=body.card,
                                                   colours=body.colours, controls=body.controls, sent=sent)
            if body.detail is not None:
                check_detail(body.detail.model_dump(), changed_spec, snap)
            if body.controls:
                check_controls(body.controls, changed_spec, snap)
        result = await _service(request).execute(changed_spec, principal, QueryContext(surface="preview",
                                                                                      request_id=_rid(request)))
        if body.visual is not None:
            guard_visual(result, snap, body.visual)

    async def fn(conn: AsyncConnection, report: dict[str, Any]) -> None:
        item = await _item(conn, report, item_id)
        if body.text is not None:
            if item["item_kind"] not in TEXT_KINDS:
                raise TelaError(ErrorCode.VALIDATION_ERROR, "Only text notes, callouts and footnotes have text.")
            if not clean_text(body.text):
                raise TelaError(ErrorCode.VALIDATION_ERROR, "Write the text first.")
            await conn.execute(text("UPDATE content.report_items SET text_content = :t WHERE id = :id"),
                               {"t": clean_text(body.text), "id": item["id"]})
        if (body.level is not None or (body.title is not None and item["widget_id"] is None)) and \
                item["item_kind"] != "callout":
            raise TelaError(ErrorCode.VALIDATION_ERROR, "Only callouts have a title and a level.")
        if item["item_kind"] == "callout" and body.title is not None:
            await conn.execute(text("UPDATE content.report_items SET text_title = :t WHERE id = :id"),
                               {"t": clean_text(body.title)[:200] or None, "id": item["id"]})
        if body.level is not None:
            await conn.execute(text("UPDATE content.report_items SET text_tone = :t WHERE id = :id"),
                               {"t": body.level, "id": item["id"]})
        if body.empty_expected is not None:
            await conn.execute(text("UPDATE content.report_items SET empty_expected = :e WHERE id = :id"),
                               {"e": body.empty_expected, "id": item["id"]})
        if body.hidden_for_roles is not None:
            await conn.execute(text("UPDATE content.report_items SET hidden_for_roles = CAST(:h AS uuid[]) "
                                    "WHERE id = :id"), {"h": body.hidden_for_roles, "id": item["id"]})
        if body.section_id is not None and body.section_id != str(item["section_id"]):
            target = await _section(conn, report, body.section_id)
            x, y = place(await section_boxes(conn, target["id"]), item["grid_w"], item["grid_h"])
            await conn.execute(text("UPDATE content.report_items SET section_id = :s, grid_x = :x, grid_y = :y "
                                    "WHERE id = :id"), {"s": target["id"], "x": x, "y": y, "id": item["id"]})
        if item["widget_id"] is None:
            return
        sets: list[str] = []
        params: dict[str, Any] = {"u": principal.user_id}
        if body.title is not None and not body.title.strip():
            raise TelaError(ErrorCode.VALIDATION_ERROR, "A widget needs a title.")
        if body.title is not None:
            sets.append("title = :title")
            params["title"] = body.title.strip()
        if body.follow_page_filters is not None:
            sets.append("follow_page_filters = :f")
            params["f"] = body.follow_page_filters
        if changed_spec is not None:
            sets += ["spec = CAST(:spec AS jsonb)", SET_DEF_HASH]
            params.update(spec=json.dumps(changed_spec), h=def_hash(changed_spec))
            if body.visual is not None:
                sets.append("visual = :v")
                params["v"] = body.visual
            if body.filters is not None:
                sets.append("own_filters = CAST(:own AS jsonb)")
                params["own"] = json.dumps([f.model_dump() for f in body.filters])
        if sets:
            params["id"] = await item_widget(conn, item, principal.user_id)
            await conn.execute(text(f"UPDATE content.widgets SET {', '.join(sets)} WHERE id = :id "  # noqa: S608
                                    "AND owner_id = CAST(:u AS uuid)"), params)

    parts = [k for k, v in body.model_dump(exclude_none=True).items()]
    summary = {"title": "Renamed a widget", "visual": f"Changed a chart to {body.visual}", "filters": "Changed widget "
               "filters", "text": "Edited a text note", "limit": f"Limited a widget to {body.limit}",
               "ops": "Added a breakdown",
               "follow_page_filters": "Changed page-filter following", "section_id": "Moved a widget to another "
               "section", "empty_expected": "Marked an empty result as expected", "hidden_for_roles": "Changed who a "
               "widget is hidden for", "tone": "Set a figure's tone threshold", "note": "Changed a meter's note",
               "level": "Changed a callout's level", "detail": "Changed a detail table's columns",
               "colour": "Showed a table in colour" if body.colour else "Showed a table without colour",
               "card": "Changed a widget's card colour", "colours": "Changed a chart's colours",
               "controls": "Changed a widget's filters for readers"
               }.get(parts[0] if parts else "", "Changed an item")
    out, _ = await mutate(principal, report_id, source="canvas", summary=summary, fn=fn, response=response,
                          request_id=_rid(request))
    return out


VISUAL_TYPES = set(VISUALS)


def check_detail(detail: dict[str, Any], spec: dict[str, Any], snap: ModelSnapshot) -> None:
    """A detail table lists fields of its measure's entity, and sorts by one of them."""
    m = snap.measure(ref_key(spec["measure"]["ref"])) if spec["measure"]["ref"].startswith("measure:") else None
    entity = snap.entity(m.entity) if m is not None else None
    if entity is None:
        raise TelaError(ErrorCode.VALIDATION_ERROR, "A detail table needs a certified measure for its population.")
    unknown = [k for k in detail["columns"] if entity.field(k) is None]
    sort = (detail.get("sort") or {}).get("field")
    if unknown or (sort and sort not in detail["columns"]):
        raise TelaError(ErrorCode.VALIDATION_ERROR, f"A detail table lists and sorts by fields of {entity.label}.",
                        {"unknown": unknown + ([sort] if sort and sort not in detail["columns"] else [])})


@router.delete("/reports/{report_id}/items/{item_id}")
async def delete_item(report_id: str, item_id: str, request: Request, response: Response,
                      principal: Principal = Depends(require(Perm.REPORTS_CREATE))) -> dict[str, Any]:
    async def fn(conn: AsyncConnection, report: dict[str, Any]) -> None:
        item = await _item(conn, report, item_id)
        await conn.execute(text("DELETE FROM content.report_items WHERE id = :id"), {"id": item["id"]})

    out, _ = await mutate(principal, report_id, source="canvas", summary="Removed an item", fn=fn,
                          response=response, request_id=_rid(request))
    return out


@router.post("/reports/{report_id}/items/{item_id}/duplicate", status_code=201)
async def duplicate_item(report_id: str, item_id: str, request: Request, response: Response,
                         principal: Principal = Depends(require(Perm.REPORTS_CREATE))) -> dict[str, Any]:
    async def fn(conn: AsyncConnection, report: dict[str, Any]) -> str:
        item = await _item(conn, report, item_id)
        boxes = await section_boxes(conn, item["section_id"])
        x, y = place(boxes, item["grid_w"], item["grid_h"])
        new_item = {"id": new_id(), "section_id": str(item["section_id"]), "kind": item["item_kind"],
                    "text": item["text_content"], "text_title": item["text_title"], "level": item["text_tone"],
                    "x": x, "y": y, "w": item["grid_w"], "h": item["grid_h"]}
        if item["widget_id"] is not None:
            # A copy that can diverge (another chart type, other filters): no def_hash, so it is not a duplicate.
            wid = new_id()
            await conn.execute(
                text("INSERT INTO content.widgets (id, tenant_id, title, spec, visual, follow_page_filters, "
                     "model_version_id, connection_id, certification_state, owner_id, question, source_kind, "
                     "own_filters, created_by) SELECT :new, tenant_id, left(title || ' (copy)', 200), spec, visual, "
                     "follow_page_filters, model_version_id, connection_id, certification_state, owner_id, question, "
                     "source_kind, own_filters, :u FROM content.widgets WHERE id = :old"),
                {"new": wid, "u": principal.user_id, "old": item["widget_id"]})
            new_item["widget_id"] = wid
        await _insert_item(conn, report, new_item)
        return str(new_item["id"])

    out, new = await mutate(principal, report_id, source="canvas", summary="Duplicated an item", fn=fn,
                            response=response, request_id=_rid(request))
    return {**out, "item_id": new}


# --------------------------------------------------------------------------- layout (API-027)


class LayoutItem(BaseModel):
    item_id: str
    section_id: str
    x: int
    y: int
    w: int
    h: int


class LayoutIn(BaseModel):
    items: list[LayoutItem] = Field(max_length=200)


@router.put("/reports/{report_id}/layout")
async def put_layout(report_id: str, body: LayoutIn, request: Request, response: Response,
                     if_match: str | None = Header(default=None),
                     principal: Principal = Depends(require(Perm.REPORTS_CREATE))) -> dict[str, Any]:
    if if_match is None:
        raise TelaError(ErrorCode.PRECONDITION_FAILED, "Send the report revision in If-Match.")

    async def fn(conn: AsyncConnection, report: dict[str, Any]) -> None:
        items = {str(r["id"]): r for r in await fetch_all(conn, "SELECT * FROM content.report_items WHERE report_id = "
                                                                ":r", {"r": report["id"]})}
        sections = {str(r["id"]) for r in await fetch_all(conn, "SELECT id FROM content.report_sections WHERE "
                                                                "report_id = :r", {"r": report["id"]})}
        given = {i.item_id: i for i in body.items}
        unknown = [i for i in given if i not in items] + [g.section_id for g in given.values() if g.section_id
                                                           not in sections]
        if unknown:
            raise TelaError(ErrorCode.VALIDATION_ERROR, "The layout names items or sections that are not on this "
                            "report.", {"unknown": unknown[:10]})
        by_section: dict[str, list[Box]] = {}
        for iid, row in items.items():
            g = given.get(iid)
            box = Box(iid, g.x, g.y, g.w, g.h) if g else Box(iid, row["grid_x"], row["grid_y"], row["grid_w"],
                                                              row["grid_h"])
            by_section.setdefault(g.section_id if g else str(row["section_id"]), []).append(box)
        errors = [p for boxes in by_section.values() for p in problems(boxes)]
        if errors:
            raise TelaError(ErrorCode.VALIDATION_ERROR, "That layout does not fit the 12-column grid.",
                            {"problems": errors[:20]})
        for sid, boxes in by_section.items():
            for b in compact(boxes):
                await conn.execute(text("UPDATE content.report_items SET section_id = CAST(:s AS uuid), grid_x = :x, "
                                        "grid_y = :y, grid_w = :w, grid_h = :h WHERE id = CAST(:id AS uuid)"),
                                   {"s": sid, "x": b.x, "y": b.y, "w": b.w, "h": b.h, "id": b.id})

    out, _ = await mutate(principal, report_id, source="canvas", summary="Rearranged the layout", fn=fn,
                          if_match=if_match, response=response, request_id=_rid(request))
    return out


# --------------------------------------------------------------------------- undo (API-029)


@router.post("/reports/{report_id}/undo")
async def undo(report_id: str, request: Request, response: Response,
               principal: Principal = Depends(require(Perm.REPORTS_CREATE))) -> dict[str, Any]:
    async with tenant_tx(principal.tenant_id) as conn:
        report = await own_report(conn, principal, report_id, lock=True)
        last = await fetch_one(conn, "SELECT id, inverse_patch, summary FROM content.drafts_undo_log WHERE "
                                     "object_type = 'report' AND object_id = :r AND NOT undone ORDER BY revision DESC "
                                     "LIMIT 1", {"r": report["id"]})
        if last is None:
            raise TelaError(ErrorCode.CONFLICT, "Nothing to undo.")
        await restore_state(conn, report, last["inverse_patch"][0]["value"])
        await conn.execute(text("UPDATE content.drafts_undo_log SET undone = true WHERE id = :id"), {"id": last["id"]})
        await conn.execute(text("UPDATE content.reports SET draft_revision = draft_revision + 1 WHERE id = :id"),
                           {"id": report["id"]})
        fresh = await own_report(conn, principal, report_id)
        out = await report_out(conn, principal, fresh)
    response.headers["ETag"] = f'"{out["revision"]}"'
    return {**out, "undone": last["summary"]}


@router.post("/reports/{report_id}/undo-all")
async def undo_all(report_id: str, request: Request, response: Response,
                   principal: Principal = Depends(require(Perm.REPORTS_CREATE))) -> dict[str, Any]:
    async def fn(conn: AsyncConnection, report: dict[str, Any]) -> None:
        state = report["published_snapshot"] or empty_state(report["title"])
        await restore_state(conn, report, state)

    out, _ = await mutate(principal, report_id, source="system", summary="Undo all — back to the published version",
                          fn=fn, response=response, request_id=_rid(request), audit_action="report.undo_all")
    return out


@router.get("/reports/{report_id}/history")
async def history(report_id: str, principal: Principal = Depends(require(Perm.REPORTS_CREATE))) -> dict[str, Any]:
    async with tenant_tx(principal.tenant_id) as conn:
        report = await own_report(conn, principal, report_id)
        rows = await fetch_all(conn, "SELECT l.revision, l.change_source, l.summary, l.undone, l.created_at, "
                                     "u.display_name FROM content.drafts_undo_log l JOIN iam.users u ON u.id = "
                                     "l.actor_id WHERE l.object_type = 'report' AND l.object_id = :r ORDER BY "
                                     "l.revision DESC LIMIT 50", {"r": report["id"]})
    return {"items": rows}
