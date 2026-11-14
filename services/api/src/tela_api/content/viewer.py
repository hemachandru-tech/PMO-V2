"""Reading: published dashboards, widget runs, definitions and drill (API-022, API-043 to API-045;
FEAT-042 to FEAT-044; US-052, US-054, US-055).

* A dashboard is visible only when published to one of the caller's roles (or to every role);
  anything else is a 404 with no metadata ("You don't have access to this dashboard.").
* Every widget runs live for the caller through ``QueryService``: scope, hidden fields, limits and the
  scope-keyed cache all apply. Page filters are ANDed after scope and only reach widgets whose measure
  can reach the filter's dimension through approved links (RULE-005).
* Drill lists the records behind a mark only if that mark is part of the widget's current result for
  this person (FEAT-044 input rule), 100 per page, capped at 1,000 (RULE-025).
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncConnection

from tela_api.ask.refine import default_time, previous_period_spec
from tela_api.audit import service as audit
from tela_api.content.access import ResolvedWidget, frozen_items, live_version, resolve_widget
from tela_api.content.common import filter_entity, is_custom, run_spec
from tela_api.content.render import render_frozen
from tela_api.content.starters import hidden_report_keys
from tela_api.core.db import fetch_all, fetch_one, tenant_tx
from tela_api.identity.deps import require
from tela_api.identity.permissions import Perm
from tela_api.identity.principal import Principal
from tela_api.identity.scope import resolve_scope
from tela_api.model.store import current_version_id, load_snapshot
from tela_api.query.asof import reporting_date, reporting_date_or_none
from tela_api.query.service import QueryContext, QueryService
from tela_api.query.validator import HIDDEN_MESSAGE, SpecValidator
from tela_api.query.viz import visualise
from tela_common.errors import ErrorCode, TelaError
from tela_query_spec.expr import measure_fields
from tela_query_spec.model import ModelSnapshot
from tela_query_spec.spec import MAX_CONTROLS, Filter, parse_spec, ref_key, ref_kind

router = APIRouter(prefix="/api/v1", tags=["reading"])
NO_ACCESS = "You don't have access to this dashboard."
DRILL_CAP = 1000

_VISIBLE = """
    SELECT d.* FROM content.dashboards d
    WHERE d.status = 'published' AND EXISTS (
      SELECT 1 FROM content.publications p
      WHERE p.dashboard_id = d.id AND (p.all_roles OR p.role_id = ANY(CAST(:roles AS uuid[]))))
"""


def _service(request: Request) -> QueryService:
    service: QueryService = request.app.state.query_service
    return service


def _rid(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


async def visible_dashboards(conn: AsyncConnection, principal: Principal) -> list[dict[str, Any]]:
    return await fetch_all(conn, _VISIBLE + " ORDER BY d.kind, d.title", {"roles": list(principal.role_ids)})


async def visible_dashboard(conn: AsyncConnection, principal: Principal, dashboard_id: str) -> dict[str, Any]:
    try:
        row = await fetch_one(conn, _VISIBLE + " AND d.id = CAST(:id AS uuid)",
                              {"roles": list(principal.role_ids), "id": dashboard_id})
    except Exception:  # noqa: BLE001 - a malformed id is just "no access", never a leak
        row = None
    if row is None:
        raise TelaError(ErrorCode.NOT_FOUND, NO_ACCESS)
    return row


def tile(d: dict[str, Any], counts: dict[str, Any]) -> dict[str, Any]:
    badges = ["certified", "starter"] if d["kind"] == "starter" else ["certified"]
    return {"id": str(d["id"]), "type": "dashboard", "title": d["title"], "badges": badges,
            "version": d["version_no"], "count": {"tabs": counts.get("tabs", 0), "widgets": counts.get("widgets", 0)},
            "last_edited_at": d["updated_at"]}


async def tile_counts(conn: AsyncConnection, ids: list[str]) -> dict[str, dict[str, int]]:
    if not ids:
        return {}
    rows = await fetch_all(
        conn, "SELECT t.dashboard_id, count(DISTINCT t.id) AS tabs, count(w.id) AS widgets "
              "FROM content.dashboard_tabs t LEFT JOIN content.widgets w ON w.dashboard_tab_id = t.id "
              "WHERE t.dashboard_id = ANY(CAST(:ids AS uuid[])) GROUP BY t.dashboard_id", {"ids": ids})
    out = {str(r["dashboard_id"]): {"tabs": r["tabs"], "widgets": r["widgets"]} for r in rows}
    versions = await fetch_all(
        conn, "SELECT d.id, v.frozen_content FROM content.dashboards d JOIN content.dashboard_versions v ON "
              "v.id = d.live_version_id WHERE d.id = ANY(CAST(:ids AS uuid[]))", {"ids": ids})
    for v in versions:
        reports = v["frozen_content"].get("reports", [])
        out[str(v["id"])] = {"tabs": len(reports), "widgets": sum(
            1 for i in frozen_items(v["frozen_content"]) if i.get("kind") == "widget")}
    return out


# --------------------------------------------------------------------------- dashboards (API-043)


@router.get("/published")
async def list_published(principal: Principal = Depends(require(Perm.DASHBOARDS_VIEW))) -> dict[str, Any]:
    async with tenant_tx(principal.tenant_id) as conn:
        rows = await visible_dashboards(conn, principal)
        counts = await tile_counts(conn, [str(r["id"]) for r in rows])
    return {"items": [tile(r, counts.get(str(r["id"]), {})) for r in rows]}


@router.get("/published/{dashboard_id}")
async def get_published(dashboard_id: str, request: Request,
                        principal: Principal = Depends(require(Perm.DASHBOARDS_VIEW))) -> dict[str, Any]:
    async with tenant_tx(principal.tenant_id) as conn:
        d = await visible_dashboard(conn, principal, dashboard_id)
        view = await dashboard_view(conn, principal, d)
        await audit.record(conn, tenant_id=principal.tenant_id, action="dashboard.viewed", object_type="dashboard",
                           object_id=dashboard_id, outcome="success", actor_id=principal.user_id,
                           correlation_id=_rid(request),
                           details={"version": view["version"]} if d["kind"] == "user" else None)
    return view


async def dashboard_view(conn: AsyncConnection, principal: Principal, d: dict[str, Any]) -> dict[str, Any]:
    """The viewer's shape of a visible dashboard for one person; exports render exactly this (US-095)."""
    if d["kind"] == "user":
        version = await live_version(conn, principal, str(d["id"]))
        if version is None:
            raise TelaError(ErrorCode.NOT_FOUND, NO_ACCESS)
        return await render_frozen(conn, principal, d, version["frozen_content"], version_id=str(version["id"]),
                                   version_no=version["version_no"])
    hidden_keys = await hidden_report_keys(conn, principal)
    tabs = [t for t in await fetch_all(conn, "SELECT id, position, title, report_key, texts FROM "
                                             "content.dashboard_tabs WHERE dashboard_id = :d ORDER BY position",
                                       {"d": d["id"]})
            if (str(d["connection_id"]), t["report_key"]) not in hidden_keys]
    if not tabs:
        raise TelaError(ErrorCode.NOT_FOUND, NO_ACCESS)  # every report on it is hidden from the person's roles
    widgets = await fetch_all(conn, "SELECT id, dashboard_tab_id, position, title, spec, visual, placement, "
                                    "follow_page_filters, certification_state FROM content.widgets "
                                    "WHERE dashboard_tab_id = ANY(CAST(:t AS uuid[])) ORDER BY position",
                              {"t": [str(t["id"]) for t in tabs]})
    snap = await load_snapshot(conn, principal.tenant_id, str(d["model_version_id"]))
    try:
        scope = await resolve_scope(conn, principal, str(d["connection_id"]))
        chip, hidden = scope.summary or "All rows", scope.hidden_fields
    except TelaError:
        chip, hidden = "No data scope is set up for your role", frozenset()
    last = await fetch_one(conn, "SELECT max(created_at) AS at FROM ops.query_executions WHERE connection_id = :c "
                                 "AND outcome = 'ok' AND NOT cache_hit", {"c": d["connection_id"]})
    validator = SpecValidator(snap, hidden_fields=hidden)
    reports = {r["item_key"]: str(r["content_id"]) for r in await fetch_all(
        conn, "SELECT item_key, content_id FROM model.starter_pack_items WHERE connection_id = :c AND "
              "starter_pack_id = (SELECT m.installed_pack_id FROM model.data_models m WHERE m.connection_id = :c) "
              "AND item_kind = 'report' AND state = 'active' AND content_id IS NOT NULL", {"c": d["connection_id"]})}

    def widget_out(w: dict[str, Any]) -> dict[str, Any]:
        try:
            is_hidden = validator.validate(parse_spec(w["spec"])[0]).hidden
        except (TelaError, ValueError):
            is_hidden = False
        return {"id": str(w["id"]), "title": w["title"], "visual": w["visual"], "placement": w["placement"],
                "follow_page_filters": w["follow_page_filters"], "hidden": is_hidden,
                "hidden_message": HIDDEN_MESSAGE if is_hidden else None,
                "badges": [w["certification_state"]], "measure": w["spec"]["measure"]["ref"]}

    filters = []
    for f in d["shared_filters"]:
        dim = snap.dimension(ref_key(f["ref"]))
        if dim is not None and f"{dim.entity}.{dim.field}" not in hidden:
            filters.append({"ref": f["ref"], "label": dim.label})
    return {
        "id": str(d["id"]), "title": d["title"], "kind": d["kind"], "layout": d["layout"], "version": d["version_no"],
        "connection_id": str(d["connection_id"]), "model_version_id": str(d["model_version_id"]),
        "locked_scope_chip": chip, "scope_message": "This is your data scope from your Microsoft groups. "
                                                    "Filters can narrow it but not widen it.",
        "as_of": last["at"] if last else None, "filters": filters,
        "reporting_date": reporting_date_or_none(snap.settings, principal.time_zone),
        # A starter tab names its pack-owned report, so a Creator can copy it (REQ-114); its callouts go above the
        # widgets and its footnotes below them (US-158).
        "tabs": [{"id": str(t["id"]), "title": t["title"], "badges": ["starter"],
                  "report_id": reports.get(t["report_key"]),
                  "callouts": [x for x in t["texts"] or [] if x.get("kind") == "callout"],
                  "footnotes": [x["text"] for x in t["texts"] or [] if x.get("kind") == "footnote"],
                  "widgets": [widget_out(w) for w in widgets if w["dashboard_tab_id"] == t["id"]]} for t in tabs],
    }


# --------------------------------------------------------------------------- widget run (API-022)


class PageFilters(BaseModel):
    page_filters: list[Filter] = Field(default_factory=list, max_length=20)


def merge_page_filters(spec: dict[str, Any], snap: ModelSnapshot, filters: list[Filter],
                       follow: bool) -> tuple[dict[str, Any], list[str], list[str]]:
    """Append page filters a widget (or an Ask answer, US-189) can reach; report the ones it cannot (pre-publish check
    4 groundwork). An ad-hoc rule (US-163) reaches from its own entity."""
    applied, skipped = [], []
    primary = spec.get("measure") or next(iter(spec.get("measures") or []), None) or {}  # US-176 "measures" list
    adhoc = primary.get("adhoc")
    measure = snap.measure(ref_key(primary["ref"])) if primary.get("ref") else None
    home = adhoc["entity"] if adhoc else measure.entity if measure else None
    merged = dict(spec)
    merged["filters"] = list(spec.get("filters", []))
    for f in filters if follow else []:
        entity = None
        if ref_kind(f.ref) == "dimension":
            dim = snap.dimension(ref_key(f.ref))
            entity = dim.entity if dim else None
        elif ref_kind(f.ref) == "field":
            entity = ref_key(f.ref).split(".", 1)[0]
        if home is None or entity is None or snap.join_path(home, entity) is None:
            skipped.append(f.ref)
            continue
        merged["filters"].append(f.model_dump())
        applied.append(f.ref)
    return merged, applied, skipped


class RunContext(BaseModel):
    dashboard_version_id: str | None = None


class RunIn(PageFilters):
    context: RunContext = Field(default_factory=RunContext)
    # US-221: what the reader picked in the widget's own filters; they apply to this widget only, page filters or not.
    widget_filters: list[Filter] = Field(default_factory=list, max_length=MAX_CONTROLS)


def apply_widget_filters(spec: dict[str, Any], snap: ModelSnapshot, filters: list[Filter]) -> dict[str, Any]:
    """A reader's widget filters (US-221): only the fields the Creator offered, as "is" or "is any of". They narrow
    the reader's scope like page filters; they never widen it."""
    if not filters:
        return spec
    offered = set((spec.get("visual") or {}).get("controls") or [])
    for f in filters:
        if f.ref not in offered or f.op not in ("=", "in"):
            raise TelaError(ErrorCode.VALIDATION_ERROR, "This widget has no filter for that field.")
    merged, _, skipped = merge_page_filters(spec, snap, filters, True)
    if skipped:
        raise TelaError(ErrorCode.VALIDATION_ERROR, "This widget has no filter for that field.")
    return merged


def widget_filter_info(w: ResolvedWidget, snap: ModelSnapshot) -> dict[str, Any]:
    """The widget's filters for the reader (US-221): the controls they can use and the Creator's fixed filters, with
    their labels. A field the model no longer has is left out."""
    controls = []
    for ref in (w.spec.get("visual") or {}).get("controls") or []:
        if (label := filter_entity(ref, snap)[1]) is not None:
            controls.append({"ref": ref, "label": label})
    fixed = []
    for f in w.own_filters:
        if (label := filter_entity(f.get("ref", ""), snap)[1]) is not None:
            fixed.append({"ref": f["ref"], "label": label, "op": f.get("op"), "values": f.get("values", [])})
    return {"controls": controls, "fixed_filters": fixed}


def hidden_result(w: ResolvedWidget) -> dict[str, Any]:
    return {"hidden": True, "message": HIDDEN_MESSAGE, "reason": "Hidden for your role on this dashboard",
            "columns": [], "rows": [], "row_count": 0, "truncated": False, "as_of": None, "filters_applied": [],
            "filters_not_applied": [], "badge": w.badge}


async def _run_widget(request: Request, principal: Principal, widget_id: str, page_filters: list[Filter],
                      version_id: str | None = None, widget_filters: list[Filter] | None = None
                      ) -> tuple[ResolvedWidget, dict[str, Any], ModelSnapshot, dict[str, Any] | None]:
    async with tenant_tx(principal.tenant_id) as conn:
        w = await resolve_widget(conn, principal, widget_id, dashboard_version_id=version_id)
        snap = await load_snapshot(conn, principal.tenant_id, w.model_version_id)
    if w.hidden_for(principal):
        return w, hidden_result(w), snap, None
    if is_custom(w.spec):
        result = await run_spec(_service(request), {**w.spec, "follow_page_filters": w.follow_page_filters},
                                principal, QueryContext(surface="widget_run", request_id=_rid(request),
                                                        widget_id=widget_id),
                                [f.model_dump() for f in page_filters])
        return w, result, snap, None
    spec, applied, skipped = merge_page_filters(w.spec, snap, page_filters, w.follow_page_filters)
    spec = apply_widget_filters(spec, snap, widget_filters or [])
    ctx = QueryContext(surface="widget_run", request_id=_rid(request), widget_id=widget_id,
                       page_filter_hash=hashlib.sha256(json.dumps(
                           [f.model_dump() for f in [*page_filters, *(widget_filters or [])]],
                           sort_keys=True).encode()).hexdigest()[:16])
    detail = (spec.get("visual") or {}).get("detail") if w.visual == "table" else None
    result = await detail_rows(_service(request), spec, detail, snap, principal, ctx) if detail else \
        await _service(request).execute(spec, principal, ctx)
    result.update(filters_applied=applied, filters_not_applied=skipped)
    return w, result, snap, spec


DETAIL_ROWS = 500  # a detail table reads its first 500 records; "See all records" pages through up to 1,000


async def detail_rows(service: QueryService, spec: dict[str, Any], detail: dict[str, Any], snap: ModelSnapshot,
                      principal: Principal, ctx: QueryContext) -> dict[str, Any]:
    """A detail table's run (US-158): the records of the measure's population with the table's columns and default
    sort, shaped like any widget run (columns, rows, row_count = every matching record, truncated, as_of). The key
    columns come first for the record drawer; ``detail.columns`` says which ones the table shows."""
    m = snap.measure(ref_key(spec["measure"]["ref"])) if ref_kind(spec["measure"]["ref"]) == "measure" else None
    entity = snap.entity(m.entity) if m is not None else None
    if entity is None:
        raise TelaError(ErrorCode.SPEC_INVALID, "A detail table needs a certified measure for its population.")
    unknown = [k for k in detail["columns"] if entity.field(k) is None]
    if unknown:
        raise TelaError(ErrorCode.SPEC_INVALID, "This table lists fields the model no longer has. Ask its owner to "
                        "update it.", {"admin_detail": {"fields": unknown}})
    sort = detail.get("sort")
    out = await service.records(spec, principal, ctx, marks={}, limit=DETAIL_ROWS, cap=DETAIL_ROWS,
                                columns=detail["columns"],
                                order=[(sort["field"], sort.get("dir") == "desc")] if sort else None)
    if out.get("hidden"):
        return {**out, "row_count": 0, "truncated": False, "as_of": None}
    shown = [c["name"] for c in out["columns"] if c["name"] in detail["columns"]]
    return {"model_version": {"id": snap.model_version_id, "version_no": snap.version_no},
            "connection_id": snap.connection_id, "scope": out["scope"], "badge": "certified", "hidden": False,
            "columns": out["columns"], "rows": out["rows"], "row_count": out["total"],
            "truncated": out["total"] > len(out["rows"]),
            "notes": [f"Showing the first {len(out['rows']):,} of {out['total']:,} records."]
            if out["total"] > len(out["rows"]) else [],
            "as_of": out["as_of"], "detail": {"columns": shown, "sort": sort, "export_name": detail.get("export_name"),
                                              "entity": out["entity"], "primary_key": out["primary_key"]}}


SPARK_RANGE = "last_12_months"
SPARK_MIN_POINTS = 3  # fewer months read as noise, not a trend


async def kpi(request: Request, principal: Principal, widget_id: str, spec: dict[str, Any], snap: ModelSnapshot,
              result: dict[str, Any]) -> dict[str, Any] | None:
    """KPI extras for a single-figure widget (US-139): the previous-period change and a monthly sparkline.

    Both run through the same scoped path as the figure. The change needs a time range on the widget; the
    sparkline needs a date the measure can reach. Either is null when it can't be computed, and the web app shows
    nothing rather than computing a figure itself (COMP-001).
    """
    if len(result["rows"]) != 1 or not result["columns"] or result["columns"][0]["kind"] != "measure":
        return None
    value = result["rows"][0][0]
    if not isinstance(value, int | float):
        return None
    ctx = QueryContext(surface="widget_run", request_id=_rid(request), widget_id=widget_id)
    base = {k: v for k, v in spec.items() if k not in ("compare", "visual")}
    change = None
    prev = previous_period_spec(base, reporting_date(snap.settings, principal.time_zone))
    if prev is not None:
        try:
            before = (await _service(request).execute(prev[0], principal, ctx))["rows"]
        except TelaError:
            before = []
        b = before[0][0] if before else None
        if isinstance(b, int | float):
            diff = value - b
            change = {"previous": b, "change": diff, "change_pct": round(diff / b * 100, 1) if b else None,
                      "previous_range": prev[1]}
    spark = None
    t = base.get("time") or {}
    ref = t.get("ref") or default_time(base, snap)
    if ref is not None:
        trend = {**base, "breakdowns": [], "extra_measures": [],
                 "time": {"ref": ref, "grain": "month", "range": t.get("range") or {"relative": SPARK_RANGE}}}
        try:
            res = await _service(request).execute(trend, principal, ctx)
        except TelaError:
            res = None
        if res is not None and len(res["rows"]) >= SPARK_MIN_POINTS:
            dim = snap.dimension(ref_key(ref)) if ref_kind(ref) == "dimension" else None
            spark = {"grain": "month", "by": dim.label if dim else ref_key(ref),
                     "range": (t.get("range") or {}).get("relative") or ("custom" if t.get("range") else SPARK_RANGE),
                     "points": [{"period": r[0], "value": r[1]} for r in res["rows"]]}
    m = snap.measure(ref_key(spec["measure"]["ref"])) if spec["measure"]["ref"].startswith("measure:") else None
    return {"value": value, "format": result["columns"][0].get("format"), "change": change, "sparkline": spark,
            "tone": figure_tone(value, (spec.get("visual") or {}).get("tone")),
            "definition": m.definition if m is not None else None}


def figure_tone(value: float, tone: dict[str, Any] | None) -> dict[str, Any] | None:
    """A figure's tone from the widget's threshold (US-157): critical, warning or good, with the threshold as words
    so colour is never the only signal. None when the widget has no threshold."""
    if not tone or (tone.get("warning") is None and tone.get("critical") is None):
        return None
    worse = tone.get("higher_is_worse", True)
    word = "at or above" if worse else "at or below"
    for level, key in (("critical", "critical"), ("warning", "warning")):
        if (t := tone.get(key)) is not None and (value >= t if worse else value <= t):
            return {"tone": level, "label": level.capitalize(), "rule": f"{level.capitalize()} {word} {t:g}"}
    t = tone.get("warning") if tone.get("warning") is not None else tone.get("critical")
    return {"tone": "good", "label": "Good", "rule": f"Good {'below' if worse else 'above'} {t:g}"}


@router.post("/widgets/{widget_id}/run")
async def run_widget(widget_id: str, body: RunIn, request: Request,
                     principal: Principal = Depends(require(Perm.DASHBOARDS_VIEW))) -> dict[str, Any]:
    w, result, snap, spec = await _run_widget(request, principal, widget_id, body.page_filters,
                                              body.context.dashboard_version_id, body.widget_filters)
    viz = visualise(result, snap, w.visual, w.spec.get("visual")) if not result.get("hidden") \
        else visualise({"hidden": True}, snap)
    extras = await kpi(request, principal, widget_id, spec, snap, result) \
        if spec is not None and viz["visual"] == "figure" else None
    return {**result, "widget": {"id": widget_id, "title": w.title, "source": w.source}, "badges": [w.badge],
            "viz": viz, "kpi": extras,
            **(widget_filter_info(w, snap) if not is_custom(w.spec) else {"controls": [], "fixed_filters": []})}


# --------------------------------------------------------------------------- drill (API-045)


class DrillIn(RunIn):
    mark: dict[str, Any] = Field(default_factory=dict)
    offset: int = Field(default=0, ge=0, le=DRILL_CAP)


def _mark_in_result(result: dict[str, Any], mark: dict[str, Any]) -> bool:
    names = [c["name"] for c in result["columns"]]
    if any(k not in names for k in mark):
        return False
    idx = {k: names.index(k) for k in mark}
    return any(all(str(r[i]) == str(mark[k]) if mark[k] is not None else r[i] is None for k, i in idx.items())
               for r in result["rows"])


async def _drill(request: Request, principal: Principal, spec: dict[str, Any], result: dict[str, Any],
                 mark: dict[str, Any], offset: int, object_type: str, object_id: str) -> dict[str, Any]:
    if result.get("hidden"):
        return {"hidden": True, "message": HIDDEN_MESSAGE, "columns": [], "rows": []}
    if mark and not _mark_in_result(result, mark):
        raise TelaError(ErrorCode.VALIDATION_ERROR, "That mark is not part of this chart for you.")
    out = await _service(request).records(spec, principal, QueryContext(surface="drill", request_id=_rid(request)),
                                          marks=mark, offset=offset, cap=DRILL_CAP)
    async with tenant_tx(principal.tenant_id) as conn:
        await audit.record(conn, tenant_id=principal.tenant_id, action="drill.viewed", object_type=object_type,
                           object_id=object_id, outcome="success", actor_id=principal.user_id,
                           correlation_id=_rid(request), details={"records": out.get("total"), "offset": offset})
    return {**out, "record_link_template": None, "record_view": "panel"}


@router.post("/widgets/{widget_id}/drill")
async def drill_widget(widget_id: str, body: DrillIn, request: Request,
                       principal: Principal = Depends(require(Perm.DRILL_USE))) -> dict[str, Any]:
    async with tenant_tx(principal.tenant_id) as conn:
        w = await resolve_widget(conn, principal, widget_id, dashboard_version_id=body.context.dashboard_version_id)
        snap = await load_snapshot(conn, principal.tenant_id, w.model_version_id)
    if w.hidden_for(principal):
        return {"hidden": True, "message": HIDDEN_MESSAGE, "columns": [], "rows": []}
    if is_custom(w.spec):
        raise TelaError(ErrorCode.VALIDATION_ERROR, "Records can't be listed for a custom SQL widget.")
    spec, _, _ = merge_page_filters(w.spec, snap, body.page_filters, w.follow_page_filters)
    spec = apply_widget_filters(spec, snap, body.widget_filters)
    result = await _service(request).execute(spec, principal, QueryContext(surface="widget_run",
                                                                           request_id=_rid(request),
                                                                           widget_id=widget_id))
    return await _drill(request, principal, spec, result, body.mark, body.offset, "widget", widget_id)


class RecordsIn(BaseModel):
    spec: dict[str, Any]
    mark: dict[str, Any] = Field(default_factory=dict)
    offset: int = Field(default=0, ge=0, le=DRILL_CAP)


@router.post("/query/records")
async def query_records(body: RecordsIn, request: Request,
                        principal: Principal = Depends(require(Perm.DRILL_USE))) -> dict[str, Any]:
    """Drill from a chat answer: same rules as a widget drill."""
    result = await _service(request).execute(body.spec, principal, QueryContext(surface="drill",
                                                                                request_id=_rid(request)))
    return await _drill(request, principal, body.spec, result, body.mark, body.offset, "spec",
                        hashlib.sha256(json.dumps(body.spec, sort_keys=True).encode()).hexdigest()[:16])


# --------------------------------------------------------------------------- definitions (API-044)


@router.get("/definitions/{ref}")
async def definition(ref: str, connection_id: str, request: Request, model_version_id: str | None = None,
                     principal: Principal = Depends(require(Perm.DEFINITIONS_VIEW))) -> dict[str, Any]:
    async with tenant_tx(principal.tenant_id) as conn:
        version_id = model_version_id or await current_version_id(conn, connection_id)
        if version_id is None:
            raise TelaError(ErrorCode.MODEL_NOT_APPROVED, "This data source has no approved model yet.")
        snap = await load_snapshot(conn, principal.tenant_id, version_id)
        if snap.connection_id != connection_id:
            raise TelaError(ErrorCode.NOT_FOUND, "Definition not found.")
        version = await fetch_one(
            conn, "SELECT v.version_no, v.approved_at, u.display_name FROM model.model_versions v "
                  "LEFT JOIN iam.users u ON u.id = v.approved_by WHERE v.id = CAST(:v AS uuid)", {"v": version_id})
        try:
            scope = await resolve_scope(conn, principal, connection_id)
            scope_text, hidden = scope.summary, scope.hidden_fields
        except TelaError:
            scope_text, hidden = "No data scope is set up for your role", frozenset()
    kind = ref_kind(ref) if ":" in ref else ""
    key = ref.split(":", 1)[1] if ":" in ref else ref
    certified = {"by": version["display_name"] if version else None, "at": version["approved_at"] if version else None,
                 "model_version": version["version_no"] if version else None}
    if kind == "measure" and (m := snap.measure(key)) is not None:
        ent = snap.entity(m.entity)
        assert ent is not None
        used = sorted(measure_fields(m.expression))
        if any(f"{m.entity}.{f}" in hidden for f in used):
            raise TelaError(ErrorCode.NOT_FOUND, HIDDEN_MESSAGE)
        return {"ref": ref, "kind": "measure", "label": m.label, "badge": "certified", "rule_text": m.plain_rule,
                "rule_ref": m.rule_ref, "fields_used": [_label(ent.field(f), f) for f in used],
                "blank_handling": m.blank_handling, "certified": certified, "in_scope_text": f"Within your scope: "
                f"{scope_text}", "badge_text": _badge_text(certified),
                "can_show_query": principal.has(Perm.WIDGETS_CREATE)}
    if kind == "dimension" and (d := snap.dimension(key)) is not None:
        if f"{d.entity}.{d.field}" in hidden:
            raise TelaError(ErrorCode.NOT_FOUND, HIDDEN_MESSAGE)
        ent = snap.entity(d.entity)
        field = ent.field(d.field) if ent else None
        return {"ref": ref, "kind": "dimension", "label": d.label, "badge": "certified",
                "rule_text": (field.description if field and field.description else f"{d.label} of each "
                              f"{ent.label.lower() if ent else 'record'}."),
                "fields_used": [field.label if field else d.field], "value_labels": d.value_labels,
                "blank_handling": (field.blank_handling if field and field.blank_handling
                                   else "Blank values are shown as their own group."),
                "certified": certified, "in_scope_text": f"Within your scope: {scope_text}",
                "badge_text": _badge_text(certified), "can_show_query": False}
    raise TelaError(ErrorCode.NOT_FOUND, "Definition not found.")


def _label(field: Any, fallback: str) -> str:
    return str(field.label) if field is not None else fallback


def _badge_text(certified: dict[str, Any]) -> str:
    when = certified["at"].strftime("%-d %b %Y") if certified.get("at") else "approval"
    who = certified.get("by") or "the PMO admin"
    return f"✓ Certified — approved by {who} on {when} (model v{certified.get('model_version')})"


class ExplainIn(BaseModel):
    spec: dict[str, Any]
    page_filters: list[Filter] = Field(default_factory=list, max_length=20)


@router.post("/query/explain")
async def explain(body: ExplainIn, request: Request,
                  principal: Principal = Depends(require(Perm.WIDGETS_CREATE))) -> dict[str, Any]:
    """"Show query" for Creators and Admins (FEAT-043): the SQL with the scope part labelled."""
    spec = dict(body.spec)
    spec["filters"] = [*spec.get("filters", []), *(f.model_dump() for f in body.page_filters)]
    return await _service(request).explain(spec, principal, QueryContext(surface="preview", request_id=_rid(request)))
