import { scaleLinear } from "d3-scale";
import { curveStepAfter, line } from "d3-shape";

import { focusOf, GRID, Legend, MUTED, SERIES, short, spreadLabels, useWidth } from "./svgChart";

const MARGIN = { top: 12, right: 120, bottom: 34, left: 48 };
const HEIGHT = 280;

export interface SurvivalFigures {
  event: string;
  as_of: string;
  left_out: number;
  max_t: number;
  curves: { label: string; n: number; events: number; median: number | null; points: { t: number; s: number; at_risk: number; events: number; censored: number }[] }[];
}

/** The step line of a curve (d3-shape, `curveStepAfter`): the share holds until the next event. */
export function stepPath(points: { t: number; s: number }[], x: (t: number) => number, y: (s: number) => number, maxT: number): string {
  const last = points[points.length - 1];
  const pts = last && last.t < maxT ? [...points, { t: maxT, s: last.s }] : points;
  return line<{ t: number; s: number }>().x((p) => x(p.t)).y((p) => y(p.s)).curve(curveStepAfter)(pts) ?? "";
}

/**
 * Time to event (D3, US-188): the Kaplan–Meier share of items not yet ended by days since their start, one step
 * line per group (at most 6), items still open marked as ticks where they leave the count, the median at 50 %.
 * The estimate is worked out by the server (COMP-001); this only draws it.
 */
export function Survival({ figures, title, highlight }: { figures: SurvivalFigures; title?: string; highlight?: string[] }) {
  const [ref, width] = useWidth();
  const x = scaleLinear().domain([0, Math.max(1, figures.max_t)]).nice().range([MARGIN.left, width - MARGIN.right]);
  const y = scaleLinear().domain([0, 1]).range([HEIGHT - MARGIN.bottom, MARGIN.top]);
  const focus = focusOf(highlight, figures.curves.map((c) => c.label));
  const colour = (k: number, label: string) => (focus && !focus.has(label) ? MUTED : SERIES[k % SERIES.length]!);
  const ends = figures.curves.map((c) => y(c.points[c.points.length - 1]?.s ?? 1));
  const labelY = spreadLabels(ends);
  const pct = (s: number) => `${Math.round(s * 1000) / 10}%`;
  const label = title ?? `Share not yet at ${figures.event.toLowerCase()}, by days since start`;
  return (
    <div ref={ref} data-chart="survival" className="w-full">
      {figures.curves.length > 1 && <Legend items={figures.curves.map((c, k) => ({ label: c.label, colour: colour(k, c.label) }))} />}
      <svg width={width} height={HEIGHT} role="img" aria-label={label} className="block">
        <title>{label}</title>
        <g aria-hidden>
          {y.ticks(4).map((t) => (
            <g key={t}>
              <line x1={MARGIN.left} x2={width - MARGIN.right} y1={y(t)} y2={y(t)} stroke={GRID} strokeDasharray={t === 0.5 ? "4 3" : undefined} />
              <text x={MARGIN.left - 6} y={y(t) + 4} textAnchor="end" className="fill-ink-muted text-[11px]">{pct(t)}</text>
            </g>
          ))}
          {x.ticks(Math.max(2, Math.floor((width - MARGIN.left - MARGIN.right) / 80))).map((t) => (
            <text key={t} x={x(t)} y={HEIGHT - MARGIN.bottom + 14} textAnchor="middle" className="fill-ink-muted text-[11px]">{t}</text>
          ))}
          <text x={(MARGIN.left + width - MARGIN.right) / 2} y={HEIGHT - 4} textAnchor="middle" className="fill-ink-muted text-[11px]">Days since start</text>
        </g>
        {figures.curves.map((c, k) => (
          <g key={c.label} data-curve={c.label}>
            <title>{`${c.label}: ${c.events} of ${c.n} ended${c.median !== null ? `; half by day ${c.median}` : "; fewer than half have ended"}`}</title>
            <path d={stepPath(c.points, x, y, figures.max_t)} fill="none" stroke={colour(k, c.label)} strokeWidth={2} />
            {c.points.filter((p) => p.censored > 0).map((p) => (
              <line key={p.t} x1={x(p.t)} x2={x(p.t)} y1={y(p.s) - 4} y2={y(p.s) + 4} stroke={colour(k, c.label)} strokeWidth={1.5}>
                <title>{`Day ${p.t}: ${p.censored} still open`}</title>
              </line>
            ))}
            {c.median !== null && <circle cx={x(c.median)} cy={y(0.5)} r={3.5} fill={colour(k, c.label)}><title>{`${c.label}: half by day ${c.median}`}</title></circle>}
            {/* direct label at the line's end (US-188), moved apart from its neighbours */}
            <text x={width - MARGIN.right + 6} y={labelY[k]! + 4} className="fill-ink text-small">{short(c.label, 14)} {pct(c.points[c.points.length - 1]?.s ?? 1)}</text>
          </g>
        ))}
      </svg>
      {figures.left_out > 0 && (
        <p className="text-small text-ink-muted">{figures.left_out} {figures.left_out === 1 ? "item has" : "items have"} no start date or end before they start: left out.</p>
      )}
    </div>
  );
}
