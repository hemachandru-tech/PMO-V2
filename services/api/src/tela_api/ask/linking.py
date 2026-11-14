"""Schema linking (ENH-013, US-181; FEAT-064): the few measures, dimensions, values and time words a question can
mean, scored, before AG-03 plans it.

A deterministic pass first. Measures score by how well their label, key or synonyms name something in the question
(the rules planner's ``_score``), with word forms forgiven ("plan" finds "Past planning", as ``refusal_help`` does), a
measure pair (US-176) or a bare money word ("cost": every money measure) counted as named. Dimensions score the same
way among those reachable from the candidate measures; a dimension the value index (US-173) linked a value of counts
as named, and a date dimension is a candidate when the question has a time word. AG-03 then plans from 5-10
candidates (at most ``MAX_MEASURES`` + ``MAX_DIMENSIONS``) instead of the whole catalogue — a look-alike it never sees
can't be picked.

Only on a miss (no measure named well enough) does AG-03 pick candidates from the compact catalogue (keys and
labels): ``LINK_SYSTEM`` / ``LinkOut``; without a model, or when that call fails, the closest by word forms are kept.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, Field

from tela_api.ask import meaning as semantic
from tela_api.ask.planner import (
    COUNT_QUESTION,
    GRAINS,
    MONEY_QUALIFIERS,
    MONEY_WORDS,
    PERCENT_WORDS,
    PERCENTILE,
    RANGES,
    STOP,
    TOP_N_PER,
    LinkedValue,
    ValueChoice,
    _generic,
    _near_share,
    _record_ref,
    _score,
    auto_count,
    measure_pair,
    norm,
    words,
)
from tela_query_spec.model import Dimension, Measure, ModelSnapshot

MAX_MEASURES = 5
MAX_LINKED = 8  # measures, with the ones retrieval found by meaning (US-192)
MAX_DIMENSIONS = 5
MIN_CANDIDATES = 5  # measures and dimensions together
NAMED = 0.5  # a measure scoring this is named by the question (``match_measures``' floor); below it, a miss
TIME_WORDS = re.compile(r"\b(trend|over time|when|date|dated|due|since|until|before|after|between|during|period|"
                        r"recent|recently|ago)\b")


@dataclass(frozen=True)
class Candidate:
    kind: Literal["measure", "dimension", "value", "time"]
    ref: str  # measure:key, dimension:key, value dimension:key=stored, time range:… / grain:…
    label: str
    score: float
    # named, synonym, words, meaning, qualifier, pair, money, linked value, value choice, date field, reachable, model
    why: str
    meaning: float = 0.0  # measures (US-192): the retrieved-meaning score beside the word score

    def public(self) -> dict[str, Any]:
        out = {"kind": self.kind, "ref": self.ref, "label": self.label, "score": round(self.score, 3),
               "why": self.why}
        return out | ({"meaning": round(self.meaning, 3)} if self.meaning else {})


@dataclass
class SchemaLinks:
    measures: list[Candidate] = field(default_factory=list)
    dimensions: list[Candidate] = field(default_factory=list)
    values: list[Candidate] = field(default_factory=list)
    time: list[Candidate] = field(default_factory=list)
    linked: list[LinkedValue] = field(default_factory=list)  # the value index's links, for ``linked_block``
    miss: bool = False  # no measure was named well enough: AG-03 may pick candidates (``LINK_SYSTEM``)
    by: Literal["rules", "model"] = "rules"

    @property
    def refs(self) -> list[str]:
        return [c.ref for c in (*self.measures, *self.dimensions)]

    def public(self) -> dict[str, Any]:
        return {"by": self.by, "miss": self.miss,
                **{k: [c.public() for c in getattr(self, k)] for k in ("measures", "dimensions", "values", "time")}}


def _measure_scores(question: str, measures: list[Measure], snap: ModelSnapshot,
                    meaning: dict[str, float] | None = None) -> list[tuple[float, str, Measure]]:
    q, qw = norm(question), set(words(question))
    qlist = [w for w in words(question) if w not in STOP and len(w) > 2]
    percent = bool(qw & PERCENT_WORDS) or "%" in question
    counting = bool(COUNT_QUESTION.search(q))
    named_tables = {e.key for e in snap.entities if (lw := words(e.label)) and any(
        w.rstrip("s") == lw[-1].rstrip("s") for w in qw)}
    pair = measure_pair(question, measures)
    money = qw & MONEY_WORDS and not qw & MONEY_QUALIFIERS
    quals = semantic.qualifiers(question)
    meaning = meaning or {}
    out: list[tuple[float, str, Measure]] = []
    for m in measures:
        named = _score(q, qw, m.label, m.key, m.synonyms, generic=_generic(m))
        if m.format == "percent":
            named += 0.05 if percent else (-0.6 if counting else -0.05)
        share, hits = max(_near_share(qlist, n) for n in (m.label, m.key.replace("_", " "), *m.synonyms))
        near = 0.8 * share + 0.01 * hits + (0.1 if m.entity in named_tables else 0.0) - (
            0.2 if _generic(m) or auto_count(m) else 0.0)
        if m.format == "percent" and counting and not percent:
            near -= 0.6  # "how many … reported Amber" is a count, as in ``match_measures``
        score, why = (named, "synonym" if 0.94 < named < 0.96 else "named") if named >= near else (near, "words")
        # US-192: meaning beside words — retrieval's rank, a little more when the words agree; then the qualifiers.
        if m.key in meaning and (meant := meaning[m.key] + 0.15 * max(min(score, 1.0), 0.0)) > score:
            score, why = meant, "meaning"
        if semantic.asks_open(quals, m) and (why in ("words", "meaning") or score < 0.9):
            why = "qualifier"  # the question asked for an open one ("live projects"), not by its name
        if (shift := semantic.qualifier_shift(quals, m)) < 0 and why == "meaning":
            why = "words"  # retrieval found it, but the question asks for money or a percentage and it isn't one
        score += shift
        if why in ("meaning", "qualifier") and m.format == "percent" and counting and not percent:
            score -= 0.6  # "how many" is a count, whatever retrieval found
        if pair is not None and m in (pair.a, pair.b):
            score, why = 2.0 if m is pair.a else 1.9, "pair"
        elif money and m.format == "currency" and score < NAMED:
            score, why = NAMED, "money"
        out.append((score, why, m))
    out.sort(key=lambda s: -s[0])
    return out


def _time_words(question: str) -> list[Candidate]:
    q = norm(question)
    out = [Candidate("time", f"range:{r}", r.replace("_", " "), 1.0, "named") for pattern, r in RANGES
           if re.search(pattern, q)]
    grains = [g for w, g in GRAINS.items() if re.search(rf"\b{w}\b", q) and (w != "day" or "by day" in q)]
    out += [Candidate("time", f"grain:{g}", f"by {g}", 1.0, "named") for g in dict.fromkeys(grains)]
    if not out and re.search(r"\b(trend|over time)\b", q):
        out.append(Candidate("time", "grain:month", "by month", 0.6, "trend"))
    return out


def rank_measures(question: str, snap: ModelSnapshot, measures: list[Measure],
                  meaning: dict[str, float] | None = None) -> list[tuple[float, str, str]]:
    """The measures as linking ranks them (words, meaning, qualifiers), named well enough to plan from, with why: the
    rules planner's ``linked_measures`` (US-192). A bare money word's candidates are a choice to ask about, not a
    link."""
    return [(sc, m.key, why) for sc, why, m in _measure_scores(question, measures, snap, meaning)
            if sc >= NAMED and why != "money"]


def link_schema(question: str, snap: ModelSnapshot, measures: list[Measure], dims: list[Dimension], *,
                linked: list[LinkedValue] | None = None, choices: list[ValueChoice] | None = None,
                considered: list[str] | None = None, meaning: dict[str, float] | None = None) -> SchemaLinks:
    """The deterministic pass. ``measures`` and ``dims`` are what the person may see; ``linked`` / ``choices`` the
    value index's links already limited to their scope; ``considered`` the measure keys the rules planner weighed
    (kept first among equals, so AG-03 always sees the rules' reading); ``meaning`` the measures retrieval found by
    meaning (``meaning.from_hits``, US-192)."""
    linked, choices = list(linked or []), list(choices or [])
    meaning = meaning or {}
    out = SchemaLinks(linked=linked)
    scored = _measure_scores(question, measures, snap, meaning)
    ruled = {k: i for i, k in enumerate(considered or [])}
    scored.sort(key=lambda s: (-(s[0] + (0.05 if s[2].key in ruled else 0.0)), ruled.get(s[2].key, 99)))
    out.miss = not scored or scored[0][0] < NAMED
    kept = [s for s in scored if s[0] >= NAMED][:MAX_MEASURES]
    kept += [s for s in scored if s not in kept][:max(0, 3 - len(kept))]  # at least three to choose among
    # US-192: what retrieval found by meaning is a candidate too, after those (AG-03 sees it even when unnamed).
    kept += sorted([s for s in scored if s not in kept and s[2].key in meaning],
                   key=lambda s: -meaning[s[2].key])[:max(0, MAX_LINKED - len(kept))]
    out.measures = [Candidate("measure", f"measure:{m.key}", m.label, sc, why, meaning.get(m.key, 0.0))
                    for sc, why, m in kept]

    roots = list(dict.fromkeys(m.entity for _, _, m in kept))
    reach = [d for d in dims if any(snap.join_path(r, d.entity) is not None for r in roots)]
    out.time = _time_words(question)
    q, qw = norm(question), set(words(question))
    qlist = [w for w in words(question) if w not in STOP and len(w) > 2]
    by_value: dict[str, float] = {}
    for v in linked:
        by_value[v.dimension] = max(by_value.get(v.dimension, 0.0), 0.9 * v.score)
    for c in choices:
        by_value[c.dimension] = max(by_value.get(c.dimension, 0.0), 0.7)
    timed = bool(out.time) or bool(TIME_WORDS.search(q))
    dscored: list[tuple[float, str, Dimension]] = []
    for i, d in enumerate(reach):
        named = _score(q, qw, d.label, d.key, d.synonyms)
        near = 0.8 * _near_share(qlist, d.label)[0]
        score, why = (named, "named") if named >= near else (near, "words")
        if by_value.get(d.key, 0.0) > score:
            score, why = by_value[d.key], "linked value"
        if d.is_temporal and timed and score < 0.8:
            score, why = 0.8 if d.entity == roots[0] else 0.6, "date field"
        # Among equals, the first measure's own table, then model order.
        dscored.append((score - (0.0 if d.entity == roots[0] else 0.02) - i * 1e-4, why, d))
    dscored.sort(key=lambda s: -s[0])
    dkept = [s for s in dscored if s[0] >= NAMED][:MAX_DIMENSIONS]
    room = MIN_CANDIDATES - len(out.measures) - len(dkept)
    dkept += [(s[0], "reachable", s[2]) for s in dscored if s not in dkept][:max(0, room)]
    out.dimensions = [Candidate("dimension", f"dimension:{d.key}", d.label, max(sc, 0.0), why)
                      for sc, why, d in dkept]
    # "Top 3 projects per programme", "median … by programme" (US-177): one row per record of the measure's table,
    # so its name field is a candidate too (the last place, keeping the cap).
    top = TOP_N_PER.search(q)
    if top is not None or PERCENTILE.search(q):
        record = _record_ref(snap, roots[0], top.group(2) if top else None) if roots else None
        if record is not None and record not in out.refs:
            cand = Candidate("dimension", record, "One row per record", 0.8, "per record")
            if len(out.dimensions) >= MAX_DIMENSIONS:
                out.dimensions[-1] = cand
            else:
                out.dimensions.append(cand)
    labels = {d.key: d.label for d in dims}
    out.values = [Candidate("value", f"dimension:{v.dimension}={v.stored}", f"{labels.get(v.dimension, v.dimension)}"
                            f": {v.label}", v.score, f"linked value ({v.how})") for v in linked
                  if v.dimension in labels]
    out.values += [Candidate("value", f"dimension:{c.dimension}={s}", f"{labels[c.dimension]}: {lbl}", 0.5,
                             f"value choice (“{c.phrase}”)") for c in choices if c.dimension in labels
                   for s, lbl in c.options]
    return out


def from_model(out: LinkOut, base: SchemaLinks, snap: ModelSnapshot, measures: list[Measure],
               dims: list[Dimension]) -> SchemaLinks:
    """AG-03's picks on a miss, kept only where the person may see them; the deterministic candidates fill the
    rest, so a bad pick narrows nothing."""
    visible_m = {m.key: m for m in measures}
    visible_d = {d.key: d for d in dims}
    ms = [visible_m[k] for k in dict.fromkeys(r.split(":", 1)[-1] for r in out.measures) if k in visible_m]
    if not ms:
        return base
    picked = [Candidate("measure", f"measure:{m.key}", m.label, 0.9, "model") for m in ms[:MAX_MEASURES]]
    rest = [c for c in base.measures if c.ref not in {p.ref for p in picked}]
    roots = [m.entity for m in ms]
    ds = [visible_d[k] for k in dict.fromkeys(r.split(":", 1)[-1] for r in out.dimensions) if k in visible_d
          and any(snap.join_path(r, visible_d[k].entity) is not None for r in roots)]
    dpicked = [Candidate("dimension", f"dimension:{d.key}", d.label, 0.9, "model") for d in ds[:MAX_DIMENSIONS]]
    drest = [c for c in base.dimensions if c.ref not in {p.ref for p in dpicked}
             and (d := visible_d.get(c.ref.split(":", 1)[1])) is not None
             and any(snap.join_path(r, d.entity) is not None for r in roots)]
    mkept = (picked + rest)[:MAX_MEASURES]
    dkept = (dpicked + drest)[:MAX_DIMENSIONS]
    return SchemaLinks(measures=mkept, dimensions=dkept, values=base.values, time=base.time, linked=base.linked,
                       miss=True, by="model")


class LinkOut(BaseModel):
    """AG-03's candidate pick on a miss: references only."""

    measures: list[str] = Field(default_factory=list, max_length=MAX_MEASURES)
    dimensions: list[str] = Field(default_factory=list, max_length=MAX_DIMENSIONS)


LINK_SYSTEM = (
    "You are Tela's query planner (AG-03), choosing candidates. From the catalogue below, list up to five measures "
    "that could answer the question, closest first, and up to five dimensions it may break down or filter by. Use "
    "only references listed. Reply as JSON: {\"measures\": [\"measure:…\"], \"dimensions\": [\"dimension:…\"]}."
)


def _answers(d: Dimension) -> str:
    """" (answers "why", "how long")": the question shapes a dimension answers (US-194)."""
    return f" (answers {', '.join(repr(a.replace('_', ' ')) for a in d.answers)})" if d.answers else ""


def catalogue_block(measures: list[Measure], dims: list[Dimension]) -> str:
    """The compact catalogue for ``LINK_SYSTEM``: references and labels only, no rules."""
    ms = "\n".join(f"- measure:{m.key} — {m.label}" for m in measures)
    ds = "\n".join(f"- dimension:{d.key} — {d.label}" + (" (date)" if d.is_temporal else "") + _answers(d)
                   for d in dims)
    return f"Measures:\n{ms}\n\nDimensions:\n{ds}"


def candidates_block(links: SchemaLinks, snap: ModelSnapshot) -> str:
    """AG-03's catalogue (replaces the full one): the candidate measures with their rules, the candidate dimensions
    and the time words, each with its score."""
    lines = ["Candidates follow, chosen for this question and scored 0-1 (higher is closer). Plan only from these "
             "references; if none of the measures can answer, set unsupported true and name the missing data.",
             "Certified measures:"]
    for c in links.measures:
        m = snap.measure(c.ref.split(":", 1)[1])
        rule = f": {m.plain_rule}" if m and m.plain_rule else ""
        lines.append(f"- {c.ref} — {c.label}{rule} (score {min(c.score, 1.0):.2f})")
    lines.append("")
    lines.append("Dimensions:")
    for c in links.dimensions:
        d = snap.dimension(c.ref.split(":", 1)[1])
        lines.append(f"- {c.ref} — {c.label}" + (" (date)" if d and d.is_temporal else "") +
                     (_answers(d) if d else "") + f" (score {min(c.score, 1.0):.2f})")
    if links.time:
        lines.append("")
        lines.append("Time words: " + "; ".join(c.ref for c in links.time))
    return "\n".join(lines)
