import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Copy, KeyRound, RefreshCw, UserRoundCog } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { api } from "../../../api/client";
import { getConfig } from "../../../app/config";
import { PeoplePicker, type Person } from "../../../components/PeoplePicker";
import { formatRelative } from "../../../components/format";
import { Alert, FormAlert } from "../../../components/ui/alert";
import { Badge } from "../../../components/ui/badge";
import { Button } from "../../../components/ui/button";
import { ConfirmDialog, type ConfirmRequest } from "../../../components/ui/confirm-dialog";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "../../../components/ui/dialog";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "../../../components/ui/table";
import { asApiError } from "../../../lib/api-errors";
import { useAppName } from "../../../app/branding";

type Owned = Record<"dashboards" | "reports" | "widgets" | "custom_sql" | "fields", number>;
interface FormerOwner { user: { id: string; display_name: string; status: string }; deprovisioned_at: string | null; owned: Owned }
interface Findings {
  fixed: { memberships_removed: number; sessions_revoked: number };
  roles_refreshed: number;
  tasks: { done: number; skipped: number; retrying: number; failed: number };
  unlinked_users: { count: number; items: { id: string; display_name: string; email: string | null; last_login_at: string | null }[] };
  deleted_groups_mapped: { id: string; display_name: string; external_group_id: string; mappings: number }[];
  deprovisioned_owners: FormerOwner[];
  failed_tasks: { id: string; user: { id: string; display_name: string }; attempts: number; last_error: string | null }[];
}
interface Run {
  id: string; trigger: "schedule" | "manual"; started_at: string; finished_at: string | null;
  outcome: "running" | "ok" | "drift" | "failed"; summary: Partial<Findings>; error: string | null;
}
export interface ProvisioningStatus {
  enabled: boolean;
  endpoint_path: string;
  token: { prefix: string; created_at: string; created_by: string | null; last_used_at: string | null } | null;
  idp_sessions_configured: boolean;
  last_activity_at: string | null;
  counts: { users: Record<"active" | "pending" | "suspended" | "deprovisioned", number>; groups: number; groups_deleted: number; requests_24h: number; errors_24h: number };
  recent: { at: string; method: string; resource: string; name: string | null; operation: string; status: number; outcome: "ok" | "error"; detail: string | null }[];
  tasks: { id: string; status: "pending" | "failed"; attempts: number; last_error: string | null; next_attempt_at: string; user: { id: string; display_name: string } }[];
  last_run: Run | null;
  runs: Run[];
  deprovisioned_owners: FormerOwner[];
}

const KEY = ["admin", "provisioning"];
const OUTCOME: Record<Run["outcome"], { label: string; variant: "certified" | "stale" | "neutral" | "outline" }> = {
  ok: { label: "In sync", variant: "certified" }, drift: { label: "Needs attention", variant: "stale" },
  failed: { label: "Failed", variant: "stale" }, running: { label: "Running", variant: "outline" },
};
const OWNED_LABELS: [keyof Owned, string, string][] = [
  ["dashboards", "dashboard", "dashboards"], ["reports", "report", "reports"], ["widgets", "widget", "widgets"],
  ["custom_sql", "custom SQL query", "custom SQL queries"], ["fields", "field", "fields"],
];
const ownedText = (o: Owned) => OWNED_LABELS.filter(([k]) => o[k] > 0).map(([k, one, many]) => `${o[k]} ${o[k] === 1 ? one : many}`).join(", ");

/**
 * Entra provisioning (US-118, US-119; FEAT-004): turn the SCIM endpoint on with a token shown once, see what Entra
 * sent and whether it worked, run Reconcile and act on what it finds, and hand a former user's content to someone
 * else (E-17). The hourly `scim_reconcile` job runs the same reconcile and notifies the Admins when findings change.
 */
export function ProvisioningPage() {
  const appName = useAppName();
  const qc = useQueryClient();
  const q = useQuery({ queryKey: KEY, queryFn: () => api<ProvisioningStatus>("/api/v1/admin/provisioning") });
  const [confirm, setConfirm] = useState<ConfirmRequest | null>(null);
  const [secret, setSecret] = useState<string | null>(null);
  const [reassign, setReassign] = useState<FormerOwner | null>(null);
  const refresh = () => void qc.invalidateQueries({ queryKey: KEY });

  const createToken = useMutation({
    mutationFn: () => api<{ token: string; rotated: boolean }>("/api/v1/admin/provisioning/token", { method: "POST" }),
    onSuccess: (r) => { setSecret(r.token); refresh(); },
    onError: (e) => toast.error(asApiError(e, "The token could not be created.").message),
  });
  const revokeToken = useMutation({
    mutationFn: () => api<void>("/api/v1/admin/provisioning/token", { method: "DELETE" }),
    onSuccess: () => { toast.success("Provisioning is off. Entra can no longer change users here."); refresh(); },
    onError: (e) => toast.error(asApiError(e, "Provisioning could not be turned off.").message),
  });
  const reconcile = useMutation({
    mutationFn: () => api<Run>("/api/v1/admin/provisioning/reconcile", { method: "POST" }),
    onSuccess: (r) => { toast.success(r.outcome === "ok" ? "Everything is in sync." : "Reconcile finished. Review what it found."); refresh(); },
    onError: (e) => toast.error(asApiError(e, "Reconcile could not run.").message),
  });

  if (q.error) return <FormAlert error={asApiError(q.error)} />;
  if (!q.data) return <p className="text-ink-muted">Loading provisioning status…</p>;
  const s = q.data;
  const endpoint = `${getConfig().apiBaseUrl.replace(/\/$/, "")}${s.endpoint_path}`;
  const findings = s.last_run?.summary;

  return (
    <div className="grid gap-6">
      <section aria-labelledby="scim-endpoint" className="grid gap-3 rounded-md border border-line bg-surface p-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="grid gap-1">
            <h2 id="scim-endpoint" className="flex items-center gap-2 text-heading">
              Entra provisioning <Badge variant={s.enabled ? "certified" : "outline"}>{s.enabled ? "On" : "Off"}</Badge>
            </h2>
            <p className="text-small text-ink-muted">
              Entra creates, updates and removes people and groups here. Removing someone in Entra ends their access in {appName}; their content stays.
            </p>
          </div>
          <div className="flex flex-wrap gap-2">
            {s.enabled ? (
              <>
                <Button variant="secondary" disabled={createToken.isPending} onClick={() => setConfirm({
                  title: "Rotate the provisioning token?",
                  body: "The current token stops working at once. Paste the new one into the Entra enterprise app's Secret Token before the next sync.",
                  confirm: "Rotate token", onConfirm: () => createToken.mutate(),
                })}><KeyRound aria-hidden />Rotate token…</Button>
                <Button variant="secondary" disabled={revokeToken.isPending} onClick={() => setConfirm({
                  title: "Turn provisioning off?",
                  body: `Entra's calls will be refused. People keep their current access; changes in Entra stop reaching ${appName} until you turn it on again.`,
                  confirm: "Turn off", danger: true, onConfirm: () => revokeToken.mutate(),
                })}>Turn off…</Button>
              </>
            ) : (
              <Button disabled={createToken.isPending} onClick={() => createToken.mutate()}><KeyRound aria-hidden />Turn on</Button>
            )}
          </div>
        </div>
        <dl className="grid gap-x-6 gap-y-2 text-small sm:grid-cols-2">
          <div className="grid gap-0.5"><dt className="text-ink-muted">Tenant URL</dt><dd className="break-all font-mono">{endpoint}</dd></div>
          <div className="grid gap-0.5">
            <dt className="text-ink-muted">Token</dt>
            <dd>{s.token ? <><span className="font-mono">{s.token.prefix}…</span> · created {formatRelative(s.token.created_at)}{s.token.created_by ? ` by ${s.token.created_by}` : ""} · {s.token.last_used_at ? `last used ${formatRelative(s.token.last_used_at)}` : "not used yet"}</> : "None"}</dd>
          </div>
        </dl>
        {s.enabled && !s.idp_sessions_configured && (
          <Alert variant="info">
            <p className="text-small">Signing people out of Keycloak is not configured. {appName} still refuses their tokens as soon as Entra suspends or removes them.</p>
          </Alert>
        )}
      </section>

      <section aria-labelledby="sync-counts" className="grid gap-2">
        <h2 id="sync-counts" className="sr-only">Provisioned people and groups</h2>
        <dl className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
          {([
            ["Active people", s.counts.users.active + s.counts.users.pending],
            ["Suspended", s.counts.users.suspended],
            ["Removed", s.counts.users.deprovisioned],
            ["Groups", s.counts.groups],
            ["Requests (24 h)", s.counts.requests_24h],
            ["Errors (24 h)", s.counts.errors_24h],
          ] as const).map(([label, n]) => (
            <div key={label} className="grid gap-0.5 rounded-md border border-line bg-surface p-3">
              <dt className="text-small text-ink-muted">{label}</dt>
              <dd className={`font-mono text-title tabular-nums ${label.startsWith("Errors") && n > 0 ? "text-status-danger" : ""}`}>{n}</dd>
            </div>
          ))}
        </dl>
        <p className="text-small text-ink-muted">{s.last_activity_at ? `Last request from Entra ${formatRelative(s.last_activity_at)}.` : "Entra has not called yet."}</p>
      </section>

      <section aria-labelledby="reconcile" className="grid gap-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="grid gap-0.5">
            <h2 id="reconcile" className="flex items-center gap-2 text-heading">
              Reconcile {s.last_run && <Badge variant={OUTCOME[s.last_run.outcome].variant}>{OUTCOME[s.last_run.outcome].label}</Badge>}
            </h2>
            <p className="text-small text-ink-muted">
              {s.last_run
                ? `Last run ${formatRelative(s.last_run.finished_at ?? s.last_run.started_at)} (${s.last_run.trigger === "manual" ? "by an Admin" : "hourly"}). `
                : "Not run yet. "}
              Retries failed sign-outs, fixes drift {appName} can fix and lists what needs you.
            </p>
          </div>
          <Button variant="secondary" disabled={reconcile.isPending} onClick={() => reconcile.mutate()}>
            <RefreshCw aria-hidden className={reconcile.isPending ? "animate-spin" : undefined} />Reconcile now
          </Button>
        </div>
        {s.last_run?.outcome === "failed" && <Alert variant="danger"><p>The last reconcile failed. It runs again within the hour.</p></Alert>}
        {findings && s.last_run?.outcome !== "failed" && <FindingsList f={findings} />}
      </section>

      {s.deprovisioned_owners.length > 0 && (
        <section aria-labelledby="former-owners" className="grid gap-2">
          <h2 id="former-owners" className="text-heading">Content owned by people who left</h2>
          <p className="text-small text-ink-muted">Their dashboards stay live and shared items stay readable. Give them a new owner so someone can keep them up to date.</p>
          <Table aria-label="Former owners">
            <TableHeader><TableRow><TableHead>Person</TableHead><TableHead>Removed</TableHead><TableHead>Owns</TableHead><TableHead><span className="sr-only">Actions</span></TableHead></TableRow></TableHeader>
            <TableBody>
              {s.deprovisioned_owners.map((o) => (
                <TableRow key={o.user.id}>
                  <TableCell className="font-medium">{o.user.display_name}</TableCell>
                  <TableCell>{formatRelative(o.deprovisioned_at)}</TableCell>
                  <TableCell>{ownedText(o.owned)}</TableCell>
                  <TableCell className="text-right">
                    <Button size="sm" variant="secondary" onClick={() => setReassign(o)} aria-label={`Reassign ${o.user.display_name}'s content`}>
                      <UserRoundCog aria-hidden />Reassign…
                    </Button>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </section>
      )}

      {s.tasks.length > 0 && (
        <section aria-labelledby="sign-outs" className="grid gap-2">
          <h2 id="sign-outs" className="text-heading">Sign-outs waiting</h2>
          <ul className="grid gap-1">
            {s.tasks.map((t) => (
              <li key={t.id} className="text-small">
                <Badge variant={t.status === "failed" ? "stale" : "outline"}>{t.status === "failed" ? "Gave up" : "Retrying"}</Badge>{" "}
                {t.user.display_name} · {t.attempts} {t.attempts === 1 ? "attempt" : "attempts"}{t.status === "pending" ? ` · next ${formatRelative(t.next_attempt_at)}` : ""}
                {t.last_error && <span className="text-ink-muted"> · {t.last_error}</span>}
              </li>
            ))}
          </ul>
        </section>
      )}

      <section aria-labelledby="recent-requests" className="grid gap-2">
        <h2 id="recent-requests" className="text-heading">Recent requests</h2>
        {s.recent.length === 0 ? <p className="text-small text-ink-muted">Nothing yet. Requests from Entra appear here, without their contents.</p> : (
          <Table aria-label="Recent provisioning requests">
            <TableHeader><TableRow><TableHead>When</TableHead><TableHead>Request</TableHead><TableHead>Who or what</TableHead><TableHead>Result</TableHead></TableRow></TableHeader>
            <TableBody>
              {s.recent.map((r, i) => (
                <TableRow key={`${r.at}-${i}`}>
                  <TableCell className="whitespace-nowrap">{formatRelative(r.at)}</TableCell>
                  <TableCell><span className="font-mono text-small">{r.method}</span> {r.resource} · {r.operation}</TableCell>
                  <TableCell>{r.name ?? "—"}</TableCell>
                  <TableCell>
                    {r.outcome === "ok" ? <Badge variant="certified">{r.status}</Badge> : <Badge variant="stale">{r.status}</Badge>}
                    {r.detail && <span className="ml-2 text-small text-ink-muted">{r.detail}</span>}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </section>

      <TokenDialog token={secret} endpoint={endpoint} onClose={() => setSecret(null)} />
      <ReassignDialog owner={reassign} onClose={() => setReassign(null)} onDone={refresh} />
      <ConfirmDialog request={confirm} onClose={() => setConfirm(null)} busy={createToken.isPending || revokeToken.isPending} />
    </div>
  );
}

function FindingsList({ f }: { f: Partial<Findings> }) {
  const appName = useAppName();
  const items: React.ReactNode[] = [];
  const fixed = f.fixed;
  if (fixed && (fixed.memberships_removed || fixed.sessions_revoked)) {
    items.push(<>Fixed: {fixed.memberships_removed} group {fixed.memberships_removed === 1 ? "membership" : "memberships"} of removed people, {fixed.sessions_revoked} missing session {fixed.sessions_revoked === 1 ? "revocation" : "revocations"}.</>);
  }
  if (f.failed_tasks?.length) {
    items.push(<>Signing out {f.failed_tasks.map((t) => t.user.display_name).join(", ")} failed after {f.failed_tasks[0]!.attempts} attempts. Their {appName} access is already gone; end their Keycloak sessions by hand.</>);
  }
  if (f.unlinked_users?.count) {
    items.push(<>{f.unlinked_users.count} active {f.unlinked_users.count === 1 ? "person signs" : "people sign"} in but {f.unlinked_users.count === 1 ? "is" : "are"} not provisioned from Entra: {f.unlinked_users.items.map((u) => u.display_name).join(", ")}{f.unlinked_users.count > f.unlinked_users.items.length ? " and others" : ""}. Assign them to the Entra app, or check that their sign-in carries the Entra object id.</>);
  }
  if (f.deleted_groups_mapped?.length) {
    items.push(<>Groups deleted in Entra still have role mappings: {f.deleted_groups_mapped.map((g) => g.display_name).join(", ")}. Remove the mappings in Group mappings.</>);
  }
  if (f.deprovisioned_owners?.length) {
    items.push(<>{f.deprovisioned_owners.length} {f.deprovisioned_owners.length === 1 ? "person who left still owns" : "people who left still own"} content (below).</>);
  }
  if (f.roles_refreshed) items.push(<>Role summaries refreshed for {f.roles_refreshed} {f.roles_refreshed === 1 ? "person" : "people"}.</>);
  if (items.length === 0) return <p className="text-small">No drift: people, groups and sessions match what Entra sent.</p>;
  return <ul aria-label="Reconcile findings" className="grid list-disc gap-1 pl-5 text-small">{items.map((n, i) => <li key={i}>{n}</li>)}</ul>;
}

function TokenDialog({ token, endpoint, onClose }: { token: string | null; endpoint: string; onClose: () => void }) {
  const appName = useAppName();
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(token ?? "");
      toast.success("Token copied.");
    } catch {
      toast.error("Copy failed. Select the token and copy it.");
    }
  };
  return (
    <Dialog open={token !== null} onOpenChange={(open) => { if (!open) onClose(); }}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Provisioning token</DialogTitle>
          <DialogDescription>Copy it now: {appName} keeps only a fingerprint and cannot show it again.</DialogDescription>
        </DialogHeader>
        <ol className="grid list-decimal gap-2 pl-5 text-small">
          <li>In the Entra enterprise app, open Provisioning and choose Automatic.</li>
          <li>Tenant URL: <span className="break-all font-mono">{endpoint}</span></li>
          <li>Secret Token: <code className="break-all rounded-sm bg-surface-sunken px-1 font-mono" aria-label="Provisioning token">{token}</code></li>
          <li>Test the connection, then start provisioning.</li>
        </ol>
        <DialogFooter>
          <Button variant="secondary" onClick={() => void copy()}><Copy aria-hidden />Copy token</Button>
          <Button onClick={onClose}>Done</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function ReassignDialog({ owner, onClose, onDone }: { owner: FormerOwner | null; onClose: () => void; onDone: () => void }) {
  const [to, setTo] = useState<Person | null>(null);
  const move = useMutation({
    mutationFn: () => api<{ moved: Owned; skipped: Owned; to: { display_name: string } }>(
      `/api/v1/admin/users/${owner!.user.id}/reassign-content`, { method: "POST", body: { to_user_id: to!.id } }),
    onSuccess: (r) => {
      const moved = ownedText(r.moved);
      const skipped = ownedText(r.skipped);
      toast.success(`${moved || "Nothing"} moved to ${r.to.display_name}.${skipped ? ` ${skipped} stay with ${owner!.user.display_name}: ${r.to.display_name} already has the same.` : ""}`);
      setTo(null);
      onDone();
      onClose();
    },
  });
  const close = () => { setTo(null); move.reset(); onClose(); };
  return (
    <Dialog open={owner !== null} onOpenChange={(open) => { if (!open) close(); }}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Reassign {owner?.user.display_name}'s content</DialogTitle>
          <DialogDescription>{owner ? `${ownedText(owner.owned)}. Shares and publications stay as they are; the new owner is notified.` : null}</DialogDescription>
        </DialogHeader>
        <PeoplePicker label="New owner" admin single exclude={owner ? [owner.user.id] : []} onPick={setTo} pickHint="Enter to choose" />
        <FormAlert error={move.error ? asApiError(move.error) : null} />
        <DialogFooter>
          <Button variant="secondary" onClick={close}>Cancel</Button>
          <Button disabled={!to || move.isPending} onClick={() => move.mutate()}>Reassign to {to?.name ?? "…"}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
