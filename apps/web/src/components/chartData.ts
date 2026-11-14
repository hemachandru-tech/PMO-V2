import type { Column } from "../api/app";
import type { ChartSpec } from "../api/querySpec";
import { chartPaint, cssColour } from "./chartColours";
import { formatValue } from "./format";
import { focusOf, MUTED, SERIES } from "./svgChart";
import type { Layout } from "../design/layout";

/**
 * The standard charts (ranked bars, columns, trend, paired bars, stacked status bars, donut) as plain data, for the
 * Recharts and Observable Plot drawers (ENH-018). It reads the spec exactly as `toOption` does for ECharts, so the
 * three libraries show the same bars, labels, colours and clicks.
 */
export const STANDARD_TYPES = new Set(["ranked_bar", "columns", "trend", "paired_bars", "stacked_status", "donut"]);

export interface StandardSeries {
  /** A safe key for chart configs and data objects ("s0", "s1", …). */
  key: string;
  label: string;
  column: number;
  colour: string;
  format: (v: unknown) => string;
}

export interface StandardChart {
  kind: "bar" | "hbar" | "line" | "donut";
  labels: string[];
  series: StandardSeries[];
  /** One object per row: `label` plus each series' value under its key. */
  data: Record<string, unknown>[];
  /** Highlight-and-grey (US-186): the labels that keep their colour, or null. */
  focus: Set<string> | null;
  /** Ordered buckets (US-157): one ramp colour per bar and its value on top. */
  ordinal: boolean;
  /** Several lines are named at their ends instead of in a legend (US-188). */
  ends: boolean;
  integer: boolean;
  /** A derived figure or share labelling the series it is worked out from (US-176), "" when none. */
  companion: (series: number, row: number) => string;
  /** The widget's colour for row `ri` of series `si` when it differs from its series (US-224), as a CSS colour. */
  point: (si: number, ri: number) => string | undefined;
  /** Colour of a bar left out of the highlight: grey, or with Vivid its own colour faded (ENH-027). */
  muted: (si: number, ri: number) => string;
  /** Colour of a donut's slice `ri`. */
  slice: (ri: number) => string;
}

export const RAMP = ["var(--tl-seq-2)", "var(--tl-seq-3)", "var(--tl-seq-4)", "var(--tl-seq-5)"];
export const EDGE = "var(--tl-chart-1)";
export { MUTED };

/** Colour of the i-th of n ordered buckets: the lowest the lightest step, the highest the strongest. */
export function rampAt(i: number, n: number): string {
  return RAMP[n <= 1 ? RAMP.length - 1 : Math.round((i / (n - 1)) * (RAMP.length - 1))]!;
}

export function standardChart(spec: ChartSpec, columns: Column[], rows: unknown[][], highlight?: string[], layout?: Layout): StandardChart {
  const xi = columns.findIndex((c) => c.name === spec.x);
  const labels = rows.map((r) => (xi >= 0 ? (r[xi] == null ? "Not set" : formatValue(r[xi])) : ""));
  const measures = (spec.series ?? []).map((s) => columns.findIndex((c) => c.name === s)).filter((i) => i >= 0);
  const kind = spec.type === "donut" ? "donut" : spec.type === "ranked_bar" ? "hbar" : spec.type === "trend" ? "line" : "bar";
  const picked = kind === "donut" ? measures.slice(0, 1) : measures;
  const paint = chartPaint(spec.colours, picked.map((mi) => columns[mi]?.label ?? columns[mi]?.name ?? ""), labels, layout);
  const series = picked.map((mi, k) => ({
    key: `s${k}`,
    label: columns[mi]?.label ?? columns[mi]?.name ?? "",
    column: mi,
    colour: paint ? cssColour(paint.series(k)) : SERIES[k % SERIES.length]!,
    format: (v: unknown) => formatValue(v, columns[mi]?.format),
  }));
  const data = rows.map((r, i) => Object.fromEntries([["label", labels[i]], ...series.map((s) => [s.key, r[s.column] ?? null])]));
  const first = columns[series[0]?.column ?? -1];
  const companion = (si: number, ri: number) => {
    const of = columns[series[si]?.column ?? -1]?.name;
    return Object.entries(spec.companions ?? {})
      .filter(([, m]) => m === of)
      .map(([name]) => columns.findIndex((c) => c.name === name))
      .filter((ci) => ci >= 0)
      .map((ci) => (rows[ri]?.[ci] == null ? "–" : formatValue(rows[ri]![ci], columns[ci]?.format)))
      .join(" · ");
  };
  return {
    kind,
    labels,
    series,
    data,
    focus: series.length === 1 ? focusOf(highlight?.map((h) => formatValue(h)), labels) : null,
    ordinal: spec.type === "columns" && spec.ramp === "ordinal",
    ends: kind === "line" && series.length > 1,
    integer: first?.format === "integer" || first?.data_type === "integer",
    companion,
    point: (si, ri) => {
      const t = paint?.point(si, ri);
      return t ? cssColour(t) : undefined;
    },
    slice: (ri) => (paint ? cssColour(paint.slice(ri)) : SERIES[ri % SERIES.length]!),
    muted: (si, ri) => {
      const t = paint?.byBar ? paint.point(si, ri) : undefined;
      return t ? `color-mix(in srgb, ${cssColour(t)} 35%, var(--tl-surface-raised))` : MUTED;
    },
  };
}

/**
 * One row of several measures and no breakdown, as ranked bars (EXEC-173): the row turned into one row per measure,
 * the measure's name as the category, so every library draws ordinary ranked bars. Null for any other chart.
 */
export function measureBars(spec: ChartSpec, columns: Column[], rows: unknown[][]): { spec: ChartSpec; columns: Column[]; rows: unknown[][] } | null {
  if (spec.type !== "ranked_bar" || spec.x || rows.length !== 1) return null;
  const picked = (spec.series ?? []).map((s) => columns.findIndex((c) => c.name === s)).filter((i) => i >= 0);
  if (picked.length < 2) return null;
  const first = columns[picked[0]!]!;
  const { names, common } = sharedName(picked.map((i) => columns[i]!.label || columns[i]!.name));
  return {
    spec: { ...spec, x: "measure", series: ["value"], companions: {} },
    columns: [
      { name: "measure", kind: "dimension", label: "Measure", ref: "", data_type: "string", format: null },
      { name: "value", kind: first.kind, label: common || "Value", ref: "", data_type: first.data_type, format: first.format },
    ],
    rows: picked.map((i, k) => [names[k], rows[0]![i] ?? null]),
  };
}

/**
 * Bar names without the words every measure shares, so five bars aren't all cut to "Forecast cost: in…":
 * "Forecast cost: internal (USD)" … → "Internal" …, and "Forecast cost (USD)" for the values.
 */
export function sharedName(labels: string[]): { names: string[]; common: string } {
  const unit = /\s*\([^()]*\)$/.exec(labels[0] ?? "")?.[0] ?? "";
  const bare = unit && labels.every((l) => l.endsWith(unit)) ? labels.map((l) => l.slice(0, -unit.length)) : labels;
  const stem = /^(.+?):\s+/.exec(bare[0] ?? "")?.[1];
  const cut = stem && bare.every((l) => l.startsWith(`${stem}: `)) ? stem.length + 2 : 0;
  const names = bare.map((l) => l.slice(cut)).map((n) => n.charAt(0).toUpperCase() + n.slice(1));
  if (names.some((n) => !n) || new Set(names).size !== names.length) return { names: labels, common: "" };
  const common = [cut ? stem : "", bare === labels ? "" : unit.trim()].filter(Boolean).join(" ");
  return { names, common };
}

/** The mark a click names (the same as ECharts): the row's category, and its stack value on a stacked chart. */
export function standardMark(spec: ChartSpec, columns: Column[], rows: unknown[][], row: number, seriesLabel?: string): Record<string, unknown> | null {
  const xi = columns.findIndex((c) => c.name === spec.x);
  if (xi < 0 || !spec.x || rows[row] === undefined) return null;
  const mark: Record<string, unknown> = { [spec.x]: rows[row]![xi] ?? null };
  if (spec.stack && seriesLabel !== undefined) mark[spec.stack] = seriesLabel === "Not set" ? null : seriesLabel;
  return mark;
}
