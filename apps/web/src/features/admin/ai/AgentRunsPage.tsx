import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  BookOpen, Bot, CircleAlert, CircleCheck, Copy, Database, Download, Eye, Flag, GitBranch, Loader2, RotateCcw, Ruler,
  ShieldCheck, Sparkles, Trash2, Workflow, type LucideIcon,
} from "lucide-react";
import { useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { toast } from "sonner";

import { api } from "../../../api/client";
import { FormAlert } from "../../../components/ui/alert";
import { Badge } from "../../../components/ui/badge";
import { Button } from "../../../components/ui/button";
import { ConfirmDialog, type ConfirmRequest } from "../../../components/ui/confirm-dialog";
import { DataTable, type DataColumn } from "../../../components/ui/data-table";
import { FilterChip } from "../../../components/ui/filter-chip";
import { Input } from "../../../components/ui/input";
import { Label } from "../../../components/ui/label";
import { NativeSelect } from "../../../components/ui/native-select";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "../../../components/ui/sheet";
import { Switch } from "../../../components/ui/switch";
import { asApiError } from "../../../lib/api-errors";
import { cn } from "../../../lib/utils";

export interface AgentRun {
  id: string;
  kind: string;
  title: string;
  agents: string[];
  turn_id: string | null;
  object_ref: string | null;
  user: { id: string | null; name: string | null };
  outcome: "running" | "ok" | "fallback" | "clarify" | "refused" | "error";
  llm_calls: number;
  prompt_tokens: number;
  completion_tokens: number;
  started_at: string;
  ended_at: string | null;
  duration_ms: number | null;
  text_kept: boolean;
}

export interface AgentStep {
  seq: number;
  agent: string | null;
  kind: string;
  label: string;
  status: "ok" | "failed" | "blocked";
  started_at: string;
  duration_ms: number | null;
  detail: Record<string, unknown>;
  task: string | null;
  provider: string | null;
  model: string | null;
  prompt_tokens: number | null;
  completion_tokens: number | null;
  has_text: boolean;
  text_cleared: boolean;
  text_cut: boolean;
}

interface RunDetail extends AgentRun {
  steps: AgentStep[];
  agent_names: Record<string, string>;
  related: { id: string; kind: string; title: string; started_at: string }[];
}

interface StepText {
  seq: number;
  prompt: { role: string; content: string }[] | null;
  reply: string | null;
  cut: boolean;
  cleared_at: string | null;
  model: string | null;
}

interface TraceSettings { capture_text: boolean; text_days: number; run_days: number }

type Facets = Record<"agent" | "kind" | "outcome", Record<string, number>>;

export const KIND_LABELS: Record<string, string> = {
  ask: "Ask", model_assist: "Model assistant", pack_mapping: "Pack mapping", certification: "Certification",
  custom_sql: "Custom SQL", report_plan: "Report planner", canvas_edit: "Canvas edit", agent: "Other",
};
export const OUTCOME_LABELS: Record<AgentRun["outcome"], string> = {
  running: "Running", ok: "Done", fallback: "Fell back", clarify: "Asked", refused: "Refused", error: "Error",
};
const AGENT_NAMES: Record<string, string> = {
  "AG-01": "Intent router", "AG-02": "Clarifier", "AG-03": "Query planner", "AG-04": "Model assistant",
  "AG-05": "Report planner", "AG-06": "Canvas editor", "AG-07": "Visual advisor", "AG-08": "Narrator",
  "AG-09": "Certification reviewer", "AG-10": "SQL drafter",
};
const STEP_ICONS: Record<string, LucideIcon> = {
  rule: Ruler, retrieve: BookOpen, llm_call: Sparkles, validate: ShieldCheck, retry: RotateCcw, fallback: GitBranch,
  decision: Workflow, query: Database, error: CircleAlert, done: Flag,
};
const STEP_KINDS: Record<string, string> = {
  rule: "Rule", retrieve: "Retrieval", llm_call: "LLM call", validate: "Check", retry: "Retry", fallback: "Fallback",
  decision: "Decision", query: "Query", error: "Error", done: "Done",
};

interface Filters { agent: string; kind: string; outcome: string; llm: string; q: string; turn_id: string; range: string }
const EMPTY: Filters = { agent: "", kind: "", outcome: "", llm: "", q: "", turn_id: "", range: "" };
const KEYS = Object.keys(EMPTY) as (keyof Filters)[];
const RANGES: Record<string, { label: string; hours: number }> = {
  "1h": { label: "Last hour", hours: 1 }, "24h": { label: "Last 24 hours", hours: 24 }, "7d": { label: "Last 7 days", hours: 168 },
};

function query(f: Filters, cursor?: string | null): string {
  const p = new URLSearchParams();
  for (const k of KEYS) if (f[k] && k !== "range") p.set(k, f[k]);
  const range = RANGES[f.range];
  if (range) p.set("from", new Date(Date.now() - range.hours * 3_600_000).toISOString());
  if (cursor) p.set("cursor", cursor);
  return p.toString();
}

const when = (iso: string) => new Date(iso).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "medium" });
export const took = (ms: number | null) => (ms === null ? "—" : ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(1)} s`);
const agentName = (a: string) => (AGENT_NAMES[a] ? `${a} ${AGENT_NAMES[a]}` : a);

function OutcomeBadge({ outcome }: { outcome: AgentRun["outcome"] }) {
  const variant = outcome === "ok" ? "certified" : outcome === "running" || outcome === "clarify" ? "outline" : outcome === "error" || outcome === "refused" ? "stale" : "neutral";
  return (
    <Badge variant={variant} data-outcome={outcome}>
      {outcome === "running" && <Loader2 aria-hidden className="size-3.5 animate-spin" />}
      {OUTCOME_LABELS[outcome]}
    </Badge>
  );
}

/**
 * Agent runs (ENH-024, US-227 – US-229): every run of AG-01 to AG-10 with its steps, newest first. A run opens a
 * sheet with its step timeline; a model call shows what went to the model (masked, as sent) and its raw reply,
 * fetched on demand because each read is audited. "Live" refreshes every 3 seconds while runs happen (US-228).
 */
export function AgentRunsPage() {
  const [params, setParams] = useSearchParams();
  const filters = useMemo(() => Object.fromEntries(KEYS.map((k) => [k, params.get(k) ?? ""])) as unknown as Filters, [params]);
  const [live, setLive] = useState(false);
  const [openId, setOpenId] = useState<string | null>(null);
  const runs = useInfiniteQuery({
    queryKey: ["agent-runs", filters],
    queryFn: ({ pageParam }) => api<{ items: AgentRun[]; next_cursor: string | null }>(`/api/v1/admin/agent-runs?${query(filters, pageParam)}&limit=50`),
    initialPageParam: null as string | null,
    getNextPageParam: (last) => last.next_cursor,
    refetchInterval: live ? 3000 : false,
  });
  const facets = useQuery({
    queryKey: ["agent-run-facets", filters],
    queryFn: () => api<Facets>(`/api/v1/admin/agent-runs/facets?${query(filters)}`),
    refetchInterval: live ? 3000 : false,
  });
  const items = useMemo(() => runs.data?.pages.flatMap((p) => p.items) ?? [], [runs.data]);
  const set = (changes: Partial<Filters>) => setParams((p) => {
    const n = new URLSearchParams(p);
    for (const [k, v] of Object.entries(changes)) { if (v) n.set(k, v); else n.delete(k); }
    return n;
  });
  const applied = KEYS.filter((k) => filters[k] && ["turn_id", "q", "llm"].includes(k));

  const columns = useMemo<DataColumn<AgentRun>[]>(() => [
    { id: "started_at", header: "When", alwaysVisible: true, cell: (r) => <span className="whitespace-nowrap font-mono text-small tabular-nums">{when(r.started_at)}</span> },
    { id: "kind", header: "Work", value: (r) => KIND_LABELS[r.kind] ?? r.kind },
    { id: "title", header: "Request", value: (r) => r.title, cell: (r) => <span className="line-clamp-1 max-w-[28rem]">{r.title || "—"}</span> },
    { id: "agents", header: "Agents", value: (r) => r.agents.join(" "), cell: (r) => (
      <span className="flex flex-wrap gap-1">{r.agents.map((a) => <Badge key={a} variant="outline" title={AGENT_NAMES[a]} className="font-mono">{a}</Badge>)}</span>
    ) },
    { id: "user", header: "Person", value: (r) => r.user.name ?? "System" },
    { id: "outcome", header: "Outcome", value: (r) => OUTCOME_LABELS[r.outcome], cell: (r) => <OutcomeBadge outcome={r.outcome} /> },
    { id: "llm_calls", header: "LLM calls", value: (r) => r.llm_calls, cell: (r) => <span className="font-mono tabular-nums">{r.llm_calls}</span> },
    { id: "tokens", header: "Tokens", value: (r) => r.prompt_tokens + r.completion_tokens, cell: (r) => (
      <span className="font-mono text-small tabular-nums">{r.llm_calls ? (r.prompt_tokens + r.completion_tokens).toLocaleString() : "—"}</span>
    ) },
    { id: "duration", header: "Took", value: (r) => r.duration_ms ?? 0, cell: (r) => <span className="font-mono text-small tabular-nums">{took(r.duration_ms)}</span> },
  ], []);

  return (
    <section className="tl grid gap-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="grid gap-1">
          <h1 className="text-title">Agent runs</h1>
          <p className="text-ink-muted">Every run of the agents, step by step, with what went to the model and what it answered.</p>
        </div>
        <label className="flex h-control-md items-center gap-2">
          <Switch checked={live} onCheckedChange={setLive} aria-label="Live" />
          Live{live && <span className="text-small text-ink-muted">· refreshes every 3 s</span>}
        </label>
      </div>
      <TraceSettingsCard />
      <div className="grid gap-3 rounded-lg border border-line bg-surface-raised p-4" role="group" aria-label="Filter agent runs">
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
          <div className="grid gap-1">
            <Label htmlFor="runs-range">When</Label>
            <NativeSelect id="runs-range" value={filters.range} onChange={(e) => set({ range: e.target.value })}>
              <option value="">Any time</option>
              {Object.entries(RANGES).map(([k, r]) => <option key={k} value={k}>{r.label}</option>)}
            </NativeSelect>
          </div>
          <Facet id="runs-agent" label="Agent" value={filters.agent} counts={facets.data?.agent} name={agentName} onChange={(v) => set({ agent: v })} />
          <Facet id="runs-kind" label="Work" value={filters.kind} counts={facets.data?.kind} name={(k) => KIND_LABELS[k] ?? k} onChange={(v) => set({ kind: v })} />
          <Facet id="runs-outcome" label="Outcome" value={filters.outcome} counts={facets.data?.outcome} name={(k) => OUTCOME_LABELS[k as AgentRun["outcome"]] ?? k} onChange={(v) => set({ outcome: v })} />
          <div className="grid gap-1">
            <Label htmlFor="runs-q">Request contains</Label>
            <Input id="runs-q" defaultValue={filters.q} placeholder="Words of the question" onKeyDown={(e) => { if (e.key === "Enter") set({ q: e.currentTarget.value.trim() }); }}
              onBlur={(e) => { if (e.target.value.trim() !== filters.q) set({ q: e.target.value.trim() }); }} />
          </div>
        </div>
        <label className="flex h-control-md items-center gap-2">
          <Switch checked={filters.llm === "true"} onCheckedChange={(on) => set({ llm: on ? "true" : "" })} aria-label="Only runs that called a model" />
          Only runs that called a model
        </label>
        {applied.length > 0 && (
          <div className="flex flex-wrap items-center gap-2">
            {applied.map((k) => (
              <FilterChip key={k} field={k === "turn_id" ? "Answer" : k === "q" ? "Request" : "Model"} applied
                value={k === "turn_id" ? filters.turn_id.slice(0, 8) : k === "llm" ? "called" : filters[k]}
                onClear={() => set({ [k]: "" })} onClick={() => set({ [k]: "" })} />
            ))}
            <Button variant="link" size="sm" onClick={() => setParams({})}>Clear all</Button>
          </div>
        )}
      </div>
      <DataTable
        data={items}
        columns={columns}
        label="Agent runs"
        getRowId={(r) => r.id}
        serverPaged
        compact
        maxHeight={640}
        virtualizeAbove={50}
        loading={runs.isLoading}
        error={runs.error && <FormAlert error={asApiError(runs.error)} />}
        empty={{ title: "No agent runs match", body: "Ask a question, or change or clear the filters." }}
        onRowActivate={(r) => setOpenId(r.id)}
        rowLabel={(r) => `${KIND_LABELS[r.kind] ?? r.kind}: ${r.title}, ${OUTCOME_LABELS[r.outcome]}, ${when(r.started_at)}`}
        footer={
          <span className="flex items-center gap-3">
            <span>{items.length.toLocaleString()} runs loaded · newest first</span>
            {runs.hasNextPage && (
              <Button variant="secondary" size="sm" onClick={() => void runs.fetchNextPage()} disabled={runs.isFetchingNextPage}>
                {runs.isFetchingNextPage ? "Loading…" : "Load more"}
              </Button>
            )}
          </span>
        }
      />
      <Sheet open={!!openId} onOpenChange={(o) => !o && setOpenId(null)}>
        <SheetContent className="w-full overflow-y-auto sm:max-w-2xl">
          {openId && <RunDetailView runId={openId} live={live} onOpen={setOpenId} />}
        </SheetContent>
      </Sheet>
    </section>
  );
}

function Facet({ id, label, value, counts, name, onChange }: {
  id: string;
  label: string;
  value: string;
  counts?: Record<string, number>;
  name: (v: string) => string;
  onChange: (v: string) => void;
}) {
  const list = Object.entries(counts ?? {});
  const values = value && !list.some(([k]) => k === value) ? [[value, 0] as [string, number], ...list] : list;
  return (
    <div className="grid gap-1">
      <Label htmlFor={id}>{label}</Label>
      <NativeSelect id={id} value={value} onChange={(e) => onChange(e.target.value)}>
        <option value="">Any</option>
        {values.map(([k, n]) => <option key={k} value={k}>{name(k)} ({n.toLocaleString()})</option>)}
      </NativeSelect>
    </div>
  );
}

function TraceSettingsCard() {
  const qc = useQueryClient();
  const [confirm, setConfirm] = useState<ConfirmRequest | null>(null);
  const settings = useQuery({ queryKey: ["agent-run-settings"], queryFn: () => api<TraceSettings>("/api/v1/admin/agent-runs/settings") });
  const save = useMutation({
    mutationFn: (body: { capture_text: boolean; text_days: number }) => api<TraceSettings>("/api/v1/admin/agent-runs/settings", { method: "PUT", body }),
    onSuccess: (data) => qc.setQueryData(["agent-run-settings"], data),
  });
  const purge = useMutation({
    mutationFn: () => api<{ steps: number }>("/api/v1/admin/agent-runs/purge-text", { method: "POST" }),
    onSuccess: (r) => { toast.success(`Cleared the text of ${r.steps.toLocaleString()} model calls.`); void qc.invalidateQueries({ queryKey: ["agent-run"] }); },
  });
  const s = settings.data;
  if (!s) return null;
  return (
    <div className="flex flex-wrap items-center gap-x-6 gap-y-3 rounded-lg border border-line bg-surface-raised p-4" role="group" aria-label="What the log keeps">
      <label className="flex items-center gap-2">
        <Switch checked={s.capture_text} disabled={save.isPending} onCheckedChange={(on) => save.mutate({ capture_text: on, text_days: s.text_days })}
          aria-label="Keep what was sent to the model and its replies" />
        Keep what was sent to the model and its replies
      </label>
      <div className="flex items-center gap-2">
        <Label htmlFor="runs-days">Keep text for</Label>
        <NativeSelect id="runs-days" value={String(s.text_days)} disabled={save.isPending} onChange={(e) => save.mutate({ capture_text: s.capture_text, text_days: Number(e.target.value) })}>
          {[1, 3, 7, 14, 30, 60, 90].map((d) => <option key={d} value={d}>{d} {d === 1 ? "day" : "days"}</option>)}
        </NativeSelect>
      </div>
      <span className="text-small text-ink-muted">Runs and their steps are kept {s.run_days} days. Only Admins see the text; each look is in the audit log.</span>
      <Button variant="secondary" size="sm" className="ml-auto" disabled={purge.isPending} onClick={() => setConfirm({
        title: "Clear all model text now?",
        body: "Every prompt and reply kept in the agent run log is removed. The runs and their steps stay.",
        confirm: "Clear text", danger: true, onConfirm: () => purge.mutate(),
      })}><Trash2 aria-hidden />Clear text now</Button>
      <FormAlert error={save.error ? asApiError(save.error) : purge.error ? asApiError(purge.error) : null} />
      <ConfirmDialog request={confirm} onClose={() => setConfirm(null)} busy={purge.isPending} />
    </div>
  );
}

function RunDetailView({ runId, live, onOpen }: { runId: string; live: boolean; onOpen: (id: string) => void }) {
  const run = useQuery({
    queryKey: ["agent-run", runId],
    queryFn: () => api<RunDetail>(`/api/v1/admin/agent-runs/${runId}`),
    refetchInterval: (q) => (live || q.state.data?.outcome === "running" ? 3000 : false),
  });
  const exportJson = useMutation({
    mutationFn: async () => {
      const data = await api<unknown>(`/api/v1/admin/agent-runs/${runId}/export`);
      const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: "application/json" }));
      const a = document.createElement("a");
      a.href = url;
      a.download = `tela-agent-run-${runId.slice(0, 8)}.json`;
      a.click();
      URL.revokeObjectURL(url);
    },
  });
  if (run.isLoading) return <p className="p-4 text-ink-muted">Loading…</p>;
  if (run.error || !run.data) return <div className="p-4"><FormAlert error={asApiError(run.error)} /></div>;
  const r = run.data;
  return (
    <>
      <SheetHeader>
        <SheetTitle className="flex items-center gap-2"><Bot aria-hidden className="size-5" />{KIND_LABELS[r.kind] ?? r.kind}</SheetTitle>
        <SheetDescription>{r.title || "—"}</SheetDescription>
      </SheetHeader>
      <div className="grid gap-4 px-4 pb-4">
        <dl className="grid gap-x-4 gap-y-2 text-small sm:grid-cols-[max-content_1fr]">
          <dt className="text-ink-muted">Outcome</dt><dd><OutcomeBadge outcome={r.outcome} /></dd>
          <dt className="text-ink-muted">Started</dt><dd>{when(r.started_at)} · took {took(r.duration_ms)}</dd>
          <dt className="text-ink-muted">Person</dt><dd>{r.user.name ?? "System"}</dd>
          <dt className="text-ink-muted">Agents</dt><dd>{r.agents.map(agentName).join(", ") || "—"}</dd>
          <dt className="text-ink-muted">Model calls</dt>
          <dd>{r.llm_calls} · {r.prompt_tokens.toLocaleString()} prompt + {r.completion_tokens.toLocaleString()} reply tokens{!r.text_kept && " · text not kept"}</dd>
          {r.object_ref && <><dt className="text-ink-muted">About</dt><dd className="font-mono break-all">{r.object_ref}</dd></>}
        </dl>
        <div className="flex flex-wrap gap-2">
          {r.turn_id && <Button asChild variant="secondary" size="sm"><Link to={`/ask?turn=${r.turn_id}`}>Open in Ask</Link></Button>}
          <Button variant="secondary" size="sm" onClick={() => exportJson.mutate()} disabled={exportJson.isPending}><Download aria-hidden />Export JSON</Button>
        </div>
        {r.related.length > 0 && (
          <nav aria-label="Other runs of this answer" className="grid gap-1 text-small">
            {r.related.map((x) => <Button key={x.id} variant="link" size="sm" className="justify-self-start" onClick={() => onOpen(x.id)}>{KIND_LABELS[x.kind] ?? x.kind}: {x.title}</Button>)}
          </nav>
        )}
        <ol className="grid gap-2" aria-label="Steps">
          {r.steps.map((s) => <StepItem key={s.seq} runId={r.id} step={s} last={r.outcome === "running" && s.seq === r.steps.length} />)}
          {r.outcome === "running" && r.steps.length === 0 && <li className="text-ink-muted">Starting…</li>}
        </ol>
        <FormAlert error={exportJson.error ? asApiError(exportJson.error) : null} />
      </div>
    </>
  );
}

function StepItem({ runId, step: s, last }: { runId: string; step: AgentStep; last: boolean }) {
  const [open, setOpen] = useState(false);
  const [showText, setShowText] = useState(false);
  const Icon = STEP_ICONS[s.kind] ?? Workflow;
  const detail = Object.entries(s.detail ?? {}).filter(([, v]) => v !== null && v !== undefined && !(Array.isArray(v) && v.length === 0));
  return (
    <li className={cn("grid gap-2 rounded-md border border-line p-3", s.status !== "ok" && "border-status-warn bg-status-warn-soft")} data-step-kind={s.kind}>
      <div className="flex items-start gap-3">
        <span className="font-mono text-small tabular-nums text-ink-muted">{s.seq}</span>
        {last ? <Loader2 aria-hidden className="mt-0.5 size-4 shrink-0 animate-spin" /> : <Icon aria-hidden className={cn("mt-0.5 size-4 shrink-0", s.status === "ok" ? "text-ink-muted" : "text-status-warn")} />}
        <div className="grid flex-1 gap-1">
          <span className="font-medium">{s.label}</span>
          <span className="flex flex-wrap items-center gap-2 text-small text-ink-muted">
            <span>{STEP_KINDS[s.kind] ?? s.kind}</span>
            {s.agent && <Badge variant="outline" className="font-mono" title={AGENT_NAMES[s.agent]}>{s.agent}</Badge>}
            {s.status !== "ok" && <Badge variant="stale">{s.status === "blocked" ? "Blocked" : "Failed"}</Badge>}
            {s.kind === "llm_call" && s.model && <span className="font-mono">{s.provider} · {s.model}</span>}
            {s.kind === "llm_call" && s.prompt_tokens !== null && <span className="font-mono tabular-nums">{s.prompt_tokens}+{s.completion_tokens ?? 0} tokens</span>}
            <span className="font-mono tabular-nums">{took(s.duration_ms)}</span>
          </span>
        </div>
        {s.status === "ok" ? null : <CircleAlert aria-hidden className="size-4 text-status-warn" />}
      </div>
      <div className="flex flex-wrap gap-2 pl-8">
        {detail.length > 0 && <Button variant="link" size="sm" onClick={() => setOpen((o) => !o)}>{open ? "Hide details" : "Details"}</Button>}
        {s.kind === "llm_call" && s.has_text && (
          <Button variant="link" size="sm" onClick={() => setShowText((o) => !o)}><Eye aria-hidden />{showText ? "Hide prompt and reply" : "Prompt and reply"}</Button>
        )}
        {s.kind === "llm_call" && !s.has_text && <span className="text-small text-ink-muted">{s.text_cleared ? "Text cleared" : "Text not kept"}</span>}
      </div>
      {open && (
        <pre className="max-h-72 overflow-auto rounded bg-surface-sunken p-2 font-mono text-small whitespace-pre-wrap break-words">{JSON.stringify(Object.fromEntries(detail), null, 2)}</pre>
      )}
      {showText && <StepTextView runId={runId} seq={s.seq} />}
    </li>
  );
}

function pretty(text: string): string {
  try { return JSON.stringify(JSON.parse(text), null, 2); } catch { return text; }
}

function copy(text: string) {
  void navigator.clipboard?.writeText(text).then(() => toast.success("Copied."));
}

function StepTextView({ runId, seq }: { runId: string; seq: number }) {
  const text = useQuery({
    queryKey: ["agent-run-text", runId, seq],
    queryFn: () => api<StepText>(`/api/v1/admin/agent-runs/${runId}/steps/${seq}/text`),
    staleTime: Infinity,
  });
  if (text.isLoading) return <p className="pl-8 text-small text-ink-muted">Loading…</p>;
  if (text.error || !text.data) return <FormAlert error={asApiError(text.error)} />;
  const t = text.data;
  return (
    <div className="grid gap-3 pl-8" aria-label="What went to the model and what it answered">
      {t.cut && <p className="text-small text-status-warn">Long text was cut to 64,000 characters.</p>}
      <div className="grid gap-2">
        <h3 className="text-small font-medium">Sent to the model (masked)</h3>
        {(t.prompt ?? []).map((m, i) => (
          <div key={i} className="grid gap-1">
            <span className="flex items-center justify-between text-small text-ink-muted">
              <span className="font-mono">{m.role}</span>
              <Button variant="ghost" size="sm" onClick={() => copy(m.content)} aria-label={`Copy the ${m.role} message`}><Copy aria-hidden /></Button>
            </span>
            <pre className="max-h-80 overflow-auto rounded bg-surface-sunken p-2 font-mono text-small whitespace-pre-wrap break-words" data-role={m.role}>{m.content}</pre>
          </div>
        ))}
      </div>
      <div className="grid gap-1">
        <span className="flex items-center justify-between">
          <h3 className="text-small font-medium">Model reply (raw)</h3>
          {t.reply && <Button variant="ghost" size="sm" onClick={() => copy(t.reply ?? "")} aria-label="Copy the reply"><Copy aria-hidden /></Button>}
        </span>
        {t.reply === null
          ? <p className="text-small text-ink-muted"><CircleCheck aria-hidden className="mr-1 inline size-3.5" />No reply: the call was stopped before the model answered.</p>
          : <pre className="max-h-80 overflow-auto rounded bg-surface-sunken p-2 font-mono text-small whitespace-pre-wrap break-words" data-role="reply">{pretty(t.reply)}</pre>}
      </div>
    </div>
  );
}
