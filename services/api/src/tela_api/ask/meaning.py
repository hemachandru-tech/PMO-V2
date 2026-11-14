"""Semantic measure linking (ENH-016, US-192; FEAT-066 / FEAT-062): measures matched by meaning as well as by words.

Two signals sit beside the word score (``linking._measure_scores``):

* **Retrieved meaning** — each certified measure's label, synonyms and rule are embedded as its ``certified_measure``
  knowledge chunk (``knowledge/corpus.py``), so typed retrieval (dense + lexical, reranked) already finds "sanctioned
  money" near "Approved budget". The measures of those hits score by reciprocal rank: the first 0.85, the second
  0.64, the third 0.51, …
* **Qualifiers** — words that say which kind of figure is wanted. "Live", "pending", "open", "still running" prefer
  an open or active measure over the total; "money", "budget", "spend" a money measure; "percentage", "share", "rate"
  a percent one. A qualifier the measure's own name uses doesn't count against it ("Over-budget projects" is not a
  money measure, but "budget" names it).

The rules planner takes the linked ranking when meaning chose its first measure — retrieval, or an open qualifier
(``asks_open``; money and percent words only prefer a kind) — and the word match is weak or outscored
(``planner.plan_question``'s ``linked_measures``), so a paraphrase is planned by rules before it falls to AG-03. The
plan says "Matched by meaning" and is less sure: 0.75 with an open qualifier or the words naming half of the measure,
0.55 on retrieval alone, below "balanced", so Ask asks which reading was meant rather than risk a confident wrong
number (W4). Retrieval evidence a kind qualifier contradicts (money asked, not a money measure) is not counted.
"""

from __future__ import annotations

import re
from typing import Any

from tela_api.ask.planner import MONEY_WORDS, PERCENT_WORDS, SHARE, _generic, norm, words
from tela_query_spec.expr import AggExpr
from tela_query_spec.model import Measure

RR_K = 2  # reciprocal rank 1/(k + rank), scaled so the first hit scores RR_TOP
RR_TOP = 0.85
OPEN_QUESTION = re.compile(r"\b(?:open|pending|outstanding|ongoing|active|in flight|still (?:running|going|open|"
                           r"active|in progress)|in progress)\b|(?<!go )(?<!went )\blive\b")
OPEN_MEASURE = {"open", "active", "awaiting", "pending", "outstanding"}
MONEY_QUESTION = MONEY_WORDS | {"spent", "spending", "funding", "funds", "usd", "dollar"}
PERCENT_QUESTION = PERCENT_WORDS | {"proportion", "percentages", "compliant", "compliance"}
OPEN_SHIFT = 0.2
TOTAL_SHIFT = -0.1  # the total, when an open measure is asked for
KIND_SHIFT = 0.1  # money or percent measure, when that kind is asked for
OTHER_SHIFT = -0.3  # another kind, when the question asks for money or a percentage it doesn't name


def from_hits(hits: list[Any], visible: set[str] | None = None) -> dict[str, float]:
    """Measure key → meaning score from retrieval's ``certified_measure`` hits, by reciprocal rank."""
    out: dict[str, float] = {}
    for h in hits:
        key = (h.metadata or {}).get("measure") if getattr(h, "kind", None) == "certified_measure" else None
        if not key or key in out or (visible is not None and key not in visible):
            continue
        out[key] = RR_TOP * (RR_K + 1) / (RR_K + 1 + len(out))
    return out


def qualifiers(question: str) -> dict[str, set[str]]:
    """The qualifier words of a question, by kind: open, money, percent."""
    q, qw = norm(question.replace("%", " percent ")), set(words(question.replace("%", " percent ")))
    out: dict[str, set[str]] = {}
    if found := {m.group(0) for m in OPEN_QUESTION.finditer(q)}:
        out["open"] = found
    if money := qw & MONEY_QUESTION:
        out["money"] = money
    if (pct := qw & PERCENT_QUESTION) and not SHARE.search(q):  # "share of the total" is a breakdown's share
        out["percent"] = pct
    return out


def total(m: Measure) -> bool:
    """Every row of its table ("Projects", "Requests"): what "live" or "pending" narrows."""
    e = m.expression
    return _generic(m) or (isinstance(e, AggExpr) and e.agg == "count" and e.field is None and e.value is None
                           and e.where is None)


def every_row(m: Measure) -> bool:
    """Every row of its table counted or added up ("Forecast cost: internal" sums every project): what "open" narrows
    (EXEC-172)."""
    e = m.expression
    return total(m) or (isinstance(e, AggExpr) and e.agg == "sum" and e.value is None and e.where is None)


def asks_open(quals: dict[str, set[str]], m: Measure) -> bool:
    """An open qualifier the measure's name doesn't say, and the measure is an open one: evidence for it ("live
    projects" → Open projects). Money and percent words only prefer a kind; they don't point at one measure."""
    own = {w for name in (m.label, m.key.replace("_", " "), *m.synonyms) for w in words(name)}
    own |= {w.rstrip("s") for w in own}
    return bool(own & OPEN_MEASURE) and any(not set(words(p)) <= own for p in quals.get("open", set()))


def qualifier_shift(quals: dict[str, set[str]], m: Measure) -> float:
    """How much the question's qualifiers prefer this measure (+) or another of its kind (-)."""
    own = {w for name in (m.label, m.key.replace("_", " "), *m.synonyms) for w in words(name)}
    own |= {w.rstrip("s") for w in own}
    shift = 0.0
    # A qualifier counts unless the measure's own name says it ("open projects" names Open projects, it is not asked).
    if any(not set(words(p)) <= own for p in quals.get("open", set())):
        if own & OPEN_MEASURE:
            shift += OPEN_SHIFT
        elif total(m):
            shift += TOTAL_SHIFT
    for kind, fmt in (("money", "currency"), ("percent", "percent")):
        if not quals.get(kind):
            continue
        if m.format == fmt:
            shift += KIND_SHIFT
        elif not all(w in own or w.rstrip("s") in own for w in quals[kind]):
            shift += OTHER_SHIFT
    return shift
