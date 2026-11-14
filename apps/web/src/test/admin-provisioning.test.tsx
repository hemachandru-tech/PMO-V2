import { QueryClient } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Me } from "../api/types";
import { AppRoutes, Providers } from "../app/App";
import { TestAuthProvider } from "../app/auth";
import { setConfigForTests } from "../app/config";
import type { ProvisioningStatus } from "../features/admin/access/ProvisioningPage";

const admin: Me = {
  user_id: "u1", display_name: "Ada Admin", email: null, tenant_id: "t1", roles: ["admin"], level: "admin", audience_roles: [],
  permissions: ["dashboards.view", "mappings.manage"], scope_summary: [], locale: null, time_zone: null,
};

const OFF: ProvisioningStatus = {
  enabled: false, endpoint_path: "/scim/v2", token: null, idp_sessions_configured: false, last_activity_at: null,
  counts: { users: { active: 0, pending: 0, suspended: 0, deprovisioned: 0 }, groups: 0, groups_deleted: 0, requests_24h: 0, errors_24h: 0 },
  recent: [], tasks: [], last_run: null, runs: [], deprovisioned_owners: [],
};
const OWNER = { user: { id: "u9", display_name: "Cleo Creator", status: "deprovisioned" }, deprovisioned_at: "2026-10-22T05:00:00Z",
  owned: { dashboards: 1, reports: 2, widgets: 0, custom_sql: 0, fields: 0 } };
const ON: ProvisioningStatus = {
  ...OFF, enabled: true, token: { prefix: "tela_scim_AbCd", created_at: "2026-10-22T05:00:00Z", created_by: "Ada Admin", last_used_at: null },
  last_activity_at: "2026-10-22T05:10:00Z",
  counts: { users: { active: 12, pending: 1, suspended: 1, deprovisioned: 1 }, groups: 3, groups_deleted: 0, requests_24h: 40, errors_24h: 2 },
  recent: [{ at: "2026-10-22T05:10:00Z", method: "POST", resource: "User", name: null, operation: "error", status: 409, outcome: "error", detail: "userName is already in use by another user." }],
  tasks: [{ id: "k1", status: "failed", attempts: 5, last_error: "RuntimeError: keycloak unavailable", next_attempt_at: "2026-10-22T05:10:00Z", user: { id: "u9", display_name: "Cleo Creator" } }],
  last_run: {
    id: "r1", trigger: "schedule", started_at: "2026-10-22T05:40:00Z", finished_at: "2026-10-22T05:40:01Z", outcome: "drift", error: null,
    summary: {
      fixed: { memberships_removed: 1, sessions_revoked: 0 }, roles_refreshed: 0, tasks: { done: 0, skipped: 0, retrying: 0, failed: 1 },
      unlinked_users: { count: 1, items: [{ id: "u1", display_name: "Ada Admin", email: null, last_login_at: null }] },
      deleted_groups_mapped: [{ id: "g1", display_name: "Old PMO", external_group_id: "x", mappings: 1 }],
      deprovisioned_owners: [OWNER], failed_tasks: [{ id: "k1", user: { id: "u9", display_name: "Cleo Creator" }, attempts: 5, last_error: "x" }],
    },
  },
  deprovisioned_owners: [OWNER],
};

const calls: { url: string; method: string; body: unknown }[] = [];
function serve(handler: (url: string, init?: RequestInit) => Response | undefined) {
  calls.length = 0;
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ url, method: init?.method ?? "GET", body: init?.body ? JSON.parse(String(init.body)) : undefined });
    if (url.endsWith("/api/v1/me")) return json(admin);
    if (url.endsWith("/api/v1/admin/group-mappings")) return json({ mappings: [], etag: "e" });
    return handler(url, init) ?? (url.includes("/api/v1/notifications") ? json({ items: [], unread_count: 0, next_cursor: null, kinds: [] }) : json({ error: { code: "not_found", message: "Not found" } }, 404));
  }));
}
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

function renderAt(path: string) {
  window.history.pushState({}, "", path);
  return render(
    <TestAuthProvider token="tok">
      <Providers client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><AppRoutes /></Providers>
    </TestAuthProvider>,
  );
}

beforeEach(() => setConfigForTests({ apiBaseUrl: "http://api.test", oidcAuthority: "http://kc", oidcClientId: "tela-web" }));
afterEach(() => vi.unstubAllGlobals());

describe("Provisioning (US-118, US-119)", () => {
  it("turns provisioning on and shows the token once with the tenant URL", async () => {
    let on = false;
    serve((url, init) => {
      if (url.endsWith("/api/v1/admin/provisioning")) return json(on ? ON : OFF);
      if (url.endsWith("/api/v1/admin/provisioning/token") && init?.method === "POST") {
        on = true;
        return json({ token: "tela_scim_secret-value", prefix: "tela_scim_AbCd", created_at: "2026-10-22T05:00:00Z", rotated: false }, 201);
      }
      return undefined;
    });
    renderAt("/admin/access/provisioning");
    expect(await screen.findByText("Entra has not called yet.")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Turn on" }));
    const dialog = await screen.findByRole("dialog", { name: "Provisioning token" });
    expect(within(dialog).getByLabelText("Provisioning token")).toHaveTextContent("tela_scim_secret-value");
    expect(within(dialog).getByText("http://api.test/scim/v2")).toBeInTheDocument();
    await userEvent.click(within(dialog).getByRole("button", { name: "Done" }));
    expect(await screen.findByText(/tela_scim_AbCd…/)).toBeInTheDocument();
  });

  it("shows reconcile findings, runs Reconcile and reassigns a former user's content", async () => {
    serve((url, init) => {
      if (url.endsWith("/api/v1/admin/provisioning")) return json(ON);
      if (url.endsWith("/api/v1/admin/provisioning/reconcile")) return json({ ...ON.last_run, trigger: "manual" });
      if (url.includes("/api/v1/admin/users?q=")) return json({ items: [{ id: "u2", display_name: "Nomsa Osei" }] });
      if (url.endsWith("/api/v1/admin/users/u9/reassign-content") && init?.method === "POST") {
        return json({ moved: { dashboards: 1, reports: 2, widgets: 0, custom_sql: 0, fields: 0 }, skipped: { dashboards: 0, reports: 0, widgets: 0, custom_sql: 0, fields: 0 }, to: { id: "u2", display_name: "Nomsa Osei" } });
      }
      return undefined;
    });
    renderAt("/admin/access/provisioning");
    const findings = await screen.findByRole("list", { name: "Reconcile findings" });
    expect(within(findings).getByText(/Signing out Cleo Creator failed after 5 attempts/)).toBeInTheDocument();
    expect(within(findings).getByText(/Old PMO/)).toBeInTheDocument();
    expect(screen.getByText("Needs attention")).toBeInTheDocument();
    expect(within(screen.getByRole("table", { name: "Recent provisioning requests" })).getByText("409")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Reconcile now" }));
    await waitFor(() => expect(calls.some((c) => c.url.endsWith("/provisioning/reconcile") && c.method === "POST")).toBe(true));

    const owners = screen.getByRole("table", { name: "Former owners" });
    expect(within(owners).getByText("1 dashboard, 2 reports")).toBeInTheDocument();
    await userEvent.click(within(owners).getByRole("button", { name: "Reassign Cleo Creator's content" }));
    const dialog = await screen.findByRole("dialog");
    await userEvent.type(within(dialog).getByRole("combobox"), "Nom");
    await userEvent.click(await within(dialog).findByRole("option", { name: /Nomsa Osei/ }));
    await userEvent.click(within(dialog).getByRole("button", { name: "Reassign to Nomsa Osei" }));
    await waitFor(() => expect(calls.find((c) => c.url.endsWith("/reassign-content"))?.body).toEqual({ to_user_id: "u2" }));
  });
});
