"""Future and time words read honestly (US-219, FEAT-072).

Tela counts what the data holds on the reporting date; it doesn't forecast. A question in the future tense ("How many
projects will be active in UAE") is answered with today's figure, and the answer says so in a data note. When a
certified measure counts ahead of the reporting date (a rule whose date window ends after it: "Go-lives in the next
30 days") and its own words are in the question, the note offers it.

A question that names a short horizon ("go-lives next month", "upcoming go-lives") is planned on that measure
outright: the planner prefers it over a measure of the past ("Go-lives per month" is a 12-month average), and says
which horizon it read the words as. Rules only, no model call (ENH-022 W5).
"""

from __future__ import annotations

import re
from typing import Any

from tela_api.ask.planner import SPAN_SKIP, _one, norm, words
from tela_query_spec.expr import AggExpr, All, Arith, AsOf, Compare, Today
from tela_query_spec.model import Measure, ModelSnapshot
from tela_query_spec.spec import ref_key

NOTE = "Counts what the data shows today; Tela doesn't forecast."
FUTURE = re.compile(r"\b(?:will|shall|going to|gonna|expected to|expect to|upcoming|coming up|in the coming|"
                    r"next (?:week|month|quarter|year|\d{1,3} days)|in the future|from now on|yet to come|"
                    r"still to come)\b")
# Horizons a forward measure starting at the reporting date stands for; "next quarter" or "next year" is longer than
# the packs' nearest windows, so it gets the note, not a different measure.
HORIZON = re.compile(r"\b(?:next (?:week|month|few weeks|\d{1,2} days)|in the coming (?:days|weeks|month)|"
                     r"upcoming|coming up|soon)\b")
# Words a forward measure's name spends on its window ("Go-lives in the next 30 days"), not on what it counts.
WINDOW_WORDS = {"next", "day", "days", "week", "weeks", "month", "months", "within", "coming", "upcoming", "ahead"}


def _window(cond: Any) -> tuple[int, int] | None:
    """The days ahead of the reporting date a condition's date window covers (start, end), when it ends after it:
    ``planned_deployment_date <= asOf + 30`` with ``>= asOf + 0``. Conditions under ``not`` or ``any`` don't count."""
    parts = cond.all if isinstance(cond, All) else [cond]
    start, end = 0, None
    for c in parts:
        if not isinstance(c, Compare) or not isinstance(c.value, AsOf | Today):
            continue
        days = c.value.asof_offset_days if isinstance(c.value, AsOf) else c.value.offset_days
        if c.op in ("<", "<=") and days > 0:
            end = days if end is None else min(end, days)
        elif c.op in (">", ">="):
            start = max(start, days)
    return (start, end) if end is not None and start < end else None


def ahead(measure: Measure) -> tuple[int, int] | None:
    """The window a measure counts ahead of the reporting date, else None (it counts today's or past records)."""
    expr = measure.expression
    if isinstance(expr, Arith) or not isinstance(expr, AggExpr) or expr.where is None:
        return None
    return _window(expr.where)


def _own_words(m: Measure) -> set[str]:
    """What a forward measure counts, in its label's words: "Go-lives in the next 30 days" → go, live."""
    return {_one(w) for w in words(m.label) if w not in SPAN_SKIP and w not in WINDOW_WORDS and not w.isdigit()}


def fitting(question: str, measures: list[Measure], *, entity: str | None = None) -> list[Measure]:
    """Forward measures whose own words are all in the question, the nearest window first."""
    said = {_one(w) for w in words(question)}
    found = [(win, m) for m in measures if (entity is None or m.entity == entity) and (win := ahead(m)) is not None
             and (own := _own_words(m)) and own <= said]
    return [m for _, m in sorted(found, key=lambda x: x[0])]


def horizon(question: str) -> str | None:
    """The short horizon a question names ("next month", "upcoming"), else None."""
    found = HORIZON.search(norm(question))
    return found.group(0) if found else None


def horizon_measure(question: str, measures: list[Measure]) -> tuple[str, Measure] | None:
    """The forward measure starting at the reporting date that a horizon question asks of ("go-lives next month" →
    Go-lives in the next 30 days), with the horizon's words."""
    if (said := horizon(question)) is None:
        return None
    near = [m for m in fitting(question, measures) if (ahead(m) or (1, 0))[0] == 0]
    return (said, near[0]) if near else None


def note(question: str, spec: dict[str, Any], snap: ModelSnapshot, measures: list[Measure]) -> dict[str, Any] | None:
    """The data note for a question about the future that the answer counts as of today: none when the measure
    already counts ahead or the plan has a range ahead ("next 30 days"). The forward measure that fits is offered."""
    if not FUTURE.search(norm(question)) or "adhoc" in (spec.get("measure") or {}):
        return None
    measure = snap.measure(ref_key(spec["measure"]["ref"]))
    if measure is None or ahead(measure) is not None:
        return None
    if str(((spec.get("time") or {}).get("range") or {}).get("relative") or "").startswith("next_"):
        return None
    out: dict[str, Any] = {"text": NOTE, "fact": "future"}
    if offer := fitting(question, [m for m in measures if m.key != measure.key], entity=measure.entity):
        out["action"] = {"label": f"Show {offer[0].label.lower()}",
                         "ops": [{"op": "set_measure", "ref": f"measure:{offer[0].key}"}]}
    return out
