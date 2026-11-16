"""Report planner (AG-05), plan editing in words and Build (API-025, API-026; FEAT-023; US-066 to US-068).

Planning is **certified-first and rules-first**, like Ask: one sentence is matched to the installed pack's
report blueprints (the report catalogue's screens, US-159: "delayed projects", "CAPEX budget vs forecast" …),
else to its starter report themes ("delivery", "finance", "governance" …), and to certified measures named in it.
The plan proposes headline figures, sections of widgets with a suitable visual each (a blueprint brings its
figures, visuals, detail table, callout and footnote), page filters and a "Not included" list of the parts the
model has no data for. Every planned spec is validated under the Creator's scope before it is offered; a request
with nothing supportable returns gaps only. The title passes the wording check (no "spend" claims).

Build creates the draft report, pins each planned widget to the Creator's library (reusing identical
definitions) and runs every widget once, flagging empty ("No data in your scope") and failed widgets.
"""

from __future__ import annotations

import copy
import json
import re
from collections.abc import AsyncIterator
from typing import Any, Literal

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import text

from tela_api.ask.orchestrator import Orchestrator, Source
from tela_api.ask.planner import GAP_TOPICS, STOP, match_dimension, match_measures, norm, words
from tela_api.audit import service as audit
from tela_api.content import plan_review, wording
from tela_api.content.common import def_hash, default_size, remember, replay
from tela_api.content.layout import Box, place
from tela_api.content.reports import TEXT_SIZES, add_section, create_report, place_widget
from tela_api.content.starters import add_text_item
from tela_api.core.db import fetch_one, tenant_tx
from tela_api.identity.deps import require
from tela_api.identity.permissions import Perm
from tela_api.identity.principal import Principal
from tela_api.knowledge.guidance import Blueprint, available, blueprint_spec, load_blueprints, match_blueprints
from tela_api.knowledge.patterns import pack_source_for
from tela_api.llm import trace
from tela_api.llm.review import review as llm_review
from tela_api.llm.router import tenant_llm_settings
from tela_api.packs.manifest import PackManifest, widget_spec
from tela_api.query.service import QueryContext, QueryService
from tela_chart_spec import ResultShape, choose_visual, suitability
from tela_common.errors import ErrorCode, TelaError
from tela_common.ids import new_id
from tela_query_spec.model import Measure, ModelSnapshot
from tela_query_spec.spec import ref_key

router = APIRouter(prefix="/api/v1", tags=["reports"])
BUILD_ROUTE = "POST /reports/plans/build"
MAX_HEADLINE = 4
MAX_BLUEPRINT_HEADLINE = 5  # the catalogue's KPI strip has 4 or 5 figures
MAX_WIDGETS = 24

def _sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


def spec_shape(spec: dict[str, Any], snap: ModelSnapshot) -> ResultShape:
    """A result shape from the spec alone (no data yet): used to pick plan visuals."""
    cats, dates, statuses, ordered = [], [], [], []
    for b in spec.get("breakdowns", []):
        d = snap.dimension(ref_key(b["ref"])) if b["ref"].startswith("dimension:") else None
        name = ref_key(b["ref"])
        if d is not None and d.is_temporal:
            dates.append(name)
            continue
        cats.append(name)
        if d is not None and d.is_status:
            statuses.append(name)
        if d is not None and (d.sort_order or d.bands):  # banded dimensions are ordered too (US-157)
            ordered.append(name)
    if (spec.get("time") or {}).get("grain"):
        dates.append("period")
    measures = [spec["measure"]["ref"], *(m["ref"] for m in spec.get("extra_measures", []))]
    fmts = []
    additive = True
    for r in measures:
        m = snap.measure(ref_key(r))
        fmts.append(m.format if m else "integer")
        if m is not None and m.format == "percent":
            additive = False
    rows = 1 if not cats and not dates else 6
    return ResultShape(row_count=rows, categories=cats, dates=dates, statuses=statuses, ordered=ordered,
                       measures=measures, measure_formats=fmts, additive=additive,
                       distinct_counts=dict.fromkeys(cats, 6))


def plan_visual(spec: dict[str, Any], snap: ModelSnapshot, requested: str | None = None) -> str:
    shape = spec_shape(spec, snap)
    if requested and next((s["suitable"] for s in suitability(shape) if s["visual"] == requested), False):
        return requested
    return str(choose_visual(shape)[0])


class PlanIn(BaseModel):
    request: str = Field(min_length=1, max_length=2000)
    connection_id: str | None = None


class PlanEditIn(BaseModel):
    instruction: str = Field(min_length=1, max_length=1000)


class PlanShowIn(BaseModel):
    show: Literal["ai", "rules"]


# --------------------------------------------------------------------------- the planner


class Planner:
    def __init__(self, src: Source, manifest: PackManifest | None, blueprints: list[Blueprint] | None = None) -> None:
        self.src, self.snap, self.manifest = src, src.snap, manifest
        self.blueprints = blueprints or []
        self.visible = {m.key: m for m in src.measures}
        self.n = 0

    def _ref(self) -> str:
        self.n += 1
        return f"w{self.n}"

    def base_spec(self) -> dict[str, Any]:
        return {"model_version_id": self.snap.model_version_id, "connection_id": str(self.src.connection["id"])}

    def widget(self, spec: dict[str, Any], title: str, visual: str | None = None) -> dict[str, Any]:
        spec = {**self.base_spec(), **{k: v for k, v in spec.items() if k not in ("model_version_id", "connection_id")}}
        asked = spec.get("visual") or {}
        v = plan_visual(spec, self.snap, visual or asked.get("type"))
        # A blueprint's detail table and meter note stay with their visual (US-158, US-159).
        spec["visual"] = {**asked, "type": v} if asked.get("type") == v else {"type": v}
        m = self.snap.measure(ref_key(spec["measure"]["ref"]))
        return {"ref": self._ref(), "title": title, "visual": v, "certified": True, "spec": spec,
                "measure_label": m.label if m else spec["measure"]["ref"]}

    def figure(self, m: Measure) -> dict[str, Any]:
        return self.widget({"measure": {"ref": f"measure:{m.key}"}}, m.label, "figure")

    def _usable(self, measures: list[str]) -> bool:
        return all(k in self.visible and self.visible[k].entity in self.src.scope.entities for k in measures)

    def blueprint_section(self, bp: Blueprint) -> tuple[list[dict[str, Any]], dict[str, Any], list[dict[str, Any]]]:
        """(figures, section, gaps) of one report blueprint, from the certified measures this Creator can use."""
        cut, left = available(bp, self.snap)
        ids = {"model_version_id": self.snap.model_version_id, "connection_id": str(self.src.connection["id"])}
        figures = []
        for k in cut.figures():
            if not self._usable([k.measure]):
                left.append(k.title or self.snap.measure(k.measure).label if self.snap.measure(k.measure) else
                            k.measure)
                continue
            label = self.visible[k.measure].label
            figures.append(self.widget(blueprint_spec(cut, k, **ids), k.title or label, "figure"))
        widgets = []
        for w in cut.widgets:
            if not self._usable([w.measure, *w.extra_measures]):
                left.append(w.title)
                continue
            widgets.append(self.widget(blueprint_spec(cut, w, **ids), w.title, w.visual))
        if cut.table is not None:
            if self._usable([cut.table.measure]):
                spec = blueprint_spec(cut, cut.table.widget(), **ids, detail=cut.table.detail())
                widgets.append(self.widget(spec, cut.table.title, "table"))
            else:
                left.append(cut.table.title)
        section = {"key": f"bp_{bp.id}", "title": bp.title, "kind": "normal", "widgets": widgets,
                   "blueprint": {"id": bp.id, "title": bp.title, "question": bp.question,
                                 "population": bp.population},
                   "callouts": [bp.callout.model_dump()] if bp.callout else [], "footnote": bp.footnote}
        gaps = [{"request_part": part, "reason": f"Not in this model (part of the {bp.title} layout)"}
                for part in dict.fromkeys(left)]
        return figures, section, gaps

    def theme_sections(self, request: str) -> list[dict[str, Any]]:
        """The pack's starter reports the request names. Nothing here knows a domain: a report is picked when the
        request shares a word with the report's own name, section, tab or widget titles, and when the request names
        a dashboard of the pack (its key's first part, e.g. "requests"), only that dashboard's reports are kept."""
        if self.manifest is None:
            return []
        skip = set(STOP) | {"report", "reports", "dashboard", "dashboards", "page", "show", "me", "my"}
        qw = {w for w in words(request.replace("-", "")) if w not in skip}
        qw |= {w.rstrip("s") for w in qw}
        entity_of = {m.key: m.entity for m in self.snap.measures}

        def own_words(*texts: Any) -> set[str]:
            out = {w for t in texts if t for w in words(str(t).replace("_", " ").replace("-", "")) if w not in skip}
            return out | {w.rstrip("s") for w in out}

        prefixes = {key: str(report.get("dashboard") or key.split("_", 1)[0])
                    for key, report in self.manifest.reports.items()}
        named_prefixes = {p for p in prefixes.values() if own_words(p) & qw}
        out = []
        for key, report in self.manifest.reports.items():
            if named_prefixes and prefixes[key] not in named_prefixes:
                continue
            theme = report.get("section") or (key.split("_", 1)[1] if "_" in key else key)
            prefix_words = own_words(prefixes[key])
            topic = own_words(theme, report.get("tab"), report.get("title")) - prefix_words
            # The request names this report's topic, or names only the dashboard ("the requests dashboard").
            if not topic & qw and not (named_prefixes and not qw - prefix_words):
                continue
            widgets = []
            for i, w in enumerate(report["widgets"]):
                if w["measure"] not in self.visible:
                    continue
                spec = widget_spec(self.manifest, key, i, model_version_id=self.snap.model_version_id,
                                   connection_id=str(self.src.connection["id"]))
                refs = [b["ref"] for b in spec["breakdowns"]] + [f["ref"] for f in spec["filters"]]
                if any(self.snap.dimension(ref_key(r)) is None for r in refs):
                    continue
                if entity_of.get(w["measure"]) not in self.src.scope.entities:
                    continue
                widgets.append(self.widget(spec, w["title"], w["visual"]))
            if widgets:
                out.append({"key": key, "title": report.get("tab") or report["title"].split("—", 1)[-1].strip(),
                            "kind": "normal",
                            "widgets": widgets})
        return out

    def plan(self, request: str, *, themes: bool = True) -> dict[str, Any]:
        q = norm(request)
        gaps = [{"request_part": term, "reason": f"No {topic} data in this model"}
                for term, topic in GAP_TOPICS.items() if re.search(rf"\b{re.escape(term)}\b", q)]
        gaps = list({g["reason"]: g for g in gaps}.values())
        picked = match_blueprints(self.blueprints, request) if themes else []
        sections: list[dict[str, Any]] = []
        blueprint_figures: list[dict[str, Any]] = []
        for bp in picked:
            if bp.unavailable:
                said = {w for t in bp.triggers for w in words(t)}
                gaps = [g for g in gaps if not set(words(g["request_part"])) & said]
                gaps.append({"request_part": bp.title, "reason": f"No {', '.join(bp.unavailable)} in this model"})
                continue
            figures, section, bp_gaps = self.blueprint_section(bp)
            gaps += bp_gaps
            if not blueprint_figures:
                blueprint_figures = figures  # the first blueprint's figures open the report
            else:
                section["widgets"] = figures + section["widgets"]
            sections.append(section)
        if not picked and themes:
            sections = self.theme_sections(request)
        named: list[Measure] = []
        for part in re.split(r",|;|\band\b|\bwith\b|\bincluding\b|\bplus\b|\bshowing\b|\bfor\b", q):
            part = part.strip()
            if not part or not [w for w in words(part) if w not in STOP and w not in ("report", "dashboard")]:
                continue
            hits = match_measures(part, list(self.visible.values()))
            if hits and hits[0][0] >= 0.6 and hits[0][1] not in named:
                named.append(hits[0][1])
        headline: list[dict[str, Any]] = list(blueprint_figures)
        for m in named[:MAX_HEADLINE]:
            if len(headline) < (MAX_BLUEPRINT_HEADLINE if blueprint_figures else MAX_HEADLINE) and all(
                    h["spec"]["measure"]["ref"] != f"measure:{m.key}" or h["spec"].get("filters") for h in headline):
                headline.append(self.figure(m))
        dims = [d for d in self.src.dims if not d.is_temporal]
        breakdowns = []
        for m in re.finditer(r"\b(?:by|per|across|for (?:each|my))\s+([a-z ]+?)(?=$|,|\b(?:and|with|for|in)\b)", q):
            d = match_dimension(m.group(1).strip(), dims) or match_dimension(m.group(1).strip().rstrip("s"), dims)
            if d is not None and d not in breakdowns:
                breakdowns.append(d)
        extra_widgets = []
        for m in named[:2]:
            for d in breakdowns[:2]:
                if self.snap.join_path(m.entity, d.entity) is not None:
                    extra_widgets.append(self.widget({"measure": {"ref": f"measure:{m.key}"},
                                                      "breakdowns": [{"ref": f"dimension:{d.key}"}]},
                                                     f"{m.label} by {d.label.lower()}"))
        if extra_widgets:
            sections.insert(0, {"key": "named", "title": "Requested breakdowns", "kind": "normal",
                                "widgets": extra_widgets})
        if not headline and sections:
            for s in sections:
                for w in s["widgets"]:
                    if w["visual"] == "figure" and len(headline) < MAX_HEADLINE and \
                            all(h["spec"]["measure"] != w["spec"]["measure"] for h in headline):
                        headline.append(copy.deepcopy(w) | {"ref": self._ref()})
        headline_measures = {h["spec"]["measure"]["ref"] for h in headline}
        for s in sections:  # a figure already in the headline row is not repeated in a section
            s["widgets"] = [w for w in s["widgets"] if not (w["visual"] == "figure" and not w["spec"].get("filters")
                                                            and w["spec"]["measure"]["ref"] in headline_measures)]
        sections = [s for s in sections if s["widgets"]]
        if headline:
            sections.insert(0, {"key": "headline", "title": "Headline figures", "kind": "headline",
                                "widgets": headline})
        if not sections:
            if not gaps:
                gaps = [{"request_part": request[:200], "reason": "No certified measure in this model matches it"}]
            return self.finish(request, [], [], gaps)
        total = 0
        for s in sections:
            s["widgets"] = s["widgets"][:max(0, MAX_WIDGETS - total)]
            total += len(s["widgets"])
        sections = [s for s in sections if s["widgets"]]  # sections past the widget limit are left out
        page_filters = self.page_filters(request, sections, breakdowns)
        return self.finish(request, sections, page_filters, gaps)

    def page_filters(self, request: str, sections: list[dict[str, Any]], mentioned: list[Any]) -> list[dict[str, Any]]:
        entities = {self.snap.measure(ref_key(w["spec"]["measure"]["ref"])).entity
                    for s in sections for w in s["widgets"]}
        keys: list[str] = [d.key for d in mentioned]
        if re.search(r"\bmy (region|regions)\b", norm(request)):
            keys.append("region")
        if self.manifest is not None:
            for dash in self.manifest.dashboards.values():
                keys += dash.get("filters", [])
        out = []
        for k in dict.fromkeys(keys):
            d = self.snap.dimension(k)
            if d is None or d.is_temporal or f"{d.entity}.{d.field}" in self.src.scope.hidden_fields:
                continue
            if any(self.snap.join_path(e, d.entity) is not None for e in entities):
                out.append({"ref": f"dimension:{d.key}", "label": d.label})
        return out[:5]

    def finish(self, request: str, sections: list[dict[str, Any]], page_filters: list[dict[str, Any]],
               gaps: list[dict[str, Any]]) -> dict[str, Any]:
        title = re.sub(r"^(please\s+)?(build|create|make|design|generate)\s+(me\s+)?(an?\s+|the\s+)?", "", request,
                       flags=re.I).strip().rstrip(".?!")
        title = (title[:1].upper() + title[1:])[:120] if title else "New report"
        allowed = wording.allowed_from(self.snap)
        notes = []
        if any(i.kind == "finance" for i in wording.issues(request, allowed=allowed)):
            notes.append("There is no actual spend in this data: the plan shows forecast cost (EAC) and approved "
                         "budgets, and its title says so.")
        title = wording.fix(title, allowed=allowed)
        return {"title": title, "connection_id": str(self.src.connection["id"]),
                "connection": self.src.connection["name"], "model_version_id": self.snap.model_version_id,
                "model_version": self.snap.version_no,
                "headline_figures": [{"ref": w["ref"], "measure": w["spec"]["measure"]["ref"], "label": w["title"]}
                                     for s in sections if s["kind"] == "headline" for w in s["widgets"]],
                "sections": sections, "page_filters": page_filters, "gaps": gaps, "notes": notes,
                "layout_preview": layout_preview(sections), "diff": None}


def layout_preview(sections: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for s in sections:
        cw, ch = TEXT_SIZES["callout"]
        boxes: list[Box] = [Box(f"callout{i}", 0, i * ch, cw, ch) for i in range(len(s.get("callouts", [])))]
        for w in s["widgets"]:
            ww, hh = default_size(w["visual"])
            x, y = place(boxes, ww, hh)
            boxes.append(Box(w["ref"], x, y, ww, hh))
            out.append({"widget_ref": w["ref"], "section": s["key"], "x": x, "y": y, "w": ww, "h": hh})
    return out


async def _manifest(conn: Any, request: Request, connection_id: str) -> tuple[PackManifest | None, list[Blueprint]]:
    """The installed pack's manifest and report blueprints (US-159), both from the stored pack version (US-231)."""
    row = await fetch_one(conn, "SELECT m.id, p.manifest FROM model.data_models m JOIN model.starter_packs p ON "
                                "p.id = m.installed_pack_id WHERE m.connection_id = :c", {"c": connection_id})
    if row is None:
        return None, []
    pack = await pack_source_for(conn, str(row["id"]), request.app.state.settings.packs_dir)
    return PackManifest.model_validate(row["manifest"]), load_blueprints(pack) if pack else []


async def _validate(request: Request, principal: Principal, plan: dict[str, Any]) -> dict[str, Any]:
    """Every planned spec is validated under the Creator; failures move to "Not included"."""
    service: QueryService = request.app.state.query_service
    for s in plan["sections"]:
        kept = []
        for w in s["widgets"]:
            try:
                await service.prepare(w["spec"], principal, QueryContext(surface="preview"))
                kept.append(w)
            except TelaError as exc:
                plan["gaps"].append({"request_part": w["title"], "reason": exc.message})
        s["widgets"] = kept
    plan["sections"] = [s for s in plan["sections"] if s["widgets"]]
    plan["headline_figures"] = [h for h in plan["headline_figures"]
                                if any(w["ref"] == h["ref"] for s in plan["sections"] for w in s["widgets"])]
    plan["layout_preview"] = layout_preview(plan["sections"])
    return plan


async def _review(request: Request, principal: Principal, planner: Planner, plan: dict[str, Any],
                  asked: str) -> dict[str, Any]:
    """US-243 (DEC-28): with LLM review on, one ``report_plan`` call reviews the rules plan; what the LLM changes is
    checked under the Creator's scope and kept beside the rules plan. Review off returns ``plan`` as it is."""
    async with tenant_tx(principal.tenant_id) as conn:
        settings = await tenant_llm_settings(conn)
    got = await llm_review(getattr(request.app.state, "gateway", None), plan_review.ReportPlanOut, settings=settings,
                           tenant_id=principal.tenant_id, user_id=principal.user_id, agent="AG-05",
                           task="report_plan", system=plan_review.AG05_SYSTEM, request=asked,
                           draft=plan_review.draft(plan), context=plan_review.context(planner))
    service: QueryService = request.app.state.query_service

    async def validate(w: dict[str, Any]) -> str | None:
        try:
            await service.prepare(w["spec"], principal, QueryContext(surface="preview"))
        except TelaError as exc:
            return str(exc.message)
        return None

    return await plan_review.apply(planner, plan, got, validate, MAX_BLUEPRINT_HEADLINE, MAX_WIDGETS)


async def _source(request: Request, principal: Principal, text_: str, connection_id: str | None) -> Source:
    orch = Orchestrator(request.app.state.query_service, None)
    sources = await orch.sources(principal, connection_id)
    if not sources:
        raise TelaError(ErrorCode.CONFLICT, "No data source with an approved model is available to you yet.")
    if len(sources) > 1:
        matched = [s for s in sources if match_measures(text_, s.measures)]
        sources = matched or sources
    return sources[0]


# --------------------------------------------------------------------------- endpoints


@router.post("/reports/plan")
async def plan_report(body: PlanIn, request: Request,
                      principal: Principal = Depends(require(Perm.REPORTS_CREATE))) -> Any:
    """Plan a report from one sentence (SSE: progress → plan → complete; JSON without text/event-stream)."""
    async with trace.agent_run(principal.tenant_id, principal.user_id, "report_plan", title=body.request,
                               agent="AG-05"):
        return await _plan_report(body, request, principal)


async def _plan_report(body: PlanIn, request: Request, principal: Principal) -> Any:
    src = await _source(request, principal, body.request, body.connection_id)
    async with tenant_tx(principal.tenant_id) as conn:
        manifest, blueprints = await _manifest(conn, request, str(src.connection["id"]))
    planner = Planner(src, manifest, blueprints)
    plan = await _validate(request, principal, planner.plan(body.request))
    plan = await _review(request, principal, planner, plan, body.request)
    await trace.step("rule", f"Planned {sum(len(s['widgets']) for s in plan['sections'])} widgets from certified "
                     f"measures, {len(plan['gaps'])} gaps", agent="AG-05",
                     detail={"connection": plan.get("connection"), "gaps": plan["gaps"],
                             "sections": [s.get("title") for s in plan["sections"]]})
    plan_id = new_id()
    plan["plan_id"] = plan_id
    async with tenant_tx(principal.tenant_id) as conn:
        await conn.execute(
            text("INSERT INTO content.report_plans (id, tenant_id, owner_id, request, connection_id, model_version_id, "
                 "plan) VALUES (:id, :t, :u, :r, :c, :v, CAST(:p AS jsonb))"),
            {"id": plan_id, "t": principal.tenant_id, "u": principal.user_id, "r": body.request,
             "c": plan["connection_id"], "v": plan["model_version_id"], "p": json.dumps(plan)})
        await audit.record(conn, tenant_id=principal.tenant_id, action="report.planned", object_type="report_plan",
                           object_id=plan_id, outcome="success", actor_id=principal.user_id,
                           correlation_id=getattr(request.state, "request_id", None),
                           details={"widgets": sum(len(s["widgets"]) for s in plan["sections"]),
                                    "gaps": len(plan["gaps"])})
    if "text/event-stream" not in request.headers.get("accept", ""):
        return plan_review.public(plan)

    async def events() -> AsyncIterator[str]:
        yield _sse("progress", {"stage": "Reading the approved model", "connection": plan["connection"]})
        yield _sse("progress", {"stage": "Matching certified measures"})
        yield _sse("plan", plan_review.public(plan))
        yield _sse("complete", {"plan_id": plan_id})

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})


async def _own_plan(conn: Any, principal: Principal, plan_id: str) -> dict[str, Any]:
    try:
        row = await fetch_one(conn, "SELECT * FROM content.report_plans WHERE id = CAST(:id AS uuid) AND owner_id = :u",
                              {"id": plan_id, "u": principal.user_id})
    except Exception:  # noqa: BLE001
        row = None
    if row is None:
        raise TelaError(ErrorCode.NOT_FOUND, "That plan does not exist.")
    return row


@router.get("/reports/plans/{plan_id}")
async def get_plan(plan_id: str, principal: Principal = Depends(require(Perm.REPORTS_CREATE))) -> dict[str, Any]:
    async with tenant_tx(principal.tenant_id) as conn:
        row = await _own_plan(conn, principal, plan_id)
    return {**plan_review.public(row["plan"]), "plan_id": plan_id, "status": row["status"],
            "report_id": str(row["report_id"]) if row["report_id"] else None}


def _titles(plan: dict[str, Any]) -> dict[str, str]:
    return {w["ref"]: w["title"] for s in plan["sections"] for w in s["widgets"]}


def _match_widgets(plan: dict[str, Any], phrase: str) -> list[dict[str, Any]]:
    p = norm(phrase)
    pw = {w for w in words(phrase) if w not in STOP}
    hits = []
    for s in plan["sections"]:
        for w in s["widgets"]:
            t = norm(w["title"])
            if p == t or (pw and pw <= set(words(w["title"]))):
                hits.append(w)
    return hits


@router.post("/reports/plans/{plan_id}/edit")
async def edit_plan(plan_id: str, body: PlanEditIn, request: Request,
                    principal: Principal = Depends(require(Perm.REPORTS_CREATE))) -> dict[str, Any]:
    """Edit the plan in words (US-067): remove, add, rename a section, add a page filter; returns a diff."""
    async with tenant_tx(principal.tenant_id) as conn:
        row = await _own_plan(conn, principal, plan_id)
        if row["status"] == "built":
            raise TelaError(ErrorCode.CONFLICT, "This plan is already built. Edit the report on the canvas.")
        manifest, blueprints = await _manifest(conn, request, str(row["connection_id"]))
    src = await _source(request, principal, body.instruction, str(row["connection_id"]))
    plan = copy.deepcopy(row["plan"])
    plan.pop("review", None)  # the edit starts from the plan on screen and is reviewed afresh (US-243)
    before = _titles(plan)
    planner = Planner(src, manifest, blueprints)
    planner.n = max([int(r[1:]) for r in before] or [0])
    notes: list[str] = []
    for clause in re.split(r";|\bthen\b|\band also\b|\.\s", body.instruction):
        c = clause.strip().rstrip(".")
        if not c:
            continue
        cn = norm(c)
        if m := re.match(r"^(remove|drop|delete|take out|no|without)\s+(the\s+)?(.+?)( section)?$", cn):
            target = m.group(3)
            sec = next((s for s in plan["sections"] if norm(s["title"]) == target or target in norm(s["title"])), None)
            if (m.group(4) and sec) or (sec and not _match_widgets(plan, target)):
                plan["sections"].remove(sec)
                continue
            hits = _match_widgets(plan, target)
            if not hits:
                notes.append(f"Nothing in the plan is called “{target}”.")
            for s in plan["sections"]:
                s["widgets"] = [w for w in s["widgets"] if w not in hits]
        elif m := re.match(r"^rename (the\s+)?(.+?)( section)? to (.+)$", cn):
            sec = next((s for s in plan["sections"] if m.group(2) in norm(s["title"])), None)
            if sec is None:
                notes.append(f"No section is called “{m.group(2)}”.")
            else:
                sec["title"] = clause.strip()[clause.lower().rfind(" to ") + 4:].strip()[:120]
        elif m := re.match(r"^(add|include)\s+(a\s+|an\s+)?(.+?)\s+filter$|^filter by\s+(.+)$", cn):
            phrase = m.group(3) or m.group(4)
            d = match_dimension(phrase, [x for x in src.dims if not x.is_temporal])
            if d is None:
                notes.append(f"There is no “{phrase}” to filter by.")
            elif all(f["ref"] != f"dimension:{d.key}" for f in plan["page_filters"]):
                plan["page_filters"].append({"ref": f"dimension:{d.key}", "label": d.label})
        elif m := re.match(r"^(add|include|also show|show|plus)\s+(.+)$", cn):
            sub = Planner(src, manifest, blueprints)
            sub.n = planner.n
            # A named measure adds just that; a catalogue screen ("the delayed projects layout") adds its section, a
            # theme word ("finance") the theme's.
            extra = sub.plan(m.group(2), themes=False)
            if not extra["sections"] or (match_blueprints(blueprints, m.group(2)) and
                                         re.search(r"\b(layout|report|section|screen|page)\b", m.group(2))):
                sub.n = planner.n
                extra = sub.plan(m.group(2))
            planner.n = sub.n
            added = list(extra["sections"])
            if not added:
                plan["gaps"] += [g for g in extra["gaps"] if g not in plan["gaps"]]
                notes.append(f"I couldn't find certified data for “{m.group(2)}”.")
            for s in added:
                existing = next((x for x in plan["sections"] if x["kind"] == s["kind"] and
                                 (s["kind"] == "headline" or x["title"] == s["title"])), None)
                known = {json.dumps(w["spec"], sort_keys=True) for x in plan["sections"] for w in x["widgets"]}
                fresh = [w for w in s["widgets"] if json.dumps(w["spec"], sort_keys=True) not in known]
                if existing is not None:
                    existing["widgets"] += fresh
                elif fresh:
                    plan["sections"].append({**s, "widgets": fresh})
        else:
            notes.append(f"I couldn't turn “{c}” into a plan change. Try “remove …”, “add …”, “add a region "
                         "filter” or “rename … to …”.")
    plan["sections"] = [s for s in plan["sections"] if s["widgets"]]
    plan = await _validate(request, principal, plan)
    plan["notes"] = notes
    plan = await _review(request, principal, planner, plan, f"{row['request']}\nChange: {body.instruction}")
    for p in (plan, (plan.get("review") or {}).get("alternate")):
        if p is not None:
            after = _titles(p)
            p["diff"] = {"added": [after[r] for r in after if r not in before],
                         "removed": [before[r] for r in before if r not in after]}
    async with tenant_tx(principal.tenant_id) as conn:
        await conn.execute(text("UPDATE content.report_plans SET plan = CAST(:p AS jsonb) WHERE id = :id"),
                           {"p": json.dumps(plan), "id": row["id"]})
    return {**plan_review.public(plan), "plan_id": plan_id}


@router.post("/reports/plans/{plan_id}/show")
async def show_plan(plan_id: str, body: PlanShowIn,
                    principal: Principal = Depends(require(Perm.REPORTS_CREATE))) -> dict[str, Any]:
    """"Show the rules plan" / "Show the AI plan" (US-243): swap a plan refined by the LLM with the rules plan."""
    async with tenant_tx(principal.tenant_id) as conn:
        row = await _own_plan(conn, principal, plan_id)
        if row["status"] == "built":
            raise TelaError(ErrorCode.CONFLICT, "This plan is already built. Edit the report on the canvas.")
        if "alternate" not in (row["plan"].get("review") or {}):
            raise TelaError(ErrorCode.CONFLICT, "This plan wasn't refined by AI.")
        plan = plan_review.swap(row["plan"], body.show)
        await conn.execute(text("UPDATE content.report_plans SET plan = CAST(:p AS jsonb) WHERE id = :id"),
                           {"p": json.dumps(plan), "id": row["id"]})
    return {**plan_review.public(plan), "plan_id": plan_id}


async def build_report(conn: Any, principal: Principal, plan: dict[str, Any], plan_id: str,
                       question: str) -> tuple[dict[str, Any], list[tuple[str, dict[str, Any]]]]:
    """Create the draft report of a plan: its sections, text blocks and widgets (pinned to the Creator's library,
    reusing identical definitions). Returns the report and the placed widgets (plan ref, widget)."""
    report = await create_report(conn, principal, plan["title"], [{"ref": f["ref"]} for f in plan["page_filters"]],
                                 plan_id=plan_id, with_section=False)
    placed: list[tuple[str, dict[str, Any]]] = []
    for s in plan["sections"]:
        sid = await add_section(conn, principal.tenant_id, report["id"], s["title"], s["kind"])
        section = {"id": sid}
        # A blueprint's callouts open its section and its footnote closes it (US-158 text blocks, US-159).
        ch = TEXT_SIZES["callout"][1]
        for i, c in enumerate(s.get("callouts", [])):
            await add_text_item(conn, principal.tenant_id, report["id"], sid, "callout", c["text"], i * ch,
                                title=c.get("title"), level=c.get("level", "info"))
        bottom = len(s.get("callouts", [])) * ch
        for w in s["widgets"]:
            h = def_hash(w["spec"])
            existing = await fetch_one(conn, "SELECT id FROM content.widgets WHERE owner_id = :u AND def_hash = :h "
                                             "AND deleted_at IS NULL", {"u": principal.user_id, "h": h})
            wid = str(existing["id"]) if existing else new_id()
            if existing is None:
                await conn.execute(
                    text("INSERT INTO content.widgets (id, tenant_id, title, spec, visual, model_version_id, "
                         "connection_id, certification_state, owner_id, question, def_hash, created_by) VALUES "
                         "(:id, :t, :title, CAST(:s AS jsonb), :v, :mv, :c, 'certified', :u, :q, :h, :u)"),
                    {"id": wid, "t": principal.tenant_id, "title": w["title"], "s": json.dumps(w["spec"]),
                     "v": w["visual"], "mv": w["spec"]["model_version_id"], "c": w["spec"]["connection_id"],
                     "u": principal.user_id, "q": question, "h": h})
            lay = next((p for p in plan["layout_preview"] if p["widget_ref"] == w["ref"]), None)
            await place_widget(conn, report, section, wid, w["visual"], **(
                {"x": lay["x"], "y": lay["y"], "w": lay["w"], "h": lay["h"]} if lay else {}))
            bottom = max(bottom, lay["y"] + lay["h"] if lay else bottom + default_size(w["visual"])[1])
            placed.append((w["ref"], {**w, "widget_id": wid}))
        if s.get("footnote"):
            await add_text_item(conn, principal.tenant_id, report["id"], sid, "footnote", s["footnote"], bottom)
    return report, placed


async def run_placed(service: QueryService, principal: Principal,
                     placed: list[tuple[str, dict[str, Any]]]) -> list[dict[str, Any]]:
    """Run every placed widget once under the Creator: ok (rows), empty, hidden or failed."""
    results = []
    for ref, w in placed:
        try:
            res = await service.execute(w["spec"], principal, QueryContext(surface="preview",
                                                                          widget_id=w["widget_id"]))
            if res.get("hidden"):
                results.append({"widget_ref": ref, "widget_id": w["widget_id"], "title": w["title"],
                                "status": "hidden", "message": res.get("message")})
            elif res["row_count"] == 0:
                results.append({"widget_ref": ref, "widget_id": w["widget_id"], "title": w["title"],
                                "status": "empty", "message": "No data in your scope"})
            else:
                results.append({"widget_ref": ref, "widget_id": w["widget_id"], "title": w["title"],
                                "status": "ok", "rows": res["row_count"]})
        except TelaError as exc:
            results.append({"widget_ref": ref, "widget_id": w["widget_id"], "title": w["title"], "status": "failed",
                            "message": exc.message})
    return results


@router.post("/reports/plans/{plan_id}/build", status_code=201)
async def build(plan_id: str, request: Request, idempotency_key: str | None = Header(default=None),
                principal: Principal = Depends(require(Perm.REPORTS_CREATE))) -> dict[str, Any]:
    """Build (API-026): create the draft report and run every planned widget once under the Creator's scope."""
    route = f"{BUILD_ROUTE}/{plan_id}"
    async with tenant_tx(principal.tenant_id) as conn:
        if (prior := await replay(conn, principal, route, idempotency_key)) is not None:
            return prior
        row = await _own_plan(conn, principal, plan_id)
        if row["status"] == "built" and row["report_id"]:
            raise TelaError(ErrorCode.CONFLICT, "This plan is already built.", {"report_id": str(row["report_id"])})
        plan = row["plan"]
        if not plan["sections"]:
            raise TelaError(ErrorCode.CONFLICT, "There is nothing in this plan to build.")
        report, placed = await build_report(conn, principal, plan, plan_id, row["request"])
        await conn.execute(text("UPDATE content.report_plans SET status = 'built', report_id = :r WHERE id = :id"),
                           {"r": report["id"], "id": row["id"]})
    results = await run_placed(request.app.state.query_service, principal, placed)
    out = {"report_id": str(report["id"]), "results": results}
    async with tenant_tx(principal.tenant_id) as conn:
        await audit.record(conn, tenant_id=principal.tenant_id, action="report.built", object_type="report",
                           object_id=str(report["id"]), outcome="success", actor_id=principal.user_id,
                           correlation_id=getattr(request.state, "request_id", None),
                           details={"plan_id": plan_id, "widgets": len(results),
                                    "empty": sum(r["status"] == "empty" for r in results),
                                    "failed": sum(r["status"] == "failed" for r in results)})
        await remember(conn, principal, route, idempotency_key, 201, out)
    return out
