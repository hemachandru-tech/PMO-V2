import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, RefreshCw, X } from "lucide-react";
import { useEffect, useId, useMemo, useRef, useState } from "react";
import { toast } from "sonner";

import { api } from "../../../api/client";
import type { Catalog } from "../../../api/govern";
import type { CurationCollected, CurationKind, CurationProposal, CurationQueue, CurationSource } from "../../../api/knowledge";
import { formatRelative } from "../../../components/format";
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

type Filter = "pending" | "approved" | "dismissed" | "all";
export const KIND: Record<CurationKind, string> = { measure_synonym: "Measure synonym", glossary_term: "Glossary term", gold_example: "Gold example" };
export const SOURCE: Record<CurationSource, string> = { refused: "Refused", clarification: "Clarification chosen", repair: "Plan repaired", feedback: "Marked wrong", direct_sql: "Direct SQL answer" };
/** Parts of a spec a gold example doesn't keep: the connection and model are set by the server. */
const PINNED = new Set(["model_version_id", "connection_id", "spec_version"]);

function StatusBadge({ p }: { p: CurationProposal }) {
  if (p.status === "approved") return <Badge variant="certified">Approved</Badge>;
  if (p.status === "dismissed") return <Badge variant="outline">Dismissed</Badge>;
  return <Badge variant="stale">Waiting for review</Badge>;
}

/** What the proposal would add, in words. */
function Proposed({ p }: { p: CurationProposal }) {
  if (p.kind === "measure_synonym") {
    return <span>“{p.phrase}” → {p.measure ? p.measure.label ?? p.measure.ref : <span className="text-ink-muted">pick a measure</span>}</span>;
  }
  if (p.kind === "glossary_term") return <span>{p.phrase} means: {p.meaning ?? <span className="text-ink-muted">to write</span>}</span>;
  const ref = String((p.spec?.measure as { ref?: string } | undefined)?.ref ?? "").replace(/^measure:/, "");
  return <span className="grid gap-0.5"><span className="font-mono text-small">{ref || "—"}</span>{p.needs_fix && <span className="text-small text-status-danger">Marked wrong: correct the spec</span>}</span>;
}

/**
 * Admin → Knowledge → From misses (US-198; FEAT-066 / FEAT-014): questions Ask refused, clarifications people chose,
 * plans AG-03 repaired and answers marked wrong, turned into proposals — a measure synonym, a glossary term or a gold
 * example — with the questions behind each. Nothing applies until an Admin approves it: a synonym is approved as the
 * model's next version, a term as approved glossary knowledge, a gold example through the gold set. Held-out
 * evaluation questions are never proposed.
 */
export function CurationPage() {
  const qc = useQueryClient();
  const filterId = useId();
  const [status, setStatus] = useState<Filter>("pending");
  const q = useQuery({ queryKey: ["curation", status], queryFn: () => api<CurationQueue>(`/api/v1/knowledge/curation?status=${status}`) });
  const [approving, setApproving] = useState<CurationProposal | null>(null);
  const [dismissing, setDismissing] = useState<CurationProposal | null>(null);
  const refresh = () => void qc.invalidateQueries({ queryKey: ["curation"] });

  // The variable: an Admin pressed "Read new misses" (the read on opening the page is silent).
  const collect = useMutation<CurationCollected, Error, boolean>({
    mutationFn: () => api<CurationCollected>("/api/v1/knowledge/curation/collect", { method: "POST", body: {} }),
    onSuccess: (out, manual) => {
      refresh();
      if (manual) toast.success(out.added ? `${out.added} new ${out.added === 1 ? "proposal" : "proposals"}` : "No new proposals");
    },
    onError: (e) => toast.error(asApiError(e, "New misses could not be read.").message),
  });
  // Opening the queue reads the misses since the last visit (each is read once).
  const started = useRef(false);
  useEffect(() => {
    if (!started.current) { started.current = true; collect.mutate(false); }
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const columns = useMemo<DataColumn<CurationProposal>[]>(() => [
    {
      id: "question", header: "Question behind it", alwaysVisible: true, value: (p) => p.questions.map((x) => x.question).join(" "),
      cell: (p) => (
        <span className="grid gap-0.5">
          <span className="font-medium">{p.questions[0]?.question ?? p.question}</span>
          {p.questions[0]?.detail && <span className="text-small text-ink-muted">{p.questions[0].detail}</span>}
          {p.seen > 1 && <span className="text-small text-ink-muted">and {p.seen - 1} more {p.seen === 2 ? "question" : "questions"}</span>}
        </span>
      ),
    },
    { id: "kind", header: "Proposes", filter: "select", value: (p) => KIND[p.kind] },
    { id: "what", header: "What it adds", sortable: false, value: (p) => `${p.phrase ?? ""} ${p.measure?.label ?? ""}`, cell: (p) => <Proposed p={p} /> },
    {
      id: "from", header: "From", filter: "select", value: (p) => Object.keys(p.by_source).map((s) => SOURCE[s as CurationSource]).join(", "),
      cell: (p) => <span className="grid gap-0.5">{Object.entries(p.by_source).map(([s, n]) => <span key={s}>{SOURCE[s as CurationSource]}{n > 1 ? ` × ${n}` : ""}</span>)}</span>,
    },
    { id: "seen", header: "Times", value: (p) => p.seen, cell: (p) => <span className="tabular-nums">{p.seen}</span> },
    { id: "status", header: "Status", value: (p) => p.status, cell: (p) => (
      <span className="grid gap-0.5"><StatusBadge p={p} />{p.reviewed_at && <span className="text-small text-ink-muted">{p.reviewed_by?.display_name} · {formatRelative(p.reviewed_at)}</span>}</span>
    ) },
    {
      id: "actions", header: "Actions", alwaysVisible: true, sortable: false, value: () => "",
      cell: (p) => p.status === "pending" ? (
        <span className="flex flex-wrap gap-x-3">
          <Button variant="link" size="sm" aria-label={`Review ${p.question}`} onClick={() => setApproving(p)}><Check aria-hidden />Approve</Button>
          <Button variant="link" size="sm" aria-label={`Dismiss ${p.question}`} onClick={() => setDismissing(p)}><X aria-hidden />Dismiss</Button>
        </span>
      ) : null,
    },
  ], []);

  const counts = q.data?.counts;
  return (
    <div className="tl grid gap-4">
      <div className="grid gap-1">
        <h1 className="text-title">Knowledge</h1>
        <p className="max-w-prose text-ink-muted">Questions Ask refused, asked about, repaired or answered wrongly, turned into proposals. Nothing changes until you approve one: a synonym becomes part of the model, a term part of the glossary, a question a gold example.</p>
      </div>
      <KnowledgeNav />
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div className="flex flex-wrap items-end gap-3">
          <div className="grid gap-1">
            <Label htmlFor={filterId}>Show</Label>
            <NativeSelect id={filterId} value={status} onChange={(e) => setStatus(e.target.value as Filter)}>
              <option value="pending">Waiting for review</option>
              <option value="approved">Approved</option>
              <option value="dismissed">Dismissed</option>
              <option value="all">All proposals</option>
            </NativeSelect>
          </div>
          <Button variant="secondary" onClick={() => collect.mutate(true)} disabled={collect.isPending}><RefreshCw aria-hidden />Read new misses</Button>
        </div>
        {counts && (
          <p className="text-small text-ink-muted" aria-label="Counts">
            {counts.pending} waiting ({counts.pending_by_kind.measure_synonym} synonyms · {counts.pending_by_kind.glossary_term} terms · {counts.pending_by_kind.gold_example} gold examples) · {counts.approved} approved · {counts.dismissed} dismissed
          </p>
        )}
      </div>
      {counts && counts.pending > 0 && (
        <p className="text-small text-ink-muted">Behind the waiting proposals: {(Object.keys(SOURCE) as CurationSource[]).map((s) => `${counts.pending_by_source[s]} ${SOURCE[s].toLowerCase()}`).join(" · ")}</p>
      )}
      <DataTable
        data={q.data?.items ?? []}
        columns={columns}
        label="Proposals from Ask's misses"
        getRowId={(p) => p.id}
        rowLabel={(p) => p.question}
        loading={q.isLoading}
        error={q.error && <FormAlert error={asApiError(q.error)} />}
        empty={{ title: status === "pending" ? "Nothing to review" : "No proposals", body: "Refused questions, clarification choices, repaired plans and answers marked wrong in Ask show up here as proposals." }}
        search={(q.data?.items.length ?? 0) > 8}
      />
      <ApproveDialog item={approving} onClose={() => setApproving(null)} onDone={refresh} />
      <DismissDialog item={dismissing} onClose={() => setDismissing(null)} onDone={refresh} />
    </div>
  );
}

function specText(spec: Record<string, unknown> | null): string {
  return JSON.stringify(Object.fromEntries(Object.entries(spec ?? {}).filter(([k]) => !PINNED.has(k))), null, 2);
}

/** Approve: edit what the proposal adds (the words, the measure, the meaning or the spec), then apply it through its gate. */
function ApproveDialog({ item, onClose, onDone }: { item: CurationProposal | null; onClose: () => void; onDone: () => void }) {
  const ids = { form: useId(), phrase: useId(), measure: useId(), meaning: useId(), question: useId(), spec: useId(), specError: useId(), note: useId() };
  const [opened, setOpened] = useState<CurationProposal | null>(null);
  const [phrase, setPhrase] = useState("");
  const [measure, setMeasure] = useState("");
  const [meaning, setMeaning] = useState("");
  const [question, setQuestion] = useState("");
  const [spec, setSpec] = useState("");
  const [specError, setSpecError] = useState<string | null>(null);
  const [note, setNote] = useState("");
  if (item !== opened) {
    setOpened(item);
    setPhrase(item?.phrase ?? "");
    setMeasure(item?.measure?.ref ?? "");
    setMeaning(item?.meaning ?? "");
    setQuestion(item?.question ?? "");
    setSpec(specText(item?.spec ?? null));
    setSpecError(null);
    setNote("");
  }
  const cid = item?.connection.id ?? "";
  const catalog = useQuery({
    queryKey: ["catalog", cid], enabled: item?.kind === "measure_synonym" && !!cid,
    queryFn: () => api<Catalog>(`/api/v1/query/catalog?connection_id=${encodeURIComponent(cid)}`),
  });
  const save = useMutation({
    mutationFn: (body: Record<string, unknown>) => api<CurationProposal>(`/api/v1/knowledge/curation/${item!.id}/approve`, { method: "POST", body }),
    onSuccess: (p) => {
      onDone();
      toast.success(p.kind === "measure_synonym" ? `Approved: the model is now version ${String(p.applied?.version ?? "")}` : p.kind === "glossary_term" ? "Added to the glossary" : "Saved as a gold example");
      onClose();
    },
  });
  const submit = () => {
    const body: Record<string, unknown> = note.trim() ? { note: note.trim() } : {};
    if (item!.kind === "measure_synonym") Object.assign(body, { phrase, measure });
    if (item!.kind === "glossary_term") Object.assign(body, { phrase, meaning });
    if (item!.kind === "gold_example") {
      try {
        Object.assign(body, { question, expected_spec: JSON.parse(spec) as Record<string, unknown> });
      } catch {
        setSpecError("The spec is not valid JSON.");
        return;
      }
    }
    setSpecError(null);
    save.mutate(body);
  };
  const options = item?.options ?? [];
  const others = (catalog.data?.measures ?? []).filter((m) => !options.some((o) => o.ref === m.ref));
  const title = item ? { measure_synonym: "Approve a measure synonym", glossary_term: "Approve a glossary term", gold_example: "Approve a gold example" }[item.kind] : "";
  const ready = item?.kind === "measure_synonym" ? phrase.trim().length >= 2 && !!measure : item?.kind === "glossary_term" ? !!phrase.trim() && !!meaning.trim() : question.trim().length >= 3;
  return (
    <Dialog open={!!item} onOpenChange={(o) => { if (!o) { save.reset(); onClose(); } }}>
      <DialogContent className="max-w-xl">
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>
            {item?.kind === "measure_synonym" && "The words are added to the measure and the model is approved as its next version, so Ask reads them by word and by meaning."}
            {item?.kind === "glossary_term" && "The term is added to this connection's approved glossary, which Ask reads when it rewrites and plans a question."}
            {item?.kind === "gold_example" && "The question is saved as an approved gold example, checked against the approved model; flagged answers behind it are marked fixed."}
          </DialogDescription>
        </DialogHeader>
        <form id={ids.form} className="grid gap-3" onSubmit={(e) => { e.preventDefault(); submit(); }}>
          {item && (
            <div className="grid gap-1 text-small">
              <span className="text-ink-muted">Asked in {item.connection.name}:</span>
              <ul className="grid gap-0.5">
                {item.questions.map((x, i) => <li key={i}>“{x.question}” <span className="text-ink-muted">· {SOURCE[x.source]}{x.detail ? ` · ${x.detail}` : ""}</span></li>)}
              </ul>
            </div>
          )}
          {item?.kind !== "gold_example" && (
            <div className="grid gap-1.5">
              <Label htmlFor={ids.phrase}>{item?.kind === "glossary_term" ? "Term" : "Words people use"}</Label>
              <Input id={ids.phrase} value={phrase} maxLength={60} required onChange={(e) => setPhrase(e.target.value)} />
            </div>
          )}
          {item?.kind === "measure_synonym" && (
            <div className="grid gap-1.5">
              <Label htmlFor={ids.measure}>Measure they mean</Label>
              <NativeSelect id={ids.measure} value={measure} required onChange={(e) => setMeasure(e.target.value)}>
                <option value="">Pick a measure…</option>
                {options.length > 0 && <optgroup label="Considered by Ask">{options.map((o) => <option key={o.ref} value={o.ref}>{o.label}</option>)}</optgroup>}
                {others.length > 0 && <optgroup label="All certified measures">{others.map((m) => <option key={m.ref} value={m.ref}>{m.label}</option>)}</optgroup>}
              </NativeSelect>
            </div>
          )}
          {item?.kind === "glossary_term" && (
            <div className="grid gap-1.5">
              <Label htmlFor={ids.meaning}>What it means</Label>
              <Input id={ids.meaning} value={meaning} maxLength={200} required placeholder="e.g. request for quotation" onChange={(e) => setMeaning(e.target.value)} />
            </div>
          )}
          {item?.kind === "gold_example" && (
            <>
              <div className="grid gap-1.5">
                <Label htmlFor={ids.question}>Question</Label>
                <Input id={ids.question} value={question} minLength={3} maxLength={500} required onChange={(e) => setQuestion(e.target.value)} />
              </div>
              <div className="grid gap-1.5">
                <Label htmlFor={ids.spec}>{item.needs_fix ? "Correct Query Spec (JSON) — someone marked this answer wrong" : "Query Spec (JSON)"}</Label>
                <Textarea id={ids.spec} className="min-h-40 font-mono text-small" value={spec} onChange={(e) => setSpec(e.target.value)} aria-invalid={!!specError} aria-describedby={specError ? ids.specError : undefined} />
                {specError && <p id={ids.specError} className="text-small text-status-danger">{specError}</p>}
              </div>
            </>
          )}
          <div className="grid gap-1.5">
            <Label htmlFor={ids.note}>Note <span className="text-ink-muted">(optional)</span></Label>
            <Textarea id={ids.note} value={note} maxLength={500} onChange={(e) => setNote(e.target.value)} />
          </div>
          {save.error && <FormAlert error={asApiError(save.error, "The proposal could not be approved.")} />}
        </form>
        <DialogFooter>
          <Button variant="secondary" onClick={onClose}>Cancel</Button>
          <Button type="submit" form={ids.form} disabled={save.isPending || !ready}>Approve</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function DismissDialog({ item, onClose, onDone }: { item: CurationProposal | null; onClose: () => void; onDone: () => void }) {
  const noteId = useId();
  const [note, setNote] = useState("");
  const dismiss = useMutation({
    mutationFn: () => api(`/api/v1/knowledge/curation/${item!.id}/dismiss`, { method: "POST", body: note.trim() ? { note: note.trim() } : {} }),
    onSuccess: () => { onDone(); toast.success("Dismissed"); setNote(""); onClose(); },
  });
  return (
    <Dialog open={!!item} onOpenChange={(o) => { if (!o) { dismiss.reset(); onClose(); } }}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Dismiss this proposal</DialogTitle>
          <DialogDescription>Nothing is added, and the same wording is not proposed again.</DialogDescription>
        </DialogHeader>
        <div className="grid gap-1.5">
          <Label htmlFor={noteId}>Note <span className="text-ink-muted">(optional)</span></Label>
          <Textarea id={noteId} value={note} maxLength={500} onChange={(e) => setNote(e.target.value)} />
        </div>
        {dismiss.error && <FormAlert error={asApiError(dismiss.error, "The proposal could not be dismissed.")} />}
        <DialogFooter>
          <Button variant="secondary" onClick={onClose}>Cancel</Button>
          <Button onClick={() => dismiss.mutate()} disabled={dismiss.isPending}>Dismiss</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
