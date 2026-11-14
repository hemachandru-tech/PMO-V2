import { QueryClient } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { DashboardGroup, MyDashboardGroup } from "../api/authoring";
import type { Me } from "../api/types";
import { AppRoutes, Providers } from "../app/App";
import { TestAuthProvider } from "../app/auth";
import { setConfigForTests } from "../app/config";
import { dashboardIdOf } from "../app/shell/SidebarGroups";

vi.mock("echarts/core", () => ({
  use: vi.fn(),
  registerTheme: vi.fn(),
  init: () => ({ setOption: vi.fn(), on: vi.fn(), resize: vi.fn(), dispose: vi.fn() }),
}));

const viewer: Me = {
  user_id: "u3", display_name: "Vic Viewer", email: null, tenant_id: "t1", roles: ["viewer"], level: "viewer", audience_roles: [],
  permissions: ["dashboards.view", "ask.use", "drill.use", "definitions.view"], scope_summary: [], locale: null, time_zone: null,
};
const creator: Me = {
  ...viewer, user_id: "u2", display_name: "Cleo Creator", roles: ["creator"], level: "creator",
  permissions: [...viewer.permissions, "reports.create", "dashboards.create", "dashboards.group"],
};
const admin: Me = { ...creator, user_id: "u1", display_name: "Ada Admin", roles: ["admin"], level: "admin", permissions: [...creator.permissions, "dashboards.publish"] };

const MINE: MyDashboardGroup[] = [
  { id: "g1", name: "Delivery", icon: "rocket", dashboards: [
    { id: "d1", title: "PMO — Projects", kind: "starter", link: "/dashboards/d1", published: true },
    { id: "d2", title: "Gulf delivery", kind: "user", link: "/dashboards/d2/draft", published: false },
  ] },
  { id: "g2", name: "Finance", icon: null, dashboards: [{ id: "d3", title: "CAPEX", kind: "user", link: "/dashboards/d3", published: true }] },
];
const group = (g: Partial<DashboardGroup> & { id: string; name: string }): DashboardGroup => ({
  icon: null, position: 0, created_by: { id: "u2", name: "Cleo Creator" }, can_edit: true, hidden_count: 0, dashboards: [], ...g,
});
const MANAGE = [
  group({ id: "g1", name: "Delivery", dashboards: MINE[0]!.dashboards, hidden_count: 1 }),
  group({ id: "g2", name: "Finance", position: 1, created_by: { id: "u9", name: "Otto Creator" }, can_edit: false, dashboards: MINE[1]!.dashboards }),
];
const DASH = {
  id: "d1", title: "PMO — Projects", kind: "starter", layout: "tabs", version: 1, version_id: "dv1", connection_id: "c1", model_version_id: "v1",
  locked_scope_chip: "All rows", scope_message: "Your scope.", as_of: null, filters: [], tabs: [],
};

type Handler = (url: string, init?: RequestInit) => Response | undefined;
const calls: { url: string; method: string; body: unknown }[] = [];

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

function serve(me: Me, { mine = MINE, manage = MANAGE, canReorder = false, handler }: {
  mine?: MyDashboardGroup[]; manage?: DashboardGroup[]; canReorder?: boolean; handler?: Handler;
} = {}) {
  calls.length = 0;
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    const method = init?.method ?? "GET";
    calls.push({ url, method, body: init?.body ? JSON.parse(String(init.body)) : undefined });
    const extra = handler?.(url, init);
    if (extra) return extra;
    if (url.endsWith("/api/v1/me")) return json(me);
    if (url.includes("/api/v1/notifications")) return json({ items: [], unread_count: 0 });
    if (url.endsWith("/api/v1/me/dashboard-groups")) return json({ items: mine });
    if (url.endsWith("/api/v1/dashboard-groups") && method === "GET") return json({ items: manage, can_reorder: canReorder });
    if (url.endsWith("/api/v1/published/d1")) return json(DASH);
    if (url.endsWith("/api/v1/home")) {
      return json({ greeting: { name: "x", scopes: [], as_of: null }, empty_text: {}, can_create: true, shelves: {
        starter: [{ id: "d1", type: "dashboard", title: "PMO — Projects", badges: [], version: 1, count: {}, last_edited_at: "" }],
        published_to_my_role: [{ id: "d4", type: "dashboard", title: "Risks", badges: [], version: 1, count: {}, last_edited_at: "" }],
        shared_with_me: [], mine: [] } });
    }
    if (url.includes("/api/v1/dashboards?") || url.endsWith("/api/v1/dashboards")) {
      return json({ items: [{ id: "d2", title: "Gulf delivery", layout: "tabs", status: "draft", updated_at: "2026-10-27T10:00:00Z", owner_name: "Cleo Creator", live_version: null, reports: 2, mine: true }] });
    }
    return json({ items: [] });
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

const sidebar = async () => {
  const nav = await screen.findByRole("navigation", { name: "Main" });
  await within(nav).findByRole("button", { name: "Delivery" });
  return nav;
};
const writes = () => calls.filter((c) => c.method !== "GET").map((c) => ({ url: c.url.replace("http://api.test", ""), method: c.method, body: c.body }));

beforeEach(() => {
  setConfigForTests({ apiBaseUrl: "http://api.test", oidcAuthority: "http://kc", oidcClientId: "tela-web" });
  window.localStorage.clear();
});
afterEach(() => vi.unstubAllGlobals());

describe("dashboard groups in the sidebar (US-171)", () => {
  it("shows a Viewer the groups between Explore and Build, closed until opened, and remembers the choice", async () => {
    serve(viewer);
    const view = renderAt("/");
    const nav = await sidebar();
    const ids = within(nav).getAllByRole("group").map((g) => g.getAttribute("aria-labelledby"));
    expect(ids).toEqual(["nav-Explore", "nav-Dashboards"]);
    const delivery = within(nav).getByRole("button", { name: "Delivery" });
    expect(delivery).toHaveAttribute("aria-expanded", "false");
    expect(within(nav).queryByRole("link", { name: "Gulf delivery" })).not.toBeInTheDocument();

    await userEvent.click(delivery);
    expect(delivery).toHaveAttribute("aria-expanded", "true");
    expect(within(nav).getByRole("link", { name: "PMO — Projects" })).toHaveAttribute("href", "/dashboards/d1");
    expect(within(nav).getByRole("link", { name: "Gulf delivery" })).toHaveAttribute("href", "/dashboards/d2/draft");
    expect(JSON.parse(window.localStorage.getItem("tela.sidebar.openGroups")!)).toEqual(["g1"]);

    view.unmount();
    serve(viewer);
    renderAt("/");
    const again = await sidebar();
    expect(within(again).getByRole("button", { name: "Delivery" })).toHaveAttribute("aria-expanded", "true");
    expect(within(again).getByRole("button", { name: "Finance" })).toHaveAttribute("aria-expanded", "false");
  });

  it("opens the group holding the open dashboard and marks it current", async () => {
    serve(viewer);
    renderAt("/dashboards/d1");
    const nav = await sidebar();
    expect(within(nav).getByRole("button", { name: "Delivery" })).toHaveAttribute("aria-expanded", "true");
    expect(within(nav).getByRole("link", { name: "PMO — Projects" })).toHaveAttribute("aria-current", "page");
    // Aurora (ENH-027): each group carries its own chip colour, after the Explore screens' colours.
    const group = within(nav).getByRole("button", { name: "Delivery" });
    expect(group.querySelector("[data-nav-icon]")).toHaveAttribute("data-nav-icon", "4");
    expect(group.closest("[data-nav-tone]")).toHaveAttribute("data-nav-tone", "4");
    expect(dashboardIdOf("/dashboards/d2/draft")).toBe("d2");
    expect(dashboardIdOf("/dashboards/d2/edit")).toBeUndefined();
  });

  it("leaves the section out when no group has a dashboard for this person", async () => {
    serve(viewer, { mine: [] });
    renderAt("/");
    const nav = await screen.findByRole("navigation", { name: "Main" });
    await waitFor(() => expect(calls.some((c) => c.url.endsWith("/me/dashboard-groups"))).toBe(true));
    expect(within(nav).queryByText("Dashboards")).not.toBeInTheDocument();
  });

  it("shows each group as an icon with a popover list when the sidebar is collapsed", async () => {
    serve(creator);
    renderAt("/");
    await sidebar();
    await userEvent.click(screen.getByRole("button", { name: "Collapse sidebar" }));
    await userEvent.click(within(screen.getByRole("navigation", { name: "Main" })).getByRole("button", { name: "Finance" }));
    const list = await screen.findByRole("navigation", { name: "Finance" });
    expect(within(list).getByRole("link", { name: "CAPEX" })).toHaveAttribute("href", "/dashboards/d3");
  });

  it("lists grouped dashboards under the group's name in the command palette", async () => {
    serve(viewer);
    renderAt("/");
    await sidebar();
    await userEvent.keyboard("{Control>}k{/Control}");
    const palette = await screen.findByRole("dialog");
    const finance = await within(palette).findByRole("group", { name: "Finance" });
    expect(within(finance).getByRole("option", { name: /CAPEX/ })).toBeInTheDocument();
  });
});

describe("managing groups on Build → Dashboards (US-170)", () => {
  it("is offered only with dashboards.group", async () => {
    serve({ ...creator, permissions: creator.permissions.filter((p) => p !== "dashboards.group") });
    renderAt("/dashboards");
    await screen.findByRole("link", { name: "Gulf delivery" });
    expect(screen.queryByRole("tab", { name: "Groups" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Add Gulf delivery to a group/ })).not.toBeInTheDocument();
  });

  it("lets a Creator create a group and change only their own groups", async () => {
    serve(creator, { handler: (url, init) => (url.endsWith("/api/v1/dashboard-groups") && init?.method === "POST"
      ? json(group({ id: "g9", name: "Risk" }), 201) : undefined) });
    renderAt("/dashboards?tab=groups");
    const list = await screen.findByRole("list", { name: "Dashboard groups" });
    expect(within(list).getByText(/Also 1 dashboard you can't open/)).toBeInTheDocument();
    expect(within(list).getByRole("button", { name: "Rename Delivery" })).toBeInTheDocument();
    expect(within(list).queryByRole("button", { name: "Rename Finance" })).not.toBeInTheDocument();
    expect(within(list).queryByRole("button", { name: "Move Delivery down" })).not.toBeInTheDocument(); // only an Admin reorders groups

    await userEvent.click(screen.getByRole("button", { name: "New group" }));
    const dialog = await screen.findByRole("dialog", { name: "New group" });
    await userEvent.type(within(dialog).getByLabelText("Name"), "Risk");
    await userEvent.selectOptions(within(dialog).getByLabelText("Icon"), "shield");
    await userEvent.click(within(dialog).getByRole("button", { name: "Create group" }));
    await waitFor(() => expect(writes()).toEqual([{ url: "/api/v1/dashboard-groups", method: "POST", body: { name: "Risk", icon: "shield" } }]));
  });

  it("adds, reorders and removes dashboards in a group", async () => {
    serve(creator);
    renderAt("/dashboards?tab=groups");
    const inGroup = await screen.findByRole("list", { name: "Dashboards in Delivery" });
    await userEvent.click(within(inGroup).getByRole("button", { name: "Move Gulf delivery up" }));
    await userEvent.click(within(inGroup).getByRole("button", { name: "Remove PMO — Projects from Delivery" }));

    await userEvent.click(screen.getByRole("button", { name: "Add dashboards" }));
    const dialog = await screen.findByRole("dialog", { name: "Dashboards in “Delivery”" });
    const risks = await within(dialog).findByRole("checkbox", { name: /Risks/ });
    await userEvent.click(risks);
    await userEvent.click(within(dialog).getByRole("button", { name: "Save (3)" }));
    await waitFor(() => expect(writes()).toEqual([
      { url: "/api/v1/dashboard-groups/g1/items", method: "PUT", body: { dashboard_ids: ["d2", "d1"] } },
      { url: "/api/v1/dashboard-groups/g1/items", method: "PUT", body: { dashboard_ids: ["d2"] } },
      { url: "/api/v1/dashboard-groups/g1/items", method: "PUT", body: { dashboard_ids: ["d1", "d2", "d4"] } },
    ]));
  });

  it("lets an Admin reorder groups and delete any group after confirming", async () => {
    serve(admin, { canReorder: true, manage: MANAGE.map((g) => ({ ...g, can_edit: true })) });
    renderAt("/dashboards?tab=groups");
    const list = await screen.findByRole("list", { name: "Dashboard groups" });
    expect(within(list).getByRole("button", { name: "Move Delivery up" })).toBeDisabled();
    await userEvent.click(within(list).getByRole("button", { name: "Move Finance up" }));
    await userEvent.click(within(list).getByRole("button", { name: "Delete Finance" }));
    const confirm = await screen.findByRole("dialog", { name: "Delete “Finance”?" });
    await userEvent.click(within(confirm).getByRole("button", { name: "Delete group" }));
    await waitFor(() => expect(writes()).toEqual([
      { url: "/api/v1/dashboard-groups/order", method: "PUT", body: { group_ids: ["g2", "g1"] } },
      { url: "/api/v1/dashboard-groups/g2", method: "DELETE", body: undefined },
    ]));
  });

  it("adds a dashboard to groups from its row", async () => {
    serve(creator);
    renderAt("/dashboards");
    await userEvent.click(await screen.findByRole("button", { name: "Add Gulf delivery to a group" }));
    const dialog = await screen.findByRole("dialog", { name: "Add “Gulf delivery” to groups" });
    const delivery = await within(dialog).findByRole("checkbox", { name: "Delivery" });
    expect(delivery).toBeChecked();
    expect(within(dialog).queryByRole("checkbox", { name: "Finance" })).not.toBeInTheDocument(); // not hers to change
    await userEvent.click(delivery);
    await userEvent.click(within(dialog).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(writes()).toEqual([{ url: "/api/v1/dashboards/d2/groups", method: "PUT", body: { group_ids: [] } }]));
  });
});
