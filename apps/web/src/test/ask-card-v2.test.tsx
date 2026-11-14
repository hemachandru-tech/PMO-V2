import { QueryClient } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Me } from "../api/types";
import { AppRoutes, Providers } from "../app/App";
import { TestAuthProvider } from "../app/auth";
import { setConfigForTests } from "../app/config";
import { toOption } from "../components/Chart";

vi.mock("echarts/core", () => ({
  use: vi.fn(),
  registerTheme: vi.fn(),
  init: () => ({ setOption: vi.fn(), on: vi.fn(), resize: vi.fn(), dispose: vi.fn() }),
}));

const base = { tenant_id: "t1", audience_roles: [], scope_summary: [], locale: null, time_zone: null, email: null };
const creator: Me = { ...base, user_id: "u2", display_name: "Cleo Creator", roles: ["creator"], level: "creator",
  permissions: ["dashboards.view", "ask.use", "drill.use", "definitions.view", "widgets.create"] };
const viewer: Me = { ...base, user_id: "u3", display_name: "Vic Viewer", roles: ["viewer"], level: "viewer",
  permissions: ["dashboards.view", "ask.use", "definitions.view"] };

const calls: { url: string; method: string; body: unknown }[] = [];
function serve(me: Me, handler: (url: string, init?: RequestInit) => Response | undefined) {
  calls.length = 0;
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ url, method: init?.method ?? "GET", body: init?.body ? JSON.parse(String(init.body)) : undefined });
    if (url.endsWith("/api/v1/me")) return json(me);
    if (url.includes("/api/v1/notifications")) return json({ items: [], unread_count: 0 });
    if (url.endsWith("/api/v1/ask/conversations")) return json({ items: [] });
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

const SPEC = { model_version_id: "v1", connection_id: "c1", measure: { ref: "measure:open_projects" }, breakdowns: [{ ref: "dimension:region" }],
  time: { ref: "dimension:planned_deployment_date", range: { relative: "this_quarter" } } };
const COLS = [{ name: "region", kind: "dimension", label: "Region", ref: "dimension:region", data_type: "text", format: null },
  { name: "open_projects", kind: "measure", label: "Open projects", ref: "measure:open_projects", data_type: "integer", format: "integer" }];
const FACTS = [
  { id: "f1", type: "concentration", priority: 0.6, text: "The top 3 of 5 account for 90.6% of open projects (53); 2 of 5 make up 80%.",
    numbers: { items: 5, top_n: 3, top_share_pct: 90.6, total: 53 }, members: ["KSA", "GCC - UAE", "Egypt"] },
  { id: "f2", type: "data_note", priority: 0.4, text: "5 of 58 (8.6%) has no region (Not set).", numbers: { value: 5, total: 58, share_pct: 8.6 }, members: ["Not set"] },
  { id: "f3", type: "driver", priority: 0.5, text: "KSA drove most of the change in this period vs Q2: +6 of the +5 total (120%).",
    numbers: { total_delta: 5, contributions: [{ member: "KSA", delta: 6, share_pct: 120 }, { member: "Egypt", delta: -1, share_pct: -20 }], period: "this period", previous_period: "Q2" },
    members: ["KSA", "Egypt"] },
];
const DRIVER = { ...FACTS[2], dimension: { ref: "dimension:portfolio", label: "Portfolio" } };
const NARRATIVE = {
  headline: "KSA has the most open projects (40 of 53).", headline_facts: ["f0"], sentences: [], source: "template", insights: FACTS,
  cited: [{ text: FACTS[0]!.text, facts: ["f1"], source: "template" }, { text: FACTS[2]!.text, facts: ["f3"], source: "template" }],
  notes: [{ text: FACTS[1]!.text, fact: "f2" }], driver: DRIVER,
  next: [{ kind: "driver", label: "Break down by portfolio", ops: [{ op: "add_breakdown", ref: "dimension:portfolio" }] },
    { kind: "compare", label: "Compare with the previous period", ops: [{ op: "compare_previous" }] },
    { kind: "drill", label: "Drill into KSA", ops: [{ op: "add_filter", ref: "dimension:region", values: ["KSA"] }] }],
};
function answer(turnId: string, narrative: Record<string, unknown> = NARRATIVE, extra: Record<string, unknown> = {}) {
  return sse([["intent", {}], ["plan", { chips: [{ kind: "breakdown", label: "Region" }] }],
    ["viz", { visual: "ranked_bar", alternatives: [], disabled: [], note: null, chart_spec: { type: "ranked_bar", x: "region", series: ["open_projects"] } }],
    ["narrative", narrative],
    ["complete", { turn_id: turnId, certified: true, scope_text: "projects: Gulf regions", filters_text: "None", as_of: null, interpreted: ["this quarter"],
      definitions: [{ ref: "measure:open_projects", label: "Open projects", plain_rule: "", blank_handling: "", rule_ref: null, format: "integer" }],
      columns: COLS, rows: [["KSA", 40], ["GCC - UAE", 8], ["Egypt", 3], ["Jordan", 2], [null, 5]], row_count: 5, connection: "TPG", model_version: 1, spec: SPEC, can_pin: true, ...extra }]]);
}

beforeEach(() => setConfigForTests({ apiBaseUrl: "http://api.test", oidcAuthority: "http://kc", oidcClientId: "tela-web" }));
afterEach(() => vi.unstubAllGlobals());

describe("answer card v2 (US-186)", () => {
  it("lays out headline, cited insights, chart, driver, data notes, Ask next and how it was worked out, in order", async () => {
    serve(creator, (url, init) => {
      if (url.endsWith("/api/v1/ask/turns") && init?.method === "POST") return json({ turn_id: "t1", conversation_id: "c1" }, 202);
      if (url.endsWith("/t1/stream")) return answer("t1");
      if (url.endsWith("/t1/refine")) return json({ turn_id: "t2", conversation_id: "c1" }, 202);
      if (url.endsWith("/t2/stream")) return answer("t2");
      if (url.endsWith("/api/v1/query/explain")) return json({ sql: "SELECT region, count(*) FROM projects", dialect: "postgres", scope: [{ entity: "projects", predicate: "region IN ('KSA')" }] });
      return undefined;
    });
    renderAt("/ask?q=open%20projects%20by%20region");
    const card = await screen.findByRole("article", { name: "Answer" });
    const heading = within(card).getByRole("heading", { level: 2, name: /KSA has the most open projects/ });
    const insights = within(card).getByRole("region", { name: "Insights" });
    const driving = within(card).getByRole("region", { name: "What's driving this" });
    const notes = within(card).getByRole("region", { name: "Data notes" });
    const next = within(card).getByRole("region", { name: "Ask next" });
    const chart = within(card).getByRole("img", { name: /KSA has the most/ });
    const order = [heading, insights, chart, driving, notes, next];
    for (let i = 1; i < order.length; i++) {
      expect(order[i - 1]!.compareDocumentPosition(order[i]!) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    }
    // The insights are the cited sentences; data notes are listed apart, never as an insight.
    expect(within(insights).getAllByRole("listitem")).toHaveLength(2);
    expect(within(insights).queryByText(/has no region/)).toBeNull();
    expect(within(notes).getByText(/has no region \(Not set\)/)).toBeInTheDocument();
    // "Why?" opens the fact behind the sentence, with its numbers.
    await userEvent.click(within(insights).getAllByRole("button", { name: /^Why\?/ })[0]!);
    expect(within(insights).getByText("Top share %")).toBeInTheDocument();
    expect(within(insights).getByText("90.6%")).toBeInTheDocument();
    // What's driving this: ranked contributions, and the driver dimension one click away.
    const bars = within(driving).getByRole("list", { name: /Contribution to the change by portfolio/ });
    expect(within(bars).getAllByRole("listitem").map((li) => li.textContent)).toEqual(["KSA+6 (120%)", "Egypt−1 (-20%)"]);
    await userEvent.click(within(driving).getByRole("button", { name: "Break down by portfolio" }));
    await waitFor(() => expect(calls.find((c) => c.url.endsWith("/t1/refine"))?.body).toEqual({ chip_ops: [{ op: "add_breakdown", ref: "dimension:portfolio" }] }));
    // Ask next: three follow-ups applied as refinements.
    expect(within(next).getAllByRole("button").map((b) => b.textContent)).toEqual(["Break down by portfolio", "Compare with the previous period", "Drill into KSA"]);
    calls.length = 0;
    await userEvent.click(within(next).getByRole("button", { name: "Drill into KSA" }));
    await waitFor(() => expect(calls.find((c) => c.url.endsWith("/t1/refine"))?.body).toEqual({ chip_ops: [{ op: "add_filter", ref: "dimension:region", values: ["KSA"] }] }));
    // The Ask card no longer shows how it was worked out, the query or the scope footer.
    expect(within(card).queryByRole("region", { name: "How this was worked out" })).toBeNull();
    expect(within(card).queryByRole("button", { name: "Show query" })).toBeNull();
    expect(within(card).queryByText(/^Filters: /)).toBeNull();
  });

  it("shows no query to a Viewer and no empty sections for an answer without facts", async () => {
    serve(viewer, (url, init) => {
      if (url.endsWith("/api/v1/ask/turns") && init?.method === "POST") return json({ turn_id: "t1", conversation_id: "c1" }, 202);
      if (url.endsWith("/t1/stream")) return answer("t1", { headline: "KSA has the most open projects.", sentences: [] });
      return undefined;
    });
    renderAt("/ask?q=open%20projects%20by%20region");
    const card = await screen.findByRole("article", { name: "Answer" });
    await within(card).findByRole("heading", { name: /KSA has the most/ });
    for (const name of ["Insights", "What's driving this", "Data notes", "Ask next"]) {
      expect(within(card).queryByRole("region", { name })).toBeNull();
    }
    expect(within(card).queryByRole("button", { name: "Show query" })).toBeNull();
  });
});

describe("highlight-and-grey (US-186)", () => {
  it("keeps the colour of the items an insight names and mutes the rest", () => {
    const rows = [["KSA", 40], ["GCC - UAE", 8], ["Egypt", 3]];
    const opt = toOption({ type: "ranked_bar", x: "region", series: ["open_projects"] }, COLS as never, rows, undefined, { labels: ["KSA"], muted: "#999" }) as { series: { data: unknown[] }[] };
    // US-188: the highlighted bar also carries its value as a direct label
    expect(opt.series[0]!.data).toMatchObject([{ value: 40, label: { show: true } }, { value: 8, itemStyle: { color: "#999" } }, { value: 3, itemStyle: { color: "#999" } }]);
    const none = toOption({ type: "ranked_bar", x: "region", series: ["open_projects"] }, COLS as never, rows, undefined, { labels: ["Oman"], muted: "#999" }) as { series: { data: unknown[] }[] };
    expect(none.series[0]!.data).toEqual([40, 8, 3]); // nothing named on the chart: nothing muted
    const trend = toOption({ type: "trend", x: "region", series: ["open_projects"] }, COLS as never, rows, undefined, { labels: ["Egypt"], muted: "#999" }) as { series: { data: unknown[] }[] };
    expect(trend.series[0]!.data).toMatchObject([40, 8, { value: 3, symbolSize: 10, label: { show: true } }]);
  });
});
