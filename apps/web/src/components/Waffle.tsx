import { formatValue } from "./format";
import { MUTED, SERIES } from "./svgChart";

export interface WaffleFigures {
  parts: { label: string; value: number; pct: number | null; cells: number }[];
  total: number;
}

/** The 100 cells in order: each part takes its server-given count of cells (COMP-001). */
export function waffleCells(parts: WaffleFigures["parts"]): number[] {
  return parts.flatMap((p, k) => Array.from({ length: p.cells }, () => k));
}

/**
 * Waffle (HTML, US-188): 100 cells, each part filling its share, with a legend that gives every part's value and
 * share in words, so colour is never the only signal. A part and its whole shows the rest as muted cells.
 */
export function Waffle({ figures, format, title, highlight }: { figures: WaffleFigures; format?: string | null; title?: string; highlight?: string[] }) {
  const focus = highlight?.length && figures.parts.some((p) => highlight.includes(p.label)) ? new Set(highlight) : null;
  const rest = figures.parts.length === 2 && figures.parts[1]!.label.startsWith("Rest of");
  const colour = (k: number) => (rest && k === 1) || (focus && !focus.has(figures.parts[k]!.label)) ? MUTED : SERIES[k % SERIES.length]!;
  return (
    <div className="flex flex-wrap items-start gap-4" data-chart="waffle">
      <div className="grid size-40 shrink-0 grid-cols-10 gap-0.5" role="img" aria-label={title ?? "Out of 100"}>
        {waffleCells(figures.parts).map((k, i) => <span key={i} className="rounded-[2px]" style={{ background: colour(k) }} data-part={k} />)}
      </div>
      <ul className="grid gap-1 text-small" aria-label={title ?? "Out of 100"}>
        {figures.parts.map((p, k) => (
          <li key={p.label} className="flex items-center gap-2">
            <span className="size-3 shrink-0 rounded-[2px]" style={{ background: colour(k) }} aria-hidden />
            <span className="text-ink">{p.label}</span>
            <span className="font-mono tabular-nums text-ink-muted">{formatValue(p.value, format)}{p.pct !== null ? ` · ${p.pct}%` : ""}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}
