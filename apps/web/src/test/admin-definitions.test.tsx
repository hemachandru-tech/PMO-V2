import { QueryClient } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { DefinitionList, KnowledgeDefinition } from "../api/knowledge";
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

const base = { connection: { id: "c1", name: "TPG MySQL" }, model_version: { id: "v1", version_no: 2 },
  document: { id: "d1", title: "PMO definitions", status: "approved" }, reviewed_by: null, reviewed_at: null, review_note: null,
  created_at: "2026-11-03T08:00:00Z", updated_at: "2026-11-03T08:00:00Z" };
const OPEN: KnowledgeDefinition = { ...base, id: "k1", status: "approved", term: "Open project",
  meaning: "a project whose current state is Project Created, Active or On Hold",
  rule: { entity: "projects", field: "current_state", op: "in", values: ["Project Created", "Active", "On Hold"] },
  rule_text: "Projects where Current state is one of Project Created, Active, On Hold", expressible: true, gaps: [],
  measure: { ref: "measure:open_projects", label: "Open projects" },
  checks: { checked_at: "2026-11-03T08:01:00Z", figure: 431, values: { "Project Created": 73, Active: 279, "On Hold": 79 },
    measure: { ref: "measure:open_projects", label: "Open projects", rule: "Status is Active", figure: 420 }, same_rule: false, conflict: true },
  conflict: true, reviewed_by: { id: "u1", display_name: "Ada Admin" }, reviewed_at: "2026-11-03T08:01:00Z",
  passage: { chunk_id: "ch1", chunk_no: 0, heading: "Open project", text: "An open project is a project whose current state is Project Created, Active or On Hold." } };
const GATED: KnowledgeDefinition = { ...base, id: "k2", status: "pending", term: "Gated project", meaning: "a project whose stage gate is G3 or G4",
  rule: null, rule_text: null, expressible: false, gaps: ["The model has no field “stage gate” on Projects."], measure: null, checks: null, conflict: false,
  passage: { chunk_id: "ch2", chunk_no: 1, heading: "Gated project", text: "A gated project is a project whose stage gate is G3 or G4." } };
const PAUSED: KnowledgeDefinition = { ...base, id: "k3", status: "pending", term: "Paused project", meaning: "current state On Hold or Parked",
  rule: { entity: "projects", field: "current_state", op: "in", values: ["On Hold", "Parked"] },
  rule_text: "Projects where Current state is one of On Hold, Parked", expressible: true, gaps: [], measure: null, checks: null, conflict: false,
  passage: { chunk_id: "ch3", chunk_no: 2, heading: "Terms", text: "- Paused project: current state On Hold or Parked." } };
const LIST: DefinitionList = { items: [OPEN, GATED, PAUSED], counts: { pending: 2, approved: 1, rejected: 0, conflicts: 1, text_only: 1 } };
const CATALOG = { connection_id: "c1", model_version_id: "v1", version_no: 2, engine: "mysql", scope: { summary: "", locked: false }, dimensions: [], links: [],
  measures: [{ ref: "measure:open_projects", label: "Open projects", plain_rule: "", format: "integer", entity: "projects" }],
  entities: [{ key: "projects", label: "Projects", table: "projects", fields: [
    { ref: "field:projects.current_state", key: "current_state", label: "Current state", data_type: "text", column: "current_state" },
    { ref: "field:projects.status", key: "status", label: "Status", data_type: "text", column: "record_status" },
    { ref: "field:projects.approved_budget", key: "approved_budget", label: "Approved budget", data_type: "decimal", column: "budget" }] }] };

function routes(url: string, init?: RequestInit): Response | undefined {
  if (url.includes("/api/v1/knowledge/definitions?status=")) return json(LIST);
  if (url.includes("/api/v1/query/catalog")) return json(CATALOG);
  if (url.endsWith("/definitions/k3/review") && init?.method === "POST") return json({ ...PAUSED, status: "approved" });
  if (url.endsWith("/definitions/k2/review") && init?.method === "POST") return json({ ...GATED, status: "rejected" });
  if (url.endsWith("/definitions/k1/check") && init?.method === "POST") return json(OPEN);
  return undefined;
}

beforeEach(() => setConfigForTests({ apiBaseUrl: "http://api.test", oidcAuthority: "http://kc", oidcClientId: "tela-web" }));
afterEach(() => vi.unstubAllGlobals());

describe("Admin → Knowledge → Definitions (US-203)", () => {
  it("lists definitions with their rule, gaps and the measure they differ from, with both figures", async () => {
    serve(routes);
    renderAt("/admin/knowledge/definitions");
    const table = await screen.findByRole("table", { name: "Business definitions" });
    expect(screen.getByRole("link", { name: "Definitions" })).toHaveAttribute("aria-current", "page");
    expect(await within(table).findByText("Projects where Current state is one of Project Created, Active, On Hold")).toBeInTheDocument();
    expect(within(table).getByText("Rule: Status is Active")).toBeInTheDocument();
    expect(within(table).getByText("Measure 420 · this definition 431")).toBeInTheDocument();
    expect(within(table).getByText("Differs from the measure")).toBeInTheDocument();
    expect(within(table).getByText("The model has no field “stage gate” on Projects.")).toBeInTheDocument();
    expect(screen.getByLabelText("Counts")).toHaveTextContent("2 waiting · 1 approved · 0 rejected · 1 differ from a measure · 1 text only");
    await userEvent.click(screen.getByLabelText("Only those that differ from a measure"));
    await waitFor(() => expect(calls.some((c) => c.url.includes("conflicts=true"))).toBe(true));
  });

  it("approves with an edited rule, rejects with a note and counts again", async () => {
    serve(routes);
    renderAt("/admin/knowledge/definitions");
    const table = await screen.findByRole("table", { name: "Business definitions" });

    await userEvent.click(await within(table).findByRole("button", { name: "Review Paused project" }));
    let dialog = await screen.findByRole("dialog", { name: "Review a business definition" });
    expect(within(dialog).getByText("- Paused project: current state On Hold or Parked.")).toBeInTheDocument();
    const field = within(dialog).getByLabelText("Field");
    await waitFor(() => expect(within(field).getByRole("option", { name: "Current state" })).toBeInTheDocument());
    expect(within(field).queryByRole("option", { name: "Approved budget" })).toBeNull();  // only text fields
    const values = within(dialog).getByLabelText(/Values/);
    await userEvent.clear(values);
    await userEvent.type(values, "On Hold");
    await userEvent.click(within(dialog).getByRole("button", { name: "Approve" }));
    await waitFor(() => expect(calls.find((c) => c.url.endsWith("/k3/review"))?.body).toEqual({
      decision: "approve", rule: { field: "current_state", op: "in", values: ["On Hold"] } }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());

    await userEvent.click(within(table).getByRole("button", { name: "Review Gated project" }));
    dialog = await screen.findByRole("dialog", { name: "Review a business definition" });
    await userEvent.type(within(dialog).getByLabelText(/Note/), "We don't track gates.");
    await userEvent.click(within(dialog).getByRole("button", { name: "Reject" }));
    await waitFor(() => expect(calls.find((c) => c.url.endsWith("/k2/review"))?.body).toEqual({ decision: "reject", note: "We don't track gates." }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());

    await userEvent.click(within(table).getByRole("button", { name: "Count Open project" }));
    await waitFor(() => expect(calls.some((c) => c.url.endsWith("/k1/check") && c.method === "POST")).toBe(true));
  });

  it("can't approve before the document is approved", async () => {
    serve((url, init) => url.includes("/definitions?status=")
      ? json({ ...LIST, items: [{ ...PAUSED, document: { ...PAUSED.document, status: "pending_review" } }] }) : routes(url, init));
    renderAt("/admin/knowledge/definitions");
    const table = await screen.findByRole("table", { name: "Business definitions" });
    await userEvent.click(await within(table).findByRole("button", { name: "Review Paused project" }));
    const dialog = await screen.findByRole("dialog", { name: "Review a business definition" });
    expect(within(dialog).getByText("Approve the document “PMO definitions” first.")).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Approve" })).toBeDisabled();
  });
});
