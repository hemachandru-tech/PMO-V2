import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Archive, Calculator, Check } from "lucide-react";
import { useId, useMemo, useState } from "react";
import { toast } from "sonner";

import { api } from "../../../api/client";
import type { Catalog } from "../../../api/govern";
import type { DefinitionList, DefinitionRule, KnowledgeDefinition } from "../../../api/knowledge";
import { formatRelative, formatValue } from "../../../components/format";
import { FormAlert } from "../../../components/ui/alert";
import { Badge } from "../../../components/ui/badge";
import { Button } from "../../../components/ui/button";
import { DataTable, type DataColumn } from "../../../components/ui/data-table";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "../../../components/ui/dialog";
import { Input } from "../../../components/ui/input";
import { Label } from "../../../components/ui/label";
import { NativeSelect } from "../../../components/ui/native-select";
import { Textarea } from "../../../components/ui/textarea";
import { asApiError } from "../../../lib/api-errors";
import { KnowledgeNav } from "./KnowledgeNav";

type Filter = "pending" | "approved" | "rejected" | "all";

function StatusBadge({ d }: { d: KnowledgeDefinition }) {
  if (d.status === "approved") return <Badge variant="certified">Approved</Badge>;
  if (d.status === "rejected" || d.status === "retired") return <Badge variant="outline">{d.status === "rejected" ? "Rejected" : "Retired"}</Badge>;
  return <Badge variant="stale">Waiting for review</Badge>;
}

/** The rule in words, or why the definition is text only. */
function RuleCell({ d }: { d: KnowledgeDefinition }) {
  if (d.expressible && d.rule_text) return <span>{d.rule_text}</span>;
  return (
    <span className="grid gap-0.5">
      <span className="text-ink-muted">Text only</span>
      {d.gaps.map((g) => <span key={g} className="text-small text-status-warn">{g}</span>)}
    </span>
  );
}

/** The certified measure of the same term, with both figures once counted. */
function MeasureCell({ d }: { d: KnowledgeDefinition }) {
  if (!d.measure) return <span className="text-ink-muted">No measure of this name</span>;
  const c = d.checks;
  return (
    <span className="grid gap-0.5">
      <span>{d.measure.label ?? d.measure.ref}</span>
      {c?.measure && <span className="text-small text-ink-muted">Rule: {c.measure.rule}</span>}
      {c && c.figure !== null && c.measure && (
        <span className="text-small tabular-nums">Measure {formatValue(c.measure.figure)} · this definition {formatValue(c.figure)}</span>
      )}
      {d.conflict && <Badge variant="stale">Differs from the measure</Badge>}
      {c?.same_rule && <span className="text-small text-ink-muted">Same rule as the measure</span>}
      {!c && <span className="text-small text-ink-muted">Not counted yet</span>}
    </span>
  );
}

/**
 * Admin → Knowledge → Definitions (US-203; FEAT-067 / FEAT-014): the business definitions read from uploaded documents
 * and glossaries — a term, its meaning and, where the model can express it, a rule — each linked to its passage.
 * Nothing applies until an Admin approves it (after the document itself). Approval counts the definition on the data
 * beside the certified measure of the same term; a difference is listed here, never resolved silently.
 */
export function DefinitionsPage() {
  const qc = useQueryClient();
  const ids = { filter: useId(), conflicts: useId() };
  const [status, setStatus] = useState<Filter>("pending");
  const [conflicts, setConflicts] = useState(false);
  const q = useQuery({
    queryKey: ["definitions", status, conflicts],
    queryFn: () => api<DefinitionList>(`/api/v1/knowledge/definitions?status=${status}${conflicts ? "&conflicts=true" : ""}`),
  });
  const [reviewing, setReviewing] = useState<KnowledgeDefinition | null>(null);
  const refresh = () => void qc.invalidateQueries({ queryKey: ["definitions"] });
  const check = useMutation({
    mutationFn: (d: KnowledgeDefinition) => api<KnowledgeDefinition>(`/api/v1/knowledge/definitions/${d.id}/check`, { method: "POST", body: {} }),
    onSuccess: (d) => { refresh(); toast.success(d.conflict ? `${d.term}: differs from ${d.measure?.label ?? "the measure"}` : `${d.term}: counted`); },
    onError: (e) => toast.error(asApiError(e, "The definition could not be counted.").message),
  });
  const retire = useMutation({
    mutationFn: (d: KnowledgeDefinition) => api<KnowledgeDefinition>(`/api/v1/knowledge/definitions/${d.id}/review`, { method: "POST", body: { decision: "retire" } }),
    onSuccess: (d) => { refresh(); toast.success(`${d.term}: retired`); },
    onError: (e) => toast.error(asApiError(e, "The definition could not be retired.").message),
  });

  const columns = useMemo<DataColumn<KnowledgeDefinition>[]>(() => [
    {
      id: "term", header: "Term", alwaysVisible: true, value: (d) => d.term,
      cell: (d) => (
        <span className="grid gap-0.5">
          <span className="font-medium">{d.term}</span>
          <span className="text-small text-ink-muted">{d.document.title} · {d.connection.name}</span>
        </span>
      ),
    },
    {
      id: "meaning", header: "Meaning", value: (d) => d.meaning,
      cell: (d) => <span className="grid gap-0.5"><span>{d.meaning}</span>{d.passage.heading && <span className="text-small text-ink-muted">Under “{d.passage.heading}”</span>}</span>,
    },
    { id: "rule", header: "Rule on the model", sortable: false, value: (d) => d.rule_text ?? d.gaps.join(" "), cell: (d) => <RuleCell d={d} /> },
    { id: "measure", header: "Certified measure", sortable: false, value: (d) => d.measure?.label ?? "", cell: (d) => <MeasureCell d={d} /> },
    { id: "status", header: "Status", value: (d) => d.status, cell: (d) => (
      <span className="grid gap-0.5"><StatusBadge d={d} />{d.reviewed_at && <span className="text-small text-ink-muted">{d.reviewed_by?.display_name} · {formatRelative(d.reviewed_at)}</span>}</span>
    ) },
    {
      id: "actions", header: "Actions", alwaysVisible: true, sortable: false, value: () => "",
      cell: (d) => (
        <span className="flex flex-wrap gap-x-3">
          {(d.status === "pending" || d.status === "rejected") && (
            <Button variant="link" size="sm" aria-label={`Review ${d.term}`} onClick={() => setReviewing(d)}><Check aria-hidden />Review</Button>
          )}
          {(d.status === "pending" || d.status === "approved") && (
            <Button variant="link" size="sm" aria-label={`Count ${d.term}`} disabled={check.isPending} onClick={() => check.mutate(d)}><Calculator aria-hidden />Count</Button>
          )}
          {d.status === "approved" && (
            <Button variant="link" size="sm" aria-label={`Retire ${d.term}`} disabled={retire.isPending} onClick={() => retire.mutate(d)}><Archive aria-hidden />Retire</Button>
          )}
        </span>
      ),
    },
  ], [check, retire]);

  const counts = q.data?.counts;
  return (
    <div className="tl grid gap-4">
      <div className="grid gap-1">
        <h1 className="text-title">Knowledge</h1>
        <p className="max-w-prose text-ink-muted">Business definitions read from your documents and glossaries. Approved ones tell Ask what a term means; when a definition counts differently from the certified measure of the same name, both figures are shown here for you to decide.</p>
      </div>
      <KnowledgeNav />
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div className="flex flex-wrap items-end gap-3">
          <div className="grid gap-1">
            <Label htmlFor={ids.filter}>Show</Label>
            <NativeSelect id={ids.filter} value={status} onChange={(e) => setStatus(e.target.value as Filter)}>
              <option value="pending">Waiting for review</option>
              <option value="approved">Approved</option>
              <option value="rejected">Rejected</option>
              <option value="all">All definitions</option>
            </NativeSelect>
          </div>
          <label htmlFor={ids.conflicts} className="flex items-center gap-2 pb-2 text-small">
            <input id={ids.conflicts} type="checkbox" checked={conflicts} onChange={(e) => setConflicts(e.target.checked)} />
            Only those that differ from a measure
          </label>
        </div>
        {counts && (
          <p className="text-small text-ink-muted" aria-label="Counts">
            {counts.pending} waiting · {counts.approved} approved · {counts.rejected} rejected · {counts.conflicts} differ from a measure · {counts.text_only} text only
          </p>
        )}
      </div>
      <DataTable
        data={q.data?.items ?? []}
        columns={columns}
        label="Business definitions"
        getRowId={(d) => d.id}
        rowLabel={(d) => d.term}
        loading={q.isLoading}
        error={q.error && <FormAlert error={asApiError(q.error)} />}
        empty={{ title: status === "pending" ? "Nothing to review" : "No definitions", body: "Upload a document or glossary that says what your terms mean (“An open project is a project whose current state is …”); its definitions show up here as it is indexed." }}
        search={(q.data?.items.length ?? 0) > 8}
      />
      <ReviewDialog item={reviewing} onClose={() => setReviewing(null)} onDone={refresh} />
    </div>
  );
}

function sameValues(a: string[], b: string[]) {
  return a.length === b.length && a.every((v, i) => v === b[i]);
}

/** Review: approve as read (or with the term, meaning or rule edited, or kept as text), or reject. */
function ReviewDialog({ item, onClose, onDone }: { item: KnowledgeDefinition | null; onClose: () => void; onDone: () => void }) {
  const ids = { form: useId(), term: useId(), meaning: useId(), field: useId(), op: useId(), values: useId(), textOnly: useId(), note: useId() };
  const [opened, setOpened] = useState<KnowledgeDefinition | null>(null);
  const [term, setTerm] = useState("");
  const [meaning, setMeaning] = useState("");
  const [field, setField] = useState("");
  const [op, setOp] = useState<DefinitionRule["op"]>("in");
  const [values, setValues] = useState("");
  const [textOnly, setTextOnly] = useState(false);
  const [note, setNote] = useState("");
  if (item !== opened) {
    setOpened(item);
    setTerm(item?.term ?? "");
    setMeaning(item?.meaning ?? "");
    setField(item?.rule?.field ?? "");
    setOp(item?.rule?.op ?? "in");
    setValues((item?.rule?.values ?? []).join(", "));
    setTextOnly(false);
    setNote("");
  }
  const cid = item?.connection.id ?? "";
  const catalog = useQuery({
    queryKey: ["catalog", cid], enabled: !!cid,
    queryFn: () => api<Catalog>(`/api/v1/query/catalog?connection_id=${encodeURIComponent(cid)}`),
  });
  const entity = item?.rule?.entity ?? catalog.data?.measures.find((m) => m.ref === item?.measure?.ref)?.entity;
  const fields = (catalog.data?.entities.find((e) => e.key === entity)?.fields ?? []).filter((f) => f.data_type === "text" || f.data_type === "boolean");
  const decide = useMutation({
    mutationFn: (body: Record<string, unknown>) => api<KnowledgeDefinition>(`/api/v1/knowledge/definitions/${item!.id}/review`, { method: "POST", body }),
    onSuccess: (d) => {
      onDone();
      if (d.status === "rejected") toast.success(`${d.term}: rejected`);
      else if (d.conflict) toast.warning(`${d.term} approved; it counts ${formatValue(d.checks?.figure)} where ${d.measure?.label ?? "the measure"} counts ${formatValue(d.checks?.measure?.figure)}`);
      else toast.success(d.expressible ? `${d.term} approved` : `${d.term} approved as text only`);
      onClose();
    },
  });
  const listed = values.split(",").map((v) => v.trim()).filter(Boolean);
  const approve = () => {
    const body: Record<string, unknown> = { decision: "approve" };
    if (note.trim()) body.note = note.trim();
    if (term.trim() && term.trim() !== item!.term) body.term = term.trim();
    if (meaning.trim() && meaning.trim() !== item!.meaning) body.meaning = meaning.trim();
    if (textOnly) body.text_only = true;
    else if (field && listed.length && (field !== item!.rule?.field || op !== item!.rule?.op || !sameValues(listed, item!.rule?.values ?? []))) {
      body.rule = { field, op, values: listed };
    }
    decide.mutate(body);
  };
  const reject = () => decide.mutate(note.trim() ? { decision: "reject", note: note.trim() } : { decision: "reject" });
  const docPending = item?.document.status !== "approved";
  return (
    <Dialog open={!!item} onOpenChange={(o) => { if (!o) { decide.reset(); onClose(); } }}>
      <DialogContent className="max-w-xl">
        <DialogHeader>
          <DialogTitle>Review a business definition</DialogTitle>
          <DialogDescription>Approved, Ask reads it whenever a question uses the term. It is counted on the data beside the certified measure of the same name.</DialogDescription>
        </DialogHeader>
        <form id={ids.form} className="grid gap-3" onSubmit={(e) => { e.preventDefault(); approve(); }}>
          {item && (
            <div className="grid gap-1 text-small">
              <span className="text-ink-muted">From “{item.document.title}”{item.passage.heading ? `, under “${item.passage.heading}”` : ""}:</span>
              <blockquote className="border-l-2 border-line pl-3">{item.passage.text}</blockquote>
              {item.gaps.map((g) => <span key={g} className="text-status-warn">{g}</span>)}
            </div>
          )}
          {item && docPending && <p className="text-small text-status-warn">Approve the document “{item.document.title}” first.</p>}
          <div className="grid gap-1.5">
            <Label htmlFor={ids.term}>Term</Label>
            <Input id={ids.term} value={term} maxLength={80} required onChange={(e) => setTerm(e.target.value)} />
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor={ids.meaning}>Meaning</Label>
            <Textarea id={ids.meaning} value={meaning} maxLength={600} required onChange={(e) => setMeaning(e.target.value)} />
          </div>
          <label htmlFor={ids.textOnly} className="flex items-center gap-2 text-small">
            <input id={ids.textOnly} type="checkbox" checked={textOnly} onChange={(e) => setTextOnly(e.target.checked)} />
            Keep as text only (no rule)
          </label>
          {!textOnly && entity && (
            <fieldset className="grid gap-2">
              <legend className="text-small font-medium">Rule on {catalog.data?.entities.find((e) => e.key === entity)?.label ?? entity}</legend>
              <div className="flex flex-wrap gap-2">
                <div className="grid gap-1">
                  <Label htmlFor={ids.field}>Field</Label>
                  <NativeSelect id={ids.field} value={field} onChange={(e) => setField(e.target.value)}>
                    <option value="">Pick a field…</option>
                    {fields.map((f) => <option key={f.key} value={f.key}>{f.label}</option>)}
                  </NativeSelect>
                </div>
                <div className="grid gap-1">
                  <Label htmlFor={ids.op}>Is</Label>
                  <NativeSelect id={ids.op} value={op} onChange={(e) => setOp(e.target.value as DefinitionRule["op"])}>
                    <option value="in">one of</option>
                    <option value="not_in">not one of</option>
                  </NativeSelect>
                </div>
              </div>
              <div className="grid gap-1">
                <Label htmlFor={ids.values}>Values <span className="text-ink-muted">(comma-separated, as stored)</span></Label>
                <Input id={ids.values} value={values} onChange={(e) => setValues(e.target.value)} />
              </div>
            </fieldset>
          )}
          <div className="grid gap-1.5">
            <Label htmlFor={ids.note}>Note <span className="text-ink-muted">(optional)</span></Label>
            <Textarea id={ids.note} value={note} maxLength={500} onChange={(e) => setNote(e.target.value)} />
          </div>
          {decide.error && <FormAlert error={asApiError(decide.error, "The definition could not be saved.")} />}
        </form>
        <DialogFooter>
          <Button variant="secondary" onClick={onClose}>Cancel</Button>
          {item?.status === "pending" && <Button variant="secondary" onClick={reject} disabled={decide.isPending}>Reject</Button>}
          <Button type="submit" form={ids.form} disabled={decide.isPending || docPending || !term.trim() || meaning.trim().length < 3}>Approve</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
