/** Response shapes of P2 authoring and publishing (widgets, reports, plans, dashboards, shares, audit). */
import type { CalloutLevel, TextKind } from "./app";
import type { QuerySpec } from "./querySpec";

export interface VisualOptions {
  suitable: string[];
  disabled: { visual: string; label: string; reason: string }[];
  recommended: string | null;
  /** Per visual, the breakdown/date ops that make it fit a single-figure widget ("by status"). */
  fixes?: Record<string, { label: string; ops: ({ op: string } & Record<string, unknown>)[] }>;
  hidden?: boolean;
}

export const VISUAL_NAMES: Record<string, string> = {
  figure: "Figure", ranked_bar: "Ranked bars", columns: "Columns", trend: "Trend", paired_bars: "Paired bars",
  stacked_status: "Stacked status bars", donut: "Donut", heat_grid: "Heat grid", meter: "Meter", dumbbell: "Dumbbell",
  waterfall: "Waterfall", bullet: "Bullet", pareto: "Pareto", xmr: "Control chart", gantt: "Gantt", funnel: "Funnel",
  small_multiples: "Small multiples", calendar: "Calendar heatmap", sankey: "Sankey", upset: "UpSet", beeswarm: "Beeswarm",
  ridgeline: "Ridgeline", survival: "Time to event", bump: "Bump chart", treemap: "Treemap", waffle: "Waffle", table: "Table",
};

export interface LibraryWidget {
  id: string;
  title: string;
  visual: string;
  badges: string[];
  spec: QuerySpec;
  question: string | null;
  model_version_id: string;
  connection_id: string;
  follow_page_filters: boolean;
  used_on_reports: number;
}

export interface PinResult extends LibraryWidget {
  reused: boolean;
  placement: { item_id: string; section_id: string; x: number; y: number; w: number; h: number } | null;
  report: { id: string; title: string; revision: number } | null;
}

export interface ReportItem {
  id: string;
  section_id: string;
  kind: "widget" | TextKind;
  x: number;
  y: number;
  w: number;
  h: number;
  empty_expected: boolean;
  hidden_for_roles: string[];
  text?: string;
  /** A callout's level (US-158); its title is `title`. */
  level?: CalloutLevel;
  widget_id?: string;
  title?: string | null;
  visual?: string;
  badges?: string[];
  measure?: string;
  follow_page_filters?: boolean;
  own_filters?: unknown[];
  connection_id?: string;
  model_version_id?: string;
  spec?: QuerySpec;
}

export interface ReportSection {
  id: string;
  title: string;
  kind: "headline" | "normal";
  position: number;
  collapsed: boolean;
  items: ReportItem[];
}

export interface Report {
  id: string;
  title: string;
  page_filters: { ref: string }[];
  revision: number;
  owner: { id: string | null; name: string | null };
  can_edit: boolean;
  published: boolean;
  updated_at: string;
  plan_id: string | null;
  /** The model's reporting date (US-158 "Data as of"), when every widget shares one. */
  reporting_date?: string | null;
  /** "starter" for pack-owned reports (read-only), "copy" for a person's copy of one. */
  origin?: "user" | "starter" | "copy";
  copied_from_id?: string | null;
  in_dashboards: { id: string; title: string }[];
  can_undo: boolean;
  undo_summary: string | null;
  sections: ReportSection[];
  item_id?: string;
  section_id?: string;
  undone?: string;
}

export interface ReportListItem {
  id: string;
  title: string;
  updated_at: string;
  widgets: number;
  published: boolean;
  mine: boolean;
  owner_name: string;
}

export interface PlannedWidget {
  ref: string;
  title: string;
  visual: string;
  certified: boolean;
  spec: QuerySpec;
  measure_label: string;
}

/** A plan section; a report catalogue screen (US-159) brings its question, population, callouts and footnote. */
export interface PlanSection {
  key: string;
  title: string;
  kind: "headline" | "normal";
  widgets: PlannedWidget[];
  blueprint?: { id: string; title: string; question: string | null; population: string | null };
  callouts?: { title: string | null; text: string; level: "info" | "warning" }[];
  footnote?: string | null;
}

export interface ReportPlan {
  plan_id: string;
  title: string;
  connection_id: string;
  connection: string;
  model_version: number | null;
  headline_figures: { ref: string; measure: string; label: string }[];
  sections: PlanSection[];
  page_filters: { ref: string; label: string }[];
  gaps: { request_part: string; reason: string }[];
  layout_preview: { widget_ref: string; section: string; x: number; y: number; w: number; h: number }[];
  diff: { added: string[]; removed: string[] } | null;
  notes?: string[];
  status?: string;
  report_id?: string | null;
  /** US-243: the LLM's review of the rules plan (absent when review is off). */
  review?: PlanReview;
}

export interface PlanReview {
  verdict: "confirm" | "correct" | "ask" | "fallback";
  reason: string;
  /** What the AI changed against the rules plan, in plain words; empty when the rules plan stands. */
  changes: string[];
  added: string[];
  removed: string[];
  /** AI suggestions left out (unknown keys, widgets outside your scope …). */
  dropped: string[];
  showing: "ai" | "rules";
}

export interface BuildResult {
  report_id: string;
  results: { widget_ref: string; widget_id: string; title: string; status: "ok" | "empty" | "failed" | "hidden"; rows?: number; message?: string }[];
}

export interface ChatChange {
  op: string;
  target?: string;
  item_id?: string | null;
  summary?: string;
  request?: string;
  reason?: string;
  alternative?: string | null;
  alternative_label?: string | null;
  /** "ai": the LLM review added this change (or refused its part of it); absent for the rules' own (US-244). */
  by?: "ai";
}

export interface ChatClarifyOption {
  item_id: string | null;
  title: string;
  /** An instruction the rules read exactly (the AI's question, US-244); absent: swap the phrase for the title. */
  instruction?: string;
}

export interface ChatPreview {
  preview_id: string | null;
  changes: ChatChange[];
  highlights: string[];
  clarification: { question: string; options: ChatClarifyOption[]; why?: string[] } | null;
  /** The LLM review's verdict (US-244); null or absent when review is off or skipped. */
  review?: { verdict: "confirm" | "correct" | "ask" | "fallback"; reason: string; notes: string[] } | null;
}

export interface ReachWidget {
  widget_id: string;
  item_id: string;
  title: string;
  report: string;
  reached: boolean;
  excluded: boolean;
  message: string | null;
}

export interface SharedFilter {
  ref: string;
  exclude_widget_ids?: string[];
}

export interface DashboardDetail {
  id: string;
  title: string;
  layout: "tabs" | "grouped";
  status: string;
  owner: { id: string; name: string | null };
  can_edit: boolean;
  can_publish: boolean;
  shared_filters: SharedFilter[];
  reach: { ref: string; label: string; widgets: ReachWidget[]; reaches_all: boolean }[];
  available_filters: { ref: string; label: string }[];
  reports: { id: string; title: string; group_title: string | null; widgets: number; starter?: boolean }[];
  /** Starter reports an Admin deleted that the live version still shows: republish to apply (RULE-017). */
  starter_removed?: { id: string; title: string }[];
  connections: { id: string; name: string }[];
  live: { version_id: string; version: number; published_at: string; published_by: string; roles: RoleRef[] } | null;
  draft_differs_from_live: boolean;
  revision: number;
  /** The latest publish request (owner and Admins only). */
  publish_request: PublishRequest | null;
}

export interface PublishRequest {
  id: string;
  dashboard: { id: string; title: string };
  requested_by: { id: string; name: string | null };
  roles: RoleRef[];
  note: string | null;
  status: "open" | "published" | "declined" | "withdrawn";
  created_at: string;
  decided_by: string | null;
  decided_at: string | null;
  decision_note: string | null;
  version: number | null;
}

export interface DashboardListItem {
  id: string;
  title: string;
  layout: string;
  status: string;
  updated_at: string;
  owner_name: string;
  live_version: number | null;
  reports: number;
  mine: boolean;
}

/** A dashboard inside a group, as this person may open it (ENH-009, US-169): `link` is the published view if they can read it, else the draft. */
export interface DashboardGroupLink {
  id: string;
  title: string;
  kind: "starter" | "user";
  link: string;
  published: boolean;
}

/** `GET /me/dashboard-groups`: the sidebar's groups, only with dashboards this person can open; empty groups left out. */
export interface MyDashboardGroup {
  id: string;
  name: string;
  icon: string | null;
  dashboards: DashboardGroupLink[];
}

/** `GET /dashboard-groups` (`dashboards.group`): `hidden_count` items in the group are ones this person cannot open. */
export interface DashboardGroup extends MyDashboardGroup {
  position: number;
  created_by: { id: string; name: string };
  can_edit: boolean;
  hidden_count: number;
}

export interface DashboardGroupsResponse {
  items: DashboardGroup[];
  /** Only an Admin changes the order of the groups (it is everyone's sidebar). */
  can_reorder: boolean;
}

export interface RoleRef {
  id: string;
  key: string;
  name: string;
  kind?: string;
  /** Active people who hold the role (from `GET /roles`). */
  members?: number;
}

export interface CheckFix {
  action: string;
  available?: boolean;
  note?: string;
  role?: string;
  role_id?: string;
  filter?: string;
  /** For `request_certification`: the personal fields or custom SQL the widget uses. */
  items?: { kind: string; id: string }[];
}

export interface CheckItem {
  widget_id?: string;
  item_id?: string;
  title?: string;
  report?: string;
  report_id?: string;
  role?: string;
  role_name?: string;
  filter?: string;
  message: string;
  fixes: CheckFix[];
}

export interface Prechecks {
  passed: boolean;
  checks: { check: string; label: string; passed: boolean; items: CheckItem[] }[];
  notes: string[];
  roles: RoleRef[];
}

export interface PreviewResult {
  as: { role?: string; user_id?: string; name: string };
  scope_text: string;
  rows_in_scope: Record<string, number> | null;
  counts_note: string | null;
  widgets: { widget_id: string; item_id: string; title: string; shown: boolean; reason: string | null; rows?: number }[];
}

export interface Version {
  id: string;
  version: number;
  state: "live" | "superseded" | "withdrawn";
  note: string | null;
  change_summary: string | null;
  published_at: string;
  published_by: string;
  restored_from: number | null;
  requested_by: string | null;
  roles: string[];
}

export interface PublishOut {
  version: number;
  notified: number;
  change_summary: string;
  roles: RoleRef[];
  request_id: string | null;
}

export interface ShareWarning {
  user_id: string;
  name: string;
  connection: string;
  message: string;
}

export interface Share {
  id: string;
  object: { kind: string; id: string };
  user: { id: string; name: string | null };
  permission: string;
  created_at: string;
}

export interface NotificationItem {
  id: string;
  kind: string;
  title: string;
  body: string;
  link: string | null;
  read_at: string | null;
  created_at: string;
}

export interface AuditItem {
  id: number;
  at: string;
  actor: { id: string | null; name: string | null };
  action: string;
  object: { type: string; id: string | null; link: string | null };
  outcome: string;
  security: boolean;
  request_id: string;
  details: Record<string, unknown>;
}

/** `GET /admin/audit/facets` (US-146): counts per value, each under every other filter but its own. */
export interface AuditFacets {
  action: { value: string; count: number }[];
  object_type: { value: string; count: number }[];
  outcome: { value: string; count: number }[];
  actor: { value: string; count: number }[];
  security: number;
  total: number;
}

/** `GET /notifications` (US-107, US-146): newest first, paged by cursor, with counts per kind. */
export interface NotificationsPage {
  items: NotificationItem[];
  unread_count: number;
  next_cursor: string | null;
  kinds: { kind: string; count: number; unread: number }[];
}

/** `GET /admin/llm-settings` (API-053; US-106/US-146). The API key is write-only: only `key_set` comes back. */
export interface LlmSettings {
  provider: string | null;
  models: Partial<Record<"planner" | "narrator" | "advisor" | "embeddings", string>>;
  allow_samples: boolean;
  allow_aggregates: boolean;
  monthly_token_budget: number;
  rates: Record<string, { input_per_mtok: number; output_per_mtok: number }>;
  key_set: boolean;
  /** Azure OpenAI resource, e.g. `https://acme.openai.azure.com` (DEC-01). */
  endpoint?: string | null;
  last_test: { ok: boolean; tested_at: string; latency_ms?: number | null; model?: string | null; code?: string; message?: string } | null;
  allowed_providers: string[];
  /** Providers that need an API key; each keeps its own key. */
  keyed_providers?: string[];
  /** Providers with an embeddings service; others use the deployment's local embeddings. */
  embedding_providers?: string[];
  /** The deployment lets aggregates reach a model (DEC-01); the tenant toggle still decides. */
  aggregates_allowed?: boolean;
  /** Agents send their rules draft to the model for review (US-239, DEC-28). */
  llm_review?: boolean;
  /** The switch's default for the saved provider: on for hosted, off for local. */
  llm_review_default?: boolean;
  /** AG-03 fit (US-191, DEC-23): local or hosted planning, the model for it, its schema failures over `days`. */
  planner_health?: PlannerHealth;
}

export interface PlannerHealth {
  option: "local" | "hosted" | null;
  provider: string | null;
  model: string | null;
  days: number;
  calls: number;
  schema_failures: number;
  timeouts: number;
  failure_rate: number | null;
  warn: boolean;
  recommended: string | null;
  minimum: string | null;
}

/** `GET /admin/usage` (API-054): the metadata-only ledger aggregated by model, user, task or day. */
export interface LlmUsage {
  group_by: "model" | "user" | "task" | "day";
  days: number;
  items: {
    key: string;
    calls: number;
    prompt_tokens: number;
    completion_tokens: number;
    blocked: number;
    failed: number;
    estimated_cost: number;
    p50_ms: number | null;
    p95_ms: number | null;
  }[];
  cost_note: string;
}

/** A starter report the person may put on a dashboard or copy (`GET /starter-reports`, REQ-114). */
export interface StarterReport {
  id: string;
  title: string;
  key: string;
  widgets: number;
  connection: { id: string; name: string };
  copied: boolean;
}

/** One starter report on Admin → Starter content (API-047, FEAT-046). */
export interface StarterItem {
  id: string;
  key: string;
  title: string;
  state: "active" | "hidden" | "deleted";
  availability: "available" | "unavailable";
  gaps: string[];
  report_id: string | null;
  connection: { id: string; name: string };
  pack: { key: string; version: string };
  widgets: number;
  copies: number;
  dashboards: number;
  visibility: { role_id: string; key: string; name: string; visible: boolean }[];
  updated_at: string;
}

export interface StarterDashboardEffect {
  dashboard_id: string;
  title: string;
  owner: string | null;
  live_version_stays: number | null;
}

/** What deleting a starter report does (RULE-017), from `DELETE /admin/starter/{id}?dry_run=true`. */
export interface StarterEffects {
  user_copies_unaffected: number;
  dashboards_getting_new_draft: StarterDashboardEffect[];
  drafts_without_it: StarterDashboardEffect[];
  starter_dashboards: { dashboard_id: string; title: string }[];
}

/** One thing a newer pack version adds, changes or removes on a connection (API-048, FEAT-047). */
export interface PackChange {
  kind: "report" | "measure" | "dimension" | "entity" | "field";
  key: string;
  title: string;
  change: "added" | "changed" | "removed";
}

/** A newer starter pack version for one connection's model, from `GET /admin/packs/updates` (API-048). */
export interface PackUpdate {
  pack_id: string;
  pack_key: string;
  title: string;
  installed: string;
  available: string;
  released_at: string;
  model_id: string;
  connection: { id: string; name: string };
  /** "awaiting_approval": applied, and its measure changes wait in the model draft. */
  status: "available" | "awaiting_approval";
  pending: { version: string; draft_version_id: string } | null;
  changes: PackChange[];
  summary: string;
  /** Measures, dimensions or fields change, so the update goes through a model draft that needs approval. */
  model_draft_required: boolean | null;
  user_copies_affected: number;
  user_copies_unaffected: number;
  dashboards_getting_new_draft: (StarterDashboardEffect & { report: string; change: PackChange["change"] })[];
  error: string | null;
}
