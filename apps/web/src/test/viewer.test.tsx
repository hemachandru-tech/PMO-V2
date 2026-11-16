import { QueryClient } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Me } from "../api/types";
import { AppRoutes, Providers } from "../app/App";
import { TestAuthProvider } from "../app/auth";
import { setConfigForTests } from "../app/config";
import { formatRelative } from "../components/format";

// Wave 2 Viewer screens on the new design system: Home (US-138), dashboard viewer (US-139), Ask (US-140).

vi.mock("echarts/core", () => ({
  use: vi.fn(),
  registerTheme: vi.fn(),
  init: () => ({ setOption: vi.fn(), on: vi.fn(), resize: vi.fn(), dispose: vi.fn() }),
}));

const viewer: Me = {
  user_id: "u1", display_name: "Nomsa Osei", email: null, tenant_id: "t1", roles: ["viewer"], level: "viewer",
  audience_roles: ["cio"], permissions: ["dashboards.view", "ask.use", "drill.use", "definitions.view"],
  scope_summary: [], locale: null, time_zone: null,
};
const creator: Me = { ...viewer, level: "creator", roles: ["creator"], permissions: [...viewer.permissions, "reports.create", "widgets.create", "dashboards.create"] };

type Handler = (url: string, init?: RequestInit) => Response | undefined;

export function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

function serve(handler: Handler, me: Me = viewer) {
  const fetch = vi.fn(async (url: string, init?: RequestInit) => {
    if (url.endsWith("/api/v1/me")) return json(me);
    return handler(url, init) ?? json({ error: { code: "not_found", message: "Not found" } }, 404);
  });
  vi.stubGlobal("fetch", fetch);
  return fetch;
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
  greeting: { name: "Nomsa Osei", scopes: [{ connection_id: "c1", connection_name: "TPG", text: "projects: Own CIO" }], as_of: "2026-10-21T06:00:00Z" },
  shelves: {
    starter: [{ id: "d1", type: "dashboard", title: "PMO — Projects", badges: ["certified", "starter"], version: 2, count: { tabs: 6, widgets: 27 }, last_edited_at: "2026-10-20T06:00:00Z", owner: "Tela starter" }],
    published_to_my_role: [], shared_with_me: [],
  },
  empty_text: { starter: "No starter content yet.", published_to_my_role: "Nothing published to your role yet.", mine: "Your drafts and reports will appear here.", shared_with_me: "Nothing has been shared with you yet." },
  can_create: false,
  suggested_questions: ["Open projects by status", "Overdue DARB gates by priority"],
};

describe("home (US-138)", () => {
  it("starts with a question: suggested questions go to Ask; shelves show badges, owner and freshness", async () => {
    serve((url) => {
      if (url.endsWith("/api/v1/home")) return json(HOME);
      if (url.endsWith("/api/v1/ask/conversations")) return json({ items: [{ id: "c9", title: "Overdue go-lives by platform", updated_at: new Date().toISOString(), turns: 2 }] });
      if (url.endsWith("/api/v1/ask/turns")) return json({ turn_id: null, message: "stop" });
      return undefined;
    });
    renderAt("/");
    const starter = await screen.findByRole("region", { name: "Starter" });
    expect(within(starter).getByRole("link", { name: "PMO — Projects" })).toHaveAttribute("href", "/dashboards/d1");
    expect(within(starter).getByText(/Tela starter · updated/)).toBeInTheDocument();
    expect(within(starter).getByText("Certified")).toBeInTheDocument();
    const empty = screen.getByRole("region", { name: "Published to my role" });
    expect(within(empty).getByText(/publishes dashboards to roles/)).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /Build with chat/ })).not.toBeInTheDocument();
    expect(await screen.findByRole("link", { name: "Overdue go-lives by platform" })).toHaveAttribute("href", "/ask?c=c9");
    await userEvent.click(screen.getByRole("button", { name: "Open projects by status" }));
    expect(window.location.pathname).toBe("/ask");
  });

  it("offers Build with chat and New report to Creators, with their own shelf", async () => {
    serve((url) => (url.endsWith("/api/v1/home") ? json({ ...HOME, can_create: true, shelves: { ...HOME.shelves, mine: [] } })
      : url.endsWith("/api/v1/ask/conversations") ? json({ items: [] }) : undefined), creator);
    renderAt("/");
    expect(await screen.findByRole("link", { name: /Build with chat/ })).toHaveAttribute("href", "/reports/new");
    expect(screen.getByRole("link", { name: "New report" })).toHaveAttribute("href", "/reports");
    expect(within(screen.getByRole("region", { name: "Mine" })).getByText(/Build with chat, or New report/)).toBeInTheDocument();
    expect(await screen.findByText("No questions yet")).toBeInTheDocument();
  });

  it("formats freshness relative to now", () => {
    const now = new Date("2026-10-21T12:00:00Z");
    expect(formatRelative("2026-10-18T12:00:00Z", now)).toBe("3 days ago");
    expect(formatRelative("2026-10-21T11:59:30Z", now)).toBe("just now");
    expect(formatRelative(null, now)).toBe("");
  });
});

const DASH = {
  id: "d1", title: "PMO — Projects", kind: "starter", layout: "tabs", version: 1, version_id: "dv1", connection_id: "c1", model_version_id: "v1",
  locked_scope_chip: "projects: Own CIO", scope_message: "This is your data scope from your Microsoft groups. Filters can narrow it but not widen it.",
  as_of: null, filters: [{ ref: "dimension:region", label: "Region" }],
  tabs: [{ id: "t1", title: "Overview", widgets: [
    { id: "w1", title: "Open projects", visual: "figure", placement: { x: 0, y: 0, w: 3, h: 2 }, follow_page_filters: true, hidden: false, hidden_message: null, badges: ["certified"], measure: "measure:open_projects" },
    { id: "w2", title: "Approved budget", visual: "figure", placement: { x: 3, y: 0, w: 3, h: 2 }, follow_page_filters: true, hidden: true, hidden_message: "Hidden for your role", badges: ["certified"], measure: "measure:approved_budget_total" },
    { id: "w3", title: "Open projects by region", visual: "ranked_bar", placement: { x: 0, y: 2, w: 12, h: 4 }, follow_page_filters: true, hidden: false, hidden_message: null, badges: ["personal"], measure: "measure:open_projects" },
  ] }, { id: "t2", title: "Finance", widgets: [] }],
};
const COLS = [{ name: "open_projects", kind: "measure", label: "Open projects", ref: "measure:open_projects", data_type: "decimal", format: "integer" }];
const BASE_RUN = { truncated: false, as_of: null, hidden: false, scope: { summary: "", locked: true }, definitions: [], model_version: { id: "v1", version_no: 1 }, connection_id: "c1", badges: ["certified"], filters_applied: [] };

describe("dashboard viewer (US-139)", () => {
  it("shows the locked scope, hidden widgets, a retryable error card, then a KPI card with the server's change and sparkline", async () => {
    let failures = 0;
    const bodies: string[] = [];
    serve((url, init) => {
      if (url.endsWith("/api/v1/published/d1")) return json(DASH);
      if (url.endsWith("/filter-values")) return json({ values: ["KSA", "DPO"] });
      if (url.endsWith("/widgets/w1/run")) {
        bodies.push(String(init?.body));
        failures += 1;
        if (failures === 1) return json({ error: { code: "source_unreachable", message: "Source unavailable — Retry.", request_id: "r1" } }, 503);
        return json({ ...BASE_RUN, columns: COLS, rows: [[12]], row_count: 1, filters_not_applied: [],
          viz: { visual: "figure", alternatives: [], disabled: [], note: null, chart_spec: { type: "figure", series: ["open_projects"] } }, widget: { id: "w1", title: "Open projects" },
          kpi: { value: 12, format: "integer", change: { previous: 9, change: 3, change_pct: 33.3, previous_range: "1 Jul 2026 to 30 Sep 2026" },
            sparkline: { grain: "month", by: "Planned deployment date", range: "last_12_months", points: [{ period: "2026-08-01", value: 2 }, { period: "2026-09-01", value: 4 }] } } });
      }
      if (url.endsWith("/widgets/w3/run")) {
        return json({ ...BASE_RUN, columns: [{ name: "region", kind: "dimension", label: "Region", ref: "dimension:region", data_type: "text", format: null }, ...COLS],
          rows: [["KSA", 7], ["DPO", 5]], row_count: 2, filters_not_applied: ["dimension:region"], badges: ["personal"],
          viz: { visual: "ranked_bar", alternatives: [], disabled: [], note: null, chart_spec: { type: "ranked_bar", x: "region", series: ["open_projects"] } }, widget: { id: "w3", title: "Open projects by region" } });
      }
      if (url.endsWith("/widgets/w1/drill")) return json({ columns: [{ name: "project", kind: "dimension", label: "Project", ref: "", data_type: "text", format: null }], rows: [["PRJ-1"]], total: 1, offset: 0, next_offset: null, notice: null });
      if (url.includes("/api/v1/definitions/")) return json({ ref: "measure:open_projects", kind: "measure", label: "Open projects", rule_text: "Projects not closed.", fields_used: ["state"], blank_handling: "Blank states are not counted.", badge_text: "Certified by PMO Office", in_scope_text: "Within projects: Own CIO", can_show_query: false });
      return undefined;
    });
    renderAt("/dashboards/d1");
    expect(await screen.findByLabelText(/Your data scope: projects: Own CIO/)).toBeInTheDocument();
    expect(screen.getByText("Hidden for your role")).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Finance" })).toBeInTheDocument();
    expect(await screen.findByText("Request id r1")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByRole("button", { name: "12" })).toBeInTheDocument();
    expect(screen.getByText("+3 (+33.3%) vs 1 Jul 2026 to 30 Sep 2026")).toBeInTheDocument();
    expect(screen.getByRole("img", { name: /Open projects by Planned deployment date, last 12 months: 2026-08 2, 2026-09 4/ })).toBeInTheDocument();
    // Widget card: one header pattern, personal badge, filter reach, chart ↔ table.
    const card = screen.getByRole("article", { name: "Open projects by region" });
    expect(within(card).getByText("Personal")).toBeInTheDocument();
    expect(within(card).getByText("Not affected by Region")).toBeInTheDocument();
    await userEvent.click(within(card).getByRole("button", { name: "Table" }));
    // The table view loads on first use (US-148 bundle budget).
    expect(await within(card).findByRole("cell", { name: "KSA" })).toBeInTheDocument();

    // Filter chip: pick a value; widgets re-run with the page filter.
    await userEvent.click(screen.getByRole("button", { name: "Filter by Region" }));
    await userEvent.click(await screen.findByRole("checkbox", { name: "KSA" }));
    await userEvent.keyboard("{Escape}");
    expect(await screen.findByRole("button", { name: "Region: KSA. Change filter" })).toBeInTheDocument();
    await waitFor(() => expect(bodies.some((b) => b.includes('"values":["KSA"]'))).toBe(true));
    await userEvent.click(screen.getByRole("button", { name: "Reset filters" }));
    expect(screen.queryByRole("button", { name: "Reset filters" })).not.toBeInTheDocument();

    // KPI value → drill sheet with the definition first.
    await userEvent.click(await screen.findByRole("button", { name: "12" }));
    const sheet = await screen.findByRole("dialog");
    expect(within(sheet).getByText("Drill to records")).toBeInTheDocument();
    expect(await within(sheet).findByText("Projects not closed.")).toBeInTheDocument();
    expect(await within(sheet).findByRole("cell", { name: "PRJ-1" })).toBeInTheDocument();
  });

  it("exports with the reader's filters, follows the job and offers the signed download (US-095)", async () => {
    const posts: unknown[] = [];
    let polls = 0;
    const job = { job_id: "j1", format: "xlsx", dashboard_id: "d1", file_name: null, size_bytes: null, row_count: null, excluded_widgets: null,
      download_url: null, expires_at: null, file_expires_at: null, error: null };
    serve((url, init) => {
      if (url.endsWith("/api/v1/published/d1")) return json({ ...DASH, tabs: [{ id: "t1", title: "Overview", widgets: [] }] });
      if (url.endsWith("/filter-values")) return json({ values: ["KSA", "DPO"] });
      if (url.endsWith("/api/v1/exports") && init?.method === "POST") {
        posts.push({ body: JSON.parse(String(init.body)), key: (init.headers as Record<string, string>)["Idempotency-Key"] });
        return json({ ...job, status: "queued" }, 202);
      }
      if (url.endsWith("/api/v1/exports/j1")) {
        polls += 1;
        return json(polls < 2 ? { ...job, status: "running" } : { ...job, status: "succeeded", file_name: "PMO-Projects-2026-10-21.xlsx", size_bytes: 20480,
          row_count: 1234, excluded_widgets: 1, download_url: "/api/v1/exports/j1/download?token=abc", expires_at: "2026-10-21T10:15:00Z" });
      }
      return undefined;
    }, { ...viewer, permissions: [...viewer.permissions, "export.use"] });
    renderAt("/dashboards/d1");
    await userEvent.click(await screen.findByRole("button", { name: "Filter by Region" }));
    await userEvent.click(await screen.findByRole("checkbox", { name: "KSA" }));
    await userEvent.keyboard("{Escape}");
    await userEvent.click(screen.getByRole("button", { name: "Export" }));
    await userEvent.click(await screen.findByRole("menuitem", { name: /Excel/ }));
    const dialog = await screen.findByRole("dialog", { name: "Excel export" });
    expect(posts).toEqual([{ body: { dashboard_id: "d1", dashboard_version_id: "dv1", format: "xlsx",
      page_filters: [{ ref: "dimension:region", op: "=", values: ["KSA"] }] }, key: expect.any(String) }]);
    expect(await within(dialog).findByText("PMO-Projects-2026-10-21.xlsx", {}, { timeout: 4000 })).toBeInTheDocument();
    expect(within(dialog).getByText(/20 KB · 1,234 rows · The link works for 15 minutes/)).toBeInTheDocument();
    expect(within(dialog).getByText("1 widget is hidden for your role and not included.")).toBeInTheDocument();
    expect(within(dialog).getByRole("link", { name: "Download" })).toHaveAttribute("href", "http://api.test/api/v1/exports/j1/download?token=abc");
  });

  it("hides Export without the permission", async () => {
    serve((url) => (url.endsWith("/api/v1/published/d1") ? json({ ...DASH, tabs: [] }) : undefined));
    renderAt("/dashboards/d1");
    await screen.findByText("This dashboard has no reports yet");
    expect(screen.queryByRole("button", { name: "Export" })).not.toBeInTheDocument();
  });

  it("survives a dashboard without tabs or filters", async () => {
    serve((url) => (url.endsWith("/api/v1/published/d2") ? json({ ...DASH, id: "d2", tabs: undefined, filters: undefined }) : undefined));
    renderAt("/dashboards/d2");
    expect(await screen.findByText("This dashboard has no reports yet")).toBeInTheDocument();
  });
});

function sse(events: [string, unknown][]) {
  const text = events.map(([e, d], i) => `id: ${i + 1}\nevent: ${e}\ndata: ${JSON.stringify(d)}\n\n`).join("");
  return new Response(text, { status: 200, headers: { "Content-Type": "text/event-stream" } });
}

const FIGURE_ANSWER: [string, unknown][] = [
  ["intent", {}], ["plan", { chips: [] }],
  ["viz", { visual: "figure", alternatives: [], disabled: [], note: null, chart_spec: { type: "figure", series: ["open_projects"] } }],
  ["narrative", { headline: "24 open projects in your scope.", sentences: [] }],
  ["complete", { turn_id: "t1", certified: true, scope_text: "projects: Own CIO", filters_text: "None", as_of: "2026-10-21T06:00:00Z", interpreted: [], row_count: 1,
    definitions: [{ ref: "measure:open_projects", label: "Open projects", plain_rule: "", blank_handling: "", rule_ref: null, format: "integer" }],
    columns: COLS, rows: [[24]], connection: "TPG", model_version: 4,
    spec: { model_version_id: "v1", connection_id: "c1", measure: { ref: "measure:open_projects" } } }],
];

describe("ask (US-140)", () => {
  it("reopens a conversation from the history, with a headline figure", async () => {
    serve((url) => {
      if (url.endsWith("/api/v1/ask/conversations")) return json({ items: [{ id: "c9", title: "Open projects", updated_at: new Date().toISOString(), turns: 1 }] });
      if (url.endsWith("/api/v1/ask/conversations/c9")) return json({ id: "c9", title: "Open projects", turns: [{ id: "t1", question: "How many open projects?" }] });
      if (url.endsWith("/t1/stream")) return sse(FIGURE_ANSWER);
      return undefined;
    });
    renderAt("/ask?c=c9");
    expect(await screen.findByRole("heading", { name: "24 open projects in your scope." })).toBeInTheDocument();
    expect(screen.getByText("How many open projects?")).toBeInTheDocument();
    expect(screen.getByLabelText("Open projects: 24")).toBeInTheDocument();
    const history = screen.getAllByRole("navigation", { name: "Conversation history" })[0]!;
    expect(await within(history).findByRole("link", { name: "Open projects" })).toHaveAttribute("aria-current", "page");
    await userEvent.click(within(history).getByRole("button", { name: "New conversation" }));
    expect(screen.queryByText("How many open projects?")).not.toBeInTheDocument();
    expect(screen.getByText("Ask about your data")).toBeInTheDocument();
  });

  it("offers what it can answer when it can't, as one-click questions", async () => {
    const asked: string[] = [];
    serve((url, init) => {
      if (url.endsWith("/api/v1/ask/conversations")) return json({ items: [] });
      if (url.endsWith("/api/v1/ask/turns") && init?.method === "POST") {
        asked.push(JSON.parse(String(init.body)).question as string);
        return json({ turn_id: `t${asked.length}`, conversation_id: "c1" }, 202);
      }
      if (url.endsWith("/t1/stream")) return sse([["intent", {}], ["error", { code: "unsupported_question", message: "There's no timesheet data.", examples: ["Open projects"] }]]);
      if (url.endsWith("/t2/stream")) return sse(FIGURE_ANSWER);
      return undefined;
    });
    renderAt("/ask?q=team%20utilisation");
    expect(await screen.findByRole("alert")).toHaveTextContent("There's no timesheet data.");
    await userEvent.click(screen.getByRole("button", { name: "Open projects" }));
    expect(await screen.findByRole("heading", { name: "24 open projects in your scope." })).toBeInTheDocument();
    expect(asked).toEqual(["team utilisation", "Open projects"]);
  });
});

describe("Ask on a dashboard (US-189)", () => {
  const W1_RUN = { ...BASE_RUN, columns: COLS, rows: [[12]], row_count: 1, filters_not_applied: [], widget: { id: "w1", title: "Open projects" },
    viz: { visual: "figure", alternatives: [], disabled: [], note: null, chart_spec: { type: "figure", series: ["open_projects"] } } };

  function serveAsk(posts: Record<string, unknown>[], me: Me = viewer) {
    return serve((url, init) => {
      if (url.endsWith("/api/v1/published/d1")) return json(DASH);
      if (url.endsWith("/filter-values")) return json({ values: ["KSA", "DPO"] });
      if (url.endsWith("/widgets/w1/run") || url.endsWith("/widgets/w3/run")) return json(W1_RUN);
      if (url.endsWith("/api/v1/ask/turns") && init?.method === "POST") {
        posts.push(JSON.parse(String(init.body)) as Record<string, unknown>);
        return json({ turn_id: `t${posts.length}`, conversation_id: "c7" }, 202);
      }
      if (/\/t\d+\/stream$/.test(url)) return sse(FIGURE_ANSWER);
      return undefined;
    }, me);
  }

  it("opens Ask beside the dashboard and answers with the dashboard's filters, keeping the dashboard usable", async () => {
    const posts: Record<string, unknown>[] = [];
    serveAsk(posts);
    renderAt("/dashboards/d1");
    await userEvent.click(await screen.findByRole("button", { name: "Filter by Region" }));
    await userEvent.click(await screen.findByRole("checkbox", { name: "KSA" }));
    await userEvent.keyboard("{Escape}");
    await userEvent.click(screen.getByRole("button", { name: "Ask Tela about this dashboard" }));
    const panel = await screen.findByRole("dialog", { name: "Ask about this dashboard" });
    expect(within(panel).getByText("1 dashboard filter applied")).toBeInTheDocument();
    const box = within(panel).getByRole("textbox", { name: "Your question" });
    expect(box).toHaveFocus();
    await userEvent.type(box, "How many open projects?{Enter}");
    expect(await within(panel).findByRole("heading", { name: "24 open projects in your scope." })).toBeInTheDocument();
    expect(posts[0]).toEqual({ question: "How many open projects?", conversation_id: null, connection_id: "c1",
      context: { dashboard_id: "d1", dashboard_version_id: "dv1", page_filters: [{ ref: "dimension:region", op: "=", values: ["KSA"] }] } });
    expect(within(panel).getByRole("link", { name: "Open in Ask" })).toHaveAttribute("href", "/ask?c=c7");
    // Not modal: the dashboard behind still works, and the panel stays open.
    expect(screen.getByRole("tab", { name: "Finance" })).toBeVisible();
    await userEvent.click(screen.getByRole("tab", { name: "Finance" }));
    expect(screen.getByRole("dialog", { name: "Ask about this dashboard" })).toBeInTheDocument();
    // A follow-up stays in the conversation; New conversation clears it.
    await userEvent.type(within(panel).getByRole("textbox", { name: "Your question" }), "By region{Enter}");
    await waitFor(() => expect(posts[1]).toMatchObject({ question: "By region", conversation_id: "c7" }));
    await userEvent.click(within(panel).getByRole("button", { name: "New conversation" }));
    expect(within(panel).queryByText("How many open projects?")).not.toBeInTheDocument();
    // Closing brings the launcher back.
    await userEvent.keyboard("{Escape}");
    expect(await screen.findByRole("button", { name: "Ask Tela about this dashboard" })).toBeInTheDocument();
  });

  it("asks about one widget from its menu or a suggestion, in the panel", async () => {
    const posts: Record<string, unknown>[] = [];
    serveAsk(posts);
    renderAt("/dashboards/d1");
    // w3's run is a figure, so its card becomes a KPI card once the run is in; the menu is on every card (EXEC-214).
    await userEvent.click(await screen.findByRole("button", { name: "More actions for Open projects by region" }));
    await userEvent.click(await screen.findByRole("menuitem", { name: "Ask about this" }));
    const panel = await screen.findByRole("dialog", { name: "Ask about this dashboard" });
    expect(within(panel).getByText("About the widget “Open projects by region”")).toBeInTheDocument();
    expect(within(panel).getByRole("textbox", { name: "Your question" })).toHaveValue("Why is this so high?");
    await userEvent.click(within(panel).getByRole("button", { name: "Ask" }));
    await waitFor(() => expect(posts[0]).toMatchObject({ question: "Why is this so high?", context: { dashboard_id: "d1", widget_id: "w3" } }));
    await userEvent.click(within(panel).getByRole("button", { name: "Stop asking about this widget" }));
    await userEvent.click(within(panel).getByRole("button", { name: "New conversation" }));
    // Suggested widgets (hidden ones left out) before the first question.
    const group = within(panel).getByRole("group", { name: "Ask about a widget" });
    expect(within(group).getAllByRole("button").map((b) => b.textContent)).toEqual(["Open projects", "Open projects by region"]);
    await userEvent.click(within(group).getByRole("button", { name: "Open projects" }));
    expect(within(panel).getByText("About the widget “Open projects”")).toBeInTheDocument();
  });

  it("has no Ask without ask.use", async () => {
    serveAsk([], { ...viewer, permissions: viewer.permissions.filter((p) => p !== "ask.use") });
    renderAt("/dashboards/d1");
    await screen.findByRole("article", { name: "Open projects by region" });
    expect(screen.queryByRole("button", { name: /about this dashboard/ })).not.toBeInTheDocument();
  });
});
