import { QueryClient } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Me } from "../api/types";
import { AppRoutes, Providers } from "../app/App";
import { TestAuthProvider } from "../app/auth";
import { setConfigForTests } from "../app/config";
import type { AgentPrompt, AgentSkill, AgentSummary } from "../features/admin/ai/AgentsPage";

// ENH-030 (US-264 to US-266): the Agents screen.

vi.mock("echarts/core", () => ({
  use: vi.fn(),
  registerTheme: vi.fn(),
  init: () => ({ setOption: vi.fn(), on: vi.fn(), resize: vi.fn(), dispose: vi.fn() }),
}));

const admin: Me = {
  user_id: "u1", display_name: "Ada Admin", email: null, tenant_id: "t1", roles: ["admin"], level: "admin", audience_roles: [],
  permissions: ["dashboards.view", "settings.manage", "audit.read"],
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

const STATS = { runs: 3, llm_calls: 5, failed: 1, fallbacks: 0, avg_ms: 1800, tokens: 900 };
const LIMITS = { prompt: 20000, skill: 8000, skills_on: 12000 };
const REVIEW = { reviews: true, on: true, choice: null, default_on: true, switch_on: true };
const agent = (a: Partial<AgentSummary>): AgentSummary => ({
  code: "AG-01", name: "Intent router", purpose: "Routes the turn.", role: "planner", tasks: ["intent"], review_off: false, review: REVIEW,
  prompts: 2, edited: 0, skills: 0, skills_on: 0, stats: { ...STATS, llm_calls: 0, failed: 0 }, ...a,
});
const prompt = (p: Partial<AgentPrompt>): AgentPrompt => ({
  key: "ask.plan", agent: "AG-03", task: "plan", title: "Query planner", used_for: "Ask: plans the Query Spec.", editable: true,
  note: "", edited: false, version: 0, updated_at: null, updated_by: null, change_note: "", default: "You are Tela's query planner.",
  text: "You are Tela's query planner.", ...p,
});
const SHARED = prompt({ key: "review.frame", agent: "shared", task: "*", title: "LLM review frame", used_for: "Every LLM review.", default: "You review a draft.", text: "You review a draft." });
const LIST = {
  items: [agent({}), agent({ code: "AG-03", name: "Query planner", purpose: "Plans the query.", prompts: 4, edited: 1, skills_on: 1, stats: STATS }),
    agent({ code: "AG-05", name: "Report planner", review_off: true, review: { ...REVIEW, on: false, default_on: false } })],
  shared: [SHARED], stats_days: 7, limits: LIMITS,
};
const SKILL: AgentSkill = { id: "s1", name: "Slipping", body: "'Slipping' means schedule risk.", enabled: true, updated_at: "2026-11-13T09:00:00Z", updated_by: "Ada Admin" };
const DETAIL = {
  ...LIST.items[1], inputs: "The question and candidates.", output: "A Query Spec.", guard: "The validator checks every ID.",
  knowledge: [{ label: "Gold examples", href: "/admin/knowledge/gold" }],
  prompts: [prompt({ edited: true, version: 2, text: "Plan PMO questions exactly.", updated_at: "2026-11-13T09:00:00Z", updated_by: "Ada Admin", change_note: "Shorter" }),
    prompt({ key: "ask.link", title: "Linking words to fields", default: "Pick candidates.", text: "Pick candidates." })],
  shared: [SHARED], skills: [SKILL], stats_days: 7, limits: LIMITS,
};

describe("Agents screen (ENH-030)", () => {
  it("turns LLM review on for an agent that is off by default, and goes back to the default (US-268)", async () => {
    let review = { ...REVIEW, on: false, default_on: false };
    serve((url, init) => {
      if (url.endsWith("/api/v1/admin/agents/AG-05")) return json({ ...DETAIL, code: "AG-05", name: "Report planner", review_off: !review.on, review });
      if (url.endsWith("/api/v1/admin/agents/AG-05/review") && init?.method === "PUT") {
        const on = (JSON.parse(String(init.body)) as { on: boolean | null }).on;
        review = { ...review, choice: on, on: on ?? review.default_on } as typeof review;
        return json(review);
      }
      return undefined;
    });
    const user = userEvent.setup();
    renderAt("/admin/agents/AG-05");
    const sw = await screen.findByRole("switch", { name: "LLM review for AG-05" });
    expect(sw).not.toBeChecked();
    expect(screen.getByText("Deployment default")).toBeInTheDocument();
    await user.click(sw);
    await waitFor(() => expect(calls.find((c) => c.method === "PUT")?.body).toEqual({ on: true }));
    expect(await screen.findByText("Set by an Admin")).toBeInTheDocument();
    expect(screen.getByRole("switch", { name: "LLM review for AG-05" })).toBeChecked();
    await user.click(screen.getByRole("button", { name: "Use default (off)" }));
    await waitFor(() => expect(calls.filter((c) => c.method === "PUT").at(-1)?.body).toEqual({ on: null }));
    expect(await screen.findByText("Deployment default")).toBeInTheDocument();
  });

  it("says when the tenant's LLM review switch is off", async () => {
    serve((url) => (url.endsWith("/api/v1/admin/agents/AG-03") ? json({ ...DETAIL, review: { ...REVIEW, switch_on: false } }) : undefined));
    renderAt("/admin/agents/AG-03");
    expect(await screen.findByText(/LLM review is off for the whole tenant/)).toBeInTheDocument();
  });

  it("lists every agent with its prompts, skills and last 7 days, and the shared prompts", async () => {
    serve((url) => (url.endsWith("/api/v1/admin/agents") ? json(LIST) : undefined));
    renderAt("/admin/agents");
    expect(await screen.findByRole("heading", { name: "Agents" })).toBeInTheDocument();
    const list = await screen.findByRole("list", { name: "Agents" });
    const ag03 = within(list).getByRole("link", { name: "AG-03 Query planner" });
    expect(ag03).toHaveAttribute("href", "/admin/agents/AG-03");
    expect(within(ag03).getByText("4 prompts · 1 edited")).toBeInTheDocument();
    expect(within(ag03).getByText("1 skill on")).toBeInTheDocument();
    expect(within(ag03).getByText("1.8 s")).toBeInTheDocument();
    expect(within(list).getByText("LLM review off")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Shared by all agents" })).toBeInTheDocument();
    expect(screen.getByRole("article", { name: "Prompt LLM review frame" })).toBeInTheDocument();
  });

  it("edits a prompt with a note, and resets it to the default after confirming", async () => {
    serve((url, init) => {
      if (url.endsWith("/api/v1/admin/agents/AG-03")) return json(DETAIL);
      if (url.endsWith("/api/v1/admin/agents/AG-03/prompts/ask.plan")) {
        return init?.method === "DELETE" ? json(prompt({ version: 3 })) : json(prompt({ edited: true, version: 3, text: "New text" }));
      }
      return undefined;
    });
    const user = userEvent.setup();
    renderAt("/admin/agents/AG-03");
    const card = await screen.findByRole("article", { name: "Prompt Query planner" });
    expect(within(card).getByText("Edited · v2")).toBeInTheDocument();
    expect(within(card).getByText(/by Ada Admin — Shorter/)).toBeInTheDocument();
    await user.click(within(card).getByRole("button", { name: "Edit" }));
    const box = within(card).getByLabelText("Prompt text");
    expect(box).toHaveValue("Plan PMO questions exactly.");
    await user.clear(box);
    await user.type(box, "Plan carefully.");
    await user.type(within(card).getByLabelText("What changed (optional)"), "Clearer");
    await user.click(within(card).getByRole("button", { name: "Save prompt" }));
    await waitFor(() => expect(calls.some((c) => c.method === "PUT")).toBe(true));
    expect(calls.find((c) => c.method === "PUT")!.body).toEqual({ text: "Plan carefully.", note: "Clearer" });

    await user.click(await within(card).findByRole("button", { name: "Reset" }));
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: "Reset to default" }));
    await waitFor(() => expect(calls.some((c) => c.method === "DELETE" && c.url.endsWith("/prompts/ask.plan"))).toBe(true));
  });

  it("adds a skill, turns one off, and shows the details", async () => {
    serve((url, init) => {
      if (url.endsWith("/api/v1/admin/agents/AG-03")) return json(DETAIL);
      if (url.endsWith("/api/v1/admin/agents/AG-03/skills") && init?.method === "POST") return json({ ...SKILL, id: "s2", name: "Fiscal year" }, 201);
      if (url.endsWith("/api/v1/admin/agents/AG-03/skills/s1")) return json({ ...SKILL, enabled: false });
      return undefined;
    });
    const user = userEvent.setup();
    renderAt("/admin/agents/AG-03");
    await user.click(await screen.findByRole("tab", { name: /Skillset/ }));
    const skill = await screen.findByRole("article", { name: "Skill Slipping" });
    await user.click(within(skill).getByRole("switch", { name: "Use Slipping" }));
    await waitFor(() => expect(calls.find((c) => c.method === "PUT")?.body).toEqual({ name: "Slipping", body: SKILL.body, enabled: false }));

    await user.click(screen.getByRole("button", { name: "Add skill" }));
    const form = screen.getByRole("form", { name: "New skill" });
    await user.type(within(form).getByLabelText("Name"), "Fiscal year");
    await user.type(within(form).getByLabelText("Skill (Markdown)"), "This FY runs April to March.");
    await user.click(within(form).getByRole("button", { name: "Add skill" }));
    await waitFor(() => expect(calls.find((c) => c.method === "POST")?.body).toEqual({ name: "Fiscal year", body: "This FY runs April to March.", enabled: true }));

    await user.click(screen.getByRole("tab", { name: "Details" }));
    expect(await screen.findByText("The validator checks every ID.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Gold examples" })).toHaveAttribute("href", "/admin/knowledge/gold");
    expect(screen.getByRole("link", { name: /This agent's runs/ })).toHaveAttribute("href", "/admin/agent-runs?agent=AG-03");
  });

  it("tries a draft prompt on a question and shows the saved and draft results side by side (US-267)", async () => {
    const sse = (event: string, data: unknown) => `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;
    const call = (prompts: string[]) => ({ agent: "AG-03", task: "plan", ok: true, reply: "{\"measure\": \"...\"}", ms: 900, tuned: { prompts, skills: [] } });
    serve((url, init) => {
      if (url.endsWith("/api/v1/admin/agents/AG-03")) return json(DETAIL);
      if (url.endsWith("/api/v1/admin/agents/AG-03/try") && init?.method === "POST") {
        return new Response(
          sse("start", { kind: "ask", connection: { id: "c1", name: "TPG" } })
          + sse("side", { side: "after", status: "ok", planner: "llm", confidence: 0.8, reading: "Schedule risk by Region", spec: { measure: { ref: "measure:schedule_risk" } }, calls: [call(["ask.plan"])], took_ms: 1200 })
          + sse("side", { side: "before", status: "ok", planner: "llm", confidence: 0.6, reading: "Open projects by Region", spec: { measure: { ref: "measure:open_projects" } }, calls: [call([])], took_ms: 1100 })
          + sse("complete", {}),
          { status: 200, headers: { "Content-Type": "text/event-stream" } });
      }
      return undefined;
    });
    const user = userEvent.setup();
    renderAt("/admin/agents/AG-03");
    const card = await screen.findByRole("article", { name: "Prompt Query planner" });
    await user.click(within(card).getByRole("button", { name: "Edit" }));
    const tryIt = within(card).getByRole("group", { name: "Try it" });
    await user.type(within(tryIt).getByLabelText("Question to try"), "where are we slipping");
    await user.click(within(tryIt).getByRole("button", { name: "Try it" }));
    const draft = await within(tryIt).findByRole("region", { name: "Your draft result" });
    expect(within(draft).getByText("Schedule risk by Region")).toBeInTheDocument();
    expect(within(tryIt).getByRole("region", { name: "Saved result" })).toHaveTextContent("Open projects by Region");
    expect(within(tryIt).getByText(/On TPG\. Different results\./)).toBeInTheDocument();
    const sent = calls.find((c) => c.url.endsWith("/try"))!;
    expect(sent.body).toEqual({ question: "where are we slipping", prompt: { key: "ask.plan", text: "Plan PMO questions exactly." } });
    expect(calls.some((c) => c.method === "PUT")).toBe(false);
  });

  it("offers Try it only for agents whose work it can run", async () => {
    serve((url) => (url.endsWith("/api/v1/admin/agents/AG-08") ? json({ ...DETAIL, code: "AG-08", name: "Narrator", prompts: [prompt({ key: "ask.narrate", agent: "AG-08", title: "Answer narration" })] }) : undefined));
    const user = userEvent.setup();
    renderAt("/admin/agents/AG-08");
    const card = await screen.findByRole("article", { name: "Prompt Answer narration" });
    await user.click(within(card).getByRole("button", { name: "Edit" }));
    expect(within(card).queryByRole("group", { name: "Try it" })).not.toBeInTheDocument();
  });
});

