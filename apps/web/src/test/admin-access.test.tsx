import { QueryClient } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { MappingsResponse, Me, ScopePreview } from "../api/types";
import { AppRoutes, Providers } from "../app/App";
import { TestAuthProvider } from "../app/auth";
import { setConfigForTests } from "../app/config";

const admin: Me = {
  user_id: "u1", display_name: "Ada Admin", email: null, tenant_id: "t1", roles: ["admin"], level: "admin", audience_roles: [],
  permissions: ["dashboards.view", "connections.manage", "mappings.manage", "policies.manage", "audit.read"],
  scope_summary: [], locale: null, time_zone: null,
};

const MAPPINGS: MappingsResponse = {
  etag: 'W/"e1"',
  mappings: [
    { id: "m1", idp_group_id: "TELA-Admins", group_name: "TELA-Admins", role: "admin", role_kind: "system", requires_approval: true, status: "active", scope_attributes: {}, requested_by: null },
    { id: "m2", idp_group_id: "TELA-CIO-MEA", group_name: "TELA-CIO-MEA", role: "cio", role_kind: "audience", requires_approval: false, status: "active", scope_attributes: { cio: ["Meera Pillai"] }, requested_by: null },
  ],
};
const GROUPS = [
  { id: "g1", external_group_id: "TELA-Admins", display_name: "TELA-Admins", source: "claim", member_count: 3 },
  { id: "g2", external_group_id: "TELA-CIO-MEA", display_name: "TELA-CIO-MEA", source: "claim", member_count: 6 },
  { id: "g3", external_group_id: "TELA-Contractors", display_name: "TELA-Contractors", source: "claim", member_count: 18 },
];
const ROLES = [
  { id: "r1", key: "viewer", name: "Viewer", kind: "system", is_protected: false },
  { id: "r2", key: "cio", name: "CIO", kind: "audience", is_protected: false },
];
const POLICIES = [
  { id: "p1", connection_id: "c1", entity: "projects", role: "cio", all_rows: false, predicates: [{ field: "cio", op: "=", attr: "cio" }], description: "Own CIO", is_active: true },
];

const PERSON: ScopePreview = {
  subject: { kind: "person", id: "u9", display_name: "Meera Pillai" }, user: { id: "u9", display_name: "Meera Pillai" },
  groups: ["TELA-CIO-MEA", "TELA-Viewers"], permissions: ["ask.use", "dashboards.view", "drill.use", "export.use"],
  level: "viewer", audience_roles: ["cio"],
  scope: { summary: "projects: Own CIO", entities: { projects: { all_rows: false, alternatives: [[{ field: "cio", op: "=", values: ["Meera Pillai"] }]] } },
    hidden_fields: ["projects.approved_budget"], masked_fields: [], error: null },
  rows_in_scope: { projects: 14 },
  grants: [{
    id: "g1", user: { id: "u9", display_name: "Meera Pillai", email: "meera@x" }, permission: "ask.freehand",
    connection: { id: "c1", name: "TPG Postgres" }, reason: "Portfolio review", granted_by: { id: "u1", display_name: "Ada Admin" },
    granted_at: "2026-10-27T09:00:00Z", expires_at: "2027-01-25T09:00:00Z", removed_at: null, removed_by: null, removed_reason: null, status: "active",
  }],
};
const ROLE_CIO: ScopePreview = {
  ...PERSON, subject: { kind: "role", id: "r2", display_name: "CIO", key: "cio" }, user: { id: "r2", display_name: "CIO" }, groups: [],
  level: null, rows_in_scope: null, grants: [],
  scope: { summary: null, entities: {}, hidden_fields: [], masked_fields: [], error: { code: "scope_unresolved", message: "The cio attribute is missing.", details: { missing_attribute: "cio" } } },
};

type Handler = (url: string, init?: RequestInit) => Response | undefined;
const calls: { url: string; method: string; body: unknown }[] = [];

function serve(handler: Handler = () => undefined) {
  calls.length = 0;
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ url, method: init?.method ?? "GET", body: init?.body ? JSON.parse(String(init.body)) : undefined });
    if (url.endsWith("/api/v1/me")) return json(admin);
    if (url.includes("/api/v1/notifications")) return json({ items: [], unread_count: 0 });
    const own = handler(url, init);
    if (own) return own;
    if (url.endsWith("/api/v1/admin/group-mappings")) return json(MAPPINGS);
    if (url.endsWith("/api/v1/admin/groups")) return json({ items: GROUPS });
    if (url.endsWith("/api/v1/admin/roles")) return json({ items: ROLES });
    if (url.endsWith("/api/v1/connections")) return json({ items: [{ id: "c1", name: "TPG", engine: "postgres", status: "connected" }] });
    if (url.includes("/api/v1/admin/scope-policies?")) return json({ items: POLICIES });
    if (url.includes("/api/v1/admin/field-policies?")) return json({ rules: [{ field_ref: "projects.approved_budget", hidden_for: ["viewer"], effect: "hide" }] });
    return json({ error: { code: "not_found", message: "Not found" } }, 404);
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

beforeEach(() => setConfigForTests({ apiBaseUrl: "http://api.test", oidcAuthority: "http://kc", oidcClientId: "tela-web" }));
afterEach(() => vi.unstubAllGlobals());

describe("Access screens (US-145)", () => {
  it("lists unmapped groups with their members, and Map fills in the form", async () => {
    serve();
    renderAt("/admin/access/mappings");
    const table = await screen.findByRole("table", { name: "Group mappings" });
    const contractors = (await within(table).findByText("Not mapped: members cannot sign in")).closest("tr")!;
    expect(contractors).toHaveTextContent("TELA-Contractors");
    expect(contractors).toHaveTextContent("18");
    expect(screen.getByText(/2 mappings · 1 group not mapped/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Group mappings/ })).toHaveTextContent("2");
    await userEvent.click(within(contractors).getByRole("button", { name: "Map" }));
    expect(screen.getByLabelText("Group")).toHaveValue("TELA-Contractors");
  });

  it("asks before deleting or deactivating a scope policy; Cancel changes nothing", async () => {
    serve((url, init) => {
      if (url.endsWith("/api/v1/admin/scope-policies/p1") && init?.method) return new Response(null, { status: 204 });
      return undefined;
    });
    renderAt("/admin/access/scope");
    // With one connection it is chosen for you and kept in the URL.
    const table = await screen.findByRole("table", { name: "Scope policies" });
    await waitFor(() => expect(window.location.search).toBe("?connection=c1"));
    await userEvent.click(within(table).getByRole("button", { name: "Delete" }));
    let dialog = await screen.findByRole("dialog", { name: "Delete the cio policy on projects?" });
    expect(dialog).toHaveTextContent("cio = the person's cio");
    await userEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
    expect(calls.some((c) => c.method === "DELETE")).toBe(false);

    await userEvent.click(within(table).getByRole("button", { name: "Deactivate" }));
    dialog = await screen.findByRole("dialog", { name: "Deactivate the cio policy on projects?" });
    await userEvent.click(within(dialog).getByRole("button", { name: "Deactivate" }));
    await waitFor(() => expect(calls.find((c) => c.method === "PATCH")?.body).toEqual({ is_active: false }));

    await userEvent.click(within(table).getByRole("button", { name: "Delete" }));
    await userEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Delete policy" }));
    await waitFor(() => expect(calls.some((c) => c.method === "DELETE" && c.url.endsWith("/scope-policies/p1"))).toBe(true));
    // The connection follows to the other tabs.
    expect(screen.getByRole("link", { name: "Field policies" })).toHaveAttribute("href", "/admin/access/fields?connection=c1");
  });

  it("saves one rule for several entities and roles at once (US-172)", async () => {
    serve((url, init) => {
      if (url.includes("/scope-policies/entities?connection_id=c1")) return json({ model_version_id: "v1", entities: [
        { key: "projects", label: "Projects", table: "public.projects", fields: [{ key: "region", label: "Region" }, { key: "cio", label: "CIO" }] },
        { key: "risks", label: "Risks", table: "public.risks", fields: [{ key: "cio", label: "CIO" }, { key: "region", label: "Region" }] },
        { key: "demand", label: "Demand", table: "public.demand", fields: [{ key: "status", label: "Status" }] },
      ] });
      if (url.endsWith("/api/v1/admin/scope-policies/bulk") && init?.method === "POST") return json({
        created: [POLICIES[0], POLICIES[0], POLICIES[0]],
        skipped: [{ entity: "risks", role: "cio", id: "p9", is_active: false }],
      }, 201);
      return undefined;
    });
    renderAt("/admin/access/scope?connection=c1");
    const entities = await screen.findByRole("group", { name: "Entities" });
    const roles = screen.getByRole("group", { name: "Roles" });
    const field = screen.getByRole("combobox", { name: "Field" });
    expect(field).toBeDisabled();
    await userEvent.click(within(entities).getByRole("checkbox", { name: /Projects/ }));
    expect(within(field).getAllByRole("option").map((o) => o.textContent)).toEqual(["Choose…", "Region (region)", "CIO (cio)"]);
    await userEvent.selectOptions(field, "region");
    // A second entity keeps only the fields both have; one without them clears the choice.
    await userEvent.click(within(entities).getByRole("checkbox", { name: /Risks/ }));
    expect(field).toHaveValue("region");
    expect(screen.getByText("Only fields every chosen entity has.")).toBeInTheDocument();
    await userEvent.click(within(entities).getByRole("checkbox", { name: /Demand/ }));
    expect(field).toHaveValue("");
    expect(field).toBeDisabled();
    await userEvent.click(within(entities).getByRole("checkbox", { name: /Demand/ }));
    await userEvent.selectOptions(field, "region");
    for (const name of ROLES.slice(0, 2).map((r) => r.name)) await userEvent.click(within(roles).getByRole("checkbox", { name }));
    expect(within(roles).getByText("2 of " + ROLES.length + " chosen")).toBeInTheDocument();
    await userEvent.type(screen.getByLabelText("Values (comma separated)"), "KSA, KSA, GCC - UAE");
    await userEvent.type(screen.getByLabelText("Shown on the scope chip"), "Region: KSA");
    await userEvent.click(screen.getByRole("button", { name: "Save 4 policies" }));
    await waitFor(() => expect(calls.find((c) => c.method === "POST")?.body).toEqual({
      connection_id: "c1", entities: ["projects", "risks"], roles: ROLES.slice(0, 2).map((r) => r.key), all_rows: false,
      predicates: [{ field: "region", op: "in", values: ["KSA", "GCC - UAE"] }], description: "Region: KSA",
    }));
    const notice = await screen.findByText("Added 3 policies.");
    expect(notice.parentElement).toHaveTextContent("1 pair already had this exact policy and was left as it is: risks for cio (inactive).");
    // Roles stay chosen for the next batch; entities start again.
    expect(within(entities).getByText("0 of 3 chosen")).toBeInTheDocument();
    expect(within(roles).getByText("2 of " + ROLES.length + " chosen")).toBeInTheDocument();
  });

  it("selects all entities, filtered by search, and asks for at least one of each", async () => {
    const many = Array.from({ length: 12 }, (_, i) => ({ key: `t${i}`, label: i < 3 ? `Task ${i}` : `Other ${i}`, table: `public.t${i}`, fields: [] }));
    serve((url, init) => {
      if (url.includes("/scope-policies/entities?connection_id=c1")) return json({ model_version_id: "v1", entities: many });
      if (url.endsWith("/scope-policies/bulk") && init?.method === "POST") return json({ created: [], skipped: [] }, 201);
      return undefined;
    });
    renderAt("/admin/access/scope?connection=c1");
    const entities = await screen.findByRole("group", { name: "Entities" });
    await userEvent.click(screen.getByRole("button", { name: "Save policy" }));
    expect(await screen.findByText("Choose at least one entity.")).toBeInTheDocument();
    expect(screen.getByText("Choose at least one role.")).toBeInTheDocument();
    await userEvent.type(within(entities).getByRole("searchbox", { name: "Search entities" }), "task");
    await userEvent.click(within(entities).getByRole("button", { name: "Select all 3 shown" }));
    expect(within(entities).getByText("3 of 12 chosen")).toBeInTheDocument();
    await userEvent.clear(within(entities).getByRole("searchbox", { name: "Search entities" }));
    await userEvent.click(within(entities).getByRole("button", { name: "Select all" }));
    expect(within(entities).getByText("12 of 12 chosen")).toBeInTheDocument();
    await userEvent.click(within(screen.getByRole("group", { name: "Roles" })).getByRole("checkbox", { name: ROLES[0]!.name }));
    await userEvent.click(screen.getByRole("checkbox", { name: "All rows" }));
    expect(screen.getByText("Everyone with this role will see every row of these entities.")).toBeInTheDocument();
    await userEvent.type(screen.getByLabelText("Shown on the scope chip"), "All");
    await userEvent.click(screen.getByRole("button", { name: "Save 12 policies" }));
    await waitFor(() => expect(calls.find((c) => c.method === "POST")?.body).toMatchObject({
      entities: many.map((e) => e.key), all_rows: true, predicates: [] }));
    expect(await screen.findByText("Nothing new to add.")).toBeInTheDocument();
  });

  it("types the entities when the connection has no approved model", async () => {
    serve((url, init) => {
      if (url.includes("/scope-policies/entities?connection_id=c1")) return json({ model_version_id: null, entities: [] });
      if (url.endsWith("/scope-policies/bulk") && init?.method === "POST") return json({ created: [POLICIES[0]], skipped: [] }, 201);
      return undefined;
    });
    renderAt("/admin/access/scope?connection=c1");
    await userEvent.type(await screen.findByLabelText("Entities (comma separated)"), "projects, risks, projects");
    await userEvent.click(within(screen.getByRole("group", { name: "Roles" })).getByRole("checkbox", { name: ROLES[0]!.name }));
    await userEvent.type(screen.getByLabelText("Field"), "region");
    await userEvent.type(screen.getByLabelText("Values (comma separated)"), "KSA");
    await userEvent.type(screen.getByLabelText("Shown on the scope chip"), "Region: KSA");
    await userEvent.click(screen.getByRole("button", { name: "Save 2 policies" }));
    await waitFor(() => expect(calls.find((c) => c.method === "POST")?.body).toMatchObject({ entities: ["projects", "risks"] }));
  });

  it("asks before showing a hidden field again", async () => {
    serve((url, init) => (url.endsWith("/api/v1/admin/field-policies") && init?.method === "PUT" ? json({ rules: [] }) : undefined));
    renderAt("/admin/access/fields?connection=c1");
    const table = await screen.findByRole("table", { name: "Field policies" });
    await userEvent.click(await within(table).findByRole("button", { name: "Remove" }));
    const dialog = await screen.findByRole("dialog", { name: "Show projects.approved_budget again?" });
    expect(dialog).toHaveTextContent("viewer will see this field");
    await userEvent.click(within(dialog).getByRole("button", { name: "Remove rule" }));
    await waitFor(() => expect(calls.find((c) => c.method === "PUT")?.body).toEqual({ connection_id: "c1", rules: [] }));
  });

  it("shows effective access for a person or a role, as the server resolves it", async () => {
    serve((url) => {
      if (url.includes("/api/v1/admin/users?q=")) return json({ items: [{ id: "u9", display_name: "Meera Pillai", email: "meera@x", status: "active", roles: [] }] });
      if (url.includes("/scope-policies/preview")) return json(url.includes("role=cio") ? ROLE_CIO : PERSON);
      return undefined;
    });
    renderAt("/admin/access/mappings");
    await userEvent.click(await screen.findByRole("button", { name: "Effective access" }));
    expect(window.location.search).toContain("panel=effective");
    const panel = await screen.findByRole("dialog", { name: "Effective access" });
    await waitFor(() => expect(within(panel).getByLabelText("Connection")).toHaveValue("c1"));

    await userEvent.type(within(panel).getByRole("combobox", { name: "Person" }), "Meera");
    await userEvent.click(await within(panel).findByRole("option", { name: /Meera Pillai/ }));
    const result = await within(panel).findByRole("region", { name: "Access for Meera Pillai" });
    expect(result).toHaveTextContent("TELA-CIO-MEA, TELA-Viewers");
    expect(result).toHaveTextContent("Viewer (the highest of these groups)");
    expect(result).toHaveTextContent("projects: cio is Meera Pillai");
    expect(within(result).getByText("14 rows")).toBeInTheDocument();
    expect(result).toHaveTextContent("projects.approved_budget");
    expect(result).toHaveTextContent("Read shared dashboards, ask, drill to records, export (from their roles)");
    // A personal grant (US-161) and where it comes from.
    expect(result).toHaveTextContent(/Ask free-hand questions until .*2027/);
    expect(result).toHaveTextContent(/given by Ada Admin on .*2026: Portfolio review/);
    expect(within(result).getByRole("link", { name: "Meera Pillai's recent activity" })).toHaveAttribute("href", "/admin/audit?actor=Meera+Pillai");
    expect(calls.find((c) => c.url.includes("/preview"))!.url).toContain("user_id=u9");

    await userEvent.click(within(panel).getByRole("radio", { name: "A role" }));
    await userEvent.selectOptions(within(panel).getByLabelText("Role"), "cio");
    const role = await within(panel).findByRole("region", { name: "Access for CIO" });
    expect(role).toHaveTextContent("The cio attribute is missing.");
    expect(role).toHaveTextContent("depend on each person's cio");
    expect(role).toHaveTextContent("None: cannot sign in");
    expect(role).not.toHaveTextContent("Given");
  });
});
