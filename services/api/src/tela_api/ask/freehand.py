"""Free-hand planning (ENH-008 US-164; FEAT-060 / FEAT-018): a question's own rule over the model's fields.

Only for a person with a live ``ask.freehand`` grant on the connection. **Certified first**: when a certified
measure names the question, Ask uses it; a free-hand rule is built only when nothing certified fits, or beside the
certified answer when the person spells out another meaning ("past the Plan stage, *meaning* …", D1).

The rules here turn everyday words into the measure expression format (never SQL): the question names one table
("how many projects"), an aggregate ("total approved budget of …"), and conditions joined by *and*, each a field
phrase and what it must be ("is blank or before today", "at least one stage planned end date"). Approved glossary
terms are read first ("S2" → planning stage). Only fields the person can see are used; anything the words don't
resolve to one field (or a group of fields) is refused, never guessed. The result is an ``adhoc`` measure for the
query spec (US-163), whose validator applies the grant, hidden fields, scope and limits again.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from tela_api.ask.planner import STOP, Plan, _near, match_dimension, match_measures, span_of, words
from tela_api.governance.lookalike import describe
from tela_query_spec.expr import condition_fields
from tela_query_spec.model import Dimension, Measure, ModelEntity, ModelField, ModelSnapshot
from tela_query_spec.spec import AdhocMeasure

# "… past the Plan stage, meaning …": the person gives their own rule for a name (D1).
RULE_CUE = re.compile(r"\b(?:meaning|i\s?e|that is to say|defined as|by which i mean|in other words|where by that i "
                      r"mean)\b")
# Words that start a condition on the records: "projects whose …", "requests that have …", "with …".
SUBJECT_CUE = re.compile(r"\b(?:whose|which|that|where|with|without|have|has|having|who)\b")
# Phrases only a rule uses; with free-hand access they mean the question is not a certified name alone.
CONDITION_CUE = re.compile(r"\b(?:blank|empty|not set|unset|missing|before today|after today|earlier than today|"
                           r"later than today|overdue|past due|in the past|in the future|upcoming|at least one|"
                           r"one or more|none of|is set|are set|filled in|whose|without|on or before|on or after|"
                           r"more than \d|less than \d|fewer than \d|at least \d|at most \d)\b")
LEAD = re.compile(r"^(?:(?:how many|number of|count of|count|what is the|what are the|what s the|show me the|show me|"
                  r"show|list|give me|tell me|total|sum of|average|mean|avg|the|a|an|of|all|our|my)\s+)+")
FILLER = {"one", "least", "their", "its", "they", "it", "them", "have", "has", "had", "having", "been", "being",
          "does", "did", "not", "no", "field", "fields", "value", "values", "recorded"}
VERB = r"(?:(?:is|are|was|were|be|has been|have been|being|gets|get)\s+)?"
TODAY = {"today": True}
DATE_TYPES = ("date", "datetime")
NUMBER_TYPES = ("integer", "decimal")
MONTHS = {m: i + 1 for i, m in enumerate(("january", "february", "march", "april", "may", "june", "july", "august",
                                           "september", "october", "november", "december"))}


class FreehandUnsupported(Exception):
    """The words could not be turned into a rule; ``reason`` says what was missing, in plain words."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass
class Rule:
    """A free-hand rule: the ``adhoc`` measure, its breakdowns, chips, and what was read from the words."""

    adhoc: dict[str, Any]
    breakdowns: list[dict[str, str]] = field(default_factory=list)
    chips: list[dict[str, Any]] = field(default_factory=list)
    understood: list[str] = field(default_factory=list)

    def spec(self) -> dict[str, Any]:
        return {"measure": {"ref": "measure:_adhoc", "adhoc": self.adhoc}, "breakdowns": self.breakdowns,
                "filters": []}


# --------------------------------------------------------------------------- glossary


TERM_LINE = re.compile(r"^\s*(?:[-*•]\s*)?\**([A-Za-z0-9][\w&/.-]*(?:\s[\w&/.-]+){0,3}?)\**\s*(?:means\s*:?|stands "
                       r"for|:|=|\u2014|\u2013)\s*(.+?)\s*$")


def glossary_terms(texts: list[str]) -> dict[str, str]:
    """Approved glossary terms ("S2 means: planning stage. …" or "S2: planning stage") → the short meaning, used to
    read the question (term → its words). The meaning is the text up to the first full stop or bracket."""
    out: dict[str, str] = {}
    for text in texts:
        for line in text.splitlines():
            m = TERM_LINE.match(line)
            if not m:
                continue
            term, meaning = m.group(1).strip().lower(), re.split(r"[.(;]", m.group(2), maxsplit=1)[0].strip()
            meaning = re.sub(r"^(?:the|a|an)\s+", "", meaning, flags=re.I).lower()
            if term and meaning and len(term.split()) <= 4 and len(meaning) <= 80 and term != meaning:
                out.setdefault(term, meaning)
    return out


def expand(text: str, terms: dict[str, str]) -> tuple[str, list[str]]:
    """The text with glossary terms replaced by their meaning; also what was read ("S2 read as planning stage")."""
    read: list[str] = []
    for term in sorted(terms, key=len, reverse=True):
        pattern = rf"(?<![\w-]){re.escape(term)}(?![\w-])"
        if re.search(pattern, text):
            text = re.sub(pattern, terms[term], text)
            read.append(f"{term.upper() if len(term) <= 4 else term} read as {terms[term]}")
        elif (spans := span_of(term, text)) is not None:  # US-216: "project will be active" says "active projects"
            (s, e), rest = spans[0], spans[1:]
            for rs, re_ in sorted(rest, reverse=True):
                text = text[:rs] + text[re_:]
            text = re.sub(r"\s{2,}", " ", text[:s] + terms[term] + text[e:])
            read.append(f"{term} read as {terms[term]}")
    return text, read


# --------------------------------------------------------------------------- fields


def _field_words(f: ModelField) -> set[str]:
    return set(words(f.label)) | set(words(f.key.replace("_", " ")))


def _phrase_words(phrase: str) -> list[str]:
    return [w for w in words(phrase) if w not in STOP and w not in FILLER]


def _covers(word: str, fw: set[str], f: ModelField) -> bool:
    if word in ("date", "dates", "day", "when"):
        return f.data_type in DATE_TYPES
    return any(_near(word, w) for w in fw)


def _fit(phrase_words: list[str], f: ModelField) -> tuple[float, float]:
    """(share of the phrase's words the field has, share of the field label's words the phrase has)."""
    fw = _field_words(f)
    if not phrase_words:
        return 0.0, 0.0
    phrase_cov = sum(1 for w in phrase_words if _covers(w, fw, f)) / len(phrase_words)
    label = [w for w in words(f.label) if w not in STOP]
    label_cov = sum(1 for w in label if any(_near(w, p) for p in phrase_words)) / len(label) if label else 0.0
    return phrase_cov, label_cov


def resolve_field(phrase: str, fields: list[ModelField], *, types: tuple[str, ...] | None = None) -> ModelField:
    """The one field a phrase names ("s2 plan planned end date" after the glossary → Planning stage planned end)."""
    pw = _phrase_words(phrase)
    scored = []
    for f in fields:
        if types and f.data_type not in types:
            continue
        pc, lc = _fit(pw, f)
        if pc == 1.0 and lc >= 0.5:  # every word of the phrase is the field's: a near name is never a guess
            scored.append((pc + lc, f))
    scored.sort(key=lambda s: -s[0])
    if not scored:
        raise FreehandUnsupported(f"I couldn't find a field for “{phrase.strip()}”.")
    if len(scored) > 1 and abs(scored[0][0] - scored[1][0]) < 1e-9:
        raise FreehandUnsupported(f"“{phrase.strip()}” could be {scored[0][1].label} or {scored[1][1].label}. "
                                  "Name the field you mean.")
    return scored[0][1]


def resolve_group(phrase: str, fields: list[ModelField]) -> list[ModelField]:
    """Every field a group phrase names ("stage planned end date" → each stage's planned end)."""
    pw = _phrase_words(phrase)
    group = [f for f in fields if pw and _fit(pw, f)[0] == 1.0]
    if not group:
        raise FreehandUnsupported(f"I couldn't find the fields for “{phrase.strip()}”.")
    return group


# --------------------------------------------------------------------------- conditions


def _date_literal(text: str) -> str | None:
    if m := re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", text):
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3))).isoformat()
    if m := re.fullmatch(r"(\d{1,2}) ([a-z]+) (\d{4})", text):
        if m.group(2) in MONTHS:
            return date(int(m.group(3)), MONTHS[m.group(2)], int(m.group(1))).isoformat()
    if m := re.fullmatch(r"([a-z]+) (\d{1,2}),? (\d{4})", text):
        if m.group(1) in MONTHS:
            return date(int(m.group(3)), MONTHS[m.group(1)], int(m.group(2))).isoformat()
    return None


def _number(text: str) -> float | int | None:
    t = text.replace(",", "")
    mult = 1
    if m := re.fullmatch(r"(-?\d+(?:\.\d+)?)\s*(k|m|million|thousand)", t):
        t, mult = m.group(1), {"k": 1000, "thousand": 1000, "m": 1_000_000, "million": 1_000_000}[m.group(2)]
    try:
        v = float(t) * mult
    except ValueError:
        return None
    return int(v) if v.is_integer() else v


# A predicate: (pattern after the optional verb, builder(field key, match) → condition | None, field types).
Pred = tuple[str, Any, tuple[str, ...] | None]
PREDICATES: list[Pred] = [
    (r"(?:on or before|no later than) today", lambda k, m: {"field": k, "op": "<=", "value": TODAY}, DATE_TYPES),
    (r"(?:on or after|no earlier than) today|today or later", lambda k, m: {"field": k, "op": ">=", "value": TODAY},
     DATE_TYPES),
    (r"(?:before|earlier than|prior to|older than) today|overdue|past due|in the past|already passed|passed",
     lambda k, m: {"field": k, "op": "<", "value": TODAY}, DATE_TYPES),
    (r"(?:after|later than) today|upcoming|in the future|still to come|yet to come",
     lambda k, m: {"field": k, "op": ">", "value": TODAY}, DATE_TYPES),
    (r"today", lambda k, m: {"field": k, "op": "=", "value": TODAY}, DATE_TYPES),
    (r"(?:within|in) the next (\d{1,4}) days?",
     lambda k, m: {"all": [{"field": k, "op": ">=", "value": TODAY},
                           {"field": k, "op": "<=", "value": {"today": True, "offset_days": int(m.group(1))}}]},
     DATE_TYPES),
    (r"(?:within|in) the (?:last|past) (\d{1,4}) days?",
     lambda k, m: {"all": [{"field": k, "op": ">=", "value": {"today": True, "offset_days": -int(m.group(1))}},
                           {"field": k, "op": "<=", "value": TODAY}]}, DATE_TYPES),
    (r"more than (\d{1,4}) days? ago",
     lambda k, m: {"field": k, "op": "<", "value": {"today": True, "offset_days": -int(m.group(1))}}, DATE_TYPES),
    (r"(before|after|on or before|on or after|since|until|by) ([a-z0-9, -]+)",
     lambda k, m: (lambda d: None if d is None else {"field": k, "op": {
         "before": "<", "after": ">", "on or before": "<=", "on or after": ">=", "since": ">=", "until": "<=",
         "by": "<="}[m.group(1)], "value": d})(_date_literal(m.group(2).strip())), DATE_TYPES),
    (r"blank|empty|not set|unset|missing|null|not (?:filled|entered|recorded|given|known)(?: in)?|unknown",
     lambda k, m: {"field": k, "op": "is_null"}, None),
    (r"set|filled(?: in)?|recorded|entered|given|known|present|not blank|not empty|not null|not missing",
     lambda k, m: {"field": k, "op": "not_null"}, None),
    (r"(?:more than|greater than|over|above|higher than|bigger than|exceeding|exceeds?) (-?[\d,.]+\s*(?:k|m|million|"
     r"thousand)?)", lambda k, m: _num(k, ">", m), NUMBER_TYPES),
    (r"(?:at least|no less than|not less than) (-?[\d,.]+\s*(?:k|m|million|thousand)?)",
     lambda k, m: _num(k, ">=", m), NUMBER_TYPES),
    (r"(?:less than|fewer than|under|below|lower than|smaller than) (-?[\d,.]+\s*(?:k|m|million|thousand)?)",
     lambda k, m: _num(k, "<", m), NUMBER_TYPES),
    (r"(?:at most|no more than|not more than|up to) (-?[\d,.]+\s*(?:k|m|million|thousand)?)",
     lambda k, m: _num(k, "<=", m), NUMBER_TYPES),
    (r"(?:equal to|exactly|=) (-?[\d,.]+)", lambda k, m: _num(k, "=", m), NUMBER_TYPES),
]
# Where a predicate may start, each looked for on its own so overlapping readings are all tried.
PRED_STARTS = [re.compile(r"\b(?:is|are|was|were|has been|have been)\s+"),
               *(re.compile(rf"\b(?:{p})(?=$|\s|,)") for p, _, _ in PREDICATES)]
OR = re.compile(r"(?<!\bon )\bor\b")  # "blank or before today", not "on or before"


def _pred_starts(text: str) -> list[int]:
    return sorted({m.start() for r in PRED_STARTS for m in r.finditer(text) if m.start() > 0})


def _num(key: str, op: str, m: re.Match[str]) -> dict[str, Any] | None:
    v = _number(m.group(1).strip())
    return None if v is None else {"field": key, "op": op, "value": v}


def _predicate(text: str, f: ModelField, values: list[str]) -> dict[str, Any] | None:
    """One predicate on one field ("blank", "before today", "more than 5", "Active", "not one of A, B")."""
    t = re.sub(rf"^{VERB}", "", text.strip()).strip(" ,")
    for pattern, build, types in PREDICATES:
        if m := re.fullmatch(pattern, t):
            if types is not None and f.data_type not in types:
                continue
            return build(f.key, m)  # type: ignore[no-any-return]
    if f.data_type == "text" and values:
        neg = bool(re.match(r"not\s+", t))
        t = re.sub(r"^not\s+", "", t)
        t = re.sub(r"^(?:one of|any of|either|in)\s+", "", t)
        known = {v.lower(): v for v in values}
        parts = [p.strip() for p in re.split(r",|\bor\b|\band\b", t) if p.strip()]
        picked = [known[p] for p in parts if p in known]
        if parts and len(picked) == len(parts):
            if len(picked) == 1:
                return {"field": f.key, "op": "!=" if neg else "=", "value": picked[0]}
            return {"field": f.key, "op": "not_in" if neg else "in", "values": picked}
    return None


def _strip_lead(text: str) -> str:
    t = text.strip(" ,.?")
    while True:
        n = re.sub(r"^(?:whose|which|that|where|with|who|have|has|had|having|they|it|its|their|are|is|the|a|an|"
                   r"also|both|still|do|does)\s+", "", t)
        if n == t:
            return t
        t = n


def clause(text: str, fields: list[ModelField], values: dict[str, list[str]]) -> dict[str, Any]:
    """One condition: "[at least one of] <field phrase> [<predicate> [or <predicate>]]"."""
    t = _strip_lead(text)
    negate = False
    if m := re.match(r"(?:no|without|do not have|don t have|does not have|doesn t have|not)\s+(?:a|an|any)?\s*", t):
        negate, t = True, t[m.end():]
    quant = None
    if m := re.match(r"(?:at least one|one or more|any|some)\s+(?:of\s+)?(?:the\s+|their\s+|its\s+)?", t):
        quant, t = "any", t[m.end():]
    elif m := re.match(r"(?:all|every|each)\s+(?:of\s+)?(?:the\s+|their\s+|its\s+)?", t):
        quant, t = "all", t[m.end():]
    elif m := re.match(r"(?:none)\s+(?:of\s+)?(?:the\s+|their\s+|its\s+)?", t):
        quant, negate, t = "all", True, t[m.end():]
    # Try each place the predicate could start ("on hold since is set" is the field "on hold since" + "is set"),
    # then the whole text as a field phrase; the first reading that resolves wins.
    starts = [*_pred_starts(t), len(t)]
    first_error: FreehandUnsupported | None = None
    for at in dict.fromkeys(starts):
        try:
            return _read(t[:at], t[at:], quant, negate, fields, values)
        except FreehandUnsupported as exc:
            first_error = first_error or exc
    assert first_error is not None
    raise first_error


def _read(phrase: str, pred: str, quant: str | None, negate: bool, fields: list[ModelField],
          values: dict[str, list[str]]) -> dict[str, Any]:
    targets = resolve_group(phrase, fields) if quant is not None else [resolve_field(phrase, fields)]
    conds = []
    for f in targets:
        if not pred.strip():
            leaf: dict[str, Any] = {"field": f.key, "op": "is_null" if negate else "not_null"}
        else:
            alts = [p for p in OR.split(pred) if p.strip()]
            parsed: list[dict[str, Any]] = []
            for a in alts:
                cond = _predicate(a, f, values.get(f.key, []))
                if cond is None:
                    raise FreehandUnsupported(f"I couldn't read “{a.strip()}” as a condition on {f.label}.")
                parsed.append(cond)
            leaf = parsed[0] if len(parsed) == 1 else {"any": parsed}
            if negate:
                leaf = {"not": leaf}
        conds.append(leaf)
    if len(conds) == 1:
        return conds[0]
    if negate and not pred.strip():
        return {"all": conds}  # "none of the X": every one blank
    return {"any" if quant == "any" else "all": conds}


def _complete(piece: str) -> bool:
    """A piece of a rule that is a condition on its own: it has a predicate, a quantifier, or a having verb."""
    t = piece.strip()
    return bool(_pred_starts(" " + _strip_lead(t)) or re.match(
        r"(?:(?:that|which|who)\s+)?(?:have|has|had|having|with|without|no|at least|one or more|any|all|none)\b", t))


def _clauses(body: str) -> list[str]:
    """Split a rule into conditions at "and" / "but" / ";" — but not inside a field name: a piece that is not a
    condition on its own joins the next ("warranty and closure planned end is upcoming")."""
    out: list[str] = []
    pending = ""
    for p in re.split(r";|\b(?:and|but)\b", body):
        p = f"{pending} and {p}" if pending else p
        pending = ""
        if _complete(p) or not p.strip():
            out.append(p)
        else:
            pending = p
    if pending:
        out.append(pending)
    return [c for c in (o.strip(" ,.?") for o in out) if c]


# --------------------------------------------------------------------------- the question


AGGS = [
    (r"\b(?:total|sum of|sum)\s+(?:the\s+)?(.+?)\s+(?:of|for|across|on)\b", "sum"),
    (r"\b(?:average|mean|avg)\s+(?:the\s+)?(.+?)\s+(?:of|for|across|on)\b", "avg"),
    (r"\b(?:highest|maximum|max|largest|latest)\s+(?:the\s+)?(.+?)\s+(?:of|for|across|on)\b", "max"),
    (r"\b(?:lowest|minimum|min|smallest|earliest)\s+(?:the\s+)?(.+?)\s+(?:of|for|across|on)\b", "min"),
]


def _entity(head: str, snap: ModelSnapshot, entities: set[str]) -> ModelEntity:
    hw = words(head)
    found = []
    for e in snap.entities:
        if e.key not in entities:
            continue
        names = [w for w in words(e.label) if w not in ("project",)] or words(e.label)
        hits = [i for i, w in enumerate(hw) if any(_near(n, w) for n in names[-1:])]
        if hits:
            found.append((hits[0], -len(words(e.label)), e))
    if not found:
        if len(entities) == 1:
            only = snap.entity(next(iter(entities)))
            if only is not None:
                return only
        raise FreehandUnsupported("Say which records to count, for example projects or requests.")
    found.sort(key=lambda x: (x[0], x[1]))
    return found[0][2]


def _label(head: str, ent: ModelEntity) -> str:
    """A short name from the question's name part ("Projects past the plan stage"); just the table's name becomes
    "Projects matching your rule"."""
    t = re.sub(r"\([^)]*\)", " ", head)
    t = LEAD.sub("", t.strip(" ,.?").lower())
    t = re.sub(r"\b(?:are|is|were|was|there|do we have|we have|have|in any state|any state)\b", " ", t)
    t = re.sub(r"\b(?:by|per|for each|across|split by|grouped by)\s+.*$", " ", t)
    t = re.sub(r"\s+", " ", t).strip(" ,")
    if not t or all(any(_near(w, n) for n in words(ent.label)) for w in words(t)):
        return f"{ent.label} matching your rule"
    return (t[:1].upper() + t[1:])[:100]


def same_meaning(head: str, measures: list[Measure]) -> Measure | None:
    """A certified measure the question names in other words (D1): every word of its name (two or more) or of a
    synonym is in the question's name part ("past the Plan stage" → Past planning)."""
    hw = [w for w in words(head) if w not in STOP or w in ("past",)]
    best: tuple[int, Measure] | None = None
    for m in measures:
        for name in (m.label, *m.synonyms):
            tokens = [w for w in words(re.sub(r"\([^)]*\)", " ", name)) if w not in STOP or w == "past"]
            if len(tokens) >= 2 and all(any(_near(t, w) for w in hw) for t in tokens):
                if best is None or len(tokens) > best[0]:
                    best = (len(tokens), m)
    return best[1] if best else None


def split_question(question: str) -> tuple[str, str, bool]:
    """(name part, rule part, whether the person spelled the rule out with "meaning …")."""
    q = " ".join(re.findall(r"[a-z0-9]+(?:[-.][a-z0-9]+)*|[,;()]", question.lower()))
    q = re.sub(r"\s+([,;)])", r"\1", re.sub(r"\(\s+", "(", q))
    if m := RULE_CUE.search(q):
        head, body = q[:m.start()], q[m.end():]
        body = re.sub(r"^\s*,?\s*", "", body)
        return head, body, True
    m = SUBJECT_CUE.search(q)
    return (q[:m.start()], q[m.start():], False) if m else (q, "", False)


def build_rule(question: str, snap: ModelSnapshot, *, visible: dict[str, set[str]], dims: list[Dimension],
               glossary: dict[str, str] | None = None, values: dict[str, dict[str, list[str]]] | None = None,
               ) -> Rule:
    """A free-hand rule from the question's words. ``visible`` maps entity → the field keys the person can see;
    ``values`` maps entity → field → in-scope text values. Raises :class:`FreehandUnsupported`."""
    text, read = expand(question.lower(), glossary or {})
    head, body, _ = split_question(text)
    ent = _entity(head or body, snap, set(visible))
    fields = [f for f in ent.fields if f.key in visible.get(ent.key, set()) and f.is_queryable]
    understood = [*read, f"Counting {ent.label.lower()}"]
    agg, agg_field = "count", None
    for pattern, kind in AGGS:
        if m := re.search(pattern, head):
            f = resolve_field(m.group(1), fields, types=NUMBER_TYPES if kind in ("sum", "avg") else None)
            agg, agg_field = kind, f.key
            understood[-1] = f"{ {'sum': 'Total', 'avg': 'Average', 'max': 'Highest', 'min': 'Lowest'}[kind]} " \
                             f"{f.label} of {ent.label.lower()}"
            break
    body = re.sub(rf"^\s*{re.escape(words(ent.label)[-1])}\w*\s+", "", body.strip())  # "meaning projects that …"
    conds = [clause(c, fields, (values or {}).get(ent.key, {})) for c in _clauses(body)] if body.strip() else []
    if not conds and agg == "count":
        raise FreehandUnsupported("Say which records to count, for example “whose planned deployment date is blank”.")
    where: dict[str, Any] | None = conds[0] if len(conds) == 1 else ({"all": conds} if conds else None)
    if "any state" in text or "all states" in text or "any status" in text:
        understood.append("In any state")
    breakdowns: list[dict[str, str]] = []
    reach = [d for d in dims if not d.is_temporal and snap.join_path(ent.key, d.entity) is not None]
    for m in re.finditer(r"\b(?:by|per|for each|across|split by|grouped by)\s+([a-z0-9 ]+?)(?=$|,|\?|\b(?:whose|"
                         r"that|which|where|with|meaning|and)\b)", head):
        d = match_dimension(m.group(1), reach)
        if d is not None and len(breakdowns) < 2 and all(b["ref"] != f"dimension:{d.key}" for b in breakdowns):
            breakdowns.append({"ref": f"dimension:{d.key}"})
            understood.append(f"By {d.label}")
    adhoc: dict[str, Any] = {"entity": ent.key, "agg": agg, **({"field": agg_field} if agg_field else {}),
                             **({"where": where} if where else {}), "label": _label(head, ent),
                             "plain_rule": "-"}
    adhoc["plain_rule"] = plain_rule(adhoc, snap)
    try:
        AdhocMeasure.model_validate(adhoc)
    except ValidationError as exc:
        raise FreehandUnsupported("That rule is not one Tela can run.") from exc
    return Rule(adhoc=adhoc, breakdowns=breakdowns, chips=rule_chips(adhoc, snap), understood=understood)


# --------------------------------------------------------------------------- words, chips and checks


AGG_WORDS = {"count": "Count of", "count_distinct": "Distinct count of", "sum": "Total {field} of",
             "avg": "Average {field} of", "min": "Lowest {field} of", "max": "Highest {field} of"}


def plain_rule(adhoc: dict[str, Any], snap: ModelSnapshot) -> str:
    """The rule in words, worked out from the rule itself (never the planner's own reading)."""
    m = AdhocMeasure.model_validate({**adhoc, "plain_rule": adhoc.get("plain_rule") or "-"})
    ent = snap.entity(m.entity)
    f = ent.field(m.field) if ent and m.field and "." not in m.field else None
    lead = AGG_WORDS[m.agg].format(field=f.label if f else (m.field or ""))
    text = f"{lead} {ent.label.lower() if ent else m.entity}"
    if m.where is not None:
        text += f" where {describe(m.where, ent)}"
    return (text + ".")[:500]


def rule_chips(adhoc: dict[str, Any], snap: ModelSnapshot) -> list[dict[str, Any]]:
    """One chip per condition the person can change (field, condition, value); ``path`` locates it in the rule."""
    ent = snap.entity(adhoc["entity"])
    out: list[dict[str, Any]] = []

    def walk(node: dict[str, Any], path: list[int]) -> None:
        for key in ("all", "any"):
            if key in node:
                for i, child in enumerate(node[key]):
                    walk(child, [*path, i])
                return
        if "not" in node:
            walk(node["not"], [*path, 0])
            return
        if "field" not in node:  # a calculated value ("of": health, a ratio): shown in the rule, not a chip
            return
        f = ent.field(node["field"]) if ent and "." not in node["field"] else None
        parsed = AdhocMeasure.model_validate({"entity": adhoc["entity"], "agg": "count", "where": node, "label": "-",
                                              "plain_rule": "-"}).where
        out.append({"kind": "rule", "path": ".".join(map(str, path)), "field": node["field"],
                    "field_label": f.label if f else node["field"], "field_type": f.data_type if f else None,
                    "op": node.get("op"), "value": node.get("value", node.get("values")),
                    "label": describe(parsed, ent)})

    if adhoc.get("where"):
        walk(adhoc["where"], [])
    return out


def set_condition(adhoc: dict[str, Any], path: str, change: dict[str, Any], snap: ModelSnapshot) -> dict[str, Any]:
    """The rule with one condition changed (a chip edit): ``change`` holds ``field``, ``op`` and ``value``/``values``.
    The new rule is checked again like any other; ``plain_rule`` is worked out anew."""
    import copy

    out = copy.deepcopy(adhoc)
    if not out.get("where"):
        raise FreehandUnsupported("This rule has no condition to change.")
    node: Any = out["where"]
    parent: Any = None
    idx: int | str | None = None
    for step in [int(s) for s in path.split(".") if s != ""]:
        key = "all" if "all" in node else "any" if "any" in node else "not" if "not" in node else None
        if key is None:
            raise FreehandUnsupported("That condition is not in the rule.")
        parent, idx = (node[key], step) if key != "not" else (node, "not")
        node = parent[idx]
    leaf = {k: v for k, v in change.items() if k in ("field", "op", "value", "values", "other_field")
            and v is not None}
    if not leaf.get("field") or not leaf.get("op"):
        raise FreehandUnsupported("Give the field and the condition.")
    if parent is None:
        out["where"] = leaf
    else:
        parent[idx] = leaf
    try:
        out["plain_rule"] = plain_rule(out, snap)
        AdhocMeasure.model_validate(out)
    except (ValidationError, ValueError) as exc:
        raise FreehandUnsupported("That change doesn't make a rule Tela can run.") from exc
    return out


def rule_fields_visible(adhoc: AdhocMeasure, visible: dict[str, set[str]]) -> bool:
    """Every field the rule reads is one the person can see (planner check before the validator's own)."""
    refs = condition_fields(adhoc.where) | ({adhoc.field} if adhoc.field else set())
    for ref in refs:
        ent, key = (ref.split(".", 1) if "." in ref else (adhoc.entity, ref))
        if key not in visible.get(ent, set()):
            return False
    return True


def wants_rule(question: str) -> bool:
    """The question reads as a rule (conditions on fields), not only a measure's name."""
    q = " ".join(words(question))
    return bool(RULE_CUE.search(q) or CONDITION_CUE.search(q))


# --------------------------------------------------------------------------- certified first


STRONG = 0.95  # a certified measure named this well (its label or a synonym) answers, unless rule words remain


def _without_names(question: str, m: Measure) -> str:
    """The question without the words of the measure's name and synonyms ("overdue" of "overdue projects")."""
    own = {w for name in (m.label, m.key.replace("_", " "), *m.synonyms) for w in words(name) if w not in STOP}
    return " ".join(w for w in words(question) if not any(_near(w, o) for o in own))


def _certified_spec(m: Measure, breakdowns: list[dict[str, str]]) -> dict[str, Any]:
    return {"measure": {"ref": f"measure:{m.key}"}, "breakdowns": breakdowns, "filters": []}


def choose(question: str, snap: ModelSnapshot, rules: Plan, *, measures: list[Measure], dims: list[Dimension],
           visible: dict[str, set[str]], glossary: dict[str, str] | None = None,
           values: dict[str, dict[str, list[str]]] | None = None) -> Plan | None:
    """The plan for a person with free-hand access, or None to keep the rules planner's.

    Certified first: a question that reads as a measure's name (no rule words), or whose certified match is strong,
    keeps the certified plan. With rule words the free-hand rule answers; when the person spelled out another
    meaning for a certified measure's name ("past the Plan stage, meaning …"), the certified measure answers and the
    free-hand rule is shown beside it (D1). A rule the words don't make is refused with what was missing, unless a
    certified plan exists.
    """
    if not wants_rule(question):
        return None
    expanded, _ = expand(question.lower(), glossary or {})
    head, _, explicit = split_question(expanded)
    top = match_measures(question, measures)
    if not explicit and rules.status in ("ok", "clarify") and top and top[0][0] >= STRONG \
            and not wants_rule(_without_names(question, top[0][1])):
        return None  # the rule words are the certified measure's own name ("overdue go-lives")
    try:
        rule = build_rule(question, snap, visible=visible, dims=dims, glossary=glossary, values=values)
    except FreehandUnsupported as exc:
        # Rule words a certified name doesn't cover: a certified answer would drop them, so say what was missing.
        if rules.status == "ok" and not (top and wants_rule(_without_names(question, top[0][1]))):
            return None
        return Plan("unsupported", reason=exc.reason, measures_considered=rules.measures_considered,
                    interpreted=[f"Free-hand: {exc.reason}"], freehand=True)
    same = same_meaning(head, measures)
    if same is not None and same.entity != rule.adhoc["entity"]:
        same = None
    chips = [{"kind": "measure", "label": rule.adhoc["label"], "ref": "measure:_adhoc", "freehand": True},
             *rule.chips]
    freehand_side = {"kind": "freehand", "spec": rule.spec(), "label": rule.adhoc["label"],
                     "plain_rule": rule.adhoc["plain_rule"], "chips": rule.chips}
    if explicit and same is not None:
        # D1: the certified meaning stays the official answer; the person's own rule is shown next to it.
        return Plan("ok", spec=_certified_spec(same, rule.breakdowns),
                    chips=[{"kind": "measure", "label": same.label, "ref": f"measure:{same.key}"}],
                    measures_considered=[f"measure:{same.key}"], beside=freehand_side,
                    interpreted=[f"Answered with the certified {same.label}; your rule is shown beside it",
                                 *rule.understood])
    beside = None
    if same is not None:
        beside = {"kind": "certified", "spec": _certified_spec(same, rule.breakdowns), "label": same.label,
                  "plain_rule": same.plain_rule, "ref": f"measure:{same.key}"}
    return Plan("ok", spec=rule.spec(), chips=chips, measures_considered=rules.measures_considered, freehand=True,
                beside=beside, interpreted=rule.understood)


# --------------------------------------------------------------------------- AG-03 v2 (language model)


class AdhocOut(BaseModel):
    entity: str = Field(max_length=63)
    agg: str = Field(max_length=20)
    field: str | None = Field(default=None, max_length=130)
    where: dict[str, Any] | None = None
    label: str = Field(max_length=120)


AG03_FREEHAND = (
    "This person may also ask free-hand questions. Prefer a certified measure: return \"measure\" when one answers "
    "the question. Only when none fits, return \"adhoc\" instead: {\"entity\": key, \"agg\": \"count\"|\"sum\"|"
    "\"avg\"|\"min\"|\"max\"|\"count_distinct\", \"field\": key (not for count), \"where\": condition, \"label\": "
    "short name}. A condition is {\"field\": key, \"op\": \"=\"|\"!=\"|\"<\"|\"<=\"|\">\"|\">=\", \"value\": v} "
    "or {\"field\": key, \"op\": \"in\"|\"not_in\", \"values\": [...]} or {\"field\": key, \"op\": \"is_null\"|"
    "\"not_null\"} or {\"all\": [conditions]} / {\"any\": [conditions]} / {\"not\": condition}. Today is "
    "{\"today\": true} (\"offset_days\": n for days from today). Use only the fields listed; never SQL; if the "
    "fields can't express it, set unsupported true."
)


def fields_context(snap: ModelSnapshot, visible: dict[str, set[str]], samples: dict[str, dict[str, list[str]]],
                   glossary: dict[str, str]) -> str:
    """The fields AG-03 may use (hidden ones left out), a few in-scope values of text fields, and the glossary."""
    lines = ["Fields (entity.field — label, type, example values):"]
    for e in snap.entities:
        keys = visible.get(e.key)
        if not keys:
            continue
        syn = {d.field: d.synonyms for d in snap.dimensions if d.entity == e.key and d.synonyms}
        for f in e.fields:
            if f.key not in keys or not f.is_queryable:
                continue
            extra = f", also called {', '.join(syn[f.key][:3])}" if syn.get(f.key) else ""
            vals = (samples.get(e.key) or {}).get(f.key) or []
            ex = f"; e.g. {', '.join(vals[:5])}" if vals and f.data_type == "text" else ""
            lines.append(f"- {e.key}.{f.key} — {f.label}, {f.data_type}{extra}{ex}")
    if glossary:
        lines.append("Glossary (approved):")
        lines += [f"- {t}: {m}" for t, m in sorted(glossary.items())]
    return "\n".join(lines)[:12000]


def rule_from_planner(out: AdhocOut, snap: ModelSnapshot, visible: dict[str, set[str]]) -> Rule:
    """AG-03's ad-hoc rule, checked against the schema and the visible fields; anything invalid is refused, never
    repaired. The plain-words reading is worked out from the rule itself."""
    adhoc = {"entity": out.entity, "agg": out.agg, **({"field": out.field} if out.field else {}),
             **({"where": out.where} if out.where else {}), "label": out.label.strip() or "Free-hand rule",
             "plain_rule": "-"}
    try:
        parsed = AdhocMeasure.model_validate(adhoc)
    except ValidationError as exc:
        raise FreehandUnsupported("The suggested rule is not one Tela can run.") from exc
    ent = snap.entity(parsed.entity)
    if ent is None or parsed.entity not in visible or not rule_fields_visible(parsed, visible):
        raise FreehandUnsupported("The suggested rule uses data you can't see or that isn't in the model.")
    for ref in condition_fields(parsed.where) | ({parsed.field} if parsed.field else set()):
        e2, key = (ref.split(".", 1) if "." in ref else (parsed.entity, ref))
        target = snap.entity(e2)
        if target is None or target.field(key) is None:
            raise FreehandUnsupported("The suggested rule uses a field that isn't in the model.")
    adhoc["plain_rule"] = plain_rule(adhoc, snap)
    return Rule(adhoc=adhoc, chips=rule_chips(adhoc, snap), understood=["Rule suggested by the planner"])
