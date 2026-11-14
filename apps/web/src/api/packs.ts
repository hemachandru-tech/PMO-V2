import { apiRequest, api, getToken } from "./client";
import { getConfig } from "../app/config";

/** Pack Studio (US-232; `/api/v1/packs`). A pack version is a manifest plus knowledge documents. */

export type PackOwner = "system" | "tenant";
export type PackStatus = "draft" | "released" | "retired";

export interface PackCheck {
  path: string;
  message: string;
}

export interface PackChecks {
  valid: boolean;
  errors: PackCheck[];
  warnings: PackCheck[];
}

export interface PackVersion {
  id: string;
  pack_key: string;
  version: string;
  title: string;
  status: PackStatus;
  owner: PackOwner;
  editable: boolean;
  released_at: string | null;
  updated_at: string | null;
  based_on_pack_id: string | null;
  revision: string;
  counts: { entities: number; links: number; measures: number; dimensions: number };
  installed_on: { connection_id: string; name: string }[];
  /** Where a tenant version is in its review (US-238); null for a system pack. */
  review?: PackReviewState | null;
}

export interface PackReviewState {
  state: "draft" | "in_review" | "returned" | "released" | "retired";
  created_by?: string | null;
  submitted_by?: string | null;
  submitted_at?: string | null;
  returned_by?: string | null;
  returned_at?: string | null;
  released_by?: string | null;
  reason?: string | null;
  note?: string | null;
}

export interface PackListItem {
  pack_key: string;
  owner: PackOwner;
  title: string;
  latest: PackVersion;
  versions: PackVersion[];
  installed: number;
}

export interface PackList {
  items: PackListItem[];
  /** Ways the API can create a pack now, and the ones a later story adds (method → story). */
  create: { methods: string[]; planned: Record<string, string> };
}

/** A manifest as written in `manifest.yaml`; sections are edited as plain JSON and checked by the API. */
export interface PackManifest {
  pack_key: string;
  pack_version: string;
  title: string;
  released_at: string;
  signature: { primary_entity?: string; min_confidence?: number; [k: string]: unknown };
  entities: PackEntity[];
  links?: PackLink[];
  measures: PackMeasure[];
  dimensions?: PackDimension[];
  calculators?: Record<string, unknown>[];
  reports_from?: string;
  reports?: Record<string, Record<string, unknown>>;
  dashboards?: Record<string, Record<string, unknown>>;
  unavailable_reports?: Record<string, Record<string, unknown>>;
  [k: string]: unknown;
}

export interface PackField {
  key: string;
  label: string;
  data_type: string;
  columns: string[];
  required?: boolean;
  semantic_type?: string | null;
  sensitivity?: string;
  description?: string | null;
  synonyms?: string[];
  values?: string[];
  [k: string]: unknown;
}

export interface PackEntity {
  key: string;
  label: string;
  grain?: string | null;
  object_candidates: string[];
  primary_key: string[];
  display_fields?: string[];
  synonyms?: string[];
  fields: PackField[];
}

export interface PackLink {
  from_entity: string;
  to_entity: string;
  from_fields: string[];
  to_fields: string[];
  cardinality: string;
  inherits_scope?: boolean;
  [k: string]: unknown;
}

export interface PackMeasure {
  key: string;
  label: string;
  entity: string;
  plain_rule: string;
  expression: Record<string, unknown>;
  definition?: string | null;
  format?: string;
  rule_ref?: string | null;
  synonyms?: string[];
  [k: string]: unknown;
}

export interface PackDimension {
  key: string;
  label: string;
  entity: string;
  field: string;
  is_status?: boolean;
  is_temporal?: boolean;
  sort_order?: string[] | null;
  synonyms?: string[];
  answers?: string[];
  [k: string]: unknown;
}

export interface PackDetail extends PackVersion {
  manifest: PackManifest;
  /** Knowledge document kind → number of items. */
  documents: Record<string, number>;
  checks: PackChecks;
}

export type PackSection = "pack_version" | "title" | "signature" | "entities" | "links" | "measures" | "dimensions" |
  "calculators" | "reports" | "dashboards" | "unavailable_reports";

export interface TryResult {
  measure: string;
  available: boolean;
  value: number | string | null;
  gaps: string[];
  confidence: number;
}

export const listPacks = () => api<PackList>("/api/v1/packs");

export async function getPack(id: string): Promise<{ pack: PackDetail; etag: string }> {
  const r = await apiRequest<PackDetail>(`/api/v1/packs/${id}`);
  return { pack: r.data, etag: r.etag ?? `"${r.data.revision}"` };
}

export const listVersions = (id: string) => api<{ pack_key: string; items: PackVersion[] }>(`/api/v1/packs/${id}/versions`);

export interface CreatePackIn {
  method: string;
  pack_key: string;
  title: string;
  version?: string;
  from_pack_id?: string;
  /** Method-specific choices, e.g. `model`: connection_id, measures, report_ids, dashboard_ids, knowledge. */
  options?: Record<string, unknown>;
}

/** Something a Create method could not take into the draft, with the reason. */
export interface PackLeftOut {
  kind: string;
  key: string;
  reason: string;
  report?: string;
}

export interface CreatedPack extends PackVersion {
  checks: PackChecks;
  left_out?: PackLeftOut[];
  source?: { connection_id: string; model_version_id: string; version_no: number | null; reporting_date: string | null };
}

export const createPack = (body: CreatePackIn) => api<CreatedPack>("/api/v1/packs", { method: "POST", body });

// ---- From an approved model (US-236; `/api/v1/packs/from-model`)

export type PackKnowledgeKind = "glossary" | "dashboard_rules" | "chart_patterns" | "analysis_recipes" | "value_synonyms" | "gold_examples";

export interface ModelSource {
  connection_id: string;
  name: string;
  engine: string;
  model_version_id: string;
  version_no: number | null;
  counts: { entities: number; links: number; measures: number; dimensions: number };
}

/** What a pack from this connection's approved model would hold, and the choices the Create dialog offers. */
export interface ModelContents {
  connection_id: string;
  name: string;
  model_version_id: string;
  version_no: number | null;
  installed_pack: string | null;
  counts: { entities: number; fields: number; links: number; links_not_accepted: number; dimensions: number; personal_fields: number };
  measures: { key: string; label: string; entity: string; status: "certified" | "deprecated" }[];
  knowledge: { kind: PackKnowledgeKind; count: number; available: boolean; reason: string | null }[];
  reports: { id: string; title: string; origin: "user" | "starter" | "copy" }[];
  dashboards: { id: string; title: string; tabs: number }[];
  reporting_date: string | null;
}

export const listModelSources = () => api<{ items: ModelSource[] }>("/api/v1/packs/from-model/models");
export const getModelContents = (connectionId: string) => api<ModelContents>(`/api/v1/packs/from-model/models/${connectionId}`);

export async function savePack(id: string, etag: string, sections: Partial<Record<PackSection, unknown>>) {
  const r = await apiRequest<PackDetail>(`/api/v1/packs/${id}`, { method: "PATCH", body: { sections }, ifMatch: etag });
  return { pack: r.data, etag: r.etag ?? `"${r.data.revision}"` };
}

export const validatePack = (id: string, sections?: Partial<Record<PackSection, unknown>>) =>
  api<PackChecks>(`/api/v1/packs/${id}/validate`, { method: "POST", body: sections ? { sections } : {} });

export const deletePack = (id: string, etag: string) => api<void>(`/api/v1/packs/${id}`, { method: "DELETE", ifMatch: etag });

export const tryMeasure = (id: string, connection_id: string, measure: string) =>
  api<TryResult>(`/api/v1/packs/${id}/try`, { method: "POST", body: { connection_id, measure } });

// ---- One YAML for measures, dimensions, report blueprints and starter dashboards (`/api/v1/packs/{id}/bundle`)

export type BundleMode = "merge" | "replace";
export type BundlePart = "measures" | "dimensions" | "reports" | "tones" | "dashboards";

/** What an import adds, replaces and removes in each part of the draft (keys), and how many stay the same. */
export interface BundleChange { added: string[]; updated: string[]; removed: string[]; unchanged: number }

export interface BundleCheck extends PackChecks { changes: Partial<Record<BundlePart, BundleChange>> }

/** The draft's measures, dimensions, blueprints and dashboards as one YAML file (to edit and import, or as a template). */
export const getBundle = (id: string) => api<{ yaml: string; file_name: string }>(`/api/v1/packs/${id}/bundle`);

/** Checks the file without saving (`dry_run`). */
export const checkBundle = (id: string, yaml: string, mode: BundleMode) =>
  api<BundleCheck>(`/api/v1/packs/${id}/bundle`, { method: "POST", body: { yaml, mode, dry_run: true } });

/** Imports the file on the revision this screen loaded; nothing is saved when any part has an error. */
export async function importBundle(id: string, etag: string, yaml: string, mode: BundleMode) {
  const r = await apiRequest<PackDetail & { checks: BundleCheck }>(`/api/v1/packs/${id}/bundle`, { method: "POST", body: { yaml, mode }, ifMatch: etag });
  return { pack: r.data, etag: r.etag ?? `"${r.data.revision}"` };
}

export function importPack(file: File, packKey?: string) {
  const form = new FormData();
  form.append("file", file);
  if (packKey) form.append("pack_key", packKey);
  return api<PackVersion & { skipped: string[]; checks: PackChecks }>("/api/v1/packs/import", { method: "POST", body: form });
}

/** Downloads the pack as its `packs/<key>/` folder, zipped (the browser saves the file). */
export async function exportPack(id: string, name: string): Promise<void> {
  const token = getToken();
  const resp = await fetch(`${getConfig().apiBaseUrl}/api/v1/packs/${id}/export`, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
  if (!resp.ok) throw new Error("The pack could not be exported.");
  const url = URL.createObjectURL(await resp.blob());
  const a = document.createElement("a");
  a.href = url;
  a.download = `${name}.zip`;
  a.click();
  URL.revokeObjectURL(url);
}


// ---- Release checks, review and versions (US-238)

export interface ReleaseIssue {
  /** Stable for the same check, path and message: a warning's reason is keyed by it. */
  id: string;
  level: "error" | "warning";
  check: string;
  path: string;
  message: string;
}

export interface ReleaseChecks {
  passed: boolean;
  errors: ReleaseIssue[];
  warnings: ReleaseIssue[];
  steps: { key: string; label: string; ran: boolean; errors: number; warnings: number }[];
  facts: {
    connection?: { id: string; name: string };
    confidence?: number;
    compiled?: { measures: number; dimensions: number };
    gold_planned?: number;
    figures?: Record<string, unknown>;
  };
}

export interface PackChange {
  kind: string;
  key: string;
  title: string;
  change: "added" | "changed" | "removed";
  fields?: string[];
  rule?: { before: string | null; after: string | null };
}

export interface PackDiff {
  against: { id: string; version: string } | null;
  changes: PackChange[];
  summary: Record<string, { added: number; changed: number; removed: number }>;
}

export interface PackReview {
  state: PackReviewState | null;
  people: Record<string, string>;
  review: {
    connection_id?: string;
    note?: string | null;
    reasons?: Record<string, string>;
    checks?: ReleaseChecks;
    against?: { id: string; version: string } | null;
  } | null;
  you: { author: boolean; can_submit: boolean; can_withdraw: boolean; can_release: boolean; can_return: boolean; can_open_next: boolean };
}

export const runReleaseChecks = (id: string, connection_id: string) =>
  api<{ checks: ReleaseChecks; diff: PackDiff; revision: string }>(`/api/v1/packs/${id}/release-checks`, { method: "POST", body: { connection_id } });
export const getPackDiff = (id: string) => api<PackDiff>(`/api/v1/packs/${id}/diff`);
export const getPackReview = (id: string) => api<PackReview>(`/api/v1/packs/${id}/review`);
export const submitPack = (id: string, etag: string, body: { connection_id: string; reasons: Record<string, string>; note?: string }) =>
  api<PackVersion>(`/api/v1/packs/${id}/submit`, { method: "POST", body, ifMatch: etag });
export const withdrawPack = (id: string) => api<PackVersion>(`/api/v1/packs/${id}/withdraw`, { method: "POST" });
export const returnPack = (id: string, reason: string) => api<PackVersion>(`/api/v1/packs/${id}/return`, { method: "POST", body: { reason } });
export const releasePack = (id: string) =>
  api<PackVersion & { diff: PackDiff; update_notice: { connections: number; notified: number } }>(`/api/v1/packs/${id}/release`, { method: "POST" });
export const openNextVersion = (id: string, version?: string) =>
  api<PackVersion>(`/api/v1/packs/${id}/next-version`, { method: "POST", body: version ? { version } : {} });
