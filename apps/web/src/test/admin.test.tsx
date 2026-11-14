import { QueryClient } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { AdminOverview } from "../api/app";
import type { Me } from "../api/types";
import { AppRoutes, Providers } from "../app/App";
import { TestAuthProvider } from "../app/auth";
import { setConfigForTests } from "../app/config";
import { adminScreen, adminSections } from "../app/shell/nav";

const admin: Me = {
  user_id: "u1", display_name: "Ada Admin", email: null, tenant_id: "t1", roles: ["admin"], level: "admin", audience_roles: [],
  permissions: ["dashboards.view", "ask.use", "dashboards.create", "dashboards.publish", "connections.manage", "model.edit",
    "model.approve", "mappings.manage", "policies.manage", "certification.decide", "audit.read", "settings.manage"],
  scope_summary: [], locale: null, time_zone: null,
};
const auditor: Me = { ...admin, user_id: "u3", level: "creator", permissions: ["dashboards.view", "audit.read"] };

const OVERVIEW: AdminOverview = {
  generated_at: "2026-10-21T05:50:00Z", tz: "UTC", today: "2026-10-21",
  connections: { total: 3, healthy: 2, by_status: { connected: 2, error: 1 }, attention: [{ id: "c3", name: "Dataverse sandbox", status: "error" }] },
  models: { total: 1, drafts: 1, broken: 0, items: [{ id: "m1", connection: "TPG extract", version_no: 4, approved_at: "2026-09-12T06:00:00Z",
    has_draft: true, pending_changes: 3, open_questions: 0, broken: false }] },
  certification: { pending: 4, blocks_publish: 1, with_lookalikes: 1, oldest_at: "2026-10-18T06:00:00Z" },
  publish_requests: { open: 2, oldest_at: "2026-10-20T06:00:00Z", oldest_by: "Leena Thomas" },
  value_synonyms: { pending: 77, pack_pending: 76, connections: [{ id: "c1", name: "Dataverse", pending: 77, pack_pending: 76 }] },
  ai: { provider: "ollama", today: { calls: 212, tokens: 640_000, blocked: 3, failed: 1, estimated_cost: 0 },
    daily_calls: Array.from({ length: 14 }, (_, i) => ({ day: `2026-10-${String(8 + i).padStart(2, "0")}`, calls: i * 10 })) },
  security: { days: 7, since: "2026-10-14T05:50:00Z", total: 3, by_action: { "query.scope_refused": 2, "custom_sql.rejected": 1 },
    latest: { action: "custom_sql.rejected", at: "2026-10-20T09:32:00Z", actor: "Arjun Rao" } },
  jobs: { hours: 24, failed: 1, items: [
    { key: "introspection", label: "Read schema", runs: 3, failed: 1, last_run_at: "2026-10-21T00:30:00Z", last_failure: { at: "2026-10-21T00:30:00Z", label: "Dataverse sandbox" } },
    { key: "cache_refresh", label: "Refresh cache", runs: 1, failed: 0, last_run_at: "2026-10-21T05:30:00Z", last_failure: null },
    { key: "exports", label: "Exports", runs: 0, failed: 0, last_run_at: null, last_failure: null },
    { key: "events", label: "Deliver events", runs: 12, failed: 0, last_run_at: "2026-10-21T05:40:00Z", last_failure: null },
  ] },
};

const calls: string[] = [];

function serve(me: Me, handler: (url: string) => Response | undefined) {
  calls.length = 0;
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    calls.push(url);
    if (url.endsWith("/api/v1/me")) return json(me);
    if (url.includes("/api/v1/notifications")) return json({ items: [], unread_count: 0 });
    return handler(url) ?? json({ error: { code: "not_found", message: "Not found" } }, 404);
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

describe("Admin sub-navigation", () => {
  it("lists the areas a person can open and finds the screen for a path", () => {
    expect(adminSections(admin).map((s) => `${s.label}:${s.screens.map((x) => x.label).join("+")}`)).toEqual([
      "Data:Connections+SSH keys", "Access:Access", "Content:Certification+Publish requests", "Platform:Audit log+AI settings+Agents+Agent runs",
    ]);
    expect(adminSections(auditor).map((s) => s.label)).toEqual(["Platform"]);
    const sections = adminSections(admin);
    expect(adminScreen(sections, "/admin/models/m1")?.screen.label).toBe("Connections");
    expect(adminScreen(sections, "/admin/ssh-keys")?.screen.label).toBe("SSH keys");
    expect(adminScreen(sections, "/govern/publish-requests")?.section.label).toBe("Content");
  });

  it("shows the strip on Govern screens with the open area's screens", async () => {
    serve(admin, (url) => (url.includes("/publish-requests") ? json({ items: [] }) : undefined));
    renderAt("/govern/publish-requests");
    const nav = await screen.findByRole("navigation", { name: "Admin" });
    expect(within(nav).getAllByRole("link").map((l) => l.textContent)).toEqual([
      "Overview", "Data", "Access", "Content", "Platform", "Certification", "Publish requests",
    ]);
    expect(within(nav).getByRole("link", { name: "Publish requests" })).toHaveAttribute("aria-current", "page");
    expect(within(nav).getByRole("link", { name: "Data" })).toHaveAttribute("href", "/admin/connections");
  });
});

describe("Admin overview", () => {
  it("lists what needs attention and links each card to its filtered screen", async () => {
    serve(admin, (url) => (url.includes("/api/v1/admin/overview") ? json(OVERVIEW) : undefined));
    renderAt("/admin");
    const attention = await screen.findByRole("region", { name: "Needs your attention" });
    const rows = within(attention).getAllByRole("listitem").map((li) => li.textContent);
    expect(rows[0]).toContain("Dataverse sandbox cannot connect");
    expect(rows.some((r) => r?.includes("Read schema: 1 run failed in the last 24 hours, latest Dataverse sandbox"))).toBe(true);
    expect(rows.some((r) => r?.includes("2 publish requests, the oldest from Leena Thomas"))).toBe(true);
    expect(rows.some((r) => r?.includes("1 certification request holds up a dashboard publish"))).toBe(true);
    expect(rows.some((r) => r?.includes("The TPG extract draft model has 3 suggested changes waiting"))).toBe(true);
    expect(rows.some((r) => r?.includes("76 pack synonyms for Dataverse wait for review"))).toBe(true);
    expect(within(attention).getAllByRole("link", { name: "Review" }).map((l) => l.getAttribute("href"))).toEqual([
      "/govern/publish-requests?status=open", "/govern/certification?status=pending&blocks=1", "/admin/knowledge/values?connection=c1",
    ]);

    const main = within(screen.getByRole("main"));
    const href = (name: RegExp) => main.getAllByRole("link", { name }).at(-1)!.getAttribute("href");
    expect(href(/^Connections/)).toBe("/admin/connections?status=attention");
    expect(href(/^Data model/)).toBe("/admin/models/m1");
    expect(href(/^Certification queue/)).toBe("/govern/certification?status=pending");
    expect(href(/^Publish requests/)).toBe("/govern/publish-requests?status=open");
    expect(href(/^Security, last 7 days/)).toMatch(/^\/admin\/audit\?security=true&from=2026-10-1\dT/);
    expect(screen.getByRole("link", { name: /^Read schema$/ }).getAttribute("href")).toMatch(/action=connection.introspected&outcome=failure/);
    expect(screen.getByText("custom SQL refused as unsafe")).toBeInTheDocument();
    expect(screen.getByText("Provider: ollama")).toBeInTheDocument();
    expect(screen.getByRole("table", { name: "AI calls per day" })).toBeInTheDocument();
    expect(calls.find((u) => u.includes("/admin/overview"))).toMatch(/\?tz=/);
  });

  it("says when nothing needs attention and hides sections without permission", async () => {
    const quiet: AdminOverview = { ...OVERVIEW, connections: null, models: null, certification: null, publish_requests: null, ai: null, jobs: null, value_synonyms: null,
      security: { ...OVERVIEW.security!, total: 0, by_action: {}, latest: null } };
    serve(auditor, (url) => (url.includes("/api/v1/admin/overview") ? json(quiet) : undefined));
    renderAt("/admin");
    expect(await screen.findByText("Nothing needs you right now.")).toBeInTheDocument();
    expect(screen.getByText("No security events.")).toBeInTheDocument();
    expect(screen.queryByText("Background jobs")).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /^Connections/ })).not.toBeInTheDocument();
  });
});

describe("filters from the overview's links", () => {
  it("opens the audit log filtered from the URL and keeps new filters there", async () => {
    serve(admin, (url) => {
      if (url.includes("/api/v1/admin/audit/facets")) {
        return json({ action: [{ value: "custom_sql.rejected", count: 2 }], object_type: [], outcome: [], actor: [{ value: "Arjun Rao", count: 2 }], security: 2, total: 2 });
      }
      return url.includes("/api/v1/admin/audit") ? json({ items: [], next_cursor: null }) : undefined;
    });
    renderAt("/admin/audit?security=true&from=2026-10-14T11:20");
    await screen.findByRole("heading", { name: "Audit log" });
    expect(await screen.findByRole("switch", { name: "Security events only" })).toBeChecked();
    expect(screen.getByLabelText("When")).toHaveValue("custom");
    expect(screen.getByLabelText("From")).toHaveValue("2026-10-14T11:20");
    const first = calls.find((u) => u.includes("/admin/audit?"))!;
    expect(first).toContain("security=true");
    expect(first).toContain(`from=${encodeURIComponent(new Date("2026-10-14T11:20").toISOString())}`);
    const person = screen.getByLabelText("Person");
    await screen.findByRole("option", { name: "Arjun Rao (2)" });
    await userEvent.selectOptions(person, "Arjun Rao");
    expect(window.location.search).toBe("?security=true&from=2026-10-14T11%3A20&actor=Arjun+Rao");
  });

  it("shows only connections needing attention, with a filter to clear", async () => {
    const conn = (id: string, name: string, status: string) => ({ id, name, engine: "postgres", status, descriptor: {}, secret_set: true,
      read_only: { verified_at: null }, freshness: {}, model: { id: null, version_no: null, status: null }, data_as_of: null, etag: "e" });
    serve(admin, (url) => (url.endsWith("/api/v1/connections") ? json({ items: [conn("c1", "TPG extract", "connected"), conn("c3", "Dataverse sandbox", "error")] }) : undefined));
    renderAt("/admin/connections?status=attention");
    expect(await screen.findByText("Dataverse sandbox")).toBeInTheDocument();
    expect(screen.queryByText("TPG extract")).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Clear Status filter" }));
    expect(await screen.findByText("TPG extract")).toBeInTheDocument();
  });
});
