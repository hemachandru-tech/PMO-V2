import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, Copy, Download, FileCode2, Lock, Trash2 } from "lucide-react";
import { useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { toast } from "sonner";

import { deletePack, exportPack, getPack } from "../../../api/packs";
import { Alert } from "../../../components/ui/alert";
import { Badge } from "../../../components/ui/badge";
import { Button } from "../../../components/ui/button";
import { ConfirmDialog, type ConfirmRequest } from "../../../components/ui/confirm-dialog";
import { EmptyState } from "../../../components/ui/empty-state";
import { Skeleton } from "../../../components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "../../../components/ui/tabs";
import { asApiError, friendlyMessage } from "../../../lib/api-errors";
import { CreatePackDialog } from "./CreatePackDialog";
import { ImportYamlDialog } from "./ImportYamlDialog";
import { PackProvider, packKey } from "./PackContext";
import { PACK_TABS } from "./tabs/registry";
import { PackStatusBadge } from "./tabs/VersionsTab";

/** One pack version on its tabs (US-232). System packs and released versions open read-only, with "Clone to edit". */
export function PackPage() {
  const { id = "" } = useParams();
  const nav = useNavigate();
  const qc = useQueryClient();
  const [params, setParams] = useSearchParams();
  const tab = PACK_TABS.some((t) => t.key === params.get("tab")) ? params.get("tab")! : PACK_TABS[0]!.key;
  const q = useQuery({ queryKey: packKey(id), queryFn: () => getPack(id) });
  const [cloning, setCloning] = useState(false);
  const [yaml, setYaml] = useState(false);
  const [confirm, setConfirm] = useState<ConfirmRequest | null>(null);
  const remove = useMutation({
    mutationFn: () => deletePack(id, q.data!.etag),
    onSuccess: () => { void qc.invalidateQueries({ queryKey: ["packs"] }); toast.success("Draft deleted"); nav("/admin/packs"); },
    onError: (e) => toast.error(friendlyMessage(asApiError(e, "The draft could not be deleted."))),
  });

  if (q.isLoading) return <div className="tl grid gap-4"><Skeleton className="h-10 w-1/2" /><Skeleton className="h-64" /></div>;
  if (q.isError || !q.data) {
    return <EmptyState title="Pack not found" action={<Button asChild variant="secondary"><Link to="/admin/packs">Back to packs</Link></Button>}>{asApiError(q.error).message}</EmptyState>;
  }
  const { pack, etag } = q.data;
  const readOnly = !pack.editable;
  return (
    <PackProvider pack={pack} etag={etag}>
      <div className="tl grid gap-4">
        <Link to="/admin/packs" className="flex w-fit items-center gap-1 text-small text-ink-muted hover:underline"><ArrowLeft aria-hidden className="size-4" />Packs</Link>
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="grid gap-1">
            <h1 className="text-title">{pack.title}</h1>
            <p className="flex flex-wrap items-center gap-2 text-ink-muted">
              <span className="font-mono">{pack.pack_key} {pack.version}</span>
              <PackStatusBadge status={pack.status} review={pack.review} />
              {pack.owner === "system" && <Badge variant="outline">System pack</Badge>}
              {pack.installed_on.length > 0 && <span>Installed on {pack.installed_on.map((c) => c.name).join(", ")}</span>}
            </p>
          </div>
          <div className="flex flex-wrap gap-2">
            <Button variant="secondary" onClick={() => void exportPack(pack.id, `${pack.pack_key}-${pack.version}`).catch((e: Error) => toast.error(e.message))}><Download aria-hidden />Export</Button>
            <Button variant="secondary" onClick={() => setYaml(true)}><FileCode2 aria-hidden />{readOnly ? "YAML" : "Import YAML"}</Button>
            {readOnly
              ? (pack.owner === "system" || pack.status !== "draft") && <Button onClick={() => setCloning(true)}><Copy aria-hidden />Clone to edit</Button>
              : <Button variant="ghost" onClick={() => setConfirm({
                  title: `Delete the draft ${pack.title} ${pack.version}?`, body: "The draft and its knowledge are deleted. Released versions are not affected.",
                  confirm: "Delete draft", danger: true, onConfirm: () => remove.mutate() })}><Trash2 aria-hidden />Delete draft</Button>}
          </div>
        </div>
        {readOnly && (
          <Alert><span className="flex items-center gap-2"><Lock aria-hidden className="size-4" />
            {pack.owner === "system" ? "System packs are read-only. Clone the pack to change it; the copy is yours."
              : pack.review?.state === "in_review" ? `Version ${pack.version} is waiting for review and can't change. See Versions to release it, send it back or withdraw it.`
                : `Version ${pack.version} is ${pack.status} and can't change. Open the next version on Versions to edit it.`}
          </span></Alert>
        )}
        {pack.checks.errors.length > 0 && <Alert>This draft has {pack.checks.errors.length} errors to fix. See Overview.</Alert>}
        <Tabs value={tab} onValueChange={(v) => setParams((p) => { const n = new URLSearchParams(p); n.set("tab", v); return n; }, { replace: true })}>
          <TabsList aria-label="Pack contents" className="min-w-0 overflow-x-auto overflow-y-hidden">
            {PACK_TABS.map((t) => (
              <TabsTrigger key={t.key} value={t.key} className="gap-1.5">{t.label}{t.count && <span className="font-mono text-small text-ink-muted">{t.count(pack)}</span>}</TabsTrigger>
            ))}
          </TabsList>
          {PACK_TABS.map((t) => <TabsContent key={t.key} value={t.key} className="pt-3"><t.Component /></TabsContent>)}
        </Tabs>
      </div>
      <CreatePackDialog open={cloning} onOpenChange={setCloning} from={{ id: pack.id, title: pack.title, key: pack.pack_key }}
                        onCreated={(newId) => { setCloning(false); void qc.invalidateQueries({ queryKey: ["packs"] }); toast.success("Cloned: this is your draft"); nav(`/admin/packs/${newId}`); }} />
      <ImportYamlDialog open={yaml} onOpenChange={setYaml} />
      <ConfirmDialog request={confirm} onClose={() => setConfirm(null)} busy={remove.isPending} />
    </PackProvider>
  );
}
