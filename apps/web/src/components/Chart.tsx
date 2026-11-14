import * as echarts from "echarts/core";
import { BarChart, LineChart, PieChart } from "echarts/charts";
import { GridComponent, LegendComponent, TooltipComponent } from "echarts/components";
import { LabelLayout } from "echarts/features";
import { CanvasRenderer } from "echarts/renderers";
import { lazy, Suspense, useEffect, useRef, useState } from "react";

import type { Column } from "../api/app";
import type { ChartSpec } from "../api/querySpec";
import { type ChartLibrary, useChartLibrary } from "../design/chartLibrary";
import { echartsTheme, tokenColor, VIVID_SERIES } from "../design/echartsTheme";
import { type Layout, useLayout } from "../design/layout";
import { paletteColor, usePaletteKey } from "../design/palette";
import { type ResolvedTheme, useTheme } from "../design/theme";
import { chartPaint, effectiveScheme, SCHEMES } from "./chartColours";
import { measureBars } from "./chartData";
import { formatValue } from "./format";
import { HeatGrid, Meter, type MeterFigures } from "./HeatGrid";
import { Button } from "./ui/button";
import { Skeleton } from "./ui/skeleton";

// The table view loads on demand, so a dashboard of charts does not download the DataTable (US-148 bundle budget).
const ResultTable = lazy(() => import("./ResultTable").then((m) => ({ default: m.ResultTable })));

echarts.use([BarChart, LineChart, PieChart, GridComponent, LegendComponent, TooltipComponent, CanvasRenderer, LabelLayout]);

// The visual catalogue (US-187) loads on demand with its own ECharts modules and d3-scale, so the charts most answers
// use keep their size (US-148 bundle budget). The print route waits for it before it says it is ready.
export const loadCatalogue = () => import("./CatalogueChart");
const CatalogueChart = lazy(() => loadCatalogue().then((m) => ({ default: m.CatalogueChart })));
const CATALOGUE_VISUALS = new Set(["dumbbell", "waterfall", "bullet", "pareto", "xmr", "gantt", "funnel", "small_multiples",
  "calendar", "sankey", "upset", "beeswarm", "ridgeline", "survival", "bump", "treemap", "waffle"]);
export const isCatalogueVisual = (type: string | undefined) => type !== undefined && CATALOGUE_VISUALS.has(type);

// The other chart libraries (ENH-018) draw the standard charts; each is its own chunk, loaded only when chosen.
const STANDARD_VISUALS = new Set(["ranked_bar", "columns", "trend", "paired_bars", "stacked_status", "donut"]);
export const isStandardVisual = (type: string | undefined) => type !== undefined && STANDARD_VISUALS.has(type);
export const loadChartLibrary = (library: ChartLibrary) =>
  library === "recharts" ? import("./RechartsChart") : library === "plot" ? import("./PlotChart") : Promise.resolve(null);
const RechartsChart = lazy(() => import("./RechartsChart").then((m) => ({ default: m.RechartsChart })));
const PlotChart = lazy(() => import("./PlotChart").then((m) => ({ default: m.PlotChart })));
// Token-driven light and dark themes (US-134), rebuilt when the tenant's palette changes (US-151); charts re-render
// when either changes. The Aurora layout (ENH-027) draws in the Vivid series.
let registeredFor: string | null = null;
function chartTheme(resolved: "light" | "dark", paletteKey: string, layout: Layout): string {
  if (registeredFor !== paletteKey) {
    echarts.registerTheme("tela-light", echartsTheme("light"));
    echarts.registerTheme("tela-dark", echartsTheme("dark"));
    echarts.registerTheme("tela-light-vivid", echartsTheme("light", VIVID_SERIES));
    echarts.registerTheme("tela-dark-vivid", echartsTheme("dark", VIVID_SERIES));
    registeredFor = paletteKey;
  }
  return layout === "aurora" ? `tela-${resolved}-vivid` : `tela-${resolved}`;
}

export interface ChartProps {
  spec: ChartSpec | null;
  columns: Column[];
  rows: unknown[][];
  onMark?: (mark: Record<string, unknown>) => void;
  onFigure?: () => void;
  title?: string;
  /** Controlled chart/table view: the caller shows the toggle (the viewer's widget header, US-139). */
  view?: "chart" | "table";
  /**
   * A still chart for print (US-096): SVG, no animation, no interaction, so the PDF holds vector charts drawn at
   * once. The print route registers the SVG renderer.
   */
  still?: boolean;
  /** The mark a cross-filter came from (US-157), shown pressed on a heat grid. */
  selected?: Record<string, unknown> | null;
  /** Highlight-and-grey (US-186): these category labels keep their colour, the others are muted. */
  highlight?: string[];
  /** Draw with this library instead of the tenant's (the Branding preview, US-206). */
  library?: ChartLibrary;
}

/** Renders a chart spec (US-051, ADR-007). Every chart has a table alternative. */
export function Chart({ spec, columns, rows, onMark, onFigure, title, view, still, selected, highlight, library: only }: ChartProps) {
  const [ownTable, setAsTable] = useState(false);
  const tenantLibrary = useChartLibrary();
  const library = only ?? tenantLibrary;
  const asTable = view ? view === "table" : ownTable;
  if (!spec || rows.length === 0) return <p className="text-small text-ink-muted">No records match the current scope and filters.</p>;
  if (spec.type === "figure") {
    const measure = columns.find((c) => c.kind === "measure");
    return (
      <button type="button" className="rounded-sm py-1 font-mono text-display tabular-nums text-ink focus-visible:focus-ring" onClick={onFigure} title="Show how this figure is defined">
        {formatValue(rows[0]?.[columns.indexOf(measure!)], measure?.format)}
      </button>
    );
  }
  // One row of measures as bars (EXEC-173): one bar per measure; a bar names no category, so it filters nothing.
  const bars = measureBars(spec, columns, rows);
  const drawn = bars ?? { spec, columns, rows };
  const onBar = bars ? undefined : onMark;
  return (
    <div>
      {spec.type !== "table" && !view && (
        <div className="flex justify-end">
          <Button variant="link" size="sm" onClick={() => setAsTable(!asTable)}>{asTable ? "Show chart" : "Show as table"}</Button>
        </div>
      )}
      {spec.type === "table" || asTable ? (
        <Suspense fallback={<Skeleton className="h-40 w-full" />}>
          <ResultTable columns={columns} rows={rows} onMark={onMark} caption={title} tones={spec.type === "table" && spec.colour ? spec.tones ?? {} : undefined} />
        </Suspense>
      ) : spec.type === "meter" && spec.meter ? (
        <Meter meter={spec.meter as unknown as MeterFigures} format={columns.find((c) => c.kind === "measure")?.format} title={title} />
      ) : isCatalogueVisual(spec.type) ? (
        <Suspense fallback={<Skeleton className="h-[280px] w-full" />}>
          <CatalogueChart spec={spec} columns={columns} rows={rows} onMark={onMark} title={title} still={still} highlight={highlight} />
        </Suspense>
      ) : spec.type === "heat_grid" ? (
        <HeatGrid spec={spec} columns={columns} rows={rows} title={title} onMark={still ? undefined : onMark} selected={selected} />
      ) : library !== "echarts" && isStandardVisual(spec.type) ? (
        <Suspense fallback={<Skeleton className="h-[280px] w-full" />}>
          {library === "recharts"
            ? <RechartsChart spec={drawn.spec} columns={drawn.columns} rows={drawn.rows} onMark={onBar} title={title} still={still} highlight={highlight} />
            : <PlotChart spec={drawn.spec} columns={drawn.columns} rows={drawn.rows} onMark={onBar} title={title} still={still} highlight={highlight} />}
        </Suspense>
      ) : (
        <EChart spec={drawn.spec} columns={drawn.columns} rows={drawn.rows} onMark={onBar} title={title} still={still} highlight={highlight} />
      )}
    </div>
  );
}

export interface EChartProps extends ChartProps {
  spec: ChartSpec;
  /** The option to draw instead of `toOption` (the catalogue's, US-187). */
  build?: (theme: ResolvedTheme) => echarts.EChartsCoreOption;
  /** The mark a click names, instead of the row at the clicked index. */
  markOf?: (p: { name: string; seriesName?: string; dataIndex: number }) => Record<string, unknown> | null;
  /** Box height in px, for charts that need more room than one chart. */
  height?: number;
}

export function EChart({ spec, columns, rows, onMark, title, still, highlight, build, markOf, height }: EChartProps) {
  const ref = useRef<HTMLDivElement>(null);
  const { resolved } = useTheme();
  const paletteKey = usePaletteKey();
  const layout = useLayout();
  useEffect(() => {
    if (!ref.current) return;
    const chart = echarts.init(ref.current, chartTheme(resolved, paletteKey, layout), { renderer: still ? "svg" : "canvas" });
    const muted = paletteColor("chart-other", resolved) ?? tokenColor("chart-other", resolved);
    // US-224: the widget's chart colours, as this theme's (and palette's) colours.
    const resolve = (token: string) => paletteColor(token, resolved) ?? tokenColor(token, resolved);
    const scheme = SCHEMES.find((s) => s.key === effectiveScheme(spec.colours, layout));
    const option = build
      ? { ...build(resolved), ...(scheme && scheme.key !== "brand" ? { color: scheme.tokens.map(resolve) } : {}) }
      : toOption(spec, columns, rows, rampColours(resolved), highlight?.length ? { labels: highlight, muted } : undefined, resolve);
    // Print cards are narrow: keep long axis labels (currency) inside the chart instead of clipping them.
    // Small multiples place their own panels (US-187): their grids are kept as they are.
    const grid = Array.isArray(option.grid) ? option.grid : { ...(option.grid as object), containLabel: true, left: 8 };
    chart.setOption(still ? { ...option, animation: false, tooltip: undefined, grid } : option);
    if (still) return () => chart.dispose();
    const xi = columns.findIndex((c) => c.name === spec.x);
    chart.on("click", (p) => {
      if (!onMark) return;
      if (markOf) {
        const mark = markOf({ name: p.name, seriesName: p.seriesName, dataIndex: p.dataIndex as number });
        if (mark) onMark(mark);
        return;
      }
      if (xi < 0) return;
      const row = rows[p.dataIndex as number];
      const mark: Record<string, unknown> = { [spec.x!]: row?.[xi] ?? null };
      if (spec.stack) mark[spec.stack] = p.seriesName === "Not set" ? null : p.seriesName;
      onMark(mark);
    });
    // Follows its box, not only the window: canvas widgets are resized on the grid (US-141).
    const observer = new ResizeObserver(() => chart.resize());
    observer.observe(ref.current);
    return () => {
      observer.disconnect();
      chart.dispose();
    };
  }, [spec, columns, rows, onMark, resolved, paletteKey, layout, still, highlight, build, markOf]);
  return <div ref={ref} data-chart={spec.type} className={still ? "h-[240px] w-full" : "h-[280px] w-full"} style={height ? { height } : undefined} role="img" aria-label={title ?? "Chart"} />;
}

/**
 * A KPI card's sparkline (US-139): a small line over the points the widget run API returned (never computed here).
 * Decorative for screen readers, which get the same series as text from `label`.
 */
export function Sparkline({ points, label }: { points: { period: string; value: number | null }[]; label: string }) {
  const ref = useRef<HTMLDivElement>(null);
  const { resolved } = useTheme();
  const paletteKey = usePaletteKey();
  const layout = useLayout();
  useEffect(() => {
    if (!ref.current) return;
    const chart = echarts.init(ref.current, chartTheme(resolved, paletteKey, layout), { renderer: "canvas" });
    chart.setOption({
      animation: false,
      grid: { left: 2, right: 2, top: 4, bottom: 4 },
      xAxis: { type: "category", show: false, data: points.map((p) => p.period), boundaryGap: false },
      yAxis: { type: "value", show: false, scale: true },
      series: [{ type: "line", data: points.map((p) => p.value), symbol: "none", lineStyle: { width: 1.5 }, areaStyle: { opacity: 0.12 } }],
    });
    const resize = () => chart.resize();
    window.addEventListener("resize", resize);
    return () => {
      window.removeEventListener("resize", resize);
      chart.dispose();
    };
  }, [points, resolved, paletteKey, layout]);
  return <div ref={ref} className="h-10 w-full" role="img" aria-label={label} />;
}

/** The ordinal ramp for ordered buckets (US-157): seq-2 … seq-5 with a chart-1 edge so every bar keeps 3:1. */
export function rampColours(theme: ResolvedTheme): { ramp: string[]; edge: string } {
  const c = (name: string) => paletteColor(name, theme) ?? tokenColor(name, theme);
  return { ramp: ["seq-2", "seq-3", "seq-4", "seq-5"].map(c), edge: c("chart-1") };
}

/** Colour of the i-th of n ordered buckets: the lowest bucket the lightest step, the highest the strongest. */
export function ordinalColour(i: number, n: number, ramp: string[]): string {
  return ramp[n <= 1 ? ramp.length - 1 : Math.round((i / (n - 1)) * (ramp.length - 1))]!;
}

/**
 * Highlight-and-grey (US-186): with one bar series, the bars whose label is highlighted keep the series colour and
 * the rest take `muted`; on a line the highlighted points are drawn larger. Labels that match nothing change nothing.
 */
export interface Highlight {
  labels: string[];
  muted: string;
}

export function toOption(spec: ChartSpec, columns: Column[], rows: unknown[][], colours?: { ramp: string[]; edge: string }, highlight?: Highlight,
  resolve?: (token: string) => string): echarts.EChartsCoreOption {
  const xi = columns.findIndex((c) => c.name === spec.x);
  // Dates read as words ("Jan 13 2025"); a click still finds its row by index (BUG-023).
  const labels = rows.map((r) => (xi >= 0 ? (r[xi] == null ? "Not set" : formatValue(r[xi])) : ""));
  const measureIdx = (spec.series ?? []).map((s) => columns.findIndex((c) => c.name === s));
  const fmt = (i: number) => (v: number) => formatValue(v, columns[i]?.format);
  const tooltip = { trigger: spec.type === "donut" ? "item" : "axis" };
  // The widget's chart colours (US-224); without them the theme's palette applies as before.
  const paint = resolve ? chartPaint(spec.colours, (spec.type === "donut" ? measureIdx.slice(0, 1) : measureIdx).map((mi) => columns[mi]?.label ?? ""), labels) : null;
  const paintPoint = (v: unknown, si: number, i: number) => {
    const t = paint?.point(si, i);
    if (!t || !resolve || spec.type === "trend") return v;
    if (v !== null && typeof v === "object") return "itemStyle" in v ? v : { ...v, itemStyle: { color: resolve(t) } };
    return { value: v, itemStyle: { color: resolve(t) } };
  };
  // US-176: a derived figure or share of the total labels the series it is worked out from, never a bar of its own.
  const companionText = (mi: number, row: unknown[] | undefined) =>
    Object.entries(spec.companions ?? {})
      .filter(([, of]) => of === columns[mi]?.name)
      .map(([name]) => columns.findIndex((c) => c.name === name))
      .filter((ci) => ci >= 0)
      .map((ci) => (row?.[ci] == null ? "–" : fmt(ci)(row[ci] as number)))
      .join(" · ");
  if (spec.type === "donut") {
    const mi = measureIdx[0] ?? -1;
    const label = companionText(mi, rows[0]) ? { formatter: (p: { name: string; dataIndex: number }) => `${p.name}: ${companionText(mi, rows[p.dataIndex])}` } : undefined;
    return { tooltip, series: [{ type: "pie", radius: ["45%", "70%"], label, data: rows.map((r, i) => ({ name: labels[i], value: r[mi],
      ...(paint && resolve ? { itemStyle: { color: resolve(paint.slice(i)) } } : {}) })) }] };
  }
  const horizontal = spec.type === "ranked_bar";
  const cat = { type: "category", data: labels, axisLabel: { overflow: "truncate", width: 120 } };
  const first = columns[measureIdx[0] ?? -1];
  const integer = first?.format === "integer" || first?.data_type === "integer";
  const val = { type: "value", minInterval: integer ? 1 : undefined, axisLabel: { formatter: fmt(measureIdx[0] ?? -1) } };
  const ordinal = spec.type === "columns" && spec.ramp === "ordinal" && colours !== undefined;
  const focus = highlight && measureIdx.length === 1 && labels.some((l) => highlight.labels.map((h) => formatValue(h)).includes(l)) ? new Set(highlight.labels.map((h) => formatValue(h))) : null;
  // Direct labels (US-188): a highlighted bar or point carries its value, so the eye needs no axis to read it.
  const marked = (r: unknown[], mi: number, i: number) => {
    if (!focus) return r[mi];
    const direct = { show: true, position: spec.type === "trend" ? "top" : horizontal ? "right" : "top", formatter: (p: { value: number }) => fmt(mi)(p.value) };
    if (spec.type === "trend") return focus.has(labels[i]!) ? { value: r[mi], symbolSize: 10, label: direct } : r[mi];
    // Vivid (ENH-027): a bar left out keeps its own colour, faded, instead of grey.
    const own = paint?.byBar && resolve ? paint.point(0, i) : undefined;
    return focus.has(labels[i]!) ? { value: r[mi], label: direct }
      : { value: r[mi], itemStyle: own ? { color: resolve!(own), opacity: 0.4 } : { color: highlight!.muted } };
  };
  // Several lines are named at their ends instead of in a legend; labels that would collide move apart (US-188).
  const ends = spec.type === "trend" && measureIdx.length > 1;
  const series = measureIdx.map((mi, si) => ({
    name: columns[mi]?.label,
    type: spec.type === "trend" ? "line" : "bar",
    ...(paint && resolve ? { itemStyle: { color: resolve(paint.series(si)) }, ...(spec.type === "trend" ? { lineStyle: { color: resolve(paint.series(si)) } } : {}) } : {}),
    // Ordered buckets: one ramp step per bucket, its value on top, so the colour is never the only signal.
    data: ordinal
      ? rows.map((r, i) => ({ value: r[mi], itemStyle: { color: ordinalColour(i, rows.length, colours.ramp), borderColor: colours.edge, borderWidth: 1 } }))
      : rows.map((r, i) => paintPoint(marked(r, mi, i), si, i)),
    label: ordinal
      ? { show: true, position: "top", formatter: (p: { value: number }) => fmt(mi)(p.value) }
      : companionText(mi, rows[0])
        ? { show: true, position: horizontal ? "right" : "top", formatter: (p: { dataIndex: number }) => companionText(mi, rows[p.dataIndex]) }
        : undefined,
    ...(ends ? { endLabel: { show: true, formatter: "{a}", width: 110, overflow: "truncate" }, labelLayout: { moveOverlap: "shiftY" } } : {}),
  }));
  return {
    tooltip,
    legend: series.length > 1 && !ends ? {} : undefined,
    grid: { left: horizontal ? 130 : 60, right: ends ? 120 : 20, top: 30, bottom: 40 },
    xAxis: horizontal ? val : cat,
    yAxis: horizontal ? { ...cat, inverse: true } : val,
    series,
  };
}
