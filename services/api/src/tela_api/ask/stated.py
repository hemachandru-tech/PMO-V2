"""Knowledge-led planning route (ENH-017, US-204; FEAT-067 / FEAT-066 / FEAT-062).

A question that says what it means is answered by that meaning, not by a measure's built-in rule (W3):

* **Stated criteria win.** "How many open projects (current state Project Created, Active or On Hold) are there?"
  planned by the rules as Open projects (status Active) filtered by current state stacks the question's criteria on
  the measure's rule. When the criteria are on a field the rule reads — the same field, or another field of the
  table on the same source column (EXEC-115: Status mapped to ``current_state``) — and allow values the rule doesn't,
  that part of the rule is replaced: the table's certified measure with the same aggregate and the rest of the rule
  (Projects, a plain count), filtered by the stated values. Every value of the listed set stays in the filter (the
  "On Hold" the rules read as the Projects on hold measure, found from the field's scoped values as US-205 does).
* **Approved definitions** (US-203) are applied the same way: a question that uses the term of an expressible
  definition whose certified measure counts by another rule is counted by the definition's condition in place of
  the measure's rule (the measure's aggregate, as ``definitions.definition_spec`` counts it at approval). A
  definition with no measure answers a question the rules refused, as a count of its table.
* **Numbers come from queries only** (W2): the reading is a certified measure plus a filter, compiled and validated
  like any other spec; no ad-hoc rule (that needs free-hand access, US-162) and no model call. A rule this can't
  express (no certified base measure, a non-text field) keeps the rules' plan, and AG-03 plans text-only definitions
  with the definitions first in its context (``Orchestrator._plan_for``).
* **Visibly** (W3): the plan carries ``CountedBy`` — the interpreted line "Counted by your definition: …" or "Counted by
  the approved definition “Open project”: …", the badge, the passage cited — and the certified measure's own figure is
  shown beside the answer when they differ.

Plain questions keep the rules planner (W4): nothing here applies unless the question states criteria on the rule's
field or uses an approved definition's term.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any, Literal

from tela_api.ask import confidence
from tela_api.ask.planner import STOP, Plan, norm, words
from tela_api.ask.refine import chips_for
from tela_api.ask.rewrite import _swap_measure
from tela_api.knowledge import definitions
from tela_query_spec.expr import AggExpr, All, Compare, InSet
from tela_query_spec.model import Dimension, Measure, ModelSnapshot
from tela_query_spec.spec import ref_key

MSG_YOURS = "Counted by your definition: {rule}"
MSG_APPROVED = "Counted by the approved definition “{term}”: {rule}"
BADGES = {"question": "your_definition", "definition": "knowledge_definition"}
UNSUPPORTED_SPEC = ("extra_measures", "derived", "share_of_total", "exists_in", "steps", "recipe", "cohort")


@dataclass
class CountedBy:
    """What an answer was counted by instead of a certified measure's rule (US-204)."""

    source: Literal["question", "definition"]
    measure: Measure  # the certified measure whose rule was replaced
    rule: str  # "Current state in Project Created, Active, On Hold"
    filters: list[dict[str, Any]]  # the filters counted by, in the plan's spec
    definition: dict[str, Any] | None = None  # the approved definition row (source "definition")

    @property
    def text(self) -> str:
        if self.definition is None:
            return MSG_YOURS.format(rule=self.rule)
        return MSG_APPROVED.format(term=self.definition["term"], rule=self.rule)

    @property
    def badge(self) -> str:
        return BADGES[self.source]

    def cites(self) -> list[str]:
        """The passage the definition came from, as a knowledge chunk id."""
        if self.definition is None or not self.definition.get("chunk_id"):
            return []
        return [f"chunk:{self.definition['chunk_id']}"]

    def public(self) -> dict[str, Any]:
        d = self.definition
        return {"source": self.source, "text": self.text, "rule": self.rule,
                "measure": {"ref": f"measure:{self.measure.key}", "label": self.measure.label,
                            "plain_rule": self.measure.plain_rule},
                "definition": None if d is None else {
                    "id": str(d["id"]), "term": d["term"], "meaning": d["meaning"],
                    "document": {"id": str(d["document_id"]), "title": d.get("document_title")},
                    "chunk_id": str(d["chunk_id"]) if d.get("chunk_id") else None, "passage": d.get("passage")}}

    def beside(self, spec: dict[str, Any]) -> dict[str, Any]:
        """The certified measure's own answer, with the plan's other filters (not the stated ones)."""
        return {"kind": "certified", "spec": spec, "label": self.measure.label, "plain_rule": self.measure.plain_rule,
                "ref": f"measure:{self.measure.key}", "own_filters": True, "when_differs": True}


# --------------------------------------------------------------------------- the measure's rule


def rule_parts(m: Measure) -> list[Any]:
    """The conditions of a measure's rule that all must hold (an ``all`` is its parts)."""
    expr = m.expression
    if not isinstance(expr, AggExpr) or expr.where is None:
        return []
    return list(expr.where.all) if isinstance(expr.where, All) else [expr.where]


def _part_field(part: Any) -> str | None:
    """The field of a listed-values condition ("status = Active", "status in (…)"); None for any other."""
    if isinstance(part, InSet):
        return str(part.field)
    if isinstance(part, Compare) and part.op in ("=", "!=") and part.other_field is None and \
            isinstance(part.value, str):
        return str(part.field)
    return None


def _allows(part: Any, value: str) -> bool:
    v = norm(value)
    if isinstance(part, InSet):
        listed = {norm(str(x)) for x in part.values}
        return bool((v in listed) == (part.op == "in"))
    return bool((v == norm(str(part.value))) == (part.op == "="))


def same_field(snap: ModelSnapshot, entity: str, a: str, b: str) -> bool:
    """Two fields of a table are one when they are, or read the same source column (EXEC-115)."""
    if a == b:
        return True
    ent = snap.entity(entity)
    fa, fb = (ent.field(a), ent.field(b)) if ent else (None, None)
    return fa is not None and fb is not None and fa.source_column == fb.source_column


def restated_rule(spec: dict[str, Any], question: str, snap: ModelSnapshot, m: Measure,
                  values: dict[str, list[str]] | None) -> dict[str, Any] | None:
    """The filter that only restates the measure's own rule, or None (US-202, DEC-24): "open projects (current state
    Project Created, Active or On Hold)" is Open projects itself. The listed values the filter left out (On Hold, read
    as the Projects on hold measure) count as stated; a filter that narrows the rule ("open projects that are active")
    is not a restatement."""
    parts = rule_parts(m)
    if not parts:
        return None
    left_out = confidence.listed_values(norm(question), spec, snap, values, confidence._own(m))
    full = confidence.with_listed(spec, left_out, snap, [])[0] if left_out else spec
    filters: list[dict[str, Any]] = spec.get("filters", [])
    for f, said in zip(filters, full.get("filters", []), strict=True):
        d = snap.dimension(ref_key(f["ref"]))
        if d is None or d.entity != m.entity or d.of is not None or d.bands or f.get("op") not in ("=", "in"):
            continue
        stated = {norm(str(v)) for v in said.get("values", [])}
        for p in parts:
            if (pf := _part_field(p)) is None or not same_field(snap, m.entity, pf, d.field):
                continue
            rule = ({norm(str(x)) for x in p.values} if isinstance(p, InSet) and p.op == "in"
                    else {norm(str(p.value))} if isinstance(p, Compare) and p.op == "=" else None)
            if rule == stated:
                return f
    return None


def base_measure(m: Measure, rest: list[Any], measures: list[Measure]) -> Measure | None:
    """The certified measure of the same table counting the same way with only ``rest`` of the rule: Projects for
    Open projects once its status condition is replaced."""
    expr = m.expression
    if not isinstance(expr, AggExpr):
        return None
    where = None if not rest else rest[0] if len(rest) == 1 else All(all=rest)
    for x in measures:
        e = x.expression
        if x.key != m.key and x.entity == m.entity and isinstance(e, AggExpr) and e.agg == expr.agg and \
                e.field == expr.field and e.value == expr.value and e.p == expr.p and e.where == where:
            return x
    return None


def dimension_for(snap: ModelSnapshot, entity: str, field: str, dims: list[Dimension]) -> Dimension | None:
    """The plain dimension the person can see on a field (no bands, not a grouping of another)."""
    return next((d for d in dims if d.entity == entity and d.field == field and d.of is None and not d.bands), None)


def _rule_text(snap: ModelSnapshot, filters: list[dict[str, Any]]) -> str:
    """"Current state in Project Created, Active, On Hold"."""
    out = []
    for f in filters:
        d = snap.dimension(ref_key(f["ref"]))
        labels = dict(d.value_labels) if d is not None else {}
        verb = "not in" if f["op"] in ("!=", "not_in") else "in"
        listed = ", ".join(str(labels.get(v, v)) for v in f["values"])
        out.append(f"{d.label if d else ref_key(f['ref'])} {verb} {listed}")
    return "; ".join(out)


def _plannable(plan: Plan) -> bool:
    spec = plan.spec
    return plan.status == "ok" and spec is not None and not plan.freehand and \
        not (spec.get("measure") or {}).get("adhoc") and not any(spec.get(k) for k in UNSUPPORTED_SPEC)


def _plan(spec: dict[str, Any], counted: CountedBy, base: dict[str, Any], plan: Plan,
          snap: ModelSnapshot) -> Plan:
    return Plan("ok", spec=spec, chips=chips_for(spec, snap), measures_considered=plan.measures_considered,
                interpreted=[counted.text, *(i for i in plan.interpreted if not i.startswith("Counted by"))],
                beside=counted.beside(base), counted_by=counted)


# --------------------------------------------------------------------------- the routes


def from_question(plan: Plan, question: str, snap: ModelSnapshot, measures: list[Measure],
                  values: dict[str, list[str]] | None) -> Plan | None:
    """The plan counted by the question's own criteria on the rule's field, or None to keep the rules' plan."""
    if not _plannable(plan):
        return None
    assert plan.spec is not None
    m = snap.measure(ref_key(plan.spec["measure"]["ref"]))
    parts = rule_parts(m) if m is not None else []
    if m is None or not parts:
        return None
    measure_words = confidence._own(m)
    left_out = confidence.listed_values(norm(question), plan.spec, snap, values, measure_words)
    full = confidence.with_listed(plan.spec, left_out, snap, measures)[0] if left_out else copy.deepcopy(plan.spec)
    replaced: set[int] = set()
    stated: list[dict[str, Any]] = []
    disagrees = False
    for f in full.get("filters", []):
        d = snap.dimension(ref_key(f["ref"]))
        if d is None or d.entity != m.entity or d.of is not None or d.bands or f.get("op") not in ("=", "in"):
            continue
        hits = [i for i, p in enumerate(parts) if (pf := _part_field(p)) is not None
                and same_field(snap, m.entity, pf, d.field)]
        if not hits:
            continue
        replaced |= set(hits)
        stated.append(f)
        disagrees = disagrees or any(not _allows(parts[i], str(v)) for i in hits for v in f.get("values", []))
    if not stated or not disagrees:
        return None  # no criteria on the rule's field, or they only narrow it ("open projects that are active")
    base = base_measure(m, [p for i, p in enumerate(parts) if i not in replaced], measures)
    if base is None:
        return None
    spec = _swap_measure(full, base, snap)
    certified = copy.deepcopy(plan.spec)
    stated_refs = {f["ref"] for f in stated}
    certified["filters"] = [f for f in certified.get("filters", []) if f["ref"] not in stated_refs]
    return _plan(spec, CountedBy("question", m, _rule_text(snap, stated), stated), certified, plan, snap)


def from_definition(plan: Plan, question: str, snap: ModelSnapshot, measures: list[Measure], dims: list[Dimension],
                    rows: list[dict[str, Any]]) -> Plan | None:
    """The plan counted by an approved definition the question uses (``rows``: approved, the person can see their
    field), or None: the rules planned the definition's certified measure, whose rule is replaced by the definition's
    condition. A definition with no measure replaces nothing; AG-03 plans its question with it first in the context.
    """
    for row in rows:
        rule = row.get("rule")
        if not row.get("expressible") or not rule or row.get("held") or rule.get("op") not in ("in", "not_in") or \
                not definitions.uses_term(question, row, snap):
            continue  # held (US-252): it disagrees with the certified measure and the Admin hasn't chosen
        d = dimension_for(snap, rule["entity"], rule["field"], dims)
        if d is None:
            continue
        stated = {"ref": f"dimension:{d.key}", "op": rule["op"], "values": list(rule["values"])}
        m = snap.measure(ref_key(row["measure_ref"])) if row.get("measure_ref") else None
        if m is None or not _plannable(plan) or plan.spec is None or \
                ref_key(plan.spec["measure"]["ref"]) != m.key or definitions.same_rule(m, rule):
            continue
        base = base_measure(m, [], measures)
        if base is None:
            continue
        spec = _swap_measure(plan.spec, base, snap)
        spec["filters"] = [f for f in spec.get("filters", []) if not (
            (fd := snap.dimension(ref_key(f["ref"]))) is not None and fd.entity == rule["entity"]
            and same_field(snap, rule["entity"], fd.field, rule["field"]))] + [stated]
        counted = CountedBy("definition", m, _rule_text(snap, [stated]), [stated], row)
        return _plan(spec, counted, copy.deepcopy(plan.spec), plan, snap)
    return None


def check(led: Plan, question: str, snap: ModelSnapshot, measures: list[Measure], dims: list[Dimension],
          linked: list[Any], ranked: list[tuple[float, str, str]] | None, values: dict[str, list[str]] | None) -> None:
    """The route's plan checked as a rules plan is (US-193): the replaced measure is out of the running (the question
    names it on purpose), as is a measure named only by a stated value ("On Hold" is a listed state, not Projects on
    hold), and a definition's condition is read as said (the question used its term)."""
    counted: CountedBy = led.counted_by
    key = counted.measure.key
    said = {w for f in counted.filters for v in f.get("values", []) for w in words(str(v))}
    tables = {e.key: set(words(e.label)) for e in snap.entities}
    by_value = {m.key for m in measures if (own := confidence._own(m) - confidence._forms(tables.get(m.entity, set())))
                and {w for w in own if w not in STOP} <= confidence._forms(said)}
    measures = [m for m in measures if m.key not in by_value]
    # A definition is what the question's term means: the question is checked as read with it.
    said_as = f"{question} ({counted.rule})" if counted.source == "definition" else question
    confidence.check_rules(led, said_as, snap, [m for m in measures if m.key != key], dims, linked,
                           [r for r in ranked or [] if r[1] != key], values)


def unapplied(rows: list[dict[str, Any]], question: str, snap: ModelSnapshot) -> list[dict[str, Any]]:
    """Approved definitions the question uses that this route can't count by (text only): AG-03 plans with them."""
    return [r for r in rows if not r.get("expressible") and definitions.uses_term(question, r, snap)]


def conflicting(plan: Plan, rows: list[dict[str, Any]], question: str, snap: ModelSnapshot) -> dict[str, Any] | None:
    """US-205: an approved definition of a term in the question that disagrees with the measure the plan answers by
    (``checks.conflict`` at approval), when the plan was not counted by it."""
    if plan.status != "ok" or plan.spec is None or plan.counted_by is not None:
        return None
    key = ref_key(plan.spec["measure"]["ref"])
    return next((r for r in rows if (r.get("checks") or {}).get("conflict") and r.get("measure_ref") == f"measure:{key}"
                 and definitions.uses_term(question, r, snap)), None)
