import { QueryClient } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { PackDetail, PackList, PackMeasure, PackVersion } from "../api/packs";
import type { Me } from "../api/types";
import { AppRoutes, Providers } from "../app/App";
import { TestAuthProvider } from "../app/auth";
import { setConfigForTests } from "../app/config";
import { expressionNode } from "../features/admin/packs/ExpressionTree";
import { itemErrors } from "../features/admin/packs/SectionEditor";
import { compact, fromYaml, toYaml } from "../features/admin/packs/yaml";

const admin: Me = {
  user_id: "u1", display_name: "Ada Admin", email: null, tenant_id: "t1", roles: ["admin"], level: "admin", audience_roles: [],
  permissions: ["dashboards.view", "packs.author", "starter.manage"], scope_summary: [], locale: null, time_zone: null,
};

const OPEN: PackMeasure = {
  key: "open_projects", label: "Open projects", entity: "projects", rule_ref: "RULE-026", status: "certified",
  plain_rule: "Projects with status Active.", synonyms: ["active projects"],
  expression: { agg: "count", where: { all: [{ field: "status", op: "=", value: "Active" }, { field: "go_live", op: "<", value: { asof_offset_days: 0 } }] } },
};
const version = (over: Partial<PackVersion>): PackVersion => ({
  id: "p-pmo", pack_key: "pmo_tpg", version: "2.1.0", title: "PMO starter pack (TPG)", status: "released", owner: "system", editable: false,
  released_at: "2026-10-27T00:00:00Z", updated_at: "2026-11-05T10:00:00Z", based_on_pack_id: null, revision: "r1",
  counts: { entities: 1, links: 0, measures: 1, dimensions: 0 }, installed_on: [{ connection_id: "c1", name: "TPG Dataverse" }], ...over,
});
const detail = (over: Partial<PackVersion>): PackDetail => ({
  ...version(over),
  manifest: {
    pack_key: over.pack_key ?? "pmo_tpg", pack_version: "2.1.0", title: over.title ?? "PMO starter pack (TPG)", released_at: "2026-10-27T00:00:00Z",
    signature: { primary_entity: "projects", min_confidence: 0.05 },
    entities: [{ key: "projects", label: "Projects", object_candidates: ["projects", "tpg_project"], primary_key: ["project_id"], fields: [
      { key: "project_id", label: "Project id", data_type: "text", columns: ["project_id"], required: true },
      { key: "status", label: "Status", data_type: "text", columns: ["status", "statecodename"], blank_handling: "Blank is not set." },
    ] }],
    links: [], measures: [OPEN], dimensions: [], reports_from: "report_blueprints.yaml",
    dashboards: { projects: { title: "PMO — Projects", reports: ["a", "b"] } },
  },
  documents: { glossary: 40, report_blueprints: 38 },
  checks: { valid: true, errors: [], warnings: [] },
});
const PMO = detail({});
const MINE = detail({ id: "p-mine", pack_key: "acme_pmo", title: "Acme PMO", status: "draft", owner: "tenant", editable: true, revision: "r7", installed_on: [] });
const LIST: PackList = {
  items: [
    { pack_key: "pmo_tpg", owner: "system", title: PMO.title, latest: version({}), versions: [version({})], installed: 1 },
    { pack_key: "acme_pmo", owner: "tenant", title: "Acme PMO", latest: version({ id: "p-mine", pack_key: "acme_pmo", title: "Acme PMO", status: "draft", owner: "tenant", editable: true, installed_on: [] }), versions: [], installed: 0 },
  ],
  create: { methods: ["ai", "blank", "clone", "model"], planned: {} },
};

type Handler = (url: string, init?: RequestInit) => Response | undefined;
const calls: { url: string; method: string; body: unknown; headers: Record<string, string> }[] = [];

function serve(handler: Handler) {
  calls.length = 0;
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    const body = typeof init?.body === "string" ? JSON.parse(init.body) : undefined;
    calls.push({ url, method: init?.method ?? "GET", body, headers: (init?.headers ?? {}) as Record<string, string> });
    if (url.endsWith("/api/v1/me")) return json(admin);
    if (url.endsWith("/api/v1/connections")) return json({ items: [{ id: "c1", name: "TPG Dataverse", engine: "dataverse", status: "connected" }] });
    return handler(url, init) ?? (url.includes("/api/v1/notifications") ? json({ items: [], unread_count: 0, next_cursor: null, kinds: [] }) : json({ error: { code: "not_found", message: "Not found" } }, 404));
  }));
}

function json(body: unknown, status = 200, headers: Record<string, string> = {}) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json", ...headers } });
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

describe("Pack Studio (US-232)", () => {
  it("lists system and tenant packs, with Create slots for the later stories", async () => {
    serve((url) => (url.endsWith("/api/v1/packs") ? json(LIST) : undefined));
    renderAt("/admin/packs");
    const table = await screen.findByRole("table", { name: "Packs" });
    const pmo = await within(table).findByRole("row", { name: /PMO starter pack/ });
    expect(within(pmo).getByText("System · read-only")).toBeInTheDocument();
    expect(within(pmo).getByText("Released")).toBeInTheDocument();
    const mine = within(table).getByRole("row", { name: /Acme PMO/ });
    expect(within(mine).getByText("Draft")).toBeInTheDocument();
    // The page is no longer in the menu (v2 simplified Admin); it still opens by its URL.

    await userEvent.click(screen.getByRole("button", { name: /Create/ }));
    const blank = await screen.findByRole("menuitem", { name: /Blank pack/ });
    expect(blank).not.toHaveAttribute("aria-disabled", "true");
    // Every slot has its dialog now (AI draft: US-237, pack-author.test.tsx).
    for (const name of [/Clone a pack/, /From an approved model/, /AI draft/]) {
      expect(screen.getByRole("menuitem", { name })).not.toHaveAttribute("aria-disabled", "true");
    }
    expect(screen.queryByText(/— comes with/)).not.toBeInTheDocument();
  });

  it("opens a system pack read-only and clones it to edit", async () => {
    serve((url, init) => {
      if (url.endsWith("/api/v1/packs/p-pmo")) return json(PMO, 200, { ETag: '"r1"' });
      if (url.endsWith("/api/v1/packs") && init?.method === "POST") return json({ ...MINE, checks: MINE.checks }, 201);
      if (url.endsWith("/api/v1/packs/p-mine")) return json(MINE, 200, { ETag: '"r7"' });
      return undefined;
    });
    renderAt("/admin/packs/p-pmo?tab=measures");
    expect(await screen.findByText(/System packs are read-only/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Add measure/ })).not.toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /Measures/ })).toHaveAttribute("aria-selected", "true");

    await userEvent.click(screen.getByRole("button", { name: "Clone to edit" }));
    const dialog = await screen.findByRole("dialog", { name: /Clone PMO starter pack/ });
    expect(within(dialog).getByLabelText("Key")).toHaveValue("pmo_tpg_copy");
    await userEvent.clear(within(dialog).getByLabelText("Key"));
    await userEvent.type(within(dialog).getByLabelText("Key"), "acme_pmo");
    await userEvent.click(within(dialog).getByRole("button", { name: "Clone" }));
    await waitFor(() => expect(calls.find((c) => c.method === "POST")?.body).toEqual({
      method: "clone", pack_key: "acme_pmo", title: "PMO starter pack (TPG) (copy)", from_pack_id: "p-pmo" }));
    expect(await screen.findByRole("heading", { name: "Acme PMO" })).toBeInTheDocument();
  });

  it("edits a measure in its side panel: rule tree, If-Match save, errors on the field", async () => {
    let fail = true;
    serve((url, init) => {
      if (url.endsWith("/api/v1/packs/p-mine") && init?.method === "PATCH") {
        if (fail) {
          fail = false;
          return json({ error: { code: "validation_error", message: "Fix the errors before saving.", details: {
            errors: [{ path: "measures[0].plain_rule", message: "Say the rule in words." }, { path: "dimensions[2].field", message: "No such field." }], warnings: [] } } }, 400);
        }
        const sections = (JSON.parse(String(init.body)) as { sections: { measures: PackMeasure[] } }).sections;
        return json({ ...MINE, manifest: { ...MINE.manifest, measures: sections.measures }, revision: "r8" }, 200, { ETag: '"r8"' });
      }
      if (url.endsWith("/api/v1/packs/p-mine")) return json(MINE, 200, { ETag: '"r7"' });
      return undefined;
    });
    renderAt("/admin/packs/p-mine?tab=measures");
    const table = await screen.findByRole("table", { name: "Measures" });
    await userEvent.click(within(table).getByRole("row", { name: /Open projects/ }));
    const panel = await screen.findByRole("dialog", { name: "Open projects" });
    const tree = within(panel).getByRole("list", { name: "The rule as a tree" });
    expect(within(tree).getByText("Count of records")).toBeInTheDocument();
    expect(within(tree).getByText("status is Active")).toBeInTheDocument();
    expect(within(tree).getByText("go_live is before or below the reporting date")).toBeInTheDocument();
    expect(within(panel).getByRole("region", { name: "Try on a connection" })).toBeInTheDocument();

    const label = within(panel).getByLabelText("Label");
    await userEvent.clear(label);
    await userEvent.type(label, "Open portfolio");
    await userEvent.click(within(panel).getByRole("button", { name: "Save measure" }));
    expect(await within(panel).findByText("Say the rule in words.")).toBeInTheDocument();
    expect(within(panel).getByText(/Other parts of the pack need fixing first: dimensions\[2\].field/)).toBeInTheDocument();

    await userEvent.click(within(panel).getByRole("button", { name: "Save measure" }));
    await waitFor(() => expect(calls.filter((c) => c.method === "PATCH")).toHaveLength(2));
    const patch = calls.filter((c) => c.method === "PATCH")[1]!;
    expect(patch.headers["If-Match"]).toBe('"r7"');
    const saved = (patch.body as { sections: { measures: PackMeasure[] } }).sections.measures;
    // The edited label, and the keys the form doesn't show kept as they were.
    expect(saved).toEqual([{ ...OPEN, label: "Open portfolio" }]);
  });

  it("round-trips an item through its YAML view", async () => {
    serve((url) => (url.endsWith("/api/v1/packs/p-mine") ? json(MINE, 200, { ETag: '"r7"' }) : undefined));
    renderAt("/admin/packs/p-mine?tab=entities");
    const table = await screen.findByRole("table", { name: "Entities" });
    await userEvent.click(within(table).getByRole("row", { name: /Projects/ }));
    const panel = await screen.findByRole("dialog", { name: "Projects" });
    await userEvent.click(within(panel).getByRole("tab", { name: "YAML" }));
    const box = within(panel).getByLabelText(/as written in manifest.yaml/);
    const text = (box as HTMLTextAreaElement).value;
    expect(text).toContain("object_candidates:\n  - projects\n  - tpg_project");
    expect(text).toContain("blank_handling: Blank is not set.");
    expect(fromYaml(text)).toEqual({ ok: true, value: MINE.manifest.entities[0] });

    await userEvent.clear(box);
    await userEvent.click(box);
    await userEvent.paste(text.replace("label: Projects", "label: Portfolio projects"));
    await userEvent.click(within(panel).getByRole("button", { name: "Use this YAML" }));
    expect((await within(panel).findAllByLabelText("Label"))[0]).toHaveValue("Portfolio projects");

    await userEvent.click(within(panel).getByRole("tab", { name: "YAML" }));
    await userEvent.clear(within(panel).getByLabelText(/as written in manifest.yaml/));
    await userEvent.click(within(panel).getByLabelText(/as written in manifest.yaml/));
    await userEvent.paste("key: projects\nlabel: [unclosed");
    await userEvent.click(within(panel).getByRole("button", { name: "Use this YAML" }));
    expect(await within(panel).findByRole("alert")).toBeInTheDocument();
  });
});

describe("Pack Studio › Import YAML", () => {
  it("checks the file, shows what changes in each part and imports it on the loaded revision", async () => {
    const changes = { measures: { added: ["gulf_projects"], updated: [], removed: [], unchanged: 0 },
                      reports: { added: ["gulf_overview"], updated: [], removed: [], unchanged: 0 },
                      dashboards: { added: ["gulf"], updated: [], removed: [], unchanged: 0 } };
    let fail = true;
    serve((url, init) => {
      if (url.endsWith("/api/v1/packs/p-mine/bundle") && init?.method === "POST") {
        const body = JSON.parse(String(init.body)) as { dry_run?: boolean; mode: string };
        if (body.dry_run && fail) {
          fail = false;
          return json({ error: { code: "validation_error", message: "Fix the errors before importing.", details: {
            valid: false, errors: [{ path: "reports[0].kpis.0", message: "the pack has no measure nope" }], warnings: [], changes } } }, 400);
        }
        if (body.dry_run) return json({ valid: true, errors: [], warnings: [{ path: "dashboards.gulf.reports.1", message: "data_trust is a data gap: it stays in the pack without a tab." }], changes });
        return json({ ...MINE, revision: "r8", checks: { valid: true, errors: [], warnings: [], changes } }, 200, { ETag: '"r8"' });
      }
      if (url.endsWith("/api/v1/packs/p-mine/bundle")) return json({ yaml: "measures: []\n", file_name: "acme_pmo-2.1.0-items.yaml" });
      if (url.endsWith("/api/v1/packs/p-mine")) return json(MINE, 200, { ETag: '"r7"' });
      return undefined;
    });
    renderAt("/admin/packs/p-mine");
    await userEvent.click(await screen.findByRole("button", { name: "Import YAML" }));
    const dialog = await screen.findByRole("dialog", { name: "Import YAML" });
    const importBtn = within(dialog).getByRole("button", { name: "Import" });
    expect(importBtn).toBeDisabled();

    await userEvent.click(within(dialog).getByRole("button", { name: "Start from the template" }));
    expect((within(dialog).getByLabelText("YAML") as HTMLTextAreaElement).value).toContain("dashboards:\n  gulf:");
    await userEvent.click(within(dialog).getByRole("button", { name: "Check" }));
    const errors = await within(dialog).findByRole("list", { name: "Errors" });
    expect(within(errors).getByText("reports[0].kpis.0")).toBeInTheDocument();
    expect(importBtn).toBeDisabled();

    await userEvent.click(within(dialog).getByRole("radio", { name: /Replace/ }));
    await userEvent.click(within(dialog).getByRole("button", { name: "Check" }));
    const table = await within(dialog).findByRole("table", { name: "What changes" });
    expect(within(table).getByRole("row", { name: /Measures 1 0 0 0/ })).toBeInTheDocument();
    expect(within(dialog).getByText(/Ready to import\. 1 warning: the import goes ahead\./)).toBeInTheDocument();
    expect(within(within(dialog).getByRole("list", { name: "Warnings" })).getByText(/data_trust is a data gap/)).toBeInTheDocument();
    await userEvent.click(importBtn);
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "Import YAML" })).not.toBeInTheDocument());
    const done = calls.filter((c) => c.url.endsWith("/bundle") && c.method === "POST").at(-1)!;
    expect(done.headers["If-Match"]).toBe('"r7"');
    expect(done.body).toMatchObject({ mode: "replace" });
    expect((done.body as { dry_run?: boolean }).dry_run).toBeUndefined();

    // Start from this pack loads the draft's own file.
    await userEvent.click(screen.getByRole("button", { name: "Import YAML" }));
    const again = await screen.findByRole("dialog", { name: "Import YAML" });
    await userEvent.click(within(again).getByRole("button", { name: "Start from this pack" }));
    await waitFor(() => expect(within(again).getByLabelText("YAML")).toHaveValue("measures: []\n"));
  });

  it("offers a read-only pack's YAML as a download only", async () => {
    serve((url) => (url.endsWith("/api/v1/packs/p-pmo") ? json(PMO, 200, { ETag: '"r1"' }) : undefined));
    renderAt("/admin/packs/p-pmo");
    await userEvent.click(await screen.findByRole("button", { name: "YAML" }));
    const dialog = await screen.findByRole("dialog", { name: "Pack YAML" });
    expect(within(dialog).getByRole("button", { name: /Download this pack's YAML/ })).toBeInTheDocument();
    expect(within(dialog).queryByRole("button", { name: "Import" })).not.toBeInTheDocument();
  });
});

describe("Pack Studio helpers", () => {
  it("YAML in and out keeps an item", () => {
    expect(fromYaml(toYaml(OPEN))).toEqual({ ok: true, value: OPEN });
    expect(fromYaml("- a\n- b")).toMatchObject({ ok: false });
    expect(compact({ key: "k", grain: "", synonyms: [], required: false, primary_key: [] })).toEqual({ key: "k", primary_key: [] });
  });

  it("reads expressions and maps API paths to form fields", () => {
    expect(expressionNode({ op: "div", scale: 100, left: { agg: "sum", field: "cost" }, right: { agg: "count" } })).toEqual({
      text: "divided by, times 100", children: [{ text: "Sum of cost", children: [] }, { text: "Count of records", children: [] }] });
    expect(itemErrors([{ path: "entities[1].fields[2].key", message: "x" }, { path: "entities[10].key", message: "y" }], "entities", 1))
      .toEqual([{ path: "fields.2.key", message: "x" }]);
  });
});
