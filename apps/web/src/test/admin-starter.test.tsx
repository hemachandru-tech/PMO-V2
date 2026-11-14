import { QueryClient } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { PackUpdate, StarterEffects, StarterItem } from "../api/authoring";
import type { Me } from "../api/types";
import { AppRoutes, Providers } from "../app/App";
import { TestAuthProvider } from "../app/auth";
import { setConfigForTests } from "../app/config";

const admin: Me = {
  user_id: "u1", display_name: "Ada Admin", email: null, tenant_id: "t1", roles: ["admin"], level: "admin", audience_roles: [],
  permissions: ["dashboards.view", "starter.manage", "reports.create"],
  scope_summary: [], locale: null, time_zone: null,
};

const ROLES = [{ id: "r-admin", key: "admin", name: "Admin" }, { id: "r-creator", key: "creator", name: "Creator" }, { id: "r-viewer", key: "viewer", name: "Viewer" }];
const visibility = (hidden: string[] = []) => ROLES.map((r) => ({ role_id: r.id, key: r.key, name: r.name, visible: !hidden.includes(r.id) }));
const item = (over: Partial<StarterItem>): StarterItem => ({
  id: "i1", key: "projects_delivery", title: "Projects — Delivery", state: "active", availability: "available", gaps: [],
  report_id: "rep1", connection: { id: "c1", name: "TPG Postgres" }, pack: { key: "pmo_tpg", version: "1.0.0" },
  widgets: 5, copies: 12, dashboards: 1, visibility: visibility(), updated_at: "2026-10-21T09:00:00Z", ...over,
});
const DELIVERY = item({});
const FLOW = item({ id: "i2", key: "projects_flow", title: "Projects — Flow", state: "deleted", copies: 0, dashboards: 0 });
const RAID = item({ id: "i3", key: "raid_summary", title: "RAID Summary", availability: "unavailable", report_id: null, widgets: 0, copies: 0, dashboards: 0,
  gaps: ["risks, assumptions, issues and dependencies registers"] });
// Pack 2.0 (US-155): a report shows what the source has and names the widgets it left out.
const OVERVIEW = item({ id: "i4", key: "projects_overview", title: "Projects — Projects overview", copies: 0, dashboards: 0,
  gaps: ["measure overdue_go_lives"] });
const EFFECTS: StarterEffects = {
  user_copies_unaffected: 12,
  dashboards_getting_new_draft: [{ dashboard_id: "d1", title: "Portfolio review", owner: "Nomsa Osei", live_version_stays: 9 }],
  drafts_without_it: [],
  starter_dashboards: [{ dashboard_id: "s1", title: "PMO — Projects" }],
};

type Handler = (url: string, init?: RequestInit) => Response | undefined;
const calls: { url: string; method: string; body: unknown }[] = [];

function serve(handler: Handler) {
  calls.length = 0;
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ url, method: init?.method ?? "GET", body: init?.body ? JSON.parse(String(init.body)) : undefined });
    if (url.endsWith("/api/v1/me")) return json(admin);
    return handler(url, init) ?? (url.includes("/api/v1/notifications") ? json({ items: [], unread_count: 0, next_cursor: null, kinds: [] }) : json({ error: { code: "not_found", message: "Not found" } }, 404));
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

describe("Starter content (US-099)", () => {
  it("lists state, visibility, copies and gaps, and deletes only after showing the effects", async () => {
    serve((url, init) => {
      if (url.endsWith("/api/v1/admin/starter")) return json({ items: [DELIVERY, FLOW, RAID, OVERVIEW], roles: ROLES });
      if (url.includes("/api/v1/admin/starter/i1?dry_run=true")) return json({ dry_run: true, effects: EFFECTS });
      if (url.endsWith("/api/v1/admin/starter/i1") && init?.method === "DELETE") {
        return json({ dry_run: false, effects: EFFECTS, notified: 1, item: { ...DELIVERY, state: "deleted" } });
      }
      if (url.endsWith("/api/v1/admin/starter/i2/restore")) return json({ item: { ...FLOW, state: "active" } });
      return undefined;
    });
    renderAt("/admin/starter");
    const table = await screen.findByRole("table", { name: "Starter reports" });
    const delivery = await within(table).findByRole("row", { name: /Projects — Delivery/ });
    expect(within(delivery).getByText("Active")).toBeInTheDocument();
    expect(within(delivery).getByText("Everyone")).toBeInTheDocument();
    expect(within(delivery).getByText("12")).toBeInTheDocument();
    const raid = within(table).getByRole("row", { name: /RAID Summary/ });
    expect(within(raid).getByText("Unavailable")).toBeInTheDocument();
    expect(within(raid).getByText(/Needs risks, assumptions/)).toBeInTheDocument();
    const overview = within(table).getByRole("row", { name: /Projects overview/ });
    expect(within(overview).getByText("Some widgets left out: needs measure overdue_go_lives")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Starter content" })).toHaveAttribute("aria-current", "page");

    await userEvent.click(within(delivery).getByRole("button", { name: "Delete" }));
    const dialog = await screen.findByRole("dialog", { name: "Delete Projects — Delivery?" });
    expect(await within(dialog).findByText("12 user copies stay and keep working.")).toBeInTheDocument();
    expect(within(dialog).getByText(/It leaves PMO — Projects at once/)).toBeInTheDocument();
    expect(within(dialog).getByText(/Portfolio review — version 9 stays live · Nomsa Osei is notified/)).toBeInTheDocument();
    expect(calls.filter((c) => c.method === "DELETE").map((c) => c.url)).toEqual(["http://api.test/api/v1/admin/starter/i1?dry_run=true"]);
    expect(within(dialog).getByRole("button", { name: "Cancel" })).toHaveFocus();
    await userEvent.click(within(dialog).getByRole("button", { name: "Delete starter report" }));
    await waitFor(() => expect(calls.some((c) => c.method === "DELETE" && c.url.endsWith("/admin/starter/i1"))).toBe(true));
    expect(await within(table).findAllByText("Deleted")).toHaveLength(2);

    const flow = within(table).getByRole("row", { name: /Projects — Flow/ });
    await userEvent.click(within(flow).getByRole("button", { name: "Restore from pack" }));
    await waitFor(() => expect(calls.some((c) => c.method === "POST" && c.url.endsWith("/admin/starter/i2/restore"))).toBe(true));
    expect(await within(flow).findByText("Active")).toBeInTheDocument();
  });

  it("hides a report from a role and shows it to all again", async () => {
    let current = DELIVERY;
    serve((url, init) => {
      if (url.endsWith("/api/v1/admin/starter")) return json({ items: [current], roles: ROLES });
      if (url.endsWith("/api/v1/admin/starter/i1/visibility")) {
        const body = JSON.parse(String(init?.body)) as { hide_from_roles?: string[] };
        const hidden = body.hide_from_roles ?? [];
        current = { ...DELIVERY, state: hidden.length ? "hidden" : "active", visibility: visibility(hidden) };
        return json(current);
      }
      return undefined;
    });
    renderAt("/admin/starter");
    await userEvent.click(await screen.findByRole("button", { name: "Visibility of Projects — Delivery" }));
    await userEvent.click(await screen.findByRole("checkbox", { name: "Viewer" }));
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(calls.find((c) => c.url.endsWith("/visibility"))?.body).toEqual({ hide_from_roles: ["r-viewer"] }));
    expect(await screen.findByText("Hidden from Viewer")).toBeInTheDocument();
    expect(screen.getByText("Hidden")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Visibility of Projects — Delivery" }));
    await userEvent.click(await screen.findByRole("button", { name: "Show to all" }));
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(calls.filter((c) => c.url.endsWith("/visibility")).at(-1)?.body).toEqual({ show_to_all: true }));
    expect(await screen.findByText("Everyone")).toBeInTheDocument();
  });

  it("offers a newer pack version, says copies stay untouched, and applies it after confirming", async () => {
    let applied = false;
    const update: PackUpdate = {
      pack_id: "p13", pack_key: "pmo_tpg", title: "PMO starter pack (TPG)", installed: "1.2.0", available: "1.3.0",
      released_at: "2026-10-22T00:00:00Z", model_id: "m1", connection: { id: "c1", name: "TPG Postgres" }, status: "available",
      pending: null, summary: "starter reports: 1 changed, 1 removed.", model_draft_required: false,
      changes: [{ kind: "report", key: "projects_delivery", title: "Projects — Delivery", change: "changed" },
        { kind: "report", key: "projects_finance", title: "Projects — Finance", change: "removed" }],
      user_copies_affected: 0, user_copies_unaffected: 12,
      dashboards_getting_new_draft: [{ dashboard_id: "d1", title: "Portfolio review", owner: "Nomsa Osei", live_version_stays: 9, report: "Projects — Delivery", change: "changed" }],
      error: null,
    };
    serve((url, init) => {
      if (url.endsWith("/api/v1/admin/starter")) return json({ items: [DELIVERY], roles: ROLES });
      if (url.endsWith("/api/v1/admin/packs/updates")) return json({ items: applied ? [] : [update] });
      if (url.endsWith("/api/v1/admin/packs/p13/update") && init?.method === "POST") {
        applied = true;
        return json({ status: "applied", summary: update.summary, owners_notified: 1 });
      }
      return undefined;
    });
    renderAt("/admin/starter");
    const card = await screen.findByRole("article", { name: "PMO starter pack (TPG) 1.3.0 for TPG Postgres" });
    expect(within(card).getByText(/Installed 1.2.0/)).toBeInTheDocument();
    expect(within(card).getByText(/people's copies are never touched \(12 copies here\)/)).toBeInTheDocument();
    expect(within(card).getByText(/Portfolio review gets a new draft \(Projects — Delivery changed\) — version 9 stays live · Nomsa Osei is notified/)).toBeInTheDocument();
    await userEvent.click(within(card).getByText("What changes (2)"));
    expect(within(card).getByText(/Starter report: Projects — Finance/)).toBeInTheDocument();

    await userEvent.click(within(card).getByRole("button", { name: "Update…" }));
    const dialog = await screen.findByRole("dialog", { name: "Update TPG Postgres to 1.3.0?" });
    expect(within(dialog).getByText("12 user copies stay exactly as they are.")).toBeInTheDocument();
    expect(within(dialog).getByText("The starter reports are replaced now.")).toBeInTheDocument();
    await userEvent.click(within(dialog).getByRole("button", { name: "Apply update" }));
    await waitFor(() => expect(calls.find((c) => c.url.endsWith("/packs/p13/update"))?.body).toEqual({ connection_id: "c1" }));
    await waitFor(() => expect(screen.queryByRole("article")).not.toBeInTheDocument());
  });

  it("points to the model draft when an applied version waits for approval", async () => {
    serve((url) => {
      if (url.endsWith("/api/v1/admin/starter")) return json({ items: [DELIVERY], roles: ROLES });
      if (url.endsWith("/api/v1/admin/packs/updates")) return json({ items: [{
        pack_id: "p14", pack_key: "pmo_tpg", title: "PMO starter pack (TPG)", installed: "1.3.0", available: "1.4.0",
        released_at: "2026-10-22T00:00:00Z", model_id: "m1", connection: { id: "c1", name: "TPG Postgres" },
        status: "awaiting_approval", pending: { version: "1.4.0", draft_version_id: "v9" },
        summary: "measures: 1 added.", model_draft_required: true,
        changes: [{ kind: "measure", key: "all", title: "All project records", change: "added" }],
        user_copies_affected: 0, user_copies_unaffected: 0, dashboards_getting_new_draft: [], error: null,
      } satisfies PackUpdate] });
      return undefined;
    });
    renderAt("/admin/starter");
    const card = await screen.findByRole("article", { name: /1.4.0 for TPG Postgres/ });
    expect(within(card).getByText("Waiting for model approval")).toBeInTheDocument();
    expect(within(card).getByRole("link", { name: "Review the model draft" })).toHaveAttribute("href", "/admin/models/m1/approve");
    expect(within(card).queryByRole("button", { name: "Update…" })).not.toBeInTheDocument();
  });
});
