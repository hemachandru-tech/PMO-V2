"""AG-06 canvas edits reviewed by the LLM (ENH-026, US-244; FEAT-076, FEAT-025; DEC-28).

The rules (``chat_edit.parse``) read the instruction first. With LLM review on, one ``canvas_edit`` call gets the
instruction, the report's widgets (id, title, visual, section, position, size), the dimensions that can become page
filters and the rules' reading as layout ops by widget id (or the rules' question or refusals). The reply is layout
ops by widget id or one question, and is merged here under DEC-28:

* an op the rules also made stands as the rules made it;
* an op the rules missed is added and marked as the AI's, so the Creator sees it in the preview before Apply;
* an op on a different widget from the rules' op of the same kind is the existing "Which widget do you mean?" question
  with both widgets (never a silent swap);
* an unknown widget id, op, section or dimension is dropped and listed as not changed;
* an op the rules missed must be on a widget (and, for "next to", beside a widget) or filter the instruction's words
  name, and an added move needs a placement word ("move", "top", "beside", a section's title …); otherwise it is
  left out with a note (US-246 gate: the AI's additions on unnamed widgets were its
  confident-wrong edits).

AG-06 is kept out of review by default (``llm_review_off_agents``, DEC-28 point 3: the US-246 final gate on qwen3:8b
left one confident-wrong preview, EXEC-169); this merge runs when it is put back in, by an Admin on the Agents
screen (US-268) or the deployment setting.

Every op that survives still goes through ``apply``, ``settle`` and ``_guard`` (RULE-008) in ``chat_edit``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, Field

from tela_api.ask.planner import VISUAL_WORDS, norm
from tela_api.content.layout import COLS

OPS = ("resize", "move", "remove", "set_visual", "set_limit", "add_filter")
SIZE_NAMES = {12: "full width", 6: "half width", 4: "a third", 3: "a quarter"}
VISUAL_NAMES: dict[str, str] = {}
for _word, _visual in VISUAL_WORDS.items():
    VISUAL_NAMES.setdefault(_visual, _word)
MAX_WIDGETS = 60
MAX_DIMENSIONS = 60
NOT_UNDERSTOOD = "Neither the rules nor the AI could turn that into a layout change."

SYSTEM = (
    "You are Tela's canvas editor (AG-06). A Creator described a change to a report in words. You get the instruction, "
    "the report's widgets (id, title, visual, section, x, y, w, h on a 12-column grid), the dimensions that can become "
    "page filters, and the rules' reading of the instruction as layout ops (or the rules' question or refusals). "
    "Layout ops: resize (widget, width 2-12, height 2-24), move (widget, and to: \"top\", \"bottom\" or a section "
    "title, or next_to: another widget's id), remove (widget), set_visual (widget, visual: a chart key such as "
    "ranked_bar, columns, donut, trend, table, figure), set_limit (widget, limit: show the top N), add_filter "
    "(dimension: a key from the dimensions). Name a widget only by an id from the widgets. A replacement lists every "
    "op the instruction asks for; put any part of the instruction you cannot turn into one of these ops in "
    "not_understood. Options of an \"ask\" are alternative op lists."
)


class LayoutOpOut(BaseModel):
    op: str = Field(max_length=40)
    widget: str | None = Field(default=None, max_length=80)
    width: int | None = None
    height: int | None = None
    to: str | None = Field(default=None, max_length=200)
    next_to: str | None = Field(default=None, max_length=80)
    visual: str | None = Field(default=None, max_length=40)
    limit: int | None = None
    dimension: str | None = Field(default=None, max_length=200)


class CanvasEditOut(BaseModel):
    ops: list[LayoutOpOut] = Field(default_factory=list, max_length=20)
    not_understood: list[str] = Field(default_factory=list, max_length=10)


@dataclass
class Merged:
    ops: list[dict[str, Any]] = field(default_factory=list)
    refused: list[dict[str, Any]] = field(default_factory=list)
    by_ai: set[int] = field(default_factory=set)  # id() of the ops the AI added
    question: dict[str, Any] | None = None
    notes: list[str] = field(default_factory=list)


def _items(state: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {i["id"]: i for i in state["items"] if i["kind"] == "widget"}


def _title(state: dict[str, Any], item: dict[str, Any]) -> str:
    return str(state["widgets"][item["widget_id"]]["title"])


def widgets(state: dict[str, Any]) -> list[dict[str, Any]]:
    """The report's widgets as the prompt names them: the canvas item id is the widget's id."""
    secs = {s["id"]: s["title"] for s in state["sections"]}
    return [{"id": i["id"], "title": _title(state, i), "visual": state["widgets"][i["widget_id"]]["visual"],
             "section": secs.get(i["section_id"]), "x": i["x"], "y": i["y"], "w": i["w"], "h": i["h"]}
            for i in list(_items(state).values())[:MAX_WIDGETS]]


def dimensions(dims: list[Any]) -> list[dict[str, str]]:
    out: dict[str, dict[str, str]] = {}
    for d in dims:
        if not d.is_temporal and d.key not in out:
            out[d.key] = {"key": d.key, "label": d.label}
    return list(out.values())[:MAX_DIMENSIONS]


def as_out(op: dict[str, Any], state: dict[str, Any], seen: dict[str, Any] | None = None) -> dict[str, Any]:
    """A rules op in the reply's own format, so the LLM confirms or corrects like for like."""
    o: dict[str, Any] = {"op": op["op"]}
    if op["op"] == "add_filter":
        return {**o, "dimension": str(op["ref"]).partition(":")[2]}
    o["widget"] = op["item_id"]
    to: Any = op.get("to")
    if op["op"] == "resize":
        o.update(width=to["w"], height=to["h"])
    elif op["op"] == "move":
        item = _items(state).get(op["item_id"], {})
        if seen and seen.get("next_to"):
            o["next_to"] = seen["next_to"]
        elif to["y"] < 0:
            o["to"] = "top"
        elif to["section_id"] == item.get("section_id"):
            o["to"] = "bottom"
        else:
            o["to"] = next((s["title"] for s in state["sections"] if s["id"] == to["section_id"]), None)
    elif op["op"] == "set_visual":
        o["visual"] = to
    elif op["op"] == "set_limit":
        o["limit"] = to
    return o


def draft(ops: list[dict[str, Any]], refused: list[dict[str, Any]], seen: list[dict[str, Any]],
          state: dict[str, Any], question: dict[str, Any] | None = None) -> dict[str, Any]:
    if question is not None:
        return {"clarify": question["question"], "options": [o["title"] for o in question["options"]]}
    return {"ops": [as_out(op, state, s) for op, s in zip(ops, seen, strict=False)],
            "refused": [{"request": r.get("request"), "reason": r.get("reason")} for r in refused]}


def _key(op: dict[str, Any]) -> tuple[str, str]:
    return op["op"], str(op.get("item_id") or op.get("ref"))


def _dimension(phrase: str, dims: list[Any]) -> Any:
    p = norm(phrase.removeprefix("dimension:"))
    return next((d for d in dims if not d.is_temporal and (norm(d.key) == p or norm(d.label) == p)), None)


def from_out(o: LayoutOpOut, state: dict[str, Any], dims: list[Any]) -> tuple[dict[str, Any] | None,
                                                                                dict[str, Any] | None]:
    """The AI's op as a chat-edit op, or why it was dropped (a refusal shown as not changed)."""
    items = _items(state)
    item = items.get(o.widget or "")
    name = _title(state, item) if item else (o.widget or "a widget")
    request = f"{o.op.replace('_', ' ')} {o.dimension if o.op == 'add_filter' else name}".strip()

    def no(reason: str) -> tuple[None, dict[str, Any]]:
        return None, {"request": request, "reason": reason, "by": "ai"}

    if o.op not in OPS:
        return no(f"“{o.op}” isn't a layout change Tela can make.")
    if o.op == "add_filter":
        d = _dimension(o.dimension or "", dims)
        if d is None:
            return no(f"There is no “{o.dimension or ''}” to filter by.")
        return {"op": "add_filter", "ref": f"dimension:{d.key}", "label": d.label}, None
    if item is None:
        return no(f"The AI named a widget that isn't on this report (“{o.widget or ''}”), so nothing changed for it.")
    widget = state["widgets"][item["widget_id"]]
    if o.op == "remove":
        return {"op": "remove", "item_id": item["id"]}, None
    if o.op == "resize":
        w = max(2, min(COLS, o.width if o.width is not None else item["w"]))
        h = max(2, min(24, o.height if o.height is not None else item["h"]))
        return {"op": "resize", "item_id": item["id"], "to": {"w": w, "h": h},
                "from": {"w": item["w"], "h": item["h"]}}, None
    if o.op == "move":
        from tela_api.content.chat_edit import move_end, move_next_to, move_to_section

        if o.next_to:
            other = items.get(o.next_to)
            if other is None or other is item:
                return no(f"The AI named a widget that isn't on this report (“{o.next_to}”), so nothing moved.")
            return move_next_to(item, other), None
        where = norm(o.to or "")
        if where in ("top", "the top", "first"):
            return move_end(item, True), None
        if where in ("bottom", "the bottom", "last", "end"):
            return move_end(item, False), None
        sec = next((s for s in state["sections"] if where and where in norm(s["title"])), None)
        if sec is None:
            return no(f"No section is called “{o.to or ''}”.")
        return move_to_section(item, sec), None
    if o.op == "set_visual":
        if not o.visual:
            return no("The AI didn't say which chart.")
        return {"op": "set_visual", "item_id": item["id"], "to": o.visual, "from": widget["visual"]}, None
    if o.limit is None or not 1 <= o.limit <= 1000:
        return no("A top-N limit needs a number from 1 to 1000.")
    return {"op": "set_limit", "item_id": item["id"], "to": o.limit, "from": widget["spec"].get("limit")}, None


def widget_question(phrase: str, item_ids: list[str], state: dict[str, Any], reason: str = "") -> dict[str, Any]:
    """The existing "Which widget do you mean?" question (the panel swaps the phrase for the chosen title)."""
    items = _items(state)
    out: dict[str, Any] = {"question": f"Which widget do you mean by “{phrase.strip()}”?",
                           "options": [{"item_id": i, "title": _title(state, items[i])}
                                       for i in dict.fromkeys(item_ids) if i in items][:5]}
    if reason:
        out["why"] = [reason]
    return out


STOP = frozenset({"by", "of", "the", "and", "per", "a", "an", "in", "for", "to", "vs"})


def _stem(word: str) -> str:
    w = word.lower()
    if len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
        w = w[:-1]
    return w[:5]


def _stems(text: str) -> set[str]:
    return {_stem(t) for t in norm(text).replace("-", " ").split() if t and t not in STOP}


def named(instruction: str, state: dict[str, Any]) -> set[str]:
    """The widgets the instruction's words name: it holds a word of the widget's title that at most half the
    widgets share ("region" names Open by region; "open" alone, on four of five titles, names none)."""
    words = _stems(instruction)
    titles = {i: _stems(_title(state, item)) for i, item in _items(state).items()}
    half = max(1, len(titles) // 2)
    out: set[str] = set()
    for i, stems in titles.items():
        distinct = {s for s in stems if sum(s in t for t in titles.values()) <= half} or stems
        if distinct & words:
            out.add(i)
    return out


PLACE_WORDS = frozenset({"move", "moved", "put", "place", "send", "bump", "shift", "top", "bottom", "beside", "next",
                         "first", "last", "end", "above", "below", "before", "after", "section", "under"})


def _placed(instruction: str, state: dict[str, Any]) -> bool:
    """The instruction asks for a new place: a placement word or a section's title (US-246 gate, ce11: a move added for
    "stretch … across the whole page")."""
    words = set(norm(instruction).replace("-", " ").split())
    if words & PLACE_WORDS:
        return True
    text = norm(instruction)
    return any(norm(s["title"]) and norm(s["title"]) in text for s in state["sections"])


def _named_op(op: dict[str, Any], instruction: str, names: set[str],
              seen_next: str | None) -> bool:
    if op["op"] == "add_filter":
        words = _stems(instruction)
        key = str(op["ref"]).partition(":")[2]
        return bool((_stems(op.get("label") or "") | _stems(key.replace("_", " "))) & words)
    if op.get("item_id") not in names:
        return False
    return seen_next is None or seen_next in names


def merge(rules_ops: list[dict[str, Any]], rules_refused: list[dict[str, Any]], seen: list[dict[str, Any]],
          reply: CanvasEditOut, state: dict[str, Any], dims: list[Any], reason: str = "",
          instruction: str | None = None) -> Merged:
    """The AI's correction against the rules' reading (DEC-28: the rules' ops stand, additions are marked, a different
    target asks). With ``instruction``, an added op must be on a widget or filter its words name."""
    out = Merged()
    converted: list[dict[str, Any]] = []
    rules_keys = {_key(r) for r in rules_ops}
    names = named(instruction, state) if instruction is not None else None
    next_to: dict[int, str | None] = {}
    for o in reply.ops:
        op, why = from_out(o, state, dims)
        if why is not None:
            out.refused.append(why)
        elif op is not None and _key(op) not in {_key(c) for c in converted}:
            converted.append(op)
            next_to[id(op)] = o.next_to if o.op == "move" else None
    ai_keys = {_key(c) for c in converted}
    added: list[dict[str, Any]] = []
    for op in converted:
        if _key(op) in rules_keys:
            continue
        others = [(r, s) for r, s in zip(rules_ops, seen, strict=False)
                  if r["op"] == op["op"] and _key(r) not in ai_keys]
        if op["op"] == "add_filter":
            if others:
                out.notes.append(f"The AI suggested a {op['label']} filter instead; the rules' filter stands.")
                continue
        elif others and others[0][1].get("phrase"):
            r, s = others[0]
            out.question = widget_question(s["phrase"], [r["item_id"], op["item_id"]], state, reason)
            return out
        if (names is not None and instruction is not None
                and (not _named_op(op, instruction, names, next_to.get(id(op)))
                     or (op["op"] == "move" and not _placed(instruction, state)))):
            from tela_api.content.chat_edit import describe

            out.notes.append(f"The AI also suggested “{describe(op, state)['summary']}”, but the instruction "
                             "doesn't ask for that, so it was left out.")
            continue
        added.append(op)
    out.ops = [*rules_ops, *added]
    out.by_ai = {id(a) for a in added}
    if added:
        covered = {a.get("item_id") for a in added}
        kept = [r for r in rules_refused if r.get("item_id") and r["item_id"] not in covered]
        out.refused = kept + out.refused + [{"request": t, "reason": NOT_UNDERSTOOD, "by": "ai"}
                                            for t in reply.not_understood if t.strip()]
    else:
        out.refused = rules_refused + out.refused
    return out


def canonical(o: LayoutOpOut, state: dict[str, Any], dims: list[Any]) -> str | None:
    """The op in words the rules read exactly, for an option of the AI's question (None when there are none)."""
    op, _ = from_out(o, state, dims)
    if op is None:
        return None
    if op["op"] == "add_filter":
        return f"add a {op['label']} filter"
    items = _items(state)
    item = items[op["item_id"]]
    title = _title(state, item)
    if op["op"] == "remove":
        return f"remove {title}"
    if op["op"] == "set_limit":
        return f"limit {title} to top {op['to']}"
    if op["op"] == "set_visual":
        word = VISUAL_NAMES.get(op["to"])
        return f"show {title} as {word}" if word else None
    if op["op"] == "resize":
        name = SIZE_NAMES.get(op["to"]["w"])
        return f"make {title} {name}" if name and op["to"]["h"] == item["h"] else None
    if o.next_to:
        return f"move {title} next to {_title(state, items[o.next_to])}"
    where = norm(o.to or "")
    if where in ("top", "the top", "first"):
        return f"move {title} to the top"
    if where in ("bottom", "the bottom", "last", "end"):
        return f"move {title} to the bottom"
    sec = next(s for s in state["sections"] if s["id"] == op["to"]["section_id"])
    return f"move {title} to the {norm(sec['title'])} section"


def ask(rules_ops: list[dict[str, Any]], seen: list[dict[str, Any]], options: list[CanvasEditOut],
        state: dict[str, Any], dims: list[Any], reason: str = "") -> dict[str, Any] | None:
    """The AI's one question: a different target from the rules' becomes "Which widget do you mean?"; otherwise each
    option becomes an instruction the rules read exactly. None when fewer than two usable options remain."""
    from tela_api.content.chat_edit import describe

    converted = [[op for op, _ in (from_out(o, state, dims) for o in opt.ops) if op is not None] for opt in options]
    for r, s in zip(rules_ops, seen, strict=False):
        if r["op"] == "add_filter" or not s.get("phrase"):
            continue
        targets = [r["item_id"], *(c["item_id"] for ops in converted for c in ops if c["op"] == r["op"])]
        if len(set(targets)) > 1:
            return widget_question(s["phrase"], targets, state, reason)
    choices: dict[str, dict[str, Any]] = {}
    for opt, ops in zip(options, converted, strict=True):
        clauses = [canonical(o, state, dims) for o in opt.ops]
        if not clauses or any(c is None for c in clauses) or len(ops) != len(clauses):
            continue
        instruction = "; ".join(c for c in clauses if c)
        choices.setdefault(instruction, {"item_id": next((op.get("item_id") for op in ops if op.get("item_id")),
                                                         None),
                                         "title": "; ".join(describe(op, state)["summary"] for op in ops),
                                         "instruction": instruction})
    if len(choices) < 2:
        return None
    out: dict[str, Any] = {"question": "Which change do you mean?", "options": list(choices.values())[:4]}
    if reason:
        out["why"] = [reason]
    return out
