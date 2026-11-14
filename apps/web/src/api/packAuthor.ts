import { api, apiRequest } from "./client";
import type { PackChecks, PackDetail } from "./packs";
import { streamPost, type SseEvent } from "./sse";

/** The AI pack author (US-237; `/api/v1/packs/{id}/author`): stages drafted by rules and the model, reviewed item by
 * item. Nothing reaches the draft until an item is accepted. */

export type AuthorStage = "entities" | "links" | "measures" | "dimensions" | "synonyms" | "knowledge";
export type AuthorKnowledgeKind = "glossary" | "value_synonyms" | "dashboard_rules" | "chart_patterns" | "analysis_recipes";
export type AuthorKind = "entity" | "link" | "measure" | "dimension" | "synonyms" | AuthorKnowledgeKind;

export interface AuthorTemplate { id: string; pack_key: string; version: string; title: string }

export interface AuthorOverview {
  ai: boolean;
  provider: string | null;
  /** DEC-27: frequent values of text columns may go to the (local) model. */
  samples: boolean;
  templates: AuthorTemplate[];
  template_id: string | null;
  tables: { qualified_name: string; columns: number; in_draft: boolean }[];
  stages: { stage: AuthorStage; count: number | null }[];
  knowledge: { kind: AuthorKnowledgeKind; count: number }[];
}

export interface AuthorProblem { path: string; message: string }

export interface AuthorProposal {
  id: string;
  kind: AuthorKind;
  key: string;
  title: string;
  item: Record<string, unknown>;
  source: "ai" | "rules";
  reason: string;
  checks: { errors: AuthorProblem[]; warnings: AuthorProblem[]; count: number | string | null; count_of: "rows" | "value" | "values" | "bands" | null };
}

export interface AuthorStageInfo {
  stage: AuthorStage;
  kind: AuthorKind;
  ai: boolean;
  provider: string | null;
  samples: boolean;
  template: AuthorTemplate | null;
}

export interface AuthorRequest {
  connection_id: string;
  stage: AuthorStage;
  kind?: AuthorKnowledgeKind;
  template_pack_id?: string | null;
  tables?: string[];
  entities?: string[];
}

export interface AuthorHandlers {
  onStage?: (s: AuthorStageInfo) => void;
  onProposal?: (p: AuthorProposal) => void;
  onDropped?: (p: AuthorProposal) => void;
  onNote?: (message: string) => void;
  onComplete?: (c: { proposed: number; dropped: number; sources: string[] }) => void;
}

export const getAuthorOverview = (packId: string, connectionId: string) =>
  api<AuthorOverview>(`/api/v1/packs/${packId}/author?connection_id=${encodeURIComponent(connectionId)}`);

/** Runs one stage; resolves when the stream ends. An `error` event rejects with its message. */
export async function streamAuthor(packId: string, body: AuthorRequest, on: AuthorHandlers, signal?: AbortSignal): Promise<void> {
  let failure: string | null = null;
  await streamPost(`/api/v1/packs/${packId}/author`, body, (e: SseEvent) => {
    if (e.event === "stage") on.onStage?.(e.data as AuthorStageInfo);
    else if (e.event === "proposal") on.onProposal?.(e.data as AuthorProposal);
    else if (e.event === "dropped") on.onDropped?.(e.data as AuthorProposal);
    else if (e.event === "note") on.onNote?.((e.data as { message: string }).message);
    else if (e.event === "complete") on.onComplete?.(e.data as { proposed: number; dropped: number; sources: string[] });
    else if (e.event === "error") failure = (e.data as { message: string }).message;
  }, { signal });
  if (failure) throw new Error(failure);
}

export interface CheckedItem {
  index: number;
  item: Record<string, unknown>;
  errors: AuthorProblem[];
  warnings: AuthorProblem[];
  count: number | string | null;
  count_of: AuthorProposal["checks"]["count_of"];
}

export const checkAuthorItems = (packId: string, connectionId: string, kind: AuthorKind, items: Record<string, unknown>[]) =>
  api<{ valid: boolean; items: CheckedItem[] }>(`/api/v1/packs/${packId}/author/check`, { method: "POST", body: { connection_id: connectionId, kind, items } });

/** Adds accepted items to the draft (checked again on the server). Manifest items need the pack's revision. */
export async function acceptAuthorItems(packId: string, etag: string, connectionId: string, kind: AuthorKind,
  items: Record<string, unknown>[], sources: string[]): Promise<{ pack: PackDetail; etag: string; checks: PackChecks }> {
  const r = await apiRequest<PackDetail & { checks: PackChecks; accepted: number }>(`/api/v1/packs/${packId}/author/accept`, {
    method: "POST", ifMatch: etag, body: { connection_id: connectionId, kind, items, sources },
  });
  return { pack: r.data, etag: r.etag ?? `"${r.data.revision}"`, checks: r.data.checks };
}
