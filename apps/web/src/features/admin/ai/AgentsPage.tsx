import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowLeft, Bot, Download, FileText, FlaskConical, History, Loader2, Pencil, Plus, RotateCcw, Sparkles, Trash2, Upload, Workflow,
} from "lucide-react";
import { useRef, useState, type ReactNode } from "react";
import { Link, useParams } from "react-router-dom";
import { toast } from "sonner";

import { api } from "../../../api/client";
import { streamPost } from "../../../api/sse";
import { FormAlert } from "../../../components/ui/alert";
import { Badge } from "../../../components/ui/badge";
import { Button } from "../../../components/ui/button";
import { ConfirmDialog, type ConfirmRequest } from "../../../components/ui/confirm-dialog";
import { Input } from "../../../components/ui/input";
import { Label } from "../../../components/ui/label";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "../../../components/ui/sheet";
import { Switch } from "../../../components/ui/switch";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "../../../components/ui/tabs";
import { Textarea } from "../../../components/ui/textarea";
import { asApiError } from "../../../lib/api-errors";
import { cn } from "../../../lib/utils";

// ENH-030 (US-264 to US-266; FEAT-080): every agent with its prompts and skillset, editable by Admins.

interface Stats { runs: number; llm_calls: number; failed: number; fallbacks: number; avg_ms: number | null; tokens: number }
interface Limits { prompt: number; skill: number; skills_on: number }
/** US-268: whether the agent sends its rules draft for LLM review, and who decided. */
export interface AgentReview { reviews: boolean; on: boolean; choice: boolean | null; default_on: boolean; switch_on: boolean }

export interface AgentSummary {
  code: string;
  name: string;
  purpose: string;
  role: string;
  tasks: string[];
  review_off: boolean;
  review: AgentReview;
  prompts: number;
  edited: number;
  skills: number;
  skills_on: number;
  stats: Stats;
}

export interface AgentPrompt {
  key: string;
  agent: string;
  task: string;
  title: string;
  used_for: string;
  editable: boolean;
  note: string;
  edited: boolean;
  version: number;
  updated_at: string | null;
  updated_by: string | null;
  change_note: string;
  default: string;
  text: string;
}

export interface AgentSkill { id: string; name: string; body: string; enabled: boolean; updated_at: string; updated_by: string | null }

interface AgentList { items: AgentSummary[]; shared: AgentPrompt[]; stats_days: number; limits: Limits }

interface AgentDetail extends Omit<AgentSummary, "prompts" | "edited" | "skills" | "skills_on"> {
  inputs: string;
  output: string;
  guard: string;
  knowledge: { label: string; href: string | null }[];
  prompts: AgentPrompt[];
  shared: AgentPrompt[];
  skills: AgentSkill[];
  stats_days: number;
  limits: Limits;
}

interface PromptVersion { version: number; text: string | null; is_default: boolean; note: string; created_at: string; by: string | null }

const BASE = "/api/v1/admin/agents";
const ROLE_LABELS: Record<string, string> = { planner: "Planner model", narrator: "Narrator model", advisor: "Advisor model" };

function took(ms: number | null): string {
  if (ms == null) return "—";
  return ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(1)} s`;
}

function when(at: string | null): string {
  return at ? new Date(at).toLocaleString() : "";
}

/** Save text as a Markdown file in the browser. */
function download(name: string, text: string) {
  const url = URL.createObjectURL(new Blob([text], { type: "text/markdown" }));
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
  URL.revokeObjectURL(url);
}

function slug(s: string): string {
  return s.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") || "file";
}

/** A hidden file input for a .md or .txt file; the button opens it and `onText` gets the file's text. */
function UploadButton({ label, onText }: { label: string; onText: (text: string, name: string) => void }) {
  const ref = useRef<HTMLInputElement>(null);
  return (
    <>
      <input ref={ref} type="file" accept=".md,.markdown,.txt,text/markdown,text/plain" className="sr-only" aria-label={label} tabIndex={-1}
        onChange={async (e) => {
          const file = e.target.files?.[0];
          if (file) onText(await file.text(), file.name);
          e.target.value = "";
        }} />
      <Button type="button" variant="ghost" size="sm" onClick={() => ref.current?.click()}><Upload aria-hidden /> {label}</Button>
    </>
  );
}

function StatLine({ stats, days }: { stats: Stats; days: number }) {
  const items: [string, string][] = [
    ["Model calls", String(stats.llm_calls)],
    ["Failed", String(stats.failed)],
    ["Fell back", String(stats.fallbacks)],
    ["Average time", took(stats.avg_ms)],
  ];
  return (
    <dl className="grid grid-cols-4 gap-2" aria-label={`Last ${days} days`}>
      {items.map(([k, v]) => (
        <div key={k} className="grid gap-0.5">
          <dt className="text-small text-ink-muted">{k}</dt>
          <dd className={cn("font-mono tabular-nums text-body-strong", k === "Failed" && stats.failed > 0 && "text-status-danger")}>{v}</dd>
        </div>
      ))}
    </dl>
  );
}

// --------------------------------------------------------------------------- the list

export function AgentsPage() {
  const list = useQuery({ queryKey: ["agents"], queryFn: () => api<AgentList>(BASE) });
  return (
    <section className="tl grid gap-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="grid gap-1">
          <h1 className="text-title">Agents</h1>
          <p className="max-w-3xl text-ink-muted">
            Every AI agent in Tela, what it does and the prompts it sends. Edit a prompt or give an agent skills to improve its answers;
            Tela's own checks still run on every answer, and a change takes effect within 30 seconds.
          </p>
        </div>
        <Button asChild variant="secondary"><Link to="/admin/agent-runs"><Workflow aria-hidden /> Agent runs</Link></Button>
      </div>
      {list.isError && <FormAlert error={asApiError(list.error)} />}
      {list.isPending && <p className="flex items-center gap-2 text-ink-muted"><Loader2 className="animate-spin" aria-hidden /> Loading agents…</p>}
      {list.data && (
        <>
          <ul className="grid gap-3 md:grid-cols-2 xl:grid-cols-3" aria-label="Agents">
            {list.data.items.map((a) => (
              <li key={a.code}>
                <Link to={`/admin/agents/${a.code}`} aria-label={`${a.code} ${a.name}`}
                  className="grid h-full gap-3 rounded-lg border border-line bg-surface-raised p-4 transition-colors hover:border-accent focus-visible:focus-ring">
                  <div className="flex items-start gap-3">
                    <span className="grid size-9 shrink-0 place-items-center rounded-md bg-accent-soft text-accent"><Bot aria-hidden className="size-5" /></span>
                    <div className="grid min-w-0 gap-0.5">
                      <span className="font-mono text-small text-ink-muted">{a.code}</span>
                      <span className="text-body-strong">{a.name}</span>
                    </div>
                    <Badge variant="outline" className="ml-auto shrink-0">{ROLE_LABELS[a.role] ?? a.role}</Badge>
                  </div>
                  <p className="line-clamp-3 text-small text-ink-muted">{a.purpose}</p>
                  <div className="flex flex-wrap gap-1.5">
                    <Badge variant={a.edited ? "certified" : "neutral"}>
                      <FileText aria-hidden className="size-3.5" /> {a.prompts} {a.prompts === 1 ? "prompt" : "prompts"}{a.edited ? ` · ${a.edited} edited` : ""}
                    </Badge>
                    <Badge variant={a.skills_on ? "certified" : "neutral"}>
                      <Sparkles aria-hidden className="size-3.5" /> {a.skills_on} {a.skills_on === 1 ? "skill" : "skills"} on
                    </Badge>
                    {a.review_off && <Badge variant="stale">LLM review off</Badge>}
                  </div>
                  <StatLine stats={a.stats} days={list.data.stats_days} />
                </Link>
              </li>
            ))}
          </ul>
          <p className="text-small text-ink-muted">Figures cover the last {list.data.stats_days} days of agent runs.</p>
          {list.data.shared.length > 0 && (
            <div className="grid gap-3">
              <h2 className="text-heading">Shared by all agents</h2>
              {list.data.shared.map((p) => <PromptCard key={p.key} code="shared" prompt={p} limit={list.data.limits.prompt} />)}
            </div>
          )}
        </>
      )}
    </section>
  );
}

// --------------------------------------------------------------------------- one agent

export function AgentPage() {
  const { code = "" } = useParams();
  const detail = useQuery({ queryKey: ["agents", code], queryFn: () => api<AgentDetail>(`${BASE}/${code}`) });
  const a = detail.data;
  return (
    <section className="tl grid gap-4">
      <Link to="/admin/agents" className="flex w-fit items-center gap-1 text-small text-ink-muted hover:text-ink"><ArrowLeft aria-hidden className="size-4" /> All agents</Link>
      {detail.isError && <FormAlert error={asApiError(detail.error)} />}
      {detail.isPending && <p className="flex items-center gap-2 text-ink-muted"><Loader2 className="animate-spin" aria-hidden /> Loading…</p>}
      {a && (
        <>
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div className="grid max-w-3xl gap-1">
              <span className="font-mono text-small text-ink-muted">{a.code} · {ROLE_LABELS[a.role] ?? a.role}</span>
              <h1 className="text-title">{a.name}</h1>
              <p className="text-ink-muted">{a.purpose}</p>
            </div>
            <Button asChild variant="secondary"><Link to={`/admin/agent-runs?agent=${a.code}`}><Workflow aria-hidden /> This agent's runs</Link></Button>
          </div>
          <div className="rounded-lg border border-line bg-surface-raised p-4">
            <StatLine stats={a.stats} days={a.stats_days} />
            <p className="mt-2 text-small text-ink-muted">Last {a.stats_days} days · {a.stats.tokens.toLocaleString()} tokens</p>
          </div>
          {a.review.reviews && <ReviewCard code={a.code} review={a.review} />}
          <Tabs defaultValue="prompts">
            <TabsList>
              <TabsTrigger value="prompts">Prompts <Badge>{a.prompts.length}</Badge></TabsTrigger>
              <TabsTrigger value="skills">Skillset <Badge>{a.skills.filter((s) => s.enabled).length}</Badge></TabsTrigger>
              <TabsTrigger value="details">Details</TabsTrigger>
            </TabsList>
            <TabsContent value="prompts" className="grid gap-3">
              <p className="text-small text-ink-muted">
                The answer format and the schema are added by Tela after each prompt, so a prompt can't change what an answer must contain.
                After a change, run the <Link className="underline" to="/admin/knowledge/gold">gold examples</Link> to see whether answers got better.
              </p>
              {a.prompts.map((p) => <PromptCard key={p.key} code={a.code} prompt={p} limit={a.limits.prompt} />)}
            </TabsContent>
            <TabsContent value="skills"><Skillset code={a.code} skills={a.skills} limits={a.limits} /></TabsContent>
            <TabsContent value="details"><Details agent={a} /></TabsContent>
          </Tabs>
        </>
      )}
    </section>
  );
}

// --------------------------------------------------------------------------- LLM review on or off (US-268)

function ReviewCard({ code, review: r }: { code: string; review: AgentReview }) {
  const qc = useQueryClient();
  const set = useMutation({
    mutationFn: (on: boolean | null) => api<AgentReview>(`${BASE}/${code}/review`, { method: "PUT", body: { on } }),
    onSuccess: (got) => {
      toast.success(got.on ? `LLM review is on for ${code}.` : `LLM review is off for ${code}.`);
      void qc.invalidateQueries({ queryKey: ["agents"] });
    },
  });
  const source = r.choice === null ? "Deployment default" : "Set by an Admin";
  return (
    <div className="grid gap-2 rounded-lg border border-line bg-surface-raised p-4" aria-label="LLM review">
      <div className="flex flex-wrap items-center gap-3">
        <label className="flex items-center gap-2 text-body-strong">
          <Switch checked={r.on} disabled={set.isPending} onCheckedChange={(on) => set.mutate(on)} aria-label={`LLM review for ${code}`} />
          LLM review
        </label>
        <Badge variant={r.choice === null ? "neutral" : "outline"}>{source}</Badge>
        {r.choice !== null && (
          <Button size="sm" variant="ghost" disabled={set.isPending} onClick={() => set.mutate(null)}>
            <RotateCcw aria-hidden /> Use default ({r.default_on ? "on" : "off"})
          </Button>
        )}
      </div>
      <p className="text-small text-ink-muted">
        When on, the LLM checks this agent's rules result: it confirms, corrects or asks. A correction is checked and never replaces the result silently.
        Each review adds one model call.
      </p>
      {!r.switch_on && (
        <p className="text-small text-ink-muted">
          LLM review is off for the whole tenant, so no agent reviews now. Turn it on in <Link className="underline" to="/admin/ai">AI settings</Link>.
        </p>
      )}
      {set.isError && <FormAlert error={asApiError(set.error)} />}
    </div>
  );
}

function Details({ agent: a }: { agent: AgentDetail }) {
  const rows: [string, ReactNode][] = [
    ["Reads", a.inputs],
    ["Answers with", a.output],
    ["Guard", a.guard],
    ["Model tasks", <span className="font-mono text-small">{a.tasks.join(", ")}</span>],
    ["Model", <>{ROLE_LABELS[a.role] ?? a.role} — set in <Link className="underline" to="/admin/ai">AI settings</Link></>],
    ["LLM review", !a.review.reviews ? "This agent doesn't send its work for review"
      : a.review.on ? "On for this agent — it reviews while the switch in AI settings is on" : "Off for this agent"],
    ["Knowledge it uses", a.knowledge.length ? (
      <ul className="flex flex-wrap gap-2">
        {a.knowledge.map((k) => (
          <li key={k.label}>{k.href ? <Link className="underline" to={k.href}>{k.label}</Link> : k.label}</li>
        ))}
      </ul>
    ) : "None"],
  ];
  return (
    <dl className="grid gap-3 rounded-lg border border-line bg-surface-raised p-4 sm:grid-cols-[12rem_1fr]">
      {rows.map(([k, v]) => (
        <div key={k} className="contents">
          <dt className="text-body-strong">{k}</dt>
          <dd className="text-ink-muted">{v}</dd>
        </div>
      ))}
    </dl>
  );
}

// --------------------------------------------------------------------------- a prompt

function PromptCard({ code, prompt: p, limit }: { code: string; prompt: AgentPrompt; limit: number }) {
  const qc = useQueryClient();
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState(p.text);
  const [note, setNote] = useState("");
  const [showDefault, setShowDefault] = useState(false);
  const [history, setHistory] = useState(false);
  const [confirm, setConfirm] = useState<ConfirmRequest | null>(null);
  const url = `${BASE}/${code}/prompts/${p.key}`;
  const done = (msg: string) => {
    toast.success(msg);
    void qc.invalidateQueries({ queryKey: ["agents"] });
  };
  const save = useMutation({
    mutationFn: (body: { text: string; note: string }) => api<AgentPrompt>(url, { method: "PUT", body }),
    onSuccess: () => { setEditing(false); setNote(""); done(`Saved "${p.title}".`); },
  });
  const reset = useMutation({
    mutationFn: () => api<AgentPrompt>(url, { method: "DELETE" }),
    onSuccess: () => { setConfirm(null); done(`"${p.title}" is back to the default.`); },
  });
  const over = text.length > limit;
  return (
    <article className="grid gap-3 rounded-lg border border-line bg-surface-raised p-4" aria-label={`Prompt ${p.title}`}>
      <div className="flex flex-wrap items-start gap-3">
        <div className="grid min-w-0 flex-1 gap-0.5">
          <div className="flex flex-wrap items-center gap-2">
            <h3 className="text-body-strong">{p.title}</h3>
            {p.edited ? <Badge variant="certified">Edited · v{p.version}</Badge> : <Badge>Default</Badge>}
            {!p.editable && <Badge variant="outline">Read-only</Badge>}
            <span className="font-mono text-small text-ink-muted">{p.key}</span>
          </div>
          <p className="text-small text-ink-muted">{p.used_for}</p>
          {p.version > 0 && p.updated_at && (
            <p className="text-small text-ink-muted">Changed {when(p.updated_at)}{p.updated_by ? ` by ${p.updated_by}` : ""}{p.change_note ? ` — ${p.change_note}` : ""}</p>
          )}
          {p.note && <p className="text-small text-ink-muted">{p.note}</p>}
        </div>
        {!editing && (
          <div className="flex flex-wrap gap-1">
            {p.editable && <Button size="sm" variant="secondary" onClick={() => { setText(p.text); setEditing(true); }}><Pencil aria-hidden /> Edit</Button>}
            {p.version > 0 && <Button size="sm" variant="ghost" onClick={() => setHistory(true)}><History aria-hidden /> History</Button>}
            {p.edited && (
              <Button size="sm" variant="ghost" onClick={() => setConfirm({
                title: `Reset "${p.title}"?`, confirm: "Reset to default",
                body: "The agent goes back to Tela's default prompt. Your edited text stays in the history, so you can restore it.",
                onConfirm: () => reset.mutate(),
              })}><RotateCcw aria-hidden /> Reset</Button>
            )}
            <Button size="sm" variant="ghost" aria-label={`Download ${p.title}`} onClick={() => download(`${p.key}.md`, p.text)}><Download aria-hidden /></Button>
          </div>
        )}
      </div>
      {editing ? (
        <form className="grid gap-2" onSubmit={(e) => { e.preventDefault(); if (!over && text.trim()) save.mutate({ text, note }); }}>
          <Label htmlFor={`prompt-${p.key}`}>Prompt text</Label>
          <Textarea id={`prompt-${p.key}`} value={text} onChange={(e) => setText(e.target.value)} rows={12} className="font-mono text-small" aria-invalid={over || undefined} />
          <div className="flex flex-wrap items-center justify-between gap-2 text-small">
            <span className={cn("tabular-nums", over ? "text-status-danger" : "text-ink-muted")}>{text.length.toLocaleString()} / {limit.toLocaleString()} characters</span>
            <div className="flex flex-wrap gap-1">
              <UploadButton label="Upload .md" onText={(t) => setText(t)} />
              <Button type="button" variant="ghost" size="sm" onClick={() => setText(p.default)}>Use default text</Button>
              <Button type="button" variant="ghost" size="sm" onClick={() => setShowDefault((v) => !v)}>{showDefault ? "Hide default" : "Show default"}</Button>
            </div>
          </div>
          {showDefault && <pre className="max-h-64 overflow-auto whitespace-pre-wrap rounded-md bg-surface-sunken p-3 font-mono text-small text-ink-muted" aria-label="Default prompt">{p.default}</pre>}
          <Label htmlFor={`note-${p.key}`}>What changed (optional)</Label>
          <Input id={`note-${p.key}`} value={note} maxLength={500} onChange={(e) => setNote(e.target.value)} placeholder="e.g. Explain that 'slipping' means schedule risk" />
          {TRY_AGENTS.has(code) && <TryPanel code={code} draft={() => ({ prompt: { key: p.key, text } })} disabled={over || !text.trim()} />}
          {save.isError && <FormAlert error={asApiError(save.error)} />}
          <div className="flex gap-2">
            <Button type="submit" disabled={save.isPending || over || !text.trim()}>{save.isPending && <Loader2 className="animate-spin" aria-hidden />} Save prompt</Button>
            <Button type="button" variant="ghost" onClick={() => { setEditing(false); setText(p.text); save.reset(); }}>Cancel</Button>
          </div>
        </form>
      ) : (
        <pre className="max-h-48 overflow-auto whitespace-pre-wrap rounded-md bg-surface-sunken p-3 font-mono text-small" aria-label={`${p.title} text`}>{p.text}</pre>
      )}
      {history && <PromptHistory url={url} prompt={p} onClose={() => setHistory(false)} onRestore={(t, v) => { setHistory(false); save.mutate({ text: t, note: `Restored version ${v}` }); }} />}
      <ConfirmDialog request={confirm} onClose={() => setConfirm(null)} busy={reset.isPending} />
    </article>
  );
}

function PromptHistory({ url, prompt, onClose, onRestore }: { url: string; prompt: AgentPrompt; onClose: () => void; onRestore: (text: string, version: number) => void }) {
  const versions = useQuery({ queryKey: ["agents", "versions", url], queryFn: () => api<{ default: string; items: PromptVersion[] }>(`${url}/versions`) });
  return (
    <Sheet open onOpenChange={(open) => { if (!open) onClose(); }}>
      <SheetContent className="w-full overflow-y-auto sm:max-w-2xl">
        <SheetHeader>
          <SheetTitle>History · {prompt.title}</SheetTitle>
          <SheetDescription>Every saved version, newest first. Restoring saves it again as a new version.</SheetDescription>
        </SheetHeader>
        {versions.isPending && <p className="p-4 text-ink-muted">Loading…</p>}
        <ol className="grid gap-3 p-4">
          {versions.data?.items.map((v, i) => (
            <li key={v.version} className="grid gap-2 rounded-lg border border-line p-3">
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-body-strong">Version {v.version}</span>
                {v.is_default && <Badge>Default</Badge>}
                {i === 0 && <Badge variant="certified">In use</Badge>}
                <span className="text-small text-ink-muted">{when(v.created_at)}{v.by ? ` · ${v.by}` : ""}</span>
                {i > 0 && (
                  <Button size="sm" variant="secondary" className="ml-auto" onClick={() => onRestore(v.text ?? versions.data.default, v.version)}>
                    <RotateCcw aria-hidden /> Restore
                  </Button>
                )}
              </div>
              {v.note && <p className="text-small text-ink-muted">{v.note}</p>}
              <pre className="max-h-40 overflow-auto whitespace-pre-wrap rounded-md bg-surface-sunken p-2 font-mono text-small">{v.text ?? versions.data.default}</pre>
            </li>
          ))}
        </ol>
      </SheetContent>
    </Sheet>
  );
}

// --------------------------------------------------------------------------- the skillset

function Skillset({ code, skills, limits }: { code: string; skills: AgentSkill[]; limits: Limits }) {
  const [adding, setAdding] = useState(false);
  const used = skills.filter((s) => s.enabled).reduce((n, s) => n + s.name.length + s.body.length, 0);
  return (
    <div className="grid gap-3">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <p className="max-w-3xl text-small text-ink-muted">
          Skills are Markdown notes this agent follows on every call: your PMO's words, conventions and examples. Skills that are on are
          added after the agent's prompt; they never override Tela's rules or the answer format.
          <span className="block tabular-nums">{used.toLocaleString()} / {limits.skills_on.toLocaleString()} characters of skills on.</span>
        </p>
        {!adding && <Button onClick={() => setAdding(true)}><Plus aria-hidden /> Add skill</Button>}
      </div>
      {adding && <SkillForm code={code} limit={limits.skill} onDone={() => setAdding(false)} />}
      {skills.length === 0 && !adding && (
        <div className="grid place-items-center gap-2 rounded-lg border border-dashed border-line p-8 text-center text-ink-muted">
          <Sparkles aria-hidden />
          <p>No skills yet. Add one, or upload a Markdown file.</p>
        </div>
      )}
      {skills.map((s) => <SkillCard key={s.id} code={code} skill={s} limit={limits.skill} />)}
    </div>
  );
}

function SkillForm({ code, skill, limit, onDone }: { code: string; skill?: AgentSkill; limit: number; onDone: () => void }) {
  const qc = useQueryClient();
  const [name, setName] = useState(skill?.name ?? "");
  const [body, setBody] = useState(skill?.body ?? "");
  const save = useMutation({
    mutationFn: () => api<AgentSkill>(skill ? `${BASE}/${code}/skills/${skill.id}` : `${BASE}/${code}/skills`,
      { method: skill ? "PUT" : "POST", body: { name: name.trim(), body, enabled: skill?.enabled ?? true } }),
    onSuccess: () => {
      toast.success(skill ? `Saved "${name.trim()}".` : `Added "${name.trim()}".`);
      void qc.invalidateQueries({ queryKey: ["agents"] });
      onDone();
    },
  });
  const over = body.length > limit;
  const id = skill?.id ?? "new";
  return (
    <form className="grid gap-2 rounded-lg border border-line bg-surface-raised p-4" aria-label={skill ? `Edit skill ${skill.name}` : "New skill"}
      onSubmit={(e) => { e.preventDefault(); if (name.trim() && body.trim() && !over) save.mutate(); }}>
      <Label htmlFor={`skill-name-${id}`}>Name</Label>
      <Input id={`skill-name-${id}`} value={name} maxLength={120} onChange={(e) => setName(e.target.value)} placeholder="e.g. PMO wording" />
      <Label htmlFor={`skill-body-${id}`}>Skill (Markdown)</Label>
      <Textarea id={`skill-body-${id}`} value={body} onChange={(e) => setBody(e.target.value)} rows={10} className="font-mono text-small" aria-invalid={over || undefined}
        placeholder={"- 'Slipping' means a project's schedule risk is amber or red.\n- 'This FY' runs April to March."} />
      <div className="flex flex-wrap items-center justify-between gap-2 text-small">
        <span className={cn("tabular-nums", over ? "text-status-danger" : "text-ink-muted")}>{body.length.toLocaleString()} / {limit.toLocaleString()} characters</span>
        <UploadButton label="Upload .md" onText={(t, file) => { setBody(t); if (!name.trim()) setName(file.replace(/\.(md|markdown|txt)$/i, "")); }} />
      </div>
      {TRY_AGENTS.has(code) && (
        <TryPanel code={code} disabled={over || !name.trim() || !body.trim()}
          draft={() => ({ skill: { id: skill?.id ?? null, name: name.trim(), body, enabled: true } })} />
      )}
      {save.isError && <FormAlert error={asApiError(save.error)} />}
      <div className="flex gap-2">
        <Button type="submit" disabled={save.isPending || over || !name.trim() || !body.trim()}>{save.isPending && <Loader2 className="animate-spin" aria-hidden />} {skill ? "Save skill" : "Add skill"}</Button>
        <Button type="button" variant="ghost" onClick={onDone}>Cancel</Button>
      </div>
    </form>
  );
}

function SkillCard({ code, skill: s, limit }: { code: string; skill: AgentSkill; limit: number }) {
  const qc = useQueryClient();
  const [editing, setEditing] = useState(false);
  const [confirm, setConfirm] = useState<ConfirmRequest | null>(null);
  const refresh = () => void qc.invalidateQueries({ queryKey: ["agents"] });
  const toggle = useMutation({
    mutationFn: (enabled: boolean) => api<AgentSkill>(`${BASE}/${code}/skills/${s.id}`, { method: "PUT", body: { name: s.name, body: s.body, enabled } }),
    onSuccess: (out) => { toast.success(`"${s.name}" is ${out.enabled ? "on" : "off"}.`); refresh(); },
    onError: (e) => toast.error(asApiError(e).message),
  });
  const remove = useMutation({
    mutationFn: () => api<void>(`${BASE}/${code}/skills/${s.id}`, { method: "DELETE" }),
    onSuccess: () => { setConfirm(null); toast.success(`Deleted "${s.name}".`); refresh(); },
  });
  if (editing) return <SkillForm code={code} skill={s} limit={limit} onDone={() => setEditing(false)} />;
  return (
    <article className={cn("grid gap-2 rounded-lg border border-line bg-surface-raised p-4", !s.enabled && "opacity-70")} aria-label={`Skill ${s.name}`}>
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="text-body-strong">{s.name}</h3>
        {!s.enabled && <Badge>Off</Badge>}
        <span className="text-small text-ink-muted">{when(s.updated_at)}{s.updated_by ? ` · ${s.updated_by}` : ""}</span>
        <div className="ml-auto flex items-center gap-1">
          <label className="mr-2 flex items-center gap-2 text-small">
            <Switch checked={s.enabled} disabled={toggle.isPending} onCheckedChange={(on) => toggle.mutate(on)} aria-label={`Use ${s.name}`} /> On
          </label>
          <Button size="sm" variant="ghost" onClick={() => setEditing(true)}><Pencil aria-hidden /> Edit</Button>
          <Button size="sm" variant="ghost" aria-label={`Download ${s.name}`} onClick={() => download(`${slug(s.name)}.md`, s.body)}><Download aria-hidden /></Button>
          <Button size="sm" variant="ghost" aria-label={`Delete ${s.name}`} onClick={() => setConfirm({
            title: `Delete "${s.name}"?`, confirm: "Delete skill", danger: true,
            body: "The agent stops using this skill. Download it first if you may want it back.", onConfirm: () => remove.mutate(),
          })}><Trash2 aria-hidden /></Button>
        </div>
      </div>
      <pre className="max-h-48 overflow-auto whitespace-pre-wrap rounded-md bg-surface-sunken p-3 font-mono text-small">{s.body}</pre>
      <ConfirmDialog request={confirm} onClose={() => setConfirm(null)} busy={remove.isPending} />
    </article>
  );
}

// --------------------------------------------------------------------------- Try it (US-267)

/** Agents whose work "Try it" can run: Ask planning and SQL drafting. */
export const TRY_AGENTS = new Set(["AG-01", "AG-02", "AG-03", "AG-10"]);

export interface TryCall { agent: string; task: string; ok: boolean; reply: string | null; ms: number; tuned: { prompts: string[]; skills: string[] } }

export interface TrySide {
  side: "before" | "after";
  status: string;
  planner?: string | null;
  confidence?: number;
  reason?: string | null;
  reading?: string | null;
  spec?: Record<string, unknown> | null;
  clarify?: { question: string | null; options: string[] } | null;
  sql?: string | null;
  calls: TryCall[];
  took_ms: number;
}

const STATUS_LABELS: Record<string, string> = { ok: "Answered", clarify: "Asked a question", unsupported: "Couldn't answer", error: "Failed" };

function outcome(s: TrySide): string {
  return JSON.stringify([s.status, s.spec ?? null, s.sql ?? null, s.clarify ?? null]);
}

function TryPanel({ code, draft, disabled }: { code: string; draft: () => Record<string, unknown>; disabled?: boolean }) {
  const [question, setQuestion] = useState("");
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<ReturnType<typeof asApiError> | null>(null);
  const [connection, setConnection] = useState<string | null>(null);
  const [sides, setSides] = useState<Partial<Record<"before" | "after", TrySide>>>({});
  const run = async () => {
    setRunning(true);
    setError(null);
    setSides({});
    try {
      await streamPost(`${BASE}/${code}/try`, { question: question.trim(), ...draft() }, (e) => {
        if (e.event === "start") setConnection((e.data as { connection: { name: string | null } }).connection.name);
        if (e.event === "side") { const d = e.data as TrySide; setSides((x) => ({ ...x, [d.side]: d })); }
      });
    } catch (err) {
      setError(asApiError(err));
    } finally {
      setRunning(false);
    }
  };
  const done = sides.before && sides.after;
  const failed = done && [sides.before!, sides.after!].some((x) => x.status === "error" || x.calls.some((c) => !c.ok));
  const same = done && outcome(sides.before!) === outcome(sides.after!);
  return (
    <div className="grid gap-2 rounded-md border border-dashed border-line p-3" role="group" aria-label="Try it">
      <div className="flex items-center gap-2 text-body-strong"><FlaskConical aria-hidden className="size-4" /> Try it</div>
      <p className="text-small text-ink-muted">
        Run one question with the saved version, then with your draft, and see both side by side. Nothing is saved{code === "AG-10" ? "" : " and no query runs"}.
      </p>
      <div className="flex flex-wrap gap-2">
        <Input aria-label="Question to try" value={question} maxLength={500} className="min-w-0 flex-1"
          placeholder={code === "AG-10" ? "e.g. projects by region" : "e.g. where are we slipping"}
          onChange={(e) => setQuestion(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); if (question.trim() && !running && !disabled) void run(); } }} />
        <Button type="button" variant="secondary" disabled={!question.trim() || running || disabled} onClick={() => void run()}>
          {running ? <Loader2 className="animate-spin" aria-hidden /> : <FlaskConical aria-hidden />} Try it
        </Button>
      </div>
      {error && <FormAlert error={error} />}
      {(running || done) && (
        <>
          <p className="text-small text-ink-muted" aria-live="polite">
            {connection ? `On ${connection}. ` : ""}
            {running ? (sides.before ? "Now running your draft…" : "Running the saved version first, then your draft; a local model can take a few minutes.")
              : failed ? "A model call failed on one side, so the two can't be compared fairly. Try again."
                : same ? "Same result with both." : "Different results."}
          </p>
          <div className="grid gap-2 md:grid-cols-2">
            <TryResult title="Saved" side={sides.before} />
            <TryResult title="Your draft" side={sides.after} highlight={Boolean(done && !same)} />
          </div>
        </>
      )}
    </div>
  );
}

function TryResult({ title, side, highlight }: { title: string; side?: TrySide; highlight?: boolean }) {
  return (
    <section className={cn("grid content-start gap-2 rounded-md border border-line bg-surface p-3", highlight && "border-accent")} aria-label={`${title} result`}>
      <div className="flex flex-wrap items-center gap-2">
        <h4 className="text-body-strong">{title}</h4>
        {side ? (
          <>
            <Badge variant={side.status === "ok" ? "certified" : side.status === "error" ? "stale" : "neutral"}>{STATUS_LABELS[side.status] ?? side.status}</Badge>
            <span className="text-small text-ink-muted">{took(side.took_ms)}{side.planner ? ` · by ${side.planner === "llm" ? "the model" : side.planner}` : ""}{side.confidence != null ? ` · confidence ${Math.round(side.confidence * 100)}%` : ""}</span>
          </>
        ) : <span className="flex items-center gap-1 text-small text-ink-muted"><Loader2 className="size-3.5 animate-spin" aria-hidden /> Running…</span>}
      </div>
      {side?.reading && <p>{side.reading}</p>}
      {side?.sql && <pre className="max-h-40 overflow-auto whitespace-pre-wrap rounded-md bg-surface-sunken p-2 font-mono text-small">{side.sql}</pre>}
      {side?.clarify && (
        <div className="text-small">
          <p>{side.clarify.question}</p>
          {side.clarify.options.length > 0 && <ul className="mt-1 flex flex-wrap gap-1">{side.clarify.options.map((o) => <li key={o}><Badge variant="outline">{o}</Badge></li>)}</ul>}
        </div>
      )}
      {side?.reason && <p className="text-small text-ink-muted">{side.reason}</p>}
      {side && (side.calls.length === 0 ? (
        <p className="text-small text-ink-muted">No model call: Tela's rules answered this question, so the prompt wasn't used. Try a harder question.</p>
      ) : (
        <details className="text-small">
          <summary className="cursor-pointer text-ink-muted">{side.calls.length} model {side.calls.length === 1 ? "call" : "calls"}</summary>
          <ol className="mt-2 grid gap-2">
            {side.calls.map((c, i) => (
              <li key={i} className="grid gap-1">
                <span className="text-ink-muted">
                  {c.agent} {c.task} · {took(c.ms)}{c.ok ? "" : " · failed"}
                  {c.tuned.prompts.length + c.tuned.skills.length > 0 && ` · used ${[...c.tuned.prompts, ...c.tuned.skills].join(", ")}`}
                </span>
                {c.reply && <pre className="max-h-32 overflow-auto whitespace-pre-wrap rounded-md bg-surface-sunken p-2 font-mono">{c.reply}</pre>}
              </li>
            ))}
          </ol>
        </details>
      ))}
    </section>
  );
}
