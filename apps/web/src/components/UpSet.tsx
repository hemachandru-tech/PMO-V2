import { scaleBand, scaleLinear } from "d3-scale";

import { formatCompact, formatValue } from "./format";
import { MUTED, SERIES, short, useWidth } from "./svgChart";

const LEFT = 190; // set names and their size bars
const BARS = 120; // height of the intersection bars
const ROW = 22;
const SIZE_BAR = 56;

export interface UpSetFigures {
  sets: { label: string; size: number }[];
  intersections: { members: boolean[]; value: number; degree: number }[];
  more: number;
  total: number;
}

/** The words for a combination: the sets it is in, joined with "+" ("None of these" for none). */
export function combinationWords(sets: { label: string }[], members: boolean[]): string {
  return sets.filter((_, i) => members[i]).map((s) => s.label).join(" + ") || "None of these";
}

/**
 * UpSet (D3, US-188): which yes/no flags occur together (at most 6 sets). Bars on top count each combination,
 * largest first; the dot matrix below says which sets make it; each set's size is on the left. Counts come from the
 * server (COMP-001); every bar carries its value.
 */
export function UpSet({ figures, format, title, highlight }: { figures: UpSetFigures; format?: string | null; title?: string; highlight?: string[] }) {
  const [ref, width] = useWidth();
  const { sets, intersections } = figures;
  const x = scaleBand<number>().domain(intersections.map((_, i) => i)).range([LEFT, width - 8]).padding(0.25);
  const y = scaleLinear().domain([0, Math.max(1, ...intersections.map((c) => c.value))]).range([BARS, 16]);
  const sizes = scaleLinear().domain([0, Math.max(1, ...sets.map((s) => s.size))]).range([0, SIZE_BAR]);
  const height = BARS + 8 + sets.length * ROW + 8;
  const rowY = (k: number) => BARS + 8 + k * ROW + ROW / 2;
  const words = intersections.map((c) => combinationWords(sets, c.members));
  const focus = highlight?.length ? new Set(highlight) : null;
  const fmt = (v: number) => formatValue(v, format);
  return (
    <div ref={ref} data-chart="upset" className="w-full">
      <svg width={width} height={height} role="img" aria-label={title ?? "Which occur together"} className="block">
        <title>{title ?? "Which occur together"}</title>
        {sets.map((s, k) => (
          <g key={s.label} data-set={s.label}>
            <title>{`${s.label}: ${fmt(s.size)}`}</title>
            {k % 2 === 0 && <rect x={LEFT} y={rowY(k) - ROW / 2} width={Math.max(0, width - LEFT - 8)} height={ROW} fill="var(--tl-surface-sunken)" opacity={0.6} />}
            <rect x={SIZE_BAR + 4 - sizes(s.size)} y={rowY(k) - 6} width={sizes(s.size)} height={12} fill={focus && !focus.has(s.label) ? MUTED : SERIES[1]} />
            <text x={SIZE_BAR + 10} y={rowY(k) + 4} className="fill-ink text-small">{short(s.label, 18)}</text>
          </g>
        ))}
        {intersections.map((c, i) => {
          const cx = (x(i) ?? 0) + x.bandwidth() / 2;
          const on = c.members.map((m, k) => (m ? k : -1)).filter((k) => k >= 0);
          const muted = focus && !sets.some((s, k) => c.members[k] && focus.has(s.label));
          return (
            <g key={i} data-combination={words[i]}>
              <title>{`${words[i]}: ${fmt(c.value)}`}</title>
              <rect x={x(i)} y={y(c.value)} width={x.bandwidth()} height={BARS - y(c.value)} fill={muted ? MUTED : SERIES[0]} />
              <text x={cx} y={y(c.value) - 4} textAnchor="middle" className="fill-ink font-mono text-[11px]">{formatCompact(c.value, format)}</text>
              {sets.map((_, k) => <circle key={k} cx={cx} cy={rowY(k)} r={5} fill={c.members[k] ? "var(--tl-ink)" : "var(--tl-line)"} />)}
              {on.length > 1 && <line x1={cx} x2={cx} y1={rowY(on[0]!)} y2={rowY(on[on.length - 1]!)} stroke="var(--tl-ink)" strokeWidth={2} />}
            </g>
          );
        })}
      </svg>
      {figures.more > 0 && <p className="text-small text-ink-muted">{figures.more} smaller {figures.more === 1 ? "combination is" : "combinations are"} in the table.</p>}
    </div>
  );
}
