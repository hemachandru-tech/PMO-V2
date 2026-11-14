import type { Column } from "../api/app";
import type { ChartSpec } from "../api/querySpec";
import { formatCompact, formatValue } from "./format";

/**
 * ECharts options for the PMO visual catalogue wave 1 (US-187): waterfall, Pareto, XmR control chart, Gantt, funnel
 * and small multiples; and wave 2 (US-188): calendar heatmap, sankey and treemap. Every figure drawn here — running totals, cumulative shares, limits, drop-offs, the shared
 * axis — comes from the API in `spec.figures` (COMP-001); this file only places it. Every mark carries its value as
 * a label or in the tooltip, so colour is never the only signal.
 */
export interface CatalogueColours {
  up: string;
  down: string;
  total: string;
  muted: string;
  limit: string;
  danger: string;
  series: string;
  line: string;
  ink: string;
  /** US-188: the sequential ramp, lightest first (calendar), and the categorical series (sankey, treemap). */
  seq: string[];
  palette: string[];
}

type Option = Record<string, unknown>;
interface Step { label: string; kind: "start" | "up" | "down" | "total"; value: number; low: number; high: number }
interface Stage { label: string; value: number; pct_of_first: number | null; drop_pct: number | null }

const NOT_SET = "Not set";
const label = (v: unknown) => (v === null || v === undefined ? NOT_SET : String(v));

/** Label text in the theme's ink with no halo (ECharts' default text border reads as a smudge on dark). */
const text = (c: CatalogueColours, color = c.ink) => ({ color, textBorderWidth: 0 });

/** Height of the chart box: panels and timeline rows need more room than one chart (px). */
export function catalogueHeight(spec: ChartSpec, rows: unknown[][]): number | undefined {
  if (spec.type === "calendar") return ((spec.figures?.years as number[] | undefined) ?? [0]).length * CALENDAR_HEIGHT + 40;
  if (spec.type === "sankey") return 340;
  if (spec.type === "treemap") return 320;
  if (spec.type === "small_multiples") {
    const n = ((spec.figures?.panels as string[] | undefined) ?? []).length;
    return Math.ceil(n / panelColumns(n)) * PANEL_HEIGHT + 20;
  }
  if (spec.type === "gantt") return Math.max(160, Math.min(rows.length, 40) * 24 + 70);
  return undefined;
}

const PANEL_HEIGHT = 140;

export function panelColumns(n: number): number {
  return n <= 3 ? n : n <= 4 ? 2 : n <= 9 ? 3 : 4;
}

export function catalogueOption(spec: ChartSpec, columns: Column[], rows: unknown[][], colours: CatalogueColours, highlight?: string[]): Option {
  // Highlight-and-grey across visuals (US-188): named items keep their colour, the rest are muted.
  const focus = highlight?.length ? new Set(highlight) : null;
  const keep = (name: string) => !focus || focus.has(name);
  const figures = (spec.figures ?? {}) as Record<string, unknown>;
  const at = (name: string | null | undefined) => columns.findIndex((c) => c.name === name);
  const mi = at(spec.series?.[0]);
  const format = columns[mi]?.format;
  const fmt = (v: unknown) => formatValue(v, format);
  switch (spec.type) {
    case "waterfall":
      return waterfall(figures.steps as Step[], (figures.format as string | undefined) ?? format, colours, keep);
    case "pareto":
      return pareto(figures, columns[mi]?.label ?? "", fmt, colours, keep);
    case "xmr":
      return xmr(figures, rows.map((r) => label(r[at(spec.x)])), rows.map((r) => r[mi] as number | null), columns[mi]?.label ?? "", fmt, colours);
    case "gantt":
      return gantt(rows, at(spec.x), at(figures.start as string), at(figures.end as string), colours);
    case "funnel":
      return funnel(figures.stages as Stage[], fmt, colours, keep);
    case "small_multiples":
      return smallMultiples(spec, rows, at(spec.x), at(spec.y), mi, fmt, colours);
    case "calendar":
      return calendar(figures, rows.map((r) => [label(r[at(spec.x)]), r[mi] as number | null]), columns[mi]?.label ?? "", fmt, colours);
    case "sankey":
      return sankey(figures, fmt, colours, keep);
    case "treemap":
      return treemap(figures, columns[mi]?.label ?? "", fmt, colours, keep);
    default:
      return {};
  }
}

/** Stacked bars on a transparent base: each step floats from the running total; start and total stand on zero. */
function waterfall(steps: Step[], format: string | null | undefined, c: CatalogueColours, keep: (name: string) => boolean): Option {
  const signed = (s: Step, short = false) => {
    const text = (short ? formatCompact : formatValue)(s.value, format);
    return s.kind === "up" ? `+${text}` : text;
  };
  const colour = (s: Step) => (s.kind === "start" || s.kind === "total" || keep(s.label) ? (s.kind === "up" ? c.up : s.kind === "down" ? c.down : c.total) : c.muted);
  return {
    tooltip: { trigger: "axis", axisPointer: { type: "shadow" }, formatter: (ps: { dataIndex: number }[]) => {
      const s = steps[ps[0]?.dataIndex ?? 0]!;
      return `${s.label}: ${signed(s)}`;
    } },
    grid: { left: 60, right: 20, top: 30, bottom: steps.length > 4 ? 70 : 50 },
    xAxis: { type: "category", data: steps.map((s) => s.label), axisLabel: { overflow: "truncate", width: 80, interval: 0, rotate: steps.length > 4 ? 30 : 0 } },
    yAxis: { type: "value", axisLabel: { formatter: (v: number) => formatCompact(v, format) } },
    series: [
      { type: "bar", stack: "w", silent: true, itemStyle: { color: "transparent" }, emphasis: { disabled: true }, data: steps.map((s) => s.low) },
      {
        type: "bar", stack: "w", name: "Change",
        data: steps.map((s) => ({ value: s.high - s.low, itemStyle: { color: colour(s) } })),
        label: { show: true, position: "top", ...text(c), formatter: (p: { dataIndex: number }) => signed(steps[p.dataIndex]!, true) },
      },
    ],
  };
}

/** Bars largest first with the cumulative share on a second axis; past the vital few the bars are muted. */
function pareto(f: Record<string, unknown>, measure: string, fmt: (v: unknown) => string, c: CatalogueColours, keep: (name: string) => boolean): Option {
  const labels = (f.labels as string[]) ?? [];
  const values = (f.values as number[]) ?? [];
  const cumulative = (f.cumulative_pct as number[]) ?? [];
  const vital = (f.vital_few as number) ?? labels.length;
  return {
    tooltip: { trigger: "axis" },
    legend: {},
    grid: { left: 60, right: 50, top: 40, bottom: 50 },
    xAxis: { type: "category", data: labels, axisLabel: { overflow: "truncate", width: 90 } },
    yAxis: [
      { type: "value", axisLabel: { formatter: fmt } },
      { type: "value", min: 0, max: 100, splitLine: { show: false }, axisLabel: { formatter: "{value}%" } },
    ],
    series: [
      { type: "bar", name: measure, data: values.map((v, i) => (i < vital && keep(labels[i]!) ? v : { value: v, itemStyle: { color: c.muted } })) },
      {
        type: "line", name: "Cumulative share", yAxisIndex: 1, data: cumulative, itemStyle: { color: c.line }, lineStyle: { color: c.line },
        label: { show: true, position: "top", ...text(c), formatter: (p: { value: number }) => `${p.value}%` },
        markLine: { silent: true, symbol: "none", lineStyle: { type: "dashed", color: c.limit }, label: { formatter: "80%", position: "insideStartTop", ...text(c, c.limit) }, data: [{ yAxis: 80 }] },
      },
    ],
  };
}

/** Individuals chart: the series, its mean and the natural process limits; points past a limit are marked. */
function xmr(f: Record<string, unknown>, labels: string[], values: (number | null)[], measure: string, fmt: (v: unknown) => string, c: CatalogueColours): Option {
  const beyond = new Set((f.beyond as string[]) ?? []);
  const line = (name: string, value: unknown, type: string) => ({ name, yAxis: value, lineStyle: { type, color: c.limit }, label: { formatter: `${name} ${fmt(value)}`, position: "insideEndTop", ...text(c, c.limit) } });
  return {
    tooltip: { trigger: "axis" },
    grid: { left: 60, right: 20, top: 30, bottom: 40 },
    xAxis: { type: "category", data: labels },
    // the limits stay in view even when every point is inside them
    yAxis: { type: "value", scale: true, axisLabel: { formatter: fmt },
      min: (v: { min: number }) => Math.min(v.min, Number(f.lower)), max: (v: { max: number }) => Math.max(v.max, Number(f.upper)) },
    series: [{
      type: "line", name: measure,
      data: values.map((v, i) => (beyond.has(labels[i]!) ? { value: v, symbolSize: 11, symbol: "diamond", itemStyle: { color: c.danger } } : v)),
      markLine: { silent: true, symbol: "none", data: [line("Upper limit", f.upper, "dashed"), line("Mean", f.mean, "solid"), line("Lower limit", f.lower, "dashed")] },
    }],
  };
}

const day = (v: unknown) => (v === null || v === undefined ? NaN : Date.parse(String(v)));

/** Bars from start to end on a time axis, one row per item; rows without both dates in order are not drawn. */
export function ganttData(rows: unknown[][], xi: number, si: number, ei: number): { names: string[]; data: { name: string; value: [number, number, number, number] }[] } {
  const names = rows.slice(0, 40).map((r) => label(r[xi]));
  const data = rows.slice(0, 40).flatMap((r, i) => {
    const s = day(r[si]);
    const e = day(r[ei]);
    return Number.isNaN(s) || Number.isNaN(e) || e < s ? [] : [{ name: names[i]!, value: [i, s, e, i] as [number, number, number, number] }];
  });
  return { names, data };
}

function gantt(rows: unknown[][], xi: number, si: number, ei: number, c: CatalogueColours): Option {
  const { names, data } = ganttData(rows, xi, si, ei);
  const iso = (ms: number) => new Date(ms).toISOString().slice(0, 10);
  return {
    tooltip: { formatter: (p: { name: string; value: number[] }) => `${p.name}: ${iso(p.value[1]!)} – ${iso(p.value[2]!)}` },
    grid: { left: 140, right: 20, top: 20, bottom: 40 },
    xAxis: { type: "time" },
    yAxis: { type: "category", data: names, inverse: true, axisLabel: { overflow: "truncate", width: 130 } },
    series: [{
      type: "custom",
      encode: { x: [1, 2], y: 0 },
      data,
      itemStyle: { color: c.series },
      renderItem: (_: unknown, api: { value: (i: number) => number; coord: (v: number[]) => number[]; size: (v: number[]) => number[]; style: () => object }) => {
        const start = api.coord([api.value(1), api.value(0)]);
        const end = api.coord([api.value(2), api.value(0)]);
        const height = api.size([0, 1])[1]! * 0.6;
        return { type: "rect", shape: { x: start[0], y: start[1]! - height / 2, width: Math.max(2, end[0]! - start[0]!), height, r: 2 }, style: api.style() };
      },
    }],
  };
}

/** Stages in order, as they are (never re-sorted), each labelled with its value, share of the first and drop. */
function funnel(stages: Stage[], fmt: (v: unknown) => string, c: CatalogueColours, keep: (name: string) => boolean): Option {
  const figures = (s: Stage) => [fmt(s.value), s.pct_of_first !== null ? `${s.pct_of_first}% of first` : null, s.drop_pct !== null ? `−${s.drop_pct}%` : null].filter(Boolean).join(" · ");
  const words = (s: Stage) => `${s.label}: ${figures(s)}`;
  return {
    tooltip: { trigger: "item", formatter: (p: { dataIndex: number }) => words(stages[p.dataIndex]!) },
    series: [{
      type: "funnel", sort: "none", left: "2%", width: "48%", top: 10, bottom: 10, minSize: "8%", gap: 2,
      data: stages.map((s) => ({ name: s.label, value: s.value, ...(keep(s.label) ? {} : { itemStyle: { color: c.muted } }) })),
      // two short lines beside each stage: its name, then its figures
      label: { show: true, position: "right", ...text(c), formatter: (p: { dataIndex: number }) => `${stages[p.dataIndex]!.label}\n${figures(stages[p.dataIndex]!)}` },
    }],
  };
}

/** One small trend per item on a shared value axis, largest total first (≤ 12 panels, US-187). */
function smallMultiples(spec: ChartSpec, rows: unknown[][], xi: number, yi: number, mi: number, fmt: (v: unknown) => string, c: CatalogueColours): Option {
  const f = (spec.figures ?? {}) as { panels?: string[]; max?: number; min?: number };
  const panels = (f.panels ?? []).slice(0, 12);
  const periods = [...new Set(rows.map((r) => label(r[xi])))];
  const cols = panelColumns(panels.length);
  const w = 100 / cols;
  const top = (i: number) => Math.floor(i / cols) * PANEL_HEIGHT;
  const grid = panels.map((_, i) => ({ left: `${(i % cols) * w + 2}%`, width: `${w - 5}%`, top: top(i) + 30, height: PANEL_HEIGHT - 58, containLabel: true }));
  return {
    tooltip: { trigger: "axis" },
    title: panels.map((p, i) => ({ text: p, left: `${(i % cols) * w + 2}%`, top: top(i) + 4, textStyle: { fontSize: 12, fontWeight: 600 } })),
    grid,
    xAxis: panels.map((_, i) => ({ type: "category", gridIndex: i, data: periods, axisLabel: { fontSize: 10 } })),
    yAxis: panels.map((_, i) => ({ type: "value", gridIndex: i, min: Math.min(0, f.min ?? 0), max: f.max, splitNumber: 2, axisLabel: { show: i % cols === 0, formatter: fmt, fontSize: 10 } })),
    series: panels.map((p, i) => ({
      type: "line", name: p, xAxisIndex: i, yAxisIndex: i, symbolSize: 4, itemStyle: { color: c.series }, lineStyle: { color: c.series },
      data: periods.map((period) => {
        const row = rows.find((r) => label(r[xi]) === period && label(r[yi]) === p);
        return row ? (row[mi] as number | null) : null;
      }),
    })),
  };
}

// --------------------------------------------------------------------------- US-188 wave 2 (ECharts)

const CALENDAR_HEIGHT = 130;

/** The ramp step of a day's value: 0 below the first edge … edges.length at or above the last (server's quartiles). */
export function calendarLevel(v: number, edges: number[]): number {
  return edges.filter((e) => v >= e).length;
}

/** One calendar per year in the range, each day coloured by its level, the legend naming each level's values. */
function calendar(f: Record<string, unknown>, days: [string, number | null][], measure: string, fmt: (v: unknown) => string, c: CatalogueColours): Option {
  const years = (f.years as number[]) ?? [];
  const edges = (f.edges as number[]) ?? [];
  const [start, end] = [String(f.start), String(f.end)];
  const ramp = c.seq.length ? c.seq : [c.series];
  const colour = (level: number) => ramp[Math.min(ramp.length - 1, Math.round((level / Math.max(1, edges.length)) * (ramp.length - 1)))]!;
  const bounds = [-Infinity, ...edges, Infinity];
  const pieces = bounds.slice(1).map((hi, k) => ({
    gte: bounds[k] === -Infinity ? undefined : bounds[k], lt: hi === Infinity ? undefined : hi, color: colour(k),
    label: k === 0 ? `Below ${fmt(edges[0])}` : hi === Infinity ? `${fmt(bounds[k])} or more` : `${fmt(bounds[k])} – ${fmt(hi)}`,
  }));
  return {
    tooltip: { formatter: (p: { value: [string, number] }) => `${p.value[0]}: ${measure} ${fmt(p.value[1])}` },
    // one piece per level; a single value has one level, no legend
    visualMap: { type: "piecewise", orient: "horizontal", left: "center", bottom: 0, show: edges.length > 0, pieces: edges.length ? pieces : [{ color: colour(0) }], textStyle: { color: c.ink }, itemWidth: 14, itemHeight: 10 },
    calendar: years.map((y, i) => ({
      top: 30 + i * CALENDAR_HEIGHT, left: 40, right: 16, cellSize: ["auto", 14],
      range: [`${y}` === start.slice(0, 4) ? start : `${y}-01-01`, `${y}` === end.slice(0, 4) ? end : `${y}-12-31`],
      yearLabel: { show: years.length > 1, position: "left", ...text(c) }, dayLabel: { firstDay: 1, nameMap: "en", ...text(c, c.limit), fontSize: 10 },
      monthLabel: { nameMap: "en", ...text(c, c.limit) }, itemStyle: { borderWidth: 1, borderColor: c.muted, color: "transparent" },
      splitLine: { lineStyle: { color: c.limit } },
    })),
    series: years.map((y, i) => ({ type: "heatmap", coordinateSystem: "calendar", calendarIndex: i, data: days.filter(([d]) => d.startsWith(`${y}`)) })),
  };
}

interface FlowNode { label: string; level: number; value: number }

/** Flows left to right through the breakdowns; a node's colour follows its first-level ancestor's position. */
function sankey(f: Record<string, unknown>, fmt: (v: unknown) => string, c: CatalogueColours, keep: (name: string) => boolean): Option {
  const nodes = (f.nodes as FlowNode[]) ?? [];
  const links = (f.links as { source: number; target: number; value: number }[]) ?? [];
  const focus = nodes.some((n) => !keep(n.label)) && nodes.some((n) => keep(n.label));
  const colour = (i: number) => (focus && !keep(nodes[i]!.label) ? c.muted : c.palette[i % c.palette.length]!);
  return {
    tooltip: { trigger: "item", formatter: (p: { dataType: string; dataIndex: number; data: { source?: number; target?: number; value: number } }) =>
      p.dataType === "edge" ? `${nodes[p.data.source!]?.label} → ${nodes[p.data.target!]?.label}: ${fmt(p.data.value)}` : `${nodes[p.dataIndex]?.label}: ${fmt(nodes[p.dataIndex]?.value)}` },
    series: [{
      type: "sankey", left: 8, right: 130, top: 12, bottom: 12, nodeGap: 10, nodeWidth: 14, draggable: false, layoutIterations: 32,
      emphasis: { focus: "adjacency" },
      // names must be unique: a value on two levels ("Open" → "Open") is two nodes
      data: nodes.map((n, i) => ({ name: `${n.level}:${n.label}`, depth: n.level, itemStyle: { color: colour(i) } })),
      links: links.map((l) => ({ ...l, lineStyle: { color: focus && !(keep(nodes[l.source]!.label) || keep(nodes[l.target]!.label)) ? c.muted : "source", opacity: 0.35 } })),
      label: { ...text(c), formatter: (p: { dataIndex: number }) => `${nodes[p.dataIndex]?.label} ${fmt(nodes[p.dataIndex]?.value)}` },
      labelLayout: { moveOverlap: "shiftY", hideOverlap: true },
    }],
  };
}

interface Tile { label: string; value: number; share: number | null; children: { label: string; value: number; share: number | null }[] }

/** Tiles sized by value, an outer level holding the inner one; every tile names its value and share. */
function treemap(f: Record<string, unknown>, measure: string, fmt: (v: unknown) => string, c: CatalogueColours, keep: (name: string) => boolean): Option {
  const nodes = (f.nodes as Tile[]) ?? [];
  const words = (t: { label: string; value: number; share: number | null }) => `${t.label}\n${fmt(t.value)}${t.share !== null ? ` · ${t.share}%` : ""}`;
  return {
    tooltip: { formatter: (p: { data: { words: string } }) => p.data?.words?.replace("\n", ": ") ?? "" },
    series: [{
      type: "treemap", name: measure, roam: false, nodeClick: false, breadcrumb: { show: false }, left: 4, right: 4, top: 4, bottom: 4,
      label: { show: true, ...text(c, "#fff"), formatter: (p: { data: { words: string } }) => p.data.words, overflow: "truncate" },
      upperLabel: { show: nodes.some((n) => n.children.length), height: 20, ...text(c, "#fff"), formatter: (p: { data: { words: string } }) => p.data.words.replace("\n", " · ") },
      levels: [{ itemStyle: { gapWidth: 3, borderColor: "transparent" } }, { itemStyle: { gapWidth: 1, borderColorSaturation: 0.5 } }],
      labelLayout: { hideOverlap: true },
      data: nodes.map((n, i) => ({
        name: n.label, value: n.value, words: words(n),
        itemStyle: { color: keep(n.label) ? c.palette[i % c.palette.length] : c.muted, borderColor: keep(n.label) ? c.palette[i % c.palette.length] : c.muted },
        children: n.children.length ? n.children.map((k) => ({ name: k.label, value: k.value, words: words(k), itemStyle: keep(n.label) || keep(k.label) ? undefined : { color: c.muted } })) : undefined,
      })),
    }],
  };
}
