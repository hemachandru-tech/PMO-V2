"""Report blueprints and starter dashboards authored in a pack (ENH-025, US-234; FEAT-075).

A pack's starter reports are its report blueprints (``report_blueprints`` document, read through the manifest's
``reports_from``, US-155) and its starter dashboards are the manifest's ``dashboards``: each a title, its blueprints
as tabs in order and the shared filters. This module checks one blueprint against the pack it is written for, keeps
the two in step when a draft is edited, and turns an existing report or dashboard into blueprints.

* ``check_blueprint`` — the blueprint's schema (errors with paths), the pack's names (``widget_gaps`` over the pack's
  own measures, dimensions and fields), the dashboard it sits on, and the starter report it becomes
  (``check_report_texts``).
* ``put_blueprint`` / ``drop_blueprint`` / ``put_dashboard`` / ``drop_dashboard`` — edits of the draft's two parts:
  a blueprint is a tab of the one dashboard its ``dashboard`` names.
* ``blueprint_from_report`` — "Save as blueprint" (canvas or composer): each widget becomes a blueprint figure,
  visual or detail table by measure and dimension key; widgets on uncertified measures, their own rules, custom SQL,
  fields instead of dimensions, calculations a blueprint can't hold, or names the pack lacks are left out and named.
* ``blueprints_from_dashboard`` / ``add_dashboard`` — "Save as starter dashboard": each report a blueprint, as the
  dashboard's tabs in order, with its shared filters (also used by a pack made from an approved model, US-236).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from tela_api.content.common import is_custom
from tela_api.knowledge.guidance import Blueprint, BlueprintsFile
from tela_api.packs.manifest import blueprint_reports, check_report_texts, widget_gaps
from tela_query_spec.model import ModelSnapshot
from tela_query_spec.spec import ADHOC_KEY, ref_key, ref_kind

BLUEPRINTS_FILE = "report_blueprints.yaml"
MAX_WIDGETS = 6  # Blueprint.widgets
MAX_KPIS = 5
# Query Spec parts a blueprint widget has no place for: a widget using one is left out, never silently changed.
CALCULATIONS = ("derived", "share_of_total", "personal_fields", "personal_measures", "compare", "exists_in", "cohort",
                "top_n_per_group", "running_total", "moving_avg", "rank", "bucket", "percentile", "having")


def empty_document() -> dict[str, Any]:
    return {"version": "0.1.0", "status": "draft", "tones": {}, "blueprints": []}


def errors_of(exc: ValidationError, prefix: str = "") -> list[dict[str, str]]:
    return [{"path": ".".join(str(p) for p in (prefix, *e["loc"]) if p != ""),
             "message": str(e["msg"]).removeprefix("Value error, ")} for e in exc.errors()]


# --------------------------------------------------------------------------- the pack's names


class PackNames:
    """What a pack's blueprints may name: measures (by entity), dimensions, each entity's fields, dashboards."""

    def __init__(self, head: dict[str, Any]) -> None:
        self.measures: dict[str, str] = {str(m.get("key")): str(m.get("entity")) for m in head.get("measures", [])}
        self.measure_labels = {str(m.get("key")): str(m.get("label") or m.get("key")) for m in head.get("measures", [])}
        self.dimensions = {str(d.get("key")) for d in head.get("dimensions", [])}
        self.fields = {str(e.get("key")): {str(f.get("key")) for f in e.get("fields", [])}
                       for e in head.get("entities", [])}
        self.dashboards: dict[str, Any] = dict(head.get("dashboards") or {})

    def field_gaps(self, measure: str, cols: set[str]) -> list[str]:
        return sorted(cols - self.fields.get(self.measures.get(measure, ""), set()))


def check_blueprint(raw: dict[str, Any], head: dict[str, Any], doc: dict[str, Any]) -> list[dict[str, str]]:
    """Problems that keep a blueprint out of the pack, each with the path of the part it is about."""
    try:
        bp = Blueprint.model_validate(raw)
    except ValidationError as exc:
        return errors_of(exc)
    names = PackNames(head)
    out: list[dict[str, str]] = []
    if bp.dashboard not in names.dashboards:
        out.append({"path": "dashboard", "message": f"the pack has no dashboard {bp.dashboard}"})
    if bp.unavailable:
        return out
    for i, k in enumerate(bp.figures()):
        if k.measure not in names.measures:
            out.append({"path": f"kpis.{i}", "message": f"the pack has no measure {k.measure}"})
        out += [{"path": f"kpis.{i}.filters", "message": f"the pack has no dimension {f['ref']}"}
                for f in [*bp.filters, *k.filters] if f.get("ref") not in names.dimensions]
    for i, w in enumerate(bp.widgets):
        d = w.model_dump(exclude_defaults=True)
        d["filters"] = [*bp.filters, *w.filters]
        gaps, _ = widget_gaps(d, set(names.measures), names.dimensions)
        out += [{"path": f"widgets.{i}", "message": f"{w.title}: the pack has no {g}"} for g in gaps]
    if bp.table is not None:
        t = bp.table
        if t.measure not in names.measures:
            out.append({"path": "table.measure", "message": f"the pack has no measure {t.measure}"})
        else:
            cols = set(t.columns) | ({t.sort["field"]} if t.sort else set())
            entity = names.measures[t.measure]
            out += [{"path": "table.columns", "message": f"{entity} has no field {c}"}
                    for c in names.field_gaps(t.measure, cols)]
    # The starter report it becomes (US-155, US-158): texts, figure tones, visuals.
    try:
        reports, _ = blueprint_reports({**doc, "blueprints": [raw]}, head)
        for key, rep in reports.items():
            check_report_texts(key, rep)
    except (ValidationError, ValueError) as exc:
        out += errors_of(exc) if isinstance(exc, ValidationError) else [{"path": "", "message": str(exc)}]
    return out


def check_document(doc: dict[str, Any], head: dict[str, Any]) -> dict[str, list[dict[str, str]]]:
    """Problems of every blueprint, by blueprint id; the document's own problems under ``""``."""
    out: dict[str, list[dict[str, str]]] = {}
    try:
        BlueprintsFile.model_validate({**doc, "blueprints": []})
    except ValidationError as exc:
        out[""] = errors_of(exc)
    for raw in doc.get("blueprints", []):
        problems = check_blueprint(raw, head, doc)
        if problems:
            out[str(raw.get("id"))] = problems
    return out


# --------------------------------------------------------------------------- edits of a draft


def _ids(doc: dict[str, Any]) -> list[str]:
    return [str(b.get("id")) for b in doc.get("blueprints", [])]


def put_blueprint(head: dict[str, Any], doc: dict[str, Any], raw: dict[str, Any], *,
                  replaces: str | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    """The draft with this blueprint added (or put in place of ``replaces``, which may rename it); the blueprint
    becomes a tab of its dashboard (at the end when new there) and leaves any other dashboard."""
    bid = str(raw["id"])
    blueprints = [dict(b) for b in doc.get("blueprints", [])]
    at = next((i for i, b in enumerate(blueprints) if b.get("id") == (replaces or bid)), None)
    if replaces and bid != replaces and bid in _ids(doc):
        raise ValueError(f"the pack already has a blueprint {bid}")
    if at is None:
        blueprints.append(raw)
    else:
        blueprints[at] = raw
    dashboards = {k: {**d, "reports": [bid if r == replaces else r for r in d.get("reports", [])]}
                  for k, d in (head.get("dashboards") or {}).items()}
    for key, d in dashboards.items():
        reports = [r for r in d["reports"] if r != bid]
        if key == raw.get("dashboard") and not raw.get("unavailable"):
            reports = d["reports"] if bid in d["reports"] else [*reports, bid]
        d["reports"] = reports
    return {**head, "dashboards": dashboards, "reports_from": head.get("reports_from", BLUEPRINTS_FILE)}, \
        {**doc, "blueprints": blueprints}


def drop_blueprint(head: dict[str, Any], doc: dict[str, Any], bid: str) -> tuple[dict[str, Any], dict[str, Any]]:
    if bid not in _ids(doc):
        raise KeyError(bid)
    tones_used = {m for b in doc["blueprints"] if b.get("id") != bid for m in _figure_measures(b)}
    dashboards = {k: {**d, "reports": [r for r in d.get("reports", []) if r != bid]}
                  for k, d in (head.get("dashboards") or {}).items()}
    tones = {m: t for m, t in (doc.get("tones") or {}).items() if m in tones_used}
    return {**head, "dashboards": dashboards}, \
        {**doc, "tones": tones, "blueprints": [b for b in doc["blueprints"] if b.get("id") != bid]}


def _figure_measures(raw: dict[str, Any]) -> set[str]:
    return {k if isinstance(k, str) else str(k.get("measure")) for k in raw.get("kpis", [])}


def put_dashboard(head: dict[str, Any], doc: dict[str, Any], key: str, body: dict[str, Any]
                  ) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, str]]]:
    """A starter dashboard: title, its blueprints as tabs in order (each moves here from any other dashboard) and
    the shared filters (pack dimensions)."""
    names = PackNames(head)
    by_id = {str(b.get("id")): b for b in doc.get("blueprints", [])}
    problems = [{"path": f"reports.{i}", "message": f"the pack has no blueprint {r}"}
                for i, r in enumerate(body["reports"]) if r not in by_id]
    problems += [{"path": f"reports.{i}", "message": f"{r} is a data gap and has no tab"}
                 for i, r in enumerate(body["reports"]) if r in by_id and by_id[r].get("unavailable")]
    problems += [{"path": f"filters.{i}", "message": f"the pack has no dimension {f}"}
                 for i, f in enumerate(body["filters"]) if f not in names.dimensions]
    if len(set(body["reports"])) != len(body["reports"]):
        problems.append({"path": "reports", "message": "a blueprint is a tab once"})
    if problems:
        return head, doc, problems
    dashboards = {k: {**d, "reports": [r for r in d.get("reports", []) if r not in body["reports"]]}
                  for k, d in (head.get("dashboards") or {}).items() if k != key}
    dashboards[key] = {**(head.get("dashboards") or {}).get(key, {}), "title": body["title"],
                       "reports": list(body["reports"]), "filters": list(body["filters"])}
    blueprints = [{**b, "dashboard": key} if b.get("id") in body["reports"] else b for b in doc.get("blueprints", [])]
    return {**head, "dashboards": dashboards, "reports_from": head.get("reports_from", BLUEPRINTS_FILE)}, \
        {**doc, "blueprints": blueprints}, []


def drop_dashboard(head: dict[str, Any], doc: dict[str, Any], key: str) -> dict[str, Any]:
    """Without this dashboard; refused while a blueprint still names it (move or delete those first)."""
    dashboards = dict(head.get("dashboards") or {})
    if key not in dashboards:
        raise KeyError(key)
    using = [str(b.get("id")) for b in doc.get("blueprints", []) if b.get("dashboard") == key]
    if using:
        raise ValueError(f"blueprints {', '.join(using)} are on this dashboard: move or delete them first")
    dashboards.pop(key)
    return {**head, "dashboards": dashboards}


# --------------------------------------------------------------------------- save as blueprint


def slug(title: str, taken: set[str]) -> str:
    base = re.sub(r"[^a-z0-9]+", "_", title.lower()).strip("_")
    base = base if re.match(r"^[a-z]", base or "") else f"report_{base}".rstrip("_")
    base = base[:56] or "report"
    out, n = base, 2
    while out in taken or len(out) < 2:
        out, n = f"{base}_{n}", n + 1
    return out


def _reason(item: dict[str, Any], snap: ModelSnapshot | None, names: PackNames) -> str | None:
    """Why a report widget can't be a blueprint widget, or None when it can."""
    spec = item.get("spec") or {}
    if item.get("badge") == "custom_sql" or is_custom(spec) or not spec.get("measure"):
        return "custom SQL"
    refs = [spec["measure"], *spec.get("extra_measures", [])]
    if any(r.get("adhoc") or ref_kind(r["ref"]) != "measure" or ref_key(r["ref"]) == ADHOC_KEY for r in refs):
        return "its own rule, not a certified measure"
    keys = [ref_key(r["ref"]) for r in refs]
    if item.get("badge") not in (None, "certified"):
        return "not certified"
    for k in keys:
        m = snap.measure(k) if snap else None
        if m is None or m.status != "certified":
            return f"measure {k} is not certified"
    used = [c.replace("_", " ") for c in CALCULATIONS if spec.get(c)]
    if used:
        return f"a calculation a blueprint can't hold ({', '.join(used)})"
    dims = [b["ref"] for b in spec.get("breakdowns", [])] + [f["ref"] for f in spec.get("filters", [])]
    if spec.get("time"):
        dims.append(spec["time"]["ref"])
    if any(ref_kind(r) != "dimension" for r in dims):
        return "reads a field, not a dimension"
    if len(spec.get("breakdowns", [])) > 2 or len(keys) > 4:
        return "more breakdowns or measures than a blueprint visual holds"
    missing = [f"measure {k}" for k in keys if k not in names.measures]
    missing += [f"dimension {ref_key(r)}" for r in dims if ref_key(r) not in names.dimensions]
    if missing:
        return f"not in this pack: {', '.join(dict.fromkeys(missing))}"
    return None


def _filters(spec: dict[str, Any]) -> list[dict[str, Any]]:
    return [{**f, "ref": ref_key(f["ref"])} for f in spec.get("filters", [])]


def blueprint_from_report(report: dict[str, Any], snaps: dict[str, ModelSnapshot], head: dict[str, Any], *,
                          bid: str, dashboard: str, taken_tones: dict[str, Any] | None = None
                          ) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, str]]]:
    """(blueprint, its figure tones, widgets left out with the reason) from a report in the frozen form
    (``content/render.py::freeze_dashboard``: sections of items with spec, visual, title and badge)."""
    names = PackNames(head)
    kpis: list[Any] = []
    widgets: list[dict[str, Any]] = []
    table: dict[str, Any] | None = None
    tones: dict[str, Any] = {}
    callout: dict[str, Any] | None = None
    footnote: str | None = None
    left: list[dict[str, str]] = []
    items = [i for s in report.get("sections", []) for i in s.get("items", [])]
    for i in items:
        kind = i.get("kind")
        if kind == "callout":
            if callout is None and str(i.get("text") or "").strip():
                callout = {k: v for k, v in {"title": i.get("title"), "text": i["text"],
                                             "level": i.get("level") or "info"}.items() if v}
            else:
                left.append({"title": i.get("title") or "Callout", "reason": "a blueprint has one callout"})
            continue
        if kind == "footnote":
            footnote = " ".join(t for t in (footnote, str(i.get("text") or "").strip()) if t) or None
            continue
        if kind != "widget":
            left.append({"title": (i.get("text") or "Note")[:60], "reason": "a note has no place in a blueprint"})
            continue
        spec = i.get("spec") or {}
        title = str(i.get("title") or "")[:120]
        snap = snaps.get(str(spec.get("model_version_id")))
        reason = _reason(i, snap, names)
        if reason:
            left.append({"title": title, "reason": reason})
            continue
        measure = ref_key(spec["measure"]["ref"])
        visual = spec.get("visual") or {"type": i.get("visual")}
        vtype = visual.get("type") or i.get("visual")
        if vtype == "figure" and not spec.get("breakdowns") and not spec.get("time") and \
                not spec.get("extra_measures"):
            if len(kpis) >= MAX_KPIS:
                left.append({"title": title, "reason": f"a blueprint has {MAX_KPIS} headline figures"})
                continue
            label = names.measure_labels.get(measure, measure)
            filters = _filters(spec)
            kpis.append({"title": title, "measure": measure, "filters": filters}
                        if filters or (title and title != label) else measure)
            if visual.get("tone") and measure not in (taken_tones or {}):
                tones[measure] = visual["tone"]
            continue
        if vtype == "table" and visual.get("detail"):
            if table is not None:
                left.append({"title": title, "reason": "a blueprint has one detail table"})
                continue
            if spec.get("filters"):
                left.append({"title": title, "reason": "a detail table lists the blueprint's whole population, "
                                                       "without filters of its own"})
                continue
            d = visual["detail"]
            table = {"title": title, "measure": measure, "columns": list(d["columns"]),
                     **({"sort": d["sort"]} if d.get("sort") else {}),
                     **({"export_name": d["export_name"]} if d.get("export_name") else {})}
            continue
        if len(widgets) >= MAX_WIDGETS:
            left.append({"title": title, "reason": f"a blueprint has {MAX_WIDGETS} visuals"})
            continue
        w: dict[str, Any] = {"title": title, "measure": measure, "visual": vtype}
        if spec.get("extra_measures"):
            w["extra_measures"] = [ref_key(r["ref"]) for r in spec["extra_measures"]]
        if spec.get("breakdowns"):
            w["breakdowns"] = [ref_key(b["ref"]) for b in spec["breakdowns"]]
        if spec.get("filters"):
            w["filters"] = _filters(spec)
        if spec.get("time"):
            w["time"] = {**spec["time"], "ref": ref_key(spec["time"]["ref"])}
        if visual.get("note"):
            w["note"] = visual["note"]
        widgets.append(w)
    if not kpis:  # a blueprint opens on figures: the first visual's measure, when the report had none
        first = widgets[0]["measure"] if widgets else table["measure"] if table else None
        if first:
            kpis.append(first)
    title = str(report.get("title") or "Report")[:120]
    first_entity = names.measures.get(kpis[0] if kpis and isinstance(kpis[0], str) else
                                      (kpis[0]["measure"] if kpis else ""), "")
    blueprint = {
        "id": bid, "tab": title[:40], "title": title, "dashboard": dashboard,
        "question": f"What does {title} show?"[:300],
        "population": (f"All {first_entity.replace('_', ' ')} records, as each figure's rule counts them."
                       if first_entity else "As each figure's rule counts them.")[:300],
        "triggers": [title.lower()[:80]], "kpis": kpis, "widgets": widgets,
        **({"table": table} if table else {}), **({"callout": callout} if callout else {}),
        "footnote": (footnote or f"Saved from the report “{title}”.")[:600],
    }
    return blueprint, tones, left


@dataclass
class DashboardBlueprints:
    """A dashboard turned into a starter dashboard: its tabs as blueprints, in order, and its shared filters."""

    dashboard: dict[str, Any]
    blueprints: list[dict[str, Any]]
    tones: dict[str, Any]
    left_out: list[dict[str, str]]
    problems: dict[str, list[dict[str, str]]]
    head: dict[str, Any]  # the pack with the (still empty) dashboard added, as the blueprints were checked against


def blueprints_from_dashboard(frozen: dict[str, Any], snaps: dict[str, ModelSnapshot], head: dict[str, Any],
                              doc: dict[str, Any], *, key: str, title: str,
                              shared_filters: list[dict[str, Any]] | None) -> DashboardBlueprints:
    """Each report of a frozen dashboard (``content/render.py::freeze_dashboard``) as a blueprint on a new starter
    dashboard ``key``; its shared filters the pack has become the dashboard's, the others are named."""
    names = PackNames(head)
    filters = [ref.split(":", 1)[1] for f in shared_filters or [] if (ref := str(f.get("ref", ""))) and ":" in ref]
    left: list[dict[str, str]] = [{"title": f, "reason": "not in this pack: dimension " + f}
                                  for f in filters if f not in names.dimensions]
    head = {**head, "dashboards": {**(head.get("dashboards") or {}),
                                   key: {"title": title, "reports": [], "filters": []}}}
    taken = {str(b.get("id")) for b in doc.get("blueprints", [])}
    made: list[dict[str, Any]] = []
    tones: dict[str, Any] = {}
    problems: dict[str, list[dict[str, str]]] = {}
    for r in frozen["reports"]:
        bid = slug(r["title"], taken)
        taken.add(bid)
        raw, t, lo = blueprint_from_report(r, snaps, head, bid=bid, dashboard=key,
                                           taken_tones={**(doc.get("tones") or {}), **tones})
        tones.update(t)
        left += [{**x, "report": r["title"]} for x in lo]
        if p := check_blueprint(raw, head, {**doc, "tones": {**(doc.get("tones") or {}), **tones}}):
            problems[bid] = p
        made.append(raw)
    dash = {"key": key, "title": title, "reports": [b["id"] for b in made],
            "filters": [f for f in filters if f in names.dimensions]}
    return DashboardBlueprints(dash, made, tones, left, problems, head)


def add_dashboard(doc: dict[str, Any], made: DashboardBlueprints, *, only: set[str] | None = None
                  ) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, str]]]:
    """The pack with the starter dashboard and its blueprints written in (``only``: the blueprint ids to keep)."""
    keep = [b for b in made.blueprints if only is None or b["id"] in only]
    head, doc = made.head, {**doc, "tones": {**(doc.get("tones") or {}), **made.tones}}
    for raw in keep:
        head, doc = put_blueprint(head, doc, raw)
    dash = made.dashboard
    return put_dashboard(head, doc, dash["key"], {"title": dash["title"], "reports": [b["id"] for b in keep],
                                                  "filters": dash["filters"]})
