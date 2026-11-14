import { QueryClient } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Me } from "../api/types";
import { AppRoutes, Providers } from "../app/App";
import { TestAuthProvider } from "../app/auth";
import { setConfigForTests } from "../app/config";

vi.mock("echarts/core", () => ({
  use: vi.fn(),
  registerTheme: vi.fn(),
  init: () => ({ setOption: vi.fn(), on: vi.fn(), resize: vi.fn(), dispose: vi.fn() }),
}));

/** Answers counted by a definition (ENH-017 US-204): the badge, the line, the definition's source and the certified figure beside. */
const viewer: Me = {
  user_id: "u1", display_name: "Nomsa Osei", email: null, tenant_id: "t1", roles: ["viewer"], level: "viewer",
  audience_roles: ["cio"], permissions: ["dashboards.view", "ask.use", "drill.use", "definitions.view"],
  scope_summary: [], locale: null, time_zone: null,
};

type Handler = (url: string, init?: RequestInit) => Response | undefined;
const calls: { url: string; body: unknown }[] = [];

function serve(handler: Handler) {
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ url, body: init?.body ? JSON.parse(String(init.body)) : null });
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

const COUNTED = {
  source: "definition", text: "Counted by the approved definition “Open project”: Current state in Project Created, Active, On Hold",
  rule: "Current state in Project Created, Active, On Hold",
  measure: { ref: "measure:open_projects", label: "Open projects", plain_rule: "Projects with status Active." },
  definition: { id: "d1", term: "Open project", meaning: "a project whose current state is Project Created, Active or On Hold.",
    document: { id: "doc1", title: "PMO definitions" }, chunk_id: "k1", passage: "An open project is …" },
};

beforeEach(() => {
  calls.length = 0;
  setConfigForTests({ apiBaseUrl: "http://api.test", oidcAuthority: "http://kc", oidcClientId: "tela-web" });
});
afterEach(() => vi.unstubAllGlobals());

describe("answers counted by a definition", () => {
  it("names the definition, its source and shows the certified figure beside", async () => {
    serve((url, init) => {
      if (url.endsWith("/api/v1/ask/turns") && init?.method === "POST") return json({ turn_id: "t1", conversation_id: "c1", stream_url: "/api/v1/ask/turns/t1/stream" }, 202);
      if (url.endsWith("/t1/stream")) return sse([["intent", {}], ["plan", { chips: [{ kind: "measure", label: "Projects" }], certified: true, rule: null, planner: "knowledge" }],
        ["viz", { visual: "figure", alternatives: [], disabled: [], note: null, chart_spec: { type: "figure", series: ["v"] } }],
        ["narrative", { headline: "431 projects", sentences: [{ text: COUNTED.text, cites: ["chunk:k1"] }] }],
        ["complete", { turn_id: "t1", certified: false, badge: "knowledge_definition", can_export: true, rule: null, scope_text: "All", filters_text: "Current state",
          as_of: null, interpreted: [COUNTED.text], counted_by: COUNTED,
          columns: [{ name: "v", kind: "measure", label: "Projects", ref: "measure:total_projects", data_type: "integer", format: "integer" }], rows: [[431]],
          definitions: [{ ref: "measure:total_projects", label: "Projects", plain_rule: "Every project.", blank_handling: "", rule_ref: null, format: "integer" }],
          spec: { model_version_id: "v1", connection_id: "c1", measure: { ref: "measure:total_projects" } },
          beside: { kind: "certified", label: "Open projects", plain_rule: "Projects with status Active.", value: 420, format: "integer", certified: true } }]]);
      return undefined;
    });
    renderAt("/ask?q=how%20many%20open%20projects");
    const compare = await screen.findByRole("region", { name: "Compare with certified" });
    expect(screen.getByLabelText(/^Approved definition: /)).toBeInTheDocument();
    expect(screen.queryByLabelText("Certified: Every measure used is certified")).not.toBeInTheDocument();
    expect(screen.getByText(/“Open project” means: a project whose current state/)).toHaveTextContent("from “PMO definitions”");
    const boxes = within(compare).getAllByText(/^(Certified|Approved definition)$/).map((n) => n.textContent);
    expect(boxes).toEqual(["Certified", "Approved definition"]);
    expect(compare).toHaveTextContent("420");
    expect(compare).toHaveTextContent("431");
    expect(compare).toHaveTextContent("Current state in Project Created, Active, On Hold");
  });
});

describe("a filter left out because the answer was empty (ENH-022 US-218)", () => {
  it("shows the not-certified badge and what was left out", async () => {
    const LEFT = "Left out Delivery manager: Lina Farouk: with it nothing matched";
    serve((url, init) => {
      if (url.endsWith("/api/v1/ask/turns") && init?.method === "POST") return json({ turn_id: "t2", conversation_id: "c1", stream_url: "/api/v1/ask/turns/t2/stream" }, 202);
      if (url.endsWith("/t2/stream")) return sse([["intent", {}], ["plan", { chips: [{ kind: "measure", label: "Open projects" }], certified: true, rule: null, planner: "rules" }],
        ["viz", { visual: "figure", alternatives: [], disabled: [], note: null, chart_spec: { type: "figure", series: ["v"] } }],
        ["narrative", { headline: "11 open projects", sentences: [] }],
        ["complete", { turn_id: "t2", certified: false, badge: "filter_left_out", can_export: false, rule: null, scope_text: "All", filters_text: "Region: KSA",
          as_of: null, interpreted: [LEFT],
          columns: [{ name: "v", kind: "measure", label: "Open projects", ref: "measure:open_projects", data_type: "integer", format: "integer" }], rows: [[11]],
          definitions: [], spec: { model_version_id: "v1", connection_id: "c1", measure: { ref: "measure:open_projects" } } }]]);
      return undefined;
    });
    renderAt("/ask?q=open%20projects%20in%20KSA%20for%20Lina");
    expect(await screen.findByLabelText(/^Not certified · a filter was left out: /)).toBeInTheDocument();
    expect(screen.queryByLabelText("Certified: Every measure used is certified")).not.toBeInTheDocument();
    expect(screen.getByText(LEFT)).toBeInTheDocument();
  });
});
