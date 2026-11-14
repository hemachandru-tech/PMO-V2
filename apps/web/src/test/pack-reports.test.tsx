import { QueryClient } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Blueprint, PackReports } from "../api/packReports";
import { Providers } from "../app/App";
import { TestAuthProvider } from "../app/auth";
import { setConfigForTests } from "../app/config";
import { ReportsTab } from "../features/admin/packs/reports/ReportsTab";
import { SaveAsBlueprint } from "../features/admin/packs/reports/SaveFromDialog";

const overview: Blueprint = {
  id: "projects_overview", tab: "Overview", title: "Projects overview", dashboard: "projects",
  question: "How is the open portfolio doing?", population: "Open projects.", triggers: ["projects overview"],
  kpis: ["open_projects", "in_delivery"],
  widgets: [{ title: "Open by region", measure: "open_projects", breakdowns: ["region"], visual: "ranked_bar" }],
  table: { title: "Open projects", measure: "open_projects", columns: ["name", "region"] },
  footnote: "Open = not closed.",
};
const aging: Blueprint = { ...overview, id: "on_hold_aging", title: "On hold aging", tab: "On hold", kpis: ["missing_measure"] };

const reports = (over: Partial<PackReports> = {}): PackReports => ({
  pack: { id: "p1", key: "acme_pmo", version: "0.1.0", title: "Acme PMO", status: "draft", system: false, read_only: false },
  blueprints: [overview, aging], tones: {},
  dashboards: [{ key: "projects", title: "PMO — Projects", reports: ["projects_overview", "on_hold_aging"], filters: ["region"] }],
  problems: { on_hold_aging: [{ path: "kpis.0", message: "the pack has no measure missing_measure" }] },
  names: {
    measures: [{ key: "open_projects", label: "Open projects", entity: "projects" }, { key: "in_delivery", label: "In delivery", entity: "projects" }],
    dimensions: [{ key: "region", label: "Region", entity: "projects" }, { key: "cio", label: "CIO", entity: "projects" }],
    fields: { projects: ["name", "region", "cio"] },
  },
  etag: '"e1"', ...over,
});

const calls: { url: string; method: string; body: unknown; ifMatch: string | null }[] = [];

function json(body: unknown, status = 200, headers: Record<string, string> = {}) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json", ...headers } });
}

function serve(handler: (url: string, init?: RequestInit) => Response | undefined) {
  calls.length = 0;
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    const headers = new Headers(init?.headers);
    calls.push({ url, method: init?.method ?? "GET", body: init?.body ? JSON.parse(String(init.body)) : undefined, ifMatch: headers.get("If-Match") });
    if (url.endsWith("/api/v1/connections")) return json({ items: [{ id: "c1", name: "TPG" }] });
    return handler(url, init) ?? json({ error: { code: "not_found", message: "Not found" } }, 404);
  }));
}

function renderTab(ui = <ReportsTab packId="p1" />) {
  return render(
    <TestAuthProvider token="tok">
      <Providers client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        {ui}
      </Providers>
    </TestAuthProvider>,
  );
}

beforeEach(() => setConfigForTests({ apiBaseUrl: "http://api.test", oidcAuthority: "http://kc", oidcClientId: "tela-web" }));
afterEach(() => vi.unstubAllGlobals());

describe("Pack Studio › Reports & dashboards (US-234)", () => {
  it("lists blueprints with their checks, edits one, previews it and saves with If-Match", async () => {
    serve((url, init) => {
      if (url.endsWith("/api/v1/packs/p1/reports")) return json(reports(), 200, { ETag: '"e1"' });
      if (url.endsWith("/reports/preview")) {
        return json({
          problems: [], connection: { id: "c1", name: "TPG" }, model_version: 3, gaps: [{ request_part: "Open projects", reason: "Not in this model" }],
          sections: [{ key: "headline", title: "Headline figures", kind: "headline", widgets: [{ ref: "w1", title: "Open projects", visual: "figure", measure_label: "Open projects", spec: {} }] }],
          layout_preview: [{ widget_ref: "w1", section: "headline", x: 0, y: 0, w: 3, h: 2 }], results: { w1: { rows: [[431]], row_count: 1 } },
        });
      }
      if (url.endsWith("/reports/blueprints/projects_overview") && init?.method === "PUT") return json(reports({ etag: '"e2"' }));
      return undefined;
    });
    renderTab();
    const table = await screen.findByRole("table", { name: "Report blueprints" });
    expect(within(within(table).getByRole("row", { name: /On hold aging/ })).getByText("1 problem")).toBeInTheDocument();
    expect(within(within(table).getByRole("row", { name: /Projects overview/ })).getByText("2 figures · 1 visual · table")).toBeInTheDocument();

    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Edit Projects overview" }));
    const form = await screen.findByRole("form", { name: "Blueprint" });
    await user.clear(within(form).getByLabelText("Question it answers"));
    await user.type(within(form).getByLabelText("Question it answers"), "What needs attention first?");
    await user.click(within(form).getByRole("button", { name: "Remove figure 2" }));
    await user.selectOptions(within(form).getByLabelText("Visual 1 chart"), "donut");

    await user.selectOptions(within(form).getByLabelText("Preview connection"), "c1");
    await user.click(within(form).getByRole("button", { name: /Preview/ }));
    const preview = await within(form).findByRole("region", { name: "Live preview" });
    expect(within(preview).getByText(/431/)).toBeInTheDocument();
    expect(within(preview).getByText("Open projects: Not in this model")).toBeInTheDocument();
    const sent = calls.find((c) => c.url.endsWith("/reports/preview"))!.body as { blueprint: Blueprint; connection_id: string };
    expect(sent.connection_id).toBe("c1");
    expect(sent.blueprint.kpis).toEqual(["open_projects"]);

    await user.click(within(form).getByRole("button", { name: "Save blueprint" }));
    await waitFor(() => expect(calls.some((c) => c.method === "PUT")).toBe(true));
    const put = calls.find((c) => c.method === "PUT")!;
    expect(put.ifMatch).toBe('"e1"');
    const saved = (put.body as { blueprint: Blueprint }).blueprint;
    expect(saved.question).toBe("What needs attention first?");
    expect(saved.widgets![0]!.visual).toBe("donut");
    expect(saved.table).toEqual(overview.table);
  });

  it("shows the API's problems with their paths when a save is refused", async () => {
    serve((url, init) => {
      if (url.endsWith("/api/v1/packs/p1/reports")) return json(reports());
      if (init?.method === "PUT") {
        return json({ error: { code: "validation_error", message: "The blueprint has problems: x", details: { problems: [{ path: "table.columns", message: "projects has no field budget" }] } } }, 400);
      }
      return undefined;
    });
    renderTab();
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Edit Projects overview" }));
    await user.click(await screen.findByRole("button", { name: "Save blueprint" }));
    const list = await screen.findByRole("list", { name: "Problems" });
    expect(within(list).getByText("projects has no field budget")).toBeInTheDocument();
    expect(within(list).getByText("table.columns")).toBeInTheDocument();
  });

  it("reorders a dashboard's tabs and adds a shared filter", async () => {
    serve((url, init) => {
      if (url.endsWith("/api/v1/packs/p1/reports")) return json(reports());
      if (url.endsWith("/reports/dashboards/projects") && init?.method === "PUT") return json(reports({ etag: '"e3"' }));
      return undefined;
    });
    renderTab();
    const user = userEvent.setup();
    const card = await screen.findByRole("article", { name: "Dashboard PMO — Projects" });
    await user.click(within(card).getByRole("button", { name: "Move On hold aging up" }));
    await user.selectOptions(within(card).getByLabelText("Add a shared filter to PMO — Projects"), "cio");
    await user.click(within(card).getByRole("button", { name: "Save dashboard" }));
    await waitFor(() => expect(calls.some((c) => c.method === "PUT")).toBe(true));
    const put = calls.find((c) => c.method === "PUT")!;
    expect(put.ifMatch).toBe('"e1"');
    expect(put.body).toEqual({ title: "PMO — Projects", reports: ["on_hold_aging", "projects_overview"], filters: ["region", "cio"] });
  });

  it("saves a report as a blueprint and names the widgets left out", async () => {
    serve((url, init) => {
      if (url.endsWith("/api/v1/packs/p1/reports")) return json(reports());
      if (url.endsWith("/api/v1/reports")) return json({ items: [{ id: "r1", title: "Regional delivery", updated_at: "", widgets: 4, published: false, mine: true }] });
      if (url.endsWith("/reports/from-report")) {
        const body = JSON.parse(String(init?.body)) as { save: boolean };
        const out = { blueprint: { ...overview, id: "regional_delivery", title: "Regional delivery" }, tones: {}, problems: [],
          left_out: [{ title: "My SQL", reason: "custom SQL" }], saved: body.save };
        return json(body.save ? { ...out, reports: reports({ etag: '"e4"' }) } : out);
      }
      return undefined;
    });
    renderTab();
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: /From a report/ }));
    const dialog = await screen.findByRole("dialog");
    await user.selectOptions(within(dialog).getByLabelText("Report"), "r1");
    const left = await within(dialog).findByRole("list", { name: "Left out" });
    expect(within(left).getByText("My SQL: custom SQL")).toBeInTheDocument();
    expect(within(dialog).getByText(/Regional delivery: 2 figures, 1 visual, a detail table/)).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "Save to the pack" }));
    await waitFor(() => expect(calls.some((c) => c.url.endsWith("/from-report") && (c.body as { save: boolean }).save)).toBe(true));
    const saved = calls.find((c) => c.url.endsWith("/from-report") && (c.body as { save: boolean }).save)!;
    expect(saved.ifMatch).toBe('"e1"');
    expect(saved.body).toEqual({ report_id: "r1", dashboard: "projects", save: true });
  });

  it("opens a system pack read-only", async () => {
    serve((url) => (url.endsWith("/api/v1/packs/p1/reports")
      ? json(reports({ pack: { id: "p1", key: "pmo_tpg", version: "2.1.0", title: "PMO", status: "released", system: true, read_only: true } }))
      : undefined));
    renderTab();
    expect(await screen.findByRole("note")).toHaveTextContent("clone it");
    expect(screen.queryByRole("button", { name: /New blueprint/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Delete Projects overview/ })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Open Projects overview" })).toBeInTheDocument();
  });

  it("edits a blueprint as YAML and saves what was typed", async () => {
    serve((url, init) => {
      if (url.endsWith("/api/v1/packs/p1/reports")) return json(reports());
      if (init?.method === "PUT") return json(reports({ etag: '"e5"' }));
      return undefined;
    });
    renderTab();
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Edit Projects overview" }));
    const form = await screen.findByRole("form", { name: "Blueprint" });
    await user.click(within(form).getByRole("button", { name: "Source" }));
    const text = within(form).getByRole("textbox", { name: "Blueprint source (YAML)" }) as HTMLTextAreaElement;
    expect(text.value).toContain("id: projects_overview");
    expect(text.value).toContain("  - open_projects");
    await user.clear(text);
    await user.click(text);
    await user.paste("id: projects_overview\ntitle: Projects overview\ndashboard: projects\nkpis: [open_projects]\n");
    await user.click(within(form).getByRole("button", { name: "Save blueprint" }));
    await waitFor(() => expect(calls.some((c) => c.method === "PUT")).toBe(true));
    expect((calls.find((c) => c.method === "PUT")!.body as { blueprint: Blueprint }).blueprint).toEqual(
      { id: "projects_overview", title: "Projects overview", dashboard: "projects", kpis: ["open_projects"] });
  });

  it("saves a report as a blueprint from the canvas into a chosen pack draft", async () => {
    serve((url, init) => {
      if (url.endsWith("/api/v1/packs")) {
        const v = (id: string, title: string, editable: boolean) => ({ id, pack_key: id, version: "0.1.0", title, status: editable ? "draft" : "released", owner: editable ? "tenant" : "system", editable });
        return json({ items: [
          { pack_key: "pmo_tpg", owner: "system", title: "PMO", latest: v("sys", "PMO", false), versions: [v("sys", "PMO", false)], installed: 1 },
          { pack_key: "acme_pmo", owner: "tenant", title: "Acme PMO", latest: v("p1", "Acme PMO", true), versions: [v("p1", "Acme PMO", true)], installed: 0 },
        ], create: { methods: [], planned: {} } });
      }
      if (url.endsWith("/api/v1/packs/p1/reports")) return json(reports());
      if (url.endsWith("/reports/from-report")) {
        const body = JSON.parse(String(init?.body)) as { save: boolean };
        const out = { blueprint: { ...overview, id: "regional_delivery", title: "Regional delivery" }, tones: {}, problems: [], left_out: [], saved: body.save };
        return json(body.save ? { ...out, reports: reports({ etag: '"e6"' }) } : out);
      }
      return undefined;
    });
    renderTab(<SaveAsBlueprint kind="report" sourceId="r1">{(open) => <button type="button" onClick={open}>Save as blueprint</button>}</SaveAsBlueprint>);
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Save as blueprint" }));
    const dialog = await screen.findByRole("dialog");
    const packs = within(dialog).getByLabelText("Pack draft");
    await waitFor(() => expect(within(packs).getAllByRole("option")).toHaveLength(2));
    expect(within(packs).queryByRole("option", { name: /^PMO/ })).not.toBeInTheDocument();
    expect(within(dialog).queryByLabelText("Report")).not.toBeInTheDocument();
    await user.selectOptions(packs, "p1");
    expect(await within(dialog).findByText(/Regional delivery: 2 figures/)).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "Save to the pack" }));
    await waitFor(() => expect(calls.some((c) => c.url.endsWith("/from-report") && (c.body as { save: boolean }).save)).toBe(true));
    const saved = calls.find((c) => c.url.endsWith("/from-report") && (c.body as { save: boolean }).save)!;
    expect(saved.ifMatch).toBe('"e1"');
    expect(saved.body).toEqual({ report_id: "r1", dashboard: "projects", save: true });
  });
});
