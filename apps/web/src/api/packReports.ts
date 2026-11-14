import { api, apiRequest } from "./client";

/** A pack's report blueprints and starter dashboards (US-234; `/api/v1/packs/{id}/reports`). */

export interface BlueprintFilter { ref: string; op: string; values?: (string | number | boolean)[] }
export interface BlueprintKpi { title: string; measure: string; filters?: BlueprintFilter[] }
export interface BlueprintWidget {
  title: string;
  measure: string;
  extra_measures?: string[];
  breakdowns?: string[];
  filters?: BlueprintFilter[];
  time?: Record<string, unknown> | null;
  visual: string;
  note?: string | null;
}
export interface BlueprintTable {
  title: string;
  measure: string;
  columns: string[];
  sort?: { field: string; dir: "asc" | "desc" } | null;
  export_name?: string | null;
}
export interface Blueprint {
  id: string;
  tab?: string | null;
  title: string;
  dashboard: string;
  section?: string | null;
  question?: string | null;
  population?: string | null;
  triggers: string[];
  filters?: BlueprintFilter[];
  kpis?: (string | BlueprintKpi)[];
  widgets?: BlueprintWidget[];
  table?: BlueprintTable | null;
  callout?: { title?: string | null; text: string; level?: "info" | "warning" } | null;
  footnote?: string | null;
  unavailable?: string[];
}
export interface FigureTone { warning: number; critical: number; higher_is_worse?: boolean }
export interface Problem { path: string; message: string }
export interface StarterDashboard { key: string; title: string; reports: string[]; filters: string[] }
export interface Named { key: string; label: string; entity: string }

export interface PackReports {
  pack: { id: string; key: string; version: string; title: string; status: string; system: boolean; read_only: boolean };
  blueprints: Blueprint[];
  tones: Record<string, FigureTone>;
  dashboards: StarterDashboard[];
  /** Problems by blueprint id; the document's own under "". */
  problems: Record<string, Problem[]>;
  names: { measures: Named[]; dimensions: Named[]; fields: Record<string, string[]> };
  etag: string;
}

export interface PreviewWidget { ref: string; title: string; visual: string; measure_label: string; spec: Record<string, unknown> }
export interface PreviewSection {
  key: string;
  title: string;
  kind: string;
  widgets: PreviewWidget[];
  callouts?: { title?: string | null; text: string; level?: string }[];
  footnote?: string | null;
}
export interface Preview {
  problems: Problem[];
  connection?: { id: string; name: string };
  model_version?: number;
  sections: PreviewSection[];
  gaps: { request_part: string; reason: string }[];
  layout_preview: { widget_ref: string; section: string; x: number; y: number; w: number; h: number }[];
  results: Record<string, { columns?: { name: string }[] | string[]; rows: unknown[][]; row_count?: number; hidden?: boolean; message?: string }>;
}

export interface LeftOut { title: string; reason: string; report?: string }
export interface FromReport { blueprint: Blueprint; tones: Record<string, FigureTone>; left_out: LeftOut[]; problems: Problem[]; saved: boolean; reports?: PackReports }
export interface FromDashboard {
  dashboard: StarterDashboard;
  blueprints: Blueprint[];
  left_out: LeftOut[];
  problems: Record<string, Problem[]>;
  saved: boolean;
  reports?: PackReports;
}

const base = (packId: string) => `/api/v1/packs/${packId}/reports`;

export const packReportsKey = (packId: string) => ["pack-reports", packId] as const;

export async function getPackReports(packId: string): Promise<PackReports> {
  const { data, etag } = await apiRequest<PackReports>(base(packId));
  return { ...data, etag: etag ?? data.etag };
}

export const saveBlueprint = (packId: string, etag: string, id: string, blueprint: Blueprint,
  tones: Record<string, FigureTone | null> = {}) =>
  api<PackReports>(`${base(packId)}/blueprints/${encodeURIComponent(id)}`, { method: "PUT", ifMatch: etag, body: { blueprint, tones } });

export const deleteBlueprint = (packId: string, etag: string, id: string) =>
  api<PackReports>(`${base(packId)}/blueprints/${encodeURIComponent(id)}`, { method: "DELETE", ifMatch: etag });

export const saveDashboard = (packId: string, etag: string, d: StarterDashboard) =>
  api<PackReports>(`${base(packId)}/dashboards/${encodeURIComponent(d.key)}`, {
    method: "PUT", ifMatch: etag, body: { title: d.title, reports: d.reports, filters: d.filters },
  });

export const deleteDashboard = (packId: string, etag: string, key: string) =>
  api<PackReports>(`${base(packId)}/dashboards/${encodeURIComponent(key)}`, { method: "DELETE", ifMatch: etag });

export const previewBlueprint = (packId: string, blueprint: Blueprint, connectionId: string) =>
  api<Preview>(`${base(packId)}/preview`, { method: "POST", body: { blueprint, connection_id: connectionId } });

export const blueprintFromReport = (packId: string, etag: string | undefined,
  body: { report_id: string; dashboard?: string; id?: string; save: boolean }) =>
  api<FromReport>(`${base(packId)}/from-report`, { method: "POST", ifMatch: body.save ? etag : undefined, body });

export const blueprintsFromDashboard = (packId: string, etag: string | undefined,
  body: { dashboard_id: string; key?: string; title?: string; save: boolean }) =>
  api<FromDashboard>(`${base(packId)}/from-dashboard`, { method: "POST", ifMatch: body.save ? etag : undefined, body });

/** "projects_overview" or {title, measure} → the measure key of a headline figure. */
export const kpiMeasure = (k: string | BlueprintKpi): string => (typeof k === "string" ? k : k.measure);
