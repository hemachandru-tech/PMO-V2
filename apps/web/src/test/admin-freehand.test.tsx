import { QueryClient } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { AccessRequest, GrantConnection, Me, PermissionGrant } from "../api/types";
import { AppRoutes, Providers } from "../app/App";
import { TestAuthProvider } from "../app/auth";
import { setConfigForTests } from "../app/config";
import { dayFromNow } from "../features/admin/access/FreehandPage";

const admin: Me = {
  user_id: "u1", display_name: "Ada Admin", email: null, tenant_id: "t1", roles: ["admin"], level: "admin", audience_roles: [],
  permissions: ["dashboards.view", "connections.manage", "mappings.manage", "policies.manage", "audit.read"],
  scope_summary: [], locale: null, time_zone: null,
};
const creator: Me = { ...admin, user_id: "u2", display_name: "Nomsa Osei", roles: ["creator"], level: "creator", permissions: ["dashboards.view", "ask.use", "customsql.use"] };

const GRANT: PermissionGrant = {
  id: "g1", user: { id: "u9", display_name: "Meera Pillai", email: "meera@x" }, permission: "ask.freehand",
  connection: { id: "c1", name: "TPG Postgres" }, reason: "Portfolio review", granted_by: { id: "u1", display_name: "Ada Admin" },
  granted_at: "2026-10-27T09:00:00Z", expires_at: "2027-01-25T09:00:00Z", removed_at: null, removed_by: null, removed_reason: null,
  last_used_at: null, status: "active",
};
const REQUEST: AccessRequest = {
  id: "q1", user: { id: "u7", display_name: "Vic Viewer", email: null }, permission: "ask.freehand",
  connection: { id: "c1", name: "TPG Postgres" }, note: "Stage-date questions", status: "pending",
  requested_at: "2026-10-27T08:00:00Z", decided_by: null, decided_at: null, decision_reason: null, grant_id: null,
};
const CONNECTIONS: GrantConnection[] = [
  { id: "c1", name: "TPG Postgres", visible: true, grant: null },
  { id: "c2", name: "Finance", visible: false, grant: null },
];

type Handler = (url: string, init?: RequestInit) => Response | undefined;
const calls: { url: string; method: string; body: unknown }[] = [];

function serve({ me = admin, grants = [GRANT], requests = [REQUEST], handler = () => undefined }: {
  me?: Me; grants?: PermissionGrant[]; requests?: AccessRequest[]; handler?: Handler;
} = {}) {
  calls.length = 0;
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    const method = init?.method ?? "GET";
    calls.push({ url, method, body: init?.body ? JSON.parse(String(init.body)) : undefined });
    if (url.endsWith("/api/v1/me")) return json(me);
    if (url.includes("/api/v1/notifications")) return json({ items: [], unread_count: 0 });
    const own = handler(url, init);
    if (own) return own;
    if (url.endsWith("/api/v1/admin/group-mappings")) return json({ etag: 'W/"e1"', mappings: [] });
    if (url.includes("/api/v1/admin/permission-grants/connections")) return json({ items: CONNECTIONS });
    if (url.includes("/api/v1/admin/permission-grants") && method === "GET") return json({ items: grants });
    if (url.endsWith("/api/v1/admin/permission-requests")) return json({ items: requests });
    if (url.includes("/api/v1/admin/users")) return json({ items: [{ id: "u7", display_name: "Vic Viewer" }] });
    if (method !== "GET") return json({});
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

const sent = (method: string, part: string) => calls.filter((c) => c.method === method && c.url.includes(part));

beforeEach(() => setConfigForTests({ apiBaseUrl: "http://api.test", oidcAuthority: "http://kc", oidcClientId: "tela-web" }));
afterEach(() => vi.unstubAllGlobals());

describe("Free-hand query screen (US-162)", () => {
  it("lists people with access and pending requests; the tab shows the request count", async () => {
    serve();
    renderAt("/admin/access/freehand");
    const table = await screen.findByRole("table", { name: "People with free-hand access" });
    const row = (await within(table).findByText("Meera Pillai")).closest("tr")!;
    expect(row).toHaveTextContent("TPG Postgres");
    expect(row).toHaveTextContent("Portfolio review");
    expect(row).toHaveTextContent("Ada Admin");
    expect(row).toHaveTextContent("Never");
    const requests = await screen.findByRole("table", { name: "Free-hand access requests" });
    expect(requests).toHaveTextContent("Stage-date questions");
    expect(screen.getByRole("link", { name: /Free-hand query/ })).toHaveTextContent("1");
  });

  it("Show ended adds ended grants with their status", async () => {
    const removed: PermissionGrant = { ...GRANT, id: "g0", reason: "Old review", status: "removed", removed_at: "2026-10-26T09:00:00Z", removed_reason: "admin" };
    serve({ handler: (url) => (url.includes("/permission-grants?") && url.includes("include_ended=true") ? json({ items: [GRANT, removed] }) : undefined) });
    renderAt("/admin/access/freehand");
    const table = await screen.findByRole("table", { name: "People with free-hand access" });
    await within(table).findByText("Portfolio review");
    expect(within(table).queryByText("Old review")).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("checkbox", { name: "Show ended" }));
    const row = (await within(table).findByText("Old review")).closest("tr")!;
    expect(row).toHaveTextContent("Removed");
    expect(within(row).queryByRole("button", { name: /Remove|Extend/ })).not.toBeInTheDocument();
  });

  it("explains free-hand access when nobody has it", async () => {
    serve({ grants: [], requests: [] });
    renderAt("/admin/access/freehand");
    expect(await screen.findByText("Nobody can ask free-hand questions yet")).toBeInTheDocument();
    expect(screen.queryByRole("table", { name: "Free-hand access requests" })).not.toBeInTheDocument();
  });

  it("gives access: person, connections they can see, end date, reason, then a confirmation", async () => {
    serve({ grants: [] });
    renderAt("/admin/access/freehand");
    await userEvent.click((await screen.findAllByRole("button", { name: "Give access" }))[0]!);
    const dialog = await screen.findByRole("dialog", { name: "Give free-hand access" });
    expect(within(dialog).getByLabelText("Ends on")).toHaveValue(dayFromNow(90));

    await userEvent.click(within(dialog).getByRole("button", { name: "Continue" }));
    expect(await within(dialog).findByText("Choose a person.")).toBeInTheDocument();

    await userEvent.type(within(dialog).getByRole("combobox"), "Vic");
    await userEvent.click(await within(dialog).findByRole("option", { name: /Vic Viewer/ }));
    const finance = (await within(dialog).findByText("Finance")).closest("label")!;
    expect(finance).toHaveTextContent("Vic Viewer can't see this connection");
    expect(within(finance).getByRole("checkbox")).toBeDisabled();
    await userEvent.click(within((within(dialog).getByText("TPG Postgres")).closest("label")!).getByRole("checkbox"));
    await userEvent.type(within(dialog).getByLabelText("Reason"), "Stage-date analysis");
    await userEvent.click(within(dialog).getByRole("button", { name: "Continue" }));

    const confirm = await screen.findByRole("dialog", { name: "Give Vic Viewer free-hand access?" });
    expect(confirm).toHaveTextContent("Vic Viewer will be able to ask questions in their own words");
    expect(confirm).toHaveTextContent("TPG Postgres");
    expect(confirm).toHaveTextContent("Not certified");
    expect(sent("POST", "/permission-grants")).toHaveLength(0);
    await userEvent.click(within(confirm).getByRole("button", { name: "Give access" }));
    await waitFor(() => expect(sent("POST", "/permission-grants")).toHaveLength(1));
    expect(sent("POST", "/permission-grants")[0]!.body).toMatchObject({
      user_id: "u7", connection_id: "c1", permission: "ask.freehand", reason: "Stage-date analysis",
    });
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("removes access after a confirmation; Cancel changes nothing", async () => {
    serve();
    renderAt("/admin/access/freehand");
    await userEvent.click(await screen.findByRole("button", { name: "Remove Meera Pillai on TPG Postgres" }));
    let dialog = await screen.findByRole("dialog", { name: "Remove Meera Pillai's free-hand access on TPG Postgres?" });
    expect(dialog).toHaveTextContent("It stops at once");
    await userEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
    expect(sent("DELETE", "/permission-grants")).toHaveLength(0);
    await userEvent.click(screen.getByRole("button", { name: "Remove Meera Pillai on TPG Postgres" }));
    dialog = await screen.findByRole("dialog", { name: /Remove Meera Pillai/ });
    await userEvent.click(within(dialog).getByRole("button", { name: "Remove access" }));
    await waitFor(() => expect(sent("DELETE", "/permission-grants/g1")).toHaveLength(1));
  });

  it("extends access to a new end date", async () => {
    serve();
    renderAt("/admin/access/freehand");
    await userEvent.click(await screen.findByRole("button", { name: "Extend Meera Pillai on TPG Postgres" }));
    const dialog = await screen.findByRole("dialog", { name: "Extend Meera Pillai's access" });
    const date = within(dialog).getByLabelText("New end date");
    await userEvent.clear(date);
    await userEvent.type(date, dayFromNow(400));
    await userEvent.click(within(dialog).getByRole("button", { name: "Save end date" }));
    expect(await within(dialog).findByText("Access can last at most a year.")).toBeInTheDocument();
    await userEvent.clear(date);
    await userEvent.type(date, dayFromNow(180));
    await userEvent.click(within(dialog).getByRole("button", { name: "Save end date" }));
    await waitFor(() => expect(sent("PATCH", "/permission-grants/g1")).toHaveLength(1));
    const body = sent("PATCH", "/permission-grants/g1")[0]!.body as { expires_at: string };
    expect(body.expires_at.slice(0, 4)).toBe(dayFromNow(180).slice(0, 4));
  });

  it("approves a request with the note as the reason", async () => {
    serve();
    renderAt("/admin/access/freehand");
    await userEvent.click(await screen.findByRole("button", { name: "Approve Vic Viewer on TPG Postgres" }));
    const dialog = await screen.findByRole("dialog", { name: "Approve Vic Viewer's request?" });
    expect(within(dialog).getByLabelText("Reason")).toHaveValue("Stage-date questions");
    await userEvent.click(within(dialog).getByRole("button", { name: "Approve" }));
    await waitFor(() => expect(sent("POST", "/permission-requests/q1/approve")).toHaveLength(1));
    expect(sent("POST", "/permission-requests/q1/approve")[0]!.body).toMatchObject({ reason: "Stage-date questions" });
  });

  it("declines a request only with a reason", async () => {
    serve();
    renderAt("/admin/access/freehand");
    await userEvent.click(await screen.findByRole("button", { name: "Decline Vic Viewer on TPG Postgres" }));
    const dialog = await screen.findByRole("dialog", { name: "Decline Vic Viewer's request?" });
    await userEvent.click(within(dialog).getByRole("button", { name: "Decline" }));
    expect(await within(dialog).findByText("Say why, so they know what to do next.")).toBeInTheDocument();
    expect(sent("POST", "/decline")).toHaveLength(0);
    await userEvent.type(within(dialog).getByLabelText("Reason"), "Use Past planning");
    await userEvent.click(within(dialog).getByRole("button", { name: "Decline" }));
    await waitFor(() => expect(sent("POST", "/permission-requests/q1/decline")).toHaveLength(1));
    expect(sent("POST", "/decline")[0]!.body).toEqual({ reason: "Use Past planning" });
  });

  it("a Creator cannot open it", async () => {
    serve({ me: creator });
    renderAt("/admin/access/freehand");
    expect(await screen.findByRole("alert")).toHaveTextContent("You don't have permission to manage access.");
    expect(sent("GET", "/permission-grants")).toHaveLength(0);
    expect(sent("GET", "/permission-requests")).toHaveLength(0);
  });
});
