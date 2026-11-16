"""Dashboard planned by the LLM from the approved model (AG-05, ``report_plan``): one request in words becomes a
dashboard of reports (pages), each with headline figures (KPIs) and at least ``min_widgets`` charts.

Nothing in this module knows a domain. What the LLM may use comes from the approved model only:

* **measures**  the certified measures in the Creator's scope (key, label, table, format, definition)
* **group-bys** the model's dimensions, and the model's own columns that are good to group, filter or trend by:
  columns with a known vocabulary (``values`` from the data profile), flags and real date columns
* **reach**     which tables a measure's table can be grouped by (the model's accepted links)
* **rules**     the model's business rules by term and meaning (what "open", "late" … mean)
* **data date** ``settings.reporting_date``

The LLM answers with a plan (report → KPIs and widgets → measure key, group-by keys, time grain, filters). Every key
is checked against the model, every group-by must be reachable from the measure's table, every filter value must be
one of the column's known values (matched to its exact spelling), the visual is checked for the result's shape, and
every widget is validated under the Creator's scope. What fails is left out with the reason. A report still short of
``min_widgets`` gets one LLM repair round (told what was dropped and why) and then generic fill-ins from the model
(measures whose words match the report, by its reachable group-bys). So keys are never invented and nothing is
domain-hardcoded.

Build creates one draft report per planned report (through the report planner's build) and a dashboard with those
reports as tabs, then runs every widget once (ok / empty / failed). Planning and building run as a background job:
``POST /dashboards/ai/plan`` → ``GET /dashboards/ai/jobs/{id}`` → ``POST /dashboards/ai/jobs/{id}/build``.
"""

from __future__ import annotations

import asyncio
import copy
import json
import re
from typing import Any, Literal

import structlog
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import text

from tela_api.ask.orchestrator import Source
from tela_api.ask.planner import STOP, words
from tela_api.audit import service as audit
from tela_api.content import wording
from tela_api.content.access import own_dashboard
from tela_api.content.planner import Planner, _source, build_report, layout_preview, run_placed
from tela_api.content.reports import create_report
from tela_api.core.db import fetch_one, tenant_tx
from tela_api.identity.deps import require
from tela_api.identity.permissions import Perm
from tela_api.identity.principal import Principal
from tela_api.llm.client import AgentFailed, run_agent
from tela_api.query.compiler import PERCENTILE_DIALECTS
from tela_api.query.compilers import dialect_for
from tela_api.query.service import QueryContext, QueryService
from tela_common.errors import ErrorCode, TelaError
from tela_common.ids import new_id
from tela_query_spec.model import Dimension, ModelField, ModelSnapshot
from tela_query_spec.spec import ref_key

log = structlog.get_logger()
router = APIRouter(prefix="/api/v1", tags=["dashboards"])

AGENT = "AG-05"
GATEWAY_TASK = "report_plan"
JOB_TTL_S = 7 * 24 * 3600
MAX_REPORTS = 8
MAX_KPIS = 6
MAX_WIDGETS_PER_REPORT = 12
MAX_GROUP_FIELDS = 160
MAX_RULES = 40
VALUES_SHOWN = 15
GRAINS = ("day", "week", "month", "quarter", "year")
VISUAL_HINTS = ("figure", "ranked_bar", "columns", "trend", "stacked_status", "donut", "paired_bars", "heat_grid",
                "treemap", "table", "funnel", "pareto", "meter", "bullet", "waffle")

SYSTEM = (
    "You are a BI dashboard planner. You plan a dashboard for the user's request using ONLY the catalogue you are "
    "given: certified measures (by key), group-bys (by key) and the model's business rules. Rules:\n"
    "1. Use keys exactly as listed. Never invent a measure, group-by, column or value. If something the request asks "
    "for is not in the catalogue, list it under not_possible with a short reason instead of approximating it.\n"
    "2. A widget's group_by keys must be reachable from its measure's table (see reach). A filter's values must be "
    "taken from that group-by's listed values, spelled exactly. Prefer a measure that already means the population "
    "(its definition says so) over adding filters.\n"
    "3. Each report (page) has 2-{max_kpis} KPIs (single figures, no group_by) and at least {min_widgets} widgets "
    "(charts), each answering a different question. Mix group-bys, trends over a date group-by (time + grain), "
    "rankings (top N) and status splits. No duplicate widgets.\n"
    "4. Visual: one of {visuals}. trend needs time; stacked_status needs a status group-by; ranked_bar for a ranking; "
    "donut only for a few parts of a whole; table for a list.\n"
    "5. Respect the business rules: use their terms and meanings for titles and filters (for example a display "
    "relabel or the default population). Never claim actual spend when the measure is a forecast or a budget.\n"
    "6. If the request names reports, KPIs, widgets, global filters or things to avoid, follow them in order; map "
    "every named KPI/widget to catalogue keys. Otherwise design the pages yourself from the catalogue so the "
    "dashboard covers the data's main tables and how they flow into each other.\n"
    "7. global_filters: up to 5 group-by keys (prefer ones every page's measures can reach).\n"
    "Return only the JSON object."
)

REPAIR = (
    "Some planned widgets could not be used (reasons below). For each report listed, give replacement widgets from "
    "the same catalogue so it reaches {min_widgets} widgets. Same rules as before: keys exactly as listed, group-bys "
    "reachable from the measure's table, filter values from the listed values only, no duplicates of the kept widgets."
)


# --------------------------------------------------------------------------- the LLM's answer


class FilterOut(BaseModel):
    by: str = Field(max_length=160)
    op: Literal["=", "!=", "in", "not_in", "is_null", "not_null"] = "in"
    values: list[str] = Field(default_factory=list, max_length=30)


class TimeOut(BaseModel):
    by: str = Field(max_length=160)
    grain: Literal["day", "week", "month", "quarter", "year"] = "month"


class KpiOut(BaseModel):
    title: str = Field(default="", max_length=120)
    measure: str = Field(max_length=120)
    filters: list[FilterOut] = Field(default_factory=list, max_length=4)


class WidgetOut(BaseModel):
    title: str = Field(default="", max_length=120)
    visual: str | None = Field(default=None, max_length=40)
    measure: str = Field(max_length=120)
    extra_measures: list[str] = Field(default_factory=list, max_length=3)
    group_by: list[str] = Field(default_factory=list, max_length=2)
    time: TimeOut | None = None
    filters: list[FilterOut] = Field(default_factory=list, max_length=4)
    top: int | None = Field(default=None, ge=1, le=50)
    question: str = Field(default="", max_length=200)


class ReportOut(BaseModel):
    title: str = Field(max_length=120)
    purpose: str = Field(default="", max_length=300)
    kpis: list[KpiOut] = Field(default_factory=list, max_length=10)
    widgets: list[WidgetOut] = Field(default_factory=list, max_length=20)


class DashboardPlanOut(BaseModel):
    title: str = Field(max_length=160)
    global_filters: list[str] = Field(default_factory=list, max_length=8)
    reports: list[ReportOut] = Field(default_factory=list, max_length=MAX_REPORTS + 2)
    not_possible: list[str] = Field(default_factory=list, max_length=20)


class RepairReport(BaseModel):
    report: str = Field(max_length=120)
    widgets: list[WidgetOut] = Field(default_factory=list, max_length=12)


class RepairOut(BaseModel):
    reports: list[RepairReport] = Field(default_factory=list, max_length=MAX_REPORTS + 2)


# --------------------------------------------------------------------------- the catalogue


class GroupBy:
    """One thing to group, filter or trend by: a model dimension or a model column."""

    def __init__(self, key: str, ref: str, label: str, entity: str, kind: str, values: list[str],
                 labels: dict[str, str] | None = None, temporal: bool = False) -> None:
        self.key, self.ref, self.label, self.entity, self.kind = key, ref, label, entity, kind
        self.values, self.labels, self.temporal = values, labels or {}, temporal

    def raw(self, value: str) -> str | None:
        """The stored spelling of ``value`` (or of the display label that stands for it); None when not known.
        A column with no profiled vocabulary accepts the value as given (checked when the widget runs)."""
        v = value.strip()
        back = {lab.lower(): raw for raw, lab in self.labels.items()}
        if v.lower() in back:
            return back[v.lower()]
        if not self.values:
            return v
        return next((x for x in self.values if x.lower() == v.lower()), None)


ID_NAME = re.compile(r"(^|_)(id|uuid|guid|key)$", re.I)


def _field_kind(f: ModelField) -> str | None:
    if not f.is_queryable or f.sensitivity == "personal" or f.semantic_type in ("identifier", "json", "free_text"):
        return None
    if ID_NAME.search(f.key) and f.data_type not in ("date", "datetime"):
        return None
    if f.data_type in ("date", "datetime"):
        return "date"
    if f.semantic_type == "flag" or f.data_type == "boolean":
        return "flag"
    if f.values:
        return "category"
    return None


class Catalogue:
    def __init__(self, src: Source) -> None:
        self.src, self.snap = src, src.snap
        snap: ModelSnapshot = src.snap
        hidden = src.scope.hidden_fields
        self.measures = {m.key: m for m in src.measures if m.entity in src.scope.entities}
        # Measures this database can't compute (a median or percentile outside the dialects that have one) are left
        # out before the LLM sees them, so the plan never offers a figure that fails when it runs.
        dialect = dialect_for(str(src.connection.get("engine") or snap.engine or ""))
        self.unsupported: dict[str, str] = {}
        if dialect not in PERCENTILE_DIALECTS:
            for k, m in list(self.measures.items()):
                if re.search(r'"agg":\s*"(median|percentile)"', m.expression.model_dump_json()):
                    self.unsupported[k] = f"{m.label}: needs a median or percentile, which this database can't compute"
                    del self.measures[k]
        self.group_bys: dict[str, GroupBy] = {}
        for d in src.dims:
            if d.entity not in src.scope.entities:
                continue
            self.group_bys[d.key] = GroupBy(d.key, f"dimension:{d.key}", d.label, d.entity,
                                            "date" if d.is_temporal else "status" if d.is_status else "category",
                                            self._dim_values(d), d.value_labels, d.is_temporal)
        covered = {(d.entity, d.field) for d in src.dims}
        # Keys are for joining, not grouping: primary keys, link columns and id-named columns are left out.
        for e in snap.entities:
            covered |= {(e.key, k) for k in e.primary_key}
        for ln in snap.links:
            covered |= {(ln.from_entity, k) for k in ln.from_fields} | {(ln.to_entity, k) for k in ln.to_fields}
        extra: list[GroupBy] = []
        for e in snap.entities:
            if e.key not in src.scope.entities:
                continue
            for f in e.fields:
                kind = _field_kind(f)
                if kind is None or (e.key, f.key) in covered or f"{e.key}.{f.key}" in hidden:
                    continue
                extra.append(GroupBy(f"{e.key}.{f.key}", f"field:{e.key}.{f.key}", f.label, e.key, kind,
                                     list(f.values), temporal=kind == "date"))
        extra.sort(key=lambda g: (0 if g.kind in ("category", "flag") else 1, g.entity, g.key))
        for g in extra[:MAX_GROUP_FIELDS]:
            self.group_bys[g.key] = g
        entities = {m.entity for m in self.measures.values()} | {g.entity for g in self.group_bys.values()}
        self.reach: dict[str, list[str]] = {
            a: sorted(b for b in entities if b == a or snap.join_path(a, b) is not None) for a in sorted(entities)}

    def _dim_values(self, d: Dimension) -> list[str]:
        if d.bands:
            return [b.label for b in d.bands]
        e = self.snap.entity(d.entity)
        f = e.field(d.field) if e else None
        return list(f.values) if f is not None and d.of is None else list(d.sort_order or [])

    def reachable(self, measure_entity: str, g: GroupBy) -> bool:
        return g.entity in self.reach.get(measure_entity, [])

    def prompt(self) -> dict[str, Any]:
        snap = self.snap
        rules = [r for r in snap.rules if r.status == "ok"]
        rules.sort(key=lambda r: (not r.always, r.kind not in ("population", "display"), r.entity or "", r.term or ""))
        return {
            "data_date": snap.settings.get("reporting_date"),
            "tables": [{"table": e.key, "label": e.label, "about": (e.description or "")[:160]}
                       for e in snap.entities if e.key in self.src.scope.entities],
            "measures": [{"key": m.key, "label": m.label, "table": m.entity, "format": m.format,
                          "means": (m.definition or m.plain_rule or "")[:160]} for m in self.measures.values()],
            "group_bys": [{"key": g.key, "label": g.label, "table": g.entity, "kind": g.kind,
                           **({"values": [g.labels.get(v, v) for v in g.values[:VALUES_SHOWN]]} if g.values else {}),
                           **({"more_values": len(g.values) - VALUES_SHOWN} if len(g.values) > VALUES_SHOWN else {})}
                          for g in self.group_bys.values()],
            "reach": self.reach,
            "rules": [{"term": r.term, "table": r.entity, "kind": r.kind, "meaning": (r.meaning or "")[:160],
                       **({"applies_always": True} if r.always else {})} for r in rules[:MAX_RULES]],
        }


# --------------------------------------------------------------------------- checking the plan


class Checker:
    """Turns the LLM's widgets into validated plan widgets (or reasons they were left out)."""

    def __init__(self, cat: Catalogue, service: QueryService, principal: Principal) -> None:
        self.cat, self.service, self.principal = cat, service, principal
        self.planner = Planner(cat.src, None)
        self.dropped: list[dict[str, str]] = []

    def _drop(self, report: str, title: str, reason: str) -> None:
        self.dropped.append({"report": report, "widget": title, "reason": reason})

    def _filters(self, report: str, title: str, measure_entity: str,
                 filters: list[FilterOut]) -> list[dict[str, Any]] | None:
        out = []
        for f in filters:
            g = self.cat.group_bys.get(f.by)
            if g is None:
                self._drop(report, title, f"“{f.by}” is not a group-by in the model")
                return None
            if not self.cat.reachable(measure_entity, g):
                self._drop(report, title, f"{g.label} is not linked to this measure's table")
                return None
            if f.op in ("is_null", "not_null"):
                out.append({"ref": g.ref, "op": f.op, "values": []})
                continue
            raws = [g.raw(v) for v in f.values]
            if not raws or any(r is None for r in raws):
                bad = next((v for v, r in zip(f.values, raws, strict=False) if r is None), "")
                self._drop(report, title, f"“{bad}” is not a value of {g.label}" if bad else "a filter has no value")
                return None
            op = f.op
            if op in ("=", "!=") and len(raws) > 1:
                op = "in" if op == "=" else "not_in"
            if op in ("in", "not_in") and len(raws) == 1:
                op = "=" if op == "in" else "!="
            out.append({"ref": g.ref, "op": op, "values": [r for r in raws if r is not None]})
        return out

    async def widget(self, report: str, w: WidgetOut | KpiOut, *, kpi: bool) -> dict[str, Any] | None:
        title = (w.title or w.measure).strip()[:120]
        m = self.cat.measures.get(w.measure)
        if m is None:
            self._drop(report, title, self.cat.unsupported.get(w.measure)
                       or f"“{w.measure}” is not a certified measure you can use")
            return None
        if title in (m.key, "") or title.replace(" ", "_").lower() == m.key:
            title = m.label  # a key as a title reads badly: the measure's own label
        spec: dict[str, Any] = {"measure": {"ref": f"measure:{m.key}"}}
        if not kpi:
            assert isinstance(w, WidgetOut)
            extra = []
            for k in w.extra_measures:
                em = self.cat.measures.get(k)
                if em is None or em.entity != m.entity:
                    self._drop(report, title, f"“{k}” can't be shown beside {m.label}")
                    return None
                extra.append({"ref": f"measure:{em.key}"})
            if extra:
                spec["extra_measures"] = extra
            breakdowns = []
            for k in w.group_by:
                g = self.cat.group_bys.get(k)
                if g is None:
                    self._drop(report, title, f"“{k}” is not a group-by in the model")
                    return None
                if not self.cat.reachable(m.entity, g):
                    self._drop(report, title, f"{m.label} can't be grouped by {g.label} (tables not linked)")
                    return None
                if g.temporal and w.time is None:
                    spec["time"] = {"ref": g.ref, "grain": "month"}
                    continue
                breakdowns.append({"ref": g.ref})
            if breakdowns:
                spec["breakdowns"] = breakdowns
            if w.time is not None:
                g = self.cat.group_bys.get(w.time.by)
                if g is None or not g.temporal:
                    self._drop(report, title, f"“{w.time.by}” is not a date in the model")
                    return None
                if not self.cat.reachable(m.entity, g):
                    self._drop(report, title, f"{m.label} can't be trended by {g.label} (tables not linked)")
                    return None
                spec["time"] = {"ref": g.ref, "grain": w.time.grain}
            if w.top and breakdowns:
                spec["sort"] = [{"by": "measure", "dir": "desc"}]
                spec["limit"] = w.top
        filters = self._filters(report, title, m.entity, w.filters)
        if filters is None:
            return None
        if filters:
            spec["filters"] = filters
        title = wording.fix(title, allowed=wording.allowed_from(self.cat.snap))
        visual = "figure" if kpi else (w.visual if isinstance(w, WidgetOut) else None)
        planned = self.planner.widget(spec, title, visual)
        try:
            await self.service.prepare(planned["spec"], self.principal, QueryContext(surface="preview"))
        except TelaError as exc:
            if "sort" in planned["spec"]:  # a ranking the engine can't sort: keep the breakdown unsorted
                planned["spec"].pop("sort", None)
                planned["spec"].pop("limit", None)
                try:
                    await self.service.prepare(planned["spec"], self.principal, QueryContext(surface="preview"))
                except TelaError as exc2:
                    self._drop(report, title, str(exc2.message))
                    return None
            else:
                self._drop(report, title, str(exc.message))
                return None
        planned["question"] = getattr(w, "question", "") or ""
        return planned


def _sig(w: dict[str, Any]) -> str:
    spec = {k: v for k, v in w["spec"].items() if k not in ("visual",)}
    return json.dumps(spec, sort_keys=True)


def _filler(cat: Catalogue, report: ReportOut, kept: list[dict[str, Any]], need: int) -> list[WidgetOut]:
    """Generic fill-ins for a report short of widgets: measures whose words match the report (else the report's
    own measures' table), each by a group-by it can reach, not already planned. No domain knowledge."""
    used = {_sig(w) for w in kept}
    rw = {w for w in words(f"{report.title} {report.purpose}") if w not in STOP}
    planned_measures = [ref_key(w["spec"]["measure"]["ref"]) for w in kept]
    tables = {cat.measures[k].entity for k in planned_measures if k in cat.measures}

    def score(mkey: str) -> tuple[int, int]:
        m = cat.measures[mkey]
        mw = set(words(f"{m.label} {m.key} {m.entity} {m.definition or ''}"))
        return (-(len(rw & mw) + (2 if m.entity in tables else 0) + (1 if mkey in planned_measures else 0)), 0)

    ranked = sorted(cat.measures, key=score)
    groups = sorted(cat.group_bys.values(),
                    key=lambda g: ({"status": 0, "category": 1, "flag": 2, "date": 3}.get(g.kind, 4),
                                   0 if 2 <= len(g.values) <= 12 else 1, g.key))
    out: list[WidgetOut] = []
    for mkey in ranked[:12]:
        m = cat.measures[mkey]
        for g in groups:
            if len(out) >= need:
                return out
            if not cat.reachable(m.entity, g) or (g.kind != "date" and len(g.values) == 1):
                continue
            if g.temporal:
                w = WidgetOut(title=f"{m.label} by {g.label.lower()}", visual="trend", measure=mkey,
                              time=TimeOut(by=g.key, grain="month"))
            else:
                w = WidgetOut(title=f"{m.label} by {g.label.lower()}",
                              visual="stacked_status" if g.kind == "status" else None,
                              measure=mkey, group_by=[g.key])
            probe = {"measure": {"ref": f"measure:{mkey}"},
                     **({"time": {"ref": g.ref, "grain": "month"}} if g.temporal else
                        {"breakdowns": [{"ref": g.ref}]})}
            if json.dumps(probe, sort_keys=True) in used:
                continue
            used.add(json.dumps(probe, sort_keys=True))
            out.append(w)
            break  # one widget per measure first, for variety
    return out


async def plan_dashboard(request_app: Any, principal: Principal, src: Source, asked: str, min_widgets: int,
                         step: Any) -> dict[str, Any]:
    """The checked dashboard plan: reports with KPIs and widgets, global filters, what was left out and why."""
    gateway = getattr(request_app.state, "gateway", None)
    cat = Catalogue(src)
    if not cat.measures:
        raise TelaError(ErrorCode.CONFLICT, "The model has no certified measures you can use, so there is nothing to "
                        "plan from yet.")
    context = cat.prompt()
    await step(f"Read the model: {len(cat.measures)} measures, {len(cat.group_bys)} group-bys, "
               f"{len(context['rules'])} rules")
    system = SYSTEM.format(max_kpis=MAX_KPIS, min_widgets=min_widgets, visuals=", ".join(VISUAL_HINTS))
    user = json.dumps({"request": asked, "catalogue": context}, default=str)
    try:
        got = await run_agent(gateway, DashboardPlanOut, tenant_id=principal.tenant_id, user_id=principal.user_id,
                              agent=AGENT, task=GATEWAY_TASK, system=system, user=user, max_tokens=8000,
                              temperature=0.2)
    except AgentFailed as exc:
        raise TelaError(ErrorCode.PLANNER_UNAVAILABLE, f"The language model gave no usable plan ({exc.code}).") \
            from exc
    await step(f"The LLM planned {len(got.reports)} pages; checking every key against the model")
    service: QueryService = request_app.state.query_service
    checker = Checker(cat, service, principal)
    reports: list[dict[str, Any]] = []
    for r in got.reports[:MAX_REPORTS]:
        kpis, widgets, seen = [], [], set()
        for k in r.kpis[:MAX_KPIS]:
            if (w := await checker.widget(r.title, k, kpi=True)) and _sig(w) not in seen:
                seen.add(_sig(w))
                kpis.append(w)
        for x in r.widgets[:MAX_WIDGETS_PER_REPORT]:
            if (w := await checker.widget(r.title, x, kpi=False)) and _sig(w) not in seen:
                seen.add(_sig(w))
                widgets.append(w)
        reports.append({"out": r, "kpis": kpis, "widgets": widgets, "seen": seen, "filled": 0})

    short = [p for p in reports if len(p["widgets"]) < min_widgets]
    if short:
        await step(f"{len(short)} pages are short of {min_widgets} widgets; asking the LLM for replacements")
        ask = {"reports": [{"report": p["out"].title, "kept": [w["title"] for w in p["widgets"]],
                            "needed": min_widgets - len(p["widgets"])} for p in short],
               "dropped": checker.dropped[-30:], "catalogue": context}
        try:
            fix = await run_agent(gateway, RepairOut, tenant_id=principal.tenant_id, user_id=principal.user_id,
                                  agent=AGENT, task=GATEWAY_TASK, system=REPAIR.format(min_widgets=min_widgets),
                                  user=json.dumps(ask, default=str), max_tokens=5000, temperature=0.2)
            by_title = {p["out"].title: p for p in short}
            for rr in fix.reports:
                p = by_title.get(rr.report)
                if p is None:
                    continue
                for x in rr.widgets:
                    if len(p["widgets"]) >= MAX_WIDGETS_PER_REPORT:
                        break
                    if (w := await checker.widget(p["out"].title, x, kpi=False)) and _sig(w) not in p["seen"]:
                        p["seen"].add(_sig(w))
                        p["widgets"].append(w)
        except AgentFailed:
            log.info("dashboard_ai.repair_failed")
    for p in reports:
        need = min_widgets - len(p["widgets"])
        if need <= 0:
            continue
        for x in _filler(cat, p["out"], p["kpis"] + p["widgets"], need + 4):
            if len(p["widgets"]) >= min_widgets:
                break
            if (w := await checker.widget(p["out"].title, x, kpi=False)) and _sig(w) not in p["seen"]:
                p["seen"].add(_sig(w))
                p["widgets"].append(w)
                p["filled"] += 1

    global_filters = []
    page_entities = {cat.measures[ref_key(w["spec"]["measure"]["ref"])].entity
                     for p in reports for w in p["kpis"] + p["widgets"]}
    for gk in got.global_filters:
        g = cat.group_bys.get(gk)
        if g is None or g.temporal:
            continue
        global_filters.append({"key": g.key, "ref": g.ref, "label": g.label,
                               "reaches_all": all(cat.reachable(e, g) for e in page_entities),
                               "shared": g.ref.startswith("dimension:")})
    allowed = wording.allowed_from(cat.snap)
    pages = []
    for p in reports:
        if not p["kpis"] and not p["widgets"]:
            continue
        sections = []
        if p["kpis"]:
            sections.append({"key": "headline", "title": "Key figures", "kind": "headline", "widgets": p["kpis"]})
        if p["widgets"]:
            sections.append({"key": "widgets", "title": p["out"].title, "kind": "normal", "widgets": p["widgets"]})
        page_filters = [{"ref": f["ref"], "label": f["label"]} for f in global_filters if f["shared"]][:5]
        pages.append({"title": wording.fix(p["out"].title, allowed=allowed)[:120], "purpose": p["out"].purpose,
                      "sections": sections, "page_filters": page_filters, "layout_preview": layout_preview(sections),
                      "filled_in": p["filled"],
                      "widget_count": len(p["widgets"]), "kpi_count": len(p["kpis"])})
    plan = {
        "title": wording.fix(got.title or asked[:80], allowed=allowed)[:160],
        "connection_id": str(src.connection["id"]), "connection": src.connection["name"],
        "model_version_id": cat.snap.model_version_id, "model_version": cat.snap.version_no,
        "data_date": cat.snap.settings.get("reporting_date"),
        "min_widgets": min_widgets, "reports": pages, "global_filters": global_filters,
        "not_possible": got.not_possible + list(cat.unsupported.values()), "dropped": checker.dropped,
        "catalogue": {"measures": len(cat.measures), "group_bys": len(cat.group_bys),
                      "rules": len(context["rules"])},
    }
    await step(f"Plan ready: {len(pages)} pages, {sum(p['widget_count'] for p in pages)} widgets, "
               f"{sum(p['kpi_count'] for p in pages)} KPIs, {len(checker.dropped)} left out")
    return plan


def public_plan(plan: dict[str, Any]) -> dict[str, Any]:
    """The plan as the UI shows it: per widget its title, visual, measure, group-bys, time and filters by key."""
    def show(w: dict[str, Any]) -> dict[str, Any]:
        s = w["spec"]
        return {"ref": w["ref"], "title": w["title"], "visual": w["visual"], "measure": ref_key(s["measure"]["ref"]),
                "measure_label": w.get("measure_label"),
                "extra_measures": [ref_key(x["ref"]) for x in s.get("extra_measures", [])],
                "group_by": [b["ref"].split(":", 1)[1] for b in s.get("breakdowns", [])],
                "time": ({"by": s["time"]["ref"].split(":", 1)[1], "grain": s["time"].get("grain")}
                         if s.get("time") else None),
                "filters": [{"by": f["ref"].split(":", 1)[1], "op": f["op"], "values": f["values"]}
                            for f in s.get("filters", [])],
                "top": s.get("limit"), "question": w.get("question", "")}

    out = copy.deepcopy({k: v for k, v in plan.items() if k != "reports"})
    out["reports"] = [{k: v for k, v in p.items() if k not in ("sections", "layout_preview")}
                      | {"kpis": [show(w) for s in p["sections"] if s["kind"] == "headline" for w in s["widgets"]],
                         "widgets": [show(w) for s in p["sections"] if s["kind"] != "headline"
                                     for w in s["widgets"]]}
                      for p in plan["reports"]]
    return out


# --------------------------------------------------------------------------- jobs


_LOCAL: dict[str, dict[str, Any]] = {}
_TASKS: set[asyncio.Task[None]] = set()


async def _put(app: Any, job_id: str, state: dict[str, Any]) -> None:
    redis = getattr(app.state, "redis", None)
    if redis is None:
        _LOCAL[job_id] = state
        return
    await redis.set(f"tela:dashplan:{job_id}", json.dumps(state, default=str), ex=JOB_TTL_S)


async def _get(app: Any, job_id: str) -> dict[str, Any] | None:
    redis = getattr(app.state, "redis", None)
    if redis is None:
        return _LOCAL.get(job_id)
    raw = await redis.get(f"tela:dashplan:{job_id}")
    return json.loads(raw) if raw else None


class PlanIn(BaseModel):
    request: str = Field(min_length=1, max_length=8000)
    connection_id: str | None = None
    min_widgets: int = Field(default=6, ge=1, le=12)


async def _run_plan(app: Any, principal: Principal, job_id: str, body: PlanIn, src: Source) -> None:
    state: dict[str, Any] = {"job_id": job_id, "owner": principal.user_id, "status": "planning", "steps": [],
                             "request": body.request}

    async def step(msg: str) -> None:
        state["steps"].append(msg)
        await _put(app, job_id, state)

    try:
        plan = await plan_dashboard(app, principal, src, body.request, body.min_widgets, step)
        state.update(status="planned", plan=plan)
    except TelaError as exc:
        state.update(status="failed", error=exc.message)
    except Exception as exc:
        log.exception("dashboard_ai.plan_failed")
        state.update(status="failed", error=f"Planning failed: {type(exc).__name__}")
    await _put(app, job_id, state)


@router.post("/dashboards/ai/plan", status_code=202)
async def start_plan(body: PlanIn, request: Request,
                     principal: Principal = Depends(require(Perm.DASHBOARDS_CREATE))) -> dict[str, Any]:
    """Plan a dashboard from words with the LLM, from the approved model (background job)."""
    if getattr(request.app.state, "gateway", None) is None:
        raise TelaError(ErrorCode.PLANNER_UNAVAILABLE, "Planning a dashboard needs a language model. Set one up in "
                        "AI settings.")
    if not principal.has(Perm.REPORTS_CREATE):
        raise TelaError(ErrorCode.FORBIDDEN, "Building a dashboard needs permission to create reports.")
    src = await _source(request, principal, body.request, body.connection_id)
    job_id = new_id()
    await _put(request.app, job_id, {"job_id": job_id, "owner": principal.user_id, "status": "planning",
                                     "steps": ["Started"], "request": body.request})
    task = asyncio.create_task(_run_plan(request.app, principal, job_id, body, src))
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)
    return {"job_id": job_id, "connection": src.connection["name"]}


async def _own_job(request: Request, principal: Principal, job_id: str) -> dict[str, Any]:
    state = await _get(request.app, job_id)
    if state is None or state.get("owner") != principal.user_id:
        raise TelaError(ErrorCode.NOT_FOUND, "That dashboard plan is not known (it may have expired).")
    return state


@router.get("/dashboards/ai/jobs/{job_id}")
async def job(job_id: str, request: Request,
              principal: Principal = Depends(require(Perm.DASHBOARDS_CREATE))) -> dict[str, Any]:
    state = await _own_job(request, principal, job_id)
    if state.get("plan"):
        state = {**state, "plan": public_plan(state["plan"])}
    return state


class RemoveIn(BaseModel):
    report: int = Field(ge=0)
    widget_ref: str | None = None  # None removes the whole page


@router.post("/dashboards/ai/jobs/{job_id}/remove")
async def remove(job_id: str, body: RemoveIn, request: Request,
                 principal: Principal = Depends(require(Perm.DASHBOARDS_CREATE))) -> dict[str, Any]:
    """Take a page or a widget out of the plan before building."""
    state = await _own_job(request, principal, job_id)
    plan = state.get("plan")
    if state.get("status") != "planned" or not plan or body.report >= len(plan["reports"]):
        raise TelaError(ErrorCode.CONFLICT, "This plan can't be changed now.")
    if body.widget_ref is None:
        plan["reports"].pop(body.report)
    else:
        page = plan["reports"][body.report]
        for s in page["sections"]:
            s["widgets"] = [w for w in s["widgets"] if w["ref"] != body.widget_ref]
        page["sections"] = [s for s in page["sections"] if s["widgets"]]
        page["layout_preview"] = layout_preview(page["sections"])
        page["widget_count"] = sum(len(s["widgets"]) for s in page["sections"] if s["kind"] != "headline")
        page["kpi_count"] = sum(len(s["widgets"]) for s in page["sections"] if s["kind"] == "headline")
    await _put(request.app, job_id, state)
    return {**state, "plan": public_plan(plan)}


@router.post("/dashboards/ai/jobs/{job_id}/build", status_code=201)
async def build(job_id: str, request: Request,
                principal: Principal = Depends(require(Perm.DASHBOARDS_CREATE))) -> dict[str, Any]:
    """Create one draft report per planned page and a dashboard with them as tabs; run every widget once."""
    if not principal.has(Perm.REPORTS_CREATE):
        raise TelaError(ErrorCode.FORBIDDEN, "Building a dashboard needs permission to create reports.")
    state = await _own_job(request, principal, job_id)
    if state.get("status") == "built":
        raise TelaError(ErrorCode.CONFLICT, "This plan is already built.", {"dashboard_id": state.get("dashboard_id")})
    plan = state.get("plan")
    if state.get("status") != "planned" or not plan or not plan["reports"]:
        raise TelaError(ErrorCode.CONFLICT, "There is nothing in this plan to build.")
    placed_all: list[tuple[str, dict[str, Any], str]] = []
    report_ids: list[str] = []
    async with tenant_tx(principal.tenant_id) as conn:
        for page in plan["reports"]:
            plan_id = new_id()
            page_plan = {**page, "connection_id": plan["connection_id"], "connection": plan["connection"],
                         "model_version_id": plan["model_version_id"], "model_version": plan["model_version"],
                         "gaps": [], "notes": [], "headline_figures": [], "diff": None}
            await conn.execute(
                text("INSERT INTO content.report_plans (id, tenant_id, owner_id, request, connection_id, "
                     "model_version_id, plan) VALUES (:id, :t, :u, :r, :c, :v, CAST(:p AS jsonb))"),
                {"id": plan_id, "t": principal.tenant_id, "u": principal.user_id,
                 "r": f"{state['request'][:1500]}\n[page] {page['title']}", "c": plan["connection_id"],
                 "v": plan["model_version_id"], "p": json.dumps(page_plan)})
            report, placed = await build_report(conn, principal, page_plan, plan_id, state["request"][:2000])
            await conn.execute(text("UPDATE content.report_plans SET status = 'built', report_id = :r WHERE id = :id"),
                               {"r": report["id"], "id": plan_id})
            report_ids.append(str(report["id"]))
            placed_all += [(ref, w, page["title"]) for ref, w in placed]
        did = new_id()
        shared = [{"ref": f["ref"], "exclude_widget_ids": []} for f in plan["global_filters"]
                  if f["shared"] and f["reaches_all"]][:5]
        await conn.execute(
            text("INSERT INTO content.dashboards (id, tenant_id, title, kind, layout, shared_filters, status, "
                 "version_no, owner_id, created_by) VALUES (:id, :t, :title, 'user', 'tabs', CAST(:f AS jsonb), "
                 "'draft', 0, :u, :u)"),
            {"id": did, "t": principal.tenant_id, "title": plan["title"], "f": json.dumps(shared),
             "u": principal.user_id})
        await own_dashboard(conn, principal, did, lock=True)
        for pos, rid in enumerate(report_ids):
            await conn.execute(
                text("INSERT INTO content.dashboard_reports (tenant_id, dashboard_id, report_id, position, "
                     "group_title) VALUES (:t, :d, CAST(:r AS uuid), :p, NULL)"),
                {"t": principal.tenant_id, "d": did, "r": rid, "p": pos})
        await audit.record(conn, tenant_id=principal.tenant_id, action="dashboard.created", object_type="dashboard",
                           object_id=did, outcome="success", actor_id=principal.user_id,
                           correlation_id=getattr(request.state, "request_id", None),
                           details={"planned_by": AGENT, "reports": len(report_ids), "widgets": len(placed_all)})
    results = await run_placed(request.app.state.query_service, principal, [(r, w) for r, w, _ in placed_all])
    for res, (_, _, page_title) in zip(results, placed_all, strict=True):
        res["report"] = page_title
    state.update(status="built", dashboard_id=did, report_ids=report_ids, results=results)
    await _put(request.app, job_id, state)
    return {"dashboard_id": did, "report_ids": report_ids, "results": results}



# --------------------------------------------------------------------------- pin a chat answer to a dashboard

PIN_PAGE = "Pinned from chat"


@router.post("/dashboards/{dashboard_id}/pin-page")
async def pin_page(dashboard_id: str, request: Request,
                   principal: Principal = Depends(require(Perm.DASHBOARDS_CREATE))) -> dict[str, Any]:
    """Where a chat answer pinned to this dashboard goes: the dashboard's "Pinned from chat" page (one of your
    reports), made and put first on the dashboard the first time. Returns its report and section."""
    async with tenant_tx(principal.tenant_id) as conn:
        d = await own_dashboard(conn, principal, dashboard_id, lock=True)
        row = await fetch_one(
            conn, "SELECT r.id FROM content.dashboard_reports dr JOIN content.reports r ON r.id = dr.report_id "
                  "WHERE dr.dashboard_id = :d AND r.owner_id = CAST(:u AS uuid) AND r.title = :t AND "
                  "r.deleted_at IS NULL ORDER BY dr.position LIMIT 1",
            {"d": d["id"], "u": principal.user_id, "t": PIN_PAGE})
        if row is None:
            report = await create_report(conn, principal, PIN_PAGE)
            await conn.execute(text("UPDATE content.dashboard_reports SET position = position + 1 "
                                    "WHERE dashboard_id = :d"), {"d": d["id"]})
            await conn.execute(
                text("INSERT INTO content.dashboard_reports (tenant_id, dashboard_id, report_id, position, "
                     "group_title) VALUES (:t, :d, :r, 0, NULL)"),
                {"t": principal.tenant_id, "d": d["id"], "r": report["id"]})
            await conn.execute(text("UPDATE content.dashboards SET updated_at = now(), "
                                    "draft_revision = draft_revision + 1 WHERE id = :d"), {"d": d["id"]})
            await audit.record(conn, tenant_id=principal.tenant_id, action="dashboard.pin_page_added",
                               object_type="dashboard", object_id=str(d["id"]), outcome="success",
                               actor_id=principal.user_id, correlation_id=getattr(request.state, "request_id", None))
            rid = str(report["id"])
        else:
            rid = str(row["id"])
        sec = await fetch_one(conn, "SELECT id FROM content.report_sections WHERE report_id = CAST(:r AS uuid) "
                                    "ORDER BY position LIMIT 1", {"r": rid})
    return {"dashboard_id": str(d["id"]), "dashboard": d["title"], "report_id": rid,
            "section_id": str(sec["id"]) if sec else None, "page": PIN_PAGE}
