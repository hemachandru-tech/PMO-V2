"""Chart enhancement suggestions (AG-07 visual advisor; US-104, FEAT-016, REQ-040; RULE-008; TEST-018).

Up to three typed spec changes, each with a reason, for an Ask answer or a widget in a draft report. They are
built from the result's *shape* — column kinds, row count and distinct counts per column, never cell values —
the RULE-008 decision table and the pack's chart patterns (``knowledge/patterns.py``). With a language model,
AG-07 sees the same shape, the deterministic candidates and the retrieved chart-pattern knowledge, and may pick
or propose changes; what it proposes is held to the same typed ops over certified, visible measures and
dimensions, and its titles and reasons pass the wording check (US-159, ``content/wording.py``). Without one (or
when its answer doesn't validate) the deterministic suggestions stand (FEAT-016 A2).

Every suggestion is checked before it is shown: the changed spec must be valid, may only add certified measures
the person can see (never an uncertified measure into a certified widget), and the visual it is read as must be
suitable for the changed result (RULE-008) — when an op changes the result's shape, the changed spec is run
(through the cache, under the person's scope) to check it. Applying is a refinement (Ask) or one report change
(canvas), so it can be undone. Nothing changes until the person applies a suggestion.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, Field

from tela_api.ask.refine import apply_ops
from tela_api.content import wording
from tela_api.knowledge.patterns import SUGGEST_OPS, ChartPattern
from tela_api.knowledge.retrieval import Hit, context_block
from tela_api.llm.client import AgentFailed, GatewayClient, run_agent
from tela_api.query.viz import result_shape
from tela_chart_spec import REASONS, VISUALS, ResultShape, choose_visual, suitability
from tela_common.errors import TelaError
from tela_query_spec.model import Dimension, Measure, ModelSnapshot
from tela_query_spec.spec import ref_key

MAX_SUGGESTIONS = 3
MANY_BARS = 12
TOP_N = 10
LOOKS_GOOD = "Looks good as is."
VISUAL_NAMES = {"figure": "a figure", "ranked_bar": "ranked bars", "columns": "columns", "trend": "a trend line",
                "paired_bars": "paired bars", "stacked_status": "stacked status bars", "donut": "a donut",
                "heat_grid": "a heat grid", "meter": "a meter", "table": "a table"}
SHAPE_OPS = {"add_measure", "add_breakdown", "set_limit", "compare_previous", "remove_breakdown", "set_time"}
KIND_OF = {"set_visual": "visual", "add_measure": "measure", "add_breakdown": "breakdown", "set_limit": "top_n",
           "compare_previous": "comparison", "sort": "sort"}

Runner = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


@dataclass
class Candidate:
    title: str
    reason: str
    ops: list[dict[str, Any]]
    visual: str | None = None  # the visual the suggestion is meant to be read as
    source: str = "rules"  # rules | pattern | llm
    pattern: str | None = None
    cites: list[str] = field(default_factory=list)
    fix: bool = False  # corrects a visual RULE-008 refuses; always shown first

    @property
    def kind(self) -> str:
        return next((KIND_OF[o["op"]] for o in self.ops if o.get("op") in KIND_OF and o["op"] != "set_visual"),
                    "visual")

    def key(self) -> str:
        return json.dumps(sorted(json.dumps(o, sort_keys=True) for o in self.ops), sort_keys=True)


@dataclass
class Context:
    """What the advisor knows about one widget or answer."""

    spec: dict[str, Any]
    visual: str
    result: dict[str, Any]
    snap: ModelSnapshot
    measures: list[Measure]  # certified measures the person can see
    dims: list[Dimension]  # dimensions the person can see
    patterns: list[ChartPattern] = field(default_factory=list)

    @property
    def shape(self) -> ResultShape:
        return result_shape(self.result, self.snap, target=has_target(self.spec))


def has_target(spec: dict[str, Any]) -> bool:
    """The widget sets a target band (``visual.tone``), so a bullet may be suggested (US-187)."""
    tone = (spec.get("visual") or {}).get("tone") or {}
    return tone.get("warning") is not None or tone.get("critical") is not None


# --------------------------------------------------------------------------- deterministic candidates


def _label(ctx: Context, ref: str) -> str:
    key = ref_key(ref)
    found = ctx.snap.measure(key) if ref.startswith("measure:") else ctx.snap.dimension(key)
    return str(found.label) if found else key.replace("_", " ")


def _spec_measures(spec: dict[str, Any]) -> set[str]:
    return {ref_key(m["ref"]) for m in [spec["measure"], *spec.get("extra_measures", [])]}


def _pattern_matches(p: ChartPattern, ctx: Context) -> bool:
    if p.when is None or p.suggest is None:
        return False
    w, spec = p.when, ctx.spec
    main = ref_key(spec["measure"]["ref"])
    breakdowns = {ref_key(b["ref"]) for b in spec.get("breakdowns", [])}
    if w.measure and main not in w.measure:
        return False
    if w.breakdown and not breakdowns & set(w.breakdown):
        return False
    if w.also_breakdown and not breakdowns & set(w.also_breakdown):
        return False
    if w.no_breakdown and (breakdowns or spec.get("time")):
        return False
    return not _spec_measures(spec) & set(w.no_measure)


def candidates(ctx: Context) -> list[Candidate]:
    """Deterministic suggestions, best first: an unsuitable visual, the pack's chart patterns, then shape rules."""
    shape, spec, current = ctx.shape, ctx.spec, ctx.visual
    table = {s["visual"]: s for s in suitability(shape)}
    out: list[Candidate] = []
    # 1. The current visual breaks RULE-008: the most suitable one instead (TEST-018).
    if current in table and current != "table" and not table[current]["suitable"]:
        alt = choose_visual(shape)[0]
        reason = f"{REASONS[current]}; {VISUAL_NAMES[alt]} suit this result."
        first = shape.categories[0] if shape.categories else None
        n = shape.distinct_counts.get(first, shape.row_count) if first else shape.row_count
        if current == "donut" and first and n > 7:
            reason = f"{n} slices is too many for a donut; {VISUAL_NAMES[alt]} compare them more clearly."
        out.append(Candidate(f"Show as {VISUAL_NAMES[alt]}", reason, [{"op": "set_visual", "value": alt}], alt,
                             fix=True))
    # 2. House chart patterns that fit this spec.
    for p in ctx.patterns:
        if _pattern_matches(p, ctx) and p.suggest is not None:
            out.append(Candidate(p.title, p.reason or p.title, [dict(o) for o in p.suggest.ops], p.suggest.visual,
                                 "pattern", p.id))
    # 3. An ordered breakdown ranked by value loses its order.
    ordered = shape.ordered[0] if shape.ordered else None
    if ordered and current in ("ranked_bar", "donut") and table["columns"]["suitable"]:
        dim = next((ctx.snap.dimension(ref_key(b["ref"])) for b in spec.get("breakdowns", [])), None)
        order = f" ({dim.sort_order[0]} to {dim.sort_order[-1]})" if dim and dim.sort_order else ""
        out.append(Candidate("Show as columns, in order", f"{dim.label if dim else 'This breakdown'} has a natural "
                             f"order{order}; columns keep it.", [{"op": "set_visual", "value": "columns"}], "columns"))
    # 4. Too many bars to read: the top 10.
    first = shape.categories[0] if len(shape.categories) == 1 else None
    if first and not spec.get("limit") and shape.distinct_counts.get(first, 0) > MANY_BARS and not ordered:
        n = shape.distinct_counts[first]
        out.append(Candidate(f"Show the top {TOP_N}", f"{n} bars are hard to read; rank them and keep the top "
                             f"{TOP_N} (the table still lists all).", [{"op": "set_limit", "value": TOP_N}],
                             "ranked_bar"))
    # 5. A time range without a comparison.
    if (spec.get("time") or {}).get("range") and not spec.get("compare"):
        out.append(Candidate("Compare with the previous period", "Set this period against the previous one of the "
                             "same length, so the change shows.", [{"op": "compare_previous"}]))
    seen: set[str] = set()
    unique = []
    for c in out:
        if c.key() not in seen:
            seen.add(c.key())
            unique.append(c)
    return unique


# --------------------------------------------------------------------------- the check (RULE-008 and certification)


def _core(spec: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in spec.items() if k != "visual" and v not in (None, [], {})}


async def check(ctx: Context, cand: Candidate, run: Runner) -> dict[str, Any] | None:
    """The suggestion as shown, or None when it can't be applied safely."""
    if any(o.get("op") not in SUGGEST_OPS for o in cand.ops):
        return None
    try:
        spec, said = apply_ops(ctx.spec, cand.ops, ctx.snap)
    except (TelaError, ValueError, TypeError):
        return None
    # Only certified measures the person can see, only dimensions they can see.
    allowed = {m.key for m in ctx.measures if m.status == "certified"}
    if not (_spec_measures(spec) - _spec_measures(ctx.spec)) <= allowed:
        return None
    dims = {d.key for d in ctx.dims}
    new_dims = {ref_key(b["ref"]) for b in spec.get("breakdowns", [])} - {ref_key(b["ref"])
                                                                         for b in ctx.spec.get("breakdowns", [])}
    if not new_dims <= dims:
        return None
    result = ctx.result
    if any(o["op"] in SHAPE_OPS for o in cand.ops):
        try:
            result = await run({k: v for k, v in spec.items() if k not in ("compare", "visual")})
        except TelaError:
            return None
        if result.get("hidden"):
            return None
    shape = result_shape(result, ctx.snap, target=has_target(spec))
    suitable = {s["visual"] for s in suitability(shape) if s["suitable"]} | {"table"}
    asked = next((str(o.get("value")) for o in cand.ops if o["op"] == "set_visual"), None)
    if asked is not None and asked not in suitable:
        return None  # a suggestion never proposes a chart RULE-008 refuses
    visual = cand.visual if cand.visual in suitable else asked or (ctx.visual if ctx.visual in suitable else
                                                                  choose_visual(shape)[0])
    ops = [o for o in cand.ops if o["op"] != "set_visual"]
    if visual != ctx.visual:
        ops.append({"op": "set_visual", "value": visual})
    spec, said = apply_ops(ctx.spec, ops, ctx.snap)
    if not ops or (_core(spec) == _core(ctx.spec) and visual == ctx.visual):
        return None  # nothing would change
    digest = hashlib.sha256(json.dumps(ops, sort_keys=True).encode()).hexdigest()[:12]
    return {"id": digest, "kind": cand.kind, "title": cand.title, "reason": cand.reason, "ops": ops,
            "visual": visual, "changes": said, "source": cand.source, "pattern": cand.pattern, "cites": cand.cites}


# --------------------------------------------------------------------------- AG-07 with a language model


class AdviceItem(BaseModel):
    candidate: str | None = Field(default=None, max_length=8)  # pick a listed candidate by id …
    ops: list[dict[str, Any]] = Field(default_factory=list, max_length=3)  # … or propose ops of the listed kinds
    title: str = Field(default="", max_length=80)
    reason: str = Field(default="", max_length=200)
    cites: list[str] = Field(default_factory=list, max_length=3)


class AdviceOut(BaseModel):
    suggestions: list[AdviceItem] = Field(default_factory=list, max_length=MAX_SUGGESTIONS)
    looks_good: bool = False


AG07_SYSTEM = (
    "You are Tela's visual advisor (AG-07). Suggest at most three changes that make this chart clearer or more "
    "useful: a better-suited visual, a comparison, a related certified measure, a breakdown or a top-N cut. You see "
    "the chart's shape only. Pick listed candidates by id, or propose ops of these kinds only: set_visual "
    "{value}, add_measure {ref}, add_breakdown {ref}, set_limit {value}, sort {dir}, compare_previous. Use only "
    "the refs listed. Never propose a visual listed as unsuitable. Each suggestion needs a short title and a "
    "one-sentence reason a business reader understands; do not state figures. If the chart is fine, return "
    "looks_good true and no suggestions."
)


def prompt(ctx: Context, cands: list[Candidate]) -> str:
    shape = ctx.shape
    labels = {c["name"]: c["label"] for c in ctx.result.get("columns", [])}
    table = suitability(shape)
    main = ctx.snap.measure(ref_key(ctx.spec["measure"]["ref"]))
    entity = main.entity if main else None
    measures = [m for m in ctx.measures if m.status == "certified" and m.entity == entity][:20]
    reach = [d for d in ctx.dims if entity and ctx.snap.join_path(entity, d.entity) is not None][:20]
    return json.dumps({
        "chart": {"measure": _label(ctx, ctx.spec["measure"]["ref"]),
                  "extra_measures": [_label(ctx, m["ref"]) for m in ctx.spec.get("extra_measures", [])],
                  "breakdowns": [_label(ctx, b["ref"]) for b in ctx.spec.get("breakdowns", [])],
                  "time": ctx.spec.get("time"), "top_n": ctx.spec.get("limit"), "visual": ctx.visual},
        "shape": {"rows": shape.row_count,
                  "columns": [{"label": labels.get(n, n), "kind": k, "distinct": shape.distinct_counts.get(n)}
                              for k, names in (("category", shape.categories), ("date", shape.dates),
                                               ("measure", shape.measures)) for n in names]},
        "suitable_visuals": [s["visual"] for s in table if s["suitable"]] + ["table"],
        "unsuitable_visuals": [{"visual": s["visual"], "reason": s["reason"]} for s in table if not s["suitable"]],
        "candidates": [{"id": f"c{i}", "title": c.title, "reason": c.reason, "ops": c.ops}
                       for i, c in enumerate(cands)],
        "measures": [{"ref": f"measure:{m.key}", "label": m.label} for m in measures],
        "dimensions": [{"ref": f"dimension:{d.key}", "label": d.label} for d in reach if not d.is_temporal],
    }, default=str)


def _numbers_ok(text_: str, ctx: Context) -> bool:
    """A reason may only repeat counts from the shape it was given (no invented figures)."""
    shape = ctx.shape
    known = {str(shape.row_count), *map(str, shape.distinct_counts.values()), "7", "10", "12", "25", "30", "60",
             "90"}
    return all(n in known for n in re.findall(r"\d+", text_))


def from_llm(out: AdviceOut, ctx: Context, cands: list[Candidate], hits: list[Hit]) -> list[Candidate]:
    supplied = {h.ref for h in hits}
    allowed = wording.allowed_from(ctx.snap)
    picked: list[Candidate] = []
    for item in out.suggestions[:MAX_SUGGESTIONS]:
        cites = [c for c in item.cites if c in supplied]
        if item.candidate and re.fullmatch(r"c\d{1,2}", item.candidate) and int(item.candidate[1:]) < len(cands):
            base = cands[int(item.candidate[1:])]
            reason = item.reason if item.reason and _numbers_ok(item.reason, ctx) and \
                wording.ok(item.reason, allowed=allowed) else base.reason
            picked.append(Candidate(base.title, reason, base.ops, base.visual, base.source, base.pattern,
                                    cites or base.cites, base.fix))
            continue
        ops = [o for o in item.ops if isinstance(o, dict) and o.get("op") in SUGGEST_OPS]
        if not ops or len(ops) != len(item.ops) or not item.title or not item.reason \
                or not _numbers_ok(item.title + " " + item.reason, ctx) \
                or not wording.ok(item.title + " " + item.reason, allowed=allowed):
            continue
        visual = next((str(o.get("value")) for o in ops if o["op"] == "set_visual"), None)
        clean = [{k: v for k, v in o.items() if k in ("op", "value", "ref", "dir")} for o in ops]
        picked.append(Candidate(item.title.strip(), item.reason.strip(), clean, visual if visual in VISUALS else None,
                                "llm", None, cites))
    return picked


# --------------------------------------------------------------------------- advise


async def advise(ctx: Context, run: Runner, *, gateway: GatewayClient | None = None, use_llm: bool = False,
                 hits: list[Hit] | None = None, tenant_id: str = "", user_id: str | None = None) -> dict[str, Any]:
    """Up to three checked suggestions for this chart, or "Looks good as is"."""
    if ctx.result.get("hidden") or not ctx.result.get("columns"):
        return {"suggestions": [], "message": LOOKS_GOOD, "source": "rules"}
    hits = hits or []
    by_pattern = {h.metadata.get("pattern"): h.ref for h in hits if h.metadata.get("pattern")}
    cands = candidates(ctx)
    for c in cands:
        if c.pattern and c.pattern in by_pattern:
            c.cites = [by_pattern[c.pattern]]
    source = "rules"
    ordered = cands
    if use_llm and gateway is not None:
        try:
            out = await run_agent(gateway, AdviceOut, tenant_id=tenant_id, user_id=user_id, agent="AG-07",
                                  task="visual_advice", system="\n\n".join(p for p in (
                                      AG07_SYSTEM, context_block(hits, limit_chars=4000)) if p),
                                  user=prompt(ctx, cands))
            chosen = from_llm(out, ctx, cands, hits)
            if chosen or out.looks_good:
                source = "llm"
                # An unsuitable visual is always fixed first, whatever the model says (RULE-008).
                ordered = [c for c in cands if c.fix] + chosen + ([] if out.looks_good and not chosen else cands)
        except AgentFailed:
            pass
    shown: list[dict[str, Any]] = []
    seen: set[str] = set()
    for cand in ordered:
        if len(shown) >= MAX_SUGGESTIONS:
            break
        s = await check(ctx, cand, run)
        if s is None or s["id"] in seen:
            continue
        seen.add(s["id"])
        shown.append(s)
    return {"suggestions": shown, "message": None if shown else LOOKS_GOOD, "source": source}
