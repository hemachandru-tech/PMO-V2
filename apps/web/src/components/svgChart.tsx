import { useEffect, useRef, useState } from "react";

/**
 * Shared pieces of the D3 visuals (US-188; DEC-22 as amended): D3 works out scales, shapes and the swarm layout;
 * React draws the SVG, so the visuals print as vectors and follow the theme through the same tokens as ECharts.
 */

/** Series colours in token order (the tenant's palette overrides the tokens). */
export const SERIES = [1, 2, 3, 4, 5, 6, 7, 8].map((i) => `var(--tl-chart-${i})`);
export const MUTED = "var(--tl-chart-other)";
export const GRID = "var(--tl-chart-grid)";

/** The box's width, following resizes (canvas widgets are resized on the grid, US-141). */
export function useWidth(initial = 560): [React.RefObject<HTMLDivElement>, number] {
  const ref = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(initial);
  useEffect(() => {
    if (!ref.current || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(([e]) => e && setWidth(Math.max(280, e.contentRect.width)));
    observer.observe(ref.current);
    return () => observer.disconnect();
  }, []);
  return [ref, width];
}

export const short = (s: string, n = 16) => (s.length > n ? `${s.slice(0, n - 1)}…` : s);

/** Highlight-and-grey (US-186, across visuals in US-188): with a highlight, the named items keep their colour. */
export function focusOf(highlight: string[] | undefined, labels: string[]): Set<string> | null {
  return highlight?.length && labels.some((l) => highlight.includes(l)) ? new Set(highlight) : null;
}

/** Spread labels at `ys` so none sits closer than `gap` px to the next, keeping their order (line-end labels). */
export function spreadLabels(ys: number[], gap = 12): number[] {
  const order = ys.map((y, i) => [y, i] as const).sort((a, b) => a[0] - b[0]);
  const out = [...ys];
  let last = -Infinity;
  for (const [y, i] of order) {
    out[i] = Math.max(y, last + gap);
    last = out[i]!;
  }
  return out;
}

export function Legend({ items }: { items: { label: string; colour: string; dashed?: boolean }[] }) {
  return (
    <div className="flex flex-wrap gap-x-4 gap-y-1 text-small text-ink-muted" aria-hidden>
      {items.map((i) => (
        <span key={i.label} className="flex items-center gap-1.5">
          <span className="size-2.5 rounded-full" style={{ background: i.colour }} />
          {i.label}
        </span>
      ))}
    </div>
  );
}
