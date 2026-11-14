import { QueryClient } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Me } from "../api/types";
import { AppRoutes, Providers } from "../app/App";
import { TestAuthProvider } from "../app/auth";
import { setConfigForTests } from "../app/config";
import type { TurnComplete } from "../api/app";
import { AnswerPanel } from "../features/ask/AnswerPanel";

vi.mock("echarts/core", () => ({
  use: vi.fn(),
  registerTheme: vi.fn(),
  init: () => ({ setOption: vi.fn(), on: vi.fn(), resize: vi.fn(), dispose: vi.fn() }),
}));

/** Direct SQL answers in Ask (ENH-028 US-257b, US-260): Custom SQL badge, the SQL shown, open in the Custom SQL editor, and every answer action. */
const creator: Me = {
  user_id: "u1", display_name: "Chen Creator", email: null, tenant_id: "t1", roles: ["creator"], level: "creator", audience_roles: [],
  permissions: ["dashboards.view", "ask.use", "widgets.create", "customsql.use"], scope_summary: [], locale: null, time_zone: null,
};
const SQL = "SELECT name, status FROM projects WHERE status = 'Open'";

function serve(handler: (url: string, init?: RequestInit) => Response | undefined) {
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    if (url.endsWith("/api/v1/me")) return json(creator);
    if (url.includes("/api/v1/notifications")) return json({ items: [], unread_count: 0, next_cursor: null, kinds: [] });
    return handler(url, init) ?? json({ error: { code: "not_found", message: "Not found" } }, 404);
  }));
}
function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}
function sse(events: [string, unknown][]) {
  const text = events.map(([e, d], i) => `id: ${i + 1}\nevent: ${e}\ndata: ${JSON.stringify(d)}\n\n`).join("");
  return new Response(text, { status: 200, headers: { "Content-Type": "text/event-stream" } });
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
const COMPLETE = {
  turn_id: "t1", ask_mode: "direct_sql", status: "ok", shape: "list_of_records", sql: SQL, badge: "custom_sql", badges: ["custom_sql"],
  certified: false, publishable: false, can_pin: true, can_export: false, repaired: true,
  publish_note: "Direct SQL answers carry the Custom SQL badge and can't go on a published dashboard until certified.",
  conditions: [{ term: "Open projects", sql: "status = 'Open'", rule: "Status is Open" }],
  columns: [{ name: "name", label: "Name", kind: "dimension" }, { name: "status", label: "Status", kind: "dimension" }],
  rows: [["Apollo", "Open"], ["Borealis", "Open"]], row_count: 2, truncated: false, as_of: "2026-11-12T05:00:00Z",
  scope_text: "Your rows only", connection: "TPG extract", connection_id: "c9", model_version: 3,
};

beforeEach(() => setConfigForTests({ apiBaseUrl: "http://api.test", oidcAuthority: "http://kc", oidcClientId: "tela-web" }));
afterEach(() => vi.unstubAllGlobals());

describe("Direct SQL answers", () => {
  it("shows the Custom SQL badge, the SQL and the rows, offers Pin and feedback, and opens the SQL in the editor", async () => {
    serve((url, init) => {
      if (url.endsWith("/api/v1/ask/turns") && init?.method === "POST") return json({ turn_id: "t1", conversation_id: "c1", stream_url: "/api/v1/ask/turns/t1/stream" }, 202);
      if (url.endsWith("/t1/stream")) return sse([["intent", { ask_mode: "direct_sql" }], ["plan", { planner: "direct_sql", certified: false, sql: SQL }],
        ["narrative", { headline: "The open projects.", sentences: [] }], ["complete", COMPLETE]]);
      if (url.endsWith("/api/v1/home")) return json({ greeting: { scopes: [{ connection_id: "c9", connection: "TPG extract" }] } });
      if (url.includes("/api/v1/query/catalog")) return json({ engine: "postgres", entities: [] });
      return undefined;
    });
    renderAt("/ask?q=list%20the%20open%20projects");
    const card = await screen.findByRole("article", { name: "Answer" });
    expect(within(card).getByText("Custom SQL · not certified")).toBeInTheDocument();
    expect(within(card).queryByText("Certified")).not.toBeInTheDocument();
    expect(within(card).getByLabelText("The SQL")).toHaveTextContent(SQL);
    expect(within(card).getByText("Apollo")).toBeInTheDocument();
    expect(within(card).getByText(/can't go on a published dashboard until certified/)).toBeInTheDocument();
    expect(within(card).getByText("Agreed terms used: Open projects")).toBeInTheDocument();
    expect(within(card).getByText("SQL repaired after a failed check")).toBeInTheDocument();
    expect(within(card).getByRole("button", { name: "Pin" })).toBeInTheDocument();
    expect(within(card).getByRole("button", { name: "Drill to records" })).toBeInTheDocument();
    expect(within(card).getByRole("button", { name: "Save as gold example" })).toBeInTheDocument();
    expect(within(card).getByRole("group", { name: "Rate this answer" })).toBeInTheDocument();
    expect(within(card).getByRole("button", { name: "This isn't what I asked" })).toBeInTheDocument();

    await userEvent.click(within(card).getByRole("link", { name: "Open in Custom SQL" }));
    expect(window.location.pathname).toBe("/sql/new");
  });

  it("draws the chart, switches it from Suggestions, pins it as a Custom SQL widget, drills and refines by follow-up and suggests a gold example (US-260)", async () => {
    const BY_REGION = {
      ...COMPLETE, shape: "breakdown", sql: "SELECT region, count(*) AS n FROM projects GROUP BY region",
      columns: [{ name: "region", label: "Region", kind: "dimension" }, { name: "n", label: "Projects", kind: "measure", format: "integer" }],
      rows: [["KSA", 4], ["UAE", 2]], row_count: 2,
    };
    const bar = { type: "ranked_bar", x: "region", series: ["n"], labels: { region: "Region", n: "Projects" } };
    const viz = { visual: "ranked_bar", alternatives: ["donut", "table"], disabled: [], note: null, chart_spec: bar,
      visuals: { suitable: ["ranked_bar", "donut", "table"], disabled: [], recommended: "ranked_bar" },
      charts: { ranked_bar: bar, donut: { ...bar, type: "donut" }, table: { type: "table", labels: bar.labels } } };
    const asked: string[] = [];
    const calls: { url: string; body: unknown }[] = [];
    serve((url, init) => {
      const body = init?.body ? JSON.parse(String(init.body)) as Record<string, unknown> : undefined;
      if (init?.method === "POST") calls.push({ url, body });
      if (url.endsWith("/api/v1/ask/turns") && init?.method === "POST") {
        asked.push(String(body!.question));
        const id = `t${asked.length + 10}`;
        return json({ turn_id: id, conversation_id: "c1", stream_url: `/api/v1/ask/turns/${id}/stream` }, 202);
      }
      if (url.endsWith("/t11/stream")) return sse([["intent", { ask_mode: "direct_sql" }], ["viz", viz],
        ["narrative", { headline: "Projects by region.", sentences: [] }], ["complete", { ...BY_REGION, turn_id: "t11" }]]);
      if (/\/t1[2-9]\/stream$/.test(url)) return sse([["intent", { ask_mode: "direct_sql" }]]);
      if (url.endsWith("/feedback")) return json({ feedback: null });
      if (url.endsWith("/api/v1/reports")) return json({ items: [{ id: "r1", title: "PMO weekly", mine: true }] });
      if (url.endsWith("/api/v1/custom-sql/check")) return json({ ok: true, checks: [], check_token: "token-123456", warnings: [], follows_page_filters: false });
      if (url.endsWith("/api/v1/widgets/custom-sql")) return json({ widget_id: "w1", custom_sql_id: "d1", badges: ["custom_sql"] }, 201);
      if (url.endsWith("/api/v1/knowledge/curation/from-answer")) return json({ proposal_id: "p1", status: "pending" }, 201);
      if (url.endsWith("/api/v1/home")) return json({ greeting: { scopes: [{ connection_id: "c9", connection: "TPG extract" }] } });
      if (url.includes("/api/v1/query/catalog")) return json({ engine: "postgres", entities: [] });
      return undefined;
    });
    renderAt("/ask?q=projects%20by%20region");
    const card = await screen.findByRole("article", { name: "Answer" });
    expect(within(card).getByRole("button", { name: "Show as table" })).toBeInTheDocument();

    await userEvent.click(within(card).getByRole("button", { name: "Suggestions" }));
    await userEvent.click(await screen.findByRole("menuitem", { name: "Donut" }));

    await userEvent.click(within(card).getByRole("button", { name: "Pin" }));
    const dialog = await screen.findByRole("dialog", { name: "Pin to a report" });
    expect(within(dialog).getByLabelText("Visual")).toHaveValue("donut");
    await screen.findByRole("option", { name: "PMO weekly" });
    await userEvent.selectOptions(within(dialog).getByLabelText("Report"), "r1");
    await userEvent.click(within(dialog).getByRole("button", { name: "Pin" }));
    expect(await within(dialog).findByText(/Pinned as a live Custom SQL widget/)).toBeInTheDocument();
    const saved = calls.find((x) => x.url.endsWith("/widgets/custom-sql"))!.body as Record<string, unknown>;
    expect(saved).toMatchObject({ connection_id: "c9", sql: BY_REGION.sql, check_token: "token-123456", visual: "donut", report_id: "r1", drafted_by_ai: true });
    await userEvent.click(within(dialog).getByRole("button", { name: "Done" }));

    await userEvent.click(within(card).getByRole("button", { name: "Save as gold example" }));
    expect(await within(card).findByRole("button", { name: "Suggested as gold example" })).toBeDisabled();
    expect(calls.find((x) => x.url.endsWith("/curation/from-answer"))!.body).toEqual({ turn_id: "t11" });

    // Quick refinements sit under the latest answer only (US-261); the actions stay on every answer.
    await userEvent.click(within(card).getByRole("button", { name: "Top 5" }));
    await waitFor(() => expect(within(card).queryByRole("button", { name: "Top 5" })).toBeNull());
    await userEvent.click(within(card).getByRole("button", { name: "Drill to records" }));
    expect(asked).toEqual(["projects by region", "Top 5", "List the records behind: projects by region"]);
    const followUps = calls.filter((x) => x.url.endsWith("/api/v1/ask/turns")).slice(1).map((x) => x.body as Record<string, unknown>);
    expect(followUps.every((b) => b.conversation_id === "c1" && b.connection_id === "c9")).toBe(true);
  });

  it("shows the SQL a refusal was given for", async () => {
    serve((url, init) => {
      if (url.endsWith("/api/v1/ask/turns") && init?.method === "POST") return json({ turn_id: "t2", conversation_id: "c1", stream_url: "/api/v1/ask/turns/t2/stream" }, 202);
      if (url.endsWith("/t2/stream")) return sse([["intent", { ask_mode: "direct_sql" }], ["error", {
        code: "direct_sql_refused", message: "The SQL written for this question failed the check: Only SELECT statements are allowed.",
        sql: "DELETE FROM projects", reason: "check_failed", badge: "custom_sql" }]]);
      return undefined;
    });
    renderAt("/ask?q=delete%20the%20old%20projects");
    expect(await screen.findByText(/failed the check/)).toBeInTheDocument();
    expect(screen.getByLabelText("The SQL that was refused")).toHaveTextContent("DELETE FROM projects");
  });

  it("tells the side panel it was answered with Direct SQL and which agreed terms it used", () => {
    render(<AnswerPanel turns={[{ id: "t1", question: "Open projects", stages: [], chips: [], complete: COMPLETE as unknown as TurnComplete }]} />);
    const panel = screen.getByRole("region", { name: "How this was answered" });
    expect(within(panel).getByText("Direct SQL")).toBeInTheDocument();
    expect(within(panel).getByText("Open projects")).toBeInTheDocument();
    expect(within(panel).getByText(/Not certified: the model wrote the SQL/)).toBeInTheDocument();
  });

  it("says which filter values the data doesn't hold when no rows match (run 01a1ce59)", async () => {
    const empty = { ...COMPLETE, repaired: false, rows: [], row_count: 0, value_notes: ["tpg_totalactual_state has no 1, 2, 3; its values include 0."] };
    serve((url, init) => {
      if (url.endsWith("/api/v1/ask/turns") && init?.method === "POST") return json({ turn_id: "t3", conversation_id: "c1", stream_url: "/api/v1/ask/turns/t3/stream" }, 202);
      if (url.endsWith("/t3/stream")) return sse([["intent", { ask_mode: "direct_sql" }], ["complete", { ...empty, turn_id: "t3" }]]);
      if (url.endsWith("/api/v1/home")) return json({ greeting: { scopes: [{ connection_id: "c9", connection: "TPG extract" }] } });
      return undefined;
    });
    renderAt("/ask?q=projects%20by%20colour");
    const card = await screen.findByRole("article", { name: "Answer" });
    expect(within(card).getByText("No rows match.")).toBeInTheDocument();
    const note = within(card).getByRole("note", { name: "Filter values not in the data" });
    expect(note).toHaveTextContent("tpg_totalactual_state has no 1, 2, 3; its values include 0.");
  });
});
