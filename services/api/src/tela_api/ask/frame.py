"""Question frame (US-253, ENH-028 W3): what kind of answer a question asks for, before any catalogue matching.

A ``QuestionFrame`` holds the answer's shape (one number, a list of records, a breakdown …), its subject, the
measure phrase, the conditions, groupings, time, order and whether it follows up the previous answer. Every word of
the question lands in a slot, among the cue words that set the shape, or in ``words_unused``; only true grammar is
dropped, so a word nothing read ("list" before US-253) is visible instead of silently lost.

Two extractors fill it:

- ``rules_frame``: deterministic, catalogue-free, from the planner's existing pieces (negation, time ranges and
  grains, top N, visual words, comparison words, the definition pattern).
- ``llm_frame``: one small call (gateway task ``frame``, AG-01, a catalogue-free prompt, no thinking).

``combine`` cross-checks them: agreement on shape and conditions makes the frame confident; a slot they disagree on
is listed in ``uncertain``. If the LLM fails, the rules' frame stands alone.

Shadow mode (this story): ``shadow`` builds the frame after every Ask turn and records it as a step of the turn's
agent run; answers are unchanged. ``TELA_ASK_FRAME`` = ``shadow`` (both extractors, default), ``rules`` (no LLM
call, e.g. while a local model is busy) or ``off``.
"""

from __future__ import annotations

import asyncio
import re
import time
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, Field

from tela_api.ask.planner import (
    COMPARE_WORDS,
    GRAINS,
    GRAMMAR,
    RANGES,
    VISUAL_WORDS,
    except_words,
    norm,
    requested_visual,
    route_intent,
)
from tela_api.core.db import fetch_one, tenant_tx
from tela_api.core.settings import get_settings
from tela_api.llm import trace
from tela_api.llm.client import AgentFailed, run_agent
from tela_common.logging import get_logger

if TYPE_CHECKING:
    from tela_api.ask.rewrite import Turn

log = get_logger(__name__)

Shape = Literal["number", "list_of_records", "breakdown", "ranking", "trend", "comparison", "share", "distribution",
                "record_detail", "definition"]
SHAPES: tuple[str, ...] = Shape.__args__  # type: ignore[attr-defined]
Op = Literal["=", "!=", "in", "not_in", ">", ">=", "<", "<=", "between", "exists", "is"]
AGENT, TASK = "AG-01", "frame"
# The Ask answer modes that plan from the frame (ENH-028 W3/W4): no catalogue matching, no LLM reviews (US-258).
FRAME_MODES = frozenset({"fields", "hybrid"})


def frame_mode(mode: str | None) -> bool:
    """True when a source's answer mode (US-256) plans from the question frame: Fields or Hybrid."""
    return mode in FRAME_MODES
# Modes that build the frame to answer with and record it themselves (US-254, US-255; Direct SQL, US-257b)
OWN_FRAME_MODES = FRAME_MODES | {"direct_sql"}


class Condition(BaseModel):
    phrase: str
    field_hint: str | None = None
    op: Op | None = None
    value: str | None = None
    negated: bool = False


class TimeSlot(BaseModel):
    phrase: str
    field_hint: str | None = None
    range: str | None = None
    grain: Literal["day", "week", "month", "quarter", "year"] | None = None


class Order(BaseModel):
    by: str | None = None
    dir: Literal["asc", "desc"] | None = None
    limit: int | None = Field(default=None, ge=1, le=10_000)


class QuestionFrame(BaseModel):
    shape: Shape
    subject: str | None = None
    measure_phrase: str | None = None
    conditions: list[Condition] = Field(default_factory=list)
    groupings: list[str] = Field(default_factory=list)
    time: TimeSlot | None = None
    order: Order | None = None
    follow_up: Literal["none", "refine", "replace"] = "none"
    visual: str | None = None
    words_used: list[str] = Field(default_factory=list)
    words_unused: list[str] = Field(default_factory=list)


class FrameOut(BaseModel):
    """What the LLM returns (``words_used`` / ``words_unused`` are worked out here, not trusted from it)."""

    shape: Shape
    subject: str | None = None
    measure_phrase: str | None = None
    conditions: list[Condition] = Field(default_factory=list)
    groupings: list[str] = Field(default_factory=list)
    time: TimeSlot | None = None
    order: Order | None = None
    follow_up: Literal["none", "refine", "replace"] = "none"
    visual: str | None = None


# Words that carry no meaning of their own (articles, auxiliaries, pronouns, politeness, bare prepositions): dropped.
# Negation, comparison and time words are not here: they must land in a slot.
FRAME_GRAMMAR = GRAMMAR | {
    "the", "a", "an", "of", "is", "are", "was", "were", "be", "it", "its", "me", "my", "our", "we", "you", "please",
    "can", "do", "have", "has", "had", "there", "to", "and", "or", "for", "in", "on", "at", "as", "from", "with",
    "that", "this", "these", "those", "who", "whose", "s", "tell", "about", "currently", "now", "any", "get", "see",
    "need", "want", "would", "like", "find", "what", "which", "how", "show", "give", "display", "provide", "fetch",
    "where", "when", "by", "per", "each", "every", "all", "them", "their", "theirs", "one", "ones",
}
# Words that set the shape; counted as used when the frame's shape is the one they point to.
CUES: dict[str, frozenset[str]] = {
    "number": frozenset({"how", "many", "much", "number", "count", "total", "sum", "average", "avg", "mean", "median",
                         "overall"}),
    "list_of_records": frozenset({"list", "names", "name", "which", "who", "all", "every", "show", "records",
                                  "rows"}),
    "breakdown": frozenset({"breakdown", "broken", "split", "grouped", "group", "break", "down"}),
    "ranking": frozenset({"top", "bottom", "highest", "lowest", "most", "least", "largest", "smallest", "biggest",
                          "fewest", "rank", "ranking", "ranked", "best", "worst", "first", "last", "greatest"}),
    "trend": frozenset({"trend", "trends", "over", "time", "monthly", "weekly", "daily", "quarterly", "yearly",
                        "annual", "annually"}),
    "comparison": frozenset({"compare", "compared", "comparison", "vs", "versus", "against"}),
    "share": frozenset({"share", "percentage", "percent", "proportion", "fraction", "ratio", "split"}),
    "distribution": frozenset({"distribution", "spread", "histogram", "dispersion", "range"}),
    "record_detail": frozenset({"details", "detail", "info", "information", "summary", "profile"}),
    "definition": frozenset({"define", "definition", "meaning", "mean", "means", "calculated", "defined", "counted"}),
}
COPULA = frozenset({"are", "is", "were", "was", "being"})
NEGATION = frozenset({"not", "non", "no", "without", "excluding", "exclude", "except", "never", "other", "than"})

# --- the deterministic extractor -------------------------------------------------------------------------------

_B = (r"(?=\s*$|\s{2,}|\s+(?:by|per|in|for|with|without|where|and|or|but|not|excluding|except|top|vs|versus|"
      r"compared|sorted|ordered|going|order|sort|as|over|since|before|after|during|between|that|which|who|whose|"
      r"this|last|next|is|are|has|have|was|were)\b)")
_PHRASE = r"([a-z0-9]+(?: [a-z0-9]+){0,3}?)"  # one to four words, the shortest that reaches a boundary
MONTHS = ("january|february|march|april|may|june|july|august|september|october|november|december|jan|feb|mar|apr|"
          "jun|jul|aug|sep|sept|oct|nov|dec")
EXTRA_RANGES = [
    (rf"\b(?:in|during|for) ((?:q[1-4] )?(?:{MONTHS})?(?: ?(?:19|20)\d\d)|q[1-4](?: (?:19|20)\d\d)?|(?:{MONTHS}))\b",
     None),
    (r"\b(?:this|last|next|previous|past) (?:week|month|quarter|year)\b", None),
    (r"\b(?:the )?(?:last|past|next) \d{1,3} (?:days|weeks|months|quarters|years)\b", None),
    (r"\b(?:since|before|after|until|till) (?:(?:19|20)\d\d(?: \d{1,2}){0,2}|" + MONTHS
     + r"(?: (?:19|20)\d\d)?|last \w+|this \w+|yesterday|today)\b", None),
    (r"\b(?:yesterday|today|tomorrow|ytd|mtd|year to date|month to date|so far this (?:year|month))\b", None),
]
GRAIN_PATTERNS = [
    re.compile(r"\b(?:by|per|each|every|for each|for every|in each) (day|week|month|quarter|year)\b"),
    re.compile(r"\b(daily|weekly|monthly|quarterly|yearly|annual|annually)\b"),
]
OVER_TIME = re.compile(r"\b(?:over time|trend(?:s|ing)?(?: of| in| for)?|through time|over the (?:years|months))\b")
TOP_N = re.compile(r"\b(top|bottom|first|last|best|worst|highest|lowest|largest|smallest) (\d{1,4})\b")
SUPERLATIVE = re.compile(r"\b(highest|largest|biggest|most|greatest|maximum|max|lowest|smallest|least|fewest|"
                         r"minimum|min|best|worst)\b(?: (?:number of|amount of|count of))? " + _PHRASE + _B)
DESC = frozenset({"top", "highest", "largest", "biggest", "most", "greatest", "maximum", "max", "best", "first"})
SORTED = re.compile(r"\b(?:sorted|ordered|sort|order|ranked|rank) by " + _PHRASE + r"(?: (asc|ascending|desc|"
                    r"descending))?" + _B)
DIRECTION = re.compile(r"\b(?:in )?(ascending|descending) order\b|\b(asc|desc)\b")
GROUP = re.compile(r"\b(?:broken down by|break(?:ing)? down by|break down by|breakdown by|split by|grouped by|"
                   r"group(?:ed)? by|by|per|for each|for every|in each|across(?: all)?|across each) " + _PHRASE + _B)
SHARE = re.compile(r"\b(?:(?:share|percentage|percent|proportion|fraction) of|what (?:percent|percentage|share|"
                   r"proportion|fraction)|as a (?:share|percentage)|ratio of)\b")
DISTRIBUTION = re.compile(r"\b(?:distribution|spread|histogram|dispersion)\b")
COMPARE = re.compile(r"\b(?:compare|compared (?:to|with)|comparison of|vs|versus)\b")
COUNT_CUE = re.compile(r"\b(?:how many|number of|count of|count|how much|total|sum of|average|avg|mean|median)\b")
MEASURE = re.compile(r"\b(total|sum of|average|avg|mean|median|overall|min|max|minimum|maximum) "
                     r"(?:the )?(?:number of )?" + _PHRASE + r"(?= of|" + _B[3:])
HOW_MUCH = re.compile(r"\bhow much (?:did|do|does|have|has|is|are|was|were)? ?(?:we |they |it )?([a-z]+)\b")
LIST_CUE = re.compile(r"^(?:please )?(?:list|names? of|show (?:me )?(?:all|every|the list of|a list of|list of)|"
                      r"give (?:me )?(?:all|every|a list of|the list of|the names of)|which|who|what are the)|"
                      r"\blist(?:ing)?\b|\b(?:names|ids) of\b|\bwhich\b")
WEAK_LIST = re.compile(r"^(?:please )?(?:show|give|display|get|fetch|find) (?:me )?(?:the )?(?!number|count|total|"
                       r"sum|average|how)")
DETAIL = re.compile(r"\b(?:details?|info(?:rmation)?|summary|profile|everything) (?:of|for|about|on) "
                    r"(?!all\b|every\b)")
TELL_ABOUT = re.compile(r"^(?:tell me|what do (?:we|you) know) about (?!all\b)")
NEG_COND = re.compile(r"\b(?:not|non|no|without|excluding|exclude|except|never)(?: (?:in|from|at|the|a|an|any|yet|"
                      r"being|been|counting|including|with|those|these|ones|that|which|who|are|is))* "
                      r"([a-z0-9]+(?: [a-z0-9]+){0,3}?)" + _B)
COMPARE_OP = re.compile(r"\b([a-z]+(?: [a-z]+)?) (?:is |are |of )?(over|above|more than|greater than|at least|"
                        r"under|below|less than|fewer than|at most|exceeding|exceeds) (\d[\d ]*[kmb]?)\b")
BETWEEN = re.compile(r"\b([a-z]+(?: [a-z]+)?) (?:is |are |of )?between (\d+[kmb]?) and (\d+[kmb]?)\b")
OPS = {"over": ">", "above": ">", "more than": ">", "greater than": ">", "exceeding": ">", "exceeds": ">",
       "at least": ">=", "under": "<", "below": "<", "less than": "<", "fewer than": "<", "at most": "<="}
WHERE = re.compile(r"\b(?:where|whose|with) (?:the )?([a-z]+(?: [a-z]+)?) (?:is|are|=|equals|equal to|of|as) "
                   + _PHRASE + _B)
IN_PLACE = re.compile(r"\b(?:in|from|at|within|located in|based in) (?:the )?" + _PHRASE + _B)
WITH_LINK = re.compile(r"\b(?:with|that have|that has|which have|which has|having) (?:a |an |any |at least one |"
                       r"one or more |some )?" + _PHRASE + _B)
RELATIVE = re.compile(r"\b(?:that|which|who) (?:are|is|were|was) (?:still |currently )?" + _PHRASE + _B)
FOLLOW_REFINE = re.compile(r"^(?:only|just|excluding|exclude|except|without|by|per|split|break|as an?|make it|"
                           r"show (?:it|that|them|those)|top \d|bottom \d|sort|order|that)\b|\b(?:it|those|them|"
                           r"these|same)\b")
FOLLOW_REPLACE = re.compile(r"^(?:what about|how about|and what about|instead|and for|and|now)\b")


class _Work:
    """The normalised question with the spans read so far blanked out, so no word is read twice."""

    def __init__(self, q: str) -> None:
        self.q = q
        self.w = q

    def find(self, pattern: re.Pattern[str] | str) -> list[re.Match[str]]:
        return list(re.finditer(pattern, self.w))

    def take(self, start: int, end: int) -> None:
        self.w = self.w[:start] + " " * (end - start) + self.w[end:]

    def take_match(self, m: re.Match[str], group: int = 0) -> str:
        self.take(m.start(group), m.end(group))
        return m.group(group)

    def left(self) -> str:
        return " ".join(self.w.split())


def _content(phrase: str) -> str:
    """A phrase without leading and trailing grammar words."""
    ws = phrase.split()
    while ws and ws[0] in FRAME_GRAMMAR:
        ws.pop(0)
    while ws and ws[-1] in FRAME_GRAMMAR:
        ws.pop()
    return " ".join(ws)


def _stem(w: str) -> str:
    return w[:-1] if len(w) > 3 and w.endswith("s") and not w.endswith("ss") else w


def rules_frame(question: str, *, follow_up_possible: bool = False,
                subjects: set[str] | None = None) -> QuestionFrame:
    """The frame the rules read. ``subjects``: nouns that name a table (optional, singular or plural), to pick the
    subject among the words left; without them the last word left is taken."""
    q = norm(except_words(question.lower()))
    work = _Work(q)
    m: re.Match[str] | None
    intent = route_intent(question)
    f = QuestionFrame(shape="number")

    if follow_up_possible:
        if FOLLOW_REFINE.search(q):
            f.follow_up = "refine"
        elif FOLLOW_REPLACE.search(q):
            f.follow_up = "replace"

    if intent.kind == "definition" and not intent.loose:
        f.shape = "definition"
        f.subject = norm(intent.subject or "") or None
        return account(f, question)

    if (visual := requested_visual(q)) is not None:
        f.visual = visual
        names = [w for w, v in VISUAL_WORDS.items() if v == visual] + ["chart", "graph", "plot", "table", "format",
                                                                       "view", "tabular", "diagram"]
        for m in work.find(r"\b(?:as|in|into)? ?(?:an? |the )?(?:" + "|".join(map(re.escape, names))
                           + r")(?: (?:chart|graph|plot|view|form|format))?\b"):
            work.take(m.start(), m.end())

    # comparison and time before groupings, so "by month" is a grain and "vs last month" is not a range
    compare_kind = None
    for pattern, kind in COMPARE_WORDS:
        for hit in work.find(pattern):
            compare_kind = kind
            work.take_match(hit)
    compared = bool(compare_kind) or bool(work.find(COMPARE))
    for m in work.find(COMPARE):
        work.take_match(m)

    time_slot = _time(work)
    if compare_kind is not None:
        time_slot = time_slot or TimeSlot(phrase=compare_kind.replace("_", " "))
        time_slot.range = time_slot.range or compare_kind
    f.time = time_slot

    trend = bool(time_slot and time_slot.grain) or bool(work.find(OVER_TIME)) or f.visual == "trend"
    for m in work.find(OVER_TIME):
        work.take_match(m)

    # numeric conditions before order, so "at least 5" is not read as "least"
    f.conditions.extend(_numeric(work))

    # order: "top 5", "highest cost", "sorted by date"
    order = Order()
    if (m := TOP_N.search(work.w)) is not None:
        order.limit = int(m.group(2))
        order.dir = "desc" if m.group(1) in DESC else "asc"
        work.take_match(m)
    for s in work.find(SORTED):
        order.by = _content(s.group(1)) or None
        if s.group(2):
            order.dir = "asc" if s.group(2).startswith("asc") else "desc"
        work.take_match(s)
    if (m := SUPERLATIVE.search(work.w)) is not None:
        order.dir = order.dir or ("desc" if m.group(1) in DESC else "asc")
        order.by = order.by or _content(m.group(2)) or None
        work.take_match(m)
        if order.limit is None and re.match(r"^(?:which|what|who)\b", q) and re.search(r"\b(?:has|is|was|had)\b", q):
            order.limit = 1
    if (m := DIRECTION.search(work.w)) is not None:
        order.dir = "asc" if (m.group(1) or m.group(2)).startswith("asc") else "desc"
        work.take_match(m)
    ranked = order.limit is not None or (order.by is not None and bool(re.search(r"\b(?:top|bottom|highest|lowest|"
                                                                                r"most|least|largest|smallest|"
                                                                                r"biggest|fewest|rank)", q)))
    if order.by is None and order.limit is not None:  # "top 5 projects by cost": the "by" is the order
        if (m := re.search(r"\bby " + _PHRASE + _B, work.w)) is not None:
            order.by = _content(m.group(1)) or None
            work.take_match(m)
    f.order = order if (order.by or order.dir or order.limit) else None

    share = bool(work.find(SHARE))
    for m in work.find(SHARE):
        work.take_match(m)
    distribution = bool(work.find(DISTRIBUTION))
    for m in work.find(DISTRIBUTION):
        work.take_match(m)

    # groupings: "by region and status", "per programme"
    for m in work.find(GROUP):
        phrase = _content(m.group(1))
        if not phrase or phrase in GRAINS:
            continue
        f.groupings.extend(p for p in (_content(x) for x in re.split(r" and |, ", phrase)) if p)
        work.take_match(m)
        if (more := re.match(r" *and " + _PHRASE + _B, work.w[m.end():])) is not None:
            extra = _content(more.group(1))
            if extra:
                f.groupings.append(extra)
                work.take(m.end(), m.end() + more.end())

    # the figure asked for
    counted = bool(COUNT_CUE.search(q))
    if (m := MEASURE.search(work.w)) is not None:
        phrase = _content(m.group(0))
        if phrase and not re.fullmatch(r"(?:total|overall)(?: number(?: of)?)?", phrase):
            f.measure_phrase = phrase
        work.take_match(m)
    elif (m := HOW_MUCH.search(work.w)) is not None:
        f.measure_phrase = m.group(1)
        work.take_match(m)

    # conditions
    f.conditions.extend(_conditions(work))

    # the subject and the qualifiers before it ("open high risk projects")
    f.subject, qualifiers = _subject(work, subjects)
    for phrase in qualifiers:
        f.conditions.append(Condition(phrase=phrase))

    # the shape, most specific first
    listed = not counted and (bool(LIST_CUE.search(q)) or (bool(WEAK_LIST.search(q)) and not f.groupings
                                                           and not f.measure_phrase))
    detail = bool(DETAIL.search(q) or TELL_ABOUT.search(q)) and not _plural(f.subject)
    if compared:
        f.shape = "comparison"
    elif share:
        f.shape = "share"
    elif distribution:
        f.shape = "distribution"
    elif trend:
        f.shape = "trend"
    elif ranked:
        f.shape = "ranking"
    elif detail:
        f.shape = "record_detail"
    elif listed:
        f.shape = "list_of_records"
    elif f.groupings:
        f.shape = "breakdown"
    else:
        f.shape = "number"
    return account(f, question)


def _plural(subject: str | None) -> bool:
    return bool(subject) and subject.split()[-1].endswith("s")  # type: ignore[union-attr]


def _time(work: _Work) -> TimeSlot | None:
    slot: TimeSlot | None = None
    for pattern in GRAIN_PATTERNS:
        if (m := pattern.search(work.w)) is not None:
            word = m.group(1)
            grain = GRAINS.get(word) or {"annually": "year"}.get(word)
            slot = TimeSlot(phrase=m.group(0), grain=grain)  # type: ignore[arg-type]
            work.take_match(m)
            break
    ranges: list[tuple[str, str | None]] = [*RANGES, *EXTRA_RANGES]
    for rx, key in ranges:
        if (m := re.search(rx, work.w)) is None:
            continue
        said = m.group(0).strip()
        rng = key or re.sub(r"^(?:in|during|for) ", "", said).replace(" ", "_")
        prefix = work.w[:m.start()].rstrip()
        start, field_hint = m.start(), None
        if (hint := re.search(r"(?:^| )([a-z]+ed|due|started|begun|done)$", prefix)) is not None \
                and hint.group(1) not in FRAME_GRAMMAR:
            field_hint = hint.group(1)
            start = len(prefix) - len(field_hint)
        work.take(start, m.end())
        if slot is None:
            slot = TimeSlot(phrase=said, range=rng, field_hint=field_hint)
        else:
            slot.phrase, slot.range, slot.field_hint = f"{slot.phrase} {said}", rng, field_hint
        break
    return slot


def _numeric(work: _Work) -> list[Condition]:
    out: list[Condition] = []
    for m in work.find(BETWEEN):
        out.append(Condition(phrase=m.group(0), field_hint=_content(m.group(1)) or None, op="between",
                             value=f"{m.group(2)} and {m.group(3)}"))
        work.take_match(m)
    for m in work.find(COMPARE_OP):
        out.append(Condition(phrase=m.group(0), field_hint=_content(m.group(1)) or None, op=OPS[m.group(2)],  # type: ignore[arg-type]
                             value=m.group(3).replace(" ", "")))
        work.take_match(m)
    return out


def _value(phrase: str) -> str:
    """A value as said, without a leading article ("the", "a") but keeping its own words ("on hold")."""
    return re.sub(r"^(?:(?:the|a|an|any) )+", "", phrase).strip()


def _conditions(work: _Work) -> list[Condition]:
    out: list[Condition] = []
    for m in work.find(NEG_COND):
        parts, end = [m.group(1)], m.end()
        while (more := re.match(r" (?:or|and|nor) ([a-z0-9]+(?: [a-z0-9]+){0,3}?)" + _B, work.w[end:])) is not None:
            parts.append(more.group(1))  # "except KSA or UAE": both are left out
            end += more.end()
        for part in parts:
            value = _value(part)
            if value:
                out.append(Condition(phrase=f"not {value}", value=value, op="!=", negated=True))
        work.take(m.start(), end)
    for m in work.find(WHERE):
        value = _content(m.group(2))
        if value:
            out.append(Condition(phrase=m.group(0).strip(), field_hint=_content(m.group(1)) or None, op="=",
                                 value=value))
            work.take_match(m)
    for m in work.find(WITH_LINK):
        value = _content(m.group(1))
        if value:
            out.append(Condition(phrase=m.group(0).strip(), op="exists", value=value))
            work.take_match(m)
    for m in work.find(RELATIVE):
        value = _content(m.group(1))
        if value:
            out.append(Condition(phrase=value, value=value))
            work.take_match(m)
    for m in work.find(IN_PLACE):
        value = _content(m.group(1))
        if value:
            out.append(Condition(phrase=m.group(0).strip(), op="=", value=value))
            work.take_match(m)
    return out


def _subject(work: _Work, subjects: set[str] | None) -> tuple[str | None, list[str]]:
    """The subject among the words left, and the runs of words before it (qualifiers, "open high risk"). Words after
    the subject stay unread and end up in ``words_unused``."""
    ws = work.left().split()
    content = [i for i, w in enumerate(ws) if w not in FRAME_GRAMMAR and w not in NEGATION and not _cue(w)]
    if not content:
        return None, []
    # "which projects are late": what follows the verb describes the subject before it
    copula = next((i for i, w in enumerate(ws) if w in COPULA and any(c < i for c in content)), None)
    after = [ws[i] for i in content if copula is not None and i > copula]
    if copula is not None:
        content = [i for i in content if i < copula]
    known = {_stem(s) for s in subjects or set()}
    at = next((i for i in reversed(content) if _stem(ws[i]) in known), None) if known else None
    if at is None:
        at = content[-1]
    qualifiers: list[str] = [" ".join(after)] if after else []
    run: list[str] = []
    prev: int | None = None
    for i in content:
        if i >= at:
            break
        if run and prev is not None and i != prev + 1:
            qualifiers.append(" ".join(run))
            run = []
        run.append(ws[i])
        prev = i
    if run:
        qualifiers.insert(len(qualifiers) - (1 if after else 0), " ".join(run))
    work.w = " ".join(ws[i] for i in content if i > at)
    return ws[at], qualifiers


def _cue(w: str) -> bool:
    return any(w in cues for cues in CUES.values())


def account(f: QuestionFrame, question: str) -> QuestionFrame:
    """Set ``words_used`` and ``words_unused``: every word of the question that is not grammar is either in a slot
    (or a cue for the frame's shape, order or time) or unused."""
    slot_words: set[str] = set()

    def add(text: str | None) -> None:
        if text:
            slot_words.update(_stem(w) for w in norm(text).split())

    add(f.subject)
    add(f.measure_phrase)
    for c in f.conditions:
        add(c.phrase)
        add(c.field_hint)
        add(c.value)
    for g in f.groupings:
        add(g)
    if f.time is not None:
        add(f.time.phrase)
        add(f.time.field_hint)
        add(f.time.grain)
    if f.order is not None:
        add(f.order.by)
        if f.order.limit is not None:
            slot_words.add(str(f.order.limit))
    cues = set(CUES.get(f.shape, set())) | CUES["number"]
    if f.order is not None:
        cues |= CUES["ranking"] | {"sorted", "ordered", "sort", "order", "ascending", "descending", "asc", "desc"}
    if f.time is not None:
        cues |= CUES["trend"] | {"since", "before", "after", "until", "during", "last", "next", "this", "past"}
    if f.visual is not None:
        cues |= set(VISUAL_WORDS) | {"chart", "graph", "plot", "table", "format", "view", "tabular", "diagram",
                                     "bar", "line", "pie"}
    if f.shape in ("comparison", "share"):
        cues |= CUES["comparison"] | CUES["share"]
    if any(c.negated for c in f.conditions):
        cues |= NEGATION
    if any(c.op is not None and c.op not in ("=", "!=", "exists") for c in f.conditions):
        cues |= {"over", "above", "more", "greater", "least", "under", "below", "less", "fewer", "most", "than",
                 "between", "exceeding", "exceeds"}
    used: list[str] = []
    unused: list[str] = []
    for w in norm(except_words(question.lower())).split():
        if w in FRAME_GRAMMAR and w not in slot_words and _stem(w) not in slot_words:
            continue
        (used if _stem(w) in slot_words or w in cues else unused).append(w)
    f.words_used, f.words_unused = list(dict.fromkeys(used)), list(dict.fromkeys(unused))
    return f


# --- the LLM extractor -----------------------------------------------------------------------------------------

FRAME_SYSTEM = (
    "You read ONE question to a business data assistant and say what kind of answer it asks for. You do not know the "
    "data: never guess table or field names, keep the person's own words.\n"
    "shape: number (one figure: how many, total, average), list_of_records (one row per record: list of, which, "
    "show all, names of), breakdown (a figure per group: by region, per status), ranking (top/bottom N, highest, "
    "most), trend (a figure over time: by month, monthly, over time), comparison (this vs that, compared with last "
    "year), share (percentage or share of a total), distribution (spread, histogram), record_detail (everything "
    "about one named record), definition (what a term means or how it is calculated).\n"
    "subject: what is counted or listed (e.g. 'projects'). measure_phrase: the figure asked for in their words "
    "(e.g. 'total budget'); null for a plain count or a list.\n"
    "conditions: every word that narrows the records ('open', 'in UAE', 'not on hold', 'budget over 1m', 'with a "
    "high risk'), each with phrase, field_hint (only if a field is named), op (= != in not_in > >= < <= between "
    "exists), value as said, negated (true for not, no, without, excluding, except, other than).\n"
    "groupings: words after by / per / for each. time: phrase, field_hint ('created', 'due'), range (e.g. "
    "last_month, this_year, 2025), grain (day week month quarter year) for 'by month', 'monthly'. order: by, dir "
    "(asc or desc), limit (top 5 -> 5; 'which project has the most' -> 1).\n"
    "follow_up: refine when it changes the previous answer ('only UAE', 'as a chart', 'by status'), replace when it "
    "asks the same about something else ('what about risks?'), else none. visual: a chart or table the person "
    "names, else null.\n"
    "'How many' is always a number, even with 'list' nearby; 'list of' or 'which' without a count is a list."
)


async def llm_frame(gateway: Any, *, tenant_id: str, user_id: str | None, question: str, previous: str | None,
                    turn_id: str | None) -> QuestionFrame:
    """One ``frame`` call; raises ``AgentFailed`` when the model gives no usable answer."""
    user = question if previous is None else f"Previous question: {previous}\nQuestion: {question}"
    out = await run_agent(gateway, FrameOut, tenant_id=tenant_id, user_id=user_id, agent=AGENT, task=TASK,
                          system=FRAME_SYSTEM, user=user, turn_id=turn_id)
    f = QuestionFrame(**out.model_dump())
    if previous is None:
        f.follow_up = "none"
    return account(f, question)


# --- cross-check ---------------------------------------------------------------------------------------------

def _condition_words(f: QuestionFrame) -> tuple[frozenset[str], frozenset[str]]:
    """The content words of the conditions, kept and negated: two readings agree when they read the same words the
    same way round, however they split them into conditions."""
    kept: set[str] = set()
    negated: set[str] = set()
    for c in f.conditions:
        text = " ".join(t for t in (c.value or c.phrase, c.field_hint) if t)
        ws = {_stem(w) for w in norm(text).split()} - FRAME_GRAMMAR - NEGATION
        (negated if c.negated or c.op in ("!=", "not_in") else kept).update(ws)
    return frozenset(kept), frozenset(negated)


def _same_words(a: str | None, b: str | None) -> bool:
    return {_stem(w) for w in norm(a or "").split()} - FRAME_GRAMMAR == \
        {_stem(w) for w in norm(b or "").split()} - FRAME_GRAMMAR


def combine(rules: QuestionFrame, llm: QuestionFrame | None) -> dict[str, Any]:
    """The frame to use (the LLM's reading when it answered, else the rules'), whether it is confident, and which
    slots the two readings disagree on."""
    if llm is None:
        return {"frame": rules, "confident": False, "uncertain": ["llm"], "agree": {}}
    agree = {
        "shape": rules.shape == llm.shape,
        "conditions": _condition_words(rules) == _condition_words(llm),
        "subject": _same_words(rules.subject, llm.subject),
        "groupings": set(map(norm, rules.groupings)) == set(map(norm, llm.groupings)),
        "time": (rules.time is None) == (llm.time is None)
        and (rules.time is None or llm.time is None or rules.time.grain == llm.time.grain),
        "order": (rules.order.limit if rules.order else None) == (llm.order.limit if llm.order else None),
        "follow_up": rules.follow_up == llm.follow_up,
    }
    uncertain = [slot for slot, same in agree.items() if not same]
    return {"frame": llm, "confident": agree["shape"] and agree["conditions"], "uncertain": uncertain,
            "agree": agree}


# --- shadow mode ---------------------------------------------------------------------------------------------

async def llm_on(conn: Any) -> bool:
    """Whether the frame's LLM reading runs: ``TELA_ASK_FRAME=shadow`` and the tenant has a provider."""
    return get_settings().ask_frame == "shadow" and bool(
        (await fetch_one(conn, "SELECT provider FROM ops.llm_settings") or {}).get("provider"))


async def build(gateway: Any, *, tenant_id: str, user_id: str | None, question: str, previous: str | None,
                turn_id: str | None, use_llm: bool, subjects: set[str] | None = None) -> dict[str, Any]:
    """Both readings and their cross-check; the LLM's failure or timeout leaves the rules' frame alone."""
    began = time.monotonic()
    rules = rules_frame(question, follow_up_possible=previous is not None, subjects=subjects)
    llm: QuestionFrame | None = None
    llm_status = "off"
    if use_llm and gateway is not None:
        try:
            llm = await asyncio.wait_for(
                llm_frame(gateway, tenant_id=tenant_id, user_id=user_id, question=question, previous=previous,
                          turn_id=turn_id), timeout=get_settings().ask_frame_timeout_s)
            llm_status = "ok"
        except AgentFailed as exc:
            llm_status = f"failed: {exc.code}"
        except TimeoutError:
            llm_status = "timeout"
    got = combine(rules, llm)
    return {**got, "rules": rules, "llm": llm, "llm_status": llm_status,
            "ms": int((time.monotonic() - began) * 1000)}


def public(got: dict[str, Any]) -> dict[str, Any]:
    return {"frame": got["frame"].model_dump(exclude_defaults=True), "confident": got["confident"],
            "uncertain": got["uncertain"], "agree": got["agree"], "llm_status": got["llm_status"],
            "rules": got["rules"].model_dump(exclude_defaults=True),
            "llm": got["llm"].model_dump(exclude_defaults=True) if got["llm"] is not None else None}


async def shadow(orchestrator: Any, principal: Any, turn_id: str) -> None:
    """Build the turn's frame after it is answered and record it as a step of its agent run (US-253 shadow mode).
    The answer is never changed or held up, and a failure here is only logged."""
    mode = get_settings().ask_frame
    if mode == "off" or trace.current() is None:
        return
    try:
        async with tenant_tx(principal.tenant_id) as conn:
            turn = await fetch_one(conn, "SELECT question, conversation_id, context, ask_mode FROM chat.turns "
                                         "WHERE id = :id", {"id": turn_id})
            use_llm = await llm_on(conn)
        if turn is None or (turn["context"] or {}).get("preset_spec") or turn["ask_mode"] in OWN_FRAME_MODES:
            return  # Plan B, Hybrid and Direct SQL record their own frame
        history: list[Turn] = await orchestrator.history(principal, turn["conversation_id"], turn_id)
        previous = history[-1].question if history else None
        got = await build(orchestrator.gateway, tenant_id=principal.tenant_id, user_id=principal.user_id,
                          question=turn["question"], previous=previous, turn_id=turn_id, use_llm=use_llm)
        f = got["frame"]
        label = (f"Question frame (shadow): {f.shape}, "
                 + ("confident" if got["confident"] else "uncertain: " + ", ".join(got["uncertain"])))
        await trace.step("decision", label, agent=AGENT, detail=public(got), duration_ms=got["ms"])
    except Exception as exc:  # noqa: BLE001 - shadow mode never affects the answer
        log.warning("ask.frame_failed", error_class=type(exc).__name__)
