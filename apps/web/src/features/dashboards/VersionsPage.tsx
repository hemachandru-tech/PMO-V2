import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, RotateCcw } from "lucide-react";
import { useState } from "react";
import { Link, useLocation, useNavigate, useParams } from "react-router-dom";
import { toast } from "sonner";

import { api, type ApiError } from "../../api/client";
import type { DashboardDetail, Prechecks, PublishOut, Version } from "../../api/authoring";
import { GROUPS_KEY } from "../../app/shell/dashboardGroups";
import { Alert, FormAlert } from "../../components/ui/alert";
import { Badge } from "../../components/ui/badge";
import { Button } from "../../components/ui/button";
import { Card } from "../../components/ui/card";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "../../components/ui/dialog";
import { EmptyState } from "../../components/ui/empty-state";
import { Label } from "../../components/ui/label";
import { Skeleton } from "../../components/ui/skeleton";
import { StatusBadge } from "../../components/ui/status-badge";
import { Textarea } from "../../components/ui/textarea";
import { asApiError } from "../../lib/api-errors";
import { cn } from "../../lib/utils";

const STATE = {
  live: { label: "Live", variant: "certified" },
  superseded: { label: "Superseded", variant: "neutral" },
  withdrawn: { label: "Withdrawn", variant: "stale" },
} as const;

const when = (iso: string) => new Date(iso).toLocaleString([], { day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" });

type Confirm = { kind: "restore"; version: Version } | { kind: "unpublish" };

/**
 * Version history as a timeline (FEAT-040; RULE-016; US-142): every publish, restore and withdrawal with who, when, for
 * which roles, the release note and what changed. Admins restore an earlier version (a new version with its content,
 * after the checks run again) or unpublish the live one; both ask first.
 */
export function VersionsPage() {
  const { id } = useParams();
  const qc = useQueryClient();
  const navigate = useNavigate();
  const published = (useLocation().state as { published?: PublishOut } | null)?.published;
  const d = useQuery({ queryKey: ["dashboard-draft", id], queryFn: () => api<DashboardDetail>(`/api/v1/dashboards/${id}`) });
  const v = useQuery({ queryKey: ["versions", id], queryFn: () => api<{ items: Version[]; can_restore: boolean }>(`/api/v1/dashboards/${id}/versions`) });
  const [confirm, setConfirm] = useState<Confirm | null>(null);

  if (d.isLoading || v.isLoading) return <div className="tl grid gap-4" aria-busy="true"><Skeleton className="h-10 w-1/3" /><Skeleton className="h-96" /></div>;
  if (d.error || v.error) return <div className="tl"><FormAlert error={asApiError(d.error ?? v.error)} /></div>;
  const dash = d.data!;
  const { items, can_restore } = v.data!;
  const live = items.find((x) => x.state === "live");
  const done = () => {
    void qc.invalidateQueries({ queryKey: ["versions", id] });
    void qc.invalidateQueries({ queryKey: ["dashboard-draft", id] });
    void qc.invalidateQueries({ queryKey: [GROUPS_KEY] });
    navigate(".", { replace: true, state: null });
  };

  return (
    <div className="tl grid gap-4">
      {published && (
        <Alert>
          <div className="flex flex-wrap items-center justify-between gap-2">
            <p>Published version {published.version} to {published.roles.map((r) => r.name).join(" and ")}. {published.notified} {published.notified === 1 ? "person was" : "people were"} notified.</p>
            <Button asChild size="sm" variant="secondary"><Link to={`/dashboards/${id}`}>View dashboard</Link></Button>
          </div>
        </Alert>
      )}
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div className="grid gap-1">
          <Button asChild variant="ghost" size="sm" className="w-fit"><Link to={`/dashboards/${id}/edit`}><ArrowLeft aria-hidden />Back to the composer</Link></Button>
          <h1 className="text-title">{dash.title}</h1>
          <p className="text-ink-muted">Version history · every publish, restore and withdrawal is kept and audited.</p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button asChild variant="secondary"><Link to={`/dashboards/${id}/edit`}>Open draft</Link></Button>
          {can_restore && live && <Button variant="danger" onClick={() => setConfirm({ kind: "unpublish" })}>Unpublish</Button>}
        </div>
      </header>

      <div className="grid items-start gap-4 lg:grid-cols-[minmax(0,1fr)_20rem]">
        {items.length === 0 ? (
          <EmptyState title="Not published yet">Versions appear here after the first publish.{dash.reports.length > 0 && <> <Link to={`/dashboards/${id}/publish`}>{dash.can_publish ? "Publish it" : "Request publish"}</Link>.</>}</EmptyState>
        ) : (
          <ol className="grid" aria-label="Versions, newest first">
            {items.map((x, i) => (
              <li key={x.id} className="relative grid grid-cols-[1.5rem_minmax(0,1fr)] gap-3 pb-4">
                {i < items.length - 1 && <span aria-hidden className="absolute left-[0.6875rem] top-6 bottom-0 w-px bg-line" />}
                <span aria-hidden className={cn("mt-1.5 size-3.5 justify-self-center rounded-full border-2", x.state === "live" ? "border-accent bg-accent" : "border-line-strong bg-surface-raised")} />
                <Card className={cn("grid gap-2 p-4", x.state === "live" && "border-accent")}>
                  <div className="flex flex-wrap items-center gap-2">
                    <h2 className="text-heading">Version {x.version}</h2>
                    <Badge variant={STATE[x.state].variant}>{STATE[x.state].label}</Badge>
                    {x.restored_from && <Badge variant="outline"><RotateCcw aria-hidden />Restored from version {x.restored_from}</Badge>}
                    <span className="ml-auto flex gap-1">
                      {x.state === "live" && <Button asChild variant="ghost" size="sm"><Link to={`/dashboards/${id}`}>View</Link></Button>}
                      {can_restore && x.state !== "live" && <Button variant="secondary" size="sm" onClick={() => setConfirm({ kind: "restore", version: x })}>Restore</Button>}
                    </span>
                  </div>
                  <p className="text-small text-ink-muted">
                    Published <time dateTime={x.published_at}>{when(x.published_at)}</time> by {x.published_by}
                    {x.requested_by ? ` · requested by ${x.requested_by}` : ""} · {x.roles.join(", ") || "no roles"}
                  </p>
                  {x.note && <blockquote className="border-l-2 border-line pl-3">“{x.note}”</blockquote>}
                  {x.change_summary && <p className="text-small"><span className="text-ink-muted">Changes:</span> {x.change_summary}</p>}
                </Card>
              </li>
            ))}
          </ol>
        )}
        <Card className="grid gap-3 p-4 text-small">
          <h2 className="text-heading">How restore works</h2>
          <p>Restoring an earlier version publishes a new version with its content. The checks run again first; the current version stays in history.</p>
          <p>If a definition in it was merged or retired since, the current certified one is used and you see a note.</p>
          <h2 className="text-heading">Unpublish</h2>
          <p>Withdraws the live version from all roles. History and the draft stay; you can publish again later.</p>
          {!can_restore && <p className="text-ink-muted">Only an Admin can restore or unpublish.</p>}
        </Card>
      </div>

      <ConfirmDialog dashboardId={id!} confirm={confirm} next={(items[0]?.version ?? 0) + 1} onClose={() => setConfirm(null)} onDone={(msg) => { setConfirm(null); toast(msg); done(); }} />
    </div>
  );
}

function ConfirmDialog({ dashboardId, confirm, next, onClose, onDone }: {
  dashboardId: string;
  confirm: Confirm | null;
  next: number;
  onClose: () => void;
  onDone: (message: string) => void;
}) {
  const [note, setNote] = useState("");
  const [error, setError] = useState<ApiError | null>(null);
  const [failed, setFailed] = useState<Prechecks | null>(null);
  const [busy, setBusy] = useState(false);
  const close = () => { setNote(""); setError(null); setFailed(null); onClose(); };
  const run = async () => {
    if (!confirm) return;
    setBusy(true);
    setError(null);
    setFailed(null);
    try {
      if (confirm.kind === "unpublish") {
        await api(`/api/v1/dashboards/${dashboardId}/unpublish`, { method: "POST" });
        onDone("Unpublished. Readers no longer see this dashboard.");
      } else {
        const out = await api<PublishOut & { notes: string[] }>(`/api/v1/dashboards/${dashboardId}/versions/${confirm.version.version}/restore`, { method: "POST", body: { note: note.trim() || null } });
        onDone(`Restored version ${confirm.version.version} as version ${out.version}.${out.notes.length ? ` ${out.notes.join(" ")}` : ""}`);
      }
      setNote("");
    } catch (e) {
      const err = asApiError(e);
      if (err.code === "precheck_failed") setFailed(err.details as unknown as Prechecks);
      setError(err);
    } finally {
      setBusy(false);
    }
  };
  return (
    <Dialog open={confirm !== null} onOpenChange={(o) => !o && close()}>
      <DialogContent>
        {confirm?.kind === "unpublish" ? (
          <DialogHeader>
            <DialogTitle>Unpublish this dashboard?</DialogTitle>
            <DialogDescription>Readers lose access at once, until you publish again. History and the draft stay.</DialogDescription>
          </DialogHeader>
        ) : confirm && (
          <>
            <DialogHeader>
              <DialogTitle>Restore version {confirm.version.version}?</DialogTitle>
              <DialogDescription>This publishes version {next} with version {confirm.version.version}'s content to {confirm.version.roles.join(", ")}. The checks run again first.</DialogDescription>
            </DialogHeader>
            <div className="grid gap-1">
              <Label htmlFor="restore-note">Release note (optional)</Label>
              <Textarea id="restore-note" value={note} maxLength={500} rows={2} onChange={(e) => setNote(e.target.value)} placeholder={`Restored version ${confirm.version.version}`} />
            </div>
          </>
        )}
        {failed ? (
          <div className="grid gap-2" role="alert">
            <p className="text-body-strong">{error?.message}</p>
            <ul className="grid gap-1 text-small">
              {failed.checks.filter((c) => !c.passed).map((c) => (
                <li key={c.check} className="grid gap-0.5">
                  <StatusBadge status="danger">{c.label}</StatusBadge>
                  {c.items.map((it, i) => <span key={i} className="pl-2">{it.title ? `“${it.title}”: ` : ""}{it.message}</span>)}
                </li>
              ))}
            </ul>
          </div>
        ) : <FormAlert error={error} />}
        <DialogFooter>
          <Button variant="secondary" onClick={close}>Cancel</Button>
          {confirm?.kind === "unpublish"
            ? <Button variant="danger" disabled={busy} onClick={() => void run()}>Unpublish</Button>
            : <Button disabled={busy} onClick={() => void run()}>Restore as version {next}</Button>}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
