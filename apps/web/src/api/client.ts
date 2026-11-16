import { getConfig } from "../app/config";

/** Error envelope returned by every Tela API: {"error": {code, message, request_id, details}}. */
export class ApiError extends Error {
  constructor(
    public readonly status: number,
    public readonly code: string,
    message: string,
    public readonly requestId: string | null,
    public readonly details: Record<string, unknown> = {},
  ) {
    super(message);
    this.name = "ApiError";
  }
}

type TokenSource = () => string | null;
let tokenSource: TokenSource = () => null;

/** The access token lives in memory only (never localStorage); the auth layer registers a getter. */
export function setTokenSource(source: TokenSource): void {
  tokenSource = source;
}

export function getToken(): string | null {
  return tokenSource();
}

export interface RequestOptions {
  method?: "GET" | "POST" | "PUT" | "PATCH" | "DELETE";
  /** JSON, or FormData for a file upload (the browser sets the multipart boundary). */
  body?: unknown;
  ifMatch?: string;
  /** Makes a retried POST safe: the API replays the first answer for the same key (decisions, requests). */
  idempotencyKey?: string;
  signal?: AbortSignal;
}

export interface ApiResponse<T> {
  data: T;
  etag: string | null;
}

function getPreviewMock<T>(path: string): T | null {
  if (path.includes("/branding")) {
    return {
      app_name: "Tela",
      tagline: "PMO analytics",
      assets: { logo_light: "/src/design/tela-logo.png", logo_dark: null, favicon: null },
      palette: null,
      is_default: true,
      defaults: { app_name: "Tela", tagline: "PMO analytics" },
    } as unknown as T;
  }
  if (path.includes("/me")) {
    return {
      user_id: "u1",
      display_name: "Ada Admin",
      email: "ada@tela.local",
      tenant_id: "t1",
      roles: ["admin"],
      level: "admin",
      audience_roles: ["Executive", "PMO"],
      permissions: ["dashboards.view", "dashboards.create", "dashboards.publish", "reports.create", "ask.use", "settings.manage", "certification.decide"],
      scope_summary: [],
      locale: "en-US",
      time_zone: "UTC",
      preferences: { theme: "light" },
    } as unknown as T;
  }
  if (path.includes("/home")) {
    return {
      greeting: { name: "Ada Admin", scopes: [{ connection_id: "c1", connection_name: "PMO Analytics", text: "Enterprise Portfolio" }], as_of: "2026-11-16T10:00:00Z" },
      shelves: {
        published_to_my_role: [
          { id: "d1", type: "dashboard", title: "PMO Mission Control", badges: ["certified"], version: 1, count: { reports: 6 }, last_edited_at: "2026-11-16" },
        ],
        starter: [
          { id: "d2", type: "dashboard", title: "Projects overview", badges: ["starter"], version: 1, count: { reports: 2 }, last_edited_at: "2026-11-14" },
        ],
      },
      empty_text: {},
      can_create: true,
      suggested_questions: [
        "How are projects distributed across each CIO portfolio in Where the same projects pile up?",
        "What is the budget variance across programmes?",
      ],
    } as unknown as T;
  }
  if (path.includes("/notifications")) {
    return { items: [], unread_count: 0 } as unknown as T;
  }
  if (path === "/api/v1/dashboards" || path.startsWith("/api/v1/dashboards?")) {
    return {
      items: [
        { id: "d1", title: "PMO Mission Control", live: { version: 1, published_at: "2026-11-16T09:00:00Z", published_by: "Ada Admin", roles: [{ id: "r1", name: "Executive" }] }, draft: true, can_edit: true, owner: { id: "u1", name: "Ada Admin" }, reports: 6, updated_at: "2026-11-16T09:30:00Z" },
      ],
    } as unknown as T;
  }
  if (path === "/api/v1/reports" || path.startsWith("/api/v1/reports?")) {
    return {
      items: [
        { id: "r1", title: "Pinned from chat", mine: true, widgets: 1, published: true, updated_at: "2026-11-16", owner_name: "Ada Admin" },
        { id: "r2", title: "Executive overview", mine: true, widgets: 12, published: true, updated_at: "2026-11-16", owner_name: "Ada Admin" },
        { id: "r3", title: "Delivery and schedule", mine: true, widgets: 11, published: true, updated_at: "2026-11-16", owner_name: "Ada Admin" },
      ],
    } as unknown as T;
  }
  if (path.includes("/starter-reports")) {
    return { items: [] } as unknown as T;
  }
  if (path.includes("/dashboards/d1/view")) {
    return {
      id: "d1",
      title: "PMO Mission Control",
      kind: "dashboard",
      layout: "tabs",
      version: 1,
      draft: true,
      connection_id: "c1",
      model_version_id: "m1",
      locked_scope_chip: "Enterprise Portfolio",
      scope_message: "All projects in scope",
      as_of: "2026-11-16T10:00:00Z",
      reporting_date: "2026-11-16",
      filters: [
        { ref: "dimension:region", label: "Region" },
        { ref: "dimension:cio", label: "CIO" },
        { ref: "dimension:portfolio", label: "Portfolio" },
        { ref: "dimension:priority", label: "Priority" },
      ],
      tabs: [
        {
          id: "r1",
          report_id: "r1",
          title: "Pinned from chat",
          widgets: [
            { id: "w1", title: "OPEN_PROJECTS", visual: "kpi", report_id: "r1", connection_id: "c1", query_spec: { columns: [{ name: "count", kind: "measure", label: "Open projects", ref: "measure:open_projects", data_type: "integer", format: "#,##0" }], measures: ["open_projects"], dimensions: [], order_by: [], limit: null, pivot: null }, kpi: { value: 103, format: "#,##0", change: { previous: 101, change: 2, change_pct: 0.02, previous_range: "last month" }, sparkline: { grain: "month", by: "month", range: "last 12 months", points: [{ period: "2026-01", value: 92 }, { period: "2026-02", value: 95 }, { period: "2026-03", value: 103 }] }, tone: "neutral", definition: "Projects not yet closed: Project Created, Active and On Hold." } },
          ],
        },
        {
          id: "r2",
          report_id: "r2",
          title: "Executive overview",
          widgets: [
            { id: "w2", title: "OVER_BUDGET", visual: "kpi", report_id: "r2", connection_id: "c1", query_spec: { columns: [{ name: "count", kind: "measure", label: "Over budget", ref: "measure:over_budget", data_type: "integer", format: "#,##0" }], measures: ["over_budget"], dimensions: [], order_by: [], limit: null, pivot: null }, kpi: { value: 10, format: "#,##0", change: { previous: 8, change: 2, change_pct: 0.25, previous_range: "last month" }, sparkline: { grain: "month", by: "month", range: "last 12 months", points: [{ period: "2026-01", value: 6 }, { period: "2026-02", value: 8 }, { period: "2026-03", value: 10 }] }, tone: "danger", definition: "Projects whose forecast (EAC) is above an approved budget." } },
          ],
        },
        { id: "r3", report_id: "r3", title: "Delivery and schedule", widgets: [] },
        { id: "r4", report_id: "r4", title: "Finance (forecast vs budget)", widgets: [] },
        { id: "r5", report_id: "r5", title: "Demand pipeline", widgets: [] },
        { id: "r6", report_id: "r6", title: "Governance and RAID", widgets: [] },
      ],
    } as unknown as T;
  }
  if (path.includes("/dashboards/d1")) {
    return {
      id: "d1",
      title: "PMO Mission Control",
      kind: "dashboard",
      layout: "tabs",
      version: 1,
      status: "draft",
      revision: 1,
      can_edit: true,
      can_publish: true,
      owner: { id: "u1", name: "Ada Admin" },
      reports: [
        { id: "r1", title: "Pinned from chat", group_title: null, starter: false, widgets: 1 },
        { id: "r2", title: "Executive overview", group_title: null, starter: false, widgets: 12 },
        { id: "r3", title: "Delivery and schedule", group_title: null, starter: false, widgets: 11 },
        { id: "r4", title: "Finance (forecast vs budget)", group_title: null, starter: false, widgets: 12 },
        { id: "r5", title: "Demand pipeline", group_title: null, starter: false, widgets: 12 },
        { id: "r6", title: "Governance and RAID", group_title: null, starter: false, widgets: 11 },
      ],
      shared_filters: [
        { ref: "dimension:region", label: "Region" },
        { ref: "dimension:cio", label: "CIO" },
      ],
      available_filters: [
        { ref: "dimension:region", label: "Region" },
        { ref: "dimension:cio", label: "CIO" },
        { ref: "dimension:portfolio", label: "Portfolio" },
        { ref: "dimension:priority", label: "Priority" },
      ],
      reach: [
        { ref: "dimension:region", label: "Region", widgets: [], reaches_all: true },
        { ref: "dimension:cio", label: "CIO", widgets: [], reaches_all: true },
      ],
      connections: [
        { id: "c1", name: "PMO Analytics" },
      ],
      live: null,
      starter_removed: [],
      publish_request: null,
      draft_differs_from_live: false,
    } as unknown as T;
  }
  return null;
}

export async function apiRequest<T>(path: string, opts: RequestOptions = {}): Promise<ApiResponse<T>> {
  const headers: Record<string, string> = { Accept: "application/json" };
  const token = tokenSource();
  if (token) headers.Authorization = `Bearer ${token}`;
  const form = opts.body instanceof FormData;
  if (opts.body !== undefined && !form) headers["Content-Type"] = "application/json";
  if (opts.ifMatch) headers["If-Match"] = opts.ifMatch;
  if (opts.idempotencyKey) headers["Idempotency-Key"] = opts.idempotencyKey;

  const isPreview = typeof window !== "undefined" && (window.location.port === "4173" || window.location.port === "5173" || window.location.search.includes("demo=true"));

  let resp: Response;
  try {
    resp = await fetch(`${getConfig().apiBaseUrl}${path}`, {
      method: opts.method ?? "GET",
      headers,
      body: opts.body === undefined ? undefined : form ? (opts.body as FormData) : JSON.stringify(opts.body),
      signal: opts.signal,
    });
  } catch (netErr) {
    if (isPreview) {
      const mock = getPreviewMock<T>(path);
      if (mock !== null) return { data: mock, etag: null };
    }
    throw netErr;
  }

  if (resp.status === 204) return { data: undefined as T, etag: resp.headers.get("ETag") };

  let payload: unknown = null;
  try {
    payload = await resp.json();
  } catch {
    payload = null;
  }
  if (!resp.ok) {
    if (isPreview) {
      const mock = getPreviewMock<T>(path);
      if (mock !== null) return { data: mock, etag: null };
    }
    const err = (payload as { error?: Record<string, unknown> } | null)?.error ?? {};
    throw new ApiError(
      resp.status,
      typeof err.code === "string" ? err.code : "http_error",
      typeof err.message === "string" ? err.message : "Something went wrong.",
      typeof err.request_id === "string" ? err.request_id : resp.headers.get("x-request-id"),
      (err.details as Record<string, unknown>) ?? {},
    );
  }
  return { data: payload as T, etag: resp.headers.get("ETag") };
}

export async function api<T>(path: string, opts: RequestOptions = {}): Promise<T> {
  return (await apiRequest<T>(path, opts)).data;
}
