import { QueryClient } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Me } from "../api/types";
import { parseBlock } from "../api/sse";
import { AppRoutes, Providers } from "../app/App";
import { TestAuthProvider } from "../app/auth";
import { setConfigForTests } from "../app/config";
import { toOption } from "../components/Chart";
import { formatValue } from "../components/format";

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

type Handler = (url: string, init?: RequestInit) => Response | undefined;

function serve(handler: Handler) {
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    if (url.endsWith("/api/v1/me")) return json(viewer);
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

beforeEach(() => setConfigForTests({ apiBaseUrl: "http://api.test", oidcAuthority: "http://kc", oidcClientId: "tela-web" }));
afterEach(() => vi.unstubAllGlobals());

describe("helpers", () => {
  it("parses SSE blocks and formats figures", () => {
    expect(parseBlock('id: 7\nevent: plan\ndata: {"a": 1}')).toEqual({ id: "7", event: "plan", data: { a: 1 } });
    expect(parseBlock(": heartbeat")).toBeNull();
    expect(formatValue(1234567, "currency")).toBe("$1,234,567");
    expect(formatValue(-7.877, "percent")).toBe("-7.88%");
  });

  it("maps a ranked-bar chart spec to horizontal bars", () => {
    const cols = [{ name: "region", kind: "dimension", label: "Region", ref: "", data_type: "text", format: null },
      { name: "n", kind: "measure", label: "N", ref: "", data_type: "integer", format: "integer" }] as const;
    const opt = toOption({ type: "ranked_bar", x: "region", series: ["n"] }, [...cols], [["KSA", 4], ["DPO", 2]]) as { yAxis: { data: string[] }; series: { data: number[] }[] };
    expect(opt.yAxis.data).toEqual(["KSA", "DPO"]);
    expect(opt.series[0]!.data.map((d) => (typeof d === "object" ? (d as { value: number }).value : d))).toEqual([4, 2]);
  });
});

describe("home", () => {
  it("lists starter dashboards on the right shelf", async () => {
    serve((url) => url.endsWith("/api/v1/home") ? json({
      greeting: { name: "Nomsa Osei", scopes: [], as_of: "2026-10-21T06:00:00Z" },
      shelves: { starter: [{ id: "d1", type: "dashboard", title: "PMO — Projects", badges: ["certified", "starter"], version: 2, count: { tabs: 6, widgets: 27 }, last_edited_at: "" }], published_to_my_role: [], shared_with_me: [] },
      empty_text: { published_to_my_role: "Nothing published to your role yet.", shared_with_me: "Nothing shared." }, can_create: false,
    }) : undefined);
    renderAt("/");
    const starter = await screen.findByRole("region", { name: "Starter" });
    expect(within(starter).getByRole("link", { name: "PMO — Projects" })).toHaveAttribute("href", "/dashboards/d1");
    expect(within(starter).getByText(/6 tabs · 27 widgets/)).toBeInTheDocument();
    expect(screen.getByText("Nothing published to your role yet.")).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Mine" })).not.toBeInTheDocument();
  });
});

describe("ask", () => {
  it("streams stages, asks a clarifying question and shows a certified answer card", async () => {
    let posts = 0;
    serve((url, init) => {
      if (url.endsWith("/api/v1/ask/turns") && init?.method === "POST") return json({ turn_id: "t1", conversation_id: "c1", stream_url: "/api/v1/ask/turns/t1/stream" }, 202);
      if (url.endsWith("/clarification")) { posts += 1; return json({ turn_id: "t1" }, 202); }
      if (url.endsWith("/stream")) {
        return posts === 0
          ? sse([["intent", {}], ["retrieval", {}], ["clarify", { key: "money:cost", question: "By 'cost' do you mean Forecast cost (EAC, USD) or Approved budget (USD)?", options: [{ id: "a", label: "Forecast cost (EAC, USD)", measure: "measure:forecast_cost_total" }, { id: "b", label: "Approved budget (USD)", measure: "measure:approved_budget_total" }] }]])
          : sse([["intent", {}], ["plan", { chips: [{ kind: "breakdown", label: "Region" }] }],
            ["viz", { visual: "table", alternatives: [], disabled: [], note: null, chart_spec: { type: "table", x: "region", series: ["forecast_cost_total"] } }],
            ["narrative", { headline: "KSA has the most forecast cost ($1,200 of $2,000).", sentences: [] }],
            ["complete", { turn_id: "t1", certified: true, scope_text: "projects: Own CIO", filters_text: "None", as_of: "2026-10-21T06:00:00Z", interpreted: [],
              columns: [{ name: "region", kind: "dimension", label: "Region", ref: "dimension:region", data_type: "text", format: null }, { name: "forecast_cost_total", kind: "measure", label: "Forecast cost", ref: "measure:forecast_cost_total", data_type: "decimal", format: "currency" }],
              rows: [["KSA", 1200], ["DPO", 800]], connection: "TPG", model_version: 1,
              spec: { model_version_id: "v1", connection_id: "c1", measure: { ref: "measure:forecast_cost_total" } } }]]);
      }
      return undefined;
    });
    renderAt("/ask?q=total%20cost%20by%20region");
    await userEvent.click(await screen.findByRole("button", { name: "Forecast cost (EAC, USD)" }));
    expect(await screen.findByRole("heading", { name: /KSA has the most forecast cost/ })).toBeInTheDocument();
    expect(within(screen.getByRole("article", { name: "Answer" })).getByText("Certified")).toBeInTheDocument();
    expect(await screen.findByRole("cell", { name: "$1,200" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Pin" })).not.toBeInTheDocument();
  });

  it("drops an answered clarify's buttons and says when a choice is refused", async () => {
    const clarify = { key: "reading:x", question: "I can read this more than one way. Which did you mean?",
      options: [{ id: "a", label: "Open projects", value: "reading:0" }] };
    serve((url, init) => {
      if (url.endsWith("/api/v1/ask/turns") && init?.method === "POST") return json({ turn_id: "t5", conversation_id: "c1", stream_url: "/x" }, 202);
      if (url.endsWith("/clarification")) return json({ error: { code: "conflict", message: "This question is not waiting for an answer." } }, 409);
      if (url.endsWith("/t5/stream")) return sse([["intent", {}], ["clarify", clarify]]);
      return undefined;
    });
    renderAt("/ask?q=open%20projects%20by%20status");
    await userEvent.click(await screen.findByRole("button", { name: "Open projects" }));
    expect(await screen.findByText(/not waiting for an answer\. Ask it again/)).toBeInTheDocument();
    expect(screen.queryByRole("group", { name: "Clarifying question" })).not.toBeInTheDocument();
  });

  it("replays a reopened conversation past an answered clarify", async () => {
    serve((url) => {
      if (url.endsWith("/api/v1/ask/conversations/c9")) return json({ id: "c9", turns: [{ id: "t6", question: "open projects by status" }] });
      if (url.endsWith("/api/v1/ask/conversations")) return json({ items: [] });
      if (url.endsWith("/t6/stream")) return sse([["intent", {}], ["clarify", { key: "reading:x", question: "Which did you mean?",
        options: [{ id: "a", label: "Open projects", value: "reading:0" }] }], ["intent", {}],
        ["error", { code: "scope_unresolved", message: "Could not apply your data rules to this query, so it was not run." }]]);
      return undefined;
    });
    renderAt("/ask?c=c9");
    expect(await screen.findByRole("alert")).toHaveTextContent("Could not apply your data rules");
    expect(screen.queryByRole("group", { name: "Clarifying question" })).not.toBeInTheDocument();
  });

  it("names a gap instead of guessing", async () => {
    serve((url, init) => {
      if (url.endsWith("/api/v1/ask/turns") && init?.method === "POST") return json({ turn_id: "t2", conversation_id: "c1", stream_url: "/x" }, 202);
      if (url.endsWith("/stream")) return sse([["intent", {}], ["error", { code: "unsupported_question", message: "I can't answer that from the connected data — there's no timesheet data.", missing_data: ["timesheet"] }]]);
      return undefined;
    });
    renderAt("/ask?q=team%20utilisation");
    expect(await screen.findByRole("alert")).toHaveTextContent("there's no timesheet data");
  });

  it("says what it understood and offers the closest measures when nothing matches (US-167)", async () => {
    const asked: string[] = [];
    serve((url, init) => {
      if (url.endsWith("/api/v1/ask/turns") && init?.method === "POST") {
        asked.push((JSON.parse(String(init.body)) as { question: string }).question);
        return json({ turn_id: `t${asked.length}`, conversation_id: "c1", stream_url: "/x" }, 202);
      }
      if (url.endsWith("/stream")) return sse([["intent", {}], ["error", { code: "unsupported_question", message: "I couldn't match that to a certified measure.",
        missing_data: [], examples: ["Past planning", "Open projects"], did_you_mean: { label: "Past planning", measure: "measure:baselining_in_scope" },
        understood: ["Projects"], unrecognised: ["meaning", "blank"] }]]);
      return undefined;
    });
    renderAt("/ask?q=projects%20past%20the%20plan%20stage");
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Understood: Projects");
    expect(alert).toHaveTextContent("Not recognised: meaning, blank");
    expect(within(screen.getByRole("group", { name: "Closest measures" })).getAllByRole("button").map((b) => b.textContent))
      .toEqual(["Past planning", "Open projects"]);
    await userEvent.click(within(alert).getAllByRole("button", { name: "Past planning" })[0]!);
    await vi.waitFor(() => expect(asked).toEqual(["projects past the plan stage", "Past planning"]));
  });
});

