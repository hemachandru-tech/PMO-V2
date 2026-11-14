"""Business definitions from knowledge (ENH-017, US-203; FEAT-067 / FEAT-014 / FEAT-062).

An uploaded document or glossary often says what a business term means ("An open project is a project whose current
state is Project Created, Active or On Hold"). Read as background text, AG-03 gets a few hundred characters of it at
best (EXEC-115); this module turns such passages into structured definitions that wait for an Admin's approval:

* **Proposed on indexing** (``propose``, called when the worker's chunks are stored): each passage that defines a
  term — a heading with "An X is …" under it, a ``Term: meaning`` / ``Term = meaning`` / "Term means …" line, a
  two-column table row — becomes a proposal per connection the document is for (its own, or every connection with
  an approved model), linked to the passage (``chunk_id``, the passage text kept). Reading is deterministic; no
  model is called (W1: nothing applies until an Admin decides).
* **A rule where the model can express it**: the term's entity (its noun, "project" → Projects), a field the meaning
  names by label ("current state"), the operator ("is", "in", "one of"; "not", "other than", "except") and the
  listed values, in the measure expression format (``{entity, field, op: in | not_in, values}``). Values are matched
  to their stored spelling through the value index (US-173) when it has the field. A field or value the model lacks
  is a **gap**: the definition is kept as text only, with the gap shown ("The model has no field “stage gate” on
  Projects"). Approval checks the values again on the data (a trial count per value).
* **Conflicts**: the certified measure of the same term (by its label, key or a synonym)
  is compared at approval: when its rule is not the definition's, both figures are counted (``certification_preview``
  surface, under the Admin's scope) and a difference is listed for the Admin (``conflict``), never resolved here
  (W3; US-204 applies definitions in answers, US-205 flags certified answers that conflict).
* **Per connection and model version**: a new approved model version carries the definitions forward (re-read on
  the new model, still approved or pending) and retires the old version's rows.
* **In AG-03's context**: approved definitions of an approved document whose term (or its measure's name) the
  question uses are always written, as their own section ahead of the budgeted ones (``for_question``; US-196's
  section budgets never cut them).

``GET /knowledge/definitions`` lists them with counts; ``POST /knowledge/definitions/{id}/review`` approves (with
optional edits), rejects or retires; ``POST /knowledge/definitions/{id}/check`` counts again. Audit:
``knowledge.definition_approved``, ``knowledge.definition_rejected``, ``knowledge.definition_retired``.
"""

from __future__ import annotations

import difflib
import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from tela_api.ask.planner import norm, words
from tela_api.audit import service as audit
from tela_api.content.exports import _rid
from tela_api.core.db import fetch_all, fetch_one, tenant_tx
from tela_api.governance.lookalike import _total, describe, differs
from tela_api.identity.deps import require
from tela_api.identity.permissions import Perm
from tela_api.identity.principal import Principal
from tela_api.identity.scope import resolve_scope
from tela_api.knowledge.retrieval import Hit
from tela_api.model.store import load_snapshot
from tela_api.query.service import QueryContext, QueryService
from tela_common.errors import ErrorCode, TelaError
from tela_common.ids import new_id
from tela_common.logging import get_logger
from tela_query_spec.expr import AggExpr, All, Compare, InSet, NullCheck, Today
from tela_query_spec.model import Measure, ModelEntity, ModelField, ModelSnapshot

log = get_logger(__name__)
router = APIRouter(prefix="/api/v1/knowledge", tags=["knowledge"])

KIND = "business_definition"  # the kind of a definition in AG-03's context
MAX_TERM_WORDS = 5
MAX_VALUES = 20
MAX_VALUE_WORDS = 5
TEXT_TYPES = ("text", "boolean")
NUMBER_TYPES = ("integer", "decimal")
DATE_TYPES = ("date", "datetime")
LISTED = ("in", "not_in")
# A rule's operators (US-251 widens US-203's listed values to the business-context sheet's operators).
RULE_OPS = (*LISTED, "blank", "not_blank", "<", ">", "between", "before_today", "after_today")
# How the sheet may write them.
SHEET_OPS = {"=": "in", "==": "in", "is": "in", "equals": "in", "in": "in", "one_of": "in",
             "\u2260": "not_in", "!=": "not_in", "<>": "not_in", "not": "not_in", "not_in": "not_in",
             "not_one_of": "not_in", "blank": "blank", "is_blank": "blank", "empty": "blank",
             "not_blank": "not_blank", "is_not_blank": "not_blank", "not_empty": "not_blank",
             "<": "<", "less_than": "<", "before": "<", ">": ">", "more_than": ">", "greater_than": ">",
             "after": ">", "between": "between", "before_today": "before_today", "after_today": "after_today"}
VALUE_COUNT = {"blank": 0, "not_blank": 0, "before_today": 0, "after_today": 0, "<": 1, ">": 1, "between": 2}
_NUMBER = re.compile(r"^-?\d+(?:\.\d+)?$")
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}(?:[T ][\d:.]+)?$")
TAIL_WORDS = {"the", "a", "an", "of", "for", "in", "on", "by", "and", "or", "to", "is", "are", "with", "as", "at"}
ADHOC_AGGS = ("count", "count_distinct", "sum", "avg", "min", "max")

_BULLET = re.compile(r"^\s*(?:[-*+•]|\d{1,2}[.)])\s+")
_MARKS = re.compile(r"[*_`]{1,3}")
# "Term: meaning", "Term = meaning", "Term — meaning", "Term means meaning", "Term is defined as …", "Term refers to …"
_DEF_LINE = re.compile(r"^(?P<term>[A-Za-z0-9][\w &/'\u2019-]{0,58}?)\s*(?::|=|\u2014|\u2013|\s-\s|\s+means\b:?|"
                       r"\s+is defined as\b|\s+refers to\b)\s*(?P<meaning>\S.{2,598})$")
# "An open project is …", "Open projects are …" (accepted only when it restates the heading or names a measure).
_DEF_SENTENCE = re.compile(r"^(?:an?\s+|the\s+)?(?P<term>[A-Za-z0-9][\w &/'\u2019-]{1,58}?)\s+(?:is|are|means)\s+"
                           r"(?P<meaning>\S.{2,598})$", re.IGNORECASE)
_SENTENCE_END = re.compile(r"(?<=[a-z0-9)\]”\"'])[.;](?=\s|$)", re.IGNORECASE)
_NEGATIVE = re.compile(r"^(?:is\s+not\s+one\s+of|is\s+not\s+any\s+of|is\s+not\s+in|is\s+not|isn['\u2019]t|are\s+not|"
                       r"not\s+in|not\s+one\s+of|other\s+than|except(?:\s+for)?|excluding|!=|<>)(?=\s|$)")
_POSITIVE = re.compile(r"^(?:is\s+one\s+of|is\s+any\s+of|are\s+one\s+of|is\s+in|is\s+either|is\s+set\s+to|is|are|in|"
                       r"=|==|equals?|equal\s+to|one\s+of|any\s+of|either|of|set\s+to|:)(?=\s|$)")
_VALUE_SPLIT = re.compile(r"\s*(?:,|;|/|\bor\b|\band\b|\bnor\b)\s*", re.IGNORECASE)
_QUOTES = "\u201c\u201d\"'\u2018\u2019`*_ "
# A field phrase the model may lack: "whose stage gate is …", "with a delivery track in …".
_FIELD_PHRASE = re.compile(r"\b(?:whose|with(?:\s+an?|\s+the)?|where(?:\s+the)?|having(?:\s+an?|\s+the)?)\s+"
                           r"(?P<field>[a-z][a-z ]{1,40}?)\s+(?:is|are|in|of|=|equals?)\b")


def singular(w: str) -> str:
    return w[:-1] if len(w) > 3 and w.endswith("s") and not w.endswith("ss") else w


def term_key(term: str) -> str:
    """A term as it is matched: lower case, words only, each singular ("Open projects" → "open project")."""
    return " ".join(singular(w) for w in words(term))[:80]


def _said(phrase: str, sentence: str) -> bool:
    return bool(phrase) and re.search(rf"\b{re.escape(phrase)}\b", sentence) is not None


# --------------------------------------------------------------------------- reading passages


@dataclass
class Passage:
    """A term and its meaning read from one chunk."""

    term: str
    meaning: str
    chunk_no: int
    passage: str
    chunk_id: str | None = None
    heading: str | None = None
    restated: bool = False  # the term is the chunk's heading


def _clean(line: str) -> str:
    return " ".join(_MARKS.sub("", _BULLET.sub("", line)).split())


def _first_sentence(text_: str) -> str:
    m = _SENTENCE_END.search(text_)
    return (text_[:m.start()] if m else text_).strip()


def _term_ok(term: str) -> bool:
    ws = words(term)
    return 0 < len(ws) <= MAX_TERM_WORDS and ws[-1] not in TAIL_WORDS and not term.strip().endswith("?")


def read_chunk(content: str, chunk_no: int, *, heading: str | None = None,
               chunk_id: str | None = None) -> list[Passage]:
    """The terms one chunk defines. A chunk under a heading starts with the heading's line (``chunk_text``)."""
    lines = [ln for ln in content.splitlines() if ln.strip()]
    if heading and lines and _clean(lines[0]) == heading.strip():
        lines = lines[1:]
    head_key = term_key(heading) if heading else ""
    out: list[Passage] = []

    def add(term: str, meaning: str, raw: str) -> None:
        term, meaning = term.strip(" :-\u2013\u2014"), meaning.strip()
        if heading and head_key and term_key(term) == head_key:
            term = heading.strip()  # "An open project is …" under "## Open project": the heading's spelling
        if _term_ok(term) and len(meaning) >= 3 and all(term_key(p.term) != term_key(term) for p in out):
            out.append(Passage(term=term[:80], meaning=meaning[:600], chunk_no=chunk_no, passage=raw.strip()[:2000],
                               chunk_id=chunk_id, heading=heading,
                               restated=bool(head_key) and term_key(term) == head_key))

    for raw in lines:
        if "|" in raw:  # a table row: | Term | Meaning |
            cells = [c.strip() for c in raw.strip().strip("|").split("|")]
            if len(cells) >= 2 and cells[0] and not set(cells[1]) <= set("-: ") and \
                    term_key(cells[0]) not in ("term", "name", "definition", "word"):
                add(_clean(cells[0]), _clean(cells[1]), raw)
            continue
        line = _clean(raw)
        if m := _DEF_LINE.match(line):
            add(m.group("term"), m.group("meaning"), raw)
            continue
        if m := _DEF_SENTENCE.match(_first_sentence(line)):
            add(m.group("term"), m.group("meaning"), raw)
    if heading and _term_ok(heading) and not out and lines:
        # "## Open project" with the meaning as the paragraph under it (a heading over a list of terms is not one).
        body = " ".join(_clean(ln) for ln in lines if "|" not in ln)
        if body:
            add(heading, _first_sentence(body), body)
    return out


def read_chunks(chunks: list[dict[str, Any]]) -> list[Passage]:
    """Every term the document's chunks define, the first passage of each term kept."""
    seen: set[str] = set()
    out = []
    for c in chunks:
        meta = c.get("metadata") or {}
        for p in read_chunk(c["content"], int(c["chunk_no"]), heading=meta.get("heading"),
                            chunk_id=str(c["id"]) if c.get("id") else None):
            if (k := term_key(p.term)) not in seen:
                seen.add(k)
                out.append(p)
    return out


# --------------------------------------------------------------------------- on the model


@dataclass
class Definition:
    term: str
    meaning: str
    rule: dict[str, Any] | None = None  # {entity, field, op, values}
    gaps: list[str] = field(default_factory=list)
    measure_ref: str | None = None

    @property
    def expressible(self) -> bool:
        return self.rule is not None and not self.gaps

    @property
    def key(self) -> str:
        return term_key(self.term)


def _names(f: ModelField) -> list[str]:
    bare = norm(re.sub(r"\([^)]*\)", " ", f.label))
    return [n for n in dict.fromkeys((norm(f.label), bare, f.key.replace("_", " "))) if n]


def entity_for(term: str, snap: ModelSnapshot) -> ModelEntity | None:
    """The entity the term is about: the longest entity label (or key) all of whose words, singular, the term has."""
    tw = {singular(w) for w in words(term)}
    best: tuple[int, ModelEntity] | None = None
    for e in snap.entities:
        for name in (e.label, e.key.replace("_", " ")):
            nw = [singular(w) for w in words(name)]
            if nw and set(nw) <= tw and (best is None or len(nw) > best[0]):
                best = (len(nw), e)
    return best[1] if best else None


def measure_for(term: str, snap: ModelSnapshot, entity: str | None = None) -> Measure | None:
    """The certified measure named by the term: by label, key or synonym, plural or not (never a looser match: a
    term that is not the measure's name is a different thing).
    Of the term's entity when it has one."""
    key = term_key(term)
    certified = [m for m in snap.measures if m.status == "certified" and (entity is None or m.entity == entity)]
    for m in certified:
        names = (m.label, re.sub(r"\([^)]*\)", " ", m.label), m.key.replace("_", " "), *m.synonyms)
        if key in {term_key(n) for n in names}:
            return m
    return None


def _field_in(meaning: str, entities: list[ModelEntity]) -> tuple[ModelEntity, ModelField] | None:
    """The field the meaning names: the longest name, the first of equal ones."""
    low = norm(meaning)
    best: tuple[int, int, ModelEntity, ModelField] | None = None
    for e in entities:
        for f in e.fields:
            if not f.is_queryable:
                continue
            for name in _names(f):
                m = re.search(rf"\b{re.escape(name)}\b", low)
                if m and (best is None or (len(name), -m.start()) > (best[0], best[1])):
                    best = (len(name), -m.start(), e, f)
    return (best[2], best[3]) if best else None


def _values(rest: str, *, bare: bool = False) -> tuple[Literal["in", "not_in"], list[str]] | None:
    """The operator and listed values after a field's name: "is Project Created, Active or On Hold". ``bare``: the
    meaning starts with the field's name or "with" it, so the values may follow it directly ("Current state On
    Hold", "projects with status Active")."""
    rest = re.sub(r"\([^)]*\)", " ", rest).strip()
    op: Literal["in", "not_in"] = "in"
    low = rest.lower()
    if m := _NEGATIVE.match(low):
        op, rest = "not_in", rest[m.end():].strip()
    found = op == "not_in"
    while m := _POSITIVE.match(rest.lower()):
        found, rest = True, rest[m.end():].strip()
    if not found and not bare:
        return None
    if m := _NEGATIVE.match(rest.lower()):  # "is not …" after "is"
        op, rest = "not_in", rest[m.end():].strip()
    end = _SENTENCE_END.search(rest)
    listed = (rest[:end.start()] if end else rest).strip().rstrip(".")
    values = [v.strip(_QUOTES) for v in _VALUE_SPLIT.split(listed)]
    values = [v for v in values if v]
    if not values or len(values) > MAX_VALUES or any(len(v) > 60 or len(v.split()) > MAX_VALUE_WORDS for v in values):
        return None
    return op, list(dict.fromkeys(values))


def interpret(p: Passage | Definition, snap: ModelSnapshot,
              known: dict[str, dict[str, str]] | None = None) -> Definition | None:
    """The passage as a definition on this model, or None when it defines nothing the model knows about (no rule,
    no field phrase, no measure of that name). ``known``: stored values by ``entity.field``, lower case → stored."""
    term, meaning = p.term, p.meaning
    entity = entity_for(term, snap)
    measure = measure_for(term, snap, entity.key if entity else None)
    if entity is None and measure is not None:
        entity = snap.entity(measure.entity)
    if entity is None:  # "Book of work: projects with status Active" names it in the meaning
        entity = entity_for(" ".join(words(meaning)[:4]), snap)
    d = Definition(term=term, meaning=meaning, measure_ref=f"measure:{measure.key}" if measure else None)
    hit = _field_in(meaning, [entity]) if entity else None
    if hit is None:
        phrase = _FIELD_PHRASE.search(meaning.lower())
        if phrase:
            where = f" on {entity.label}" if entity else ""
            d.gaps.append(f"The model has no field “{phrase.group('field').strip()}”{where}.")
            return d
        return d if measure is not None or (isinstance(p, Passage) and p.restated and entity is not None) else None
    ent, f = hit
    lead, rest = _around(meaning, f)
    parsed = _values(rest, bare=lead in ("", "with") or lead.endswith(" with"))
    if parsed is None:
        if measure is None and not (isinstance(p, Passage) and p.restated):
            return None
        d.gaps.append(f"The values of {f.label} could not be read from the definition.")
        return d
    op, values = parsed
    d.rule = {"entity": ent.key, "field": f.key, "op": op, "values": values}
    if f.data_type not in TEXT_TYPES:
        d.gaps.append(f"{f.label} is not a text field; only listed text values can be read from a definition.")
        return d
    stored = (known or {}).get(f"{ent.key}.{f.key}")
    if stored:
        missing = [v for v in values if v.lower() not in stored]
        d.rule["values"] = [stored.get(v.lower(), v) for v in values]
        d.gaps += [f"{f.label} has no value “{v}”." for v in missing]
    return d


def _key(name: str) -> str:
    return " ".join(singular(w) for w in words(name.replace("_", " ")))


def _sheet_entity(name: str, snap: ModelSnapshot) -> ModelEntity | None:
    """The table a sheet row names: by key, label or source table, plural or not."""
    want = _key(name)
    for e in snap.entities:
        if want in {_key(e.key), _key(e.label), _key(e.source_object.rsplit(".", 1)[-1])}:
            return e
    return None


def _sheet_field(name: str, ent: ModelEntity) -> ModelField | None:
    """The field a sheet row names: by key, label (with or without its bracketed part) or source column."""
    want = _key(name)
    for f in ent.fields:
        if want in {_key(f.key), _key(f.label), _key(re.sub(r"\([^)]*\)", " ", f.label)), _key(f.source_column)}:
            return f
    return None


def closest(value: str, stored: dict[str, str]) -> str | None:
    """The stored value most like one the sheet spelled differently ("Actve" → "Active")."""
    hit = difflib.get_close_matches(value.lower(), list(stored), n=1, cutoff=0.6)
    return stored[hit[0]] if hit else None


def interpret_row(cells: dict[str, Any], snap: ModelSnapshot,
                  known: dict[str, dict[str, str]] | None = None) -> Definition | None:
    """A business-context sheet row (US-251) as a definition on this model: its table, field, operator and values
    read as written (no wording to guess), checked against the model and the value index. A row that fails stays as
    text with the reason; a row with no rule is text only."""
    term = " ".join(str(cells.get("term") or "").split())[:80]
    if not term:
        return None
    meaning = " ".join(str(cells.get("meaning") or "").split())[:600]
    table, fname = str(cells.get("table") or "").strip(), str(cells.get("field") or "").strip()
    raw_op = str(cells.get("operator") or "").strip().lower().replace(" ", "_")
    values = [" ".join(str(v).split()) for v in cells.get("values") or [] if str(v).strip()]
    ent = _sheet_entity(table, snap) if table else entity_for(term, snap)
    measure = measure_for(term, snap, ent.key if ent else None)
    d = Definition(term=term, meaning=meaning or term, measure_ref=f"measure:{measure.key}" if measure else None)
    if not (table or fname or raw_op or values):
        d.gaps.append("The sheet gives no rule for this term.")
        return d
    if ent is None:
        d.gaps.append(f"The model has no table “{table}”." if table else "Name the table this term is about.")
        return d
    f = _sheet_field(fname, ent) if fname else None
    if f is None:
        d.gaps.append(f"The model has no field “{fname}” on {ent.label}." if fname else
                      f"Name the field of {ent.label} this term reads.")
        return d
    op = SHEET_OPS.get(raw_op)
    if op is None:
        d.gaps.append(f"“{cells.get('operator')}” is not an operator Tela knows." if raw_op else
                      "Choose an operator.")
        return d
    d.rule = {"entity": ent.key, "field": f.key, "op": op, "values": values}
    if not meaning:
        d.meaning = rule_words(d.rule, snap) or term
    if gaps := check_rule(d.rule, snap):
        d.gaps += gaps
        return d
    stored = (known or {}).get(f"{ent.key}.{f.key}")
    if op in LISTED and stored:
        out = []
        for v in values:
            if v.lower() in stored:
                out.append(stored[v.lower()])
                continue
            out.append(v)
            near = closest(v, stored)
            d.gaps.append(f"{f.label} has no value “{v}”" + (f" (closest: “{near}”)." if near else "."))
        d.rule["values"] = list(dict.fromkeys(out))
    if not meaning:  # in the stored spellings
        d.meaning = rule_words(d.rule, snap) or term
    return d


def _around(meaning: str, f: ModelField) -> tuple[str, str]:
    """The meaning's words before the field's name (normalised) and its text after it (as written)."""
    for name in sorted(_names(f), key=len, reverse=True):
        pattern = r"\W+".join(re.escape(w) for w in name.split())
        m = re.search(rf"\b{pattern}\b", meaning, re.IGNORECASE)
        if m:
            return norm(meaning[:m.start()]), meaning[m.end():]
    return "", ""


def check_rule(rule: dict[str, Any], snap: ModelSnapshot) -> list[str]:
    """The gaps of a rule (as read or as an Admin edited it) on this model."""
    ent = snap.entity(str(rule.get("entity")))
    if ent is None:
        return [f"The model has no {str(rule.get('entity')).replace('_', ' ')}."]
    f = ent.field(str(rule.get("field")))
    if f is None or not f.is_queryable:
        return [f"The model has no field “{str(rule.get('field')).replace('_', ' ')}” on {ent.label}."]
    op = rule.get("op")
    values = [str(v) for v in rule.get("values") or []]
    if op not in RULE_OPS:
        return [f"“{op}” is not an operator Tela knows."]
    if op in LISTED:
        if f.data_type not in TEXT_TYPES:
            return [f"{f.label} is not a text field; only listed text values can be read from a definition."]
        return [] if values else [f"Name the values of {f.label}."]
    if op in ("before_today", "after_today") and f.data_type not in DATE_TYPES:
        return [f"{f.label} is not a date field; “{op.replace('_', ' ')}” needs one."]
    if op in ("<", ">", "between") and f.data_type not in (*NUMBER_TYPES, *DATE_TYPES):
        return [f"{f.label} is not a number or date field; “{op}” needs one."]
    want = VALUE_COUNT.get(op, 0)
    if len(values) != want:
        return [f"“{op.replace('_', ' ')}” takes {['no values', 'one value', 'two values'][want]}."]
    shape = _NUMBER if f.data_type in NUMBER_TYPES else _ISO_DATE
    if bad := [v for v in values if not shape.match(v)]:
        kind = "a number" if f.data_type in NUMBER_TYPES else "a date (YYYY-MM-DD)"
        return [f"“{bad[0]}” is not {kind} for {f.label}."]
    return []


def _literal(v: Any) -> Any:
    s = str(v)
    if _NUMBER.match(s):
        return float(s) if "." in s else int(s)
    return s


def condition(rule: dict[str, Any]) -> Any:
    """A rule as a measure condition: listed values, blank, a comparison, a range, or a date against today."""
    op, f, vals = rule["op"], rule["field"], list(rule.get("values") or [])
    if op in LISTED:
        return InSet(field=f, op=op, values=vals)
    if op in ("blank", "not_blank"):
        return NullCheck(field=f, op="is_null" if op == "blank" else "not_null")
    if op in ("<", ">"):
        return Compare(field=f, op=op, value=_literal(vals[0]))
    if op == "between":
        return All(all=[Compare(field=f, op=">=", value=_literal(vals[0])),
                        Compare(field=f, op="<=", value=_literal(vals[1]))])
    return Compare(field=f, op="<" if op == "before_today" else ">", value=Today(today=True))


def rule_words(rule: dict[str, Any] | None, snap: ModelSnapshot) -> str | None:
    """"Projects where Current state is one of Project Created, Active, On Hold"."""
    if not rule:
        return None
    ent = snap.entity(rule["entity"])
    f = ent.field(rule["field"]) if ent else None
    label = f.label if f else str(rule["field"]).replace("_", " ")
    vals = [str(v) for v in rule.get("values") or []]
    op = rule["op"]
    if op in LISTED:
        verb = ("is not" if op == "not_in" else "is") + ("" if len(vals) == 1 else " one of")
        said = f"{verb} {', '.join(vals)}"
    else:
        said = {"blank": "is blank", "not_blank": "is not blank", "before_today": "is before today",
                "after_today": "is after today", "<": f"is below {vals[0] if vals else ''}",
                ">": f"is above {vals[0] if vals else ''}",
                "between": f"is between {' and '.join(vals)}"}.get(op, f"{op} {', '.join(vals)}").strip()
    return f"{ent.label if ent else rule['entity']} where {label} {said}"


def same_rule(measure: Measure, rule: dict[str, Any]) -> bool:
    """The measure counts by exactly the definition's condition (an "=" of one value is "in" that value)."""
    expr = measure.expression
    if not isinstance(expr, AggExpr) or measure.entity != rule["entity"]:
        return False
    where = expr.where
    if isinstance(where, Compare) and where.op in ("=", "!=") and where.other_field is None and \
            isinstance(where.value, str):
        where = InSet(field=where.field, op="in" if where.op == "=" else "not_in", values=[where.value])
    return isinstance(where, InSet) and where.field == rule["field"] and where.op == rule["op"] and \
        {str(v).lower() for v in where.values} == {str(v).lower() for v in rule["values"]}


# --------------------------------------------------------------------------- storing proposals


async def known_values(conn: AsyncConnection, model_version_id: str, snap: ModelSnapshot) -> dict[str, dict[str, str]]:
    """Stored values by ``entity.field`` from the value index (US-173), lower case → stored spelling."""
    rows = await fetch_all(conn, "SELECT dimension, stored_value, label FROM rag.dimension_values WHERE "
                                 "model_version_id = CAST(:v AS uuid)", {"v": model_version_id})
    out: dict[str, dict[str, str]] = {}
    for r in rows:
        d = snap.dimension(r["dimension"])
        if d is None or d.of is not None or d.bands:
            continue
        bag = out.setdefault(f"{d.entity}.{d.field}", {})
        bag[str(r["stored_value"]).lower()] = r["stored_value"]
        bag.setdefault(str(r["label"]).lower(), r["stored_value"])
    return out


_UPSERT = """
    INSERT INTO rag.knowledge_definitions AS d (id, tenant_id, connection_id, model_version_id, document_id, chunk_id,
        chunk_no, heading, passage, term, term_key, meaning, rule, gaps, expressible, measure_ref, status,
        reviewed_by, reviewed_at, review_note)
    VALUES (:id, :t, CAST(:c AS uuid), CAST(:v AS uuid), CAST(:doc AS uuid), CAST(:chunk AS uuid), :no, :head, :passage,
        :term, :key, :meaning, CAST(:rule AS jsonb), CAST(:gaps AS jsonb), :expr, :m, :status, CAST(:by AS uuid),
        CAST(:at AS timestamptz), :note)
    ON CONFLICT (tenant_id, connection_id, model_version_id, document_id, term_key) DO UPDATE SET
        chunk_id = EXCLUDED.chunk_id, chunk_no = EXCLUDED.chunk_no, heading = EXCLUDED.heading,
        passage = EXCLUDED.passage, term = EXCLUDED.term, meaning = EXCLUDED.meaning, rule = EXCLUDED.rule,
        gaps = EXCLUDED.gaps, expressible = EXCLUDED.expressible, measure_ref = EXCLUDED.measure_ref,
        checks = CASE WHEN d.meaning = EXCLUDED.meaning AND d.rule IS NOT DISTINCT FROM EXCLUDED.rule
                      THEN d.checks END,
        status = CASE WHEN d.meaning = EXCLUDED.meaning AND d.rule IS NOT DISTINCT FROM EXCLUDED.rule
                      AND d.status <> 'retired' THEN d.status ELSE 'pending' END,
        reviewed_by = CASE WHEN d.meaning = EXCLUDED.meaning AND d.rule IS NOT DISTINCT FROM EXCLUDED.rule
                           AND d.status <> 'retired' THEN d.reviewed_by END,
        reviewed_at = CASE WHEN d.meaning = EXCLUDED.meaning AND d.rule IS NOT DISTINCT FROM EXCLUDED.rule
                           AND d.status <> 'retired' THEN d.reviewed_at END
    RETURNING id
"""


async def _store(conn: AsyncConnection, tenant_id: str, *, connection_id: str, version_id: str, document_id: str,
                 p: Passage, d: Definition, status: str = "pending", reviewed: dict[str, Any] | None = None) -> str:
    row = await fetch_one(conn, _UPSERT, {
        "id": new_id(), "t": tenant_id, "c": connection_id, "v": version_id, "doc": document_id, "chunk": p.chunk_id,
        "no": p.chunk_no, "head": (p.heading or None) and p.heading[:200], "passage": p.passage, "term": d.term[:80],
        "key": d.key, "meaning": d.meaning[:600], "rule": json.dumps(d.rule) if d.rule else None,
        "gaps": json.dumps(d.gaps), "expr": d.expressible, "m": d.measure_ref, "status": status,
        "by": (reviewed or {}).get("reviewed_by"), "at": (reviewed or {}).get("reviewed_at"),
        "note": (reviewed or {}).get("review_note")})
    assert row is not None
    return str(row["id"])


async def _targets(conn: AsyncConnection, connection_id: str | None) -> list[tuple[str, str]]:
    """(connection, current approved model version) the definitions of a document are for."""
    rows = await fetch_all(conn, "SELECT m.connection_id, m.current_version_id FROM model.data_models m JOIN "
                                 "conn.connections c ON c.id = m.connection_id WHERE m.current_version_id IS NOT NULL "
                                 "AND c.deleted_at IS NULL AND (CAST(:c AS uuid) IS NULL OR m.connection_id = "
                                 "CAST(:c AS uuid)) ORDER BY c.name", {"c": connection_id})
    return [(str(r["connection_id"]), str(r["current_version_id"])) for r in rows]


async def propose(conn: AsyncConnection, tenant_id: str, doc: dict[str, Any]) -> int:
    """Read the definitions of an indexed document into proposals (re-indexing reads them again: an unchanged
    definition keeps its review; a changed one waits again; one no longer found is dropped, or retired when it was
    approved). Returns the number of definitions found."""
    chunks = await fetch_all(conn, "SELECT id, chunk_no, content, metadata FROM rag.knowledge_chunks WHERE "
                                   "document_id = :d ORDER BY chunk_no", {"d": doc["id"]})
    sheet = str(doc.get("content_type") or "").endswith("spreadsheetml.sheet")
    # US-251: a business-context sheet's rows carry their cells; prose keeps the reading of US-203.
    rows = [(Passage(term=str(c["metadata"]["sheet"].get("term") or "")[:80], meaning="", chunk_no=int(c["chunk_no"]),
                     passage=str(c["content"])[:2000], chunk_id=str(c["id"]), restated=True), c["metadata"]["sheet"])
            for c in chunks if sheet and isinstance((c["metadata"] or {}).get("sheet"), dict)]
    passages = [] if sheet else read_chunks([dict(c) for c in chunks])
    found = 0
    conn_id = str(doc["connection_id"]) if doc.get("connection_id") else None
    for cid, vid in await _targets(conn, conn_id):
        snap = await load_snapshot(conn, tenant_id, vid)
        known = await known_values(conn, vid, snap)
        keys: list[str] = []
        read = [(p, interpret(p, snap, known)) for p in passages] + \
            [(p, interpret_row(cells, snap, known)) for p, cells in rows]
        for p, d in read:
            if d is None or d.key in keys:
                continue
            await _store(conn, tenant_id, connection_id=cid, version_id=vid, document_id=str(doc["id"]), p=p, d=d)
            keys.append(d.key)
            found += 1
        gone = {"c": cid, "v": vid, "doc": str(doc["id"]), "keys": keys}
        await conn.execute(text("DELETE FROM rag.knowledge_definitions WHERE connection_id = CAST(:c AS uuid) AND "
                                "model_version_id = CAST(:v AS uuid) AND document_id = CAST(:doc AS uuid) AND "
                                "status IN ('pending', 'rejected') AND NOT (term_key = ANY(CAST(:keys AS text[])))"),
                           gone)
        await conn.execute(text("UPDATE rag.knowledge_definitions SET status = 'retired' WHERE connection_id = "
                                "CAST(:c AS uuid) AND model_version_id = CAST(:v AS uuid) AND document_id = "
                                "CAST(:doc AS uuid) AND status = 'approved' AND NOT (term_key = ANY(CAST(:keys AS "
                                "text[])))"), gone)
        from tela_api.knowledge import conditions  # US-252: a definition no longer approved takes its condition

        await conditions.settle(conn, tenant_id, connection_id=cid, version_id=vid, snap=snap)
    return found


async def carry_forward(conn: AsyncConnection, tenant_id: str, *, connection_id: str, version_id: str,
                        snap: ModelSnapshot) -> int:
    """A newly approved model version takes over the connection's pending and approved definitions, read again on
    its model (its fields and measures), keeping their review; the older versions' rows are retired."""
    rows = await fetch_all(
        conn, "SELECT DISTINCT ON (d.document_id, d.term_key) d.* FROM rag.knowledge_definitions d WHERE "
              "d.connection_id = CAST(:c AS uuid) AND d.model_version_id <> CAST(:v AS uuid) AND d.status IN "
              "('pending', 'approved') AND NOT EXISTS (SELECT 1 FROM rag.knowledge_definitions n WHERE "
              "n.model_version_id = CAST(:v AS uuid) AND n.document_id = d.document_id AND n.term_key = d.term_key) "
              "ORDER BY d.document_id, d.term_key, d.updated_at DESC", {"c": connection_id, "v": version_id})
    known = await known_values(conn, version_id, snap) if rows else {}
    n = 0
    for r in rows:
        p = Passage(term=r["term"], meaning=r["meaning"], chunk_no=r["chunk_no"], passage=r["passage"],
                    chunk_id=str(r["chunk_id"]) if r["chunk_id"] else None, heading=r["heading"])
        d = reread(r, snap, known)
        await _store(conn, tenant_id, connection_id=connection_id, version_id=version_id,
                     document_id=str(r["document_id"]), p=p, d=d, status=r["status"],
                     reviewed={"reviewed_by": str(r["reviewed_by"]) if r["reviewed_by"] else None,
                               "reviewed_at": r["reviewed_at"], "review_note": r["review_note"]})
        n += 1
    await conn.execute(text("UPDATE rag.knowledge_definitions SET status = 'retired' WHERE connection_id = "
                            "CAST(:c AS uuid) AND model_version_id <> CAST(:v AS uuid) AND status IN "
                            "('pending', 'approved')"), {"c": connection_id, "v": version_id})
    return n


def reread(row: dict[str, Any], snap: ModelSnapshot, known: dict[str, dict[str, str]] | None = None) -> Definition:
    """A stored definition on (another) model: its rule kept when the model still has the field, its gaps again."""
    rule = row["rule"]
    m = snap.measure(row["measure_ref"].split(":", 1)[1]) if row.get("measure_ref") else None
    if m is None:
        ent = snap.entity(rule["entity"]) if rule else entity_for(row["term"], snap)
        m = measure_for(row["term"], snap, ent.key if ent else None)
    d = Definition(term=row["term"], meaning=row["meaning"], rule=rule,
                   measure_ref=f"measure:{m.key}" if m and m.status == "certified" else None)
    if rule is None:
        d.gaps = list(row.get("gaps") or []) or ["The definition has no rule."]
        return d
    d.gaps = check_rule(rule, snap)
    stored = (known or {}).get(f"{rule['entity']}.{rule['field']}")
    if stored and not d.gaps and rule["op"] in LISTED:
        d.gaps = [f"{snap.entity(rule['entity']).field(rule['field']).label} has no value “{v}”."
                  for v in rule["values"] if str(v).lower() not in stored]
    return d


# --------------------------------------------------------------------------- trial counts


def _base(snap: ModelSnapshot) -> dict[str, Any]:
    return {"model_version_id": snap.model_version_id, "connection_id": snap.connection_id}


def definition_spec(rule: dict[str, Any], measure: Measure | None, snap: ModelSnapshot, term: str) -> dict[str, Any]:
    """The definition as a figure: the measure's aggregate with the definition's condition in place of its rule
    (W3), or a count of the entity when there is no such measure."""
    expr = measure.expression if measure is not None else None
    agg: dict[str, Any] = {"agg": "count"}
    if isinstance(expr, AggExpr) and measure is not None and measure.entity == rule["entity"] and \
            expr.agg in ADHOC_AGGS and expr.value is None:
        agg = {"agg": expr.agg, **({"field": expr.field} if expr.field else {})}
    where = condition(rule).model_dump(mode="json", by_alias=True, exclude_none=True)
    adhoc = {"entity": rule["entity"], **agg, "where": where, "label": term[:120],
             "plain_rule": (rule_words(rule, snap) or term)[:500]}
    return {**_base(snap), "measure": {"ref": "measure:_adhoc", "adhoc": adhoc}}


async def _per_value(service: QueryService, principal: Principal, rule: dict[str, Any], snap: ModelSnapshot,
                     term: str) -> dict[str, float] | None:
    """Rows per listed value (a count by the field's dimension), or None without a plain dimension on the field."""
    dim = next((d for d in snap.dimensions if d.entity == rule["entity"] and d.field == rule["field"]
                and d.of is None and not d.bands), None)
    if dim is None or rule["op"] != "in":
        return None
    spec = definition_spec(rule, None, snap, term)
    spec["breakdowns"] = [{"ref": f"dimension:{dim.key}"}]
    try:
        res = await service.execute(spec, principal, QueryContext(surface="certification_preview"))
    except TelaError:
        return None
    if res.get("hidden"):
        return None
    got = {str(r[0]).lower(): float(r[-1] or 0) for r in res["rows"] if r[0] is not None}
    return {str(v): got.get(str(v).lower(), 0.0) for v in rule["values"]}


async def run_checks(service: QueryService, principal: Principal, row: dict[str, Any],
                     snap: ModelSnapshot) -> tuple[dict[str, Any], list[str]]:
    """The trial counts of a definition (under the Admin's scope): the definition's figure, rows per listed value,
    and the certified measure of the same term with its figure and whether the two disagree. Returns the checks and
    the gaps the data adds (a listed value no row has)."""
    rule = row["rule"]
    m = snap.measure(row["measure_ref"].split(":", 1)[1]) if row.get("measure_ref") else None
    checks: dict[str, Any] = {"checked_at": datetime.now(UTC).isoformat(), "figure": None, "values": None,
                              "measure": None, "same_rule": None, "conflict": False}
    gaps: list[str] = []
    usable = rule is not None and not check_rule(rule, snap)
    if usable:
        per = await _per_value(service, principal, rule, snap, row["term"])
        if per is not None:
            checks["values"] = per
            label = snap.entity(rule["entity"]).field(rule["field"]).label
            gaps = [f"No rows have {label} “{v}”." for v, n in per.items() if not n]
        checks["figure"] = await _total(service, principal, definition_spec(rule, m, snap, row["term"]))
    if m is not None:
        ent = snap.entity(m.entity)
        expr = m.expression
        mrule = describe(expr.where, ent) if isinstance(expr, AggExpr) and expr.where is not None else m.plain_rule
        figure = await _total(service, principal, {**_base(snap), "measure": {"ref": f"measure:{m.key}"}})
        checks["measure"] = {"ref": f"measure:{m.key}", "label": m.label, "rule": mrule, "figure": figure}
        if usable:
            checks["same_rule"] = same_rule(m, rule)
            checks["conflict"] = not checks["same_rule"] and (
                differs(figure, checks["figure"]) or figure is None or checks["figure"] is None)
    return checks, gaps


# --------------------------------------------------------------------------- in AG-03's context


_APPROVED = """
    SELECT k.*, d.title AS document_title FROM rag.knowledge_definitions k
    JOIN rag.knowledge_documents d ON d.id = k.document_id
    WHERE k.status = 'approved' AND d.review_status = 'approved'
      AND k.connection_id = CAST(:c AS uuid) AND k.model_version_id = CAST(:v AS uuid)
    ORDER BY k.term_key
"""


async def approved(conn: AsyncConnection, connection_id: str, model_version_id: str) -> list[dict[str, Any]]:
    """The approved definitions of a connection's model version, from approved documents (US-204, US-205 read
    them too)."""
    return [dict(r) for r in await fetch_all(conn, _APPROVED, {"c": connection_id, "v": model_version_id})]


def uses_term(question: str, row: dict[str, Any], snap: ModelSnapshot) -> bool:
    """The question says the term, or the name of the certified measure the term is."""
    q = " ".join(singular(w) for w in words(question))
    names = [row["term"]]
    m = snap.measure(row["measure_ref"].split(":", 1)[1]) if row.get("measure_ref") else None
    if m is not None:
        names += [m.label, re.sub(r"\([^)]*\)", " ", m.label), *m.synonyms]
    return any(_said(term_key(n), q) for n in names)


def definition_text(row: dict[str, Any], snap: ModelSnapshot) -> str:
    lines = [f"{row['term']} means: {row['meaning']}"]
    if row["expressible"] and (words_ := rule_words(row["rule"], snap)):
        lines.append(f"Rule: count {words_}.")
    elif row["gaps"]:
        lines.append("Text only: " + " ".join(row["gaps"]))
    checks = row.get("checks") or {}
    if checks.get("conflict") and checks.get("measure"):
        lines.append(f"Differs from the certified measure {checks['measure']['label']} "
                     f"({checks['measure']['rule']}).")
    lines.append(f"Approved definition, from “{row['document_title']}”.")
    return "\n".join(lines)


def visible(row: dict[str, Any], fields: dict[str, set[str]] | None) -> bool:
    rule = row["rule"]
    return fields is None or rule is None or rule["field"] in fields.get(rule["entity"], set())


async def for_question(conn: AsyncConnection, question: str, snap: ModelSnapshot, *, connection_id: str,
                       fields: dict[str, set[str]] | None = None) -> list[Hit]:
    """The approved definitions a question uses, as context chunks (cited by their passage's chunk)."""
    rows = [r for r in await approved(conn, connection_id, snap.model_version_id)
            if uses_term(question, r, snap) and visible(r, fields)]
    return [Hit(chunk_id=str(r["chunk_id"] or r["id"]), document_id=str(r["document_id"]), kind=KIND,
                title=f"Definition: {r['term']}", content=definition_text(r, snap), score=1.0,
                metadata={"kind": KIND, "definition_id": str(r["id"]), "term": r["term"],
                          "measure": (r["measure_ref"] or "").split(":", 1)[-1] or None})
            for r in rows]


# --------------------------------------------------------------------------- API


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


_SELECT = """
    SELECT k.*, d.title AS document_title, d.review_status AS document_status, c.name AS connection_name,
           v.version_no, u.display_name AS reviewed_by_name
    FROM rag.knowledge_definitions k
    JOIN rag.knowledge_documents d ON d.id = k.document_id
    JOIN conn.connections c ON c.id = k.connection_id
    JOIN model.model_versions v ON v.id = k.model_version_id
    LEFT JOIN iam.users u ON u.id = k.reviewed_by
"""


def definition_out(r: dict[str, Any], snap: ModelSnapshot | None) -> dict[str, Any]:
    m = snap.measure(r["measure_ref"].split(":", 1)[1]) if snap is not None and r["measure_ref"] else None
    return {
        "id": str(r["id"]), "status": r["status"], "term": r["term"], "meaning": r["meaning"],
        "rule": r["rule"], "rule_text": rule_words(r["rule"], snap) if snap is not None else None,
        "expressible": r["expressible"], "gaps": r["gaps"] or [],
        "measure": {"ref": r["measure_ref"], "label": m.label if m else None} if r["measure_ref"] else None,
        "checks": r["checks"], "conflict": bool((r["checks"] or {}).get("conflict")),
        "connection": {"id": str(r["connection_id"]), "name": r["connection_name"]},
        "model_version": {"id": str(r["model_version_id"]), "version_no": r["version_no"]},
        "document": {"id": str(r["document_id"]), "title": r["document_title"], "status": r["document_status"]},
        "passage": {"chunk_id": str(r["chunk_id"]) if r["chunk_id"] else None, "chunk_no": r["chunk_no"],
                    "heading": r["heading"], "text": r["passage"]},
        "reviewed_by": {"id": str(r["reviewed_by"]), "display_name": r["reviewed_by_name"]}
        if r["reviewed_by"] else None,
        "reviewed_at": _iso(r["reviewed_at"]), "review_note": r["review_note"],
        "created_at": _iso(r["created_at"]), "updated_at": _iso(r["updated_at"]),
    }


async def _row(conn: AsyncConnection, definition_id: str, *, lock: bool = False) -> dict[str, Any]:
    try:
        row = await fetch_one(conn, _SELECT + " WHERE k.id = CAST(:id AS uuid)" + (" FOR UPDATE OF k" if lock else ""),
                              {"id": definition_id})
    except Exception:  # noqa: BLE001 - a malformed id is not found
        row = None
    if row is None:
        raise TelaError(ErrorCode.NOT_FOUND, "That definition was not found.")
    return dict(row)


@router.get("/definitions")
async def list_definitions(status: Literal["pending", "approved", "rejected", "retired", "all"] = Query(default="all"),
                           connection_id: str | None = Query(default=None, max_length=64),
                           document_id: str | None = Query(default=None, max_length=64),
                           conflicts: bool = Query(default=False),
                           principal: Principal = Depends(require(Perm.KNOWLEDGE_MANAGE))) -> dict[str, Any]:
    async with tenant_tx(principal.tenant_id) as conn:
        rows = await fetch_all(
            conn, _SELECT + " WHERE (:s = 'all' AND k.status <> 'retired' OR k.status = :s) "
                            "AND (CAST(:c AS uuid) IS NULL OR k.connection_id = CAST(:c AS uuid)) "
                            "AND (CAST(:doc AS uuid) IS NULL OR k.document_id = CAST(:doc AS uuid)) "
                            "AND (NOT :conf OR coalesce((k.checks->>'conflict')::boolean, false)) "
                            "ORDER BY (k.status = 'pending') DESC, k.term_key, c.name LIMIT 500",
            {"s": status, "c": connection_id, "doc": document_id, "conf": conflicts})
        counts = await fetch_one(
            conn, "SELECT count(*) FILTER (WHERE status = 'pending') AS pending, count(*) FILTER (WHERE status = "
                  "'approved') AS approved, count(*) FILTER (WHERE status = 'rejected') AS rejected, count(*) FILTER "
                  "(WHERE status IN ('pending', 'approved') AND coalesce((checks->>'conflict')::boolean, false)) AS "
                  "conflicts, count(*) FILTER (WHERE status IN ('pending', 'approved') AND NOT expressible) AS "
                  "text_only FROM rag.knowledge_definitions WHERE status <> 'retired' AND (CAST(:c AS uuid) IS NULL "
                  "OR connection_id = CAST(:c AS uuid))", {"c": connection_id})
        snaps: dict[str, ModelSnapshot] = {}
        items = []
        for r in rows:
            vid = str(r["model_version_id"])
            if vid not in snaps:
                snaps[vid] = await load_snapshot(conn, principal.tenant_id, vid)
            items.append(definition_out(dict(r), snaps[vid]))
    return {"items": items, "counts": {k: int(v) for k, v in dict(counts or {}).items()}}


class RuleIn(BaseModel):
    field: str = Field(min_length=1, max_length=63)
    op: Literal["in", "not_in"] = "in"
    values: list[str] = Field(min_length=1, max_length=MAX_VALUES)


class ReviewIn(BaseModel):
    decision: Literal["approve", "reject", "retire"]
    term: str | None = Field(default=None, min_length=1, max_length=80)
    meaning: str | None = Field(default=None, min_length=3, max_length=600)
    rule: RuleIn | None = None  # an edited rule on the definition's entity
    text_only: bool = False  # keep it as text, without a rule
    note: str | None = Field(default=None, max_length=500)


def _edited(row: dict[str, Any], body: ReviewIn, snap: ModelSnapshot) -> Definition:
    """The definition as the Admin approves it: their term, meaning or rule, re-read on the model."""
    term = " ".join((body.term or row["term"]).split())
    meaning = " ".join((body.meaning or row["meaning"]).split())
    if body.text_only:
        m = measure_for(term, snap)
        return Definition(term=term, meaning=meaning, gaps=["Kept as text by the Admin."],
                          measure_ref=f"measure:{m.key}" if m else row["measure_ref"])
    if body.rule is not None:
        measure = snap.measure(row["measure_ref"].split(":", 1)[1]) if row["measure_ref"] else None
        ent = entity_for(term, snap)
        entity = (row["rule"] or {}).get("entity") or (ent.key if ent else None) or (measure.entity if measure
                                                                                       else None)
        if entity is None:
            raise TelaError(ErrorCode.VALIDATION_ERROR, "Name what the term counts first (its table isn't known).",
                            {"field": "rule"})
        values = [" ".join(v.split()) for v in body.rule.values if v.strip()]
        rule = {"entity": entity, "field": body.rule.field, "op": body.rule.op, "values": list(dict.fromkeys(values))}
        if gaps := check_rule(rule, snap):
            raise TelaError(ErrorCode.VALIDATION_ERROR, gaps[0], {"field": "rule"})
        return reread({**row, "term": term, "meaning": meaning, "rule": rule}, snap)
    if body.term is None and body.meaning is None:
        return reread(row, snap)
    d = interpret(Passage(term=term, meaning=meaning, chunk_no=row["chunk_no"], passage=row["passage"], restated=True),
                  snap)
    return d or Definition(term=term, meaning=meaning, gaps=["The definition has no rule."])


async def _unseen(principal: Principal, rule: dict[str, Any], snap: ModelSnapshot, connection_id: str) -> str | None:
    """Why the person can't see what a rule reads (its table out of their scope, or the field hidden), or None."""
    async with tenant_tx(principal.tenant_id) as conn:
        try:
            scope = await resolve_scope(conn, principal, connection_id)
        except TelaError:
            return "You can't see this source."
    ent = snap.entity(rule["entity"])
    f = ent.field(rule["field"]) if ent else None
    if ent is None or f is None:
        return None
    if ent.key not in scope.entities:
        return f"You can't see {ent.label}, so this rule can't be checked."
    if f"{ent.key}.{f.key}" in scope.hidden_fields:
        return f"{f.label} on {ent.label} is hidden from you, so this rule can't be checked."
    return None


async def _checked(request: Request, principal: Principal, row: dict[str, Any], d: Definition,
                   snap: ModelSnapshot) -> tuple[Definition, dict[str, Any]]:
    service: QueryService = request.app.state.query_service
    probe = {**row, "term": d.term, "rule": d.rule if d.expressible else None, "measure_ref": d.measure_ref}
    checks, gaps = await run_checks(service, principal, probe, snap)
    if gaps:
        d.gaps = [*d.gaps, *gaps]
        checks["conflict"] = False  # text only now: nothing to compare
    return d, checks


async def _save(conn: AsyncConnection, row: dict[str, Any], d: Definition, checks: dict[str, Any] | None, *,
                status: str, principal: Principal, note: str | None) -> None:
    await conn.execute(
        text("UPDATE rag.knowledge_definitions SET term = :term, term_key = :key, meaning = :meaning, rule = "
             "CAST(:rule AS jsonb), gaps = CAST(:gaps AS jsonb), expressible = :expr, measure_ref = :m, checks = "
             "CAST(:checks AS jsonb), status = :s, reviewed_by = CAST(:u AS uuid), reviewed_at = now(), "
             "review_note = :note WHERE id = :id"),
        {"term": d.term, "key": d.key, "meaning": d.meaning, "rule": json.dumps(d.rule) if d.rule else None,
         "gaps": json.dumps(d.gaps), "expr": d.expressible, "m": d.measure_ref,
         "checks": json.dumps(checks) if checks is not None else None, "s": status, "u": principal.user_id,
         "note": note, "id": row["id"]})


@router.post("/definitions/{definition_id}/review")
async def review_definition(definition_id: str, body: ReviewIn, request: Request,
                            principal: Principal = Depends(require(Perm.KNOWLEDGE_MANAGE))) -> dict[str, Any]:
    async with tenant_tx(principal.tenant_id) as conn:
        row = await _row(conn, definition_id)
        snap = await load_snapshot(conn, principal.tenant_id, str(row["model_version_id"]))
    allowed = {"approve": ("pending", "rejected"), "reject": ("pending",), "retire": ("approved",)}[body.decision]
    if row["status"] not in allowed:
        raise TelaError(ErrorCode.CONFLICT, f"This definition is {row['status']}.", {"status": row["status"]})
    note = " ".join((body.note or "").split()) or None
    checks: dict[str, Any] | None = row["checks"]
    d = reread(row, snap)
    if body.decision == "approve":
        if row["document_status"] != "approved":
            raise TelaError(ErrorCode.CONFLICT, "Approve the document this definition comes from first.",
                            {"document_status": row["document_status"]})
        d = _edited(row, body, snap)
        if d.rule and (unseen := await _unseen(principal, d.rule, snap, str(row["connection_id"]))):
            d.gaps = [*d.gaps, unseen]  # kept as text: the person approving can't see what it reads
        d, checks = await _checked(request, principal, row, d, snap)
    new = {"approve": "approved", "reject": "rejected", "retire": "retired"}[body.decision]
    async with tenant_tx(principal.tenant_id) as conn:
        locked = await _row(conn, definition_id, lock=True)
        if locked["status"] != row["status"]:
            raise TelaError(ErrorCode.CONFLICT, "This definition was decided meanwhile.")
        if d.key != row["term_key"] and await fetch_one(
                conn, "SELECT 1 FROM rag.knowledge_definitions WHERE connection_id = :c AND model_version_id = :v AND "
                      "document_id = :doc AND term_key = :k AND id <> :id",
                {"c": row["connection_id"], "v": row["model_version_id"], "doc": row["document_id"], "k": d.key,
                 "id": row["id"]}):
            raise TelaError(ErrorCode.CONFLICT, f"The document already defines “{d.term}”.", {"field": "term"})
        await _save(conn, row, d, checks, status=new, principal=principal, note=note)
        from tela_api.knowledge import conditions  # US-252: the term stored once as a condition

        await conditions.from_definition(conn, principal.tenant_id, await _row(conn, definition_id), snap,
                                         decided_by=principal.user_id)
        await audit.record(conn, tenant_id=principal.tenant_id, action=f"knowledge.definition_{new}",
                           object_type="knowledge_definition", object_id=definition_id, outcome="success",
                           actor_id=principal.user_id, correlation_id=_rid(request),
                           details={"term": d.term, "connection_id": str(row["connection_id"]),
                                    "document_id": str(row["document_id"]), "rule": d.rule if d.expressible else None,
                                    "gaps": d.gaps, "measure": d.measure_ref,
                                    "conflict": bool((checks or {}).get("conflict")), "note": note})
        return definition_out(await _row(conn, definition_id), snap)


@router.post("/definitions/{definition_id}/check")
async def check_definition(definition_id: str, request: Request,
                           principal: Principal = Depends(require(Perm.KNOWLEDGE_MANAGE))) -> dict[str, Any]:
    """Count again: the definition's figure, its values and the certified measure's figure."""
    async with tenant_tx(principal.tenant_id) as conn:
        row = await _row(conn, definition_id)
        snap = await load_snapshot(conn, principal.tenant_id, str(row["model_version_id"]))
    if row["status"] not in ("pending", "approved"):
        raise TelaError(ErrorCode.CONFLICT, f"This definition is {row['status']}.", {"status": row["status"]})
    d, checks = await _checked(request, principal, row, reread(row, snap), snap)
    async with tenant_tx(principal.tenant_id) as conn:
        await conn.execute(
            text("UPDATE rag.knowledge_definitions SET gaps = CAST(:gaps AS jsonb), expressible = :expr, measure_ref "
                 "= :m, checks = CAST(:checks AS jsonb) WHERE id = :id"),
            {"gaps": json.dumps(d.gaps), "expr": d.expressible, "m": d.measure_ref, "checks": json.dumps(checks),
             "id": row["id"]})
        return definition_out(await _row(conn, definition_id), snap)
