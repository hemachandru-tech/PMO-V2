import { QueryClient } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { AuthorOverview, AuthorProposal } from "../api/packAuthor";
import type { PackDetail, PackList } from "../api/packs";
import type { Me } from "../api/types";
import { AppRoutes, Providers } from "../app/App";
import { TestAuthProvider } from "../app/auth";
import { setConfigForTests } from "../app/config";

const admin: Me = {
  user_id: "u1", display_name: "Ada Admin", email: null, tenant_id: "t1", roles: ["admin"], level: "admin", audience_roles: [],
  permissions: ["dashboards.view", "packs.author", "starter.manage"], scope_summary: [], locale: null, time_zone: null,
};
const TEMPLATE = { id: "pmo1", pack_key: "pmo_tpg", version: "2.1.0", title: "PMO starter pack (TPG)" };
const LIST: PackList = {
  items: [{ pack_key: "pmo_tpg", owner: "system", title: TEMPLATE.title, installed: 0,
    latest: { id: "pmo1", pack_key: "pmo_tpg", version: "2.1.0", title: TEMPLATE.title, status: "released", owner: "system", editable: false,
      released_at: "2026-10-01", updated_at: null, based_on_pack_id: null, revision: "x", counts: { entities: 10, links: 9, measures: 140, dimensions: 51 }, installed_on: [] },
    versions: [] }],
  create: { methods: ["ai", "blank", "clone", "model"], planned: {} },
};
LIST.items[0]!.versions = [LIST.items[0]!.latest];
const PACK: PackDetail = {
  id: "p-new", pack_key: "acme_ai", version: "0.1.0", title: "Acme AI pack", status: "draft", owner: "tenant", editable: true,
  released_at: null, updated_at: null, based_on_pack_id: null, revision: "r1", counts: { entities: 0, links: 0, measures: 0, dimensions: 0 },
  installed_on: [], documents: {}, checks: { valid: true, errors: [], warnings: [] },
  manifest: { pack_key: "acme_ai", pack_version: "0.1.0", title: "Acme AI pack", signature: { min_confidence: 0.5 }, entities: [], links: [], measures: [], dimensions: [] } as unknown as PackDetail["manifest"],
};
const OVERVIEW: AuthorOverview = {
  ai: false, provider: null, samples: false, templates: [TEMPLATE], template_id: "pmo1",
  tables: [{ qualified_name: "public.projects", columns: 40, in_draft: false }, { qualified_name: "public.risks", columns: 9, in_draft: false }],
  stages: [], knowledge: [],
};
const projects: AuthorProposal = {
  id: "entity:1", kind: "entity", key: "projects", title: "Projects", source: "rules", reason: "Table public.projects: 2 columns.",
  item: { key: "projects", label: "Projects", object_candidates: ["projects"], primary_key: ["project_id"],
          fields: [{ key: "project_id", label: "Project id", data_type: "text", columns: ["project_id"], required: true }, { key: "status", label: "Status", data_type: "text", columns: ["status"] }] },
  checks: { errors: [], warnings: [], count: 100, count_of: "rows" },
};
const risks: AuthorProposal = { ...projects, id: "entity:2", key: "risks", title: "Risks", item: { ...projects.item, key: "risks", label: "Risks", object_candidates: ["risks"] }, checks: { ...projects.checks, count: 40 } };
const ghost: AuthorProposal = { ...projects, id: "entity:3", key: "ghost", title: "Ghost", source: "ai", checks: { errors: [{ path: "object_candidates", message: "No table on this source has that name." }], warnings: [], count: null, count_of: null } };
const calls: { url: string; method: string; body: unknown; ifMatch: string | null }[] = [];

function json(body: unknown, status = 200, headers: Record<string, string> = {}) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json", ...headers } });
}

const sse = (events: [string, unknown][]) =>
  new Response(events.map(([e, d]) => `event: ${e}\ndata: ${JSON.stringify(d)}\n\n`).join(""), { status: 200, headers: { "Content-Type": "text/event-stream" } });

function serve() {
  calls.length = 0;
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    const body = typeof init?.body === "string" ? JSON.parse(init.body) : undefined;
    const headers = (init?.headers ?? {}) as Record<string, string>;
    calls.push({ url, method: init?.method ?? "GET", body, ifMatch: headers["If-Match"] ?? null });
    if (url.endsWith("/api/v1/me")) return json(admin);
    if (url.endsWith("/api/v1/connections")) return json({ items: [{ id: "c1", name: "TPG Postgres" }] });
    if (url.endsWith("/api/v1/packs") && init?.method === "POST") {
      return json({ ...PACK, left_out: [], source: { connection_id: "c1", template_pack_id: "pmo1" } }, 201);
    }
    if (url.endsWith("/api/v1/packs")) return json(LIST);
    if (url.endsWith("/api/v1/packs/p-new")) return json(PACK, 200, { ETag: '"r1"' });
    if (url.includes("/api/v1/packs/p-new/author?connection_id=c1")) return json(OVERVIEW);
    if (url.endsWith("/api/v1/packs/p-new/author")) {
      return sse([["stage", { stage: "entities", kind: "entity", ai: false, provider: null, samples: false, template: TEMPLATE }],
                  ["proposal", projects], ["proposal", risks], ["dropped", ghost], ["complete", { proposed: 2, dropped: 1, sources: ["rules"] }]]);
    }
    if (url.endsWith("/api/v1/packs/p-new/author/check")) {
      return json({ valid: true, items: [{ index: 0, item: body.items[0], errors: [], warnings: [], count: 100, count_of: "rows" }] });
    }
    if (url.endsWith("/api/v1/packs/p-new/author/accept")) {
      return json({ ...PACK, revision: "r2", counts: { ...PACK.counts, entities: 1 }, accepted: 1, checks: PACK.checks }, 200, { ETag: '"r2"' });
    }
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

describe("AI pack author (US-237)", () => {
  it("creates an AI draft on a connection and opens its AI author tab", async () => {
    serve();
    renderAt("/admin/packs");
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: /create/i }));
    await user.click(await screen.findByRole("menuitem", { name: /AI draft/ }));
    const dialog = await screen.findByRole("dialog", { name: "New pack drafted by AI" });
    await waitFor(() => expect(within(dialog).getAllByRole("option", { name: "TPG Postgres" }).length).toBe(1));
    await user.selectOptions(within(dialog).getByLabelText("Connection"), "c1");
    await user.type(within(dialog).getByLabelText("Title"), "Acme AI pack");
    await user.type(within(dialog).getByLabelText("Key"), "acme_ai");
    await user.click(within(dialog).getByRole("button", { name: "Create and draft" }));
    await waitFor(() => expect(window.location.search).toBe("?tab=author&connection=c1&template=pmo1"));
    const post = calls.find((c) => c.url.endsWith("/api/v1/packs") && c.method === "POST");
    expect(post?.body).toEqual({ method: "ai", pack_key: "acme_ai", title: "Acme AI pack", options: { connection_id: "c1" } });
    expect(await screen.findByTestId("author-mode")).toHaveTextContent("No AI model is set up, so Tela's rules draft every stage");
  });

  it("streams checked proposals; accept, edit and reject each one, and shows what the checks left out", async () => {
    serve();
    renderAt("/admin/packs/p-new?tab=author&connection=c1");
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Draft entities & fields by rules" }));
    const list = await screen.findByRole("list", { name: "Proposals" });
    const card = await within(list).findByRole("article", { name: "Projects" });
    expect(within(card).getByText("Rules proposed")).toBeInTheDocument();
    expect(within(card).getByTestId("count")).toHaveTextContent("100 rows");
    expect(within(card).getByText(/projects · 2 fields · key project_id/)).toBeInTheDocument();
    expect(screen.getByText("Left out by the checks (1)")).toBeInTheDocument();
    expect(within(screen.getByRole("list", { name: "Left out" })).getByText(/No table on this source has that name/)).toBeInTheDocument();
    const stream = calls.find((c) => c.url.endsWith("/author"));
    expect(stream?.body).toEqual({ connection_id: "c1", stage: "entities", template_pack_id: "pmo1" });
    expect(calls.filter((c) => c.url.endsWith("/author/accept"))).toHaveLength(0); // nothing written yet

    // Edit: the YAML is checked again before it can be accepted.
    await user.click(within(card).getByRole("button", { name: "Edit" }));
    const yaml = within(card).getByLabelText("YAML of Projects");
    await user.clear(yaml);
    await user.click(yaml);
    await user.paste("key: projects\nlabel: Project\nobject_candidates: [projects]\nprimary_key: [project_id]\nfields: []\n");
    await user.click(within(card).getByRole("button", { name: "Check" }));
    const edited = await within(list).findByRole("article", { name: "Project" });
    await user.click(within(edited).getByRole("button", { name: "Accept" }));
    await waitFor(() => expect(within(edited).getByText("Accepted")).toBeInTheDocument());
    const accept = calls.find((c) => c.url.endsWith("/author/accept"))!;
    expect(accept.ifMatch).toBe('"r1"');
    expect(accept.body).toMatchObject({ connection_id: "c1", kind: "entity", sources: ["rules"], items: [{ key: "projects", label: "Project" }] });

    await user.click(within(within(list).getByRole("article", { name: "Risks" })).getByRole("button", { name: "Reject" }));
    expect(within(list).queryByRole("article", { name: "Risks" })).not.toBeInTheDocument();
    expect(calls.filter((c) => c.url.endsWith("/author/accept"))).toHaveLength(1);
  });
});
