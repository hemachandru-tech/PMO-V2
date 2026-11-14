"""AG-05 report plan reviewed by the LLM (ENH-026, US-243; FEAT-076 / FEAT-023; DEC-28).

With the tenant's LLM review on, the rules plan goes to the LLM in plain words (blueprint or theme, headline figures,
sections, widgets with their visuals, page filters, "Not included" gaps) beside the certified measures and dimensions
in the Creator's scope and the pack's blueprints by title. The LLM confirms it or replies with a title, a section
order and, per widget, keep / remove / replace / add, naming measures and dimensions by key only.

The rules stay in charge of what is built: every changed or added widget is checked under the Creator's scope (the
caller's ``validate``) and gets its visual from ``plan_visual`` (RULE-008); an unknown key or a widget that fails is
left out with a note; the gaps are the rules' (the LLM can't claim data the model lacks); the title passes the
wording check. A refined plan says "Refined by AI" with what changed and keeps the rules plan beside it, so "Show the
rules plan" puts it back (a correction never replaces the draft silently).
"""

from __future__ import annotations

import copy
import json
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, Field

from tela_api.content import wording
from tela_api.llm import trace
from tela_api.llm.review import Review
from tela_query_spec.spec import ref_key

if TYPE_CHECKING:
    from tela_api.content.planner import Planner

MAX_CHANGES = 24
AI_SECTION = "ai_added"

AG05_SYSTEM = (
    "You are Tela's report planner (AG-05). The draft is the report plan Tela's rules made for the request, built "
    "only from certified measures. If the plan fits the request, confirm it. Otherwise correct it: give the report "
    "title, the section order (section keys, the headline section stays first) and one entry per widget you change: "
    "\"keep\", \"remove\" or \"replace\" name the widget by its ref; \"add\" names the section key it goes in "
    "(\"headline\" for a figure in the headline row). A replaced or added widget names one measure key and at most two "
    "dimension keys from the context, a short title and a visual if the request asks for one. Never invent a key, "
    "never add what the gaps say the model has no data for, and never claim actual spend."
)


class WidgetChange(BaseModel):
    action: Literal["keep", "remove", "replace", "add"]
    ref: str | None = Field(default=None, max_length=40)
    section: str | None = Field(default=None, max_length=80)
    title: str | None = Field(default=None, max_length=120)
    measure: str | None = Field(default=None, max_length=120)
    breakdowns: list[str] = Field(default_factory=list, max_length=2)
    visual: str | None = Field(default=None, max_length=40)


class ReportPlanOut(BaseModel):
    title: str = Field(default="", max_length=160)
    section_order: list[str] = Field(default_factory=list, max_length=30)
    widgets: list[WidgetChange] = Field(default_factory=list, max_length=MAX_CHANGES * 2)


def _widget_words(w: dict[str, Any]) -> dict[str, Any]:
    spec = w["spec"]
    return {"ref": w["ref"], "title": w["title"], "visual": w["visual"], "measure": ref_key(spec["measure"]["ref"]),
            "extra_measures": [ref_key(m["ref"]) for m in spec.get("extra_measures", [])],
            "breakdowns": [ref_key(b["ref"]) for b in spec.get("breakdowns", [])],
            "filtered": bool(spec.get("filters"))}


def draft(plan: dict[str, Any]) -> dict[str, Any]:
    """The rules plan in plain words: what the LLM reviews (no rows; specs by key)."""
    return {
        "title": plan["title"],
        "layouts": [s["blueprint"]["title"] if s.get("blueprint") else s["title"] for s in plan["sections"]
                    if s["kind"] != "headline"],
        "headline_figures": [h["label"] for h in plan["headline_figures"]],
        "sections": [{"key": s["key"], "title": s["title"], "kind": s["kind"],
                      "widgets": [_widget_words(w) for w in s["widgets"]]} for s in plan["sections"]],
        "page_filters": [f["label"] for f in plan["page_filters"]],
        "gaps": [f"{g['request_part']}: {g['reason']}" for g in plan["gaps"]],
    }


def context(planner: Planner) -> dict[str, Any]:
    """The certified measures and dimensions in the Creator's scope, and the pack's blueprints by title."""
    return {
        "measures": [{"key": m.key, "label": m.label} for m in planner.visible.values()
                     if m.entity in planner.src.scope.entities],
        "dimensions": [{"key": d.key, "label": d.label} for d in planner.src.dims],
        "blueprints": [bp.title for bp in planner.blueprints],
    }


def _plain(plan: dict[str, Any]) -> dict[str, Any]:
    """The plan without its review (what "Show the rules plan" and the AI plan swap between)."""
    return {k: v for k, v in plan.items() if k != "review"}


Validate = Callable[[dict[str, Any]], Awaitable[str | None]]


async def apply(planner: Planner, rules: dict[str, Any], got: Review[ReportPlanOut], validate: Validate,
                headline_max: int, total_max: int) -> dict[str, Any]:
    """The plan after the LLM's verdict. ``rules`` is returned unchanged when review is off; otherwise it carries a
    ``review`` with the verdict and, for a correction that changed anything, what changed and the rules plan to go
    back to. ``validate`` returns why a widget can't be planned under the Creator's scope (None when it can)."""
    if got.verdict == "off":
        return rules
    review: dict[str, Any] = {"verdict": got.verdict, "reason": got.reason, "changes": [], "added": [],
                              "removed": [], "dropped": [], "showing": "rules"}
    if got.verdict == "ask":
        review["dropped"].append("The AI read your request more than one way, so the rules plan is shown.")
    if got.verdict != "correct" or got.replacement is None:
        return {**rules, "review": review}

    out = got.replacement
    plan = copy.deepcopy(_plain(rules))
    sections: list[dict[str, Any]] = plan["sections"]
    planner.n = max([planner.n, *(int(w["ref"][1:]) for s in sections for w in s["widgets"]
                                  if w["ref"][1:].isdigit())])
    where = {w["ref"]: s for s in sections for w in s["widgets"]}
    dropped: list[str] = review["dropped"]
    changes: list[str] = review["changes"]

    async def build(c: WidgetChange, headline: bool) -> dict[str, Any] | None:
        said = c.title or c.measure or "a widget"
        m = planner.visible.get(c.measure or "")
        if m is None or m.entity not in planner.src.scope.entities:
            dropped.append(f"Left out “{said}”: “{c.measure}” is not a certified measure you can use.")
            return None
        dims = {d.key: d for d in planner.src.dims}
        if unknown := [k for k in c.breakdowns if k not in dims]:
            dropped.append(f"Left out “{said}”: “{unknown[0]}” is not a dimension you can use.")
            return None
        if headline and c.breakdowns:
            dropped.append(f"Left out “{said}”: a headline figure has no breakdown.")
            return None
        if any(planner.snap.join_path(m.entity, dims[k].entity) is None for k in c.breakdowns):
            dropped.append(f"Left out “{said}”: {m.label} can't be broken down that way.")
            return None
        title = (c.title or (f"{m.label} by {' and '.join(dims[k].label.lower() for k in c.breakdowns)}"
                             if c.breakdowns else m.label)).strip()[:120]
        title = wording.fix(title, allowed=wording.allowed_from(planner.snap))
        w = planner.widget({"measure": {"ref": f"measure:{m.key}"},
                            "breakdowns": [{"ref": f"dimension:{k}"} for k in c.breakdowns]},
                           title, "figure" if headline else c.visual)
        known = {json.dumps(x["spec"], sort_keys=True) for s in sections for x in s["widgets"]}
        if json.dumps(w["spec"], sort_keys=True) in known:
            dropped.append(f"Left out “{title}”: the plan already has it.")
            return None
        if reason := await validate(w):
            dropped.append(f"Left out “{title}”: {reason}")
            return None
        return w

    for c in out.widgets[:MAX_CHANGES * 2]:
        if c.action == "keep":
            continue
        if c.action in ("remove", "replace"):
            sec = where.get(c.ref or "")
            if sec is None:
                dropped.append(f"Ignored a change to “{c.ref}”: the plan has no such widget.")
                continue
            old = next(w for w in sec["widgets"] if w["ref"] == c.ref)
            if c.action == "remove":
                sec["widgets"].remove(old)
                del where[old["ref"]]
                changes.append(f"Removed {old['title']}")
                review["removed"].append(old["title"])
                continue
            new = await build(c, sec["kind"] == "headline")
            if new is None:
                continue
            sec["widgets"][sec["widgets"].index(old)] = new
            del where[old["ref"]]
            where[new["ref"]] = sec
            changes.append(f"Replaced {old['title']} with {new['title']}")
            review["removed"].append(old["title"])
            review["added"].append(new["title"])
            continue
        # add
        if sum(len(s["widgets"]) for s in sections) >= total_max:
            dropped.append(f"Left out “{c.title or c.measure}”: the plan already has {total_max} widgets.")
            continue
        key = c.section or AI_SECTION
        sec = next((s for s in sections if s["key"] == key), None)
        headline = key == "headline"
        new = await build(c, headline)
        if new is None:
            continue
        if headline and sec is not None and len(sec["widgets"]) >= headline_max:
            dropped.append(f"Left out “{new['title']}”: the headline row is full.")
            continue
        if sec is None and headline:
            sec = {"key": "headline", "title": "Headline figures", "kind": "headline", "widgets": []}
            sections.insert(0, sec)
        elif sec is None:
            sec = {"key": AI_SECTION, "title": "Also for your request", "kind": "normal", "widgets": []}
            sections.append(sec)
        sec["widgets"].append(new)
        where[new["ref"]] = sec
        changes.append(f"Added {new['title']}")
        review["added"].append(new["title"])

    plan["sections"] = sections = [s for s in sections if s["widgets"]]
    if out.section_order:
        rank = {k: i for i, k in enumerate(dict.fromkeys(out.section_order))}
        ordered = sorted(sections, key=lambda s: (s["kind"] != "headline", rank.get(s["key"], len(rank))))
        if [s["key"] for s in ordered] != [s["key"] for s in sections]:
            changes.append("Changed the section order")
        plan["sections"] = sections = ordered
    title = wording.fix(out.title.strip()[:120], allowed=wording.allowed_from(planner.snap)) if out.title else ""
    if title and title != plan["title"]:
        changes.append(f"Titled it “{title}”")
        plan["title"] = title

    if not changes or not sections:
        if changes:
            dropped.append("The AI's plan had nothing left to build, so the rules plan is shown.")
        review["changes"], review["added"], review["removed"] = [], [], []
        await trace.step("decision", "AG-05 review changed nothing; the rules plan stands", agent="AG-05",
                         detail={"dropped": dropped[:10]})
        return {**rules, "review": review}
    plan["headline_figures"] = [{"ref": w["ref"], "measure": w["spec"]["measure"]["ref"], "label": w["title"]}
                                for s in sections if s["kind"] == "headline" for w in s["widgets"]]
    from tela_api.content.planner import layout_preview  # planner imports this module

    plan["layout_preview"] = layout_preview(sections)
    plan["gaps"] = copy.deepcopy(rules["gaps"])  # the rules' "Not included" stands whatever the LLM says
    review["showing"] = "ai"
    review["alternate"] = _plain(rules)
    await trace.step("decision", f"AG-05 plan refined by AI: {len(changes)} changes, {len(dropped)} left out",
                     agent="AG-05", detail={"changes": changes, "dropped": dropped[:10]})
    return {**plan, "review": review}


def swap(plan: dict[str, Any], show: Literal["ai", "rules"]) -> dict[str, Any]:
    """The plan showing the AI plan or the rules plan ("Show the rules plan"), each keeping the other to go back."""
    review = plan.get("review") or {}
    if review.get("showing") == show or "alternate" not in review:
        return plan
    other = review["alternate"]
    return {**other, "plan_id": plan.get("plan_id"),
            "review": {**review, "showing": show, "alternate": _plain({k: v for k, v in plan.items()
                                                                        if k != "plan_id"})}}


def public(plan: dict[str, Any]) -> dict[str, Any]:
    """The plan as the API returns it: the plan to go back to stays on the server."""
    if "review" not in plan:
        return plan
    return {**plan, "review": {k: v for k, v in plan["review"].items() if k != "alternate"}}
