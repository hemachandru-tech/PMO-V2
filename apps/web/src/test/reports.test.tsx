import { QueryClient } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Me } from "../api/types";
import { AppRoutes, Providers } from "../app/App";
import { TestAuthProvider } from "../app/auth";
import { setConfigForTests } from "../app/config";
import { dropCell, nudge, type Box } from "../features/reports/canvas/layout";

// US-141: report planner and canvas on the new design system.

vi.mock("echarts/core", () => ({
  use: vi.fn(),
  registerTheme: vi.fn(),
  init: () => ({ setOption: vi.fn(), on: vi.fn(), resize: vi.fn(), dispose: vi.fn() }),
}));

const creator: Me = {
  user_id: "u2", display_name: "Arjun Rao", email: null, tenant_id: "t1", roles: ["creator"], level: "creator", audience_roles: [],
  permissions: ["dashboards.view", "ask.use", "widgets.create", "reports.create", "drill.use"],
  scope_summary: [], locale: null, time_zone: null,
};

type Call = { url: string; method: string; body: unknown; headers: Record<string, string> };
let calls: Call[] = [];

function json(body: unknown, status = 200, headers: Record<string, string> = {}) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json", ...headers } });
}

function serve(handler: (url: string, init?: RequestInit) => Response | undefined) {
  calls = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ url, method: init?.method ?? "GET", body: init?.body ? JSON.parse(String(init.body)) : undefined, headers: (init?.headers ?? {}) as Record<string, string> });
    if (url.endsWith("/api/v1/me")) return json(creator);
    if (url.endsWith("/api/v1/home")) return json(HOME);
    if (url.includes("/api/v1/query/catalog")) return json(CATALOG);
    if (url.endsWith("/api/v1/widgets")) return json({ items: LIBRARY });
    return handler(url, init) ?? json({ error: { code: "not_found", message: "Not found" } }, 404);
  }));
}

function renderAt(path: string) {
  window.history.pushState({}, "", path);
  return render(
    <TestAuthProvider token="tok">
      <Providers client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <AppRoutes />
      </Providers>
    </TestAuthProvider>,
  );
}

beforeEach(() => setConfigForTests({ apiBaseUrl: "http://api.test", oidcAuthority: "http://kc", oidcClientId: "tela-web" }));
afterEach(() => vi.unstubAllGlobals());

const HOME = {
  greeting: { name: "Arjun Rao", scopes: [{ connection_id: "c1", connection_name: "TPG", text: "projects where you are delivery manager" }], as_of: null },
  shelves: { starter: [], published_to_my_role: [], shared_with_me: [] }, empty_text: {}, can_create: true,
};
const CATALOG = {
  connection_id: "c1", model_version_id: "mv1", version_no: 4, engine: "postgres", scope: { summary: "", locked: true },
  measures: [{ ref: "measure:schedule_risk", label: "Schedule risk", plain_rule: "Projects at risk of slipping", format: "integer", entity: "project" }],
  dimensions: [], entities: [], links: [],
};
const lib = (id: string, title: string, visual: string, badges: string[]) => ({
  id, title, visual, badges, spec: {}, question: null, model_version_id: "mv1", connection_id: "c1", follow_page_filters: true, used_on_reports: 1,
});
const LIBRARY = [lib("w1", "Open projects", "figure", ["certified"]), lib("w9", "Delay band by programme", "columns", ["personal"])];
const widgetItem = (id: string, widget: string, title: string, x: number) => ({
  id, section_id: "s1", kind: "widget", x, y: 0, w: 3, h: 2, empty_expected: false, hidden_for_roles: [], widget_id: widget, title,
  visual: "figure", badges: ["certified"], measure: "measure:open_projects", follow_page_filters: true, connection_id: "c1", model_version_id: "mv1",
  spec: { measure: { ref: "measure:open_projects" } },
});
const REPORT = {
  id: "rp1", title: "Delivery health", page_filters: [], revision: 3, owner: { id: "u2", name: "Arjun Rao" }, can_edit: true, published: false,
  updated_at: "2026-10-21T05:12:00Z", plan_id: null, in_dashboards: [], can_undo: true, undo_summary: "Added a text note",
  sections: [
    { id: "s1", title: "Headline", kind: "headline", position: 0, collapsed: false, items: [widgetItem("i1", "w1", "Open projects", 0), widgetItem("i2", "w2", "Overdue go-lives", 3)] },
    { id: "s2", title: "Delivery", kind: "normal", position: 1, collapsed: false, items: [] },
  ],
};

function canvasServer(extra?: (url: string, init?: RequestInit) => Response | undefined) {
  serve((url, init) => {
    const out = extra?.(url, init);
    if (out) return out;
    if (url.endsWith("/api/v1/reports/rp1") && (init?.method ?? "GET") === "GET") return json(REPORT, 200, { ETag: '"3"' });
    if (url.endsWith("/api/v1/reports/rp1/layout")) return json({ ...REPORT, revision: 4 }, 200, { ETag: '"4"' });
    return undefined;
  });
}

describe("canvas layout maths", () => {
  const boxes: Box[] = [{ id: "a", x: 0, y: 0, w: 6, h: 4 }, { id: "b", x: 0, y: 4, w: 6, h: 4 }, { id: "c", x: 6, y: 0, w: 6, h: 2 }];
  it("swaps with the widget below or above and leaves the rest", () => {
    const down = nudge(boxes, "a", "down", false);
    expect(down.find((b) => b.id === "b")).toMatchObject({ y: 0 });
    expect(down.find((b) => b.id === "a")).toMatchObject({ y: 4 });
    expect(down.find((b) => b.id === "c")).toMatchObject({ x: 6, y: 0 });
    const up = nudge(down, "a", "up", false);
    expect(up.find((b) => b.id === "a")).toMatchObject({ y: 0 });
    expect(nudge(boxes, "b", "down", false)).toBe(boxes); // nothing below: no change
  });
  it("resizes within 2 – 12 columns and keeps the box on the grid", () => {
    const wide = nudge([{ id: "a", x: 10, y: 0, w: 2, h: 2 }], "a", "right", true)[0]!;
    expect(wide).toMatchObject({ x: 9, w: 3 });
    expect(nudge([{ id: "a", x: 0, y: 0, w: 2, h: 2 }], "a", "left", true)[0]).toMatchObject({ w: 2 });
  });
  it("turns a drop position into a grid cell", () => {
    // 12 columns over 1188 px with 12 px gaps: 100 px per column step; rows are 96 + 12 px.
    expect(dropCell({ left: 100, top: 50, width: 1188 }, { left: 405, top: 240 }, 3)).toEqual({ x: 3, y: 2 });
    expect(dropCell({ left: 0, top: 0, width: 1188 }, { left: 1150, top: -30 }, 6)).toEqual({ x: 6, y: 0 });
  });
});

describe("report canvas (US-141)", () => {
  it("moves a widget with the arrow keys, announces it and autosaves the layout with an Undo toast", async () => {
    canvasServer();
    renderAt("/reports/rp1");
    const item = await screen.findByRole("group", { name: "Open projects" });
    act(() => item.focus());
    expect(await screen.findByRole("toolbar", { name: "Open projects tools" })).toBeInTheDocument();
    fireEvent.keyDown(item, { key: "ArrowRight", shiftKey: true });
    expect(screen.getByText("Open projects: column 1, row 1, 4 of 12 columns wide, 2 rows high")).toBeInTheDocument();
    expect(screen.getByText("Unsaved changes")).toBeInTheDocument();
    await waitFor(() => expect(calls.some((c) => c.url.endsWith("/layout"))).toBe(true), { timeout: 2000 });
    const put = calls.find((c) => c.url.endsWith("/layout"))!;
    expect(put.method).toBe("PUT");
    expect(put.headers["If-Match"]).toBe('"3"');
    expect(put.body).toMatchObject({ items: expect.arrayContaining([expect.objectContaining({ item_id: "i1", w: 4, section_id: "s1" })]) });
    expect(await screen.findByText("Layout saved")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Undo" })).toBeInTheDocument();
  });

  it("adds a certified measure from the library and lists widgets already on the report as not addable", async () => {
    canvasServer((url, init) => (url.endsWith("/rp1/items") && init?.method === "POST" ? json({ ...REPORT, item_id: "i3" }, 201) : undefined));
    renderAt("/reports/rp1");
    const library = await screen.findByRole("complementary", { name: "Add to the report" });
    expect(await within(library).findByRole("button", { name: "Add Open projects (On this report)" })).toBeDisabled();
    await userEvent.click(await within(library).findByRole("button", { name: "Add Schedule risk" }));
    await waitFor(() => expect(calls.find((c) => c.url.endsWith("/rp1/items"))?.body).toEqual({
      kind: "measure", measure: "measure:schedule_risk", connection_id: "c1",
    }));
    expect(await screen.findByText("Added Schedule risk")).toBeInTheDocument();
  });

  it("edits with chat in a side panel: change list, refused change with its alternative, then Apply", async () => {
    canvasServer((url, init) => {
      if (url.endsWith("/rp1/chat-edit") && init?.method === "POST") {
        return json({
          preview_id: "p1", highlights: ["i1"], clarification: null,
          changes: [
            { op: "resize", item_id: "i1", summary: "Resize Open projects to 12 columns" },
            { op: "refused", item_id: "i2", request: "show Overdue go-lives as trend", reason: "A trend needs a date", alternative: "ranked_bar", alternative_label: "Show as ranked bars" },
          ],
        });
      }
      if (url.endsWith("/chat-edit/p1/apply")) return json({ ...REPORT, undo_summary: "Chat edit: widen it" });
      if (url.endsWith("/items/i2") && init?.method === "PATCH") return json(REPORT);
      return undefined;
    });
    renderAt("/reports/rp1");
    await userEvent.click(await screen.findByRole("button", { name: "Edit with chat" }));
    const panel = screen.getByRole("complementary", { name: "Edit with chat" });
    await userEvent.type(within(panel).getByLabelText("Describe a change to this report"), "widen it");
    await userEvent.click(within(panel).getByRole("button", { name: "Preview" }));
    const proposed = await within(panel).findByRole("region", { name: "Proposed changes" });
    expect(proposed).toHaveTextContent("Resize Open projects to 12 columns");
    expect(proposed).toHaveTextContent("Not changed: show Overdue go-lives as trend — A trend needs a date");
    expect(screen.getByRole("group", { name: "Open projects" }).className).toContain("ring-status-warn");
    await userEvent.click(within(proposed).getByRole("button", { name: "Show as ranked bars" }));
    await waitFor(() => expect(calls.find((c) => c.url.endsWith("/items/i2"))?.body).toEqual({ visual: "ranked_bar" }));
    await userEvent.click(within(proposed).getByRole("button", { name: "Apply" }));
    const made = await within(panel).findByRole("region", { name: "Changes made" });
    expect(made).toHaveTextContent("Resize Open projects to 12 columns");
    expect(within(made).getByRole("button", { name: "Undo these changes" })).toBeInTheDocument();
  });

  it("shows the AI review: its verdict, the changes it added, and its question answered without another review (US-244)", async () => {
    canvasServer((url, init) => {
      if (url.endsWith("/rp1/chat-edit") && init?.method === "POST") {
        const body = typeof init.body === "string" ? JSON.parse(init.body) as { instruction: string; answered?: boolean } : { instruction: "" };
        if (body.answered) {
          return json({ preview_id: "p2", highlights: ["i2"], clarification: null, review: null,
            changes: [{ op: "set_limit", item_id: "i2", summary: "Show the top 5 in Overdue go-lives" }] });
        }
        if (body.instruction.startsWith("only")) {
          return json({ preview_id: null, highlights: [], changes: [], review: { verdict: "ask", reason: "two bar charts", notes: [] },
            clarification: { question: "Which change do you mean?", why: ["two bar charts"], options: [
              { item_id: "i1", title: "Show the top 5 in Open projects", instruction: "limit Open projects to top 5" },
              { item_id: "i2", title: "Show the top 5 in Overdue go-lives", instruction: "limit Overdue go-lives to top 5" }] } });
        }
        return json({
          preview_id: "p1", highlights: ["i1"], clarification: null,
          review: { verdict: "correct", reason: "across the page is full width", notes: [] },
          changes: [
            { op: "resize", item_id: "i1", summary: "Resize Open projects to 12 x 4", by: "ai" },
            { op: "refused", request: "remove w9", reason: "The AI named a widget that isn't on this report", by: "ai" },
          ],
        });
      }
      return undefined;
    });
    renderAt("/reports/rp1");
    await userEvent.click(await screen.findByRole("button", { name: "Edit with chat" }));
    const panel = screen.getByRole("complementary", { name: "Edit with chat" });
    const box = within(panel).getByLabelText("Describe a change to this report");
    await userEvent.type(box, "stretch it across the page");
    await userEvent.click(within(panel).getByRole("button", { name: "Preview" }));
    const proposed = await within(panel).findByRole("region", { name: "Proposed changes" });
    expect(proposed).toHaveTextContent("Checked by AI: changes marked AI were added by the AI — across the page is full width");
    expect(within(proposed).getAllByTitle("Added by the AI review")).toHaveLength(2);
    expect(proposed).toHaveTextContent("Not changed: remove w9 — The AI named a widget that isn't on this report");

    await userEvent.clear(box);
    await userEvent.type(box, "only the five biggest");
    await userEvent.click(within(panel).getByRole("button", { name: "Preview" }));
    const question = await within(panel).findByRole("group", { name: "Clarifying question" });
    expect(question).toHaveTextContent("Which change do you mean?two bar charts");
    await userEvent.click(within(question).getByRole("button", { name: "Show the top 5 in Overdue go-lives" }));
    await waitFor(() => expect(calls.filter((c) => c.url.endsWith("/rp1/chat-edit")).at(-1)?.body).toEqual({
      instruction: "limit Overdue go-lives to top 5", answered: true }));
    expect(await within(panel).findByRole("region", { name: "Proposed changes" })).toHaveTextContent("Show the top 5 in Overdue go-lives");
    expect(calls.find((c) => c.url.endsWith("/rp1/chat-edit"))?.body).toEqual({ instruction: "stretch it across the page" });
  });

  it("asks before Undo all and before removing a section", async () => {
    canvasServer((url) => (url.endsWith("/rp1/undo-all") ? json({ ...REPORT, sections: [{ ...REPORT.sections[0]!, items: [] }] }) : undefined));
    renderAt("/reports/rp1");
    await userEvent.click(await screen.findByRole("button", { name: "Undo all" }));
    const dialog = await screen.findByRole("dialog", { name: "Remove everything from this report?" });
    expect(calls.some((c) => c.url.endsWith("/undo-all"))).toBe(false);
    await userEvent.click(within(dialog).getByRole("button", { name: "Undo all" }));
    await waitFor(() => expect(calls.some((c) => c.url.endsWith("/undo-all") && c.method === "POST")).toBe(true));
    expect(await screen.findByText("Removed everything")).toBeInTheDocument();
  });
});

describe("report planner (US-141)", () => {
  const PLAN = {
    plan_id: "pl1", title: "Delivery health", connection_id: "c1", connection: "TPG", model_version: 4,
    headline_figures: [], page_filters: [{ ref: "dimension:region", label: "Region" }],
    sections: [
      { key: "headline", title: "Headline figures", kind: "headline", widgets: [
        { ref: "w1", title: "Open projects", visual: "figure", certified: true, spec: {}, measure_label: "Open projects" },
        { ref: "w2", title: "Stale projects", visual: "figure", certified: true, spec: {}, measure_label: "Stale projects" },
      ] },
    ],
    gaps: [{ request_part: "workload", reason: "No team workload data in this model" }],
    layout_preview: [{ widget_ref: "w1", section: "headline", x: 0, y: 0, w: 3, h: 2 }, { widget_ref: "w2", section: "headline", x: 3, y: 0, w: 3, h: 2 }],
    diff: null,
  };

  it("plans from a request, previews on the mini-grid, edits in words and builds into the canvas", async () => {
    serve((url, init) => {
      if (url.endsWith("/api/v1/reports/plan")) return json(PLAN);
      if (url.endsWith("/plans/pl1/edit")) return json({ ...PLAN, diff: { added: ["Stale projects"], removed: [] } });
      if (url.endsWith("/plans/pl1/build")) {
        return json({ report_id: "rp1", results: [
          { widget_ref: "w1", widget_id: "w1", title: "Open projects", status: "ok", rows: 1 },
          { widget_ref: "w2", widget_id: "w2", title: "Stale projects", status: "empty", message: "No data in your scope" },
        ] }, 201);
      }
      if (url.endsWith("/api/v1/reports/rp1") && (init?.method ?? "GET") === "GET") return json(REPORT, 200, { ETag: '"3"' });
      return undefined;
    });
    renderAt("/reports/new?request=Delivery%20health%20with%20workload");
    const plan = await screen.findByRole("region", { name: "Report plan" });
    expect(within(plan).getByRole("heading", { name: "Plan · Delivery health" })).toBeInTheDocument();
    expect(within(plan).getByText("1 section · 2 widgets · 12-column layout")).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Not included" })).toHaveTextContent("“workload” — No team workload data in this model");
    expect(screen.getByText(/Every widget uses your scope: projects where you are delivery manager/)).toBeInTheDocument();
    expect(screen.getByText(/Planned in .* s · uses model TPG v4/)).toBeInTheDocument();

    await userEvent.type(within(plan).getByLabelText("Change the plan in words"), "add stale projects");
    await userEvent.click(within(plan).getByRole("button", { name: "Apply" }));
    expect(await within(plan).findByText("Added Stale projects")).toBeInTheDocument();
    expect(within(plan).getByText(/added from your words/)).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Build report" }));
    expect(await screen.findByRole("group", { name: "Open projects" })).toBeInTheDocument();
    expect(await screen.findByText("Built 2 widgets")).toBeInTheDocument();
    expect(screen.getByText("Stale projects: No data in your scope")).toBeInTheDocument();
  });

  it("shows what the AI refined and switches to the rules plan and back (US-243)", async () => {
    const review = { verdict: "correct", reason: "The request is about delivery by region.", changes: ["Removed Stale projects", "Added Open projects by region"],
      added: ["Open projects by region"], removed: ["Stale projects"], dropped: ["Left out “Actual spend”: “actual_spend_total” is not a certified measure you can use."] };
    const ai = {
      ...PLAN, review: { ...review, showing: "ai" },
      sections: [{ key: "headline", title: "Headline figures", kind: "headline", widgets: [
        PLAN.sections[0]!.widgets[0],
        { ref: "w3", title: "Open projects by region", visual: "ranked_bar", certified: true, spec: {}, measure_label: "Open projects" },
      ] }],
      layout_preview: [PLAN.layout_preview[0], { widget_ref: "w3", section: "headline", x: 3, y: 0, w: 6, h: 4 }],
    };
    const shown: string[] = [];
    serve((url, init) => {
      if (url.endsWith("/api/v1/reports/plan")) return json(ai);
      if (url.endsWith("/plans/pl1/show")) {
        const which = JSON.parse(String(init?.body)).show as string;
        shown.push(which);
        return json(which === "rules" ? { ...PLAN, review: { ...review, showing: "rules" } } : ai);
      }
      return undefined;
    });
    renderAt("/reports/new?request=Delivery%20by%20region");
    const plan = await screen.findByRole("region", { name: "Report plan" });
    const note = within(plan).getByRole("status", { name: "AI review" });
    expect(within(note).getByText("Refined by AI")).toBeInTheDocument();
    expect(within(note).getByText("Added Open projects by region")).toBeInTheDocument();
    expect(within(note).getByText("1 AI suggestion left out")).toBeInTheDocument();
    expect(within(plan).getByText(/added by AI/)).toBeInTheDocument();

    await userEvent.click(within(note).getByRole("button", { name: "Show the rules plan" }));
    expect(await within(plan).findByText("Showing the rules plan")).toBeInTheDocument();
    expect(within(plan).getByText("Stale projects")).toBeInTheDocument();
    expect(within(plan).queryByText("Open projects by region")).not.toBeInTheDocument();
    await userEvent.click(within(plan).getByRole("button", { name: "Show the AI plan" }));
    expect(await within(plan).findByText("Refined by AI")).toBeInTheDocument();
    expect(shown).toEqual(["rules", "ai"]);
  });

  it("says when the AI checked the plan and changed nothing (US-243)", async () => {
    serve((url) => (url.endsWith("/api/v1/reports/plan") ? json({ ...PLAN, review: {
      verdict: "confirm", reason: "fits", changes: [], added: [], removed: [], dropped: [], showing: "rules" } }) : undefined));
    renderAt("/reports/new?request=Delivery%20health");
    const plan = await screen.findByRole("region", { name: "Report plan" });
    expect(within(plan).getByText("Checked by AI: the plan fits your request")).toBeInTheDocument();
    expect(within(plan).queryByRole("button", { name: "Show the rules plan" })).not.toBeInTheDocument();
  });

  it("shows a report catalogue layout with its question, callout and footnote (US-159)", async () => {
    const capex = {
      ...PLAN, title: "CAPEX forecast cost report",
      notes: ["There is no actual spend in this data: the plan shows forecast cost (EAC) and approved budgets, and its title says so."],
      sections: [{
        key: "bp_capex_budget", title: "CAPEX budget vs forecast cost", kind: "normal",
        blueprint: { id: "capex_budget", title: "CAPEX budget vs forecast cost",
          question: "How does the forecast cost (EAC) of CAPEX projects compare with their approved budget?",
          population: "CAPEX projects with a comparable approved budget and forecast." },
        callouts: [{ title: "Forecasts are estimates", text: "Forecast cost (EAC) is an estimate at completion.", level: "info" }],
        footnote: "Budget type CAPEX. Forecast minus budget is a forecast comparison.",
        widgets: [{ ref: "w1", title: "Approved budget and forecast cost by portfolio", visual: "paired_bars", certified: true, spec: {}, measure_label: "Approved budget (USD)" }],
      }],
      layout_preview: [{ widget_ref: "w1", section: "bp_capex_budget", x: 0, y: 2, w: 6, h: 4 }],
      gaps: [],
    };
    serve((url) => (url.endsWith("/api/v1/reports/plan") ? json(capex) : undefined));
    renderAt("/reports/new?request=CAPEX%20spend%20report");
    const plan = await screen.findByRole("region", { name: "Report plan" });
    expect(within(plan).getByRole("heading", { name: "Plan · CAPEX forecast cost report" })).toBeInTheDocument();
    expect(within(plan).getByText(/no actual spend in this data/)).toBeInTheDocument();
    const section = within(plan).getByRole("region", { name: "CAPEX budget vs forecast cost" });
    expect(within(section).getByText(/Report catalogue layout · How does the forecast cost/)).toHaveTextContent(
      "Counts: CAPEX projects with a comparable approved budget and forecast.");
    expect(within(section).getByRole("complementary", { name: "Note: Forecasts are estimates" })).toBeInTheDocument();
    expect(within(section).getByRole("note", { name: "Footnote" })).toHaveTextContent("forecast comparison");
    // The callout's rows are not drawn on the mini-grid: the widget starts on the first row.
    expect(within(section).getByText("Approved budget and forecast cost by portfolio").closest("li")).toHaveStyle({ gridRow: "1 / span 4" });
  });

  it("starts a blank report from a dialog", async () => {
    serve((url, init) => {
      if (url.endsWith("/api/v1/reports") && init?.method === "POST") return json({ ...REPORT, title: "Board pack" }, 201);
      if (url.endsWith("/api/v1/reports/rp1") && (init?.method ?? "GET") === "GET") return json(REPORT, 200, { ETag: '"3"' });
      return undefined;
    });
    renderAt("/reports/new");
    await userEvent.click(await screen.findByRole("button", { name: "Start from a blank canvas" }));
    const dialog = await screen.findByRole("dialog", { name: "New blank report" });
    const title = within(dialog).getByLabelText("Report title");
    await userEvent.clear(title);
    await userEvent.type(title, "Board pack");
    await userEvent.click(within(dialog).getByRole("button", { name: "Create report" }));
    await waitFor(() => expect(calls.find((c) => c.url.endsWith("/api/v1/reports") && c.method === "POST")?.body).toEqual({ title: "Board pack" }));
    expect(await screen.findByRole("group", { name: "Open projects" })).toBeInTheDocument();
  });
});
