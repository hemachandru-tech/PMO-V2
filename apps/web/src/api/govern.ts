/** Response shapes of the EPIC-07/08 API (personal fields, custom SQL, certification). Hand-written to the API. */
import type { Column } from "./app";

export type FieldKind = "grouping" | "band" | "join_measure";
export type CertKind = FieldKind | "custom_sql" | "freehand";

export interface CatalogField { ref: string; key: string; label: string; data_type: string; column: string }

export interface Catalog {
  connection_id: string;
  model_version_id: string;
  version_no: number | null;
  /** postgres, mysql, sqlserver, excel or csv: picks the editor's SQL dialect. */
  engine: string;
  scope: { summary: string; locked: boolean };
  measures: { ref: string; label: string; plain_rule: string; format: string; entity: string }[];
  dimensions: { ref: string; label: string; entity: string; temporal: boolean }[];
  entities: { key: string; label: string; table: string; fields: CatalogField[] }[];
  links: [string, string][];
}

export interface PersonalField {
  id: string;
  kind: FieldKind;
  name: string;
  key: string;
  connection_id: string;
  model_version_id: string;
  definition: Record<string, unknown>;
  plain_rule: string;
  badge: "certified" | "personal";
  cert_status: string;
  certified_as: string | null;
  broken: boolean;
  broken_reason: string | null;
  updated_at: string;
}

/** Body of POST /fields, PATCH /fields/{id} and the `field` of POST /fields/preview. */
export interface FieldBody {
  kind: FieldKind;
  name: string;
  connection_id: string;
  source_ref?: string;
  groups?: { label: string; values: string[] }[];
  catch_all_label?: string;
  not_set_label?: string;
  case_sensitive?: boolean;
  edges?: number[];
  labels?: string[];
  open_ended_top?: boolean;
  path?: string[];
  agg?: "count" | "count_distinct" | "sum" | "avg" | "min" | "max";
  target?: string | null;
}

/** A field drafted from words (`POST /fields/draft`, or Ask's `build_field` action; US-075). Nothing is saved. */
export interface FieldDraft {
  draft: Partial<FieldBody> & Pick<FieldBody, "kind" | "connection_id">;
  source_label?: string;
  understood?: string[];
  missing?: string[];
}

export interface BandRow { label: string; lower: number; upper: number | null; status: "same" | "renamed" | "new" | "removed" }

/** `POST /fields/{id}/edit-in-words` (US-076): a proposal to apply with PATCH, and what it changes. */
export interface BandEdit {
  proposal: FieldBody;
  changes: string[];
  diff: { before: BandRow[]; after: BandRow[] };
  rule_before: string;
  rule_after: string;
}

export interface FieldPreview {
  kind: FieldKind;
  edge_rules: string;
  as_of: string | null;
  scope_text?: string;
  measure?: string | null;
  buckets?: { label: string; count: number }[];
  value?: number;
  semantics?: string | null;
  hidden?: boolean;
  message?: string;
}

export interface MyRequest {
  id: string;
  item_type: CertKind;
  item_id: string;
  status: "pending" | "certified" | "merged" | "rejected";
  created_at: string;
  name: string;
  decision_note: string | null;
  decision: string | null;
}

export interface SqlCheck { name: string; ok: boolean; code?: string; detail?: string }

export interface SqlCheckResult {
  ok: true;
  checks: SqlCheck[];
  check_token: string;
  warnings: string[];
  follows_page_filters: boolean;
  model_version: number | null;
  expires_in_s: number;
}

export interface SqlRunResult {
  columns: Column[];
  rows: unknown[][];
  row_count: number;
  truncated: boolean;
  as_of: string | null;
  duration_ms: number;
  notes?: string[];
  warnings?: string[];
  badge: "custom_sql";
  suggested_visual: string | null;
  visuals: { suitable: string[]; recommended: string | null; disabled: { visual: string; label?: string; reason: string }[] } | null;
  scope?: { summary: string };
}

export interface SqlDefinition {
  id: string;
  title: string;
  sql: string;
  dialect: string;
  connection_id: string;
  model_version_id: string;
  follows_page_filters: boolean;
  drafted_by_ai: boolean;
  cert_status: string;
  badge: string;
  last_check: { ok: boolean; checks: SqlCheck[]; row_count?: number } | null;
  updated_at: string;
}

export interface QueueItem {
  request_id: string;
  kind: CertKind;
  item_id: string;
  name: string;
  author: { id: string; name: string };
  submitted_at: string;
  age_days: number;
  reason: string;
  trigger: string;
  note: string | null;
  blocks_publish: boolean;
  usage: { widgets: number; reports: number; dashboards: number };
  pending_publishes: string[];
  lookalikes: number;
  dependency_pending: boolean;
  changed_since_requested: boolean;
  status: string;
  connection_id: string | null;
  /** A free-hand rule's question (US-166). */
  question?: string | null;
}

export interface LookAlike {
  ref: string;
  kind: string;
  label?: string;
  similarity: number;
  reasons: string[];
  difference: string;
  result_delta: { item: number | null; certified: number | null; delta: number | null; differs: boolean } | null;
}

export interface CertDetail {
  request_id: string;
  status: string;
  kind: CertKind;
  item_id: string;
  name: string;
  author: { id: string; name: string };
  trigger: string;
  note: string | null;
  submitted_at: string;
  changed_since_requested: boolean;
  rule: { plain: string; rephrased: string | null; blank_handling: string; source: string };
  definition: { kind: CertKind; definition?: Record<string, unknown>; sql?: string; dialect?: string };
  results: {
    error?: string;
    columns?: Column[]; rows?: unknown[][]; row_count?: number; truncated?: boolean;
    buckets?: { label: string; count: number }[]; value?: number; measure?: string | null; semantics?: string | null;
    as_of?: string | null;
    /** A free-hand rule's records (US-166): a sample across the reviewer's scope. */
    sample?: { columns?: Column[]; rows?: unknown[][]; total?: number; error?: string };
  };
  results_scope: string;
  usage: { widgets: { id: string; title: string }[]; reports: { id: string; title: string }[]; dashboards: { id: string; title: string; live: boolean }[]; shared: boolean };
  generated: string | null;
  dependencies: { id: string; status: string; name: string }[];
  lookalikes: LookAlike[];
  lookalike_note: string | null;
  semantic_checks: { check: string; status: "matches" | "differs" | "review_manually" | "info" | "fails"; detail: string; measure?: string; row_difference?: number | null }[];
  can_decide: { allowed: boolean; reason: string | null };
  question?: string | null;
  decisions?: string[];
}

export const KIND_LABEL: Record<CertKind, string> = {
  grouping: "Group values",
  band: "Bands",
  join_measure: "Cross-table measure",
  custom_sql: "Custom SQL",
  freehand: "Free-hand rule",
};
