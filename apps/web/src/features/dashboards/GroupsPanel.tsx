import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowDown, ArrowUp, FolderPlus, Pencil, Plus, Trash2, X } from "lucide-react";
import { useMemo, useState } from "react";
import { useForm } from "react-hook-form";
import { Link } from "react-router-dom";
import { toast } from "sonner";
import { z } from "zod";

import type { Home } from "../../api/app";
import type { DashboardGroup, DashboardGroupsResponse, DashboardListItem } from "../../api/authoring";
import { api } from "../../api/client";
import { GROUP_ICONS, GROUPS_KEY, groupIcon } from "../../app/shell/dashboardGroups";
import { FormAlert } from "../../components/ui/alert";
import { Button } from "../../components/ui/button";
import { Card } from "../../components/ui/card";
import { Checkbox } from "../../components/ui/checkbox";
import { ConfirmDialog, type ConfirmRequest } from "../../components/ui/confirm-dialog";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "../../components/ui/dialog";
import { EmptyState } from "../../components/ui/empty-state";
import { Field, Form } from "../../components/ui/form";
import { Input } from "../../components/ui/input";
import { NativeSelect } from "../../components/ui/native-select";
import { Skeleton } from "../../components/ui/skeleton";
import { asApiError } from "../../lib/api-errors";

const URL = "/api/v1/dashboard-groups";

export function useDashboardGroups(enabled = true) {
  return useQuery({ queryKey: [GROUPS_KEY, "manage"], queryFn: () => api<DashboardGroupsResponse>(URL), enabled });
}

/** A dashboard that can be added to a group: one this person can open. */
interface Candidate {
  id: string;
  title: string;
  note: string;
}

/** Dashboards this person can open: Home's shelves (starter, published to their roles, shared, theirs) and the list. */
function useCandidates(list: DashboardListItem[]): Candidate[] {
  const home = useQuery({ queryKey: ["home"], queryFn: () => api<Home>("/api/v1/home") });
  return useMemo(() => {
    const out = new Map<string, Candidate>();
    for (const [shelf, note] of [["starter", "Starter"], ["published_to_my_role", "Published"], ["mine", "Mine"], ["shared_with_me", "Shared with me"]] as const) {
      for (const t of home.data?.shelves[shelf] ?? []) {
        if (t.type === "dashboard" && !out.has(t.id)) out.set(t.id, { id: t.id, title: t.title, note: shelf === "mine" ? (t.published ? "Published" : "Draft") : note });
      }
    }
    for (const d of list) if (!out.has(d.id)) out.set(d.id, { id: d.id, title: d.title, note: d.live_version ? "Published" : `Draft · ${d.mine ? "Me" : d.owner_name}` });
    return [...out.values()].sort((a, b) => a.title.localeCompare(b.title));
  }, [home.data, list]);
}

function useGroupMutation<T>(fn: (vars: T) => Promise<unknown>, done?: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: fn,
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: [GROUPS_KEY] });
      if (done) toast.success(done);
    },
    onError: (e) => toast.error("The group could not be changed", { description: asApiError(e).message }),
  });
}

const move = <T,>(list: T[], i: number, by: number): T[] => {
  const next = [...list];
  const [x] = next.splice(i, 1);
  next.splice(i + by, 0, x!);
  return next;
};

/**
 * Build → Dashboards → Groups (ENH-009, US-170): Admins and Creators group dashboards; the groups show in everyone's
 * sidebar (US-171). A Creator changes the groups they created, an Admin any group and the order of the groups. Each
 * person sees, in a group, only the dashboards they can open; the API keeps the others and only counts them.
 */
export function GroupsPanel({ list }: { list: DashboardListItem[] }) {
  const groups = useDashboardGroups();
  const candidates = useCandidates(list);
  const [editing, setEditing] = useState<DashboardGroup | "new" | null>(null);
  const [adding, setAdding] = useState<DashboardGroup | null>(null);
  const [confirm, setConfirm] = useState<ConfirmRequest | null>(null);
  const items = useGroupMutation(({ id, ids }: { id: string; ids: string[] }) =>
    api(`${URL}/${id}/items`, { method: "PUT", body: { dashboard_ids: ids } }));
  const order = useGroupMutation((ids: string[]) => api(`${URL}/order`, { method: "PUT", body: { group_ids: ids } }));
  const remove = useGroupMutation((id: string) => api(`${URL}/${id}`, { method: "DELETE" }), "Group deleted.");

  if (groups.isLoading) return <div className="grid gap-3" aria-busy="true"><Skeleton className="h-24" /><Skeleton className="h-24" /></div>;
  if (groups.error) return <FormAlert error={asApiError(groups.error)} />;
  const { items: all, can_reorder } = groups.data!;
  const ids = all.map((g) => g.id);
  const newButton = <Button onClick={() => setEditing("new")}><FolderPlus aria-hidden />New group</Button>;

  return (
    <div className="grid gap-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="max-w-2xl text-ink-muted">
          Groups appear in everyone's sidebar, between Explore and Build. Each person sees only the dashboards in a group that
          they can already open; a group with none of them is hidden from that person.
        </p>
        {all.length > 0 && newButton}
      </div>
      {all.length === 0 ? (
        <EmptyState icon={FolderPlus} title="No groups yet" action={newButton}>
          Put related dashboards together, such as “Delivery” or “Finance”, so people can open them from the sidebar.
        </EmptyState>
      ) : (
        <ol className="grid gap-3" aria-label="Dashboard groups">
          {all.map((g, gi) => {
            const Icon = groupIcon(g.icon);
            const dashIds = g.dashboards.map((d) => d.id);
            return (
              <li key={g.id}>
                <Card className="grid gap-3 p-4">
                  <div className="flex flex-wrap items-start justify-between gap-2">
                    <div className="flex min-w-0 items-center gap-2">
                      <Icon aria-hidden className="size-5 shrink-0 text-ink-muted" />
                      <div className="grid min-w-0">
                        <h3 className="truncate text-body-strong">{g.name}</h3>
                        <span className="text-small text-ink-muted">
                          {g.dashboards.length} {g.dashboards.length === 1 ? "dashboard" : "dashboards"} · created by {g.created_by.name}
                        </span>
                      </div>
                    </div>
                    <div className="flex flex-wrap items-center gap-1">
                      {can_reorder && (
                        <>
                          <Button variant="ghost" size="icon" aria-label={`Move ${g.name} up`} disabled={gi === 0 || order.isPending}
                            onClick={() => order.mutate(move(ids, gi, -1))}><ArrowUp aria-hidden /></Button>
                          <Button variant="ghost" size="icon" aria-label={`Move ${g.name} down`} disabled={gi === all.length - 1 || order.isPending}
                            onClick={() => order.mutate(move(ids, gi, 1))}><ArrowDown aria-hidden /></Button>
                        </>
                      )}
                      {g.can_edit && (
                        <>
                          <Button variant="secondary" size="sm" onClick={() => setAdding(g)}><Plus aria-hidden />Add dashboards</Button>
                          <Button variant="ghost" size="icon" aria-label={`Rename ${g.name}`} onClick={() => setEditing(g)}><Pencil aria-hidden /></Button>
                          <Button variant="ghost" size="icon" aria-label={`Delete ${g.name}`} onClick={() => setConfirm({
                            title: `Delete “${g.name}”?`, confirm: "Delete group", danger: true, onConfirm: () => remove.mutate(g.id),
                            body: "The group leaves everyone's sidebar. The dashboards in it are not changed.",
                          })}><Trash2 aria-hidden /></Button>
                        </>
                      )}
                    </div>
                  </div>
                  {g.dashboards.length > 0 && (
                    <ol className="grid gap-1" aria-label={`Dashboards in ${g.name}`}>
                      {g.dashboards.map((d, di) => (
                        <li key={d.id} className="flex min-w-0 items-center gap-1 rounded-md border border-line px-2 py-1">
                          <Link to={d.link} className="min-w-0 flex-1 truncate text-link hover:underline focus-visible:focus-ring" title={d.title}>{d.title}</Link>
                          {!d.published && <span className="text-small text-ink-muted">Draft</span>}
                          {g.can_edit && (
                            <>
                              <Button variant="ghost" size="icon" aria-label={`Move ${d.title} up`} disabled={di === 0 || items.isPending}
                                onClick={() => items.mutate({ id: g.id, ids: move(dashIds, di, -1) })}><ArrowUp aria-hidden /></Button>
                              <Button variant="ghost" size="icon" aria-label={`Move ${d.title} down`} disabled={di === dashIds.length - 1 || items.isPending}
                                onClick={() => items.mutate({ id: g.id, ids: move(dashIds, di, 1) })}><ArrowDown aria-hidden /></Button>
                              <Button variant="ghost" size="icon" aria-label={`Remove ${d.title} from ${g.name}`} disabled={items.isPending}
                                onClick={() => items.mutate({ id: g.id, ids: dashIds.filter((x) => x !== d.id) })}><X aria-hidden /></Button>
                            </>
                          )}
                        </li>
                      ))}
                    </ol>
                  )}
                  {g.dashboards.length === 0 && g.hidden_count === 0 && (
                    <p className="text-small text-ink-muted">No dashboards yet. Until it has one, the group is not shown in the sidebar.</p>
                  )}
                  {g.hidden_count > 0 && (
                    <p className="text-small text-ink-muted">
                      Also {g.hidden_count} {g.hidden_count === 1 ? "dashboard" : "dashboards"} you can't open. People who can open them see them here; your changes keep them.
                    </p>
                  )}
                </Card>
              </li>
            );
          })}
        </ol>
      )}
      <GroupDialog group={editing} onClose={() => setEditing(null)} />
      <AddDashboardsDialog group={adding} candidates={candidates} onClose={() => setAdding(null)}
        onSave={(g, chosen) => items.mutate({ id: g.id, ids: chosen }, { onSuccess: () => setAdding(null) })} saving={items.isPending} />
      <ConfirmDialog request={confirm} onClose={() => setConfirm(null)} busy={remove.isPending} />
    </div>
  );
}

const groupForm = z.object({
  name: z.string().trim().min(1, "Give the group a name.").max(60, "Use 60 characters or fewer."),
  icon: z.string(),
});

function GroupDialog({ group, onClose }: { group: DashboardGroup | "new" | null; onClose: () => void }) {
  const isNew = group === "new";
  const current = group && group !== "new" ? group : null;
  const form = useForm<z.infer<typeof groupForm>>({
    resolver: zodResolver(groupForm), mode: "onTouched",
    values: { name: current?.name ?? "", icon: current?.icon ?? "folder" },
  });
  const qc = useQueryClient();
  const save = useMutation({
    mutationFn: (v: z.infer<typeof groupForm>) => isNew
      ? api(URL, { method: "POST", body: { name: v.name, icon: v.icon } })
      : api(`${URL}/${current!.id}`, { method: "PATCH", body: { name: v.name, icon: v.icon } }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: [GROUPS_KEY] });
      toast.success(isNew ? "Group created." : "Group saved.");
      onClose();
    },
  });
  return (
    <Dialog open={group !== null} onOpenChange={(o) => { if (!o) { save.reset(); onClose(); } }}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{isNew ? "New group" : `Edit “${current?.name}”`}</DialogTitle>
          <DialogDescription>The name and icon show in everyone's sidebar.</DialogDescription>
        </DialogHeader>
        <Form {...form}>
          <form noValidate onSubmit={form.handleSubmit((v) => save.mutate(v))} className="grid gap-4">
            <Field control={form.control} name="name" label="Name" render={(f) => <Input {...f} autoFocus maxLength={60} />} />
            <Field control={form.control} name="icon" label="Icon" render={(f) => (
              <NativeSelect {...f}>
                {Object.entries(GROUP_ICONS).map(([key, { label }]) => <option key={key} value={key}>{label}</option>)}
              </NativeSelect>
            )} />
            <FormAlert error={save.error ? asApiError(save.error) : null} />
            <DialogFooter>
              <Button type="button" variant="secondary" onClick={onClose}>Cancel</Button>
              <Button type="submit" disabled={save.isPending}>{isNew ? "Create group" : "Save"}</Button>
            </DialogFooter>
          </form>
        </Form>
      </DialogContent>
    </Dialog>
  );
}

function AddDashboardsDialog({ group, candidates, onClose, onSave, saving }: {
  group: DashboardGroup | null;
  candidates: Candidate[];
  onClose: () => void;
  onSave: (g: DashboardGroup, ids: string[]) => void;
  saving: boolean;
}) {
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [q, setQ] = useState("");
  const [forGroup, setForGroup] = useState<string | null>(null);
  if (group && forGroup !== group.id) {
    setForGroup(group.id);
    setPicked(new Set(group.dashboards.map((d) => d.id)));
    setQ("");
  }
  const shown = candidates.filter((c) => c.title.toLowerCase().includes(q.trim().toLowerCase()));
  const toggle = (id: string, on: boolean) => setPicked((prev) => {
    const next = new Set(prev);
    if (on) next.add(id);
    else next.delete(id);
    return next;
  });
  const submit = () => {
    if (!group) return;
    const kept = group.dashboards.map((d) => d.id).filter((id) => picked.has(id));
    const added = candidates.map((c) => c.id).filter((id) => picked.has(id) && !kept.includes(id));
    onSave(group, [...kept, ...added]);
  };
  return (
    <Dialog open={group !== null} onOpenChange={(o) => { if (!o) { setForGroup(null); onClose(); } }}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>Dashboards in “{group?.name}”</DialogTitle>
          <DialogDescription>Tick the dashboards to show in this group. New ones go to the end; you can reorder them after.</DialogDescription>
        </DialogHeader>
        <label htmlFor="group-pick-search" className="sr-only">Find a dashboard</label>
        <Input id="group-pick-search" placeholder="Find a dashboard" value={q} onChange={(e) => setQ(e.target.value)} />
        <ul className="grid max-h-80 gap-1 overflow-y-auto" aria-label="Dashboards you can open">
          {shown.length === 0 && <li className="px-2 py-1 text-ink-muted">No dashboards match.</li>}
          {shown.map((c) => (
            <li key={c.id}>
              <label className="flex min-w-0 cursor-pointer items-center gap-2 rounded-md px-2 py-1.5 hover:bg-surface-sunken">
                <Checkbox checked={picked.has(c.id)} onCheckedChange={(v) => toggle(c.id, v === true)} />
                <span className="min-w-0 flex-1 truncate">{c.title}</span>
                <span className="shrink-0 text-small text-ink-muted">{c.note}</span>
              </label>
            </li>
          ))}
        </ul>
        <DialogFooter>
          <Button type="button" variant="secondary" onClick={onClose}>Cancel</Button>
          <Button type="button" disabled={saving} onClick={submit}>Save ({picked.size})</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

/** "Add to group…" from a dashboard row: tick the groups (that this person may change) to put it in. */
export function AddToGroupDialog({ dashboard, onClose }: { dashboard: { id: string; title: string } | null; onClose: () => void }) {
  const groups = useDashboardGroups(dashboard !== null);
  const editable = (groups.data?.items ?? []).filter((g) => g.can_edit);
  const [picked, setPicked] = useState<Set<string> | null>(null);
  const inGroups = new Set(editable.filter((g) => dashboard && g.dashboards.some((d) => d.id === dashboard.id)).map((g) => g.id));
  const chosen = picked ?? inGroups;
  const save = useGroupMutation((ids: string[]) => api(`/api/v1/dashboards/${dashboard!.id}/groups`, { method: "PUT", body: { group_ids: ids } }), "Groups saved.");
  const close = () => { setPicked(null); onClose(); };
  return (
    <Dialog open={dashboard !== null} onOpenChange={(o) => !o && close()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Add “{dashboard?.title}” to groups</DialogTitle>
          <DialogDescription>Groups you can change. People see it in a group only if they can open it.</DialogDescription>
        </DialogHeader>
        {groups.isLoading ? <Skeleton className="h-20" /> : editable.length === 0 ? (
          <p className="text-ink-muted">You have no groups to change yet. Create one on the Groups tab.</p>
        ) : (
          <ul className="grid max-h-80 gap-1 overflow-y-auto" aria-label="Groups">
            {editable.map((g) => {
              const Icon = groupIcon(g.icon);
              return (
                <li key={g.id}>
                  <label className="flex cursor-pointer items-center gap-2 rounded-md px-2 py-1.5 hover:bg-surface-sunken">
                    <Checkbox checked={chosen.has(g.id)} onCheckedChange={(v) => {
                      const next = new Set(chosen);
                      if (v === true) next.add(g.id);
                      else next.delete(g.id);
                      setPicked(next);
                    }} />
                    <Icon aria-hidden className="size-4 text-ink-muted" />
                    <span className="truncate">{g.name}</span>
                  </label>
                </li>
              );
            })}
          </ul>
        )}
        <DialogFooter>
          <Button type="button" variant="secondary" onClick={close}>Cancel</Button>
          <Button type="button" disabled={save.isPending || editable.length === 0}
            onClick={() => save.mutate([...chosen], { onSuccess: close })}>Save</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
