"""Fields + definitions planner — Plan B (US-254, ENH-028 W4; FEAT-078 / FEAT-019).

Answers from the model's fields and the connection's approved business terms, so any combination of terms works
without a measure made for it. It starts from the question frame (US-253) and never matches the question against
the measure catalogue:

1. **Ground each slot separately** (``ground``): subject → entity; each condition → an approved named condition
   (US-252, the order of authority) or a field and a stored value (value linking, US-173) or a numeric field;
   groupings → dimensions or fields; time → a date field; the measure phrase → an aggregation and field (a count of
   records when there is none). Every slot gets a score and a reason (``named``, ``synonym``, ``meaning``,
   ``value``, ``definition``, ``chosen``, ``only``, ``default``).
2. **Compile one template per shape** (``compile_plan``): number, list_of_records, breakdown, ranking, trend,
   comparison, share — all on an ad-hoc measure (US-163) with ``field:`` / ``dimension:`` breakdowns, so the query
   service applies scope, hidden fields and limits as for every other answer. A shape with no template (a
   distribution, one record's details, a definition) is refused as such, never answered as another shape.
3. **An ungrounded slot** asks about that slot only ("Which field do you mean by “region”?"), or, with nothing to
   offer, refuses naming it. The person's choice is kept in the conversation's memory under
   ``fields:<slot>:<phrase>``.
4. **Certified means approved** (W5): certified only when every slot was read from approved knowledge or a stored
   value, and the measure part is a certified measure (its count or total with no rule of its own, or one whose
   rule is exactly the conditions used). Otherwise the answer carries "not certified" with its rule.

Pure functions; the orchestrator (``Orchestrator._fields_plan``) loads the frame, the approved conditions and the
linked values and records the grounding as a step of the agent run.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from dataclasses import field as dc_field
from typing import Any

from tela_api.ask import confidence, verify
from tela_api.ask.frame import Condition, QuestionFrame
from tela_api.ask.freehand import (
    DATE_TYPES,
    NUMBER_TYPES,
    FreehandUnsupported,
    _number,
    plain_rule,
    resolve_field,
)
from tela_api.ask.planner import GRAMMAR, LinkedValue, Plan, compare_words, match_dimension, norm, words
from tela_api.ask.refine import chips_for
from tela_api.knowledge import conditions as named
from tela_api.knowledge.definitions import singular, term_key
from tela_api.knowledge.scoring import is_record_ref
from tela_query_spec.expr import AggExpr
from tela_query_spec.model import Dimension, Measure, ModelEntity, ModelField, ModelSnapshot
from tela_query_spec.spec import Relative

TEMPLATES = ("number", "list_of_records", "breakdown", "ranking", "trend", "comparison", "share")
NOT_YET = {"distribution": "a spread of values", "record_detail": "everything about one record",
           "definition": "what a term means"}
MSG_SHAPE = "Tela can't answer this kind of question yet: {kind}. Ask for a number, a list, a breakdown, a ranking " \
            "or a trend."
RELATIVE: frozenset[str] = frozenset(Relative.__args__)
COMPARE_KINDS = ("previous_period", "same_period_last_year")
RANK_LIMIT = 10
MAX_OPTIONS = 4
# How a slot was read, and whether that reading can carry a certified answer (W5).
CERTAIN = frozenset({"named", "synonym", "value", "definition", "chosen", "only", "default"})
SCORES = {"named": 1.0, "definition": 1.0, "chosen": 1.0, "default": 1.0, "synonym": 0.95, "value": 1.0,
          "partial": 0.8, "only": 0.8, "spelling": 0.7, "meaning": 0.6}
AGE_UNITS = {"day": 1, "week": 7, "month": 30, "year": 365}
AGE_OPS = {">": "<", ">=": "<=", "<": ">", "<=": ">="}  # older than N days: the date is before today - N
LINK_HOW = {"exact": "value", "synonym": "synonym", "partial": "partial", "spelling": "spelling", "meaning": "meaning"}
AGG_WORDS = {"total": "sum", "sum": "sum", "overall": "sum", "average": "avg", "avg": "avg", "mean": "avg",
             "minimum": "min", "min": "min", "lowest": "min", "smallest": "min", "earliest": "min",
             "maximum": "max", "max": "max", "highest": "max", "largest": "max", "latest": "max"}
COUNT_WORDS = frozenset({"number", "count", "how", "many"})
# Words that carry no condition of their own ("projects on hold right now", "in total"): read past, never refused.
NOISE = frozenset({"right", "now", "currently", "current", "presently", "present", "still", "yet", "ever", "there",
                   "total", "overall", "all", "really", "today", "at", "moment", "so", "far", "any", "some", "our",
                   "us", "we", "do", "does", "did", "have", "has", "had", "need", "needs", "needing", "come",
                   "comes", "came", "coming"})
SOURCE_WORDS = {"definition_sheet": "business-context sheet", "document": "approved document",
                "pack_measure": "certified measure", "value": "stored value", "field": "field", "chosen": "your choice"}


@dataclass
class Slot:
    """One frame slot read against the model: what it became (``ref``), how sure (``score``) and why."""

    slot: str  # subject | measure | condition | grouping | time | order
    phrase: str
    ref: str | None
    score: float
    how: str
    reason: str
    source: str | None = None  # a condition's origin: definition_sheet | document | pack_measure | value | field

    def public(self) -> dict[str, Any]:
        out = {"slot": self.slot, "phrase": self.phrase, "ref": self.ref, "score": round(self.score, 3),
               "how": self.how, "reason": self.reason}
        return {**out, "source": self.source} if self.source else out


class Ungrounded(Exception):
    """A slot Ask could not read: it asks about that slot (``options``: (value, label)) or refuses naming it."""

    def __init__(self, slot: str, phrase: str, question: str, options: list[tuple[str, str]] | None = None) -> None:
        super().__init__(question)
        self.slot, self.phrase, self.question, self.options = slot, phrase, question, options or []


@dataclass
class Context:
    snap: ModelSnapshot
    visible: dict[str, set[str]]  # entity → field keys the person may see
    dims: list[Dimension]  # dimensions the person may see
    measures: list[Measure]  # certified measures the person may see
    conditions: list[dict[str, Any]] = dc_field(default_factory=list)  # approved named conditions they may see
    linked: list[LinkedValue] = dc_field(default_factory=list)  # values the question names, in their scope
    memory: dict[str, str] = dc_field(default_factory=dict)  # the conversation's choices (``fields:<slot>:<phrase>``)
    question: str = ""  # as planned (a comparison's kind: "compared with last month")


@dataclass
class Grounding:
    entity: ModelEntity
    agg: str = "count"
    field: str | None = None
    where: list[dict[str, Any]] = dc_field(default_factory=list)
    breakdowns: list[str] = dc_field(default_factory=list)
    time_ref: str | None = None
    slots: list[Slot] = dc_field(default_factory=list)
    used: list[dict[str, Any]] = dc_field(default_factory=list)  # conditions for "How this was answered"
    unread: list[str] = dc_field(default_factory=list)  # words of the question nothing read
    as_time: list[str] = dc_field(default_factory=list)  # groupings read as the time slot's date


# --------------------------------------------------------------------------- helpers


def _key(slot: str, phrase: str) -> str:
    return f"fields:{slot}:{' '.join(words(phrase))}"[:120]


def _stems(text: str | None) -> set[str]:
    return {singular(w) for w in words(text or "")}


def _fields(ctx: Context, ent: ModelEntity, types: tuple[str, ...] | None = None) -> list[ModelField]:
    keys = ctx.visible.get(ent.key, set())
    return [f for f in ent.fields if f.key in keys and f.is_queryable and (types is None or f.data_type in types)]


def _entity_words(e: ModelEntity) -> set[str]:
    return _stems(e.label) | _stems(e.key.replace("_", " "))


def _entities(ctx: Context) -> list[ModelEntity]:
    return [e for e in ctx.snap.entities if e.key in ctx.visible]


def _names_entity(phrase: str | None, ctx: Context) -> ModelEntity | None:
    """The table a phrase names: every word of its label, or its last word ("projects", "project requests")."""
    ws = _stems(phrase)
    if not ws:
        return None
    full = [e for e in _entities(ctx) if _stems(e.label) and _stems(e.label) <= ws]
    if full:
        return max(full, key=lambda e: len(_stems(e.label)))
    last = [e for e in _entities(ctx) if singular(words(e.label)[-1]) in ws] if ws else []
    return last[0] if len(last) == 1 else None


def _reach(ctx: Context, ent: ModelEntity) -> list[Dimension]:
    return [d for d in ctx.dims if d.entity == ent.key or ctx.snap.join_path(ent.key, d.entity) is not None]


def _field_ref(ent: ModelEntity, d: Dimension) -> str:
    """A dimension's field as an ad-hoc condition field: its own key, or ``entity.field`` on a linked table."""
    return d.field if d.entity == ent.key else f"{d.entity}.{d.field}"


def resolve(phrase: str, fields: list[ModelField]) -> ModelField:
    """The field a phrase names: its label word for word (without a parenthesis: "forecast cost" is "Forecast cost
    (EAC, USD)", not "Forecast cost: internal") or its key, else the free-hand reading (raises when it can't tell)."""
    said = " ".join(words(phrase))
    exact = [f for f in fields if " ".join(words(re.sub(r"\([^)]*\)", " ", f.label))) == said
             or f.key.replace("_", " ") == said]
    if len(exact) == 1:
        return exact[0]
    return resolve_field(phrase, fields)


def _chosen(ctx: Context, slot: str, phrase: str) -> str | None:
    return ctx.memory.get(_key(slot, phrase))


def _ref_field(ref: str, ctx: Context) -> tuple[str, ModelField] | None:
    if ref.startswith("dimension:"):
        d = ctx.snap.dimension(ref.split(":", 1)[1])
        ent = ctx.snap.entity(d.entity) if d else None
        f = ent.field(d.field) if ent and d else None
        return (d.entity, f) if d and f else None
    if ref.startswith("field:"):
        e_key, f_key = ref.split(":", 1)[1].split(".", 1)
        ent = ctx.snap.entity(e_key)
        f = ent.field(f_key) if ent else None
        return (e_key, f) if f else None
    return None


# --------------------------------------------------------------------------- grounding


def ground_subject(frame: QuestionFrame, ctx: Context) -> tuple[ModelEntity, Slot, list[str]]:
    """The table the question counts or lists, and any grouping hidden in the subject ("which region has the most
    projects": region is the grouping, projects the table)."""
    phrase = frame.subject or ""
    extra: list[str] = []
    if (chosen := _chosen(ctx, "subject", phrase)) and (e := ctx.snap.entity(chosen.removeprefix("entity:"))):
        return e, Slot("subject", phrase, f"entity:{e.key}", 1.0, "chosen", f"You chose {e.label}"), extra
    ent = _names_entity(phrase, ctx)
    if ent is not None:
        return ent, Slot("subject", phrase, f"entity:{ent.key}", 1.0, "named", f"“{phrase}” names {ent.label}"), extra
    for other in (frame.order.by if frame.order else None, frame.measure_phrase):
        if (ent := _names_entity(other, ctx)) is not None:
            if phrase:
                extra.append(phrase)  # the subject was the grouping
            return ent, Slot("subject", other or "", f"entity:{ent.key}", 1.0, "named",
                             f"“{other}” names {ent.label}"), extra
    tables = {d.entity for v in ctx.linked if (d := ctx.snap.dimension(v.dimension)) is not None}
    tables |= {r["entity"] for r in ctx.conditions if any(named.said(c.phrase, r) for c in frame.conditions)}
    if len(tables) == 1 and (ent := ctx.snap.entity(next(iter(tables)))) is not None and ent.key in ctx.visible:
        return ent, Slot("subject", phrase, f"entity:{ent.key}", SCORES["partial"], "partial",
                         f"The values asked for are {ent.label}"), extra
    if len(_entities(ctx)) == 1:
        ent = _entities(ctx)[0]
        return ent, Slot("subject", phrase, f"entity:{ent.key}", SCORES["only"], "only",
                         f"{ent.label} is the only table"), extra
    options = [(f"entity:{e.key}", e.label) for e in _entities(ctx)]
    said = f" by “{phrase}”" if phrase else ""
    raise Ungrounded("subject", phrase, f"Which records do you mean{said}?", options)


def ground_measure(frame: QuestionFrame, ent: ModelEntity, ctx: Context) -> tuple[str, str | None, Slot]:
    """The aggregation and field: "total budget" → sum of Approved budget; no measure phrase → a count."""
    phrase = frame.measure_phrase or ""
    if frame.shape == "ranking" and not phrase and frame.order and frame.order.by and \
            _names_entity(frame.order.by, ctx) is None:
        phrase = frame.order.by  # "top 5 projects by cost"
    ws = [w for w in words(phrase) if w not in ("of", "the")]
    if not ws or set(ws) <= COUNT_WORDS or _names_entity(phrase, ctx) is ent:
        return "count", None, Slot("measure", phrase, None, 1.0, "default", f"Count of {ent.label.lower()}")
    agg = AGG_WORDS.get(ws[0])
    rest = " ".join(ws[1:] if agg else ws)
    if agg is None and frame.order and frame.order.by and frame.shape == "ranking":
        agg = "sum"
    agg = agg or "sum"
    if not rest or _names_entity(rest, ctx) is ent:
        return "count", None, Slot("measure", phrase, None, 1.0, "default", f"Count of {ent.label.lower()}")
    numeric = _fields(ctx, ent, (*NUMBER_TYPES, *DATE_TYPES) if agg in ("min", "max") else NUMBER_TYPES)
    if (chosen := _chosen(ctx, "measure", rest)) and (got := _ref_field(chosen, ctx)) and got[0] == ent.key:
        return agg, got[1].key, Slot("measure", phrase, chosen, 1.0, "chosen", f"You chose {got[1].label}")
    try:
        f = resolve(rest, numeric)
    except FreehandUnsupported:
        options = [(f"field:{ent.key}.{f.key}", f.label) for f in numeric]
        raise Ungrounded("measure", rest, f"Which field do you mean by “{rest}”?", options) from None
    return agg, f.key, Slot("measure", phrase, f"field:{ent.key}.{f.key}", 1.0, "named",
                            f"{agg} of {f.label}")


def _named_match(text: str, subject: str | None, ent: ModelEntity, ctx: Context) -> tuple[dict[str, Any], str] | None:
    """The approved named condition a condition's words name: its term or a synonym, with or without the table's
    words ("open" or "open projects" → Open projects)."""
    said = {term_key(text), term_key(f"{text} {subject or ''}")}
    for row in ctx.conditions:
        if row["entity"] != ent.key:
            continue
        for i, name in enumerate([row["term"], *(row.get("synonyms") or [])]):
            keys = {term_key(name), term_key(named.qualifier(name, ent.key, ctx.snap))}
            if said & keys:
                return row, "definition" if i == 0 else "synonym"
    return None


def _named_condition(row: dict[str, Any], ctx: Context) -> tuple[dict[str, Any], str]:
    """The rule a named condition answers with, by the order of authority (US-252): the condition, or its certified
    measure's rule while a conflict is unsettled or the Admin chose the measure."""
    if named.decide(row) == "measure" and row.get("measure_ref"):
        m = ctx.snap.measure(row["measure_ref"].split(":", 1)[1])
        if m is not None and (cond := named.from_measure(m)) is not None:
            return cond, "pack_measure"
    return row["condition"], row["source"]


def _negate(cond: dict[str, Any]) -> dict[str, Any]:
    op = cond.get("op")
    if op in ("=", "!=") and "value" in cond and len(cond) == 3:
        return {**cond, "op": "!=" if op == "=" else "="}
    if op in ("in", "not_in") and "values" in cond and len(cond) == 3:
        return {**cond, "op": "not_in" if op == "in" else "in"}
    return {"not": cond}


def _value_condition(links: list[LinkedValue], ent: ModelEntity, ctx: Context, negated: bool) -> dict[str, Any] | None:
    d = ctx.snap.dimension(links[0].dimension)
    if d is None:
        return None
    stored = list(dict.fromkeys(v.stored for v in links))
    key = _field_ref(ent, d)
    cond: dict[str, Any] = {"field": key, "op": "=", "value": stored[0]} if len(stored) == 1 else \
        {"field": key, "op": "in", "values": stored}
    return _negate(cond) if negated else cond


Read = tuple[dict[str, Any], Slot, dict[str, Any]]  # a condition, its slot, and what "How this was answered" shows


def _named_read(row: dict[str, Any], how: str, phrase: str, said: str, negated: bool, ent: ModelEntity,
                ctx: Context) -> Read:
    cond, source = _named_condition(row, ctx)
    cond = _negate(cond) if negated else cond
    note = " (it differs from the certified measure; the measure's rule is used until an Admin chooses)" \
        if named.held(row) else ""
    slot = Slot("condition", phrase, f"condition:{row['term_key']}", SCORES[how], how,
                f"“{said}” is the approved term {row['term']}{note}", source)
    return cond, slot, {"phrase": phrase, "term": row["term"], "source": source,
                        "rule": named.rule_text(cond, ent.key, ctx.snap)}


def _value_read(links: list[LinkedValue], phrase: str, negated: bool, ent: ModelEntity, ctx: Context) -> Read | None:
    cond = _value_condition(links, ent, ctx, negated)
    if cond is None:
        return None
    best = max(links, key=lambda v: v.score)
    how = LINK_HOW.get(best.how, "meaning")
    d = ctx.snap.dimension(links[0].dimension)
    slot = Slot("condition", phrase, f"dimension:{links[0].dimension}", min(best.score, SCORES.get(how, 1.0)), how,
                f"“{best.phrase}” is the {d.label if d else links[0].dimension} value "
                f"{', '.join(dict.fromkeys(v.label for v in links))}", "value")
    return cond, slot, {"phrase": phrase, "source": "value", "rule": named.rule_text(cond, ent.key, ctx.snap)}


def _links_for(span: set[str], ent: ModelEntity, ctx: Context, taken: set[int], around: set[str]) -> list[int]:
    """The untaken linked values a run of words names (its words, or part of a value's: "hold" of "on hold"), on
    one dimension: the one whose name the words ``around`` it say ("Red health" → Calculated health), else the
    entity's own before a linked table's ("critical" requests by their own priority)."""
    reach = {d.key: d for d in _reach(ctx, ent)}
    hits = [i for i, v in enumerate(ctx.linked) if i not in taken and v.dimension in reach and _stems(v.phrase)
            and (_stems(v.phrase) == span or span <= _stems(v.phrase))]
    if not hits:
        return []

    def rank(i: int) -> tuple[int, int, int]:
        d = reach[ctx.linked[i].dimension]
        return (-len(_stems(d.label) & around), 0 if d.entity == ent.key else 1, hits.index(i))

    dim = ctx.linked[min(hits, key=rank)].dimension
    return [i for i in hits if ctx.linked[i].dimension == dim]


def _numeric_read(c: Any, negated: bool, ent: ModelEntity, ctx: Context) -> Read:
    numeric = _fields(ctx, ent, (*NUMBER_TYPES, *DATE_TYPES))
    try:
        f = resolve(c.field_hint, numeric)
    except FreehandUnsupported:
        raise Ungrounded("condition", c.phrase, f"Tela couldn't find a field for “{c.field_hint}”.") from None
    if c.op == "between":
        lo, _, hi = (c.value or "").partition(" and ")
        a, b = _number(lo), _number(hi)
        if a is None or b is None:
            raise Ungrounded("condition", c.phrase, f"Tela couldn't read the numbers in “{c.phrase}”.")
        cond: dict[str, Any] = {"all": [{"field": f.key, "op": ">=", "value": a},
                                        {"field": f.key, "op": "<=", "value": b}]}
    else:
        v = _number(c.value or "")
        if v is None:
            raise Ungrounded("condition", c.phrase, f"Tela couldn't read the number in “{c.phrase}”.")
        cond = {"field": f.key, "op": c.op, "value": v}
        if f.data_type in DATE_TYPES:  # "on hold for more than 90 days": the date is over 90 days before today
            unit = next((n for w, n in AGE_UNITS.items() if re.search(rf"\b{w}s?\b", ctx.question)), None)
            if unit is None:
                raise Ungrounded("condition", c.phrase, f"Tela couldn't tell what {v} means for {f.label}: say days, "
                                 "weeks, months or years.")
            cond = {"field": f.key, "op": AGE_OPS[c.op], "value": {"today": True, "offset_days": -int(v * unit)}}
    cond = _negate(cond) if negated else cond
    slot = Slot("condition", c.phrase, f"field:{ent.key}.{f.key}", 1.0, "named", f"“{c.field_hint}” is {f.label}",
                "field")
    return cond, slot, {"phrase": c.phrase, "source": "field", "rule": named.rule_text(cond, ent.key, ctx.snap)}


def read_words(text: str, phrase: str, negated: bool, subject: str | None, ent: ModelEntity, ctx: Context,
               taken: set[int]) -> tuple[list[Read], list[str]]:
    """The conditions a run of words holds, longest pieces first: approved terms, then stored values. Returns them
    and the words nothing read (filler, and the name of a field whose value was read, already dropped)."""
    own = _entity_words(ent)
    ws = [w for w in words(text) if singular(w) not in own]  # "overdue action items": the table's own words
    around = _stems(" ".join(ws))
    out: list[Read] = []
    left: list[str] = []
    used_dims: set[str] = set()
    said: set[str] = set()  # phrases a value was read from: one phrase is one condition
    i = 0
    while i < len(ws):
        for n in range(len(ws) - i, 0, -1):
            span = " ".join(ws[i:i + n])
            if (hit := _named_match(span, subject, ent, ctx)) is not None:
                out.append(_named_read(*hit, phrase, span, negated, ent, ctx))
                break
            stems = _stems(span) - NOISE
            if stems and (got := _links_for(stems, ent, ctx, taken, around)):
                read = _value_read([ctx.linked[k] for k in got], phrase, negated, ent, ctx)
                if read is not None:
                    taken.update(got)
                    used_dims.add(ctx.linked[got[0]].dimension)
                    said.update(ctx.linked[k].phrase for k in got)
                    out.append(read)
                    break
        else:
            left.append(ws[i])
            n = 1
        i += n
    left = [w for w in left if w not in NOISE and w not in GRAMMAR]
    if left:  # "Yusuf Ali is the project manager": the field's name, its value read elsewhere in the question
        d = match_dimension(" ".join(left), _reach(ctx, ent))
        if d is not None and d.key not in used_dims and (got := [k for k, v in enumerate(ctx.linked)
                                                                 if k not in taken and v.dimension == d.key
                                                                 and v.phrase not in said]):
            read = _value_read([ctx.linked[k] for k in got], phrase, negated, ent, ctx)
            if read is not None:
                taken.update(got)
                out.append(read)
                used_dims.add(d.key)
        labels = {singular(w) for k in used_dims if (d := ctx.snap.dimension(k)) for w in words(d.label)}
        labels |= _rule_labels([cond for cond, _, _ in out], ent, ctx)
        left = [w for w in left if singular(w) not in labels]
    return out, left


def _rule_labels(conds: list[dict[str, Any]], ent: ModelEntity, ctx: Context) -> set[str]:
    """The words of what an approved term's rule reads (US-258: "insufficient data for calculated health" reads
    the calculated health, so those words are used): the labels of its fields, and of the entity's calculated
    dimensions when the rule reads a calculated value."""
    keys: set[str] = set()

    def walk(c: Any) -> None:
        if isinstance(c, dict):
            if isinstance(c.get("field"), str):
                keys.add(c["field"].split(".")[-1])
            for v in c.values():
                walk(v)
        elif isinstance(c, list):
            for v in c:
                walk(v)

    for c in conds:
        walk(c)
    out = {singular(w) for k in keys if (f := ent.field(k)) is not None for w in words(f.label)}
    if any(_calculated(c) for c in conds):
        out |= {singular(w) for d in _reach(ctx, ent) if d.computed for w in words(d.label)}
    return out


def ground_condition(c: Any, frame: QuestionFrame, ent: ModelEntity, ctx: Context, taken: set[int]) -> list[Read]:
    """One frame condition as rules on the entity: a numeric field ("budget over 1m"), else its words read as
    approved named conditions and stored values. A word nothing reads is never dropped silently: Ask refuses,
    naming it. ``taken``: linked values already used."""
    negated = c.negated or c.op in ("!=", "not_in")
    if c.op in (">", ">=", "<", "<=", "between") and c.field_hint:
        return [_numeric_read(c, negated, ent, ctx)]
    # the term as said, negation included ("not started" is a term of its own)
    if c.negated and (hit := _named_match(c.phrase, frame.subject, ent, ctx)) is not None:
        return [_named_read(*hit, c.phrase, c.phrase, False, ent, ctx)]
    if (hit := _named_match(c.phrase, frame.subject, ent, ctx)) is not None:  # "in delivery", "over budget"
        return [_named_read(*hit, c.phrase, c.phrase, negated, ent, ctx)]
    text = re.sub(r"^(?:not|non|no|without|excluding|except) ", "", c.value or c.phrase).strip()
    reads, left = read_words(text, c.phrase, negated, frame.subject, ent, ctx, taken)
    if left:
        raise Ungrounded("condition", " ".join(left), f"Tela couldn't tell what “{' '.join(left)}” means for "
                         f"{ent.label.lower()}: it is not an approved business term or a value it knows. Add it in "
                         "Admin → Knowledge, or name the field and value.")
    return reads


def ground_grouping(phrase: str, ent: ModelEntity, ctx: Context) -> tuple[str, Slot]:
    if (chosen := _chosen(ctx, "grouping", phrase)) and _ref_field(chosen, ctx) is not None:
        return chosen, Slot("grouping", phrase, chosen, 1.0, "chosen", "Your choice")
    reach = [d for d in _reach(ctx, ent) if not d.is_temporal]
    if (d := match_dimension(phrase, reach)) is not None:
        return f"dimension:{d.key}", Slot("grouping", phrase, f"dimension:{d.key}", 1.0, "named",
                                          f"“{phrase}” is {d.label}")
    try:
        f = resolve(phrase, _fields(ctx, ent, ("text",)))
    except FreehandUnsupported:
        options = [(f"dimension:{d.key}", d.label) for d in reach]
        raise Ungrounded("grouping", phrase, f"Which field do you mean by “{phrase}”?", options) from None
    ref = f"field:{ent.key}.{f.key}"
    return ref, Slot("grouping", phrase, ref, 1.0, "named", f"“{phrase}” is {f.label}")


def ground_time(frame: QuestionFrame, ent: ModelEntity, ctx: Context) -> tuple[str, Slot]:
    """The date field a time slot is about: the one it names ("created last month"), the entity's only date, or
    the person's choice."""
    t = frame.time
    assert t is not None
    hint = t.field_hint or ""
    if (chosen := _chosen(ctx, "time", hint or t.phrase)) and _ref_field(chosen, ctx) is not None:
        return chosen, Slot("time", t.phrase, chosen, 1.0, "chosen", "Your choice")
    dates = _fields(ctx, ent, DATE_TYPES)
    temporal = {d.field: d for d in ctx.dims if d.entity == ent.key and d.is_temporal and not d.computed}

    def ref(f: ModelField) -> str:
        return f"dimension:{temporal[f.key].key}" if f.key in temporal else f"field:{ent.key}.{f.key}"

    if hint:
        try:
            f = resolve(hint, dates)
            return ref(f), Slot("time", t.phrase, ref(f), 1.0, "named", f"“{hint}” is {f.label}")
        except FreehandUnsupported:
            pass
    candidates = [f for f in dates if f.key in temporal] or dates
    if len(candidates) == 1:
        f = candidates[0]
        return ref(f), Slot("time", t.phrase, ref(f), SCORES["only"], "only", f"{f.label} is the date of "
                            f"{ent.label.lower()}")
    options = [(ref(f), f.label) for f in candidates]
    raise Ungrounded("time", hint or t.phrase, f"Which date do you mean by “{hint or t.phrase}”?", options)


def ground(frame: QuestionFrame, ctx: Context) -> Grounding:
    """Every slot of the frame read against the model; raises ``Ungrounded`` on the first slot it can't read."""
    ent, subject, extra_groups = ground_subject(frame, ctx)
    g = Grounding(entity=ent, slots=[subject])
    g.agg, g.field, slot = ground_measure(frame, ent, ctx)
    g.slots.append(slot)
    taken: set[int] = set()
    for c in frame.conditions:
        for cond, slot, used in ground_condition(c, frame, ent, ctx, taken):
            g.where.append(cond)
            g.slots.append(slot)
            g.used.append({**used, "how": slot.how, "score": round(slot.score, 3)})
    slot_words = {singular(w) for s in g.slots for w in words(s.phrase)}
    unused = [w for w in frame.words_unused if w not in NOISE and w not in GRAMMAR and singular(w) not in slot_words]
    if unused:  # words the frame left: an approved term or a stored value among them is a condition too
        reads, g.unread = read_words(" ".join(unused), " ".join(unused), False, frame.subject, ent, ctx, taken)
        for cond, slot, used in reads:
            g.where.append(cond)
            g.slots.append(slot)
            g.used.append({**used, "how": slot.how, "score": round(slot.score, 3)})
    groupings = [*extra_groups, *frame.groupings]
    if frame.time is not None and not frame.time.field_hint:
        # "projects by planned deployment date by month", "projects created by month": a date grouping, or a word
        # nothing else read that names a date field, is the time slot's date
        dates = _fields(ctx, ent, DATE_TYPES)
        for phrase in [p for p in (*groupings, " ".join(frame.words_unused)) if p]:
            try:
                resolve(phrase, dates)
            except FreehandUnsupported:
                continue
            frame = frame.model_copy(update={"time": frame.time.model_copy(update={"field_hint": phrase})})
            if phrase in groupings:
                groupings.remove(phrase)
                g.as_time.append(phrase)
            break
    for phrase in groupings:
        ref, slot = ground_grouping(phrase, ent, ctx)
        if ref not in g.breakdowns:
            g.breakdowns.append(ref)
        g.slots.append(slot)
    if frame.time is not None and (frame.time.grain or frame.time.range in RELATIVE or frame.shape == "comparison"):
        g.time_ref, slot = ground_time(frame, ent, ctx)
        g.slots.append(slot)
    elif frame.time is not None:
        raise Ungrounded("time", frame.time.phrase, f"Tela can't read the period “{frame.time.phrase}” yet. Try "
                         "this month, last quarter or this year.")
    # words read after all (US-258 asks about any left): the date the time slot follows ("created"), and the unit of
    # an age on a date ("more than 90 days")
    read = set(words(frame.time.field_hint or "")) if g.time_ref and frame.time is not None else set()
    if "offset_days" in str(g.where):
        read |= {w for w in g.unread if singular(w) in AGE_UNITS}
    names = _used_labels(g, ctx)
    g.unread = [w for w in g.unread if w not in read and not _names(w, names)]
    return g


def _names(word: str, labels: set[str]) -> bool:
    """A word that is a label word, or its verb ("manage" of Project manager, "deliver" of Delivery manager)."""
    w = singular(word)
    return w in labels or (len(w) >= 4 and any(lw.startswith(w) for lw in labels))


def _used_labels(g: Grounding, ctx: Context) -> set[str]:
    """The words of everything the answer reads: its table, the fields and dimensions of its slots (with their
    synonyms), the measure's field and the date the time slot follows, and what its approved terms' rules read."""
    out = _entity_words(g.entity)
    refs = [s.ref for s in g.slots if s.ref] + ([g.time_ref] if g.time_ref else [])
    if g.field:
        refs.append(f"field:{g.entity.key}.{g.field}")
    for ref in refs:
        if ref.startswith("dimension:") and (d := ctx.snap.dimension(ref.split(":", 1)[1])) is not None:
            out |= {singular(w) for text in (d.label, *d.synonyms) for w in words(text)}
        if (got := _ref_field(ref, ctx)) is not None:
            out |= {singular(w) for w in words(got[1].label)}
    return out | _rule_labels(g.where, g.entity, ctx)


# --------------------------------------------------------------------------- templates


def record_fields(ent: ModelEntity, ctx: Context) -> list[str]:
    """The fields that name one record: its name or title, then its number or id (so two records of one name stay
    two rows)."""
    keys = ctx.visible.get(ent.key, set())
    order = [*ent.display_fields, *(f.key for f in ent.fields)]
    out: list[str] = []
    for k in dict.fromkeys(order):
        f = ent.field(k)
        if f is None or k not in keys or not f.is_queryable or not is_record_ref(f"field:{ent.key}.{k}"):
            continue
        if f.semantic_type == "identifier" and k in ent.primary_key and out:
            continue
        out.append(k)
    named_first = sorted(out, key=lambda k: (0 if re.search(r"(?:name|title)$", k) else 1, out.index(k)))
    if not named_first:
        named_first = [k for k in ent.primary_key if k in keys][:1]
    return named_first[:2]


def _label(frame: QuestionFrame, g: Grounding, ctx: Context) -> str:
    lead = {"count": "", "sum": "Total {f} of ", "avg": "Average {f} of ", "min": "Lowest {f} of ",
            "max": "Highest {f} of "}[g.agg]
    f = g.entity.field(g.field) if g.field else None
    phrases = [c.phrase for c in frame.conditions]
    text = lead.format(f=f.label if f else "") + g.entity.label.lower()
    if phrases:
        text += f" ({', '.join(phrases)})"
    return str(text[:1].upper() + text[1:])[:120]


def _where(g: Grounding) -> dict[str, Any] | None:
    return g.where[0] if len(g.where) == 1 else ({"all": g.where} if g.where else None)


def certified_by(g: Grounding, ctx: Context) -> Measure | None:
    """The certified measure the answer's measure part is (W5): the same count or total with no rule of its own (the
    conditions are then filters on it, as on any certified answer), or one whose rule is exactly these
    conditions."""
    where = _where(g)
    base: Measure | None = None
    for m in ctx.measures:
        e = m.expression
        if m.entity != g.entity.key or not isinstance(e, AggExpr) or e.value is not None:
            continue
        agg = "count" if e.agg in ("count", "count_distinct") and g.agg == "count" else e.agg
        if agg != g.agg or (e.field or None) != (g.field or None):
            continue
        if where is not None and e.where is not None and named.same_rows(named.dump(e.where), where):
            return m  # the same rule exactly
        if e.where is None and base is None:
            base = m
    return base


def twin(g: Grounding, ctx: Context) -> Measure | None:
    """The certified measure that *is* the grounded measure part (US-255): same entity, aggregation, field and
    rule, nothing more and nothing less ("How many open projects" → Open projects; "How many projects" → Total
    projects). A measure that only carries the answer's conditions as filters is not one."""
    m = certified_by(g, ctx)
    if m is None or not isinstance(m.expression, AggExpr):
        return None
    where, rule = _where(g), m.expression.where
    if where is None and rule is None:
        return m
    return m if where is not None and rule is not None and named.same_rows(named.dump(rule), where) else None


def _records(shape: str, g: Grounding) -> bool:
    """One row per record: a list, or a ranking of records (no grouping)."""
    return shape == "list_of_records" or (shape == "ranking" and not g.breakdowns)


def _calculated(cond: Any) -> bool:
    """A rule that reads a calculated value (health, a ratio): ad-hoc rules compare fields with values only."""
    if isinstance(cond, dict):
        return "of" in cond or any(_calculated(v) for v in cond.values())
    if isinstance(cond, list):
        return any(_calculated(v) for v in cond)
    return False


def compile_plan(frame: QuestionFrame, g: Grounding, ctx: Context, *, hybrid: bool = False) -> Plan:
    """The shape's template over the grounded slots. ``hybrid`` (US-255): a measure part equal to a certified
    measure is that measure (certified, the same SQL as the Certified mode), except where the answer is one row per
    record; there the certified measure is the B answer's counterpart, run beside it by the gold suite."""
    ent = g.entity
    where = _where(g)
    # A term whose rule reads a calculated value (health, a ratio) can't be an ad-hoc rule: alone it answers through
    # its certified measure (the same rule), combined with other conditions it is refused by name.
    calc = certified_by(g, ctx) if _calculated(where) else None
    if _calculated(where) and (calc is None or calc.expression.where is None
                               or (frame.shape == "share" and not g.breakdowns)):
        terms = [u.get("term") or u["phrase"] for u, w in zip(g.used, g.where, strict=False) if _calculated(w)]
        return Plan("unsupported", reason=f"Tela can't combine “{', '.join(terms) or 'that term'}” with other "
                    "conditions yet: it reads a calculated value. Ask for it on its own.",
                    signals={"fields": {"shape": frame.shape, "calculated": terms}})
    adhoc: dict[str, Any] = {"entity": ent.key, "agg": g.agg, **({"field": g.field} if g.field else {}),
                             "label": _label(frame, g, ctx), "plain_rule": "-"}
    if where is not None:
        adhoc["where"] = where
    adhoc["plain_rule"] = plain_rule(adhoc, ctx.snap)
    measure = {"ref": f"measure:{calc.key}"} if calc else {"ref": "measure:_adhoc", "adhoc": adhoc}
    spec: dict[str, Any] = {"measure": measure, "breakdowns": [], "filters": []}
    breakdowns = list(g.breakdowns)
    shape = frame.shape
    order = frame.order
    if shape == "list_of_records" or (shape == "ranking" and not breakdowns):
        keys = record_fields(ent, ctx)
        if not keys:
            return Plan("unsupported", reason=f"Tela can't list {ent.label.lower()}: no field names a record.")
        breakdowns = [f"field:{ent.key}.{k}" for k in keys] + breakdowns
        if g.agg == "count" and where is not None:
            spec["having"] = {"op": ">", "value": 0}  # only the records the conditions select (EXEC-189)
        if shape == "list_of_records":
            spec["visual"] = {"type": "table"}
    if shape == "ranking":
        spec["sort"] = [{"by": "measure", "dir": (order.dir if order and order.dir else "desc")}]
        spec["limit"] = order.limit if order and order.limit else RANK_LIMIT
        if g.field is not None:  # records or groups without a value are not ranked (PostgreSQL sorts NULL first)
            spec["filters"] = [{"ref": f"field:{ent.key}.{g.field}", "op": "not_null", "values": []}]
    elif shape in ("breakdown", "share") and not breakdowns:
        value_dims = [s.ref for s in g.slots if s.slot == "condition" and s.source == "value" and s.ref]
        if shape == "share" and len(value_dims) == 1:
            # "what share of projects are in UAE": every region's share, UAE's among them
            i = next(i for i, s in enumerate([s for s in g.slots if s.slot == "condition"]) if s.ref == value_dims[0])
            g.where.pop(i)
            g.used.pop(i)
            breakdowns = value_dims
            spec["measure"]["adhoc"] = {**adhoc, **({"where": _where(g)} if g.where else {})}
            if not g.where:
                spec["measure"]["adhoc"].pop("where", None)
            spec["measure"]["adhoc"]["plain_rule"] = plain_rule(spec["measure"]["adhoc"], ctx.snap)
        else:
            return Plan("unsupported", reason="Say what to break it down by, for example “by region”.")
    if shape == "share":
        spec["share_of_total"] = [measure["ref"]]
    if shape == "trend":
        if g.time_ref is None or frame.time is None:
            return Plan("unsupported", reason="Say which date to follow over time.")
        spec["time"] = {"ref": g.time_ref, "grain": frame.time.grain or "month"}
    if g.time_ref is not None and frame.time is not None and frame.time.range in RELATIVE:
        spec.setdefault("time", {"ref": g.time_ref})["range"] = {"relative": frame.time.range}
    if shape == "comparison":
        kind = frame.time.range if frame.time else None
        kind = kind if kind in COMPARE_KINDS else compare_words(norm(ctx.question))[0]
        if kind in COMPARE_KINDS and g.time_ref is not None:
            spec["compare"] = {"kind": kind}
            spec.setdefault("time", {"ref": g.time_ref})
        elif not breakdowns:
            return Plan("unsupported", reason="Tela can't compare these yet: compare with the previous period or "
                                              "last year, or name the values to set side by side.")
    spec["breakdowns"] = [{"ref": r} for r in breakdowns[:3]]
    shortcut = counterpart = None
    if spec["measure"].get("adhoc") and (same := twin(g, ctx)) is not None:
        ref = f"measure:{same.key}"
        if hybrid and not _records(shape, g):
            shortcut = ref
            spec["measure"] = {"ref": ref}
            if "share_of_total" in spec:
                spec["share_of_total"] = [ref]
        elif not (shape == "ranking" and not g.breakdowns):  # a ranking's limit keeps it from equalling the count
            counterpart = ref
    chips = chips_for(spec, ctx.snap)
    for c in chips:
        if c.get("kind") == "breakdown" and str(c.get("ref", "")).startswith("field:"):
            got = _ref_field(c["ref"], ctx)
            c["label"] = got[1].label if got else c["label"]
    score = confidence.frame_score([s.score for s in g.slots], True)  # times the structural check, in ``plan``
    base = certified_by(g, ctx)
    certified = base is not None and all(s.how in CERTAIN for s in g.slots) and not g.unread
    why_not = None
    if base is None:
        why_not = "no certified measure counts or totals this the same way"
    elif g.unread:
        why_not = f"“{' '.join(g.unread)}” was not used"
    elif not certified:
        weak = next(s for s in g.slots if s.how not in CERTAIN)
        why_not = f"“{weak.phrase}” was matched by {weak.how}, not named"
    interpreted = [s.reason for s in g.slots if s.reason and s.how != "default"]
    if g.unread:
        interpreted.append(f"Not used: {' '.join(g.unread)}")
    if shortcut is not None and base is not None:
        interpreted.append(f"Answered with the certified measure {base.label}")
    signals = {"fields": {"slots": [s.public() for s in g.slots], "conditions_used": g.used,
                          "certified": certified, "certified_by": f"measure:{base.key}" if base else None,
                          "unread": g.unread,
                          "not_certified_because": why_not, "shape": shape,
                          **({"hybrid": shortcut} if shortcut else {}),
                          **({"counterpart": counterpart} if counterpart else {})}}
    return Plan("ok", spec=spec, chips=chips, measures_considered=[f"measure:{base.key}"] if base else [],
                interpreted=interpreted, confidence=round(score, 3), signals=signals)


def normalise(frame: QuestionFrame, ctx: Context) -> QuestionFrame:
    """A subject that names no table while the order does ("which region has the most open projects"): the table
    comes from the order, the subject becomes the grouping, and the order's other words its conditions."""
    if frame.subject and _names_entity(frame.subject, ctx) is None:
        # "overdue go-lives": the subject (with the words before it) is an approved term of a table
        plain = [c for c in frame.conditions if c.op is None and not c.negated and not c.field_hint]
        for k in range(len(plain), -1, -1):
            said = term_key(" ".join([*(c.phrase for c in plain[len(plain) - k:]), frame.subject]))
            row = next((r for r in ctx.conditions if r["entity"] in ctx.visible and said in
                        {term_key(n) for n in [r["term"], *(r.get("synonyms") or [])]}), None)
            if row is not None and (ent := ctx.snap.entity(row["entity"])) is not None:
                rest = [c for c in frame.conditions if c not in plain[len(plain) - k:]]
                return frame.model_copy(update={"subject": ent.label.lower(),
                                                "conditions": [*rest, Condition(phrase=row["term"])]})
    by = frame.order.by if frame.order else None
    if frame.order is None or by is None or _names_entity(frame.subject, ctx) is not None:
        return frame
    ent = _names_entity(by, ctx)
    if ent is None:
        return frame
    noun = _entity_words(ent)
    qualifier = " ".join(w for w in words(by) if singular(w) not in noun)
    table = " ".join(w for w in words(by) if singular(w) in noun)
    conditions = [*frame.conditions, *([Condition(phrase=qualifier)] if qualifier else [])]
    groupings = [*([frame.subject] if frame.subject else []), *frame.groupings]
    return frame.model_copy(update={"subject": table, "groupings": groupings, "conditions": conditions,
                                    "order": frame.order.model_copy(update={"by": None})})


def plan(frame: QuestionFrame, ctx: Context, *, hybrid: bool = False) -> Plan:
    """Plan B (or Hybrid, US-255) for one question frame: a plan, a question about one slot, or a refusal naming what
    is missing."""
    frame = normalise(frame, ctx)
    if (chose := _chosen(ctx, "shape", frame.subject or "")) and chose.removeprefix("shape:") in TEMPLATES:
        frame = frame.model_copy(update={"shape": chose.removeprefix("shape:")})  # the kind they chose (US-258)
    if frame.shape not in TEMPLATES:
        return Plan("unsupported", reason=MSG_SHAPE.format(kind=NOT_YET.get(frame.shape, frame.shape)),
                    signals={"fields": {"shape": frame.shape, "template": None}})
    try:
        g = ground(frame, ctx)
    except Ungrounded as u:
        fields = {"shape": frame.shape, "ungrounded": {"slot": u.slot, "phrase": u.phrase}}
        if u.options and u.slot != "condition":
            options = [{"id": str(i + 1), "label": label, "value": value}
                       for i, (value, label) in enumerate(u.options[:MAX_OPTIONS])]
            return Plan("clarify", question={"question": u.question, "key": _key(u.slot, u.phrase), "kind": "field",
                                             "options": options}, signals={"fields": fields})
        return Plan("unsupported", reason=u.question, signals={"fields": fields})
    return checked(frame, g, ctx, compile_plan(frame, g, ctx, hybrid=hybrid))


def _check_key(f: verify.Failure, frame: QuestionFrame) -> str:
    return _key("shape", frame.subject or "") if f.slot == "shape" else _key(f"check_{f.slot}", f.phrase)


def checked(frame: QuestionFrame, g: Grounding, ctx: Context, p: Plan) -> Plan:
    """The structural check against the frame (US-258, ``ask.verify``): a plan that doesn't do what was asked is not
    certified and Ask asks about the failed slot — which kind of answer, or whether to answer without the words it
    couldn't use. A slot the person already said to leave out passes (the answer stays uncertified); a kind of
    answer they chose that still can't be given is refused. Confidence is the slot scores times the check."""
    if p.status != "ok" or p.spec is None:
        return p
    failures = verify.check(frame, p.spec, [s.public() for s in g.slots], unread=g.unread, as_time=g.as_time,
                            snap=ctx.snap, noise=NOISE | _entity_words(g.entity) | set(words(g.entity.label)))
    left_out = [f for f in failures if f.slot != "shape" and ctx.memory.get(_check_key(f, frame)) == verify.IGNORE]
    failing = [f for f in failures if f not in left_out]
    fields = (p.signals or {}).setdefault("fields", {})
    fields["check"] = {"passed": not failing, "failures": [f.public() for f in failing],
                       "left_out": [f.phrase for f in left_out]}
    p.confidence = confidence.frame_score([s.score for s in g.slots], not failing)
    if left_out:
        fields["certified"] = False
        fields["not_certified_because"] = f"“{left_out[0].phrase}” was left out, as you chose"
        p.interpreted = [*p.interpreted, *(f"Left out “{f.phrase}”, as you chose" for f in left_out)]
    if not failing:
        return p
    f = failing[0]
    fields["certified"] = False
    fields["not_certified_because"] = f.why
    key = _check_key(f, frame)
    if f.slot == "shape":
        if ctx.memory.get(key):  # they chose a kind of answer this template still can't give
            return Plan("unsupported", reason=f"Tela can't give {verify.SHAPE_WORDS.get(f.wanted or '', f.wanted)} "
                        "for this question yet.", signals=p.signals)
        options = [(f"shape:{s}", verify.SHAPE_WORDS.get(s, s or "")) for s in (f.wanted, f.got) if s in TEMPLATES]
        question = "Which kind of answer do you want?"
    else:
        options = [(verify.IGNORE, f"Answer without “{f.phrase}”")]
        question = f"Tela couldn't use “{f.phrase}” in the answer: {f.why}. Answer without it, or rephrase?"
    return Plan("clarify", question={"question": question, "key": key, "kind": "field", "why": [f.why],
                                     "options": [{"id": str(i + 1), "label": label[:1].upper() + label[1:],
                                                  "value": value} for i, (value, label) in enumerate(options)]},
                confidence=p.confidence, signals=p.signals, why=[f.why])


def measure_conditions(measures: list[Measure], rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The approved conditions, with the rule of every certified count measure whose term has none stored yet
    (the second authority, DEC-29): named conditions are written when knowledge is indexed (US-252), so a model
    approved since has its measures' terms only here, read exactly as ``conditions.from_measures`` would store them."""
    have = {r["term_key"] for r in rows}
    out = list(rows)
    for m in measures:
        cond = named.from_measure(m)
        term = named.measure_term(m)
        if cond is None or term_key(term) in have:
            continue
        have.add(term_key(term))
        out.append({"term": term, "term_key": term_key(term), "synonyms": list(m.synonyms), "entity": m.entity,
                    "condition": cond, "source": "pack_measure", "measure_ref": f"measure:{m.key}",
                    "status": "approved", "conflict": None, "prefer": None})
    return out


def unread(frame: QuestionFrame, ctx: Context) -> list[str]:
    """The condition words no approved term and no linked value reads yet: their values are looked up in the
    person's scoped value lists (the value index may not be filled yet, or may miss a new value)."""
    out = []
    ent = _names_entity(frame.subject, ctx)
    for c in frame.conditions:
        text = re.sub(r"^(?:not|non|no|without|excluding|except) ", "", c.value or c.phrase).strip()
        if c.op in (">", ">=", "<", "<=", "between") or (ent is not None and _named_match(text, frame.subject, ent,
                                                                                         ctx) is not None):
            continue
        cw = _stems(text)
        if not any(_stems(v.phrase) and (_stems(v.phrase) <= cw or cw <= _stems(v.phrase)) for v in ctx.linked):
            out.append(text)
    return out


def value_dimensions(frame: QuestionFrame, ctx: Context, limit: int = 12) -> list[Dimension]:
    """The text dimensions whose scoped values may hold an unread condition: the subject's table's first."""
    ent = _names_entity(frame.subject, ctx)
    reach = _reach(ctx, ent) if ent is not None else ctx.dims
    out = []
    for d in reach:
        e = ctx.snap.entity(d.entity)
        f = e.field(d.field) if e else None
        if d.is_temporal or d.computed or f is None or f.data_type != "text" or d.entity not in ctx.visible:
            continue
        out.append(d)
    return out[:limit]


def subjects(snap: ModelSnapshot) -> set[str]:
    """The nouns that name a table, for the rules' frame to pick the subject among the words."""
    out: set[str] = set()
    for e in snap.entities:
        out |= set(words(e.label)) | set(words(e.key.replace("_", " ")))
    return out


def public(p: Plan) -> dict[str, Any]:
    """What "How this was answered" shows of a Plan B answer: the conditions used and where each came from."""
    f = (p.signals or {}).get("fields") or {}
    return {"certified": bool(f.get("certified")), "certified_by": f.get("certified_by"),
            "not_certified_because": f.get("not_certified_because"),
            "conditions_used": [{**c, "source_label": SOURCE_WORDS.get(c.get("source") or "", c.get("source"))}
                                for c in f.get("conditions_used", [])]}
