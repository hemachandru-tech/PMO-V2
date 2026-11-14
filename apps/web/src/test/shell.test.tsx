import { QueryClient } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Me } from "../api/types";
import { AppRoutes, Providers } from "../app/App";
import { TestAuthProvider } from "../app/auth";
import { setConfigForTests } from "../app/config";
import { breadcrumbs, visibleNav } from "../app/shell/nav";

vi.mock("echarts/core", () => ({
  use: vi.fn(),
  registerTheme: vi.fn(),
  init: () => ({ setOption: vi.fn(), on: vi.fn(), resize: vi.fn(), dispose: vi.fn() }),
}));

const viewer: Me = {
  user_id: "u1", display_name: "Nomsa Osei", email: null, tenant_id: "t1", roles: ["viewer"], level: "viewer", audience_roles: ["cio"],
  permissions: ["dashboards.view", "ask.use", "drill.use", "definitions.view"], scope_summary: [], locale: null, time_zone: null,
};
const creator: Me = { ...viewer, display_name: "Cleo Creator", level: "creator", audience_roles: [], permissions: [...viewer.permissions, "reports.create", "dashboards.create"] };
const admin: Me = {
  ...creator, display_name: "Ada Admin", level: "admin",
  permissions: [...creator.permissions, "dashboards.publish", "connections.manage", "mappings.manage", "policies.manage", "audit.read"],
};

const HOME = {
  greeting: { name: "Nomsa", scopes: [], as_of: null },
  shelves: {
    starter: [{ id: "d1", type: "dashboard", title: "PMO — Projects", badges: [], version: 1, count: {}, last_edited_at: "" }],
    published_to_my_role: [], shared_with_me: [{ id: "d2", type: "dashboard", title: "Gulf delivery", badges: [], version: null, count: {}, last_edited_at: "" }],
  },
  empty_text: {}, can_create: false,
};

function serve(me: Me) {
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    const json = (body: unknown) => new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
    if (url.endsWith("/api/v1/me")) return json(me);
    if (url.endsWith("/api/v1/home")) return json(HOME);
    if (url.includes("/api/v1/notifications")) {
      return json({ items: [{ id: "n1", kind: "share", title: "Cleo shared Gulf delivery", body: "View only.", link: "/dashboards/d2/draft", read_at: null, created_at: "" }], unread_count: 1 });
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

beforeEach(() => setConfigForTests({ apiBaseUrl: "http://api.test", oidcAuthority: "http://kc", oidcClientId: "tela-web" }));
afterEach(() => vi.unstubAllGlobals());

const groups = (nav: HTMLElement) => within(nav).getAllByRole("group").map((g) => g.getAttribute("aria-labelledby")!.replace("nav-", ""));

describe("navigation by role", () => {
  it("shows Explore to viewers, adds Build for creators and Administer for admins", () => {
    expect(visibleNav(viewer).map((i) => i.label)).toEqual(["Home", "Ask", "Shared with me"]);
    expect(visibleNav(creator).map((i) => i.label)).toEqual(["Home", "Ask", "Shared with me", "Reports", "Dashboards"]);
    expect(visibleNav(admin).map((i) => `${i.group}/${i.label}`)).toEqual([
      "Explore/Home", "Explore/Ask", "Explore/Shared with me", "Build/Reports", "Build/Dashboards",
      "Govern/Publish requests", "Administer/Overview", "Administer/Data", "Administer/Access", "Administer/Audit log",
    ]);
  });

  it("names the page in breadcrumbs", () => {
    expect(breadcrumbs(visibleNav(admin), "/admin/models/m1")).toEqual([{ label: "Administer" }, { label: "Data", to: "/admin/connections" }, { label: "Model studio" }]);
    expect(breadcrumbs(visibleNav(admin), "/")).toEqual([{ label: "Explore" }, { label: "Home" }]);
    expect(breadcrumbs(visibleNav(viewer), "/dashboards/d1")).toEqual([{ label: "Explore" }, { label: "Dashboard" }]);
    expect(breadcrumbs(visibleNav(admin), "/dashboards/d1/publish")).toEqual([{ label: "Build" }, { label: "Dashboards", to: "/dashboards" }, { label: "Publish" }]);
  });

  it("renders the sidebar groups for the signed-in role and marks the current page", async () => {
    serve(admin);
    renderAt("/admin/access/scope");
    const nav = await screen.findByRole("navigation", { name: "Main" });
    expect(groups(nav)).toEqual(["Explore", "Build", "Govern", "Administer"]);
    expect(within(nav).getByRole("link", { name: "Access" })).toHaveAttribute("aria-current", "page");
    const crumbs = screen.getByRole("navigation", { name: "Breadcrumb" });
    expect(crumbs).toHaveTextContent("Administer/Access/Scope policies");
    await waitFor(() => expect(document.title).toBe("Scope policies · Tela"));
  });

  it("gives viewers Explore only", async () => {
    serve(viewer);
    renderAt("/");
    const nav = await screen.findByRole("navigation", { name: "Main" });
    expect(groups(nav)).toEqual(["Explore"]);
    expect(within(nav).getByText("Viewer · cio")).toBeInTheDocument();
  });
});

describe("shell", () => {
  it("starts with a skip link to the main content", async () => {
    serve(viewer);
    renderAt("/");
    await screen.findByRole("navigation", { name: "Main" });
    await userEvent.tab();
    const skip = screen.getByRole("link", { name: "Skip to content" });
    expect(skip).toHaveFocus();
    expect(skip).toHaveAttribute("href", "#main");
    expect(document.getElementById("main")).toHaveAttribute("tabindex", "-1");
  });

  it("collapses the sidebar to icons", async () => {
    serve(creator);
    renderAt("/");
    await userEvent.click(await screen.findByRole("button", { name: "Collapse sidebar" }));
    const nav = screen.getByRole("navigation", { name: "Main" });
    expect(within(nav).getByRole("link", { name: "Reports" })).toBeInTheDocument(); // label kept for screen readers
    expect(screen.getByRole("button", { name: "Expand sidebar" })).toHaveAttribute("aria-expanded", "false");
  });

  it("opens notifications with the unread count", async () => {
    serve(viewer);
    renderAt("/");
    await userEvent.click(await screen.findByRole("button", { name: "Notifications, 1 unread" }));
    const panel = await screen.findByRole("dialog", { name: "Notifications" });
    expect(within(panel).getByRole("button", { name: /Cleo shared Gulf delivery/ })).toBeInTheDocument();
  });

  it("shows name, level, theme and sign out in the account menu", async () => {
    serve(admin);
    renderAt("/");
    await userEvent.click(await screen.findByRole("button", { name: "Account menu" }));
    const menu = await screen.findByRole("menu");
    expect(within(menu).getByText("Ada Admin")).toBeInTheDocument();
    expect(within(menu).getByText("Admin")).toBeInTheDocument();
    expect(within(menu).getByRole("menuitemradio", { name: "Match my system" })).toBeChecked();
    expect(within(menu).getByRole("menuitem", { name: "Sign out" })).toBeInTheDocument();
  });
});

describe("command palette", () => {
  it("opens with Ctrl+K, goes to screens and content, or asks", async () => {
    serve(creator);
    renderAt("/");
    await screen.findByRole("navigation", { name: "Main" });
    await userEvent.keyboard("{Control>}k{/Control}");
    const dialog = await screen.findByRole("dialog", { name: "Command palette" });
    expect(within(dialog).getByRole("option", { name: /Reports/ })).toBeInTheDocument();
    expect(await within(dialog).findByRole("option", { name: /Gulf delivery/ })).toBeInTheDocument();

    await userEvent.type(within(dialog).getByRole("combobox"), "gulf");
    await waitFor(() => expect(within(dialog).queryByRole("option", { name: /^Reports/ })).not.toBeInTheDocument());
    expect(within(dialog).getByRole("option", { name: /Gulf delivery/ })).toBeInTheDocument();
    await userEvent.clear(within(dialog).getByRole("combobox"));
    await userEvent.type(within(dialog).getByRole("combobox"), "reports");
    // A matching screen comes before Ask, so Enter opens it.
    expect(await within(dialog).findByRole("option", { name: /^Reports/ })).toHaveAttribute("aria-selected", "true");
    expect(within(dialog).getByRole("option", { name: "Ask “reports”" })).toBeInTheDocument();
    await userEvent.keyboard("{Enter}");
    await waitFor(() => expect(window.location.pathname).toBe("/reports"));

    await userEvent.keyboard("{Control>}k{/Control}");
    const again = await screen.findByRole("dialog", { name: "Command palette" });
    await userEvent.type(within(again).getByRole("combobox"), "overdue go-lives");
    expect(within(again).getByRole("option", { name: "Ask “overdue go-lives”" })).toHaveAttribute("aria-selected", "true");
    await userEvent.keyboard("{Enter}");
    // Ask takes the question from the URL and starts it.
    await waitFor(() => expect(window.location.pathname).toBe("/ask"));
    const fetchMock = vi.mocked(fetch);
    await waitFor(() => expect(fetchMock.mock.calls.some(([url, init]) => String(url).endsWith("/api/v1/ask/turns") && String(init?.body).includes("overdue go-lives"))).toBe(true));
  });
});
