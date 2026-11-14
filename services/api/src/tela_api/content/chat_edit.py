"""Edit a report with chat (AG-06; API-028; FEAT-025, FEAT-026; US-070).

A sentence becomes **LayoutOps** (resize, move, remove, set_visual, set_limit, add_filter) that are applied
to a copy of the report and returned as a preview list with the affected items highlighted. Nothing changes
until Apply; Apply is one undoable step. A target that matches two widgets equally becomes a clarifying
question; an unsuitable chart is refused with an alternative while the other ops still apply (RULE-008).
A preview is bound to the revision it was made from: if the report changed since, Apply asks for a new
preview. With LLM review on, the rules' reading is reviewed by the LLM first (US-244, ``chat_review``).
"""

from __future__ import annotations

import copy
import json
import re
from typing import Any

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import text

from tela_api.ask.planner import STOP, VISUAL_WORDS, match_dimension, norm, words
from tela_api.content import chat_review
from tela_api.content.access import own_report
from tela_api.content.common import guard_visual, refusal
from tela_api.content.layout import COLS, Box, settle
from tela_api.content.reports import mutate, report_state, restore_state
from tela_api.core.db import fetch_one, tenant_tx
from tela_api.identity.deps import require
from tela_api.identity.permissions import Perm
from tela_api.identity.principal import Principal
from tela_api.llm import trace
from tela_api.llm.review import review as llm_review
from tela_api.llm.router import tenant_llm_settings
from tela_api.model.store import load_snapshot
from tela_api.query.service import QueryContext, QueryService
from tela_common.errors import ErrorCode, TelaError
from tela_common.ids import new_id

router = APIRouter(prefix="/api/v1", tags=["reports"])

SIZE_WORDS = {"full width": 12, "full-width": 12, "full": 12, "half width": 6, "half-width": 6, "half": 6,
              "third": 4, "quarter": 3}
CATALOGUE_EXTRA = {"map", "gauge", "scatter", "heatmap", "treemap", "sankey", "funnel", "radar", "bubble"}


class ChatEditIn(BaseModel):
    instruction: str = Field(min_length=1, max_length=1000)
    # The instruction answers a clarifying question (the person chose): read by the rules, not reviewed again.
    answered: bool = False


class Unresolved(Exception):
    def __init__(self, question: dict[str, Any]) -> None:
        self.question = question


def _widgets(state: dict[str, Any]) -> list[dict[str, Any]]:
    return [i for i in state["items"] if i["kind"] == "widget"]


def find_target(state: dict[str, Any], phrase: str) -> dict[str, Any] | None:
    """Best widget for a phrase; two equally good candidates raise ``Unresolved`` (a clarifying question)."""
    p = norm(re.sub(r"\b(widget|chart|figure|table|the)\b", " ", phrase))
    pw = {w for w in words(p) if w not in STOP}
    if not pw and not p:
        return None
    scored = []
    for i in _widgets(state):
        title = state["widgets"][i["widget_id"]]["title"]
        t = norm(title)
        tw = set(words(title))
        # Tiers, not lengths, so "open by" ties between "Open by region" and "Open by state".
        if p == t:
            score = 3.0
        elif p and re.search(rf"\b{re.escape(p)}\b", t):
            score = 2.0
        elif pw and pw <= tw:
            score = 1.5
        elif pw & tw:
            score = len(pw & tw) / len(pw)
        else:
            continue
        scored.append((score, i, title))
    if not scored:
        return None
    scored.sort(key=lambda s: -s[0])
    best = scored[0][0]
    if best < 0.5:
        return None
    tied = [s for s in scored if abs(s[0] - best) < 1e-9]
    if len(tied) > 1:
        raise Unresolved({"question": f"Which widget do you mean by “{phrase.strip()}”?",
                          "options": [{"item_id": s[1]["id"], "title": s[2]} for s in tied[:5]]})
    return scored[0][1]


def parse(instruction: str, state: dict[str, Any], dims: list[Any],
          seen: list[dict[str, Any]] | None = None) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Clauses → ops and refusals (ops refer to item ids). ``seen`` gets, per op, the words that named its widget and
    the other widget of a "next to" move (US-244: the LLM review asks about a target it reads differently)."""
    ops: list[dict[str, Any]] = []
    seen = seen if seen is not None else []

    def add(op: dict[str, Any], phrase: str | None, next_to: str | None = None) -> None:
        ops.append(op)
        seen.append({"phrase": phrase, "next_to": next_to})

    refused: list[dict[str, Any]] = []
    clauses = re.split(r";|\bthen\b|,\s*and\b|\band\b(?=\s+(?:make|move|remove|delete|drop|show|put|change|limit|"
                       r"add|turn|set|resize))", instruction, flags=re.I)
    for raw in clauses:
        c = norm(raw)
        if not c:
            continue
        if m := re.match(r"^(make|set|resize)\s+(?P<t>.+?)\s+(?P<how>wider|bigger|larger|full width|full|half width|"
                         r"half|a third|third|a quarter|quarter|smaller|narrower|taller|shorter)( width)?$", c):
            item = find_target(state, m.group("t"))
            if item is None:
                refused.append({"request": raw.strip(), "reason": f"No widget is called “{m.group('t')}”."})
                continue
            how = m.group("how").replace("a ", "")
            w, h = item["w"], item["h"]
            if how in SIZE_WORDS:
                w = SIZE_WORDS[how]
            elif how in ("wider", "bigger", "larger"):
                w = min(COLS, w + 3)
                h = h if how == "wider" else h + 1
            elif how in ("smaller", "narrower"):
                w = max(2, w - 3)
            elif how == "taller":
                h = min(24, h + 2)
            elif how == "shorter":
                h = max(2, h - 2)
            add({"op": "resize", "item_id": item["id"], "to": {"w": w, "h": h}, "from": {"w": item["w"],
                                                                                        "h": item["h"]}},
                m.group("t"))
        elif m := re.match(r"^(move|put)\s+(?P<t>.+?)\s+(?P<where>to the top|to top|first|to the bottom|to bottom|"
                           r"last|to (the )?(?P<sec>.+?) section|into (the )?(?P<sec2>.+?) section|next to "
                           r"(?P<other>.+))$", c):
            item = find_target(state, m.group("t"))
            if item is None:
                refused.append({"request": raw.strip(), "reason": f"No widget is called “{m.group('t')}”."})
                continue
            where = m.group("where")
            sec_name = m.group("sec") or m.group("sec2")
            if sec_name:
                sec = next((s for s in state["sections"] if sec_name in norm(s["title"])), None)
                if sec is None:
                    refused.append({"request": raw.strip(), "reason": f"No section is called “{sec_name}”."})
                    continue
                add(move_to_section(item, sec), m.group("t"))
            elif m.group("other"):
                other = find_target(state, m.group("other"))
                if other is None:
                    refused.append({"request": raw.strip(), "reason": f"No widget is called “{m.group('other')}”."})
                    continue
                add(move_next_to(item, other), m.group("t"), other["id"])
            else:
                add(move_end(item, where in ("to the top", "to top", "first")), m.group("t"))
        elif m := re.match(r"^(remove|delete|drop|take out)\s+(?P<t>.+)$", c):
            item = find_target(state, m.group("t"))
            if item is None:
                refused.append({"request": raw.strip(), "reason": f"No widget is called “{m.group('t')}”."})
                continue
            add({"op": "remove", "item_id": item["id"]}, m.group("t"))
        elif m := re.match(r"^(show|display|change|turn|make|set)\s+(?P<t>.+?)\s+(as|into|to)\s+(an?\s+)?"
                           r"(?P<v>[a-z ]+?)( chart)?$", c):
            v = m.group("v").strip()
            visual = (VISUAL_WORDS.get(v) or VISUAL_WORDS.get(v.rstrip("s"))
                      or (VISUAL_WORDS.get(f"{v} chart") if m.group(6) else None))  # "a control chart" (US-187)
            if visual is None and m.group("v").strip() in ("ranked bars", "ranked bar"):
                visual = "ranked_bar"
            item = find_target(state, m.group("t"))
            if item is None:
                refused.append({"request": raw.strip(), "reason": f"No widget is called “{m.group('t')}”."})
                continue
            if visual is None:
                refused.append({"request": raw.strip(), "item_id": item["id"],
                                "reason": f"{m.group('v').strip().capitalize()}: Not available yet",
                                "alternative": None})
                continue
            add({"op": "set_visual", "item_id": item["id"], "to": visual,
                 "from": state["widgets"][item["widget_id"]]["visual"]}, m.group("t"))
        elif m := (re.match(r"^(limit|cut|trim)\s+(?P<t>.+?)\s+to\s+(the\s+)?top\s+(?P<n>\d{1,4})$", c)
                   or re.match(r"^(show\s+)?(only\s+)?(the\s+)?top\s+(?P<n>\d{1,4})\s+(in|for|on)\s+(?P<t>.+)$", c)):
            item = find_target(state, m.group("t"))
            if item is None:
                refused.append({"request": raw.strip(), "reason": f"No widget is called “{m.group('t')}”."})
                continue
            add({"op": "set_limit", "item_id": item["id"], "to": int(m.group("n")),
                 "from": state["widgets"][item["widget_id"]]["spec"].get("limit")}, m.group("t"))
        elif m := re.match(r"^(add|include)\s+(an?\s+)?(?P<d>.+?)\s+filter$|^filter (the page |the report )?by "
                           r"(?P<d2>.+)$", c):
            phrase = m.group("d") or m.group("d2")
            d = match_dimension(phrase, [x for x in dims if not x.is_temporal])
            if d is None:
                refused.append({"request": raw.strip(), "reason": f"There is no “{phrase}” to filter by."})
                continue
            add({"op": "add_filter", "ref": f"dimension:{d.key}", "label": d.label}, phrase)
        else:
            refused.append({"request": raw.strip(), "reason": "I couldn't turn that into a layout change. Try “make "
                            "X full width”, “move X to the top”, “remove X”, “show X as columns”, “top 5 in X” or "
                            "“add a region filter”."})
    return ops, refused


def move_end(item: dict[str, Any], top: bool) -> dict[str, Any]:
    """Move to the top or the bottom of the item's own section."""
    return {"op": "move", "item_id": item["id"],
            "to": {"section_id": item["section_id"], "x": 0 if top else item["x"], "y": -1 if top else 999}}


def move_to_section(item: dict[str, Any], sec: dict[str, Any]) -> dict[str, Any]:
    return {"op": "move", "item_id": item["id"], "to": {"section_id": sec["id"], "x": 0, "y": 999}}


def move_next_to(item: dict[str, Any], other: dict[str, Any]) -> dict[str, Any]:
    """Beside ``other`` when the row has room, else under it."""
    x = other["x"] + other["w"]
    to = {"section_id": other["section_id"], "x": x, "y": other["y"]} if x + item["w"] <= COLS else \
        {"section_id": other["section_id"], "x": other["x"], "y": other["y"] + other["h"]}
    return {"op": "move", "item_id": item["id"], "to": to}


def apply(state: dict[str, Any], ops: list[dict[str, Any]]) -> dict[str, Any]:
    """Apply layout ops to a copy of a report state and settle every touched section."""
    out = copy.deepcopy(state)
    touched: dict[str, str | None] = {}
    for op in ops:
        item = next((i for i in out["items"] if i["id"] == op.get("item_id")), None)
        if op["op"] == "add_filter":
            if all(f["ref"] != op["ref"] for f in out["page_filters"]):
                out["page_filters"].append({"ref": op["ref"]})
            continue
        if item is None:
            continue
        if op["op"] == "remove":
            out["items"].remove(item)
            touched[item["section_id"]] = None
        elif op["op"] == "resize":
            item["w"], item["h"] = op["to"]["w"], op["to"]["h"]
            item["x"] = min(item["x"], COLS - item["w"])
            touched[item["section_id"]] = item["id"]
        elif op["op"] == "move":
            old = item["section_id"]
            item["section_id"], item["x"], item["y"] = op["to"]["section_id"], op["to"]["x"], op["to"]["y"]
            if item["y"] < 0:  # to the top: everything else in the section moves down by this item's height
                for other in out["items"]:
                    if other is not item and other["section_id"] == item["section_id"]:
                        other["y"] += item["h"]
                item["y"] = 0
            touched[old] = None
            touched[item["section_id"]] = item["id"]
        elif op["op"] == "set_visual":
            w = out["widgets"][item["widget_id"]]
            w["visual"] = op["to"]
            w["spec"] = {**w["spec"], "visual": {"type": op["to"]}}
        elif op["op"] == "set_limit":
            w = out["widgets"][item["widget_id"]]
            w["spec"] = {**w["spec"], "limit": op["to"], "sort": w["spec"].get("sort") or [{"by": "measure",
                                                                                           "dir": "desc"}]}
    for sid, first in touched.items():
        boxes = [Box(i["id"], i["x"], i["y"], i["w"], i["h"]) for i in out["items"] if i["section_id"] == sid]
        for b in settle(boxes, first):
            i = next(i for i in out["items"] if i["id"] == b.id)
            i["x"], i["y"] = b.x, b.y
    return out


def describe(op: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    item = next((i for i in state["items"] if i["id"] == op.get("item_id")), None)
    target = state["widgets"][item["widget_id"]]["title"] if item and item.get("widget_id") else op.get("label")
    out = {"op": op["op"], "target": target, "item_id": op.get("item_id"), "from": op.get("from"),
           "to": op.get("to")}
    to: Any = op.get("to")
    # One summary per op, built only for its own op ("to" is a size, a place, a visual or a number).
    summary: dict[str, Any] = {
        "resize": lambda: f"Resize {target} to {to['w']} x {to['h']}",
        "move": lambda: f"Move {target}", "remove": lambda: f"Remove {target}",
        "set_visual": lambda: f"Show {target} as {str(to).replace('_', ' ')}",
        "set_limit": lambda: f"Show the top {to} in {target}",
        "add_filter": lambda: f"Add a {op.get('label')} page filter",
    }
    out["summary"] = summary[op["op"]]()
    return out


async def _guard(request: Request, principal: Principal, state: dict[str, Any],
                 ops: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Run each set_visual through the chart integrity guard; unsuitable ones become refusals."""
    service: QueryService = request.app.state.query_service
    kept, refused = [], []
    for op in ops:
        if op["op"] != "set_visual":
            kept.append(op)
            continue
        item = next(i for i in state["items"] if i["id"] == op["item_id"])
        w = state["widgets"][item["widget_id"]]
        try:
            result = await service.execute(w["spec"], principal, QueryContext(surface="preview"))
            async with tenant_tx(principal.tenant_id) as conn:
                snap = await load_snapshot(conn, principal.tenant_id, w["spec"]["model_version_id"])
            guard_visual(result, snap, op["to"])
            kept.append(op)
        except TelaError as exc:
            if exc.code != ErrorCode.VISUAL_NOT_SUITABLE:
                raise
            refused.append({"request": f"show {w['title']} as {op['to'].replace('_', ' ')}", "item_id": item["id"],
                            **refusal(exc)})
    return kept, refused


@router.post("/reports/{report_id}/chat-edit")
async def preview(report_id: str, body: ChatEditIn, request: Request,
                  principal: Principal = Depends(require(Perm.REPORTS_CREATE))) -> dict[str, Any]:
    async with trace.agent_run(principal.tenant_id, principal.user_id, "canvas_edit", title=body.instruction,
                               object_ref=f"report:{report_id}", agent="AG-06"):
        return await _preview(report_id, body, request, principal)


async def _preview(report_id: str, body: ChatEditIn, request: Request, principal: Principal) -> dict[str, Any]:
    async with tenant_tx(principal.tenant_id) as conn:
        report = await own_report(conn, principal, report_id)
        state = await report_state(conn, report["id"])
        dims: list[Any] = []
        for v in {w["spec"]["model_version_id"] for w in state["widgets"].values()}:
            dims += (await load_snapshot(conn, principal.tenant_id, v)).dimensions
        settings = await tenant_llm_settings(conn)
    seen: list[dict[str, Any]] = []
    question: dict[str, Any] | None = None
    ops: list[dict[str, Any]] = []
    refused: list[dict[str, Any]] = []
    try:
        ops, refused = parse(body.instruction, state, dims, seen)
    except Unresolved as u:
        question = u.question
    merged, reviewed = await _review(request, principal, body, state, dims, settings, ops, refused, seen, question)
    if merged is not None:
        if merged.question is not None:
            question = merged.question
        else:
            ops, refused = merged.ops, merged.refused
    if question is not None:
        await trace.step("decision", "Asked which was meant", agent="AG-06", detail={"question": question})
        trace.set_outcome("clarify")
        return {"preview_id": None, "changes": [], "highlights": [], "clarification": question, "review": reviewed}
    by_ai = merged.by_ai if merged is not None else set()
    ops, guard_refused = await _guard(request, principal, state, ops)
    refused += guard_refused
    await trace.step("rule", f"Read the instruction as {len(ops)} change(s), {len(refused)} refused", agent="AG-06",
                     detail={"ops": [op.get("op") for op in ops], "refused": refused,
                             "added_by_ai": sum(1 for op in ops if id(op) in by_ai)})
    after = apply(state, ops)
    changes = ([describe(op, state) | ({"by": "ai"} if id(op) in by_ai else {}) for op in ops]
               + [{"op": "refused", **r} for r in refused])
    preview_id = new_id()
    async with tenant_tx(principal.tenant_id) as conn:
        await conn.execute(
            text("INSERT INTO content.report_edit_previews (id, tenant_id, report_id, owner_id, instruction, ops, "
                 "changes, base_revision) VALUES (:id, :t, :r, :u, :i, CAST(:o AS jsonb), CAST(:c AS jsonb), :rev)"),
            {"id": preview_id, "t": principal.tenant_id, "r": report["id"], "u": principal.user_id,
             "i": body.instruction, "o": json.dumps(ops), "c": json.dumps(changes, default=str),
             "rev": report["draft_revision"]})
    return {"preview_id": preview_id if ops else None, "changes": changes, "clarification": None,
            "highlights": sorted({op["item_id"] for op in ops if op.get("item_id")}), "review": reviewed,
            "layout": {"sections": after["sections"], "items": after["items"],
                       "page_filters": after["page_filters"]}}


async def _review(request: Request, principal: Principal, body: ChatEditIn, state: dict[str, Any], dims: list[Any],
                  settings: dict[str, Any], ops: list[dict[str, Any]], refused: list[dict[str, Any]],
                  seen: list[dict[str, Any]], question: dict[str, Any] | None
                  ) -> tuple[chat_review.Merged | None, dict[str, Any] | None]:
    """US-244 (DEC-28): with LLM review on, one ``canvas_edit`` call reviews the rules' reading. Returns the merged
    reading (None: the rules' stands) and the verdict for the panel (None: review off). An answer to a question is
    not reviewed again; the rules' own question stands whatever the LLM says."""
    if body.answered:
        return None, None
    got = await llm_review(
        getattr(request.app.state, "gateway", None), chat_review.CanvasEditOut, settings=settings,
        tenant_id=principal.tenant_id, user_id=principal.user_id, agent="AG-06", task="canvas_edit",
        system=chat_review.SYSTEM, request=body.instruction,
        draft=chat_review.draft(ops, refused, seen, state, question),
        context={"widgets": chat_review.widgets(state), "dimensions": chat_review.dimensions(dims),
                 "sections": [s["title"] for s in state["sections"]]})
    if got.verdict == "off":
        return None, None
    verdict = {"verdict": got.verdict, "reason": got.reason, "notes": []}
    merged: chat_review.Merged | None = None
    if question is not None:  # the rules' own question stands
        merged = None
    elif got.verdict == "correct" and got.replacement is not None:
        merged = chat_review.merge(ops, refused, seen, got.replacement, state, dims, got.reason,
                                       instruction=body.instruction)
        verdict["notes"] = merged.notes
    elif got.verdict == "ask":
        q = chat_review.ask(ops, seen, got.options, state, dims, got.reason)
        if q is not None:
            merged = chat_review.Merged(question=q)
        else:
            verdict["notes"] = ["The AI's question had no options Tela could use, so the rules' reading stands."]
    await trace.step("validate", "Canvas edit review merged", agent="AG-06",
                     detail={"verdict": got.verdict, "asked": bool(merged and merged.question),
                             "added": len(merged.by_ai) if merged else 0, "notes": verdict["notes"]})
    return merged, verdict


async def _pending(conn: Any, principal: Principal, report_id: str, preview_id: str) -> dict[str, Any]:
    try:
        row = await fetch_one(conn, "SELECT * FROM content.report_edit_previews WHERE id = CAST(:p AS uuid) AND "
                                    "report_id = CAST(:r AS uuid) AND owner_id = :u",
                              {"p": preview_id, "r": report_id, "u": principal.user_id})
    except Exception:  # noqa: BLE001
        row = None
    if row is None or row["status"] != "pending":
        raise TelaError(ErrorCode.NOT_FOUND, "That preview is no longer available. Describe the change again.")
    return row


@router.post("/reports/{report_id}/chat-edit/{preview_id}/apply")
async def apply_preview(report_id: str, preview_id: str, request: Request, response: Response,
                        principal: Principal = Depends(require(Perm.REPORTS_CREATE))) -> dict[str, Any]:
    async def fn(conn: Any, report: dict[str, Any]) -> None:
        p = await _pending(conn, principal, report_id, preview_id)
        if p["base_revision"] != report["draft_revision"]:
            raise TelaError(ErrorCode.CONFLICT, "The report changed since this preview. Describe the change again.")
        state = await report_state(conn, report["id"])
        await restore_state(conn, report, apply(state, p["ops"]))
        await conn.execute(text("UPDATE content.report_edit_previews SET status = 'applied' WHERE id = :id"),
                           {"id": p["id"]})

    async with tenant_tx(principal.tenant_id) as conn:
        p = await _pending(conn, principal, report_id, preview_id)
    out, _ = await mutate(principal, report_id, source="chat", summary=f"Chat edit: {p['instruction'][:200]}", fn=fn,
                          response=response, request_id=getattr(request.state, "request_id", None),
                          audit_action="report.chat_edit_applied")
    return out


@router.delete("/reports/{report_id}/chat-edit/{preview_id}", status_code=204)
async def cancel(report_id: str, preview_id: str,
                 principal: Principal = Depends(require(Perm.REPORTS_CREATE))) -> Response:
    async with tenant_tx(principal.tenant_id) as conn:
        p = await _pending(conn, principal, report_id, preview_id)
        await conn.execute(text("UPDATE content.report_edit_previews SET status = 'cancelled' WHERE id = :id"),
                           {"id": p["id"]})
    return Response(status_code=204)
