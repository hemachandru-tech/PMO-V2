import { QueryClient } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { ConnectionItem, DraftImpact, DraftModel, PackMapping, PackMappingField } from "../api/app";
import type { Me } from "../api/types";
import { AppRoutes, Providers } from "../app/App";
import { TestAuthProvider } from "../app/auth";
import { setConfigForTests } from "../app/config";

const admin: Me = {
  user_id: "u1", display_name: "Ada Admin", email: null, tenant_id: "t1", roles: ["admin"], level: "admin", audience_roles: [],
  permissions: ["dashboards.view", "ask.use", "connections.manage", "model.edit", "model.approve", "mappings.manage", "policies.manage", "audit.read"],
  scope_summary: [], locale: null, time_zone: null,
};

const conn = (over: Partial<ConnectionItem>): ConnectionItem => ({
  id: "c1", name: "TPG extract", engine: "postgres", status: "connected", descriptor: { host: "tpg", port: 5432, database: "tpg", username: "ro", approved_schemas: ["public"], tls: { mode: "require" } },
  secret_set: true, read_only: { verified_at: "2026-10-21T05:00:00Z" }, freshness: { mode: "live" }, model: { id: "m1", version_no: 4, status: "ok" },
  data_as_of: null, etag: 'W/"1"', ...over,
});
const NO_MAPPING: PackMapping = { entities: {}, fields: {}, values: {} };
const CONNECTIONS = [
  conn({}),
  conn({ id: "c2", name: "Requests DB", model: { id: null, version_no: null, status: null } }),
  conn({ id: "c3", name: "Dataverse sandbox", status: "error", read_only: { verified_at: null }, model: { id: null, version_no: null, status: null } }),
  conn({ id: "c4", name: "Finance draft", status: "draft", read_only: { verified_at: null }, model: { id: null, version_no: null, status: null } }),
];

const DRAFT: DraftModel = {
  model_id: "m1", draft_version_id: "d1", draft_etag: 'W/"7"',
  entities: [
    { key: "projects", label: "Projects", source_object: "public.projects", primary_key: ["project_id"], fields: [
      { key: "title", label: "Project name", source_column: "tpg_title", data_type: "text", sensitivity: "none", description: "Title shown in lists" },
      { key: "region", label: "Region", source_column: "tpgmea_region", data_type: "text", sensitivity: "none" },
    ] },
    { key: "project_requests", label: "Project requests", source_object: "public.project_requests", fields: [
      { key: "request_state", label: "Request state", source_column: "request_state", data_type: "text", sensitivity: "none" },
    ] },
  ],
  links: [{ from_entity: "project_requests", to_entity: "projects", from_fields: ["project_id"], to_fields: ["project_id"], cardinality: "many_to_one", origin: "inferred", accepted: false }],
  measures: [{ key: "open_projects", label: "Open projects", entity: "projects", plain_rule: "Projects whose status is Active" }],
  dimensions: [{ key: "region", label: "Region", entity: "projects", field: "region" }],
  settings: {},
  open_questions: [{ id: "q1", question: "Is Yellow the same as Amber?", options: ["Yes", "No"], status: "open", blocking: true, answer: null }],
  issues: [],
  unaccepted_links: [],
};

const IMPACT: DraftImpact = {
  draft_etag: 'W/"7"', base_version: 4,
  changes: {
    entities: { added: [], removed: [] },
    fields: { added: [], removed: [], changed: [{ key: "projects.region", label: "Projects · Region", what: ["description"], before: { description: null }, after: { description: "Country of delivery" } }] },
    measures: { added: [], removed: [{ key: "stale_projects", label: "Stale projects" }], changed: [{ key: "open_projects", label: "Open projects", what: ["rule"], before: "Status is Active or Yellow", after: "Status is Active" }] },
    dimensions: { added: [], removed: [] },
    links: { added: [], removed: [] },
  },
  breaking_changes: [{ measure: "stale_projects", change: "removed" }, { measure: "open_projects", change: "rule_changed" }],
  dashboards: [
    { id: "s1", title: "PMO — Projects", version: 1, changes_at_approval: true, widgets: [{ title: "Open projects", reasons: ["Open projects: rule changes"], breaks: false }] },
    { id: "p1", title: "Delivery health", version: 3, changes_at_approval: false, widgets: [{ title: "Stale", reasons: ["Stale projects is removed"], breaks: true }] },
  ],
  widget_count: 2,
  personal_fields_broken: [{ id: "f1", label: "Region group", owner: "Cleo Creator", reason: "The base field is no longer in the approved model." }],
};

type Handler = (url: string, init?: RequestInit) => Response | undefined;
const calls: { url: string; method: string; body: unknown }[] = [];

function serve(handler: Handler) {
  calls.length = 0;
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ url, method: init?.method ?? "GET", body: init?.body ? JSON.parse(String(init.body)) : undefined });
    if (url.endsWith("/api/v1/me")) return json(admin);
    if (url.includes("/api/v1/notifications")) return json({ items: [], unread_count: 0 });
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

const method = (init?: RequestInit) => init?.method ?? "GET";

beforeEach(() => setConfigForTests({ apiBaseUrl: "http://api.test", oidcAuthority: "http://kc", oidcClientId: "tela-web" }));
afterEach(() => vi.unstubAllGlobals());

describe("Connections (US-144)", () => {
  it("shows status cards whose Needs attention card filters the table", async () => {
    serve((url) => (url.endsWith("/api/v1/connections") ? json({ items: CONNECTIONS }) : undefined));
    renderAt("/admin/connections");
    const cards = await screen.findByRole("list", { name: "Connection status" });
    expect(within(cards).getByText("Connected").parentElement).toHaveTextContent("2of 4 connections");
    expect(within(cards).getByText("In a model").parentElement).toHaveTextContent("1");
    const attention = within(cards).getByRole("button", { name: /Needs attention/ });
    expect(attention).toHaveAttribute("aria-pressed", "false");
    await userEvent.click(attention);
    expect(window.location.search).toBe("?status=attention");
    const table = screen.getByRole("table", { name: "Connections" });
    await waitFor(() => expect(within(table).queryByText("TPG extract")).not.toBeInTheDocument());
    expect(within(table).getByText("Dataverse sandbox")).toBeInTheDocument();
    expect(within(table).getByRole("link", { name: "Fix details" })).toHaveAttribute("href", "/admin/connections/new?id=c3&step=details");
    expect(screen.getByRole("link", { name: "Add connection" })).toHaveAttribute("href", "/admin/connections/new");
  });

  it("adds a connection step by step: engine, details, test, read schema, then a starter pack", async () => {
    let tested = 0;
    serve((url, init) => {
      if (url.endsWith("/api/v1/connections") && method(init) === "POST") return json({ connection_id: "c9", status: "draft" }, 201);
      if (url.endsWith("/api/v1/connections/c9")) return json(conn({ id: "c9", name: "Delivery finance", status: tested > 1 ? "connected" : "draft", model: { id: null, version_no: null, status: null } }));
      if (url.endsWith("/api/v1/connections/c9/test")) {
        tested++;
        return tested === 1
          ? json({ reachable: true, authenticated: true, read_only: { verdict: "unsafe", checks: [], reasons: ["Can INSERT into public.costs"] }, latency_ms: 38, server_version: "16.2", status: "error", message: "This account can write. Provide a read-only account." })
          : json({ reachable: true, authenticated: true, read_only: { verdict: "safe", checks: [], reasons: [] }, latency_ms: 38, server_version: "16.2", status: "connected", message: null });
      }
      if (url.endsWith("/api/v1/connections/c9/introspect")) return json({ status: "connected", objects: 2, diff: { added: ["public.costs", "public.lines"], removed: [], changed: [] }, model_broken: [] });
      if (url.endsWith("/api/v1/connections/c9/objects")) return json({ items: [
        { object: "finance.cost_lines", kind: "table", columns: [{ name: "a", type: "int", nullable: false }, { name: "b", type: "text", nullable: true }], keys: [], row_estimate: 1_200_000, allowed: false, removed: false },
        { object: "delivery.sites", kind: "table", columns: [{ name: "id", type: "int", nullable: false }], keys: [], row_estimate: 40, allowed: false, removed: false },
      ], introspected_at: "2026-10-21T05:00:00Z", schema_checksum: "x" });
      if (url.includes("/api/v1/starter-packs/match")) return json({ matches: [{ pack_id: "p1", pack_key: "pmo_tpg", version: "1.0", confidence: 0.92, matched: true, missing_objects: [] }] });
      if (url.endsWith("/api/v1/starter-packs/p1/install")) return json({ model_id: "m9" }, 202);
      if (url.endsWith("/api/v1/connections")) return json({ items: [] });
      return undefined;
    });
    renderAt("/admin/connections/new");
    expect(await screen.findByRole("button", { name: /Dataverse/ })).toBeEnabled();
    for (const name of [/MySQL/, /SQL Server/, /MongoDB/, /Excel or CSV/]) expect(screen.getByRole("button", { name })).toBeEnabled();
    await userEvent.click(screen.getByRole("button", { name: /PostgreSQL/ }));
    const form = await screen.findByRole("form", { name: "Connection details · PostgreSQL" });
    for (const [label, value] of [["Name people will see", "Delivery finance"], ["Host", "fin-ro"], ["Database", "delivery_finance"], ["Read-only user", "tela_reader"], ["Password", "secret"]] as const) {
      await userEvent.type(within(form).getByLabelText(label), value);
    }
    await userEvent.selectOptions(within(form).getByLabelText("Freshness"), "cached");
    await userEvent.clear(within(form).getByLabelText("Cache for (minutes)"));
    await userEvent.type(within(form).getByLabelText("Cache for (minutes)"), "30");
    await userEvent.click(within(form).getByRole("button", { name: "Create and test" }));

    // The failed check says what is wrong and offers to change the details.
    expect(await screen.findByText("This account can change data. Use a read-only account.")).toBeInTheDocument();
    expect(screen.getByText("Reached the server (38 ms)")).toBeInTheDocument();
    expect(new URLSearchParams(window.location.search).get("step")).toBe("test");
    expect(calls.find((c) => c.method === "POST" && c.url.endsWith("/api/v1/connections"))!.body)
      .toMatchObject({ engine: "postgres", password: "secret", freshness: { mode: "cached", cache_ttl_minutes: 30 } });
    expect(screen.getByRole("button", { name: "Change details" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Read schema" })).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Test again" }));
    await userEvent.click(await screen.findByRole("button", { name: "Read schema" }));
    expect(await screen.findByText(/2 tables and views, 3 columns · 2 new, 0 removed, 0 changed/)).toBeInTheDocument();
    expect(screen.getByText("finance, delivery")).toBeInTheDocument();
    expect(screen.getByText("finance.cost_lines · about 1,200,000 rows")).toBeInTheDocument();
    expect(await screen.findByText("pmo_tpg 1.0 matches (92% confidence)")).toBeInTheDocument();
    // BUG-003: no model yet, so an empty model_broken list is no warning.
    expect(screen.queryByText(/Tables the model uses changed/)).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Install pmo_tpg" }));
    await waitFor(() => expect(window.location.pathname).toBe("/admin/models/m9"));
  });

  it("offers the starter pack on a connection that already has a model and merges it (BUG-004, US-153)", async () => {
    serve((url) => {
      if (url.endsWith("/api/v1/connections")) return json({ items: CONNECTIONS });
      if (url.includes("/api/v1/starter-packs/match?connection_id=c1")) return json({ matches: [{ pack_id: "p1", pack_key: "pmo_tpg", version: "1.2.0", confidence: 0.95, matched: true, missing_objects: [] }] });
      if (url.endsWith("/api/v1/starter-packs/p1/preview")) return json({
        pack_id: "p1", pack: "pmo_tpg", version: "1.2.0", confidence: 0.95, matched: true, model_exists: true, installed_pack_id: null,
        mapping: NO_MAPPING, mapping_step: { objects: ["public.projects"], entities: [] },
        added: { entities: 0, fields: 30, measures: 24, dimensions: 15, links: 1 }, kept_own: { entities: 2, measures: 1, dimensions: 1 },
        clashes: [{ kind: "measure", ref: "measure:open_projects", title: "Open projects", mine: "Projects whose status is Active", pack: "Open = Project Created, Active, On Hold", choice: "pack" }],
        same_source: [], reports: { available: 11, unavailable: 8 },
        folded: [{ entity: "projects", column: "record_status", mine: "record_status", mine_label: "Record status", pack: "status", pack_label: "Status" }],
        same_column: [{ entity: "issues", column: "issue_name", mine: "issue_name", mine_label: "Issue name", pack: "title", pack_label: "Issue" }],
        unavailable: [{ kind: "report", key: "raid_summary", title: "RAID Summary", gaps: ["risks register"] }],
      });
      if (url.endsWith("/api/v1/starter-packs/p1/install")) return json({ model_id: "m1", mode: "merge", installed: { measures: 25, reports: 11 }, unavailable: [{}, {}] }, 202);
      return undefined;
    });
    renderAt("/admin/connections");
    const table = await screen.findByRole("table", { name: "Connections" });
    const row = (await within(table).findByText("TPG extract")).closest("tr")!;
    await userEvent.click(within(row).getByRole("button", { name: "Starter pack" }));
    const dialog = await screen.findByRole("dialog", { name: "Starter pack for TPG extract" });
    expect(await within(dialog).findByText(/Keeps your 2 tables, 1 measure and 1 dimension/)).toBeInTheDocument();
    expect(within(dialog).getByText(/11 starter reports and the starter dashboards/)).toBeInTheDocument();
    expect(within(dialog).getByText("Yours: Projects whose status is Active")).toBeInTheDocument();
    expect(within(dialog).getByText(/Folds 1 of your fields into the pack's fields on the same columns: Record status → Status/)).toBeInTheDocument();
    expect(within(dialog).getByText(/Your Issue name and the pack's Issue both read issue_name/)).toBeInTheDocument();
    await userEvent.selectOptions(within(dialog).getByLabelText("Which Open projects to keep"), "mine");
    await userEvent.click(within(dialog).getByRole("button", { name: "Add to the model draft" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(calls.find((c) => c.url.endsWith("/api/v1/starter-packs/p1/install"))!.body)
      .toEqual({ connection_id: "c1", keep_mine: ["measure:open_projects"], mapping: NO_MAPPING });
    expect(await screen.findByText(/Added the starter pack to the model draft: 11 starter reports; 2 items unavailable/)).toBeInTheDocument();
  });

  it("lets the Admin choose the PMO pack when another pack matches the connection better", async () => {
    const preview = (id: string, pack: string, matched: boolean) => ({
      pack_id: id, pack, version: "1.0.0", confidence: matched ? 0.9 : 0.4, matched, model_exists: true, installed_pack_id: null,
      mapping: NO_MAPPING, mapping_step: { objects: ["public.projects"], entities: [] },
      added: { entities: 1, fields: 3, measures: id === "p2" ? 24 : 2, dimensions: 1, links: 0 }, kept_own: { entities: 2, measures: 1, dimensions: 1 },
      clashes: [], same_source: [], reports: { available: 3, unavailable: 0 }, folded: [], same_column: [], unavailable: [],
    });
    serve((url) => {
      if (url.endsWith("/api/v1/connections")) return json({ items: CONNECTIONS });
      if (url.includes("/api/v1/starter-packs/match?connection_id=c1")) return json({ matches: [
        { pack_id: "p1", pack_key: "dataverse_pack", title: "Dataverse pack", version: "0.1.0", confidence: 0.9, matched: true, missing_objects: [] },
        { pack_id: "p2", pack_key: "pmo_tpg", title: "PMO starter pack (TPG)", version: "2.1.0", confidence: 0.4, matched: false, missing_objects: [] },
      ] });
      if (url.endsWith("/api/v1/starter-packs/p1/preview")) return json(preview("p1", "dataverse_pack", true));
      if (url.endsWith("/api/v1/starter-packs/p2/preview")) return json({ ...preview("p2", "pmo_tpg", true), version: "2.1.0" });
      if (url.endsWith("/api/v1/starter-packs/p2/install")) return json({ model_id: "m1", mode: "merge", installed: { measures: 24, reports: 3 }, unavailable: [] }, 202);
      return undefined;
    });
    renderAt("/admin/connections");
    const table = await screen.findByRole("table", { name: "Connections" });
    const row = (await within(table).findByText("TPG extract")).closest("tr")!;
    await userEvent.click(within(row).getByRole("button", { name: "Starter pack" }));
    const dialog = await screen.findByRole("dialog", { name: "Starter pack for TPG extract" });
    const choice = await within(dialog).findByLabelText("Starter pack");
    expect(within(choice).getAllByRole("option").map((o) => o.textContent)).toEqual([
      "Dataverse pack 0.1.0 · 90% of its fields found", "PMO starter pack (TPG) 2.1.0 · 40% of its fields found"]);
    expect(await within(dialog).findByText(/Adds 2 measures/)).toBeInTheDocument();
    await userEvent.selectOptions(choice, "p2");
    expect(await within(dialog).findByText(/Adds 24 measures/)).toBeInTheDocument();
    await userEvent.click(within(dialog).getByRole("button", { name: "Add to the model draft" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(calls.some((c) => c.url.endsWith("/api/v1/starter-packs/p2/install"))).toBe(true);
    expect(calls.some((c) => c.url.endsWith("/api/v1/starter-packs/p1/install"))).toBe(false);
  });

  it("maps a pack to a source with other names: a required column and a status value (US-154)", async () => {
    const field = (key: string, over: Partial<PackMappingField>): PackMappingField => ({
      key, label: key, data_type: "text", semantic_type: null, required: false, column: null, how: "missing", ...over });
    const step = (mapped: boolean) => ({ objects: ["dataverse.tpg_project", "dataverse.tpg_projectrequests"], entities: [{
      key: "projects", label: "Projects", object: "dataverse.tpg_project", how: "exact", confidence: 0.7, primary: true,
      columns: [{ name: "tpgmea_productdeploydate", type: "timestamp with time zone" }, { name: "tpgmea_regionname", type: "text" }],
      fields: [
        field("planned_deployment_date", { label: "Planned deployment date", data_type: "date", required: true,
          ...(mapped ? { column: "tpgmea_productdeploydate", how: "mapped" } : {}) }),
        field("region", { label: "Region", column: "tpgmea_regionname", how: "dataverse_label" }),
        field("schedule_kpi", { label: "Schedule KPI", column: "tpg_schedulekpiname", how: "dataverse_label", values: ["Amber", "Green", "Red"], value_map: {} }),
      ] }] });
    serve((url, init) => {
      if (url.endsWith("/api/v1/connections")) return json({ items: CONNECTIONS });
      if (url.includes("/api/v1/starter-packs/match?connection_id=c1")) return json({ matches: [{ pack_id: "p1", pack_key: "pmo_tpg", version: "1.2.0", confidence: 0.6, matched: false, missing_objects: [] }] });
      if (url.endsWith("/api/v1/starter-packs/p1/preview")) {
        const mapping = (JSON.parse(String(init?.body)) as { mapping?: PackMapping }).mapping;
        const mapped = !!mapping?.fields["projects.planned_deployment_date"];
        return json({
          pack_id: "p1", pack: "pmo_tpg", version: "1.2.0", confidence: mapped ? 0.62 : 0.6, matched: mapped, model_exists: true,
          installed_pack_id: null, mapping: mapping ?? NO_MAPPING, mapping_step: step(mapped),
          added: { entities: 1, fields: 20, measures: 20, dimensions: 10, links: 0 }, kept_own: { entities: 2, measures: 0, dimensions: 0 },
          clashes: [], same_source: [], folded: [], same_column: [], reports: { available: 9, unavailable: 10 }, unavailable: [],
        });
      }
      if (url.endsWith("/api/v1/starter-packs/p1/install")) return json({ model_id: "m1", mode: "merge", installed: { measures: 20, reports: 9 }, unavailable: [] }, 202);
      return undefined;
    });
    renderAt("/admin/connections");
    const table = await screen.findByRole("table", { name: "Connections" });
    await userEvent.click(within((await within(table).findByText("TPG extract")).closest("tr")!).getByRole("button", { name: "Starter pack" }));
    const dialog = await screen.findByRole("dialog", { name: "Starter pack for TPG extract" });
    expect(await within(dialog).findByText(/This source uses other names than the pack/)).toBeInTheDocument();
    expect(within(dialog).getByText(/1 required field needs a column/)).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Add to the model draft" })).toBeDisabled();

    // Every column is listed (BUG-021); a date field can't take a text column, which says why.
    await userEvent.click(within(dialog).getByRole("button", { name: "Column for Planned deployment date" }));
    const list = await screen.findByRole("listbox");
    const text = within(list).getByText("tpgmea_regionname").closest("[cmdk-item]")!;
    expect(text).toHaveAttribute("aria-disabled", "true");
    expect(within(text as HTMLElement).getByText("text · not date")).toBeInTheDocument();
    await userEvent.click(within(list).getByText("tpgmea_productdeploydate"));
    await waitFor(() => expect(within(dialog).queryByText(/This source uses other names/)).not.toBeInTheDocument());

    // Status values: the source's "Yellow" means the pack's "Amber".
    await userEvent.click(within(dialog).getByText(/found automatically/));
    await userEvent.click(within(dialog).getByRole("button", { name: "Map values" }));
    await userEvent.type(within(dialog).getByLabelText("Source value for Schedule KPI"), "Yellow");
    await userEvent.click(within(dialog).getByRole("button", { name: "Add" }));
    await waitFor(() => expect(within(dialog).getByRole("button", { name: "Add to the model draft" })).toBeEnabled());
    await userEvent.click(within(dialog).getByRole("button", { name: "Add to the model draft" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(calls.find((c) => c.url.endsWith("/api/v1/starter-packs/p1/install"))!.body).toEqual({
      connection_id: "c1", keep_mine: [],
      mapping: { entities: {}, fields: { "projects.planned_deployment_date": "tpgmea_productdeploydate" },
        values: { "projects.schedule_kpi": { Yellow: "Amber" } } },
    });
  });

  it("stops a pack field mapped to another field's column, shows the column's values and checks measures (US-199)", async () => {
    const field = (key: string, over: Partial<PackMappingField>): PackMappingField => ({
      key, label: key, data_type: "text", semantic_type: null, required: true, column: null, how: "missing", ...over });
    const CURRENT = [{ value: "Active", rows: 279 }, { value: "Completed", rows: 120 }, { value: "On Hold", rows: 79 }];
    const step = (wrong: boolean) => ({ objects: ["tpg.projects"], entities: [{
      key: "projects", label: "Projects", object: "tpg.projects", how: "exact", confidence: 0.9, primary: true,
      columns: [{ name: "current_state", type: "varchar" }, { name: "record_status", type: "varchar" }],
      fields: [
        field("status", { label: "Status", values: ["Active", "Inactive"], value_map: {}, column: wrong ? "current_state" : "record_status", how: "mapped",
          source_values: wrong ? CURRENT : [{ value: "Active", rows: 420 }, { value: "Inactive", rows: 131 }] }),
        field("current_state", { label: "Current state", ...(wrong ? { how: "column_taken" } : { column: "current_state", how: "exact" }) }),
      ] }] });
    serve((url, init) => {
      if (url.endsWith("/api/v1/connections")) return json({ items: CONNECTIONS });
      if (url.includes("/api/v1/starter-packs/match?connection_id=c1")) return json({ matches: [{ pack_id: "p1", pack_key: "pmo_tpg", version: "2.1.0", confidence: 0.9, matched: true, missing_objects: [] }] });
      if (url.endsWith("/api/v1/starter-packs/p1/preview")) {
        const mapping = (JSON.parse(String(init?.body)) as { mapping?: PackMapping }).mapping;
        const wrong = (mapping?.fields["projects.status"] ?? "current_state") === "current_state";
        return json({
          pack_id: "p1", pack: "pmo_tpg", version: "2.1.0", confidence: 0.9, matched: true, model_exists: false,
          installed_pack_id: null, mapping: mapping ?? { ...NO_MAPPING, fields: { "projects.status": "current_state" } }, mapping_step: step(wrong),
          checks: wrong ? {
            blocking: [{ ref: "field:projects.current_state", message: "current_state is Current state's own column, but it is mapped to Status." }],
            warnings: [{ kind: "values", ref: "field:projects.status", missing: ["Inactive"], issue: "Status: the pack's rules use Active, Inactive; current_state has Active (279). Not found: Inactive." }],
            values_read: true,
          } : { blocking: [], warnings: [], values_read: true },
          added: { entities: 1, fields: 20, measures: 20, dimensions: 10, links: 0 }, kept_own: { entities: 0, measures: 0, dimensions: 0 },
          clashes: [], same_source: [], folded: [], same_column: [], reports: { available: 9, unavailable: 0 }, unavailable: [],
        });
      }
      if (url.endsWith("/api/v1/starter-packs/p1/check")) {
        return json({ measures: [{ key: "open_projects", label: "Open projects", value: 420 }],
          warnings: [{ kind: "zero", ref: "measure:cancelled", issue: "Cancelled projects returns 0 on your data." }] });
      }
      return undefined;
    });
    renderAt("/admin/connections");
    const table = await screen.findByRole("table", { name: "Connections" });
    await userEvent.click(within((await within(table).findByText("TPG extract")).closest("tr")!).getByRole("button", { name: "Starter pack" }));
    const dialog = await screen.findByRole("dialog", { name: "Starter pack for TPG extract" });
    const blocking = await within(dialog).findByTestId("pack-blocking");
    expect(blocking).toHaveTextContent("mapped to Status");
    expect(within(dialog).getByTestId("pack-value-warnings")).toHaveTextContent("Not found: Inactive");
    expect(within(dialog).getByTestId("column-values-status")).toHaveTextContent("Pack: Active, Inactive · current_state: Active (279), Completed (120), On Hold (79) · not found: Inactive");
    expect(within(dialog).getByText("Its column is mapped to another field")).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Install starter pack" })).toBeDisabled();
    expect(within(dialog).getByRole("button", { name: "Check the measures" })).toBeDisabled();

    await userEvent.click(within(dialog).getByRole("button", { name: "Column for Status" }));
    await userEvent.click(within(await screen.findByRole("listbox")).getByText("record_status"));
    await waitFor(() => expect(within(dialog).queryByTestId("pack-blocking")).not.toBeInTheDocument());
    expect(within(dialog).getByRole("button", { name: "Install starter pack" })).toBeEnabled();
    await userEvent.click(within(dialog).getByRole("button", { name: "Check the measures" }));
    expect(await within(dialog).findByText("Cancelled projects returns 0 on your data.")).toBeInTheDocument();
    expect(calls.find((c) => c.url.endsWith("/p1/check"))!.body).toEqual({
      connection_id: "c1", mapping: { entities: {}, fields: { "projects.status": "record_status" }, values: {} } });
  });

  it("suggests a column by its values and applies it only when the Admin uses it (US-200)", async () => {
    const field = (key: string, over: Partial<PackMappingField>): PackMappingField => ({
      key, label: key, data_type: "text", semantic_type: null, required: false, column: null, how: "missing", ...over });
    const step = (picked: boolean) => ({ objects: ["tpg.projects"], entities: [{
      key: "projects", label: "Projects", object: "tpg.projects", how: "exact", confidence: 0.9, primary: true,
      columns: [{ name: "current_state", type: "varchar" }, { name: "record_status", type: "varchar" }],
      fields: [
        field("status", { label: "Status", values: ["Active", "Inactive"], value_map: {},
          ...(picked ? { column: "record_status", how: "mapped", source_values: [{ value: "Active", rows: 420 }, { value: "Inactive", rows: 131 }] }
            : { suggestion: { column: "record_status", confidence: 1, reason: "unmapped", evidence: "Values Active, Inactive match the pack's Status.", alternatives: [] } }) }),
        field("current_state", { label: "Current state", column: "current_state", how: "exact" }),
      ] }] });
    serve((url, init) => {
      if (url.endsWith("/api/v1/connections")) return json({ items: CONNECTIONS });
      if (url.includes("/api/v1/starter-packs/match?connection_id=c1")) return json({ matches: [{ pack_id: "p1", pack_key: "pmo_tpg", version: "2.1.0", confidence: 0.9, matched: true, missing_objects: [] }] });
      if (url.endsWith("/api/v1/starter-packs/p1/preview")) {
        const mapping = (JSON.parse(String(init?.body)) as { mapping?: PackMapping }).mapping;
        const picked = !!mapping?.fields["projects.status"];
        return json({
          pack_id: "p1", pack: "pmo_tpg", version: "2.1.0", confidence: 0.9, matched: true, model_exists: false,
          installed_pack_id: null, mapping: mapping ?? NO_MAPPING, mapping_step: step(picked),
          checks: { blocking: [], warnings: [], values_read: true, suggestions: picked ? 0 : 1 },
          added: { entities: 1, fields: 20, measures: 20, dimensions: 10, links: 0 }, kept_own: { entities: 0, measures: 0, dimensions: 0 },
          clashes: [], same_source: [], folded: [], same_column: [], reports: { available: 9, unavailable: 0 }, unavailable: [],
        });
      }
      return undefined;
    });
    renderAt("/admin/connections");
    const table = await screen.findByRole("table", { name: "Connections" });
    await userEvent.click(within((await within(table).findByText("TPG extract")).closest("tr")!).getByRole("button", { name: "Starter pack" }));
    const dialog = await screen.findByRole("dialog", { name: "Starter pack for TPG extract" });
    const suggestion = await within(dialog).findByTestId("suggestion-status");
    expect(suggestion).toHaveTextContent("Suggested by values: record_status100% confidence");
    expect(suggestion).toHaveTextContent("Values Active, Inactive match the pack's Status.");
    expect(within(dialog).getByRole("button", { name: "Column for Status" })).toHaveTextContent("Choose a column"); // not applied
    await userEvent.click(within(suggestion).getByRole("button", { name: "Use record_status" }));
    await waitFor(() => expect(within(dialog).queryByTestId("suggestion-status")).not.toBeInTheDocument());
    expect(within(dialog).getByRole("button", { name: "Column for Status" })).toHaveTextContent("record_status");
    expect(calls.filter((c) => c.url.endsWith("/p1/preview")).at(-1)!.body).toEqual({
      connection_id: "c1", mapping: { entities: {}, fields: { "projects.status": "record_status" }, values: {} } });
  });

  it("asks the AI about open fields, shows its reasons and flags, and applies only what the Admin uses (US-201)", async () => {
    const field = (key: string, over: Partial<PackMappingField>): PackMappingField => ({
      key, label: key, data_type: "text", semantic_type: null, required: false, column: null, how: "missing", ...over });
    const step = (picked: boolean) => ({ objects: ["tpg.projects"], entities: [{
      key: "projects", label: "Projects", object: "tpg.projects", how: "exact", confidence: 0.9, primary: true,
      columns: [{ name: "current_state", type: "varchar" }, { name: "go_live_target", type: "date" }],
      fields: [
        field("planned_deployment_date", { label: "Planned deployment date", data_type: "date", required: true,
          ...(picked ? { column: "go_live_target", how: "mapped" } : {}) }),
        field("cio", { label: "CIO" }),
        field("current_state", { label: "Current state", column: "current_state", how: "exact" }),
      ] }] });
    serve((url, init) => {
      if (url.endsWith("/api/v1/connections")) return json({ items: CONNECTIONS });
      if (url.includes("/api/v1/starter-packs/match?connection_id=c1")) return json({ matches: [{ pack_id: "p1", pack_key: "pmo_tpg", version: "2.1.0", confidence: 0.9, matched: true, missing_objects: [] }] });
      if (url.endsWith("/api/v1/starter-packs/p1/ai-suggestions")) return json({
        available: true, message: null, dropped: 0,
        tables: [{ entity: "projects", asked: 2, status: "ok", suggested: 2 }],
        suggestions: {
          "projects.planned_deployment_date": { column: "go_live_target", confidence: 0.85, reason: "A date column named for the go-live target.", why: "unmapped", source: "ai", flags: [] },
          "projects.cio": { column: "current_state", confidence: 0.3, reason: "Guessing.", why: "unmapped", source: "ai",
            flags: ["current_state is already Current state's column. One column can stand for one pack field only."] },
        },
      });
      if (url.endsWith("/api/v1/starter-packs/p1/preview")) {
        const mapping = (JSON.parse(String(init?.body)) as { mapping?: PackMapping }).mapping;
        const picked = !!mapping?.fields["projects.planned_deployment_date"];
        return json({
          pack_id: "p1", pack: "pmo_tpg", version: "2.1.0", confidence: 0.9, matched: true, model_exists: false,
          installed_pack_id: null, mapping: mapping ?? NO_MAPPING, mapping_step: step(picked),
          checks: { blocking: [], warnings: [], values_read: true, suggestions: 0, ai_available: true },
          added: { entities: 1, fields: 20, measures: 20, dimensions: 10, links: 0 }, kept_own: { entities: 0, measures: 0, dimensions: 0 },
          clashes: [], same_source: [], folded: [], same_column: [], reports: { available: 9, unavailable: 0 }, unavailable: [],
        });
      }
      return undefined;
    });
    renderAt("/admin/connections");
    const table = await screen.findByRole("table", { name: "Connections" });
    await userEvent.click(within((await within(table).findByText("TPG extract")).closest("tr")!).getByRole("button", { name: "Starter pack" }));
    const dialog = await screen.findByRole("dialog", { name: "Starter pack for TPG extract" });
    await userEvent.click(await within(dialog).findByRole("button", { name: "Suggest with AI" }));
    const good = await within(dialog).findByTestId("ai-suggestion-planned_deployment_date");
    expect(good).toHaveTextContent("Suggested by AIgo_live_target85% confidence");
    expect(good).toHaveTextContent("A date column named for the go-live target.");
    expect(within(dialog).getByTestId("ai-suggestion-cio")).toHaveTextContent("already Current state's column");
    expect(within(dialog).getByTestId("pack-ai")).toHaveTextContent("The AI suggested 2 columns for 2 open fields");
    expect(within(dialog).getByRole("button", { name: "Column for Planned deployment date" })).toHaveTextContent("Choose a column"); // not applied
    expect(calls.filter((c) => c.url.endsWith("/p1/preview")).every((c) => !(c.body as { mapping?: PackMapping }).mapping)).toBe(true);
    await userEvent.click(within(good).getByRole("button", { name: "Use go_live_target" }));
    await waitFor(() => expect(within(dialog).queryByTestId("ai-suggestion-planned_deployment_date")).not.toBeInTheDocument());
    expect(within(dialog).getByRole("button", { name: "Column for Planned deployment date" })).toHaveTextContent("go_live_target");
    expect(within(dialog).getByTestId("ai-suggestion-cio")).toBeInTheDocument(); // the others stay
    expect(calls.filter((c) => c.url.endsWith("/p1/preview")).at(-1)!.body).toEqual({
      connection_id: "c1", mapping: { entities: {}, fields: { "projects.planned_deployment_date": "go_live_target" }, values: {} } });
  });

  it("offers no AI suggestions when no model is set up (US-201)", async () => {
    serve((url) => {
      if (url.endsWith("/api/v1/connections")) return json({ items: CONNECTIONS });
      if (url.includes("/api/v1/starter-packs/match?connection_id=c1")) return json({ matches: [{ pack_id: "p1", pack_key: "pmo_tpg", version: "2.1.0", confidence: 0.9, matched: true, missing_objects: [] }] });
      if (url.endsWith("/api/v1/starter-packs/p1/preview")) return json({
        pack_id: "p1", pack: "pmo_tpg", version: "2.1.0", confidence: 0.9, matched: true, model_exists: false,
        installed_pack_id: null, mapping: NO_MAPPING, mapping_step: { objects: [], entities: [] },
        checks: { blocking: [], warnings: [], values_read: false, suggestions: 0, ai_available: false },
        added: { entities: 1, fields: 20, measures: 20, dimensions: 10, links: 0 }, kept_own: { entities: 0, measures: 0, dimensions: 0 },
        clashes: [], same_source: [], folded: [], same_column: [], reports: { available: 9, unavailable: 0 }, unavailable: [],
      });
      return undefined;
    });
    renderAt("/admin/connections");
    const table = await screen.findByRole("table", { name: "Connections" });
    await userEvent.click(within((await within(table).findByText("TPG extract")).closest("tr")!).getByRole("button", { name: "Starter pack" }));
    const dialog = await screen.findByRole("dialog", { name: "Starter pack for TPG extract" });
    await within(dialog).findByTestId("pack-mapping");
    expect(within(dialog).queryByRole("button", { name: "Suggest with AI" })).not.toBeInTheDocument();
  });

  it("updates a connection's details without asking for the password again", async () => {
    serve((url, init) => {
      if (url.endsWith("/api/v1/connections/c3") && method(init) === "PATCH") return json(conn({ id: "c3" }));
      if (url.endsWith("/api/v1/connections/c3")) return json(CONNECTIONS[2]);
      if (url.endsWith("/api/v1/connections/c3/test")) return json({ reachable: false, authenticated: false, read_only: { verdict: "unknown", checks: [], reasons: [] }, latency_ms: null, server_version: null, status: "error", message: "Tela could not reach this database." });
      return undefined;
    });
    renderAt("/admin/connections/new?id=c3&step=details");
    const form = await screen.findByRole("form", { name: "Connection details · PostgreSQL" });
    await waitFor(() => expect(within(form).getByLabelText("Name people will see")).toHaveValue("Dataverse sandbox"));
    await userEvent.click(within(form).getByRole("button", { name: "Save and test" }));
    await screen.findByText("Could not reach the server. Check the host, port and network path.");
    expect(screen.getAllByText("Not checked")).toHaveLength(2);
    const patch = calls.find((c) => c.method === "PATCH")!;
    expect(patch.body).not.toHaveProperty("password");
    expect(patch.body).toMatchObject({ host: "tpg", tls: { mode: "require" } });
  });
});

describe("Model studio (US-144)", () => {
  const studio: Handler = (url, init) => {
    if (url.endsWith("/api/v1/models/m1")) return json({ model_id: "m1", connection_id: "c1", current_version_id: "v4", draft_version_id: "d1", installed_pack_id: null, status: "ok", broken_reasons: [] });
    if (url.endsWith("/api/v1/models/m1/draft") && method(init) === "GET") return json(DRAFT);
    if (url.endsWith("/api/v1/models/m1/draft/impact")) return json(IMPACT);
    if (url.endsWith("/api/v1/models/m1/versions")) return json({ items: [{ id: "v4", version_no: 4, status: "approved", change_summary: "Adds requests", approved_at: "2026-09-12T06:00:00Z" }] });
    if (url.endsWith("/api/v1/connections")) return json({ items: CONNECTIONS });
    return undefined;
  };

  it("shows entities and fields, the draft's changes and the assistant's correction diff", async () => {
    serve((url, init) => {
      if (url.endsWith("/api/v1/models/m1/assistant")) {
        return new Response(`event: proposal\ndata: ${JSON.stringify({ patch_id: "pa1", kind: "correction", source: "rules", ops: [
          { index: 0, op: "alias", valid: true, target: "Region", before: "Saudi Arabia", after: "KSA", detail: {} },
          { index: 1, op: "alias", valid: true, target: "Region", before: "UAE", after: "GCC - UAE", detail: {} },
        ], dropped: [], questions: [], affected: ["2 widgets"], review: { verdict: "confirm", reason: "both are region aliases" } })}\n\n`, { headers: { "Content-Type": "text/event-stream" } });
      }
      if (url.endsWith("/api/v1/models/m1/patches/pa1/apply")) return json({ applied: 1 });
      return studio(url, init);
    });
    renderAt("/admin/models/m1");
    expect(await screen.findByRole("heading", { name: "TPG extract model · draft v5" })).toBeInTheDocument();
    expect(screen.getByText(/Based on approved v4 · 3 changes/)).toBeInTheDocument();
    const fields = await screen.findByRole("table", { name: "Projects fields" });
    expect(within(fields).getByText("tpgmea_region")).toBeInTheDocument();
    expect(within(fields).getByText("Title shown in lists")).toBeInTheDocument();
    await userEvent.selectOptions(screen.getByLabelText("Entity"), "project_requests");
    expect(await screen.findByRole("table", { name: "Project requests fields" })).toBeInTheDocument();
    expect(window.location.search).toContain("entity=project_requests");

    const changes = screen.getByRole("complementary", { name: "Assistant and changes" });
    expect(within(changes).getByText("Stale projects")).toBeInTheDocument();
    expect(within(changes).getByText("Is Yellow the same as Amber?")).toBeInTheDocument();

    await userEvent.type(within(changes).getByLabelText("Describe a correction"), "KSA and Saudi Arabia are the same region.");
    await userEvent.click(within(changes).getByRole("button", { name: "Send" }));
    const proposal = await within(changes).findByRole("article", { name: "Change list" });
    expect(within(proposal).getByText("Saudi Arabia").tagName).toBe("DEL");
    expect(within(proposal).getByText("KSA").tagName).toBe("INS");
    expect(within(proposal).getByText("Checked by AI: both are region aliases")).toBeInTheDocument();
    await userEvent.click(within(proposal).getAllByRole("checkbox")[1]!);
    await userEvent.click(within(proposal).getByRole("button", { name: "Apply to draft" }));
    expect(await within(proposal).findByText("Applied")).toBeInTheDocument();
    expect(calls.find((c) => c.url.endsWith("/apply"))!.body).toEqual({ accept: [0], answers: {} });
  });

  it("approves step by step: checks with fixes, changes, impact, then a note", async () => {
    let draft = DRAFT;
    serve((url, init) => {
      if (url.endsWith("/api/v1/models/m1/draft") && method(init) === "PATCH") {
        draft = { ...draft, draft_etag: 'W/"8"', links: draft.links.map((l) => ({ ...l, accepted: true })) };
        return json(draft);
      }
      if (url.endsWith("/api/v1/models/m1/draft") && method(init) === "GET") return json(draft);
      if (url.endsWith("/api/v1/models/m1/questions/q1/answer")) {
        draft = { ...draft, open_questions: [] };
        return json({ status: "answered" });
      }
      if (url.endsWith("/api/v1/models/m1/approve")) return json({ model_version_id: "v5", version: 5, change_summary: "Region aliases", impact: { breaking_changes: [{}, {}] } }, 201);
      return studio(url, init);
    });
    renderAt("/admin/models/m1/approve");
    expect(await screen.findByRole("heading", { name: "Approve TPG extract model v5" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Next" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Changes$/ })).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: "Confirm this link" }));
    await userEvent.click(await screen.findByRole("button", { name: "Yes" }));
    await userEvent.click(await screen.findByRole("button", { name: "Next" }));
    expect(calls.find((c) => c.method === "PATCH")!.body).toMatchObject({ links: [{ accepted: true }] });

    expect(await screen.findByText("Status is Active or Yellow")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Next" }));
    expect(screen.getByText("2 widgets on 2 published dashboards use a changed definition")).toBeInTheDocument();
    expect(screen.getByText("1 personal field will break")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "PMO — Projects" })).toHaveAttribute("href", "/dashboards/s1");
    expect(screen.getByText(/Change when upgraded/)).toBeInTheDocument();
    expect(screen.getByText("will stop working")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Next" }));

    await userEvent.type(screen.getByLabelText("Approval note (optional)"), "Region aliases");
    await userEvent.click(screen.getByRole("button", { name: "Approve and certify" }));
    expect(await screen.findByRole("heading", { name: "Approved as v5" })).toBeInTheDocument();
    expect(calls.find((c) => c.url.endsWith("/approve"))!.body).toEqual({ draft_etag: 'W/"8"', note: "Region aliases" });
  });

  it("lists fields sharing a column and approves anyway only with a note (US-199)", async () => {
    const draft = { ...DRAFT, links: DRAFT.links.map((l) => ({ ...l, accepted: true })), open_questions: [] };
    const shared = [{ ref: "field:projects.status", issue: "Status and Current state both read current_state in tpg.projects." }];
    serve((url, init) => {
      if (url.endsWith("/api/v1/models/m1/draft") && method(init) === "GET") return json(draft);
      if (url.endsWith("/api/v1/models/m1/draft/checks?data=false")) return json({ draft_etag: draft.draft_etag, blocking: shared, warnings: null });
      if (url.endsWith("/api/v1/models/m1/draft/checks")) {
        return json({ draft_etag: draft.draft_etag, blocking: shared, warnings: [{ kind: "values", ref: "measure:inactive_projects", issue: "Inactive projects filters Status on Inactive; current_state has Active (279)." }] });
      }
      if (url.endsWith("/api/v1/models/m1/approve")) {
        const body = JSON.parse(String(init?.body)) as { override_note?: string };
        if (!body.override_note) return json({ error: { code: "model_invalid", message: "Two fields read one source column.", details: { issues: [{ ref: "field:projects.status", issue: shared[0]!.issue }], can_override: true } } }, 422);
        return json({ model_version_id: "v5", version: 5, change_summary: "Approved", impact: { breaking_changes: [] }, checks: { blocking: shared } }, 201);
      }
      return studio(url, init);
    });
    renderAt("/admin/models/m1/approve");
    expect(await screen.findByTestId("check-shared-columns")).toHaveTextContent("Status and Current state both read current_state");
    await userEvent.click(screen.getByRole("button", { name: "Check the data" }));
    expect(await screen.findByText(/Inactive projects filters Status on Inactive/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Next" }));
    await userEvent.click(await screen.findByRole("button", { name: "Next" }));
    await userEvent.click(await screen.findByRole("button", { name: "Next" }));
    await userEvent.click(screen.getByRole("button", { name: "Approve and certify" }));
    const anyway = await screen.findByRole("button", { name: "Approve anyway" });
    expect(anyway).toBeDisabled();
    await userEvent.type(screen.getByLabelText("Why approve anyway?"), "Same values in this source");
    await userEvent.click(anyway);
    expect(await screen.findByRole("heading", { name: "Approved as v5" })).toBeInTheDocument();
    expect(calls.filter((c) => c.url.endsWith("/approve")).at(-1)!.body).toEqual({ draft_etag: draft.draft_etag, override_note: "Same values in this source" });
  });

  it("dismisses every blocking question at once", async () => {
    const q2 = { ...DRAFT.open_questions[0]!, id: "q2", question: "Count On Hold as open?" };
    let draft = { ...DRAFT, links: DRAFT.links.map((l) => ({ ...l, accepted: true })), open_questions: [...DRAFT.open_questions, q2] };
    serve((url, init) => {
      if (url.endsWith("/api/v1/models/m1/draft") && method(init) === "GET") return json(draft);
      const answered = /\/questions\/(q\d)\/answer$/.exec(url)?.[1];
      if (answered) {
        draft = { ...draft, open_questions: draft.open_questions.filter((q) => q.id !== answered) };
        return json({ status: "dismissed" });
      }
      return studio(url, init);
    });
    renderAt("/admin/models/m1/approve");
    await userEvent.click(await screen.findByRole("button", { name: "Dismiss all 2 questions" }));
    expect(await screen.findByRole("button", { name: "Next" })).toBeInTheDocument();
    expect(calls.filter((c) => c.url.includes("/questions/")).map((c) => [c.url.split("/").at(-2), c.body]))
      .toEqual([["q1", { dismiss: true }], ["q2", { dismiss: true }]]);
  });
});

describe("Read schema warns only when the model broke (BUG-003)", () => {
  it("names the entities whose columns changed", async () => {
    serve((url) => {
      if (url.endsWith("/api/v1/connections/c4")) return json(conn({ id: "c4", name: "TPG", status: "connected", model: { id: "m4", version_no: 2, status: "broken" } }));
      if (url.endsWith("/api/v1/connections/c4/introspect")) return json({ status: "connected", objects: 1, diff: { added: [], removed: [], changed: ["public.projects"] },
        model_broken: [{ entity: "project", problem: "columns removed or changed" }] });
      if (url.endsWith("/api/v1/connections/c4/objects")) return json({ items: [
        { object: "public.projects", kind: "table", columns: [{ name: "id", type: "int", nullable: false }], keys: [], row_estimate: 10, allowed: true, removed: false },
      ], introspected_at: "2026-10-27T05:00:00Z", schema_checksum: "x" });
      return undefined;
    });
    renderAt("/admin/connections/new?id=c4&step=schema");
    await userEvent.click(await screen.findByRole("button", { name: "Read again" }));
    expect(await screen.findByText("Tables the model uses changed or can no longer be read (project). Open Model studio to fix the model.")).toBeInTheDocument();
  });

  it("names tables the account may not read", async () => {
    serve((url) => {
      if (url.endsWith("/api/v1/connections/c5")) return json(conn({ id: "c5", name: "Dataverse", status: "connected" }));
      if (url.endsWith("/api/v1/connections/c5/objects")) return json({ items: [
        { object: "dbo.tpg_project", kind: "dataverse_table", columns: [{ name: "id", type: "int", nullable: false }], keys: [], row_estimate: null, allowed: false, readable: true, removed: false },
        { object: "dbo.new_projectlifecycle", kind: "dataverse_table", columns: [{ name: "id", type: "int", nullable: false }], keys: [], row_estimate: null, allowed: false, readable: false, removed: false },
      ], introspected_at: "2026-10-30T05:00:00Z", schema_checksum: "x" });
      return undefined;
    });
    renderAt("/admin/connections/new?id=c5&step=schema");
    expect(await screen.findByText(/1 table can't be read/)).toBeInTheDocument();
    expect(screen.getByText(/dbo\.new_projectlifecycle\./)).toBeInTheDocument();
    expect(screen.queryByText(/dbo\.tpg_project\./)).not.toBeInTheDocument();
  });
});
