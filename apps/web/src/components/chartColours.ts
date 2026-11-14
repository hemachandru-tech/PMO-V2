import type { ChartColours } from "../api/querySpec";
import { resolvedLayout } from "../design/layout";

/**
 * A widget's chart colours (ENH-023, US-224): a scheme, and a swatch for any series or bar named by its label. Every
 * colour is a palette token, so light, dark and a tenant's palette (US-151) carry over. ECharts, Recharts and
 * Observable Plot all read the same answer from `chartPaint`, so the three libraries draw the same colours.
 */
export type Scheme = NonNullable<ChartColours["scheme"]>;
export type SwatchName = NonNullable<ChartColours["overrides"]>[string];

export const SCHEMES: { key: Scheme; label: string; tokens: string[] }[] = [
  { key: "brand", label: "Brand", tokens: ["chart-1", "chart-2", "chart-3", "chart-4", "chart-5", "chart-6", "chart-7", "chart-8"] },
  { key: "single", label: "Single colour", tokens: ["seq-4", "seq-3", "seq-5", "seq-2"] },
  { key: "status", label: "Status", tokens: ["status-ok", "status-warn", "status-danger", "chart-other"] },
  { key: "warm", label: "Warm", tokens: ["chart-2", "chart-4", "chart-6", "chart-3"] },
  { key: "cool", label: "Cool", tokens: ["chart-1", "chart-8", "chart-7", "chart-5"] },
  { key: "grey", label: "Grey", tokens: ["ink-muted", "line-strong", "ink-disabled"] },
  // ENH-027 (US-249): bright and distinct; a single series gives every bar its own colour.
  { key: "vivid", label: "Vivid", tokens: ["vivid-1", "vivid-2", "vivid-3", "vivid-4", "vivid-5", "vivid-6", "vivid-7", "vivid-8"] },
];

/**
 * The scheme a chart draws in: the widget's own, else the brand's — which the Aurora layout (ENH-027) draws in Vivid.
 */
export function effectiveScheme(colours: unknown, layout = resolvedLayout()): Scheme {
  const own = (colours as ChartColours | null | undefined)?.scheme ?? "brand";
  return own === "brand" && layout === "aurora" ? "vivid" : own;
}

export const SWATCHES: { key: SwatchName; label: string }[] = [
  ...([1, 2, 3, 4, 5, 6, 7, 8] as const).map((i) => ({ key: `chart-${i}` as SwatchName, label: `Colour ${i}` })),
  { key: "accent", label: "Brand" },
  { key: "chart-other", label: "Grey" },
  { key: "status-ok", label: "Good" },
  { key: "status-warn", label: "Warning" },
  { key: "status-danger", label: "Critical" },
];

export const cssColour = (token: string) => `var(--tl-${token})`;

export interface ChartPaint {
  /** Token of series `si` (its legend colour). */
  series: (si: number) => string;
  /** Token of row `ri` of series `si` when it differs from its series (a bar named in the overrides). */
  point: (si: number, ri: number) => string | undefined;
  /** Token of slice `ri` of a donut. */
  slice: (ri: number) => string;
  /** Each bar has its own colour (Vivid, one series): a highlight fades the other bars instead of greying them. */
  byBar?: boolean;
}

/**
 * The colours to draw, or null when the widget keeps the brand's (the drawers then do exactly what they did before).
 * A bar or slice named in the overrides wins over its series; on a chart of several series only series are named.
 * Vivid (also the Aurora layout's default, ENH-027) colours each bar of a single-series chart in turn.
 */
export function chartPaint(colours: unknown, seriesLabels: string[], categoryLabels: string[], layout = resolvedLayout()): ChartPaint | null {
  const c = colours as ChartColours | null | undefined;
  const key = effectiveScheme(c, layout);
  const o = c?.overrides ?? {};
  if (key === "brand" && Object.keys(o).length === 0) return null;
  const tokens = (SCHEMES.find((s) => s.key === key) ?? SCHEMES[0]!).tokens;
  const single = seriesLabels.length <= 1;
  const byBar = single && key === "vivid";
  return {
    series: (si) => o[seriesLabels[si] ?? ""] ?? tokens[si % tokens.length]!,
    point: (_si, ri) => (single ? o[categoryLabels[ri] ?? ""] ?? (byBar ? tokens[ri % tokens.length] : undefined) : undefined),
    slice: (ri) => o[categoryLabels[ri] ?? ""] ?? tokens[ri % tokens.length]!,
    byBar,
  };
}
