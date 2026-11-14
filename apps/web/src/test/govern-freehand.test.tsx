import { QueryClient } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Me } from "../api/types";
import { AppRoutes, Providers } from "../app/App";
import { TestAuthProvider } from "../app/auth";
import { setConfigForTests } from "../app/config";

// Free-hand rules in the certification queue (ENH-008 US-166): evidence, then Approve / Ask for changes / Reject.

vi.mock("echarts/core", () => ({
  use: vi.fn(),
  registerTheme: vi.fn(),
  init: () => ({ setOption: vi.fn(), on: vi.fn(), resize: vi.fn(), dispose: vi.fn() }),
}));

const admin: Me = {
  user_id: "a1", display_name: "Ada Admin", email: null, tenant_id: "t1", roles: ["admin"], level: "admin", audience_roles: [],
  permissions: ["dashboards.view", "ask.use", "certification.decide", "model.approve", "widgets.create", "reports.create"],
  scope_summary: [], locale: null, time_zone: null,
};

type Call = { url: string; method: string; body: unknown };
let calls: Call[] = [];

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

function serve(handler: (url: string, init?: RequestInit) => Response | undefined) {
  calls = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ url, method: init?.method ?? "GET", body: init?.body ? JSON.parse(String(init.body)) : undefined });
    if (url.endsWith("/api/v1/me")) return json(admin);
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

const QUESTION = "How many projects without a budget code and whose planned deployment date is before today?";
const ITEM = {
  request_id: "r9", kind: "freehand", item_id: "f9", name: "Projects matching your rule", author: { id: "u2", name: "Cleo Creator" },
  submitted_at: "2026-10-28T06:00:00Z", age_days: 0, reason: "a free-hand answer added as a widget", trigger: "manual", note: null,
  blocks_publish: false, usage: { widgets: 1, reports: 0, dashboards: 0 }, pending_publishes: [], lookalikes: 0, dependency_pending: false,
  changed_since_requested: false, status: "pending", connection_id: "c1", question: QUESTION,
};
const PLAIN = "Count of projects where Budget code is blank and Planned deployment date is before today.";
const DETAIL = {
  request_id: "r9", status: "pending", kind: "freehand", item_id: "f9", name: "Projects matching your rule", author: { id: "u2", name: "Cleo Creator" },
  trigger: "manual", note: null, submitted_at: "2026-10-28T06:00:00Z", changed_since_requested: false, question: QUESTION,
  decisions: ["certify", "request_changes", "reject"],
  rule: { plain: PLAIN, rephrased: null, blank_handling: "A blank field matches only an “is blank” condition.", source: "deterministic" },
  definition: { kind: "freehand", definition: { adhoc: { entity: "projects", agg: "count" }, question: QUESTION } },
  results: {
    columns: [{ name: "value", kind: "measure", label: "Projects matching your rule", ref: "measure:_adhoc", data_type: "integer", format: "integer" }],
    rows: [[14]], row_count: 1, truncated: false, as_of: null,
    sample: { columns: [{ name: "name", kind: "field", label: "Project name", ref: "field:projects.name", data_type: "text", format: null }], rows: [["Atlas"], ["Borealis"]], total: 14 },
  },
  results_scope: "Your certification scope",
  usage: { widgets: [{ id: "w9", title: "No code, overdue" }], reports: [], dashboards: [], shared: false },
  generated: "SELECT count(*) FROM projects WHERE budget_code IS NULL", dependencies: [],
  lookalikes: [{ ref: "measure:budget_code_set", kind: "measure", label: "With a budget code", similarity: 0.75, reasons: ["similar name"],
    difference: "With a budget code counts where Budget code is not blank.", result_delta: { item: 14, certified: 80, delta: -66, differs: true } }],
  lookalike_note: null, semantic_checks: [], can_decide: { allowed: true, reason: null },
};

describe("free-hand rules in certification (US-166)", () => {
  it("shows the question, the rule, the number, a sample of records and the look-alikes, then approves as a named measure", async () => {
    serve((url, init) => {
      if (url.includes("/api/v1/certification/queue")) return json({ items: [ITEM] });
      if (url.endsWith("/api/v1/certification/requests/r9")) return json(DETAIL);
      if (url.endsWith("/r9/decision") && init?.method === "POST") return json({ request_id: "r9", decision: "certify", status: "certified", widgets_changed: ["No code, overdue"], measure: "measure:no_code_overdue", model_version: 4 }, 201);
      return undefined;
    });
    renderAt("/govern/certification?r=r9");
    expect(await screen.findByText(`“${QUESTION}”`)).toBeInTheDocument();
    expect(screen.getAllByText(PLAIN)[0]).toBeInTheDocument();
    expect(screen.getByText("Pending certification")).toBeInTheDocument();
    expect(screen.getByText(/Sample of records \(2 of 14\)/)).toBeInTheDocument();
    expect(screen.getByRole("cell", { name: "Borealis" })).toBeInTheDocument();
    expect(within(screen.getByRole("region", { name: "Look-alikes" })).getByText(/With a budget code counts where/)).toBeInTheDocument();

    const decision = screen.getByRole("region", { name: "Decision" });
    expect(within(decision).getByRole("radio", { name: /Approve as a certified measure/ })).toBeChecked();
    const name = within(decision).getByLabelText("Measure name");
    await userEvent.clear(name);
    await userEvent.type(name, "No code, overdue");
    expect(within(decision).getByLabelText("Key")).toHaveValue("no_code_overdue");
    await userEvent.click(within(decision).getByRole("button", { name: "Approve and notify Cleo" }));
    expect(await within(decision).findByText("Say in one line what it shows.")).toBeInTheDocument();
    await userEvent.type(within(decision).getByLabelText(/Definition/), "Projects with no budget code past their planned deployment.");
    await userEvent.click(within(decision).getByRole("button", { name: "Approve and notify Cleo" }));
    await vi.waitFor(() => expect(calls.find((c) => c.url.endsWith("/r9/decision"))?.body).toEqual({
      decision: "certify", note: null, merge_into: null,
      measure: { key: "no_code_overdue", label: "No code, overdue", definition: "Projects with no budget code past their planned deployment.", plain_rule: PLAIN },
    }));
  });

  it("asks for changes or rejects only with a note, and can point to an existing measure", async () => {
    serve((url, init) => {
      if (url.includes("/api/v1/certification/queue")) return json({ items: [ITEM] });
      if (url.endsWith("/api/v1/certification/requests/r9")) return json(DETAIL);
      if (url.endsWith("/r9/decision") && init?.method === "POST") return json({ request_id: "r9", decision: "reject", status: "rejected", widgets_changed: [] }, 201);
      return undefined;
    });
    renderAt("/govern/certification?r=r9");
    const decision = await screen.findByRole("region", { name: "Decision" });
    await userEvent.click(within(decision).getByRole("radio", { name: /Ask for changes/ }));
    await userEvent.click(within(decision).getByRole("button", { name: "Ask for changes and notify Cleo" }));
    expect(await within(decision).findByText("Say what should change; the author will see the note.")).toBeInTheDocument();
    await userEvent.click(within(decision).getByRole("radio", { name: /Reject/ }));
    await userEvent.selectOptions(within(decision).getByLabelText(/Point to an existing measure/), "measure:budget_code_set");
    await userEvent.type(within(decision).getByLabelText("Note to Cleo"), "We already count this.");
    await userEvent.click(within(decision).getByRole("button", { name: "Reject and notify Cleo" }));
    await vi.waitFor(() => expect(calls.find((c) => c.url.endsWith("/r9/decision"))?.body).toEqual({
      decision: "reject", note: "We already count this.", merge_into: "measure:budget_code_set", measure: null }));
  });
});
