import { describe, expect, it } from "vitest";

import type { Column } from "../api/app";
import type { ChartSpec } from "../api/querySpec";
import { toOption } from "../components/Chart";
import { chartPaint, effectiveScheme, SCHEMES } from "../components/chartColours";
import { standardChart } from "../components/chartData";
import { applyUserLayout } from "../design/layout";
import tokens from "../design/tokens.json";

// ENH-027 (US-249): the Vivid chart scheme, and the Aurora layout drawing charts in it by default.

const col = (name: string, label: string, role: "dimension" | "measure" = "dimension") => ({ name, label, role }) as unknown as Column;
const COLUMNS = [col("region", "Region"), col("n", "Projects", "measure")];
const ROWS = [["KSA", 40], ["UAE", 35], ["Qatar", 12]];
const BARS: ChartSpec = { type: "columns", x: "region", series: ["n"] };

describe("Vivid chart colours (US-249)", () => {
  it("has eight vivid tokens for light and dark, and a Vivid scheme", () => {
    const names = tokens.color.tokens.map((t) => t.name);
    for (let i = 1; i <= 8; i++) expect(names).toContain(`vivid-${i}`);
    expect(SCHEMES.find((s) => s.key === "vivid")?.tokens).toHaveLength(8);
  });

  it("gives every bar of a single series its own colour; a named bar keeps its swatch", () => {
    const p = chartPaint({ scheme: "vivid", overrides: { UAE: "status-danger" } }, ["Projects"], ["KSA", "UAE", "Qatar"], "classic")!;
    expect([p.point(0, 0), p.point(0, 1), p.point(0, 2)]).toEqual(["vivid-1", "status-danger", "vivid-3"]);
    const two = chartPaint({ scheme: "vivid" }, ["Projects", "Budget"], ["KSA", "UAE"], "classic")!;
    expect([two.series(0), two.series(1), two.point(0, 1)]).toEqual(["vivid-1", "vivid-2", undefined]);
  });

  it("draws the brand's colours in Vivid in the Aurora layout, but a widget's own scheme wins", () => {
    expect(chartPaint(null, ["Projects"], ["KSA"], "classic")).toBeNull();
    expect(effectiveScheme(null, "aurora")).toBe("vivid");
    expect(effectiveScheme({ scheme: "status" }, "aurora")).toBe("status");
    expect(chartPaint({ scheme: "brand", overrides: {} }, ["Projects"], ["KSA", "UAE"], "aurora")!.point(0, 1)).toBe("vivid-2");
    expect(chartPaint({ scheme: "status" }, ["Projects"], ["KSA", "UAE"], "aurora")!.point(0, 1)).toBeUndefined();
  });

  it("colours the bars the same in Recharts / Plot data and in ECharts", () => {
    const c = standardChart(BARS, COLUMNS, ROWS, undefined, "aurora");
    expect([0, 1, 2].map((i) => c.point(0, i))).toEqual(["var(--tl-vivid-1)", "var(--tl-vivid-2)", "var(--tl-vivid-3)"]);
    applyUserLayout("aurora");
    try {
      const option = toOption(BARS, COLUMNS, ROWS, undefined, undefined, (t) => `#${t}`) as { series: { data: { itemStyle?: { color: string } }[] }[] };
      expect(option.series[0]!.data.map((d) => d.itemStyle?.color)).toEqual(["#vivid-1", "#vivid-2", "#vivid-3"]);
    } finally {
      applyUserLayout("default");
    }
  });

  it("fades the other bars of a highlight in their own colours instead of grey", () => {
    const c = standardChart(BARS, COLUMNS, ROWS, ["UAE"], "aurora");
    expect(c.muted(0, 0)).toBe("color-mix(in srgb, var(--tl-vivid-1) 35%, var(--tl-surface-raised))");
    expect(standardChart(BARS, COLUMNS, ROWS, ["UAE"], "classic").muted(0, 0)).toBe("var(--tl-chart-other)");
    applyUserLayout("aurora");
    try {
      const option = toOption(BARS, COLUMNS, ROWS, undefined, { labels: ["UAE"], muted: "#grey" }, (t) => `#${t}`) as
        { series: { data: { itemStyle?: { color: string; opacity?: number } }[] }[] };
      expect(option.series[0]!.data.map((d) => d.itemStyle)).toMatchObject([
        { color: "#vivid-1", opacity: 0.4 }, { color: "#vivid-2" }, { color: "#vivid-3", opacity: 0.4 }]);
      expect((option.series[0]!.data[1]!.itemStyle as { opacity?: number }).opacity).toBeUndefined();
    } finally {
      applyUserLayout("default");
    }
  });
});
