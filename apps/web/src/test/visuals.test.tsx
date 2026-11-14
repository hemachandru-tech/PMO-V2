import { QueryClient } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Column } from "../api/app";
import type { ChartSpec } from "../api/querySpec";
import type { Me } from "../api/types";
import { AppRoutes, Providers } from "../app/App";
import { TestAuthProvider } from "../app/auth";
import { setConfigForTests } from "../app/config";
import { ordinalColour, toOption } from "../components/Chart";
import { HeatGrid, Meter, rampStep } from "../components/HeatGrid";
import { KpiCard } from "../components/ui/kpi-card";
import { markFilters } from "../features/viewer/ViewerWidget";

// US-157: heat grid, meter, ordinal columns, figure tone and the cross-filter within a report.

vi.mock("echarts/core", () => ({
  use: vi.fn(),
  registerTheme: vi.fn(),
  init: () => ({ setOption: vi.fn(), on: vi.fn(), resize: vi.fn(), dispose: vi.fn() }),
}));

const COLS: Column[] = [
  { name: "region", kind: "dimension", label: "Region", ref: "dimension:region", data_type: "text", format: null },
  { name: "reported_status", kind: "dimension", label: "Reported status", ref: "dimension:reported_status", data_type: "text", format: null },
  { name: "open_projects", kind: "measure", label: "Open projects", ref: "measure:open_projects", data_type: "integer", format: "integer" },
];
const ROWS = [["KSA", "Green", 9], ["KSA", "Red", 2], ["Egypt", "Green", 4], ["Egypt", null, 1]];
const RAG: ChartSpec = { type: "heat_grid", x: "region", y: "reported_status", series: ["open_projects"], ramp: "rag", tones: { Green: "good", Red: "critical" } };

describe("heat grid (US-157)", () => {
  it("is a table: status words and icons on the columns, a number in every cell, RAG fills", () => {
    render(<HeatGrid spec={RAG} columns={COLS} rows={ROWS} title="Region by status" />);
    const table = screen.getByRole("table", { name: "Region by status" });
    expect(within(table).getAllByRole("columnheader").map((h) => h.textContent)).toEqual(["Region", "Green", "Red", "Not set"]);
    const ksa = within(table).getByRole("row", { name: /KSA/ });
    expect(within(ksa).getAllByRole("cell").map((c) => c.textContent)).toEqual(["9", "2", "—No records"]);
    expect(within(ksa).getAllByRole("cell").map((c) => c.getAttribute("data-tone"))).toEqual(["good", "critical", null]);
    const egypt = within(table).getByRole("row", { name: /Egypt/ });
    expect(within(egypt).getAllByRole("cell")[2]).toHaveAttribute("data-tone", "neutral");
  });

  it("uses the single-hue ramp with a legend for other columns, and cells are buttons when marks are clickable", async () => {
    const onMark = vi.fn();
    const cols = [COLS[0]!, { ...COLS[1]!, name: "priority", label: "Priority", ref: "dimension:priority" }, COLS[2]!];
    const rows = [["KSA", "P1", 1], ["KSA", "P2", 11], ["Egypt", "P1", 6]];
    render(<HeatGrid spec={{ type: "heat_grid", x: "region", y: "priority", series: ["open_projects"], ramp: "sequential" }} columns={cols} rows={rows}
      onMark={onMark} selected={{ region: "KSA", priority: "P2" }} />);
    const high = screen.getByRole("button", { name: "KSA, P2: 11" });
    expect(high).toHaveAttribute("aria-pressed", "true");
    expect(high.closest("td")).toHaveAttribute("data-step", "5");
    expect(screen.getByRole("button", { name: "KSA, P1: 1" }).closest("td")).toHaveAttribute("data-step", "1");
    await userEvent.click(screen.getByRole("button", { name: "Egypt, P1: 6" }));
    expect(onMark).toHaveBeenCalledWith({ region: "Egypt", priority: "P1" });
  });

  it("ramp steps run from the lowest to the highest value", () => {
    expect([0, 1, 5, 9, 10].map((v) => rampStep(v, 0, 10))).toEqual([1, 1, 3, 5, 5]);
    expect(rampStep(4, 4, 4)).toBe(3);
  });
});

describe("meter (US-157)", () => {
  it("says the part, the whole, the percent and the note, and is a meter for assistive tech", () => {
    render(<Meter meter={{ part: 31, whole: 111, percent: 27.9, note: "Open projects with a budget code" }} format="integer" title="Budget code" />);
    const meter = screen.getByRole("meter", { name: "Budget code" });
    expect(meter).toHaveAttribute("aria-valuenow", "27.9");
    expect(meter).toHaveAttribute("aria-valuetext", "31 of 111 (27.9%)");
    expect(screen.getByText("27.9%")).toBeInTheDocument();
    expect(screen.getByText("31 of 111")).toBeInTheDocument();
    expect(screen.getByText("Open projects with a budget code")).toBeInTheDocument();
  });
});

describe("ordinal columns (US-157)", () => {
  it("colours ordered buckets lightest to strongest, with an edge and the value on every bar", () => {
    const ramp = ["#a", "#b", "#c", "#d"];
    const cols: Column[] = [{ ...COLS[0]!, name: "days_late", label: "Days late" }, COLS[2]!];
    const rows = [["Not past due", 50], ["1–30", 9], ["31–60", 4], ["61–90", 2], ["90+", 1]];
    const opt = toOption({ type: "columns", x: "days_late", series: ["open_projects"], ramp: "ordinal" }, cols, rows, { ramp, edge: "#edge" }) as {
      series: { data: { value: number; itemStyle: { color: string; borderColor: string } }[]; label: { show: boolean } }[];
    };
    expect(opt.series[0]!.data.map((d) => d.itemStyle.color)).toEqual(["#a", "#b", "#c", "#c", "#d"]);
    expect(opt.series[0]!.data.every((d) => d.itemStyle.borderColor === "#edge")).toBe(true);
    expect(opt.series[0]!.label.show).toBe(true);
    expect(ordinalColour(0, 1, ramp)).toBe("#d");
    // Without the ramp, columns stay plain values.
    const plain = toOption({ type: "columns", x: "days_late", series: ["open_projects"] }, cols, rows) as { series: { data: number[] }[] };
    expect(plain.series[0]!.data).toEqual([50, 9, 4, 2, 1]);
  });
});

describe("derived figures and shares label their series (US-176)", () => {
  const cols: Column[] = [
    { name: "region", kind: "dimension", label: "Region", ref: "dimension:region", data_type: "text", format: null },
    { name: "forecast", kind: "measure", label: "Forecast", ref: "measure:forecast", data_type: "decimal", format: "currency" },
    { name: "budget", kind: "measure", label: "Budget", ref: "measure:budget", data_type: "decimal", format: "currency" },
    { name: "pct", kind: "measure", label: "Forecast as % of Budget", ref: "derived:pct", data_type: "decimal", format: "percent" },
  ];
  const rows = [["KSA", 120, 100, 120], ["UAE", 50, 0, null]];

  it("draws the pair as bars and shows the percentage as the first series' label", () => {
    const opt = toOption({ type: "paired_bars", x: "region", series: ["forecast", "budget"], companions: { pct: "forecast" } }, cols, rows) as {
      series: { name: string; label?: { show: boolean; formatter: (p: { dataIndex: number }) => string } }[];
    };
    expect(opt.series.map((s) => s.name)).toEqual(["Forecast", "Budget"]);
    expect(opt.series[0]!.label!.formatter({ dataIndex: 0 })).toBe("120.00%");
    expect(opt.series[0]!.label!.formatter({ dataIndex: 1 })).toBe("–"); // zero budget: blank, not an error
    expect(opt.series[1]!.label).toBeUndefined();
  });

  it("labels donut slices with their share of the total", () => {
    const share: Column[] = [cols[0]!, { ...cols[1]!, format: "integer" }, { ...cols[3]!, name: "share", ref: "share:forecast" }];
    const opt = toOption({ type: "donut", x: "region", series: ["forecast"], companions: { share: "forecast" } }, share, [["KSA", 6, 75], ["UAE", 2, 25]]) as {
      series: { label: { formatter: (p: { name: string; dataIndex: number }) => string } }[];
    };
    expect(opt.series[0]!.label.formatter({ name: "UAE", dataIndex: 1 })).toBe("UAE: 25.00%");
  });
});

describe("figure tone (US-157)", () => {
  it("shows the tone as a word and icon with its rule, the edge colour, and the definition", () => {
    render(<KpiCard label="Overdue go-lives" value="12" tone={{ tone: "critical", label: "Critical", rule: "Critical at or above 10" }}
      definition="In delivery, planned go-live before the reporting date, not deployed." />);
    expect(screen.getByText("Critical")).toBeInTheDocument();
    expect(screen.getByText("· Critical at or above 10")).toBeInTheDocument();
    expect(screen.getByText(/planned go-live before the reporting date/)).toBeInTheDocument();
    expect(document.querySelector("[data-slot=kpi-card]")).toHaveAttribute("data-tone", "critical");
  });
});

describe("cross-filter marks (US-157)", () => {
  it("turns a mark into page filters, but not a blank (Not set) value", () => {
    expect(markFilters({ region: "KSA", reported_status: "Red" }, COLS)).toEqual({
      filters: [{ ref: "dimension:region", op: "=", values: ["KSA"] }, { ref: "dimension:reported_status", op: "=", values: ["Red"] }],
      text: "Region: KSA, Reported status: Red",
    });
    expect(markFilters({ region: "KSA", reported_status: null }, COLS)).toBeNull();
    expect(markFilters({ open_projects: 3 }, COLS)).toBeNull();
    expect(markFilters({}, COLS)).toBeNull();
  });
});

// ------------------------------------------------------------------ the cross-filter on a published report

const viewer: Me = {
  user_id: "u1", display_name: "Nomsa Osei", email: null, tenant_id: "t1", roles: ["viewer"], level: "viewer",
  audience_roles: ["cio"], permissions: ["dashboards.view", "ask.use", "drill.use", "definitions.view"],
  scope_summary: [], locale: null, time_zone: null,
};

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

const DASH = {
  id: "d1", title: "PMO — Governance", kind: "starter", layout: "tabs", version: 1, version_id: "dv1", connection_id: "c1", model_version_id: "v1",
  locked_scope_chip: "projects: Own CIO", scope_message: "Your data scope.", as_of: null, filters: [],
  tabs: [{ id: "t1", title: "Status", widgets: [
    { id: "hg", title: "Region by status", visual: "heat_grid", placement: { x: 0, y: 0, w: 6, h: 4 }, follow_page_filters: true, hidden: false, hidden_message: null, badges: ["certified"], measure: "measure:open_projects" },
    { id: "tb", title: "Projects", visual: "table", placement: { x: 0, y: 4, w: 12, h: 5 }, follow_page_filters: true, hidden: false, hidden_message: null, badges: ["certified"], measure: "measure:open_projects" },
  ] }],
};
const BASE_RUN = { truncated: false, as_of: null, hidden: false, scope: { summary: "", locked: true }, definitions: [], model_version: { id: "v1", version_no: 1 }, connection_id: "c1", badges: ["certified"], filters_applied: [], filters_not_applied: [] };

beforeEach(() => setConfigForTests({ apiBaseUrl: "http://api.test", oidcAuthority: "http://kc", oidcClientId: "tela-web" }));
afterEach(() => vi.unstubAllGlobals());

describe("cross-filter within a report (US-157)", () => {
  it("a heat-grid cell filters the report's table; the same cell again or Clear removes it", async () => {
    const tableBodies: { page_filters: unknown[] }[] = [];
    const drills: string[] = [];
    vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
      if (url.endsWith("/api/v1/me")) return json(viewer);
      if (url.endsWith("/api/v1/published/d1")) return json(DASH);
      if (url.endsWith("/widgets/hg/run")) {
        return json({ ...BASE_RUN, columns: COLS, rows: ROWS, row_count: 4, widget: { id: "hg", title: "Region by status" },
          viz: { visual: "heat_grid", alternatives: [], disabled: [], note: null, chart_spec: RAG } });
      }
      if (url.endsWith("/widgets/tb/run")) {
        tableBodies.push(JSON.parse(String(init?.body)));
        return json({ ...BASE_RUN, columns: [COLS[0], COLS[2]], rows: [["KSA", 11]], row_count: 1, widget: { id: "tb", title: "Projects" },
          viz: { visual: "table", alternatives: [], disabled: [], note: null, chart_spec: { type: "table", series: ["open_projects"] } } });
      }
      if (url.includes("/drill")) {
        drills.push(url);
        return json({ columns: [], rows: [], total: 0, offset: 0, next_offset: null, notice: null });
      }
      return json({ error: { code: "not_found", message: "Not found" } }, 404);
    }));
    window.history.pushState({}, "", "/dashboards/d1");
    render(
      <TestAuthProvider token="tok">
        <Providers client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><AppRoutes /></Providers>
      </TestAuthProvider>,
    );
    const cell = await screen.findByRole("button", { name: "KSA, Red: 2" });
    await waitFor(() => expect(tableBodies).toHaveLength(1));
    expect(tableBodies[0]!.page_filters).toEqual([]);

    await userEvent.click(cell);
    expect(await screen.findByRole("status")).toHaveTextContent("Tables filtered by Region: KSA, Reported status: Red (from Region by status)");
    await waitFor(() => expect(tableBodies.at(-1)!.page_filters).toEqual([
      { ref: "dimension:region", op: "=", values: ["KSA"] }, { ref: "dimension:reported_status", op: "=", values: ["Red"] }]));
    expect(screen.getByRole("button", { name: "KSA, Red: 2" })).toHaveAttribute("aria-pressed", "true");

    await userEvent.click(screen.getByRole("button", { name: "KSA, Red: 2" }));
    await waitFor(() => expect(screen.queryByRole("status")).toBeNull());

    await userEvent.click(screen.getByRole("button", { name: "KSA, Green: 9" }));
    await userEvent.click(await screen.findByRole("button", { name: "Clear" }));
    await waitFor(() => expect(screen.queryByRole("status")).toBeNull());
    expect(tableBodies.at(-1)!.page_filters).toEqual([]);

    // A Not set cell can't be a page filter: it drills to the records instead.
    await userEvent.click(screen.getByRole("button", { name: "Egypt, Not set: 1" }));
    await waitFor(() => expect(drills.some((u) => u.endsWith("/widgets/hg/drill"))).toBe(true));
    expect(screen.queryByRole("status")).toBeNull();
  });
});
