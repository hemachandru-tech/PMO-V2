"""What a pack version must pass before it is released (ENH-025, US-238; FEAT-075), and what changed since the last.

``static_checks`` reads the pack alone:

* **schema** — ``validate.validate``'s errors (manifest, items, knowledge documents on their own schemas);
* **references** — what ``validate`` only warns about while editing (a measure reading a field the pack lacks, a link
  between unknown entities, no primary entity) blocks a release;
* **knowledge** — every glossary term, value synonym, dashboard rule, chart pattern, analysis recipe and gold example
  names measures, dimensions and fields the pack has (``knowledge.check_document``);
* **blueprints** — every starter report widget (the manifest's and those its report blueprints give) and every
  dashboard's report list resolve in the pack.

``source_checks`` binds the pack to a chosen test connection as an install would (its saved field mapping too):

* **compile** — every measure and dimension the source can serve compiles to SQL; what it can't serve is a warning
  with the gaps;
* **sanity** — one trial run of every measure over all rows (US-199/US-205: aggregates only): a measure the source
  can't compute, one that returns 0, measures of one table with different rules and the same figure;
* **reports** — every starter widget the source can serve compiles; a widget left out on this source is a warning;
* **gold** — every gold example is planned only: its expected Query Spec is validated on the bound pack and compiled,
  no rows are fetched.

Errors block a release; each warning needs a reason when the version is submitted. An issue's ``id`` is stable for
the same check, path and message, so a reason given at submission still matches when the reviewer releases.

``diff`` lists what a version adds, changes and removes against the previous released one, with a measure's rule text
before and after.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncConnection

from tela_api.core.db import fetch_one
from tela_api.identity.principal import Principal
from tela_api.model import guards
from tela_api.model.catalog import stamp_source_types
from tela_api.model.store import build_snapshot
from tela_api.packs.manifest import PackManifest, bind, expand_reports, fit_widgets, match, spec_of, widget_gaps
from tela_api.packs.source import document_sha
from tela_api.packs.studio import knowledge, store
from tela_api.packs.studio.validate import validate
from tela_api.query.asof import pin_dates, reporting_date, today_in
from tela_api.query.compilers import compile_spec
from tela_api.query.service import QueryContext, QueryService
from tela_api.query.validator import SpecValidator
from tela_common.errors import ErrorCode, TelaError
from tela_common.ids import new_id
from tela_query_spec.model import ModelSnapshot
from tela_query_spec.spec import QuerySpec

CHECKS: tuple[tuple[str, str], ...] = (
    ("schema", "Schema"), ("references", "References"), ("knowledge", "Knowledge"),
    ("blueprints", "Report blueprints"), ("compile", "Compiles on the test connection"),
    ("sanity", "Sanity checks"), ("reports", "Starter reports on the test connection"),
    ("gold", "Gold examples (plan only)"),
)


@dataclass
class Checks:
    issues: list[dict[str, str]] = field(default_factory=list)
    ran: set[str] = field(default_factory=set)
    facts: dict[str, Any] = field(default_factory=dict)

    def add(self, level: str, check: str, path: str, message: str) -> None:
        iid = hashlib.sha256(f"{check}|{path}|{message}".encode()).hexdigest()[:16]
        if any(i["id"] == iid for i in self.issues):
            return
        self.issues.append({"id": iid, "level": level, "check": check, "path": path, "message": message})

    def error(self, check: str, path: str, message: str) -> None:
        self.add("error", check, path, message)

    def warn(self, check: str, path: str, message: str) -> None:
        self.add("warning", check, path, message)

    def merge(self, other: Checks) -> Checks:
        for i in other.issues:
            self.add(i["level"], i["check"], i["path"], i["message"])
        self.ran |= other.ran
        self.facts.update(other.facts)
        return self

    @property
    def errors(self) -> list[dict[str, str]]:
        return [i for i in self.issues if i["level"] == "error"]

    @property
    def warnings(self) -> list[dict[str, str]]:
        return [i for i in self.issues if i["level"] == "warning"]

    def out(self) -> dict[str, Any]:
        steps = []
        for key, label in CHECKS:
            mine = [i for i in self.issues if i["check"] == key]
            steps.append({"key": key, "label": label, "ran": key in self.ran,
                          "errors": sum(i["level"] == "error" for i in mine),
                          "warnings": sum(i["level"] == "warning" for i in mine)})
        return {"passed": not self.errors, "errors": self.errors, "warnings": self.warnings, "steps": steps,
                "facts": self.facts}


def expanded(head: dict[str, Any], docs: dict[str, Any]) -> dict[str, Any]:
    """The manifest as it is stored on release: starter reports expanded from the blueprints (``load-packs``'s
    form, which install, match and updates read)."""
    return expand_reports(head, docs.get)


# --------------------------------------------------------------------------- the pack alone


def static_checks(head: dict[str, Any], docs: dict[str, Any]) -> Checks:
    out = Checks(ran={"schema", "references", "knowledge", "blueprints"})
    report = validate(head, docs)
    for e in report.errors:
        out.error("schema", e["path"], e["message"])
    for w in report.warnings:
        out.error("references", w["path"], w["message"])
    if report.errors:
        return out
    full = expanded(head, docs)
    for kind in knowledge.LISTS:
        if kind in docs:
            for w in knowledge.check_document(kind, docs[kind], full)["warnings"]:
                out.error("knowledge", w["path"], w["message"])
    _blueprints(out, full)
    return out


def _blueprints(out: Checks, full: dict[str, Any]) -> None:
    measures = {m["key"]: m["entity"] for m in full.get("measures") or []}
    dims = {d["key"] for d in full.get("dimensions") or []}
    fields = {e["key"]: {f["key"] for f in e.get("fields") or []} for e in full.get("entities") or []}

    def field_gaps(measure: str, cols: set[str]) -> list[str]:
        return sorted(cols - fields.get(measures.get(measure, ""), set()))

    reports = full.get("reports") or {}
    for key, rep in reports.items():
        for i, w in enumerate(rep.get("widgets") or []):
            hard, soft = widget_gaps(w, set(measures), dims, field_gaps)
            if hard or soft:
                out.error("blueprints", f"reports.{key}.widgets[{i}]",
                          f"{rep.get('title', key)}: the widget {w.get('title', i)!r} needs "
                          f"{', '.join(hard + soft)}, which the pack doesn't have.")
    known = set(reports) | set(full.get("unavailable_reports") or {})
    for dkey, dash in (full.get("dashboards") or {}).items():
        for j, rkey in enumerate((dash or {}).get("reports") or []):
            if rkey not in known:
                out.error("blueprints", f"dashboards.{dkey}.reports[{j}]",
                          f"The dashboard {dkey} lists the report {rkey}, which the pack doesn't have.")


# --------------------------------------------------------------------------- on a test connection


def compile_problem(snap: ModelSnapshot, connection: dict[str, Any], spec: dict[str, Any]) -> str | None:
    """Why a Query Spec doesn't validate or compile on this bound pack (None when it compiles). No rows are read."""
    try:
        qs = QuerySpec.model_validate({**spec, "model_version_id": snap.model_version_id,
                                       "connection_id": snap.connection_id})
        as_of = reporting_date(snap.settings, None)
        compile_spec(connection, pin_dates(SpecValidator(snap).validate(qs), as_of=as_of, today=today_in(None)),
                     as_of=as_of)
    except TelaError as exc:
        reason = (exc.details or {}).get("reason") if isinstance(exc.details, dict) else None
        return f"{exc.message} {reason}".strip() if reason else str(exc.message)
    except (ValueError, ValidationError) as exc:
        return str(exc).splitlines()[0]
    return None


def _refs(spec: Any) -> set[str]:
    if isinstance(spec, dict):
        found = {str(spec["ref"])} if isinstance(spec.get("ref"), str) else set()
        return found.union(*(_refs(v) for v in spec.values()))
    if isinstance(spec, list):
        return set().union(*(_refs(v) for v in spec)) if spec else set()
    return set()


@dataclass
class Bound:
    """The pack bound to the test connection as an install would bind it."""

    snap: ModelSnapshot
    connection: dict[str, Any]
    connection_id: str
    name: str
    definition: dict[str, Any]
    manifest: PackManifest


async def source_checks(conn: AsyncConnection, service: QueryService, principal: Principal, row: store.PackRow,
                        head: dict[str, Any], docs: dict[str, Any], connection_id: str,
                        request_id: str | None) -> Checks:
    """Bind the pack to the test connection and run the compile, sanity, report and gold checks. Reads nothing but
    the trial figures (aggregates)."""
    out = Checks(ran={"compile", "sanity", "reports", "gold"})
    bound = await _bind(conn, service, out, row, expanded(head, docs), connection_id)
    if bound is None:
        return out
    compiled = _compile_items(out, bound)
    await _sanity(out, service, principal, bound, compiled, request_id)
    _report_widgets(out, bound)
    _gold(out, bound, docs)
    return out


async def _bind(conn: AsyncConnection, service: QueryService, out: Checks, row: store.PackRow,
                full: dict[str, Any], connection_id: str) -> Bound | None:
    from tela_api.packs.router import _catalog, _model_and_base, saved_mapping  # the pack router imports the studio
    from tela_api.packs.studio.author import catalog as read_catalog

    await read_catalog(conn, connection_id)  # the schema must have been read (409 otherwise)
    catalog = await _catalog(conn, connection_id)
    _, base = await _model_and_base(conn, connection_id)
    try:
        connection = await service._connection(conn, connection_id)
    except TelaError as exc:
        raise TelaError(ErrorCode.CONNECTION_NOT_READY, "The test connection can't be queried right now.") from exc
    named = await fetch_one(conn, "SELECT name FROM conn.connections WHERE id = CAST(:c AS uuid)", {"c": connection_id})
    name = str(named["name"]) if named else "the test connection"
    manifest = PackManifest.model_validate(full)
    result = match(manifest, catalog, model=base, mapping=await saved_mapping(conn, connection_id, row.pack_key))
    binding = bind(manifest, result)
    out.facts.update(connection={"id": connection_id, "name": name}, confidence=round(result.confidence, 3))
    found = {e["key"] for e in binding.definition["entities"]}
    for e in manifest.entities:
        if e.key not in found:
            out.warn("compile", f"entities.{e.key}", f"{e.label} is not found on {name}.")
    if not found:
        out.error("compile", "entities", f"None of the pack's entities is found on {name}. Choose a test connection "
                                         "this pack is meant for.")
        return None
    for item in binding.items:
        if item["availability"] == "unavailable" and item["kind"] in ("measure", "dimension"):
            out.warn("compile", f"{item['kind']}s.{item['key']}",
                     f"{item['title']} is not available on {name}: it needs {', '.join(item['gaps'])}.")
    snap = build_snapshot(binding.definition, version_id=new_id(), connection_id=connection_id, version_no=None,
                          engine=connection["engine"])
    stamp_source_types(snap, {c["qualified_name"].lower(): c["columns"] for c in catalog})
    return Bound(snap, connection, connection_id, name, binding.definition, manifest)


def _compile_items(out: Checks, b: Bound) -> list[dict[str, Any]]:
    """Every measure and dimension the source serves compiles; returns the measures that do."""
    compiled = []
    for m in b.definition["measures"]:
        problem = compile_problem(b.snap, b.connection, {"measure": {"ref": f"measure:{m['key']}"}})
        if problem:
            out.error("compile", f"measures.{m['key']}", f"{m['label']} doesn't compile on {b.name}: {problem}")
        else:
            compiled.append(m)
    by_entity: dict[str, str] = {}
    for m in compiled:
        by_entity.setdefault(m["entity"], m["key"])
    for d in b.definition["dimensions"]:
        measure = by_entity.get(d["entity"]) or next(iter(by_entity.values()), None)
        if measure is None:
            continue
        problem = compile_problem(b.snap, b.connection, {"measure": {"ref": f"measure:{measure}"},
                                                         "breakdowns": [{"ref": f"dimension:{d['key']}"}]})
        if problem:
            out.error("compile", f"dimensions.{d['key']}", f"{d['label']} doesn't compile on {b.name}: {problem}")
    out.facts["compiled"] = {"measures": len(compiled), "dimensions": len(b.definition["dimensions"])}
    return compiled


async def _sanity(out: Checks, service: QueryService, principal: Principal, b: Bound, compiled: list[dict[str, Any]],
                  request_id: str | None) -> None:
    """One trial run of every compiled measure over all rows, with US-199's sanity warnings."""
    if not compiled:
        return
    try:
        _, figures = await service.trial(
            snapshot=b.snap, connection=b.connection, principal=principal, fields=[], measures=True,
            ctx=QueryContext(surface="precheck", request_id=request_id, use_cache=False))
    except TelaError as exc:
        out.warn("sanity", "measures", f"The trial run on {b.name} failed: {exc.message}")
        return
    for m in compiled:
        if figures.get(m["key"]) is None:
            out.warn("sanity", f"measures.{m['key']}", f"{m['label']} could not be computed on {b.name}.")
    for w in guards.measure_warnings(compiled, figures):
        out.warn("sanity", f"measures.{w['ref'].partition(':')[2]}", w["issue"])
    out.facts["figures"] = {m["key"]: figures.get(m["key"]) for m in compiled}


def _report_widgets(out: Checks, b: Bound) -> None:
    """Every starter widget the source serves compiles; one it can't serve is left out (a warning)."""
    for key, rep in b.manifest.reports.items():
        for i, w in enumerate(fit_widgets(rep, b.definition)):
            path = f"reports.{key}.widgets[{i}]"
            title = rep["widgets"][i].get("title", i)
            if w is None:
                out.warn("reports", path, f"{rep['title']}: the widget {title!r} is left out on {b.name}.")
                continue
            spec = spec_of(w, model_version_id=b.snap.model_version_id, connection_id=b.connection_id)
            if (problem := compile_problem(b.snap, b.connection, spec)) is not None:
                out.error("reports", path, f"{rep['title']}: the widget {title!r} doesn't compile: {problem}")


def _gold(out: Checks, b: Bound, docs: dict[str, Any]) -> None:
    """Gold examples planned only: the expected spec validates on the bound pack and compiles; no rows are read."""
    available = {f"measure:{m['key']}" for m in b.definition["measures"]} | {
        f"dimension:{d['key']}" for d in b.definition["dimensions"]}
    planned = 0
    for i, ex in enumerate((docs.get("gold_examples") or {}).get("examples") or []):
        path = f"gold_examples.examples[{i}]"
        question = str(ex.get("question") or "")
        spec = {k: v for k, v in (ex.get("spec") or {}).items() if k not in ("model_version_id", "connection_id")}
        missing = sorted(r for r in _refs(spec) if r.split(":")[0] in ("measure", "dimension") and r not in available)
        if missing:
            out.warn("gold", path, f"{question!r} can't be planned on {b.name}: it needs {', '.join(missing)}.")
        elif (problem := compile_problem(b.snap, b.connection, spec)) is not None:
            out.error("gold", path, f"{question!r}: its expected answer doesn't plan: {problem}")
        else:
            planned += 1
    out.facts["gold_planned"] = planned


# --------------------------------------------------------------------------- what changed


def _by(items: list[dict[str, Any]] | None, key: Callable[[dict[str, Any]], str]) -> dict[str, dict[str, Any]]:
    return {key(i): i for i in items or [] if isinstance(i, dict)}


def _same(a: Any, b: Any) -> bool:
    return json.dumps(a, sort_keys=True, default=str) == json.dumps(b, sort_keys=True, default=str)


def _change(before: Any, after: Any) -> str:
    if before is None:
        return "added"
    return "removed" if after is None else "changed"


def _link(ln: dict[str, Any]) -> str:
    return f"{ln.get('from_entity')}.{','.join(ln.get('from_fields') or [])}>{ln.get('to_entity')}"


# Knowledge items by kind: the list they are kept in and what names one item.
_ITEM_KEY: dict[str, tuple[str, Callable[[dict[str, Any]], str]]] = {
    "glossary": ("terms", lambda t: str(t.get("term"))),
    "value_synonyms": ("values", lambda v: f"{','.join(v.get('dimensions') or [])}:{v.get('value')}"),
    "dashboard_rules": ("rules", lambda r: str(r.get("id") or r.get("title"))),
    "chart_patterns": ("patterns", lambda p: str(p.get("id") or p.get("title"))),
    "analysis_recipes": ("recipes", lambda r: str(r.get("id") or r.get("title"))),
    "report_blueprints": ("blueprints", lambda b: str(b.get("id"))),
    "gold_examples": ("examples", lambda e: str(e.get("question"))),
}


def diff(before: tuple[dict[str, Any], dict[str, Any]] | None,
         after: tuple[dict[str, Any], dict[str, Any]]) -> list[dict[str, Any]]:
    """What ``after`` (manifest as stored on release, documents) adds, changes and removes against ``before`` (the
    previous released version; None for a first release, where everything is added). Measures carry their rule text
    (``plain_rule``) before and after; a changed item names which of its keys changed."""
    old_head, old_docs = before or ({}, {})
    new_head, new_docs = after
    out: list[dict[str, Any]] = []

    def compare(kind: str, old: dict[str, dict[str, Any]], new: dict[str, dict[str, Any]],
                title: Callable[[dict[str, Any]], str], rule: bool = False) -> None:
        for k in sorted(old.keys() | new.keys()):
            a, b = old.get(k), new.get(k)
            if a is not None and b is not None and _same(a, b):
                continue
            entry: dict[str, Any] = {"kind": kind, "key": k, "title": title(b or a or {}), "change": _change(a, b)}
            if a is not None and b is not None:
                entry["fields"] = sorted(f for f in a.keys() | b.keys() if not _same(a.get(f), b.get(f)))
            if rule:
                entry["rule"] = {"before": (a or {}).get("plain_rule"), "after": (b or {}).get("plain_rule")}
            out.append(entry)

    compare("entity", {k: {x: y for x, y in e.items() if x != "fields"} for k, e in
                       _by(old_head.get("entities"), lambda e: str(e["key"])).items()},
            {k: {x: y for x, y in e.items() if x != "fields"} for k, e in
             _by(new_head.get("entities"), lambda e: str(e["key"])).items()}, lambda e: str(e.get("label")))

    def fields(head: dict[str, Any]) -> dict[str, dict[str, Any]]:
        return {f"{e['key']}.{f['key']}": f for e in head.get("entities") or [] for f in e.get("fields") or []}

    compare("field", fields(old_head), fields(new_head), lambda f: str(f.get("label")))
    compare("link", _by(old_head.get("links"), _link), _by(new_head.get("links"), _link),
            lambda ln: f"{ln.get('from_entity')} → {ln.get('to_entity')}")
    compare("measure", _by(old_head.get("measures"), lambda m: str(m["key"])),
            _by(new_head.get("measures"), lambda m: str(m["key"])), lambda m: str(m.get("label")), rule=True)
    compare("dimension", _by(old_head.get("dimensions"), lambda d: str(d["key"])),
            _by(new_head.get("dimensions"), lambda d: str(d["key"])), lambda d: str(d.get("label")))
    compare("report", dict(old_head.get("reports") or {}), dict(new_head.get("reports") or {}),
            lambda r: str(r.get("title")))
    compare("dashboard", dict(old_head.get("dashboards") or {}), dict(new_head.get("dashboards") or {}),
            lambda d: str(d.get("title") or ""))
    for kind, (lst, key) in _ITEM_KEY.items():
        compare(kind, _by((old_docs.get(kind) or {}).get(lst), key), _by((new_docs.get(kind) or {}).get(lst), key),
                lambda i, key=key: key(i)[:160])  # type: ignore[misc]
    for k in ("signature", "calculators"):
        if not _same(old_head.get(k), new_head.get(k)) and (old_head.get(k) or new_head.get(k)):
            out.append({"kind": "settings", "key": k, "title": k.capitalize(),
                        "change": _change(old_head.get(k) or None, new_head.get(k) or None)})
    return out


def diff_summary(changes: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    out: dict[str, dict[str, int]] = {}
    for c in changes:
        out.setdefault(c["kind"], {"added": 0, "changed": 0, "removed": 0})[c["change"]] += 1
    return out


def content_revision(row: store.PackRow, docs: dict[str, Any], attachments: list[str]) -> str:
    """One revision over the manifest, every knowledge document and the attached files: what was submitted is what
    is released."""
    return document_sha({"manifest": row.sha, "documents": {k: document_sha(v) for k, v in sorted(docs.items())},
                         "attachments": sorted(attachments)})
