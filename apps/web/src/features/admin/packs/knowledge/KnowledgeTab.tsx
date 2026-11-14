import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Download, FileUp, Trash2 } from "lucide-react";
import { useId, useRef, useState } from "react";
import { toast } from "sonner";

import { ApiError } from "../../../../api/client";
import {
  attachDocument, detachDocument, downloadAttachment, getKnowledge, getKnowledgeOverview, KNOWLEDGE_KINDS, knowledgeKey,
  type KnowledgeChecks, type KnowledgeDocument, type KnowledgeKind, type KnowledgeKindView, type PackAttachment,
} from "../../../../api/packKnowledge";
import { Alert } from "../../../../components/ui/alert";
import { Badge } from "../../../../components/ui/badge";
import { Button } from "../../../../components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../../../../components/ui/card";
import { ConfirmDialog, type ConfirmRequest } from "../../../../components/ui/confirm-dialog";
import { Input } from "../../../../components/ui/input";
import { Label } from "../../../../components/ui/label";
import { Skeleton } from "../../../../components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "../../../../components/ui/tabs";
import { Textarea } from "../../../../components/ui/textarea";
import { asApiError, friendlyMessage } from "../../../../lib/api-errors";
import { load as loadYaml } from "js-yaml";
import { toYaml } from "../yaml";
import { KIND_EDITOR, KIND_LABEL } from "./kinds";
import { useSaveKnowledge } from "./ListEditor";

const bytes = (n: number) => (n < 1024 ? `${n} B` : n < 1024 * 1024 ? `${Math.round(n / 1024)} KB` : `${(n / 1024 / 1024).toFixed(1)} MB`);

/** The whole document as YAML (the pack's `<kind>.yaml`): paste or edit many items at once, checked before saving. */
function DocumentYaml({ view }: { view: KnowledgeKindView }) {
  const id = useId();
  const [text, setText] = useState(() => toYaml(view.document));
  const [problems, setProblems] = useState<string[]>([]);
  const save = useSaveKnowledge(view);
  const readOnly = view.pack.read_only;
  const onSave = async () => {
    let doc: unknown;
    try {
      doc = loadYaml(text);
    } catch (e) {
      setProblems([e instanceof Error ? e.message.split("\n")[0]! : "This isn't valid YAML."]);
      return;
    }
    if (!doc || typeof doc !== "object" || Array.isArray(doc)) { setProblems(["Write the document as a mapping (version, status and its list)."]); return; }
    setProblems([]);
    try {
      const out = await save.mutateAsync(doc as KnowledgeDocument);
      setText(toYaml(out.document));
      toast.success(`${KIND_LABEL[view.kind]} saved`);
    } catch (e) {
      const err = asApiError(e, "The document could not be saved.");
      const details = err instanceof ApiError ? (err.details as Partial<KnowledgeChecks>) : {};
      if (err.code === "validation_error" && details.errors) setProblems(details.errors.map((p) => `${p.path}: ${p.message}`));
      else toast.error(err instanceof ApiError && err.status === 412 ? "Someone changed this document since you opened it. Reload, then make your change again." : friendlyMessage(err));
    }
  };
  return (
    <div className="grid gap-2">
      <Label htmlFor={id}>{view.kind}.yaml</Label>
      <Textarea id={id} value={text} readOnly={readOnly} spellCheck={false} rows={22} className="font-mono text-small"
                aria-invalid={problems.length > 0} onChange={(e) => setText(e.target.value)} />
      {problems.length > 0 && <ul role="alert" className="grid gap-1 text-small text-status-danger">{problems.slice(0, 8).map((p) => <li key={p}>{p}</li>)}</ul>}
      {!readOnly && <Button type="button" className="w-fit" disabled={save.isPending} onClick={() => void onSave()}>Save document</Button>}
    </div>
  );
}

function KindPanel({ packId, kind }: { packId: string; kind: KnowledgeKind }) {
  const q = useQuery({ queryKey: knowledgeKey(packId, kind), queryFn: () => getKnowledge(packId, kind) });
  const [view, setView] = useState("items");
  if (q.isPending) return <Skeleton className="h-40" />;
  if (q.isError) return <Alert>{friendlyMessage(asApiError(q.error, "The document could not be loaded."))}</Alert>;
  const Editor = KIND_EDITOR[kind];
  const docErrors = q.data.checks.document_errors;
  return (
    <div className="grid gap-3">
      {docErrors.length > 0 && <Alert>The document needs fixing: {docErrors.slice(0, 3).map((p) => `${p.path}: ${p.message}`).join(" ")}</Alert>}
      <Tabs value={view} onValueChange={setView}>
        <TabsList aria-label={`${KIND_LABEL[kind]} view`}>
          <TabsTrigger value="items">Items</TabsTrigger>
          <TabsTrigger value="yaml">Document YAML</TabsTrigger>
        </TabsList>
        <TabsContent value="items"><Editor view={q.data} /></TabsContent>
        <TabsContent value="yaml"><DocumentYaml key={q.data.etag} view={q.data} /></TabsContent>
      </Tabs>
    </div>
  );
}

function Attachments({ packId, items, readOnly }: { packId: string; items: PackAttachment[]; readOnly: boolean }) {
  const qc = useQueryClient();
  const fileRef = useRef<HTMLInputElement>(null);
  const titleId = useId();
  const [title, setTitle] = useState("");
  const [confirm, setConfirm] = useState<ConfirmRequest | null>(null);
  const refresh = () => qc.invalidateQueries({ queryKey: knowledgeKey(packId), exact: true });
  const attach = useMutation({
    mutationFn: (file: File) => attachDocument(packId, file, title.trim() || undefined),
    onSuccess: (a) => { toast.success(`“${a.title}” attached`); setTitle(""); void refresh(); },
    onError: (e) => toast.error(friendlyMessage(asApiError(e, "The document could not be attached."))),
  });
  const detach = useMutation({
    mutationFn: (a: PackAttachment) => detachDocument(packId, a.id),
    onSuccess: () => { toast.success("Document taken off the pack"); void refresh(); },
    onError: (e) => toast.error(friendlyMessage(asApiError(e, "The document could not be removed."))),
  });
  return (
    <Card>
      <CardHeader>
        <CardTitle>Context documents</CardTitle>
        <CardDescription>Markdown, PDF or Word files with the business context behind this pack. Installing the pack copies them to the connection's knowledge, where they wait for review under Admin → Knowledge before Ask reads them.</CardDescription>
      </CardHeader>
      <CardContent className="grid gap-3">
        {items.length === 0 ? <p className="text-small text-ink-muted">No documents attached.</p> : (
          <ul aria-label="Attached documents" className="grid gap-1">
            {items.map((a) => (
              <li key={a.id} className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-line px-3 py-2">
                <span className="grid">
                  <span className="font-medium">{a.title}</span>
                  <span className="text-small text-ink-muted">{a.file_name} · {bytes(a.size_bytes)}</span>
                </span>
                <span className="flex gap-1">
                  <Button variant="ghost" size="sm" aria-label={`Download ${a.title}`} onClick={() => void downloadAttachment(packId, a).catch((e: unknown) => toast.error(friendlyMessage(asApiError(e, "The document could not be downloaded."))))}><Download aria-hidden /></Button>
                  {!readOnly && <Button variant="ghost" size="sm" aria-label={`Remove ${a.title}`} onClick={() => setConfirm({
                    title: `Remove “${a.title}” from the pack?`, body: "Connections it was installed on keep their copy.", confirm: "Remove", danger: true,
                    onConfirm: () => detach.mutate(a),
                  })}><Trash2 aria-hidden /></Button>}
                </span>
              </li>
            ))}
          </ul>
        )}
        {!readOnly && (
          <div className="flex flex-wrap items-end gap-2">
            <div className="grid gap-1">
              <Label htmlFor={titleId}>Title (optional)</Label>
              <Input id={titleId} value={title} className="w-64" onChange={(e) => setTitle(e.target.value)} />
            </div>
            <input ref={fileRef} type="file" accept=".md,.markdown,.txt,.pdf,.docx" className="sr-only" aria-label="Document to attach"
                   onChange={(e) => { const f = e.target.files?.[0]; if (f) attach.mutate(f); e.target.value = ""; }} />
            <Button type="button" variant="secondary" disabled={attach.isPending} onClick={() => fileRef.current?.click()}><FileUp aria-hidden />Attach a document</Button>
          </div>
        )}
      </CardContent>
      <ConfirmDialog request={confirm} onClose={() => setConfirm(null)} />
    </Card>
  );
}

/**
 * The Knowledge tab of a pack (US-233): an editor for each kind of business knowledge, each item with what it names
 * and its checks, and the context documents attached to the pack.
 */
export function KnowledgeTab({ packId }: { packId: string }) {
  const q = useQuery({ queryKey: knowledgeKey(packId), queryFn: () => getKnowledgeOverview(packId) });
  const [kind, setKind] = useState<KnowledgeKind>("glossary");
  if (q.isPending) return <Skeleton className="h-60" />;
  if (q.isError) return <Alert>{friendlyMessage(asApiError(q.error, "The pack's knowledge could not be loaded."))}</Alert>;
  const byKind = Object.fromEntries(q.data.kinds.map((k) => [k.kind, k]));
  return (
    <div className="grid gap-4">
      <Card>
        <CardHeader>
          <CardTitle>Business knowledge</CardTitle>
          <CardDescription>Ask, the planner and the advisor use the pack's knowledge on every connection it is installed on. Each item shows the measures, dimensions and fields it names; a name the pack doesn't have is flagged.</CardDescription>
        </CardHeader>
        <CardContent>
          <Tabs value={kind} onValueChange={(v) => setKind(v as KnowledgeKind)}>
            <TabsList aria-label="Knowledge kinds" className="flex-wrap">
              {KNOWLEDGE_KINDS.map((k) => {
                const s = byKind[k];
                const flagged = s ? s.errors + s.warnings : 0;
                return (
                  <TabsTrigger key={k} value={k} className="gap-2">
                    {KIND_LABEL[k]}
                    <span className="font-mono text-ink-muted">{s?.count ?? 0}</span>
                    {flagged > 0 && <Badge variant="stale">{flagged}<span className="sr-only"> to check</span></Badge>}
                  </TabsTrigger>
                );
              })}
            </TabsList>
            {KNOWLEDGE_KINDS.map((k) => (
              <TabsContent key={k} value={k} className="pt-3">{kind === k && <KindPanel packId={packId} kind={k} />}</TabsContent>
            ))}
          </Tabs>
        </CardContent>
      </Card>
      <Attachments packId={packId} items={q.data.attachments} readOnly={q.data.pack.read_only} />
    </div>
  );
}
