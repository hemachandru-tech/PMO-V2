import { QueryClient } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Me } from "../api/types";
import { AppRoutes, Providers } from "../app/App";
import { TestAuthProvider } from "../app/auth";
import { setConfigForTests } from "../app/config";

vi.mock("echarts/core", () => ({
  use: vi.fn(),
  registerTheme: vi.fn(),
  init: () => ({ setOption: vi.fn(), on: vi.fn(), resize: vi.fn(), dispose: vi.fn() }),
}));

const creator: Me = {
  user_id: "u2", display_name: "Cleo Creator", email: null, tenant_id: "t1", roles: ["creator"], level: "creator", audience_roles: [],
  permissions: ["dashboards.view", "ask.use", "drill.use", "definitions.view", "widgets.create", "reports.create", "dashboards.create", "share.create"],
  scope_summary: [], locale: null, time_zone: null,
};
const admin: Me = { ...creator, user_id: "u1", display_name: "Ada Admin", level: "admin", permissions: [...creator.permissions, "dashboards.publish", "audit.read"] };

type Handler = (url: string, init?: RequestInit) => Response | undefined;
const calls: { url: string; method: string; body: unknown }[] = [];

function serve(me: Me, handler: Handler) {
  calls.length = 0;
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ url, method: init?.method ?? "GET", body: init?.body ? JSON.parse(String(init.body)) : undefined });
    if (url.endsWith("/api/v1/me")) return json(me);
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

beforeEach(() => setConfigForTests({ apiBaseUrl: "http://api.test", oidcAuthority: "http://kc", oidcClientId: "tela-web" }));
afterEach(() => vi.unstubAllGlobals());

const ROLES = [
  { id: "b1", key: "cio", name: "CIO", kind: "audience", members: 6 },
  { id: "b2", key: "viewer", name: "Viewer", kind: "system", members: 41 },
];
const DASH = {
  id: "d1", title: "Gulf delivery", layout: "tabs", status: "draft", owner: { id: "u2", name: "Cleo Creator" }, can_edit: true, can_publish: false,
  shared_filters: [{ ref: "dimension:region" }, { ref: "dimension:programme" }],
  available_filters: [{ ref: "dimension:region", label: "Region" }, { ref: "dimension:programme", label: "Programme" }, { ref: "dimension:priority", label: "Priority" }],
  reach: [
    { ref: "dimension:region", label: "Region", reaches_all: true, widgets: [{ widget_id: "w1", item_id: "i1", title: "Overdue", report: "Delivery", reached: true, excluded: false, message: null }] },
    { ref: "dimension:programme", label: "Programme", reaches_all: false, widgets: [
      { widget_id: "w1", item_id: "i1", title: "Overdue", report: "Delivery", reached: true, excluded: false, message: null },
      { widget_id: "w2", item_id: "i2", title: "Budget by portfolio", report: "Finance", reached: false, excluded: false, message: "Not affected by Programme" }] },
  ],
  reports: [{ id: "r1", title: "Delivery", group_title: null, widgets: 1 }, { id: "r2", title: "Finance", group_title: null, widgets: 1 }],
  connections: [{ id: "c1", name: "TPG" }], live: null, draft_differs_from_live: false, revision: 3, publish_request: null,
};
const VIEW = {
  id: "d1", title: "Gulf delivery", kind: "user", layout: "tabs", version: null, version_id: null, connection_id: "c1", model_version_id: "v1",
  locked_scope_chip: "projects: Own CIO", scope_message: "", as_of: null, filters: [],
  tabs: [{ id: "r1", title: "Delivery", widgets: [] }, { id: "r2", title: "Finance", widgets: [] }],
};
const check = (name: string, label: string, passed: boolean, items: unknown[] = []) => ({ check: name, label, passed, items });

describe("dashboard composer (US-142)", () => {
  it("reorders reports with Alt + arrow keys, excludes an unreached widget and re-renders the preview", async () => {
    let views = 0;
    let state = DASH;
    serve(creator, (url, init) => {
      if (url.endsWith("/api/v1/dashboards/d1") && init?.method === "GET") return json(state);
      if (url.endsWith("/api/v1/dashboards/d1/view")) { views += 1; return json(VIEW); }
      if (url.endsWith("/api/v1/reports")) return json({ items: [{ id: "r3", title: "Risks", mine: true, widgets: 2, published: false, updated_at: "", owner_name: "Cleo" }] });
      if (url.endsWith("/api/v1/dashboards/d1/reports")) {
        const items = JSON.parse(String(init!.body)).items as { report_id: string }[];
        const all = [...DASH.reports, { id: "r3", title: "Risks", group_title: null, widgets: 2 }];
        state = { ...DASH, reports: items.map((i) => all.find((r) => r.id === i.report_id)!) };
        return json(state);
      }
      if (url.endsWith("/api/v1/dashboards/d1") && init?.method === "PATCH") return json(state);
      return undefined;
    });
    renderAt("/dashboards/d1/edit");
    const list = await screen.findByRole("list", { name: "Reports on this dashboard" });
    expect(await screen.findByRole("tab", { name: "Delivery" })).toBeInTheDocument(); // live preview
    expect(views).toBe(1);

    const handle = within(list).getByRole("button", { name: "Move Delivery, position 1" });
    handle.focus();
    fireEvent.keyDown(handle, { key: "ArrowDown", altKey: true });
    await waitFor(() => expect(calls.find((c) => c.url.endsWith("/d1/reports"))?.body).toEqual({ items: [{ report_id: "r2", group_title: null }, { report_id: "r1", group_title: null }] }));
    expect(screen.getByText("Moved Delivery: position 2 of 2.")).toBeInTheDocument();
    await waitFor(() => expect(views).toBe(2));

    expect(screen.getByText("Reaches 1 of 2")).toBeInTheDocument();
    expect(screen.getByText(/“Budget by portfolio” \(Finance\) has no link to Programme/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Exclude widget" }));
    await waitFor(() => expect(calls.find((c) => c.method === "PATCH")?.body).toEqual({
      shared_filters: [{ ref: "dimension:region" }, { ref: "dimension:programme", exclude_widget_ids: ["w2"] }],
    }));

    await userEvent.click(screen.getByRole("button", { name: "Add a report" }));
    await userEvent.click(await screen.findByRole("option", { name: "Risks" }));
    await waitFor(() => expect(calls.filter((c) => c.url.endsWith("/d1/reports")).at(-1)?.body).toEqual({
      items: [{ report_id: "r2", group_title: null }, { report_id: "r1", group_title: null }, { report_id: "r3" }],
    }));
    expect(screen.getByRole("link", { name: /Request publish/ })).toHaveAttribute("href", "/dashboards/d1/publish");
  });

  it("hides and unhides reports in order and filters the composer preview", async () => {
    sessionStorage.clear();
    serve(creator, (url) => {
      if (url.endsWith("/api/v1/dashboards/d1")) return json(DASH);
      if (url.endsWith("/api/v1/dashboards/d1/view")) return json(VIEW);
      if (url.endsWith("/api/v1/reports")) return json({ items: [] });
      return undefined;
    });
    renderAt("/dashboards/d1/edit");
    const list = await screen.findByRole("list", { name: "Reports on this dashboard" });
    expect(await screen.findByRole("tab", { name: "Delivery" })).toBeInTheDocument();

    const hideBtn = within(list).getByRole("button", { name: "Hide Delivery" });
    await userEvent.click(hideBtn);

    // "Hidden" badge and "1 hidden" header badge appear
    expect(within(list).getByText("Hidden")).toBeInTheDocument();
    expect(screen.getByText("1 hidden")).toBeInTheDocument();
    // Delivery tab should no longer be in the preview tabs
    expect(screen.queryByRole("tab", { name: "Delivery" })).not.toBeInTheDocument();

    // Clicking unhide all restores it
    await userEvent.click(screen.getByRole("button", { name: "Unhide all" }));
    expect(screen.queryByText("1 hidden")).not.toBeInTheDocument();
    expect(await screen.findByRole("tab", { name: "Delivery" })).toBeInTheDocument();
  });

  it("tells an Admin about an open request and a Creator why theirs came back", async () => {
    const request = { id: "pr1", dashboard: { id: "d1", title: "Gulf delivery" }, requested_by: { id: "u2", name: "Cleo Creator" }, roles: [ROLES[0]],
      note: "October review", status: "open", created_at: new Date().toISOString(), decided_by: null, decided_at: null, decision_note: null, version: null };
    serve(admin, (url) => {
      if (url.endsWith("/api/v1/dashboards/d1")) return json({ ...DASH, can_edit: false, can_publish: true, publish_request: request });
      if (url.endsWith("/view")) return json(VIEW);
      if (url.endsWith("/api/v1/reports")) return json({ items: [] });
      return undefined;
    });
    const { unmount } = renderAt("/dashboards/d1/edit");
    expect(await screen.findByText(/asked .* to publish this to CIO/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Review the request" })).toHaveAttribute("href", "/dashboards/d1/publish?request=pr1");
    unmount();

    serve(creator, (url) => {
      if (url.endsWith("/api/v1/dashboards/d1")) return json({ ...DASH, publish_request: { ...request, status: "declined", decided_by: "Ada Admin", decided_at: new Date().toISOString(), decision_note: "Remove the personal field first." } });
      if (url.endsWith("/view")) return json(VIEW);
      if (url.endsWith("/api/v1/reports")) return json({ items: [] });
      return undefined;
    });
    renderAt("/dashboards/d1/edit");
    expect(await screen.findByText(/Ada Admin returned this/)).toBeInTheDocument();
    expect(screen.getByText(/Remove the personal field first/)).toBeInTheDocument();
  });
});

describe("publish steps (US-142)", () => {
  it("walks an Admin through audience, preview, checks with a fix, and the release note, then shows the new version", async () => {
    let fixed = false;
    const checks = () => ({
      passed: fixed, notes: [], roles: [ROLES[0]],
      checks: [
        check("all_widgets_certified", "Every widget is certified", true),
        check("returns_data_for_each_role", "Every widget returns data for each role", fixed, fixed ? [] : [
          { widget_id: "w1", item_id: "i1", title: "Stale projects", report: "Delivery", report_id: "r1", role: "cio", message: "Returns no rows for CIO",
            fixes: [{ action: "mark_empty_expected" }, { action: "remove_widget" }] }]),
        check("publish_permission", "You can publish to roles", true),
      ],
    });
    const published = { version: 1, notified: 6, change_summary: "First version", roles: [ROLES[0]], request_id: null };
    serve(admin, (url, init) => {
      if (url.endsWith("/api/v1/dashboards/d1")) return json({ ...DASH, can_publish: true, can_edit: false });
      if (url.endsWith("/api/v1/roles")) return json({ items: ROLES });
      if (url.endsWith("/preview")) {
        return json({ as: { role: "cio", name: "CIO" }, scope_text: "Their own projects (depends on each person)", rows_in_scope: null, counts_note: null,
          widgets: [{ widget_id: "w1", item_id: "i1", title: "Overdue", shown: true, reason: null }, { widget_id: "w2", item_id: "i2", title: "Budget", shown: false, reason: "Uses fields hidden for CIO" }] });
      }
      if (url.endsWith("/prechecks")) return json(checks());
      if (url.endsWith("/reports/r1/items/i1") && init?.method === "PATCH") { fixed = true; return json({ id: "r1" }); }
      if (url.endsWith("/d1/publish")) return json(published, 201);
      if (url.endsWith("/d1/versions")) {
        return json({ can_restore: true, items: [{ id: "v1", version: 1, state: "live", note: "October review", change_summary: "First version", published_at: "2026-10-21T07:00:00Z", published_by: "Ada Admin", restored_from: null, requested_by: null, roles: ["CIO"] }] });
      }
      return undefined;
    });
    renderAt("/dashboards/d1/publish");
    const steps = await screen.findByRole("navigation", { name: "Publish steps" });
    expect(within(steps).getByRole("button", { name: /Audience/ })).toHaveAttribute("aria-current", "step");
    await userEvent.click(screen.getByRole("button", { name: "Next: preview" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Choose at least one role.");
    await userEvent.click(screen.getByRole("checkbox", { name: "CIO" }));
    expect(screen.getByText("1 role · about 6 people")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Next: preview" }));

    expect(await screen.findByText("Their own projects (depends on each person)")).toBeInTheDocument();
    expect(screen.getByText("1 of 2 · 1 hidden or empty")).toBeInTheDocument();
    expect(window.location.search).toContain("roles=cio");
    await userEvent.click(screen.getByRole("button", { name: "Next: checks" }));

    expect(await screen.findByText("1 of 3 needs a fix")).toBeInTheDocument();
    const next = screen.getByRole("button", { name: "Next: release note" });
    expect(next).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: "Mark empty as expected" }));
    expect(await screen.findByText("All 3 checks pass")).toBeInTheDocument();
    await waitFor(() => expect(next).toBeEnabled());
    await userEvent.click(next);

    await userEvent.type(await screen.findByRole("textbox", { name: "Release note" }), "October review");
    await userEvent.click(screen.getByRole("button", { name: "Publish to 1 role" }));
    expect(await screen.findByText(/Published version 1 to CIO\. 6 people were notified\./)).toBeInTheDocument();
    expect(calls.find((c) => c.url.endsWith("/d1/publish"))!.body).toEqual({ target_roles: ["cio"], note: "October review" });
    expect(screen.getByRole("heading", { name: "Version 1" })).toBeInTheDocument();
  });

  it("lets a Creator send a request although only an Admin can publish", async () => {
    serve(creator, (url) => {
      if (url.endsWith("/api/v1/dashboards/d1")) return json({ ...DASH, live: { version_id: "v1", version: 1, published_at: "", published_by: "Ada", roles: [ROLES[0]] } });
      if (url.endsWith("/api/v1/roles")) return json({ items: ROLES });
      if (url.endsWith("/view")) return json(VIEW);
      if (url.endsWith("/api/v1/reports")) return json({ items: [] });
      if (url.endsWith("/prechecks")) {
        return json({ passed: false, notes: [], roles: [ROLES[0]], checks: [check("all_widgets_certified", "Every widget is certified", true),
          check("publish_permission", "You can publish to roles", false, [{ message: "Only an Admin can publish to roles.", fixes: [{ action: "request_publish" }] }])] });
      }
      if (url.endsWith("/request-publish")) return json({ requested: true, request_id: "pr1", notified: 1, certification_requests: 0 }, 202);
      return undefined;
    });
    renderAt("/dashboards/d1/publish?step=checks");
    expect(await screen.findByText(/All 2 checks pass that you can fix/)).toBeInTheDocument();
    expect(screen.getByText("Admin step")).toBeInTheDocument();
    expect(new URLSearchParams(window.location.search).get("roles")).toBe("cio"); // from the live version
    await userEvent.click(screen.getByRole("button", { name: "Next: release note" }));
    await userEvent.click(await screen.findByRole("button", { name: "Send request" }));
    await waitFor(() => expect(calls.find((c) => c.url.endsWith("/request-publish"))?.body).toEqual({ target_roles: ["cio"], note: null }));
    expect(await screen.findByText("Your request was sent to the Admins.")).toBeInTheDocument();
  });

  it("returns a request to its Creator with a note", async () => {
    const request = { id: "pr1", dashboard: { id: "d1", title: "Gulf delivery" }, requested_by: { id: "u2", name: "Cleo Creator" }, roles: [ROLES[0]],
      note: "October review", status: "open", created_at: new Date().toISOString(), decided_by: null, decided_at: null, decision_note: null, version: null };
    serve(admin, (url) => {
      if (url.endsWith("/api/v1/dashboards/d1")) return json({ ...DASH, can_publish: true, can_edit: false, publish_request: request });
      if (url.endsWith("/api/v1/roles")) return json({ items: ROLES });
      if (url.endsWith("/prechecks")) return json({ passed: true, notes: [], roles: [ROLES[0]], checks: [check("all_widgets_certified", "Every widget is certified", true)] });
      if (url.endsWith("/publish-requests/pr1/decline")) return json({ ...request, status: "declined" });
      if (url.includes("/api/v1/publish-requests")) return json({ items: [] });
      return undefined;
    });
    renderAt("/dashboards/d1/publish?request=pr1");
    expect(await screen.findByText(/Requested by Cleo Creator/)).toBeInTheDocument();
    expect(screen.getByText("“October review”")).toBeInTheDocument();
    expect(new URLSearchParams(window.location.search).get("roles")).toBe("cio"); // from the request
    await userEvent.click(await screen.findByRole("button", { name: "Return to Cleo Creator with a note" }));
    const dialog = await screen.findByRole("dialog");
    await userEvent.click(within(dialog).getByRole("button", { name: "Return with note" }));
    expect(await within(dialog).findByText("Enter a note for the requester.")).toBeInTheDocument();
    await userEvent.type(within(dialog).getByRole("textbox", { name: "Note for the requester" }), "Remove the personal field first.");
    await userEvent.click(within(dialog).getByRole("button", { name: "Return with note" }));
    expect(await screen.findByRole("heading", { name: "Publish requests" })).toBeInTheDocument();
    expect(calls.find((c) => c.url.endsWith("/decline"))!.body).toEqual({ note: "Remove the personal field first." });
  });
});

describe("version history (US-142)", () => {
  it("restores an earlier version after confirming, and shows why when the checks fail", async () => {
    let attempts = 0;
    serve(admin, (url) => {
      if (url.endsWith("/api/v1/dashboards/d1")) return json({ ...DASH, can_publish: true });
      if (url.endsWith("/d1/versions")) {
        return json({ can_restore: true, items: [
          { id: "v2", version: 2, state: "live", note: null, change_summary: "-1 widgets", published_at: "2026-10-21T07:00:00Z", published_by: "Ada Admin", restored_from: null, requested_by: "Cleo Creator", roles: ["CIO"] },
          { id: "v1", version: 1, state: "superseded", note: "Pilot", change_summary: "First version", published_at: "2026-10-01T07:00:00Z", published_by: "Ada Admin", restored_from: null, requested_by: null, roles: ["CIO"] }] });
      }
      if (url.endsWith("/versions/1/restore")) {
        attempts += 1;
        if (attempts === 1) {
          return json({ error: { code: "precheck_failed", message: "Version 1 can't be restored until every check passes.", details: { passed: false, notes: [], roles: [],
            checks: [check("returns_data_for_each_role", "Every widget returns data for each role", false, [{ title: "Overdue", message: "Returns no rows for CIO", fixes: [] }])] } } }, 409);
        }
        return json({ version: 3, notified: 6, change_summary: "", roles: [], request_id: null, notes: [] }, 201);
      }
      return undefined;
    });
    renderAt("/dashboards/d1/versions");
    expect(await screen.findByText(/requested by Cleo Creator/)).toBeInTheDocument();
    expect(screen.getByText("“Pilot”")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Restore" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText(/publishes version 3 with version 1's content to CIO/)).toBeInTheDocument();
    await userEvent.click(within(dialog).getByRole("button", { name: "Restore as version 3" }));
    expect(await within(dialog).findByText(/“Overdue”: Returns no rows for CIO/)).toBeInTheDocument();
    await userEvent.click(within(dialog).getByRole("button", { name: "Restore as version 3" }));
    expect(await screen.findByText("Restored version 1 as version 3.")).toBeInTheDocument();
  });

  it("asks before unpublishing", async () => {
    serve(admin, (url, init) => {
      if (url.endsWith("/api/v1/dashboards/d1")) return json({ ...DASH, can_publish: true });
      if (url.endsWith("/d1/versions")) return json({ can_restore: true, items: [{ id: "v1", version: 1, state: "live", note: null, change_summary: null, published_at: "2026-10-21T07:00:00Z", published_by: "Ada Admin", restored_from: null, requested_by: null, roles: ["CIO"] }] });
      if (url.endsWith("/d1/unpublish") && init?.method === "POST") return json({ withdrawn: true });
      return undefined;
    });
    renderAt("/dashboards/d1/versions");
    await userEvent.click(await screen.findByRole("button", { name: "Unpublish" }));
    const dialog = await screen.findByRole("dialog", { name: "Unpublish this dashboard?" });
    expect(calls.some((c) => c.url.endsWith("/unpublish"))).toBe(false);
    await userEvent.click(within(dialog).getByRole("button", { name: "Unpublish" }));
    expect(await screen.findByText(/Unpublished\. Readers no longer see this dashboard\./)).toBeInTheDocument();
  });
});

describe("publish requests (US-142)", () => {
  it("lists waiting requests for Admins and opens the publish steps", async () => {
    serve(admin, (url) => {
      if (url.endsWith("/api/v1/publish-requests?status=open")) {
        return json({ items: [{ id: "pr1", dashboard: { id: "d1", title: "Gulf delivery" }, requested_by: { id: "u2", name: "Cleo Creator" }, roles: [ROLES[0]],
          note: "October review", status: "open", created_at: new Date().toISOString(), decided_by: null, decided_at: null, decision_note: null, version: null }] });
      }
      return undefined;
    });
    renderAt("/govern/publish-requests");
    const link = await screen.findByRole("link", { name: "Gulf delivery" });
    expect(link).toHaveAttribute("href", "/dashboards/d1/publish?request=pr1");
    expect(screen.getByText("“October review”")).toBeInTheDocument();
    expect(within(screen.getByRole("navigation", { name: "Main" })).getByRole("link", { name: "Publish requests" })).toHaveAttribute("aria-current", "page");
  });
});

describe("share dialog (US-142)", () => {
  it("warns per person before sharing, then shares with everyone picked", async () => {
    serve(creator, (url, init) => {
      if (url.endsWith("/api/v1/dashboards/d1")) return json(DASH);
      if (url.endsWith("/view")) return json(VIEW);
      if (url.endsWith("/api/v1/reports")) return json({ items: [] });
      if (url.includes("/api/v1/shares?object_type=dashboard")) return json({ items: [{ id: "s1", object: { kind: "dashboard", id: "d1" }, user: { id: "u9", name: "Sam Shared" }, permission: "view", created_at: "2026-10-18T07:00:00Z" }] });
      if (url.includes("/api/v1/directory/users")) return json({ items: [{ id: "u9", name: "Sam Shared" }, { id: "u3", name: "Priya Nair" }, { id: "u4", name: "Leena Thomas" }] });
      if (url.endsWith("/api/v1/shares/check")) {
        const ids = JSON.parse(String(init!.body)).with_user_ids as string[];
        return json({ warnings: ids.includes("u3") ? [{ user_id: "u3", name: "Priya Nair", connection: "Requests DB", message: "" }] : [] });
      }
      if (url.endsWith("/api/v1/shares") && init?.method === "POST") return json({ shared_with: 2, already: 0, warnings: [], items: [] }, 201);
      return undefined;
    });
    renderAt("/dashboards/d1/edit");
    await userEvent.click(await screen.findByRole("button", { name: "Share" }));
    const dialog = await screen.findByRole("dialog");
    expect(await within(dialog).findByText("Sam Shared")).toBeInTheDocument();
    await userEvent.click(within(dialog).getByRole("combobox", { name: "People" }));
    expect(await within(dialog).findByRole("option", { name: /Priya Nair/ })).toBeInTheDocument();
    expect(within(dialog).queryByRole("option", { name: /Sam Shared/ })).not.toBeInTheDocument(); // already shared
    await userEvent.click(within(dialog).getByRole("option", { name: /Priya Nair/ }));
    expect(await within(dialog).findByText(/Priya Nair cannot use Requests DB/)).toBeInTheDocument();
    await userEvent.click(within(dialog).getByRole("option", { name: /Leena Thomas/ }));
    await userEvent.click(within(dialog).getByRole("button", { name: "Share with 2 people" }));
    await waitFor(() => expect(calls.find((c) => c.url.endsWith("/api/v1/shares") && c.method === "POST")?.body).toEqual({
      object: { kind: "dashboard", id: "d1" }, with_user_ids: ["u3", "u4"], permission: "view",
    }));
    expect(await screen.findByText(/Shared with 2 people/)).toBeInTheDocument();
  });
});
