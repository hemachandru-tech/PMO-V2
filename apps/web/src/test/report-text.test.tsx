import { QueryClient } from "@tanstack/react-query";
import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Column } from "../api/app";
import type { Me } from "../api/types";
import { AppRoutes, Providers } from "../app/App";
import { TestAuthProvider } from "../app/auth";
import { setConfigForTests } from "../app/config";
import { TextBlock } from "../components/TextBlock";
import { PrintExport, type ExportPayload } from "../features/print/PrintExport";
import { ViewerWidget } from "../features/viewer/ViewerWidget";

// US-158: callouts and footnotes, "Data as of" the reporting date, detail tables and dim-on-refresh.

vi.mock("echarts/core", () => ({
  use: vi.fn(),
  registerTheme: vi.fn(),
  init: () => ({ setOption: vi.fn(), on: vi.fn(), resize: vi.fn(), dispose: vi.fn() }),
}));
vi.mock("echarts/renderers", () => ({ SVGRenderer: {}, CanvasRenderer: {} }));

const person = (permissions: string[]): Me => ({
  user_id: "u2", display_name: "Arjun Rao", email: null, tenant_id: "t1", roles: ["creator"], level: "creator", audience_roles: [],
  permissions, scope_summary: [], locale: null, time_zone: null,
});
const creator = person(["dashboards.view", "ask.use", "widgets.create", "reports.create", "drill.use", "definitions.view"]);

type Call = { url: string; method: string; body: unknown };
let calls: Call[] = [];

function json(body: unknown, status = 200, headers: Record<string, string> = {}) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json", ...headers } });
}

function serve(handler: (url: string, init?: RequestInit) => Response | Promise<Response> | undefined) {
  calls = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ url, method: init?.method ?? "GET", body: init?.body ? JSON.parse(String(init.body)) : undefined });
    if (url.endsWith("/api/v1/me")) return json(creator);
    if (url.endsWith("/api/v1/home")) return json({ greeting: { name: "Arjun Rao", scopes: [], as_of: null }, shelves: { starter: [], published_to_my_role: [], shared_with_me: [] }, empty_text: {}, can_create: true });
    if (url.includes("/api/v1/query/catalog")) return json({ connection_id: "c1", model_version_id: "mv1", version_no: 4, engine: "postgres", scope: { summary: "", locked: true }, measures: [], dimensions: [], entities: [], links: [] });
    if (url.endsWith("/api/v1/widgets")) return json({ items: [] });
    return (await handler(url, init)) ?? json({ error: { code: "not_found", message: "Not found" } }, 404);
  }));
}

function renderAt(path: string) {
  window.history.pushState({}, "", path);
  return render(
    <TestAuthProvider token="tok">
      <Providers client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><AppRoutes /></Providers>
    </TestAuthProvider>,
  );
}

beforeEach(() => setConfigForTests({ apiBaseUrl: "http://api.test", oidcAuthority: "http://kc", oidcClientId: "tela-web" }));
afterEach(() => vi.unstubAllGlobals());

const RECORD_COLS: Column[] = [
  { name: "project_id", kind: "dimension", label: "Project ID", ref: "field:projects.project_id", data_type: "text", format: null },
  { name: "name", kind: "dimension", label: "Project", ref: "field:projects.name", data_type: "text", format: null },
  { name: "region", kind: "dimension", label: "Region", ref: "field:projects.region", data_type: "text", format: null },
  { name: "planned_deployment_date", kind: "dimension", label: "Planned go-live", ref: "field:projects.planned_deployment_date", data_type: "date", format: null },
];
const BASE_RUN = { truncated: false, as_of: null, hidden: false, scope: { summary: "", locked: true }, definitions: [], model_version: { id: "v1", version_no: 1 }, connection_id: "c1", badges: ["certified"], filters_applied: [], filters_not_applied: [] };
const DETAIL_RUN = {
  ...BASE_RUN, columns: RECORD_COLS, row_count: 3, widget: { id: "tb", title: "Overdue projects" },
  rows: [["P1", "Alpha", "KSA", "2026-07-01"], ["P2", "Beta", null, "2026-08-15"], ["P3", "Gamma", "Egypt", "2026-09-29"]],
  viz: { visual: "table", alternatives: [], disabled: [], note: null, chart_spec: { type: "table", series: [] } },
  detail: { columns: ["name", "region", "planned_deployment_date"], sort: { field: "planned_deployment_date", dir: "asc" }, export_name: "Overdue projects", entity: "projects", primary_key: ["project_id"] },
};

describe("text blocks (US-158)", () => {
  it("a callout says Note or Warning in words with its title; a footnote is a note", () => {
    render(<><TextBlock kind="callout" level="warning" title="Partial data" text="Budget is set on **31** projects." /><TextBlock kind="footnote" text="Open projects only." /></>);
    const callout = screen.getByRole("complementary", { name: "Warning: Partial data" });
    expect(callout).toHaveAttribute("data-level", "warning");
    expect(within(callout).getByText("Warning")).toBeInTheDocument();
    expect(within(callout).getByText("31").tagName).toBe("STRONG");
    expect(screen.getByRole("note", { name: "Footnote" })).toHaveTextContent("Open projects only.");
  });
});

describe("starter report in the viewer (US-158)", () => {
  const DASH = {
    id: "d1", title: "PMO — Projects", kind: "starter", layout: "tabs", version: 1, version_id: null, connection_id: "c1", model_version_id: "v1",
    locked_scope_chip: "projects: Own CIO", scope_message: "Your data scope.", as_of: "2026-10-21T06:00:00Z", reporting_date: "2026-09-30", filters: [],
    tabs: [{
      id: "t1", title: "Delivery", badges: ["starter"], report_id: null,
      callouts: [{ kind: "callout", title: "Forecasts are estimates", text: "EAC is a forecast.", level: "info" }],
      footnotes: ["Overdue = in delivery, planned go-live before the reporting date and not deployed."],
      widgets: [{ id: "tb", title: "Overdue projects", visual: "table", placement: { x: 0, y: 0, w: 12, h: 5 }, follow_page_filters: true, hidden: false, hidden_message: null, badges: ["certified"], measure: "measure:overdue_go_lives" }],
    }],
  };

  it("shows Data as of the reporting date, the callout above, the footnote below, and a detail table with a record drawer", async () => {
    serve((url) => {
      if (url.endsWith("/api/v1/published/d1")) return json(DASH);
      if (url.endsWith("/widgets/tb/run")) return json(DETAIL_RUN);
      return undefined;
    });
    renderAt("/dashboards/d1");
    expect(await screen.findByText("Data as of 30 Sep 2026")).toBeInTheDocument();
    expect(screen.getByRole("complementary", { name: "Note: Forecasts are estimates" })).toBeInTheDocument();
    expect(screen.getByRole("note", { name: "Footnote" })).toHaveTextContent("planned go-live before the reporting date");
    const table = await screen.findByRole("table", { name: "Overdue projects" });
    // The key column is in the Columns menu, not shown; blanks read Not set; the API's sort is kept.
    expect(within(table).getAllByRole("columnheader").map((h) => h.textContent?.trim())).toEqual(expect.arrayContaining(["Project", "Region", "Planned go-live"]));
    expect(within(table).queryByRole("columnheader", { name: /Project ID/ })).toBeNull();
    const rows = within(table).getAllByRole("row").slice(1);
    expect(rows.map((r) => within(r).getAllByRole("cell")[0]!.textContent)).toEqual(["Alpha", "Beta", "Gamma"]);
    expect(within(rows[1]!).getByText("Not set")).toBeInTheDocument();
    expect(screen.getByText("3 records")).toBeInTheDocument();
    await userEvent.click(within(rows[2]!).getByText("Gamma"));
    const drawer = await screen.findByRole("dialog", { name: "Gamma · Egypt" });
    expect(within(drawer).getByText("Project ID")).toBeInTheDocument();
    expect(within(drawer).getByText("P3")).toBeInTheDocument();
  });
});

describe("refresh keeps the old figures (US-158)", () => {
  it("dims the widget with aria-busy instead of blanking it while new filters run", async () => {
    let release: () => void = () => undefined;
    serve((url, init) => {
      if (!url.endsWith("/widgets/w1/run")) return undefined;
      const filtered = (JSON.parse(String(init?.body)) as { page_filters: unknown[] }).page_filters.length > 0;
      const body = { ...BASE_RUN, columns: [{ name: "region", kind: "dimension", label: "Region", ref: "dimension:region", data_type: "text", format: null }, { name: "n", kind: "measure", label: "Open projects", ref: "measure:open_projects", data_type: "integer", format: "integer" }],
        rows: filtered ? [["KSA", 4]] : [["KSA", 9], ["Egypt", 3]], row_count: filtered ? 1 : 2, widget: { id: "w1", title: "By region" },
        viz: { visual: "table", alternatives: [], disabled: [], note: null, chart_spec: { type: "table", series: ["n"] } } };
      if (!filtered) return json(body);
      return new Promise<Response>((resolve) => { release = () => resolve(json(body)); });
    });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const view = (filters: { ref: string; op: "="; values: string[] }[]) => (
      <TestAuthProvider token="tok"><Providers client={client}>
        <ViewerWidget id="w1" title="By region" visual="table" versionId={null} filters={filters} filterLabels={{}} onDrill={vi.fn()} />
      </Providers></TestAuthProvider>
    );
    const { rerender } = render(view([]));
    expect(await screen.findByText("Egypt")).toBeInTheDocument();
    rerender(view([{ ref: "dimension:region", op: "=", values: ["KSA"] }]));
    await waitFor(() => expect(document.querySelector("[aria-busy=true]")).not.toBeNull());
    expect(screen.getByText("Egypt")).toBeInTheDocument(); // old rows still there, dimmed
    expect(document.querySelector("[aria-busy=true]")).toHaveClass("opacity-60");
    act(() => release());
    await waitFor(() => expect(screen.queryByText("Egypt")).toBeNull());
    expect(document.querySelector("[aria-busy=true]")).toBeNull();
  });
});

describe("text blocks on the canvas (US-158)", () => {
  const REPORT = {
    id: "rp1", title: "Delivery detail", page_filters: [], revision: 3, owner: { id: "u2", name: "Arjun Rao" }, can_edit: true, published: false,
    updated_at: "2026-10-21T05:12:00Z", plan_id: null, in_dashboards: [], can_undo: false, undo_summary: null, reporting_date: "2026-09-30",
    sections: [{ id: "s1", title: "Section 1", kind: "normal", position: 0, collapsed: false, items: [
      { id: "c1", section_id: "s1", kind: "callout", x: 0, y: 0, w: 12, h: 2, empty_expected: false, hidden_for_roles: [], text: "Counts open projects.", title: "Read me", level: "info" },
      { id: "f1", section_id: "s1", kind: "footnote", x: 0, y: 2, w: 12, h: 2, empty_expected: false, hidden_for_roles: [], text: "Population: open projects." },
    ] }],
  };

  it("adds a callout and a footnote, edits the callout's title, kind and text, and shows Data as of", async () => {
    serve((url, init) => {
      if (url.endsWith("/api/v1/reports/rp1") && (init?.method ?? "GET") === "GET") return json(REPORT, 200, { ETag: '"3"' });
      if (url.endsWith("/rp1/items") && init?.method === "POST") return json({ ...REPORT, item_id: "c1" }, 201);
      if (url.endsWith("/rp1/items/c1") && init?.method === "PATCH") return json(REPORT);
      return undefined;
    });
    renderAt("/reports/rp1");
    expect(await screen.findByText("Data as of 30 Sep 2026")).toBeInTheDocument();
    expect(screen.getByRole("complementary", { name: "Note: Read me" })).toBeInTheDocument();
    expect(screen.getByRole("note", { name: "Footnote" })).toHaveTextContent("Population: open projects.");

    await userEvent.click(screen.getByRole("button", { name: "Add callout" }));
    await waitFor(() => expect(calls.some((c) => c.method === "POST" && (c.body as { kind?: string })?.kind === "callout")).toBe(true));
    await userEvent.click(screen.getByRole("button", { name: "Add footnote" }));
    await waitFor(() => expect(calls.some((c) => c.method === "POST" && (c.body as { kind?: string })?.kind === "footnote")).toBe(true));

    act(() => screen.getByRole("group", { name: "Callout" }).focus());
    const tools = await screen.findByRole("toolbar", { name: "Callout tools" });
    await userEvent.click(within(tools).getByRole("button", { name: "Edit" }));
    const dialog = await screen.findByRole("dialog", { name: "Edit callout" });
    await userEvent.clear(within(dialog).getByLabelText("Title"));
    await userEvent.type(within(dialog).getByLabelText("Title"), "Partial data");
    await userEvent.selectOptions(within(dialog).getByLabelText("Kind"), "warning");
    await userEvent.click(within(dialog).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(calls.find((c) => c.method === "PATCH")?.body).toEqual({ title: "Partial data", text: "Counts open projects.", level: "warning" }));
  });
});

describe("print (US-158)", () => {
  it("prints Data as of the reporting date, the starter tab's callout and footnote, report text blocks and only a detail table's columns", () => {
    Object.defineProperty(document, "fonts", { value: { ready: Promise.resolve() }, configurable: true });
    const payload: ExportPayload = {
      dashboard: { id: "d1", title: "PMO — Projects", kind: "starter", layout: "tabs", version: null },
      scope: "projects: Own CIO", as_of: "2026-10-21 06:00", reporting_date: "30 Sep 2026", filters_text: "No filters", exported_at: "2026-10-21 14:05 +03",
      footer: "", excluded_widgets: 0,
      tabs: [{
        title: "Delivery", callouts: [{ kind: "callout", title: "Forecasts are estimates", text: "EAC is a forecast.", level: "info" }],
        footnotes: ["Overdue = in delivery and past the planned go-live."],
        sections: [{ title: null, kind: "body", items: [
          { kind: "widget", id: "tb", title: "Overdue projects", visual: "table", placement: { x: 0, y: 0, w: 12, h: 5 }, badges: ["certified"],
            columns: RECORD_COLS, rows: DETAIL_RUN.rows, row_count: 3, detail_columns: ["name", "region"], viz: { visual: "table", chart_spec: { type: "table" } as never } },
          { kind: "callout", text: "Check the region filter.", title: "Heads up", level: "warning", placement: { x: 0, y: 5, w: 12, h: 2 } },
        ] }],
      }],
    };
    render(<PrintExport payload={payload} />);
    expect(screen.getByText(/Scope: projects: Own CIO · Data as of 30 Sep 2026 · Refreshed 2026-10-21 06:00/)).toBeInTheDocument();
    expect(screen.getByRole("complementary", { name: "Note: Forecasts are estimates" })).toBeInTheDocument();
    expect(screen.getByRole("complementary", { name: "Warning: Heads up" })).toBeInTheDocument();
    expect(screen.getByRole("note", { name: "Footnote" })).toHaveTextContent("past the planned go-live");
    expect(within(screen.getByRole("table")).getAllByRole("columnheader").map((h) => h.textContent)).toEqual(["Project", "Region"]);
  });
});
