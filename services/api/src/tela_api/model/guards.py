"""Mapping guards (US-199; FEAT-067): a wrong field mapping is stopped before the model is certified.

EXEC-115: the pack's Status (record state, Active/Inactive) was mapped by hand to ``current_state`` (lifecycle values),
which Current state also read. The model was certified and every Status measure changed (Open projects 279, Closed
projects 0). Three checks catch it, at the pack's mapping step and again at model approval:

* **Same column** (blocking): two fields of one table read one source column. At approval it needs an Admin
  override with a note (``model.approval_override``).
* **Values** (warning): a field's measure rules filter on values its column does not hold (Status expects Active,
  Inactive; ``current_state`` has Completed, Active, On Hold …). Read as the column's most frequent values.
* **Measures** (warning): a trial figure of each measure on the real data; one that returns 0, or the same figure as
  another measure of its table with a different rule (Open projects = In delivery).

The reads are aggregates over every row (``index_scope``, as the value index), recorded as ``precheck`` executions.
"""

from __future__ import annotations

import json
from typing import Any

from tela_query_spec.expr import split_ref
from tela_query_spec.model import ModelSnapshot

TOP_VALUES = 50  # a column with more distinct values is free text: its values are not checked
SHOWN_VALUES = 8


def shared_columns(definition: dict[str, Any]) -> list[dict[str, str]]:
    """Fields of one table that read one source column (blocking)."""
    issues: list[dict[str, str]] = []
    for e in definition.get("entities", []):
        by_column: dict[str, list[dict[str, Any]]] = {}
        for f in e.get("fields", []):
            by_column.setdefault(str(f["source_column"]).lower(), []).append(f)
        for fields in by_column.values():
            if len(fields) > 1:
                names = " and ".join(str(f["label"]) for f in fields)
                issues.append({"ref": f"field:{e['key']}.{fields[0]['key']}",
                               "issue": f"{names} both read {fields[0]['source_column']} in {e['source_object']}. "
                                        "Measures on either read the other's values."})
    return issues


def _conditions(entity: str, node: Any) -> list[tuple[str, list[str]]]:
    """``(entity.field, values)`` of each equality or list filter in a measure rule on literal text values."""
    out: list[tuple[str, list[str]]] = []
    if isinstance(node, dict):
        fld, op = node.get("field"), node.get("op")
        if isinstance(fld, str) and node.get("other_field") is None and op in ("=", "!=", "in", "not_in"):
            raw = [node.get("value")] if op in ("=", "!=") else list(node.get("values") or [])
            values = [v for v in raw if isinstance(v, str) and v]  # a blank test is about NULLs: not checked
            if values:
                ek, fk = split_ref(entity, fld)
                out.append((f"{ek}.{fk}", values))
        for v in node.values():
            out += _conditions(entity, v)
    elif isinstance(node, list):
        for v in node:
            out += _conditions(entity, v)
    return out


def filter_fields(snapshot: ModelSnapshot) -> list[str]:
    """``entity.field`` of each text field a measure rule filters on literal values."""
    refs: set[str] = set()
    for m in snapshot.measures:
        for ref, _values in _conditions(m.entity, m.model_dump(mode="json", by_alias=True)["expression"]):
            ent_key, _, fkey = ref.partition(".")
            ent = snapshot.entity(ent_key)
            f = ent.field(fkey) if ent else None
            if f is not None and f.data_type == "text":
                refs.add(ref)
    return sorted(refs)


def _found(profile: list[dict[str, Any]] | None) -> set[str] | None:
    """The column's values, or None when they were not read or the column looks like free text."""
    if profile is None or len(profile) >= TOP_VALUES:
        return None
    return {str(v["value"]) for v in profile if v["value"] is not None}


def _has(profile: list[dict[str, Any]]) -> str:
    shown = ", ".join(f"{v['value']} ({v['rows']:,})" for v in profile[:SHOWN_VALUES] if v["value"] is not None)
    more = len(profile) - SHOWN_VALUES
    return (shown + (f" and {more} more" if more > 0 else "")) or "no values"


def value_warnings(expected: dict[str, list[str]], profiles: dict[str, list[dict[str, Any]] | None],
                   columns: dict[str, tuple[str, str]], value_maps: dict[str, dict[str, str]]) -> list[dict[str, Any]]:
    """Pack step: fields whose rule values are not in the mapped column. ``columns``: ref → (field label, column);
    a pack value the Admin mapped a source value to counts as found."""
    out: list[dict[str, Any]] = []
    for ref, values in sorted(expected.items()):
        found = _found(profiles.get(ref))
        if found is None or ref not in columns:
            continue
        mapped = set(value_maps.get(ref, {}).values())
        missing = [v for v in values if v and v not in found and v not in mapped]
        if missing:
            label, column = columns[ref]
            out.append({"kind": "values", "ref": f"field:{ref}", "missing": missing,
                        "issue": f"{label}: the pack's rules use {', '.join(values)}; {column} has "
                                 f"{_has(profiles[ref] or [])}. Not found: {', '.join(missing)}."})
    return out


def filter_warnings(snapshot: ModelSnapshot, profiles: dict[str, list[dict[str, Any]] | None]) -> list[dict[str, Any]]:
    """Approval: measure filters none of whose values are in the column (the filter can only match no rows)."""
    out: list[dict[str, Any]] = []
    for m in snapshot.measures:
        for ref, values in _conditions(m.entity, m.model_dump(mode="json", by_alias=True)["expression"]):
            found = _found(profiles.get(ref))
            if found is None or any(v in found for v in values):
                continue
            ent_key, _, fkey = ref.partition(".")
            ent = snapshot.entity(ent_key)
            f = ent.field(fkey) if ent else None
            if f is None:
                continue
            out.append({"kind": "values", "ref": f"measure:{m.key}", "missing": values,
                        "issue": f"{m.label} filters {f.label} on {', '.join(values)}; {f.source_column} has "
                                 f"{_has(profiles[ref] or [])}."})
    return out


def measure_warnings(measures: list[dict[str, Any]], figures: dict[str, Any]) -> list[dict[str, Any]]:
    """Measures that return 0 on the data, and measures of one table with different rules and the same figure."""
    out: list[dict[str, Any]] = []
    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for m in measures:
        v = figures.get(m["key"])
        if v is None:
            continue
        try:
            number = float(v)
        except (TypeError, ValueError):
            continue
        if number == 0:
            out.append({"kind": "zero", "ref": f"measure:{m['key']}", "issue": f"{m['label']} returns 0 on your data."})
        else:
            groups.setdefault((m["entity"], str(m.get("format")), repr(number)), []).append(m)
    for (_entity, _fmt, _v), same in groups.items():
        rules = {json.dumps(m["expression"], sort_keys=True) for m in same}
        if len(same) > 1 and len(rules) > 1:
            names = ", ".join(m["label"] for m in same)
            out.append({"kind": "same_figure", "ref": f"measure:{same[0]['key']}",
                        "measures": [m["key"] for m in same],
                        "issue": f"{names} all return {figures[same[0]['key']]} with different rules."})
    return out
