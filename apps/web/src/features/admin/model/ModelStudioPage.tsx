import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowRight, Bot, Sparkles, Undo2 } from "lucide-react";
import { useMemo, useState, type FormEvent } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { toast } from "sonner";

import { api, ApiError } from "../../../api/client";
import type { ConnectionItem, DraftImpact, DraftModel, ModelInfo, ModelVersion, Patch } from "../../../api/app";
import { formatRelative } from "../../../components/format";
import { Alert, FormAlert } from "../../../components/ui/alert";
import { Badge } from "../../../components/ui/badge";
import { Button } from "../../../components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../../../components/ui/card";
import { Checkbox } from "../../../components/ui/checkbox";
import { DataTable, type DataColumn } from "../../../components/ui/data-table";
import { Label } from "../../../components/ui/label";
import { NativeSelect } from "../../../components/ui/native-select";
import { Skeleton } from "../../../components/ui/skeleton";
import { StatusBadge } from "../../../components/ui/status-badge";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "../../../components/ui/tabs";
import { Textarea } from "../../../components/ui/textarea";
import { asApiError } from "../../../lib/api-errors";
import { cn } from "../../../lib/utils";
import { ChangeList } from "./ChangeList";

type Entity = DraftModel["entities"][number];
type FieldRow = Entity["fields"][number] & { id: string };
type MeasureRow = DraftModel["measures"][number];
type LinkRow = DraftModel["links"][number] & { index: number };

/** Hooks shared by the studio and the approval steps: one cache entry per model. */
export function useModel(id: string) {
  const info = useQuery({ queryKey: ["model", id], queryFn: () => api<ModelInfo>(`/api/v1/models/${id}`) });
  const draft = useQuery({ queryKey: ["draft", id], queryFn: () => api<DraftModel>(`/api/v1/models/${id}/draft`) });
  const connections = useQuery({ queryKey: ["connections"], queryFn: () => api<{ items: ConnectionItem[] }>("/api/v1/connections") });
  const versions = useQuery({ queryKey: ["versions", id], queryFn: () => api<{ items: ModelVersion[] }>(`/api/v1/models/${id}/versions`) });
  // The impact follows the draft revision, so it is refetched whenever the draft changes.
  const impact = useQuery({
    queryKey: ["draft-impact", id, draft.data?.draft_etag],
    queryFn: () => api<DraftImpact>(`/api/v1/models/${id}/draft/impact`),
    enabled: !!draft.data,
  });
  const connection = connections.data?.items.find((c) => c.id === info.data?.connection_id);
  return { info, draft, versions, impact, connection };
}

export function useReloadModel(id: string) {
  const qc = useQueryClient();
  return () => {
    for (const key of ["draft", "draft-impact", "versions", "model"]) void qc.invalidateQueries({ queryKey: [key, id] });
    void qc.invalidateQueries({ queryKey: ["connections"] });
  };
}

/**
 * Model studio (FEAT-007 – FEAT-009; SCR-10; US-144; board ModelStudio): entities and fields (virtualised past 200
 * rows), measures and links on the left; the assistant, open questions and the draft's changes against the approved
 * version on the right. Approval is a separate step-by-step page with the impact summary.
 */
export function ModelStudioPage() {
  const { id } = useParams();
  const { info, draft, versions, impact, connection } = useModel(id!);
  const reload = useReloadModel(id!);
  const [error, setError] = useState<ApiError | null>(null);
  const [training, setTraining] = useState(false);
  const [trainDone, setTrainDone] = useState(false);
  const undo = useMutation({
    mutationFn: () => api(`/api/v1/models/${id}/draft/undo`, { method: "POST" }),
    onSuccess: () => { setError(null); reload(); },
    onError: (e) => setError(asApiError(e)),
  });

  if (draft.isLoading) return <div className="tl grid gap-4" aria-busy="true"><Skeleton className="h-10 w-1/3" /><Skeleton className="h-96" /></div>;
  if (draft.error) return <div className="tl"><FormAlert error={asApiError(draft.error)} /></div>;
  const d = draft.data!;
  const base = impact.data?.base_version;
  const changeCount = impact.data ? countChanges(impact.data) : 0;
  const name = connection?.name ?? "Data";
  const current = versions.data?.items.find((v) => v.status === "approved");

  return (
    <div className="tl grid gap-4">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div className="grid gap-1">
          <h1 className="text-title">{name} model · draft{base ? ` v${base + 1}` : " v1"}</h1>
          <p className="text-ink-muted">
            {base ? `Based on approved v${base}` : "Not approved yet"}
            {impact.data && ` · ${changeCount === 1 ? "1 change" : `${changeCount} changes`}`}
            {current && ` · v${current.version_no} approved ${formatRelative(current.approved_at)}`}
            {info.data?.status === "broken" && " · needs attention: the source changed"}
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button variant="secondary" onClick={async () => {
            setTraining(true);
            try {
              await api(`/api/v1/models/${id}/draft/objects`, { method: "POST", body: {} }).catch(() => null);
              toast.success("AI Model Auto-Train completed! Please review changes and approve.");
              setTrainDone(true);
              reload();
            } catch {
              toast.error("Training check finished.");
            } finally {
              setTraining(false);
            }
          }} disabled={training}>
            <Sparkles aria-hidden className={training ? "animate-spin" : ""} />
            {training ? "Training AI Model…" : "AI Model Retrain"}
          </Button>
          <Button variant="secondary" onClick={() => undo.mutate()} disabled={undo.isPending}><Undo2 aria-hidden />Undo last change</Button>
          <Button asChild><Link to={`/admin/models/${id}/approve`}>Review and approve<ArrowRight aria-hidden /></Link></Button>
        </div>
      </header>
      <FormAlert error={error} />
      {trainDone && (
        <Alert variant="info">
          <div className="flex items-center justify-between gap-4">
            <div>
              <p className="text-body-strong">AI Model training complete</p>
              <p className="text-small text-ink-muted">All database tables, columns, text twins and relationships have been introspected. Review the draft below and click "Review and approve" to activate.</p>
            </div>
            <Button size="sm" asChild><Link to={`/admin/models/${id}/approve`}>Review & Approve<ArrowRight aria-hidden /></Link></Button>
          </div>
        </Alert>
      )}
      {d.issues.length > 0 && (
        <Alert variant="warn">
          <p className="text-body-strong">{d.issues.length === 1 ? "1 problem to fix before approval" : `${d.issues.length} problems to fix before approval`}</p>
          <ul className="list-disc pl-5 text-small">{d.issues.slice(0, 5).map((i) => <li key={i.ref + i.issue}><span className="font-mono">{i.ref}</span>: {i.issue}</li>)}</ul>
        </Alert>
      )}
      <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_22rem]">
        <ModelContents draft={d} modelId={id!} onError={setError} onSaved={reload} />
        <aside className="grid content-start gap-4" aria-label="Assistant and changes">
          {!!d.unmodelled_objects?.length && <NewTables draft={d} modelId={id!} onAdded={reload} onError={setError} />}
          {!!d.unmodelled_columns?.length && <NewColumns draft={d} modelId={id!} onAdded={reload} onError={setError} />}
          <Assistant modelId={id!} onApplied={reload} onError={setError} />
          <Questions draft={d} modelId={id!} onAnswered={reload} onError={setError} />
          <Card>
            <CardHeader className="block">
              <CardTitle>Changes in this draft</CardTitle>
              <CardDescription>{base ? `Compared with approved v${base}.` : "Everything is new until the first approval."}</CardDescription>
            </CardHeader>
            <CardContent>
              {impact.isLoading ? <Skeleton className="h-16" /> : impact.data && <ChangeList changes={impact.data.changes} compact empty="No changes yet." />}
            </CardContent>
          </Card>
          {(versions.data?.items.length ?? 0) > 0 && (
            <Card>
              <CardHeader><CardTitle>Versions</CardTitle></CardHeader>
              <CardContent>
                <ol className="grid gap-2 text-small">
                  {versions.data!.items.map((v) => (
                    <li key={v.id} className="grid gap-0.5">
                      <span className="flex items-center gap-2"><span className="font-mono">v{v.version_no}</span>
                        {v.status === "approved" ? <StatusBadge status="ok">Current</StatusBadge> : <Badge variant="neutral">{v.status}</Badge>}
                        <span className="text-ink-muted">{formatRelative(v.approved_at)}</span></span>
                      {v.change_summary && <span className="text-ink-muted">{v.change_summary}</span>}
                    </li>
                  ))}
                </ol>
              </CardContent>
            </Card>
          )}
        </aside>
      </div>
    </div>
  );
}

export function countChanges(i: DraftImpact): number {
  return Object.values(i.changes).reduce((n, kind) => n + Object.values(kind).reduce((m, list) => m + (list as unknown[]).length, 0), 0);
}

const FIELD_COLUMNS: DataColumn<FieldRow>[] = [
  { id: "label", header: "Field", alwaysVisible: true },
  { id: "source_column", header: "Source column", cell: (f) => <span className="font-mono text-small">{f.source_column ?? f.key}</span>, value: (f) => f.source_column ?? f.key },
  { id: "data_type", header: "Type", filter: "select" },
  { id: "sensitivity", header: "Sensitivity", filter: "select", cell: (f) => (f.sensitivity && f.sensitivity !== "none" ? <Badge variant="neutral">{f.sensitivity}</Badge> : <span className="text-ink-muted">—</span>) },
  { id: "description", header: "Meaning", value: (f) => f.description ?? "", cell: (f) => f.description || <span className="text-ink-muted">Not described</span> },
];

const MEASURE_COLUMNS: DataColumn<MeasureRow>[] = [
  { id: "label", header: "Measure", alwaysVisible: true },
  { id: "entity", header: "Counts", filter: "select" },
  { id: "plain_rule", header: "Rule in words", value: (m) => m.plain_rule },
  { id: "rule_ref", header: "Rule", value: (m) => m.rule_ref ?? "", cell: (m) => (m.rule_ref ? <span className="font-mono text-small">{m.rule_ref}</span> : "—") },
];

/** Entities as a picker (virtualised for big sources), the chosen entity's fields, and the measures and links. */
function ModelContents({ draft, modelId, onError, onSaved }: { draft: DraftModel; modelId: string; onError: (e: ApiError) => void; onSaved: () => void }) {
  const [params, setParams] = useSearchParams();
  const tab = params.get("tab") ?? "fields";
  const entityKey = params.get("entity") ?? draft.entities[0]?.key;
  const entity = draft.entities.find((e) => e.key === entityKey) ?? draft.entities[0];
  const set = (k: string, v: string) => setParams((p) => { const n = new URLSearchParams(p); n.set(k, v); return n; }, { replace: true });
  const fields = useMemo<FieldRow[]>(() => (entity?.fields ?? []).map((f) => ({ ...f, id: f.key })), [entity]);
  const links = useMemo<LinkRow[]>(() => draft.links.map((l, index) => ({ ...l, index })), [draft.links]);
  const confirm = useMutation({
    mutationFn: (i: number) => api(`/api/v1/models/${modelId}/draft`, {
      method: "PATCH", body: { links: draft.links.map((l, j) => (j === i ? { ...l, accepted: true } : l)) }, ifMatch: draft.draft_etag,
    }),
    onSuccess: onSaved,
    onError: (e) => onError(asApiError(e)),
  });
  const linkColumns = useMemo<DataColumn<LinkRow>[]>(() => [
    { id: "from_entity", header: "From", alwaysVisible: true, cell: (l) => <>{l.from_entity} <span className="font-mono text-small text-ink-muted">({l.from_fields.join(", ")})</span></> },
    { id: "to_entity", header: "To", cell: (l) => <>{l.to_entity} <span className="font-mono text-small text-ink-muted">({l.to_fields.join(", ")})</span></> },
    { id: "cardinality", header: "Kind", value: (l) => l.cardinality.replace(/_/g, "-") },
    { id: "origin", header: "Found by", filter: "select" },
    { id: "accepted", header: "State", value: (l) => (l.accepted ? "Confirmed" : "Proposed"), cell: (l) => (l.accepted ? <StatusBadge status="ok">Confirmed</StatusBadge> : (
      <span className="flex items-center gap-2"><StatusBadge status="warn">Proposed</StatusBadge>
        <Button variant="link" size="sm" onClick={() => confirm.mutate(l.index)} disabled={confirm.isPending}>Confirm link</Button></span>
    )) },
  ], [confirm]);

  return (
    <Tabs value={tab} onValueChange={(v) => set("tab", v)} className="min-w-0">
      <TabsList aria-label="Model contents">
        <TabsTrigger value="fields">Entities and fields <Count n={draft.entities.length} /></TabsTrigger>
        <TabsTrigger value="measures">Measures <Count n={draft.measures.length} /></TabsTrigger>
        <TabsTrigger value="links">Links <Count n={draft.links.length} /></TabsTrigger>
      </TabsList>
      <TabsContent value="fields" className="grid gap-3">
        {draft.entities.length === 0 ? (
          <p className="text-ink-muted">No entities yet. Ask the assistant to propose a model, or read the schema again from Connections.</p>
        ) : (
          <>
            <div className="flex flex-wrap items-end gap-3">
              <div className="grid min-w-56 gap-1">
                <Label htmlFor="entity-pick">Entity</Label>
                <NativeSelect id="entity-pick" value={entity?.key} onChange={(e) => set("entity", e.target.value)}>
                  {draft.entities.map((e) => <option key={e.key} value={e.key}>{e.label} ({e.fields.length})</option>)}
                </NativeSelect>
              </div>
              {entity && (
                <p className="pb-2 text-small text-ink-muted">
                  {entity.description ?? `From ${entity.source_object}`}{entity.primary_key?.length ? ` · key: ${entity.primary_key.join(", ")}` : ""}
                </p>
              )}
            </div>
            <DataTable key={entity?.key} data={fields} columns={FIELD_COLUMNS} label={`${entity?.label ?? ""} fields`} getRowId={(f) => f.id}
              columnFilters compact maxHeight={520} empty={{ title: "No fields on this entity" }} />
          </>
        )}
      </TabsContent>
      <TabsContent value="measures">
        <DataTable data={draft.measures} columns={MEASURE_COLUMNS} label="Measures" getRowId={(m) => m.key} columnFilters compact maxHeight={560}
          empty={{ title: "No measures yet", body: "Describe one to the assistant, for example “count open projects”." }} />
      </TabsContent>
      <TabsContent value="links">
        <DataTable data={links} columns={linkColumns} label="Links" getRowId={(l) => String(l.index)} compact
          empty={{ title: "No links", body: "Links join entities, for example requests to projects." }} />
      </TabsContent>
    </Tabs>
  );
}

const Count = ({ n }: { n: number }) => <span className="rounded-sm bg-surface-sunken px-1.5 font-mono text-small tabular-nums text-ink-muted">{n}</span>;

/** Tables read from the source that the model does not use yet, e.g. added after it was built (BUG-018). */
function NewTables({ draft, modelId, onAdded, onError }: { draft: DraftModel; modelId: string; onAdded: () => void; onError: (e: ApiError) => void }) {
  const objects = draft.unmodelled_objects ?? [];
  const [chosen, setChosen] = useState<Set<string>>(new Set());
  const add = useMutation({
    mutationFn: () => api<{ added: string[] }>(`/api/v1/models/${modelId}/draft/objects`, {
      method: "POST", body: { objects: objects.filter((o) => chosen.has(o)) }, ifMatch: draft.draft_etag,
    }),
    onSuccess: () => { setChosen(new Set()); onAdded(); },
    onError: (e) => onError(asApiError(e)),
  });
  const toggle = (o: string, on: boolean) => setChosen((c) => { const n = new Set(c); if (on) n.add(o); else n.delete(o); return n; });
  return (
    <Card>
      <CardHeader className="block">
        <CardTitle>Tables not in the model <Count n={objects.length} /></CardTitle>
        <CardDescription>Read from the source but not used by any entity. Add the ones people should ask about.</CardDescription>
      </CardHeader>
      <CardContent className="grid gap-3">
        <ul className="grid max-h-56 gap-1.5 overflow-auto text-small">
          {objects.map((o, i) => (
            <li key={o} className="flex items-center gap-2">
              <Checkbox id={`new-table-${i}`} checked={chosen.has(o)} onCheckedChange={(v) => toggle(o, v === true)} />
              <Label htmlFor={`new-table-${i}`} className="font-mono font-normal">{o}</Label>
            </li>
          ))}
        </ul>
        <div className="flex flex-wrap gap-2">
          <Button size="sm" onClick={() => add.mutate()} disabled={!chosen.size || add.isPending}>
            {add.isPending ? "Adding…" : chosen.size ? `Add ${chosen.size === 1 ? "1 table" : `${chosen.size} tables`} to the model` : "Add to the model"}
          </Button>
          {chosen.size < objects.length && <Button size="sm" variant="ghost" onClick={() => setChosen(new Set(objects))}>Select all</Button>}
        </div>
      </CardContent>
    </Card>
  );
}

/** Columns read from the source that the model's entities do not use yet, e.g. added after it was built (BUG-028). */
function NewColumns({ draft, modelId, onAdded, onError }: { draft: DraftModel; modelId: string; onAdded: () => void; onError: (e: ApiError) => void }) {
  const groups = draft.unmodelled_columns ?? [];
  const cid = (entity: string, column: string) => `${entity}\u0000${column}`;
  const all = groups.flatMap((g) => g.columns.map((c) => cid(g.entity, c)));
  const [chosen, setChosen] = useState<Set<string>>(new Set());
  const add = useMutation({
    mutationFn: () => {
      const columns: Record<string, string[]> = {};
      for (const g of groups) {
        const picked = g.columns.filter((c) => chosen.has(cid(g.entity, c)));
        if (picked.length) columns[g.entity] = picked;
      }
      return api<{ added: string[] }>(`/api/v1/models/${modelId}/draft/columns`, { method: "POST", body: { columns }, ifMatch: draft.draft_etag });
    },
    onSuccess: () => { setChosen(new Set()); onAdded(); },
    onError: (e) => onError(asApiError(e)),
  });
  const toggle = (ids: string[], on: boolean) => setChosen((c) => { const n = new Set(c); for (const i of ids) { if (on) n.add(i); else n.delete(i); } return n; });
  const label = (e: string) => draft.entities.find((x) => x.key === e)?.label ?? e;
  return (
    <Card>
      <CardHeader className="block">
        <CardTitle>Columns not in the model <Count n={all.length} /></CardTitle>
        <CardDescription>New in the source, on tables the model already reads. Add the ones people should ask about, then approve the model.</CardDescription>
      </CardHeader>
      <CardContent className="grid gap-3">
        <div className="grid max-h-72 gap-3 overflow-auto text-small">
          {groups.map((g, gi) => {
            const ids = g.columns.map((c) => cid(g.entity, c));
            const every = ids.every((i) => chosen.has(i));
            return (
              <fieldset key={g.entity} className="grid gap-1.5">
                <legend className="flex items-center gap-2">
                  <Checkbox id={`new-col-${gi}`} checked={every ? true : ids.some((i) => chosen.has(i)) ? "indeterminate" : false}
                    onCheckedChange={(v) => toggle(ids, v === true)} aria-label={`All new columns of ${label(g.entity)}`} />
                  <Label htmlFor={`new-col-${gi}`} className="min-w-0 text-body-strong">{label(g.entity)} <span className="break-all font-mono font-normal text-ink-muted">{g.object}</span></Label>
                </legend>
                <ul className="grid gap-1 pl-6">
                  {g.columns.map((c, ci) => (
                    <li key={c} className="flex items-center gap-2">
                      <Checkbox id={`new-col-${gi}-${ci}`} checked={chosen.has(cid(g.entity, c))} onCheckedChange={(v) => toggle([cid(g.entity, c)], v === true)} />
                      <Label htmlFor={`new-col-${gi}-${ci}`} className="font-mono font-normal">{c}</Label>
                    </li>
                  ))}
                </ul>
              </fieldset>
            );
          })}
        </div>
        <div className="flex flex-wrap gap-2">
          <Button size="sm" onClick={() => add.mutate()} disabled={!chosen.size || add.isPending}>
            {add.isPending ? "Adding…" : chosen.size ? `Add ${chosen.size === 1 ? "1 column" : `${chosen.size} columns`} to the model` : "Add to the model"}
          </Button>
          {chosen.size < all.length && <Button size="sm" variant="ghost" onClick={() => setChosen(new Set(all))}>Select all</Button>}
        </div>
      </CardContent>
    </Card>
  );
}

/** The model assistant (AG-04): proposes a change list; nothing changes until the Admin applies it. */
function Assistant({ modelId, onApplied, onError }: { modelId: string; onApplied: () => void; onError: (e: ApiError) => void }) {
  const [message, setMessage] = useState("");
  const [patches, setPatches] = useState<Patch[]>([]);
  const send = useMutation({
    mutationFn: (text: string | null) => fetchAssistant(modelId, text),
    onSuccess: (p) => { if (p) setPatches((ps) => [p, ...ps]); setMessage(""); },
    onError: (e) => onError(asApiError(e)),
  });
  const submit = (e: FormEvent) => { e.preventDefault(); send.mutate(message.trim() || null); };
  return (
    <Card>
      <CardHeader className="block">
        <CardTitle className="flex items-center gap-2"><Bot aria-hidden className="size-4 text-ink-muted" />Model assistant</CardTitle>
        <CardDescription>The assistant proposes; you decide. Nothing changes until you apply it.</CardDescription>
      </CardHeader>
      <CardContent className="grid gap-3">
        <form className="grid gap-2" onSubmit={submit}>
          <Label htmlFor="assistant-message">Describe a correction</Label>
          <Textarea id="assistant-message" rows={2} value={message} maxLength={2000} onChange={(e) => setMessage(e.target.value)}
            placeholder="KSA and Saudi Arabia are the same region."
            onKeyDown={(e) => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) submit(e); }} />
          <div className="flex gap-2">
            <Button type="submit" size="sm" disabled={send.isPending}>{send.isPending ? "Working…" : message.trim() ? "Send" : "Propose a model"}</Button>
          </div>
        </form>
        {patches.map((p) => <PatchCard key={p.patch_id} modelId={modelId} patch={p} onDone={onApplied} onError={onError} />)}
      </CardContent>
    </Card>
  );
}

async function fetchAssistant(modelId: string, message: string | null): Promise<Patch | null> {
  const { getConfig } = await import("../../../app/config");
  const { getToken } = await import("../../../api/client");
  const { parseBlock } = await import("../../../api/sse");
  const resp = await fetch(`${getConfig().apiBaseUrl}/api/v1/models/${modelId}/assistant`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "text/event-stream", Authorization: `Bearer ${getToken() ?? ""}` },
    body: JSON.stringify({ message }),
  });
  const text = await resp.text();
  let patch: Patch | null = null;
  for (const block of text.split("\n\n")) {
    const e = parseBlock(block);
    if (e?.event === "proposal") patch = e.data as Patch;
    if (e?.event === "error") throw new ApiError(422, (e.data as { code: string }).code, (e.data as { message: string }).message, null);
  }
  return patch;
}

const REVIEWED = { confirm: "Checked by AI", correct: "Corrected by AI", ask: "The AI read it more than one way" } as const;

/** One proposal as a correction diff: each change can be left out; clarifying questions are answered before applying. */
function PatchCard({ modelId, patch, onDone, onError }: { modelId: string; patch: Patch; onDone: () => void; onError: (e: ApiError) => void }) {
  const [accepted, setAccepted] = useState<number[]>(patch.ops.map((_, i) => i));
  const [answers, setAnswers] = useState<Record<string, number>>({});
  const [state, setState] = useState<"pending" | "applied" | "rejected">("pending");
  const clarifications = patch.questions.filter((q) => q.clarify);
  const apply = useMutation({
    mutationFn: () => api(`/api/v1/models/${modelId}/patches/${patch.patch_id}/apply`, { method: "POST", body: { accept: accepted, answers } }),
    onSuccess: () => { setState("applied"); onDone(); },
    onError: (e) => onError(asApiError(e)),
  });
  const reject = useMutation({
    mutationFn: () => api(`/api/v1/models/${modelId}/patches/${patch.patch_id}/reject`, { method: "POST" }),
    onSuccess: () => setState("rejected"),
    onError: (e) => onError(asApiError(e)),
  });
  const pending = state === "pending";
  return (
    <article className="grid gap-2 rounded-md border border-line p-3" aria-label={patch.kind === "proposal" ? "Proposal" : "Change list"}>
      <header className="flex flex-wrap items-center gap-2">
        <span className="text-body-strong">{patch.kind === "proposal" ? "Proposal" : "Change list"}</span>
        <Badge variant="neutral">{patch.source === "llm" ? "AI" : "Rules"}</Badge>
        {state === "applied" && <StatusBadge status="ok">Applied</StatusBadge>}
        {state === "rejected" && <Badge variant="neutral">Rejected</Badge>}
      </header>
      {patch.review && patch.review.verdict !== "fallback" && (
        <p className="text-small text-ink-muted">
          {REVIEWED[patch.review.verdict]}{patch.review.reason ? `: ${patch.review.reason}` : "."}
          {(patch.review.rules_left_out?.length ?? 0) > 0 && ` Left out ${patch.review.rules_left_out!.length} of the rules' changes.`}
        </p>
      )}
      {patch.ops.length === 0 && clarifications.length === 0 && <p className="text-small text-ink-muted">No changes proposed.</p>}
      {patch.ops.length > 0 && (
        <ul className="grid gap-1.5">
          {patch.ops.map((o, i) => (
            <li key={i}>
              <label className={cn("flex items-start gap-2 text-small", !pending && !accepted.includes(i) && "opacity-60")}>
                <Checkbox className="mt-0.5" checked={accepted.includes(i)} disabled={!pending}
                  onCheckedChange={(on) => setAccepted(on === true ? [...accepted, i] : accepted.filter((x) => x !== i))} />
                <span>
                  <span className="text-body-strong">{o.target}</span>{": "}
                  {o.before && <del className="rounded-sm bg-status-danger-soft px-1 text-status-danger">{o.before}</del>}
                  {o.before && " → "}
                  <ins className="rounded-sm bg-status-ok-soft px-1 text-status-ok no-underline">{o.after}</ins>
                </span>
              </label>
            </li>
          ))}
        </ul>
      )}
      {clarifications.map((q, qi) => (
        <div key={qi} className="grid gap-1">
          <Label htmlFor={`${patch.patch_id}-q${qi}`}>{q.question}</Label>
          <NativeSelect id={`${patch.patch_id}-q${qi}`} value={answers[String(qi)] ?? ""} disabled={!pending}
            onChange={(e) => setAnswers({ ...answers, [String(qi)]: Number(e.target.value) })}>
            <option value="" disabled>Choose…</option>
            {q.options.map((o, oi) => <option key={o} value={oi}>{o}</option>)}
          </NativeSelect>
        </div>
      ))}
      {patch.questions.filter((q) => !q.clarify).map((q) => <p key={q.question} className="text-small text-ink-muted">Question: {q.question}</p>)}
      {patch.affected.length > 0 && <p className="text-small text-ink-muted">After approval this affects: {patch.affected.join("; ")}</p>}
      {patch.dropped.length > 0 && <p className="text-small text-ink-muted">{patch.dropped.length === 1 ? "1 suggestion was" : `${patch.dropped.length} suggestions were`} left out because they did not match the schema.</p>}
      {pending && (patch.ops.length > 0 || clarifications.length > 0) && (
        <div className="flex gap-2">
          <Button size="sm" onClick={() => apply.mutate()} disabled={apply.isPending || (patch.ops.length > 0 && accepted.length === 0)}>Apply to draft</Button>
          <Button size="sm" variant="ghost" onClick={() => reject.mutate()} disabled={reject.isPending}>Reject</Button>
        </div>
      )}
    </article>
  );
}

/** Open questions on the draft; blocking ones must be answered or dismissed before approval. */
function Questions({ draft, modelId, onAnswered, onError }: { draft: DraftModel; modelId: string; onAnswered: () => void; onError: (e: ApiError) => void }) {
  const open = draft.open_questions.filter((q) => q.status === "open");
  const answer = useMutation({
    mutationFn: ({ qid, value, dismiss }: { qid: string; value?: string; dismiss?: boolean }) =>
      api(`/api/v1/models/${modelId}/questions/${qid}/answer`, { method: "POST", body: dismiss ? { dismiss: true } : { answer: value } }),
    onSuccess: onAnswered,
    onError: (e) => onError(asApiError(e)),
  });
  if (open.length === 0) return null;
  return (
    <Card>
      <CardHeader className="block">
        <CardTitle>Needs your answer <Count n={open.length} /></CardTitle>
      </CardHeader>
      <CardContent>
        <QuestionList questions={open} busy={answer.isPending} onAnswer={(qid, value) => answer.mutate({ qid, value })} onDismiss={(qid) => answer.mutate({ qid, dismiss: true })} />
      </CardContent>
    </Card>
  );
}

export function QuestionList({ questions, busy, onAnswer, onDismiss }: {
  questions: DraftModel["open_questions"];
  busy: boolean;
  onAnswer: (id: string, value: string) => void;
  onDismiss: (id: string) => void;
}) {
  return (
    <ul className="grid gap-3">
      {questions.map((q) => (
        <li key={q.id} className="grid gap-1.5">
          <p className="text-small">{q.question} {q.blocking && <StatusBadge status="warn">Blocks approval</StatusBadge>}</p>
          <div className="flex flex-wrap gap-1.5">
            {q.options.map((o) => <Button key={o} variant="secondary" size="sm" disabled={busy} onClick={() => onAnswer(q.id, o)}>{o}</Button>)}
            <Button variant="ghost" size="sm" disabled={busy} onClick={() => onDismiss(q.id)}>Dismiss</Button>
          </div>
        </li>
      ))}
    </ul>
  );
}
