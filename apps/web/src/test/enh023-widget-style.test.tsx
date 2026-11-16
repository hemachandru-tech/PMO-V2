import { QueryClient } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Column } from "../api/app";
import type { ReportItem } from "../api/authoring";
import type { ChartSpec } from "../api/querySpec";
import type { Me } from "../api/types";
import { Providers } from "../app/App";
import { TestAuthProvider } from "../app/auth";
import { setConfigForTests } from "../app/config";
import { toOption } from "../components/Chart";
import { chartPaint } from "../components/chartColours";
import { standardChart } from "../components/chartData";
import { WidgetFiltersDialog, WidgetStyleDialog } from "../features/reports/canvas/WidgetSettings";
import { ViewerWidget } from "../features/viewer/ViewerWidget";

// ENH-023: widget filters (US-221), card colour (US-223) and chart colours (US-224).

vi.mock("echarts/core", () => ({
  use: vi.fn(),
  registerTheme: vi.fn(),
  init: () => ({ setOption: vi.fn(), on: vi.fn(), resize: vi.fn(), dispose: vi.fn() }),
}));

const me: Me = {
  user_id: "u1", display_name: "Nomsa Osei", email: null, tenant_id: "t1", roles: ["creator"], level: "creator",
  audience_roles: [], permissions: ["dashboards.view", "drill.use", "definitions.view", "reports.create", "widgets.create"],
  scope_summary: [], locale: null, time_zone: null,
};

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

const col = (name: string, label: string, kind: Column["kind"] = "dimension"): Column =>
  ({ name, kind, label, ref: `${kind}:${name}`, data_type: kind === "measure" ? "integer" : "text", format: kind === "measure" ? "integer" : null }) as Column;
const COLUMNS = [col("region", "Region"), col("n", "Projects", "measure")];
const ROWS = [["KSA", 40], ["UAE", 35], ["Qatar", 12]];
const BARS: ChartSpec = { type: "ranked_bar", x: "region", series: ["n"] };

beforeEach(() => setConfigForTests({ apiBaseUrl: "http://api.test", oidcAuthority: "http://kc", oidcClientId: "tela-web" }));
afterEach(() => vi.unstubAllGlobals());

describe("chart colours from one helper (US-224)", () => {
  it("keeps the brand's colours when nothing is chosen", () => {
    expect(chartPaint(null, ["Projects"], ["KSA"])).toBeNull();
    expect(chartPaint({ scheme: "brand", overrides: {} }, ["Projects"], ["KSA"])).toBeNull();
  });

  it("gives series the scheme and a named bar its swatch", () => {
    const one = chartPaint({ scheme: "warm", overrides: { UAE: "status-danger" } }, ["Projects"], ["KSA", "UAE"])!;
    expect([one.series(0), one.point(0, 0), one.point(0, 1)]).toEqual(["chart-2", undefined, "status-danger"]);
    const two = chartPaint({ scheme: "cool", overrides: { Budget: "accent", UAE: "status-ok" } }, ["Projects", "Budget"], ["KSA", "UAE"])!;
    expect([two.series(0), two.series(1), two.point(0, 1)]).toEqual(["chart-1", "accent", undefined]); // bars are named only on one series
  });

  it("draws the same colours in Recharts / Plot data and in ECharts", () => {
    const spec = { ...BARS, colours: { scheme: "warm", overrides: { UAE: "status-danger" } } } as ChartSpec;
    const c = standardChart(spec, COLUMNS, ROWS);
    expect(c.series[0]!.colour).toBe("var(--tl-chart-2)");
    expect([c.point(0, 0), c.point(0, 1)]).toEqual([undefined, "var(--tl-status-danger)"]);
    const option = toOption(spec, COLUMNS, ROWS, undefined, undefined, (t) => `#${t}`) as { series: { itemStyle: { color: string }; data: unknown[] }[] };
    expect(option.series[0]!.itemStyle.color).toBe("#chart-2");
    expect(option.series[0]!.data).toMatchObject([{ value: 40 }, { value: 35, itemStyle: { color: "#status-danger" } }, { value: 12 }]);
    const donut = toOption({ ...spec, type: "donut" }, COLUMNS, ROWS, undefined, undefined, (t) => `#${t}`) as { series: { data: { itemStyle: { color: string } }[] }[] };
    expect(donut.series[0]!.data.map((d) => d.itemStyle.color)).toEqual(["#chart-2", "#status-danger", "#chart-6"]);
    // Without a choice the theme decides, exactly as before.
    expect(standardChart(BARS, COLUMNS, ROWS).series[0]!.colour).toBe("var(--tl-chart-1)");
    expect((toOption(BARS, COLUMNS, ROWS, undefined, undefined, (t) => t) as { series: { itemStyle?: unknown }[] }).series[0]!.itemStyle).toBeUndefined();
  });
});

function serve(run: Record<string, unknown>) {
  const calls: { url: string; body: Record<string, unknown> }[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    const body = init?.body ? JSON.parse(String(init.body)) as Record<string, unknown> : {};
    calls.push({ url, body });
    if (url.endsWith("/api/v1/me")) return json(me);
    if (url.endsWith("/run")) return json(run);
    if (url.endsWith("/query/filter-values")) return json({ values: ["KSA", "UAE"] });
    if (url.includes("/query/catalog")) {
      return json({ dimensions: [{ ref: "dimension:region", label: "Region", entity: "projects", temporal: false },
        { ref: "dimension:priority", label: "Priority", entity: "projects", temporal: false },
        { ref: "dimension:go_live", label: "Go-live", entity: "projects", temporal: true }] });
    }
    return json({ error: { code: "not_found", message: "Not found" } }, 404);
  }));
  return calls;
}

const RUN = {
  columns: COLUMNS, rows: ROWS, row_count: 3, truncated: false, as_of: null, hidden: false, badges: ["certified"],
  connection_id: "c1", model_version: { id: "mv1", version_no: 1 }, filters_applied: [], filters_not_applied: [],
  widget: { id: "w1", title: "Projects by region" },
  viz: { visual: "ranked_bar", alternatives: [], disabled: [], note: null, chart_spec: { ...BARS, card: "accent" } },
  controls: [{ ref: "dimension:region", label: "Region" }],
  fixed_filters: [{ ref: "dimension:priority", label: "Priority", op: "in", values: ["High", "Critical"] }],
};

const show = (visual = "ranked_bar") => render(
  <TestAuthProvider token="tok">
    <Providers client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <ViewerWidget id="w1" title="Projects by region" visual={visual} versionId={null} filters={[]} filterLabels={{}} onDrill={() => {}} />
    </Providers>
  </TestAuthProvider>,
);

describe("widget filters and card colour on the viewer (US-221, US-223)", () => {
  it("shows the fixed filters with a lock, the reader's filter, and the card colour", async () => {
    const calls = serve(RUN);
    show();
    const card = await screen.findByRole("article", { name: "Projects by region" });
    await waitFor(() => expect(card.getAttribute("data-card")).toBe("accent"));
    const group = within(card).getByRole("group", { name: "Widget filters" });
    expect(group.textContent).toContain("Priority: High, Critical");
    await userEvent.click(within(group).getByRole("button", { name: "Filter by Region" }));
    await userEvent.click(await screen.findByRole("checkbox", { name: "UAE" }));
    await waitFor(() => expect(calls.some((c) => c.url.endsWith("/run") && JSON.stringify(c.body.widget_filters) === JSON.stringify([{ ref: "dimension:region", op: "=", values: ["UAE"] }]))).toBe(true));
    expect(calls.filter((c) => c.url.endsWith("/run")).every((c) => JSON.stringify(c.body.page_filters) === "[]")).toBe(true);
  });

  it("draws a dark figure card in the dark theme", async () => {
    serve({ ...RUN, columns: [COLUMNS[1]], rows: [[87]], controls: [], fixed_filters: [],
      viz: { visual: "figure", alternatives: [], disabled: [], note: null, chart_spec: { type: "figure", card: "dark" } },
      kpi: { value: 87, format: "integer", change: null, sparkline: null, tone: null, definition: null } });
    show("figure");
    // The loading card is replaced by the KPI card once the run is in.
    await waitFor(() => expect(screen.getByRole("article", { name: "Projects by region" }).getAttribute("data-card")).toBe("dark"));
    const card = screen.getByRole("article", { name: "Projects by region" });
    expect(card.getAttribute("data-theme")).toBe("dark");
    expect(within(card).queryByRole("group", { name: "Widget filters" })).toBeNull();
  });
});

describe("one card pattern for every widget (EXEC-214)", () => {
  it("keeps the title, the certified mark and the widget's filters on the header row", async () => {
    serve(RUN);
    show();
    const card = await screen.findByRole("article", { name: "Projects by region" });
    const head = card.querySelector("[data-widget-head]")!;
    await waitFor(() => expect(within(head as HTMLElement).getByRole("group", { name: "Widget filters" })).toBeInTheDocument());
    expect(within(head as HTMLElement).getByRole("heading", { name: "Projects by region" })).toBeInTheDocument();
    expect(within(head as HTMLElement).getByLabelText("Certified: Every measure used is certified")).toBeInTheDocument();
    // The fixed filters are a lock; their words are in its tooltip and accessible text.
    expect(within(head as HTMLElement).getByText("Always filtered to Priority: High, Critical")).toBeInTheDocument();
  });

  it("draws a Custom SQL figure (no KPI extras) as a KPI card, its filters folded into one button", async () => {
    serve({ ...RUN, columns: [COLUMNS[1]], rows: [[87]], badges: ["custom_sql"], fixed_filters: [],
      viz: { visual: "figure", alternatives: [], disabled: [], note: null, chart_spec: { type: "figure" } }, kpi: null });
    show("figure");
    await waitFor(() => expect(screen.getByRole("article", { name: "Projects by region" })).toHaveTextContent("87"));
    const card = screen.getByRole("article", { name: "Projects by region" });
    expect(card).toHaveAttribute("data-slot", "kpi-card");
    expect(within(card).getByLabelText(/^Custom SQL: /)).toBeInTheDocument();
    expect(within(card).queryByRole("group", { name: "Widget filters" })).toBeNull();
    await userEvent.click(within(card).getByRole("button", { name: "Widget filters" }));
    const group = await screen.findByRole("group", { name: "Widget filters" });
    expect(within(group).getByRole("button", { name: "Filter by Region" })).toBeInTheDocument();
  });
});

const ITEM: ReportItem = {
  id: "i1", section_id: "s1", kind: "widget", x: 0, y: 0, w: 6, h: 4, empty_expected: false, hidden_for_roles: [],
  widget_id: "w1", title: "Projects by region", visual: "ranked_bar", connection_id: "c1", model_version_id: "mv1",
  own_filters: [], spec: { visual: { type: "ranked_bar" } } as ReportItem["spec"],
};

const wrap = (ui: React.ReactNode) => render(
  <TestAuthProvider token="tok"><Providers client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>{ui}</Providers></TestAuthProvider>,
);

describe("the canvas dialogs (US-221, US-223, US-224)", () => {
  it("offers fields to readers, at most three, dates left out", async () => {
    serve(RUN);
    const onSave = vi.fn();
    wrap(<WidgetFiltersDialog open onOpenChange={() => {}} item={ITEM} onSave={onSave} />);
    const list = await screen.findByRole("list", { name: "Fields readers can filter by" });
    expect(within(list).queryByText("Go-live")).toBeNull();
    await userEvent.click(within(list).getByRole("checkbox", { name: "Region" }));
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(onSave).toHaveBeenCalledWith({ filters: [], controls: ["dimension:region"] });
  });

  it("adds a fixed filter with its values", async () => {
    serve(RUN);
    const onSave = vi.fn();
    wrap(<WidgetFiltersDialog open onOpenChange={() => {}} item={ITEM} onSave={onSave} />);
    await userEvent.click(await screen.findByRole("button", { name: "Add a fixed filter" }));
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled(); // a field and a value first
    await userEvent.selectOptions(screen.getByLabelText("Field"), "dimension:priority");
    await userEvent.click(await screen.findByRole("checkbox", { name: "KSA" }));
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(onSave).toHaveBeenCalledWith({ filters: [{ ref: "dimension:priority", op: "=", values: ["KSA"] }], controls: null });
  });

  it("picks a card colour and a scheme; filled cards only for a figure", async () => {
    serve(RUN);
    const onSave = vi.fn();
    wrap(<WidgetStyleDialog open onOpenChange={() => {}} item={ITEM} onSave={onSave} />);
    expect(screen.queryByRole("radio", { name: "Dark" })).toBeNull();
    await userEvent.click(screen.getByRole("radio", { name: "Good" }));
    await userEvent.click(screen.getByRole("radio", { name: "Warm" }));
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(onSave).toHaveBeenCalledWith({ card: "good", colours: { scheme: "warm", overrides: {} } });
  });

  it("has no chart colours on a figure, and offers the filled cards there", async () => {
    serve(RUN);
    const onSave = vi.fn();
    wrap(<WidgetStyleDialog open onOpenChange={() => {}} item={{ ...ITEM, visual: "figure", spec: { visual: { type: "figure", card: "brand" } } as ReportItem["spec"] }} onSave={onSave} />);
    expect(screen.getByRole("radio", { name: "Brand filled" })).toBeChecked();
    expect(screen.queryByRole("radio", { name: "Warm" })).toBeNull();
    await userEvent.click(screen.getByRole("radio", { name: "Dark" }));
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(onSave).toHaveBeenCalledWith({ card: "dark" });
  });
});
