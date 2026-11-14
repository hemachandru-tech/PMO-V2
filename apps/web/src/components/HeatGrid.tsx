import { CircleAlert, CircleCheck, OctagonAlert } from "lucide-react";

import type { Column } from "../api/app";
import type { ChartSpec } from "../api/querySpec";
import { cn } from "../lib/utils";
import { formatValue } from "./format";

type Tone = "good" | "warning" | "critical" | "neutral";

const NOT_SET = "Not set";
const TONE_CELL: Record<Tone, string> = {
  good: "bg-status-ok-soft",
  warning: "bg-status-warn-soft",
  critical: "bg-status-danger-soft",
  neutral: "bg-surface-sunken",
};
const TONE_HEAD = {
  good: { icon: CircleCheck, className: "text-status-ok" },
  warning: { icon: CircleAlert, className: "text-status-warn" },
  critical: { icon: OctagonAlert, className: "text-status-danger" },
} as const;
const STEPS = 5;

const label = (v: unknown) => (v === null || v === undefined ? NOT_SET : formatValue(v));

/** Ramp step 1–5 of a value between the grid's lowest and highest (presentation only; the figures are the server's). */
export function rampStep(value: number, lo: number, hi: number): number {
  if (hi <= lo) return 3;
  return Math.min(STEPS, 1 + Math.floor(((value - lo) / (hi - lo)) * STEPS));
}

export interface HeatGridProps {
  spec: ChartSpec;
  columns: Column[];
  rows: unknown[][];
  title?: string;
  /** A cell was chosen: its row and column values (null = Not set). */
  onMark?: (mark: Record<string, unknown>) => void;
  /** The chosen cell, shown pressed. */
  selected?: Record<string, unknown> | null;
}

/**
 * Heat grid (US-157): two breakdowns by one measure as a real table. Status columns take the RAG fills with the
 * status word and icon in the header; other columns a single-hue ramp with a legend. Every cell shows its number,
 * so colour is never the only signal; with `onMark` every cell is a button (cross-filter or drill).
 */
export function HeatGrid({ spec, columns, rows, title, onMark, selected }: HeatGridProps) {
  const names = columns.map((c) => c.name);
  const xi = names.indexOf(spec.x ?? "");
  const yi = names.indexOf(spec.y ?? "");
  const mi = names.indexOf(spec.series?.[0] ?? "");
  if (xi < 0 || yi < 0 || mi < 0) return <p className="text-small text-ink-muted">This heat grid can't be drawn from these columns.</p>;
  const measure = columns[mi]!;
  const xs = [...new Set(rows.map((r) => label(r[xi])))];
  const ys = [...new Set(rows.map((r) => label(r[yi])))];
  const raw = new Map<string, unknown[]>(rows.map((r) => [`${label(r[xi])}\u0000${label(r[yi])}`, r]));
  const nums = rows.map((r) => r[mi]).filter((v): v is number => typeof v === "number");
  const [lo, hi] = nums.length ? [Math.min(...nums), Math.max(...nums)] : [0, 0];
  const rag = spec.ramp === "rag";
  const tone = (y: string): Tone => (spec.tones?.[y] as Tone | undefined) ?? "neutral";
  const isSelected = (r: unknown[]) => !!selected && selected[spec.x!] === r[xi] && selected[spec.y!] === r[yi];
  return (
    <div className="grid gap-2">
      <div className="overflow-x-auto">
        <table className="w-full border-separate border-spacing-0.5 text-small" data-chart="heat_grid">
          {title && <caption className="sr-only">{title}</caption>}
          <thead>
            <tr>
              <th scope="col" className="px-2 py-1 text-left font-medium text-ink-muted">{columns[xi]!.label}</th>
              {ys.map((y) => {
                const head = rag && tone(y) !== "neutral" ? TONE_HEAD[tone(y) as keyof typeof TONE_HEAD] : null;
                const Icon = head?.icon;
                return (
                  <th key={y} scope="col" className={cn("px-2 py-1 text-right font-medium", head ? head.className : "text-ink-muted")}>
                    <span className="inline-flex items-center gap-1">{Icon && <Icon aria-hidden className="size-3.5" />}{y}</span>
                  </th>
                );
              })}
            </tr>
          </thead>
          <tbody>
            {xs.map((x) => (
              <tr key={x}>
                <th scope="row" className="max-w-48 truncate px-2 py-1 text-left font-normal text-ink" title={x}>{x}</th>
                {ys.map((y) => {
                  const r = raw.get(`${x}\u0000${y}`);
                  const v = r?.[mi];
                  if (r === undefined || typeof v !== "number") {
                    return <td key={y} className="px-2 py-1 text-right text-ink-muted"><span aria-hidden>—</span><span className="sr-only">No records</span></td>;
                  }
                  const text = formatValue(v, measure.format);
                  const step = rampStep(v, lo, hi);
                  const fill = rag ? TONE_CELL[tone(y)] : undefined;
                  const style = rag ? undefined : { backgroundColor: `var(--tl-seq-${step})` };
                  const value = rag
                    ? <span className="font-mono tabular-nums text-ink">{text}</span>
                    : <span className="rounded-sm bg-surface-raised px-1 font-mono tabular-nums text-ink">{text}</span>;
                  return (
                    <td key={y} className={cn("p-0 text-right", fill)} style={style} data-step={rag ? undefined : step} data-tone={rag ? tone(y) : undefined}>
                      {onMark ? (
                        <button type="button" aria-pressed={isSelected(r)} aria-label={`${x}, ${y}: ${text}`}
                          className={cn("block w-full px-2 py-1 text-right hover:outline hover:outline-1 hover:outline-line-strong focus-visible:focus-ring", isSelected(r) && "outline outline-2 outline-focus")}
                          onClick={() => onMark({ [spec.x!]: r[xi] ?? null, [spec.y!]: r[yi] ?? null })}>
                          {value}
                        </button>
                      ) : <span className="block px-2 py-1">{value}</span>}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {!rag && nums.length > 0 && (
        <div className="flex items-center gap-2 text-small text-ink-muted" aria-hidden>
          <span>{formatValue(lo, measure.format)}</span>
          <span className="flex">{Array.from({ length: STEPS }, (_, i) => <span key={i} className="h-2 w-5" style={{ backgroundColor: `var(--tl-seq-${i + 1})` }} />)}</span>
          <span>{formatValue(hi, measure.format)}</span>
        </div>
      )}
    </div>
  );
}

export interface MeterFigures {
  part: number;
  whole: number;
  percent: number | null;
  note: string;
}

/** Compliance meter (US-157): "31 of 111", the percent and the note, all from the server (COMP-001). */
export function Meter({ meter, format, title }: { meter: MeterFigures; format?: string | null; title?: string }) {
  const pct = meter.percent;
  const words = `${formatValue(meter.part, format)} of ${formatValue(meter.whole, format)}${pct !== null ? ` (${pct}%)` : ""}`;
  return (
    <div className="grid gap-2" data-chart="meter">
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <span className="font-mono text-figure-xl tabular-nums text-ink">{pct !== null ? `${pct}%` : "—"}</span>
        <span className="text-body text-ink-muted">{formatValue(meter.part, format)} of {formatValue(meter.whole, format)}</span>
      </div>
      <div role="meter" aria-label={title ?? meter.note} aria-valuemin={0} aria-valuemax={100} aria-valuenow={pct ?? 0} aria-valuetext={words}
        className="h-3 w-full overflow-hidden rounded-sm border border-line-strong bg-surface-sunken">
        <div className="h-full bg-accent" style={{ width: `${Math.max(0, Math.min(100, pct ?? 0))}%` }} />
      </div>
      <p className="text-small text-ink-muted">{meter.note}</p>
    </div>
  );
}
