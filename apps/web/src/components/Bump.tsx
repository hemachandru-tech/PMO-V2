import { scaleLinear, scalePoint } from "d3-scale";
import { curveBumpX, line } from "d3-shape";

import { focusOf, GRID, MUTED, SERIES, short, spreadLabels, useWidth } from "./svgChart";

const MARGIN = { top: 16, right: 130, bottom: 28, left: 36 };
const STEP = 30;

export interface BumpFigures {
  periods: string[];
  items: { label: string; ranks: (number | null)[]; values: (number | null)[] }[];
}

/** The pieces of an item's line between periods it has a rank in (a gap where it has none). */
export function bumpSegments(ranks: (number | null)[]): [number, number][][] {
  const out: [number, number][][] = [];
  let run: [number, number][] = [];
  ranks.forEach((r, i) => {
    if (r === null) {
      if (run.length) out.push(run);
      run = [];
    } else run.push([i, r]);
  });
  if (run.length) out.push(run);
  return out;
}

/**
 * Bump chart (D3, US-188): the rank of each item (at most 10) in each period, 1 at the top, joined by smooth
 * lines; each line is named at its end, so no legend is needed. Ranks come from the server (COMP-001).
 */
export function Bump({ figures, format, title, highlight }: { figures: BumpFigures; format?: string | null; title?: string; highlight?: string[] }) {
  const [ref, width] = useWidth();
  const n = figures.items.length;
  const x = scalePoint<number>().domain(figures.periods.map((_, i) => i)).range([MARGIN.left, width - MARGIN.right]).padding(0.1);
  const y = scaleLinear().domain([1, Math.max(2, n)]).range([MARGIN.top, MARGIN.top + (Math.max(2, n) - 1) * STEP]);
  const height = MARGIN.top + (Math.max(2, n) - 1) * STEP + MARGIN.bottom;
  const focus = focusOf(highlight, figures.items.map((i) => i.label));
  const colour = (k: number, label: string) => (focus && !focus.has(label) ? MUTED : SERIES[k % SERIES.length]!);
  const path = line<[number, number]>().x(([i]) => x(i) ?? 0).y(([, r]) => y(r)).curve(curveBumpX);
  const lastRank = figures.items.map((it) => [...it.ranks].reverse().find((r) => r !== null) ?? n);
  const labelY = spreadLabels(lastRank.map((r) => y(r)), 14);
  const fmt = (v: number | null) => (v === null ? "–" : v.toLocaleString(undefined, { maximumFractionDigits: format === "percent" ? 1 : 2 }));
  return (
    <div ref={ref} data-chart="bump" className="w-full">
      <svg width={width} height={height} role="img" aria-label={title ?? "Rank by period"} className="block">
        <title>{title ?? "Rank by period"}</title>
        <g aria-hidden>
          {figures.periods.map((p, i) => (
            <g key={p}>
              <line x1={x(i)} x2={x(i)} y1={MARGIN.top - 6} y2={height - MARGIN.bottom + 4} stroke={GRID} />
              <text x={x(i)} y={height - 8} textAnchor="middle" className="fill-ink-muted text-[11px]">{p}</text>
            </g>
          ))}
          {Array.from({ length: n }, (_, r) => (
            <text key={r} x={MARGIN.left - 14} y={y(r + 1) + 4} textAnchor="end" className="fill-ink-muted text-[11px]">{r + 1}</text>
          ))}
        </g>
        {/* muted lines first, so the highlighted ones are drawn on top */}
        {figures.items.map((it, k) => ({ it, k })).sort((a, b) => Number(!focus || focus.has(a.it.label)) - Number(!focus || focus.has(b.it.label))).map(({ it, k }) => (
          <g key={it.label} data-item={it.label}>
            <title>{`${it.label}: ${figures.periods.map((p, i) => `${p} rank ${it.ranks[i] ?? "–"} (${fmt(it.values[i] ?? null)})`).join(", ")}`}</title>
            {bumpSegments(it.ranks).map((seg, s) => <path key={s} d={path(seg) ?? ""} fill="none" stroke={colour(k, it.label)} strokeWidth={focus?.has(it.label) ? 3.5 : 2.5} />)}
            {it.ranks.map((r, i) => (r === null ? null : <circle key={i} cx={x(i)} cy={y(r)} r={4} fill={colour(k, it.label)} stroke="var(--tl-surface)" strokeWidth={1.5} />))}
            <text x={width - MARGIN.right + 10} y={labelY[k]! + 4} className="fill-ink text-small">{lastRank[k]}. {short(it.label, 15)}</text>
          </g>
        ))}
      </svg>
    </div>
  );
}
