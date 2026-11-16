import { paletteColor } from "./palette";
import tokens from "./tokens.json";
import type { ResolvedTheme } from "./theme";

type Themed = string | { light: string; dark: string };

const byName = new Map<string, Themed>(tokens.color.tokens.map((t) => [t.name, t.value as Themed]));

/** A colour token for one theme, read from tokens.json (the same source as tokens.css). */
export function tokenColor(name: string, theme: ResolvedTheme): string {
  const value = byName.get(name);
  if (value === undefined) throw new Error(`Unknown colour token: ${name}`);
  const v = typeof value === "string" ? value : value[theme];
  const alias = /^\{(.+)\}$/.exec(v);
  return alias ? tokenColor(alias[1]!, theme) : v;
}

export const CHART_SERIES = ["chart-1", "chart-2", "chart-3", "chart-4", "chart-5", "chart-6", "chart-7", "chart-8"];
/** The Vivid series (ENH-027): the Aurora layout's default. */
export const VIVID_SERIES = ["vivid-1", "vivid-2", "vivid-3", "vivid-4", "vivid-5", "vivid-6", "vivid-7", "vivid-8"];

/**
 * ECharts theme built from the design tokens (US-134, ADR-007): the validated categorical palette in its fixed
 * order, the system fonts, hairline grid, muted axis labels and a tooltip that looks like a Tela popover. The tenant's palette
 * (US-151), when one is applied, replaces the colours it sets.
 */
export function echartsTheme(theme: ResolvedTheme, series: string[] = CHART_SERIES): Record<string, unknown> {
  const c = (name: string) => paletteColor(name, theme) ?? tokenColor(name, theme);
  const sans = tokens.type.families.sans;
  const axis = {
    axisLine: { show: true, lineStyle: { color: c("chart-axis") } },
    axisTick: { show: false },
    axisLabel: { color: c("ink-muted"), fontFamily: sans, fontSize: 12 },
    splitLine: { show: true, lineStyle: { color: c("chart-grid") } },
    splitArea: { show: false },
    nameTextStyle: { color: c("ink-muted"), fontFamily: sans, fontSize: 12 },
  };
  return {
    color: series.map(c),
    backgroundColor: "transparent",
    textStyle: { fontFamily: sans, color: c("ink") },
    title: { textStyle: { color: c("ink"), fontFamily: sans, fontSize: 15, fontWeight: 600 } },
    legend: { textStyle: { color: c("ink-muted"), fontFamily: sans, fontSize: 12 }, icon: "roundRect", itemWidth: 10, itemHeight: 10 },
    categoryAxis: { ...axis, splitLine: { show: false } },
    valueAxis: { ...axis, axisLine: { show: false } },
    timeAxis: { ...axis, splitLine: { show: false } },
    logAxis: axis,
    tooltip: {
      backgroundColor: c("surface-raised"),
      borderColor: c("line"),
      borderWidth: 1,
      padding: [8, 12],
      textStyle: { color: c("ink"), fontFamily: sans, fontSize: 12 },
      extraCssText: `border-radius: 6px; box-shadow: ${tokens.shadow.tokens.find((t) => t.name === "shadow-overlay")!.value[theme]};`,
      axisPointer: { lineStyle: { color: c("chart-axis") }, crossStyle: { color: c("chart-axis") } },
    },
    line: { symbol: "circle", symbolSize: 6, lineStyle: { width: 2.5 }, smooth: 0.35 },
    bar: {
      barMaxWidth: 32,
      itemStyle: {
        borderRadius: [8, 8, 0, 0],
      },
    },
    pie: {
      itemStyle: { borderColor: c("surface-raised"), borderWidth: 2, borderRadius: 8 },
      label: { color: c("ink-muted"), fontFamily: sans, fontSize: 12, textBorderWidth: 0 },
      labelLine: { lineStyle: { color: c("chart-axis") } },
    },
  };
}
