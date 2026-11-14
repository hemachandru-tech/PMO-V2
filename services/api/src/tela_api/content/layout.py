"""12-column grid rules (REQ-066 to REQ-068; API-027; US-065, US-069).

Items snap to whole grid units: ``x + w ≤ 12``, ``w ≥ 2``, ``h ≥ 2``. Items in one section may not overlap;
after a layout change the section is compacted upwards, as the canvas does, and the compacted positions
are what is stored.
"""

from __future__ import annotations

from dataclasses import dataclass

COLS = 12
MIN_W = MIN_H = 2
MAX_H = 24


@dataclass
class Box:
    id: str
    x: int
    y: int
    w: int
    h: int

    def hits(self, other: Box) -> bool:
        return (self.x < other.x + other.w and other.x < self.x + self.w
                and self.y < other.y + other.h and other.y < self.y + self.h)


def problems(boxes: list[Box]) -> list[str]:
    out = []
    for b in boxes:
        if b.x < 0 or b.y < 0:
            out.append(f"{b.id}: position must not be negative")
        if b.w < MIN_W or b.h < MIN_H:
            out.append(f"{b.id}: at least {MIN_W} columns wide and {MIN_H} rows high")
        if b.x + b.w > COLS:
            out.append(f"{b.id}: x + w must be at most {COLS}")
        if b.h > MAX_H:
            out.append(f"{b.id}: at most {MAX_H} rows high")
    for i, a in enumerate(boxes):
        for b in boxes[i + 1:]:
            if a.hits(b):
                out.append(f"{a.id} overlaps {b.id}")
    return out


def compact(boxes: list[Box]) -> list[Box]:
    """Move every box up as far as it goes without overlapping the ones above it (vertical compaction)."""
    placed: list[Box] = []
    for b in sorted(boxes, key=lambda b: (b.y, b.x)):
        nb = Box(b.id, b.x, b.y, b.w, b.h)
        while nb.y > 0 and not any(Box(nb.id, nb.x, nb.y - 1, nb.w, nb.h).hits(p) for p in placed):
            nb.y -= 1
        placed.append(nb)
    return placed


def place(boxes: list[Box], w: int, h: int) -> tuple[int, int]:
    """First free slot, row by row, for a new w x h item."""
    w = max(MIN_W, min(COLS, w))
    bottom = max((b.y + b.h for b in boxes), default=0)
    for y in range(0, bottom + 1):
        for x in range(0, COLS - w + 1):
            probe = Box("new", x, y, w, h)
            if not any(probe.hits(b) for b in boxes):
                return x, y
    return 0, bottom


def settle(boxes: list[Box], first: str | None = None) -> list[Box]:
    """Resolve overlaps after a move or resize: ``first`` keeps its place, others are pushed down, then compacted."""
    order = sorted(boxes, key=lambda b: (b.id != first, b.y, b.x))
    placed: list[Box] = []
    for b in order:
        nb = Box(b.id, max(0, min(b.x, COLS - b.w)), max(0, b.y), b.w, b.h)
        while any(nb.hits(p) for p in placed):
            nb.y += 1
        placed.append(nb)
    return compact(placed)
