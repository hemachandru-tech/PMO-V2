import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Eye, Package, RotateCcw, Trash2 } from "lucide-react";
import { useCallback, useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { toast } from "sonner";

import { api } from "../../../api/client";
import type { ConnectionItem } from "../../../api/app";
import type { StarterEffects, StarterItem } from "../../../api/authoring";
import { formatRelative } from "../../../components/format";
import { Alert, FormAlert } from "../../../components/ui/alert";
import { Badge } from "../../../components/ui/badge";
import { Button } from "../../../components/ui/button";
import { Card, CardContent } from "../../../components/ui/card";
import { Checkbox } from "../../../components/ui/checkbox";
import { DataTable, type DataColumn } from "../../../components/ui/data-table";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "../../../components/ui/dialog";
import { Label } from "../../../components/ui/label";
import { NativeSelect } from "../../../components/ui/native-select";
import { Popover, PopoverContent, PopoverTrigger } from "../../../components/ui/popover";
import { Skeleton } from "../../../components/ui/skeleton";
import { asApiError } from "../../../lib/api-errors";
import { ApplyPackDialog } from "./ApplyPackDialog";
import { PackUpdates } from "./PackUpdates";

const KEY = ["admin-starter"];
// What a change to starter content shows elsewhere: starter dashboards, Home shelves, the composer shelf, drafts.
const AFFECTED = [["dashboard"], ["home"], ["starter-reports"], ["dashboard-draft"]];
type Role = { id: string; key: string; name: string };

const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;

function StateBadge({ item }: { item: StarterItem }) {
  if (item.availability === "unavailable") return <Badge variant="neutral" title="The data source cannot support this report yet">Unavailable</Badge>;
  if (item.state === "deleted") return <Badge variant="stale">Deleted</Badge>;
  if (item.state === "hidden") return <Badge variant="outline">Hidden</Badge>;
  return <Badge variant="certified">Active</Badge>;
}

function visibleTo(item: StarterItem): string {
  const hidden = item.visibility.filter((v) => !v.visible);
  if (hidden.length === 0) return "Everyone";
  return `Hidden from ${hidden.map((v) => v.name).join(", ")}`;
}

/**
 * Admin → Starter content (US-099; SCR-11; FEAT-046): every starter report of the installed packs with its state, who
 * sees it, how many people copied it and how many dashboards include it. Hide it from a role or show it to all; delete
 * it after reading what that does (RULE-017); restore it from the pack. Reports the data cannot support yet are listed
 * as Unavailable with what is missing. Newer pack versions are offered above the list (US-120).
 */
export function StarterContentPage() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: KEY, queryFn: () => api<{ items: StarterItem[]; roles: Role[] }>("/api/v1/admin/starter") });
  const [deleting, setDeleting] = useState<StarterItem | null>(null);
  const update = useCallback((item: StarterItem) => {
    qc.setQueryData<{ items: StarterItem[]; roles: Role[] }>(KEY, (old) => old && ({
      ...old, items: old.items.map((x) => (x.id === item.id ? item : x)),
    }));
    for (const queryKey of AFFECTED) void qc.invalidateQueries({ queryKey });
  }, [qc]);
  const restore = useMutation({
    mutationFn: (item: StarterItem) => api<{ item: StarterItem }>(`/api/v1/admin/starter/${item.id}/restore`, { method: "POST" }),
    onSuccess: ({ item }) => { update(item); toast.success(`${item.title} is back from the pack`); },
    onError: (e) => toast.error(asApiError(e, "The report could not be restored.").message),
  });
  const roles = useMemo(() => q.data?.roles ?? [], [q.data]);
  const items = q.data?.items ?? [];

  const columns = useMemo<DataColumn<StarterItem>[]>(() => [
    {
      id: "title", header: "Starter report", alwaysVisible: true, value: (r) => `${r.title} ${r.connection.name}`,
      cell: (r) => (
        <span className="grid gap-0.5">
          {r.report_id && r.state !== "deleted" ? <Link to={`/reports/${r.report_id}`} className="font-medium">{r.title}</Link> : <span className="font-medium">{r.title}</span>}
          <span className="text-small text-ink-muted">{r.connection.name} · {r.pack.key} {r.pack.version}{r.availability === "available" ? ` · ${plural(r.widgets, "widget")}` : ""}</span>
          {r.availability === "unavailable" && r.gaps.length > 0 && <span className="text-small text-ink-muted">Needs {r.gaps.join(", ")}</span>}
          {r.availability === "available" && r.gaps.length > 0 && <span className="text-small text-ink-muted">Some widgets left out: needs {r.gaps.join(", ")}</span>}
        </span>
      ),
    },
    { id: "state", header: "State", filter: "select", value: (r) => (r.availability === "unavailable" ? "unavailable" : r.state), cell: (r) => <StateBadge item={r} /> },
    { id: "visibility", header: "Visible to", value: visibleTo, cell: (r) => <span className="text-small">{visibleTo(r)}</span> },
    { id: "copies", header: "User copies", numeric: true, value: (r) => r.copies },
    { id: "dashboards", header: "On dashboards", numeric: true, value: (r) => r.dashboards },
    { id: "updated", header: "Changed", date: true, hidden: true, value: (r) => r.updated_at, cell: (r) => formatRelative(r.updated_at) },
    {
      id: "actions", header: "Actions", alwaysVisible: true, sortable: false, value: () => "",
      cell: (r) => r.availability === "unavailable" ? <span className="text-small text-ink-muted">Not installed</span> : r.state === "deleted" ? (
        <Button variant="link" size="sm" disabled={restore.isPending} onClick={() => restore.mutate(r)}><RotateCcw aria-hidden />Restore from pack</Button>
      ) : (
        <span className="flex flex-wrap gap-x-3">
          <VisibilityControl item={r} roles={roles} onSaved={update} />
          <Button variant="link" size="sm" className="text-status-danger" onClick={() => setDeleting(r)}><Trash2 aria-hidden />Delete</Button>
        </span>
      ),
    },
  ], [roles, restore, update]);

  const deleted = items.filter((i) => i.state === "deleted").length;
  return (
    <div className="tl grid gap-4">
      <div className="grid gap-1">
        <h1 className="text-title">Starter content</h1>
        <p className="text-ink-muted">The reports everyone starts from. Hide one from a role, or delete it: people's copies keep working, and dashboards that include it keep their live version until they are republished. You can restore any starter report from its pack.</p>
      </div>
      <ApplyToConnection />
      <PackUpdates onApplied={() => { void qc.invalidateQueries({ queryKey: KEY }); for (const queryKey of AFFECTED) void qc.invalidateQueries({ queryKey }); }} />
      {deleted > 0 && <Alert>{plural(deleted, "starter report is", "starter reports are")} deleted. Restore from pack brings one back on its starter dashboard and the composer shelf.</Alert>}
      <DataTable
        data={items}
        columns={columns}
        label="Starter reports"
        getRowId={(r) => r.id}
        loading={q.isLoading}
        error={q.error && <FormAlert error={asApiError(q.error)} />}
        empty={{ title: "No starter content yet", body: "Apply a starter pack to a connection above, then approve its model." }}
        search={items.length > 8}
      />
      <DeleteStarterDialog item={deleting} onClose={() => setDeleting(null)} onDeleted={update} />
    </div>
  );
}

/**
 * Apply a starter pack to any connection that has read its schema, with or without a model (US-153; BUG-004).
 */
function ApplyToConnection() {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const connections = useQuery({ queryKey: ["connections"], queryFn: () => api<{ items: ConnectionItem[] }>("/api/v1/connections") });
  const usable = (connections.data?.items ?? []).filter((c) => c.status !== "draft");
  const [chosen, setChosen] = useState("");
  const [open, setOpen] = useState(false);
  const target = usable.find((c) => c.id === chosen) ?? usable[0];
  if (!usable.length) return null;
  return (
    <Card>
      <CardContent className="flex flex-wrap items-end gap-3 pt-4">
        <div className="grid min-w-64 flex-1 gap-1">
          <Label htmlFor="pack-connection">Apply a starter pack to a connection</Label>
          <NativeSelect id="pack-connection" value={target?.id ?? ""} onChange={(e) => setChosen(e.target.value)}>
            {usable.map((c) => <option key={c.id} value={c.id}>{c.name}{c.model.id ? " · has a model" : ""}</option>)}
          </NativeSelect>
        </div>
        <Button variant="secondary" onClick={() => setOpen(true)} disabled={!target}><Package aria-hidden />Preview and apply</Button>
      </CardContent>
      {target && (
        <ApplyPackDialog connectionId={target.id} connectionName={target.name} open={open} onClose={() => setOpen(false)} onApplied={(r) => {
          void qc.invalidateQueries({ queryKey: ["connections"] });
          toast.success(`Starter pack ${r.mode === "merge" ? "added to the model draft" : "installed"} — approve the model to build ${r.installed.reports} starter reports.`, {
            action: { label: "Open Model studio", onClick: () => navigate(`/admin/models/${r.model_id}`) },
          });
        }} />
      )}
    </Card>
  );
}

/** "Visible to" as one checkbox per role; saving sends the roles it is hidden from, or Show to all. */
function VisibilityControl({ item, roles, onSaved }: { item: StarterItem; roles: Role[]; onSaved: (item: StarterItem) => void }) {
  const [open, setOpen] = useState(false);
  const [hidden, setHidden] = useState<Set<string>>(new Set());
  const save = useMutation({
    mutationFn: (ids: string[]) => api<StarterItem>(`/api/v1/admin/starter/${item.id}/visibility`, {
      method: "POST", body: ids.length ? { hide_from_roles: ids } : { show_to_all: true },
    }),
    onSuccess: (saved) => {
      onSaved(saved);
      setOpen(false);
      toast.success(saved.state === "hidden" ? `${saved.title}: ${visibleTo(saved).toLowerCase()}` : `${saved.title} is shown to everyone`);
    },
    onError: (e) => toast.error(asApiError(e, "Visibility could not be saved.").message),
  });
  const openWith = (next: boolean) => {
    if (next) setHidden(new Set(item.visibility.filter((v) => !v.visible).map((v) => v.role_id)));
    setOpen(next);
  };
  const headingId = `visibility-${item.id}`;
  return (
    <Popover open={open} onOpenChange={openWith}>
      <PopoverTrigger asChild>
        <Button variant="link" size="sm" aria-label={`Visibility of ${item.title}`}><Eye aria-hidden />Visibility</Button>
      </PopoverTrigger>
      <PopoverContent align="start" className="grid w-72 gap-3" aria-labelledby={headingId}>
        <div className="grid gap-0.5">
          <h2 id={headingId} className="font-medium">Visible to</h2>
          <p className="text-small text-ink-muted">A hidden report leaves the starter dashboard and the composer for that role. Dashboards that include it stay as they are.</p>
        </div>
        <ul className="grid gap-2">
          {roles.map((r) => (
            <li key={r.id}>
              <label className="flex items-center gap-2">
                <Checkbox checked={!hidden.has(r.id)} onCheckedChange={(on) => setHidden((h) => {
                  const next = new Set(h);
                  if (on) next.delete(r.id); else next.add(r.id);
                  return next;
                })} />
                {r.name}
              </label>
            </li>
          ))}
        </ul>
        <div className="flex justify-between gap-2">
          <Button variant="ghost" size="sm" onClick={() => setHidden(new Set())}>Show to all</Button>
          <Button size="sm" disabled={save.isPending} onClick={() => save.mutate([...hidden])}>Save</Button>
        </div>
      </PopoverContent>
    </Popover>
  );
}

/** Delete confirmation that first asks the API what deleting does (dry run), then says it in plain words (RULE-017). */
function DeleteStarterDialog({ item, onClose, onDeleted }: { item: StarterItem | null; onClose: () => void; onDeleted: (item: StarterItem) => void }) {
  const effects = useQuery({
    queryKey: ["admin-starter-effects", item?.id],
    enabled: !!item,
    gcTime: 0,
    queryFn: () => api<{ effects: StarterEffects }>(`/api/v1/admin/starter/${item!.id}?dry_run=true`, { method: "DELETE" }),
  });
  const del = useMutation({
    mutationFn: (i: StarterItem) => api<{ item: StarterItem; notified: number }>(`/api/v1/admin/starter/${i.id}`, { method: "DELETE" }),
    onSuccess: (r) => {
      onDeleted(r.item);
      onClose();
      toast.success(`${r.item.title} was deleted${r.notified ? `; ${plural(r.notified, "dashboard owner")} notified` : ""}`);
    },
    onError: (e) => toast.error(asApiError(e, "The report could not be deleted.").message),
  });
  const e = effects.data?.effects;
  return (
    <Dialog open={!!item} onOpenChange={(open) => !open && onClose()}>
      <DialogContent onOpenAutoFocus={(ev) => { ev.preventDefault(); (ev.currentTarget as HTMLElement).querySelector<HTMLButtonElement>("[data-cancel]")?.focus(); }}>
        {item && (
          <>
            <DialogHeader>
              <DialogTitle>Delete {item.title}?</DialogTitle>
              <DialogDescription>Here is what happens. You can restore it from the pack at any time.</DialogDescription>
            </DialogHeader>
            {effects.isLoading ? <Skeleton className="h-24" /> : effects.error ? <FormAlert error={asApiError(effects.error)} /> : e && (
              <ul className="grid list-disc gap-2 pl-5">
                <li>{e.user_copies_unaffected === 0 ? "Nobody has copied it." : `${plural(e.user_copies_unaffected, "user copy", "user copies")} stay and keep working.`}</li>
                {e.starter_dashboards.length > 0 && <li>It leaves {e.starter_dashboards.map((d) => d.title).join(" and ")} at once.</li>}
                {e.dashboards_getting_new_draft.length > 0 && (
                  <li>
                    {plural(e.dashboards_getting_new_draft.length, "published dashboard gets", "published dashboards get")} a new draft without it. The live version stays until its owner republishes:
                    <ul className="mt-1 grid gap-0.5">
                      {e.dashboards_getting_new_draft.map((d) => <li key={d.dashboard_id} className="text-small">{d.title} — version {d.live_version_stays} stays live{d.owner ? ` · ${d.owner} is notified` : ""}</li>)}
                    </ul>
                  </li>
                )}
                {e.drafts_without_it.length > 0 && <li>It is taken out of {plural(e.drafts_without_it.length, "unpublished draft")}: {e.drafts_without_it.map((d) => d.title).join(", ")}.</li>}
              </ul>
            )}
            <DialogFooter>
              <Button variant="ghost" data-cancel onClick={onClose}>Cancel</Button>
              <Button variant="danger" disabled={!e || del.isPending} onClick={() => del.mutate(item)}>Delete starter report</Button>
            </DialogFooter>
          </>
        )}
      </DialogContent>
    </Dialog>
  );
}
