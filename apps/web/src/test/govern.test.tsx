import { QueryClient } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Me } from "../api/types";
import { AppRoutes, Providers } from "../app/App";
import { TestAuthProvider } from "../app/auth";
import { setConfigForTests } from "../app/config";

// EPIC-07/08 screens on the new design system: certification (US-087), field builder (US-078), custom SQL (US-080).

vi.mock("echarts/core", () => ({
  use: vi.fn(),
  registerTheme: vi.fn(),
  init: () => ({ setOption: vi.fn(), on: vi.fn(), resize: vi.fn(), dispose: vi.fn() }),
}));

const admin: Me = {
  user_id: "a1", display_name: "Ada Admin", email: null, tenant_id: "t1", roles: ["admin"], level: "admin", audience_roles: [],
  permissions: ["dashboards.view", "ask.use", "certification.decide", "fields.create", "customsql.use", "widgets.create", "reports.create", "certification.request"],
  scope_summary: [], locale: null, time_zone: null,
};
const creator: Me = { ...admin, user_id: "u2", display_name: "Arjun Rao", level: "creator", roles: ["creator"], permissions: admin.permissions.filter((p) => p !== "certification.decide") };

type Call = { url: string; method: string; body: unknown; headers: Record<string, string> };
let calls: Call[] = [];

export function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

function serve(me: Me, handler: (url: string, init?: RequestInit) => Response | undefined) {
  calls = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ url, method: init?.method ?? "GET", body: init?.body ? JSON.parse(String(init.body)) : undefined, headers: (init?.headers ?? {}) as Record<string, string> });
    if (url.endsWith("/api/v1/me")) return json(me);
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

const QUEUE_ITEM = {
  request_id: "r1", kind: "band", item_id: "f1", name: "Delay band", author: { id: "u2", name: "Arjun Rao" }, submitted_at: "2026-10-18T06:00:00Z",
  age_days: 3, reason: "on a dashboard sent for publishing", trigger: "publish_bound", note: "Leadership wants to see how late.", blocks_publish: true,
  usage: { widgets: 1, reports: 1, dashboards: 1 }, pending_publishes: ["Gulf delivery"], lookalikes: 1, dependency_pending: false,
  changed_since_requested: false, status: "pending", connection_id: "c1",
};
const DETAIL = {
  request_id: "r1", status: "pending", kind: "band", item_id: "f1", name: "Delay band", author: { id: "u2", name: "Arjun Rao" }, trigger: "publish_bound",
  note: "Leadership wants to see how late.", submitted_at: "2026-10-18T06:00:00Z", changed_since_requested: false,
  rule: { plain: "Bands Days late. On time: 0 up to 1; Late: 1 and above.", rephrased: null, blank_handling: "Blank values go to Not set.", source: "deterministic" },
  definition: { kind: "band", definition: { bands: [{ label: "On time", lower: 0, upper: 1 }, { label: "Late", lower: 1, upper: null }] } },
  results: { buckets: [{ label: "On time", count: 12 }, { label: "Late", count: 7 }], measure: "Projects", as_of: null }, results_scope: "Your certification scope",
  usage: { widgets: [{ id: "w1", title: "Late projects" }], reports: [{ id: "rp1", title: "Delivery" }], dashboards: [{ id: "d1", title: "Gulf delivery", live: false }], shared: true },
  generated: "SELECT 1", dependencies: [],
  lookalikes: [{ ref: "dimension:schedule_risk_band", kind: "dimension", similarity: 0.91, reasons: ["same source field"], difference: "Schedule risk band gives a different answer.", result_delta: { item: 7, certified: 19, delta: -12, differs: true } }],
  lookalike_note: null, semantic_checks: [], can_decide: { allowed: true, reason: null },
};

describe("certification (US-087)", () => {
  it("shows the queue and evidence, requires a note to reject, and confirms a merge whose answer differs", async () => {
    let decisions = 0;
    serve(admin, (url, init) => {
      if (url.includes("/api/v1/certification/queue")) return json({ items: [QUEUE_ITEM] });
      if (url.endsWith("/api/v1/certification/requests/r1")) return json(DETAIL);
      if (url.endsWith("/r1/decision") && init?.method === "POST") {
        decisions += 1;
        const body = JSON.parse(String(init.body));
        if (!body.confirm_different) return json({ error: { code: "conflict", message: "schedule_risk_band gives a different answer (7 vs 19). Confirm to merge anyway.", details: { result_delta: { differs: true } } } }, 409);
        return json({ request_id: "r1", decision: "merge", status: "merged", widgets_changed: ["Late projects"] }, 201);
      }
      return undefined;
    });
    renderAt("/govern/certification");
    expect(await screen.findByRole("link", { name: "Certification" })).toHaveAttribute("aria-current", "page");
    const queue = await screen.findByRole("region", { name: "Queue" });
    expect(await within(queue).findByText("Delay band")).toBeInTheDocument();
    expect(within(within(queue).getByRole("table")).getByText("Blocks a publish")).toBeInTheDocument();
    expect(await screen.findByRole("heading", { name: "Delay band" })).toBeInTheDocument();
    expect(screen.getByText(/1 widget, 1 report, 1 dashboard; shared with colleagues/)).toBeInTheDocument();
    expect(within(screen.getByRole("list", { name: "Counts per bucket" })).getByText("Late")).toBeInTheDocument();
    const looks = screen.getByRole("region", { name: "Look-alikes" });
    expect(within(looks).getByText("91% similar")).toBeInTheDocument();
    expect(within(looks).getByText("Differs")).toBeInTheDocument();

    const decision = screen.getByRole("region", { name: "Decision" });
    await userEvent.click(within(decision).getByRole("radio", { name: /Reject/ }));
    await userEvent.click(within(decision).getByRole("button", { name: "Reject and notify Arjun" }));
    expect(await within(decision).findByText("Say why it is rejected; the author will see the note.")).toBeInTheDocument();
    expect(decisions).toBe(0);

    await userEvent.click(within(decision).getByRole("radio", { name: /Merge into a certified field/ }));
    expect(within(decision).getByRole("combobox", { name: "Merge into" })).toHaveValue("dimension:schedule_risk_band");
    await userEvent.click(within(decision).getByRole("button", { name: "Merge and notify Arjun" }));
    expect(await within(decision).findByRole("alert")).toHaveTextContent(/gives a different answer \(7 vs 19\)/);
    await userEvent.click(within(decision).getByRole("button", { name: "Merge anyway" }));
    await waitFor(() => expect(decisions).toBe(2));
    const sent = calls.filter((c) => c.url.endsWith("/decision"));
    expect(sent[1]!.body).toMatchObject({ decision: "merge", merge_into: "dimension:schedule_risk_band", confirm_different: true });
    expect(sent[0]!.headers["Idempotency-Key"]).not.toEqual(sent[1]!.headers["Idempotency-Key"]);
    expect(await screen.findByText("Delay band was merged")).toBeInTheDocument();
  });

  it("explains why an author can't decide their own request", async () => {
    serve(admin, (url) => {
      if (url.includes("/certification/queue")) return json({ items: [QUEUE_ITEM] });
      if (url.endsWith("/requests/r1")) return json({ ...DETAIL, can_decide: { allowed: false, reason: "You can't certify your own definition. Another Admin must decide." } });
      return undefined;
    });
    renderAt("/govern/certification?r=r1");
    expect(await screen.findByText(/Another Admin must decide/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Certify and notify Arjun" })).toBeDisabled();
  });

  it("is not in a Creator's sidebar", async () => {
    serve(creator, (url) => (url.endsWith("/api/v1/home") ? json({ greeting: { name: "Arjun Rao", scopes: [], as_of: null }, shelves: { starter: [], published_to_my_role: [], shared_with_me: [], mine: [] }, empty_text: {}, can_create: true }) : undefined));
    renderAt("/");
    await screen.findByRole("navigation", { name: "Main" });
    expect(screen.queryByRole("link", { name: "Certification" })).not.toBeInTheDocument();
  });
});

const HOME_C = { greeting: { name: "Arjun Rao", scopes: [{ connection_id: "c1", connection_name: "TPG", text: "projects: Own CIO" }], as_of: null }, shelves: { starter: [], published_to_my_role: [], shared_with_me: [], mine: [] }, empty_text: {}, can_create: true };
const CATALOG = {
  connection_id: "c1", model_version_id: "v1", version_no: 4, scope: { summary: "projects: Own CIO", locked: true },
  measures: [{ ref: "measure:open_projects", label: "Open projects", plain_rule: "", format: "integer", entity: "projects" }],
  dimensions: [{ ref: "dimension:platform", label: "Platform", entity: "projects", temporal: false }],
  entities: [
    { key: "projects", label: "Projects", fields: [{ ref: "field:projects.days_late", key: "days_late", label: "Days late", data_type: "integer" }, { ref: "field:projects.request_id", key: "request_id", label: "Request", data_type: "text" }] },
    { key: "project_requests", label: "Requests", fields: [{ ref: "field:project_requests.id", key: "id", label: "Id", data_type: "text" }] },
  ],
  links: [["project_requests", "projects"]],
};

describe("field builder (US-078)", () => {
  it("builds bands with no gaps, previews counts in scope, saves, then requests certification", async () => {
    serve(creator, (url, init) => {
      if (url.endsWith("/api/v1/home")) return json(HOME_C);
      if (url.includes("/api/v1/query/catalog")) return json(CATALOG);
      if (url.endsWith("/api/v1/fields/preview")) return json({ kind: "band", edge_rules: "Bands Days late. 0 - 30: 0 up to 30; 30+: 30 and above.", as_of: null, scope_text: "projects: Own CIO", measure: "Projects", buckets: [{ label: "0 - 30", count: 12 }, { label: "30+", count: 7 }] });
      if (url.endsWith("/api/v1/fields") && init?.method === "POST") return json({ id: "f1", kind: "band", name: "Delay band", key: "delay_band", connection_id: "c1", model_version_id: "v1", definition: {}, plain_rule: "", badge: "personal", cert_status: "personal", certified_as: null, broken: false, broken_reason: null, updated_at: "2026-10-21T06:00:00Z" }, 201);
      if (url.endsWith("/api/v1/fields/f1")) return json({ id: "f1", kind: "band", name: "Delay band", key: "delay_band", connection_id: "c1", model_version_id: "v1", definition: { source: "field:projects.days_late", bands: [{ label: "0 - 30", lower: 0, upper: 30 }, { label: "30+", lower: 30, upper: null }], closing_max: null }, plain_rule: "", badge: "personal", cert_status: "personal", certified_as: null, broken: false, broken_reason: null, updated_at: "2026-10-21T06:00:00Z" });
      if (url.endsWith("/api/v1/certification/requests")) return json({ error: { code: "conflict", message: "Share it or add it to a dashboard before requesting certification." } }, 409);
      return undefined;
    });
    renderAt("/fields/new");
    await userEvent.click(await screen.findByRole("radio", { name: /Bands/ }));
    await userEvent.type(screen.getByRole("textbox", { name: "Field name" }), "Delay band");
    await userEvent.selectOptions(await screen.findByRole("combobox", { name: "Based on" }), "field:projects.days_late");
    const to = screen.getByRole("textbox", { name: "Band 1 up to" });
    await userEvent.clear(to);
    await userEvent.type(to, "-5");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByText("Band edges must go up: -5 must be above 0.")).toBeInTheDocument();
    await userEvent.clear(to);
    await userEvent.type(to, "30");
    const preview = screen.getByRole("region", { name: "Preview" });
    expect(await within(preview).findByText("30+", {}, { timeout: 3000 })).toBeInTheDocument();
    const sent = calls.filter((c) => c.url.endsWith("/fields/preview")).pop()!.body as { field: Record<string, unknown> };
    expect(sent.field).toMatchObject({ kind: "band", source_ref: "field:projects.days_late", edges: [0, 30], open_ended_top: true });
    expect(within(preview).getByText("19 in total · every row falls in exactly one bucket.")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Save and request certification" }));
    const dialog = await screen.findByRole("dialog", { name: "Request certification" });
    expect(calls.find((c) => c.url.endsWith("/api/v1/fields") && c.method === "POST")!.body).toMatchObject({ kind: "band", name: "Delay band", connection_id: "c1", edges: [0, 30] });
    await userEvent.type(within(dialog).getByLabelText(/Note for the reviewer/), "Leadership asked");
    await userEvent.click(within(dialog).getByRole("button", { name: "Send request" }));
    expect(await within(dialog).findByText(/Share it or add it to a dashboard/)).toBeInTheDocument();
    expect(calls.find((c) => c.url.endsWith("/certification/requests"))!.body).toEqual({ item: { kind: "band", id: "f1" }, note: "Leadership asked" });
  });

  it("builds a cross-table measure only along approved links", async () => {
    serve(creator, (url) => {
      if (url.endsWith("/api/v1/home")) return json(HOME_C);
      if (url.includes("/api/v1/query/catalog")) return json(CATALOG);
      if (url.endsWith("/api/v1/fields/preview")) return json({ kind: "join_measure", edge_rules: "Counts distinct requests linked to projects.", as_of: null, value: 42, semantics: "Counts distinct requests that have at least one project." });
      return undefined;
    });
    renderAt("/fields/new");
    await userEvent.click(await screen.findByRole("radio", { name: /Cross-table measure/ }));
    await userEvent.type(screen.getByRole("textbox", { name: "Field name" }), "Requests with projects");
    await userEvent.selectOptions(await screen.findByRole("combobox", { name: "Count" }), "project_requests");
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Of field" }), "id");
    expect(within(screen.getByRole("combobox", { name: /Only rows linked to/ })).getAllByRole("option").map((o) => o.textContent)).toEqual(["All rows", "Projects"]);
    await userEvent.selectOptions(screen.getByRole("combobox", { name: /Only rows linked to/ }), "projects");
    expect(await within(screen.getByRole("region", { name: "Preview" })).findByText("42", {}, { timeout: 3000 })).toBeInTheDocument();
    const sent = calls.filter((c) => c.url.endsWith("/fields/preview")).pop()!.body as { field: Record<string, unknown> };
    expect(sent.field).toMatchObject({ kind: "join_measure", path: ["project_requests", "projects"], agg: "count_distinct", target: "project_requests.id" });
  });
});

describe("edit bands in words (US-076)", () => {
  const band = (bands: { label: string; lower: number; upper: number | null }[]) => ({
    id: "f1", kind: "band", name: "Delay band", key: "delay_band", connection_id: "c1", model_version_id: "v1",
    definition: { source: "field:projects.days_late", bands, closing_max: null }, plain_rule: "", badge: "personal", cert_status: "personal",
    certified_as: null, broken: false, broken_reason: null, updated_at: "2026-10-21T06:00:00Z",
  });
  const BEFORE = [{ label: "0 - 30", lower: 0, upper: 30 }, { label: "30 - 60", lower: 30, upper: 60 }, { label: "60+", lower: 60, upper: null }];
  const AFTER = [{ label: "0 - 30", lower: 0, upper: 30 }, { label: "30+", lower: 30, upper: null }];
  const EDIT = {
    proposal: { kind: "band", name: "Delay band", connection_id: "c1", source_ref: "field:projects.days_late", edges: [0, 30], labels: ["0 - 30", "30+"], open_ended_top: true },
    changes: ["The top band starts at 30 and has no upper edge. Edges above it (60) are removed."],
    diff: { before: [{ ...BEFORE[0], status: "same" }, { ...BEFORE[1], status: "removed" }, { ...BEFORE[2], status: "removed" }], after: [{ ...AFTER[0], status: "same" }, { ...AFTER[1], status: "new" }] },
    rule_before: "", rule_after: "Bands Days late. 0 - 30: 0 up to 30; 30+: 30 and above.",
  };

  it("shows the change as a diff and applies it only on confirm", async () => {
    let current = band(BEFORE);
    serve(creator, (url, init) => {
      if (url.endsWith("/api/v1/home")) return json(HOME_C);
      if (url.includes("/api/v1/query/catalog")) return json(CATALOG);
      if (url.endsWith("/api/v1/fields/preview")) return json({ kind: "band", edge_rules: "", as_of: null, buckets: [] });
      if (url.endsWith("/fields/f1/edit-in-words")) {
        const text = JSON.parse(String(init?.body)).text as string;
        return text.includes("move") ? json({ error: { code: "validation_error", message: "Band edges overlap: 30 must be above 70." } }, 400) : json(EDIT);
      }
      if (url.endsWith("/api/v1/fields/f1") && init?.method === "PATCH") { current = band(AFTER); return json(current); }
      if (url.endsWith("/api/v1/fields/f1")) return json(current);
      return undefined;
    });
    renderAt("/fields/f1");
    const words = await screen.findByRole("textbox", { name: "Change the bands in words" });
    await userEvent.type(words, "move 30 to 70");
    await userEvent.click(screen.getByRole("button", { name: "Show the change" }));
    expect(await screen.findByText("Band edges overlap: 30 must be above 70.")).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();

    await userEvent.clear(words);
    await userEvent.type(words, "make the top band 30 and above{Enter}");
    const dialog = await screen.findByRole("dialog", { name: "Change the bands of Delay band?" });
    expect(within(dialog).getByText(/Edges above it \(60\) are removed/)).toBeInTheDocument();
    expect(within(within(dialog).getByRole("list", { name: "Bands now" })).getAllByText("Removed")).toHaveLength(2);
    expect(within(within(dialog).getByRole("list", { name: "Bands after" })).getByText("New")).toBeInTheDocument();
    expect(calls.some((c) => c.method === "PATCH")).toBe(false);
    await userEvent.click(within(dialog).getByRole("button", { name: "Apply" }));
    await waitFor(() => expect(calls.find((c) => c.method === "PATCH")!.body).toEqual(EDIT.proposal));
    expect(await screen.findByText("Bands changed")).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "Band 2 name" })).toHaveValue("30+");
    expect(screen.queryByRole("textbox", { name: "Band 3 name" })).not.toBeInTheDocument();
  });
});

const CHECKS_OK = [
  { name: "engine_supported", ok: true, detail: "postgres" }, { name: "page_filters_placeholder", ok: true, detail: "found" }, { name: "parse", ok: true },
  { name: "single_select", ok: true }, { name: "read_only", ok: true }, { name: "allowed_objects", ok: true }, { name: "visible_fields", ok: true },
  { name: "placeholder_position", ok: true }, { name: "scope_applied", ok: true, detail: "projects: Own CIO" }, { name: "limits", ok: true, detail: "10,000 rows, 20 s" },
];

describe("custom SQL (US-080)", () => {
  it("drafts with chat, stops checks at the first failure, runs only after passing, then saves a Custom SQL widget", async () => {
    let drafts = 0;
    serve(creator, (url, init) => {
      if (url.endsWith("/api/v1/home")) return json(HOME_C);
      if (url.includes("/api/v1/query/catalog")) return json({ ...CATALOG, engine: "postgres", entities: CATALOG.entities.map((e) => ({ ...e, table: e.key, fields: e.fields.map((f) => ({ ...f, column: f.key })) })) });
      if (url.endsWith("/api/v1/reports")) return json({ items: [] });
      if (url.endsWith("/custom-sql/draft")) {
        drafts += 1;
        return json({ sql: drafts === 1 ? "DELETE FROM projects" : "SELECT platform, count(*) AS n FROM projects WHERE :page_filters GROUP BY platform", source: "rules", explanation: null, drafted_by_ai: false, note: "This is a draft." });
      }
      if (url.endsWith("/custom-sql/check")) {
        const body = JSON.parse(String(init!.body));
        if (String(body.sql).startsWith("DELETE")) {
          return json({ error: { code: "sql_not_select", message: "Write exactly one SELECT statement.", details: { step: "single_select", checks: [
            { name: "engine_supported", ok: true }, { name: "page_filters_placeholder", ok: true, detail: "not used" }, { name: "parse", ok: true },
            { name: "single_select", ok: false, code: "sql_not_select", detail: "Write exactly one SELECT statement." }] } } }, 422);
        }
        return json({ ok: true, checks: CHECKS_OK, check_token: "token-123456", warnings: [], follows_page_filters: true, model_version: 4, expires_in_s: 300 });
      }
      if (url.endsWith("/custom-sql/run")) {
        return json({ columns: [{ name: "platform", kind: "dimension", label: "platform", ref: "", data_type: "text", format: null }, { name: "n", kind: "measure", label: "n", ref: "", data_type: "integer", format: "integer" }],
          rows: [["Cloud", 4], ["ERP", 2]], row_count: 2, truncated: false, as_of: null, duration_ms: 812, badge: "custom_sql", suggested_visual: "ranked_bar",
          visuals: { suitable: ["ranked_bar", "table"], recommended: "ranked_bar", disabled: [{ visual: "trend", label: "Trend", reason: "Trend needs a date" }] }, scope: { summary: "projects: Own CIO" } });
      }
      if (url.endsWith("/widgets/custom-sql")) return json({ widget_id: "w9", custom_sql_id: "s9", badges: ["custom_sql"], visual: "ranked_bar", follow_page_filters: true, warnings: [], placement: null }, 201);
      if (url.endsWith("/api/v1/custom-sql/s9")) return json({ id: "s9", title: "Overdue by platform", sql: "SELECT 1", dialect: "postgres", connection_id: "c1", model_version_id: "v1", follows_page_filters: true, drafted_by_ai: false, cert_status: "personal", badge: "custom_sql", last_check: null, updated_at: "2026-10-21T06:00:00Z" });
      return undefined;
    });
    renderAt("/sql/new");
    const ask = await screen.findByLabelText("Describe the query in words");
    await userEvent.type(ask, "remove old projects");
    await userEvent.click(screen.getByRole("button", { name: "Draft SQL" }));
    await waitFor(() => expect(screen.getByRole("textbox", { name: "SQL" })).toHaveTextContent("DELETE FROM projects"));
    await userEvent.click(screen.getByRole("button", { name: "Run" }));
    const checks = await screen.findByRole("region", { name: "Checks" });
    expect(within(checks).getByText("Exactly one SELECT statement")).toBeInTheDocument();
    expect(within(checks).getAllByText("Write exactly one SELECT statement.")[0]).toBeInTheDocument();
    expect(within(checks).getByText(/Only fields you can see/).textContent).toMatch(/not checked/);
    expect(calls.some((c) => c.url.endsWith("/custom-sql/run"))).toBe(false);
    expect(screen.queryByRole("region", { name: "Save as a widget" })).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Draft SQL" }));
    await waitFor(() => expect(screen.getByRole("textbox", { name: "SQL" })).toHaveTextContent(":page_filters"));
    await userEvent.click(screen.getByRole("button", { name: "Run" }));
    const result = await screen.findByRole("region", { name: "Result" });
    expect(within(result).getByText(/2 rows · 0.8 s/)).toBeInTheDocument();
    expect(within(await screen.findByRole("region", { name: "Checks" })).getByText("All checks passed · model v4")).toBeInTheDocument();
    expect(calls.find((c) => c.url.endsWith("/custom-sql/run"))!.body).toMatchObject({ check_token: "token-123456" });

    const save = screen.getByRole("region", { name: "Save as a widget" });
    expect(within(save).getByRole("radio", { name: "Trend" })).toBeDisabled();
    await userEvent.type(within(save).getByLabelText("Title"), "Overdue by platform");
    await userEvent.click(within(save).getByRole("button", { name: "Save widget" }));
    await waitFor(() => expect(window.location.pathname).toBe("/sql/s9"));
    expect(calls.find((c) => c.url.endsWith("/widgets/custom-sql"))!.body).toMatchObject({ title: "Overdue by platform", visual: "ranked_bar", follow_page_filters: true, check_token: "token-123456" });
  });
});
