import { CustomChart, FunnelChart, HeatmapChart, SankeyChart, TreemapChart } from "echarts/charts";
import { CalendarComponent, MarkLineComponent, TitleComponent, VisualMapPiecewiseComponent } from "echarts/components";
import * as echarts from "echarts/core";
import { useCallback } from "react";

import type { Column } from "../api/app";
import type { ChartSpec } from "../api/querySpec";
import { tokenColor } from "../design/echartsTheme";
import { paletteColor } from "../design/palette";
import type { ResolvedTheme } from "../design/theme";
import { Beeswarm } from "./Beeswarm";
import { Bullet, type BulletFigures } from "./Bullet";
import { Bump, type BumpFigures } from "./Bump";
import { type CatalogueColours, catalogueHeight, catalogueOption } from "./catalogue";
import { EChart } from "./Chart";
import { Dumbbell } from "./Dumbbell";
import { Ridgeline } from "./Ridgeline";
import { Survival, type SurvivalFigures } from "./Survival";
import { UpSet, type UpSetFigures } from "./UpSet";
import { Waffle, type WaffleFigures } from "./Waffle";

echarts.use([CustomChart, FunnelChart, HeatmapChart, SankeyChart, TreemapChart, CalendarComponent, MarkLineComponent, TitleComponent, VisualMapPiecewiseComponent]);

/** Colours of the catalogue visuals (US-187) from the tokens and the tenant's palette. */
export function catalogueColours(theme: ResolvedTheme): CatalogueColours {
  const c = (name: string) => paletteColor(name, theme) ?? tokenColor(name, theme);
  return { up: c("div-pos-2"), down: c("div-neg-2"), total: c("chart-1"), muted: c("chart-other"), limit: c("ink-muted"),
    danger: c("status-danger"), series: c("chart-1"), line: c("chart-2"), ink: c("ink"),
    seq: ["seq-1", "seq-2", "seq-3", "seq-4", "seq-5"].map(c), palette: [1, 2, 3, 4, 5, 6, 7, 8].map((i) => c(`chart-${i}`)) };
}

interface Props {
  spec: ChartSpec;
  columns: Column[];
  rows: unknown[][];
  onMark?: (mark: Record<string, unknown>) => void;
  title?: string;
  still?: boolean;
  /** Highlight-and-grey (US-186; across the catalogue in US-188): these item labels keep their colour. */
  highlight?: string[];
}

/**
 * The visual catalogue, loaded on demand. Wave 1 (US-187): the bullet (HTML), the dumbbell (D3) and the ECharts
 * catalogue (waterfall, Pareto, XmR, Gantt, funnel, small multiples). Wave 2 (US-188): beeswarm, ridgeline, UpSet,
 * time to event and bump (D3), the waffle (HTML) and calendar, sankey and treemap (ECharts). A click names its item
 * by label, since the catalogue re-orders rows (Pareto, dumbbell) and adds totals (waterfall).
 */
export function CatalogueChart({ spec, columns, rows, onMark, title, still, highlight }: Props) {
  const build = useCallback((theme: ResolvedTheme) => catalogueOption(spec, columns, rows, catalogueColours(theme), highlight), [spec, columns, rows, highlight]);
  const markOf = useCallback((p: { name: string; seriesName?: string }) => {
    if (spec.type === "calendar" || spec.type === "sankey") return null; // a day or a flow is not one breakdown value
    const key = spec.type === "small_multiples" ? spec.y : spec.x;
    const ki = columns.findIndex((c) => c.name === key);
    const name = spec.type === "small_multiples" ? p.seriesName : p.name;
    const row = ki >= 0 ? rows.find((r) => (r[ki] == null ? "Not set" : String(r[ki])) === name) : undefined;
    return row && key ? { [key]: row[ki] ?? null } : null;
  }, [spec, columns, rows]);
  if (spec.type === "bullet") {
    return spec.figures
      ? <Bullet figures={spec.figures as unknown as BulletFigures} format={columns.find((c) => c.name === spec.series?.[0])?.format} title={title} />
      : null;
  }
  const format = columns.find((c) => c.name === spec.series?.[0])?.format;
  const figures = spec.figures as unknown;
  if (spec.type === "dumbbell") return <Dumbbell spec={spec} columns={columns} rows={rows} title={title} highlight={highlight} />;
  if (spec.type === "beeswarm") return <Beeswarm spec={spec} columns={columns} rows={rows} title={title} highlight={highlight} />;
  if (spec.type === "ridgeline") return <Ridgeline spec={spec} columns={columns} rows={rows} title={title} highlight={highlight} />;
  if (!figures && ["upset", "survival", "bump", "waffle"].includes(spec.type)) return null;
  if (spec.type === "upset") return <UpSet figures={figures as UpSetFigures} format={format} title={title} highlight={highlight} />;
  if (spec.type === "survival") return <Survival figures={figures as SurvivalFigures} title={title} highlight={highlight} />;
  if (spec.type === "bump") return <Bump figures={figures as BumpFigures} format={format} title={title} highlight={highlight} />;
  if (spec.type === "waffle") return <Waffle figures={figures as WaffleFigures} format={format} title={title} highlight={highlight} />;
  return <EChart spec={spec} columns={columns} rows={rows} onMark={onMark} title={title} still={still} build={build} markOf={markOf} height={catalogueHeight(spec, rows)} />;
}
