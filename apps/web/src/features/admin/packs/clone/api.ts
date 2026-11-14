import { api } from "../../../../api/client";
import type { PackAiSuggestions, PackMapping, PackPreview } from "../../../../api/app";
import type { PackChecks, PackVersion } from "../../../../api/packs";

/** Clone a pack onto a connection (US-235; `POST /api/v1/packs/{id}/repoint`, `POST /api/v1/packs` method clone). */

export type UnmappedKind = "entity" | "field" | "measure" | "dimension" | "report";

/** An item the source can't serve: kept as Unavailable, or removed with what reads it (`removes_also`). */
export interface UnmappedItem {
  ref: string;
  kind: UnmappedKind;
  key: string;
  title: string;
  reason: string;
  removes_also?: string[];
}

export interface RepointPreview extends Pick<PackPreview, "pack_id" | "pack" | "version" | "confidence" | "matched" | "mapping" | "mapping_step" | "checks"> {
  title: string;
  /** Source names the clone adds to its candidates, so it matches this source by name next time. */
  candidates: { ref: string; kind: "entity" | "field"; name: string }[];
  unmapped: UnmappedItem[];
}

export interface RenameTo { key?: string; label?: string }

export interface Renames {
  entities: Record<string, RenameTo>;
  fields: Record<string, RenameTo>;
  measures: Record<string, RenameTo>;
}

export const previewRepoint = (packId: string, connectionId: string, mapping: PackMapping | null) =>
  api<RepointPreview>(`/api/v1/packs/${packId}/repoint`, { method: "POST", body: { connection_id: connectionId, ...(mapping ? { mapping } : {}) } });

/** US-201 suggestions; only a released pack has them (the starter-pack route). */
export const aiSuggestions = (packId: string, connectionId: string, mapping: PackMapping) =>
  api<PackAiSuggestions>(`/api/v1/starter-packs/${packId}/ai-suggestions`, { method: "POST", body: { connection_id: connectionId, mapping } });

export interface CloneIn {
  pack_key: string;
  title: string;
  from_pack_id: string;
  connection_id: string;
  mapping: PackMapping;
  remove: string[];
  renames: Renames;
}

export const createClone = ({ pack_key, title, from_pack_id, ...options }: CloneIn) =>
  api<PackVersion & { checks: PackChecks }>("/api/v1/packs", { method: "POST", body: { method: "clone", pack_key, title, from_pack_id, options } });
