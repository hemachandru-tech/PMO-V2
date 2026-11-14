import { QueryClient } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { ModelContents, PackList } from "../api/packs";
import type { Me } from "../api/types";
import { AppRoutes, Providers } from "../app/App";
import { TestAuthProvider } from "../app/auth";
import { setConfigForTests } from "../app/config";

const admin: Me = {
  user_id: "u1", display_name: "Ada Admin", email: null, tenant_id: "t1", roles: ["admin"], level: "admin", audience_roles: [],
  permissions: ["dashboards.view", "packs.author", "starter.manage"], scope_summary: [], locale: null, time_zone: null,
};
const LIST: PackList = { items: [], create: { methods: ["ai", "blank", "clone", "model"], planned: {} } };
const CONTENTS: ModelContents = {
  connection_id: "c1", name: "TPG Postgres", model_version_id: "v1", version_no: 2, installed_pack: "pmo_tpg",
  counts: { entities: 9, fields: 120, links: 8, links_not_accepted: 1, dimensions: 40, personal_fields: 3 },
  measures: [
    { key: "open_projects", label: "Open projects", entity: "projects", status: "certified" },
    { key: "old_count", label: "Old count", entity: "projects", status: "deprecated" },
  ],
  knowledge: [
    { kind: "glossary", count: 12, available: true, reason: null },
    { kind: "chart_patterns", count: 30, available: false, reason: "not approved on this connection (pending review)" },
    { kind: "value_synonyms", count: 1, available: true, reason: null },
  ],
  reports: [{ id: "r1", title: "Projects overview", origin: "user" }, { id: "r2", title: "Lifecycle", origin: "starter" }],
  dashboards: [{ id: "d1", title: "Ops review", tabs: 1 }],
  reporting_date: "2026-09-30",
};
const calls: { url: string; method: string; body: unknown }[] = [];

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

function serve() {
  calls.length = 0;
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    const body = typeof init?.body === "string" ? JSON.parse(init.body) : undefined;
    calls.push({ url, method: init?.method ?? "GET", body });
    if (url.endsWith("/api/v1/me")) return json(admin);
    if (url.endsWith("/api/v1/packs") && init?.method === "POST") {
      return json({ id: "p-new", pack_key: "acme_pack", version: "0.1.0", title: "Acme pack", status: "draft", owner: "tenant", editable: true,
        released_at: null, updated_at: null, based_on_pack_id: null, revision: "r1", counts: { entities: 9, links: 8, measures: 1, dimensions: 40 },
        installed_on: [], checks: { valid: true, errors: [], warnings: [] },
        left_out: [{ kind: "measure", key: "old_count", reason: "not certified (tick it to take it)" },
                   { kind: "widget", key: "My SQL", reason: "custom SQL", report: "Projects overview" }] }, 201);
    }
    if (url.endsWith("/api/v1/packs")) return json(LIST);
    if (url.endsWith("/api/v1/packs/from-model/models")) {
      return json({ items: [{ connection_id: "c1", name: "TPG Postgres", engine: "postgres", model_version_id: "v1", version_no: 2,
        counts: { entities: 9, links: 8, measures: 2, dimensions: 40 } }] });
    }
    if (url.endsWith("/api/v1/packs/from-model/models/c1")) return json(CONTENTS);
    if (url.includes("/api/v1/notifications")) return json({ items: [], unread_count: 0, next_cursor: null, kinds: [] });
    return json({ error: { code: "not_found", message: "Not found" } }, 404);
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

describe("Pack from an approved model (US-236)", () => {
  it("offers the model's parts, leaves uncertified measures off and lists what was left out", async () => {
    serve();
    renderAt("/admin/packs");
    await userEvent.click(await screen.findByRole("button", { name: /Create/ }));
    await userEvent.click(await screen.findByRole("menuitem", { name: /From an approved model/ }));
    const dialog = await screen.findByRole("dialog", { name: /New pack from an approved model/ });
    expect(within(dialog).getByRole("button", { name: "Create pack" })).toBeDisabled();
    await userEvent.selectOptions(await within(dialog).findByLabelText("Approved model"), "c1");

    expect(await within(dialog).findByTestId("model-summary")).toHaveTextContent("9 entities, 120 fields, 8 accepted links, 1 certified measures, 40 dimensions.");
    expect(within(dialog).getByTestId("model-summary")).toHaveTextContent("3 personal or confidential fields keep their tags.");
    expect(within(dialog).getByRole("checkbox", { name: /Old count/ })).not.toBeChecked();
    expect(within(dialog).getByRole("checkbox", { name: /Glossary \(12\)/ })).toBeChecked();
    const patterns = within(dialog).getByRole("checkbox", { name: /Chart patterns/ });
    expect(patterns).toBeDisabled();
    expect(within(dialog).getByText(/not approved on this connection/)).toBeInTheDocument();
    expect(within(dialog).getByLabelText("Title")).toHaveValue("TPG Postgres pack");
    expect(within(dialog).getByLabelText("Key")).toHaveValue("tpg_postgres_pack");

    await userEvent.click(within(dialog).getByRole("checkbox", { name: /Projects overview/ }));
    await userEvent.click(within(dialog).getByRole("checkbox", { name: /Ops review/ }));
    await userEvent.click(within(dialog).getByRole("button", { name: "Create pack" }));
    await waitFor(() => expect(calls.find((c) => c.method === "POST")?.body).toEqual({
      method: "model", pack_key: "tpg_postgres_pack", title: "TPG Postgres pack",
      options: { connection_id: "c1", measures: [], knowledge: ["glossary", "value_synonyms"], report_ids: ["r1"], dashboard_ids: ["d1"] },
    }));
    const done = await screen.findByRole("dialog", { name: /Acme pack is ready/ });
    const left = within(done).getByRole("list", { name: "Left out" });
    expect(within(left).getAllByRole("listitem")).toHaveLength(2);
    expect(left).toHaveTextContent("My SQL (widget, Projects overview): custom SQL");
    expect(within(done).getByRole("button", { name: "Open the pack" })).toBeInTheDocument();
  });
});
