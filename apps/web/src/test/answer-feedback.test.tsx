import { QueryClient } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { FeedbackItem } from "../api/knowledge";
import type { Me } from "../api/types";
import { AppRoutes, Providers } from "../app/App";
import { TestAuthProvider } from "../app/auth";
import { setConfigForTests } from "../app/config";

vi.mock("echarts/core", () => ({
  use: vi.fn(),
  registerTheme: vi.fn(),
  init: () => ({ setOption: vi.fn(), on: vi.fn(), resize: vi.fn(), dispose: vi.fn() }),
}));

const base = { tenant_id: "t1", audience_roles: [], scope_summary: [], locale: null, time_zone: null, email: null };
const creator: Me = { ...base, user_id: "u2", display_name: "Cleo Creator", roles: ["creator"], level: "creator",
  permissions: ["dashboards.view", "ask.use", "drill.use", "definitions.view", "widgets.create"] };
const admin: Me = { ...base, user_id: "u1", display_name: "Ada Admin", roles: ["admin"], level: "admin",
  permissions: ["dashboards.view", "knowledge.manage", "widgets.create", "connections.manage", "ask.use"] };

const calls: { url: string; method: string; body: unknown }[] = [];
function serve(me: Me, handler: (url: string, init?: RequestInit) => Response | undefined) {
  calls.length = 0;
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ url, method: init?.method ?? "GET", body: init?.body ? JSON.parse(String(init.body)) : undefined });
    if (url.endsWith("/api/v1/me")) return json(me);
    if (url.includes("/api/v1/notifications")) return json({ items: [], unread_count: 0, next_cursor: null, kinds: [] });
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
  filters: [{ ref: "dimension:region", op: "=", values: ["KSA"] }, { ref: "dimension:cio", op: "=", values: ["Karim Haddad"] }] };
const COLS = [{ name: "region", kind: "dimension", label: "Region", ref: "dimension:region", data_type: "text", format: null },
  { name: "open_projects", kind: "measure", label: "Open projects", ref: "measure:open_projects", data_type: "integer", format: "integer" }];
const EMPTY_NOTE = { kind: "empty", fact: "s1", text: "Nothing matches with the Region: KSA filter; without it there is data.",
  action: { label: "Remove the region filter", ops: [{ op: "remove_filter", ref: "dimension:region" }] } };
function emptyAnswer(turnId: string) {
  return sse([["intent", {}], ["plan", { chips: [] }],
    ["viz", { visual: "table", alternatives: [], disabled: [], note: null, chart_spec: { type: "table" } }],
    ["narrative", { headline: "No records match the current scope and filters.", sentences: [], notes: [EMPTY_NOTE], insights: [], cited: [], next: [] }],
    ["complete", { turn_id: turnId, certified: true, scope_text: "projects: Gulf regions", filters_text: "Region: KSA; CIO: Karim Haddad", as_of: null,
      interpreted: [], definitions: [], columns: COLS, rows: [], row_count: 0, connection: "TPG", model_version: 1, spec: SPEC, can_pin: true }]]);
}
const mine = (over: Record<string, unknown>) => ({ id: "fb1", turn_id: "t1", vote: "down", reason: "wrong", comment: null, status: "open",
  created_at: "2026-10-29T08:00:00Z", updated_at: "2026-10-29T08:00:00Z", ...over });

beforeEach(() => setConfigForTests({ apiBaseUrl: "http://api.test", oidcAuthority: "http://kc", oidcClientId: "tela-web" }));
afterEach(() => vi.unstubAllGlobals());

describe("sanity notes and feedback on the answer card (US-183)", () => {
  it("offers the sanity note's refinement and sends thumbs and \"This isn't what I asked\"", async () => {
    let saved: Record<string, unknown> | null = null;
    serve(creator, (url, init) => {
      if (url.endsWith("/api/v1/ask/turns") && init?.method === "POST") return json({ turn_id: "t1", conversation_id: "c1" }, 202);
      if (url.endsWith("/t1/stream")) return emptyAnswer("t1");
      if (url.endsWith("/t1/refine")) return json({ turn_id: "t2", conversation_id: "c1" }, 202);
      if (url.endsWith("/t2/stream")) return emptyAnswer("t2");
      if (url.endsWith("/t1/feedback")) {
        if (init?.method === "PUT") { saved = mine(JSON.parse(String(init.body))); return json({ feedback: saved }); }
        if (init?.method === "DELETE") { saved = null; return new Response(null, { status: 204 }); }
        return json({ feedback: saved });
      }
      return undefined;
    });
    renderAt("/ask?q=open%20projects%20by%20region");
    const card = await screen.findByRole("article", { name: "Answer" });
    const notes = await within(card).findByRole("region", { name: "Data notes" });
    expect(within(notes).getByText(/Nothing matches with the Region: KSA filter/)).toBeInTheDocument();
    // The note only offers the refinement; it runs when clicked.
    expect(calls.some((c) => c.url.endsWith("/refine"))).toBe(false);
    await userEvent.click(within(notes).getByRole("button", { name: "Remove the region filter" }));
    await waitFor(() => expect(calls.find((c) => c.url.endsWith("/t1/refine"))?.body).toEqual({ chip_ops: [{ op: "remove_filter", ref: "dimension:region" }] }));

    const rate = within(card).getByRole("group", { name: "Rate this answer" });
    const up = within(rate).getByRole("button", { name: "Helpful" });
    expect(up).toHaveAttribute("aria-pressed", "false");
    await userEvent.click(up);
    await waitFor(() => expect(up).toHaveAttribute("aria-pressed", "true"));
    expect(calls.find((c) => c.method === "PUT")?.body).toEqual({ vote: "up" });
    await userEvent.click(up); // the same thumb again takes the vote back
    await waitFor(() => expect(up).toHaveAttribute("aria-pressed", "false"));
    expect(calls.some((c) => c.method === "DELETE" && c.url.endsWith("/t1/feedback"))).toBe(true);

    await userEvent.click(within(rate).getByRole("button", { name: "This isn't what I asked" }));
    const form = within(card).getByRole("form", { name: "What was wrong" });
    expect(within(form).getByRole("radio", { name: "This isn't what I asked" })).toBeChecked();
    await userEvent.type(within(form).getByLabelText(/What did you expect/), "Paused projects");
    calls.length = 0;
    await userEvent.click(within(form).getByRole("button", { name: "Send" }));
    await waitFor(() => expect(calls.find((c) => c.method === "PUT")?.body).toEqual({ vote: "down", reason: "not_what_i_asked", comment: "Paused projects" }));
    expect(await within(rate).findByText("Sent for review")).toBeInTheDocument();
    expect(within(rate).getByRole("button", { name: "Not helpful" })).toHaveAttribute("aria-pressed", "true");
    expect(within(card).queryByRole("form", { name: "What was wrong" })).toBeNull();
  });

  it("shows a fixed vote as final", async () => {
    serve(creator, (url, init) => {
      if (url.endsWith("/api/v1/ask/turns") && init?.method === "POST") return json({ turn_id: "t1", conversation_id: "c1" }, 202);
      if (url.endsWith("/t1/stream")) return emptyAnswer("t1");
      if (url.endsWith("/t1/feedback")) return json({ feedback: mine({ status: "fixed" }) });
      return undefined;
    });
    renderAt("/ask?q=open%20projects%20by%20region");
    const card = await screen.findByRole("article", { name: "Answer" });
    expect(await within(card).findByText(/An Admin corrected this answer/)).toBeInTheDocument();
    const rate = within(card).getByRole("group", { name: "Rate this answer" });
    for (const b of within(rate).getAllByRole("button")) expect(b).toBeDisabled();
  });
});

const ITEM: FeedbackItem = {
  id: "fb1", turn_id: "t1", vote: "down", reason: "not_what_i_asked", comment: "I meant paused projects", status: "open",
  created_at: "2026-10-29T08:00:00Z", updated_at: "2026-10-29T08:00:00Z", user: { id: "u2", display_name: "Cleo Creator" },
  question: "How many projects are on hold?", spec: { model_version_id: "v1", connection_id: "c1", measure: { ref: "measure:on_hold_projects" } },
  connection: { id: "c1", name: "TPG" }, gold_example_id: null, resolved_by: null, resolved_at: null, resolution_note: null,
};
const QUEUE = { items: [ITEM], counts: { open: 1, fixed: 2, dismissed: 0, up: 5 } };

describe("Admin → Knowledge → Flagged answers (US-183)", () => {
  it("lists down-votes and fixes one by saving a gold example from it", async () => {
    serve(admin, (url, init) => {
      if (url.includes("/api/v1/ask/feedback?status=")) return json(QUEUE);
      if (url.endsWith("/api/v1/knowledge/gold-examples") && init?.method === "POST") return json({ id: "g9", status: "approved" }, 201);
      if (url.endsWith("/api/v1/ask/feedback/fb1") && init?.method === "PATCH") return json({ ...ITEM, status: "fixed", gold_example_id: "g9" });
      return undefined;
    });
    renderAt("/admin/knowledge/feedback");
    expect(await screen.findByRole("heading", { level: 1, name: "Knowledge" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Flagged answers" })).toHaveAttribute("aria-current", "page");
    const table = await screen.findByRole("table", { name: "Flagged answers" });
    expect(await within(table).findByText("How many projects are on hold?")).toBeInTheDocument();
    expect(within(table).getByText("Not what I asked")).toBeInTheDocument();
    expect(within(table).getByText("“I meant paused projects”")).toBeInTheDocument();
    expect(screen.getByText(/1 open · 2 fixed · 0 dismissed · 5 marked helpful/)).toBeInTheDocument();

    await userEvent.click(within(table).getByRole("button", { name: "Fix How many projects are on hold?" }));
    const dialog = await screen.findByRole("dialog", { name: "Fix this answer" });
    const specBox = within(dialog).getByLabelText("Correct Query Spec (JSON)");
    expect(JSON.parse((specBox as HTMLTextAreaElement).value)).toEqual({ measure: { ref: "measure:on_hold_projects" } });
    await userEvent.clear(specBox);
    await userEvent.click(specBox);
    await userEvent.paste('{"measure": {"ref": "measure:paused_projects"}}');
    await userEvent.click(within(dialog).getByRole("button", { name: "Save as gold example" }));
    await waitFor(() => expect(calls.find((c) => c.method === "PATCH")?.body).toEqual({ status: "fixed", gold_example_id: "g9" }));
    expect(calls.find((c) => c.method === "POST")?.body).toEqual({
      question: "How many projects are on hold?", expected_spec: { measure: { ref: "measure:paused_projects" } }, connection_id: "c1",
      turn_id: "t1", tags: ["from_feedback"], notes: "I meant paused projects",
    });
  });

  it("links an existing gold example on a duplicate question, and dismisses with a note", async () => {
    serve(admin, (url, init) => {
      if (url.includes("/api/v1/ask/feedback?status=")) return json(QUEUE);
      if (url.endsWith("/api/v1/knowledge/gold-examples") && init?.method === "POST") {
        return json({ error: { code: "conflict", message: "There is already a gold example with this question.", details: { gold_example_id: "g1" } } }, 409);
      }
      if (url.endsWith("/api/v1/ask/feedback/fb1") && init?.method === "PATCH") return json({ ...ITEM, status: "fixed" });
      return undefined;
    });
    renderAt("/admin/knowledge/feedback");
    const table = await screen.findByRole("table", { name: "Flagged answers" });
    await userEvent.click(await within(table).findByRole("button", { name: "Fix How many projects are on hold?" }));
    const dialog = await screen.findByRole("dialog", { name: "Fix this answer" });
    await userEvent.click(within(dialog).getByRole("button", { name: "Save as gold example" }));
    await waitFor(() => expect(calls.find((c) => c.method === "PATCH")?.body).toEqual({ status: "fixed", gold_example_id: "g1" }));
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "Fix this answer" })).toBeNull());

    calls.length = 0;
    await userEvent.click(within(table).getByRole("button", { name: "Dismiss How many projects are on hold?" }));
    const dismiss = await screen.findByRole("dialog", { name: "Dismiss this flag" });
    await userEvent.type(within(dismiss).getByLabelText(/Note/), "The number is right.");
    await userEvent.click(within(dismiss).getByRole("button", { name: "Dismiss" }));
    await waitFor(() => expect(calls.find((c) => c.method === "PATCH")?.body).toEqual({ status: "dismissed", note: "The number is right." }));
  });
});
