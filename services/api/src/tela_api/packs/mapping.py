"""Map a starter pack to a source whose names differ (US-154; FEAT-059).

A pack lists candidate physical names for each field (the extract name and the Dataverse logical name). A real
source often uses other names: a publisher prefix (``tpgmea_region``), a plural table (``tpg_projectrequests``),
a choice or lookup column whose label sits next to it (``tpgmea_regionname``), or the Admin's own model labels.
This module holds the name rules the matcher uses beyond exact names, and the Admin's mapping:

* ``entities``: pack entity key → source object, or ``None`` for "not in this source";
* ``fields``: ``entity.field`` → source column, or ``None`` for "not in this source" (items needing it become
  Unavailable with that gap);
* ``values``: ``entity.field`` → {source value → pack value}, e.g. ``{"Yellow": "Amber", "": "Not set"}``.
  Measure rules are rewritten to the source values and dimensions show the pack's words (``value_labels``).

Value-based suggestions (US-200): a pack field with known values (Status: Active, Inactive) that the name rules leave
unmapped, or whose column's values don't fit, is looked for among the table's text columns by value overlap; the best
is suggested with its evidence and a confidence, never applied without the Admin.

The mapping is saved per connection and pack (``model.pack_field_mappings``) and reused by re-apply and pack
updates (US-120).
"""

from __future__ import annotations

import re
from collections import Counter
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from collections.abc import Collection

    from tela_api.packs.manifest import PackField, PackManifest, PackMatch

_PREFIX = re.compile(r"^[a-z][a-z0-9]{1,7}_")  # Dataverse publisher prefix: tpg_, tpgmea_, new_, cr4f2_
_NON_ALNUM = re.compile(r"[^a-z0-9]")
BLANK = ""  # value-map key for an empty source value


class PackMapping(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entities: dict[str, str | None] = Field(default_factory=dict)
    fields: dict[str, str | None] = Field(default_factory=dict)
    values: dict[str, dict[str, str]] = Field(default_factory=dict)

    def merged(self, other: PackMapping) -> PackMapping:
        """This mapping with ``other``'s choices on top."""
        return PackMapping(entities={**self.entities, **other.entities}, fields={**self.fields, **other.fields},
                           values={**self.values, **other.values})


# --------------------------------------------------------------------------- names and types


def norm(name: str) -> str:
    return _NON_ALNUM.sub("", name.lower())


def publisher_prefixes(catalog: list[dict[str, Any]]) -> frozenset[str]:
    """Dataverse publisher prefixes (``tpg_``, ``tpgmea_``, ``new_``) of a Dataverse-like catalogue: the prefixes its
    table names use and those on three or more columns, when its tables carry Dataverse system columns. Any other
    source gets none, so a name such as ``variant_status`` is never read as ``status``."""
    dataverse = any({"statecode", "versionnumber"} & {str(c.get("name", "")).lower() for c in item.get("columns", [])}
                    for item in catalog)
    if not dataverse:
        return frozenset()
    names = [item["qualified_name"].rpartition(".")[2] for item in catalog]
    columns = Counter(m.group(0) for item in catalog for c in item.get("columns", [])
                      if (m := _PREFIX.match(str(c.get("name", "")).lower())))
    return frozenset({m.group(0) for n in names if (m := _PREFIX.match(n.lower()))}
                     | {p for p, n in columns.items() if n >= 3})


def name_variants(name: str, prefixes: frozenset[str] = frozenset()) -> set[str]:
    """A physical name as compared: whole, and without the schema and a publisher prefix."""
    base = name.rpartition(".")[2].lower()
    out = {norm(base)}
    m = _PREFIX.match(base)
    if m and m.group(0) in prefixes and len(base) > len(m.group(0)):
        out.add(norm(base[len(m.group(0)):]))
    return {v for v in out if v}


def singular(word: str) -> str:
    return word[:-1] if len(word) > 3 and word.endswith("s") else word


def family(source_type: str | None) -> str | None:
    """The kind of a source column type; ``None`` when unknown (then any pack type fits)."""
    t = (source_type or "").lower()
    if not t:
        return None
    if "uuid" in t or "uniqueidentifier" in t:
        return "uuid"
    if "bool" in t or t == "bit":
        return "boolean"
    if "date" in t or "time" in t:
        return "temporal"
    if any(k in t for k in ("int", "numeric", "decimal", "double", "float", "real", "money", "number")):
        return "number"
    if any(k in t for k in ("char", "text", "string", "clob")):
        return "text"
    return None


def fits(data_type: str, semantic_type: str | None, source_type: str | None) -> bool:
    """Whether a source column can stand for a pack field of this type."""
    fam = family(source_type)
    if fam is None:
        return True
    if data_type == "text":
        return fam == "text" or (fam == "uuid" and semantic_type == "identifier")
    if data_type in ("integer", "decimal"):
        return fam == "number"
    if data_type in ("date", "datetime"):
        return fam == "temporal"
    if data_type == "boolean":
        return fam == "boolean"
    return True


def can_map(data_type: str, semantic_type: str | None, source_type: str | None) -> bool:
    """Whether the Admin may map a source column onto a pack field (BUG-021). Any column can stand for a text field:
    queries read a yes/no, number or date column as text (``ModelField.reads_text``). Other fields need a fitting
    column, since their measures add, average or compare the values."""
    return data_type == "text" or fits(data_type, semantic_type, source_type)


def label_column(column: str, cols: dict[str, dict[str, Any]], pf: PackField,
                 claimed: Collection[str] = ()) -> str | None:
    """Dataverse keeps a choice or lookup's label in ``<column>name``: use it for a text pack field, unless another
    field of the pack's table names that column as its own (``claimed``, lower case): a pack built from a Dataverse
    model has both ``createdby`` and ``createdbyname``, and each keeps its own column."""
    if pf.data_type != "text" or pf.semantic_type == "identifier":
        return None
    col = cols.get(column.lower())
    if col is None or family(col.get("type")) in (None, "text"):
        return None
    label = cols.get(f"{column.lower()}name")
    if label is None or label["name"].lower() in claimed:
        return None
    return label["name"] if family(label.get("type")) in (None, "text") else None


def field_terms(entity_key: str, entity_label: str, pf: PackField, *, primary_key: bool = False,
                prefixes: frozenset[str] = frozenset()) -> set[str]:
    """Words a source column or model label may use for a pack field (normalised)."""
    words = [pf.key, pf.label, *pf.synonyms, *pf.columns]
    ent = {singular(norm(entity_key)), singular(norm(entity_label))}
    out = {norm(w) for w in words} | {v for c in pf.columns for v in name_variants(c, prefixes)}
    out |= {e + norm(w) for e in ent for w in (pf.key, pf.label)}
    if primary_key:  # Dataverse names a table's key <table>id, e.g. tpg_projectrequestsid
        out |= {e + "id" for e in ent | {norm(entity_key), norm(entity_label)}}
    return {t for t in out if t}


def entity_terms(key: str, label: str, candidates: list[str], synonyms: list[str],
                 prefixes: frozenset[str] = frozenset()) -> set[str]:
    out = {singular(norm(w)) for w in (key, label, *synonyms)}
    out |= {singular(v) for c in candidates for v in name_variants(c, prefixes)}
    return {t for t in out if t}


# --------------------------------------------------------------------------- values


def pack_values(manifest: PackManifest, *, rules_only: bool = False) -> dict[str, list[str]]:
    """The values each pack field's rules and dimensions speak of (``entity.field`` → values); with ``rules_only``,
    only the values measure rules filter on (US-199: those decide the numbers)."""
    out: dict[str, set[str]] = {}

    def walk(entity: str, node: Any) -> None:
        if isinstance(node, dict):
            fld = node.get("field")
            if isinstance(fld, str) and node.get("op") in ("=", "!=") and isinstance(node.get("value"), str):
                out.setdefault(f"{entity}.{fld}", set()).add(node["value"])
            if isinstance(fld, str) and node.get("op") in ("in", "not_in"):
                out.setdefault(f"{entity}.{fld}", set()).update(v for v in node.get("values", [])
                                                                if isinstance(v, str))
            for v in node.values():
                walk(entity, v)
        elif isinstance(node, list):
            for v in node:
                walk(entity, v)

    for m in manifest.measures:
        walk(m["entity"], m.get("expression"))
    if rules_only:
        return {k: sorted(v) for k, v in out.items()}
    for d in manifest.dimensions:
        ref = f"{d['entity']}.{d['field']}"
        vals = [*(d.get("sort_order") or []), *(d.get("value_labels") or {}).values()]
        if vals:
            out.setdefault(ref, set()).update(str(v) for v in vals)
    for e in manifest.entities:
        for f in e.fields:
            if f.values:
                out.setdefault(f"{e.key}.{f.key}", set()).update(f.values)
    return {k: sorted(v) for k, v in out.items()}


def rewrite_values(node: Any, vmaps: dict[str, dict[str, str]]) -> Any:
    """A measure expression with each pack value also matching the source values mapped to it."""
    if isinstance(node, list):
        return [rewrite_values(v, vmaps) for v in node]
    if not isinstance(node, dict):
        return node
    fld, op = node.get("field"), node.get("op")
    vmap = vmaps.get(fld) if isinstance(fld, str) else None
    if vmap and op in ("=", "!=", "in", "not_in") and "other_field" not in node:
        wanted = [node["value"]] if op in ("=", "!=") else list(node.get("values", []))
        if not all(isinstance(v, str | int | float | bool) for v in wanted):
            return node
        raws = [raw for pv in wanted for raw, mapped in vmap.items() if mapped == str(pv)]
        if not raws:
            return node
        values = list(dict.fromkeys([*wanted, *raws]))
        positive = op in ("=", "in")
        out: dict[str, Any] = {"field": fld, "op": "in" if positive else "not_in", "values": values}
        if positive and BLANK in raws:
            return {"any": [out, {"field": fld, "op": "is_null"}]}
        return out
    return {k: rewrite_values(v, vmaps) for k, v in node.items()}


def dimension_labels(dim: dict[str, Any], vmap: dict[str, str]) -> dict[str, str]:
    """A dimension's display mapping with the source values shown as the pack's words."""
    own = dict(dim.get("value_labels") or {})
    return {**{raw: own.get(pv, pv) for raw, pv in vmap.items() if raw != pv}, **own}


# --------------------------------------------------------------------------- mapping step and checks


def check(manifest: PackManifest, catalog: list[dict[str, Any]], mapping: PackMapping,
          result: PackMatch) -> list[dict[str, str]]:
    """Problems with an Admin's mapping: unknown keys, missing tables or columns, a column of the wrong type."""
    issues: list[dict[str, str]] = []
    objects = {c["qualified_name"].lower(): c for c in catalog}
    for key, obj in mapping.entities.items():
        if manifest.entity(key) is None:
            issues.append({"ref": f"entity:{key}", "message": "The pack has no such table."})
        elif obj is not None and obj.lower() not in objects:
            issues.append({"ref": f"entity:{key}", "message": f"{obj} is not in this source's schema."})
    for ref, col in mapping.fields.items():
        ent_key, _, fkey = ref.partition(".")
        ent = manifest.entity(ent_key)
        pf = next((f for f in ent.fields if f.key == fkey), None) if ent else None
        if ent is None or pf is None:
            issues.append({"ref": f"field:{ref}", "message": "The pack has no such field."})
            continue
        if col is None:
            continue
        obj = result.entities[ent_key].object
        cols = {c["name"].lower(): c for c in objects[obj.lower()]["columns"]} if obj else {}
        source = cols.get(col.lower())
        if source is None:
            issues.append({"ref": f"field:{ref}", "message": f"{col} is not a column of {obj or 'the table'}."})
        elif not can_map(pf.data_type, pf.semantic_type, source.get("type")):
            issues.append({"ref": f"field:{ref}",
                           "message": f"{col} is {source.get('type')}; {pf.label} needs {pf.data_type}."})
    for ref in mapping.values:
        ent_key, _, fkey = ref.partition(".")
        ent = manifest.entity(ent_key)
        if ent is None or all(f.key != fkey for f in ent.fields):
            issues.append({"ref": f"values:{ref}", "message": "The pack has no such field."})
    return issues


def conflicts(manifest: PackManifest, result: PackMatch) -> list[dict[str, str]]:
    """Pack fields of one table that read one source column (US-199): blocking. Two fields on one column make every
    measure on either read the other's values (EXEC-115: Status mapped to ``current_state``, which Current state
    also reads). A field whose same-name column the Admin mapped to another field is named too."""
    issues: list[dict[str, str]] = []
    for ent in manifest.entities:
        em = result.entities[ent.key]
        labels = {f.key: f.label for f in ent.fields}
        by_column: dict[str, list[str]] = {}
        for fkey, col in em.mapping.items():
            by_column.setdefault(col.lower(), []).append(fkey)
        for keys in by_column.values():
            if len(keys) > 1:
                names = " and ".join(labels[k] for k in keys)
                issues.append({"ref": f"field:{ent.key}.{keys[0]}",
                               "message": f"{names} both read {em.mapping[keys[0]]}. One column can stand for one "
                                          "pack field only: choose another column for one of them."})
        for fkey, col in em.taken.items():
            other = next((k for k, c in em.mapping.items() if c.lower() == col.lower()), None)
            if fkey in em.mapping or other is None:
                continue
            issues.append({"ref": f"field:{ent.key}.{fkey}",
                           "message": f"{col} is {labels[fkey]}'s own column, but it is mapped to {labels[other]}. "
                                      f"Check {labels[other]}'s column, or choose another one for {labels[fkey]}."})
    return issues


def profile_definition(manifest: PackManifest, result: PackMatch) -> dict[str, Any] | None:
    """A model definition with every mapped pack field and no measures, to read the mapped columns' values before
    the pack is bound (US-199). Tables whose key is not mapped are left out."""
    entities = []
    for ent in manifest.entities:
        em = result.entities[ent.key]
        if em.object is None or any(k not in em.mapping for k in ent.primary_key):
            continue
        entities.append({"key": ent.key, "label": ent.label, "source_object": em.object,
                         "primary_key": ent.primary_key,
                         "fields": [{"key": f.key, "source_column": em.mapping[f.key], "label": f.label,
                                     "data_type": f.data_type} for f in ent.fields if f.key in em.mapping]})
    return {"entities": entities} if entities else None


# --------------------------------------------------------------------------- value-based suggestions (US-200)

FREE_TEXT = 50  # a column with this many distinct values (guards.TOP_VALUES) is free text: no value evidence
MAX_CANDIDATES = 40  # text columns read per table when looking for a field's column by its values
GOOD_FIT = 0.6  # a column scoring this much holds the pack's values: a suggestion, and a mapping that fits


def value_fit(expected: list[str], profile: list[dict[str, Any]] | None,
              vmap: dict[str, str] | None = None) -> dict[str, Any] | None:
    """How well a column's values (most frequent first, with row counts) fit a pack field's values: ``coverage`` (the
    pack's values the column holds, case aside, or the Admin value-mapped), ``purity`` (the column's rows that are
    one of them) and ``score`` = coverage * (1 + purity) / 2. None for free text or a column not read."""
    wanted = {v.casefold(): v for v in expected if v}
    if not wanted or profile is None or len(profile) >= FREE_TEXT:
        return None
    mapped = {raw.casefold(): pv.casefold() for raw, pv in (vmap or {}).items()}
    rows = sum(v["rows"] for v in profile if v["value"] not in (None, ""))
    found: dict[str, None] = {}
    inside = 0
    for v in profile:
        if v["value"] in (None, ""):
            continue
        key = mapped.get(v["value"].casefold(), v["value"].casefold())
        if key in wanted:
            found[wanted[key]] = None
            inside += v["rows"]
    coverage = len(found) / len(wanted)
    purity = inside / rows if rows else 0.0
    return {"score": round(coverage * (0.5 + 0.5 * purity), 3), "coverage": round(coverage, 3),
            "purity": round(purity, 3), "matched": list(found), "others": [
                v["value"] for v in profile if v["value"] not in (None, "")
                and mapped.get(v["value"].casefold(), v["value"].casefold()) not in wanted]}


def value_fields(manifest: PackManifest) -> dict[str, list[str]]:
    """Text pack fields whose values can identify their column: two or more known values (one value, such as Yes,
    is held by too many columns to tell them apart)."""
    values = pack_values(manifest)
    out: dict[str, list[str]] = {}
    for ent in manifest.entities:
        for f in ent.fields:
            ref = f"{ent.key}.{f.key}"
            vals = [v for v in values.get(ref, []) if v]
            if f.data_type == "text" and f.semantic_type != "identifier" and len(vals) >= 2:
                out[ref] = vals
    return out


def wanted_suggestions(manifest: PackManifest, result: PackMatch, mapping: PackMapping,
                       profiles: dict[str, list[dict[str, Any]] | None]) -> dict[str, str]:
    """Pack fields to look for by values (``ref`` → why): ``unmapped`` when the name rules found no column (and the
    Admin did not say "not in this source"), ``values_dont_fit`` when the mapped column's values don't fit."""
    out: dict[str, str] = {}
    for ref, vals in value_fields(manifest).items():
        ent_key, _, fkey = ref.partition(".")
        em = result.entities[ent_key]
        if em.object is None or em.how.get(fkey) == "not_in_source":
            continue
        if fkey not in em.mapping:
            out[ref] = "unmapped"
            continue
        fit = value_fit(vals, profiles.get(ref), result.values.get(ref) or mapping.values.get(ref))
        if fit is not None and fit["score"] < GOOD_FIT:
            out[ref] = "values_dont_fit"
    return out


def candidate_columns(manifest: PackManifest, catalog: list[dict[str, Any]], result: PackMatch,
                      wanted: dict[str, str]) -> dict[str, list[str]]:
    """Per pack table with a field to look for: its source table's text columns that no other pack field reads
    (one column, one pack field; US-199), the field's own column included."""
    objects = {c["qualified_name"].lower(): c for c in catalog}
    out: dict[str, list[str]] = {}
    for ent_key in dict.fromkeys(r.partition(".")[0] for r in wanted):
        em = result.entities[ent_key]
        ent = manifest.entity(ent_key)
        if ent is None or em.object is None or any(k not in em.mapping for k in ent.primary_key):
            continue
        looked_for = {r.partition(".")[2] for r in wanted if r.partition(".")[0] == ent_key}
        taken = {c.lower() for k, c in em.mapping.items() if k not in looked_for} | {
            em.mapping[k].lower() for k in ent.primary_key}
        cols = [c["name"] for c in objects[em.object.lower()]["columns"]
                if family(c.get("type")) in (None, "text") and c["name"].lower() not in taken]
        out[ent_key] = cols[:MAX_CANDIDATES]
    return out


def candidate_definition(manifest: PackManifest, result: PackMatch,
                         columns: dict[str, list[str]]) -> tuple[dict[str, Any] | None, dict[str, tuple[str, str]]]:
    """A model definition reading candidate columns as fields ``c0``, ``c1`` …, and ``entity.c<n>`` → (entity,
    column)."""
    entities = []
    refs: dict[str, tuple[str, str]] = {}
    for ent_key, cols in columns.items():
        ent = manifest.entity(ent_key)
        em = result.entities[ent_key]
        if ent is None or em.object is None or not cols:
            continue
        pk = [{"key": k, "source_column": em.mapping[k], "label": k, "data_type": "text"} for k in ent.primary_key]
        fields = []
        for i, col in enumerate(cols):
            fields.append({"key": f"c{i}", "source_column": col, "label": col, "data_type": "text"})
            refs[f"{ent_key}.c{i}"] = (ent_key, col)
        entities.append({"key": ent_key, "label": ent.label, "source_object": em.object,
                         "primary_key": ent.primary_key, "fields": pk + fields})
    return ({"entities": entities} if entities else None), refs


def suggestions(manifest: PackManifest, result: PackMatch, mapping: PackMapping, wanted: dict[str, str],
                columns: dict[tuple[str, str], list[dict[str, Any]] | None]) -> dict[str, dict[str, Any]]:
    """The best column by values for each wanted field, with its evidence and a confidence (US-200). ``columns``:
    (entity, column) → the column's most frequent values. Nothing is applied: the Admin confirms or picks another."""
    labels = {f"{e.key}.{f.key}": f.label for e in manifest.entities for f in e.fields}
    fields = value_fields(manifest)
    out: dict[str, dict[str, Any]] = {}
    for ref, why in wanted.items():
        ent_key, _, fkey = ref.partition(".")
        em = result.entities[ent_key]
        own = em.mapping.get(fkey)
        others = {c.lower() for k, c in em.mapping.items() if k != fkey}
        scored = []
        for (ek, col), profile in columns.items():
            if ek != ent_key or col.lower() in others:
                continue
            fit = value_fit(fields[ref], profile, mapping.values.get(ref) if own and col.lower() == own.lower()
                            else None)
            if fit and len(fit["matched"]) >= 2 and fit["score"] >= GOOD_FIT:
                scored.append((fit["score"], fit["purity"], col, fit))
        scored.sort(key=lambda s: (-s[0], -s[1], s[2]))
        if own:
            scored = [s for s in scored if s[2].lower() != own.lower()]
        if not scored:
            continue
        score, _, col, fit = scored[0]
        tie = len(scored) > 1 and scored[1][0] == score
        evidence = f"Values {', '.join(fit['matched'])} match the pack's {labels[ref]}"
        if fit["coverage"] < 1:
            evidence += f" ({len(fit['matched'])} of {len(fields[ref])})"
        if fit["others"]:
            evidence += f"; it also holds {', '.join(fit['others'][:3])}" + (" …" if len(fit["others"]) > 3 else "")
        if tie:
            evidence += f". {scored[1][2]} holds them too"
        out[ref] = {"column": col, "confidence": round(score * (0.7 if tie else 1.0), 2), "reason": why,
                    "evidence": evidence + ".", "alternatives": [s[2] for s in scored[1:3]]}
    return out


def step(manifest: PackManifest, catalog: list[dict[str, Any]], result: PackMatch, mapping: PackMapping,
         source_refs: dict[tuple[str, str], Any] | None = None,
         profiles: dict[str, list[dict[str, Any]] | None] | None = None,
         suggested: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    """What the Admin sees in the mapping step: each pack table and field, how it was found, and the choices."""
    objects = {c["qualified_name"].lower(): c for c in catalog}
    values = pack_values(manifest)
    refs = {(o.lower(), c.lower()): r for (o, c), r in (source_refs or {}).items()}
    entities = []
    for ent in manifest.entities:
        em = result.entities[ent.key]
        src = objects.get(em.object.lower()) if em.object else None
        cols = [{"name": c["name"], "type": c.get("type")} for c in src["columns"]] if src else []
        fields = []
        for f in ent.fields:
            ref = f"{ent.key}.{f.key}"
            col = em.mapping.get(f.key)
            row: dict[str, Any] = {"key": f.key, "label": f.label, "data_type": f.data_type,
                                   "semantic_type": f.semantic_type, "required": f.required, "column": col,
                                   "how": em.how.get(f.key, "missing")}
            if ref in values or ref in mapping.values:
                row["values"] = values.get(ref, [])
                row["value_map"] = mapping.values.get(ref, {})
                if em.object and col and (em.object.lower(), col.lower()) in refs:
                    row["source_values_ref"] = refs[(em.object.lower(), col.lower())]
                if col and profiles and profiles.get(ref) is not None:
                    row["source_values"] = profiles[ref]  # the column's most frequent values (US-199)
            if suggested and ref in suggested:
                row["suggestion"] = suggested[ref]  # a column by its values (US-200), not applied
            fields.append(row)
        entities.append({"key": ent.key, "label": ent.label, "object": em.object,
                         "how": em.object_how, "confidence": round(em.confidence, 3),
                         "primary": ent.key == manifest.signature.get("primary_entity", manifest.entities[0].key),
                         "fields": fields, "columns": cols})
    return {"objects": sorted(c["qualified_name"] for c in catalog), "entities": entities}
