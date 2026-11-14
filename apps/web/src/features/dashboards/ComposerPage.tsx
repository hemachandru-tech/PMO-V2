import { useQuery, useQueryClient } from "@tanstack/react-query";
import { CircleAlert, Cloud, Eye, History, Loader2, PackagePlus, Send, Share2 } from "lucide-react";
import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { toast } from "sonner";

import { api } from "../../api/client";
import type { Dashboard } from "../../api/app";
import type { DashboardDetail, ReportListItem, SharedFilter, StarterReport } from "../../api/authoring";
import { ContentBadge } from "../../components/ContentBadge";
import { formatRelative } from "../../components/format";
import { ShareDialog } from "../../components/ShareDialog";
import { Alert, FormAlert } from "../../components/ui/alert";
import { Badge } from "../../components/ui/badge";
import { Button } from "../../components/ui/button";
import { Card } from "../../components/ui/card";
import { RadioGroup, RadioGroupItem } from "../../components/ui/radio-group";
import { Skeleton } from "../../components/ui/skeleton";
import { asApiError } from "../../lib/api-errors";
import { DashboardBody } from "../viewer/DashboardPage";
import { ReportOrder, type OrderItem } from "./composer/ReportOrder";
import { SharedFilters } from "./composer/SharedFilters";
import { can, useMe } from "../../app/me";
import { SaveAsBlueprint } from "../admin/packs/reports/SaveFromDialog";

type SaveState = { kind: "idle" } | { kind: "saving" } | { kind: "saved"; at: Date } | { kind: "error" };

const LAYOUTS = [
  { value: "tabs", label: "Tabs", hint: "One report per tab" },
  { value: "grouped", label: "Grouped", hint: "All reports on one page" },
] as const;

/**
 * Dashboard composer (FEAT-038; SCR-08; US-142): reports in order (dnd-kit, keyboard too), layout, shared filters with
 * reach, and a live preview of the draft in the Creator's own scope. Every change saves at once and the preview
 * re-renders. Publishing is a separate guided page (`/dashboards/:id/publish`); history is at `/dashboards/:id/versions`.
 */
export function ComposerPage() {
  const { id } = useParams();
  const qc = useQueryClient();
  const key = ["dashboard-draft", id];
  const d = useQuery({ queryKey: key, queryFn: () => api<DashboardDetail>(`/api/v1/dashboards/${id}`) });
  const reports = useQuery({ queryKey: ["reports"], queryFn: () => api<{ items: ReportListItem[] }>("/api/v1/reports") });
  const starters = useQuery({ queryKey: ["starter-reports"], queryFn: () => api<{ items: StarterReport[] }>("/api/v1/starter-reports") });
  const preview = useQuery({ queryKey: ["dashboard", id, true], queryFn: () => api<Dashboard>(`/api/v1/dashboards/${id}/view`) });
  const [save, setSave] = useState<SaveState>({ kind: "idle" });
  const [sharing, setSharing] = useState(false);
  const me = useMe().data;

  if (d.isLoading) return <ComposerSkeleton />;
  if (d.error) return <div className="tl"><FormAlert error={asApiError(d.error)} /></div>;
  const dash = d.data!;
  const base = `/api/v1/dashboards/${dash.id}`;

  const commit = async (path: string, method: "PATCH" | "PUT", body: unknown, label: string, optimistic?: DashboardDetail) => {
    const before = qc.getQueryData<DashboardDetail>(key);
    if (optimistic) qc.setQueryData(key, optimistic);
    setSave({ kind: "saving" });
    try {
      qc.setQueryData(key, await api<DashboardDetail>(path, { method, body }));
      setSave({ kind: "saved", at: new Date() });
      toast(label);
      void qc.invalidateQueries({ queryKey: ["dashboard", id, true] });
    } catch (e) {
      if (before) qc.setQueryData(key, before);
      setSave({ kind: "error" });
      toast.error(asApiError(e, "That change could not be saved.").message);
    }
  };
  const order = (items: OrderItem[], label: string) => {
    const byId = new Map(dash.reports.map((r) => [r.id, r]));
    const optimistic = items.every((i) => byId.has(i.report_id))
      ? { ...dash, reports: items.map((i) => ({ ...byId.get(i.report_id)!, group_title: i.group_title ?? null })) }
      : undefined;
    void commit(`${base}/reports`, "PUT", { items }, label, optimistic);
  };
  const setFilters = (filters: SharedFilter[], label: string) => void commit(base, "PATCH", { shared_filters: filters }, label);
  const request = dash.publish_request;
  const publishTo = `/dashboards/${dash.id}/publish${request?.status === "open" && dash.can_publish ? `?request=${request.id}` : ""}`;

  return (
    <div className="tl grid gap-4">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex min-w-0 flex-wrap items-center gap-2">
          {dash.can_edit ? <TitleInput title={dash.title} onRename={(t) => void commit(base, "PATCH", { title: t }, `Renamed to ${t}`)} />
            : <h1 className="text-title">{dash.title}</h1>}
          <ContentBadge badge="draft" />
          {dash.live
            ? <Badge variant="certified" title={`Published by ${dash.live.published_by}`}>Live: version {dash.live.version} for {dash.live.roles.map((r) => r.name).join(", ")}</Badge>
            : <Badge variant="outline">Not published</Badge>}
          {dash.draft_differs_from_live && <Badge variant="stale">Draft has unpublished changes</Badge>}
          <span className="text-small text-ink-muted">{dash.reports.length} {dash.reports.length === 1 ? "report" : "reports"} · owner {dash.owner.name}</span>
          <SaveIndicator state={save} />
        </div>
        <div className="flex flex-wrap items-center gap-1" role="toolbar" aria-label="Dashboard">
          <Button asChild variant="ghost" size="sm"><Link to={`/dashboards/${dash.id}/draft`}><Eye aria-hidden />Full preview</Link></Button>
          {dash.can_edit && <Button variant="ghost" size="sm" onClick={() => setSharing(true)}><Share2 aria-hidden />Share</Button>}
          <Button asChild variant="ghost" size="sm"><Link to={`/dashboards/${dash.id}/versions`}><History aria-hidden />Version history</Link></Button>
          {can(me, "packs.author") && dash.reports.length > 0 && (
            <SaveAsBlueprint kind="dashboard" sourceId={dash.id}>
              {(open) => <Button variant="ghost" size="sm" onClick={open}><PackagePlus aria-hidden />Save as starter dashboard</Button>}
            </SaveAsBlueprint>
          )}
          {dash.reports.length === 0 ? (
            <Button size="sm" disabled title="Add at least one report first"><Send aria-hidden />{dash.can_publish ? "Publish" : "Request publish"}</Button>
          ) : (
            <Button asChild size="sm">
              <Link to={publishTo}><Send aria-hidden />{dash.can_publish ? "Publish" : request?.status === "open" ? "Update request" : "Request publish"}</Link>
            </Button>
          )}
        </div>
      </header>

      <RequestBanner dashboard={dash} />
      {(dash.starter_removed ?? []).length > 0 && (
        <Alert variant="warn">
          <p><strong>Starter removed — republish to apply.</strong> An Admin deleted {dash.starter_removed!.map((r) => r.title).join(", ")}. This draft no longer has it; the live version shows it until you republish.</p>
        </Alert>
      )}

      <div className="grid items-start gap-4 xl:grid-cols-[24rem_minmax(0,1fr)]">
        <div className="grid min-w-0 gap-4">
          <Card className="grid min-w-0 gap-5 p-4">
            <ReportOrder dashboard={dash} reports={reports.data?.items ?? []} starters={starters.data?.items ?? []} onOrder={order} />
            <section aria-labelledby="layout-heading" className="grid gap-2">
              <h2 id="layout-heading" className="text-heading">Layout</h2>
              <RadioGroup value={dash.layout} disabled={!dash.can_edit} aria-labelledby="layout-heading" className="grid grid-cols-2 gap-2"
                onValueChange={(v) => void commit(base, "PATCH", { layout: v }, v === "tabs" ? "Layout: tabs" : "Layout: grouped on one page")}>
                {LAYOUTS.map((l) => (
                  <label key={l.value} className="flex cursor-pointer items-start gap-2 rounded-md border border-line p-3 has-data-[state=checked]:border-accent has-data-[state=checked]:bg-accent-soft">
                    <RadioGroupItem value={l.value} className="mt-0.5" />
                    <span className="grid gap-0.5"><span className="font-medium">{l.label}</span><span className="text-small text-ink-muted">{l.hint}</span></span>
                  </label>
                ))}
              </RadioGroup>
            </section>
          </Card>
          <Card className="min-w-0 p-4"><SharedFilters dashboard={dash} onChange={setFilters} /></Card>
        </div>

        <Card className="grid min-w-0 gap-3 p-4" aria-labelledby="preview-heading">
          <div className="flex flex-wrap items-baseline justify-between gap-2">
            <h2 id="preview-heading" className="text-heading">Preview</h2>
            <span className="text-small text-ink-muted">As you see it, in your own scope. Use Publish to preview as a role.</span>
          </div>
          {preview.isLoading ? <Skeleton className="h-64" />
            : preview.error ? <FormAlert error={asApiError(preview.error)} />
              : preview.data && (
                <div className={preview.isFetching ? "opacity-60 transition-opacity" : undefined} aria-busy={preview.isFetching}>
                  <DashboardBody key={`${preview.data.layout}:${(preview.data.tabs ?? []).map((t) => t.id).join(",")}`} dashboard={preview.data} draft preview />
                </div>
              )}
        </Card>
      </div>

      {sharing && <ShareDialog kind="dashboard" id={dash.id} title={dash.title} onClose={() => setSharing(false)} />}
    </div>
  );
}

/** Where the publish request stands: waiting (with a link for Admins), or returned with the Admin's note. */
function RequestBanner({ dashboard: d }: { dashboard: DashboardDetail }) {
  const r = d.publish_request;
  if (!r || (r.status !== "open" && r.status !== "declined")) return null;
  // A publish after the request was returned makes the note old news.
  if (r.status === "declined" && d.live && r.decided_at && d.live.published_at > r.decided_at) return null;
  const roles = r.roles.map((x) => x.name).join(", ") || "its audience";
  if (r.status === "declined") {
    return (
      <Alert variant="warn">
        <p><strong>{r.decided_by} returned this {formatRelative(r.decided_at)}:</strong> “{r.decision_note}”</p>
        <p className="text-small">Change the dashboard and request publish again.</p>
      </Alert>
    );
  }
  if (d.can_publish) {
    return (
      <Alert>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <p><strong>{r.requested_by.name}</strong> asked {formatRelative(r.created_at)} to publish this to {roles}.{r.note ? ` “${r.note}”` : ""}</p>
          <Button asChild size="sm"><Link to={`/dashboards/${d.id}/publish?request=${r.id}`}>Review the request</Link></Button>
        </div>
      </Alert>
    );
  }
  return <Alert><p>You asked {formatRelative(r.created_at)} to publish this to {roles}. An Admin will review it; you'll get a notification.</p></Alert>;
}

function TitleInput({ title: initial, onRename }: { title: string; onRename: (t: string) => void }) {
  const [title, setTitle] = useState(initial);
  useEffect(() => setTitle(initial), [initial]);
  return (
    <h1 className="min-w-0">
      <input aria-label="Dashboard title" value={title} maxLength={200} onChange={(e) => setTitle(e.target.value)}
        onBlur={() => { const t = title.trim(); if (t && t !== initial) onRename(t); else setTitle(initial); }}
        onKeyDown={(e) => { if (e.key === "Enter") e.currentTarget.blur(); if (e.key === "Escape") { setTitle(initial); e.currentTarget.blur(); } }}
        size={Math.max(8, Math.min(48, title.length + 1))}
        className="max-w-full rounded-sm bg-transparent px-1 text-title hover:bg-surface-sunken focus-visible:focus-ring" />
    </h1>
  );
}

function SaveIndicator({ state }: { state: SaveState }) {
  if (state.kind === "idle") return null;
  const time = (d: Date) => d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  const [icon, text] =
    state.kind === "saving" ? [<Loader2 key="i" aria-hidden className="size-4 animate-spin motion-reduce:animate-none" />, "Saving…"]
      : state.kind === "error" ? [<CircleAlert key="i" aria-hidden className="size-4 text-status-danger" />, "Not saved"]
        : [<Cloud key="i" aria-hidden className="size-4" />, `Saved ${time(state.at)}`];
  return <span role="status" aria-live="polite" className="flex items-center gap-1 text-small text-ink-muted">{icon}{text}</span>;
}

function ComposerSkeleton() {
  return (
    <div className="tl grid gap-4" aria-busy="true" aria-label="Loading the dashboard">
      <Skeleton className="h-10 w-1/3" />
      <div className="grid gap-4 xl:grid-cols-[24rem_minmax(0,1fr)]"><Skeleton className="h-96" /><Skeleton className="h-96" /></div>
    </div>
  );
}
