import { getConfig } from "../app/config";
import { api, apiRequest, getToken } from "./client";

/** A pack's business knowledge (US-233; `/api/v1/packs/{id}/knowledge`): one document per kind, plus attachments. */

export const KNOWLEDGE_KINDS = ["glossary", "value_synonyms", "dashboard_rules", "chart_patterns", "analysis_recipes", "gold_examples"] as const;
export type KnowledgeKind = (typeof KNOWLEDGE_KINDS)[number];

/** The list each kind's document keeps its items in. */
export const ITEM_LIST: Record<KnowledgeKind, string> = {
  glossary: "terms", value_synonyms: "values", dashboard_rules: "rules", chart_patterns: "patterns",
  analysis_recipes: "recipes", gold_examples: "examples",
};

export interface KnowledgeProblem { path: string; message: string }
export interface KnowledgeItemCheck {
  index: number;
  title: string;
  refs: { measures: string[]; dimensions: string[]; fields: string[] };
  /** "measure:burn_rate", "dimension:x", "field:projects.nope" — names the pack doesn't have. */
  missing: string[];
  errors: KnowledgeProblem[];
  warnings: KnowledgeProblem[];
}
export interface KnowledgeChecks {
  valid: boolean;
  errors: KnowledgeProblem[];
  warnings: KnowledgeProblem[];
  document_errors: KnowledgeProblem[];
  items: KnowledgeItemCheck[];
}
export interface Named { key: string; label: string; entity: string | null }
export interface KnowledgeNames { measures: Named[]; dimensions: Named[]; fields: Record<string, string[]> }
export interface KnowledgePack { id: string; key: string; version: string; title: string; status: string; system: boolean; read_only: boolean }

export type KnowledgeDocument = { version?: string; status?: string } & Record<string, unknown>;

export interface KnowledgeKindView {
  pack: KnowledgePack;
  kind: KnowledgeKind;
  list: string;
  stored: boolean;
  document: KnowledgeDocument;
  checks: KnowledgeChecks;
  names: KnowledgeNames;
  etag: string;
}

export interface PackAttachment {
  id: string;
  title: string;
  file_name: string;
  content_type: string;
  size_bytes: number;
  sha256: string;
  created_at: string | null;
  created_by_name?: string | null;
}

export interface KnowledgeOverview {
  pack: KnowledgePack;
  kinds: { kind: KnowledgeKind; stored: boolean; count: number; version: string | null; errors: number; warnings: number; etag: string }[];
  names: KnowledgeNames;
  attachments: PackAttachment[];
}

const base = (packId: string) => `/api/v1/packs/${packId}/knowledge`;

export const knowledgeKey = (packId: string, kind?: KnowledgeKind) =>
  (kind ? ["pack-knowledge", packId, kind] : ["pack-knowledge", packId]) as readonly string[];

export const getKnowledgeOverview = (packId: string) => api<KnowledgeOverview>(base(packId));

export async function getKnowledge(packId: string, kind: KnowledgeKind): Promise<KnowledgeKindView> {
  const { data, etag } = await apiRequest<KnowledgeKindView>(`${base(packId)}/${kind}`);
  return { ...data, etag: etag ?? data.etag };
}

/** Replaces the whole document on the revision it was loaded at (412 when someone saved first). */
export const saveKnowledge = (packId: string, kind: KnowledgeKind, etag: string, document: KnowledgeDocument) =>
  api<KnowledgeKindView>(`${base(packId)}/${kind}`, { method: "PUT", ifMatch: etag, body: { document } });

export const checkKnowledge = (packId: string, kind: KnowledgeKind, document: KnowledgeDocument) =>
  api<KnowledgeChecks>(`${base(packId)}/${kind}/check`, { method: "POST", body: { document } });

export function attachDocument(packId: string, file: File, title?: string) {
  const form = new FormData();
  form.append("file", file);
  if (title) form.append("title", title);
  return api<PackAttachment>(`${base(packId)}/attachments`, { method: "POST", body: form });
}

export const detachDocument = (packId: string, id: string) =>
  api<void>(`${base(packId)}/attachments/${id}`, { method: "DELETE" });

export async function downloadAttachment(packId: string, a: PackAttachment): Promise<void> {
  const token = getToken();
  const resp = await fetch(`${getConfig().apiBaseUrl}${base(packId)}/attachments/${a.id}/file`, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
  if (!resp.ok) throw new Error("The document could not be downloaded.");
  const url = URL.createObjectURL(await resp.blob());
  const link = document.createElement("a");
  link.href = url;
  link.download = a.file_name;
  link.click();
  URL.revokeObjectURL(url);
}
