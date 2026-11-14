import { scaleBand, scaleLinear } from "d3-scale";
import { useEffect, useRef, useState } from "react";

import type { Column } from "../api/app";
import type { ChartSpec } from "../api/querySpec";
import { formatCompact, formatValue } from "./format";
import { focusOf, MUTED } from "./svgChart";

const NOT_SET = "Not set";
const MARGIN = { top: 8, right: 84, bottom: 24, left: 112 };
const ROW = 26;

export interface DumbbellRow {
  label: string;
  a: number | null;
  b: number | null;
  gap: number | null;
}

/** The rows in the server's order (widest gap first) with both values and the gap (COMP-001: no sums here). */
export function dumbbellRows(spec: ChartSpec, columns: Column[], rows: unknown[][]): DumbbellRow[] {
  const at = (n: string | null | undefined) => columns.findIndex((c) => c.name === n);
  const [xi, ai, bi] = [at(spec.x), at(spec.series?.[0]), at(spec.series?.[1])];
  const f = (spec.figures ?? {}) as { gaps?: (number | null)[]; order?: number[] };
  const order = f.order ?? rows.map((_, i) => i);
  const num = (v: unknown) => (typeof v === "number" ? v : null);
  return order.map((i) => ({ label: rows[i]?.[xi] == null ? NOT_SET : String(rows[i]![xi]), a: num(rows[i]?.[ai]), b: num(rows[i]?.[bi]), gap: f.gaps?.[i] ?? null }));
}

/**
 * Dumbbell (D3, US-187; DEC-22 as amended): two measures of one unit per item — baseline vs forecast — as two dots
 * joined by a line, the gap written at the end. Scales from d3-scale; React draws the SVG, so it prints as vectors
 * and follows the theme through the same tokens as ECharts.
 */
export function Dumbbell({ spec, columns, rows, title, highlight }: { spec: ChartSpec; columns: Column[]; rows: unknown[][]; title?: string; highlight?: string[] }) {
  const ref = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(560);
  useEffect(() => {
    if (!ref.current || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(([e]) => e && setWidth(Math.max(280, e.contentRect.width)));
    observer.observe(ref.current);
    return () => observer.disconnect();
  }, []);
  const items = dumbbellRows(spec, columns, rows);
  const [ca, cb] = [columns.find((c) => c.name === spec.series?.[0]), columns.find((c) => c.name === spec.series?.[1])];
  const format = ca?.format;
  const values = items.flatMap((r) => [r.a, r.b]).filter((v): v is number => v !== null);
  // The gap is the point, not the size: the scale spans the values (with a margin), not from zero.
  const [lo, hi] = values.length ? [Math.min(...values), Math.max(...values)] : [0, 1];
  const pad = (hi - lo || Math.abs(hi) || 1) * 0.1;
  const x = scaleLinear().domain([lo - pad, hi + pad]).nice().range([MARGIN.left, width - MARGIN.right]);
  const ticks = x.ticks(Math.max(2, Math.floor((width - MARGIN.left - MARGIN.right) / 90)));
  const height = MARGIN.top + MARGIN.bottom + items.length * ROW;
  const y = scaleBand<number>().domain(items.map((_, i) => i)).range([MARGIN.top, height - MARGIN.bottom]).padding(0.3);
  const fmt = (v: number | null) => formatValue(v, format);
  const focus = focusOf(highlight, items.map((r) => r.label)); // highlight-and-grey (US-188)
  return (
    <div ref={ref} data-chart="dumbbell" className="w-full">
      <div className="flex flex-wrap gap-x-4 gap-y-1 text-small text-ink-muted" aria-hidden>
        <span className="flex items-center gap-1.5"><span className="size-2.5 rounded-full bg-[var(--tl-chart-1)]" />{ca?.label}</span>
        <span className="flex items-center gap-1.5"><span className="size-2.5 rounded-full bg-[var(--tl-chart-2)]" />{cb?.label}</span>
      </div>
      <svg width={width} height={height} role="img" aria-label={title ?? `${ca?.label} and ${cb?.label}`} className="block">
        <title>{title ?? `${ca?.label} and ${cb?.label}`}</title>
        <g aria-hidden>
          {ticks.map((t) => (
            <g key={t}>
              <line x1={x(t)} x2={x(t)} y1={MARGIN.top - 4} y2={height - MARGIN.bottom} stroke="var(--tl-chart-grid)" />
              <text x={x(t)} y={height - 6} textAnchor="middle" className="fill-ink-muted text-[11px]">{formatCompact(t, format)}</text>
            </g>
          ))}
        </g>
        {items.map((r, i) => {
          const cy = (y(i) ?? 0) + y.bandwidth() / 2;
          return (
            <g key={`${r.label}-${i}`} data-row={r.label}>
              <title>{`${r.label}: ${ca?.label} ${fmt(r.a)}, ${cb?.label} ${fmt(r.b)}${r.gap !== null ? `, gap ${fmt(r.gap)}` : ""}`}</title>
              <text x={MARGIN.left - 8} y={cy + 4} textAnchor="end" className="fill-ink text-small">{r.label.length > 16 ? `${r.label.slice(0, 15)}…` : r.label}</text>
              {r.a !== null && r.b !== null && <line x1={x(r.a)} x2={x(r.b)} y1={cy} y2={cy} stroke="var(--tl-line-strong)" strokeWidth={2} />}
              {r.a !== null && <circle cx={x(r.a)} cy={cy} r={5} fill={focus && !focus.has(r.label) ? MUTED : "var(--tl-chart-1)"} />}
              {r.b !== null && <circle cx={x(r.b)} cy={cy} r={5} fill={focus && !focus.has(r.label) ? MUTED : "var(--tl-chart-2)"} />}
              {r.gap !== null && (
                <text x={width - MARGIN.right + 8} y={cy + 4} className="fill-ink-muted font-mono text-[11px]">{r.gap > 0 ? "+" : ""}{fmt(r.gap)}</text>
              )}
            </g>
          );
        })}
      </svg>
    </div>
  );
}
