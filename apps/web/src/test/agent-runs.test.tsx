import { QueryClient } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Me } from "../api/types";
import { AppRoutes, Providers } from "../app/App";
import { TestAuthProvider } from "../app/auth";
import { setConfigForTests } from "../app/config";
import type { AgentRun, AgentStep } from "../features/admin/ai/AgentRunsPage";

vi.mock("echarts/core", () => ({
  use: vi.fn(),
  registerTheme: vi.fn(),
  init: () => ({ setOption: vi.fn(), on: vi.fn(), resize: vi.fn(), dispose: vi.fn() }),
}));

const admin: Me = {
  user_id: "u1", display_name: "Ada Admin", email: null, tenant_id: "t1", roles: ["admin"], level: "admin", audience_roles: [],
  permissions: ["dashboards.view", "settings.manage", "audit.read"],
  scope_summary: [], locale: null, time_zone: null,
};

type Handler = (url: string, init?: RequestInit) => Response | undefined;
const calls: { url: string; method: string; body: unknown }[] = [];

function serve(handler: Handler) {
  calls.length = 0;
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ url, method: init?.method ?? "GET", body: init?.body ? JSON.parse(String(init.body)) : undefined });
    if (url.endsWith("/api/v1/me")) return json(admin);
    return handler(url, init) ?? (url.includes("/api/v1/notifications") ? json({ items: [], unread_count: 0, next_cursor: null, kinds: [] }) : json({ error: { code: "not_found", message: "Not found" } }, 404));
  }));
}

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
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

const RUN: AgentRun = {
  id: "r1", kind: "ask", title: "where are we slipping", agents: ["AG-01", "AG-03", "AG-08"], turn_id: "t9", object_ref: null,
  user: { id: "u1", name: "Ada Admin" }, outcome: "ok", llm_calls: 1, prompt_tokens: 40, completion_tokens: 9,
  started_at: "2026-11-05T09:00:00Z", ended_at: "2026-11-05T09:00:02Z", duration_ms: 2100, text_kept: true,
};
const step = (s: Partial<AgentStep>): AgentStep => ({
  seq: 1, agent: null, kind: "decision", label: "", status: "ok", started_at: RUN.started_at, duration_ms: 5, detail: {},
  task: null, provider: null, model: null, prompt_tokens: null, completion_tokens: null, has_text: false, text_cleared: false,
  text_cut: false, ...s,
});
const STEPS = [
  step({ seq: 1, agent: "AG-01", label: "Routed the question: ask", detail: { intent: "ask" } }),
  step({ seq: 2, agent: "AG-03", kind: "rule", label: "Rules planner: unsupported", detail: { status: "unsupported" } }),
  step({ seq: 3, agent: "AG-03", kind: "llm_call", label: "AG-03 plan", task: "plan", provider: "ollama", model: "qwen3:8b",
    prompt_tokens: 40, completion_tokens: 9, has_text: true, duration_ms: 1800 }),
  step({ seq: 4, kind: "done", label: "Answered" }),
];

describe("Agent runs (ENH-024)", () => {
  it("lists runs, opens one with its steps, and shows the prompt and raw reply of a model call on demand", async () => {
    serve((url, init) => {
      if (url.includes("/agent-runs/settings")) {
        if (init?.method === "PUT") return json({ ...JSON.parse(String(init.body)), run_days: 90 });
        return json({ capture_text: true, text_days: 14, run_days: 90 });
      }
      if (url.includes("/agent-runs/facets")) return json({ agent: { "AG-03": 1 }, kind: { ask: 1 }, outcome: { ok: 1 } });
      if (url.includes("/agent-runs/r1/steps/3/text")) {
        return json({ seq: 3, prompt: [{ role: "system", content: "You are AG-03." }, { role: "user", content: "where are we slipping" }],
          reply: '{"measure":"measure:schedule_risk"}', cut: false, cleared_at: null, model: "qwen3:8b" });
      }
      if (url.includes("/agent-runs/r1")) return json({ ...RUN, steps: STEPS, agent_names: {}, related: [] });
      if (url.includes("/api/v1/admin/agent-runs?")) return json({ items: [RUN], next_cursor: null, agents: {} });
      return undefined;
    });
    renderAt("/admin/agent-runs");
    expect(await screen.findByRole("heading", { name: "Agent runs" })).toBeInTheDocument();
    expect(await screen.findByText("where are we slipping")).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "AG-03 Query planner (1)" })).toBeInTheDocument();

    await userEvent.selectOptions(screen.getByLabelText("Agent"), "AG-03");
    expect(window.location.search).toBe("?agent=AG-03");
    await waitFor(() => expect(calls.some((c) => c.url.includes("/admin/agent-runs?agent=AG-03"))).toBe(true));

    await userEvent.click(await screen.findByText("where are we slipping"));
    const steps = await screen.findByRole("list", { name: "Steps" });
    expect(within(steps).getAllByRole("listitem")).toHaveLength(4);
    expect(within(steps).getByText("Routed the question: ask")).toBeInTheDocument();
    expect(within(steps).getByText("ollama · qwen3:8b")).toBeInTheDocument();
    expect(calls.some((c) => c.url.includes("/steps/3/text"))).toBe(false); // text is fetched only when asked (audited)

    await userEvent.click(within(steps).getByRole("button", { name: "Prompt and reply" }));
    expect(await screen.findByText("You are AG-03.")).toBeInTheDocument();
    expect(screen.getByText(/"measure": "measure:schedule_risk"/)).toBeInTheDocument(); // pretty-printed reply

    await userEvent.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("list", { name: "Steps" })).not.toBeInTheDocument());
    await userEvent.click(screen.getByLabelText("Keep what was sent to the model and its replies"));
    await waitFor(() => expect(calls.find((c) => c.method === "PUT")?.body).toEqual({ capture_text: false, text_days: 14 }));
  });

  it("links an Ask answer's runs through the turn filter", async () => {
    serve((url) => {
      if (url.includes("/agent-runs/settings")) return json({ capture_text: true, text_days: 14, run_days: 90 });
      if (url.includes("/agent-runs/facets")) return json({ agent: {}, kind: {}, outcome: {} });
      if (url.includes("/api/v1/admin/agent-runs?")) return json({ items: [{ ...RUN, outcome: "running", ended_at: null, duration_ms: null }], next_cursor: null, agents: {} });
      return undefined;
    });
    renderAt("/admin/agent-runs?turn_id=t9");
    expect(await screen.findByText("Running")).toBeInTheDocument();
    await userEvent.click(screen.getByLabelText("Live"));
    expect(screen.getByText("· refreshes every 3 s")).toBeInTheDocument();
    expect(calls.some((c) => c.url.includes("/admin/agent-runs?turn_id=t9"))).toBe(true);
    expect(screen.getAllByRole("button", { name: /Answer/ }).length).toBeGreaterThan(0);
    expect(screen.getAllByRole("button", { name: /Answer/ }).some((b) => b.textContent?.includes("t9"))).toBe(true);
  });
});
