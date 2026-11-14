"""Apply a starter pack to a connection that already has a model (US-153; BUG-004; FEAT-046, FEAT-059).

Installing onto an empty connection writes the pack's binding as the draft. Once the Admin has built a model the
pack is *merged* into the model draft instead, so nothing the Admin made is lost:

* entities, fields, measures, dimensions and links the model lacks are added;
* the model's own items stay as they are, with their labels;
* an item both have under the same key but with a different definition is a **clash**. The pack's certified form
  wins unless the Admin chose to keep their own (``keep_mine`` refs such as ``measure:open_projects``,
  ``field:projects.region`` or ``entity:projects``);
* a pack measure, dimension or link whose fields the merged model no longer has (because the Admin kept their own
  entity or field) is left out and its starter items become Unavailable with the reason named;
* a model field under another key that reads the same column as a pack field (BUG-016: a model discovered from the
  source names fields after their columns, ``record_status``, where the pack has ``status``) is folded into the
  pack's field, keeping its description, unless the model's own items use it: then both stay, the preview names
  the pair (``same_column``) and approval's mapping guard (US-199) asks for a fix.

``plan`` is pure: the preview endpoint shows it, the install endpoint stores its definition.
"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from typing import Any

from tela_api.packs.manifest import Binding, PackManifest, widget_gaps
from tela_query_spec.expr import measure_fields, split_ref
from tela_query_spec.model import Dimension, Measure

_FIELD_IDENTITY = ("source_column", "data_type")


def _same(a: Any, b: Any) -> bool:
    return json.dumps(a, sort_keys=True, default=str) == json.dumps(b, sort_keys=True, default=str)


def _link_key(link: dict[str, Any]) -> str:
    return f"{link['from_entity']}>{link['to_entity']}:{','.join(link.get('from_fields', []))}"


def _describe(kind: str, item: dict[str, Any]) -> str:
    if kind == "entity":
        return str(item.get("source_object") or "")
    if kind == "field":
        return f"{item.get('source_column')} ({item.get('data_type')})"
    if kind == "measure":
        return str(item.get("plain_rule") or json.dumps(item.get("expression"), sort_keys=True))
    return f"{item.get('entity')}.{item.get('field')}"


@dataclass
class ApplyPlan:
    definition: dict[str, Any]
    items: list[dict[str, Any]]
    clashes: list[dict[str, Any]] = field(default_factory=list)
    added: dict[str, int] = field(default_factory=dict)
    kept_own: dict[str, int] = field(default_factory=dict)
    same_source: list[dict[str, str]] = field(default_factory=list)
    folded: list[dict[str, str]] = field(default_factory=list)
    same_column: list[dict[str, str]] = field(default_factory=list)

    def out(self) -> dict[str, Any]:
        reports = [i for i in self.items if i["kind"] == "report"]
        return {
            "added": self.added, "kept_own": self.kept_own, "clashes": self.clashes, "same_source": self.same_source,
            "folded": self.folded, "same_column": self.same_column,
            "reports": {"available": sum(1 for r in reports if r["availability"] == "available"),
                        "unavailable": sum(1 for r in reports if r["availability"] == "unavailable")},
            "unavailable": [i for i in self.items if i["availability"] == "unavailable"],
        }


def _used_fields(base: dict[str, Any]) -> set[str]:
    """``entity.field`` of every field the model's measures, dimensions, links and keys read."""
    used: set[str] = set()
    for m in base.get("measures", []):
        try:
            used |= {".".join(split_ref(m["entity"], r)) for r in measure_fields(Measure.model_validate(m).expression)}
        except ValueError:  # a draft item that doesn't validate: treat every field it could read as used
            used.add("*")
    for d in base.get("dimensions", []):
        try:
            used |= {f"{d['entity']}.{f}" for f in Dimension.model_validate(d).fields()}
        except ValueError:
            used.add("*")
    for ln in base.get("links", []):
        used |= {f"{ln['from_entity']}.{f}" for f in ln.get("from_fields", [])}
        used |= {f"{ln['to_entity']}.{f}" for f in ln.get("to_fields", [])}
    for e in base.get("entities", []):
        used |= {f"{e['key']}.{f}" for f in e.get("primary_key", [])}
    return used


def plan(base: dict[str, Any], binding: Binding, manifest: PackManifest, keep_mine: set[str] | None = None
         ) -> ApplyPlan:
    """Merge ``binding`` (the pack bound to this connection) into ``base`` (the model draft definition)."""
    keep = keep_mine or set()
    new = binding.definition
    added = {"entities": 0, "fields": 0, "measures": 0, "dimensions": 0, "links": 0}
    clashes: list[dict[str, Any]] = []
    from_pack: dict[str, set[str]] = {"measure": set(), "dimension": set(), "link": set()}
    newly: dict[str, set[str]] = {"measure": set(), "dimension": set()}
    kept_fields: set[str] = set()  # "entity.field" the Admin kept over the pack's: not what pack items mean

    def clash(kind: str, ref: str, title: str, mine: dict[str, Any], pack: dict[str, Any]) -> bool:
        """Record a clash; True when the pack's form is taken."""
        choice = "mine" if ref in keep else "pack"
        clashes.append({"kind": kind, "ref": ref, "title": title, "mine": _describe(kind, mine),
                        "pack": _describe(kind, pack), "choice": choice})
        return choice == "pack"

    # Entities and fields.
    base_ents = {e["key"]: e for e in base.get("entities", [])}
    by_source = {str(e.get("source_object", "")).lower(): e["key"] for e in base.get("entities", [])}
    same_source: list[dict[str, str]] = []
    folded: list[dict[str, str]] = []
    same_column: list[dict[str, str]] = []
    used = _used_fields(base)
    merged_ents: dict[str, dict[str, Any]] = {}
    for pe in new["entities"]:
        k = pe["key"]
        be = base_ents.get(k)
        if be is None:
            merged_ents[k] = copy.deepcopy(pe)
            added["entities"] += 1
            added["fields"] += len(pe["fields"])
            other = by_source.get(str(pe["source_object"]).lower())
            if other:
                same_source.append({"pack_entity": k, "model_entity": other, "source_object": pe["source_object"]})
            continue
        if str(be.get("source_object", "")).lower() != str(pe["source_object"]).lower():
            if not clash("entity", f"entity:{k}", pe["label"], be, pe):
                merged_ents[k] = copy.deepcopy(be)
                continue
            merged_ents[k] = copy.deepcopy(pe)
            added["fields"] += len(pe["fields"])
            continue
        if f"entity:{k}" in keep:
            merged_ents[k] = copy.deepcopy(be)
            continue
        ent = {**copy.deepcopy(pe), "label": be.get("label", pe["label"])}
        base_fields = {f["key"]: f for f in be.get("fields", [])}
        fields: list[dict[str, Any]] = []
        for pf in pe["fields"]:
            bf = base_fields.pop(pf["key"], None)
            if bf is None:
                fields.append(pf)
                added["fields"] += 1
            elif any(bf.get(a) != pf.get(a) for a in _FIELD_IDENTITY):
                if clash("field", f"field:{k}.{pf['key']}", f"{pe['label']}: {pf['label']}", bf, pf):
                    fields.append(pf)
                else:
                    fields.append(bf)
                    kept_fields.add(f"{k}.{pf['key']}")
            else:
                # Same column: the pack's governance attributes, the Admin's wording.
                fields.append({**bf, **pf, "label": bf.get("label", pf["label"]),
                               **({"description": bf["description"]} if bf.get("description") else {})})
        # The Admin's own fields on this entity; one reading a pack field's column is folded into it (BUG-016).
        by_column = {str(f["source_column"]).lower(): i for i, f in enumerate(fields)}  # the pack's fields so far
        renamed: dict[str, str] = {}
        for bf in base_fields.values():
            i = by_column.get(str(bf["source_column"]).lower())
            if i is None:
                fields.append(bf)
                continue
            pf = fields[i]
            pair = {"entity": k, "column": str(bf["source_column"]), "mine": bf["key"], "mine_label": bf["label"],
                    "pack": pf["key"], "pack_label": pf["label"]}
            if f"{k}.{bf['key']}" in used or "*" in used:
                fields.append(bf)
                same_column.append(pair)
                continue
            if bf.get("description") and not pf.get("description"):
                fields[i] = {**pf, "description": bf["description"]}
            renamed[bf["key"]] = pf["key"]
            folded.append(pair)
        ent["fields"] = fields
        shown = [renamed.get(f, f) for f in be.get("display_fields", [])]
        ent["display_fields"] = list(dict.fromkeys([*shown, *pe.get("display_fields", [])]))
        merged_ents[k] = ent
    entities = [merged_ents.pop(e["key"], e) for e in base.get("entities", [])] + list(merged_ents.values())

    def keyed(kind: str, base_list: list[dict[str, Any]], pack_list: list[dict[str, Any]],
              title: str = "label") -> list[dict[str, Any]]:
        out = {i["key"]: i for i in base_list}
        for pi in pack_list:
            k = pi["key"]
            bi = out.get(k)
            if bi is None:
                out[k] = pi
                newly[kind].add(k)
                from_pack[kind].add(k)
            elif not _same(bi, pi) and clash(kind, f"{kind}:{k}", str(pi[title]), bi, pi):
                out[k] = pi
                from_pack[kind].add(k)
            elif _same(bi, pi):
                from_pack[kind].add(k)
        return list(out.values())

    measures = keyed("measure", base.get("measures", []), new["measures"])
    dimensions = keyed("dimension", base.get("dimensions", []), new["dimensions"])
    links = {_link_key(ln): ln for ln in base.get("links", [])}
    for ln in new["links"]:
        if _link_key(ln) not in links:
            links[_link_key(ln)] = ln
            added["links"] += 1
            from_pack["link"].add(_link_key(ln))

    # Pack items that lost their fields to a "keep mine" choice are left out.
    have = {e["key"]: {f["key"] for f in e.get("fields", []) if f"{e['key']}.{f['key']}" not in kept_fields}
            for e in entities}
    labels = {f"{e['key']}.{f['key']}": f"{e.get('label', e['key'])}: {f.get('label', f['key'])}"
              for e in entities for f in e.get("fields", [])}

    def gaps(entity: str, keys: set[str]) -> list[str]:
        by_entity: dict[str, set[str]] = {entity: set()}  # the measure's own entity is always needed
        for k in keys:  # ``other.field`` reads a linked entity's field (US-156)
            ek, fk = split_ref(entity, k)
            by_entity.setdefault(ek, set()).add(fk)
        out: list[str] = []
        for ek, fks in sorted(by_entity.items()):
            if ek not in have:
                out.append(f"{ek} (whole table)")
                continue
            if ek != entity and not any({ln["from_entity"], ln["to_entity"]} == {entity, ek}
                                        and ln.get("accepted", True) for ln in links.values()):
                out.append(f"link from {entity} to {ek}")
            out += [labels.get(f"{ek}.{k}", f"{ek}.{k}") + (" (kept as yours)" if f"{ek}.{k}" in kept_fields else "")
                    for k in fks - have[ek]]
        return sorted(out)

    dropped: dict[str, list[str]] = {}
    for lk in list(from_pack["link"]):
        ln = links[lk]
        if gaps(ln["from_entity"], set(ln["from_fields"])) or gaps(ln["to_entity"], set(ln["to_fields"])):
            del links[lk]
            added["links"] -= 1
    for m in list(measures):
        if m["key"] in from_pack["measure"]:
            g = gaps(m["entity"], measure_fields(Measure.model_validate(m).expression))
            if g:
                measures.remove(m)
                dropped[f"measure:{m['key']}"] = g
    for d in list(dimensions):
        if d["key"] in from_pack["dimension"]:
            g = gaps(d["entity"], Dimension.model_validate(d).fields())
            if g:
                dimensions.remove(d)
                dropped[f"dimension:{d['key']}"] = g
    ok_measures = {m["key"] for m in measures}
    ok_dims = {d["key"] for d in dimensions}
    added["measures"] = len(newly["measure"] & ok_measures)
    added["dimensions"] = len(newly["dimension"] & ok_dims)
    items = copy.deepcopy(binding.items)
    for item in items:
        ref = f"{item['kind']}:{item['key']}"
        if ref in dropped:
            item["availability"], item["gaps"] = "unavailable", dropped[ref]
        elif item["kind"] == "report" and item["key"] in manifest.reports and item["availability"] == "available":
            # A report shows the widgets the merged model can run; none left = unavailable (US-155).
            per_widget = [widget_gaps(w, ok_measures, ok_dims)[0] for w in manifest.reports[item["key"]]["widgets"]]
            missing = sorted({g for gaps in per_widget for g in gaps} | set(item["gaps"]))
            item["gaps"] = missing
            if all(per_widget):
                item["availability"] = "unavailable"

    base_counts = {"entities": len(base.get("entities", [])), "measures": len(base.get("measures", [])),
                   "dimensions": len(base.get("dimensions", []))}
    definition = {**copy.deepcopy(base), "entities": entities, "measures": measures, "dimensions": dimensions,
                  "links": list(links.values()),
                  "settings": {**base.get("settings", {}), **new.get("settings", {})}}
    return ApplyPlan(definition, items, clashes, added, base_counts, same_source, folded, same_column)
