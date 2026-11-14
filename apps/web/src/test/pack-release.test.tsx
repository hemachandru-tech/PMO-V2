import { QueryClient } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { PackDetail, PackDiff, PackReview, PackVersion, ReleaseChecks } from "../api/packs";
import type { Me } from "../api/types";
import { AppRoutes, Providers } from "../app/App";
import { TestAuthProvider } from "../app/auth";
import { setConfigForTests } from "../app/config";

const admin: Me = {
  user_id: "u1", display_name: "Ada Admin", email: null, tenant_id: "t1", roles: ["admin"], level: "admin", audience_roles: [],
  permissions: ["dashboards.view", "packs.author", "starter.manage", "certification.decide"], scope_summary: [], locale: null, time_zone: null,
};
const version = (over: Partial<PackVersion>): PackVersion => ({
  id: "p1", pack_key: "acme_pmo", version: "1.1.0", title: "Acme PMO", status: "draft", owner: "tenant", editable: true,
  released_at: null, updated_at: "2026-11-06T10:00:00Z", based_on_pack_id: "p0", revision: "r7",
  counts: { entities: 1, links: 0, measures: 1, dimensions: 0 }, installed_on: [], review: { state: "draft", created_by: "u1" }, ...over,
});
const detail = (over: Partial<PackVersion>): PackDetail => ({
  ...version(over),
  manifest: { pack_key: "acme_pmo", pack_version: "1.1.0", title: "Acme PMO", released_at: "2026-11-06T00:00:00Z", signature: { primary_entity: "projects" },
    entities: [], links: [], measures: [], dimensions: [] },
  documents: {}, checks: { valid: true, errors: [], warnings: [] },
});
const CHECKS: ReleaseChecks = {
  passed: true, errors: [],
  warnings: [{ id: "w1", level: "warning", check: "sanity", path: "measures.cancelled_projects", message: "Cancelled projects returns 0 on your data." }],
  steps: [
    { key: "schema", label: "Schema", ran: true, errors: 0, warnings: 0 },
    { key: "sanity", label: "Sanity checks", ran: true, errors: 0, warnings: 1 },
  ],
  facts: { connection: { id: "c1", name: "TPG Postgres" }, compiled: { measures: 42, dimensions: 30 }, gold_planned: 12 },
};
const DIFF: PackDiff = {
  against: { id: "p0", version: "1.0.0" },
  changes: [{ kind: "measure", key: "open_projects", title: "Open projects", change: "changed", fields: ["plain_rule"],
              rule: { before: "Projects with status Active.", after: "Projects not closed or cancelled." } }],
  summary: { measure: { added: 0, changed: 1, removed: 0 } },
};
const you = (over: Partial<PackReview["you"]>): PackReview["you"] => ({
  author: false, can_submit: false, can_withdraw: false, can_release: false, can_return: false, can_open_next: false, ...over,
});
const calls: { url: string; method: string; body: unknown; headers: Record<string, string> }[] = [];

function json(body: unknown, status = 200, headers: Record<string, string> = {}) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json", ...headers } });
}

function serve(pack: PackDetail, review: PackReview, extra: (url: string, method: string) => Response | undefined = () => undefined) {
  calls.length = 0;
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    const method = init?.method ?? "GET";
    const body = typeof init?.body === "string" ? JSON.parse(init.body) : undefined;
    calls.push({ url, method, body, headers: (init?.headers ?? {}) as Record<string, string> });
    const out = extra(url, method);
    if (out) return out;
    if (url.endsWith("/api/v1/me")) return json(admin);
    if (url.endsWith("/api/v1/connections")) return json({ items: [{ id: "c1", name: "TPG Postgres", engine: "postgres", status: "connected" }] });
    if (url.endsWith("/api/v1/packs/p1")) return json(pack, 200, { ETag: '"r7"' });
    if (url.endsWith("/api/v1/packs/p1/review")) return json(review);
    if (url.endsWith("/api/v1/packs/p1/versions")) return json({ pack_key: "acme_pmo", items: [version(pack)] });
    if (url.endsWith("/api/v1/packs/p1/diff")) return json(DIFF);
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

describe("Pack release (US-238)", () => {
  it("runs the checks on a test connection, needs a reason per warning and submits for review", async () => {
    serve(detail({}), { state: { state: "draft", created_by: "u1" }, people: {}, review: null, you: you({ author: true, can_submit: true }) },
      (url, method) => {
        if (url.endsWith("/release-checks") && method === "POST") return json({ checks: CHECKS, diff: DIFF, revision: "x" });
        if (url.endsWith("/submit") && method === "POST") return json(version({ editable: false, review: { state: "in_review" } }));
        return undefined;
      });
    renderAt("/admin/packs/p1?tab=versions");
    const panel = await screen.findByRole("region", { name: "Release" });
    await userEvent.selectOptions(within(panel).getByLabelText("Test connection"), "c1");
    await userEvent.click(within(panel).getByRole("button", { name: "Run release checks" }));

    expect(await within(panel).findByText(/42 measures and 30 dimensions compiled, 12 gold examples planned/)).toBeInTheDocument();
    expect(within(panel).getByText("Cancelled projects returns 0 on your data.")).toBeInTheDocument();
    // The diff shows the changed rule, before and after.
    expect(within(panel).getByText("Changes since 1.0.0")).toBeInTheDocument();
    expect(within(panel).getByText("Projects with status Active.")).toBeInTheDocument();
    expect(within(panel).getByText("Projects not closed or cancelled.")).toBeInTheDocument();

    const submit = within(panel).getByRole("button", { name: "Submit for review" });
    expect(submit).toBeDisabled();
    expect(within(panel).getByText("1 warnings still need a reason.")).toBeInTheDocument();
    await userEvent.type(within(panel).getByLabelText(/Reason for: Cancelled projects/), "No cancelled projects in TPG yet.");
    await userEvent.click(submit);
    await waitFor(() => expect(calls.some((c) => c.url.endsWith("/submit"))).toBe(true));
    const sent = calls.find((c) => c.url.endsWith("/submit"))!;
    expect(sent.body).toEqual({ connection_id: "c1", reasons: { w1: "No cancelled projects in TPG yet." } });
    expect(sent.headers["If-Match"]).toBe('"r7"');
  });

  it("lets a reviewer who is not the author release, and tells the author someone else must", async () => {
    const pack = detail({ editable: false, review: { state: "in_review", submitted_by: "u2", created_by: "u2" } });
    const review: PackReview = {
      state: { state: "in_review", submitted_by: "u2", created_by: "u2", submitted_at: "2026-11-06T09:00:00Z", note: "First Acme release" },
      people: { u2: "Ben Author" }, review: { connection_id: "c1", reasons: { w1: "No cancelled projects in TPG yet." }, checks: CHECKS, note: "First Acme release" },
      you: you({ can_release: true, can_return: true, can_withdraw: true }),
    };
    serve(pack, review, (url, method) => (url.endsWith("/release") && method === "POST"
      ? json({ ...version({ status: "released", editable: false }), diff: DIFF, update_notice: { connections: 1, notified: 2 } }) : undefined));
    renderAt("/admin/packs/p1?tab=versions");
    const panel = await screen.findByRole("region", { name: "Review" });
    expect(within(panel).getByText(/Submitted by Ben Author/)).toBeInTheDocument();
    expect(within(panel).getByText("Reason: No cancelled projects in TPG yet.")).toBeInTheDocument();
    expect(screen.getByText(/is waiting for review and can't change/)).toBeInTheDocument();
    expect(within(panel).queryByRole("button", { name: "Withdraw to edit" })).not.toBeInTheDocument();

    await userEvent.click(within(panel).getByRole("button", { name: "Release" }));
    const dialog = await screen.findByRole("dialog", { name: /Release Acme PMO 1.1.0/ });
    await userEvent.click(within(dialog).getByRole("button", { name: "Release" }));
    await waitFor(() => expect(calls.some((c) => c.url.endsWith("/release") && c.method === "POST")).toBe(true));
    expect(await screen.findByText("Released. 1 connection offered the update.")).toBeInTheDocument();
  });

  it("shows the author that another reviewer must release it", async () => {
    const pack = detail({ editable: false, review: { state: "in_review", submitted_by: "u1", created_by: "u1" } });
    serve(pack, { state: { state: "in_review", submitted_by: "u1", created_by: "u1" }, people: { u1: "Ada Admin" }, review: { checks: CHECKS, reasons: {} },
      you: you({ author: true, can_withdraw: true }) });
    renderAt("/admin/packs/p1?tab=versions");
    const panel = await screen.findByRole("region", { name: "Review" });
    expect(within(panel).getByText(/another Admin or a certifier releases it/)).toBeInTheDocument();
    expect(within(panel).queryByRole("button", { name: "Release" })).not.toBeInTheDocument();
    expect(within(panel).getByRole("button", { name: "Withdraw to edit" })).toBeInTheDocument();
  });
});
