import { QueryClient } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Me } from "../api/types";
import { AppRoutes, Providers } from "../app/App";
import { TestAuthProvider } from "../app/auth";
import { setConfigForTests } from "../app/config";
import { settle } from "../features/reports/ReportCanvasPage";

vi.mock("echarts/core", () => ({
  use: vi.fn(),
  registerTheme: vi.fn(),
  init: () => ({ setOption: vi.fn(), on: vi.fn(), resize: vi.fn(), dispose: vi.fn() }),
}));

const creator: Me = {
  user_id: "u2", display_name: "Cleo Creator", email: null, tenant_id: "t1", roles: ["creator"], level: "creator", audience_roles: [],
  permissions: ["dashboards.view", "ask.use", "drill.use", "definitions.view", "widgets.create", "reports.create", "dashboards.create", "share.create"],
  scope_summary: [], locale: null, time_zone: null,
};

type Handler = (url: string, init?: RequestInit) => Response | undefined;
const calls: { url: string; method: string; body: unknown }[] = [];

function serve(me: Me, handler: Handler) {
  calls.length = 0;
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ url, method: init?.method ?? "GET", body: init?.body ? JSON.parse(String(init.body)) : undefined });
    if (url.endsWith("/api/v1/me")) return json(me);
    if (url.includes("/api/v1/notifications")) return json({ items: [{ id: "n1", kind: "dashboard.published", title: "Gulf delivery was published to your role", body: "Version 1 is now live.", link: "/dashboards/d1", read_at: null, created_at: "" }], unread_count: 1 });
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

const SPEC = { model_version_id: "v1", connection_id: "c1", measure: { ref: "measure:open_projects" }, breakdowns: [{ ref: "dimension:region" }] };
const COLS = [{ name: "region", kind: "dimension", label: "Region", ref: "dimension:region", data_type: "text", format: null },
  { name: "open_projects", kind: "measure", label: "Open projects", ref: "measure:open_projects", data_type: "integer", format: "integer" }];

function answer(turnId: string, extra: Record<string, unknown> = {}) {
  return sse([["intent", {}], ["plan", { chips: [{ kind: "breakdown", label: "Region" }] }],
    ["viz", { visual: "ranked_bar", alternatives: [], disabled: [], note: null, chart_spec: { type: "table", x: "region", series: ["open_projects"] } }],
    ["narrative", { headline: "KSA has the most open projects.", sentences: [] }],
    ["complete", { turn_id: turnId, certified: true, scope_text: "projects: Gulf regions", filters_text: "None", as_of: null, interpreted: [],
      columns: COLS, rows: [["KSA", 4], ["GCC - UAE", 3]], connection: "TPG", model_version: 1, spec: SPEC, can_pin: true, ...extra }]]);
}

beforeEach(() => setConfigForTests({ apiBaseUrl: "http://api.test", oidcAuthority: "http://kc", oidcClientId: "tela-web" }));
afterEach(() => vi.unstubAllGlobals());

describe("canvas layout", () => {
  it("settles overlaps by pushing down and compacting up", () => {
    const out = settle([{ id: "a", x: 0, y: 0, w: 12, h: 4 }, { id: "b", x: 0, y: 0, w: 6, h: 4 }], "b");
    expect(out.find((b) => b.id === "b")).toMatchObject({ x: 0, y: 0 });
    expect(out.find((b) => b.id === "a")).toMatchObject({ y: 4 });
  });
});

describe("ask part 2", () => {
  it("refines with a chip, offers Undo, and redirects rule changes", async () => {
    serve(creator, (url, init) => {
      if (url.endsWith("/api/v1/ask/turns") && init?.method === "POST") return json({ turn_id: "t1", conversation_id: "c1" }, 202);
      if (url.endsWith("/refine") && JSON.parse(String(init?.body)).instruction) {
        return json({ turn_id: null, message: "That would change how Open projects is counted…" }, 202);
      }
      if (url.endsWith("/t1/refine")) {
        return json({ turn_id: "t2", conversation_id: "c1", changes: ["Top 5"] }, 202);
      }
      if (url.endsWith("/t1/stream")) return answer("t1");
      if (url.endsWith("/t2/stream")) return answer("t2", { refined_from: "t1", spec: { ...SPEC, limit: 5 } });
      return undefined;
    });
    renderAt("/ask?q=open%20projects%20by%20region");
    await screen.findByRole("heading", { name: /KSA has the most open projects/ });
    await userEvent.click(screen.getByRole("button", { name: "Top 5" }));
    await waitFor(() => expect(screen.getAllByRole("heading", { name: /KSA has the most/ })).toHaveLength(2));
    expect(calls.find((c) => c.url.endsWith("/refine"))!.body).toEqual({ chip_ops: [{ op: "set_limit", value: 5 }] });
    // Undo from the toast and from the refined answer card (US-140).
    expect(await screen.findByText("Refined: Top 5")).toBeInTheDocument();
    expect(within(screen.getAllByRole("article", { name: "Answer" })[1]!).getByRole("button", { name: "Undo" })).toBeInTheDocument();
    // Quick refinements and "Change this answer" sit under the latest answer only (US-261).
    expect(screen.getAllByRole("button", { name: "Top 5" })).toHaveLength(1);
    expect(screen.queryByLabelText("Refine this answer")).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "Change this answer" }));
    await userEvent.type(screen.getByLabelText("Refine this answer"), "count cancelled projects too");
    await userEvent.click(screen.getByRole("button", { name: "Refine" }));
    expect(await screen.findByText(/would change how Open projects is counted/)).toBeInTheDocument();
  });

  it("pins with only suitable visuals enabled", async () => {
    serve(creator, (url, init) => {
      if (url.endsWith("/api/v1/ask/turns") && init?.method === "POST") return json({ turn_id: "t1", conversation_id: "c1" }, 202);
      if (url.endsWith("/stream")) return answer("t1");
      if (url.endsWith("/visuals/suitable")) return json({ suitable: ["ranked_bar", "table"], recommended: "ranked_bar", disabled: [{ visual: "trend", label: "Trend", reason: "Trend needs a date breakdown" }] });
      if (url.endsWith("/api/v1/reports") && init?.method === "GET") return json({ items: [{ id: "r1", title: "Delivery", mine: true, widgets: 0, published: false, updated_at: "", owner_name: "Me" }] });
      if (url.endsWith("/api/v1/reports/r1")) return json({ id: "r1", title: "Delivery", sections: [{ id: "s1", title: "Section 1", items: [] }] });
      if (url.endsWith("/api/v1/widgets") && init?.method === "POST") return json({ id: "w1", reused: false, report: { id: "r1", title: "Delivery", revision: 1 }, placement: { x: 0, y: 0, w: 6, h: 4 } }, 201);
      return undefined;
    });
    renderAt("/ask?q=open%20projects%20by%20region");
    await userEvent.click(await screen.findByRole("button", { name: "Pin" }));
    const dialog = await screen.findByRole("dialog", { name: "Pin to a report" });
    const trend = await within(dialog).findByRole("radio", { name: /Trend/ });
    expect(trend).toBeDisabled();
    expect(within(dialog).getByRole("radio", { name: /Ranked bars/ })).toBeChecked();
    await within(dialog).findByRole("option", { name: "Delivery" });
    await userEvent.selectOptions(within(dialog).getByRole("combobox", { name: "Report" }), "r1");
    await userEvent.click(within(dialog).getByRole("button", { name: "Pin" }));
    expect(await within(dialog).findByText(/Pinned as a live widget/)).toBeInTheDocument();
    const pinBody = calls.find((c) => c.url.endsWith("/api/v1/widgets") && c.method === "POST")!.body as Record<string, unknown>;
    expect(pinBody).toMatchObject({ from_turn_id: "t1", visual: "ranked_bar", target: { report_id: "r1" }, follow_page_filters: true });
  });
});


describe("fields from Ask (US-075)", () => {
  const fieldCreator: Me = { ...creator, permissions: [...creator.permissions, "fields.create"] };
  const HOME = { greeting: { name: "Cleo Creator", scopes: [{ connection_id: "c1", connection_name: "TPG", text: "projects: Gulf regions" }], as_of: null }, shelves: { starter: [], published_to_my_role: [], shared_with_me: [], mine: [] }, empty_text: {}, can_create: true };
  const CAT = {
    connection_id: "c1", model_version_id: "v1", version_no: 1, scope: { summary: "projects: Gulf regions", locked: true }, measures: [],
    dimensions: [{ ref: "dimension:platform", label: "Platform", entity: "projects", temporal: false }, { ref: "dimension:region", label: "Region", entity: "projects", temporal: false }],
    entities: [{ key: "projects", label: "Projects", fields: [] }], links: [],
  };
  const builderMocks: Handler = (url) => {
    if (url.endsWith("/api/v1/home")) return json(HOME);
    if (url.includes("/api/v1/query/catalog")) return json(CAT);
    if (url.endsWith("/api/v1/query/filter-values")) return json({ values: ["Network One", "Magnati", "Network Flex"] });
    if (url.endsWith("/api/v1/fields/preview")) return json({ kind: "grouping", edge_rules: "", as_of: null, buckets: [] });
    return undefined;
  };

  it("opens the field builder with the draft from chat, matching typed values to the data", async () => {
    serve(fieldCreator, (url, init) => {
      if (url.endsWith("/api/v1/ask/turns") && init?.method === "POST") return json({ turn_id: "t1", conversation_id: "c1" }, 202);
      if (url.endsWith("/t1/stream")) {
        return sse([["intent", { intent: "build_field" }],
          ["narrative", { headline: "Here is a draft grouping of Platform.", sentences: [{ text: "Group Platform into Core and Growth." }, { text: "Pick the values for Growth." }] }],
          ["complete", { turn_id: "t1", certified: false, action: { kind: "build_field", source_label: "Platform", understood: ["Group Platform into Core and Growth."], missing: ["Pick the values for Growth."],
            draft: { kind: "grouping", name: "Platform groups", connection_id: "c1", source_ref: "dimension:platform", groups: [{ label: "Core", values: ["network one", "Legacy"] }, { label: "Growth", values: [] }] } } }]]);
      }
      return builderMocks(url, init);
    });
    renderAt("/ask?q=group%20platforms%20into%20Core%20and%20Growth");
    const card = await screen.findByRole("article", { name: "Answer" });
    expect(within(card).getByText("Pick the values for Growth.")).toBeInTheDocument();
    expect(within(card).queryByRole("button", { name: "Pin" })).not.toBeInTheDocument();
    await userEvent.click(within(card).getByRole("link", { name: "Open in the field builder" }));

    expect(await screen.findByText("Drafted from your words. Check it, then save.")).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "Field name" })).toHaveValue("Platform groups");
    expect(await screen.findByRole("combobox", { name: "Based on" })).toHaveValue("dimension:platform");
    expect(screen.getByRole("textbox", { name: "Group 1 name" })).toHaveValue("Core");
    expect(screen.getByRole("textbox", { name: "Group 2 name" })).toHaveValue("Growth");
    const core = within(screen.getAllByRole("list", { name: "Values in this group" })[0]!);
    await waitFor(() => expect(core.getByRole("checkbox", { name: "Network One" })).toBeChecked());
    expect(screen.getByText("Not in your data, so left out: Legacy.")).toBeInTheDocument();
    expect(calls.some((c) => c.url.endsWith("/api/v1/fields") && c.method === "POST")).toBe(false); // nothing saved
  });

  it("offers New field from a dimension of an answer", async () => {
    serve(fieldCreator, (url, init) => {
      if (url.endsWith("/api/v1/ask/turns") && init?.method === "POST") return json({ turn_id: "t1", conversation_id: "c1" }, 202);
      if (url.endsWith("/t1/stream")) return answer("t1");
      return builderMocks(url, init);
    });
    renderAt("/ask?q=open%20projects%20by%20region");
    await screen.findByRole("heading", { name: /KSA has the most open projects/ });
    await userEvent.click(screen.getByRole("button", { name: "New field" }));
    await userEvent.click(await screen.findByRole("menuitem", { name: "Group Region values…" }));
    expect(await screen.findByRole("combobox", { name: "Based on" })).toHaveValue("dimension:region");
    expect(screen.getByRole("textbox", { name: "Field name" })).toHaveValue("Region groups");
  });

  it("has no New field menu without the permission", async () => {
    serve(creator, (url, init) => {
      if (url.endsWith("/api/v1/ask/turns") && init?.method === "POST") return json({ turn_id: "t1", conversation_id: "c1" }, 202);
      if (url.endsWith("/t1/stream")) return answer("t1");
      return undefined;
    });
    renderAt("/ask?q=open%20projects%20by%20region");
    await screen.findByRole("heading", { name: /KSA has the most open projects/ });
    expect(screen.queryByRole("button", { name: "New field" })).not.toBeInTheDocument();
  });
});
