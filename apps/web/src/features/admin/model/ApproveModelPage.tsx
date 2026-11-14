import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, CircleCheck } from "lucide-react";
import { useState, type ReactNode } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";

import { api, type ApiError } from "../../../api/client";
import type { DraftChecks, DraftImpact, DraftModel, GuardWarning } from "../../../api/app";
import { Alert, FormAlert } from "../../../components/ui/alert";
import { Badge } from "../../../components/ui/badge";
import { Button } from "../../../components/ui/button";
import { Card } from "../../../components/ui/card";
import { Label } from "../../../components/ui/label";
import { Skeleton } from "../../../components/ui/skeleton";
import { StatusBadge } from "../../../components/ui/status-badge";
import { Steps } from "../../../components/ui/steps";
import { Textarea } from "../../../components/ui/textarea";
import { asApiError } from "../../../lib/api-errors";
import { ChangeList } from "./ChangeList";
import { countChanges, QuestionList, useModel, useReloadModel } from "./ModelStudioPage";
import { useAppName } from "../../../app/branding";

const STEPS = [
  { key: "checks", label: "Checks" },
  { key: "changes", label: "Changes" },
  { key: "impact", label: "Impact" },
  { key: "approve", label: "Approve" },
] as const;
type Step = (typeof STEPS)[number]["key"];

interface Approved {
  version: number; change_summary: string; impact: { breaking_changes: unknown[] }; starter_dashboards?: unknown;
  checks?: { blocking: { ref: string; issue: string }[] };
}

/**
 * Approve a model as steps (FEAT-009; RULE-015; US-144): what still blocks approval (with fixes in place), what the
 * draft changes against the approved version, what that does to published dashboards and personal fields, then a
 * note and Approve. The server re-runs every gate and dry-runs each measure at the source on Approve.
 */
export function ApproveModelPage() {
  const { id } = useParams();
  const [params, setParams] = useSearchParams();
  const step = (STEPS.some((s) => s.key === params.get("step")) ? params.get("step") : "checks") as Step;
  const go = (next: Step) => setParams({ step: next });
  const { draft, impact, connection } = useModel(id!);
  const reload = useReloadModel(id!);
  const [approved, setApproved] = useState<Approved | null>(null);

  if (draft.isLoading) return <div className="tl grid gap-4" aria-busy="true"><Skeleton className="h-10 w-1/3" /><Skeleton className="h-96" /></div>;
  if (draft.error) return <div className="tl"><FormAlert error={asApiError(draft.error)} /></div>;
  const d = draft.data!;
  const blockers = blockersOf(d);
  const index = STEPS.findIndex((s) => s.key === step);
  const reachable = (i: number) => !approved && (i <= index || blockers === 0);
  const base = impact.data?.base_version;

  return (
    <div className="tl grid gap-4">
      <header className="grid gap-2">
        <Button asChild variant="ghost" size="sm" className="w-fit"><Link to={`/admin/models/${id}`}><ArrowLeft aria-hidden />Back to Model studio</Link></Button>
        <h1 className="text-title">Approve {connection?.name ?? "the"} model{base ? ` v${base + 1}` : " v1"}</h1>
        <p className="text-small text-ink-muted">
          Approval freezes a certified version that Creators can use at once. {base ? `v${base} stays available; published dashboards keep the version they were built on until they are upgraded.` : ""}
        </p>
      </header>
      <Steps steps={STEPS} current={step} reachable={reachable} onSelect={go} label="Approval steps" />
      <Card className="grid gap-4 p-4">
        {approved ? (
          <Done result={approved} modelId={id!} />
        ) : (
          <>
            {step === "checks" && <ChecksStep draft={d} modelId={id!} onChanged={reload} onNext={() => go("changes")} />}
            {step === "changes" && (
              <section className="grid gap-3" aria-labelledby="changes-title">
                <h2 id="changes-title" className="text-heading">What this draft changes{base ? ` against v${base}` : ""}</h2>
                {impact.isLoading ? <Skeleton className="h-32" /> : impact.data && <ChangeList changes={impact.data.changes} empty="Nothing changes: the draft matches the approved version." />}
                <Nav onBack={() => go("checks")} onNext={() => go("impact")} />
              </section>
            )}
            {step === "impact" && (
              <section className="grid gap-3" aria-labelledby="impact-title">
                <h2 id="impact-title" className="text-heading">If you approve{base ? ` v${base + 1}` : ""}</h2>
                {impact.isLoading ? <Skeleton className="h-32" /> : impact.error ? <FormAlert error={asApiError(impact.error)} /> : impact.data && <ImpactSummary impact={impact.data} />}
                <Nav onBack={() => go("changes")} onNext={() => go("approve")} />
              </section>
            )}
            {step === "approve" && <ApproveStep draft={d} impact={impact.data} modelId={id!} onBack={() => go("impact")} onApproved={(r) => { setApproved(r); reload(); }} />}
          </>
        )}
      </Card>
    </div>
  );
}

function blockersOf(d: DraftModel): number {
  const links = d.links.filter((l) => !l.accepted).length;
  const questions = d.open_questions.filter((q) => q.status === "open" && q.blocking).length;
  return d.issues.length + links + questions + (d.measures.length === 0 ? 1 : 0);
}

function Nav({ onBack, onNext, next = "Next" }: { onBack?: () => void; onNext?: () => void; next?: string }) {
  return (
    <div className="flex gap-2">
      {onBack && <Button variant="ghost" onClick={onBack}>Back</Button>}
      {onNext && <Button onClick={onNext}>{next}</Button>}
    </div>
  );
}

/** Gates the server enforces on Approve, each fixable here (confirm a link, answer a question) or in the studio. */
function ChecksStep({ draft, modelId, onChanged, onNext }: { draft: DraftModel; modelId: string; onChanged: () => void; onNext: () => void }) {
  const appName = useAppName();
  const [error, setError] = useState<ApiError | null>(null);
  const confirmAll = useMutation({
    mutationFn: () => api(`/api/v1/models/${modelId}/draft`, { method: "PATCH", body: { links: draft.links.map((l) => ({ ...l, accepted: true })) }, ifMatch: draft.draft_etag }),
    onSuccess: () => { setError(null); onChanged(); },
    onError: (e) => setError(asApiError(e)),
  });
  const answer = useMutation({
    mutationFn: ({ qid, value, dismiss }: { qid: string; value?: string; dismiss?: boolean }) =>
      api(`/api/v1/models/${modelId}/questions/${qid}/answer`, { method: "POST", body: dismiss ? { dismiss: true } : { answer: value } }),
    onSuccess: () => { setError(null); onChanged(); },
    onError: (e) => setError(asApiError(e)),
  });
  const links = draft.links.filter((l) => !l.accepted);
  const questions = draft.open_questions.filter((q) => q.status === "open" && q.blocking);
  // No batch endpoint: dismiss one at a time, then reload once (a failure keeps the ones already dismissed).
  const dismissAll = useMutation({
    mutationFn: async () => {
      for (const q of questions) await api(`/api/v1/models/${modelId}/questions/${q.id}/answer`, { method: "POST", body: { dismiss: true } });
    },
    onSuccess: () => setError(null),
    onError: (e) => setError(asApiError(e)),
    onSettled: () => onChanged(),
  });
  const busy = answer.isPending || dismissAll.isPending;
  const ok = blockersOf(draft) === 0;
  // Mapping guards (US-199): fields sharing a column are listed at once; the data checks read each measure, on request.
  const guards = useQuery({
    queryKey: ["draft-checks", modelId, draft.draft_etag],
    queryFn: () => api<DraftChecks>(`/api/v1/models/${modelId}/draft/checks?data=false`), retry: false,
  });
  const data = useMutation({ mutationFn: () => api<DraftChecks>(`/api/v1/models/${modelId}/draft/checks`) });
  const shared = guards.data?.blocking ?? [];
  const row = (pass: boolean, label: string, detail?: ReactNode) => (
    <li className="grid gap-1.5 rounded-md border border-line px-3 py-2">
      <span className="flex items-center gap-2"><StatusBadge status={pass ? "ok" : "danger"}>{pass ? "Passed" : "To fix"}</StatusBadge>{label}</span>
      {!pass && detail}
    </li>
  );
  return (
    <section className="grid gap-3" aria-labelledby="checks-title">
      <h2 id="checks-title" className="text-heading">Checks</h2>
      <ul className="grid gap-2">
        {row(draft.issues.length === 0, "The model matches the source schema", (
          <ul className="list-disc pl-5 text-small">{draft.issues.slice(0, 10).map((i) => <li key={i.ref + i.issue}><span className="font-mono">{i.ref}</span>: {i.issue}</li>)}</ul>
        ))}
        {row(links.length === 0, "Every link is confirmed", (
          <div className="grid gap-1.5 text-small">
            <ul className="list-disc pl-5">{links.map((l) => <li key={`${l.from_entity}-${l.to_entity}-${l.from_fields.join()}`}>{l.from_entity} → {l.to_entity} ({l.from_fields.join(", ")} = {l.to_fields.join(", ")})</li>)}</ul>
            <div><Button size="sm" variant="secondary" onClick={() => confirmAll.mutate()} disabled={confirmAll.isPending}>Confirm {links.length === 1 ? "this link" : `all ${links.length} links`}</Button></div>
          </div>
        ))}
        {row(questions.length === 0, "Every blocking question is answered", (
          <div className="grid gap-2">
            <QuestionList questions={questions} busy={busy} onAnswer={(qid, value) => answer.mutate({ qid, value })} onDismiss={(qid) => answer.mutate({ qid, dismiss: true })} />
            {questions.length > 1 && (
              <div><Button size="sm" variant="secondary" onClick={() => dismissAll.mutate()} disabled={busy}>{dismissAll.isPending ? "Dismissing…" : `Dismiss all ${questions.length} questions`}</Button></div>
            )}
          </div>
        ))}
        {row(draft.measures.length > 0, "The model has at least one measure", <p className="text-small">Describe one to the assistant in Model studio.</p>)}
        {guards.data && (
          <li className="grid gap-1.5 rounded-md border border-line px-3 py-2" data-testid="check-shared-columns">
            <span className="flex items-center gap-2"><StatusBadge status={shared.length ? "danger" : "ok"}>{shared.length ? "To fix" : "Passed"}</StatusBadge>Each source column stands for one field</span>
            {shared.length > 0 && (
              <div className="grid gap-1 text-small">
                <ul className="list-disc pl-5">{shared.map((i) => <li key={i.ref}>{i.issue}</li>)}</ul>
                <p className="text-ink-muted">Fix the field's column in Model studio. Approving as it is needs a note saying why.</p>
              </div>
            )}
          </li>
        )}
        <li className="grid gap-1.5 rounded-md border border-line px-3 py-2" data-testid="check-data">
          <span className="flex flex-wrap items-center gap-2">
            {data.data?.warnings && <StatusBadge status={data.data.warnings.length ? "warn" : "ok"}>{data.data.warnings.length ? "Check" : "Passed"}</StatusBadge>}
            Measures look right on the data
            <Button size="sm" variant="secondary" className="ml-auto" onClick={() => data.mutate()} disabled={data.isPending}>
              {data.isPending ? "Reading the data…" : data.data ? "Check again" : "Check the data"}
            </Button>
          </span>
          {!data.data && !data.isPending && <p className="text-small text-ink-muted">Runs each measure once (counts only): rules filtering on values a column doesn't hold, measures that return 0, and measures with the same figure as another.</p>}
          {data.error && <FormAlert error={asApiError(data.error)} />}
          {data.data?.warnings && data.data.warnings.length > 0 && <WarningList warnings={data.data.warnings} />}
        </li>
      </ul>
      <p className="text-small text-ink-muted">On Approve, {appName} also runs every measure against the source (no rows read) to prove it works.</p>
      <FormAlert error={error} />
      <Nav onNext={ok ? onNext : undefined} />
    </section>
  );
}

/** Published dashboards, widgets and personal fields touched by the draft's changes (US-144 impact summary). */
export function ImpactSummary({ impact }: { impact: DraftImpact }) {
  const appName = useAppName();
  const dashboards = impact.dashboards;
  const now = dashboards.filter((d) => d.changes_at_approval);
  const later = dashboards.filter((d) => !d.changes_at_approval);
  const breaking = impact.breaking_changes.length;
  const measures = impact.changes.measures;
  const changedRules = measures.changed.filter((m) => m.what.includes("rule") || m.what.includes("blanks")).length;
  return (
    <div className="grid gap-3">
      <ul className="grid gap-2">
        <Line tone={changedRules + measures.removed.length ? "warn" : "ok"}>
          {changedRules + measures.removed.length === 0 ? "No measure rules change" : `${count(changedRules, "measure rule")} ${changedRules === 1 ? "changes" : "change"}, ${count(measures.removed.length, "measure")} removed`}
        </Line>
        <Line tone={impact.widget_count ? "warn" : "ok"}>
          {impact.widget_count === 0 ? "No published widget uses a changed definition" : `${count(impact.widget_count, "widget")} on ${count(dashboards.length, "published dashboard")} ${impact.widget_count === 1 ? "uses" : "use"} a changed definition`}
        </Line>
        <Line tone={impact.personal_fields_broken.length ? "danger" : "ok"}>
          {impact.personal_fields_broken.length === 0 ? "No personal fields break" : `${count(impact.personal_fields_broken.length, "personal field")} will break`}
        </Line>
        <Line tone={breaking ? "danger" : "ok"}>{breaking ? `${count(breaking, "breaking change")} (removed or changed definitions)` : "No breaking changes"}</Line>
      </ul>
      {now.length > 0 && (
        <DashboardList title="Change at approval (starter dashboards follow the newest version)" items={now} />
      )}
      {later.length > 0 && (
        <DashboardList title="Change when upgraded (these published versions keep the current model until then)" items={later} />
      )}
      {impact.personal_fields_broken.length > 0 && (
        <div className="grid gap-1">
          <h3 className="text-body-strong">Personal fields that will break</h3>
          <ul className="list-disc pl-5 text-small">{impact.personal_fields_broken.map((f) => <li key={f.id}>{f.label} ({f.owner}): {f.reason}</li>)}</ul>
          <p className="text-small text-ink-muted">{appName} marks them Broken and notifies their owners.</p>
        </div>
      )}
    </div>
  );
}

const count = (n: number, one: string) => `${n.toLocaleString()} ${n === 1 ? one : `${one}s`}`;

function Line({ tone, children }: { tone: "ok" | "warn" | "danger"; children: ReactNode }) {
  return <li className="flex items-center gap-2"><StatusBadge status={tone}>{tone === "ok" ? "Clear" : tone === "warn" ? "Review" : "Breaks"}</StatusBadge>{children}</li>;
}

function DashboardList({ title, items }: { title: string; items: DraftImpact["dashboards"] }) {
  return (
    <div className="grid gap-1">
      <h3 className="text-body-strong">{title}</h3>
      <ul className="grid gap-2">
        {items.map((d) => (
          <li key={d.id} className="grid gap-1 rounded-md border border-line px-3 py-2 text-small">
            <span className="flex flex-wrap items-center gap-2"><Link className="text-link underline-offset-4 hover:underline" to={`/dashboards/${d.id}`}>{d.title}</Link><Badge variant="neutral">v{d.version}</Badge></span>
            <ul className="grid gap-0.5 pl-3">
              {d.widgets.map((w, i) => (
                <li key={`${w.title}-${i}`}>{w.title}: {w.reasons.join("; ")}{w.breaks && <> · <span className="text-status-danger">will stop working</span></>}</li>
              ))}
            </ul>
          </li>
        ))}
      </ul>
    </div>
  );
}

function ApproveStep({ draft, impact, modelId, onBack, onApproved }: {
  draft: DraftModel;
  impact?: DraftImpact;
  modelId: string;
  onBack: () => void;
  onApproved: (r: Approved) => void;
}) {
  const appName = useAppName();
  const qc = useQueryClient();
  const [note, setNote] = useState("");
  const [why, setWhy] = useState("");
  const approve = useMutation({
    mutationFn: (override?: string) => api<Approved>(`/api/v1/models/${modelId}/approve`, {
      method: "POST", body: { draft_etag: draft.draft_etag, note: note.trim() || undefined, override_note: override },
    }),
    onSuccess: (r) => { void qc.invalidateQueries({ queryKey: ["admin-overview"] }); onApproved(r); },
  });
  const error = approve.error ? asApiError(approve.error) : null;
  const canOverride = error?.details?.can_override === true;
  const issues = (error?.details?.issues as { ref: string; issue: string }[] | undefined) ?? [];
  const questions = (error?.details?.questions as string[] | undefined) ?? [];
  const changes = impact ? countChanges(impact) : null;
  return (
    <section className="grid gap-3" aria-labelledby="approve-title">
      <h2 id="approve-title" className="text-heading">Approve</h2>
      <p>
        {draft.entities.length} entities, {draft.links.length} links, {draft.measures.length} measures and {draft.dimensions.length} dimensions
        {changes !== null && ` · ${changes === 1 ? "1 change" : `${changes} changes`}`}
        {impact && impact.widget_count > 0 && ` · ${count(impact.widget_count, "published widget")} affected`}.
      </p>
      <div className="grid max-w-xl gap-1">
        <Label htmlFor="approve-note">Approval note (optional)</Label>
        <Textarea id="approve-note" rows={3} maxLength={1000} value={note} onChange={(e) => setNote(e.target.value)} placeholder="Region: KSA and Saudi Arabia counted as one value." />
        <p className="text-small text-ink-muted">Shown in the version history. Without a note, {appName} writes a summary of the changes.</p>
      </div>
      <FormAlert error={error} />
      {issues.length > 0 && <ul className="list-disc pl-5 text-small">{issues.map((i) => <li key={i.ref + i.issue}><span className="font-mono">{i.ref}</span>: {i.issue}</li>)}</ul>}
      {questions.length > 0 && <ul className="list-disc pl-5 text-small">{questions.map((q) => <li key={q}>{q}</li>)}</ul>}
      {canOverride && (
        <div className="grid max-w-xl gap-1" data-testid="approve-override">
          <Label htmlFor="override-note">Why approve anyway?</Label>
          <Textarea id="override-note" rows={2} maxLength={1000} value={why} onChange={(e) => setWhy(e.target.value)} placeholder="Both fields hold the same values in this source." />
          <p className="text-small text-ink-muted">Recorded in the audit log with the issues above.</p>
        </div>
      )}
      <div className="flex gap-2">
        <Button variant="ghost" onClick={onBack}>Back</Button>
        {canOverride
          ? <Button variant="danger" onClick={() => approve.mutate(why.trim())} disabled={approve.isPending || why.trim().length < 3}>{approve.isPending ? "Checking every measure…" : "Approve anyway"}</Button>
          : <Button onClick={() => approve.mutate(undefined)} disabled={approve.isPending}>{approve.isPending ? "Checking every measure…" : "Approve and certify"}</Button>}
      </div>
    </section>
  );
}

function WarningList({ warnings }: { warnings: GuardWarning[] }) {
  return <ul className="list-disc pl-5 text-small">{warnings.map((w) => <li key={w.kind + w.ref}>{w.issue}</li>)}</ul>;
}

function Done({ result, modelId }: { result: Approved; modelId: string }) {
  const breaking = result.impact.breaking_changes.length;
  return (
    <section className="grid gap-3" role="status">
      <h2 className="flex items-center gap-2 text-heading"><CircleCheck aria-hidden className="size-5 text-status-ok" />Approved as v{result.version}</h2>
      <p>{result.change_summary}</p>
      {breaking > 0 && <Alert variant="warn"><p>{count(breaking, "breaking change")}. Owners of affected dashboards can upgrade their widgets from the publish checks.</p></Alert>}
      <div className="flex flex-wrap gap-2">
        <Button asChild><Link to={`/admin/models/${modelId}`}>Back to Model studio</Link></Button>
        <Button asChild variant="secondary"><Link to="/admin/connections">Connections</Link></Button>
      </div>
    </section>
  );
}
