/** Canvas grid maths (FEAT-025; US-141): 12 columns, rows of ROW px with GAP px between. The server re-checks every layout. */
export const COLS = 12;
export const ROW = 96;
export const GAP = 12;
export const MIN_W = 2;
export const MIN_H = 2;
export const MAX_H = 24;

export interface Box { id: string; x: number; y: number; w: number; h: number }

function hits(a: Box, b: Box) {
  return a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h;
}

/**
 * Keep ``first`` where it is, push anything it now overlaps downwards, then compact upwards (as the server does).
 * With ``yieldTo``, the box is placed last instead, so it goes below whatever it now overlaps.
 */
export function settle(boxes: Box[], first: string, yieldTo = false): Box[] {
  const rank = (b: Box) => Number(b.id !== first) * (yieldTo ? -1 : 1);
  const order = [...boxes].sort((a, b) => rank(a) - rank(b) || a.y - b.y || a.x - b.x);
  const placed: Box[] = [];
  for (const b of order) {
    const nb = { ...b, x: Math.max(0, Math.min(b.x, COLS - b.w)), y: Math.max(0, b.y) };
    while (placed.some((p) => hits(nb, p))) nb.y += 1;
    placed.push(nb);
  }
  const out: Box[] = [];
  for (const b of [...placed].sort((a, c) => a.y - c.y || a.x - c.x)) {
    const nb = { ...b };
    while (nb.y > 0 && !out.some((p) => hits({ ...nb, y: nb.y - 1 }, p))) nb.y -= 1;
    out.push(nb);
  }
  return out;
}

export type Nudge = "left" | "right" | "up" | "down";

/**
 * One keyboard step: arrows move by a column or a row, Shift + arrows resize (left/right narrower/wider, up/down
 * shorter/taller). The grid compacts upwards, so up and down swap with the nearest widget above or below.
 */
export function nudge(boxes: Box[], id: string, dir: Nudge, resize: boolean): Box[] {
  const b = boxes.find((x) => x.id === id);
  if (!b) return boxes;
  const n = { ...b };
  if (resize) {
    if (dir === "left") n.w = Math.max(MIN_W, b.w - 1);
    if (dir === "right") n.w = Math.min(COLS, b.w + 1);
    if (dir === "up") n.h = Math.max(MIN_H, b.h - 1);
    if (dir === "down") n.h = Math.min(MAX_H, b.h + 1);
    n.x = Math.min(n.x, COLS - n.w);
  } else {
    if (dir === "left") n.x = Math.max(0, b.x - 1);
    if (dir === "right") n.x = Math.min(COLS - b.w, b.x + 1);
    const column = (o: Box) => o.id !== id && o.x < b.x + b.w && b.x < o.x + o.w;
    if (dir === "up") {
      const above = boxes.filter((o) => column(o) && o.y + o.h <= b.y);
      n.y = above.length ? Math.max(...above.map((o) => o.y)) : b.y;
    }
    if (dir === "down") {
      const below = boxes.filter((o) => column(o) && o.y >= b.y + b.h);
      if (below.length === 0) return boxes;
      const next = below.reduce((m, o) => (o.y < m.y ? o : m));
      n.y = next.y + next.h;
      return settle(boxes.map((x) => (x.id === id ? n : x)), id, true);
    }
  }
  return settle(boxes.map((x) => (x.id === id ? n : x)), id);
}

/** Words for the live region after a move or resize. */
export function describeBox(label: string, b: Box): string {
  return `${label}: column ${b.x + 1}, row ${b.y + 1}, ${b.w} of ${COLS} columns wide, ${b.h} rows high`;
}

/** The grid cell under a dropped card's top-left corner, from the drop target's and the card's screen rectangles. */
export function dropCell(target: { left: number; top: number; width: number }, card: { left: number; top: number }, w: number): { x: number; y: number } {
  const col = (target.width + GAP) / COLS;
  const x = Math.round((card.left - target.left) / col);
  const y = Math.round((card.top - target.top) / (ROW + GAP));
  return { x: Math.max(0, Math.min(COLS - w, x)), y: Math.max(0, y) };
}
