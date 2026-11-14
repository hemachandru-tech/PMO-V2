"""Check a pack draft (US-232): schema errors with their paths, and reference warnings.

*Errors* are what ``source_manifest`` would refuse at install: the manifest (``PackManifest``), every measure and
dimension as the typed AST, the starter reports' texts, and each knowledge document on its own schema (``DOCUMENTS``,
one entry per kind; a later story adds a kind's checks there). A draft with errors is not saved.

*Warnings* are references that don't resolve: a measure reading a field its entity lacks, a dimension on a missing
field, a link between unknown entities, a signature without a primary entity. A draft with warnings is saved; release
(US-238) decides what blocks.

Paths read like the YAML: ``measures[3].expression.where.op``, ``glossary.terms[0].meaning``.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ValidationError

from tela_api.knowledge.guidance import BlueprintsFile, GlossaryFile, RecipesFile, RulesFile
from tela_api.knowledge.patterns import SUGGEST_OPS, ChartPattern
from tela_api.knowledge.values import PackValuesFile
from tela_api.packs.manifest import PackManifest, blueprint_reports, check_report_texts
from tela_api.packs.source import KINDS, kind_of
from tela_query_spec.expr import measure_refs
from tela_query_spec.model import Dimension, Measure, ModelLink

KEY = re.compile(r"^[a-z][a-z0-9_]{1,40}$")  # as model.starter_packs checks them
VERSION = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")


@dataclass
class Report:
    errors: list[dict[str, str]] = field(default_factory=list)
    warnings: list[dict[str, str]] = field(default_factory=list)

    def error(self, path: str, message: str) -> None:
        self.errors.append({"path": path, "message": message})

    def warn(self, path: str, message: str) -> None:
        self.warnings.append({"path": path, "message": message})

    def out(self) -> dict[str, Any]:
        return {"valid": not self.errors, "errors": self.errors, "warnings": self.warnings}


def path_of(prefix: str, loc: tuple[Any, ...]) -> str:
    """``("measures", 3, "expression")`` under ``""`` → ``measures[3].expression``."""
    out = prefix
    for part in loc:
        if isinstance(part, int):
            out += f"[{part}]"
        else:
            out += f".{part}" if out else str(part)
    return out


def _branch(part: Any) -> bool:
    """A union branch or validator name pydantic puts in a location (``function-after[…]``, ``AggExpr``)."""
    return isinstance(part, str) and ("[" in part or "(" in part or part[:1].isupper())


def _pydantic(report: Report, prefix: str, model: type[BaseModel], data: Any) -> Any:
    """Validate; on failure add each error at its path. Where a value may take several shapes (a measure expression
    is an aggregate or arithmetic), only the first shape's errors are kept, so the message says what to fix."""
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        first_branch: dict[tuple[Any, ...], Any] = {}
        seen: set[tuple[str, str]] = set()
        for e in exc.errors():
            loc = tuple(e["loc"])
            cut = next((i for i, p in enumerate(loc) if _branch(p)), None)
            if cut is not None:
                if first_branch.setdefault(loc[:cut], loc[cut]) != loc[cut]:
                    continue
                loc = tuple(p for p in loc if not _branch(p))
            item = (path_of(prefix, loc), str(e["msg"]))
            if item not in seen:
                seen.add(item)
                report.error(*item)
    return None


def _patterns(report: Report, body: dict[str, Any]) -> None:
    for i, p in enumerate(body.get("patterns") or []):
        pattern = _pydantic(report, f"chart_patterns.patterns[{i}]", ChartPattern, p)
        for j, op in enumerate(pattern.suggest.ops if pattern and pattern.suggest else []):
            if op.get("op") not in SUGGEST_OPS:
                report.error(f"chart_patterns.patterns[{i}].suggest.ops[{j}].op",
                             f"Use one of: {', '.join(sorted(SUGGEST_OPS))}.")


def _gold(report: Report, body: dict[str, Any]) -> None:
    for i, ex in enumerate(body.get("examples") or []):
        if not isinstance(ex, dict) or not str(ex.get("question") or "").strip():
            report.error(f"gold_examples.examples[{i}].question", "Each example needs a question.")
        elif not isinstance(ex.get("spec"), dict):
            report.error(f"gold_examples.examples[{i}].spec", "Each example needs the Query Spec of its answer.")


def _model(kind: str, model: type[BaseModel]) -> Callable[[Report, dict[str, Any]], None]:
    def check(report: Report, body: dict[str, Any]) -> None:
        _pydantic(report, kind, model, body)
    return check


# Knowledge document checks by kind (the same schemas the loaders read with). US-233 edits these documents.
DOCUMENTS: dict[str, Callable[[Report, dict[str, Any]], None]] = {
    "glossary": _model("glossary", GlossaryFile),
    "value_synonyms": _model("value_synonyms", PackValuesFile),
    "dashboard_rules": _model("dashboard_rules", RulesFile),
    "chart_patterns": _patterns,
    "analysis_recipes": _model("analysis_recipes", RecipesFile),
    "report_blueprints": _model("report_blueprints", BlueprintsFile),
    "gold_examples": _gold,
}
assert set(DOCUMENTS) == set(KINDS)


def _unique(report: Report, items: list[Any], path: str, what: str) -> None:
    seen: set[str] = set()
    for i, item in enumerate(items):
        key = item.get("key") if isinstance(item, dict) else None
        if key in seen:
            report.error(f"{path}[{i}].key", f"Another {what} already has the key {key!r}.")
        if isinstance(key, str):
            seen.add(key)


def _references(report: Report, manifest: PackManifest, measures: list[Measure | None],
                dims: list[Dimension | None]) -> None:
    fields = {e.key: {f.key for f in e.fields} for e in manifest.entities}
    primary = manifest.signature.get("primary_entity")
    if not primary:
        report.warn("signature.primary_entity", "Name the primary entity, or the pack is never detected on a source.")
    elif primary not in fields:
        report.warn("signature.primary_entity", f"There is no entity {primary!r}.")
    for i, e in enumerate(manifest.entities):
        for j, pk in enumerate(e.primary_key):
            if pk not in fields[e.key]:
                report.warn(f"entities[{i}].primary_key[{j}]", f"{e.key} has no field {pk!r}.")
        for j, shown in enumerate(e.display_fields):
            if shown not in fields[e.key]:
                report.warn(f"entities[{i}].display_fields[{j}]", f"{e.key} has no field {shown!r}.")
    for i, link in enumerate(manifest.links):
        for side in ("from", "to"):
            ent = link.get(f"{side}_entity")
            if ent not in fields:
                report.warn(f"links[{i}].{side}_entity", f"There is no entity {ent!r}.")
                continue
            for j, f in enumerate(link.get(f"{side}_fields") or []):
                if f not in fields[ent]:
                    report.warn(f"links[{i}].{side}_fields[{j}]", f"{ent} has no field {f!r}.")
    for i, m in enumerate(measures):
        if m is None:
            continue
        if m.entity not in fields:
            report.warn(f"measures[{i}].entity", f"There is no entity {m.entity!r}.")
            continue
        for ref in sorted(measure_refs(m.entity, m.expression)):
            ent, _, fk = ref.partition(".")
            if ent not in fields or fk not in fields[ent]:
                report.warn(f"measures[{i}].expression", f"The rule reads {ref}, which the pack doesn't have.")
    for i, d in enumerate(dims):
        if d is None:
            continue
        if d.entity not in fields:
            report.warn(f"dimensions[{i}].entity", f"There is no entity {d.entity!r}.")
        elif d.field not in fields[d.entity]:
            report.warn(f"dimensions[{i}].field", f"{d.entity} has no field {d.field!r}.")


def validate(head: dict[str, Any], documents: dict[str, Any]) -> Report:
    """Check a manifest (as written, ``reports_from`` unresolved) and its knowledge documents."""
    report = Report()
    raw = dict(head)
    if "reports_from" in raw:
        try:
            kind = kind_of(str(raw.pop("reports_from")))
            reports, unavailable = blueprint_reports(documents.get(kind) or {}, raw)
            raw = {**raw, "reports": {**reports, **raw.get("reports", {})},
                   "unavailable_reports": {**unavailable, **raw.get("unavailable_reports", {})}}
        except (ValueError, ValidationError, KeyError, TypeError) as exc:
            report.error("reports_from", f"The starter reports can't be read from the blueprints: {exc}")
    for kind, body in documents.items():
        if kind not in DOCUMENTS:
            report.error(kind, f"Unknown knowledge document {kind!r}.")
        elif not isinstance(body, dict):
            report.error(kind, "The document must be a mapping.")
        else:
            DOCUMENTS[kind](report, body)
    manifest = _pydantic(report, "", PackManifest, raw)
    if manifest is None:
        return report
    if not KEY.match(manifest.pack_key):
        report.error("pack_key", "Use 2 to 41 lower-case letters, digits or _, starting with a letter.")
    if not VERSION.match(manifest.pack_version):
        report.error("pack_version", "Use a version like 1.0.0.")
    _unique(report, [e.model_dump() for e in manifest.entities], "entities", "entity")
    for i, e in enumerate(manifest.entities):
        _unique(report, [f.model_dump() for f in e.fields], f"entities[{i}].fields", "field")
    _unique(report, manifest.measures, "measures", "measure")
    _unique(report, manifest.dimensions, "dimensions", "dimension")
    measures = [_pydantic(report, f"measures[{i}]", Measure, m) for i, m in enumerate(manifest.measures)]
    dims = [_pydantic(report, f"dimensions[{i}]", Dimension, d) for i, d in enumerate(manifest.dimensions)]
    for i, link in enumerate(manifest.links):
        _pydantic(report, f"links[{i}]", ModelLink, link)
    for key, rep in manifest.reports.items():
        try:
            check_report_texts(key, rep)
        except (ValueError, ValidationError, KeyError) as exc:
            report.error(f"reports.{key}", str(exc).splitlines()[0])
    _references(report, manifest, measures, dims)
    return report
