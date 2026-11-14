"""Exports to PDF and Excel (US-095 to US-097; FEAT-045; API-046; DATA-049; REQ-107, REQ-108; TEST-054, TEST-062).

DEC-15 working position: export is a per-role permission (``export.use``, held by every system role in the seed and
changed by Admins per role); 10 jobs per hour per person; at most ``export_max_rows`` rows across a file, each widget
still capped at the query row limit (RULE-006); a signed download link lives 15 minutes; the file 24 hours.

Flow. ``POST /exports`` checks the person can see the dashboard, stores a ``queued`` job with the page filters and
emits ``export.requested`` in the same transaction. The worker's outbox handler queues the render on the ``export``
queue. The worker never reads source data itself: it asks ``/internal/exports/{id}/start``, which re-resolves the
person's access from their stored group memberships *now* (REQ-108: revoked since asking → refused and audited) and
returns the dashboard exactly as the viewer renders it for them — hidden widgets left out, every widget run through
the one scoped path. The worker renders the file (Playwright print route for PDF, openpyxl for Excel) and hands it
back to ``/internal/exports/{id}/file``; the API stores it under the tenant's prefix, audits ``export.created`` (scope
and filters, never data) and notifies the person. Downloads go through the API with a signed, 15-minute token, so
object storage is never exposed to browsers.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, date, datetime, timedelta
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from tela_api import outbox
from tela_api.audit import service as audit
from tela_api.content.access import live_version
from tela_api.content.common import is_custom, notify, remember, replay
from tela_api.content.reports import TEXT_KINDS
from tela_api.content.viewer import _run_widget, dashboard_view, kpi, visible_dashboard
from tela_api.core.db import fetch_all, fetch_one, tenant_tx
from tela_api.core.metrics import EXPORT_JOBS
from tela_api.identity.deps import require
from tela_api.identity.permissions import Perm
from tela_api.identity.principal import Principal, principal_for_user
from tela_api.query.viz import visualise
from tela_common import service_auth
from tela_common.errors import ErrorCode, TelaError
from tela_common.ids import new_id
from tela_common.logging import get_logger
from tela_common.objectstore import ObjectStore, tenant_key
from tela_query_spec.spec import Filter, ref_key, ref_kind

log = get_logger(__name__)

router = APIRouter(prefix="/api/v1", tags=["exports"])
internal = APIRouter(prefix="/api/v1/internal/exports", tags=["internal"], include_in_schema=False)

ROUTE = "POST /exports"
DOWNLOAD_AUDIENCE = "tela-export-download"
WORKER_AUDIENCE = "tela-api-internal"
FORMATS = {"pdf": ("application/pdf", "PDF"),
           "xlsx": ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "Excel")}
STATUS_OUT = {"queued": "queued", "running": "running", "done": "succeeded", "failed": "failed", "expired": "expired"}
TOO_LARGE = "This export is larger than allowed."  # MSG-028
NO_LONGER = "You no longer have access to export this dashboard."
RENDER_FAILED = "The export couldn't be created. Try again in a few minutes."


def _settings(request: Request) -> Any:
    return request.app.state.settings


def _rid(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


def _signing_key(request: Request) -> bytes:
    key = _settings(request).service_signing_key
    if not key:
        raise TelaError(ErrorCode.CONFIGURATION_ERROR, "Exports are not set up on this installation.")
    return str(key).encode()


async def _store(request: Request) -> ObjectStore:
    store: ObjectStore | None = getattr(request.app.state, "object_store", None)
    if store is None:
        raise TelaError(ErrorCode.CONFIGURATION_ERROR, "File storage is not set up on this installation.")
    if not getattr(request.app.state, "object_store_ready", False):
        await store.ensure_bucket()
        request.app.state.object_store_ready = True
    return store


# --------------------------------------------------------------------------- public API (API-046)


class ExportIn(BaseModel):
    dashboard_id: str = Field(max_length=64)
    format: Literal["pdf", "xlsx"]
    page_filters: list[Filter] = Field(default_factory=list, max_length=20)
    dashboard_version_id: str | None = Field(default=None, max_length=64)


def _download_token(request: Request, job: dict[str, Any]) -> tuple[str, datetime]:
    minutes = int(_settings(request).export_link_minutes)
    token = service_auth.issue(_signing_key(request), sub=str(job["requested_by"]), aud=DOWNLOAD_AUDIENCE,
                               ttl_s=minutes * 60, claims={"job": str(job["id"]), "tenant": str(job["tenant_id"])})
    return token, datetime.now(UTC) + timedelta(minutes=minutes)


def job_out(request: Request, job: dict[str, Any]) -> dict[str, Any]:
    status = STATUS_OUT[job["status"]]
    out: dict[str, Any] = {
        "job_id": str(job["id"]), "status": status, "format": job["format"], "dashboard_id": str(job["object_id"]),
        "dashboard_version_id": str(job["dashboard_version_id"]) if job["dashboard_version_id"] else None,
        "created_at": job["created_at"], "finished_at": job["finished_at"], "file_name": job["file_name"],
        "size_bytes": job["size_bytes"], "row_count": job["row_count"], "excluded_widgets": job["excluded_widgets"],
        "file_expires_at": job["expires_at"] if status == "succeeded" else None,
        "download_url": None, "expires_at": None,
        "error": {"code": job["error_code"], "message": job["error"]} if status == "failed" else None,
    }
    if status == "succeeded" and job["expires_at"] and job["expires_at"] > datetime.now(UTC):
        token, until = _download_token(request, job)
        out["download_url"] = f"/api/v1/exports/{job['id']}/download?token={token}"
        out["expires_at"] = min(until, job["expires_at"])
    return out


async def _job(conn: AsyncConnection, job_id: str, user_id: str | None = None) -> dict[str, Any]:
    sql = "SELECT * FROM ops.export_jobs WHERE id = CAST(:id AS uuid)"
    if user_id is not None:
        sql += " AND requested_by = CAST(:u AS uuid)"
    try:
        row = await fetch_one(conn, sql, {"id": job_id, "u": user_id})
    except Exception:  # noqa: BLE001 - a malformed id is just "not found"
        row = None
    if row is None:
        raise TelaError(ErrorCode.NOT_FOUND, "Export not found.")
    return row


@router.post("/exports", status_code=202)
async def create_export(body: ExportIn, request: Request, response: Response,
                        idempotency_key: str | None = Header(default=None),
                        principal: Principal = Depends(require(Perm.EXPORT_USE, Perm.DASHBOARDS_VIEW))
                        ) -> dict[str, Any]:
    settings = _settings(request)
    async with tenant_tx(principal.tenant_id) as conn:
        if (prior := await replay(conn, principal, ROUTE, idempotency_key)) is not None:
            return prior
        d = await visible_dashboard(conn, principal, body.dashboard_id)
        version_id = None
        if d["kind"] == "user":
            version = await live_version(conn, principal, body.dashboard_id)
            if version is None:
                raise TelaError(ErrorCode.NOT_FOUND, "You don't have access to this dashboard.")
            version_id = str(version["id"])
            if body.dashboard_version_id and body.dashboard_version_id != version_id:
                raise TelaError(ErrorCode.CONFLICT, "A newer version of this dashboard is live. Reload it and "
                                                    "export again.")
        recent = await fetch_one(
            conn, "SELECT count(*) AS n, min(created_at) AS first FROM ops.export_jobs WHERE requested_by = "
                  "CAST(:u AS uuid) AND created_at > now() - interval '1 hour'", {"u": principal.user_id})
        if recent and recent["n"] >= settings.export_rate_per_hour:
            wait = max(60, int((recent["first"] + timedelta(hours=1) - datetime.now(UTC)).total_seconds()))
            raise TelaError(ErrorCode.RATE_LIMITED, f"You can export {settings.export_rate_per_hour} times an hour. "
                                                    f"Try again in {-(-wait // 60)} minutes.",
                            {"retry_after_s": wait})
        job_id = new_id()
        filters = [f.model_dump() for f in body.page_filters]
        await conn.execute(
            text("INSERT INTO ops.export_jobs (id, tenant_id, requested_by, object_type, object_id, "
                 "dashboard_version_id, format, filters, created_by) VALUES (:id, :t, CAST(:u AS uuid), 'dashboard', "
                 "CAST(:o AS uuid), CAST(:v AS uuid), :f, CAST(:filters AS jsonb), CAST(:u AS uuid))"),
            {"id": job_id, "t": principal.tenant_id, "u": principal.user_id, "o": body.dashboard_id,
             "v": version_id, "f": body.format, "filters": json.dumps(filters, default=str)})
        await outbox.emit(conn, tenant_id=principal.tenant_id, topic="export.requested", aggregate_id=job_id,
                          payload={"job_id": job_id, "format": body.format})
        await audit.record(conn, tenant_id=principal.tenant_id, action="export.requested", object_type="dashboard",
                           object_id=body.dashboard_id, outcome="allow", actor_id=principal.user_id,
                           correlation_id=_rid(request),
                           details={"job_id": job_id, "format": body.format, "filters": filters,
                                    "version_id": version_id})
        out = job_out(request, await _job(conn, job_id))
        await remember(conn, principal, ROUTE, idempotency_key, 202, out)
    response.headers["Location"] = f"/api/v1/exports/{job_id}"
    return out


@router.get("/exports/{job_id}")
async def get_export(job_id: str, request: Request,
                     principal: Principal = Depends(require(Perm.EXPORT_USE))) -> dict[str, Any]:
    async with tenant_tx(principal.tenant_id) as conn:
        return job_out(request, await _job(conn, job_id, principal.user_id))


@router.get("/exports/{job_id}/download", include_in_schema=False)
async def download(job_id: str, request: Request, token: str = Query(max_length=2000)) -> Response:
    """The signed link: no bearer token (a plain browser download), bound to this job, its requester and 15 minutes."""
    gone = TelaError(ErrorCode.NOT_FOUND, "This download link has expired. Open the dashboard and export again.")
    try:
        claims = service_auth.verify(_signing_key(request), token, aud=DOWNLOAD_AUDIENCE)
    except TelaError:
        raise gone from None
    if claims.get("job") != job_id:
        raise gone
    tenant_id, user_id = str(claims.get("tenant")), str(claims.get("sub"))
    async with tenant_tx(tenant_id) as conn:
        try:
            job = await _job(conn, job_id, user_id)
        except TelaError:
            raise gone from None
        if job["status"] != "done" or not job["object_key"] or job["expires_at"] <= datetime.now(UTC):
            raise gone
    data = await (await _store(request)).get(job["object_key"])
    async with tenant_tx(tenant_id) as conn:
        await audit.record(conn, tenant_id=tenant_id, action="export.downloaded", object_type="dashboard",
                           object_id=str(job["object_id"]), outcome="success", actor_id=user_id,
                           correlation_id=_rid(request), details={"job_id": job_id, "format": job["format"]})
    name = job["file_name"] or f"tela-export.{job['format']}"
    return Response(content=data, media_type=FORMATS[job["format"]][0], headers={
        "Content-Disposition": f'attachment; filename="{name}"', "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff"})


# --------------------------------------------------------------------------- the file's content


def _zone(name: str | None) -> ZoneInfo:
    try:
        return ZoneInfo(name or "UTC")
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


def _value_text(v: Any) -> str:
    return "Not set" if v is None else str(v)


def filters_text(filters: list[dict[str, Any]], labels: dict[str, str]) -> str:
    parts = []
    for f in filters:
        label = labels.get(f["ref"]) or ref_key(f["ref"]).replace("_", " ").capitalize()
        values = ", ".join(_value_text(v) for v in f.get("values", []))
        op = f["op"]
        if op in ("=", "in"):
            parts.append(f"{label} = {values}")
        elif op in ("is_null", "not_null"):
            parts.append(f"{label} {'is blank' if op == 'is_null' else 'is not blank'}")
        else:
            parts.append(f"{label} {op} {values}")
    return " · ".join(parts) or "No filters"


def date_text(iso: str | None) -> str | None:
    """A reporting date in words for files: 2026-09-30 → 30 Sep 2026."""
    return date.fromisoformat(iso).strftime("%-d %b %Y") if iso else None


def file_name(title: str, fmt: str, when: datetime) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "-", title).strip("-")[:80] or "Tela-export"
    return f"{slug}-{when:%Y-%m-%d}.{fmt}"


def _definition(spec: dict[str, Any], snap: Any, result: dict[str, Any]) -> dict[str, Any]:
    if is_custom(spec):
        return {"measure": None, "label": "Custom SQL", "rule_text": "Written in SQL by its author; Tela checked it "
                                                                    "was read-only and applied your scope.",
                "rule_ref": None, "blank_handling": None}
    ref = spec.get("measure", {}).get("ref", "")
    m = snap.measure(ref_key(ref)) if ref_kind(ref) == "measure" else None
    if m is not None:
        return {"measure": ref, "label": m.label, "rule_text": m.plain_rule, "rule_ref": m.rule_ref,
                "blank_handling": m.blank_handling}
    col = next((c for c in result.get("columns", []) if c.get("kind") == "measure"), None)
    return {"measure": ref, "label": col["label"] if col else ref_key(ref), "rule_text": None, "rule_ref": None,
            "blank_handling": None}


async def build_payload(request: Request, principal: Principal, job: dict[str, Any], d: dict[str, Any],
                        view: dict[str, Any], tz: ZoneInfo) -> dict[str, Any]:
    """Everything a renderer needs, for this person only: no row ever leaves without the scoped path."""
    page_filters = [Filter.model_validate(f) for f in job["filters"]]
    labels = {f["ref"]: f["label"] for f in view.get("filters", [])}
    version_id = str(job["dashboard_version_id"]) if job["dashboard_version_id"] else None
    excluded = rows_total = 0
    tabs = []
    for tab in view["tabs"]:
        sections = tab.get("sections") or [{"id": None, "title": None, "kind": "body",
                                            "items": [{**w, "kind": "widget"} for w in tab.get("widgets", [])]}]
        out_sections = []
        for s in sections:
            items = []
            for i in s["items"]:
                if i["kind"] in TEXT_KINDS:
                    items.append({k: i[k] for k in ("kind", "text", "title", "level", "placement") if k in i})
                    continue
                if i.get("hidden"):
                    excluded += 1  # FEAT-045 A2: hidden for the role → not in the file at all
                    continue
                item: dict[str, Any] = {"kind": "widget", "id": i["id"], "title": i["title"], "visual": i["visual"],
                                        "placement": i["placement"], "badges": i.get("badges", [])}
                try:
                    w, result, snap, spec = await _run_widget(request, principal, i["id"], page_filters, version_id)
                except TelaError as exc:
                    items.append({**item, "error": exc.message})
                    continue
                if result.get("hidden"):
                    excluded += 1
                    continue
                viz = visualise(result, snap, w.visual, w.spec.get("visual"))
                extras = await kpi(request, principal, i["id"], spec, snap, result) \
                    if spec is not None and viz["visual"] == "figure" else None
                rows_total += len(result["rows"])
                if result.get("detail"):  # a detail table: its columns and the name its sheet takes (US-158)
                    item.update(detail_columns=result["detail"]["columns"],
                                export_name=result["detail"].get("export_name"))
                item.update(
                    columns=result["columns"], rows=result["rows"], row_count=result["row_count"],
                    truncated=result.get("truncated", False), notes=result.get("notes", []),
                    filters_applied=[labels.get(r, ref_key(r)) for r in result.get("filters_applied", [])],
                    filters_not_applied=[labels.get(r, ref_key(r)) for r in result.get("filters_not_applied", [])],
                    viz=viz, kpi=extras, definition=_definition(w.spec, snap, result))
                items.append(item)
            out_sections.append({"title": s.get("title"), "kind": s.get("kind"), "items": items})
        tabs.append({"title": tab.get("group_title") or tab["title"], "sections": out_sections,
                     "reporting_date": date_text(tab.get("reporting_date") or view.get("reporting_date")),
                     "callouts": tab.get("callouts", []), "footnotes": tab.get("footnotes", [])})
    now = datetime.now(UTC).astimezone(tz)
    as_of = view.get("as_of")
    as_of_text = as_of.astimezone(tz).strftime("%Y-%m-%d %H:%M") if isinstance(as_of, datetime) else "not yet known"
    reporting = date_text(view.get("reporting_date"))
    ftext = filters_text(job["filters"], labels)
    version = view.get("version")
    # "Data as of" is the reporting date the rules count from (US-158); the refresh time follows it.
    as_of_words = f"Data as of {reporting} · Refreshed {as_of_text}" if reporting else f"Data as of {as_of_text}"
    footer = " · ".join(p for p in (ftext if job["filters"] else None, f"Scope: {view['locked_scope_chip']}",
                                    as_of_words, f"v{version}" if version else None,
                                    f"Exported {now:%Y-%m-%d %H:%M} {now.tzname()}") if p)
    return {
        "job": {"id": str(job["id"]), "format": job["format"], "requested_by": principal.display_name},
        "dashboard": {"id": str(d["id"]), "title": view["title"], "kind": view["kind"], "layout": view["layout"],
                      "version": version},
        "scope": view["locked_scope_chip"], "as_of": as_of_text, "reporting_date": reporting, "time_zone": str(tz),
        "filters": [{"label": labels.get(f["ref"]) or ref_key(f["ref"]), "op": f["op"],
                     "values": f.get("values", [])} for f in job["filters"]],
        "filters_text": ftext, "exported_at": now.strftime("%Y-%m-%d %H:%M ") + (now.tzname() or ""),
        "footer": footer, "tabs": tabs, "excluded_widgets": excluded, "row_count": rows_total,
        "file_name": file_name(view["title"], job["format"], now),
        "branding": await _branding(request, principal.tenant_id),
    }


async def _branding(request: Request, tenant_id: str) -> dict[str, Any]:
    from tela_api.platform.branding import export_branding  # branding imports this module's storage helpers

    return await export_branding(request, tenant_id)


# --------------------------------------------------------------------------- worker-facing (internal)


def require_worker(request: Request) -> dict[str, Any]:
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise TelaError(ErrorCode.TOKEN_INVALID, "Sign in to continue.")
    try:
        claims: dict[str, Any] = service_auth.verify(_signing_key(request), token.strip(), aud=WORKER_AUDIENCE)
    except TelaError as exc:
        raise TelaError(ErrorCode.TOKEN_INVALID, "Sign in to continue.") from exc
    if claims.get("sub") != "tela-worker":
        raise TelaError(ErrorCode.FORBIDDEN, "You don't have permission to do that.")
    return claims


async def _fail(conn: AsyncConnection, request: Request, job: dict[str, Any], code: str, message: str, *,
                outcome: Literal["deny", "failure"] = "failure") -> None:
    EXPORT_JOBS.labels(status="failed").inc()
    await conn.execute(text("UPDATE ops.export_jobs SET status = 'failed', error_code = :c, error = :m, "
                            "finished_at = now() WHERE id = :id"), {"c": code, "m": message[:500], "id": job["id"]})
    await audit.record(conn, tenant_id=str(job["tenant_id"]), action="export.created", object_type="dashboard",
                       object_id=str(job["object_id"]), outcome=outcome, actor_id=str(job["requested_by"]),
                       correlation_id=_rid(request), severity="security" if outcome == "deny" else "normal",
                       details={"job_id": str(job["id"]), "format": job["format"], "reason": code})
    await notify(conn, str(job["tenant_id"]), [str(job["requested_by"])], kind="export.failed",
                 title="Your export didn't finish", body=message,
                 link=f"/dashboards/{job['object_id']}")


class StartIn(BaseModel):
    tenant_id: str = Field(max_length=64)


@internal.post("/{job_id}/start")
async def start(job_id: str, body: StartIn, request: Request,
                _: dict[str, Any] = Depends(require_worker)) -> dict[str, Any]:
    """Re-check access now (REQ-108), mark the job running and return what to render."""
    settings = _settings(request)
    async with tenant_tx(body.tenant_id) as conn:
        job = await _job(conn, job_id)
        if job["status"] not in ("queued", "running"):
            raise TelaError(ErrorCode.CONFLICT, "This export has already finished.", {"status": job["status"]})
        principal = await principal_for_user(conn, body.tenant_id, str(job["requested_by"]))
        refusal = None
        d = None
        if principal is None or not (principal.has(Perm.EXPORT_USE) and principal.has(Perm.DASHBOARDS_VIEW)):
            refusal = NO_LONGER
        else:
            try:
                d = await visible_dashboard(conn, principal, str(job["object_id"]))
                view = await dashboard_view(conn, principal, d)
            except TelaError:
                refusal = NO_LONGER
        pinned = str(job["dashboard_version_id"]) if job["dashboard_version_id"] else None
        if refusal is None and pinned and view.get("version_id") != pinned:
            refusal = "A newer version of this dashboard went live. Export it again."
        if refusal is not None:
            await _fail(conn, request, job, "no_access", refusal, outcome="deny")
        else:
            tz_row = await fetch_one(conn, "SELECT settings->>'time_zone' AS tz FROM iam.tenants WHERE id = :t",
                                     {"t": body.tenant_id})
            await conn.execute(text("UPDATE ops.export_jobs SET status = 'running', started_at = now(), "
                                    "scope_summary = :s WHERE id = :id"),
                               {"s": view["locked_scope_chip"][:500], "id": job["id"]})
    if refusal is not None:
        raise TelaError(ErrorCode.FORBIDDEN, refusal)
    assert principal is not None and d is not None
    tz = _zone(principal.time_zone or (tz_row["tz"] if tz_row else None))
    payload = await build_payload(request, principal, job, d, view, tz)
    if payload["row_count"] > settings.export_max_rows:
        async with tenant_tx(body.tenant_id) as conn:
            await _fail(conn, request, job, "export_too_large",
                        f"{TOO_LARGE} It has {payload['row_count']:,} rows; the limit is "
                        f"{settings.export_max_rows:,}. Narrow the filters and export again.")
        raise TelaError(ErrorCode.PAYLOAD_TOO_LARGE, TOO_LARGE)
    return payload


@internal.put("/{job_id}/file")
async def put_file(job_id: str, request: Request, tenant_id: str = Query(max_length=64),
                   file_name_: str = Query(alias="file_name", max_length=200),
                   row_count: int = Query(default=0, ge=0), excluded_widgets: int = Query(default=0, ge=0),
                   _: dict[str, Any] = Depends(require_worker)) -> dict[str, Any]:
    data = await request.body()
    async with tenant_tx(tenant_id) as conn:
        job = await _job(conn, job_id)
        if job["status"] != "running":
            raise TelaError(ErrorCode.CONFLICT, "This export is not running.", {"status": job["status"]})
    expected = FORMATS[job["format"]][0]
    if request.headers.get("content-type", "").split(";")[0].strip() != expected or not data:
        raise TelaError(ErrorCode.VALIDATION_ERROR, "The file is not a valid export.")
    key = tenant_key(tenant_id, "exports", f"{job_id}.{job['format']}")
    await (await _store(request)).put(key, data, expected)
    hours = int(_settings(request).export_retention_hours)
    name = re.sub(r"[^A-Za-z0-9._-]", "-", file_name_)[:200]
    EXPORT_JOBS.labels(status="succeeded").inc()
    async with tenant_tx(tenant_id) as conn:
        await conn.execute(
            text("UPDATE ops.export_jobs SET status = 'done', object_key = :k, file_name = :n, size_bytes = :b, "
                 "row_count = :r, excluded_widgets = :x, finished_at = now(), error = NULL, error_code = NULL, "
                 "expires_at = now() + make_interval(hours => :h) WHERE id = :id"),
            {"k": key, "n": name, "b": len(data), "r": row_count, "x": excluded_widgets, "h": hours, "id": job["id"]})
        await audit.record(conn, tenant_id=tenant_id, action="export.created", object_type="dashboard",
                           object_id=str(job["object_id"]), outcome="success", actor_id=str(job["requested_by"]),
                           correlation_id=_rid(request),
                           details={"job_id": job_id, "format": job["format"], "filters": job["filters"],
                                    "scope": job["scope_summary"], "row_count": row_count,
                                    "excluded_widgets": excluded_widgets, "bytes": len(data)})
        label = FORMATS[job["format"]][1]
        await notify(conn, tenant_id, [str(job["requested_by"])], kind="export.ready",
                     title=f"Your {label} export is ready",
                     body=f"{name} is ready to download for {hours} hours.",
                     link=f"/dashboards/{job['object_id']}?export={job_id}")
        return {"status": "succeeded", "size_bytes": len(data)}


class FailIn(BaseModel):
    tenant_id: str = Field(max_length=64)
    error_code: str = Field(default="render_failed", max_length=60)


@internal.post("/{job_id}/fail")
async def fail(job_id: str, body: FailIn, request: Request,
               _: dict[str, Any] = Depends(require_worker)) -> dict[str, Any]:
    """The renderer gave up (FEAT-045 A3). The person sees a plain message and can retry; detail stays in logs."""
    async with tenant_tx(body.tenant_id) as conn:
        job = await _job(conn, job_id)
        if job["status"] in ("done", "failed", "expired"):
            return {"status": STATUS_OUT[job["status"]]}
        await _fail(conn, request, job, re.sub(r"[^a-z_]", "", body.error_code) or "render_failed", RENDER_FAILED)
    return {"status": "failed"}


@internal.post("/sweep")
async def sweep(request: Request, _: dict[str, Any] = Depends(require_worker)) -> dict[str, int]:
    """Retention (DATA-049, 24 h): expire finished jobs and delete their files, across tenants."""
    async with tenant_tx(None) as conn:
        rows = await fetch_all(conn, "SELECT id, tenant_id, object_key FROM ops.f_expire_export_jobs(200)")
    deleted = 0
    store: ObjectStore | None = getattr(request.app.state, "object_store", None)
    for r in rows:
        if r["object_key"] and store is not None:
            try:
                await store.delete(r["object_key"])
                deleted += 1
            except TelaError:
                log.warning("exports.sweep_delete_failed", job_id=str(r["id"]))
    return {"expired": len(rows), "deleted": deleted}
