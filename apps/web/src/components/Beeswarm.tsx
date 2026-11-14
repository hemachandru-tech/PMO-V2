import { forceCollide, forceSimulation, forceX, forceY } from "d3-force";
import { scaleLinear } from "d3-scale";
import { useMemo } from "react";

import type { Column } from "../api/app";
import type { ChartSpec } from "../api/querySpec";
import { formatCompact, formatValue } from "./format";
import { focusOf, GRID, MUTED, SERIES, short, useWidth } from "./svgChart";

const MARGIN = { top: 8, right: 16, bottom: 24, left: 112 };
const LANE = 64;
const R = 3.5;

export interface SpreadGroup { label: string; n: number; min: number; q1: number; median: number; q3: number; max: number; low: number; high: number }
interface Dot { label: string; group: string; value: number; x: number; y: number }

/**
 * The swarm layout: each dot pulled to its value on x and its group's lane on y, pushed apart so none overlap
 * (d3-force, run to rest before drawing). Positions are presentation only; the value is never moved.
 */
export function swarm(points: { label: string; group: string; value: number; tx: number; ty: number }[], r = R, ticks = 160): Dot[] {
  const nodes = points.map((p) => ({ ...p, x: p.tx, y: p.ty }));
  const sim = forceSimulation(nodes)
    .force("x", forceX<(typeof nodes)[number]>((d) => d.tx).strength(1))
    .force("y", forceY<(typeof nodes)[number]>((d) => d.ty).strength(0.08))
    .force("collide", forceCollide(r + 0.6))
    .stop();
  sim.tick(ticks);
  return nodes.map(({ label, group, value, x, y }) => ({ label, group, value, x, y }));
}

/**
 * Beeswarm (D3, US-188): one dot per item along the value axis, in one lane per group (at most 8), the group's
 * median marked. Quartiles come from the server (COMP-001).
 */
export function Beeswarm({ spec, columns, rows, title, highlight }: { spec: ChartSpec; columns: Column[]; rows: unknown[][]; title?: string; highlight?: string[] }) {
  const [ref, width] = useWidth();
  const at = (n: string | null | undefined) => columns.findIndex((c) => c.name === n);
  const [xi, gi, mi] = [at(spec.x), at(spec.y), at(spec.series?.[0])];
  const measure = columns[mi];
  const f = (spec.figures ?? {}) as { groups?: SpreadGroup[]; min?: number; max?: number };
  const groups = f.groups ?? [];
  const height = MARGIN.top + MARGIN.bottom + Math.max(1, groups.length) * LANE;
  const x = scaleLinear().domain([f.min ?? 0, f.max ?? 1]).nice().range([MARGIN.left, width - MARGIN.right]);
  const lane = (g: string) => MARGIN.top + (Math.max(0, groups.findIndex((s) => s.label === g)) + 0.5) * LANE;
  const groupOf = (r: unknown[]) => (gi >= 0 ? (r[gi] == null ? "Not set" : String(r[gi])) : groups[0]?.label ?? "");
  const dots = useMemo(
    () => swarm(rows.filter((r) => typeof r[mi] === "number").map((r) => ({ label: r[xi] == null ? "Not set" : String(r[xi]), group: groupOf(r), value: r[mi] as number, tx: x(r[mi] as number), ty: lane(groupOf(r)) }))),
    // x and lane follow width and figures
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [rows, width, spec],
  );
  const focus = focusOf(highlight, dots.map((d) => d.label));
  const fmt = (v: number) => formatValue(v, measure?.format);
  return (
    <div ref={ref} data-chart="beeswarm" className="w-full">
      <svg width={width} height={height} role="img" aria-label={title ?? `${measure?.label} per item`} className="block">
        <title>{title ?? `${measure?.label} per item`}</title>
        <g aria-hidden>
          {x.ticks(Math.max(2, Math.floor((width - MARGIN.left - MARGIN.right) / 90))).map((t) => (
            <g key={t}>
              <line x1={x(t)} x2={x(t)} y1={MARGIN.top} y2={height - MARGIN.bottom} stroke={GRID} />
              <text x={x(t)} y={height - 6} textAnchor="middle" className="fill-ink-muted text-[11px]">{formatCompact(t, measure?.format)}</text>
            </g>
          ))}
        </g>
        {groups.map((g) => (
          <g key={g.label} data-group={g.label}>
            <title>{`${g.label}: ${g.n} items, median ${fmt(g.median)}, middle half ${fmt(g.q1)} – ${fmt(g.q3)}`}</title>
            <text x={MARGIN.left - 8} y={lane(g.label) + 4} textAnchor="end" className="fill-ink text-small">{short(g.label)}</text>
            <line x1={x(g.median)} x2={x(g.median)} y1={lane(g.label) - LANE / 2 + 6} y2={lane(g.label) + LANE / 2 - 6} stroke="var(--tl-ink)" strokeWidth={2} />
          </g>
        ))}
        <g>
          {dots.map((d, i) => (
            <circle key={`${d.label}-${i}`} cx={d.x} cy={d.y} r={R} fill={focus && !focus.has(d.label) ? MUTED : SERIES[0]} fillOpacity={0.85} data-dot={d.label}>
              <title>{`${d.label}: ${fmt(d.value)}`}</title>
            </circle>
          ))}
        </g>
      </svg>
    </div>
  );
}
