import { scaleLinear } from "d3-scale";
import { area, curveBasis } from "d3-shape";

import type { Column } from "../api/app";
import type { ChartSpec } from "../api/querySpec";
import type { SpreadGroup } from "./Beeswarm";
import { formatCompact, formatValue } from "./format";
import { focusOf, GRID, MUTED, SERIES, short, useWidth } from "./svgChart";

const MARGIN = { top: 8, right: 16, bottom: 24, left: 112 };
const LANE = 76;
const CLOUD = 34; // px of the density's peak
const BOX = 10;

interface RidgeGroup extends SpreadGroup { density: number[] }

/** The density as an SVG path above `base` (d3-shape area): the server's curve (peak 1) over the shared range. */
export function ridgePath(density: number[], x0: number, x1: number, base: number, peak = CLOUD): string {
  const step = density.length > 1 ? (x1 - x0) / (density.length - 1) : 0;
  return area<number>().x((_, i) => x0 + i * step).y0(base).y1((d) => base - d * peak).curve(curveBasis)(density) ?? "";
}

/**
 * Ridgeline / raincloud (D3, US-188): for each group (3 to 8), its density as a ridge, its quartiles as a box with
 * whiskers, and every item as a dot below — so the shape, the summary and the raw values read together. Densities
 * and quartiles come from the server (COMP-001).
 */
export function Ridgeline({ spec, columns, rows, title, highlight }: { spec: ChartSpec; columns: Column[]; rows: unknown[][]; title?: string; highlight?: string[] }) {
  const [ref, width] = useWidth();
  const at = (n: string | null | undefined) => columns.findIndex((c) => c.name === n);
  const [xi, gi, mi] = [at(spec.x), at(spec.y), at(spec.series?.[0])];
  const measure = columns[mi];
  const f = (spec.figures ?? {}) as { groups?: RidgeGroup[]; min?: number; max?: number };
  const groups = f.groups ?? [];
  const [lo, hi] = [f.min ?? 0, f.max ?? 1];
  const x = scaleLinear().domain([lo, hi]).range([MARGIN.left, width - MARGIN.right]);
  const height = MARGIN.top + MARGIN.bottom + groups.length * LANE;
  const focus = focusOf(highlight, groups.map((g) => g.label));
  const fmt = (v: number) => formatValue(v, measure?.format);
  return (
    <div ref={ref} data-chart="ridgeline" className="w-full">
      <svg width={width} height={height} role="img" aria-label={title ?? `${measure?.label} by group`} className="block">
        <title>{title ?? `${measure?.label} by group`}</title>
        <g aria-hidden>
          {x.ticks(Math.max(2, Math.floor((width - MARGIN.left - MARGIN.right) / 90))).map((t) => (
            <g key={t}>
              <line x1={x(t)} x2={x(t)} y1={MARGIN.top} y2={height - MARGIN.bottom} stroke={GRID} />
              <text x={x(t)} y={height - 6} textAnchor="middle" className="fill-ink-muted text-[11px]">{formatCompact(t, measure?.format)}</text>
            </g>
          ))}
        </g>
        {groups.map((g, k) => {
          const base = MARGIN.top + k * LANE + CLOUD + 4;
          const colour = focus && !focus.has(g.label) ? MUTED : SERIES[k % SERIES.length]!;
          const boxY = base + 6;
          const values = rows.filter((r) => (gi < 0 || (r[gi] == null ? "Not set" : String(r[gi])) === g.label) && typeof r[mi] === "number");
          return (
            <g key={g.label} data-group={g.label}>
              <title>{`${g.label}: ${g.n} items; lowest ${fmt(g.min)}, lower quartile ${fmt(g.q1)}, median ${fmt(g.median)}, upper quartile ${fmt(g.q3)}, highest ${fmt(g.max)}`}</title>
              <text x={MARGIN.left - 8} y={base} textAnchor="end" className="fill-ink text-small">{short(g.label)}</text>
              <path d={ridgePath(g.density, x(lo), x(hi), base)} fill={colour} fillOpacity={0.35} stroke={colour} strokeWidth={1.5} />
              <line x1={x(g.low)} x2={x(g.high)} y1={boxY + BOX / 2} y2={boxY + BOX / 2} stroke="var(--tl-ink-muted)" />
              <rect x={x(g.q1)} y={boxY} width={Math.max(1, x(g.q3) - x(g.q1))} height={BOX} fill="var(--tl-surface)" stroke="var(--tl-ink)" />
              <line x1={x(g.median)} x2={x(g.median)} y1={boxY} y2={boxY + BOX} stroke="var(--tl-ink)" strokeWidth={2} />
              {values.map((r, i) => (
                // a fixed, small jitter so the strip reads as dots, the same on every render and in print
                <circle key={i} cx={x(r[mi] as number)} cy={boxY + BOX + 7 + ((i * 7) % 9) - 4} r={2} fill={colour} fillOpacity={0.7}>
                  <title>{`${r[xi] == null ? "Not set" : String(r[xi])}: ${fmt(r[mi] as number)}`}</title>
                </circle>
              ))}
            </g>
          );
        })}
      </svg>
    </div>
  );
}
