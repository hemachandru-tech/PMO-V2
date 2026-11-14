import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { Column } from "../api/app";
import type { ChartSpec } from "../api/querySpec";
import { Beeswarm, swarm } from "../components/Beeswarm";
import { Bump, bumpSegments } from "../components/Bump";
import { type CatalogueColours, calendarLevel, catalogueHeight, catalogueOption } from "../components/catalogue";
import { Chart, toOption } from "../components/Chart";
import { ridgePath } from "../components/Ridgeline";
import { stepPath, Survival, type SurvivalFigures } from "../components/Survival";
import { spreadLabels } from "../components/svgChart";
import { combinationWords, UpSet } from "../components/UpSet";
import { Waffle, waffleCells } from "../components/Waffle";

// US-188: the visual catalogue wave 2 draws the figures the API worked out (COMP-001) and labels every mark.

vi.mock("echarts/core", () => ({
  use: vi.fn(),
  registerTheme: vi.fn(),
  init: () => ({ setOption: vi.fn(), on: vi.fn(), resize: vi.fn(), dispose: vi.fn() }),
}));

const C: CatalogueColours = { up: "up", down: "down", total: "total", muted: "muted", limit: "limit", danger: "danger", series: "series", line: "line", ink: "ink", seq: ["s1", "s2", "s3", "s4", "s5"], palette: ["p1", "p2", "p3"] };
const col = (name: string, label: string, kind: Column["kind"] = "dimension", format: string | null = null): Column =>
  ({ name, kind, label, ref: `${kind}:${name}`, data_type: kind === "measure" ? "integer" : "text", format }) as Column;
type Series = { type: string; data: Record<string, unknown>[]; links?: Record<string, unknown>[]; label?: { formatter: (p: unknown) => string } };
const series = (o: Record<string, unknown>) => o.series as Series[];

describe("calendar heatmap (US-188)", () => {
  const spec: ChartSpec = { type: "calendar", x: "day", series: ["n"], figures: { start: "2025-11-03", end: "2026-02-10", years: [2025, 2026], edges: [1, 2, 3], max: 4 } };
  const rows = [["2025-11-03", 0], ["2026-01-05", 3], ["2026-02-10", 4]];

  it("draws one calendar per year, clipped to the range, and a legend of the server's levels", () => {
    const o = catalogueOption(spec, [col("day", "Day", "period"), col("n", "Reports", "measure")], rows, C);
    const cals = o.calendar as { range: string[] }[];
    expect(cals.map((c) => c.range)).toEqual([["2025-11-03", "2025-12-31"], ["2026-01-01", "2026-02-10"]]);
    expect(series(o).map((s) => s.data)).toEqual([[["2025-11-03", 0]], [["2026-01-05", 3], ["2026-02-10", 4]]]);
    const pieces = (o.visualMap as { pieces: { label: string; color: string }[] }).pieces;
    expect(pieces.map((p) => p.label)).toEqual(["Below 1", "1 – 2", "2 – 3", "3 or more"]);
    expect(pieces.map((p) => p.color)).toEqual(["s1", "s2", "s4", "s5"]);
    expect(catalogueHeight(spec, rows)).toBe(300);
  });

  it("puts a value on the level of the edges at or below it", () => {
    expect([0, 1, 1.5, 3, 9].map((v) => calendarLevel(v, [1, 2, 3]))).toEqual([0, 1, 1, 3, 3]);
  });
});

describe("sankey (US-188)", () => {
  const nodes = [{ label: "New", level: 0, value: 20 }, { label: "DOU", level: 1, value: 14 }, { label: "Open", level: 1, value: 6 }, { label: "Open", level: 2, value: 6 }];
  const spec: ChartSpec = { type: "sankey", series: ["n"], figures: { nodes, links: [{ source: 0, target: 1, value: 14 }, { source: 0, target: 2, value: 6 }, { source: 2, target: 3, value: 6 }] } };

  it("keeps a value on two levels apart and labels every node with its value", () => {
    const [s] = series(catalogueOption(spec, [col("n", "Requests", "measure")], [], C));
    expect(s!.data.map((d) => d.name)).toEqual(["0:New", "1:DOU", "1:Open", "2:Open"]);
    expect(s!.data.map((d) => d.depth)).toEqual([0, 1, 1, 2]);
    expect(s!.label!.formatter({ dataIndex: 3 })).toBe("Open 6");
  });

  it("greys the flows away from a highlighted node", () => {
    const [s] = series(catalogueOption(spec, [col("n", "Requests", "measure")], [], C, ["DOU"]));
    expect(s!.data.map((d) => (d.itemStyle as { color: string }).color)).toEqual(["muted", "p2", "muted", "muted"]);
    expect(s!.links!.map((l) => (l.lineStyle as { color: string }).color)).toEqual(["source", "muted", "muted"]);
  });
});

describe("treemap (US-188)", () => {
  it("nests the inner level and names each tile's value and share", () => {
    const nodes = [{ label: "Infra", value: 500, share: 50, children: [{ label: "Network", value: 500, share: 50 }] }, { label: "Digital", value: 400, share: 40, children: [] }];
    const spec: ChartSpec = { type: "treemap", x: "portfolio", series: ["tcv"], figures: { nodes, total: 900 } };
    const [s] = series(catalogueOption(spec, [col("portfolio", "Portfolio"), col("tcv", "TCV", "measure", "currency")], [], C, ["Digital"]));
    expect(s!.data.map((d) => [d.name, d.value, d.words])).toEqual([["Infra", 500, "Infra\n$500 · 50%"], ["Digital", 400, "Digital\n$400 · 40%"]]);
    expect(s!.data.map((d) => (d.itemStyle as { color: string }).color)).toEqual(["muted", "p2"]);
    expect((s!.data[0]!.children as { name: string }[]).map((k) => k.name)).toEqual(["Network"]);
  });
});

describe("highlight-and-grey across the ECharts catalogue (US-188)", () => {
  it("mutes the Pareto bars and funnel stages not named", () => {
    const spec: ChartSpec = { type: "pareto", x: "region", series: ["n"], figures: { labels: ["South", "East", "North"], values: [50, 30, 20], cumulative_pct: [50, 80, 100], vital_few: 3 } };
    const [bars] = series(catalogueOption(spec, [col("region", "Region"), col("n", "Open", "measure")], [], C, ["East"]));
    expect(bars!.data).toEqual([{ value: 50, itemStyle: { color: "muted" } }, 30, { value: 20, itemStyle: { color: "muted" } }]);
    const stages = [{ label: "Requests", value: 10, pct_of_first: 100, drop_pct: null }, { label: "DOU", value: 5, pct_of_first: 50, drop_pct: 50 }];
    const [f] = series(catalogueOption({ type: "funnel", series: ["n"], figures: { stages } }, [col("n", "N", "measure")], [], C, ["DOU"]));
    expect(f!.data.map((d) => d.itemStyle ?? null)).toEqual([{ color: "muted" }, null]);
  });
});

describe("direct labels (US-188)", () => {
  const cols = [col("period", "Month", "period"), col("a", "Budget", "measure"), col("b", "Forecast", "measure")];
  const rows = [["2026-01", 1, 2], ["2026-02", 2, 3], ["2026-03", 3, 4]];

  it("names several lines at their ends, moved apart, instead of a legend", () => {
    const o = toOption({ type: "trend", x: "period", series: ["a", "b"] }, cols, rows) as { legend?: unknown; series: { endLabel?: { show: boolean }; labelLayout?: { moveOverlap: string } }[] };
    expect(o.legend).toBeUndefined();
    expect(o.series.map((s) => [s.endLabel?.show, s.labelLayout?.moveOverlap])).toEqual([[true, "shiftY"], [true, "shiftY"]]);
  });

  it("writes the value on a highlighted bar only", () => {
    const o = toOption({ type: "ranked_bar", x: "period", series: ["a"] }, cols, rows, undefined, { labels: ["2026-02"], muted: "grey" }) as { series: { data: unknown[] }[] };
    const [a, b, c] = o.series[0]!.data as { value: number; label?: { show: boolean }; itemStyle?: { color: string } }[];
    expect(b!.label?.show).toBe(true);
    expect([a!.label, a!.itemStyle?.color, c!.itemStyle?.color]).toEqual([undefined, "grey", "grey"]);
  });

  it("spreads end labels that would collide, keeping their order", () => {
    expect(spreadLabels([50, 52, 100, 51], 12)).toEqual([50, 74, 100, 62]);
  });
});

const PROJECTS = Array.from({ length: 12 }, (_, i) => [`P${i}`, i % 2 ? "Infra" : "Digital", 100 + i * 10]);
const PCOLS = [col("project", "Project"), col("portfolio", "Portfolio"), col("budget", "Budget", "measure", "currency")];
const GROUPS = [
  { label: "Infra", n: 6, min: 110, q1: 130, median: 160, q3: 190, max: 210, low: 110, high: 210 },
  { label: "Digital", n: 6, min: 100, q1: 120, median: 150, q3: 180, max: 200, low: 100, high: 200 },
];

describe("beeswarm (US-188)", () => {
  it("never moves a dot off its value by more than a few pixels, and keeps dots apart", () => {
    const pts = Array.from({ length: 30 }, (_, i) => ({ label: `P${i}`, group: "g", value: 5, tx: 200 + (i % 3), ty: 50 }));
    const dots = swarm(pts);
    expect(Math.max(...dots.map((d) => Math.abs(d.x - 201)))).toBeLessThan(40);
    const near = dots.flatMap((a, i) => dots.slice(i + 1).map((b) => Math.hypot(a.x - b.x, a.y - b.y))).filter((d) => d < 6);
    expect(near.length).toBe(0);
  });

  it("draws a lane per group with its median and a dot per item, greying the rest when one is named", () => {
    const spec: ChartSpec = { type: "beeswarm", x: "project", y: "portfolio", series: ["budget"], figures: { groups: GROUPS, min: 100, max: 210 } };
    const { container } = render(<Beeswarm spec={spec} columns={PCOLS} rows={PROJECTS} highlight={["P3"]} />);
    expect([...container.querySelectorAll("[data-group]")].map((g) => g.getAttribute("data-group"))).toEqual(["Infra", "Digital"]);
    const dots = container.querySelectorAll("[data-dot]");
    expect(dots.length).toBe(12);
    expect(container.querySelector('[data-dot="P3"]')!.getAttribute("fill")).toBe("var(--tl-chart-1)");
    expect(container.querySelector('[data-dot="P2"]')!.getAttribute("fill")).toBe("var(--tl-chart-other)");
  });
});

describe("ridgeline (US-188)", () => {
  it("draws the server's density as an area above the lane", () => {
    const d = ridgePath([0, 1, 0], 0, 100, 50, 30);
    expect(d.startsWith("M0,50")).toBe(true);
    expect(d).toContain("Z");
  });

  it("names each group's quartiles in words", () => {
    const spec: ChartSpec = { type: "ridgeline", x: "project", y: "portfolio", series: ["budget"], figures: { groups: GROUPS.map((g) => ({ ...g, density: [0, 1, 0] })), min: 90, max: 220 } };
    render(<Chart spec={spec} columns={PCOLS} rows={PROJECTS} />);
    // the catalogue chunk is lazy: the table toggle is there at once
    expect(screen.getByRole("button", { name: "Show as table" })).toBeTruthy();
  });
});

describe("UpSet (US-188)", () => {
  const figures = { sets: [{ label: "No budget code", size: 35 }, { label: "No PM", size: 22 }], intersections: [{ members: [false, false], value: 50, degree: 0 }, { members: [true, true], value: 12, degree: 2 }], more: 1, total: 62 };

  it("says which sets make each combination and counts each bar", () => {
    expect(combinationWords(figures.sets, [true, true])).toBe("No budget code + No PM");
    expect(combinationWords(figures.sets, [false, false])).toBe("None of these");
    const { container } = render(<UpSet figures={figures} format="integer" title="Gaps together" />);
    const both = [...container.querySelectorAll("[data-combination]")].find((g) => g.getAttribute("data-combination") === "No budget code + No PM");
    expect(both!.querySelector("title")!.textContent).toBe("No budget code + No PM: 12");
    expect(screen.getByText("1 smaller combination is in the table.")).toBeTruthy();
  });
});

describe("time to event (US-188)", () => {
  const figures: SurvivalFigures = { event: "Go-live", as_of: "2026-01-31", left_out: 2, max_t: 30, curves: [
    { label: "All", n: 4, events: 3, median: 10, points: [{ t: 0, s: 1, at_risk: 4, events: 0, censored: 0 }, { t: 10, s: 0.5, at_risk: 4, events: 2, censored: 0 }, { t: 20, s: 0.25, at_risk: 2, events: 1, censored: 1 }] },
  ] };

  it("steps the share down at each event and holds it to the end of the axis", () => {
    const d = stepPath(figures.curves[0]!.points, (t) => t, (s) => 100 - s * 100, 30);
    expect(d.startsWith("M0,0L10,0L10,50L20,50L20,75L30,75")).toBe(true);
  });

  it("marks still-open items and the median, and names items left out", () => {
    const { container } = render(<Survival figures={figures} />);
    expect(container.querySelector('[data-curve="All"] > title')!.textContent).toBe("All: 3 of 4 ended; half by day 10");
    expect(screen.getByText("Day 20: 1 still open")).toBeTruthy();
    expect(screen.getByText("2 items have no start date or end before they start: left out.")).toBeTruthy();
  });
});

describe("bump chart (US-188)", () => {
  it("breaks a line where an item has no rank", () => {
    expect(bumpSegments([1, 2, null, 3])).toEqual([[[0, 1], [1, 2]], [[3, 3]]]);
  });

  it("names each line at its end with its last rank", () => {
    const figures = { periods: ["2026-01", "2026-02"], items: [{ label: "East", ranks: [2, 1], values: [5, 9] }, { label: "North", ranks: [1, 2], values: [8, 4] }] };
    render(<Bump figures={figures} />);
    expect(screen.getByText("1. East")).toBeTruthy();
    expect(screen.getByText("2. North")).toBeTruthy();
  });
});

describe("waffle (US-188)", () => {
  const figures = { parts: [{ label: "With a budget code", value: 31, pct: 27.9, cells: 28 }, { label: "Rest of open projects", value: 80, pct: 72.1, cells: 72 }], total: 111 };

  it("fills 100 cells in the server's counts and gives every part in words", () => {
    expect(waffleCells(figures.parts).length).toBe(100);
    const { container } = render(<Waffle figures={figures} format="integer" />);
    expect(container.querySelectorAll('[data-part="0"]').length).toBe(28);
    expect((container.querySelector('[data-part="1"]') as HTMLElement).style.background).toBe("var(--tl-chart-other)");
    expect(screen.getByText("31 · 27.9%")).toBeTruthy();
  });
});
