import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { Column } from "../api/app";
import type { ChartSpec } from "../api/querySpec";
import { bandAt, Bullet, bulletBands, type BulletFigures } from "../components/Bullet";
import { type CatalogueColours, catalogueHeight, catalogueOption, ganttData, panelColumns } from "../components/catalogue";
import { Chart } from "../components/Chart";
import { Dumbbell, dumbbellRows } from "../components/Dumbbell";

// US-187: the visual catalogue wave 1 draws the figures the API worked out (COMP-001) and labels every mark.

vi.mock("echarts/core", () => ({
  use: vi.fn(),
  registerTheme: vi.fn(),
  init: () => ({ setOption: vi.fn(), on: vi.fn(), resize: vi.fn(), dispose: vi.fn() }),
}));

const C: CatalogueColours = { up: "up", down: "down", total: "total", muted: "muted", limit: "limit", danger: "danger", series: "series", line: "line", ink: "ink", seq: ["s1", "s2", "s3"], palette: ["p1", "p2"] };
const col = (name: string, label: string, kind: Column["kind"] = "dimension", format: string | null = null): Column =>
  ({ name, kind, label, ref: `${kind}:${name}`, data_type: kind === "measure" ? "integer" : "text", format }) as Column;
type Series = { type: string; data: unknown[]; label?: { formatter: (p: unknown) => string }; markLine?: { data: { name?: string; yAxis: number }[] } };
const series = (o: Record<string, unknown>) => o.series as Series[];

const BUDGET = [col("portfolio", "Portfolio"), col("budget", "Approved budget", "measure", "currency"), col("forecast", "Forecast cost", "measure", "currency")];
const BUDGET_ROWS = [["Digital", 100, 130], ["Infra", 80, 70], ["Data", 50, 65]];

describe("waterfall (US-187)", () => {
  const steps = [
    { label: "Approved budget (total)", kind: "start", value: 230, low: 0, high: 230 },
    { label: "Digital", kind: "up", value: 30, low: 230, high: 260 },
    { label: "Infra", kind: "down", value: -10, low: 250, high: 260 },
    { label: "Forecast cost (total)", kind: "total", value: 250, low: 0, high: 250 },
  ];
  const spec: ChartSpec = { type: "waterfall", x: "portfolio", series: ["budget", "forecast"], figures: { mode: "bridge", steps, format: "currency" } };

  it("floats each step on a transparent base and labels it with its signed change", () => {
    const [base, bars] = series(catalogueOption(spec, BUDGET, BUDGET_ROWS, C));
    expect(base!.data).toEqual([0, 230, 250, 0]);
    expect((bars!.data as { value: number; itemStyle: { color: string } }[]).map((d) => [d.value, d.itemStyle.color])).toEqual([
      [230, "total"], [30, "up"], [10, "down"], [250, "total"]]);
    expect([0, 1, 2].map((i) => bars!.label!.formatter({ dataIndex: i }))).toEqual(["$230", "+$30", "-$10"]);
  });
});

describe("Pareto (US-187)", () => {
  it("draws bars largest first with the cumulative share on a second axis and the vital few in colour", () => {
    const spec: ChartSpec = { type: "pareto", x: "region", series: ["n"], figures: { labels: ["South", "East", "North"], values: [50, 25, 25], cumulative_pct: [50, 75, 100], vital_few: 2 } };
    const o = catalogueOption(spec, [col("region", "Region"), col("n", "Open projects", "measure", "integer")], [], C);
    const [bars, line] = series(o);
    expect((o.xAxis as { data: string[] }).data).toEqual(["South", "East", "North"]);
    expect(bars!.data).toEqual([50, 25, { value: 25, itemStyle: { color: "muted" } }]);
    expect(line).toMatchObject({ type: "line", yAxisIndex: 1, data: [50, 75, 100] });
    expect(line!.markLine!.data).toEqual([{ yAxis: 80 }]);
    expect((o.yAxis as { max?: number }[])[1]!.max).toBe(100);
  });
});

describe("XmR control chart (US-187)", () => {
  it("draws the mean and limits from the server and marks the points beyond", () => {
    const rows = ["2026-01", "2026-02", "2026-03"].map((p, i) => [p, [10, 12, 30][i]]);
    const spec: ChartSpec = { type: "xmr", x: "period", series: ["n"], figures: { mean: 17.33, upper: 28.1, lower: 6.5, beyond: ["2026-03"] } };
    const [line] = series(catalogueOption(spec, [col("period", "Month", "period"), col("n", "Open", "measure", "integer")], rows, C));
    expect(line!.data.slice(0, 2)).toEqual([10, 12]);
    expect(line!.data[2]).toMatchObject({ value: 30, symbol: "diamond", itemStyle: { color: "danger" } });
    expect(line!.markLine!.data.map((d) => [d.name, d.yAxis])).toEqual([["Upper limit", 28.1], ["Mean", 17.33], ["Lower limit", 6.5]]);
  });
});

describe("Gantt (US-187)", () => {
  it("draws a bar per item from start to end and leaves out rows without both dates in order", () => {
    const rows = [["A", "2026-01-01", "2026-06-30", 1], ["B", "2026-03-01", null, 1], ["C", "2026-05-01", "2026-04-01", 1]];
    const { names, data } = ganttData(rows, 0, 1, 2);
    expect(names).toEqual(["A", "B", "C"]);
    expect(data).toEqual([{ name: "A", value: [0, Date.parse("2026-01-01"), Date.parse("2026-06-30"), 0] }]);
    const spec: ChartSpec = { type: "gantt", x: "project", series: ["n"], figures: { start: "start", end: "end", unplanned: 2 } };
    const o = catalogueOption(spec, [col("project", "Project"), col("start", "Start", "period"), col("end", "End", "period"), col("n", "N", "measure")], rows, C);
    expect(series(o)[0]!.type).toBe("custom");
    expect((o.xAxis as { type: string }).type).toBe("time");
    expect(catalogueHeight(spec, rows)).toBe(160);
  });
});

describe("funnel (US-187)", () => {
  it("keeps the stages in order and labels each with its share of the first and its drop", () => {
    const stages = [{ label: "DOU", value: 200, pct_of_first: 100, drop_pct: null }, { label: "DARB", value: 120, pct_of_first: 60, drop_pct: 40 }];
    const [f] = series(catalogueOption({ type: "funnel", series: ["a", "b"], figures: { stages } }, [col("a", "DOU", "measure", "integer"), col("b", "DARB", "measure", "integer")], [[200, 120]], C));
    expect(f).toMatchObject({ type: "funnel", sort: "none", data: [{ name: "DOU", value: 200 }, { name: "DARB", value: 120 }] });
    expect(f!.label!.formatter({ dataIndex: 1 })).toBe("DARB\n120 · 60% of first · −40%");
    expect(f!.label!.formatter({ dataIndex: 0 })).toBe("DOU\n200 · 100% of first");
  });
});

describe("small multiples (US-187)", () => {
  it("draws one panel per item, largest first, on one shared axis", () => {
    const rows = [["2026-01", "North", 1], ["2026-01", "South", 3], ["2026-02", "North", 2], ["2026-02", "South", 6]];
    const spec: ChartSpec = { type: "small_multiples", x: "period", y: "region", series: ["n"], figures: { panels: ["South", "North"], max: 6, min: 0 } };
    const o = catalogueOption(spec, [col("period", "Month", "period"), col("region", "Region"), col("n", "Open", "measure", "integer")], rows, C);
    expect((o.title as { text: string }[]).map((t) => t.text)).toEqual(["South", "North"]);
    expect(series(o).map((s) => s.data)).toEqual([[3, 6], [1, 2]]);
    expect((o.yAxis as { max: number }[]).every((y) => y.max === 6)).toBe(true);
    expect([1, 3, 4, 6, 9, 12].map(panelColumns)).toEqual([1, 3, 2, 3, 3, 4]);
  });
});

describe("bullet (US-187)", () => {
  const figures: BulletFigures = {
    rows: [{ label: "North", value: 95, band: "good" }, { label: "East", value: 60, band: "critical" }],
    warning: 90, critical: 75, higher_is_worse: false, max: 100, target: 90,
  };

  it("lays the bands out by the server's rule, either way round", () => {
    expect(bulletBands(figures)).toEqual([["critical", 0, 75], ["warning", 75, 90], ["good", 90, 100]]);
    expect(bulletBands({ warning: 1, critical: 10, higher_is_worse: true, max: 20 })).toEqual([["good", 0, 1], ["warning", 1, 10], ["critical", 10, 20]]);
    expect(bulletBands({ warning: 5, critical: null, higher_is_worse: true, max: 10 })).toEqual([["good", 0, 5], ["warning", 5, 10]]);
    expect([95, 80, 75].map((v) => bandAt(v, figures))).toEqual(["good", "warning", "critical"]);
  });

  it("lists each item with its value and band in words, and the target", () => {
    render(<Bullet figures={figures} format="percent" title="Baselining compliance" />);
    const list = screen.getByRole("list", { name: "Baselining compliance" });
    expect(within(list).getAllByRole("listitem").map((li) => li.getAttribute("aria-label"))).toEqual(["North: 95.00%, good", "East: 60.00%, critical"]);
    expect(screen.getByText(/Target 90.00% · critical 75.00%/)).toBeInTheDocument();
  });
});

describe("dumbbell (US-187, D3)", () => {
  const spec: ChartSpec = { type: "dumbbell", x: "portfolio", series: ["budget", "forecast"], figures: { gaps: [30, -10, 15], order: [0, 2, 1] } };

  it("orders the items widest gap first and writes the gap", () => {
    expect(dumbbellRows(spec, BUDGET, BUDGET_ROWS).map((r) => [r.label, r.gap])).toEqual([["Digital", 30], ["Data", 15], ["Infra", -10]]);
    const { container } = render(<Dumbbell spec={spec} columns={BUDGET} rows={BUDGET_ROWS} title="Budget and forecast" />);
    expect(screen.getByRole("img", { name: "Budget and forecast" })).toBeInTheDocument();
    expect([...container.querySelectorAll("[data-row]")].map((g) => g.getAttribute("data-row"))).toEqual(["Digital", "Data", "Infra"]);
    expect(container.querySelectorAll("circle").length).toBe(6); // two dots per item; the legend is HTML
    expect(screen.getByText("+$30")).toBeInTheDocument();
  });
});

describe("Chart dispatch (US-187)", () => {
  it("renders the HTML and D3 visuals and keeps the table view", async () => {
    render(<Chart spec={{ type: "dumbbell", x: "portfolio", series: ["budget", "forecast"], figures: { gaps: [30, -10, 15], order: [0, 2, 1] } }} columns={BUDGET} rows={BUDGET_ROWS} title="Gap" />);
    expect(await screen.findByRole("img", { name: "Gap" })).toBeInTheDocument();
    expect(document.querySelector('[data-chart="dumbbell"]')).not.toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "Show as table" }));
    expect(await screen.findByRole("table")).toBeInTheDocument();
  });

  it("draws catalogue charts with ECharts in a box sized for their panels", async () => {
    const rows = [["2026-01", "North", 1], ["2026-01", "South", 3]];
    render(<Chart spec={{ type: "small_multiples", x: "period", y: "region", series: ["n"], figures: { panels: ["South", "North"], max: 3, min: 0 } }}
      columns={[col("period", "Month", "period"), col("region", "Region"), col("n", "Open", "measure", "integer")]} rows={rows} title="Panels" />);
    const box = await screen.findByRole("img", { name: "Panels" });
    expect(box).toHaveAttribute("data-chart", "small_multiples");
    expect(box.style.height).toBe("160px");
  });
});
