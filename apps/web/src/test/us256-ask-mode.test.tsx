import { QueryClient } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { AskModeInfo, ConnectionItem, TurnComplete } from "../api/app";
import type { Me } from "../api/types";
import { AppRoutes, Providers } from "../app/App";
import { TestAuthProvider } from "../app/auth";
import { setConfigForTests } from "../app/config";
import { AnswerPanel } from "../features/ask/AnswerPanel";

// US-256 (ENH-028): Connections › connection › Ask — how Ask answers for each source.

const admin: Me = {
  user_id: "u1", display_name: "Ada Admin", email: null, tenant_id: "t1", roles: ["admin"], level: "admin", audience_roles: [],
  permissions: ["dashboards.view", "ask.use", "connections.manage", "model.edit", "model.approve"], scope_summary: [], locale: null, time_zone: null,
};
const CONNECTION: ConnectionItem = {
  id: "c1", name: "TPG extract", engine: "postgres", status: "connected", descriptor: {}, secret_set: true,
  read_only: { verified_at: "2026-10-21T05:00:00Z" }, freshness: { mode: "live" }, model: { id: "m1", version_no: 4, status: "ok" },
  data_as_of: null, etag: 'W/"1"', ask_mode: "certified",
};
const INFO: AskModeInfo = {
  connection_id: "c1", connection: "TPG extract", engine: "postgres", mode: "certified", answers_with: "certified", fallback_reason: null, default: "certified",
  choices: [
    { mode: "certified", label: "Certified measures", note: "Answers only with certified measures.", default: true, unavailable: null,
      score: { run_id: "r1", run_at: "2026-11-10T08:00:00Z", plan_only: true, tags: ["exec186"], total: 88, right: 44, confident_wrong: 44, clarified: 0, refused: 0, median_ms: 1500 } },
    { mode: "fields", label: "Fields + definitions", note: "Builds each answer from the fields.", default: false, unavailable: "This answer mode isn't available yet.", score: null },
    { mode: "hybrid", label: "Hybrid", note: "Uses a certified measure when one matches.", default: false, unavailable: "This answer mode isn't available yet.", score: null },
    { mode: "direct_sql", label: "Direct SQL", note: "The model writes SQL.", default: false, unavailable: null, score: null },
  ],
};

const calls: { url: string; method: string; body: unknown }[] = [];
function serve(handler: (url: string, init?: RequestInit) => Response | undefined) {
  calls.length = 0;
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ url, method: init?.method ?? "GET", body: init?.body ? JSON.parse(String(init.body)) : undefined });
    if (url.endsWith("/api/v1/me")) return json(admin);
    if (url.includes("/api/v1/notifications")) return json({ items: [], unread_count: 0, next_cursor: null, kinds: [] });
    return handler(url, init) ?? json({ error: { code: "not_found", message: "Not found" } }, 404);
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

describe("Ask answer mode per connection (US-256)", () => {
  it("lists the four modes with their scores, keeps unbuilt ones disabled and saves the choice", async () => {
    serve((url, init) => {
      if (url.endsWith("/api/v1/connections")) return json({ items: [CONNECTION] });
      if (url.endsWith("/api/v1/connections/c1/ask-mode") && init?.method === "PUT") return json({ ...INFO, mode: "direct_sql", answers_with: "direct_sql" });
      if (url.endsWith("/api/v1/connections/c1/ask-mode")) return json(INFO);
      return undefined;
    });
    renderAt("/admin/connections");
    const table = await screen.findByRole("table", { name: "Connections" });
    const row = (await within(table).findByText("TPG extract")).closest("tr")!;
    expect(within(row).getByText("Certified measures")).toBeInTheDocument();
    await userEvent.click(within(row).getByRole("button", { name: "Ask mode" }));
    const dialog = await screen.findByRole("dialog", { name: "How Ask answers for TPG extract" });
    const group = await within(dialog).findByRole("radiogroup", { name: "Ask answer mode" });
    const radios = within(group).getAllByRole("radio");
    expect(radios).toHaveLength(4);
    expect(within(group).getByRole("radio", { name: /Certified measures/ })).toBeChecked();
    expect(within(group).getByRole("radio", { name: /Fields \+ definitions/ })).toBeDisabled();
    expect(within(group).getByRole("radio", { name: /Hybrid/ })).toBeDisabled();
    expect(within(group).getAllByText("This answer mode isn't available yet.")).toHaveLength(2);
    const [score] = within(group).getAllByTestId("ask-mode-score");
    expect(score).toHaveTextContent("44 of 88 right · 44 confidently wrong · 0 asked back · 0 refused · median 1.5 s");
    expect(within(group).getAllByText("Not tested on the gold questions yet.")).toHaveLength(3);
    const save = within(dialog).getByRole("button", { name: "Save" });
    expect(save).toBeDisabled();
    await userEvent.click(within(group).getByRole("radio", { name: /Direct SQL/ }));
    expect(within(dialog).getByText(/Direct SQL answers are never certified/)).toBeInTheDocument();
    await userEvent.click(save);
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    const put = calls.find((c) => c.method === "PUT");
    expect(put?.url).toMatch(/\/api\/v1\/connections\/c1\/ask-mode$/);
    expect(put?.body).toEqual({ mode: "direct_sql" });
  });

  it("says when the stored mode can't run and Ask answers with certified measures", async () => {
    serve((url) => {
      if (url.endsWith("/api/v1/connections")) return json({ items: [{ ...CONNECTION, ask_mode: "direct_sql" }] });
      if (url.endsWith("/api/v1/connections/c1/ask-mode")) return json({ ...INFO, mode: "direct_sql", answers_with: "certified",
        fallback_reason: "Direct SQL: Direct SQL is switched off for this installation. Answered with certified measures." });
      return undefined;
    });
    renderAt("/admin/connections");
    const row = (await within(await screen.findByRole("table", { name: "Connections" })).findByText("TPG extract")).closest("tr")!;
    expect(within(row).getByText("Direct SQL")).toBeInTheDocument();
    await userEvent.click(within(row).getByRole("button", { name: "Ask mode" }));
    const dialog = await screen.findByRole("dialog");
    expect(await within(dialog).findByText(/switched off for this installation/)).toBeInTheDocument();
  });

  it("shows the mode on How this was answered", () => {
    const complete = { spec: { measure: { ref: "open_projects" } }, columns: [], filters_text: "", connection: "TPG extract", model_version: 4,
      ask_mode: "certified" } as unknown as TurnComplete;
    render(<AnswerPanel turns={[{ id: "t1", question: "Open projects", stages: [], chips: [], complete }]} />);
    expect(screen.getByRole("region", { name: "How this was answered" })).toHaveTextContent("Answered withCertified measures");
  });
});
