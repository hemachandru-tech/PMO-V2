"""Verify an answer against its question frame (US-258, ENH-028 W3; FEAT-078 / FEAT-064).

In the frame modes (Fields + definitions, Hybrid) this structural check replaces the LLM reviews (AG-01 intent,
AG-03 plan review, AG-02 back-check): instead of asking a model whether the plan reads like the question, it checks
that the planned spec does what the frame says was asked —

* **shape**: the spec's kind of answer (``scoring.answer_shape``) is the frame's (a count is never a list);
* **conditions**: every frame condition was read as a rule, and the spec carries a rule;
* **groupings**: every grouping is a breakdown of the spec (or the date the time slot follows);
* **time**: a period is applied as a range, a grain as a trend, a comparison as one;
* **words**: no word of the question was left unread.

Any failure means the answer is not certified and Ask asks about the failed slot (``fields_planner.plan``). Pure.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from tela_api.ask.frame import QuestionFrame
from tela_api.ask.planner import GRAMMAR, words
from tela_api.knowledge.definitions import singular
from tela_api.knowledge.scoring import answer_shape
from tela_query_spec.expr import AggExpr
from tela_query_spec.model import ModelSnapshot
from tela_query_spec.spec import Relative, ref_key

# A frame shape that a spec of another kind also answers: values side by side ("UAE vs KSA") are a breakdown.
ALSO = {"comparison": frozenset({"breakdown"})}
SHAPE_WORDS = {"number": "one number", "list_of_records": "a list of the records", "breakdown": "a breakdown",
               "ranking": "a ranking", "trend": "a trend over time", "comparison": "a comparison",
               "share": "each one's share"}
IGNORE = "ignore"  # the person's answer to "Answer without it?"
RELATIVE: frozenset[str] = frozenset(Relative.__args__)


@dataclass(frozen=True)
class Failure:
    """One slot the answer doesn't do: ``slot`` shape | condition | grouping | time | words."""

    slot: str
    phrase: str
    why: str
    wanted: str | None = None  # a shape failure: the frame's shape
    got: str | None = None  # … and the spec's

    def public(self) -> dict[str, Any]:
        return {"slot": self.slot, "phrase": self.phrase, "why": self.why,
                **({"wanted": self.wanted, "got": self.got} if self.slot == "shape" else {})}


def _meaningful(text: str, noise: Iterable[str] = ()) -> bool:
    skip = set(noise)
    return any(w not in GRAMMAR and w not in skip and singular(w) not in skip for w in words(text))


def has_rule(spec: dict[str, Any], snap: ModelSnapshot | None) -> bool:
    """The spec narrows the records: an ad-hoc rule, a certified measure with a rule of its own, a filter (a
    ``not_null`` on a ranking's value is not one) or a value breakdown (a share of each value)."""
    measure = spec.get("measure") or {}
    if (measure.get("adhoc") or {}).get("where"):
        return True
    m = snap.measure(ref_key(measure["ref"])) if snap is not None and measure.get("ref") else None
    if m is not None and isinstance(m.expression, AggExpr) and m.expression.where is not None:
        return True
    if any(f.get("op") != "not_null" for f in spec.get("filters") or []):
        return True
    return bool(spec.get("share_of_total") and spec.get("breakdowns"))


def check(frame: QuestionFrame, spec: dict[str, Any], slots: list[dict[str, Any]], *,
          unread: Iterable[str] = (), as_time: Iterable[str] = (), snap: ModelSnapshot | None = None,
          shape: str | None = None, noise: Iterable[str] = ()) -> list[Failure]:
    """What the spec doesn't do of the frame, in the order Ask asks about it. ``slots``: the grounding's slots
    (``Slot.public``); ``unread``: words nothing read; ``as_time``: groupings read as the time slot's date;
    ``shape``: the shape the person chose, instead of the frame's; ``noise``: words that never carry a condition."""
    out: list[Failure] = []
    wanted, got = shape or frame.shape, answer_shape(spec)
    if got != wanted and got not in ALSO.get(wanted, frozenset()):
        out.append(Failure("shape", frame.subject or "", f"asked for {SHAPE_WORDS.get(wanted, wanted)}, the plan "
                           f"gives {SHAPE_WORDS.get(got or '', got)}", wanted=wanted, got=got))
    read = [s for s in slots if s.get("slot") == "condition"]
    for c in frame.conditions:
        if not any(s.get("phrase") == c.phrase for s in read) and _meaningful(c.value or c.phrase, noise):
            out.append(Failure("condition", c.phrase, f"“{c.phrase}” is not applied"))
    if read and not has_rule(spec, snap):
        out.append(Failure("condition", read[0].get("phrase") or "", f"“{read[0].get('phrase')}” is not applied"))
    refs = {b.get("ref") for b in spec.get("breakdowns") or []} | {(spec.get("time") or {}).get("ref")}
    grouped = [s for s in slots if s.get("slot") == "grouping"]
    timed = set(as_time)
    for phrase in frame.groupings:
        if phrase in timed:
            continue
        hit = [s for s in grouped if s.get("phrase") == phrase]
        if not hit or not any(s.get("ref") in refs for s in hit):
            out.append(Failure("grouping", phrase, f"not broken down by “{phrase}”"))
    for s in grouped:
        if s.get("phrase") not in frame.groupings and s.get("ref") not in refs:
            out.append(Failure("grouping", s.get("phrase") or "", f"not broken down by “{s.get('phrase')}”"))
    t = frame.time
    if t is not None:
        time = spec.get("time") or {}
        if t.grain and not time.get("grain"):
            out.append(Failure("time", t.phrase, f"“{t.phrase}” is not followed over time"))
        elif wanted == "comparison" and t.range and not (spec.get("compare") or spec.get("breakdowns")):
            out.append(Failure("time", t.phrase, f"“{t.phrase}” is not compared"))
        elif t.range in RELATIVE and wanted != "comparison" and not time.get("range"):
            out.append(Failure("time", t.phrase, f"the period “{t.phrase}” is not applied"))
    left = [w for w in unread if w]
    if left:
        out.append(Failure("words", " ".join(left), f"“{' '.join(left)}” was not used"))
    return out
