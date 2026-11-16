import { QueryClient } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { ValueSynonym } from "../api/knowledge";
import type { Me } from "../api/types";
import { AppRoutes, Providers } from "../app/App";
import { TestAuthProvider } from "../app/auth";
import { setConfigForTests } from "../app/config";

vi.mock("echarts/core", () => ({
  use: vi.fn(),
  registerTheme: vi.fn(),
  init: () => ({ setOption: vi.fn(), on: vi.fn(), resize: vi.fn(), dispose: vi.fn() }),
}));

const admin: Me = {
  user_id: "u1", display_name: "Ada Admin", email: null, tenant_id: "t1", roles: ["admin"], level: "admin", audience_roles: [],
  permissions: ["dashboards.view", "knowledge.manage", "connections.manage", "ask.use"],
  scope_summary: [], locale: null, time_zone: null,
};

const syn = (over: Partial<ValueSynonym>): ValueSynonym => ({
  id: "s1", dimension: "region", dimension_label: "Region", stored_value: "KSA", synonym: "Saudi Arabia", source: "pack",
  status: "pending_review", note: null, created_at: "2026-10-28T08:00:00Z", reviewed_at: null, proposed_by: null, reviewed_by: null,
  in_data: true, ...over,
});

const calls: { url: string; method: string; body: unknown }[] = [];

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

function serve(handler: (url: string, init?: RequestInit) => Response | undefined) {
  calls.length = 0;
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ url, method: init?.method ?? "GET", body: init?.body ? JSON.parse(String(init.body)) : undefined });
    if (url.endsWith("/api/v1/me")) return json(admin);
    if (url.endsWith("/api/v1/connections")) return json({ items: [{ id: "c1", name: "TPG", engine: "postgres", status: "connected" }] });
    return handler(url, init) ?? (url.includes("/api/v1/notifications") ? json({ items: [], unread_count: 0, next_cursor: null, kinds: [] }) : json({ error: { code: "not_found", message: "Not found" } }, 404));
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

describe("Value synonyms (US-173, SCR-13)", () => {
  it("lists synonyms waiting for review and approves them", async () => {
    let items = [syn({}), syn({ id: "s2", synonym: "network", stored_value: "Network Flex", dimension: "platform", dimension_label: "Platform", source: "clarification", proposed_by: "Cleo Creator" }),
      syn({ id: "s3", synonym: "Infra", stored_value: "Infrastructure", dimension: "portfolio", dimension_label: "Portfolio", source: "admin", status: "approved" })];
    serve((url, init) => {
      if (url.endsWith("/api/v1/knowledge/value-synonyms/review")) {
        const body = JSON.parse(String(init?.body)) as { ids: string[] };
        items = items.map((s) => (body.ids.includes(s.id) ? { ...s, status: "approved" } : s));
        return json({ updated: body.ids.length, status: "approved" });
      }
      if (url.includes("/api/v1/knowledge/value-synonyms?connection_id=c1")) {
        return json({ items, counts: { pending_review: 2, approved: 1 }, index: { values: 128, dimensions: 14, refreshed_at: "2026-10-28T08:00:00Z" } });
      }
      return undefined;
    });
    renderAt("/admin/knowledge/values");
    const table = await screen.findByRole("table", { name: "Value synonyms" });
    // The page is no longer in the menu (v2 simplified Admin); it still opens by its URL.
    expect(screen.getByText(/128 values of 14 fields indexed/)).toBeInTheDocument();
    expect(screen.getByText(/^2 synonyms are waiting for review\. 1 came with the pack/)).toBeInTheDocument();
    const network = within(table).getByRole("row", { name: /network/ });
    expect(within(network).getByText("Chosen in Ask")).toBeInTheDocument();
    expect(within(network).getByText("by Cleo Creator")).toBeInTheDocument();
    await userEvent.click(within(table).getByRole("button", { name: "Approve Saudi Arabia" }));
    await waitFor(() => expect(calls.some((c) => c.url.endsWith("/value-synonyms/review"))).toBe(true));
    expect(calls.find((c) => c.url.endsWith("/value-synonyms/review"))?.body).toEqual({ decision: "approve", ids: ["s1"] });
    await waitFor(() => expect(screen.getByText("1 synonym is waiting for review.")).toBeInTheDocument());
  });

  it("approves every pack synonym at once (US-215)", async () => {
    let items = [syn({}), syn({ id: "s2", synonym: "Dubai", stored_value: "GCC - UAE" }),
      syn({ id: "s3", synonym: "network", stored_value: "Network Flex", dimension: "platform", dimension_label: "Platform", source: "clarification" })];
    serve((url) => {
      if (url.endsWith("/api/v1/knowledge/value-synonyms/approve-pack")) {
        items = items.map((s) => (s.source === "pack" ? { ...s, status: "approved" } : s));
        return json({ updated: 2, status: "approved" });
      }
      if (url.includes("/api/v1/knowledge/value-synonyms?connection_id=c1")) {
        return json({ items, counts: {}, index: { values: 128, dimensions: 14, refreshed_at: null } });
      }
      return undefined;
    });
    renderAt("/admin/knowledge/values");
    expect(await screen.findByText(/2 came with the pack; until approved, Ask doesn't use them\./)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Approve all from the pack (2)" }));
    await waitFor(() => expect(calls.find((c) => c.url.endsWith("/approve-pack"))?.body).toEqual({ connection_id: "c1" }));
    await waitFor(() => expect(screen.getByText("1 synonym is waiting for review.")).toBeInTheDocument());
    expect(screen.queryByRole("button", { name: /Approve all from the pack/ })).not.toBeInTheDocument();
  });

  it("tries a phrase and adds a synonym for a stored value", async () => {
    serve((url, init) => {
      if (url.endsWith("/api/v1/knowledge/values/link")) {
        return json({ links: [{ dimension: "region", stored: "KSA", label: "KSA", phrase: "saudi arabia", how: "synonym", score: 0.95 }],
          choices: [{ dimension: "platform", phrase: "network", options: [{ stored: "Network Flex", label: "Network Flex" }, { stored: "Network One", label: "Network One" }] }] });
      }
      if (url.includes("/api/v1/knowledge/values?connection_id=c1")) {
        return json({ dimensions: [{ key: "region", label: "Region", values: [{ stored: "KSA", label: "KSA", synonyms: [] }, { stored: "Jordan", label: "Jordan", synonyms: [] }] }] });
      }
      if (url.endsWith("/api/v1/knowledge/value-synonyms") && init?.method === "POST") return json({ id: "s9", status: "approved" }, 201);
      if (url.includes("/api/v1/knowledge/value-synonyms?connection_id=c1")) {
        return json({ items: [], counts: {}, index: { values: 2, dimensions: 1, refreshed_at: null } });
      }
      return undefined;
    });
    renderAt("/admin/knowledge/values");
    await userEvent.type(await screen.findByLabelText("Phrase"), "Open projects in Saudi Arabia on network");
    await userEvent.click(screen.getByRole("button", { name: "Try" }));
    expect(await screen.findByText(/“saudi arabia” →/)).toHaveTextContent("“saudi arabia” → region: KSA (an approved synonym)");
    expect(screen.getByText("“network” could be Network Flex or Network One: Ask will ask which.")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Add synonym" }));
    const dialog = await screen.findByRole("dialog", { name: "Add a synonym" });
    await waitFor(() => expect(within(dialog).getByRole("option", { name: "Region" })).toBeInTheDocument());
    await userEvent.selectOptions(within(dialog).getByLabelText("Field"), "region");
    await userEvent.selectOptions(within(dialog).getByLabelText("Stored value"), "Jordan");
    await userEvent.type(within(dialog).getByLabelText("Words people use"), "Amman");
    await userEvent.click(within(dialog).getByRole("button", { name: "Add synonym" }));
    await waitFor(() => expect(calls.find((c) => c.method === "POST" && c.url.endsWith("/value-synonyms"))?.body).toEqual(
      { connection_id: "c1", dimension: "region", stored_value: "Jordan", synonym: "Amman" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });
});
