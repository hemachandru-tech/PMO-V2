import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, Download, RefreshCw, Search, Trash2, Upload } from "lucide-react";
import { useMemo, useRef, useState } from "react";
import { toast } from "sonner";

import { ApiError, api, getToken } from "../../../api/client";
import { getConfig } from "../../../app/config";
import {
  KIND_LABEL, REVIEW_LABEL,
  type KnowledgeDocument, type KnowledgeDocumentDetail, type SearchResult,
} from "../../../api/knowledge";
import { formatRelative } from "../../../components/format";
import { Alert, FormAlert } from "../../../components/ui/alert";
import { Badge } from "../../../components/ui/badge";
import { Button } from "../../../components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../../../components/ui/card";
import { Checkbox } from "../../../components/ui/checkbox";
import { ConfirmDialog, type ConfirmRequest } from "../../../components/ui/confirm-dialog";
import { DataTable, type DataColumn } from "../../../components/ui/data-table";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "../../../components/ui/dialog";
import { Input } from "../../../components/ui/input";
import { Label } from "../../../components/ui/label";
import { NativeSelect } from "../../../components/ui/native-select";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "../../../components/ui/sheet";
import { Textarea } from "../../../components/ui/textarea";
import { asApiError } from "../../../lib/api-errors";
import { KnowledgeNav } from "./KnowledgeNav";
import { useAppName } from "../../../app/branding";

const KEY = ["knowledge-documents"];
const ACCEPT = ".md,.markdown,.txt,.pdf,.docx,.xlsx";

function ReviewBadge({ doc }: { doc: KnowledgeDocument }) {
  const variant = doc.review_status === "approved" ? "certified" : doc.review_status === "pending_review" ? "stale" : "outline";
  return <Badge variant={variant}>{REVIEW_LABEL[doc.review_status]}</Badge>;
}

function IndexCell({ doc }: { doc: KnowledgeDocument }) {
  if (doc.index_status === "failed") return <span className="text-small text-status-danger">{doc.index_error ?? "Indexing failed"}</span>;
  if (doc.index_status !== "indexed") return <span className="text-small text-ink-muted">Indexing…</span>;
  const keywordOnly = !doc.embedding_model && doc.chunk_count > 0;
  return (
    <span className="grid text-small">
      <span>{doc.chunk_count} {doc.chunk_count === 1 ? "passage" : "passages"}</span>
      {keywordOnly && <span className="text-ink-muted">Keyword search only until embeddings are back</span>}
    </span>
  );
}

function FlagsCell({ doc }: { doc: KnowledgeDocument }) {
  const f = doc.scan_flags ?? {};
  const redacted = Object.entries(f.redacted ?? {}).map(([k, n]) => `${n} ${k}`).join(", ");
  return (
    <span className="flex flex-wrap gap-1">
      {doc.injection_flagged && <Badge variant="stale" title="Some passages read like instructions to the assistant"><AlertTriangle aria-hidden />Instructions found</Badge>}
      {(f.dropped ?? 0) > 0 && <Badge variant="outline" title={`Removed: ${(f.leak_classes ?? []).join(", ")}`}>{f.dropped} removed</Badge>}
      {redacted && <Badge variant="outline">Redacted {redacted}</Badge>}
    </span>
  );
}

/**
 * Admin → Knowledge (US-102; SCR-13; FEAT-014): reference documents the assistant may ground answers in. Uploads are
 * indexed straight away but stay out of every answer until an Admin approves them; a document whose text reads like
 * instructions to the assistant needs an explicit acknowledgement. The approved model's definitions are listed too
 * (indexed and approved with the model). "Test retrieval" shows what an agent would be given for a question.
 */
export function KnowledgePage() {
  const appName = useAppName();
  const qc = useQueryClient();
  const q = useQuery({
    queryKey: KEY,
    queryFn: () => api<{ items: KnowledgeDocument[] }>("/api/v1/knowledge/documents"),
    refetchInterval: (query) => (query.state.data?.items.some((d) => d.index_status === "queued" || d.index_status === "indexing") ? 3000 : false),
  });
  const [uploading, setUploading] = useState(false);
  const [reviewing, setReviewing] = useState<string | null>(null);
  const [confirm, setConfirm] = useState<ConfirmRequest | null>(null);
  const refresh = () => void qc.invalidateQueries({ queryKey: KEY });
  const reindex = useMutation({
    mutationFn: (d: KnowledgeDocument) => api(`/api/v1/knowledge/documents/${d.id}/reindex`, { method: "POST" }),
    onSuccess: () => { refresh(); toast.success("Re-indexing started"); },
    onError: (e) => toast.error(asApiError(e, "Re-indexing could not start.").message),
  });
  const remove = useMutation({
    mutationFn: (d: KnowledgeDocument) => api(`/api/v1/knowledge/documents/${d.id}`, { method: "DELETE" }),
    onSuccess: () => { refresh(); setConfirm(null); toast.success("Document deleted"); },
    onError: (e) => toast.error(asApiError(e, "The document could not be deleted.").message),
  });
  const items = q.data?.items ?? [];
  const pending = items.filter((d) => d.review_status === "pending_review" && d.index_status === "indexed").length;

  const columns = useMemo<DataColumn<KnowledgeDocument>[]>(() => [
    {
      id: "title", header: "Document", alwaysVisible: true, value: (d) => `${d.title} ${d.original_filename ?? ""}`,
      cell: (d) => (
        <span className="grid gap-0.5">
          <span className="font-medium">{d.title}</span>
          <span className="text-small text-ink-muted">
            {KIND_LABEL[d.source_kind] ?? d.source_kind}
            {d.source_kind === "model_definition" && d.model_version_no ? ` · v${d.model_version_no}` : ""}
            {d.connection_name ? ` · ${d.connection_name}` : ""}
            {d.original_filename ? ` · ${d.original_filename}` : ""}
          </span>
        </span>
      ),
    },
    { id: "status", header: "Review", filter: "select", value: (d) => REVIEW_LABEL[d.review_status], cell: (d) => <ReviewBadge doc={d} /> },
    { id: "index", header: "Index", value: (d) => d.index_status, cell: (d) => <IndexCell doc={d} /> },
    { id: "flags", header: "Checks", sortable: false, value: (d) => (d.injection_flagged ? "instructions" : ""), cell: (d) => <FlagsCell doc={d} /> },
    { id: "by", header: "Added by", hidden: true, value: (d) => d.created_by_name ?? "", cell: (d) => d.created_by_name ?? appName },
    { id: "updated", header: "Changed", date: true, value: (d) => d.updated_at, cell: (d) => formatRelative(d.updated_at) },
    {
      id: "actions", header: "Actions", alwaysVisible: true, sortable: false, value: () => "",
      cell: (d) => d.source_kind === "model_definition" ? <span className="text-small text-ink-muted">Follows the model</span> : (
        <span className="flex flex-wrap gap-x-3">
          <Button variant="link" size="sm" onClick={() => setReviewing(d.id)}>{d.review_status === "pending_review" ? "Review" : "Open"}</Button>
          {d.index_status === "failed" && <Button variant="link" size="sm" onClick={() => reindex.mutate(d)}><RefreshCw aria-hidden />Re-index</Button>}
          <Button variant="link" size="sm" className="text-status-danger" aria-label={`Delete ${d.title}`} onClick={() => setConfirm({
            title: `Delete “${d.title}”?`, confirm: "Delete document", danger: true, onConfirm: () => remove.mutate(d),
            body: "The file and its passages are removed. Answers stop citing it straight away. This can't be undone; upload it again to bring it back.",
          })}><Trash2 aria-hidden />Delete</Button>
        </span>
      ),
    },
  ], [reindex, remove]);

  return (
    <div className="tl grid gap-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="grid gap-1">
          <h1 className="text-title">Knowledge</h1>
          <p className="max-w-prose text-ink-muted">What the assistant may quote when it explains a figure. Uploaded documents are checked and indexed, then wait for your review: nothing reaches an answer until it is approved.</p>
        </div>
        <Button onClick={() => setUploading(true)}><Upload aria-hidden />Upload document</Button>
      </div>
      <KnowledgeNav />
      {pending > 0 && <Alert>{pending === 1 ? "1 document is" : `${pending} documents are`} waiting for review.</Alert>}
      <DataTable
        data={items}
        columns={columns}
        label="Knowledge documents"
        getRowId={(d) => d.id}
        loading={q.isLoading}
        error={q.error && <FormAlert error={asApiError(q.error)} />}
        empty={{ title: "No knowledge yet", body: "Approve a model to index its definitions, or upload a glossary or reference document." }}
        search={items.length > 8}
      />
      <RetrievalTester />
      <UploadDialog open={uploading} onClose={() => setUploading(false)} onUploaded={refresh} />
      <ReviewSheet docId={reviewing} onClose={() => setReviewing(null)} onChanged={refresh} />
      <ConfirmDialog request={confirm} onClose={() => setConfirm(null)} busy={remove.isPending} />
    </div>
  );
}

export function UploadDialog({ open, onClose, onUploaded }: { open: boolean; onClose: () => void; onUploaded: () => void }) {
  const fileRef = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [title, setTitle] = useState("");
  const [kind, setKind] = useState("uploaded_doc");
  const [source, setSource] = useState("");
  const sheet = !!file && file.name.toLowerCase().endsWith(".xlsx");
  const connections = useQuery({
    queryKey: ["connections"], enabled: open,
    queryFn: () => api<{ items: { id: string; name: string }[] }>("/api/v1/connections"),
  });
  const upload = useMutation({
    mutationFn: () => {
      const body = new FormData();
      body.append("file", file as File);
      body.append("source_kind", kind);
      if (title.trim()) body.append("title", title.trim());
      if (source) body.append("connection_id", source);
      return api<KnowledgeDocument>("/api/v1/knowledge/documents", { method: "POST", body });
    },
    onSuccess: (doc) => {
      onUploaded();
      toast.success(`${doc.title} uploaded — indexing now`);
      setFile(null);
      setTitle("");
      onClose();
    },
  });
  return (
    <Dialog open={open} onOpenChange={(o) => !o && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Upload a document</DialogTitle>
          <DialogDescription>Markdown, text, PDF, Word (.docx) or a business-context sheet (.xlsx), up to 25 MB. Contact details are removed and secrets are dropped before anything is indexed.</DialogDescription>
        </DialogHeader>
        <form id="knowledge-upload" className="grid gap-3" onSubmit={(e) => { e.preventDefault(); if (file) upload.mutate(); }}>
          <div className="grid gap-1.5">
            <Label htmlFor="knowledge-file">File</Label>
            <Input id="knowledge-file" ref={fileRef} type="file" accept={ACCEPT} onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor="knowledge-title">Title <span className="text-ink-muted">(optional)</span></Label>
            <Input id="knowledge-title" value={title} maxLength={200} onChange={(e) => setTitle(e.target.value)} placeholder="Taken from the file name" />
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor="knowledge-source">Source {!sheet && <span className="text-ink-muted">(optional)</span>}</Label>
            <NativeSelect id="knowledge-source" value={source} onChange={(e) => setSource(e.target.value)}>
              <option value="">{sheet ? "Choose the source it describes" : "Every source"}</option>
              {(connections.data?.items ?? []).map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
            </NativeSelect>
          </div>
          <BusinessContextNote sheet={sheet} />
          <div className="grid gap-1.5">
            <Label htmlFor="knowledge-kind">Kind</Label>
            <NativeSelect id="knowledge-kind" value={kind} onChange={(e) => setKind(e.target.value)}>
              <option value="uploaded_doc">Reference document</option>
              <option value="glossary">Glossary</option>
            </NativeSelect>
          </div>
          {upload.error && <FormAlert error={asApiError(upload.error, "The document could not be uploaded.")} />}
        </form>
        <DialogFooter>
          <Button variant="secondary" onClick={onClose}>Cancel</Button>
          <Button type="submit" form="knowledge-upload" disabled={!file || upload.isPending || (sheet && !source)}>{upload.isPending ? "Uploading…" : "Upload"}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

/** Business context (US-251): one sheet of terms per source, in the template's columns; each row is proposed as a
 * definition and, once approved, becomes a named condition Ask can count by. */
function BusinessContextNote({ sheet }: { sheet: boolean }) {
  const template = useMutation({
    mutationFn: async () => {
      const resp = await fetch(`${getConfig().apiBaseUrl}/api/v1/knowledge/business-context/template`, {
        headers: { Authorization: `Bearer ${getToken() ?? ""}` },
      });
      if (!resp.ok) throw new Error("download failed");
      const url = URL.createObjectURL(await resp.blob());
      const a = document.createElement("a");
      a.href = url;
      a.download = "tela-business-context.xlsx";
      a.click();
      URL.revokeObjectURL(url);
    },
    onError: () => toast.error("The template could not be downloaded."),
  });
  return (
    <div className="grid gap-1 rounded-md border border-line p-3 text-small" aria-label="Business context">
      <p className="font-medium">Business context sheet</p>
      <p className="text-ink-muted">
        {sheet
          ? "Each row becomes a definition for review: its table, field and values are checked against the source's model."
          : "Say what your terms mean in Excel — term, meaning, table, field, operator, values, notes — and upload it for one source."}
      </p>
      <Button type="button" variant="link" size="sm" className="justify-self-start px-0" disabled={template.isPending} onClick={() => template.mutate()}>
        <Download aria-hidden />Download the template
      </Button>
    </div>
  );
}

function ReviewSheet({ docId, onClose, onChanged }: { docId: string | null; onClose: () => void; onChanged: () => void }) {
  const q = useQuery({
    queryKey: ["knowledge-document", docId],
    queryFn: () => api<KnowledgeDocumentDetail>(`/api/v1/knowledge/documents/${docId}`),
    enabled: !!docId,
  });
  const [note, setNote] = useState("");
  const [ack, setAck] = useState(false);
  const review = useMutation({
    mutationFn: (decision: "approve" | "reject" | "retire") => api<KnowledgeDocument>(`/api/v1/knowledge/documents/${docId}/review`, {
      method: "POST", body: { decision, note: note.trim() || null, acknowledge_injection: ack },
    }),
    onSuccess: (doc) => {
      onChanged();
      toast.success(`${doc.title}: ${REVIEW_LABEL[doc.review_status].toLowerCase()}`);
      setNote("");
      setAck(false);
      onClose();
    },
  });
  const doc = q.data;
  const needsAck = review.error instanceof ApiError && review.error.details.needs_acknowledgement === true;
  return (
    <Sheet open={!!docId} onOpenChange={(o) => !o && onClose()}>
      <SheetContent className="w-full max-w-2xl overflow-y-auto">
        <SheetHeader>
          <SheetTitle>{doc?.title ?? "Document"}</SheetTitle>
          <SheetDescription>{doc ? `${REVIEW_LABEL[doc.review_status]} · ${doc.chunk_count} passages · ${KIND_LABEL[doc.source_kind]}` : "Loading…"}</SheetDescription>
        </SheetHeader>
        {q.error && <div className="p-4"><FormAlert error={asApiError(q.error)} /></div>}
        {doc && (
          <div className="grid gap-4 p-4">
            {doc.injection_flagged && (
              <Alert variant="warn">
                Some passages read like instructions to the assistant (highlighted below). Agents always receive knowledge as quoted reference data, but read these before you approve.
              </Alert>
            )}
            <ol className="grid gap-2" aria-label="Passages">
              {doc.chunks.map((c) => (
                <li key={c.id} className={`rounded-md border p-3 text-small ${c.metadata.injection_flagged ? "border-status-warn bg-status-warn-soft" : "border-line"}`}>
                  <span className="mb-1 block text-ink-muted">
                    Passage {c.chunk_no + 1}{c.metadata.page ? ` · page ${c.metadata.page}` : ""}{c.metadata.injection_flagged ? " · reads like an instruction" : ""}
                  </span>
                  <span className="whitespace-pre-wrap">{c.content}</span>
                </li>
              ))}
            </ol>
            <div className="grid gap-3 border-t border-line pt-4">
                <div className="grid gap-1.5">
                  <Label htmlFor="review-note">Note <span className="text-ink-muted">(optional)</span></Label>
                  <Textarea id="review-note" value={note} maxLength={1000} onChange={(e) => setNote(e.target.value)} />
                </div>
                {doc.injection_flagged && doc.review_status !== "approved" && (
                  <label className="flex items-start gap-2 text-small">
                    <Checkbox checked={ack} onCheckedChange={(v) => setAck(v === true)} />
                    I have read the highlighted passages and approve this document anyway.
                  </label>
                )}
                {review.error && !needsAck && <FormAlert error={asApiError(review.error, "The review could not be saved.")} />}
                {needsAck && <Alert variant="warn">Tick the acknowledgement to approve a document with instruction-like text.</Alert>}
                <div className="flex flex-wrap gap-2">
                  {doc.review_status !== "approved" && (
                    <Button disabled={review.isPending || doc.index_status !== "indexed"} onClick={() => review.mutate("approve")}>Approve</Button>
                  )}
                  {doc.review_status === "pending_review" && <Button variant="secondary" disabled={review.isPending} onClick={() => review.mutate("reject")}>Reject</Button>}
                  {doc.review_status === "approved" && <Button variant="secondary" disabled={review.isPending} onClick={() => review.mutate("retire")}>Retire</Button>}
                </div>
              </div>
          </div>
        )}
      </SheetContent>
    </Sheet>
  );
}

function RetrievalTester() {
  const [query, setQuery] = useState("");
  const run = useMutation({ mutationFn: (q: string) => api<SearchResult>("/api/v1/knowledge/search", { method: "POST", body: { query: q, k: 8 } }) });
  return (
    <Card>
      <CardHeader>
        <CardTitle>Test retrieval</CardTitle>
        <CardDescription>Ask a question to see the approved passages an agent would be given (best 8 by meaning and keywords).</CardDescription>
      </CardHeader>
      <CardContent className="grid gap-3">
        <form className="flex gap-2" onSubmit={(e) => { e.preventDefault(); if (query.trim()) run.mutate(query.trim()); }}>
          <Label htmlFor="retrieval-query" className="sr-only">Question</Label>
          <Input id="retrieval-query" value={query} maxLength={500} onChange={(e) => setQuery(e.target.value)} placeholder="What counts as an overdue go-live?" />
          <Button type="submit" variant="secondary" disabled={!query.trim() || run.isPending}><Search aria-hidden />Search</Button>
        </form>
        {run.error && <FormAlert error={asApiError(run.error)} />}
        {run.data?.note && <Alert>{run.data.note}</Alert>}
        {run.data && (run.data.hits.length === 0 ? <p className="text-small text-ink-muted">No approved passages match.</p> : (
          <ol className="grid gap-2" aria-label="Retrieved passages">
            {run.data.hits.map((h) => (
              <li key={h.chunk_id} className="rounded-md border border-line p-3 text-small">
                <span className="mb-1 flex flex-wrap items-center gap-2"><Badge variant="outline">{KIND_LABEL[h.kind] ?? h.kind}</Badge><span className="font-medium">{h.title}</span></span>
                {h.snippet}
              </li>
            ))}
          </ol>
        ))}
      </CardContent>
    </Card>
  );
}
