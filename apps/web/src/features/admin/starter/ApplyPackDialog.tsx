import { keepPreviousData, useMutation, useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { api } from "../../../api/client";
import type { PackAiSuggestions, PackInstallResult, PackMapping, PackMatch, PackMeasureCheck, PackPreview } from "../../../api/app";
import { Alert, FormAlert } from "../../../components/ui/alert";
import { Button } from "../../../components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "../../../components/ui/dialog";
import { Label } from "../../../components/ui/label";
import { NativeSelect } from "../../../components/ui/native-select";
import { Skeleton } from "../../../components/ui/skeleton";
import { asApiError } from "../../../lib/api-errors";
import { PackMappingStep, problemsOf } from "./PackMappingStep";

const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;
const KIND: Record<string, string> = { entity: "Table", field: "Field", measure: "Measure", dimension: "Dimension" };

/** The pack the dialog offers first: the one that matches by name, else the closest one (US-154 maps the rest). */
function bestMatch(matches: PackMatch[]): PackMatch | null {
  return matches.find((x) => x.matched) ?? matches.find((x) => x.confidence > 0) ?? null;
}

async function previewFor(connectionId: string, packId: string, mapping: PackMapping | null): Promise<PackPreview> {
  return api<PackPreview>(`/api/v1/starter-packs/${packId}/preview`, {
    method: "POST", body: { connection_id: connectionId, ...(mapping ? { mapping } : {}) },
  });
}

const packLabel = (m: PackMatch) => `${m.title || m.pack_key} ${m.version} · ${Math.round(m.confidence * 100)}% of its fields found`;

/** What the last "Suggest with AI" did, in a line (US-201); null before it ran. */
function aiNote(r: PackAiSuggestions | undefined, error: string | null): string | null {
  if (error) return `The AI could not be asked: ${error}`;
  if (!r) return null;
  if (!r.available) return r.message;
  const found = Object.values(r.suggestions).filter((s) => s.column).length;
  const failed = r.tables.filter((t) => t.status === "failed").map((t) => t.entity);
  const asked = r.tables.reduce((n, t) => n + t.asked, 0);
  if (!asked) return "Names and values settle every field: there was nothing to ask the AI.";
  return `The AI suggested ${plural(found, "column")} for ${plural(asked, "open field")}; check each reason before you use it.`
    + (failed.length ? ` Its answer for ${failed.join(", ")} could not be read; map ${failed.length === 1 ? "it" : "them"} by hand.` : "");
}

/**
 * Apply a starter pack to a connection (US-153; BUG-004): with or without a model. The preview shows what is added,
 * what of the Admin's own model stays, and each clash with a choice (the pack's certified form, or keep mine). On a
 * connection with a model the pack is merged into the model draft; nothing is live until the model is approved.
 * When the source uses other names, the mapping step (US-154) matches pack fields and values to it; each change
 * re-runs the preview, and install saves the mapping for this connection. Mapping guards (US-199): two pack fields
 * on one column block install; values missing from a column are warned; "Check the measures" tries each bound measure
 * on the data and lists those that return 0 or the same figure as another. The Admin chooses which released pack to
 * apply (the PMO pack, a database pack…); the best match is chosen first, and changing pack starts its mapping afresh.
 */
export function ApplyPackDialog({ connectionId, connectionName, open, onClose, onApplied }: {
  connectionId: string; connectionName: string; open: boolean; onClose: () => void; onApplied: (r: PackInstallResult) => void;
}) {
  const [mapping, setMapping] = useState<PackMapping | null>(null); // null: the mapping saved for this connection
  const [chosen, setChosen] = useState<string | null>(null); // null: the best match
  const matches = useQuery({
    queryKey: ["pack-choice", connectionId], enabled: open, gcTime: 0, retry: false,
    queryFn: () => api<{ matches: PackMatch[] }>(`/api/v1/starter-packs/match?connection_id=${connectionId}`).then((r) => r.matches),
  });
  const packs = matches.data ?? [];
  const packId = (packs.find((m) => m.pack_id === chosen) ?? bestMatch(packs))?.pack_id ?? null;
  const q = useQuery({
    queryKey: ["pack-preview", connectionId, packId, mapping], queryFn: () => previewFor(connectionId, packId!, mapping),
    enabled: open && !!packId, gcTime: 0, placeholderData: keepPreviousData, retry: false,
  });
  const [mine, setMine] = useState<Set<string>>(new Set());
  const trial = useMutation({
    mutationFn: (p: PackPreview) => api<PackMeasureCheck>(`/api/v1/starter-packs/${p.pack_id}/check`, {
      method: "POST", body: { connection_id: connectionId, mapping: mapping ?? p.mapping },
    }),
  });
  // US-201: the model's suggestions for what names and values leave open; kept while the Admin maps (each row hides
  // its suggestion once it reads that column), cleared on close.
  const ai = useMutation({
    mutationFn: (p: PackPreview) => api<PackAiSuggestions>(`/api/v1/starter-packs/${p.pack_id}/ai-suggestions`, {
      method: "POST", body: { connection_id: connectionId, mapping: mapping ?? p.mapping },
    }),
  });
  const remap = (m: PackMapping) => { trial.reset(); setMapping(m); };
  const close = () => { setMapping(null); setChosen(null); setMine(new Set()); trial.reset(); ai.reset(); onClose(); };
  // Another pack has its own fields: its saved mapping, choices and checks replace this one's.
  const choosePack = (id: string) => { setChosen(id); setMapping(null); setMine(new Set()); trial.reset(); ai.reset(); };
  const apply = useMutation({
    mutationFn: (p: PackPreview) => api<PackInstallResult>(`/api/v1/starter-packs/${p.pack_id}/install`, {
      method: "POST", body: { connection_id: connectionId, keep_mine: [...mine], mapping: mapping ?? p.mapping },
    }),
    onSuccess: (r) => { onApplied(r); close(); },
  });
  // A preview of the pack chosen before stays on screen (keepPreviousData) only while the same pack is loading again.
  const p = q.data && q.data.pack_id === packId ? q.data : undefined;
  const blocking = p?.checks?.blocking ?? [];
  const valueWarnings = (p?.checks?.warnings ?? []).filter((w) => w.kind === "values");
  const choose = (ref: string, value: string) => setMine((s) => {
    const next = new Set(s);
    if (value === "mine") next.add(ref); else next.delete(ref);
    return next;
  });

  return (
    <Dialog open={open} onOpenChange={(o) => !o && close()}>
      <DialogContent className="flex max-h-[calc(100dvh-2rem)] flex-col sm:max-w-[min(96rem,calc(100%-4rem))]">
        <DialogHeader>
          <DialogTitle>Starter pack for {connectionName}</DialogTitle>
          <DialogDescription>
            {p?.model_exists
              ? "The pack is added to this connection's model draft. Nothing you built is removed, and nothing is live until you approve the model."
              : "The pack builds a draft model for this connection. Nothing is live until you approve it."}
          </DialogDescription>
        </DialogHeader>
        {packs.length > 0 && (
          <div className="grid max-w-xl gap-1">
            <Label htmlFor="pack-choice">Starter pack</Label>
            <NativeSelect id="pack-choice" value={packId ?? ""} onChange={(e) => choosePack(e.target.value)}>
              {!packId && <option value="" disabled>Choose a pack</option>}
              {packs.map((m) => <option key={m.pack_id} value={m.pack_id}>{packLabel(m)}</option>)}
            </NativeSelect>
          </div>
        )}
        {(matches.error || q.error) && <FormAlert error={asApiError(matches.error ?? q.error)} />}
        {/* Landscape: header and footer stay in view; the mapping and what applying does scroll side by side. */}
        {matches.isLoading || (packId && !p && !q.error) ? <Skeleton className="h-32" /> : !p ? (
          !matches.error && !q.error && <Alert><p>{packs.length ? "No starter pack matches this source by name. Choose one above to map its fields to this source, or describe the source in Model studio instead." : "No starter pack is released yet. Describe this source in Model studio instead."}</p></Alert>
        ) : (
          <div className="grid min-h-0 flex-1 gap-4 overflow-auto lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)] lg:grid-rows-[minmax(0,1fr)] lg:overflow-hidden"
            data-testid="pack-preview" aria-busy={q.isFetching}>
            <div className="grid content-start gap-3 lg:overflow-auto lg:pr-2">
              {p.installed_pack_id && <p>This pack is already applied here; applying again only adds what is missing.</p>}
              {!p.matched && (
                <Alert><p>This source uses other names than the pack. Choose a column for each required field below, or mark it as not in this source.</p></Alert>
              )}
              {blocking.length > 0 && (
                <Alert variant="danger" data-testid="pack-blocking">
                  <p className="text-body-strong">Fix the mapping before installing</p>
                  <ul className="list-disc pl-5 text-small">{blocking.map((b) => <li key={b.ref + b.message}>{b.message}</li>)}</ul>
                </Alert>
              )}
              {valueWarnings.length > 0 && (
                <Alert variant="warn" data-testid="pack-value-warnings">
                  <p className="text-body-strong">{plural(valueWarnings.length, "field's column lacks", "fields' columns lack")} values the pack's rules use</p>
                  <ul className="list-disc pl-5 text-small">{valueWarnings.map((w) => <li key={w.ref}>{w.issue}</li>)}</ul>
                  <p className="text-small text-ink-muted">Check the column, or map the source's values to the pack's.</p>
                </Alert>
              )}
              <PackMappingStep connectionId={connectionId} objects={p.mapping_step.objects} entities={p.mapping_step.entities}
                mapping={mapping ?? p.mapping} problems={problemsOf(p.checks)} onChange={remap}
                ai={{ available: !!p.checks?.ai_available, pending: ai.isPending, suggestions: ai.data?.suggestions ?? {},
                  note: aiNote(ai.data, ai.error ? asApiError(ai.error).message : null), onAsk: () => ai.mutate(p) }} />
            </div>
            <div className="grid content-start gap-3 lg:overflow-auto lg:border-l lg:border-line lg:pl-4 lg:pr-1">
              <h3 className="text-body-strong">What applying does</h3>
              <ul className="grid list-disc gap-1 pl-5">
                <li>Adds {plural(p.added.measures, "measure")}, {plural(p.added.dimensions, "dimension")}, {plural(p.added.entities, "table")} and {plural(p.added.fields, "field")}.</li>
                {p.model_exists && <li>Keeps your {plural(p.kept_own.entities, "table")}, {plural(p.kept_own.measures, "measure")} and {plural(p.kept_own.dimensions, "dimension")}, with your labels.</li>}
                <li>{plural(p.reports.available, "starter report")} and the starter dashboards are built when you approve the model{p.reports.unavailable ? `; ${p.reports.unavailable} unavailable (data missing)` : ""}.</li>
                {p.same_source.map((s) => <li key={s.pack_entity}>The pack's <span className="font-mono">{s.pack_entity}</span> reads the same table as your <span className="font-mono">{s.model_entity}</span> ({s.source_object}); both are kept.</li>)}
                {p.folded.length > 0 && <li>Folds {plural(p.folded.length, "of your fields", "of your fields")} into the pack's fields on the same columns: {p.folded.map((f) => `${f.mine_label} → ${f.pack_label}`).join(", ")}.</li>}
                {p.same_column.map((f) => <li key={`${f.entity}.${f.mine}`} className="text-status-danger">Your {f.mine_label} and the pack's {f.pack_label} both read {f.column}; yours is used in your model, so both are kept. Point one at another column or remove yours before approving.</li>)}
              </ul>
              {p.clashes.length > 0 && (
                <div className="grid gap-2">
                  <p className="text-body-strong">{plural(p.clashes.length, "item is", "items are")} defined differently in your model</p>
                  <p className="text-small text-ink-muted">The pack's version is certified and used by its reports. Keep yours where it means something else; pack items that need it become unavailable.</p>
                  <ul className="grid gap-2">
                    {p.clashes.map((c) => (
                      <li key={c.ref} className="grid gap-1 rounded-md border border-line p-2 sm:grid-cols-[1fr_11rem] sm:items-center">
                        <div className="text-small">
                          <p className="text-body-strong">{KIND[c.kind] ?? c.kind}: {c.title}</p>
                          <p className="text-ink-muted">Yours: {c.mine}</p>
                          <p className="text-ink-muted">Pack: {c.pack}</p>
                        </div>
                        <NativeSelect aria-label={`Which ${c.title} to keep`} value={mine.has(c.ref) ? "mine" : "pack"} onChange={(e) => choose(c.ref, e.target.value)}>
                          <option value="pack">Use the pack's</option>
                          <option value="mine">Keep mine</option>
                        </NativeSelect>
                      </li>
                    ))}
                  </ul>
                </div>
              )}
              <div className="grid gap-2" data-testid="pack-measure-check">
                <div className="flex flex-wrap items-center gap-2">
                  <h3 className="text-body-strong">Measures on your data</h3>
                  <Button size="sm" variant="secondary" disabled={blocking.length > 0 || !p.matched || trial.isPending || q.isFetching} onClick={() => trial.mutate(p)}>
                    {trial.isPending ? "Checking…" : trial.data ? "Check again" : "Check the measures"}
                  </Button>
                </div>
                {!trial.data && !trial.isPending && <p className="text-small text-ink-muted">Runs each measure once on the data (counts only) and lists those that return 0, or the same figure as another measure with a different rule.</p>}
                {trial.error && <FormAlert error={asApiError(trial.error)} />}
                {trial.data && (trial.data.warnings.length === 0
                  ? <p className="text-small">All {plural(trial.data.measures.length, "measure")} return a figure of their own.</p>
                  : (
                    <ul className="grid list-disc gap-1 pl-5 text-small">{trial.data.warnings.map((w) => <li key={w.kind + w.ref}>{w.issue}</li>)}</ul>
                  ))}
              </div>
              {p.unavailable.length > 0 && (
                <details className="text-small">
                  <summary className="cursor-pointer">{plural(p.unavailable.length, "item is", "items are")} unavailable on this source</summary>
                  <ul className="mt-1 grid gap-0.5 pl-5">{p.unavailable.map((u) => <li key={`${u.kind}:${u.key}`}>{u.title} — needs {u.gaps.join(", ")}</li>)}</ul>
                </details>
              )}
            </div>
          </div>
        )}
        <FormAlert error={apply.error ? asApiError(apply.error) : null} />
        <DialogFooter>
          <Button variant="ghost" onClick={close}>Cancel</Button>
          <Button disabled={!p || !p.matched || blocking.length > 0 || q.isFetching || apply.isPending} onClick={() => p && apply.mutate(p)}>
            {apply.isPending ? "Applying…" : p?.model_exists ? "Add to the model draft" : "Install starter pack"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
