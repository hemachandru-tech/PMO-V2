import { QueryClient } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { KnowledgeChecks, KnowledgeDocument, KnowledgeKind, KnowledgeKindView, KnowledgeOverview } from "../api/packKnowledge";
import { Providers } from "../app/App";
import { TestAuthProvider } from "../app/auth";
import { setConfigForTests } from "../app/config";
import { KnowledgeTab } from "../features/admin/packs/knowledge/KnowledgeTab";
import { listAt, problemsOf, tidy, withList } from "../features/admin/packs/knowledge/ListEditor";

const pack = { id: "p1", key: "acme_pmo", version: "0.1.0", title: "Acme PMO", status: "draft", system: false, read_only: false };
const names = {
  measures: [{ key: "open_projects", label: "Open projects", entity: "projects" }, { key: "forecast_cost_total", label: "Forecast cost", entity: "projects" }],
  dimensions: [{ key: "region", label: "Region", entity: "projects" }],
  fields: { projects: ["name", "cost_total_usd"] },
};
const none = { measures: [], dimensions: [], fields: [] };

const glossary: KnowledgeDocument = {
  version: "0.2.0", status: "draft",
  terms: [{ term: "EAC", means: "forecast cost", explain: "Estimate at completion.", fields: ["projects.cost_total_usd"] }],
};
const rules: KnowledgeDocument = {
  version: "0.1.0", status: "draft",
  rules: [
    { id: "no_actual_spend", title: "Forecast cost, never spend", rule: "Never say spend.", measures: ["forecast_cost_total"] },
    { id: "no_burn", title: "No burn rate", rule: "Never show a burn rate.", measures: ["burn_rate"] },
  ],
  wording: { forbidden: [{ words: ["spend", "spent"], use: "forecast cost" }] },
};
const missing = { path: "dashboard_rules.rules[1].measures[0]", message: "There is no measure 'burn_rate' in this pack." };

const view = (kind: KnowledgeKind, document: KnowledgeDocument, checks: Partial<KnowledgeChecks>, etag = '"k1"'): KnowledgeKindView => ({
  pack, kind, list: kind === "glossary" ? "terms" : "rules", stored: true, document, names, etag,
  checks: { valid: true, errors: [], warnings: [], document_errors: [], items: [], ...checks },
});
const glossaryView = (doc = glossary, etag = '"k1"') => view("glossary", doc, {
  items: (doc.terms as { term: string; fields?: string[] }[]).map((t, i) => ({ index: i, title: t.term, refs: { ...none, fields: t.fields ?? [] }, missing: [], errors: [], warnings: [] })),
}, etag);
const rulesView = view("dashboard_rules", rules, {
  warnings: [missing],
  items: [
    { index: 0, title: "Forecast cost, never spend", refs: { ...none, measures: ["forecast_cost_total"] }, missing: [], errors: [], warnings: [] },
    { index: 1, title: "No burn rate", refs: { ...none, measures: ["burn_rate"] }, missing: ["measure:burn_rate"], errors: [], warnings: [missing] },
  ],
});

const overview = (over: Partial<KnowledgeOverview> = {}): KnowledgeOverview => ({
  pack, names, attachments: [],
  kinds: (["glossary", "value_synonyms", "dashboard_rules", "chart_patterns", "analysis_recipes", "gold_examples"] as const).map((k) => ({
    kind: k, stored: true, count: k === "glossary" ? 1 : k === "dashboard_rules" ? 2 : 0, version: "0.1.0", errors: 0,
    warnings: k === "dashboard_rules" ? 1 : 0, etag: '"k1"',
  })),
  ...over,
});

const calls: { url: string; method: string; body: unknown; ifMatch: string | null }[] = [];

function json(body: unknown, status = 200, headers: Record<string, string> = {}) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json", ...headers } });
}

function serve(handler: (url: string, init?: RequestInit) => Response | undefined) {
  calls.length = 0;
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    const headers = new Headers(init?.headers);
    const body = init?.body instanceof FormData ? init.body : init?.body ? JSON.parse(String(init.body)) : undefined;
    calls.push({ url, method: init?.method ?? "GET", body, ifMatch: headers.get("If-Match") });
    return handler(url, init) ?? json({ error: { code: "not_found", message: "Not found" } }, 404);
  }));
}

function renderTab() {
  return render(
    <TestAuthProvider token="tok">
      <Providers client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <KnowledgeTab packId="p1" />
      </Providers>
    </TestAuthProvider>,
  );
}

beforeEach(() => setConfigForTests({ apiBaseUrl: "http://api.test", oidcAuthority: "http://kc", oidcClientId: "tela-web" }));
afterEach(() => vi.unstubAllGlobals());

describe("Pack Studio › Knowledge (US-233)", () => {
  it("edits a glossary term in its form and saves the document with If-Match", async () => {
    serve((url, init) => {
      if (url.endsWith("/api/v1/packs/p1/knowledge")) return json(overview());
      if (url.endsWith("/knowledge/glossary") && init?.method === "PUT") {
        const doc = (JSON.parse(String(init.body)) as { document: KnowledgeDocument }).document;
        return json(glossaryView(doc, '"k2"'));
      }
      if (url.endsWith("/knowledge/glossary")) return json(glossaryView(), 200, { ETag: '"k1"' });
      return undefined;
    });
    renderTab();
    const table = await screen.findByRole("table", { name: "Terms" });
    const row = within(table).getByRole("row", { name: /EAC/ });
    expect(within(row).getByText("projects.cost_total_usd")).toBeInTheDocument();
    expect(within(row).getByText("OK")).toBeInTheDocument();
    await userEvent.click(row);
    const panel = await screen.findByRole("dialog", { name: "EAC" });
    expect(within(panel).getByText("Every name it uses is in the pack.")).toBeInTheDocument();
    const means = within(panel).getByLabelText("Means");
    await userEvent.clear(means);
    await userEvent.type(means, "estimate at completion");
    await userEvent.click(within(panel).getByRole("button", { name: "Save term" }));
    await waitFor(() => expect(calls.filter((c) => c.method === "PUT")).toHaveLength(1));
    const put = calls.find((c) => c.method === "PUT")!;
    expect(put.ifMatch).toBe('"k1"');
    expect((put.body as { document: KnowledgeDocument }).document).toEqual({
      ...glossary, terms: [{ term: "EAC", means: "estimate at completion", explain: "Estimate at completion.", fields: ["projects.cost_total_usd"] }],
    });
  });

  it("flags a rule naming a missing measure and shows what each rule refers to", async () => {
    serve((url) => {
      if (url.endsWith("/api/v1/packs/p1/knowledge")) return json(overview());
      if (url.endsWith("/knowledge/dashboard_rules")) return json(rulesView);
      if (url.endsWith("/knowledge/glossary")) return json(glossaryView());
      return undefined;
    });
    renderTab();
    const tab = await screen.findByRole("tab", { name: /Dashboard rules and wording/ });
    expect(within(tab).getByText("to check", { exact: false })).toBeInTheDocument();
    await userEvent.click(tab);
    const table = await screen.findByRole("table", { name: "Rules" });
    const bad = within(table).getByRole("row", { name: /No burn rate/ });
    expect(within(bad).getByText("1 missing name")).toBeInTheDocument();
    expect(within(bad).getByText("(missing measure)", { exact: false })).toBeInTheDocument();
    expect(within(within(table).getByRole("row", { name: /never spend/ })).getByText("OK")).toBeInTheDocument();
    expect(screen.getByText(/1 name doesn't resolve in this pack: There is no measure 'burn_rate'/)).toBeInTheDocument();
    // The wording terms of the same document are listed on their own.
    const wording = screen.getByRole("table", { name: "Wording terms" });
    expect(within(wording).getByRole("row", { name: /spend, spent/ })).toBeInTheDocument();
    await userEvent.click(bad);
    const panel = await screen.findByRole("dialog", { name: "No burn rate" });
    expect(within(panel).getByText("measures.0: There is no measure 'burn_rate' in this pack.")).toBeInTheDocument();
  });

  it("round-trips an item through its YAML view and shows a schema error from the API on its field", async () => {
    serve((url, init) => {
      if (url.endsWith("/api/v1/packs/p1/knowledge")) return json(overview());
      if (url.endsWith("/knowledge/glossary") && init?.method === "PUT") {
        return json({ error: { code: "validation_error", message: "Fix the errors", details: {
          errors: [{ path: "glossary.terms[0].explain", message: "String should have at most 600 characters" }], warnings: [], document_errors: [],
        } } }, 400);
      }
      if (url.endsWith("/knowledge/glossary")) return json(glossaryView());
      return undefined;
    });
    renderTab();
    const table = await screen.findByRole("table", { name: "Terms" });
    await userEvent.click(within(table).getByRole("row", { name: /EAC/ }));
    const panel = await screen.findByRole("dialog", { name: "EAC" });
    await userEvent.click(within(panel).getByRole("tab", { name: "YAML" }));
    const box = within(panel).getByLabelText(/as written in glossary.yaml/) as HTMLTextAreaElement;
    expect(box.value).toContain("term: EAC\nmeans: forecast cost");
    await userEvent.clear(box);
    await userEvent.click(box);
    await userEvent.paste("term: EAC\nmeans: cost forecast\nexplain: Estimate at completion.\nfields:\n  - projects.cost_total_usd\n");
    await userEvent.click(within(panel).getByRole("button", { name: "Use this YAML" }));
    expect(within(panel).getByLabelText("Means")).toHaveValue("cost forecast");
    await userEvent.click(within(panel).getByRole("button", { name: "Save term" }));
    expect(await within(panel).findByText("String should have at most 600 characters")).toBeInTheDocument();
  });

  it("attaches a context document and is read-only on a system pack", async () => {
    let attached = false;
    const att = { id: "a1", title: "Acme context", file_name: "acme.md", content_type: "text/markdown", size_bytes: 2048, sha256: "x", created_at: null };
    serve((url, init) => {
      if (url.endsWith("/knowledge/attachments") && init?.method === "POST") { attached = true; return json(att, 201); }
      if (url.endsWith("/api/v1/packs/p1/knowledge")) return json(overview({ attachments: attached ? [att] : [] }));
      if (url.endsWith("/knowledge/glossary")) return json(glossaryView());
      return undefined;
    });
    renderTab();
    expect(await screen.findByText("No documents attached.")).toBeInTheDocument();
    await userEvent.type(screen.getByLabelText("Title (optional)"), "Acme context");
    await userEvent.upload(screen.getByLabelText("Document to attach"), new File(["# Acme"], "acme.md", { type: "text/markdown" }));
    const list = await screen.findByRole("list", { name: "Attached documents" });
    expect(within(list).getByText("Acme context")).toBeInTheDocument();
    const post = calls.find((c) => c.method === "POST")!;
    expect((post.body as FormData).get("title")).toBe("Acme context");
    expect(((post.body as FormData).get("file") as File).name).toBe("acme.md");
  });

  it("opens a system pack read-only", async () => {
    const ro = { ...pack, system: true, read_only: true };
    serve((url) => {
      if (url.endsWith("/api/v1/packs/p1/knowledge")) return json({ ...overview(), pack: ro });
      if (url.endsWith("/knowledge/glossary")) return json({ ...glossaryView(), pack: ro });
      return undefined;
    });
    renderTab();
    await screen.findByRole("table", { name: "Terms" });
    expect(screen.queryByRole("button", { name: "Add term" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Attach a document" })).not.toBeInTheDocument();
  });

  it("reads and replaces nested lists and maps API paths onto an item", () => {
    expect(listAt(rules, ["wording", "forbidden"])).toHaveLength(1);
    const next = withList(rules, ["wording", "forbidden"], []);
    expect(next.wording).toEqual({ forbidden: [] });
    expect(next.rules).toBe(rules.rules);
    expect(problemsOf([missing, { path: "dashboard_rules.rules[10].id", message: "x" }], "dashboard_rules.rules", 1))
      .toEqual([{ path: "measures.0", message: missing.message }]);
    expect(tidy({ term: "A", means: "a", fields: [], reading: "", when: { measure: [], no_breakdown: false } })).toEqual({ term: "A", means: "a" });
  });
});
