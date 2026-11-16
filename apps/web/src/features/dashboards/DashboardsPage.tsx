import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation, useQuery } from "@tanstack/react-query";
import { FolderPlus, LayoutDashboard, Plus, Sparkles } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { useForm } from "react-hook-form";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { z } from "zod";

import { api } from "../../api/client";
import type { DashboardDetail, DashboardListItem } from "../../api/authoring";
import { can, useMe } from "../../app/me";
import { ContentBadge } from "../../components/ContentBadge";
import { formatRelative } from "../../components/format";
import { FormAlert } from "../../components/ui/alert";
import { Badge } from "../../components/ui/badge";
import { Button } from "../../components/ui/button";
import { DataTable, type DataColumn } from "../../components/ui/data-table";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "../../components/ui/dialog";
import { Field, Form } from "../../components/ui/form";
import { Input } from "../../components/ui/input";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "../../components/ui/tabs";
import { asApiError } from "../../lib/api-errors";
import { AddToGroupDialog, GroupsPanel } from "./GroupsPanel";

/**
 * Dashboards I own or that were shared with me; Admins (publishers) also see every user dashboard.
 * With `shared`, only dashboards colleagues shared with me (Explore → Shared with me, US-137). On the shared
 * DataTable since US-148, when the old stylesheet was removed. With `dashboards.group` (Admin, Creator) a Groups tab
 * and an "Add to group…" action per row manage the sidebar's dashboard groups (ENH-009, US-170).
 */
export function DashboardsPage({ shared = false }: { shared?: boolean }) {
  const me = useMe().data;
  const navigate = useNavigate();
  const publisher = can(me, "dashboards.publish") && !shared;
  const list = useQuery({
    queryKey: ["dashboards", publisher],
    queryFn: () => api<{ items: DashboardListItem[] }>(`/api/v1/dashboards${publisher ? "?all=true" : ""}`),
    select: (data) => (shared ? { items: data.items.filter((d) => !d.mine) } : data),
  });
  const [creating, setCreating] = useState(false);
  const [grouping, setGrouping] = useState<DashboardListItem | null>(null);
  const [params, setParams] = useSearchParams();
  const canGroup = !shared && can(me, "dashboards.group");
  const open = (d: DashboardListItem) =>
    shared ? (d.live_version ? `/dashboards/${d.id}` : `/dashboards/${d.id}/draft`) : d.mine || publisher ? `/dashboards/${d.id}/edit` : `/dashboards/${d.id}/draft`;
  const columns = useMemo<DataColumn<DashboardListItem>[]>(() => [
    {
      id: "title", header: "Title", alwaysVisible: true, value: (d) => d.title,
      cell: (d) => <Link to={open(d)} className="font-medium text-link hover:underline focus-visible:focus-ring">{d.title}</Link>,
    },
    { id: "reports", header: "Reports", numeric: true, value: (d) => d.reports },
    {
      id: "live", header: "Live", value: (d) => (d.live_version ? `v${d.live_version}` : "Not published"), filter: "select",
      cell: (d) => (d.live_version
        ? <Link to={`/dashboards/${d.id}`} className="focus-visible:focus-ring"><Badge variant="certified">Version {d.live_version}</Badge></Link>
        : <ContentBadge badge="draft" />),
    },
    { id: "owner", header: "Owner", value: (d) => (d.mine ? "Me" : d.owner_name), filter: "select" },
    { id: "updated_at", header: "Updated", date: true, value: (d) => d.updated_at, cell: (d) => formatRelative(d.updated_at) },
    ...(canGroup ? [{
      id: "groups", header: "Groups", sortable: false, alwaysVisible: true,
      cell: (d: DashboardListItem) => (
        <Button variant="ghost" size="sm" onClick={(e) => { e.stopPropagation(); setGrouping(d); }} aria-label={`Add ${d.title} to a group`}>
          <FolderPlus aria-hidden />Add to group…
        </Button>
      ),
    }] : []),
  ], [shared, publisher, canGroup]); // eslint-disable-line react-hooks/exhaustive-deps
  const creator = !shared && can(me, "dashboards.create");
  const items = list.data?.items ?? [];
  const table = (
    <DataTable data={items} columns={columns} label={shared ? "Shared with me" : "Dashboards"} getRowId={(d) => d.id} loading={list.isLoading}
      error={list.error && <FormAlert error={asApiError(list.error)} />}
      empty={shared
        ? { title: "Nothing shared with you yet", body: "When a colleague shares a dashboard with you, it appears here." }
        : { title: "No dashboards yet", body: "Start a dashboard, then add reports to it.",
            action: creator ? <Button variant="secondary" onClick={() => setCreating(true)}><LayoutDashboard aria-hidden />New dashboard</Button> : undefined }}
      search={items.length > 8} onRowActivate={(d) => navigate(open(d))} />
  );
  return (
    <div className="tl grid gap-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div className="grid gap-1">
          <h1 className="text-title">{shared ? "Shared with me" : "Dashboards"}</h1>
          <p className="text-ink-muted">
            {shared ? "Dashboards colleagues shared with you. You see them within your own data scope." : "Dashboards you compose from reports, then publish to roles or share."}
          </p>
        </div>
        {creator && (
          <div className="flex gap-2">
            <Button asChild variant="secondary"><Link to="/dashboards/ai"><Sparkles aria-hidden />Plan with AI</Link></Button>
            <Button onClick={() => setCreating(true)}><Plus aria-hidden />New dashboard</Button>
          </div>
        )}
      </div>
      {canGroup ? (
        <Tabs value={params.get("tab") === "groups" ? "groups" : "all"}
          onValueChange={(v) => setParams(v === "groups" ? { tab: "groups" } : {}, { replace: true })}>
          <TabsList aria-label="Dashboards">
            <TabsTrigger value="all">All dashboards</TabsTrigger>
            <TabsTrigger value="groups">Groups</TabsTrigger>
          </TabsList>
          <TabsContent value="all">{table}</TabsContent>
          <TabsContent value="groups"><GroupsPanel list={items} /></TabsContent>
        </Tabs>
      ) : table}
      <NewDashboardDialog open={creating} onOpenChange={setCreating} />
      {canGroup && <AddToGroupDialog dashboard={grouping} onClose={() => setGrouping(null)} />}
    </div>
  );
}

const newDashboard = z.object({ title: z.string().trim().min(1, "Give the dashboard a title.").max(200, "Use 200 characters or fewer.") });

function NewDashboardDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (open: boolean) => void }) {
  const navigate = useNavigate();
  const form = useForm<z.infer<typeof newDashboard>>({ resolver: zodResolver(newDashboard), defaultValues: { title: "New dashboard" }, mode: "onTouched" });
  const create = useMutation({
    mutationFn: (title: string) => api<DashboardDetail>("/api/v1/dashboards", { method: "POST", body: { title } }),
    onSuccess: (d) => navigate(`/dashboards/${d.id}/edit`),
  });
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>New dashboard</DialogTitle>
          <DialogDescription>It stays a draft only you can see until you share it or it is published.</DialogDescription>
        </DialogHeader>
        <Form {...form}>
          <form noValidate onSubmit={form.handleSubmit((v) => create.mutate(v.title))} className="grid gap-4">
          <Field control={form.control} name="title" label="Title" render={(f) => <Input {...f} autoFocus />} />
          <FormAlert error={create.error ? asApiError(create.error) : null} />
          <DialogFooter>
            <Button type="button" variant="secondary" onClick={() => onOpenChange(false)}>Cancel</Button>
            <Button type="submit" disabled={create.isPending}>Create and open</Button>
          </DialogFooter>
          </form>
        </Form>
      </DialogContent>
    </Dialog>
  );
}

/** /dashboards/new?report=… creates a dashboard from one report and opens the composer. */
export function NewDashboardPage() {
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const done = useRef(false);
  useEffect(() => {
    if (done.current) return;
    done.current = true;
    const report = params.get("report");
    void api<DashboardDetail>("/api/v1/dashboards", {
      method: "POST", body: { title: "New dashboard", report_ids: report ? [report] : [] },
    }).then((d) => navigate(`/dashboards/${d.id}/edit`, { replace: true }));
  }, []); // eslint-disable-line react-hooks/exhaustive-deps
  return <p className="tl text-ink-muted" aria-busy="true">Creating the dashboard…</p>;
}
