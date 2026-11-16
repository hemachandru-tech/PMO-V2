import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronDown, ChevronRight, FileText, KeyRound, Link2, Search, Sparkles, Upload } from "lucide-react";
import { BrandLoader } from "../../../components/BrandLoader";
import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";

import { api } from "../../../api/client";
import { Alert, FormAlert } from "../../../components/ui/alert";
import { Badge } from "../../../components/ui/badge";
import { Button } from "../../../components/ui/button";
import { Checkbox } from "../../../components/ui/checkbox";
import { Input } from "../../../components/ui/input";
import { Label } from "../../../components/ui/label";
import { Skeleton } from "../../../components/ui/skeleton";
import { asApiError } from "../../../lib/api-errors";
import { cn } from "../../../lib/utils";
import { UploadDialog } from "../knowledge/KnowledgePage";

/** What `GET /connections/{id}/model/selection` returns. */
export interface BuilderSource {
  model_id: string | null;
  objects: { object: string; columns: { name: string; type: string | null; pk: boolean; fk: boolean }[] }[];
  selected: Record<string, string[]> | null;
  settings: Record<string, string>;
  rules: BuilderRule[];
  documents: { id: string; title: string; original_filename: string | null; review_status: string; index_status: string; chunk_count: number }[];
}

export interface BuilderRule {
  key: string;
  term: string;
  meaning: string;
  entity: string | null;
  sql: string | null;
  synonyms: string[];
  always: boolean;
  origin: "document" | "inferred" | "admin";
  cite: string | null;
  status: "ok" | "needs_review" | "off";
  issue: string | null;
}

interface Preview {
  model_id: string;
  draft_version_id: string;
  mode: "new" | "merged";
  documents: { id: string; title: string }[];
  settings: Record<string, string>;
  tables: { object: string; entity: string; label: string; description: string | null; columns_shown: number; columns_total: number;
    columns: { column: string; label: string; type: string; description: string | null; values?: string[]; kind?: string | null }[] }[];
  rules: BuilderRule[];
  counts: { tables: number; columns_shown: number; columns_hidden: number; columns_described: number; links: number; measures: number; rules_ok: number; rules_review: number };
}

interface Job {
  job_id: string;
  status: "running" | "done" | "failed";
  steps: { at: number; text: string }[];
  warnings: string[];
  error?: string;
  result?: Preview;
  seconds?: number;
  llm_calls?: number;
  chars_sent?: number;
}

/** The builder's choices, kept per connection for the session so Back and a reload keep them. */
export interface BuilderChoice {
  objects: Record<string, string[]>; // qualified table → columns ("*" = every column)
  documentIds: string[];
  reportingDate: string;
  jobId: string | null;
}

const EMPTY: BuilderChoice = { objects: {}, documentIds: [], reportingDate: "", jobId: null };

export function useBuilderChoice(id: string): [BuilderChoice, (next: BuilderChoice) => void] {
  const storageKey = `tela.model-builder.${id}`;
  const [choice, setChoice] = useState<BuilderChoice>(() => {
    try {
      const raw = sessionStorage.getItem(storageKey);
      return raw ? { ...EMPTY, ...(JSON.parse(raw) as BuilderChoice) } : EMPTY;
    } catch {
      return EMPTY;
    }
  });
  const set = (next: BuilderChoice) => {
    setChoice(next);
    try { sessionStorage.setItem(storageKey, JSON.stringify(next)); } catch { /* private window: state stays in memory */ }
  };
  return [choice, set];
}

export const builderKey = (id: string) => ["model-builder-source", id];
const ordinal = (n: number) => `${n}${n === 1 ? "st" : n === 2 ? "nd" : n === 3 ? "rd" : "th"}`;
const useSource = (id: string) => useQuery({ queryKey: builderKey(id), queryFn: () => api<BuilderSource>(`/api/v1/connections/${id}/model/selection`) });

// --------------------------------------------------------------------------- 1. choose columns

/** Pick the tables and columns the model may use; everything else stays out of the LLM's sight. */
export function ColumnsStep({ id, choice, onChange, onBack, onNext }: {
  id: string; choice: BuilderChoice; onChange: (c: BuilderChoice) => void; onBack: () => void; onNext: () => void;
}) {
  const source = useSource(id);
  const [filter, setFilter] = useState("");
  const [open, setOpen] = useState<Record<string, boolean>>({});
  const objects = source.data?.objects ?? [];

  // Start from the current model's columns, or every table when the connection has no model yet.
  useEffect(() => {
    if (!source.data || Object.keys(choice.objects).length) return;
    const start = source.data.selected
      ?? Object.fromEntries(source.data.objects.map((o) => [o.object, ["*"]]));
    onChange({ ...choice, objects: start, reportingDate: choice.reportingDate || source.data.settings.reporting_date || "" });
  }, [source.data]); // eslint-disable-line react-hooks/exhaustive-deps

  const picked = (obj: string) => {
    const cols = choice.objects[obj];
    const all = objects.find((o) => o.object === obj)?.columns.map((c) => c.name) ?? [];
    if (!cols) return new Set<string>();
    return cols.includes("*") ? new Set(all) : new Set(cols);
  };
  const setTable = (obj: string, on: boolean) => {
    const next = { ...choice.objects };
    if (on) next[obj] = ["*"]; else delete next[obj];
    onChange({ ...choice, objects: next });
  };
  const setColumn = (obj: string, col: string, on: boolean) => {
    const all = objects.find((o) => o.object === obj)?.columns.map((c) => c.name) ?? [];
    const cur = picked(obj);
    if (on) cur.add(col); else cur.delete(col);
    const next = { ...choice.objects };
    if (cur.size === 0) delete next[obj]; else next[obj] = cur.size === all.length ? ["*"] : [...cur];
    onChange({ ...choice, objects: next });
  };
  const all = (on: boolean) => onChange({ ...choice, objects: on ? Object.fromEntries(objects.map((o) => [o.object, ["*"]])) : {} });

  const q = filter.trim().toLowerCase();
  const shown = objects.filter((o) => !q || o.object.toLowerCase().includes(q) || o.columns.some((c) => c.name.toLowerCase().includes(q)));
  const totalCols = objects.reduce((n, o) => n + o.columns.length, 0);
  const pickedCols = objects.reduce((n, o) => n + picked(o.object).size, 0);
  const pickedTables = Object.keys(choice.objects).length;

  return (
    <section className="grid gap-4" aria-labelledby="columns-title">
      <div className="grid gap-1">
        <h2 id="columns-title" className="text-heading">Choose tables and columns</h2>
        <p className="text-small text-ink-muted">Only what you pick is shown to the language model, which keeps answers fast and cheap. Keys and joins the model needs are kept for you.</p>
      </div>
      {source.isLoading ? <Skeleton className="h-64" /> : (
        <>
          <div className="flex flex-wrap items-center gap-2">
            <div className="relative min-w-60 flex-1">
              <Search aria-hidden className="pointer-events-none absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-ink-muted" />
              <Input aria-label="Find a table or column" placeholder="Find a table or column" value={filter} onChange={(e) => setFilter(e.target.value)} className="pl-8" />
            </div>
            <Button variant="secondary" size="sm" onClick={() => all(true)}>Select all</Button>
            <Button variant="ghost" size="sm" onClick={() => all(false)}>Clear</Button>
          </div>
          <p className="text-small text-ink-muted" aria-live="polite">{pickedTables} of {objects.length} tables · {pickedCols.toLocaleString()} of {totalCols.toLocaleString()} columns picked</p>
          <ul className="grid max-h-[28rem] gap-1 overflow-y-auto rounded-lg border border-line p-1">
            {shown.map((o) => {
              const sel = picked(o.object);
              const state = sel.size === 0 ? false : sel.size === o.columns.length ? true : "indeterminate";
              const isOpen = open[o.object] || (!!q && !o.object.toLowerCase().includes(q));
              const cols = q && !o.object.toLowerCase().includes(q) ? o.columns.filter((c) => c.name.toLowerCase().includes(q)) : o.columns;
              return (
                <li key={o.object} className="rounded-md">
                  <div className="flex items-center gap-2 rounded-md px-2 py-1.5 hover:bg-surface-sunken">
                    <Checkbox id={`t-${o.object}`} checked={state} onCheckedChange={(v) => setTable(o.object, v === true || (v === "indeterminate"))} aria-label={`Use ${o.object}`} />
                    <button type="button" className="flex flex-1 items-center gap-1.5 text-left focus-visible:focus-ring" aria-expanded={isOpen}
                      onClick={() => setOpen({ ...open, [o.object]: !open[o.object] })}>
                      {isOpen ? <ChevronDown aria-hidden className="size-4 text-ink-muted" /> : <ChevronRight aria-hidden className="size-4 text-ink-muted" />}
                      <span className="font-mono text-small">{o.object}</span>
                      <span className="ml-auto text-small text-ink-muted">{sel.size} / {o.columns.length}</span>
                    </button>
                  </div>
                  {isOpen && (
                    <ul className="grid gap-x-4 gap-y-1 px-9 pb-2 pt-1 sm:grid-cols-2 lg:grid-cols-3">
                      {cols.map((c) => (
                        <li key={c.name} className="flex min-w-0 items-center gap-2">
                          <Checkbox id={`c-${o.object}-${c.name}`} checked={sel.has(c.name)} onCheckedChange={(v) => setColumn(o.object, c.name, v === true)} />
                          <label htmlFor={`c-${o.object}-${c.name}`} className="flex min-w-0 items-center gap-1 text-small">
                            <span className="truncate font-mono">{c.name}</span>
                            {c.pk && <KeyRound aria-label="key" className="size-3 shrink-0 text-ink-muted" />}
                            {c.fk && <Link2 aria-label="join" className="size-3 shrink-0 text-ink-muted" />}
                          </label>
                        </li>
                      ))}
                    </ul>
                  )}
                </li>
              );
            })}
          </ul>
        </>
      )}
      <FormAlert error={source.error ? asApiError(source.error) : null} />
      <div className="flex flex-wrap gap-2">
        <Button variant="ghost" onClick={onBack}>Back</Button>
        <Button onClick={onNext} disabled={pickedTables === 0}>Next: knowledge</Button>
      </div>
    </section>
  );
}

// --------------------------------------------------------------------------- 2. knowledge (optional)

/** Optional business rules and glossaries the model builder reads. Without them the model comes from the schema only. */
export function KnowledgeStep({ id, choice, onChange, onBack, onNext }: {
  id: string; choice: BuilderChoice; onChange: (c: BuilderChoice) => void; onBack: () => void; onNext: () => void;
}) {
  const qc = useQueryClient();
  const source = useSource(id);
  const [uploading, setUploading] = useState(false);
  const docs = source.data?.documents ?? [];
  const toggle = (doc: string, on: boolean) =>
    onChange({ ...choice, documentIds: on ? [...new Set([...choice.documentIds, doc])] : choice.documentIds.filter((d) => d !== doc) });
  return (
    <section className="grid gap-4" aria-labelledby="knowledge-title">
      <div className="grid gap-1">
        <h2 id="knowledge-title" className="text-heading">Add business rules and a glossary <span className="text-ink-muted">(optional)</span></h2>
        <p className="text-small text-ink-muted">The model builder reads them once and turns each rule into a checked condition on your columns. Questions then use your definitions instead of guesses. Without documents, the model is built from the schema alone and only states what the schema makes certain.</p>
      </div>
      {source.isLoading ? <Skeleton className="h-24" /> : docs.length === 0 ? (
        <p className="text-ink-muted">No indexed documents yet.</p>
      ) : (
        <ul className="grid gap-1 rounded-lg border border-line p-1">
          {docs.map((d) => (
            <li key={d.id} className="flex items-center gap-3 rounded-md px-2 py-2 hover:bg-surface-sunken">
              <Checkbox id={`d-${d.id}`} checked={choice.documentIds.includes(d.id)} onCheckedChange={(v) => toggle(d.id, v === true)} />
              <label htmlFor={`d-${d.id}`} className="flex flex-1 items-center gap-2">
                <FileText aria-hidden className="size-4 text-ink-muted" />
                <span className="text-body-strong">{d.title}</span>
                <span className="text-small text-ink-muted">{d.chunk_count} passages</span>
                {choice.documentIds.includes(d.id) && <Badge variant="outline">Read {ordinal(choice.documentIds.indexOf(d.id) + 1)}</Badge>}
              </label>
              <Badge variant={d.review_status === "approved" ? "certified" : "stale"}>{d.review_status === "approved" ? "Approved" : "Not reviewed"}</Badge>
            </li>
          ))}
        </ul>
      )}
      {choice.documentIds.length > 1 && <p className="text-small text-ink-muted">Documents are read in the order you pick them. When two define the same thing differently, the one picked first wins.</p>}
      <div><Button variant="secondary" size="sm" onClick={() => setUploading(true)}><Upload aria-hidden />Upload a document</Button></div>
      <UploadDialog open={uploading} onClose={() => setUploading(false)} onUploaded={() => void qc.invalidateQueries({ queryKey: builderKey(id) })} />
      <div className="flex flex-wrap gap-2">
        <Button variant="ghost" onClick={onBack}>Back</Button>
        <Button onClick={onNext}>{choice.documentIds.length ? `Next: generate with ${choice.documentIds.length} document${choice.documentIds.length > 1 ? "s" : ""}` : "Skip: generate from the schema only"}</Button>
      </div>
    </section>
  );
}

// --------------------------------------------------------------------------- 3. generate and preview

/** The LLM writes the model (descriptions and checked business rules); the Admin previews it, then approves it. */
export function GenerateStep({ id, choice, onChange, onBack }: {
  id: string; choice: BuilderChoice; onChange: (c: BuilderChoice) => void; onBack: () => void;
}) {
  const qc = useQueryClient();
  const source = useSource(id);
  const job = useQuery({
    queryKey: ["model-job", choice.jobId],
    queryFn: () => api<Job>(`/api/v1/model-jobs/${choice.jobId}`),
    enabled: !!choice.jobId,
    refetchInterval: (q) => (q.state.data?.status === "running" ? 1500 : false),
    refetchIntervalInBackground: true, // a job keeps reporting while the Admin looks at another tab
  });
  const start = useMutation({
    mutationFn: () => api<{ job_id: string }>(`/api/v1/connections/${id}/model/generate`, {
      method: "POST",
      body: { objects: choice.objects, document_ids: choice.documentIds, reporting_date: choice.reportingDate || null },
    }),
    onSuccess: (r) => onChange({ ...choice, jobId: r.job_id }),
  });
  const j = job.data;
  useEffect(() => {
    if (j?.status === "done") {
      void qc.invalidateQueries({ queryKey: builderKey(id) });
      void qc.invalidateQueries({ queryKey: ["connections"] });
      void qc.invalidateQueries({ queryKey: ["connection", id] });
    }
  }, [j?.status]); // eslint-disable-line react-hooks/exhaustive-deps
  const running = start.isPending || j?.status === "running";
  const tables = Object.keys(choice.objects).length;
  const docs = (source.data?.documents ?? []).filter((d) => choice.documentIds.includes(d.id));

  return (
    <section className="grid gap-4" aria-labelledby="generate-title">
      <div className="grid gap-1">
        <h2 id="generate-title" className="text-heading">Generate the model</h2>
        <p className="text-small text-ink-muted">The language model describes your tables and columns and turns your documents into business rules. Every rule is checked against the real columns; anything it can't check waits for your review and is never used in answers.</p>
      </div>
      <dl className="grid gap-x-6 gap-y-1 sm:grid-cols-[max-content_1fr]">
        <dt className="text-ink-muted">Tables</dt><dd>{tables}</dd>
        <dt className="text-ink-muted">Documents</dt><dd>{docs.length ? docs.map((d) => d.title).join(", ") : "None: the schema only"}</dd>
        <dt className="text-ink-muted">Model</dt><dd>{source.data?.model_id ? "Merged into this connection's model (its measures and data rules are kept)" : "A new model"}</dd>
      </dl>
      <div className="grid max-w-xs gap-1.5">
        <Label htmlFor="data-date">Data date <span className="text-ink-muted">(optional)</span></Label>
        <Input id="data-date" type="date" value={choice.reportingDate} onChange={(e) => onChange({ ...choice, reportingDate: e.target.value })} />
        <p className="text-small text-ink-muted">What “today” means for this data, for overdue, ageing and “next 30 days”. Leave empty to use the calendar date.</p>
      </div>
      <FormAlert error={start.error ? asApiError(start.error) : job.error ? asApiError(job.error) : null} />
      <div className="flex flex-wrap gap-2">
        <Button variant="ghost" onClick={onBack} disabled={running}>Back</Button>
        <Button onClick={() => start.mutate()} disabled={running || tables === 0}>
          <Sparkles aria-hidden />{running ? "Generating…" : j?.status === "done" ? "Generate again" : "Generate the model with AI"}
        </Button>
      </div>
      {j && (
        <div className="grid gap-1 rounded-lg border border-line bg-surface-sunken/40 p-3" aria-live="polite">
          {running && <div className="flex items-center gap-3 pb-1"><BrandLoader size={36} label="Generating the model" /><span className="text-body-strong">The AI is reading your data…</span></div>}
          <ol className="grid gap-0.5 text-small">
            {j.steps.map((s, i) => (
              <li key={i} className={cn("flex gap-2", i === j.steps.length - 1 && j.status === "running" ? "text-ink" : "text-ink-muted")}>
                <span className="w-12 shrink-0 tabular-nums">{s.at.toFixed(0)} s</span>{s.text}
              </li>
            ))}
          </ol>
          {j.warnings.map((w) => <p key={w} className="text-small text-status-warn">{w}</p>)}
          {j.status === "failed" && <Alert variant="warn"><p>{j.error}</p></Alert>}
          {j.status === "done" && <p className="text-small text-ink-muted">Done in {j.seconds} s with {j.llm_calls} model calls.</p>}
        </div>
      )}
      {j?.status === "done" && j.result && <ModelPreview p={j.result} />}
    </section>
  );
}

function ModelPreview({ p }: { p: Preview }) {
  const [tab, setTab] = useState<"rules" | "tables">("rules");
  const [open, setOpen] = useState<string | null>(null);
  const review = p.rules.filter((r) => r.status === "needs_review");
  const byEntity = useMemo(() => Object.fromEntries(p.tables.map((t) => [t.entity, t.object])), [p.tables]);
  const c = p.counts;
  return (
    <section className="grid gap-4 border-t border-line pt-4" aria-labelledby="preview-title">
      <h3 id="preview-title" className="text-heading">Preview</h3>
      <ul className="grid gap-2 sm:grid-cols-3 lg:grid-cols-6">
        {[["Tables", c.tables], ["Columns shown", c.columns_shown], ["Columns described", c.columns_described], ["Joins", c.links], ["Rules checked", c.rules_ok], ["Rules to review", c.rules_review]].map(([k, v]) => (
          <li key={k as string} className="rounded-lg border border-line p-3">
            <p className="text-small text-ink-muted">{k}</p>
            <p className="font-mono text-figure tabular-nums">{(v as number).toLocaleString()}</p>
          </li>
        ))}
      </ul>
      <p className="text-small text-ink-muted">Data date: {p.settings.reporting_date || "calendar date"} · {p.mode === "merged" ? "merged into the existing model" : "new model"} · {p.documents.length ? `from ${p.documents.map((d) => d.title).join(", ")}` : "from the schema only"}</p>
      {review.length > 0 && <Alert variant="warn"><p>{review.length} {review.length === 1 ? "rule" : "rules"} could not be checked against the columns. {review.length === 1 ? "It is" : "They are"} kept for review in Model studio and not used in answers.</p></Alert>}
      <div role="tablist" aria-label="Preview" className="flex gap-2">
        <Button role="tab" aria-selected={tab === "rules"} variant={tab === "rules" ? "secondary" : "ghost"} size="sm" onClick={() => setTab("rules")}>Business rules ({p.rules.length})</Button>
        <Button role="tab" aria-selected={tab === "tables"} variant={tab === "tables" ? "secondary" : "ghost"} size="sm" onClick={() => setTab("tables")}>Tables and columns ({p.tables.length})</Button>
      </div>
      {tab === "rules" ? (
        p.rules.length === 0 ? <p className="text-ink-muted">No business rules: answers rely on column names and descriptions.</p> : (
          <div className="overflow-x-auto rounded-lg border border-line">
            <table className="w-full text-small">
              <thead className="bg-surface-sunken text-left text-ink-muted">
                <tr><th className="p-2">Term</th><th className="p-2">Table</th><th className="p-2">Condition</th><th className="p-2">From</th><th className="p-2">Status</th></tr>
              </thead>
              <tbody>
                {p.rules.map((r) => (
                  <tr key={r.key} className="border-t border-line align-top">
                    <td className="p-2"><p className="font-medium">{r.term}{r.always && <Badge variant="outline" className="ml-1">always</Badge>}</p><p className="text-ink-muted">{r.meaning}</p>
                      {r.synonyms.length > 0 && <p className="text-ink-muted">Also: {r.synonyms.join(", ")}</p>}</td>
                    <td className="p-2 font-mono">{r.entity ? byEntity[r.entity] ?? r.entity : "—"}</td>
                    <td className="max-w-md p-2 font-mono break-words">{r.sql ?? <span className="text-ink-muted">(meaning only)</span>}</td>
                    <td className="p-2 text-ink-muted">{r.origin === "inferred" ? "Inferred from the schema" : r.cite ?? "Document"}</td>
                    <td className="p-2">{r.status === "ok" ? <Badge variant="certified">Checked</Badge> : <><Badge variant="stale">Review</Badge>{r.issue && <p className="mt-1 text-ink-muted">{r.issue}</p>}</>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )
      ) : (
        <ul className="grid gap-1 rounded-lg border border-line p-1">
          {p.tables.map((t) => (
            <li key={t.object}>
              <button type="button" className="flex w-full items-start gap-2 rounded-md px-2 py-2 text-left hover:bg-surface-sunken focus-visible:focus-ring" aria-expanded={open === t.object} onClick={() => setOpen(open === t.object ? null : t.object)}>
                {open === t.object ? <ChevronDown aria-hidden className="mt-0.5 size-4" /> : <ChevronRight aria-hidden className="mt-0.5 size-4" />}
                <span className="grid gap-0.5">
                  <span className="font-mono text-small">{t.object} <span className="text-ink-muted">· {t.columns_shown} of {t.columns_total} columns</span></span>
                  {t.description && <span className="text-small text-ink-muted">{t.description}</span>}
                </span>
              </button>
              {open === t.object && (
                <ul className="grid gap-1 px-8 pb-2">
                  {t.columns.map((col) => (
                    <li key={col.column} className="grid gap-x-3 text-small sm:grid-cols-[minmax(12rem,max-content)_1fr]">
                      <span className="flex items-center gap-1 font-mono">{col.column}{col.kind && <Badge variant="outline">{col.kind.replace("_", " ")}</Badge>}</span>
                      <span className="grid">
                        <span className={col.description ? "" : "text-ink-muted"}>{col.description ?? col.label}</span>
                        {!!col.values?.length && <span className="text-ink-muted">Values: {col.values.slice(0, 12).join(", ")}{col.values.length > 12 ? ` (+${col.values.length - 12})` : ""}</span>}
                      </span>
                    </li>
                  ))}
                </ul>
              )}
            </li>
          ))}
        </ul>
      )}
      <div className="flex flex-wrap gap-2">
        <Button asChild><Link to={`/admin/models/${p.model_id}/approve`}>Review and approve</Link></Button>
        <Button asChild variant="secondary"><Link to={`/admin/models/${p.model_id}`}>Open in Model studio</Link></Button>
      </div>
    </section>
  );
}
