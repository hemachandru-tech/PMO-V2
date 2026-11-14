import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { api, type ApiError } from "../../../api/client";
import type { TestResult } from "../../../api/app";
import { Alert, FormAlert } from "../../../components/ui/alert";
import { Badge } from "../../../components/ui/badge";
import { Button } from "../../../components/ui/button";
import { Checkbox } from "../../../components/ui/checkbox";
import { Input } from "../../../components/ui/input";
import { Label } from "../../../components/ui/label";
import { NativeSelect } from "../../../components/ui/native-select";
import { Skeleton } from "../../../components/ui/skeleton";
import { asApiError } from "../../../lib/api-errors";

/** Excel and CSV sources (US-108; FEAT-011; DEC-07): upload, confirm sheets and types, replace with a diff. */

const TYPES = ["text", "integer", "decimal", "date", "datetime", "boolean"] as const;
const FLAG_TEXT: Record<string, string> = { mixed_types: "Mixed types", formula_only: "Formulas without values", empty: "Empty", array: "List" };

export function FileUploadStep({ onBack, onTested }: { onBack: () => void; onTested: (id: string, r: TestResult) => void }) {
  const [name, setName] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [retention, setRetention] = useState<"replaced_plus_30d" | "fixed_days">("replaced_plus_30d");
  const [days, setDays] = useState("90");
  const [classification, setClassification] = useState("confidential");
  const [problem, setProblem] = useState<string | null>(null);
  const upload = useMutation({
    mutationFn: async () => {
      const body = new FormData();
      body.append("file", file!);
      body.append("name", name.trim());
      body.append("retention_policy", retention);
      if (retention === "fixed_days") body.append("retention_days", days);
      body.append("classification", classification);
      const created = await api<{ connection_id: string }>("/api/v1/connections/files", { method: "POST", body });
      return { id: created.connection_id, result: await api<TestResult>(`/api/v1/connections/${created.connection_id}/test`, { method: "POST" }) };
    },
    onSuccess: ({ id, result }) => onTested(id, result),
  });
  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!name.trim()) return setProblem("Enter a name people will see.");
    if (!file) return setProblem("Choose a .xlsx or .csv file.");
    if (!/\.(xlsx|csv)$/i.test(file.name)) return setProblem("Only .xlsx or .csv up to 50 MB, without macros or passwords.");
    if (file.size > 50 * 1024 * 1024) return setProblem("The file is larger than 50 MB.");
    if (retention === "fixed_days" && !(Number(days) >= 1 && Number(days) <= 3650)) return setProblem("Keep the file between 1 and 3,650 days.");
    setProblem(null);
    upload.mutate();
  };
  return (
    <form className="grid gap-4" onSubmit={submit} aria-labelledby="file-title" noValidate>
      <h2 id="file-title" className="text-heading">Connection details · Excel or CSV</h2>
      <p className="text-small text-ink-muted">The file becomes a governed source: only people the data rules allow can see its rows.</p>
      <div className="grid gap-4 sm:grid-cols-2">
        <div className="grid gap-1.5"><Label htmlFor="file-name">Name people will see</Label>
          <Input id="file-name" value={name} maxLength={80} onChange={(e) => setName(e.target.value)} /></div>
        <div className="grid gap-1.5"><Label htmlFor="file-input">File</Label>
          <Input id="file-input" type="file" accept=".xlsx,.csv" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
          <p className="text-small text-ink-muted">.xlsx or .csv, up to 50 MB, without macros or passwords.</p></div>
        <div className="grid gap-1.5"><Label htmlFor="file-retention">Keep the file</Label>
          <NativeSelect id="file-retention" value={retention} onChange={(e) => setRetention(e.target.value as typeof retention)}>
            <option value="replaced_plus_30d">Until it is replaced, then 30 days</option>
            <option value="fixed_days">For a set number of days</option>
          </NativeSelect></div>
        {retention === "fixed_days" && (
          <div className="grid gap-1.5"><Label htmlFor="file-days">Days</Label>
            <Input id="file-days" inputMode="numeric" value={days} onChange={(e) => setDays(e.target.value)} /></div>
        )}
        <div className="grid gap-1.5"><Label htmlFor="file-class">Classification</Label>
          <NativeSelect id="file-class" value={classification} onChange={(e) => setClassification(e.target.value)}>
            <option value="internal">Internal</option>
            <option value="confidential">Confidential</option>
            <option value="restricted">Restricted</option>
          </NativeSelect></div>
      </div>
      {problem && <Alert variant="danger"><p>{problem}</p></Alert>}
      <FormAlert error={upload.error ? asApiError(upload.error) : null} />
      <div className="flex flex-wrap gap-2">
        <Button type="button" variant="ghost" onClick={onBack}>Back</Button>
        <Button type="submit" disabled={upload.isPending}>{upload.isPending ? "Uploading…" : "Upload and test"}</Button>
      </div>
    </form>
  );
}

type Column = { name: string; header: string; type: string; inferred: string; flags: string[]; personal: boolean };
type Sheet = { sheet: string; table: string; include: boolean; header_row: number; rows: number; merged_cells: number; columns: Column[] };
type Version = { version: number; status: string; filename: string; sheets: Sheet[]; retention_until: string | null; uploaded_at: string };
type Diff = { added: string[]; removed: string[]; changed: { object: string; removed_columns: string[]; added_columns: string[]; retyped: string[] }[];
  breaks_model: { entity: string; problem: string; columns?: string[] }[] };

/** Confirm sheets, header rows, column types and personal-data columns, then re-test and re-read the schema. */
export function SheetSettings({ id, onSaved }: { id: string; onSaved: () => void }) {
  const qc = useQueryClient();
  const files = useQuery({ queryKey: ["connection-files", id], queryFn: () => api<{ items: Version[] }>(`/api/v1/connections/${id}/files`) });
  const active = files.data?.items.find((v) => v.status === "active");
  const [sheets, setSheets] = useState<Sheet[]>([]);
  useEffect(() => { if (active) setSheets(active.sheets); }, [active?.version, active?.sheets.length]); // eslint-disable-line react-hooks/exhaustive-deps
  const save = useMutation({
    mutationFn: async () => {
      await api(`/api/v1/connections/${id}/files/settings`, { method: "PUT", body: { sheets: sheets.map((s) => ({
        sheet: s.sheet, include: s.include, header_row: s.header_row,
        columns: s.columns.map((c) => ({ header: c.header, type: c.type, personal: c.personal })) })) } });
      await api(`/api/v1/connections/${id}/test`, { method: "POST" });
      return api(`/api/v1/connections/${id}/introspect`, { method: "POST" });
    },
    onSuccess: () => { void qc.invalidateQueries({ queryKey: ["connection-files", id] }); void qc.invalidateQueries({ queryKey: ["connection-objects", id] }); onSaved(); },
  });
  if (files.isLoading) return <Skeleton className="h-32" />;
  if (!active || !sheets.length) return null;
  const set = (i: number, patch: Partial<Sheet>) => setSheets((all) => all.map((s, j) => (j === i ? { ...s, ...patch } : s)));
  const setCol = (i: number, k: number, patch: Partial<Column>) =>
    set(i, { columns: (sheets[i]?.columns ?? []).map((c, j) => (j === k ? { ...c, ...patch } : c)) });
  return (
    <section className="grid gap-3" aria-labelledby="sheets-title">
      <h3 id="sheets-title" className="text-body-strong">Confirm the sheets · {active.filename} (version {active.version})</h3>
      <p className="text-small text-ink-muted">Check each sheet's header row and column types, and mark personal data. Only headers and types are shown, never cell values.</p>
      {sheets.map((s, i) => (
        <fieldset key={s.sheet} className="grid gap-2 rounded-lg border border-line p-3">
          <legend className="px-1 text-body-strong">{s.sheet}</legend>
          <div className="flex flex-wrap items-center gap-4">
            <label className="flex items-center gap-2"><Checkbox checked={s.include} onCheckedChange={(v) => set(i, { include: v === true })} aria-label={`Include ${s.sheet}`} />Include</label>
            <label className="flex items-center gap-2">Header row
              <Input className="w-20" inputMode="numeric" value={String(s.header_row)} aria-label={`Header row of ${s.sheet}`}
                onChange={(e) => set(i, { header_row: Math.max(1, Number(e.target.value) || 1) })} /></label>
            <span className="text-small text-ink-muted">{s.rows.toLocaleString()} rows as table {s.table}{s.merged_cells ? ` · ${s.merged_cells} merged cells` : ""}</span>
          </div>
          {s.include && (
            <table className="w-full text-small">
              <thead><tr className="text-left text-ink-muted"><th className="py-1">Column</th><th>Type</th><th>Personal data</th><th>Notes</th></tr></thead>
              <tbody>
                {s.columns.map((c, k) => (
                  <tr key={c.header} className="border-t border-line">
                    <td className="py-1">{c.header}</td>
                    <td><NativeSelect value={c.type} aria-label={`Type of ${c.header}`} onChange={(e) => setCol(i, k, { type: e.target.value })}>
                      {TYPES.map((t) => <option key={t} value={t}>{t}{t === c.inferred ? " (found)" : ""}</option>)}
                    </NativeSelect></td>
                    <td><Checkbox checked={c.personal} aria-label={`${c.header} is personal data`} onCheckedChange={(v) => setCol(i, k, { personal: v === true })} /></td>
                    <td className="flex flex-wrap gap-1">{c.flags.map((f) => <Badge key={f} variant="stale">{FLAG_TEXT[f] ?? f}</Badge>)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </fieldset>
      ))}
      <FormAlert error={save.error ? asApiError(save.error) : null} />
      <div><Button onClick={() => save.mutate()} disabled={save.isPending || !sheets.some((s) => s.include)}>{save.isPending ? "Saving and re-reading…" : "Save and re-read"}</Button></div>
    </section>
  );
}

/** Upload a replacement: it waits as a pending version until the Admin has seen what it changes (E-13). */
export function ReplaceFile({ id, onDone }: { id: string; onDone: () => void }) {
  const qc = useQueryClient();
  const [pending, setPending] = useState<{ version: number; diff: Diff } | null>(null);
  const upload = useMutation({
    mutationFn: (file: File) => { const body = new FormData(); body.append("file", file); return api<{ version: number; diff: Diff }>(`/api/v1/connections/${id}/files`, { method: "POST", body }); },
    onSuccess: (r) => setPending(r),
  });
  const decide = useMutation({
    mutationFn: (what: "activate" | "discard") => api(`/api/v1/connections/${id}/files/${pending!.version}/${what}`, { method: "POST" }),
    onSuccess: () => { setPending(null); void qc.invalidateQueries({ queryKey: ["connection-files", id] }); void qc.invalidateQueries({ queryKey: ["connection-objects", id] }); onDone(); },
  });
  const error = (upload.error ?? decide.error) as ApiError | null;
  return (
    <section className="grid gap-2" aria-labelledby="replace-title">
      <h3 id="replace-title" className="text-body-strong">Replace the file</h3>
      {!pending ? (
        <div className="flex flex-wrap items-center gap-2">
          <Input type="file" accept=".xlsx,.csv" aria-label="Replacement file" className="max-w-sm"
            onChange={(e) => { const f = e.target.files?.[0]; if (f) upload.mutate(f); }} disabled={upload.isPending} />
          {upload.isPending && <span className="text-small text-ink-muted">Checking the new version…</span>}
        </div>
      ) : (
        <Alert variant={pending.diff.breaks_model.length ? "warn" : "info"}>
          <p><strong>Version {pending.version} is waiting.</strong> {pending.diff.added.length} sheets added, {pending.diff.removed.length} removed, {pending.diff.changed.length} changed.</p>
          {pending.diff.removed.length > 0 && <p>Removed: {pending.diff.removed.join(", ")}</p>}
          {pending.diff.breaks_model.length > 0 && <p>Certified definitions that would break: {pending.diff.breaks_model.map((b) => `${b.entity} (${b.problem})`).join("; ")}. Their widgets show as Broken until the model is fixed.</p>}
          <div className="mt-2 flex flex-wrap gap-2">
            <Button size="sm" onClick={() => decide.mutate("activate")} disabled={decide.isPending}>Use version {pending.version}</Button>
            <Button size="sm" variant="secondary" onClick={() => decide.mutate("discard")} disabled={decide.isPending}>Discard it</Button>
          </div>
        </Alert>
      )}
      <FormAlert error={error ? asApiError(error) : null} />
    </section>
  );
}
