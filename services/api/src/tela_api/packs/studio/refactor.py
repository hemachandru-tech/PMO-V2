"""Rename and remove pack items with every reference following (US-235; FEAT-075).

A pack names its entities, fields and measures by key in many places: the manifest (signature, primary keys, links,
measure rules, dimensions, starter reports' widgets and detail tables) and its knowledge documents (report blueprints
and their tones, chart patterns, analysis recipes' slots and specs, the glossary, dashboard rules and gold examples).
``_walk`` visits every one of those reference sites; ``rename`` rewrites them in one pass and ``references`` counts
them (the test that a rename keeps every reference compares the two).

``remove`` takes items out with what can't work without them (``closure``): a table takes its fields; a field takes
the links, measures and dimensions that read it; a measure or dimension takes the widgets, patterns, recipe options,
glossary terms and gold examples that name it. A blueprint whose headline figures all go becomes unavailable, naming
what it lost, so a removal never leaves a half-built report.

A field key that a record-level calculator reads by name (``pmo_health_v1``) can't be renamed: the calculator would
stop finding it.
"""

from __future__ import annotations

import copy
from collections import Counter
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from tela_query_spec.expr import CALCULATORS, measure_refs, split_ref
from tela_query_spec.model import Dimension, Measure

KEY = r"^[a-z][a-z0-9_]{1,40}$"
KINDS = ("entity", "field", "measure", "dimension", "report")
FIELD_KEYS = ("field", "other_field", "days_from", "days_to")

# visit(kind, entity the reference is read from, value) -> the value to write back. kind is entity, field or measure;
# a field value is ``field`` (on the context entity) or ``entity.field``.
Visit = Callable[[str, str, str], str]


class RenameTo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str | None = Field(default=None, pattern=KEY)
    label: str | None = Field(default=None, min_length=1, max_length=120)


class Renames(BaseModel):
    """What to rename: entities by key, fields by ``entity.field``, measures by key (all the keys before renaming)."""

    model_config = ConfigDict(extra="forbid")

    entities: dict[str, RenameTo] = Field(default_factory=dict)
    fields: dict[str, RenameTo] = Field(default_factory=dict)
    measures: dict[str, RenameTo] = Field(default_factory=dict)

    def empty(self) -> bool:
        return not (self.entities or self.fields or self.measures)


# --------------------------------------------------------------------------- the reference sites


def _expr(node: Any, ctx: str, visit: Visit) -> Any:
    """A measure rule or row value (raw JSON) with each field reference visited."""
    if isinstance(node, list):
        return [_expr(x, ctx, visit) for x in node]
    if not isinstance(node, dict):
        return node
    out = {}
    for k, v in node.items():
        if isinstance(v, str) and (k in FIELD_KEYS or (k in ("left", "right") and "calc" in node)):
            out[k] = visit("field", ctx, v)
        else:
            out[k] = _expr(v, ctx, visit)
    return out


def _tagged(node: Any, visit: Visit) -> Any:
    """Query Spec text (gold examples, recipe specs): ``measure:key`` and ``field:entity.field`` strings visited."""
    if isinstance(node, list):
        return [_tagged(x, visit) for x in node]
    if isinstance(node, dict):
        return {k: _tagged(v, visit) for k, v in node.items()}
    if isinstance(node, str):
        if node.startswith("measure:"):
            return "measure:" + visit("measure", "", node[8:])
        if node.startswith("field:") and "." in node:
            return "field:" + visit("field", "", node[6:])
    return node


def _detail(d: dict[str, Any], ctx: str, visit: Visit) -> None:
    """A detail table's columns and sort (fields of its measure's entity)."""
    d["columns"] = [visit("field", ctx, c) for c in d.get("columns") or []]
    if isinstance(d.get("sort"), dict) and d["sort"].get("field"):
        d["sort"]["field"] = visit("field", ctx, d["sort"]["field"])


def _widget(w: dict[str, Any], entity_of: dict[str, str], visit: Visit) -> None:
    ctx = entity_of.get(w.get("measure", ""), "")
    if "measure" in w:
        w["measure"] = visit("measure", "", w["measure"])
    if w.get("extra_measures"):
        w["extra_measures"] = [visit("measure", "", m) for m in w["extra_measures"]]
    if isinstance(w.get("detail"), dict):
        _detail(w["detail"], ctx, visit)


def _walk(head: dict[str, Any], docs: dict[str, Any], visit: Visit) -> None:
    """Visit every entity, field and measure reference in a manifest (as written) and its documents, in place."""
    entity_of = {m["key"]: m["entity"] for m in head.get("measures") or [] if "key" in m and "entity" in m}
    sig = head.get("signature") or {}
    if sig.get("primary_entity"):
        sig["primary_entity"] = visit("entity", "", sig["primary_entity"])
    for e in head.get("entities") or []:
        ctx = e["key"]
        e["key"] = visit("entity", "", ctx)
        e["primary_key"] = [visit("field", ctx, f) for f in e.get("primary_key") or []]
        if "display_fields" in e:
            e["display_fields"] = [visit("field", ctx, f) for f in e["display_fields"]]
        for f in e.get("fields") or []:
            f["key"] = visit("field", ctx, f["key"])
    for ln in head.get("links") or []:
        a, b = ln["from_entity"], ln["to_entity"]
        ln["from_entity"], ln["to_entity"] = visit("entity", "", a), visit("entity", "", b)
        ln["from_fields"] = [visit("field", a, f) for f in ln.get("from_fields") or []]
        ln["to_fields"] = [visit("field", b, f) for f in ln.get("to_fields") or []]
    for m in head.get("measures") or []:
        ctx = m["entity"]
        m["key"] = visit("measure", "", m["key"])
        m["entity"] = visit("entity", "", ctx)
        m["expression"] = _expr(m.get("expression"), ctx, visit)
        if m.get("currency_field"):
            m["currency_field"] = visit("field", ctx, m["currency_field"])
    for d in head.get("dimensions") or []:
        ctx = d["entity"]
        d["entity"] = visit("entity", "", ctx)
        d["field"] = visit("field", ctx, d["field"])
        if d.get("of") is not None:
            d["of"] = _expr(d["of"], ctx, visit)
    for rep in (head.get("reports") or {}).values():
        for w in rep.get("widgets") or []:
            _widget(w, entity_of, visit)

    bp = docs.get("report_blueprints") or {}
    if isinstance(bp.get("tones"), dict):
        bp["tones"] = {visit("measure", "", k): v for k, v in bp["tones"].items()}
    for b in bp.get("blueprints") or []:
        b["kpis"] = [visit("measure", "", k) if isinstance(k, str)
                     else {**k, "measure": visit("measure", "", k["measure"])} for k in b.get("kpis") or []]
        for w in b.get("widgets") or []:
            _widget(w, entity_of, visit)
        if isinstance(b.get("table"), dict):
            t = b["table"]
            ctx = entity_of.get(t.get("measure", ""), "")
            t["measure"] = visit("measure", "", t["measure"])
            _detail(t, ctx, visit)
    for p in (docs.get("chart_patterns") or {}).get("patterns") or []:
        when = p.get("when") or {}
        for k in ("measure", "no_measure"):
            if when.get(k):
                when[k] = [visit("measure", "", m) for m in when[k]]
        if isinstance(p.get("suggest"), dict):
            p["suggest"]["ops"] = _tagged(p["suggest"].get("ops") or [], visit)
    for r in (docs.get("analysis_recipes") or {}).get("recipes") or []:
        for slot in (r.get("slots") or {}).values():
            if slot.get("kind") == "measure":
                slot["options"] = [visit("measure", "", o) for o in slot.get("options") or []]
                if slot.get("default"):
                    slot["default"] = visit("measure", "", slot["default"])
        r["spec"] = _tagged(r.get("spec"), visit)
    for t in (docs.get("glossary") or {}).get("terms") or []:
        if t.get("fields"):
            t["fields"] = [visit("field", "", f) for f in t["fields"]]
        if t.get("reading"):
            t["reading"] = visit("measure", "", t["reading"])
        if t.get("instead_of"):
            t["instead_of"] = [visit("measure", "", m) for m in t["instead_of"]]
    for rule in (docs.get("dashboard_rules") or {}).get("rules") or []:
        if rule.get("measures"):
            rule["measures"] = [visit("measure", "", m) for m in rule["measures"]]
    gold = docs.get("gold_examples") or {}
    if isinstance(gold.get("examples"), list):
        gold["examples"] = _tagged(gold["examples"], visit)


def _qualified(ctx: str, ref: str) -> str:
    return ".".join(split_ref(ctx, ref)) if ctx else ref


def references(head: dict[str, Any], docs: dict[str, Any]) -> Counter[str]:
    """How many times each ``entity:``, ``field:entity.field`` and ``measure:`` reference occurs in the pack."""
    seen: Counter[str] = Counter()

    def visit(kind: str, ctx: str, value: str) -> str:
        seen[f"{kind}:{_qualified(ctx, value) if kind == 'field' else value}"] += 1
        return value

    _walk(copy.deepcopy(head), copy.deepcopy(docs), visit)
    return seen


# --------------------------------------------------------------------------- rename


def _calculator_fields(head: dict[str, Any]) -> dict[tuple[str, str], str]:
    """(entity, field) → the calculator that reads that field by its name on that entity."""
    out: dict[tuple[str, str], str] = {}

    def spot(node: Any, ent: str) -> None:
        if isinstance(node, dict):
            spec = CALCULATORS.get(str(node.get("calculator") or ""))
            if spec:
                for f in [*spec["required"], *spec["optional"]]:
                    out.setdefault((ent, f), str(node["calculator"]))
            for v in node.values():
                spot(v, ent)
        elif isinstance(node, list):
            for v in node:
                spot(v, ent)

    for m in head.get("measures") or []:
        spot(m.get("expression"), m["entity"])
    for d in head.get("dimensions") or []:
        spot(d.get("of"), d["entity"])
    return out


def rename(head: dict[str, Any], docs: dict[str, Any], renames: Renames) -> tuple[dict[str, Any], dict[str, Any]]:
    """Rename entity, field and measure keys and labels in place; every reference follows. Raises ``ValueError`` for
    an unknown item, a key another item of its kind already has, or a field a calculator reads by its name."""
    entities = {e["key"]: e for e in head.get("entities") or []}
    measures = {m["key"]: m for m in head.get("measures") or []}
    emap: dict[str, str] = {}
    fmap: dict[tuple[str, str], str] = {}
    mmap: dict[str, str] = {}
    for old, to in renames.entities.items():
        if old not in entities:
            raise ValueError(f"The pack has no entity {old!r}.")
        if to.key and to.key != old:
            emap[old] = to.key
    _clash("entity", set(entities), emap)
    calc = _calculator_fields(head)
    by_entity: dict[str, dict[str, str]] = {}
    for ref, to in renames.fields.items():
        ent, _, fk = ref.partition(".")
        if ent not in entities or fk not in {f["key"] for f in entities[ent]["fields"]}:
            raise ValueError(f"The pack has no field {ref!r}.")
        if to.key and to.key != fk:
            if (ent, fk) in calc:
                raise ValueError(f"The {calc[(ent, fk)]} calculator reads {ref} by its name; keep that key.")
            fmap[(ent, fk)] = to.key
            by_entity.setdefault(ent, {})[fk] = to.key
    for ent, moves in by_entity.items():
        _clash(f"field of {ent}", {f["key"] for f in entities[ent]["fields"]}, moves)
    for old, to in renames.measures.items():
        if old not in measures:
            raise ValueError(f"The pack has no measure {old!r}.")
        if to.key and to.key != old:
            mmap[old] = to.key
    _clash("measure", set(measures), mmap)

    def visit(kind: str, ctx: str, value: str) -> str:
        if kind == "entity":
            return emap.get(value, value)
        if kind == "measure":
            return mmap.get(value, value)
        ent, dot, fk = value.rpartition(".")
        new = fmap.get((ent if dot else ctx, fk), fk)
        return f"{emap.get(ent, ent)}.{new}" if dot else new

    for old, to in renames.entities.items():
        if to.label:
            entities[old]["label"] = to.label
    for ref, to in renames.fields.items():
        ent, _, fk = ref.partition(".")
        if to.label:
            next(f for f in entities[ent]["fields"] if f["key"] == fk)["label"] = to.label
    for old, to in renames.measures.items():
        if to.label:
            measures[old]["label"] = to.label
    _walk(head, docs, visit)
    return head, docs


def _clash(what: str, keys: set[str], moves: dict[str, str]) -> None:
    after = [moves.get(k, k) for k in keys]
    dupes = sorted({k for k in after if after.count(k) > 1})
    if dupes:
        raise ValueError(f"Another {what} already has the key {dupes[0]!r}.")


# --------------------------------------------------------------------------- remove


def _reads(item: dict[str, Any], model: type[Measure] | type[Dimension]) -> set[str]:
    """``entity.field`` names a measure or dimension (raw) reads."""
    ent = item["entity"]
    if model is Measure:
        m = Measure.model_validate(item)
        refs = measure_refs(ent, m.expression) | ({".".join(split_ref(ent, m.currency_field))}
                                                  if m.currency_field else set())
    else:
        refs = {".".join(split_ref(ent, f)) for f in Dimension.model_validate(item).fields()}
    return set(refs)


def closure(head: dict[str, Any], refs: set[str]) -> set[str]:
    """The items removing ``refs`` takes with it, ``refs`` included: a table's fields, and the measures and
    dimensions that read a removed table or field."""
    gone = set(refs)
    for e in head.get("entities") or []:
        if f"entity:{e['key']}" in gone:
            gone |= {f"field:{e['key']}.{f['key']}" for f in e["fields"]}
    gone_entities = {r[7:] for r in gone if r.startswith("entity:")}
    gone_fields = {r[6:] for r in gone if r.startswith("field:")}

    def lost(item: dict[str, Any], model: type[Measure] | type[Dimension]) -> bool:
        reads = _reads(item, model)
        return item["entity"] in gone_entities or bool(reads & gone_fields) or \
            any(r.partition(".")[0] in gone_entities for r in reads)

    gone |= {f"measure:{m['key']}" for m in head.get("measures") or [] if lost(m, Measure)}
    gone |= {f"dimension:{d['key']}" for d in head.get("dimensions") or [] if lost(d, Dimension)}
    return gone


def _check_removable(head: dict[str, Any], docs: dict[str, Any], refs: set[str]) -> None:
    entities = {e["key"]: e for e in head.get("entities") or []}
    blueprints = {b["id"] for b in (docs.get("report_blueprints") or {}).get("blueprints") or []}
    reports = blueprints | set(head.get("reports") or {}) | set(head.get("unavailable_reports") or {})
    have = {"measure": {m["key"] for m in head.get("measures") or []},
            "dimension": {d["key"] for d in head.get("dimensions") or []}, "report": reports}
    primary = (head.get("signature") or {}).get("primary_entity")
    for ref in sorted(refs):
        kind, _, key = ref.partition(":")
        if kind == "entity":
            if key not in entities:
                raise ValueError(f"The pack has no entity {key!r}.")
            if key == primary:
                raise ValueError(f"{entities[key]['label']} is the pack's primary table and can't be removed.")
        elif kind == "field":
            ent, _, fk = key.partition(".")
            if ent not in entities or fk not in {f["key"] for f in entities[ent]["fields"]}:
                raise ValueError(f"The pack has no field {key!r}.")
            if fk in entities[ent].get("primary_key", []):
                raise ValueError(f"{key} is the key of {entities[ent]['label']}; remove the table instead.")
        elif kind in have:
            if key not in have[kind]:
                raise ValueError(f"The pack has no {kind} {key!r}.")
        else:
            raise ValueError(f"Can't remove {ref!r}: name an {', '.join(KINDS)}.")


def _prune_widget(w: dict[str, Any], gm: set[str], gd: set[str], gf: set[str],
                  entity_of: dict[str, str]) -> dict[str, Any] | None:
    """A widget without what was removed, or None when it can't be shown without it."""
    if w.get("measure") in gm or set(w.get("breakdowns") or []) & gd or \
            any(f.get("ref") in gd for f in w.get("filters") or []) or ((w.get("time") or {}).get("ref") in gd):
        return None
    w = {**w}
    if w.get("extra_measures"):
        w["extra_measures"] = [m for m in w["extra_measures"] if m not in gm]
    if isinstance(w.get("detail"), dict):
        detail = _prune_detail(w["detail"], entity_of.get(w["measure"], ""), gf)
        if detail is None:
            return None
        w["detail"] = detail
    return w


def _prune_detail(d: dict[str, Any], ent: str, gf: set[str]) -> dict[str, Any] | None:
    cols = [c for c in d.get("columns") or [] if f"{ent}.{c}" not in gf]
    if not cols:
        return None
    d = {**d, "columns": cols}
    if isinstance(d.get("sort"), dict) and f"{ent}.{d['sort'].get('field')}" in gf:
        d.pop("sort")
    return d


def _prune_blueprint(b: dict[str, Any], gm: set[str], gd: set[str], gf: set[str],
                     entity_of: dict[str, str]) -> dict[str, Any]:
    if b.get("unavailable"):
        return b
    lost: list[str] = []
    named = [k if isinstance(k, str) else k["measure"] for k in b.get("kpis") or []]
    lost += [f"measure {m}" for m in named if m in gm]
    lost += [f"dimension {f['ref']}" for f in b.get("filters") or [] if f.get("ref") in gd]
    kpis = [k for k in b.get("kpis") or [] if not (
        (k if isinstance(k, str) else k["measure"]) in gm
        or (isinstance(k, dict) and any(f.get("ref") in gd for f in k.get("filters") or [])))]
    widgets = [x for w in b.get("widgets") or [] if (x := _prune_widget(w, gm, gd, gf, entity_of)) is not None]
    table = b.get("table")
    if isinstance(table, dict):
        detail = None if table["measure"] in gm else _prune_detail(table, entity_of.get(table["measure"], ""), gf)
        table = detail
    if kpis and not any(f.get("ref") in gd for f in b.get("filters") or []):
        out = {**b, "kpis": kpis, "widgets": widgets}
        if table is None:
            out.pop("table", None)
        else:
            out["table"] = table
        return out
    # Nothing to headline any more: the blueprint stays as a data gap that names what it lost.
    keep = ("id", "tab", "title", "dashboard", "section", "question", "population", "triggers")
    return {**{k: b[k] for k in keep if k in b}, "unavailable": sorted(set(lost)) or ["removed from this pack"]}


def _spec_refs(node: Any) -> set[str]:
    if isinstance(node, dict):
        return set().union(*(_spec_refs(v) for v in node.values())) if node else set()
    if isinstance(node, list):
        return set().union(*(_spec_refs(v) for v in node)) if node else set()
    if isinstance(node, str) and node.split(":", 1)[0] in ("measure", "dimension", "field") and ":" in node:
        return {node}
    return set()


def remove(head: dict[str, Any], docs: dict[str, Any], refs: set[str]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Remove items (``entity:key``, ``field:entity.field``, ``measure:key``, ``dimension:key``, ``report:id``) and
    what depends on them (``closure``); returns the new manifest and documents. Raises ``ValueError`` for an unknown
    item, a table's key field or the primary table."""
    if not refs:
        return head, docs
    _check_removable(head, docs, refs)
    gone = closure(head, refs)
    ge = {r[7:] for r in gone if r.startswith("entity:")}
    gf = {r[6:] for r in gone if r.startswith("field:")}
    gm = {r[8:] for r in gone if r.startswith("measure:")}
    gd = {r[10:] for r in gone if r.startswith("dimension:")}
    gr = {r[7:] for r in gone if r.startswith("report:")}
    entity_of = {m["key"]: m["entity"] for m in head.get("measures") or []}

    head = copy.deepcopy(head)
    docs = copy.deepcopy(docs)
    entities = []
    for e in head.get("entities") or []:
        if e["key"] in ge:
            continue
        e["fields"] = [f for f in e["fields"] if f"{e['key']}.{f['key']}" not in gf]
        if "display_fields" in e:
            e["display_fields"] = [f for f in e["display_fields"] if f"{e['key']}.{f}" not in gf]
        entities.append(e)
    head["entities"] = entities
    head["links"] = [ln for ln in head.get("links") or [] if ln["from_entity"] not in ge and ln["to_entity"] not in ge
                     and not {f"{ln['from_entity']}.{f}" for f in ln["from_fields"]} & gf
                     and not {f"{ln['to_entity']}.{f}" for f in ln["to_fields"]} & gf]
    head["measures"] = [m for m in head.get("measures") or [] if m["key"] not in gm]
    head["dimensions"] = [d for d in head.get("dimensions") or [] if d["key"] not in gd]
    if head.get("reports"):
        reports = {}
        for key, rep in head["reports"].items():
            if key in gr:
                continue
            widgets = [x for w in rep.get("widgets") or [] if (x := _prune_widget(w, gm, gd, gf, entity_of))]
            if widgets:
                reports[key] = {**rep, "widgets": widgets}
            else:
                head.setdefault("unavailable_reports", {})[key] = {"title": rep["title"], "gaps": ["removed items"]}
        head["reports"] = reports
    if head.get("unavailable_reports"):
        head["unavailable_reports"] = {k: v for k, v in head["unavailable_reports"].items() if k not in gr}
    for dash in (head.get("dashboards") or {}).values():
        if "reports" in dash:
            dash["reports"] = [r for r in dash["reports"] if r not in gr]
        if "filters" in dash:
            dash["filters"] = [f for f in dash["filters"] if f not in gd]

    bp = docs.get("report_blueprints")
    if bp:
        bp["blueprints"] = [_prune_blueprint(b, gm, gd, gf, entity_of) for b in bp.get("blueprints") or []
                            if b["id"] not in gr]
        if isinstance(bp.get("tones"), dict):
            bp["tones"] = {k: v for k, v in bp["tones"].items() if k not in gm}
    if docs.get("chart_patterns"):
        from tela_api.knowledge.patterns import ChartPattern

        def pattern_ok(p: dict[str, Any]) -> bool:
            ms, ds = ChartPattern.model_validate(p).refs()
            return not (ms & gm or ds & gd)

        docs["chart_patterns"]["patterns"] = [p for p in docs["chart_patterns"].get("patterns") or [] if pattern_ok(p)]
    if docs.get("analysis_recipes"):
        recipes = []
        for r in docs["analysis_recipes"].get("recipes") or []:
            ok = True
            for slot in (r.get("slots") or {}).values():
                lost = gm if slot.get("kind") == "measure" else gd if slot.get("kind") == "dimension" else set()
                slot["options"] = [o for o in slot.get("options") or [] if o not in lost]
                ok = ok and bool(slot["options"])
                if ok and slot.get("default") in lost:
                    slot["default"] = slot["options"][0]
            if ok and not {f"measure:{m}" for m in gm} & _spec_refs(r.get("spec")):
                recipes.append(r)
        docs["analysis_recipes"]["recipes"] = recipes
    if docs.get("glossary"):
        terms = []
        for t in docs["glossary"].get("terms") or []:
            if any(f in gf or f.partition(".")[0] in ge for f in t.get("fields") or []) or t.get("reading") in gm:
                continue
            if t.get("instead_of"):
                t["instead_of"] = [m for m in t["instead_of"] if m not in gm]
            terms.append(t)
        docs["glossary"]["terms"] = terms
    if docs.get("dashboard_rules"):
        rules = []
        for rule in docs["dashboard_rules"].get("rules") or []:
            named = rule.get("measures") or []
            rule["measures"] = [m for m in named if m not in gm]
            if not named or rule["measures"]:
                rules.append(rule)
        docs["dashboard_rules"]["rules"] = rules
    if docs.get("value_synonyms"):
        values = []
        for v in docs["value_synonyms"].get("values") or []:
            v["dimensions"] = [d for d in v.get("dimensions") or [] if d not in gd]
            if v["dimensions"]:
                values.append(v)
        docs["value_synonyms"]["values"] = values
    if docs.get("gold_examples"):
        lost_refs = {f"measure:{m}" for m in gm} | {f"dimension:{d}" for d in gd} | {f"field:{f}" for f in gf}
        docs["gold_examples"]["examples"] = [
            ex for ex in docs["gold_examples"].get("examples") or []
            if not _spec_refs(ex) & lost_refs and not any(r.startswith("field:") and r[6:].partition(".")[0] in ge
                                                          for r in _spec_refs(ex))]
    return head, docs
