import * as Plot from "@observablehq/plot";
import { arc, pie } from "d3-shape";
import { useEffect, useMemo, useRef } from "react";

import type { ChartSpec } from "../api/querySpec";
import type { ChartProps } from "./Chart";
import { EDGE, rampAt, standardChart, standardMark, type StandardChart } from "./chartData";
import { formatCompact } from "./format";
import { Legend, short, useWidth } from "./svgChart";
import { useLayout } from "../design/layout";

/** One bar or point: row `i` of series `si`, with its colour and the label drawn on it. */
interface Datum {
  i: number;
  si: number;
  label: string;
  series: string;
  value: number | null;
  fill: string;
  /** "none" without an edge: Plot drops data whose colour is null. */
  edge: string;
  big: boolean;
  text: string;
  tip: string;
}

function long(c: StandardChart): Datum[] {
  return c.series.flatMap((s, si) => c.data.map((d, i) => {
    const focused = c.focus?.has(c.labels[i]!) ?? false;
    const raw = d[s.key];
    return {
      i, si, label: c.labels[i]!, series: s.label, value: typeof raw === "number" ? raw : raw == null ? null : Number(raw),
      fill: c.ordinal ? rampAt(i, c.data.length) : c.focus && c.kind !== "line" && !focused ? c.muted(si, i)
        : c.kind === "line" ? s.colour : c.point(si, i) ?? s.colour,
      edge: c.ordinal ? EDGE : "none",
      big: focused,
      text: c.ordinal || focused ? s.format(raw) : c.companion(si, i),
      tip: `${c.labels[i]}\n${s.label}: ${s.format(raw)}`,
    };
  }));
}

/**
 * The Plot options for a standard chart: bars (grouped by facet when there are several series), horizontal bars, or
 * lines with their points. Colours are CSS variables used as they are (`identity` scales), so the tenant's palette
 * and the theme apply without a redraw.
 */
export function plotOptions(c: StandardChart, data: Datum[], width: number, height: number, still: boolean, format: string | null | undefined): Plot.PlotOptions {
  const many = c.series.length > 1;
  const tick = (v: number) => (c.integer && !Number.isInteger(v) ? "" : formatCompact(v, format));
  // Rotate category labels when the longest (cut at 18 characters) would not fit its slot side by side.
  const longest = Math.min(18, Math.max(1, ...c.labels.map((l) => l.length)));
  const crowded = (width - 100) / Math.max(1, c.labels.length) < longest * 6 + 12;
  const common = { width, height, className: "tl-plot", color: { type: "identity" as const }, r: { type: "identity" as const }, marginTop: 24 };
  const labelled = data.filter((d) => d.text);
  if (c.kind === "hbar") {
    const facet = many ? { fy: "label" } : {};
    return {
      ...common, marginLeft: 130, marginRight: 56,
      x: { grid: true, label: null, tickFormat: tick, nice: true },
      y: many ? { axis: null, domain: c.series.map((s) => s.label), padding: 0.1 } : { domain: c.labels, label: null, tickFormat: (v: string) => short(v, 18), padding: 0.2 },
      ...(many ? { fy: { domain: c.labels, label: null, tickFormat: (v: string) => short(v, 18), padding: 0.2 } } : {}),
      marks: [
        Plot.barX(data, { x: "value", y: many ? "series" : "label", ...facet, fill: "fill", stroke: "edge", rx: 3 }),
        Plot.ruleX([0], { stroke: "var(--tl-chart-axis)" }),
        Plot.text(labelled, { x: "value", y: many ? "series" : "label", ...facet, text: "text", dx: 6, textAnchor: "start", fill: "var(--tl-ink-muted)" }),
        ...(still ? [] : [Plot.tip(data, Plot.pointerY({ x: "value", y: many ? "series" : "label", ...facet, title: "tip" }))]),
      ],
    };
  }
  if (c.kind === "line") {
    return {
      ...common, marginRight: c.ends ? 120 : 24, marginLeft: 64, marginBottom: crowded ? 64 : 32,
      x: { type: "point", domain: c.labels, label: null, tickFormat: (v: string) => short(v, 18), tickRotate: crowded ? -35 : 0, padding: 0.3 },
      y: { grid: true, label: null, tickFormat: tick, nice: true, zero: true },
      marks: [
        Plot.lineY(data, { x: "label", y: "value", z: "series", stroke: "fill", strokeWidth: 2 }),
        Plot.dot(data, { x: "label", y: "value", fill: "fill", r: (d: Datum) => (d.big ? 5 : 3), stroke: "var(--tl-surface-raised)" }),
        Plot.text(labelled, { x: "label", y: "value", text: "text", dy: -10, fill: "var(--tl-ink-muted)" }),
        ...(c.ends ? [Plot.text(data, Plot.selectLast({ x: "label", y: "value", z: "series", text: (d: Datum) => short(d.series, 18), dx: 8, textAnchor: "start", fill: "fill" }))] : []),
        ...(still ? [] : [Plot.tip(data, Plot.pointerX({ x: "label", y: "value", title: "tip" }))]),
      ],
    };
  }
  const facet = many ? { fx: "label" } : {};
  return {
    ...common, marginLeft: 64, marginBottom: crowded ? 64 : 32,
    x: many ? { axis: null, domain: c.series.map((s) => s.label), padding: 0.1 } : { domain: c.labels, label: null, tickFormat: (v: string) => short(v, 18), tickRotate: crowded ? -35 : 0, padding: 0.2 },
    ...(many ? { fx: { domain: c.labels, label: null, axis: "bottom" as const, tickFormat: (v: string) => short(v, 18), tickRotate: crowded ? -35 : 0, padding: 0.2 } } : {}),
    y: { grid: true, label: null, tickFormat: tick, nice: true },
    marks: [
      Plot.barY(data, { x: many ? "series" : "label", y: "value", ...facet, fill: "fill", stroke: "edge", rx: 3 }),
      Plot.ruleY([0], { stroke: "var(--tl-chart-axis)" }),
      Plot.text(labelled, { x: many ? "series" : "label", y: "value", ...facet, text: "text", dy: -8, fill: "var(--tl-ink-muted)" }),
      ...(still ? [] : [Plot.tip(data, Plot.pointerX({ x: many ? "series" : "label", y: "value", ...facet, title: "tip" }))]),
    ],
  };
}

/**
 * The standard charts drawn with Observable Plot (ENH-018, US-208), the library behind the observablehq.com D3
 * gallery. Plot has no pie, so the donut is drawn with D3's arcs like the other D3 visuals. Plot puts a <style> tag in
 * each chart, which the strict CSP blocks: it is taken out before the chart is shown, and `.tl-plot` in index.css
 * carries its rules.
 */
export function PlotChart({ spec, columns, rows, onMark, title, still, highlight }: ChartProps & { spec: ChartSpec }) {
  const layout = useLayout(); // Aurora draws in Vivid (ENH-027): redraw when the layout changes.
  const c = useMemo(() => standardChart(spec, columns, rows, highlight, layout), [spec, columns, rows, highlight, layout]);
  const data = useMemo(() => long(c), [c]);
  const [ref, width] = useWidth();
  const host = useRef<HTMLDivElement>(null);
  const height = still ? 240 : 280;
  const click = (d: Datum | undefined) => {
    if (!d || !onMark || still) return;
    const mark = standardMark(spec, columns, rows, d.i, d.series);
    if (mark) onMark(mark);
  };
  const clickRef = useRef(click);
  clickRef.current = click;

  useEffect(() => {
    if (c.kind === "donut" || !host.current) return;
    const node = Plot.plot(plotOptions(c, data, width, height, !!still, columns[c.series[0]?.column ?? -1]?.format)) as (SVGSVGElement | HTMLElement) & { value?: Datum | null };
    node.querySelectorAll("style").forEach((s) => s.remove());
    // A bar or point names its datum (Plot keeps the index on the element); elsewhere, the one the tip points at.
    node.addEventListener("click", (e) => {
      const el = e.target as Element & { __data__?: unknown };
      const own = (el.tagName === "rect" || el.tagName === "circle") && typeof el.__data__ === "number" ? data[el.__data__] : undefined;
      clickRef.current(own ?? node.value ?? undefined);
    });
    if (onMark && !still) node.style.cursor = "pointer";
    host.current.replaceChildren(node);
    return () => node.remove();
  }, [c, data, width, height, still, columns, onMark]);

  return (
    <div ref={ref} data-chart={spec.type} data-library="plot" role="img" aria-label={title ?? "Chart"} className="w-full">
      {c.kind === "donut" ? <Donut c={c} width={width} height={height} onClick={(i) => click(data[i])} /> : <div ref={host} style={{ minHeight: height }} />}
      {c.series.length > 1 && !c.ends && c.kind !== "donut" && <div className="flex justify-center pt-2"><Legend items={c.series.map((s) => ({ label: s.label, colour: s.colour }))} /></div>}
    </div>
  );
}

function Donut({ c, width, height, onClick }: { c: StandardChart; width: number; height: number; onClick: (row: number) => void }) {
  const s = c.series[0];
  if (!s) return null;
  const r = Math.min(width, height) * 0.35;
  const values = c.data.map((d) => Math.max(0, Number(d[s.key] ?? 0)));
  const slices = pie<number>().sort(null).value((v) => v)(values);
  const shape = arc<(typeof slices)[number]>().innerRadius(r * 0.64).outerRadius(r).padAngle(0.01);
  const at = arc<(typeof slices)[number]>().innerRadius(r * 1.18).outerRadius(r * 1.18);
  return (
    <svg width={width} height={height} viewBox={`${-width / 2} ${-height / 2} ${width} ${height}`} className="tl-plot">
      {slices.map((p) => {
        const [x, y] = at.centroid(p);
        const companion = c.companion(0, p.index);
        const text = companion ? `${c.labels[p.index]}: ${companion}` : c.labels[p.index]!;
        return (
          <g key={p.index} onClick={() => onClick(p.index)}>
            <path d={shape(p) ?? undefined} fill={c.slice(p.index)} stroke="var(--tl-surface-raised)" strokeWidth={2}>
              <title>{`${c.labels[p.index]}: ${s.format(values[p.index])}`}</title>
            </path>
            {p.endAngle - p.startAngle > 0.12 && (
              <text x={x} y={y} textAnchor={x < 0 ? "end" : "start"} dominantBaseline="central" fill="var(--tl-ink-muted)">{short(text, 28)}</text>
            )}
          </g>
        );
      })}
    </svg>
  );
}
