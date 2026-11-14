import { QueryClient } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { AuditItem, LlmSettings, LlmUsage, NotificationItem } from "../api/authoring";
import type { Me } from "../api/types";
import { AppRoutes, Providers } from "../app/App";
import { TestAuthProvider } from "../app/auth";
import { setConfigForTests } from "../app/config";

vi.mock("echarts/core", () => ({
  use: vi.fn(),
  registerTheme: vi.fn(),
  init: () => ({ setOption: vi.fn(), on: vi.fn(), resize: vi.fn(), dispose: vi.fn() }),
}));

const admin: Me = {
  user_id: "u1", display_name: "Ada Admin", email: null, tenant_id: "t1", roles: ["admin"], level: "admin", audience_roles: [],
  permissions: ["dashboards.view", "connections.manage", "audit.read", "settings.manage"],
  scope_summary: [], locale: null, time_zone: null,
};

type Handler = (url: string, init?: RequestInit) => Response | undefined;
const calls: { url: string; method: string; body: unknown }[] = [];

function serve(handler: Handler) {
  calls.length = 0;
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ url, method: init?.method ?? "GET", body: init?.body ? JSON.parse(String(init.body)) : undefined });
    if (url.endsWith("/api/v1/me")) return json(admin);
    return handler(url, init) ?? (url.includes("/api/v1/notifications") ? json({ items: [], unread_count: 0, next_cursor: null, kinds: [] }) : json({ error: { code: "not_found", message: "Not found" } }, 404));
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

const EVENT: AuditItem = {
  id: 7, at: "2026-10-20T09:32:00Z", actor: { id: "u5", name: "Arjun Rao" }, action: "model.approved",
  object: { type: "model_version", id: "v5", link: null }, outcome: "success", security: false, request_id: "7f3a-91c2",
  details: { model_id: "m1", version: 5, snapshot_sha256: "9c41e07a" },
};

describe("Audit log (US-146)", () => {
  it("filters by facets with counts, at once and in the URL, and opens an event with its links", async () => {
    serve((url) => {
      if (url.includes("/api/v1/admin/audit/facets")) {
        return json({ action: [{ value: "model.approved", count: 3 }, { value: "query.scope_refused", count: 9 }], object_type: [{ value: "model_version", count: 3 }],
          outcome: [{ value: "success", count: 12 }], actor: [{ value: "Arjun Rao", count: 7 }], security: 9, total: 1284 });
      }
      if (url.includes("/api/v1/admin/audit?")) return json({ items: [EVENT], next_cursor: url.includes("cursor=") ? null : "c2" });
      return undefined;
    });
    renderAt("/admin/audit");
    expect(await screen.findByText(/of 1,284 events · newest first/)).toBeInTheDocument();
    expect(await screen.findByRole("option", { name: "query.scope_refused (9)" })).toBeInTheDocument();
    expect(screen.getByText("(9)")).toBeInTheDocument(); // security events count beside the switch

    await userEvent.selectOptions(screen.getByLabelText("Action"), "model.approved");
    expect(window.location.search).toBe("?action=model.approved");
    await waitFor(() => expect(calls.some((c) => c.url.includes("/admin/audit/facets?action=model.approved"))).toBe(true));
    await userEvent.selectOptions(screen.getByLabelText("When"), "7d");
    const params = new URLSearchParams(window.location.search);
    expect(params.get("range")).toBe("7d");
    expect(params.get("from")).toMatch(/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/);
    await userEvent.click(screen.getByRole("button", { name: "Clear Action filter" }));
    expect(new URLSearchParams(window.location.search).has("action")).toBe(false);

    await userEvent.click(screen.getByRole("button", { name: "Load more" }));
    await waitFor(() => expect(calls.some((c) => c.url.includes("cursor=c2") && c.url.includes("limit=100"))).toBe(true));

    await userEvent.click(screen.getAllByRole("button", { name: "Details" })[0]!);
    const sheet = await screen.findByRole("dialog", { name: "model.approved" });
    expect(sheet).toHaveTextContent("snapshot sha256");
    expect(sheet).toHaveTextContent("9c41e07a");
    expect(within(sheet).getByRole("link", { name: "Model v5 in Model studio" })).toHaveAttribute("href", "/admin/models/m1");
    await userEvent.click(within(sheet).getByRole("button", { name: "Events from Arjun Rao" }));
    expect(window.location.search).toBe("?actor=Arjun+Rao");
  });
});

const note = (id: string, kind: string, title: string, read: boolean, hoursAgo: number): NotificationItem => ({
  id, kind, title, body: "body", link: `/dashboards/${id}`, read_at: read ? "2026-10-21T00:00:00Z" : null,
  created_at: new Date(Date.now() - hoursAgo * 3_600_000).toISOString(),
});

describe("Notifications centre (US-146)", () => {
  it("lists today and earlier, filters by type and unread, marks read and pages", async () => {
    let allRead = false;
    serve((url, init) => {
      if (url.endsWith("/read-all") && init?.method === "POST") { allRead = true; return new Response(null, { status: 204 }); }
      if (url.includes("/api/v1/notifications/") && init?.method === "POST") return new Response(null, { status: 204 });
      if (url.includes("/api/v1/notifications")) {
        const page = url.includes("cursor=n2")
          ? [note("n9", "share.received", "Old share", true, 24 * 30)]
          : [note("n1", "dashboard.published", "PMO — Projects v8 published", allRead, 0.5), note("n2", "share.received", "Delivery health shared", true, 48)];
        return json({ items: page, unread_count: allRead ? 0 : 1, next_cursor: url.includes("cursor=") ? null : "n2",
          kinds: [{ kind: "dashboard.published", count: 4, unread: allRead ? 0 : 1 }, { kind: "share.received", count: 2, unread: 0 }] });
      }
      return undefined;
    });
    renderAt("/notifications");
    expect(await screen.findByRole("heading", { name: "Notifications" })).toBeInTheDocument();
    const today = await screen.findByRole("region", { name: "Today" });
    expect(within(today).getByText("PMO — Projects v8 published")).toBeInTheDocument();
    expect(within(screen.getByRole("region", { name: "Earlier" })).getByText("Delivery health shared")).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "Dashboards published to you (1 unread)" })).toBeInTheDocument();

    await userEvent.selectOptions(screen.getByLabelText("Type"), "share.received");
    await waitFor(() => expect(calls.some((c) => c.url.includes("kind=share.received"))).toBe(true));
    await userEvent.click(screen.getByRole("tab", { name: /Unread/ }));
    expect(window.location.search).toBe("?kind=share.received&show=unread");
    await waitFor(() => expect(calls.some((c) => c.url.includes("unread=true") && c.url.includes("kind=share.received"))).toBe(true));
    await userEvent.click(screen.getByRole("tab", { name: /All/ }));
    await userEvent.selectOptions(screen.getByLabelText("Type"), "");

    await userEvent.click(await screen.findByRole("button", { name: "Show more" }));
    expect(await screen.findByText("Old share")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Mark all as read" }));
    await waitFor(() => expect(screen.queryByRole("button", { name: "Mark all as read" })).not.toBeInTheDocument());
  });

  it("is one click from the bell", async () => {
    serve(() => undefined);
    renderAt("/");
    await userEvent.click(await screen.findByRole("button", { name: /Notifications, 0 unread/ }));
    await userEvent.click(await screen.findByRole("button", { name: "See all notifications" }));
    expect(window.location.pathname).toBe("/notifications");
  });
});

const SETTINGS: LlmSettings = {
  provider: "ollama", models: { planner: "llama3.2:3b", embeddings: "mxbai-embed-large" }, allow_samples: false, allow_aggregates: false,
  monthly_token_budget: 0, rates: { "llama3.2:3b": { input_per_mtok: 0, output_per_mtok: 0 } }, key_set: false,
  last_test: { ok: true, tested_at: "2026-10-21T05:00:00Z", latency_ms: 812, model: "llama3.2:3b" }, allowed_providers: ["ollama"],
};
const usage = (group: LlmUsage["group_by"], items: Partial<LlmUsage["items"][number]>[]): LlmUsage => ({
  group_by: group, days: 14, cost_note: "Estimated from the configured rates, not from provider bills.",
  items: items.map((i) => ({ key: "k", calls: 0, prompt_tokens: 0, completion_tokens: 0, blocked: 0, failed: 0, estimated_cost: 0, p50_ms: null, p95_ms: null, ...i })),
});

describe("AI settings and usage (US-146)", () => {
  it("saves the model per job, what models may see, rates and a write-only key", async () => {
    serve((url, init) => {
      if (url.endsWith("/api/v1/admin/llm-settings") && init?.method === "PUT") {
        const body = JSON.parse(String(init.body)) as { api_key?: string };
        return json({ ...SETTINGS, key_set: !!body.api_key, allow_aggregates: true });
      }
      if (url.endsWith("/api/v1/admin/llm-settings")) return json(SETTINGS);
      if (url.includes("/api/v1/admin/usage")) return json(usage("day", []));
      return undefined;
    });
    renderAt("/admin/ai");
    const form = await screen.findByRole("form", { name: "AI settings" });
    expect(within(form).getByText(/Only the local model is allowed/)).toBeInTheDocument();
    expect(within(form).getByLabelText("Planning answers model")).toHaveValue("llama3.2:3b");
    expect(within(form).getByText("Rules planner")).toBeInTheDocument();
    expect(within(form).getByText("Test call passed")).toBeInTheDocument();

    await userEvent.clear(within(form).getByLabelText("Planning answers model"));
    await userEvent.click(within(form).getByRole("button", { name: "Save changes" }));
    expect(await within(form).findByText("Choose a planner model for the provider.")).toBeInTheDocument();
    await userEvent.type(within(form).getByLabelText("Planning answers model"), "llama3.2:3b");
    await userEvent.click(within(form).getByRole("switch", { name: "Totals and counts in answers" }));
    const rates = within(form).getByRole("group", { name: "Cost per million tokens (USD)" });
    await userEvent.type(within(rates).getAllByLabelText("Input")[0]!, "0.5");
    expect(within(form).queryByLabelText(/API key/)).not.toBeInTheDocument(); // the local model needs no key
    await userEvent.click(within(form).getByRole("button", { name: "Save changes" }));
    expect(await within(form).findByText("Saved and tested.")).toBeInTheDocument();
    expect(calls.find((c) => c.method === "PUT")!.body).toEqual({
      provider: "ollama", models: { planner: "llama3.2:3b", embeddings: "mxbai-embed-large" }, allow_aggregates: true, allow_samples: false,
      llm_review: false, monthly_token_budget: 0,
      rates: { "llama3.2:3b": { input_per_mtok: 0.5, output_per_mtok: 0 }, "mxbai-embed-large": { input_per_mtok: 0, output_per_mtok: 0 } },
    });
  });

  it("starts LLM review on for a hosted provider and off for a local one, unless the Admin set it (US-239)", async () => {
    serve((url) => {
      if (url.endsWith("/api/v1/admin/llm-settings")) return json({ ...SETTINGS, allowed_providers: ["ollama", "anthropic"], keyed_providers: ["anthropic"] });
      if (url.includes("/api/v1/admin/usage")) return json(usage("day", []));
      return undefined;
    });
    renderAt("/admin/ai");
    const form = await screen.findByRole("form", { name: "AI settings" });
    const review = within(form).getByRole("switch", { name: "Review every draft with the model" });
    expect(review).toHaveAttribute("aria-checked", "false");
    expect(within(form).getByText(/on a local model each can take a minute/)).toBeInTheDocument();
    await userEvent.selectOptions(within(form).getByLabelText("Provider"), "anthropic");
    expect(review).toHaveAttribute("aria-checked", "true");
    await userEvent.click(review);
    await userEvent.selectOptions(within(form).getByLabelText("Provider"), "ollama");
    await userEvent.selectOptions(within(form).getByLabelText("Provider"), "anthropic");
    expect(review).toHaveAttribute("aria-checked", "false"); // the Admin's choice stays
    await userEvent.selectOptions(within(form).getByLabelText("Provider"), "");
    expect(review).toBeDisabled();
  });

  it("shows where planning runs, the recommended planner model and a warning when it fails the format check (US-191)", async () => {
    const health = { option: "local" as const, provider: "ollama", model: "llama3.2:3b", days: 7, calls: 40, schema_failures: 14, timeouts: 2, failure_rate: 0.35, warn: true, recommended: "qwen3:8b", minimum: "qwen3:8b" };
    serve((url) => {
      if (url.endsWith("/api/v1/admin/llm-settings")) return json({ ...SETTINGS, planner_health: health });
      if (url.includes("/api/v1/admin/usage")) return json(usage("day", []));
      return undefined;
    });
    renderAt("/admin/ai");
    const fit = await screen.findByLabelText("Planner model fit");
    expect(within(fit).getByText(/questions never leave your servers\. Recommended planner model: qwen3:8b\./)).toBeInTheDocument();
    expect(within(fit).getByText(/llama3\.2:3b gave answers in the wrong format on 35% of 40 questions in the last 7 days/)).toBeInTheDocument();
    expect(within(fit).getByText(/Choose qwen3:8b instead\./)).toBeInTheDocument();
  });

  it("names a hosted planner without a warning when its answers pass", async () => {
    const health = { option: "hosted" as const, provider: "anthropic", model: "claude-sonnet-5-5", days: 7, calls: 3, schema_failures: 0, timeouts: 0, failure_rate: 0, warn: false, recommended: "claude-sonnet-5-5", minimum: "claude-haiku-5-5" };
    serve((url) => {
      if (url.endsWith("/api/v1/admin/llm-settings")) return json({ ...SETTINGS, planner_health: health });
      if (url.includes("/api/v1/admin/usage")) return json(usage("day", []));
      return undefined;
    });
    renderAt("/admin/ai");
    const fit = await screen.findByLabelText("Planner model fit");
    expect(within(fit).getByText(/never individual records\. Recommended planner model: claude-sonnet-5-5 \(at least claude-haiku-5-5\)\./)).toBeInTheDocument();
    expect(within(fit).queryByText(/wrong format/)).not.toBeInTheDocument();
  });

  it("asks a hosted provider for its own key and Azure for its endpoint (DEC-01)", async () => {
    const hosted: LlmSettings = {
      ...SETTINGS, allowed_providers: ["ollama", "anthropic", "azure_openai"], keyed_providers: ["anthropic", "azure_openai", "gemini", "openai"],
      embedding_providers: ["azure_openai", "gemini", "ollama", "openai"], aggregates_allowed: true,
    };
    serve((url, init) => {
      if (url.endsWith("/api/v1/admin/llm-settings") && init?.method === "PUT") return json({ ...hosted, provider: "azure_openai", key_set: true });
      if (url.endsWith("/api/v1/admin/llm-settings")) return json(hosted);
      if (url.includes("/api/v1/admin/usage")) return json(usage("day", []));
      return undefined;
    });
    renderAt("/admin/ai");
    const form = await screen.findByRole("form", { name: "AI settings" });
    expect(within(form).queryByText(/Only the local model is allowed/)).not.toBeInTheDocument();
    await userEvent.selectOptions(within(form).getByLabelText("Provider"), "anthropic");
    expect(within(form).getByLabelText("Search over knowledge model")).toBeDisabled();
    expect(within(form).getByText(/has no embeddings service/)).toBeInTheDocument();
    await userEvent.click(within(form).getByRole("button", { name: "Save changes" }));
    expect(await within(form).findByText("Enter the API key for Anthropic Claude.")).toBeInTheDocument();

    await userEvent.selectOptions(within(form).getByLabelText("Provider"), "azure_openai");
    await userEvent.clear(within(form).getByLabelText("Planning answers model"));
    await userEvent.type(within(form).getByLabelText("Planning answers model"), "planner-prod");
    await userEvent.clear(within(form).getByLabelText("Search over knowledge model"));
    await userEvent.type(within(form).getByLabelText("Search over knowledge model"), "embed-prod");
    expect(within(form).getByText(/rebuilds the knowledge index/)).toBeInTheDocument();
    await userEvent.type(within(form).getByLabelText("Azure OpenAI API key"), "azure-key-123");
    await userEvent.type(within(form).getByLabelText("Azure OpenAI endpoint"), "acme.openai.azure.com");
    await userEvent.click(within(form).getByRole("button", { name: "Save changes" }));
    expect(await within(form).findByText(/Enter the endpoint/)).toBeInTheDocument();
    await userEvent.clear(within(form).getByLabelText("Azure OpenAI endpoint"));
    await userEvent.type(within(form).getByLabelText("Azure OpenAI endpoint"), "https://acme.openai.azure.com/");
    await userEvent.click(within(form).getByRole("button", { name: "Save changes" }));
    expect(await within(form).findByText("Saved and tested.")).toBeInTheDocument();
    expect(calls.find((c) => c.method === "PUT")!.body).toMatchObject({
      provider: "azure_openai", models: { planner: "planner-prod", embeddings: "embed-prod" }, api_key: "azure-key-123",
      endpoint: "https://acme.openai.azure.com",
    });
  });

  it("explains a failed provider test and shows usage totals, charts and a table by model", async () => {
    const today = new Date().toISOString().slice(0, 10);
    serve((url, init) => {
      if (url.endsWith("/api/v1/admin/llm-settings") && init?.method === "PUT") {
        return json({ error: { code: "planner_unavailable", message: "The test call failed, so the provider was not saved: model not found", request_id: "r1", details: {} } }, 503);
      }
      if (url.endsWith("/api/v1/admin/llm-settings")) return json(SETTINGS);
      if (url.includes("group_by=day")) return json(usage("day", [{ key: today, calls: 3412, prompt_tokens: 9_000_000, completion_tokens: 800_000, blocked: 17, estimated_cost: 1.25, p50_ms: 300, p95_ms: 2400 }]));
      if (url.includes("group_by=model")) return json(usage("model", [{ key: "ollama:llama3.2:3b", calls: 2108, prompt_tokens: 6_000_000, completion_tokens: 412_300, blocked: 17, p95_ms: 2400 }]));
      if (url.includes("group_by=task")) return json(usage("task", [{ key: "intent", calls: 9 }]));
      return undefined;
    });
    renderAt("/admin/ai");
    const form = await screen.findByRole("form", { name: "AI settings" });
    await userEvent.click(within(form).getByRole("button", { name: "Save changes" }));
    expect(await within(form).findByRole("alert")).toHaveTextContent("model not found");

    const usageSection = screen.getByRole("region", { name: "Usage" });
    expect(await within(usageSection).findByText("3,412")).toBeInTheDocument();
    expect(within(usageSection).getByText("9.8M")).toBeInTheDocument();
    expect(within(usageSection).getByText("$1.25")).toBeInTheDocument();
    expect(within(usageSection).getByText("Blocked by policy").nextElementSibling).toHaveTextContent("17");
    for (const title of ["Calls per day", "Tokens per day", "Latency per day (ms)", "Calls blocked by policy per day"]) {
      expect(within(usageSection).getByRole("img", { name: title })).toBeInTheDocument();
    }
    const table = await within(usageSection).findByRole("table", { name: "Usage by model" });
    expect(within(table).getByText("ollama:llama3.2:3b")).toBeInTheDocument();
    await userEvent.selectOptions(within(usageSection).getByLabelText("Group by"), "task");
    expect(await within(usageSection).findByRole("table", { name: "Usage by task" })).toBeInTheDocument();
    expect(window.location.search).toContain("by=task");
  });
});
