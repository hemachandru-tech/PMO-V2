import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, Plus, Search, X } from "lucide-react";
import { useId, useMemo, useState, type FormEvent } from "react";
import { toast } from "sonner";

import { api } from "../../../api/client";
import type { IndexedDimension, LinkHow, ValueLinkResult, ValueSynonym, ValueSynonymList } from "../../../api/knowledge";
import { formatRelative } from "../../../components/format";
import { Alert, FormAlert } from "../../../components/ui/alert";
import { Badge } from "../../../components/ui/badge";
import { Button } from "../../../components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../../../components/ui/card";
import { DataTable, type DataColumn } from "../../../components/ui/data-table";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "../../../components/ui/dialog";
import { Input } from "../../../components/ui/input";
import { Label } from "../../../components/ui/label";
import { NativeSelect } from "../../../components/ui/native-select";
import { asApiError } from "../../../lib/api-errors";
import { ConnectionPicker, useConnectionParam } from "../access/ConnectionPicker";
import { KnowledgeNav } from "./KnowledgeNav";

const SOURCE: Record<ValueSynonym["source"], string> = { pack: "PMO pack", clarification: "Chosen in Ask", admin: "Added by an Admin", answer: "Approved answer" };
const HOW: Record<LinkHow, string> = {
  exact: "the value itself", synonym: "an approved synonym", partial: "part of the value", spelling: "a spelling slip", meaning: "similar meaning",
};

function StatusBadge({ s }: { s: ValueSynonym }) {
  if (s.status === "approved") return <Badge variant="certified">Approved</Badge>;
  if (s.status === "rejected") return <Badge variant="outline">Rejected</Badge>;
  return <Badge variant="stale">Waiting for review</Badge>;
}

/**
 * Admin → Knowledge → Value synonyms (US-173; FEAT-062 / FEAT-014): the words people use for stored values ("Saudi
 * Arabia" for region KSA). Ask links a question's words to stored values before planning; synonyms from the PMO pack
 * and from choices people made in Ask wait here until an Admin approves them. "Try a phrase" shows what a phrase links
 * to over every indexed value (people only ever get values in their own scope).
 */
export function ValueSynonymsPage() {
  const qc = useQueryClient();
  const [connection, setConnection] = useConnectionParam();
  const key = ["value-synonyms", connection];
  const q = useQuery({
    queryKey: key, enabled: !!connection,
    queryFn: () => api<ValueSynonymList>(`/api/v1/knowledge/value-synonyms?connection_id=${encodeURIComponent(connection)}`),
  });
  const [selected, setSelected] = useState<ValueSynonym[]>([]);
  const [adding, setAdding] = useState(false);
  const refresh = () => void qc.invalidateQueries({ queryKey: ["value-synonyms"] });

  const review = useMutation({
    mutationFn: (v: { decision: "approve" | "reject"; ids: string[] }) =>
      api<{ updated: number }>("/api/v1/knowledge/value-synonyms/review", { method: "POST", body: v }),
    onSuccess: (out, v) => {
      refresh();
      setSelected([]);
      toast.success(`${out.updated} ${out.updated === 1 ? "synonym" : "synonyms"} ${v.decision === "approve" ? "approved" : "rejected"}`);
    },
    onError: (e) => toast.error(asApiError(e, "The review could not be saved.").message),
  });

  const approvePack = useMutation({
    mutationFn: () => api<{ updated: number }>("/api/v1/knowledge/value-synonyms/approve-pack", { method: "POST", body: { connection_id: connection } }),
    onSuccess: (out) => {
      refresh();
      void qc.invalidateQueries({ queryKey: ["admin-overview"] });
      toast.success(`${out.updated} pack ${out.updated === 1 ? "synonym" : "synonyms"} approved`);
    },
    onError: (e) => toast.error(asApiError(e, "The pack synonyms could not be approved.").message),
  });

  const items = q.data?.items ?? [];
  const pending = items.filter((s) => s.status === "pending_review");
  const packPending = pending.filter((s) => s.source === "pack").length;
  const columns = useMemo<DataColumn<ValueSynonym>[]>(() => [
    {
      id: "synonym", header: "Words people use", alwaysVisible: true, value: (s) => s.synonym,
      cell: (s) => <span className="font-medium">{s.synonym}</span>,
    },
    {
      id: "value", header: "Stored value", value: (s) => `${s.dimension_label}: ${s.stored_value}`,
      cell: (s) => (
        <span className="grid gap-0.5">
          <span><span className="text-ink-muted">{s.dimension_label}:</span> <span className="font-mono">{s.stored_value}</span></span>
          {!s.in_data && <span className="text-small text-ink-muted">Not in the data now, so it links to nothing</span>}
        </span>
      ),
    },
    { id: "source", header: "From", filter: "select", value: (s) => SOURCE[s.source], cell: (s) => (
      <span className="grid gap-0.5">{SOURCE[s.source]}{s.proposed_by && (s.source === "clarification" || s.source === "answer") && <span className="text-small text-ink-muted">by {s.proposed_by}</span>}</span>
    ) },
    { id: "status", header: "Status", filter: "select", value: (s) => s.status, cell: (s) => (
      <span className="grid gap-0.5"><StatusBadge s={s} />{s.note && <span className="text-small text-ink-muted">{s.note}</span>}</span>
    ) },
    {
      id: "actions", header: "Actions", alwaysVisible: true, sortable: false, value: () => "",
      cell: (s) => (
        <span className="flex flex-wrap gap-x-3">
          {s.status !== "approved" && (
            <Button variant="link" size="sm" aria-label={`Approve ${s.synonym}`} onClick={() => review.mutate({ decision: "approve", ids: [s.id] })}><Check aria-hidden />Approve</Button>
          )}
          {s.status !== "rejected" && (
            <Button variant="link" size="sm" aria-label={`Reject ${s.synonym}`} onClick={() => review.mutate({ decision: "reject", ids: [s.id] })}><X aria-hidden />Reject</Button>
          )}
        </span>
      ),
    },
  ], [review]);

  const index = q.data?.index;
  return (
    <div className="tl grid gap-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="grid gap-1">
          <h1 className="text-title">Knowledge</h1>
          <p className="max-w-prose text-ink-muted">Ask matches the words in a question to the values stored in your data, so “Saudi Arabia” filters on region KSA. Synonyms are used once approved.</p>
        </div>
        <Button variant="secondary" disabled={!connection || !index?.values} onClick={() => setAdding(true)}><Plus aria-hidden />Add synonym</Button>
      </div>
      <KnowledgeNav />
      <ConnectionPicker value={connection} onChange={setConnection} />
      {connection && (
        <>
          {index && (
            <p className="text-small text-ink-muted">
              {index.values > 0
                ? `${index.values.toLocaleString()} values of ${index.dimensions} fields indexed${index.refreshed_at ? `, refreshed ${formatRelative(index.refreshed_at)}` : ""}. The index is refreshed when a model is approved and every night.`
                : "No values indexed yet. They are read when the model is approved and every night."}
            </p>
          )}
          <TryPhrase connection={connection} />
          {pending.length > 0 && (
            <Alert>
              <span className="flex flex-wrap items-center justify-between gap-2">
                <span>{pending.length === 1 ? "1 synonym is" : `${pending.length} synonyms are`} waiting for review.{packPending > 0 && ` ${packPending} came with the pack; until approved, Ask doesn't use them.`}</span>
                {packPending > 0 && (
                  <Button size="sm" disabled={approvePack.isPending} onClick={() => approvePack.mutate()}><Check aria-hidden />Approve all from the pack ({packPending})</Button>
                )}
              </span>
            </Alert>
          )}
          <DataTable
            data={items}
            columns={columns}
            label="Value synonyms"
            getRowId={(s) => s.id}
            rowLabel={(s) => `${s.synonym} → ${s.stored_value}`}
            loading={q.isLoading}
            error={q.error && <FormAlert error={asApiError(q.error)} />}
            empty={{ title: "No synonyms yet", body: "Approving a model from the PMO pack adds its synonyms here; choices people make in Ask are added too." }}
            onSelectionChange={setSelected}
            toolbar={selected.length > 0 ? (
              <span className="flex gap-2">
                <Button size="sm" onClick={() => review.mutate({ decision: "approve", ids: selected.map((s) => s.id) })}>Approve {selected.length}</Button>
                <Button size="sm" variant="secondary" onClick={() => review.mutate({ decision: "reject", ids: selected.map((s) => s.id) })}>Reject {selected.length}</Button>
              </span>
            ) : undefined}
            search={items.length > 8}
          />
          <AddDialog open={adding} connection={connection} onClose={() => setAdding(false)} onSaved={refresh} />
        </>
      )}
    </div>
  );
}

function TryPhrase({ connection }: { connection: string }) {
  const id = useId();
  const [phrase, setPhrase] = useState("");
  const link = useMutation({
    mutationFn: (text: string) => api<ValueLinkResult>("/api/v1/knowledge/values/link", { method: "POST", body: { connection_id: connection, text } }),
  });
  const submit = (e: FormEvent) => { e.preventDefault(); if (phrase.trim()) link.mutate(phrase.trim()); };
  const out = link.data;
  return (
    <Card>
      <CardHeader>
        <CardTitle>Try a phrase</CardTitle>
        <CardDescription>See which stored values a question's words link to, over every indexed value.</CardDescription>
      </CardHeader>
      <CardContent className="grid gap-3">
        <form onSubmit={submit} className="flex max-w-xl items-end gap-2">
          <div className="grid flex-1 gap-1">
            <Label htmlFor={id}>Phrase</Label>
            <Input id={id} value={phrase} maxLength={500} onChange={(e) => setPhrase(e.target.value)} placeholder="Open projects in Saudi Arabia" />
          </div>
          <Button type="submit" variant="secondary" disabled={!phrase.trim() || link.isPending}><Search aria-hidden />Try</Button>
        </form>
        {link.error && <FormAlert error={asApiError(link.error)} />}
        {out && (
          <ul className="grid gap-1 text-small" aria-live="polite">
            {out.links.length === 0 && out.choices.length === 0 && <li className="text-ink-muted">No value linked.</li>}
            {out.links.map((l) => (
              <li key={`${l.dimension}-${l.stored}-${l.phrase}`}>“{l.phrase}” → <span className="text-ink-muted">{l.dimension}:</span> <span className="font-mono">{l.stored}</span> <span className="text-ink-muted">({HOW[l.how]})</span></li>
            ))}
            {out.choices.map((c) => (
              <li key={`${c.dimension}-${c.phrase}`}>“{c.phrase}” could be {c.options.map((o) => o.label).join(" or ")}: Ask will ask which.</li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}

function AddDialog({ open, connection, onClose, onSaved }: { open: boolean; connection: string; onClose: () => void; onSaved: () => void }) {
  const ids = { dim: useId(), value: useId(), synonym: useId() };
  const values = useQuery({
    queryKey: ["indexed-values", connection], enabled: open,
    queryFn: () => api<{ dimensions: IndexedDimension[] }>(`/api/v1/knowledge/values?connection_id=${encodeURIComponent(connection)}`),
  });
  const [dimension, setDimension] = useState("");
  const [stored, setStored] = useState("");
  const [synonym, setSynonym] = useState("");
  const dims = values.data?.dimensions ?? [];
  const chosen = dims.find((d) => d.key === dimension);
  const save = useMutation({
    mutationFn: () => api("/api/v1/knowledge/value-synonyms", { method: "POST", body: { connection_id: connection, dimension, stored_value: stored, synonym } }),
    onSuccess: () => { onSaved(); toast.success(`“${synonym}” now links to ${stored}`); close(); },
  });
  const close = () => { setDimension(""); setStored(""); setSynonym(""); save.reset(); onClose(); };
  const submit = (e: FormEvent) => { e.preventDefault(); if (dimension && stored && synonym.trim()) save.mutate(); };
  return (
    <Dialog open={open} onOpenChange={(o) => { if (!o) close(); }}>
      <DialogContent>
        <form onSubmit={submit} className="grid gap-4">
          <DialogHeader>
            <DialogTitle>Add a synonym</DialogTitle>
            <DialogDescription>Your synonym is approved as you add it, and Ask uses it for everyone on this source.</DialogDescription>
          </DialogHeader>
          {save.error && <FormAlert error={asApiError(save.error)} />}
          <div className="grid gap-1">
            <Label htmlFor={ids.dim}>Field</Label>
            <NativeSelect id={ids.dim} value={dimension} onChange={(e) => { setDimension(e.target.value); setStored(""); }} required>
              <option value="">{values.isLoading ? "Loading…" : "Choose a field…"}</option>
              {dims.map((d) => <option key={d.key} value={d.key}>{d.label}</option>)}
            </NativeSelect>
          </div>
          <div className="grid gap-1">
            <Label htmlFor={ids.value}>Stored value</Label>
            <NativeSelect id={ids.value} value={stored} onChange={(e) => setStored(e.target.value)} disabled={!chosen} required>
              <option value="">Choose a value…</option>
              {chosen?.values.map((v) => <option key={v.stored} value={v.stored}>{v.label === v.stored ? v.stored : `${v.label} (stored as ${v.stored})`}</option>)}
            </NativeSelect>
          </div>
          <div className="grid gap-1">
            <Label htmlFor={ids.synonym}>Words people use</Label>
            <Input id={ids.synonym} value={synonym} maxLength={120} onChange={(e) => setSynonym(e.target.value)} placeholder="Saudi Arabia" required />
          </div>
          <DialogFooter>
            <Button type="button" variant="secondary" onClick={close}>Cancel</Button>
            <Button type="submit" disabled={!dimension || !stored || !synonym.trim() || save.isPending}>Add synonym</Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
