import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus, Shapes, Trash2 } from "lucide-react";
import { useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { toast } from "sonner";

import { api } from "../../api/client";
import { KIND_LABEL, type MyRequest, type PersonalField } from "../../api/govern";
import { ContentBadge } from "../../components/ContentBadge";
import { formatRelative } from "../../components/format";
import { FormAlert } from "../../components/ui/alert";
import { Badge } from "../../components/ui/badge";
import { Button } from "../../components/ui/button";
import { DataTable, type DataColumn } from "../../components/ui/data-table";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "../../components/ui/dialog";
import { StatusBadge } from "../../components/ui/status-badge";
import { asApiError } from "../../lib/api-errors";
import { RequestCertification } from "./RequestCertification";

export const REQUEST_STATUS: Record<MyRequest["status"], string> = {
  pending: "Waiting for review", certified: "Certified", merged: "Merged", rejected: "Not certified",
};

/** Build → Fields (FEAT-028 – 032; SCR-05; US-078): my personal fields and their certification state. */
export function FieldsPage() {
  const navigate = useNavigate();
  const qc = useQueryClient();
  const fields = useQuery({ queryKey: ["fields"], queryFn: () => api<{ items: PersonalField[] }>("/api/v1/fields") });
  const mine = useQuery({ queryKey: ["cert-mine"], queryFn: () => api<{ items: MyRequest[] }>("/api/v1/certification/mine") });
  const [requesting, setRequesting] = useState<PersonalField | null>(null);
  const [deleting, setDeleting] = useState<PersonalField | null>(null);
  const latest = useMemo(() => {
    const out = new Map<string, MyRequest>();
    for (const r of mine.data?.items ?? []) if (!out.has(r.item_id)) out.set(r.item_id, r);
    return out;
  }, [mine.data]);
  const remove = useMutation({
    mutationFn: (f: PersonalField) => api(`/api/v1/fields/${f.id}`, { method: "DELETE" }),
    onSuccess: (_, f) => {
      toast(`${f.name} was deleted`);
      setDeleting(null);
      void qc.invalidateQueries({ queryKey: ["fields"] });
    },
  });
  const columns = useMemo<DataColumn<PersonalField>[]>(() => [
    {
      id: "name", header: "Name", alwaysVisible: true,
      cell: (f) => <Link to={`/fields/${f.id}`} className="font-medium text-link hover:underline focus-visible:focus-ring">{f.name}</Link>,
    },
    { id: "kind", header: "Kind", value: (f) => KIND_LABEL[f.kind], filter: "select" },
    {
      id: "status", header: "Status", value: (f) => (f.broken ? "Broken" : latest.get(f.id) ? REQUEST_STATUS[latest.get(f.id)!.status] : f.badge),
      cell: (f) => {
        if (f.broken) return <StatusBadge status="danger" title={f.broken_reason ?? undefined}>Broken</StatusBadge>;
        const r = latest.get(f.id);
        return (
          <span className="flex flex-wrap items-center gap-1">
            <ContentBadge badge={f.badge} />
            {r && f.badge !== "certified" && <Badge variant={r.status === "rejected" ? "stale" : "outline"} title={r.decision_note ?? undefined}>{REQUEST_STATUS[r.status]}</Badge>}
          </span>
        );
      },
    },
    { id: "updated_at", header: "Updated", date: true, cell: (f) => formatRelative(f.updated_at) },
    {
      id: "actions", header: "Actions", sortable: false, alwaysVisible: true, value: () => "",
      cell: (f) => (
        <span className="flex gap-1">
          {f.badge !== "certified" && latest.get(f.id)?.status !== "pending" && (
            <Button variant="ghost" size="sm" onClick={() => setRequesting(f)}>Request certification</Button>
          )}
          <Button variant="ghost" size="icon" className="size-control-sm" aria-label={`Delete ${f.name}`} onClick={() => setDeleting(f)}><Trash2 aria-hidden /></Button>
        </span>
      ),
    },
  ], [latest]);
  return (
    <div className="tl grid gap-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div className="grid gap-1">
          <h1 className="text-title">Fields</h1>
          <p className="text-ink-muted">Personal groupings, bands and cross-table measures. Only you can use them until they are certified.</p>
        </div>
        <Button onClick={() => navigate("/fields/new")}><Plus aria-hidden />New field</Button>
      </div>
      <DataTable data={fields.data?.items ?? []} columns={columns} label="My fields" getRowId={(f) => f.id} loading={fields.isLoading}
        error={fields.error && <FormAlert error={asApiError(fields.error)} />}
        empty={{ title: "No fields yet", body: "Group values, band numbers or count across tables, then use the field in Ask and on reports.", action: <Button asChild variant="secondary"><Link to="/fields/new"><Shapes aria-hidden />Build a field</Link></Button> }}
        search={(fields.data?.items.length ?? 0) > 8} onRowActivate={(f) => navigate(`/fields/${f.id}`)} />
      <RequestCertification item={requesting && { kind: requesting.kind, id: requesting.id }} name={requesting?.name ?? ""}
        open={requesting !== null} onOpenChange={(open) => !open && setRequesting(null)} />
      <Dialog open={deleting !== null} onOpenChange={(open) => !open && setDeleting(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Delete “{deleting?.name}”?</DialogTitle>
            <DialogDescription>Widgets that use this field stop working until you change them. This can't be undone.</DialogDescription>
          </DialogHeader>
          <FormAlert error={remove.error ? asApiError(remove.error) : null} />
          <DialogFooter>
            <Button variant="secondary" onClick={() => setDeleting(null)}>Cancel</Button>
            <Button variant="danger" disabled={remove.isPending} onClick={() => deleting && remove.mutate(deleting)}>Delete field</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
