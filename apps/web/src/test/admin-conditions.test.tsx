import { QueryClient } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { ConditionList, NamedCondition } from "../api/knowledge";
import type { Me } from "../api/types";
import { AppRoutes, Providers } from "../app/App";
import { TestAuthProvider } from "../app/auth";
import { setConfigForTests } from "../app/config";

vi.mock("echarts/core", () => ({
  use: vi.fn(),
  registerTheme: vi.fn(),
  init: () => ({ setOption: vi.fn(), on: vi.fn(), resize: vi.fn(), dispose: vi.fn() }),
}));

const admin: Me = { tenant_id: "t1", audience_roles: [], scope_summary: [], locale: null, time_zone: null, email: null,
  user_id: "u1", display_name: "Ada Admin", roles: ["admin"], level: "admin",
  permissions: ["dashboards.view", "knowledge.manage", "widgets.create", "connections.manage", "ask.use", "model.approve"] };

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

const base = { connection: { id: "c1", name: "TPG MySQL" }, model_version: { id: "v1", version_no: 2 }, synonyms: [], gaps: [],
  decided_by: null, decided_at: null, note: null, created_at: "2026-11-12T08:00:00Z", updated_at: "2026-11-12T08:00:00Z" };
const HELD: NamedCondition = { ...base, id: "n1", status: "approved", term: "Open project", name: "open", entity: "projects",
  synonyms: ["Open projects"], condition: { field: "current_state", op: "in", values: ["Project Created", "Active", "On Hold"] },
  rule_text: "Projects where Current state is one of Project Created, Active, On Hold", source: "document",
  measure: { ref: "measure:open_projects", label: "Open projects" },
  conflict: { condition: { rule: "Projects where Current state is one of Project Created, Active, On Hold", figure: 431 },
    measure: { ref: "measure:open_projects", label: "Open projects", rule: "Status is Active", figure: 420 }, checked_at: "2026-11-12T08:01:00Z" },
  prefer: null, uses: "measure", definition: { id: "k1", document: { id: "d1", title: "PMO definitions" } } };
const LATE: NamedCondition = { ...base, id: "n2", status: "approved", term: "Overdue go-lives", name: "overdue go lives", entity: "projects",
  condition: { field: "current_state", op: "=", value: "Active" }, rule_text: "Projects where Current state is Active", source: "pack_measure",
  measure: { ref: "measure:overdue_go_lives", label: "Overdue go-lives" }, conflict: null, prefer: null, uses: "measure", definition: null };
const LIST: ConditionList = { items: [HELD, LATE], counts: { proposed: 0, approved: 2, conflicts: 1 } };

function routes(url: string, init?: RequestInit): Response | undefined {
  if (url.includes("/api/v1/knowledge/conditions?status=")) return json(LIST);
  if (url.endsWith("/conditions/n1/choose") && init?.method === "POST") return json({ ...HELD, prefer: "condition", uses: "condition" });
  if (url.endsWith("/conditions/n1/check") && init?.method === "POST") return json(HELD);
  return undefined;
}

beforeEach(() => setConfigForTests({ apiBaseUrl: "http://api.test", oidcAuthority: "http://kc", oidcClientId: "tela-web" }));
afterEach(() => vi.unstubAllGlobals());

describe("Admin → Knowledge → Conditions (US-252)", () => {
  it("lists each term once with both rules and counts where they differ", async () => {
    serve(routes);
    renderAt("/admin/knowledge/conditions");
    const table = await screen.findByRole("table", { name: "Named conditions" });
    expect(screen.getByRole("link", { name: "Conditions" })).toHaveAttribute("aria-current", "page");
    expect(await within(table).findByText("This condition: 431 rows")).toBeInTheDocument();
    expect(within(table).getByText("Open projects: 420 rows")).toBeInTheDocument();
    expect(within(table).getByText("Status is Active")).toBeInTheDocument();
    expect(within(table).getByText("Until you choose, Ask uses the measure and says the two differ.")).toBeInTheDocument();
    expect(within(table).getByText("“open” on projects · TPG MySQL")).toBeInTheDocument();
    expect(within(table).getByText("Certified measure")).toBeInTheDocument();
    expect(screen.getByLabelText("Counts")).toHaveTextContent("2 in use · 1 waiting for your choice · 0 can't run");
    // A measure's own condition has nothing to choose.
    expect(within(table).queryByRole("button", { name: "Use the condition for Overdue go-lives" })).toBeNull();
    await userEvent.click(screen.getByLabelText("Only those that differ from a measure"));
    await waitFor(() => expect(calls.some((c) => c.url.includes("conflicts=true"))).toBe(true));
  });

  it("chooses the condition and counts again", async () => {
    serve(routes);
    renderAt("/admin/knowledge/conditions");
    const table = await screen.findByRole("table", { name: "Named conditions" });
    await userEvent.click(await within(table).findByRole("button", { name: "Use the condition for Open project" }));
    await waitFor(() => expect(calls.find((c) => c.url.endsWith("/n1/choose"))?.body).toEqual({ use: "condition" }));
    await userEvent.click(within(table).getByRole("button", { name: "Count Open project" }));
    await waitFor(() => expect(calls.some((c) => c.url.endsWith("/n1/check") && c.method === "POST")).toBe(true));
  });
});
