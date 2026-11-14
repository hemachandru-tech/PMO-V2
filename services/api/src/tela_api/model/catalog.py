"""Model ↔ catalogue checks and the baseline proposal (US-031; FEAT-007, FEAT-009).

``baseline_definition`` builds a first draft from the introspected catalogue
without any LLM (manual path, A2): one entity per table or view, one field per
column, links from foreign keys (flagged ``accepted: false`` until the Admin
confirms them), and explicit questions where meaning cannot be inferred.

``catalog_issues`` checks a draft against the catalogue: objects and columns
exist, field types match the source, aggregations suit the field type.
"""

from __future__ import annotations

import re
from typing import Any

from tela_query_spec.expr import measure_aggs, split_ref
from tela_query_spec.model import NUMERIC_TYPES, TEMPORAL_TYPES, ModelSnapshot

PERSONAL = re.compile(r"(^|_)(name|email|e_mail|phone|mobile|manager|owner|cio|employee|person)(_|$)")
MONEY = re.compile(r"(budget|cost|amount|price|revenue|spend|value_usd|_usd$|fee)")
STATE = re.compile(r"(^|_)(status|state|stage)(_|$)")


# (source type, declared type) pairs a field may use besides an exact match: an integer column read as decimal, a
# date column (Excel stores dates as midnight date-times) read as a date-time, and a date-time column read as a date.
# The last one holds only because the field records its source type at approval and queries then cast the column to
# its day (``ModelField.reads_day``): SQL Server and Dataverse keep date-only columns as date-times.
WIDENING = {("integer", "decimal"), ("date", "datetime"), ("datetime", "date")}


def source_type(source: str) -> str:
    """Model data type for a source column type of any engine (PostgreSQL, MySQL, SQL Server and Dataverse TDS,
    DuckDB, and the MongoDB sampler's BSON type names)."""
    t = source.lower()
    if t.startswith(("smallint", "integer", "bigint", "int", "tinyint", "mediumint", "hugeint", "ubigint",
                     "uinteger", "usmallint", "utinyint", "year", "long")):
        return "integer"
    if t.startswith(("numeric", "decimal", "real", "double", "money", "float", "smallmoney", "number")):
        return "decimal"
    if t == "date" or t == "dateonly":
        return "date"
    if t.startswith(("timestamp", "datetime", "smalldatetime")):
        return "datetime"
    if t.startswith("bool") or t == "bit":
        return "boolean"
    if t.startswith("json") or t in ("object", "array"):
        return "json"
    return "text"


def _label(name: str) -> str:
    return name.replace("_", " ").strip().capitalize()


def _key(name: str) -> str:
    key = re.sub(r"[^a-z0-9_]", "_", name.lower()).strip("_") or "field"
    return key if re.match(r"^[a-z_]", key) else f"f_{key}"


def _field(c: dict[str, Any], table: str, entity_key: str, key: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """One model field for a catalogue column, with the questions the baseline asks about it."""
    dtype = source_type(c["type"])
    # An uploader's personal-data flag (Excel/CSV, US-108) or the name heuristic.
    sensitivity = "personal" if c.get("personal") or PERSONAL.search(c["name"].lower()) else "internal"
    money = dtype == "decimal" and bool(MONEY.search(c["name"].lower()))
    field = {"key": key, "source_column": c["name"], "label": _label(c["name"]), "data_type": dtype,
             "sensitivity": sensitivity, "semantic_type": "currency" if money else None}
    questions = []
    if sensitivity == "personal":
        questions.append({"question": f"Is '{c['name']}' in {table} personal data? Confirm its sensitivity.",
                          "target_ref": {"entity": entity_key, "field": key}, "options": ["Personal", "Not personal"]})
    if money:
        questions.append({"question": f"Which currency is '{c['name']}' in, and does it include VAT?",
                          "target_ref": {"entity": entity_key, "field": key},
                          "options": ["USD, excludes VAT", "USD, includes VAT", "Mixed currencies"]})
    return field, questions


def baseline_definition(objects: list[dict[str, Any]]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """(definition, questions) from introspected objects."""
    entities: list[dict[str, Any]] = []
    by_object: dict[str, dict[str, Any]] = {}
    questions: list[dict[str, Any]] = []
    for o in objects:
        name = o["qualified_name"].rpartition(".")[2]
        pk = o["keys"].get("primary") or []
        fields = []
        for c in o["columns"]:
            field, asks = _field(c, name, _key(name), _key(c["name"]))
            fields.append(field)
            questions.extend(asks)
        if not pk:
            pk_fields = [fields[0]["key"]] if fields else []
            questions.append({"question": f"'{name}' has no primary key. Which column identifies one row?",
                              "target_ref": {"entity": _key(name)},
                              "options": [f["source_column"] for f in fields[:5]]})
        else:
            pk_fields = [_key(c) for c in pk]
        ent = {"key": _key(name), "label": _label(name), "source_object": o["qualified_name"],
               "primary_key": pk_fields, "fields": fields, "grain": f"One row per {name.rstrip('s')} record"}
        entities.append(ent)
        by_object[o["qualified_name"].lower()] = ent
    links = []
    for o in objects:
        src = by_object[o["qualified_name"].lower()]
        for fk in o["keys"].get("foreign") or []:
            tgt = by_object.get((fk.get("ref_object") or "").lower())
            if tgt is None:
                continue
            links.append({"from_entity": src["key"], "to_entity": tgt["key"],
                          "from_fields": [_key(c) for c in fk["columns"]],
                          "to_fields": [_key(c) for c in fk["ref_columns"]],
                          "cardinality": "many_to_one", "origin": "foreign_key", "accepted": False})
    measures = [{"key": f"{e['key']}_count", "label": f"Number of {e['label'].lower()}", "entity": e["key"],
                 "expression": {"agg": "count"}, "plain_rule": f"Counts every {e['label'].lower()} record.",
                 "format": "integer"} for e in entities]
    return {"entities": entities, "links": links, "measures": measures, "dimensions": [], "settings": {}}, questions


def unmodelled_objects(definition: dict[str, Any], objects: list[dict[str, Any]]) -> list[str]:
    """Readable catalogue objects no entity of the model reads (BUG-018), e.g. tables found by a later schema read."""
    used = {str(e.get("source_object", "")).lower() for e in definition.get("entities", [])}
    return [o["qualified_name"] for o in objects if o["qualified_name"].lower() not in used]


def extend_definition(definition: dict[str, Any], objects: list[dict[str, Any]], catalog: list[dict[str, Any]]
                      ) -> tuple[dict[str, Any], list[dict[str, Any]], list[str]]:
    """Add tables to an existing model the way the baseline would have (BUG-018).

    ``objects`` are the catalogue rows to add, ``catalog`` every readable row (for foreign keys between a new table
    and one the model already reads). Existing entities, measures and links are left as they are; a new entity whose
    key is taken gets a numbered key. Returns (definition, questions, added entity keys)."""
    entities = list(definition.get("entities", []))
    by_object = {str(e.get("source_object", "")).lower(): e for e in entities}
    new = [o for o in objects if o["qualified_name"].lower() not in by_object]
    base, questions = baseline_definition(new)
    taken = {e["key"] for e in entities}
    rename: dict[str, str] = {}
    for ent in base["entities"]:
        key, n = ent["key"], 2
        while key in taken:
            key, n = f"{ent['key']}_{n}", n + 1
        rename[ent["key"]] = key
        taken.add(key)
        ent["key"] = key
        by_object[ent["source_object"].lower()] = ent
    for q in questions:
        ref = q.get("target_ref") or {}
        if ref.get("entity") in rename:
            ref["entity"] = rename[ref["entity"]]
    added = {e["key"] for e in base["entities"]}

    def field_key(ent: dict[str, Any], column: str) -> str:
        return next((f["key"] for f in ent.get("fields", []) if str(f.get("source_column", "")).lower()
                     == column.lower()), _key(column))

    links = list(definition.get("links", []))
    seen = {(ln["from_entity"], ln["to_entity"], tuple(ln["from_fields"])) for ln in links}
    for o in catalog:
        src = by_object.get(o["qualified_name"].lower())
        if src is None:
            continue
        for fk in (o.get("keys") or {}).get("foreign") or []:
            tgt = by_object.get((fk.get("ref_object") or "").lower())
            if tgt is None or (src["key"] not in added and tgt["key"] not in added):
                continue
            link = {"from_entity": src["key"], "to_entity": tgt["key"],
                    "from_fields": [field_key(src, c) for c in fk["columns"]],
                    "to_fields": [field_key(tgt, c) for c in fk["ref_columns"]],
                    "cardinality": "many_to_one", "origin": "foreign_key", "accepted": False}
            ident = (link["from_entity"], link["to_entity"], tuple(link["from_fields"]))
            if ident not in seen:
                seen.add(ident)
                links.append(link)
    measures = list(definition.get("measures", []))
    measure_keys = {m["key"] for m in measures}
    for ent in base["entities"]:
        m = {"key": f"{ent['key']}_count", "label": f"Number of {ent['label'].lower()}", "entity": ent["key"],
             "expression": {"agg": "count"}, "plain_rule": f"Counts every {ent['label'].lower()} record.",
             "format": "integer"}
        if m["key"] not in measure_keys:
            measures.append(m)
    out = {**definition, "entities": entities + base["entities"], "links": links, "measures": measures}
    return out, questions, [e["key"] for e in base["entities"]]


def unmodelled_columns(definition: dict[str, Any], objects: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Catalogue columns of tables the model reads that no field of the entity uses (BUG-028), e.g. columns added to
    the source after the model was built. One item per entity: ``{"entity", "object", "columns"}``."""
    by_object = {o["qualified_name"].lower(): o for o in objects}
    out = []
    for ent in definition.get("entities", []):
        obj = by_object.get(str(ent.get("source_object", "")).lower())
        if obj is None:
            continue
        used = {str(f.get("source_column", "")).lower() for f in ent.get("fields", [])}
        new = [c["name"] for c in obj["columns"] if c["name"].lower() not in used]
        if new:
            out.append({"entity": ent["key"], "object": obj["qualified_name"], "columns": new})
    return out


def extend_columns(definition: dict[str, Any], wanted: dict[str, list[str]], catalog: list[dict[str, Any]]
                   ) -> tuple[dict[str, Any], list[dict[str, Any]], list[str]]:
    """Add source columns to entities the model already has, one field each as the baseline would (BUG-028).

    ``wanted`` maps entity key → column names. Columns the entity already reads, or that the catalogue does not list,
    are skipped; a field whose key is taken gets a numbered key. The Admin's fields, measures and links are left as
    they are. Returns (definition, questions, added ``entity.field`` refs)."""
    by_object = {o["qualified_name"].lower(): o for o in catalog}
    entities, questions, added = [], [], []
    for ent in definition.get("entities", []):
        names = {n.lower() for n in wanted.get(ent["key"], [])}
        obj = by_object.get(str(ent.get("source_object", "")).lower())
        if not names or obj is None:
            entities.append(ent)
            continue
        fields = list(ent.get("fields", []))
        used = {str(f.get("source_column", "")).lower() for f in fields}
        taken = {f["key"] for f in fields}
        table = obj["qualified_name"].rpartition(".")[2]
        for c in obj["columns"]:
            if c["name"].lower() not in names or c["name"].lower() in used:
                continue
            key, n = _key(c["name"]), 2
            while key in taken:
                key, n = f"{_key(c['name'])}_{n}", n + 1
            taken.add(key)
            used.add(c["name"].lower())
            field, asks = _field(c, table, ent["key"], key)
            fields.append(field)
            questions.extend(asks)
            added.append(f"{ent['key']}.{key}")
        entities.append({**ent, "fields": fields})
    return {**definition, "entities": entities}, questions, added


def stamp_source_types(snapshot: ModelSnapshot, catalog: dict[str, list[dict[str, Any]]]) -> None:
    """Record each field's source column type (``ModelField.source_type``) from the catalogue, before freezing."""
    for ent in snapshot.entities:
        types = {c["name"]: source_type(c["type"]) for c in catalog.get(ent.source_object.lower(), [])}
        for f in ent.fields:
            f.source_type = types.get(f.source_column)


def catalog_issues(snapshot: ModelSnapshot, catalog: dict[str, list[dict[str, Any]]],
                   unreadable: set[str] | frozenset[str] = frozenset()) -> list[dict[str, str]]:
    """Problems that block approval. ``catalog`` maps lower-case qualified name → columns; ``unreadable`` lists the
    objects whose rows the source account may not read."""
    issues: list[dict[str, str]] = []
    for ent in snapshot.entities:
        cols = catalog.get(ent.source_object.lower())
        if cols is None:
            issues.append({"ref": f"entity:{ent.key}", "issue": f"source object {ent.source_object} not found"})
            continue
        if ent.source_object.lower() in unreadable:
            issues.append({"ref": f"entity:{ent.key}", "issue": f"the source account may not read "
                           f"{ent.source_object}: give its role Read on this table, or remove {ent.label} from the "
                           f"model"})
            continue
        types = {c["name"]: source_type(c["type"]) for c in cols}
        for f in ent.fields:
            st = types.get(f.source_column)
            if st is None:
                issues.append({"ref": f"field:{ent.key}.{f.key}", "issue": f"column {f.source_column} not found"})
            elif st != f.data_type and (st, f.data_type) not in WIDENING and f.data_type != "text":
                issues.append({"ref": f"field:{ent.key}.{f.key}",
                               "issue": f"declared {f.data_type} but the source column is {st}"})
    for m in snapshot.measures:
        ment = snapshot.entity(m.entity)
        assert ment is not None
        for agg in measure_aggs(m.expression):
            if agg.field is None:
                continue
            ek, fk = split_ref(m.entity, agg.field)
            fent = snapshot.entity(ek)
            mf = fent.field(fk) if fent else None
            assert mf is not None
            if agg.agg in ("median", "percentile") and mf.data_type not in NUMERIC_TYPES:
                issues.append({"ref": f"measure:{m.key}", "issue": f"{agg.agg} needs a number; {mf.key} is "
                               f"{mf.data_type}"})
            if agg.agg in ("sum", "avg") and mf.data_type not in NUMERIC_TYPES:
                issues.append({"ref": f"measure:{m.key}",
                               "issue": f"{agg.agg} needs a number; {mf.key} is {mf.data_type}"})
            if agg.agg in ("min", "max") and mf.data_type not in NUMERIC_TYPES | TEMPORAL_TYPES:
                issues.append({"ref": f"measure:{m.key}", "issue": f"{agg.agg} needs a number or date"})
    for d in snapshot.dimensions:
        dent = snapshot.entity(d.entity)
        dfield = dent.field(d.field) if dent else None
        if d.is_temporal and dfield is not None and dfield.data_type not in TEMPORAL_TYPES:
            issues.append({"ref": f"dimension:{d.key}", "issue": "a time dimension needs a date field"})
    return issues
