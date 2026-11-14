"""Chat edit gold set (US-244, for the US-246 final gate): instructions with the layout ops a Creator should get.

``packs/pmo/fixtures/canvas_edit_gold.yaml`` holds a canvas pinned from Ask on the synthetic TPG fixture and 20 edit
instructions with the ops a right preview has (by widget title), the widgets it refuses and the questions it asks.
``score`` lists what a preview misses; a preview with no misses is right. ``wrong`` is the confident-wrong part: an op
nobody asked for or on the wrong values. The gate previews each instruction with LLM review off and on.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from tela_api.packs.source import DEFAULT_PACKS_DIR

GOLD = DEFAULT_PACKS_DIR / "pmo" / "fixtures" / "canvas_edit_gold.yaml"
PARAMS = ("w", "h", "to", "next_to", "visual", "limit")


def load(path: Path = GOLD) -> dict[str, Any]:
    """``{"canvas": [{question, title, visual}], "cases": [{id, instruction, expect}]}``."""
    return dict(yaml.safe_load(path.read_text(encoding="utf-8")))


def _value(change: dict[str, Any], key: str, before: dict[str, dict[str, Any]]) -> Any:
    to = change.get("to")
    if key in ("w", "h"):
        return to.get(key) if isinstance(to, dict) else None
    if key in ("visual", "limit"):
        return to
    if key == "to":
        y = to.get("y") if isinstance(to, dict) else None
        return "top" if y == -1 else "bottom" if y == 999 else None
    other = next((i for i in before.values() if isinstance(to, dict) and _beside(to, i)), None)
    return other["title"] if other else None


def _beside(to: dict[str, Any], other: dict[str, Any]) -> bool:
    return (to.get("x"), to.get("y")) in ((other["x"] + other["w"], other["y"]), (other["x"], other["y"] + other["h"]))


def _matches(change: dict[str, Any], want: dict[str, Any], before: dict[str, dict[str, Any]]) -> bool:
    return (change["op"] == want["op"] and change.get("target") == want["widget"]
            and all(_value(change, k, before) == want[k] for k in PARAMS if k in want))


def score(preview: dict[str, Any], expect: dict[str, Any], before: list[dict[str, Any]]) -> list[str]:
    """What ``preview`` misses against ``expect`` (empty when right). ``before`` is the canvas's items (id, title, x,
    y, w, h) the preview was made from."""
    by_title = {i["title"]: i for i in before}
    q = preview.get("clarification")
    if "clarify" in expect:
        offered = {o["title"] for o in (q or {}).get("options", [])}
        return [] if q and set(expect["clarify"]) <= offered else ["no question offering " + ", ".join(
            expect["clarify"])]
    if q:
        return ["asked instead of changing: " + q["question"]]
    changes = [c for c in preview["changes"] if c["op"] != "refused"]
    refused = [c for c in preview["changes"] if c["op"] == "refused"]
    misses: list[str] = []
    for want in expect.get("ops", []):
        if not any(_matches(c, want, by_title) for c in changes):
            misses.append(f"no {want['op']} of {want['widget']}")
    misses += wrong(preview, expect, before)
    for want in expect.get("refused", []):
        item = by_title.get(want["widget"], {})
        hit = [r for r in refused if r.get("item_id") == item.get("id")]
        if not hit:
            misses.append(f"{want['widget']} not refused")
        elif want.get("alternative") and not any(r.get("alternative") for r in hit):
            misses.append(f"{want['widget']} refused without an alternative")
    return misses


def wrong(preview: dict[str, Any], expect: dict[str, Any], before: list[dict[str, Any]]) -> list[str]:
    """The ops in ``preview`` that nobody asked for (a confident-wrong edit when the preview would be applied)."""
    by_title = {i["title"]: i for i in before}
    return [f"unexpected {c['op']} of {c.get('target')}" for c in preview.get("changes", [])
            if c["op"] != "refused" and not any(_matches(c, w, by_title) for w in expect.get("ops", []))]
