"""Checks on the AI pack author's proposals (US-237), before one is shown and again when it is accepted.

Every proposal, from the model or the rules, goes through the same checks; one with an error is never shown as a
proposal (it is reported as left out, with the reason) and is refused on accept.

* **schema** — the item's own model (``PackEntity``, ``ModelLink``, ``Measure``, ``Dimension``, the knowledge kind's
  item model);
* **references** — entities, fields, measures and dimensions it names exist in the draft (a measure may read a field
  of an entity it links to); a key the draft already has is an error;
* **values** — every literal text value a measure filters on, a dimension labels or a value synonym is about is a
  value of the column in Tela's own profile (US-199). A spelling that differs only by case or spaces is replaced by
  the stored value; any other value is an error ("not a value of Status: it has Active, Inactive"), so a filter value
  is never invented. A column whose values are not read (personal or confidential) can't be filtered on by value.

``author.py`` adds the source checks: compile, the trial count and US-199's sanity warnings.
"""

from __future__ import annotations

import copy
import re
from typing import Any

from pydantic import BaseModel, ValidationError

from tela_api.knowledge.guidance import DashboardRule, Forbidden, GlossaryTerm, Recipe
from tela_api.knowledge.patterns import SUGGEST_OPS, ChartPattern
from tela_api.knowledge.values import PackValue
from tela_api.model.guards import TOP_VALUES
from tela_api.packs.manifest import PackEntity
from tela_api.packs.studio.author_rules import table_name
from tela_api.packs.studio.knowledge import Names, item_refs
from tela_api.packs.studio.validate import Report, path_of
from tela_query_spec.expr import measure_refs, split_ref
from tela_query_spec.model import Dimension, Measure, ModelLink

Profiles = dict[str, list[dict[str, Any]] | None]
KNOWLEDGE_ITEMS: dict[str, type[BaseModel]] = {"glossary": GlossaryTerm, "value_synonyms": PackValue,
                                               "dashboard_rules": DashboardRule, "chart_patterns": ChartPattern,
                                               "analysis_recipes": Recipe}
MANIFEST_KINDS = ("entity", "link", "measure", "dimension", "synonyms")
KINDS = (*MANIFEST_KINDS, *KNOWLEDGE_ITEMS)
SHOWN = 8


def _norm(value: str) -> str:
    return re.sub(r"[\s_-]+", " ", value).strip().lower()


def _schema(report: Report, model: type[BaseModel], item: Any) -> Any:
    try:
        return model.model_validate(item)
    except ValidationError as exc:
        for e in exc.errors()[:6]:
            report.error(path_of("", tuple(p for p in e["loc"] if not (isinstance(p, str) and p[:1].isupper()))),
                         str(e["msg"]))
    return None


def _has(profile: list[dict[str, Any]]) -> str:
    vals = [str(v["value"]) for v in profile if v.get("value") not in (None, "")]
    return ", ".join(vals[:SHOWN]) + (f" and {len(vals) - SHOWN} more" if len(vals) > SHOWN else "") or "no values"


def field_of(head: dict[str, Any], entity: str, field: str) -> dict[str, Any] | None:
    ent = next((e for e in head.get("entities") or [] if e.get("key") == entity), None)
    return next((f for f in (ent or {}).get("fields") or [] if f.get("key") == field), None)


def snap_value(report: Report, path: str, ref: str, label: str, value: str, profiles: Profiles) -> str:
    """The stored spelling of ``value`` in the column's profile, or an error when it is not one of its values."""
    profile = profiles.get(ref)
    if profile is None:
        report.error(path, f"Tela has not read the values of {label}, so a filter on {value!r} can't be checked.")
        return value
    if len(profile) >= TOP_VALUES:
        report.warn(path, f"{label} holds free text; check that {value!r} is one of its values.")
        return value
    stored = [str(v["value"]) for v in profile if v.get("value") is not None]
    if value in stored:
        return value
    same = next((v for v in stored if _norm(v) == _norm(value)), None)
    if same is not None:
        report.warn(path, f"{value!r} is written {same!r} in the data.")
        return same
    report.error(path, f"{value!r} is not a value of {label}: it has {_has(profile)}.")
    return value


def snap_conditions(report: Report, head: dict[str, Any], entity: str, node: Any, profiles: Profiles,
                    path: str) -> Any:
    """``node`` (a measure expression or part of one) with every literal text filter value snapped to the profile."""
    if isinstance(node, list):
        return [snap_conditions(report, head, entity, v, profiles, f"{path}[{i}]") for i, v in enumerate(node)]
    if not isinstance(node, dict):
        return node
    out = {k: snap_conditions(report, head, entity, v, profiles, f"{path}.{k}") for k, v in node.items()}
    fld, op = node.get("field"), node.get("op")
    if isinstance(fld, str) and node.get("other_field") is None and op in ("=", "!=", "in", "not_in"):
        ek, fk = split_ref(entity, fld)
        f = field_of(head, ek, fk)
        if f is None or f.get("data_type") != "text":
            return out
        label, ref = str(f.get("label") or fk), f"{ek}.{fk}"
        if op in ("=", "!=") and isinstance(node.get("value"), str):
            out["value"] = snap_value(report, f"{path}.value", ref, label, node["value"], profiles)
        elif op in ("in", "not_in"):
            out["values"] = [snap_value(report, f"{path}.values[{i}]", ref, label, v, profiles)
                             if isinstance(v, str) else v for i, v in enumerate(node.get("values") or [])]
    return out


# --------------------------------------------------------------------------- one item of each kind


def _keys(head: dict[str, Any], section: str) -> set[str]:
    return {str(x.get("key")) for x in head.get(section) or [] if isinstance(x, dict)}


def _linked(head: dict[str, Any], a: str, b: str) -> bool:
    return any({ln.get("from_entity"), ln.get("to_entity")} == {a, b} for ln in head.get("links") or [])


def check_entity(report: Report, item: dict[str, Any], head: dict[str, Any],
                 catalog: list[dict[str, Any]]) -> dict[str, Any]:
    ent = _schema(report, PackEntity, item)
    if ent is None:
        return item
    if ent.key in _keys(head, "entities"):
        report.error("key", f"The draft already has an entity {ent.key!r}.")
    obj = next((o for o in catalog if any(c.lower() in (o["qualified_name"].lower(),
                                                       table_name(o["qualified_name"]).lower())
                                          for c in ent.object_candidates)), None)
    if obj is None:
        report.error("object_candidates", "No table on this source has that name.")
        return item
    cols = {c["name"].lower() for c in obj["columns"]}
    keys = {f.key for f in ent.fields}
    for i, f in enumerate(ent.fields):
        if not any(c.lower() in cols for c in f.columns):
            report.error(f"fields[{i}].columns", f"{obj['qualified_name']} has no column {f.columns[0]!r}.")
    for i, pk in enumerate(ent.primary_key):
        if pk not in keys:
            report.error(f"primary_key[{i}]", f"There is no field {pk!r}.")
    if not ent.primary_key:
        report.error("primary_key", "Name the field that identifies one row.")
    return item


def check_link(report: Report, item: dict[str, Any], head: dict[str, Any]) -> dict[str, Any]:
    link = _schema(report, ModelLink, item)
    if link is None:
        return item
    for side, ent, fields in (("from", link.from_entity, link.from_fields), ("to", link.to_entity, link.to_fields)):
        for i, f in enumerate(fields):
            if field_of(head, ent, f) is None:
                report.error(f"{side}_fields[{i}]", f"{ent} has no field {f!r}.")
    if any((ln.get("from_entity"), ln.get("to_entity"), list(ln.get("from_fields") or []))
           == (link.from_entity, link.to_entity, link.from_fields) for ln in head.get("links") or []):
        report.error("from_entity", "The draft already has this link.")
    return item


def check_measure(report: Report, item: dict[str, Any], head: dict[str, Any], profiles: Profiles) -> dict[str, Any]:
    m = _schema(report, Measure, item)
    if m is None:
        return item
    if m.key in _keys(head, "measures"):
        report.error("key", f"The draft already has a measure {m.key!r}.")
    if not any(e.get("key") == m.entity for e in head.get("entities") or []):
        report.error("entity", f"There is no entity {m.entity!r}.")
        return item
    for ref in sorted(measure_refs(m.entity, m.expression)):
        ek, _, fk = ref.partition(".")
        if field_of(head, ek, fk) is None:
            report.error("expression", f"The rule reads {ref}, which the draft doesn't have.")
        elif ek != m.entity and not _linked(head, m.entity, ek):
            report.error("expression", f"The rule reads {ref}, but {m.entity} has no link to {ek}.")
    out = copy.deepcopy(item)
    out["expression"] = snap_conditions(report, head, m.entity, item["expression"], profiles, "expression")
    return out


def check_dimension(report: Report, item: dict[str, Any], head: dict[str, Any],
                    profiles: Profiles) -> dict[str, Any]:
    d = _schema(report, Dimension, item)
    if d is None:
        return item
    if d.key in _keys(head, "dimensions"):
        report.error("key", f"The draft already has a dimension {d.key!r}.")
    f = field_of(head, d.entity, d.field)
    if f is None:
        report.error("field", f"{d.entity} has no field {d.field!r}.")
        return item
    for i, name in enumerate(sorted(d.fields() - {d.field})):
        if field_of(head, d.entity, name) is None:
            report.error(f"of[{i}]", f"{d.entity} has no field {name!r}.")
    if d.is_temporal and f.get("data_type") not in ("date", "datetime"):
        report.error("is_temporal", f"{f.get('label')} is not a date.")
    out = copy.deepcopy(item)
    if d.value_labels and f.get("data_type") == "text":
        out["value_labels"] = {snap_value(report, f"value_labels.{raw}", f"{d.entity}.{d.field}",
                                          str(f.get("label")), raw, profiles): shown
                               for raw, shown in d.value_labels.items()}
    if not d.computed and f.get("data_type") == "text":
        profile = profiles.get(f"{d.entity}.{d.field}")
        if profile is None:
            report.warn("field", f"Tela has not read the values of {f.get('label')}.")
        elif len(profile) >= TOP_VALUES:
            report.warn("field", f"{f.get('label')} holds free text: a breakdown by it has many small groups.")
    return out


def _target(head: dict[str, Any], ref: str) -> dict[str, Any] | None:
    what, _, key = ref.partition(":")
    section = {"measure": "measures", "dimension": "dimensions", "entity": "entities"}.get(what)
    return next((x for x in head.get(section or "") or [] if x.get("key") == key), None) if section else None


def check_synonyms(report: Report, item: dict[str, Any], head: dict[str, Any]) -> dict[str, Any]:
    ref, words = item.get("target"), item.get("synonyms")
    if not isinstance(ref, str) or _target(head, ref) is None:
        report.error("target", f"There is no {ref!r} in the draft (write measure:key, dimension:key or entity:key).")
    if not isinstance(words, list) or not all(isinstance(w, str) and 0 < len(w.strip()) <= 80 for w in words) \
            or not words:
        report.error("synonyms", "Give one or more words of 80 characters or fewer.")
    return item


def check_knowledge(report: Report, kind: str, item: dict[str, Any], head: dict[str, Any], docs: dict[str, Any],
                    profiles: Profiles) -> dict[str, Any]:
    """One knowledge item (DEC-26 point 6): its kind's schema, and every measure, dimension and field it names is in
    the draft; a value synonym's value is a value of its dimension's column."""
    if kind == "dashboard_rules" and "words" in item:  # a wording term (rules document's wording.forbidden)
        _schema(report, Forbidden, item)
        return item
    parsed = _schema(report, KNOWLEDGE_ITEMS[kind], item)
    if parsed is None:
        return item
    if kind == "chart_patterns" and parsed.suggest:
        for j, op in enumerate(parsed.suggest.ops):
            if op.get("op") not in SUGGEST_OPS:
                report.error(f"suggest.ops[{j}].op", f"Use one of: {', '.join(sorted(SUGGEST_OPS))}.")
    names = Names.of(head)
    for what, name, path in item_refs(kind, item):
        if what == "measure" and name not in names.measures:
            report.error(path, f"There is no measure {name!r} in this pack.")
        elif what == "dimension" and name not in names.dimensions:
            report.error(path, f"There is no dimension {name!r} in this pack.")
        elif what == "field":
            ent, _, fk = name.partition(".")
            if fk not in names.fields.get(ent, set()):
                report.error(path, f"There is no field {name!r} in this pack (write entity.field).")
    ident = _identity(kind, item)
    if ident is not None and ident in {_identity(kind, x) for x in _items(kind, docs)}:
        report.error(_IDENT_PATH.get(kind, "id"), "The pack's document already has this item.")
    out = copy.deepcopy(item)
    if kind == "value_synonyms" and not report.errors:
        dims = [names.dimensions[d] for d in parsed.dimensions]
        texts = [d for d in dims if not d.get("of") and (f := field_of(head, d["entity"], d["field"]))
                 and f.get("data_type") == "text"]
        if texts:
            d = texts[0]
            f = field_of(head, d["entity"], d["field"]) or {}
            out["value"] = snap_value(report, "value", f"{d['entity']}.{d['field']}", str(f.get("label")),
                                      parsed.value, profiles)
    return out


_LISTS = {"glossary": "terms", "value_synonyms": "values", "dashboard_rules": "rules", "chart_patterns": "patterns",
          "analysis_recipes": "recipes"}
_IDENT_PATH = {"glossary": "term", "value_synonyms": "value"}


def _items(kind: str, docs: dict[str, Any]) -> list[dict[str, Any]]:
    body = docs.get(kind) or {}
    items = list(body.get(_LISTS[kind]) or [])
    if kind == "dashboard_rules":
        items += list((body.get("wording") or {}).get("forbidden") or [])
    return [x for x in items if isinstance(x, dict)]


def _identity(kind: str, item: dict[str, Any]) -> str | None:
    if kind == "glossary":
        return f"term:{str(item.get('term') or '').lower()}"
    if kind == "value_synonyms":
        return f"value:{sorted(item.get('dimensions') or [])}:{str(item.get('value') or '').lower()}"
    if kind == "dashboard_rules" and "words" in item:
        return f"words:{sorted(str(w).lower() for w in item.get('words') or [])}"
    return f"id:{item.get('id')}" if item.get("id") else None


def check(kind: str, item: Any, head: dict[str, Any], docs: dict[str, Any], catalog: list[dict[str, Any]],
          profiles: Profiles) -> tuple[dict[str, Any], Report]:
    """(the item as it would be written, its report)."""
    report = Report()
    if not isinstance(item, dict):
        report.error("", "Each proposal is a mapping.")
        return {}, report
    if kind == "entity":
        out = check_entity(report, item, head, catalog)
    elif kind == "link":
        out = check_link(report, item, head)
    elif kind == "measure":
        out = check_measure(report, item, head, profiles)
    elif kind == "dimension":
        out = check_dimension(report, item, head, profiles)
    elif kind == "synonyms":
        out = check_synonyms(report, item, head)
    elif kind in KNOWLEDGE_ITEMS:
        out = check_knowledge(report, kind, item, head, docs, profiles)
    else:
        report.error("", f"Unknown kind {kind!r}.")
        out = item
    return out, report


def with_items(head: dict[str, Any], kind: str, items: list[dict[str, Any]]) -> dict[str, Any]:
    """The draft with accepted items added (a later item of a batch may name an earlier one, e.g. a measure on a
    newly accepted entity). Synonyms are merged into their targets, without repeats."""
    out = copy.deepcopy(head)
    section = {"entity": "entities", "link": "links", "measure": "measures", "dimension": "dimensions"}.get(kind)
    if section:
        out[section] = [*(out.get(section) or []), *items]
        if kind == "entity" and items and not (out.get("signature") or {}).get("primary_entity"):
            # The widest table is the likeliest subject (projects, not their action items); the Admin can change it.
            widest = max(items, key=lambda e: len(e.get("fields") or []))
            out["signature"] = {**(out.get("signature") or {}), "primary_entity": widest["key"]}
    elif kind == "synonyms":
        for item in items:
            target = _target(out, item["target"])
            if target is not None:
                have = list(target.get("synonyms") or [])
                lowered = {w.lower() for w in have}
                target["synonyms"] = have + [w.strip() for w in item["synonyms"] if w.strip().lower() not in lowered]
    return out


def with_knowledge(body: dict[str, Any] | None, kind: str, items: list[dict[str, Any]]) -> dict[str, Any]:
    """A knowledge document with items added (a new document starts as a hand-written file does)."""
    doc: dict[str, Any] = copy.deepcopy(body) if body else {"version": "0.1.0", "status": "draft"}
    if kind == "dashboard_rules":
        words = [i for i in items if "words" in i]
        rules = [i for i in items if "words" not in i]
        doc["rules"] = [*(doc.get("rules") or []), *rules]
        if words:
            wording = dict(doc.get("wording") or {})
            wording["forbidden"] = [*(wording.get("forbidden") or []), *words]
            doc["wording"] = wording
    else:
        doc[_LISTS[kind]] = [*(doc.get(_LISTS[kind]) or []), *items]
    return doc
