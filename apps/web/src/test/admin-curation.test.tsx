import { QueryClient } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { CurationProposal, CurationQueue } from "../api/knowledge";
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

const base = { status: "pending" as const, connection: { id: "c1", name: "TPG" }, meaning: null, spec: null, needs_fix: false, applied: null,
  reviewed_by: null, reviewed_at: null, review_note: null, created_at: "2026-11-01T08:00:00Z", updated_at: "2026-11-01T08:00:00Z" };
const STALLED: CurationProposal = { ...base, id: "p1", kind: "measure_synonym", question: "Show me stalled projects by region", phrase: "stalled projects",
  measure: null, options: [{ ref: "measure:total_projects", label: "Projects" }], seen: 2, by_source: { refused: 2 },
  questions: [{ question: "Show me stalled projects by region", source: "refused", detail: "I couldn't match that to a certified measure.", turn_id: "t1", at: "2026-11-01T08:00:00Z" },
    { question: "Stalled projects?", source: "refused", detail: null, turn_id: "t2", at: "2026-11-01T07:00:00Z" }] };
const RFQ: CurationProposal = { ...base, id: "p2", kind: "glossary_term", question: "How many RFQs are pending?", phrase: "RFQs", measure: null, options: [],
  seen: 1, by_source: { refused: 1 }, questions: [{ question: "How many RFQs are pending?", source: "refused", detail: null, turn_id: "t3", at: "2026-11-01T08:00:00Z" }] };
const FLAGGED: CurationProposal = { ...base, id: "p3", kind: "gold_example", question: "How many projects are on hold?", phrase: null, measure: null, options: [],
  spec: { measure: { ref: "measure:on_hold" } }, needs_fix: true, seen: 1, by_source: { feedback: 1 },
  questions: [{ question: "How many projects are on hold?", source: "feedback", detail: "Marked wrong: “I meant paused ones”", turn_id: "t4", at: "2026-11-01T08:00:00Z" }] };
const QUEUE: CurationQueue = { items: [STALLED, RFQ, FLAGGED], counts: { pending: 3, approved: 4, dismissed: 1,
  pending_by_kind: { measure_synonym: 1, glossary_term: 1, gold_example: 1 }, pending_by_source: { refused: 3, clarification: 0, repair: 0, feedback: 1, direct_sql: 0 } } };
const CATALOG = { connection_id: "c1", model_version_id: "v1", version_no: 1, engine: "postgres", scope: { summary: "", locked: false }, dimensions: [], entities: [], links: [],
  measures: [{ ref: "measure:total_projects", label: "Projects", plain_rule: "", format: "integer", entity: "projects" },
    { ref: "measure:on_hold", label: "Projects on hold", plain_rule: "", format: "integer", entity: "projects" }] };

function routes(url: string, init?: RequestInit): Response | undefined {
  if (url.endsWith("/api/v1/knowledge/curation/collect")) return json({ added: 0, read: { refused: 0, clarification: 0, repair: 0, feedback: 0 }, held_out_skipped: 0 });
  if (url.includes("/api/v1/knowledge/curation?status=")) return json(QUEUE);
  if (url.includes("/api/v1/query/catalog")) return json(CATALOG);
  if (url.endsWith("/curation/p1/approve") && init?.method === "POST") return json({ ...STALLED, status: "approved", applied: { version: 2 } });
  if (url.endsWith("/curation/p2/approve") && init?.method === "POST") return json({ ...RFQ, status: "approved" });
  if (url.endsWith("/curation/p3/approve") && init?.method === "POST") return json({ ...FLAGGED, status: "approved" });
  if (url.endsWith("/curation/p2/dismiss") && init?.method === "POST") return json({ ...RFQ, status: "dismissed" });
  return undefined;
}

beforeEach(() => setConfigForTests({ apiBaseUrl: "http://api.test", oidcAuthority: "http://kc", oidcClientId: "tela-web" }));
afterEach(() => vi.unstubAllGlobals());

describe("Admin → Knowledge → From misses (US-198)", () => {
  it("reads new misses on opening and lists proposals with counts and the questions behind them", async () => {
    serve(routes);
    renderAt("/admin/knowledge/curation");
    const table = await screen.findByRole("table", { name: "Proposals from Ask's misses" });
    expect(screen.getByRole("link", { name: "From misses" })).toHaveAttribute("aria-current", "page");
    expect(await within(table).findByText("Show me stalled projects by region")).toBeInTheDocument();
    expect(within(table).getByText("and 1 more question")).toBeInTheDocument();
    expect(within(table).getByText("“stalled projects” →")).toBeInTheDocument();
    expect(within(table).getByText("RFQs means:")).toBeInTheDocument();
    expect(within(table).getByText("Marked wrong: correct the spec")).toBeInTheDocument();
    expect(within(table).getByText("Refused × 2")).toBeInTheDocument();
    expect(screen.getByLabelText("Counts")).toHaveTextContent("3 waiting (1 synonyms · 1 terms · 1 gold examples) · 4 approved · 1 dismissed");
    expect(screen.getByText(/3 refused · 0 clarification chosen · 0 plan repaired · 1 marked wrong/)).toBeInTheDocument();
    await waitFor(() => expect(calls.filter((c) => c.url.endsWith("/curation/collect"))).toHaveLength(1));
  });

  it("approves a synonym with the measure picked, a term with its meaning, and a gold example with its corrected spec", async () => {
    serve(routes);
    renderAt("/admin/knowledge/curation");
    const table = await screen.findByRole("table", { name: "Proposals from Ask's misses" });

    await userEvent.click(await within(table).findByRole("button", { name: "Review Show me stalled projects by region" }));
    let dialog = await screen.findByRole("dialog", { name: "Approve a measure synonym" });
    expect(within(dialog).getByText("“Stalled projects?”")).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Approve" })).toBeDisabled();  // no measure yet
    const select = within(dialog).getByLabelText("Measure they mean");
    await waitFor(() => expect(within(select).getByRole("option", { name: "Projects on hold" })).toBeInTheDocument());
    await userEvent.selectOptions(select, "measure:on_hold");
    await userEvent.click(within(dialog).getByRole("button", { name: "Approve" }));
    await waitFor(() => expect(calls.find((c) => c.url.endsWith("/p1/approve"))?.body).toEqual({ phrase: "stalled projects", measure: "measure:on_hold" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());

    await userEvent.click(within(table).getByRole("button", { name: "Review How many RFQs are pending?" }));
    dialog = await screen.findByRole("dialog", { name: "Approve a glossary term" });
    await userEvent.clear(within(dialog).getByLabelText("Term"));
    await userEvent.type(within(dialog).getByLabelText("Term"), "RFQ");
    await userEvent.type(within(dialog).getByLabelText("What it means"), "request for quotation");
    await userEvent.click(within(dialog).getByRole("button", { name: "Approve" }));
    await waitFor(() => expect(calls.find((c) => c.url.endsWith("/p2/approve"))?.body).toEqual({ phrase: "RFQ", meaning: "request for quotation" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());

    await userEvent.click(within(table).getByRole("button", { name: "Review How many projects are on hold?" }));
    dialog = await screen.findByRole("dialog", { name: "Approve a gold example" });
    const spec = within(dialog).getByLabelText(/Correct Query Spec \(JSON\)/);
    await userEvent.clear(spec);
    await userEvent.click(spec);
    await userEvent.paste('{"measure": {"ref": "measure:on_hold"}, "breakdowns": [{"ref": "dimension:hold_reason"}]}');
    await userEvent.click(within(dialog).getByRole("button", { name: "Approve" }));
    await waitFor(() => expect(calls.find((c) => c.url.endsWith("/p3/approve"))?.body).toEqual({
      question: "How many projects are on hold?", expected_spec: { measure: { ref: "measure:on_hold" }, breakdowns: [{ ref: "dimension:hold_reason" }] } }));
  });

  it("dismisses with a note", async () => {
    serve(routes);
    renderAt("/admin/knowledge/curation");
    const table = await screen.findByRole("table", { name: "Proposals from Ask's misses" });
    await userEvent.click(await within(table).findByRole("button", { name: "Dismiss How many RFQs are pending?" }));
    const dialog = await screen.findByRole("dialog", { name: "Dismiss this proposal" });
    await userEvent.type(within(dialog).getByLabelText(/Note/), "Not ours.");
    await userEvent.click(within(dialog).getByRole("button", { name: "Dismiss" }));
    await waitFor(() => expect(calls.find((c) => c.url.endsWith("/p2/dismiss"))?.body).toEqual({ note: "Not ours." }));
  });
});
