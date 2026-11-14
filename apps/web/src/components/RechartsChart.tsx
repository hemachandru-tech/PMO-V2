import { useMemo } from "react";
import { Bar, BarChart, CartesianGrid, Cell, LabelList, Line, LineChart, Pie, PieChart, Tooltip, XAxis, YAxis, type PieLabelRenderProps } from "recharts";

import type { ChartProps } from "./Chart";
import { EDGE, rampAt, standardChart, standardMark } from "./chartData";
import { formatCompact } from "./format";
import { short, useWidth } from "./svgChart";
import { ChartContainer, ChartLegendContent, ChartTooltipContent, type ChartConfig } from "./ui/chart";
import type { ChartSpec } from "../api/querySpec";
import { useLayout } from "../design/layout";

const TICK = { fill: "var(--tl-ink-muted)", fontSize: 12, fontFamily: "var(--font-sans)" };
const LABEL = { fill: "var(--tl-ink-muted)", fontSize: 12 };

interface TooltipArgs {
  active?: boolean;
  label?: unknown;
  payload?: readonly { dataKey?: unknown; name?: unknown; value?: unknown; color?: string; payload?: { fill?: string } }[];
}

/**
 * The standard charts drawn with shadcn/ui Charts on Recharts (ENH-018, US-207): the same data, labels, highlight,
 * ordinal ramp and clicks as ECharts (`chartData.ts`), in the app's own style. SVG throughout, so print stays vector.
 */
export function RechartsChart({ spec, columns, rows, onMark, title, still, highlight }: ChartProps & { spec: ChartSpec }) {
  const layout = useLayout(); // Aurora draws in Vivid (ENH-027): redraw when the layout changes.
  const c = useMemo(() => standardChart(spec, columns, rows, highlight, layout), [spec, columns, rows, highlight, layout]);
  const [ref, width] = useWidth();
  const height = still ? 240 : 280;
  const anim = !still;
  const config: ChartConfig = Object.fromEntries(c.series.map((s) => [s.key, { label: s.label, color: s.colour }]));
  const click = (row: number, seriesLabel?: string) => {
    if (!onMark || still) return;
    const mark = standardMark(spec, columns, rows, row, seriesLabel);
    if (mark) onMark(mark);
  };
  // Labels drawn on the marks, worked out once per series: ordered buckets show their value, a highlighted item its
  // value (US-188 direct labels), a companion figure the series it is worked out from (US-176).
  const data = c.data.map((d, i) => ({
    ...d,
    ...Object.fromEntries(c.series.map((s, si) => [`l${si}`,
      c.ordinal || c.focus?.has(c.labels[i]!) ? s.format(d[s.key]) : c.companion(si, i) || undefined])),
    ...(c.ends ? Object.fromEntries(c.series.map((s, si) => [`e${si}`, i === c.data.length - 1 ? short(s.label, 18) : undefined])) : {}),
  }));
  const tooltip = (a: TooltipArgs) => {
    if (!a.active || !a.payload?.length) return null;
    const items = a.payload.map((p) => {
      const s = c.series.find((x) => x.key === p.dataKey);
      return { key: String(p.dataKey ?? p.name), label: c.kind === "donut" ? (s?.label ?? "") : String(p.name ?? ""), value: s ? s.format(p.value) : String(p.value ?? ""), color: p.payload?.fill ?? p.color ?? "" };
    });
    return <ChartTooltipContent title={c.kind === "donut" ? String(a.payload[0]?.name ?? "") : String(a.label ?? "")} items={items} />;
  };
  const tip = still ? null : <Tooltip cursor={c.kind === "line" ? { stroke: "var(--tl-chart-axis)" } : { fill: "var(--tl-surface-sunken)" }} content={tooltip} isAnimationActive={false} />;
  const valueTick = (v: number) => formatCompact(v, columns[c.series[0]?.column ?? -1]?.format);

  let chart;
  if (c.kind === "donut") {
    const s = c.series[0];
    const pieLabel = (p: PieLabelRenderProps) => {
      const i = p.index ?? 0;
      const text = c.companion(0, i) ? `${c.labels[i]}: ${c.companion(0, i)}` : c.labels[i];
      return <text x={p.x} y={p.y} textAnchor={p.textAnchor} dominantBaseline="central" {...LABEL}>{short(text ?? "", 28)}</text>;
    };
    chart = (
      <PieChart width={width} height={height}>
        {tip}
        {s && (
          <Pie data={data} dataKey={s.key} nameKey="label" innerRadius="40%" outerRadius="62%" stroke="var(--tl-surface-raised)" strokeWidth={2}
            isAnimationActive={anim} animationDuration={500} label={pieLabel} labelLine={{ stroke: "var(--tl-chart-axis)" }} onClick={(_, i) => click(i)} cursor={onMark ? "pointer" : undefined}>
            {data.map((d, i) => <Cell key={String(d.label)} fill={c.slice(i)} />)}
          </Pie>
        )}
      </PieChart>
    );
  } else if (c.kind === "line") {
    chart = (
      <LineChart width={width} height={height} data={data} margin={{ top: 20, right: c.ends ? 120 : 24, left: 8, bottom: 8 }}
        onClick={(state: { activeTooltipIndex?: unknown } | null) => {
          const i = Number(state?.activeTooltipIndex);
          if (Number.isInteger(i)) click(i);
        }}>
        <CartesianGrid vertical={false} stroke="var(--tl-chart-grid)" />
        <XAxis dataKey="label" tick={TICK} tickLine={false} axisLine={{ stroke: "var(--tl-chart-axis)" }} tickFormatter={(v: string) => short(v, 18)} interval="preserveStartEnd" />
        <YAxis tick={TICK} tickLine={false} axisLine={false} tickFormatter={valueTick} allowDecimals={!c.integer} width={64} />
        {tip}
        {c.series.map((s, si) => (
          <Line key={s.key} dataKey={s.key} name={s.label} type="linear" stroke={s.colour} strokeWidth={2} isAnimationActive={anim} animationDuration={500} connectNulls
            dot={(p: { cx?: number; cy?: number; index?: number }) => {
              const big = c.focus?.has(c.labels[p.index ?? -1] ?? "");
              return <circle key={p.index} cx={p.cx} cy={p.cy} r={big ? 5 : 3} fill={s.colour} stroke="var(--tl-surface-raised)" strokeWidth={1} />;
            }}
            activeDot={{ r: 5 }}>
            <LabelList dataKey={`l${si}`} position="top" {...LABEL} />
            {c.ends && <LabelList dataKey={`e${si}`} position="right" fill={s.colour} fontSize={12} />}
          </Line>
        ))}
      </LineChart>
    );
  } else {
    const horizontal = c.kind === "hbar";
    const category = { dataKey: "label", type: "category" as const, tick: TICK, tickLine: false, tickFormatter: (v: string) => short(v, 18) };
    const value = { type: "number" as const, tick: TICK, tickLine: false, axisLine: false, tickFormatter: valueTick, allowDecimals: !c.integer };
    chart = (
      <BarChart width={width} height={height} data={data} layout={horizontal ? "vertical" : "horizontal"} barCategoryGap="20%"
        margin={{ top: 20, right: horizontal ? 56 : 16, left: 8, bottom: 8 }}>
        <CartesianGrid horizontal={!horizontal} vertical={horizontal} stroke="var(--tl-chart-grid)" />
        {horizontal ? <XAxis {...value} /> : <XAxis {...category} axisLine={{ stroke: "var(--tl-chart-axis)" }} interval="preserveStartEnd" />}
        {horizontal ? <YAxis {...category} width={130} axisLine={{ stroke: "var(--tl-chart-axis)" }} interval={0} /> : <YAxis {...value} width={64} />}
        {tip}
        {c.series.map((s, si) => (
          <Bar key={s.key} dataKey={s.key} name={s.label} fill={s.colour} radius={horizontal ? [0, 4, 4, 0] : [4, 4, 0, 0]} isAnimationActive={anim} animationDuration={500}
            onClick={(_, i) => click(i, s.label)} cursor={onMark ? "pointer" : undefined}>
            {(c.ordinal || c.focus || data.some((_, i) => c.point(si, i))) && data.map((d, i) => (
              <Cell key={String(d.label)} fill={c.ordinal ? rampAt(i, data.length) : c.focus && !c.focus.has(c.labels[i]!) ? c.muted(si, i) : c.point(si, i) ?? s.colour}
                stroke={c.ordinal ? EDGE : undefined} strokeWidth={c.ordinal ? 1 : 0} />
            ))}
            <LabelList dataKey={`l${si}`} position={horizontal ? "right" : "top"} {...LABEL} />
          </Bar>
        ))}
      </BarChart>
    );
  }
  return (
    <ChartContainer ref={ref} config={config} data-chart={spec.type} data-library="recharts" role="img" aria-label={title ?? "Chart"}>
      {chart}
      {c.series.length > 1 && !c.ends && <ChartLegendContent config={config} />}
    </ChartContainer>
  );
}
