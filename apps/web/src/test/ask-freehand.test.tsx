import { QueryClient } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
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

/** Free-hand answers in Ask (ENH-008 US-165): badge, rule chips that rerun, compare with certified, request access. */
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

const CHIPS = [
  { kind: "rule", path: "0", field: "budget_code", field_label: "Budget code", field_type: "text", op: "is_null", value: null, label: "Budget code is blank" },
  { kind: "rule", path: "1", field: "planned_deployment_date", field_label: "Planned deployment date", field_type: "date", op: "<", value: { today: true }, label: "Planned deployment date is before today" },
];
const RULE = { label: "Projects matching your rule", entity: "projects", chips: CHIPS,
  plain_rule: "Count of projects where Budget code is blank and Planned deployment date is before today." };
const ADHOC_SPEC = { model_version_id: "v1", connection_id: "c1", measure: { ref: "measure:_adhoc", adhoc: { entity: "projects", agg: "count", label: RULE.label, plain_rule: RULE.plain_rule } } };

function answer(turn: string, value: number, extra: Record<string, unknown> = {}) {
  return sse([["intent", {}], ["plan", { chips: [{ kind: "measure", label: RULE.label, freehand: true }, ...CHIPS], certified: false, rule: RULE }],
    ["viz", { visual: "figure", alternatives: [], disabled: [], note: null, chart_spec: { type: "figure", series: ["value"] } }],
    ["narrative", { headline: `${value} projects`, sentences: [{ text: `Not certified · built from your question: ${RULE.plain_rule}` }] }],
    ["complete", { turn_id: turn, certified: false, badge: "not_certified", can_export: false, rule: RULE, scope_text: "projects: Own CIO", filters_text: "None",
      as_of: "2026-10-21T06:00:00Z", interpreted: ["Counting projects"], row_count: 1,
      columns: [{ name: "value", kind: "measure", label: RULE.label, ref: "measure:_adhoc", data_type: "integer", format: "integer" }],
      rows: [[value]], connection: "TPG", model_version: 3, spec: ADHOC_SPEC, ...extra }]]);
}

beforeEach(() => {
  calls.length = 0;
  setConfigForTests({ apiBaseUrl: "http://api.test", oidcAuthority: "http://kc", oidcClientId: "tela-web" });
});
afterEach(() => vi.unstubAllGlobals());

describe("free-hand answers", () => {
  it("labels the answer not certified, shows the rule and reruns a changed chip", async () => {
    serve((url, init) => {
      if (url.endsWith("/api/v1/ask/turns") && init?.method === "POST") return json({ turn_id: "t1", conversation_id: "c1", stream_url: "/api/v1/ask/turns/t1/stream" }, 202);
      if (url.endsWith("/t1/refine")) return json({ turn_id: "t2", conversation_id: "c1", changes: ["Rule changed"], stream_url: "/api/v1/ask/turns/t2/stream" }, 202);
      if (url.endsWith("/t1/stream")) return answer("t1", 7);
      if (url.endsWith("/t2/stream")) return answer("t2", 12, { refined_from: "t1" });
      if (url.includes("/api/v1/query/catalog")) return json({ entities: [{ key: "projects", label: "Projects", fields: [
        { key: "budget_code", label: "Budget code", data_type: "text" }, { key: "planned_deployment_date", label: "Planned deployment date", data_type: "date" }] }] });
      return undefined;
    });
    renderAt("/ask?q=projects%20without%20a%20budget%20code");
    const card = await screen.findByRole("article", { name: "Answer" });
    expect(within(card).getByText("Not certified · built from your question")).toBeInTheDocument();
    expect(within(card).queryByText("Certified")).not.toBeInTheDocument();
    expect(within(card).getByRole("region", { name: "Your rule" })).toHaveTextContent(RULE.plain_rule);
    expect(within(card).queryByRole("button", { name: "Why this number?" })).not.toBeInTheDocument();

    await userEvent.click(within(card).getByRole("button", { name: "Change condition: Budget code is blank" }));
    const form = await screen.findByRole("form", { name: "Change this condition" });
    await userEvent.selectOptions(within(form).getByLabelText("Condition"), "is not blank");
    await userEvent.click(within(form).getByRole("button", { name: "Apply and rerun" }));
    await vi.waitFor(() => expect(calls.find((c) => c.url.endsWith("/t1/refine"))?.body).toEqual({
      chip_ops: [{ op: "set_condition", path: "0", field: "budget_code", operator: "not_null" }] }));
    expect(await screen.findByRole("heading", { name: "12 projects" })).toBeInTheDocument();
  });

  it("shows the certified answer and the person's rule side by side (D1)", async () => {
    serve((url, init) => {
      if (url.endsWith("/api/v1/ask/turns") && init?.method === "POST") return json({ turn_id: "t3", conversation_id: "c1", stream_url: "/api/v1/ask/turns/t3/stream" }, 202);
      if (url.endsWith("/t3/stream")) return sse([["intent", {}], ["plan", { chips: [{ kind: "measure", label: "Past planning" }], certified: true, rule: null }],
        ["viz", { visual: "figure", alternatives: [], disabled: [], note: null, chart_spec: { type: "figure", series: ["v"] } }],
        ["narrative", { headline: "42 projects are past planning.", sentences: [] }],
        ["complete", { turn_id: "t3", certified: true, badge: "certified", rule: null, scope_text: "All", filters_text: "None", as_of: null, interpreted: [],
          columns: [{ name: "v", kind: "measure", label: "Past planning", ref: "measure:baselining_in_scope", data_type: "integer", format: "integer" }], rows: [[42]],
          definitions: [{ ref: "measure:baselining_in_scope", label: "Past planning", plain_rule: "Open projects whose lifecycle phase is past Planning.", blank_handling: "", rule_ref: null, format: "integer" }],
          spec: { model_version_id: "v1", connection_id: "c1", measure: { ref: "measure:baselining_in_scope" } },
          beside: { kind: "freehand", label: "Projects past the plan stage", plain_rule: "Count of projects where (a stage planned end is not blank) and (Planning stage planned end is blank or before today).", value: 37, format: "integer", certified: false } }]]);
      return undefined;
    });
    renderAt("/ask?q=past%20the%20plan%20stage%20meaning");
    const compare = await screen.findByRole("region", { name: "Compare with certified" });
    expect(compare).toHaveTextContent("Your rule, beside the certified measure");
    const boxes = within(compare).getAllByText(/^(Certified|Not certified · built from your question)$/).map((n) => n.textContent);
    expect(boxes).toEqual(["Certified", "Not certified · built from your question"]);
    expect(compare).toHaveTextContent("42");
    expect(compare).toHaveTextContent("37");
    expect(compare).toHaveTextContent("Open projects whose lifecycle phase is past Planning.");
  });

  it("offers to request free-hand access when nothing certified fits", async () => {
    serve((url, init) => {
      if (url.endsWith("/api/v1/ask/turns") && init?.method === "POST") return json({ turn_id: "t4", conversation_id: "c1", stream_url: "/api/v1/ask/turns/t4/stream" }, 202);
      if (url.endsWith("/t4/stream")) return sse([["intent", {}], ["error", { code: "unsupported_question", message: "I couldn't match that to a certified measure.",
        examples: ["Past planning"], freehand_access: { freehand: "can_request", connection_id: "c1", connection: "TPG" } }]]);
      if (url.endsWith("/api/v1/me/permission-requests") && init?.method === "POST") return json({ id: "r1", status: "pending" }, 201);
      return undefined;
    });
    renderAt("/ask?q=projects%20whose%20S2%20is%20blank");
    const alert = await screen.findByRole("alert");
    await userEvent.click(within(alert).getByRole("button", { name: "Request free-hand access" }));
    await vi.waitFor(() => expect(calls.find((c) => c.url.endsWith("/permission-requests"))?.body).toEqual({
      connection_id: "c1", note: "Asked in Ask: “projects whose S2 is blank”" }));
    expect(await within(alert).findByRole("button", { name: "Free-hand access requested" })).toBeDisabled();
  });
});
