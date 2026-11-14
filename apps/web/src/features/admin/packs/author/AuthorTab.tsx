import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Sparkles, Square } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { toast } from "sonner";

import { api } from "../../../../api/client";
import {
  acceptAuthorItems, getAuthorOverview, streamAuthor,
  type AuthorKnowledgeKind, type AuthorProposal, type AuthorStage,
} from "../../../../api/packAuthor";
import { Alert } from "../../../../components/ui/alert";
import { Button } from "../../../../components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../../../../components/ui/card";
import { Checkbox } from "../../../../components/ui/checkbox";
import { Label } from "../../../../components/ui/label";
import { NativeSelect } from "../../../../components/ui/native-select";
import { Steps } from "../../../../components/ui/steps";
import { asApiError, friendlyMessage } from "../../../../lib/api-errors";
import { packKey, usePack } from "../PackContext";
import { ProposalCard } from "./ProposalCard";

const STAGES: { key: AuthorStage; label: string; what: string }[] = [
  { key: "entities", label: "Entities & fields", what: "One entity per table, one field per column, with labels, descriptions and sensitivity tags." },
  { key: "links", label: "Links", what: "Foreign keys, then key names; each key is checked unique at the source with two counts." },
  { key: "measures", label: "Measures", what: "Per entity, up to 20 at a time: counts by state, sums and averages of money and effort, overdue, due soon, aging and shares, each counted on the source." },
  { key: "dimensions", label: "Dimensions", what: "Breakdowns with few values, dates over time and banded dates (how long, how late, when)." },
  { key: "synonyms", label: "Synonyms", what: "The other words people use for measures, dimensions and entities." },
  { key: "knowledge", label: "Knowledge", what: "Glossary terms, value synonyms, dashboard rules and wording, chart patterns and analysis recipes about this pack's measures and dimensions." },
];
const KNOWLEDGE: [AuthorKnowledgeKind, string][] = [
  ["glossary", "Glossary"], ["value_synonyms", "Value synonyms"], ["dashboard_rules", "Dashboard rules and wording"],
  ["chart_patterns", "Chart patterns"], ["analysis_recipes", "Analysis recipes"],
];

/** The AI pack author (US-237): each stage is drafted (by the model when one is set up, else by rules), every
 * proposal is checked against the source before it is shown, and nothing reaches the draft until it is accepted. */
export function AuthorTab() {
  const { pack, etag, readOnly } = usePack();
  const qc = useQueryClient();
  const [params, setParams] = useSearchParams();
  const connections = useQuery({ queryKey: ["connections"], queryFn: () => api<{ items: { id: string; name: string }[] }>("/api/v1/connections") });
  const connectionId = params.get("connection") ?? connections.data?.items[0]?.id ?? "";
  const overview = useQuery({ queryKey: ["pack-author", pack.id, connectionId, pack.revision], queryFn: () => getAuthorOverview(pack.id, connectionId), enabled: !!connectionId });
  const templateId = params.get("template") ?? overview.data?.template_id ?? "";
  const [stage, setStage] = useState<AuthorStage>("entities");
  const [kind, setKind] = useState<AuthorKnowledgeKind>("glossary");
  const [entity, setEntity] = useState("");
  const [tables, setTables] = useState<Set<string>>(new Set());
  const [proposals, setProposals] = useState<AuthorProposal[]>([]);
  const [dropped, setDropped] = useState<AuthorProposal[]>([]);
  const [notes, setNotes] = useState<string[]>([]);
  const [accepted, setAccepted] = useState<Set<string>>(new Set());
  const [running, setRunning] = useState(false);
  const [busy, setBusy] = useState(false);
  const abort = useRef<AbortController | null>(null);

  useEffect(() => () => abort.current?.abort(), []);
  const choose = (key: string, value: string) => setParams((p) => { const n = new URLSearchParams(p); n.set(key, value); return n; }, { replace: true });
  const reset = () => { setProposals([]); setDropped([]); setNotes([]); setAccepted(new Set()); };

  async function run() {
    reset();
    setRunning(true);
    abort.current = new AbortController();
    try {
      await streamAuthor(pack.id, {
        connection_id: connectionId, stage, template_pack_id: templateId || null,
        ...(stage === "knowledge" ? { kind } : {}),
        ...(stage === "entities" && tables.size ? { tables: [...tables] } : {}),
        ...((stage === "measures" || stage === "dimensions") && entity ? { entities: [entity] } : {}),
      }, {
        onProposal: (p) => setProposals((xs) => [...xs, p]),
        onDropped: (p) => setDropped((xs) => [...xs, p]),
        onNote: (m) => setNotes((xs) => [...xs, m]),
      }, abort.current.signal);
    } catch (e) {
      if (!(e instanceof DOMException && e.name === "AbortError")) toast.error(friendlyMessage(asApiError(e, "The stage could not be drafted.")));
    } finally {
      setRunning(false);
    }
  }

  async function accept(items: AuthorProposal[]) {
    if (!items.length) return;
    setBusy(true);
    try {
      const out = await acceptAuthorItems(pack.id, etag, connectionId, items[0]!.kind, items.map((p) => p.item), items.map((p) => p.source));
      qc.setQueryData(packKey(pack.id), { pack: { ...pack, ...out.pack }, etag: out.etag });
      void qc.invalidateQueries({ queryKey: ["packs"] });
      void qc.invalidateQueries({ queryKey: ["pack-knowledge", pack.id] });
      setAccepted((s) => new Set([...s, ...items.map((p) => p.id)]));
      toast.success(items.length === 1 ? `${items[0]!.title} added to the draft` : `${items.length} items added to the draft`);
    } catch (e) {
      const err = asApiError(e, "The items could not be added.");
      if (err.status === 412) void qc.invalidateQueries({ queryKey: packKey(pack.id) });
      toast.error(friendlyMessage(err));
    } finally {
      setBusy(false);
    }
  }

  if (readOnly) return <Alert>Clone this pack to draft it with AI: system packs and released versions can't change.</Alert>;
  const o = overview.data;
  const pending = proposals.filter((p) => !accepted.has(p.id));
  const current = STAGES.find((s) => s.key === stage)!;
  const ents = pack.manifest.entities ?? [];
  const newTables = (o?.tables ?? []).filter((t) => !t.in_draft);
  return (
    <div className="grid gap-4">
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2"><Sparkles aria-hidden className="size-4 text-ink-muted" />AI author</CardTitle>
          <CardDescription>AI proposes; you decide. Every proposal is checked on the connection first, and nothing goes into the draft until you accept it.</CardDescription>
        </CardHeader>
        <CardContent className="grid gap-3 sm:grid-cols-2">
          <div className="grid gap-1.5">
            <Label htmlFor="author-connection">Connection</Label>
            <NativeSelect id="author-connection" value={connectionId} onChange={(e) => { choose("connection", e.target.value); reset(); }}>
              {(connections.data?.items ?? []).map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
            </NativeSelect>
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor="author-template">Template (worked examples)</Label>
            <NativeSelect id="author-template" value={templateId} onChange={(e) => choose("template", e.target.value)}>
              {(o?.templates ?? []).map((t) => <option key={t.id} value={t.id}>{t.title} {t.version}</option>)}
            </NativeSelect>
          </div>
          {overview.isError && <p role="alert" className="text-small text-status-danger sm:col-span-2">{friendlyMessage(asApiError(overview.error, "This connection can't be read."))}</p>}
          {o && (
            <p role="status" data-testid="author-mode" className="text-small text-ink-muted sm:col-span-2">
              {o.ai
                ? `AI model: ${o.provider}. ${o.samples ? "Up to 20 frequent values of non-personal text columns go to this local model (send samples is on); they are kept in the agent run log." : "Only table and column names and types go to the model; Tela fills filter values from its own read of the data."}`
                : "No AI model is set up, so Tela's rules draft every stage (AI settings sets one up)."}
            </p>
          )}
        </CardContent>
      </Card>

      <Steps label="Pack author stages" steps={STAGES} current={stage} reachable={() => !running} onSelect={(s) => { setStage(s); reset(); }} />

      <section aria-label={current.label} className="grid gap-3">
        <p className="text-ink-muted">{current.what}</p>
        {stage === "entities" && newTables.length > 0 && (
          <fieldset className="grid gap-1.5">
            <legend className="mb-1 text-small font-medium">Tables not in the draft yet (none ticked: the first 30)</legend>
            <div className="grid max-h-40 gap-1.5 overflow-y-auto sm:grid-cols-2">
              {newTables.map((t) => (
                <label key={t.qualified_name} className="flex items-center gap-2 text-small">
                  <Checkbox checked={tables.has(t.qualified_name)} onCheckedChange={(c) => setTables((s) => { const n = new Set(s); if (c === true) n.add(t.qualified_name); else n.delete(t.qualified_name); return n; })} />
                  <span className="font-mono">{t.qualified_name}</span><span className="text-ink-muted">{t.columns} columns</span>
                </label>
              ))}
            </div>
          </fieldset>
        )}
        {(stage === "measures" || stage === "dimensions") && (
          <div className="grid max-w-xs gap-1.5">
            <Label htmlFor="author-entity">Entity</Label>
            <NativeSelect id="author-entity" value={entity} onChange={(e) => setEntity(e.target.value)}>
              <option value="">Every entity</option>
              {ents.map((e) => <option key={e.key} value={e.key}>{e.label}</option>)}
            </NativeSelect>
          </div>
        )}
        {stage === "knowledge" && (
          <div className="grid max-w-xs gap-1.5">
            <Label htmlFor="author-kind">Kind</Label>
            <NativeSelect id="author-kind" value={kind} onChange={(e) => setKind(e.target.value as AuthorKnowledgeKind)}>
              {KNOWLEDGE.map(([k, label]) => <option key={k} value={k}>{label}</option>)}
            </NativeSelect>
          </div>
        )}
        <div className="flex flex-wrap gap-2">
          <Button onClick={() => void run()} disabled={running || !connectionId || !o}>
            <Sparkles aria-hidden />{running ? "Drafting…" : o?.ai ? `Draft ${current.label.toLowerCase()} with AI` : `Draft ${current.label.toLowerCase()} by rules`}
          </Button>
          {running && <Button variant="ghost" onClick={() => abort.current?.abort()}><Square aria-hidden />Stop</Button>}
          {pending.length > 1 && <Button variant="secondary" disabled={busy || running} onClick={() => void accept(pending)}>Accept all {pending.length}</Button>}
        </div>
        {notes.map((n, i) => <p key={i} role="status" className="text-small text-ink-muted">{n}</p>)}
        <ul aria-label="Proposals" className="grid gap-2">
          {proposals.map((p) => (
            <li key={p.id}>
              <ProposalCard packId={pack.id} connectionId={connectionId} proposal={p} accepted={accepted.has(p.id)} busy={busy}
                            onAccept={(item) => void accept([{ ...p, item }])}
                            onReject={() => setProposals((xs) => xs.filter((x) => x.id !== p.id))}
                            onChange={(next) => setProposals((xs) => xs.map((x) => (x.id === p.id ? next : x)))} />
            </li>
          ))}
        </ul>
        {dropped.length > 0 && (
          <details className="rounded-md border border-line p-3">
            <summary className="cursor-pointer text-small font-medium">Left out by the checks ({dropped.length})</summary>
            <ul aria-label="Left out" className="mt-2 grid gap-1 text-small">
              {dropped.map((d) => (
                <li key={d.id}><span className="font-medium">{d.title || d.key}</span> <span className="text-ink-muted">({d.source === "ai" ? "AI" : "rules"})</span>: {d.checks.errors.map((e) => e.message).join(" ")}</li>
              ))}
            </ul>
          </details>
        )}
      </section>
    </div>
  );
}
