import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { FileText, LayoutGrid, Sparkles, Trash2 } from "lucide-react";
import { useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { toast } from "sonner";

import { api } from "../../api/client";
import { VISUAL_NAMES, type LibraryWidget, type ReportListItem } from "../../api/authoring";
import { can, useMe } from "../../app/me";
import { ContentBadge } from "../../components/ContentBadge";
import { formatRelative } from "../../components/format";
import { FormAlert } from "../../components/ui/alert";
import { Badge } from "../../components/ui/badge";
import { Button } from "../../components/ui/button";
import { DataTable, type DataColumn } from "../../components/ui/data-table";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "../../components/ui/dialog";
import { asApiError } from "../../lib/api-errors";
import { NewReportDialog } from "./NewReportDialog";
import { useAppName } from "../../app/branding";

/** Build → Reports (FEAT-021, FEAT-024; US-141): my reports, reports shared with me and my widget library. */
export function ReportsPage() {
  const appName = useAppName();
  const me = useMe().data;
  const qc = useQueryClient();
  const navigate = useNavigate();
  const reports = useQuery({ queryKey: ["reports"], queryFn: () => api<{ items: ReportListItem[] }>("/api/v1/reports") });
  const lib = useQuery({
    queryKey: ["library"], queryFn: () => api<{ items: LibraryWidget[] }>("/api/v1/widgets"), enabled: can(me, "widgets.create"),
  });
  const [creating, setCreating] = useState(false);
  const [deleting, setDeleting] = useState<ReportListItem | null>(null);
  const remove = useMutation({
    mutationFn: (r: ReportListItem) => api(`/api/v1/reports/${r.id}`, { method: "DELETE" }),
    onSuccess: (_, r) => {
      toast(`${r.title} was deleted`);
      setDeleting(null);
      void qc.invalidateQueries({ queryKey: ["reports"] });
      void qc.invalidateQueries({ queryKey: ["home"] });
    },
  });
  const columns = useMemo<DataColumn<ReportListItem>[]>(() => [
    {
      id: "title", header: "Title", alwaysVisible: true,
      cell: (r) => <Link to={`/reports/${r.id}`} className="font-medium text-link hover:underline focus-visible:focus-ring">{r.title}</Link>,
    },
    { id: "state", header: "State", value: (r) => (r.published ? "Published" : "Draft"), filter: "select",
      cell: (r) => (r.published ? <Badge variant="certified">Published</Badge> : <ContentBadge badge="draft" />) },
    { id: "widgets", header: "Widgets", numeric: true, value: (r) => r.widgets },
    { id: "owner", header: "Owner", value: (r) => (r.mine ? "Me" : `${r.owner_name} (shared)`), filter: "select" },
    { id: "updated_at", header: "Updated", date: true, cell: (r) => formatRelative(r.updated_at) },
    {
      id: "actions", header: "Actions", sortable: false, alwaysVisible: true, value: () => "",
      cell: (r) => r.mine && (
        <Button variant="ghost" size="icon" className="size-control-sm" aria-label={`Delete ${r.title}`} onClick={() => setDeleting(r)}><Trash2 aria-hidden /></Button>
      ),
    },
  ], []);
  const libColumns = useMemo<DataColumn<LibraryWidget>[]>(() => [
    { id: "title", header: "Widget", alwaysVisible: true, value: (w) => w.title },
    { id: "visual", header: "Chart", value: (w) => VISUAL_NAMES[w.visual] ?? w.visual, filter: "select" },
    { id: "badges", header: "Badges", value: (w) => w.badges.join(" "), cell: (w) => <span className="flex gap-1">{w.badges.map((b) => <ContentBadge key={b} badge={b} />)}</span> },
    { id: "used_on_reports", header: "On reports", numeric: true, value: (w) => w.used_on_reports },
  ], []);
  const creator = can(me, "reports.create");
  return (
    <div className="tl grid gap-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div className="grid gap-1">
          <h1 className="text-title">Reports</h1>
          <p className="text-ink-muted">Drafts only you can see until they are shared or added to a published dashboard.</p>
        </div>
        {creator && (
          <div className="flex flex-wrap gap-2">
            <Button variant="secondary" onClick={() => setCreating(true)}><LayoutGrid aria-hidden />New blank report</Button>
            <Button asChild><Link to="/reports/new"><Sparkles aria-hidden />Build with chat</Link></Button>
          </div>
        )}
      </div>
      <DataTable data={reports.data?.items ?? []} columns={columns} label="Reports" getRowId={(r) => r.id} loading={reports.isLoading}
        error={reports.error && <FormAlert error={asApiError(reports.error)} />}
        empty={{ title: "No reports yet", body: `Describe a report and ${appName} plans it from certified measures, or start from a blank canvas.`,
          action: creator ? <Button asChild variant="secondary"><Link to="/reports/new"><FileText aria-hidden />Plan a report</Link></Button> : undefined }}
        search={(reports.data?.items.length ?? 0) > 8} onRowActivate={(r) => navigate(`/reports/${r.id}`)} />
      {lib.data && (
        <section aria-labelledby="library-title" className="grid gap-2">
          <h2 id="library-title" className="text-heading">Widget library</h2>
          <DataTable data={lib.data.items} columns={libColumns} label="Widget library" getRowId={(w) => w.id}
            empty={{ title: "Your library is empty", body: "Pin an answer from Ask, or add a certified measure on a report's canvas." }}
            search={lib.data.items.length > 8} />
        </section>
      )}
      <NewReportDialog open={creating} onOpenChange={setCreating} />
      <Dialog open={deleting !== null} onOpenChange={(open) => !open && setDeleting(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Delete “{deleting?.title}”?</DialogTitle>
            <DialogDescription>It leaves your list and the people it was shared with lose it. Its widgets stay in your library. A report on a dashboard must be removed from the dashboard first.</DialogDescription>
          </DialogHeader>
          <FormAlert error={remove.error ? asApiError(remove.error) : null} />
          <DialogFooter>
            <Button variant="secondary" onClick={() => setDeleting(null)}>Cancel</Button>
            <Button variant="danger" disabled={remove.isPending} onClick={() => deleting && remove.mutate(deleting)}>Delete report</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
