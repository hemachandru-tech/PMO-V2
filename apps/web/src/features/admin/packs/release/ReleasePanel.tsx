import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, CircleAlert, CircleX, GitBranchPlus, Rocket, Send, Undo2 } from "lucide-react";
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";

import { api } from "../../../../api/client";
import {
  getPackDiff, getPackReview, openNextVersion, releasePack, returnPack, runReleaseChecks, submitPack, withdrawPack,
  type PackDiff, type ReleaseChecks, type ReleaseIssue,
} from "../../../../api/packs";
import { formatRelative } from "../../../../components/format";
import { Alert } from "../../../../components/ui/alert";
import { Button } from "../../../../components/ui/button";
import { ConfirmDialog, type ConfirmRequest } from "../../../../components/ui/confirm-dialog";
import { Label } from "../../../../components/ui/label";
import { NativeSelect } from "../../../../components/ui/native-select";
import { Textarea } from "../../../../components/ui/textarea";
import { asApiError, friendlyMessage } from "../../../../lib/api-errors";
import { packKey, usePack } from "../PackContext";
import { DiffTable } from "./DiffTable";

const MIN_REASON = 3;

/**
 * Release a tenant pack version (US-238, DEC-26): run the release checks on a test connection, give a reason for each
 * warning, submit for review; a reviewer who is not the author releases it or sends it back. A released version opens
 * the next draft version.
 */
export function ReleasePanel() {
  const { pack } = usePack();
  const review = useQuery({ queryKey: ["pack-review", pack.id], queryFn: () => getPackReview(pack.id), enabled: pack.owner === "tenant" });
  if (pack.owner !== "tenant" || !review.data) return null;
  const state = review.data.state?.state ?? "draft";
  if (state === "released" || state === "retired") return <Released name={review.data.people[review.data.state?.released_by ?? ""]} canOpen={review.data.you.can_open_next} />;
  if (state === "in_review") return <InReview />;
  return <DraftRelease />;
}

function useRefresh() {
  const qc = useQueryClient();
  const { pack } = usePack();
  return () => {
    for (const key of [packKey(pack.id), ["pack-review", pack.id], ["pack-versions", pack.id], ["pack-diff", pack.id], ["packs"]]) {
      void qc.invalidateQueries({ queryKey: key });
    }
  };
}

function Released({ name, canOpen }: { name?: string; canOpen: boolean }) {
  const { pack } = usePack();
  const nav = useNavigate();
  const open = useMutation({
    mutationFn: () => openNextVersion(pack.id),
    onSuccess: (out) => { toast.success(`Draft ${out.version} opened`); nav(`/admin/packs/${out.id}`); },
    onError: (e) => {
      const err = asApiError(e, "The next version could not be opened.");
      const existing = (err.details as { pack_id?: string }).pack_id;
      if (existing) { toast.info(err.message); nav(`/admin/packs/${existing}`); } else toast.error(friendlyMessage(err));
    },
  });
  return (
    <section aria-label="Release" className="grid gap-2 rounded-md border border-line p-4">
      <p className="flex items-center gap-2"><CheckCircle2 aria-hidden className="size-4 text-accent" />
        Version {pack.version} is {pack.status}{name ? `, released by ${name}` : ""}. It keeps serving installs until a newer version is released.</p>
      {canOpen && pack.status === "released" && (
        <Button className="w-fit" onClick={() => open.mutate()} disabled={open.isPending}><GitBranchPlus aria-hidden />Edit as the next version</Button>
      )}
    </section>
  );
}

function DraftRelease() {
  const { pack, etag } = usePack();
  const refresh = useRefresh();
  const review = useQuery({ queryKey: ["pack-review", pack.id], queryFn: () => getPackReview(pack.id) });
  const connections = useQuery({ queryKey: ["connections"], queryFn: () => api<{ items: { id: string; name: string }[] }>("/api/v1/connections") });
  const [connectionId, setConnectionId] = useState(pack.installed_on[0]?.connection_id ?? "");
  const [reasons, setReasons] = useState<Record<string, string>>({});
  const [note, setNote] = useState("");
  const checks = useMutation({ mutationFn: () => runReleaseChecks(pack.id, connectionId) });
  const submit = useMutation({
    mutationFn: () => submitPack(pack.id, etag, { connection_id: connectionId, reasons, note: note || undefined }),
    onSuccess: () => { toast.success("Submitted for review"); refresh(); },
    onError: (e) => toast.error(friendlyMessage(asApiError(e, "The version could not be submitted."))),
  });
  const result = checks.data;
  const missing = result ? result.checks.warnings.filter((w) => (reasons[w.id] ?? "").trim().length < MIN_REASON).length : 0;
  const returned = review.data?.state?.state === "returned" ? review.data.state : null;

  return (
    <section aria-label="Release" className="grid gap-3 rounded-md border border-line p-4">
      <h2 className="text-heading">Release {pack.version}</h2>
      {returned && (
        <Alert variant="warn">Sent back by {review.data?.people[returned.returned_by ?? ""] ?? "a reviewer"}{returned.returned_at ? ` ${formatRelative(returned.returned_at)}` : ""}: {returned.reason}</Alert>
      )}
      <p className="text-ink-muted">The checks run on a test connection: the schema, every reference, each measure and dimension compiled, sanity checks on the figures, the starter reports and the gold examples (plan only). Errors block; each warning needs a reason. Someone other than you releases it.</p>
      <div className="flex flex-wrap items-end gap-2">
        <div className="grid min-w-64 gap-1">
          <Label htmlFor="release-connection">Test connection</Label>
          <NativeSelect id="release-connection" value={connectionId} onChange={(e) => { setConnectionId(e.target.value); checks.reset(); }}>
            <option value="">Choose a connection…</option>
            {(connections.data?.items ?? []).map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
          </NativeSelect>
        </div>
        <Button variant="secondary" disabled={!connectionId || checks.isPending} onClick={() => checks.mutate()}>
          {checks.isPending ? "Running checks…" : "Run release checks"}
        </Button>
      </div>
      {checks.isError && <Alert variant="danger">{friendlyMessage(asApiError(checks.error, "The checks could not run."))}</Alert>}
      {result && (
        <>
          <ChecksView checks={result.checks} reasons={reasons} onReason={(id, text) => setReasons((r) => ({ ...r, [id]: text }))} />
          <DiffView diff={result.diff} />
          <div className="grid gap-1">
            <Label htmlFor="release-note">Note for the reviewer (optional)</Label>
            <Textarea id="release-note" value={note} maxLength={1000} onChange={(e) => setNote(e.target.value)} />
          </div>
          <div className="flex flex-wrap items-center gap-3">
            <Button disabled={!result.checks.passed || missing > 0 || submit.isPending} onClick={() => submit.mutate()}><Send aria-hidden />Submit for review</Button>
            {!result.checks.passed && <span className="text-small text-status-danger">Fix the {result.checks.errors.length} errors first.</span>}
            {result.checks.passed && missing > 0 && <span className="text-small text-ink-muted">{missing} warnings still need a reason.</span>}
          </div>
        </>
      )}
    </section>
  );
}

function InReview() {
  const { pack } = usePack();
  const refresh = useRefresh();
  const review = useQuery({ queryKey: ["pack-review", pack.id], queryFn: () => getPackReview(pack.id) });
  const diff = useQuery({ queryKey: ["pack-diff", pack.id], queryFn: () => getPackDiff(pack.id) });
  const [reason, setReason] = useState("");
  const [sendingBack, setSendingBack] = useState(false);
  const [confirm, setConfirm] = useState<ConfirmRequest | null>(null);
  const onError = (fallback: string) => (e: unknown) => toast.error(friendlyMessage(asApiError(e, fallback)));
  const release = useMutation({
    mutationFn: () => releasePack(pack.id),
    onSuccess: (out) => {
      const n = out.update_notice.connections;
      toast.success(n ? `Released. ${n} connection${n === 1 ? "" : "s"} offered the update.` : "Released");
      refresh();
    },
    onError: onError("The version could not be released."),
  });
  const sendBack = useMutation({ mutationFn: () => returnPack(pack.id, reason.trim()), onSuccess: () => { toast.success("Sent back to the author"); refresh(); }, onError: onError("The version could not be sent back.") });
  const withdraw = useMutation({ mutationFn: () => withdrawPack(pack.id), onSuccess: () => { toast.success("Withdrawn: you can edit it again"); refresh(); }, onError: onError("The version could not be withdrawn.") });
  const r = review.data;
  if (!r) return null;
  const submitter = r.people[r.state?.submitted_by ?? ""] ?? "its author";
  const stored = r.review?.checks;

  return (
    <section aria-label="Review" className="grid gap-3 rounded-md border border-line p-4">
      <h2 className="text-heading">Waiting for review</h2>
      <p>Submitted by {submitter}{r.state?.submitted_at ? ` ${formatRelative(r.state.submitted_at)}` : ""}.{r.review?.note ? ` Note: ${r.review.note}` : ""}</p>
      {r.you.author && <Alert>You authored or submitted this version, so another Admin or a certifier releases it or sends it back.</Alert>}
      {stored && <ChecksView checks={stored} reasons={r.review?.reasons ?? {}} />}
      {diff.data && <DiffView diff={diff.data} />}
      {sendingBack && (
        <div className="grid gap-1">
          <Label htmlFor="return-reason">Why it goes back</Label>
          <Textarea id="return-reason" value={reason} maxLength={1000} onChange={(e) => setReason(e.target.value)} />
        </div>
      )}
      <div className="flex flex-wrap gap-2">
        {r.you.can_release && !sendingBack && (
          <Button disabled={release.isPending} onClick={() => setConfirm({
            title: `Release ${pack.title} ${pack.version}?`,
            body: "The checks run again on the same connection. The version is then frozen, can be installed, and connections with an older version are offered the update.",
            confirm: "Release", onConfirm: () => release.mutate(),
          })}><Rocket aria-hidden />Release</Button>
        )}
        {r.you.can_return && (sendingBack
          ? <><Button variant="secondary" disabled={reason.trim().length < MIN_REASON || sendBack.isPending} onClick={() => sendBack.mutate()}>Send back</Button>
              <Button variant="ghost" onClick={() => setSendingBack(false)}>Cancel</Button></>
          : <Button variant="secondary" onClick={() => setSendingBack(true)}><Undo2 aria-hidden />Send back…</Button>)}
        {r.you.can_withdraw && r.you.author && <Button variant="ghost" disabled={withdraw.isPending} onClick={() => withdraw.mutate()}>Withdraw to edit</Button>}
      </div>
      <ConfirmDialog request={confirm} onClose={() => setConfirm(null)} busy={release.isPending} />
    </section>
  );
}

function ChecksView({ checks, reasons, onReason }: { checks: ReleaseChecks; reasons: Record<string, string>; onReason?: (id: string, text: string) => void }) {
  return (
    <div className="grid gap-3">
      <ul aria-label="Release checks" className="grid gap-1 sm:grid-cols-2">
        {checks.steps.map((s) => (
          <li key={s.key} className="flex items-center gap-2 text-small">
            {!s.ran ? <CircleAlert aria-hidden className="size-4 text-ink-muted" />
              : s.errors ? <CircleX aria-hidden className="size-4 text-status-danger" />
                : s.warnings ? <CircleAlert aria-hidden className="size-4 text-status-warn" />
                  : <CheckCircle2 aria-hidden className="size-4 text-accent" />}
            <span>{s.label}</span>
            <span className="text-ink-muted">{!s.ran ? "not run" : s.errors ? `${s.errors} errors` : s.warnings ? `${s.warnings} warnings` : "passed"}</span>
          </li>
        ))}
      </ul>
      {checks.facts.compiled && (
        <p className="text-small text-ink-muted">On {checks.facts.connection?.name}: {checks.facts.compiled.measures} measures and {checks.facts.compiled.dimensions} dimensions compiled, {checks.facts.gold_planned ?? 0} gold examples planned.</p>
      )}
      {checks.errors.length > 0 && <IssueList title="Errors" issues={checks.errors} />}
      {checks.warnings.length > 0 && <IssueList title="Warnings" issues={checks.warnings} reasons={reasons} onReason={onReason} />}
    </div>
  );
}

function IssueList({ title, issues, reasons, onReason }: { title: string; issues: ReleaseIssue[]; reasons?: Record<string, string>; onReason?: (id: string, text: string) => void }) {
  return (
    <div className="grid gap-2">
      <h3 className="text-label">{title} ({issues.length})</h3>
      <ul className="grid gap-2">
        {issues.map((i) => (
          <li key={i.id} className="grid gap-1 rounded-sm border border-line p-2">
            <span>{i.message}</span>
            <span className="font-mono text-small text-ink-muted">{i.path}</span>
            {onReason
              ? <Textarea aria-label={`Reason for: ${i.message}`} placeholder="Why this is acceptable" rows={1} value={reasons?.[i.id] ?? ""} onChange={(e) => onReason(i.id, e.target.value)} />
              : reasons?.[i.id] && <span className="text-small">Reason: {reasons[i.id]}</span>}
          </li>
        ))}
      </ul>
    </div>
  );
}

function DiffView({ diff }: { diff: PackDiff }) {
  return (
    <div className="grid gap-2">
      <h3 className="text-label">{diff.against ? `Changes since ${diff.against.version}` : "First release: everything is new"}</h3>
      {diff.changes.length === 0 ? <p className="text-ink-muted">No changes.</p> : <DiffTable changes={diff.changes} first={!diff.against} />}
    </div>
  );
}
