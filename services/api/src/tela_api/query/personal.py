"""Personal measures in a spec (FEAT-030; RULE-014).

A cross-table measure travels inside the spec (``personal_measures``) and is added to a copy of the pinned
snapshot for this one query, so validation, scope injection and fan-out protection treat it exactly like a
certified measure. "Linked to" becomes an explicit filter that the linked entity's key is present, which the
compiler reaches through approved links only; a missing link is refused (MSG-026).
"""

from __future__ import annotations

from typing import Literal

from tela_common.errors import ErrorCode, TelaError
from tela_query_spec.expr import AggExpr
from tela_query_spec.model import Measure, ModelSnapshot
from tela_query_spec.spec import Filter, PersonalMeasure, QuerySpec

AGG_WORDS = {"count": "counts", "count_distinct": "counts distinct", "sum": "adds up", "avg": "averages",
             "min": "takes the smallest", "max": "takes the largest"}
NO_LINK = "There is no approved link between {a} and {b}, so they can't be combined."


def plain_rule(pm: PersonalMeasure, snap: ModelSnapshot) -> str:
    ent = snap.entity(pm.entity)
    what = ent.label.lower() if ent else pm.entity
    field = ent.field(pm.field) if ent and pm.field else None
    text = f"{AGG_WORDS[pm.agg].capitalize()} {field.label.lower() + ' of ' if field else ''}{what}"
    if pm.linked_to:
        linked = snap.entity(pm.linked_to)
        text += f" that have at least one linked {(linked.label if linked else pm.linked_to).lower()}"
    return text + ". Personal definition, not certified."


def to_measure(pm: PersonalMeasure, snap: ModelSnapshot) -> Measure:
    ent = snap.entity(pm.entity)
    if ent is None:
        raise TelaError(ErrorCode.SPEC_INVALID, "That personal measure uses data that is not in the model.")
    if pm.field and ent.field(pm.field) is None:
        raise TelaError(ErrorCode.SPEC_INVALID, "That personal measure uses a field that is not in the model.")
    fmt: Literal["integer", "decimal"] = "integer" if pm.agg in ("count", "count_distinct") else "decimal"
    return Measure(key=pm.key, label=pm.label, entity=pm.entity, expression=AggExpr(agg=pm.agg, field=pm.field),
                   plain_rule=plain_rule(pm, snap), format=fmt, blank_handling="Blank values are not counted.")


def with_personal(spec: QuerySpec, snap: ModelSnapshot) -> tuple[QuerySpec, ModelSnapshot]:
    if not spec.personal_measures:
        return spec, snap
    measures = list(snap.measures)
    filters = list(spec.filters)
    for pm in spec.personal_measures:
        if snap.measure(pm.key) is not None:
            raise TelaError(ErrorCode.SPEC_INVALID, "A personal measure can't reuse a certified measure's key.")
        measures.append(to_measure(pm, snap))
        if pm.linked_to:
            target = snap.entity(pm.linked_to)
            if target is None or snap.join_path(pm.entity, pm.linked_to) is None:
                raise TelaError(ErrorCode.VALIDATION_ERROR, NO_LINK.format(a=pm.entity, b=pm.linked_to))
            flt = Filter(ref=f"field:{pm.linked_to}.{target.primary_key[0]}", op="not_null")
            if flt not in filters:
                filters.append(flt)
    return spec.model_copy(update={"filters": filters}), snap.model_copy(update={"measures": measures})
