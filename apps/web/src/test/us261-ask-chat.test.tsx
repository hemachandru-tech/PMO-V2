import { QueryClient } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
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

const base = { tenant_id: "t1", audience_roles: [], scope_summary: [], locale: null, time_zone: null, email: null };
const creator: Me = { ...base, user_id: "u2", display_name: "Cleo Creator", roles: ["creator"], level: "creator",
  permissions: ["dashboards.view", "ask.use", "drill.use", "definitions.view", "widgets.create"] };

const calls: { url: string; method: string; body: unknown }[] = [];
function serve(handler: (url: string, init?: RequestInit) => Response | undefined) {
  calls.length = 0;
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ url, method: init?.method ?? "GET", body: init?.body ? JSON.parse(String(init.body)) : undefined });
    if (url.endsWith("/api/v1/me")) return json(creator);
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

const SPEC = { model_version_id: "v1", connection_id: "c1", measure: { ref: "measure:open_projects" }, breakdowns: [] };
function single(turnId: string) {
  return sse([["intent", {}], ["plan", { chips: [] }],
    ["viz", { visual: "figure", alternatives: [], disabled: [], note: null, chart_spec: { type: "figure" } }],
    ["narrative", { headline: "Open projects: 111.", sentences: [{ text: "Counted across every business unit." }], cited: [], insights: [], notes: [], next: [] }],
    ["complete", { turn_id: turnId, certified: true, filters_text: "Status is open", as_of: null, interpreted: [],
      definitions: [{ ref: "measure:open_projects", label: "Open projects", plain_rule: "", blank_handling: "", rule_ref: null, format: "integer" }],
      columns: [{ name: "open_projects", kind: "measure", label: "Open projects", ref: "measure:open_projects", data_type: "integer", format: "integer" }],
      rows: [[111]], row_count: 1, connection: "TPG", model_version: 1, spec: SPEC, can_pin: true }]]);
}

beforeEach(() => setConfigForTests({ apiBaseUrl: "http://api.test", oidcAuthority: "http://kc", oidcClientId: "tela-web" }));
afterEach(() => vi.unstubAllGlobals());

describe("Ask as a chat (US-261)", () => {
  it("greets a new chat with suggested questions; one click asks it", async () => {
    serve((url, init) => {
      if (url.endsWith("/api/v1/home")) return json({ greeting: { name: "Cleo", scopes: [], as_of: null }, suggested_questions: ["Open projects by status", "Overdue go-lives"] });
      if (url.endsWith("/api/v1/ask/turns") && init?.method === "POST") return json({ turn_id: "t1", conversation_id: "c1" }, 202);
      if (url.endsWith("/t1/stream")) return single("t1");
      return undefined;
    });
    renderAt("/ask");
    const start = await screen.findByRole("region", { name: "Start a conversation" });
    expect(within(start).getByText("Hello, Cleo")).toBeInTheDocument();
    const list = await within(start).findByRole("list", { name: "Suggested questions" });
    await userEvent.click(within(list).getByRole("button", { name: "Open projects by status" }));
    await waitFor(() => expect(calls.find((c) => c.url.endsWith("/api/v1/ask/turns"))?.body).toMatchObject({ question: "Open projects by status" }));
    expect(await screen.findByRole("article", { name: "Answer" })).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Start a conversation" })).toBeNull();
  });

  it("sends with Enter, keeps Shift+Enter as a new line", async () => {
    serve((url, init) => {
      if (url.endsWith("/api/v1/ask/turns") && init?.method === "POST") return json({ turn_id: "t1", conversation_id: "c1" }, 202);
      if (url.endsWith("/t1/stream")) return single("t1");
      return undefined;
    });
    renderAt("/ask");
    const box = await screen.findByRole("textbox", { name: "Ask a question" });
    expect(screen.getByRole("button", { name: "Send" })).toBeDisabled();
    await userEvent.type(box, "How many open{Shift>}{Enter}{/Shift}projects?");
    expect(box).toHaveValue("How many open\nprojects?");
    await userEvent.keyboard("{Enter}");
    await waitFor(() => expect(calls.find((c) => c.url.endsWith("/api/v1/ask/turns"))?.body).toMatchObject({ question: "How many open\nprojects?" }));
    expect(box).toHaveValue("");
  });

  it("shows a single figure as a stat with a quiet facts line and the actions as named icons", async () => {
    serve((url, init) => {
      if (url.endsWith("/api/v1/ask/turns") && init?.method === "POST") return json({ turn_id: "t1", conversation_id: "c1" }, 202);
      if (url.endsWith("/t1/stream")) return single("t1");
      if (url.endsWith("/t1/feedback")) return json({ feedback: null });
      return undefined;
    });
    renderAt("/ask?q=how%20many%20open%20projects");
    const card = await screen.findByRole("article", { name: "Answer" });
    expect(within(card).getByRole("heading", { level: 2, name: "Open projects: 111." })).toBeInTheDocument();
    expect(within(card).getByLabelText("Open projects: 111")).toBeInTheDocument();
    expect(within(card).getByText("Status is open")).toBeInTheDocument();
    expect(within(card).getByText("TPG · model v1")).toBeInTheDocument();
    const bar = within(card).getByRole("toolbar", { name: "Answer actions" });
    for (const name of ["Copy answer", "Helpful", "Not helpful", "This isn't what I asked", "Pin", "Drill to records", "Why this number?", "Save as gold example"]) {
      const button = within(bar).getByRole("button", { name });
      expect(button).toBeInTheDocument();
      expect(button.textContent).toBe("");
    }
    // The refinements under the latest answer.
    const refine = within(card).getByRole("group", { name: "Refine" });
    expect(within(refine).getAllByRole("button").map((b) => b.textContent)).toEqual(["Top 5", "As table", "By month", "Compare with previous period", "Change this answer"]);
  });

  it("names the step it is on while the answer streams", async () => {
    let finish: () => void = () => undefined;
    serve((url, init) => {
      if (url.endsWith("/api/v1/ask/turns") && init?.method === "POST") return json({ turn_id: "t1", conversation_id: "c1" }, 202);
      if (url.endsWith("/t1/stream")) {
        const enc = new TextEncoder();
        const body = new ReadableStream({
          start(ctrl) {
            ctrl.enqueue(enc.encode("id: 1\nevent: intent\ndata: {}\n\n"));
            finish = () => ctrl.close();
          },
        });
        return new Response(body, { status: 200, headers: { "Content-Type": "text/event-stream" } });
      }
      return undefined;
    });
    renderAt("/ask?q=open%20projects");
    expect(await screen.findByText("Finding the data…")).toBeInTheDocument();
    const steps = screen.getByRole("list", { name: "Progress" });
    expect(within(steps).getAllByRole("listitem")).toHaveLength(6);
    expect(within(steps).getByText("Understanding your question (done)")).toBeInTheDocument();
    finish();
  });

  it("puts a turn's error and its ways forward in the reply, as chips", async () => {
    serve((url, init) => {
      if (url.endsWith("/api/v1/ask/turns") && init?.method === "POST") return json({ turn_id: "t1", conversation_id: "c1" }, 202);
      if (url.endsWith("/t1/stream")) {
        return sse([["intent", {}], ["error", { code: "not_in_model", message: "That breakdown or filter is not in the approved model.",
          understood: ["Tpg project"], unrecognised: ["open"], examples: ["Number of tpg project", "Number of account"] }]]);
      }
      return undefined;
    });
    renderAt("/ask?q=how%20many%20open%20projects");
    const alert = await screen.findByRole("alert");
    expect(within(alert).getByText("Not recognised: open")).toBeInTheDocument();
    const closest = within(alert).getByRole("group", { name: "Closest measures" });
    expect(within(closest).getAllByRole("button").map((b) => b.textContent)).toEqual(["Number of tpg project", "Number of account"]);
  });
});
