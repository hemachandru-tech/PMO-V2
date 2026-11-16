import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { Column } from "../api/app";
import type { ChartSpec } from "../api/querySpec";
import { measureBars, sharedName, standardChart, standardMark } from "../components/chartData";
import { Chart, toOption } from "../components/Chart";
import { PlotChart } from "../components/PlotChart";
import { RechartsChart } from "../components/RechartsChart";
import { activeChartLibrary, applyChartLibrary } from "../design/chartLibrary";

// ENH-018: the standard charts drawn with shadcn/ui Charts (Recharts, US-207) or Observable Plot (US-208), from the
// same spec, with the same labels, highlight and clicks as ECharts.

vi.mock("echarts/core", () => ({
  use: vi.fn(),
  registerTheme: vi.fn(),
  init: () => ({ setOption: vi.fn(), on: vi.fn(), resize: vi.fn(), dispose: vi.fn() }),
}));

const col = (name: string, label: string, kind: Column["kind"] = "dimension", format: string | null = null, of?: string): Column =>
  ({ name, kind, label, ref: `${kind}:${name}`, data_type: kind === "measure" ? "integer" : "text", format, of }) as Column;
const STATUS = [col("status", "Status"), col("n", "Projects", "measure", "integer"), col("share", "Share", "measure", "percent", "n")];
const ROWS = [["Active", 279, 61.3], ["On Hold", 79, 17.4], [null, 73, 16]];
const BARS: ChartSpec = { type: "columns", x: "status", series: ["n"] };

afterEach(() => applyChartLibrary("echarts"));

describe("the standard charts as data (ENH-018)", () => {
  it("reads the spec as ECharts does: labels, series, companions, highlight and the ordinal ramp", () => {
    const c = standardChart({ ...BARS, companions: { share: "n" } }, STATUS, ROWS, ["On Hold"]);
    expect(c.kind).toBe("bar");
    expect(c.labels).toEqual(["Active", "On Hold", "Not set"]);
    expect(c.series.map((s) => [s.key, s.label])).toEqual([["s0", "Projects"]]);
    expect(c.data[0]).toEqual({ label: "Active", s0: 279 });
    expect(c.companion(0, 1)).toBe("17.40%");
    expect([...c.focus!]).toEqual(["On Hold"]);
    expect(c.integer).toBe(true);
    expect(standardChart({ ...BARS, ramp: "ordinal" }, STATUS, ROWS).ordinal).toBe(true);
    expect(standardChart({ ...BARS, type: "ranked_bar" }, STATUS, ROWS).kind).toBe("hbar");
    expect(standardChart({ ...BARS, type: "donut", series: ["n", "share"] }, STATUS, ROWS).series).toHaveLength(1);
    const trend = standardChart({ type: "trend", x: "status", series: ["n", "share"] }, STATUS, ROWS);
    expect([trend.kind, trend.ends, trend.focus]).toEqual(["line", true, null]);
  });

  it("names the same mark as an ECharts click, with the stack value and Not set as null", () => {
    expect(standardMark(BARS, STATUS, ROWS, 2)).toEqual({ status: null });
    expect(standardMark({ ...BARS, stack: "state" }, STATUS, ROWS, 0, "Not set")).toEqual({ status: "Active", state: null });
    expect(standardMark({ ...BARS, x: null }, STATUS, ROWS, 0)).toBeNull();
  });
});

describe("shadcn/ui Charts on Recharts (US-207)", () => {
  it("draws a bar per row, labels the highlighted one and greys the rest, and names the clicked bar", async () => {
    // Drawn still (as in an export) the value label is there at once; jsdom never finishes Recharts' entry animation.
    const still = render(<RechartsChart spec={BARS} columns={STATUS} rows={ROWS} title="Open projects" highlight={["On Hold"]} still />);
    const box = screen.getByRole("img", { name: "Open projects" });
    expect(box).toHaveAttribute("data-library", "recharts");
    const drawn = await waitFor(() => {
      const found = still.container.querySelectorAll(".recharts-bar-rectangle path");
      expect(found).toHaveLength(3);
      return found;
    });
    expect([...drawn].map((b) => b.getAttribute("fill"))).toEqual(["var(--tl-chart-other)", "var(--tl-chart-1)", "var(--tl-chart-other)"]);
    expect(still.container.querySelector(".recharts-label-list")).toHaveTextContent("79");
    still.unmount();

    const onMark = vi.fn();
    const { container } = render(<RechartsChart spec={BARS} columns={STATUS} rows={ROWS} onMark={onMark} title="Open projects" highlight={["On Hold"]} />);
    const bars = await waitFor(() => {
      const found = container.querySelectorAll(".recharts-bar-rectangle path");
      expect(found).toHaveLength(3);
      return found;
    });
    fireEvent.click(bars[1]!);
    expect(onMark).toHaveBeenCalledWith({ status: "On Hold" });
  });

  it("draws grouped bars with a legend for several series, and horizontal bars", async () => {
    const two = render(<RechartsChart spec={{ type: "columns", x: "status", series: ["n", "share"] }} columns={STATUS} rows={ROWS} />);
    await waitFor(() => expect(two.container.querySelectorAll(".recharts-bar-rectangle path")).toHaveLength(6));
    expect(two.container).toHaveTextContent("ProjectsShare");
    const ranked = render(<RechartsChart spec={{ type: "ranked_bar", x: "status", series: ["n"] }} columns={STATUS} rows={ROWS} />);
    await waitFor(() => expect(ranked.container.querySelectorAll(".recharts-bar-rectangle path")).toHaveLength(3));
  });

  it("draws a line and a labelled donut", async () => {
    const line = render(<RechartsChart spec={{ type: "trend", x: "status", series: ["n"] }} columns={STATUS} rows={ROWS} still />);
    await waitFor(() => expect(line.container.querySelector(".recharts-line-curve")).not.toBeNull());
    // Labels show once the slices' animation ends; a still (print) donut draws them at once.
    const donut = render(<RechartsChart spec={{ type: "donut", x: "status", series: ["n"] }} columns={STATUS} rows={ROWS} still />);
    await waitFor(() => expect(donut.container.querySelectorAll(".recharts-pie-sector")).toHaveLength(3));
    expect(donut.container).toHaveTextContent("On Hold");
  });
});

describe("Observable Plot (US-208)", () => {
  it("draws an SVG without Plot's <style> tag (strict CSP), greys the others and names the clicked bar", () => {
    const onMark = vi.fn();
    const { container } = render(<PlotChart spec={BARS} columns={STATUS} rows={ROWS} onMark={onMark} title="Open projects" highlight={["On Hold"]} />);
    expect(screen.getByRole("img", { name: "Open projects" })).toHaveAttribute("data-library", "plot");
    const svg = container.querySelector("svg.tl-plot")!;
    expect(svg).not.toBeNull();
    expect(svg.querySelector("style")).toBeNull();
    const bars = svg.querySelectorAll("g[aria-label='bar'] rect");
    expect(bars).toHaveLength(3);
    expect([...bars].map((b) => b.getAttribute("fill"))).toEqual(["var(--tl-chart-other)", "var(--tl-chart-1)", "var(--tl-chart-other)"]);
    expect(svg.querySelector("g[aria-label='text']")).toHaveTextContent("79");
    fireEvent.click(bars[1]!);
    expect(onMark).toHaveBeenCalledWith({ status: "On Hold" });
  });

  it("colours ordered buckets along the ramp with an edge, and draws grouped bars, lines and a donut", () => {
    const ramp = render(<PlotChart spec={{ ...BARS, ramp: "ordinal" }} columns={STATUS} rows={ROWS} />);
    const rects = ramp.container.querySelectorAll("g[aria-label='bar'] rect");
    expect([...rects].map((r) => r.getAttribute("fill"))).toEqual(["var(--tl-seq-2)", "var(--tl-seq-4)", "var(--tl-seq-5)"]);
    expect(rects[0]!.getAttribute("stroke")).toBe("var(--tl-chart-1)");
    const two = render(<PlotChart spec={{ type: "paired_bars", x: "status", series: ["n", "share"] }} columns={STATUS} rows={ROWS} />);
    expect(two.container.querySelectorAll("g[aria-label='bar'] rect")).toHaveLength(6);
    expect(two.container).toHaveTextContent("Share");
    const line = render(<PlotChart spec={{ type: "trend", x: "status", series: ["n", "share"] }} columns={STATUS} rows={ROWS} still />);
    expect(line.container.querySelectorAll("g[aria-label='line'] path")).toHaveLength(2);
    expect(line.container.querySelector("g[aria-label='tip']")).toBeNull(); // print: no pointer
    const donut = render(<PlotChart spec={{ type: "donut", x: "status", series: ["n"] }} columns={STATUS} rows={ROWS} />);
    expect(donut.container.querySelectorAll("path")).toHaveLength(3);
  });
});

describe("Chart follows the tenant's library (US-206)", () => {
  it("draws standard charts with the chosen library and keeps ECharts for the rest", async () => {
    applyChartLibrary("plot");
    expect(activeChartLibrary()).toBe("plot");
    const { container, rerender } = render(<Chart spec={BARS} columns={STATUS} rows={ROWS} title="Open" view="chart" />);
    await waitFor(() => expect(container.querySelector("[data-library='plot']")).not.toBeNull());
    rerender(<Chart spec={BARS} columns={STATUS} rows={ROWS} title="Open" view="chart" library="recharts" />);
    await waitFor(() => expect(container.querySelector("[data-library='recharts']")).not.toBeNull());
    rerender(<Chart spec={{ ...BARS, type: "heat_grid" }} columns={STATUS} rows={ROWS} title="Open" view="chart" />);
    expect(container.querySelector("[data-library]")).toBeNull();
    applyChartLibrary("nonsense");
    expect(activeChartLibrary()).toBe("echarts");
  });
});

describe("one row of measures as bars (EXEC-173)", () => {
  const COSTS = ["internal", "external", "contingency", "hardware", "software"].map((k) => col(k, `Forecast cost: ${k} (USD)`, "measure", "currency"));
  const ROW = [[120, 62, 30, 11, 12]];
  const SPEC: ChartSpec = { type: "ranked_bar", x: null, series: COSTS.map((c) => c.name) } as unknown as ChartSpec;

  it("turns the row into one bar per measure, named by the measure", () => {
    const bars = measureBars(SPEC, COSTS, ROW)!;
    expect(bars.spec).toMatchObject({ type: "ranked_bar", x: "measure", series: ["value"] });
    expect(bars.rows).toEqual([["Internal", 120], ["External", 62], ["Contingency", 30], ["Hardware", 11], ["Software", 12]]);
    expect(bars.columns[1]).toMatchObject({ kind: "measure", format: "currency", label: "Forecast cost (USD)" });
    const option = toOption(bars.spec, bars.columns, bars.rows) as { yAxis: { data: string[] }; series: { data: unknown[] }[] };
    expect(option.series).toHaveLength(1);
    expect(option.series[0]!.data).toHaveLength(5);
    expect(measureBars({ ...SPEC, x: "status" } as ChartSpec, COSTS, ROW)).toBeNull();
    // Names with nothing in common stay whole.
    expect(sharedName(["Open projects", "Closed projects"])).toEqual({ names: ["Open projects", "Closed projects"], common: "" });
    expect(sharedName(["Budget (USD)", "Forecast (USD)"])).toEqual({ names: ["Budget", "Forecast"], common: "(USD)" });
    expect(measureBars({ ...SPEC, series: ["internal"] } as ChartSpec, COSTS, ROW)).toBeNull();
    expect(measureBars({ ...SPEC, type: "columns" } as ChartSpec, COSTS, ROW)).toBeNull();
  });

  it("draws five bars in Recharts and Plot, and clicking one filters nothing", async () => {
    const onMark = vi.fn();
    const r = render(<Chart spec={SPEC} columns={COSTS} rows={ROW} view="chart" library="recharts" onMark={onMark} />);
    await waitFor(() => expect(r.container.querySelectorAll(".recharts-bar-rectangle path")).toHaveLength(5));
    expect(r.container).toHaveTextContent("Hardware");
    fireEvent.click(r.container.querySelectorAll(".recharts-bar-rectangle path")[0]!);
    expect(onMark).not.toHaveBeenCalled();
    const p = render(<Chart spec={SPEC} columns={COSTS} rows={ROW} view="chart" library="plot" />);
    await waitFor(() => expect(p.container.querySelectorAll("g[aria-label='bar'] rect")).toHaveLength(5));
  });
});
