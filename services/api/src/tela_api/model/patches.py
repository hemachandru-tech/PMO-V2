"""Typed model change operations (ModelPatch; FEAT-007, FEAT-008).

The assistant (LLM or rules) only ever produces these operations; code validates and applies them to a
copy of the draft, so every change is previewed as a diff before it touches anything, and Undo restores
the exact previous draft.
"""

from __future__ import annotations

import copy
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field

from tela_query_spec.expr import Condition, check_ident

FORMATS = {"currency", "percent", "integer", "decimal"}


class _Op(BaseModel):
    reason: str | None = Field(default=None, max_length=300)


class AddLink(_Op):
    op: Literal["add_link"]
    from_entity: str
    to_entity: str
    from_fields: list[str] = Field(min_length=1, max_length=4)
    to_fields: list[str] = Field(min_length=1, max_length=4)
    cardinality: Literal["one_to_one", "many_to_one", "one_to_many"] = "many_to_one"
    origin: Literal["foreign_key", "proposed"] = "proposed"


class AddMeasure(_Op):
    op: Literal["add_measure"]
    key: str
    label: str = Field(min_length=1, max_length=120)
    entity: str
    agg: Literal["count", "count_distinct", "sum", "avg", "min", "max"]
    field: str | None = None
    where: Condition | None = None
    plain_rule: str = Field(min_length=1, max_length=500)
    format: Literal["integer", "decimal", "currency", "percent"] = "integer"


class AddDimension(_Op):
    op: Literal["add_dimension"]
    key: str
    label: str = Field(min_length=1, max_length=120)
    entity: str
    field: str
    is_temporal: bool = False
    is_status: bool = False


class RenameField(_Op):
    op: Literal["rename_field"]
    entity: str
    field: str
    label: str = Field(min_length=1, max_length=120)


class RenameMeasure(_Op):
    op: Literal["rename_measure"]
    measure: str
    label: str = Field(min_length=1, max_length=120)


class SetDescription(_Op):
    op: Literal["set_description"]
    entity: str
    field: str
    description: str = Field(min_length=1, max_length=500)


class SetFormat(_Op):
    op: Literal["set_format"]
    entity: str
    field: str
    format: Literal["currency", "percent", "integer", "decimal"]
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")


class SetSensitivity(_Op):
    op: Literal["set_sensitivity"]
    entity: str
    field: str
    sensitivity: Literal["public", "internal", "confidential", "personal", "sensitive"]


class SetValueLabel(_Op):
    op: Literal["set_value_label"]
    dimension: str
    raw: str = Field(min_length=1, max_length=120)
    label: str = Field(min_length=1, max_length=120)


class AddMeasureCondition(_Op):
    op: Literal["add_measure_condition"]
    measure: str
    condition: Condition


Op = Annotated[AddLink | AddMeasure | AddDimension | RenameField | RenameMeasure | SetDescription | SetFormat
               | SetSensitivity | SetValueLabel | AddMeasureCondition, Field(discriminator="op")]


class ProposedQuestion(BaseModel):
    question: str = Field(min_length=3, max_length=400)
    options: list[str] = Field(default_factory=list, max_length=6)
    target: str | None = None


MAX_OPS, MAX_QUESTIONS = 60, 20


class ModelPatch(BaseModel):
    """What AG-04 must return."""

    ops: list[Op] = Field(default_factory=list, max_length=MAX_OPS)
    questions: list[ProposedQuestion] = Field(default_factory=list, max_length=MAX_QUESTIONS)


# --------------------------------------------------------------------------- application


class OpError(ValueError):
    pass


def _entity(d: dict[str, Any], key: str) -> dict[str, Any]:
    e: dict[str, Any] | None = next((e for e in d["entities"] if e["key"] == key), None)
    if e is None:
        raise OpError(f"entity '{key}' is not in the model")
    return e


def _field(d: dict[str, Any], entity: str, key: str) -> dict[str, Any]:
    f: dict[str, Any] | None = next((f for f in _entity(d, entity)["fields"] if f["key"] == key), None)
    if f is None:
        raise OpError(f"field '{entity}.{key}' is not in the model")
    return f


def _measure(d: dict[str, Any], key: str) -> dict[str, Any]:
    m: dict[str, Any] | None = next((m for m in d["measures"] if m["key"] == key), None)
    if m is None:
        raise OpError(f"measure '{key}' is not in the model")
    return m


def apply_op(d: dict[str, Any], op: Any) -> tuple[str, str, str]:
    """Apply one op in place; returns (target, before, after) for the diff."""
    if isinstance(op, AddLink):
        for entity_key, keys in ((op.from_entity, op.from_fields), (op.to_entity, op.to_fields)):
            for key in keys:
                _field(d, entity_key, key)
        if any(ln["from_entity"] == op.from_entity and ln["to_entity"] == op.to_entity
               and ln["from_fields"] == op.from_fields for ln in d["links"]):
            raise OpError("that link already exists")
        d["links"].append({"from_entity": op.from_entity, "to_entity": op.to_entity, "from_fields": op.from_fields,
                           "to_fields": op.to_fields, "cardinality": op.cardinality, "origin": op.origin,
                           "accepted": False})
        return (f"link {op.from_entity} → {op.to_entity}", "—",
                f"{op.cardinality.replace('_', '-')} on {', '.join(op.from_fields)} (needs your confirmation)")
    if isinstance(op, AddMeasure):
        check_ident(op.key)
        if any(m["key"] == op.key for m in d["measures"]):
            raise OpError(f"measure '{op.key}' already exists")
        ent = _entity(d, op.entity)
        if op.field:
            source = _field(d, op.entity, op.field)
            if op.agg in ("sum", "avg") and source["data_type"] not in ("integer", "decimal"):
                raise OpError(f"{op.agg} needs a number; {op.field} is {source['data_type']}")
        elif op.agg != "count":
            raise OpError(f"{op.agg} needs a field")
        expr: dict[str, Any] = {"agg": op.agg}
        if op.field:
            expr["field"] = op.field
        if op.where is not None:
            expr["where"] = op.where.model_dump(by_alias=True, exclude_none=True)
        d["measures"].append({"key": op.key, "label": op.label, "entity": ent["key"], "expression": expr,
                              "plain_rule": op.plain_rule, "format": op.format})
        return f"measure {op.label}", "—", op.plain_rule
    if isinstance(op, AddDimension):
        check_ident(op.key)
        _field(d, op.entity, op.field)
        if any(x["key"] == op.key for x in d["dimensions"]):
            raise OpError(f"dimension '{op.key}' already exists")
        d["dimensions"].append({"key": op.key, "label": op.label, "entity": op.entity, "field": op.field,
                                "is_temporal": op.is_temporal, "is_status": op.is_status})
        return f"dimension {op.label}", "—", f"group by {op.entity}.{op.field}"
    if isinstance(op, RenameField):
        f = _field(d, op.entity, op.field)
        before, f["label"] = f["label"], op.label
        for dim in d["dimensions"]:
            if dim["entity"] == op.entity and dim["field"] == op.field and dim["label"] == before:
                dim["label"] = op.label
        return f"field {op.entity}.{op.field}", before, op.label
    if isinstance(op, RenameMeasure):
        m = _measure(d, op.measure)
        before, m["label"] = m["label"], op.label
        return f"measure {op.measure}", before, op.label
    if isinstance(op, SetDescription):
        f = _field(d, op.entity, op.field)
        before, f["description"] = f.get("description") or "—", op.description
        return f"description of {f['label']}", before, op.description
    if isinstance(op, SetFormat):
        f = _field(d, op.entity, op.field)
        if op.format in ("currency", "percent", "decimal") and f["data_type"] not in ("integer", "decimal"):
            raise OpError(f"{f['label']} is {f['data_type']}, so it can't be formatted as {op.format}")
        before = f.get("semantic_type") or "plain"
        fmt = dict(f.get("format") or {})
        if op.currency:
            fmt["currency"] = op.currency
        f["format"] = fmt
        f["semantic_type"] = {"currency": "currency", "percent": "percent"}.get(op.format, f.get("semantic_type"))
        for m in d["measures"]:
            if m["entity"] == op.entity and m["expression"].get("field") == op.field and op.format in FORMATS:
                m["format"] = op.format
        return f"format of {f['label']}", before, op.format + (f" ({op.currency})" if op.currency else "")
    if isinstance(op, SetSensitivity):
        f = _field(d, op.entity, op.field)
        before, f["sensitivity"] = f.get("sensitivity", "internal"), op.sensitivity
        return f"sensitivity of {f['label']}", before, op.sensitivity
    if isinstance(op, SetValueLabel):
        dim = next((x for x in d["dimensions"] if x["key"] == op.dimension), None)
        if dim is None:
            raise OpError(f"dimension '{op.dimension}' is not in the model")
        labels = dict(dim.get("value_labels") or {})
        before = labels.get(op.raw, op.raw)
        labels[op.raw] = op.label
        dim["value_labels"] = labels
        return f"values of {dim['label']}", f"{op.raw} shown as {before}", f"{op.raw} shown as {op.label}"
    if isinstance(op, AddMeasureCondition):
        m = _measure(d, op.measure)
        cond = op.condition.model_dump(by_alias=True, exclude_none=True)
        expr = m["expression"]
        if "agg" not in expr:
            raise OpError("conditions can be added to single-aggregate measures only")
        before = m["plain_rule"]
        expr["where"] = {"all": [expr["where"], cond]} if expr.get("where") else cond
        m["plain_rule"] = f"{before.rstrip('.')}; {_condition_text(cond)}."
        return f"rule of {m['label']}", before, m["plain_rule"]
    raise OpError("unsupported operation")


def _condition_text(cond: dict[str, Any]) -> str:
    if cond.get("op") == "not_in":
        return f"excluding {cond['field']} {', '.join(map(str, cond['values']))}"
    if cond.get("op") == "in":
        return f"only {cond['field']} {', '.join(map(str, cond['values']))}"
    return "with an extra condition"


def apply_ops(definition: dict[str, Any], ops: list[Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Apply ops to a copy; returns (new definition, per-op results with diff or the reason it was dropped)."""
    d = copy.deepcopy(definition)
    for section in ("entities", "links", "measures", "dimensions"):
        d.setdefault(section, [])
    results = []
    for i, op in enumerate(ops):
        trial = copy.deepcopy(d)
        try:
            target, before, after = apply_op(trial, op)
        except (OpError, ValueError) as exc:
            results.append({"index": i, "op": op.op, "valid": False, "error": str(exc)})
            continue
        d = trial
        results.append({"index": i, "op": op.op, "valid": True, "target": target, "before": before, "after": after})
    return d, results
