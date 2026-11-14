"""Report blueprints and starter dashboards of a pack, authored in Admin > Packs (ENH-025, US-234; FEAT-075).

The **Reports & dashboards** tab of the Pack Studio (US-232). Everything acts on one tenant draft pack; a system pack
or a released version opens read-only (clone or open the next draft to edit, DEC-26).

* ``GET /packs/{pack_id}/reports`` — the blueprints, figure tones and starter dashboards, each blueprint's problems,
  and the pack's measures, dimensions and fields for the form. ``ETag`` covers both parts.
* ``PUT|DELETE /packs/{pack_id}/reports/blueprints/{id}`` and ``PUT|DELETE …/dashboards/{key}`` — edit the draft
  (``If-Match`` required, 412 when stale); a blueprint with problems is refused with each problem's path.
* ``POST /packs/{pack_id}/reports/preview`` — a blueprint as the report planner lays it out on a chosen connection
  (``content/planner.py``: the certified measures that connection's model has, the parts it lacks named), each widget
  validated and run under the Admin's scope.
* ``POST /packs/{pack_id}/reports/from-report`` and ``…/from-dashboard`` — "Save as blueprint": a report (or each
  report of a dashboard, as its tabs, with its shared filters) becomes blueprints by measure and dimension key;
  widgets on uncertified items, own rules or custom SQL are named and left out.
"""

from __future__ import annotations

import json
import re
from typing import Any

from fastapi import APIRouter, Depends, Header, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from tela_api.ask.orchestrator import Orchestrator
from tela_api.audit import service as audit
from tela_api.content.access import readable_dashboard_draft, readable_report
from tela_api.content.planner import Planner, layout_preview
from tela_api.content.render import freeze_dashboard
from tela_api.content.reports import TEXT_KINDS, report_state, text_fields
from tela_api.core.db import fetch_one, tenant_tx
from tela_api.identity.deps import require
from tela_api.identity.permissions import Perm
from tela_api.identity.principal import Principal
from tela_api.knowledge.guidance import Blueprint
from tela_api.model.store import load_snapshot
from tela_api.packs import blueprints as bps
from tela_api.packs.source import document_sha
from tela_api.query.service import QueryContext, QueryService
from tela_common.errors import ErrorCode, TelaError
from tela_common.ids import new_id
from tela_query_spec.model import ModelSnapshot

router = APIRouter(prefix="/api/v1/packs", tags=["packs"])
NO_PACK = "That pack does not exist."
PREVIEW_ROWS = 20


# --------------------------------------------------------------------------- the draft


async def _pack(conn: AsyncConnection, pack_id: str, *, write: bool = False) -> dict[str, Any]:
    try:
        sql = ("SELECT id, tenant_id, pack_key, pack_version, title, status, manifest, submitted_at FROM "
               "model.starter_packs "
               "WHERE id = CAST(:id AS uuid)")
        row = await fetch_one(conn, sql, {"id": pack_id})
    except Exception:  # noqa: BLE001 - not a uuid
        row = None
    if row is None:
        raise TelaError(ErrorCode.NOT_FOUND, NO_PACK)
    if write and row["tenant_id"] is None:
        raise TelaError(ErrorCode.CONFLICT, "A system pack is read-only: clone it to edit.")
    if write and row["status"] != "draft":
        raise TelaError(ErrorCode.CONFLICT, "A released pack version is frozen: open the next draft to edit.")
    if write and row["submitted_at"] is not None:  # US-238
        raise TelaError(ErrorCode.CONFLICT, "This version is waiting for review: withdraw it from review to edit it.",
                        {"reason": "in_review"})
    if write:  # FOR UPDATE sees only rows the update policy allows: the tenant's drafts, checked above
        row = await fetch_one(conn, sql + " FOR UPDATE", {"id": pack_id})
        if row is None or row["status"] != "draft":
            raise TelaError(ErrorCode.CONFLICT, "This pack version is no longer a draft: reload it.")
    doc = await fetch_one(conn, "SELECT body FROM model.pack_documents WHERE pack_id = :p AND kind = "
                                "'report_blueprints'", {"p": row["id"]})
    return {**row, "manifest": dict(row["manifest"]), "doc": dict(doc["body"]) if doc else bps.empty_document()}


def _etag(head: dict[str, Any], doc: dict[str, Any]) -> str:
    return '"' + document_sha({"dashboards": head.get("dashboards") or {}, "doc": doc})[:32] + '"'


def _out(pack: dict[str, Any]) -> dict[str, Any]:
    head, doc = pack["manifest"], pack["doc"]
    names = bps.PackNames(head)
    return {
        "pack": {"id": str(pack["id"]), "key": pack["pack_key"], "version": pack["pack_version"],
                 "title": pack["title"], "status": pack["status"], "system": pack["tenant_id"] is None,
                 "read_only": pack["tenant_id"] is None or pack["status"] != "draft"
                 or pack.get("submitted_at") is not None},
        "blueprints": doc.get("blueprints", []), "tones": doc.get("tones") or {},
        "dashboards": [{"key": k, "title": d.get("title", k), "reports": list(d.get("reports", [])),
                        "filters": list(d.get("filters", []))} for k, d in (head.get("dashboards") or {}).items()],
        "problems": bps.check_document(doc, head),
        "names": {
            "measures": [{"key": m.get("key"), "label": m.get("label"), "entity": m.get("entity")}
                         for m in head.get("measures", [])],
            "dimensions": [{"key": d.get("key"), "label": d.get("label"), "entity": d.get("entity")}
                           for d in head.get("dimensions", [])],
            "fields": {e: sorted(fs) for e, fs in names.fields.items()},
        },
        "etag": _etag(head, doc),
    }


async def _save(conn: AsyncConnection, principal: Principal, pack: dict[str, Any], head: dict[str, Any],
                doc: dict[str, Any], *, action: str, details: dict[str, Any], rid: str | None) -> dict[str, Any]:
    await conn.execute(text("UPDATE model.starter_packs SET manifest = CAST(:m AS jsonb), manifest_sha256 = :s "
                            "WHERE id = :id"), {"m": json.dumps(head), "s": document_sha(head), "id": pack["id"]})
    await conn.execute(
        text("INSERT INTO model.pack_documents (id, pack_id, tenant_id, kind, body, sha256) VALUES (:id, :p, :t, "
             "'report_blueprints', CAST(:b AS json), :s) ON CONFLICT (pack_id, kind) DO UPDATE SET body = "
             "EXCLUDED.body, sha256 = EXCLUDED.sha256"),
        {"id": new_id(), "p": pack["id"], "t": pack["tenant_id"], "b": json.dumps(doc), "s": document_sha(doc)})
    await audit.record(conn, tenant_id=principal.tenant_id, action=action, object_type="starter_pack",
                       object_id=str(pack["id"]), outcome="success", actor_id=principal.user_id, correlation_id=rid,
                       details={"pack": pack["pack_key"], "version": pack["pack_version"], **details})
    return _out({**pack, "manifest": head, "doc": doc})


def _check_etag(pack: dict[str, Any], if_match: str | None) -> None:
    if not if_match:
        raise TelaError(ErrorCode.PRECONDITION_FAILED, "Reload the pack and try again (If-Match required).")
    if if_match != _etag(pack["manifest"], pack["doc"]):
        raise TelaError(ErrorCode.PRECONDITION_FAILED, "Someone changed this pack's reports: reload and try again.")


def _refuse(problems: list[dict[str, str]], what: str) -> None:
    if problems:
        raise TelaError(ErrorCode.VALIDATION_ERROR, f"{what} has problems: {problems[0]['message']}",
                        {"problems": problems})


def _rid(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


def _send(response: Response, out: dict[str, Any]) -> dict[str, Any]:
    response.headers["ETag"] = out["etag"]
    return out


# --------------------------------------------------------------------------- endpoints


@router.get("/{pack_id}/reports")
async def get_reports(pack_id: str, response: Response,
                      principal: Principal = Depends(require(Perm.PACKS_AUTHOR))) -> dict[str, Any]:
    async with tenant_tx(principal.tenant_id) as conn:
        return _send(response, _out(await _pack(conn, pack_id)))


class PackBlueprintIn(BaseModel):
    blueprint: dict[str, Any]
    tones: dict[str, dict[str, Any] | None] = Field(default_factory=dict)  # figure thresholds by measure; None drops


@router.put("/{pack_id}/reports/blueprints/{blueprint_id}")
async def put_blueprint(pack_id: str, blueprint_id: str, body: PackBlueprintIn, request: Request, response: Response,
                        if_match: str | None = Header(default=None),
                        principal: Principal = Depends(require(Perm.PACKS_AUTHOR))) -> dict[str, Any]:
    async with tenant_tx(principal.tenant_id) as conn:
        pack = await _pack(conn, pack_id, write=True)
        _check_etag(pack, if_match)
        head, doc = pack["manifest"], pack["doc"]
        exists = blueprint_id in [b.get("id") for b in doc.get("blueprints", [])]
        tones = {**(doc.get("tones") or {}), **{m: t for m, t in body.tones.items() if t is not None}}
        tones = {m: t for m, t in tones.items() if body.tones.get(m, t) is not None}
        doc = {**doc, "tones": tones}
        _refuse(bps.check_blueprint(body.blueprint, head, doc), "The blueprint")
        try:
            head, doc = bps.put_blueprint(head, doc, body.blueprint, replaces=blueprint_id if exists else None)
        except ValueError as exc:
            raise TelaError(ErrorCode.CONFLICT, str(exc)) from exc
        out = await _save(conn, principal, pack, head, doc, action="pack.blueprint_saved", rid=_rid(request),
                          details={"blueprint": body.blueprint.get("id"), "new": not exists})
    return _send(response, out)


@router.delete("/{pack_id}/reports/blueprints/{blueprint_id}")
async def delete_blueprint(pack_id: str, blueprint_id: str, request: Request, response: Response,
                           if_match: str | None = Header(default=None),
                           principal: Principal = Depends(require(Perm.PACKS_AUTHOR))) -> dict[str, Any]:
    async with tenant_tx(principal.tenant_id) as conn:
        pack = await _pack(conn, pack_id, write=True)
        _check_etag(pack, if_match)
        try:
            head, doc = bps.drop_blueprint(pack["manifest"], pack["doc"], blueprint_id)
        except KeyError as exc:
            raise TelaError(ErrorCode.NOT_FOUND, "That blueprint is not in this pack.") from exc
        out = await _save(conn, principal, pack, head, doc, action="pack.blueprint_deleted", rid=_rid(request),
                          details={"blueprint": blueprint_id})
    return _send(response, out)


class PackDashboardIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    reports: list[str] = Field(default_factory=list, max_length=40)
    filters: list[str] = Field(default_factory=list, max_length=10)


DASHBOARD_KEY = r"^[a-z][a-z0-9_]{0,40}$"


@router.put("/{pack_id}/reports/dashboards/{key}")
async def put_dashboard(pack_id: str, key: str, body: PackDashboardIn, request: Request, response: Response,
                        if_match: str | None = Header(default=None),
                        principal: Principal = Depends(require(Perm.PACKS_AUTHOR))) -> dict[str, Any]:
    if not re.match(DASHBOARD_KEY, key):
        raise TelaError(ErrorCode.VALIDATION_ERROR, "A dashboard key is lower case letters, digits and _.")
    async with tenant_tx(principal.tenant_id) as conn:
        pack = await _pack(conn, pack_id, write=True)
        _check_etag(pack, if_match)
        head, doc, problems = bps.put_dashboard(pack["manifest"], pack["doc"], key, body.model_dump())
        _refuse(problems, "The dashboard")
        out = await _save(conn, principal, pack, head, doc, action="pack.dashboard_saved", rid=_rid(request),
                          details={"dashboard": key, "tabs": len(body.reports)})
    return _send(response, out)


@router.delete("/{pack_id}/reports/dashboards/{key}")
async def delete_dashboard(pack_id: str, key: str, request: Request, response: Response,
                           if_match: str | None = Header(default=None),
                           principal: Principal = Depends(require(Perm.PACKS_AUTHOR))) -> dict[str, Any]:
    async with tenant_tx(principal.tenant_id) as conn:
        pack = await _pack(conn, pack_id, write=True)
        _check_etag(pack, if_match)
        try:
            head = bps.drop_dashboard(pack["manifest"], pack["doc"], key)
        except KeyError as exc:
            raise TelaError(ErrorCode.NOT_FOUND, "That dashboard is not in this pack.") from exc
        except ValueError as exc:
            raise TelaError(ErrorCode.CONFLICT, str(exc)) from exc
        out = await _save(conn, principal, pack, head, pack["doc"], action="pack.dashboard_deleted",
                          rid=_rid(request), details={"dashboard": key})
    return _send(response, out)


# --------------------------------------------------------------------------- live preview


class PackPreviewIn(BaseModel):
    blueprint: dict[str, Any]
    connection_id: str
    run: bool = True


@router.post("/{pack_id}/reports/preview")
async def preview(pack_id: str, body: PackPreviewIn, request: Request,
                  principal: Principal = Depends(require(Perm.PACKS_AUTHOR))) -> dict[str, Any]:
    """The blueprint laid out by the report planner on the chosen connection's approved model, and run."""
    async with tenant_tx(principal.tenant_id) as conn:
        pack = await _pack(conn, pack_id)
    problems = bps.check_blueprint(body.blueprint, pack["manifest"], pack["doc"])
    try:
        bp = Blueprint.model_validate(body.blueprint)
    except Exception:  # noqa: BLE001 - reported as problems
        return {"problems": problems, "sections": [], "gaps": [], "layout_preview": [], "results": {}}
    if bp.unavailable:
        return {"problems": problems, "sections": [], "layout_preview": [], "results": {},
                "gaps": [{"request_part": bp.title, "reason": f"No {', '.join(bp.unavailable)} in this model"}]}
    service: QueryService = request.app.state.query_service
    sources = await Orchestrator(service, None).sources(principal, body.connection_id)
    if not sources:
        raise TelaError(ErrorCode.CONFLICT, "That connection has no approved model you can use.")
    src = sources[0]
    figures, section, gaps = Planner(src, None, []).blueprint_section(bp)
    headline: dict[str, Any] = {"key": "headline", "title": "Headline figures", "kind": "headline",
                                "widgets": figures}
    sections: list[dict[str, Any]] = [s for s in (headline, section) if s["widgets"] or s.get("callouts")]
    results: dict[str, Any] = {}
    for s in sections:
        kept = []
        for w in s["widgets"]:
            try:
                if body.run:
                    r = await service.execute(w["spec"], principal, QueryContext(surface="preview",
                                                                                 request_id=_rid(request)))
                    results[w["ref"]] = {k: r.get(k) for k in ("columns", "row_count", "hidden", "message")} | \
                        {"rows": (r.get("rows") or [])[:PREVIEW_ROWS]}
                else:
                    await service.prepare(w["spec"], principal, QueryContext(surface="preview"))
                kept.append(w)
            except TelaError as exc:
                gaps.append({"request_part": w["title"], "reason": exc.message})
        s["widgets"] = kept
    return {"problems": problems, "connection": {"id": str(src.connection["id"]), "name": src.connection["name"]},
            "model_version": src.snap.version_no, "sections": sections, "gaps": gaps,
            "layout_preview": layout_preview(sections), "results": results}


# --------------------------------------------------------------------------- save as blueprint


async def frozen_report(conn: AsyncConnection, report_id: Any, title: str) -> dict[str, Any]:
    """One report in the frozen form ``freeze_dashboard`` gives each of a dashboard's reports."""
    state = await report_state(conn, report_id)
    sections = []
    for s in state["sections"]:
        items = []
        for i in state["items"]:
            if i["section_id"] != s["id"]:
                continue
            if i["kind"] in TEXT_KINDS:
                items.append({"kind": i["kind"], **text_fields(i)})
                continue
            w = state["widgets"].get(i["widget_id"] or "", {})
            items.append({"kind": "widget", "title": w.get("title"), "visual": w.get("visual"),
                          "spec": w.get("spec") or {}, "badge": w.get("badge")})
        sections.append({"title": s["title"], "items": items})
    return {"id": str(report_id), "title": title, "sections": sections}


async def snapshots(conn: AsyncConnection, tenant_id: str, reports: list[dict[str, Any]]) -> dict[str, ModelSnapshot]:
    out: dict[str, ModelSnapshot] = {}
    for v in {str((i.get("spec") or {}).get("model_version_id")) for r in reports for s in r["sections"]
              for i in s["items"] if (i.get("spec") or {}).get("model_version_id")}:
        try:
            out[v] = await load_snapshot(conn, tenant_id, v)
        except TelaError:
            continue  # a widget on a model version that no longer loads is left out as not certified
    return out


def _dashboard_for(head: dict[str, Any], asked: str | None) -> str:
    dashboards = head.get("dashboards") or {}
    if asked:
        if asked not in dashboards:
            raise TelaError(ErrorCode.VALIDATION_ERROR, f"The pack has no dashboard {asked}: add it first.")
        return asked
    if len(dashboards) == 1:
        return str(next(iter(dashboards)))
    raise TelaError(ErrorCode.VALIDATION_ERROR, "Choose the dashboard the blueprint is a tab of.")


class PackFromReportIn(BaseModel):
    report_id: str
    dashboard: str | None = None
    id: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]{1,60}$")
    save: bool = True


@router.post("/{pack_id}/reports/from-report")
async def from_report(pack_id: str, body: PackFromReportIn, request: Request, response: Response,
                      if_match: str | None = Header(default=None),
                      principal: Principal = Depends(require(Perm.PACKS_AUTHOR))) -> dict[str, Any]:
    """Save a report as a blueprint (or, with ``save: false``, show what it would be)."""
    async with tenant_tx(principal.tenant_id) as conn:
        pack = await _pack(conn, pack_id, write=body.save)
        if body.save:
            _check_etag(pack, if_match)
        report = await readable_report(conn, principal, body.report_id)
        frozen = await frozen_report(conn, report["id"], report["title"])
        snaps = await snapshots(conn, principal.tenant_id, [frozen])
        head, doc = pack["manifest"], pack["doc"]
        taken = {str(b.get("id")) for b in doc.get("blueprints", [])}
        bid = body.id or bps.slug(report["title"], taken)
        if bid in taken:
            raise TelaError(ErrorCode.CONFLICT, f"The pack already has a blueprint {bid}.")
        dashboard = _dashboard_for(head, body.dashboard)
        raw, tones, left = bps.blueprint_from_report(frozen, snaps, head, bid=bid, dashboard=dashboard,
                                                     taken_tones=doc.get("tones"))
        doc2 = {**doc, "tones": {**(doc.get("tones") or {}), **tones}}
        problems = bps.check_blueprint(raw, head, doc2)
        result: dict[str, Any] = {"blueprint": raw, "tones": tones, "left_out": left, "problems": problems,
                                  "saved": False}
        if not body.save:
            return result
        _refuse(problems, "The blueprint")
        head, doc2 = bps.put_blueprint(head, doc2, raw)
        out = await _save(conn, principal, pack, head, doc2, action="pack.blueprint_saved_from_report",
                          rid=_rid(request), details={"blueprint": bid, "report": body.report_id,
                                                      "left_out": len(left)})
    response.headers["ETag"] = out["etag"]
    return {**result, "saved": True, "reports": out}


class PackFromDashboardIn(BaseModel):
    dashboard_id: str
    key: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]{0,40}$")
    title: str | None = Field(default=None, min_length=1, max_length=200)
    save: bool = True


@router.post("/{pack_id}/reports/from-dashboard")
async def from_dashboard(pack_id: str, body: PackFromDashboardIn, request: Request, response: Response,
                         if_match: str | None = Header(default=None),
                         principal: Principal = Depends(require(Perm.PACKS_AUTHOR))) -> dict[str, Any]:
    """Save a dashboard as a starter dashboard: each report a blueprint, in order, and its shared filters."""
    async with tenant_tx(principal.tenant_id) as conn:
        pack = await _pack(conn, pack_id, write=body.save)
        if body.save:
            _check_etag(pack, if_match)
        d = await readable_dashboard_draft(conn, principal, body.dashboard_id)
        frozen = await freeze_dashboard(conn, d)
        snaps = await snapshots(conn, principal.tenant_id, frozen["reports"])
        head, doc = pack["manifest"], pack["doc"]
        key = body.key or bps.slug(d["title"], set(head.get("dashboards") or {}))[:41]
        if key in (head.get("dashboards") or {}):
            raise TelaError(ErrorCode.CONFLICT, f"The pack already has a dashboard {key}.")
        made = bps.blueprints_from_dashboard(frozen, snaps, head, doc, key=key, title=body.title or d["title"],
                                             shared_filters=d["shared_filters"])
        result: dict[str, Any] = {"dashboard": made.dashboard, "blueprints": made.blueprints, "tones": made.tones,
                                  "left_out": made.left_out, "problems": made.problems, "saved": False}
        if not body.save:
            return result
        if made.problems:
            first = next(iter(made.problems.values()))
            raise TelaError(ErrorCode.VALIDATION_ERROR, f"A blueprint has problems: {first[0]['message']}",
                            {"problems": made.problems})
        head, doc, p = bps.add_dashboard(doc, made)
        _refuse(p, "The dashboard")
        out = await _save(conn, principal, pack, head, doc, action="pack.dashboard_saved_from_dashboard",
                          rid=_rid(request), details={"dashboard": key, "source": body.dashboard_id,
                                                      "tabs": len(made.blueprints), "left_out": len(made.left_out)})
    response.headers["ETag"] = out["etag"]
    return {**result, "saved": True, "reports": out}
