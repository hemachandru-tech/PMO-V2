"""Clarification options from linking and retrieval (ENH-016, US-195; FEAT-066 / FEAT-019).

When Ask asks which measure was meant, the right one should be among the options, so one click answers. The options
come from the measures linking found — by words, by meaning (retrieval's ``certified_measure`` hits, US-192) and by
qualifiers — on the question's **main entity** first: the tables it names ("projects", "requests"), else the first
candidate's. A measure of another table is offered only when the question names that table ("How many projects are
past their deployment date?" no longer offers risks and issues past their due date); when that would leave fewer
than two options, the others follow.

One more source reads the measure's rule rather than its name: a question that says a date has passed ("past their
deployment date", "missed the go-live date") links the measures whose rule counts that date as before the reporting
date (Overdue go-lives: planned deployment date before the reporting date). The date is named by its field's words or
by a dimension over that field ("Go-live horizon").

Generic totals ("Projects") and the draft's per-table row counts come last: they are the reading of last resort.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from tela_api.ask.planner import COUNT_QUESTION, PERCENT_WORDS, STOP, _generic, _near, auto_count, norm, words
from tela_query_spec.expr import AggExpr, All, AsOf, Check, Compare, Condition, DaysBetween, Today, measure_aggs
from tela_query_spec.model import Measure, ModelSnapshot

MAX_OPTIONS = 4
MAX_PAST = 2  # of the options, so linking's candidates keep room
PASSED = re.compile(r"\b(?:past|passed|overdue|late|later than|missed|behind|beyond|expired|slipped)\b")


def named_entities(question: str, snap: ModelSnapshot) -> set[str]:
    """The tables a question names, by the last word of their label in any form ("requests" for Project requests),
    as linking's ``_measure_scores`` and ``refusal_help`` read them."""
    qw = words(question)
    return {e.key for e in snap.entities if (lw := words(e.label)) and any(_near(lw[-1], w) for w in qw)}


def _past_fields(cond: Condition | None) -> set[str]:
    """Date fields a condition requires to be before the reporting date (or today), directly or in an ``all``:
    ``field < asOf``, or days from the field to the reporting date above zero."""
    if isinstance(cond, All):
        return {f for c in cond.all for f in _past_fields(c)}
    if isinstance(cond, Compare) and cond.op in ("<", "<=") and isinstance(cond.value, AsOf | Today):
        offset = cond.value.asof_offset_days if isinstance(cond.value, AsOf) else cond.value.offset_days
        return {cond.field} if offset <= 0 else set()
    if isinstance(cond, Check) and isinstance(cond.of, DaysBetween) and isinstance(cond.of.days_from, str) and \
            isinstance(cond.of.days_to, AsOf | Today) and cond.op in (">", ">=") and \
            isinstance(cond.value, int | float) and cond.value >= 0:
        return {cond.of.days_from}
    return set()


def _says(qwords: list[str], name: str) -> bool:
    """Most of a name's words in the question, in any form: two or more, or the only one."""
    tokens = [w for w in dict.fromkeys(words(name.replace("_", " "))) if w not in STOP]
    hits = sum(1 for t in tokens if any(_near(t, w) for w in qwords))
    return bool(tokens) and hits >= min(2, len(tokens)) and hits / len(tokens) >= 0.5


def _conditions(cond: Condition | None) -> int:
    return sum(_conditions(c) for c in cond.all) if isinstance(cond, All) else int(cond is not None)


def past_date_measures(question: str, snap: ModelSnapshot, measures: Iterable[Measure]) -> list[Measure]:
    """The measures whose rule counts a date the question says has passed ("past their deployment date" → Overdue
    go-lives): a lateness word, and the date named by its field's words or by a dimension over that field. For "how
    many", counts first and percentages last (as ``match_measures`` reads them); then the broadest rule (fewest
    conditions): "Overdue go-lives" before "Overdue more than 90 days"."""
    q = norm(question)
    if not PASSED.search(q):
        return []
    qwords = words(question)
    counting = bool(COUNT_QUESTION.search(q)) and not (set(qwords) & PERCENT_WORDS or "%" in question)
    found: list[tuple[tuple[bool, bool, int], Measure]] = []
    for m in measures:
        aggs = [a for a in measure_aggs(m.expression) if isinstance(a, AggExpr)]
        for f in {f for a in aggs for f in _past_fields(a.where)}:
            name = f.split(".")[-1]
            dims = [d for d in snap.dimensions if d.entity == m.entity and d.fields() == {name}]
            if _says(qwords, name) or any(_says(qwords, d.label) for d in dims):
                count = isinstance(m.expression, AggExpr) and m.expression.agg == "count"
                found.append(((counting and m.format == "percent", counting and not count,
                               sum(_conditions(a.where) for a in aggs)), m))
                break
    return [m for _, m in sorted(found, key=lambda x: x[0])]


def reading_measures(question: str, snap: ModelSnapshot, visible: list[Measure], pool: Iterable[str], *,
                     exclude: Iterable[str] = (), limit: int = MAX_OPTIONS) -> list[str]:
    """The measure keys to offer as readings: the measures whose rule the question's date words describe, then
    ``pool`` (linking's candidates, words and meaning, best first), on the question's main entity first; another
    table's only when the question names it, or to make two options. Generic totals last; ``exclude`` left out."""
    by_key = {m.key: m for m in visible}
    skip = set(exclude)
    keys = [m.key for m in past_date_measures(question, snap, visible)[:MAX_PAST]]
    keys = [k for k in dict.fromkeys([*keys, *pool]) if k in by_key and k not in skip]
    if not keys:
        return []
    named = named_entities(question, snap)
    main = named or {by_key[keys[0]].entity}
    ordered = sorted(keys, key=lambda k: _generic(by_key[k]) or auto_count(by_key[k]))  # stable: pool order kept
    own = [k for k in ordered if by_key[k].entity in main]
    others = [k for k in ordered if by_key[k].entity not in main]
    if len(own) >= 2 or not others:
        return own[:limit]
    return [*own, *others][:limit]
