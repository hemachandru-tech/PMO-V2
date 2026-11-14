import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, Download, Pencil, Play, Plus, Trash2, X } from "lucide-react";
import { useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { toast } from "sonner";

import { api } from "../../../api/client";
import { ERROR_LABEL, PATH_LABEL, SHAPE_LABEL, percent, type AnswerShape, type ErrorClass, type EvalResult, type EvalRun, type EvalRunDetail, type GoldExample, type GroupScores, type PlannerPath, type SetScores, type ShapeGrid as ShapeGridData, type SpecDiff } from "../../../api/knowledge";
import { formatRelative } from "../../../components/format";
import { Alert, FormAlert } from "../../../components/ui/alert";
import { Badge } from "../../../components/ui/badge";
import { Button } from "../../../components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../../../components/ui/card";
import { ConfirmDialog, type ConfirmRequest } from "../../../components/ui/confirm-dialog";
import { DataTable, type DataColumn } from "../../../components/ui/data-table";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "../../../components/ui/dialog";
import { Input } from "../../../components/ui/input";
import { Label } from "../../../components/ui/label";
import { Popover, PopoverContent, PopoverTrigger } from "../../../components/ui/popover";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "../../../components/ui/sheet";
import { Switch } from "../../../components/ui/switch";
import { Textarea } from "../../../components/ui/textarea";
import { asApiError } from "../../../lib/api-errors";
import { ConnectionPicker, useConnections } from "../access/ConnectionPicker";
import type { AskMode } from "../../../api/app";
import { ASK_MODE_LABELS } from "../../ask/askModes";
import { KnowledgeNav } from "./KnowledgeNav";

const KEY = ["gold-examples"];
const RUNS = ["eval-runs"];
const TRIGGER: Record<EvalRun["trigger"], string> = {
  manual: "Run by hand", nightly: "Nightly", model_approval: "After model approval", llm_settings: "After an AI settings change",
};

// Where a suggestion came from (US-175: knowledge grows from use).
const ORIGIN: Partial<Record<GoldExample["origin"], string>> = { clarification: "Clarified in Ask by", freehand: "Certified free-hand question by" };
const measureOf = (g: GoldExample) => g.expected_spec.measure.ref.replace(/^measure:/, "");
const tagsText = (t: string[]) => t.join(", ");
const parseTags = (s: string) => s.split(",").map((t) => t.trim()).filter(Boolean);

function ResultBadge({ g }: { g: GoldExample }) {
  if (g.last_passed == null) return <span className="text-small text-ink-muted">Not run</span>;
  return g.last_passed ? <Badge variant="certified">Pass</Badge> : <Badge variant="stale">Fail</Badge>;
}

function DiffList({ diff }: { diff: SpecDiff[] }) {
  const show = (v: unknown) => (v == null || (Array.isArray(v) && v.length === 0) ? "nothing" : JSON.stringify(v));
  return (
    <ul className="grid gap-1 text-small">
      {diff.map((d) => (
        <li key={d.path} className="font-mono">
          <span className="font-sans font-medium">{d.path}</span>: expected {show(d.expected)}, got {show(d.actual)}
        </li>
      ))}
    </ul>
  );
}

/**
 * Admin → Knowledge → Gold examples (US-056, US-057; SCR-13; FEAT-015): verified question → Query Spec pairs. They
 * are few-shot context for the planner and the ground-truth suite. Creators suggest them from answers; Admins approve.
 * "Evaluate" plans every approved question the way Ask does and reports the pass rate by tag against the threshold,
 * with a spec diff for each failure. A low pass rate warns; it never blocks.
 */
export function GoldExamplesPage() {
  const qc = useQueryClient();
  const [params, setParams] = useSearchParams();
  const q = useQuery({ queryKey: KEY, queryFn: () => api<{ items: GoldExample[]; threshold: number }>("/api/v1/knowledge/gold-examples") });
  const runs = useQuery({
    queryKey: RUNS,
    queryFn: () => api<{ items: EvalRun[]; threshold: number }>("/api/v1/knowledge/eval-runs?limit=10"),
    refetchInterval: (query) => (query.state.data?.items.some((r) => r.status === "queued" || r.status === "running") ? 3000 : false),
  });
  const [editing, setEditing] = useState<GoldExample | "new" | null>(null);
  const [selected, setSelected] = useState<GoldExample[]>([]);
  const [confirm, setConfirm] = useState<ConfirmRequest | null>(null);
  const [importing, setImporting] = useState(false);
  const refresh = () => { void qc.invalidateQueries({ queryKey: KEY }); void qc.invalidateQueries({ queryKey: RUNS }); };

  const review = useMutation({
    mutationFn: (v: { decision: "approve" | "reject"; ids: string[] }) =>
      api<{ updated: string[]; skipped: { id: string; reason: string }[] }>("/api/v1/knowledge/gold-examples/review", { method: "POST", body: v }),
    onSuccess: (out, v) => {
      refresh();
      setSelected([]);
      const n = out.updated.length;
      toast.success(`${n} ${n === 1 ? "example" : "examples"} ${v.decision === "approve" ? "approved" : "rejected"}`);
      if (out.skipped.length) toast.warning(`${out.skipped.length} skipped: ${out.skipped[0]!.reason}`);
    },
    onError: (e) => toast.error(asApiError(e, "The review could not be saved.").message),
  });
  const remove = useMutation({
    mutationFn: (g: GoldExample) => api(`/api/v1/knowledge/gold-examples/${g.id}`, { method: "DELETE" }),
    onSuccess: () => { refresh(); setConfirm(null); toast.success("Gold example deleted"); },
    onError: (e) => toast.error(asApiError(e, "The example could not be deleted.").message),
  });
  // US-259: measure one Ask answer mode on the same questions, whatever each source is set to ("" = each its own)
  const [mode, setMode] = useState<AskMode | "">("");
  const evaluate = useMutation({
    mutationFn: () => api<{ run_id: string }>("/api/v1/knowledge/gold-examples/evaluate", { method: "POST", body: mode ? { ask_mode: mode } : {} }),
    onSuccess: () => { refresh(); toast.success("Evaluation started — results appear here when it finishes"); },
    onError: (e) => toast.error(asApiError(e, "The evaluation could not start.").message),
  });

  const items = q.data?.items ?? [];
  const threshold = q.data?.threshold ?? runs.data?.threshold ?? 0.8;
  const suggested = items.filter((g) => g.status === "suggested");
  const latest = runs.data?.items[0];
  const busy = latest?.status === "queued" || latest?.status === "running";
  const openRun = params.get("run");

  const columns = useMemo<DataColumn<GoldExample>[]>(() => [
    {
      id: "question", header: "Question", alwaysVisible: true, value: (g) => g.question,
      cell: (g) => (
        <span className="grid gap-0.5">
          {g.turns && g.turns.length > 0 && <span className="text-small text-ink-muted">Follow-up after: {g.turns.map((t) => `“${t}”`).join(" → ")}</span>}
          <span className="font-medium">{g.question}</span>
          <span className="font-mono text-small text-ink-muted">{measureOf(g)}{g.use_for_fewshot ? "" : " · not used as an example"}</span>
          {g.last_passed === false && g.last_diff && g.last_diff.length > 0 && <DiffList diff={g.last_diff} />}
        </span>
      ),
    },
    { id: "shape", header: "Kind of answer", filter: "select", value: (g) => (g.shape ? SHAPE_LABEL[g.shape] : ""), cell: (g) => (g.shape ? <span className="text-small">{SHAPE_LABEL[g.shape]}</span> : null) },
    { id: "tags", header: "Tags", filter: "select", value: (g) => tagsText(g.tags), cell: (g) => <span className="flex flex-wrap gap-1">{g.tags.map((t) => <Badge key={t} variant="outline">{t}</Badge>)}</span> },
    {
      id: "status", header: "Status", filter: "select", value: (g) => g.status,
      cell: (g) => g.status === "approved" ? <Badge variant="certified">Approved</Badge> : g.status === "suggested" ? (
        <span className="grid gap-0.5"><Badge variant="stale">Suggested</Badge>{g.origin === "pack_draft" ? <span className="text-small text-ink-muted">PMO draft set</span> : g.suggested_by_name ? <span className="text-small text-ink-muted">{ORIGIN[g.origin] ?? "by"} {g.suggested_by_name}</span> : null}</span>
      ) : <Badge variant="outline">Rejected</Badge>,
    },
    { id: "result", header: "Last run", filter: "select", value: (g) => (g.last_passed == null ? "not run" : g.last_passed ? "pass" : "fail"), cell: (g) => <ResultBadge g={g} /> },
    { id: "connection", header: "Connection", hidden: true, value: (g) => g.connection_name ?? "" },
    {
      id: "actions", header: "Actions", alwaysVisible: true, sortable: false, value: () => "",
      cell: (g) => (
        <span className="flex flex-wrap gap-x-3">
          {g.status === "suggested" && (
            <>
              <Button variant="link" size="sm" onClick={() => review.mutate({ decision: "approve", ids: [g.id] })}><Check aria-hidden />Approve</Button>
              <Button variant="link" size="sm" onClick={() => review.mutate({ decision: "reject", ids: [g.id] })}><X aria-hidden />Reject</Button>
            </>
          )}
          <Button variant="link" size="sm" aria-label={`Edit ${g.question}`} onClick={() => setEditing(g)}><Pencil aria-hidden />Edit</Button>
          <Button variant="link" size="sm" className="text-status-danger" aria-label={`Delete ${g.question}`} onClick={() => setConfirm({
            title: "Delete this gold example?", confirm: "Delete example", danger: true, onConfirm: () => remove.mutate(g),
            body: `“${g.question}” leaves the evaluation suite and the planner's examples. Past runs keep its result.`,
          })}><Trash2 aria-hidden />Delete</Button>
        </span>
      ),
    },
  ], [review, remove]);

  const selectedSuggested = selected.filter((g) => g.status === "suggested");
  return (
    <div className="tl grid gap-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="grid gap-1">
          <h1 className="text-title">Knowledge</h1>
          <p className="max-w-prose text-ink-muted">Gold examples are questions with the answer a person has checked. They teach the planner and measure how often Ask gets it right.</p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button variant="secondary" onClick={() => setImporting(true)}><Download aria-hidden />Load PMO draft set</Button>
          <Button variant="secondary" onClick={() => setEditing("new")}><Plus aria-hidden />Add example</Button>
          <select className="rounded-md border border-line bg-surface px-2 py-1" value={mode} onChange={(e) => setMode(e.target.value as AskMode | "")} aria-label="Answer mode to evaluate">
            <option value="">Each source's own mode</option>
            {(Object.keys(ASK_MODE_LABELS) as AskMode[]).map((m) => <option key={m} value={m}>{ASK_MODE_LABELS[m]}</option>)}
          </select>
          <Button disabled={busy || evaluate.isPending || !items.some((g) => g.status === "approved")} onClick={() => evaluate.mutate()}>
            <Play aria-hidden />{busy ? "Evaluating…" : "Evaluate"}
          </Button>
        </div>
      </div>
      <KnowledgeNav />
      <RunSummary run={latest} threshold={threshold} onOpen={(id) => setParams((p) => { const n = new URLSearchParams(p); n.set("run", id); return n; })} />
      {suggested.length > 0 && <Alert>{suggested.length === 1 ? "1 suggestion is" : `${suggested.length} suggestions are`} waiting for review. Approved examples join the next evaluation.</Alert>}
      <DataTable
        data={items}
        columns={columns}
        label="Gold examples"
        getRowId={(g) => g.id}
        rowLabel={(g) => g.question}
        loading={q.isLoading}
        error={q.error && <FormAlert error={asApiError(q.error)} />}
        empty={{ title: "No gold examples yet", body: "Load the PMO draft set, add one, or save an answer from Ask as a gold example." }}
        onSelectionChange={setSelected}
        toolbar={selectedSuggested.length > 0 ? (
          <span className="flex gap-2">
            <Button size="sm" onClick={() => review.mutate({ decision: "approve", ids: selectedSuggested.map((g) => g.id) })}>Approve {selectedSuggested.length}</Button>
            <Button size="sm" variant="secondary" onClick={() => review.mutate({ decision: "reject", ids: selectedSuggested.map((g) => g.id) })}>Reject {selectedSuggested.length}</Button>
          </span>
        ) : undefined}
        search={items.length > 8}
      />
      <RunHistory runs={runs.data?.items ?? []} onOpen={(id) => setParams((p) => { const n = new URLSearchParams(p); n.set("run", id); return n; })} />
      <GoldDialog value={editing} onClose={() => setEditing(null)} onSaved={refresh} />
      <ImportDialog open={importing} onClose={() => setImporting(false)} onDone={refresh} />
      <RunSheet runId={openRun} onClose={() => setParams((p) => { const n = new URLSearchParams(p); n.delete("run"); return n; })} />
      <ConfirmDialog request={confirm} onClose={() => setConfirm(null)} busy={remove.isPending} />
    </div>
  );
}

function RunSummary({ run, threshold, onOpen }: { run: EvalRun | undefined; threshold: number; onOpen: (id: string) => void }) {
  return (
    <Card>
      <CardHeader className="flex flex-row flex-wrap items-start justify-between gap-2">
        <div className="grid gap-1">
          <CardTitle>Answer accuracy</CardTitle>
          <CardDescription>
            {run ? `${TRIGGER[run.trigger]} · ${formatRelative(run.finished_at ?? run.created_at)}${run.review == null ? "" : ` · LLM review ${run.review ? "on" : "off"}`}${run.ask_mode ? ` · ${ASK_MODE_LABELS[run.ask_mode]}` : ""}` : "No evaluation yet."} Threshold <ThresholdControl value={threshold} />
          </CardDescription>
        </div>
        {run?.status === "done" && <Button variant="link" size="sm" onClick={() => onOpen(run.id)}>See every result</Button>}
      </CardHeader>
      {run && (
        <CardContent className="grid gap-3">
          {run.status === "failed" && <Alert variant="danger">{run.error ?? "The evaluation stopped."}</Alert>}
          {(run.status === "queued" || run.status === "running") && <p className="text-ink-muted">{run.status === "running" && run.total > 0 ? `Evaluating ${run.total} questions… This can take a while; the page updates when it finishes.` : "Evaluating…"}</p>}
          {run.status === "done" && (
            <>
              <dl className="flex flex-wrap gap-6">
                <Metric label={run.plan_only ? "Spec match (plan only)" : "Execution accuracy"} value={percent(run.pass_rate)} note={run.plan_only ? `${run.passed} of ${run.total} planned as expected; no rows fetched` : `${run.passed} of ${run.total} give the expected rows`} warn={!!run.below_threshold} />
                {run.spec_match_rate != null && <Metric label="Spec match" value={percent(run.spec_match_rate)} note="planned exactly as expected" />}
                <Metric label="Retrieval recall@8" value={percent(run.recall_at_8)} note={run.recall_by_kind?.dimension != null ? `dimensions ${percent(run.recall_by_kind.dimension)}` : undefined} />
                {run.mrr != null && <Metric label="MRR" value={run.mrr.toFixed(2)} note={run.ndcg_at_8 != null ? `nDCG@8 ${run.ndcg_at_8.toFixed(2)}` : undefined} />}
                {run.value_linking?.rate != null && <Metric label="Value linking" value={percent(run.value_linking.rate)} note={`${run.value_linking.correct} of ${run.value_linking.total} questions with filters`} />}
              </dl>
              <FailureCauses byError={run.by_error} />
              {run.by_set?.paraphrase && <SetComparison original={run.by_set.original} paraphrase={run.by_set.paraphrase} planOnly={!!run.plan_only} />}
              {run.below_threshold && <Alert variant="warn">Below the {percent(threshold)} threshold. Nothing is blocked — review the failures and the planner's look-alikes.</Alert>}
              <table className="w-full max-w-xl text-small">
                <caption className="sr-only">Pass rate by tag</caption>
                <thead><tr className="text-left text-ink-muted"><th className="py-1 font-medium">Tag</th><th className="font-medium">Passed</th><th className="w-1/2 font-medium">Rate</th></tr></thead>
                <tbody>
                  {Object.entries(run.by_tag).sort(([a], [b]) => a.localeCompare(b)).map(([tag, t]) => (
                    <tr key={tag} className="border-t border-line">
                      <td className="py-1">{tag}</td>
                      <td>{t.passed} / {t.total}</td>
                      <td>
                        <span className="flex items-center gap-2">
                          <span className="h-1.5 flex-1 rounded-full bg-surface-sunken" aria-hidden><span className={`block h-full rounded-full ${t.pass_rate < threshold ? "bg-status-warn" : "bg-accent"}`} style={{ width: `${t.pass_rate * 100}%` }} /></span>
                          {percent(t.pass_rate)}
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </>
          )}
        </CardContent>
      )}
    </Card>
  );
}

const count = (n: number, total: number) => `${n} of ${total}`;
const rate = (g: GroupScores | undefined, pick: (g: GroupScores) => number) => (g && g.total ? `${percent(pick(g) / g.total)} (${count(pick(g), g.total)})` : "—");

/**
 * US-190: the held-out paraphrases (wording nobody tuned on) beside the original gold questions, then the paraphrases
 * by planner path. A confident wrong answer is the worst outcome; asking with the right option is the next best.
 */
function SetComparison({ original, paraphrase, planOnly }: { original?: SetScores; paraphrase: SetScores; planOnly: boolean }) {
  const rows: { label: string; note?: string; value: (g: GroupScores | undefined) => string }[] = [
    { label: planOnly ? "Planned as expected" : "Right answer", value: (g) => rate(g, (x) => x.passed) },
    { label: "Confident wrong", note: "wrong at or above the confidence threshold", value: (g) => rate(g, (x) => x.confident_wrong) },
    { label: "Refused", value: (g) => rate(g, (x) => x.outcomes.refused ?? 0) },
    { label: "Asked to clarify", note: "right option offered", value: (g) => (g && g.total ? `${rate(g, (x) => x.outcomes.clarified ?? 0)} · ${g.clarify_right} right` : "—") },
    { label: "Required items found", note: "measure and dimensions among the candidates or retrieved", value: (g) => percent(g?.required_recall.found) },
    { label: "Planning time (median)", value: (g) => (g?.latency_ms ? `${(g.latency_ms.p50 / 1000).toFixed(1)} s` : "—") },
  ];
  const paths = (Object.entries(paraphrase.by_path) as [PlannerPath, GroupScores][]);
  return (
    <div className="grid gap-3">
      <table className="w-full max-w-2xl text-small">
        <caption className="text-left text-ink-muted">Original questions beside held-out paraphrases</caption>
        <thead><tr className="text-left text-ink-muted"><th className="py-1 font-medium">Measure</th><th className="font-medium">Original</th><th className="font-medium">Paraphrases</th></tr></thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.label} className="border-t border-line">
              <th scope="row" className="py-1 text-left font-normal">{r.label}{r.note && <span className="block text-ink-muted">{r.note}</span>}</th>
              <td>{r.value(original)}</td>
              <td className={r.label === "Confident wrong" && paraphrase.confident_wrong > 0 ? "text-status-warn" : undefined}>{r.value(paraphrase)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <table className="w-full max-w-2xl text-small">
        <caption className="text-left text-ink-muted">Paraphrases by planner path</caption>
        <thead><tr className="text-left text-ink-muted"><th className="py-1 font-medium">Path</th><th className="font-medium">Questions</th><th className="font-medium">Right</th><th className="font-medium">Confident wrong</th><th className="font-medium">Refused</th><th className="font-medium">Asked</th></tr></thead>
        <tbody>
          {paths.map(([path, g]) => (
            <tr key={path} className="border-t border-line">
              <th scope="row" className="py-1 text-left font-normal">{PATH_LABEL[path] ?? path}</th>
              <td>{g.total}</td>
              <td>{g.passed}</td>
              <td>{g.confident_wrong}</td>
              <td>{g.outcomes.refused ?? 0}</td>
              <td>{g.outcomes.clarified ?? 0}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function OutcomeBadges({ r }: { r: EvalResult }) {
  return (
    <>
      {r.confident_wrong && <Badge variant="stale">Confident wrong</Badge>}
      {r.status === "unsupported" && <Badge variant="outline">Refused</Badge>}
      {r.status === "clarify" && <Badge variant="outline">{r.clarify_right ? "Asked · right option offered" : "Asked · right option missing"}</Badge>}
      {r.tags.includes("paraphrase") && <Badge variant="outline">Paraphrase</Badge>}
    </>
  );
}

function resultNote(r: EvalResult): string {
  const parts = [r.planner ? `Planned by ${PATH_LABEL[r.planner as PlannerPath]?.toLowerCase() ?? r.planner}` : "Not planned"];
  if (r.retrieval_hit != null) parts.push(`retrieval ${r.retrieval_hit ? "found" : "missed"} the measure`);
  if (r.required?.measure) parts.push(`linking ${r.required.measure.linked ? "offered" : "did not offer"} the measure`);
  if (r.latency_ms != null) parts.push(`${(r.latency_ms / 1000).toFixed(1)} s`);
  if (r.tags.length) parts.push(r.tags.join(", "));
  return parts.join(" · ");
}

function Metric({ label, value, note, warn = false }: { label: string; value: string; note?: string; warn?: boolean }) {
  return (
    <div>
      <dt className="text-small text-ink-muted">{label}</dt>
      <dd className={`text-title ${warn ? "text-status-warn" : ""}`}>{value}</dd>
      {note && <dd className="text-small text-ink-muted">{note}</dd>}
    </div>
  );
}

function FailureCauses({ byError }: { byError: EvalRun["by_error"] }) {
  const rows = Object.entries(byError ?? {}).filter(([, n]) => (n ?? 0) > 0).sort(([, a], [, b]) => (b ?? 0) - (a ?? 0));
  if (!rows.length) return null;
  return (
    <div className="grid gap-1 text-small">
      <span className="text-ink-muted">Failures by cause</span>
      <ul className="flex flex-wrap gap-2" aria-label="Failures by cause">
        {rows.map(([cls, n]) => <li key={cls}><Badge variant="outline">{ERROR_LABEL[cls as ErrorClass] ?? cls}: {n}</Badge></li>)}
      </ul>
    </div>
  );
}

function ThresholdControl({ value }: { value: number }) {
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState(String(Math.round(value * 100)));
  const save = useMutation({
    mutationFn: (pct: number) => api("/api/v1/knowledge/policy", { method: "PUT", body: { eval_threshold: pct / 100 } }),
    onSuccess: () => { void qc.invalidateQueries({ queryKey: KEY }); void qc.invalidateQueries({ queryKey: RUNS }); setOpen(false); toast.success("Threshold saved"); },
    onError: (e) => toast.error(asApiError(e, "The threshold could not be saved.").message),
  });
  const pct = Number(draft);
  const valid = Number.isFinite(pct) && pct >= 0 && pct <= 100;
  return (
    <Popover open={open} onOpenChange={(o) => { if (o) setDraft(String(Math.round(value * 100))); setOpen(o); }}>
      <PopoverTrigger asChild><Button variant="link" size="sm" className="h-auto p-0" aria-label={`Threshold ${percent(value)}, change`}>{percent(value)}</Button></PopoverTrigger>
      <PopoverContent align="start" className="grid w-64 gap-2">
        <form className="grid gap-2" onSubmit={(e) => { e.preventDefault(); if (valid) save.mutate(pct); }}>
          <Label htmlFor="eval-threshold">Pass-rate threshold (%)</Label>
          <Input id="eval-threshold" type="number" min={0} max={100} value={draft} onChange={(e) => setDraft(e.target.value)} />
          <p className="text-small text-ink-muted">Below it, Admins are warned after each run. To be agreed with the product owner before UAT.</p>
          <Button type="submit" size="sm" disabled={!valid || save.isPending}>Save</Button>
        </form>
      </PopoverContent>
    </Popover>
  );
}

function RunHistory({ runs, onOpen }: { runs: EvalRun[]; onOpen: (id: string) => void }) {
  if (runs.length < 2) return null;
  return (
    <section className="grid gap-2" aria-labelledby="run-history">
      <h2 id="run-history" className="text-heading">Earlier runs</h2>
      <ul className="grid gap-1 text-small">
        {runs.slice(1).map((r) => (
          <li key={r.id} className="flex flex-wrap items-center gap-2">
            <Button variant="link" size="sm" className="h-auto p-0" disabled={r.status !== "done"} onClick={() => onOpen(r.id)}>{formatRelative(r.finished_at ?? r.created_at)}</Button>
            <span className="text-ink-muted">{TRIGGER[r.trigger]}</span>
            <span>{r.status === "done" ? `${percent(r.pass_rate)} (${r.passed}/${r.total})` : r.status}</span>
            {r.below_threshold && <Badge variant="stale">Below threshold</Badge>}
          </li>
        ))}
      </ul>
    </section>
  );
}

function RunSheet({ runId, onClose }: { runId: string | null; onClose: () => void }) {
  const q = useQuery({ queryKey: ["eval-run", runId], queryFn: () => api<EvalRunDetail>(`/api/v1/knowledge/eval-runs/${runId}`), enabled: !!runId });
  const run = q.data;
  return (
    <Sheet open={!!runId} onOpenChange={(o) => !o && onClose()}>
      <SheetContent className="w-full max-w-2xl overflow-y-auto">
        <SheetHeader>
          <SheetTitle>Evaluation results</SheetTitle>
          <SheetDescription>{run ? `${percent(run.pass_rate)} passed (${run.passed} of ${run.total}) · ${TRIGGER[run.trigger]}` : "Loading…"}</SheetDescription>
        </SheetHeader>
        {q.error && <div className="p-4"><FormAlert error={asApiError(q.error)} /></div>}
        {run?.shape_grid && <div className="px-4 pt-4"><ShapeGrid grid={run.shape_grid} /></div>}
        {run && (
          <ol className="grid gap-2 p-4" aria-label="Results">
            {run.results.map((r) => (
              <li key={r.id} className="grid gap-1 rounded-md border border-line p-3">
                <span className="flex flex-wrap items-center gap-2">
                  {r.passed ? <Badge variant="certified">Pass</Badge> : <Badge variant="stale">Fail</Badge>}
                  {r.passed && r.spec_match === false && <Badge variant="outline">Same rows, different spec</Badge>}
                  {!r.passed && r.error_class && <Badge variant="outline">{ERROR_LABEL[r.error_class]}</Badge>}
                  <OutcomeBadges r={r} />
                  <span className="font-medium">{r.question}</span>
                </span>
                <span className="text-small text-ink-muted">{resultNote(r)}</span>
                {r.error && <span className="text-small">{r.error}</span>}
                {r.diff.length > 0 && (r.passed ? <details className="text-small"><summary className="cursor-pointer text-ink-muted">How the spec differs</summary><DiffList diff={r.diff} /></details> : <DiffList diff={r.diff} />)}
              </li>
            ))}
          </ol>
        )}
      </SheetContent>
    </Sheet>
  );
}

/**
 * US-250: right, confident-wrong, asked and refused per kind of answer — one row per shape, totalling the run — and,
 * with a tag picked, the same for that tag's examples only (the shape × tag grid), so a whole kind of question (lists)
 * can't go missing unnoticed.
 */
export function ShapeGrid({ grid }: { grid: ShapeGridData }) {
  const [tag, setTag] = useState("");
  const rows = (Object.entries(grid.rows) as [AnswerShape | "unknown", ShapeGridData["total"]][])
    .map(([shape, all]) => [shape, tag ? grid.cells[shape]?.[tag] : all] as const)
    .filter((r): r is readonly [AnswerShape | "unknown", ShapeGridData["total"]] => !!r[1]);
  const sum = (k: keyof ShapeGridData["total"]) => rows.reduce((n, [, c]) => n + c[k], 0);
  return (
    <div className="grid gap-2">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="text-small text-ink-muted" id="shape-grid-caption">By kind of answer</span>
        <label className="flex items-center gap-2 text-small">
          Tag
          <select className="rounded-md border border-line bg-surface px-2 py-1" value={tag} onChange={(e) => setTag(e.target.value)} aria-label="Show one tag's examples">
            <option value="">All examples</option>
            {grid.tags.map((t) => <option key={t} value={t}>{t}</option>)}
          </select>
        </label>
      </div>
      <table className="w-full text-small" aria-labelledby="shape-grid-caption">
        <thead><tr className="text-left text-ink-muted"><th className="py-1 font-medium">Kind of answer</th><th className="font-medium">Questions</th><th className="font-medium">Right</th><th className="font-medium">Confident wrong</th><th className="font-medium">Asked</th><th className="font-medium">Refused</th></tr></thead>
        <tbody>
          {rows.map(([shape, c]) => (
            <tr key={shape} className="border-t border-line">
              <th scope="row" className="py-1 text-left font-normal">{SHAPE_LABEL[shape] ?? shape}</th>
              <td>{c.total}</td>
              <td className={c.right === 0 && c.total > 0 ? "text-status-warn" : undefined}>{c.right}</td>
              <td className={c.confident_wrong > 0 ? "text-status-warn" : undefined}>{c.confident_wrong}</td>
              <td>{c.clarified}</td>
              <td>{c.refused}</td>
            </tr>
          ))}
        </tbody>
        <tfoot><tr className="border-t border-line font-medium"><th scope="row" className="py-1 text-left">All</th><td>{sum("total")}</td><td>{sum("right")}</td><td>{sum("confident_wrong")}</td><td>{sum("clarified")}</td><td>{sum("refused")}</td></tr></tfoot>
      </table>
    </div>
  );
}

function GoldDialog({ value, onClose, onSaved }: { value: GoldExample | "new" | null; onClose: () => void; onSaved: () => void }) {
  const editing = value && value !== "new" ? value : null;
  const { data: connections } = useConnections();
  const [picked, setPicked] = useState("");
  const connection = picked || (connections?.items.length === 1 ? connections.items[0]!.id : "");
  const [question, setQuestion] = useState("");
  const [spec, setSpec] = useState("");
  const [tags, setTags] = useState("");
  const [fewshot, setFewshot] = useState(true);
  const [specError, setSpecError] = useState<string | null>(null);
  const [opened, setOpened] = useState<GoldExample | "new" | null>(null);
  if (value !== opened) {
    setOpened(value);
    setQuestion(editing?.question ?? "");
    setSpec(editing ? JSON.stringify(editing.expected_spec, null, 2) : '{\n  "measure": {"ref": "measure:open_projects"}\n}');
    setTags(editing ? tagsText(editing.tags) : "");
    setFewshot(editing?.use_for_fewshot ?? true);
    setSpecError(null);
  }
  const save = useMutation({
    mutationFn: (expected: Record<string, unknown>) => editing
      ? api<GoldExample>(`/api/v1/knowledge/gold-examples/${editing.id}`, { method: "PATCH", body: { question, expected_spec: expected, tags: parseTags(tags), use_for_fewshot: fewshot } })
      : api<GoldExample>("/api/v1/knowledge/gold-examples", { method: "POST", body: { question, expected_spec: expected, tags: parseTags(tags), use_for_fewshot: fewshot, connection_id: connection } }),
    onSuccess: () => { onSaved(); toast.success(editing ? "Gold example saved" : "Gold example added"); onClose(); },
  });
  const submit = () => {
    try {
      const parsed = JSON.parse(spec) as Record<string, unknown>;
      setSpecError(null);
      save.mutate(parsed);
    } catch {
      setSpecError("The expected spec is not valid JSON.");
    }
  };
  return (
    <Dialog open={!!value} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="max-w-xl">
        <DialogHeader>
          <DialogTitle>{editing ? "Edit gold example" : "Add a gold example"}</DialogTitle>
          <DialogDescription>The spec is checked against the connection's approved model when you save.</DialogDescription>
        </DialogHeader>
        <form id="gold-form" className="grid gap-3" onSubmit={(e) => { e.preventDefault(); submit(); }}>
          {!editing && <ConnectionPicker id="gold-connection" value={connection} onChange={setPicked} />}
          <div className="grid gap-1.5">
            <Label htmlFor="gold-question">Question</Label>
            <Input id="gold-question" value={question} minLength={3} maxLength={500} required onChange={(e) => setQuestion(e.target.value)} />
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor="gold-spec">Expected Query Spec (JSON)</Label>
            <Textarea id="gold-spec" className="min-h-40 font-mono text-small" value={spec} onChange={(e) => setSpec(e.target.value)} aria-invalid={!!specError} aria-describedby={specError ? "gold-spec-error" : undefined} />
            {specError && <p id="gold-spec-error" className="text-small text-status-danger">{specError}</p>}
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor="gold-tags">Tags <span className="text-ink-muted">(comma-separated)</span></Label>
            <Input id="gold-tags" value={tags} onChange={(e) => setTags(e.target.value)} placeholder="look_alike, date_field" />
          </div>
          <label className="flex items-center gap-2 text-small">
            <Switch checked={fewshot} onCheckedChange={setFewshot} />
            Use as an example for the planner
          </label>
          {save.error && <FormAlert error={asApiError(save.error, "The example could not be saved.")} />}
        </form>
        <DialogFooter>
          <Button variant="secondary" onClick={onClose}>Cancel</Button>
          <Button type="submit" form="gold-form" disabled={save.isPending || !question.trim() || (!editing && !connection)}>Save</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function ImportDialog({ open, onClose, onDone }: { open: boolean; onClose: () => void; onDone: () => void }) {
  const { data } = useConnections();
  const [connection, setConnection] = useState("");
  const chosen = connection || (data?.items.length === 1 ? data.items[0]!.id : "");
  const run = useMutation({
    mutationFn: () => api<{ created: number; skipped: { question: string; reason: string }[] }>("/api/v1/knowledge/gold-examples/import-pack", { method: "POST", body: { connection_id: chosen } }),
    onSuccess: (out) => {
      onDone();
      toast.success(`${out.created} draft ${out.created === 1 ? "example" : "examples"} added as suggestions`);
      onClose();
    },
  });
  return (
    <Dialog open={open} onOpenChange={(o) => !o && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Load the PMO draft set</DialogTitle>
          <DialogDescription>About 30 PMO questions with their expected answers — look-alikes, date fields, statuses and demand gates. They arrive as suggestions for you (and the PMO lead) to check and approve.</DialogDescription>
        </DialogHeader>
        <ConnectionPicker id="import-connection" value={chosen} onChange={setConnection} />
        {run.error && <FormAlert error={asApiError(run.error, "The draft set could not be loaded.")} />}
        <DialogFooter>
          <Button variant="secondary" onClick={onClose}>Cancel</Button>
          <Button disabled={!chosen || run.isPending} onClick={() => run.mutate()}>Load as suggestions</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
