import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, X } from "lucide-react";
import { useId, useMemo, useState } from "react";
import { toast } from "sonner";

import { api, ApiError } from "../../../api/client";
import type { FeedbackItem, FeedbackQueue, GoldExample } from "../../../api/knowledge";
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

type Filter = "open" | "fixed" | "dismissed" | "all";
const REASON: Record<string, string> = { wrong: "Looks wrong", not_what_i_asked: "Not what I asked" };
/** Parts of an answer's spec a gold example doesn't keep: the connection and model are set by the server. */
const PINNED = new Set(["model_version_id", "connection_id", "spec_version"]);

function StatusBadge({ f }: { f: FeedbackItem }) {
  if (f.status === "fixed") return <Badge variant="certified">Fixed</Badge>;
  if (f.status === "dismissed") return <Badge variant="outline">Dismissed</Badge>;
  return <Badge variant="stale">Open</Badge>;
}

/**
 * Admin → Knowledge → Flagged answers (US-183; FEAT-064 / FEAT-018): answers people marked "Not helpful" or "This isn't
 * what I asked", with the question and how Ask read it (never the rows). Fixing one saves the corrected spec as a gold
 * example (the same endpoint as Gold examples), so the suite tests it and the planner learns from it.
 */
export function AnswerFeedbackPage() {
  const qc = useQueryClient();
  const filterId = useId();
  const [status, setStatus] = useState<Filter>("open");
  const q = useQuery({ queryKey: ["answer-feedback", status], queryFn: () => api<FeedbackQueue>(`/api/v1/ask/feedback?status=${status}`) });
  const [fixing, setFixing] = useState<FeedbackItem | null>(null);
  const [dismissing, setDismissing] = useState<FeedbackItem | null>(null);
  const refresh = () => void qc.invalidateQueries({ queryKey: ["answer-feedback"] });

  const columns = useMemo<DataColumn<FeedbackItem>[]>(() => [
    {
      id: "question", header: "Question", alwaysVisible: true, value: (f) => f.question,
      cell: (f) => (
        <span className="grid gap-0.5">
          <span className="font-medium">{f.question}</span>
          {f.comment && <span className="text-small text-ink-muted">“{f.comment}”</span>}
        </span>
      ),
    },
    { id: "reason", header: "Why", filter: "select", value: (f) => REASON[f.reason ?? ""] ?? "Not helpful" },
    {
      id: "read", header: "How Ask read it", sortable: false, value: (f) => String((f.spec?.measure as { ref?: string } | undefined)?.ref ?? ""),
      cell: (f) => <span className="font-mono text-small">{String((f.spec?.measure as { ref?: string } | undefined)?.ref ?? "—").replace(/^measure:/, "")}</span>,
    },
    { id: "by", header: "From", value: (f) => f.user.display_name, cell: (f) => <span className="grid gap-0.5">{f.user.display_name}<span className="text-small text-ink-muted">{formatRelative(f.created_at)}</span></span> },
    { id: "status", header: "Status", value: (f) => f.status, cell: (f) => (
      <span className="grid gap-0.5"><StatusBadge f={f} />{f.resolution_note && <span className="text-small text-ink-muted">{f.resolution_note}</span>}</span>
    ) },
    {
      id: "actions", header: "Actions", alwaysVisible: true, sortable: false, value: () => "",
      cell: (f) => f.status === "open" ? (
        <span className="flex flex-wrap gap-x-3">
          <Button variant="link" size="sm" aria-label={`Fix ${f.question}`} disabled={!f.spec || !f.connection} onClick={() => setFixing(f)}><Check aria-hidden />Fix</Button>
          <Button variant="link" size="sm" aria-label={`Dismiss ${f.question}`} onClick={() => setDismissing(f)}><X aria-hidden />Dismiss</Button>
        </span>
      ) : null,
    },
  ], []);

  const counts = q.data?.counts;
  return (
    <div className="tl grid gap-4">
      <div className="grid gap-1">
        <h1 className="text-title">Knowledge</h1>
        <p className="max-w-prose text-ink-muted">Answers people flagged as wrong or not what they asked. Fixing one saves the right plan as a gold example, so the accuracy suite checks it and Ask learns from it.</p>
      </div>
      <KnowledgeNav />
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div className="grid gap-1">
          <Label htmlFor={filterId}>Show</Label>
          <NativeSelect id={filterId} value={status} onChange={(e) => setStatus(e.target.value as Filter)}>
            <option value="open">Open</option>
            <option value="fixed">Fixed</option>
            <option value="dismissed">Dismissed</option>
            <option value="all">All flagged answers</option>
          </NativeSelect>
        </div>
        {counts && <p className="text-small text-ink-muted">{counts.open} open · {counts.fixed} fixed · {counts.dismissed} dismissed · {counts.up} marked helpful</p>}
      </div>
      <DataTable
        data={q.data?.items ?? []}
        columns={columns}
        label="Flagged answers"
        getRowId={(f) => f.id}
        rowLabel={(f) => f.question}
        loading={q.isLoading}
        error={q.error && <FormAlert error={asApiError(q.error)} />}
        empty={{ title: status === "open" ? "Nothing to review" : "No flagged answers", body: "Answers people mark “Not helpful” or “This isn't what I asked” in Ask show up here." }}
        search={(q.data?.items.length ?? 0) > 8}
      />
      <FixDialog item={fixing} onClose={() => setFixing(null)} onDone={refresh} />
      <DismissDialog item={dismissing} onClose={() => setDismissing(null)} onDone={refresh} />
    </div>
  );
}

function specText(spec: Record<string, unknown> | null): string {
  return JSON.stringify(Object.fromEntries(Object.entries(spec ?? {}).filter(([k]) => !PINNED.has(k))), null, 2);
}

/** Fix: the corrected question → spec saved as a gold example from the flagged answer, then the feedback marked fixed. */
export function FixDialog({ item, onClose, onDone }: { item: FeedbackItem | null; onClose: () => void; onDone: () => void }) {
  const ids = { question: useId(), spec: useId(), error: useId() };
  const [opened, setOpened] = useState<FeedbackItem | null>(null);
  const [question, setQuestion] = useState("");
  const [spec, setSpec] = useState("");
  const [specError, setSpecError] = useState<string | null>(null);
  if (item !== opened) {
    setOpened(item);
    setQuestion(item?.question.replace(/^Undo: /, "") ?? "");
    setSpec(specText(item?.spec ?? null));
    setSpecError(null);
  }
  const save = useMutation({
    mutationFn: async (expected: Record<string, unknown>) => {
      let goldId: string;
      let linked = false;
      try {
        const g = await api<GoldExample>("/api/v1/knowledge/gold-examples", {
          method: "POST",
          body: { question, expected_spec: expected, connection_id: item!.connection!.id, turn_id: item!.turn_id, tags: ["from_feedback"], ...(item!.comment ? { notes: item!.comment } : {}) },
        });
        goldId = g.id;
      } catch (e) {
        const existing = e instanceof ApiError && e.status === 409 ? e.details.gold_example_id : undefined;
        if (typeof existing !== "string") throw e;
        goldId = existing;
        linked = true;
      }
      await api(`/api/v1/ask/feedback/${item!.id}`, { method: "PATCH", body: { status: "fixed", gold_example_id: goldId } });
      return linked;
    },
    onSuccess: (linked) => {
      onDone();
      toast.success(linked ? "Marked fixed and linked to the existing gold example for this question" : "Saved as a gold example and marked fixed");
      onClose();
    },
  });
  const submit = () => {
    try {
      const parsed = JSON.parse(spec) as Record<string, unknown>;
      setSpecError(null);
      save.mutate(parsed);
    } catch {
      setSpecError("The spec is not valid JSON.");
    }
  };
  return (
    <Dialog open={!!item} onOpenChange={(o) => { if (!o) { save.reset(); onClose(); } }}>
      <DialogContent className="max-w-xl">
        <DialogHeader>
          <DialogTitle>Fix this answer</DialogTitle>
          <DialogDescription>Correct how the question should be read. It is saved as an approved gold example, checked against the approved model.</DialogDescription>
        </DialogHeader>
        <form id={`${ids.question}-form`} className="grid gap-3" onSubmit={(e) => { e.preventDefault(); submit(); }}>
          {item?.comment && <p className="text-small text-ink-muted">{item.user.display_name} said: “{item.comment}”</p>}
          <div className="grid gap-1.5">
            <Label htmlFor={ids.question}>Question</Label>
            <Input id={ids.question} value={question} minLength={3} maxLength={500} required onChange={(e) => setQuestion(e.target.value)} />
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor={ids.spec}>Correct Query Spec (JSON)</Label>
            <Textarea id={ids.spec} className="min-h-40 font-mono text-small" value={spec} onChange={(e) => setSpec(e.target.value)} aria-invalid={!!specError} aria-describedby={specError ? ids.error : undefined} />
            {specError && <p id={ids.error} className="text-small text-status-danger">{specError}</p>}
          </div>
          {save.error && <FormAlert error={asApiError(save.error, "The fix could not be saved.")} />}
        </form>
        <DialogFooter>
          <Button variant="secondary" onClick={onClose}>Cancel</Button>
          <Button type="submit" form={`${ids.question}-form`} disabled={save.isPending || question.trim().length < 3}>Save as gold example</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function DismissDialog({ item, onClose, onDone }: { item: FeedbackItem | null; onClose: () => void; onDone: () => void }) {
  const noteId = useId();
  const [note, setNote] = useState("");
  const dismiss = useMutation({
    mutationFn: () => api(`/api/v1/ask/feedback/${item!.id}`, { method: "PATCH", body: { status: "dismissed", ...(note.trim() ? { note: note.trim() } : {}) } }),
    onSuccess: () => { onDone(); toast.success("Dismissed"); setNote(""); onClose(); },
  });
  return (
    <Dialog open={!!item} onOpenChange={(o) => { if (!o) { dismiss.reset(); onClose(); } }}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Dismiss this flag</DialogTitle>
          <DialogDescription>Use this when the answer was right. Nothing is added to the gold examples.</DialogDescription>
        </DialogHeader>
        <div className="grid gap-1.5">
          <Label htmlFor={noteId}>Note <span className="text-ink-muted">(optional)</span></Label>
          <Textarea id={noteId} value={note} maxLength={500} onChange={(e) => setNote(e.target.value)} />
        </div>
        {dismiss.error && <FormAlert error={asApiError(dismiss.error, "The flag could not be dismissed.")} />}
        <DialogFooter>
          <Button variant="secondary" onClick={onClose}>Cancel</Button>
          <Button onClick={() => dismiss.mutate()} disabled={dismiss.isPending}>Dismiss</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
