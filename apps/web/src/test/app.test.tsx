import { QueryClient } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError, api } from "../api/client";
import type { Me, MappingsResponse } from "../api/types";
import { AppRoutes, Providers } from "../app/App";
import { TestAuthProvider } from "../app/auth";
import { setConfigForTests } from "../app/config";
import { describePredicate } from "../features/admin/access/ScopePoliciesPage";

const baseMe: Me = {
  user_id: "u1",
  display_name: "Nomsa Osei",
  email: null,
  tenant_id: "t1",
  roles: ["viewer"],
  level: "viewer",
  audience_roles: ["cio"],
  permissions: ["dashboards.view", "ask.use"],
  scope_summary: [{ connection_id: "c1", connection_name: "TPG", text: "projects: CIO: Nomsa Osei" }],
  locale: null,
  time_zone: null,
};

type Route = (url: string, init?: RequestInit) => { status: number; body?: unknown; headers?: Record<string, string> };

function mockFetch(route: Route) {
  const calls: { url: string; init?: RequestInit }[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ url, init });
    const r = route(url, init);
    return new Response(r.body === undefined ? null : JSON.stringify(r.body), {
      status: r.status,
      headers: { "Content-Type": "application/json", ...(r.headers ?? {}) },
    });
  }));
  return calls;
}

function renderApp(path = "/") {
  window.history.pushState({}, "", path);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <TestAuthProvider token="tok-123">
      <Providers client={client}>
        <AppRoutes />
      </Providers>
    </TestAuthProvider>,
  );
}

beforeEach(() => setConfigForTests({ apiBaseUrl: "http://api.test", oidcAuthority: "http://kc", oidcClientId: "tela-web" }));
afterEach(() => vi.unstubAllGlobals());

describe("api client", () => {
  it("sends the bearer token and parses the error envelope", async () => {
    const calls = mockFetch(() => ({
      status: 403,
      body: { error: { code: "forbidden", message: "You don't have permission to do that.", request_id: "req-1", details: {} } },
    }));
    const { setTokenSource } = await import("../api/client");
    setTokenSource(() => "abc");
    await expect(api("/api/v1/admin/roles")).rejects.toMatchObject({ status: 403, code: "forbidden", requestId: "req-1" });
    expect((calls[0]!.init!.headers as Record<string, string>).Authorization).toBe("Bearer abc");
  });

  it("is an ApiError instance", () => {
    expect(new ApiError(400, "x", "m", null)).toBeInstanceOf(Error);
  });
});

describe("app shell", () => {
  it("shows the greeting and locked scope chip, and hides Admin for a viewer", async () => {
    mockFetch((url) => {
      if (url.endsWith("/api/v1/me")) return { status: 200, body: baseMe };
      if (url.endsWith("/api/v1/home")) {
        return { status: 200, body: {
          greeting: { name: "Nomsa Osei", scopes: [{ connection_id: "c1", connection_name: "TPG", text: "projects: CIO: Nomsa Osei" }], as_of: null },
          shelves: { starter: [], published_to_my_role: [], shared_with_me: [] },
          empty_text: { starter: "No starter content yet.", published_to_my_role: "Nothing published to your role yet.", shared_with_me: "Nothing shared." },
          can_create: false,
        } };
      }
      return { status: 404, body: {} };
    });
    renderApp("/");
    expect(await screen.findByText(/Nomsa$/)).toBeInTheDocument();
    expect(screen.getByText(/TPG: projects: CIO: Nomsa Osei/)).toBeInTheDocument();
    expect(screen.queryByText("Admin · Access")).not.toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Mine" })).not.toBeInTheDocument();
    expect(screen.getByRole("searchbox", { name: "Ask anything about your data" })).toBeInTheDocument();
  });

  it("shows the no-access message (MSG-001) for an unmapped user", async () => {
    mockFetch(() => ({
      status: 403,
      body: { error: { code: "no_access", message: "You don't have access to Tela yet. Ask your PMO admin.", request_id: "r" } },
    }));
    renderApp("/");
    expect(await screen.findByText("You don't have access to Tela yet. Ask your PMO admin.")).toBeInTheDocument();
  });
});

describe("group mappings", () => {
  const adminMe: Me = { ...baseMe, user_id: "admin-1", level: "admin", roles: ["admin"], permissions: ["mappings.manage", "policies.manage"] };
  const mappings: MappingsResponse = {
    etag: 'W/"e1"',
    mappings: [
      { id: "m1", idp_group_id: "TELA-Admins", group_name: "TELA-Admins", role: "admin", role_kind: "system", requires_approval: true, status: "active", scope_attributes: {}, requested_by: null },
      { id: "m2", idp_group_id: "TELA-Ops", group_name: "Ops", role: "admin", role_kind: "system", requires_approval: true, status: "pending_approval", scope_attributes: {}, requested_by: "someone-else" },
    ],
  };

  it("lists mappings, offers approval for pending ones and saves with If-Match", async () => {
    const calls = mockFetch((url, init) => {
      if (url.endsWith("/api/v1/me")) return { status: 200, body: adminMe };
      if (url.endsWith("/api/v1/admin/group-mappings") && (!init?.method || init.method === "GET")) return { status: 200, body: mappings };
      if (url.endsWith("/api/v1/admin/group-mappings") && init?.method === "PUT") return { status: 200, body: { ...mappings, mappings: [mappings.mappings[0]] } };
      if (url.endsWith("/api/v1/admin/roles")) return { status: 200, body: { items: [] } };
      if (url.endsWith("/api/v1/admin/groups")) return { status: 200, body: { items: [] } };
      return { status: 404, body: {} };
    });
    renderApp("/admin/access/mappings");
    expect(await screen.findByText("Pending approval")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Approve" })).toBeInTheDocument();

    await userEvent.click(screen.getAllByRole("button", { name: "Remove" })[1]!);
    // Removing asks first; Cancel has the focus.
    const dialog = await screen.findByRole("dialog", { name: "Remove the mapping for Ops?" });
    expect(within(dialog).getByRole("button", { name: "Cancel" })).toHaveFocus();
    expect(calls.some((c) => c.init?.method === "PUT")).toBe(false);
    await userEvent.click(within(dialog).getByRole("button", { name: "Remove mapping" }));
    await waitFor(() => expect(calls.some((c) => c.init?.method === "PUT")).toBe(true));
    const put = calls.find((c) => c.init?.method === "PUT")!;
    expect((put.init!.headers as Record<string, string>)["If-Match"]).toBe('W/"e1"');
    expect(JSON.parse(put.init!.body as string)).toEqual({
      mappings: [{ idp_group_id: "TELA-Admins", role: "admin", scope_attributes: {} }],
    });
  });
});

describe("predicate wording", () => {
  it("describes conditions in plain language", () => {
    expect(describePredicate({ field: "cio", op: "=", attr: "cio" })).toBe("cio = the person's cio");
    expect(describePredicate({ field: "region", op: "in", values: ["KSA", "GCC - UAE"] })).toBe("region in KSA, GCC - UAE");
    expect(describePredicate({ field: "region", op: "is null" })).toBe("region is blank");
  });
});
