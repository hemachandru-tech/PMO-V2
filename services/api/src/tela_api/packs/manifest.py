"""Starter pack manifests (US-037; FEAT-010, FEAT-051; DATA-022, DATA-023).

A manifest is versioned data (``packs/<pack>/manifest.yaml``): detection
signature, logical entities whose fields list candidate physical names (the
extract name and the Dataverse logical name), measures, dimensions, reports
and unavailable reports with named gaps.

``match`` scores a connection's catalogue against a manifest; ``bind`` turns a
manifest into a model draft definition bound to the physical names found.
Items whose fields are missing are not installed into the model; they are
recorded as ``unavailable`` with the missing fields named (REQ-024).

A manifest may take its starter reports from the pack's report blueprints
(``reports_from: report_blueprints.yaml``, US-155): each available blueprint
becomes a report (figures, visuals, detail table, callout, footnote, tab name)
and each data-gap blueprint an unavailable report. A starter report shows the
widgets the model can run; the others are left out and named (US-155), and the
report is unavailable only when none is left.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field

from tela_api.packs.mapping import (
    PackMapping,
    dimension_labels,
    entity_terms,
    field_terms,
    fits,
    label_column,
    name_variants,
    norm,
    publisher_prefixes,
    rewrite_values,
    singular,
)
from tela_api.packs.source import DEFAULT_PACKS_DIR as DEFAULT_PACKS_DIR  # re-exported for callers
from tela_api.packs.source import FilePackSource, PackSource, kind_of
from tela_api.packs.source import discover as discover  # re-exported for callers
from tela_query_spec.expr import measure_fields, split_ref
from tela_query_spec.model import Dimension, Measure
from tela_query_spec.spec import Visual


class PackField(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str
    label: str
    data_type: str
    columns: list[str] = Field(min_length=1)
    required: bool = False
    semantic_type: str | None = None
    sensitivity: str = "internal"
    format: dict[str, Any] = Field(default_factory=dict)
    description: str | None = None
    blank_handling: str | None = None
    synonyms: list[str] = Field(default_factory=list)  # other words a source may use (US-154)
    values: list[str] = Field(default_factory=list)  # values the rules speak of, for value mapping (US-154)


class PackEntity(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str
    label: str
    grain: str | None = None
    object_candidates: list[str] = Field(min_length=1)
    primary_key: list[str]
    display_fields: list[str] = Field(default_factory=list)
    synonyms: list[str] = Field(default_factory=list)
    fields: list[PackField]


class PackManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pack_key: str
    pack_version: str
    title: str
    released_at: str
    signature: dict[str, Any]
    entities: list[PackEntity]
    links: list[dict[str, Any]] = Field(default_factory=list)
    measures: list[dict[str, Any]]
    dimensions: list[dict[str, Any]] = Field(default_factory=list)
    calculators: list[dict[str, Any]] = Field(default_factory=list)
    reports: dict[str, dict[str, Any]] = Field(default_factory=dict)
    dashboards: dict[str, dict[str, Any]] = Field(default_factory=dict)
    unavailable_reports: dict[str, dict[str, Any]] = Field(default_factory=dict)

    def entity(self, key: str) -> PackEntity | None:
        return next((e for e in self.entities if e.key == key), None)


def load_manifest(path: Path) -> tuple[PackManifest, dict[str, Any], str]:
    """A pack folder's manifest (``packs/<key>/manifest.yaml``): validated, ``reports_from`` resolved, its SHA."""
    return source_manifest(FilePackSource(path.parent))


def source_manifest(source: PackSource) -> tuple[PackManifest, dict[str, Any], str]:
    """A pack's manifest from its source (a folder or the database, US-230): validated, starter reports taken from
    the report blueprints when it says ``reports_from``, and the SHA-256 of the result."""
    raw = expand_reports(source.head(), source.document)
    manifest = PackManifest.model_validate(raw)
    # Every measure expression must parse as the typed AST (data, never SQL).
    for m in manifest.measures:
        Measure.model_validate(m)
    for d in manifest.dimensions:
        Dimension.model_validate(d)
    for key, rep in manifest.reports.items():
        check_report_texts(key, rep)
    sha = hashlib.sha256(json.dumps(raw, sort_keys=True, default=str).encode()).hexdigest()
    return manifest, raw, sha


def expand_reports(head: dict[str, Any], document: Callable[[str], dict[str, Any] | None]) -> dict[str, Any]:
    """The manifest with ``reports_from`` resolved: the starter reports and unavailable reports its blueprints give,
    under the manifest's own. This is the form ``tela-admin load-packs`` stores and install, match and pack updates
    read, so a tenant version stores it when it is released (US-238)."""
    raw = dict(head)
    if "reports_from" not in raw:
        return raw
    kind = kind_of(str(raw.pop("reports_from")))
    reports, unavailable = blueprint_reports(document(kind) or {}, raw)
    return {**raw, "reports": {**reports, **raw.get("reports", {})},
            "unavailable_reports": {**unavailable, **raw.get("unavailable_reports", {})}}


def blueprint_reports(blueprints: dict[str, Any] | Path,
                      raw: dict[str, Any]) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    """Starter reports from report blueprints (US-155): the headline figures (with the pack's tone thresholds), the
    visuals and the detail table in the starter-report widget form; the blueprint's filters apply to every one.
    ``blueprints`` is the report blueprints document (or, as before US-230, the path of its file)."""
    from tela_api.knowledge.guidance import BlueprintsFile  # guidance imports this module

    if isinstance(blueprints, Path):
        blueprints = yaml.safe_load(blueprints.read_text()) or {}
    bf = BlueprintsFile.model_validate(blueprints)
    labels = {m["key"]: m["label"] for m in raw.get("measures", [])}
    reports: dict[str, dict[str, Any]] = {}
    unavailable: dict[str, dict[str, Any]] = {}
    for bp in bf.blueprints:
        if bp.unavailable:
            unavailable[bp.id] = {"title": bp.title, "gaps": list(bp.unavailable)}
            continue
        widgets: list[dict[str, Any]] = []
        for k in bp.figures():
            widgets.append({"title": k.title or labels.get(k.measure, k.measure), "measure": k.measure,
                            "visual": "figure", **({"filters": [*bp.filters, *k.filters]} if bp.filters or k.filters
                                                   else {}),
                            **({"tone": bf.tones[k.measure]} if k.measure in bf.tones else {})})
        for w in bp.widgets:
            d = w.model_dump(exclude_defaults=True)
            if bp.filters or w.filters:
                d["filters"] = [*bp.filters, *w.filters]
            widgets.append(d)
        if bp.table:
            widgets.append({"title": bp.table.title, "measure": bp.table.measure, "visual": "table",
                            **({"filters": list(bp.filters)} if bp.filters else {}), "detail": bp.table.detail()})
        noun = _dashboard_noun(bp.dashboard, raw.get("dashboards", {}))
        reports[bp.id] = {"title": f"{noun} — {bp.title}", "tab": bp.tab or bp.title, "dashboard": bp.dashboard,
                          "section": bp.section, "widgets": widgets,
                          **({"callouts": [bp.callout.model_dump(exclude_none=True)]} if bp.callout else {}),
                          **({"footnote": bp.footnote} if bp.footnote else {})}
    return reports, unavailable


def _dashboard_noun(key: str, dashboards: dict[str, Any]) -> str:
    """The word a starter report's title opens with: the dashboard's ``noun``, PMO's Projects / Requests, else the
    dashboard's title (a tenant pack's own dashboards, US-234)."""
    dash = dashboards.get(key) or {}
    return str(dash.get("noun") or {"projects": "Projects", "requests": "Requests"}.get(key) or dash.get("title")
               or key.replace("_", " ").capitalize())


def check_report_texts(key: str, rep: dict[str, Any]) -> None:
    """A starter report's text blocks and detail tables (US-158): callouts have text and an info/warning level, the
    footnote is text, a detail table's spec parses (it lists fields of its measure's entity, checked on binding)."""
    for c in rep.get("callouts", []):
        if not str(c.get("text", "")).strip() or c.get("level", "info") not in ("info", "warning") or \
                set(c) - {"title", "text", "level"}:
            raise ValueError(f"report {key}: a callout has title, text and level info or warning")
    if "footnote" in rep and not str(rep["footnote"]).strip():
        raise ValueError(f"report {key}: the footnote is empty")
    for w in rep["widgets"]:
        Visual.model_validate(widget_visual(w))


def widget_visual(w: dict[str, Any]) -> dict[str, Any]:
    """A starter widget's visual spec: type, a figure's tone, a meter's note, a table's detail (US-155, US-158)."""
    return {"type": w["visual"], **{k: w[k] for k in ("tone", "note", "detail") if w.get(k)}}


def widget_gaps(w: dict[str, Any], measures: set[str], dims: set[str],
                field_gaps: Any = None) -> tuple[list[str], list[str]]:
    """What one starter widget needs that the model lacks: (gaps that leave the widget out, columns its detail table
    shows without). A detail table drops the columns the model lacks (``field_gaps(measure, columns)``) and is left
    out only when none is left (US-155)."""
    gaps = [f"measure {mk}" for mk in [w["measure"], *w.get("extra_measures", [])] if mk not in measures]
    need = [*w.get("breakdowns", []), *(f["ref"] for f in w.get("filters", []))]
    if w.get("time"):
        need.append(w["time"]["ref"])
    gaps += [f"dimension {d}" for d in need if d not in dims]
    soft: list[str] = []
    if w.get("detail") and field_gaps is not None:
        missing = [f"field {g}" for g in field_gaps(w["measure"], set(w["detail"]["columns"]))]
        if len(missing) >= len(w["detail"]["columns"]):
            gaps += missing
        else:
            soft = missing
    return gaps, soft


def usable_widgets(report: dict[str, Any], definition: dict[str, Any]) -> tuple[list[int], list[str]]:
    """The widgets of a starter report this model definition can run (indexes), and the titles left out."""
    keep, left = [], []
    for i, w in enumerate(fit_widgets(report, definition)):
        if w is None:
            left.append(report["widgets"][i]["title"])
        else:
            keep.append(i)
    return keep, left


def fit_widgets(report: dict[str, Any], definition: dict[str, Any]) -> list[dict[str, Any] | None]:
    """Each widget of a starter report as this model definition can run it: a detail table without the columns the
    model lacks (its sort falls back to none when the sort field is one of them), None for a widget left out."""
    measures = {m["key"]: m["entity"] for m in definition.get("measures", [])}
    dims = {d["key"] for d in definition.get("dimensions", [])}
    fields = {e["key"]: {f["key"] for f in e["fields"]} for e in definition.get("entities", [])}

    def field_gaps(measure: str, cols: set[str]) -> list[str]:
        return sorted(cols - fields.get(measures.get(measure, ""), set()))

    out: list[dict[str, Any] | None] = []
    for w in report["widgets"]:
        hard, soft = widget_gaps(w, set(measures), dims, field_gaps)
        if hard:
            out.append(None)
        elif soft:
            have = fields[measures[w["measure"]]]
            d = w["detail"]
            detail = {**d, "columns": [c for c in d["columns"] if c in have]}
            if d.get("sort") and d["sort"]["field"] not in have:
                detail.pop("sort")
            out.append({**w, "detail": detail})
        else:
            out.append(w)
    return out


def left_out_note(left: list[str]) -> str:
    return f"Not shown on this source (the data is missing): {', '.join(left)}." if left else ""


@dataclass
class EntityMatch:
    entity: str
    object: str | None
    mapping: dict[str, str] = field(default_factory=dict)  # pack field key -> physical column
    missing: list[str] = field(default_factory=list)
    missing_required: list[str] = field(default_factory=list)
    extra: list[str] = field(default_factory=list)
    # How each field was found (US-154): exact, dataverse_label, model_label, similar_name, mapped, not_in_source;
    # column_taken (US-199) when its same-name column is mapped by the Admin to another pack field.
    how: dict[str, str] = field(default_factory=dict)
    taken: dict[str, str] = field(default_factory=dict)  # pack field key -> its same-name column the Admin took
    object_how: str = "missing"
    confidence: float = 0.0


@dataclass
class PackMatch:
    confidence: float
    matched: bool
    entities: dict[str, EntityMatch]
    values: dict[str, dict[str, str]] = field(default_factory=dict)  # entity.field -> {source value: pack value}

    def out(self) -> dict[str, Any]:
        return {
            "confidence": round(self.confidence, 3), "matched": self.matched,
            "missing_objects": [k for k, e in self.entities.items() if e.object is None],
            "entities": {k: {"object": e.object, "matched_columns": sorted(e.mapping.values()),
                             "missing_fields": e.missing, "missing_required": e.missing_required,
                             "extra_columns": e.extra, "confidence": round(e.confidence, 3),
                             "found_by": e.how} for k, e in self.entities.items()},
            "field_mapping": {f"{k}.{fk}": col for k, e in self.entities.items() for fk, col in e.mapping.items()},
        }


def _model_labels(model: dict[str, Any] | None) -> tuple[dict[str, str], dict[str, dict[str, str]]]:
    """From the Admin's model: entity words -> source object, and per source object, field words -> column."""
    objects: dict[str, str] = {}
    columns: dict[str, dict[str, str]] = {}
    for e in (model or {}).get("entities", []):
        obj = str(e.get("source_object") or "")
        if not obj:
            continue
        for w in (e.get("label"), e.get("key")):
            if w:
                objects.setdefault(singular(norm(str(w))), obj)
        cols = columns.setdefault(obj.lower(), {})
        for f in e.get("fields", []):
            for w in (f.get("label"), f.get("key"), *(f.get("synonyms") or [])):
                if w and f.get("source_column"):
                    cols.setdefault(norm(str(w)), str(f["source_column"]))
    return objects, columns


def _guess(col: str | None, cols: dict[str, dict[str, Any]], used: set[str], f: PackField,
           claimed: set[str]) -> str | None:
    """A name-based guess for a pack field, if the column is free and fits the field's type."""
    if not col or col.lower() in used or col.lower() not in cols:
        return None
    col = label_column(col, cols, f, claimed) or cols[col.lower()]["name"]
    return col if fits(f.data_type, f.semantic_type, cols[col.lower()].get("type")) else None


def match(manifest: PackManifest, catalog: list[dict[str, Any]], *, model: dict[str, Any] | None = None,
          mapping: PackMapping | None = None) -> PackMatch:
    """Score a catalogue (``[{qualified_name, columns}]``) against the pack signature.

    Names are tried in order: the Admin's mapping, the pack's candidate names, then (US-154) the Dataverse label
    column of a choice or lookup, the Admin's model labels, and names that differ only by a publisher prefix,
    plural or separators. Name-based guesses must fit the pack field's type.
    """
    mp = mapping or PackMapping()
    by_name: dict[str, dict[str, Any]] = {}
    by_variant: dict[str, dict[str, Any]] = {}
    by_qualified = {item["qualified_name"].lower(): item for item in catalog}
    prefixes = publisher_prefixes(catalog)
    for item in catalog:
        by_name.setdefault(item["qualified_name"].rpartition(".")[2].lower(), item)
        for v in name_variants(item["qualified_name"], prefixes):
            by_variant.setdefault(singular(v), item)
    model_objects, model_columns = _model_labels(model)
    entities: dict[str, EntityMatch] = {}
    total = found = 0
    taken: set[str] = set()
    for ent in manifest.entities:
        obj: dict[str, Any] | None = None
        how = "missing"
        if ent.key in mp.entities:
            chosen = mp.entities[ent.key]
            obj, how = (by_qualified.get(chosen.lower()), "mapped") if chosen else (None, "not_in_source")
        else:
            obj = next((by_name[c.lower()] for c in ent.object_candidates if c.lower() in by_name), None)
            how = "exact" if obj else how
            terms = entity_terms(ent.key, ent.label, ent.object_candidates, ent.synonyms, prefixes)
            if obj is None:
                own_obj = next((model_objects[t] for t in sorted(terms) if t in model_objects), None)
                obj = by_qualified.get(own_obj.lower()) if own_obj else None
                how = "model_label" if obj else how
            if obj is None:
                obj = next((by_variant[t] for t in sorted(terms)
                            if t in by_variant and by_variant[t]["qualified_name"] not in taken), None)
                how = "similar_name" if obj else how
        if obj is None:
            how = "not_in_source" if how == "not_in_source" else "missing"
        else:
            taken.add(obj["qualified_name"])
        em = EntityMatch(ent.key, obj["qualified_name"] if obj else None, object_how=how)
        cols = {c["name"].lower(): c for c in obj["columns"]} if obj else {}
        variants = {v: c["name"] for c in (obj["columns"] if obj else []) for v in name_variants(c["name"], prefixes)}
        own = model_columns.get(em.object.lower(), {}) if em.object else {}
        # Columns the Admin mapped are not guessed for another field (US-199): one column, one pack field.
        used: set[str] = {c.lower() for r, c in mp.fields.items()
                          if c and r.partition(".")[0] == ent.key and c.lower() in cols}
        ent_found = 0
        own_columns = {f.key: {c.lower() for c in f.columns} for f in ent.fields}
        for f in ent.fields:
            total += 1
            # Columns other fields of this table name as their own: never taken as this field's label column.
            claimed = {c for k, cs in own_columns.items() if k != f.key for c in cs}
            ref = f"{ent.key}.{f.key}"
            hit: str | None = None
            fhow = "missing"
            if ref in mp.fields:
                chosen_col = mp.fields[ref]
                if chosen_col and chosen_col.lower() in cols:
                    hit, fhow = cols[chosen_col.lower()]["name"], "mapped"
                    # A Dataverse yes/no or choice picked for a text field reads its label column (BUG-021).
                    hit = label_column(hit, cols, f, claimed) or hit
                elif chosen_col is None:
                    fhow = "not_in_source"
            elif cols:
                hit = next((cols[c.lower()]["name"] for c in f.columns if c.lower() in cols), None)
                fhow = "exact" if hit else fhow
                if hit and hit.lower() in used:
                    em.taken[f.key], hit, fhow = hit, None, "column_taken"
                if hit:
                    swapped = label_column(hit, cols, f, claimed)
                    if swapped:
                        hit, fhow = swapped, "dataverse_label"
                terms = field_terms(ent.key, ent.label, f, primary_key=f.key in ent.primary_key, prefixes=prefixes)
                if hit is None:
                    hit = next((c for t in sorted(terms) if (c := _guess(own.get(t), cols, used, f, claimed))), None)
                    fhow = "model_label" if hit else fhow
                if hit is None:
                    hit = next((c for t in sorted(terms)
                                if (c := _guess(variants.get(t), cols, used, f, claimed))), None)
                    fhow = "similar_name" if hit else fhow
                if hit is None and f.data_type == "text" and f.semantic_type != "identifier":
                    # A Dataverse label column exported without its code column, e.g. tpg_schedulekpiname.
                    hit = next((c for t in sorted(terms)
                                if (c := _guess(variants.get(t + "name"), cols, used, f, claimed))), None)
                    fhow = "dataverse_label" if hit else fhow
            if hit is None:
                em.missing.append(f.key)
                if f.required:
                    em.missing_required.append(f.key)
                if fhow in ("not_in_source", "column_taken"):
                    em.how[f.key] = fhow
            else:
                em.mapping[f.key] = hit
                em.how[f.key] = fhow
                used.add(hit.lower())
                found += 1
                ent_found += 1
        em.extra = sorted(c["name"] for k, c in cols.items() if k not in used)
        em.confidence = ent_found / len(ent.fields) if ent.fields else 0.0
        entities[ent.key] = em
    confidence = found / total if total else 0.0
    primary = entities.get(manifest.signature.get("primary_entity", manifest.entities[0].key))
    matched = bool(primary and primary.object and not primary.missing_required
                   and confidence >= float(manifest.signature.get("min_confidence", 0.6)))
    values = {ref: vm for ref, vm in mp.values.items()
              if (e := entities.get(ref.partition(".")[0])) and ref.partition(".")[2] in e.mapping and vm}
    return PackMatch(confidence, matched, entities, values)


@dataclass
class Binding:
    definition: dict[str, Any]
    items: list[dict[str, Any]]  # starter_pack_items rows: kind, key, title, availability, gaps


def bind(manifest: PackManifest, result: PackMatch, *, reporting_date: str | None = None) -> Binding:
    entities: list[dict[str, Any]] = []
    available: dict[str, set[str]] = {}
    labels: dict[str, str] = {}
    for ent in manifest.entities:
        em = result.entities[ent.key]
        for f in ent.fields:
            labels[f"{ent.key}.{f.key}"] = f"{ent.label}: {f.label}"
        if em.object is None or em.missing_required:
            continue
        fields = []
        for f in ent.fields:
            if f.key not in em.mapping:
                continue
            fields.append({k: v for k, v in {
                "key": f.key, "source_column": em.mapping[f.key], "label": f.label, "data_type": f.data_type,
                "semantic_type": f.semantic_type, "sensitivity": f.sensitivity, "format": f.format,
                "description": f.description, "blank_handling": f.blank_handling,
            }.items() if v is not None})
        available[ent.key] = set(em.mapping)
        entities.append({"key": ent.key, "label": ent.label, "source_object": em.object, "grain": ent.grain,
                         "primary_key": ent.primary_key, "fields": fields,
                         "display_fields": [k for k in ent.display_fields if k in em.mapping]})

    links: list[dict[str, Any]] = []

    def missing_for(entity: str, keys: set[str]) -> list[str]:
        """Gaps for field references read from ``entity``; ``other.field`` names a linked entity's field, which
        also needs the link between the two entities."""
        by_entity: dict[str, set[str]] = {entity: set()}  # the measure's own entity is always needed
        for k in keys:
            ek, fk = split_ref(entity, k)
            by_entity.setdefault(ek, set()).add(fk)
        out: list[str] = []
        for ek, fks in sorted(by_entity.items()):
            have = available.get(ek)
            out += [f"{ek} (whole table)"] if have is None else [labels.get(f"{ek}.{k}", k) for k in fks - have]
            if ek != entity and have is not None and not any(
                    {ln["from_entity"], ln["to_entity"]} == {entity, ek} for ln in links):
                out.append(f"link from {entity} to {ek}")
        return sorted(set(out))

    links += [{**ln, "origin": "pack", "accepted": True} for ln in manifest.links
              if not missing_for(ln["from_entity"], set(ln["from_fields"]))
              and not missing_for(ln["to_entity"], set(ln["to_fields"]))]
    items: list[dict[str, Any]] = []
    measures, ok_measures = [], set()
    vmaps: dict[str, dict[str, dict[str, str]]] = {}
    for ref, vm in result.values.items():
        ent_key, _, fkey = ref.partition(".")
        vmaps.setdefault(ent_key, {})[fkey] = vm
    for raw in manifest.measures:
        if raw["entity"] in vmaps:
            raw = {**raw, "expression": rewrite_values(raw["expression"], vmaps[raw["entity"]])}
        m = Measure.model_validate(raw)
        gaps = missing_for(m.entity, measure_fields(m.expression))
        items.append({"kind": "measure", "key": m.key, "title": m.label,
                      "availability": "unavailable" if gaps else "available", "gaps": gaps})
        if not gaps:
            measures.append(raw)
            ok_measures.add(m.key)
    dimensions, ok_dims = [], set()
    for d in manifest.dimensions:
        gaps = missing_for(d["entity"], Dimension.model_validate(d).fields())
        items.append({"kind": "dimension", "key": d["key"], "title": d["label"],
                      "availability": "unavailable" if gaps else "available", "gaps": gaps})
        if not gaps:
            dvm = vmaps.get(d["entity"], {}).get(d["field"])
            dimensions.append({**d, "value_labels": dimension_labels(d, dvm)} if dvm else d)
            ok_dims.add(d["key"])
    measure_entity = {m["key"]: m["entity"] for m in manifest.measures}

    def detail_gaps(measure: str, cols: set[str]) -> list[str]:
        # A detail table's columns are fields of its measure's entity (US-158).
        return missing_for(measure_entity[measure], cols) if measure in measure_entity else []

    for key, rep in manifest.reports.items():
        # US-155: the widgets the model lacks are left out; the report is unavailable only when none is left. The
        # gaps of the left-out widgets are named either way.
        report_gaps: list[str] = []
        shown = 0
        for w in rep["widgets"]:
            hard, soft = widget_gaps(w, ok_measures, ok_dims, detail_gaps)
            report_gaps += hard + soft
            shown += not hard
        items.append({"kind": "report", "key": key, "title": rep["title"],
                      "availability": "available" if shown else "unavailable",
                      "gaps": sorted(set(report_gaps))})
    for key, rep in manifest.unavailable_reports.items():
        items.append({"kind": "report", "key": key, "title": rep["title"], "availability": "unavailable",
                      "gaps": rep["gaps"]})
    settings = {"reporting_date": reporting_date} if reporting_date else {}
    definition = {"entities": entities, "links": links, "measures": measures, "dimensions": dimensions,
                  "settings": settings}
    return Binding(definition, items)


def widget_spec(manifest: PackManifest, report_key: str, index: int, *, model_version_id: str,
                connection_id: str) -> dict[str, Any]:
    """The Query Spec of one starter report widget (used by reconciliation tests and P2 report install)."""
    return spec_of(manifest.reports[report_key]["widgets"][index], model_version_id=model_version_id,
                   connection_id=connection_id)


def spec_of(w: dict[str, Any], *, model_version_id: str, connection_id: str) -> dict[str, Any]:
    """The Query Spec of a starter widget (as ``fit_widgets`` fits it to the model, US-155)."""
    spec: dict[str, Any] = {
        "model_version_id": model_version_id, "connection_id": connection_id,
        "measure": {"ref": f"measure:{w['measure']}"},
        "extra_measures": [{"ref": f"measure:{m}"} for m in w.get("extra_measures", [])],
        "breakdowns": [{"ref": f"dimension:{d}"} for d in w.get("breakdowns", [])],
        "filters": [{**f, "ref": f"dimension:{f['ref']}"} for f in w.get("filters", [])],
        "visual": widget_visual(w),
    }
    if w.get("time"):
        spec["time"] = {**w["time"], "ref": f"dimension:{w['time']['ref']}"}
    return spec
