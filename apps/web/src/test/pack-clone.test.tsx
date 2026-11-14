import { QueryClient } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { PackDetail, PackList, PackVersion } from "../api/packs";
import type { Me } from "../api/types";
import { AppRoutes, Providers } from "../app/App";
import { TestAuthProvider } from "../app/auth";
import { setConfigForTests } from "../app/config";
import type { RepointPreview } from "../features/admin/packs/clone/api";

const admin: Me = {
  user_id: "u1", display_name: "Ada Admin", email: null, tenant_id: "t1", roles: ["admin"], level: "admin", audience_roles: [],
  permissions: ["dashboards.view", "packs.author", "starter.manage"], scope_summary: [], locale: null, time_zone: null,
};
const version = (over: Partial<PackVersion>): PackVersion => ({
  id: "p-pmo", pack_key: "pmo_tpg", version: "2.1.0", title: "PMO starter pack (TPG)", status: "released", owner: "system", editable: false,
  released_at: "2026-10-27T00:00:00Z", updated_at: "2026-11-05T10:00:00Z", based_on_pack_id: null, revision: "r1",
  counts: { entities: 2, links: 1, measures: 2, dimensions: 1 }, installed_on: [], ...over,
});
const LIST: PackList = {
  items: [{ pack_key: "pmo_tpg", owner: "system", title: "PMO starter pack (TPG)", latest: version({}), versions: [version({})], installed: 0 }],
  create: { methods: ["blank", "clone"], planned: { model: "US-236", ai: "US-237" } },
};
const PMO: PackDetail = {
  ...version({}),
  manifest: {
    pack_key: "pmo_tpg", pack_version: "2.1.0", title: "PMO starter pack (TPG)", released_at: "2026-10-27T00:00:00Z", signature: { primary_entity: "projects" },
    entities: [
      { key: "projects", label: "Projects", object_candidates: ["projects"], primary_key: ["project_id"], fields: [
        { key: "project_id", label: "Project id", data_type: "text", columns: ["project_id"], required: true },
        { key: "status", label: "Status", data_type: "text", columns: ["status"], required: true, values: ["Active", "Inactive"] },
      ] },
      { key: "timesheets", label: "Timesheets", object_candidates: ["timesheets"], primary_key: ["timesheet_id"], fields: [
        { key: "timesheet_id", label: "Timesheet id", data_type: "text", columns: ["timesheet_id"] },
      ] },
    ],
    measures: [
      { key: "open_projects", label: "Open projects", entity: "projects", plain_rule: "Active projects.", expression: { agg: "count" } },
      { key: "hours_logged", label: "Hours logged", entity: "timesheets", plain_rule: "Hours.", expression: { agg: "count" } },
    ],
  },
  documents: {}, checks: { valid: true, errors: [], warnings: [] },
};
const statusField = (column: string | null) => ({
  key: "status", label: "Status", data_type: "text", semantic_type: null, required: true, column, how: column ? "mapped" as const : "missing" as const,
  values: ["Active", "Inactive"],
  ...(column ? {} : { suggestion: { column: "record_status", confidence: 1, reason: "unmapped" as const, evidence: "Values Active, Inactive match the pack's Status.", alternatives: [] } }),
});
const preview = (mapped: boolean): RepointPreview => ({
  pack_id: "p-pmo", pack: "pmo_tpg", version: "2.1.0", title: "PMO starter pack (TPG)", confidence: mapped ? 1 : 0.5, matched: mapped,
  mapping: { entities: {}, fields: mapped ? { "projects.status": "record_status" } : {}, values: {} },
  mapping_step: { objects: ["ms.projects"], entities: [{
    key: "projects", label: "Projects", object: "ms.projects", how: "exact", confidence: mapped ? 1 : 0.5, primary: true,
    columns: [{ name: "project_id", type: "text" }, { name: "record_status", type: "text" }],
    fields: [{ key: "project_id", label: "Project id", data_type: "text", semantic_type: "identifier", required: true, column: "project_id", how: "exact" }, statusField(mapped ? "record_status" : null)],
  }] },
  checks: { blocking: [], warnings: [], values_read: true, suggestions: mapped ? 0 : 1, ai_available: false },
  candidates: mapped ? [{ ref: "field:projects.status", kind: "field", name: "record_status" }] : [],
  unmapped: [
    { ref: "entity:timesheets", kind: "entity", key: "timesheets", title: "Timesheets", reason: "No table found on this source.", removes_also: ["measure:hours_logged"] },
    ...(mapped ? [] : [{ ref: "field:projects.status", kind: "field" as const, key: "projects.status", title: "Projects: Status", reason: "No column found on this source.", removes_also: [] }]),
    { ref: "measure:hours_logged", kind: "measure", key: "hours_logged", title: "Hours logged", reason: "Needs timesheets (whole table)." },
  ],
});

const calls: { url: string; method: string; body: unknown }[] = [];

function json(body: unknown, status = 200, headers: Record<string, string> = {}) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json", ...headers } });
}

function serve() {
  calls.length = 0;
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    const body = typeof init?.body === "string" ? JSON.parse(init.body) : undefined;
    calls.push({ url, method: init?.method ?? "GET", body });
    if (url.endsWith("/api/v1/me")) return json(admin);
    if (url.endsWith("/api/v1/connections")) return json({ items: [{ id: "c1", name: "TPG MySQL" }] });
    if (url.endsWith("/api/v1/packs") && init?.method === "POST") return json({ ...version({ id: "p-new", pack_key: "tpg_mysql", title: "TPG MySQL", status: "draft", owner: "tenant" }), checks: PMO.checks }, 201);
    if (url.endsWith("/api/v1/packs")) return json(LIST);
    if (url.endsWith("/api/v1/packs/p-pmo/repoint")) return json(preview(!!body?.mapping?.fields?.["projects.status"]));
    if (url.endsWith("/api/v1/packs/p-pmo")) return json(PMO, 200, { ETag: '"r1"' });
    if (url.endsWith("/api/v1/packs/p-new")) return json({ ...PMO, ...version({ id: "p-new", pack_key: "tpg_mysql", title: "TPG MySQL", status: "draft", owner: "tenant", editable: true }) }, 200, { ETag: '"r2"' });
    if (url.includes("/api/v1/notifications")) return json({ items: [], unread_count: 0, next_cursor: null, kinds: [] });
    return json({ error: { code: "not_found", message: "Not found" } }, 404);
  }));
}

beforeEach(() => setConfigForTests({ apiBaseUrl: "http://api.test", oidcAuthority: "http://kc", oidcClientId: "tela-web" }));
afterEach(() => vi.unstubAllGlobals());

describe("Clone a pack onto a source (US-235)", () => {
  it("maps with the value suggestion, removes an unmapped table, renames a measure and creates the clone", async () => {
    serve();
    window.history.pushState({}, "", "/admin/packs");
    render(
      <TestAuthProvider token="tok">
        <Providers client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><AppRoutes /></Providers>
      </TestAuthProvider>,
    );
    await screen.findByRole("table", { name: "Packs" });
    await userEvent.click(screen.getByRole("button", { name: /Create/ }));
    await userEvent.click(await screen.findByRole("menuitem", { name: /Clone a pack/ }));
    const dialog = await screen.findByRole("dialog", { name: /Clone a pack onto your source/ });

    // Template and source.
    const next = within(dialog).getByRole("button", { name: "Match fields" });
    expect(next).toBeDisabled();
    await waitFor(() => expect(within(dialog).getAllByRole("option", { name: /PMO starter pack/ }).length).toBeGreaterThan(0));
    await userEvent.selectOptions(within(dialog).getByLabelText("Template"), "p-pmo");
    await waitFor(() => expect(within(dialog).getAllByRole("option", { name: "TPG MySQL" }).length).toBeGreaterThan(0));
    await userEvent.selectOptions(within(dialog).getByLabelText("Your source"), "c1");
    expect(within(dialog).getByLabelText("Key")).toHaveValue("pmo_tpg_mine");
    await userEvent.clear(within(dialog).getByLabelText("Key"));
    await userEvent.type(within(dialog).getByLabelText("Key"), "tpg_mysql");
    await userEvent.clear(within(dialog).getByLabelText("Title"));
    await userEvent.type(within(dialog).getByLabelText("Title"), "TPG MySQL");
    await userEvent.click(next);

    // The starter pack's mapping step: Status has no column; the values suggest record_status.
    const step = await within(dialog).findByTestId("pack-mapping");
    await userEvent.click(within(step).getByRole("button", { name: "Use record_status" }));
    expect(await within(dialog).findByText(/1 name of yours will be added to the clone/)).toBeInTheDocument();
    const posted = calls.filter((c) => c.url.endsWith("/repoint")).map((c) => c.body);
    expect(posted.at(-1)).toEqual({ connection_id: "c1", mapping: { entities: {}, fields: { "projects.status": "record_status" }, values: {} } });
    await userEvent.click(within(dialog).getByRole("button", { name: "Next" }));

    // Unmapped items by kind with the reason; removing the table names what goes with it.
    const unmapped = await within(dialog).findByTestId("clone-unmapped");
    expect(within(unmapped).getByText("Tables (1)")).toBeInTheDocument();
    expect(within(unmapped).getByText("No table found on this source.")).toBeInTheDocument();
    expect(within(unmapped).queryByText("Projects: Status")).not.toBeInTheDocument();
    await userEvent.selectOptions(within(unmapped).getByLabelText("What to do with Timesheets"), "remove");
    expect(within(unmapped).getByText(/Also removes 1 item: hours_logged/)).toBeInTheDocument();
    expect(within(unmapped).getByText("Removed with another item")).toBeInTheDocument();

    // Rename a measure; a removed one isn't offered.
    const renames = await within(dialog).findByTestId("clone-renames");
    const items = within(renames).getByLabelText("Item to rename");
    expect(within(items).queryByRole("option", { name: /Hours logged/ })).not.toBeInTheDocument();
    await userEvent.selectOptions(items, "open_projects");
    await userEvent.type(within(renames).getByLabelText("New key"), "live_projects");
    await userEvent.type(within(renames).getByLabelText("New label"), "Live projects");
    await userEvent.click(within(renames).getByRole("button", { name: "Add" }));
    expect(within(renames).getByText("live_projects")).toBeInTheDocument();

    await userEvent.click(within(dialog).getByRole("button", { name: "Create the clone" }));
    await waitFor(() => expect(calls.find((c) => c.method === "POST" && c.url.endsWith("/api/v1/packs"))?.body).toEqual({
      method: "clone", pack_key: "tpg_mysql", title: "TPG MySQL", from_pack_id: "p-pmo",
      options: {
        connection_id: "c1", mapping: { entities: {}, fields: { "projects.status": "record_status" }, values: {} }, remove: ["entity:timesheets"],
        renames: { entities: {}, fields: {}, measures: { open_projects: { key: "live_projects", label: "Live projects" } } },
      },
    }));
    await waitFor(() => expect(window.location.pathname).toBe("/admin/packs/p-new"));
  });
});
