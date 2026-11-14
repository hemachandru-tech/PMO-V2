import { zodResolver } from "@hookform/resolvers/zod";
import { useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, Check, EyeOff, Undo2 } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { useForm } from "react-hook-form";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { toast } from "sonner";
import { z } from "zod";

import { api, type ApiError } from "../../api/client";
import type {
  CheckFix, CheckItem, DashboardDetail, Prechecks, PreviewResult, PublishOut, PublishRequest, RoleRef,
} from "../../api/authoring";
import { DeclineIn, PublishIn } from "../../api/schemas";
import { GROUPS_KEY } from "../../app/shell/dashboardGroups";
import { formatRelative } from "../../components/format";
import { PeoplePicker, type Person } from "../../components/PeoplePicker";
import { Alert, FormAlert } from "../../components/ui/alert";
import { Badge } from "../../components/ui/badge";
import { Button } from "../../components/ui/button";
import { Card } from "../../components/ui/card";
import { Checkbox } from "../../components/ui/checkbox";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "../../components/ui/dialog";
import { Field, Form } from "../../components/ui/form";
import { Skeleton } from "../../components/ui/skeleton";
import { Steps } from "../../components/ui/steps";
import { StatusBadge } from "../../components/ui/status-badge";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "../../components/ui/table";
import { Textarea } from "../../components/ui/textarea";
import { applyServerError, asApiError } from "../../lib/api-errors";
import { cn } from "../../lib/utils";

const STEPS = [
  { key: "audience", label: "Audience" },
  { key: "preview", label: "Preview" },
  { key: "checks", label: "Checks" },
  { key: "note", label: "Release note" },
] as const;
type Step = (typeof STEPS)[number]["key"];

const FIX_LABELS: Record<string, string> = {
  request_certification: "Request certification", remove_widget: "Remove widget", remove_role: "Remove role",
  mark_empty_expected: "Mark empty as expected", hide_for_role: "Hide for this role", exclude_widget: "Exclude widget",
  map_filter: "Map filter", upgrade_widget: "Upgrade widget",
};

const MAX_ROLES = 20;
const noteForm = z.object({ note: PublishIn.shape.note.unwrap().unwrap() });
type NoteForm = z.infer<typeof noteForm>;

/**
 * Publish as a stepper (FEAT-039, FEAT-040; RULE-024; US-142): audience → preview as role or person → the six checks
 * with inline fixes → release note → Publish (Admins) or Request publish (Creators). The step and the roles live in the
 * URL, so Back works and a request link (`?request=`) opens with the requested roles. The live version stays live until
 * a publish succeeds; the server re-runs every check on Publish.
 */
export function PublishPage() {
  const { id } = useParams();
  const [params, setParams] = useSearchParams();
  const qc = useQueryClient();
  const navigate = useNavigate();
  const d = useQuery({ queryKey: ["dashboard-draft", id], queryFn: () => api<DashboardDetail>(`/api/v1/dashboards/${id}`) });
  const roles = useQuery({ queryKey: ["roles"], queryFn: () => api<{ items: RoleRef[] }>("/api/v1/roles") });
  const dash = d.data;
  const request = dash?.publish_request?.status === "open" && (params.get("request") === dash.publish_request.id || !dash.can_publish)
    ? dash.publish_request : null;
  const step = (STEPS.some((s) => s.key === params.get("step")) ? params.get("step") : "audience") as Step;
  const targets = useMemo(() => (params.get("roles") ?? "").split(",").filter(Boolean), [params]);

  // First visit: start from the request's roles, else the live version's.
  useEffect(() => {
    if (!dash || params.has("roles")) return;
    const initial = (request?.roles ?? dash.live?.roles ?? []).map((r) => r.key);
    setParams((p) => { const n = new URLSearchParams(p); n.set("roles", initial.join(",")); return n; }, { replace: true });
  }, [dash]); // eslint-disable-line react-hooks/exhaustive-deps

  const [declining, setDeclining] = useState(false);
  const go = (next: Step) => setParams((p) => { const n = new URLSearchParams(p); n.set("step", next); return n; });
  const setTargets = (keys: string[]) => setParams((p) => { const n = new URLSearchParams(p); n.set("roles", keys.join(",")); return n; }, { replace: true });

  if (d.isLoading || roles.isLoading) return <div className="tl grid gap-4" aria-busy="true"><Skeleton className="h-10 w-1/3" /><Skeleton className="h-96" /></div>;
  if (d.error) return <div className="tl"><FormAlert error={asApiError(d.error)} /></div>;
  const all = roles.data?.items ?? [];
  const chosen = targets.map((k) => all.find((r) => r.key === k)).filter((r): r is RoleRef => !!r);
  const index = STEPS.findIndex((s) => s.key === step);
  const reachable = (i: number) => i <= index || (i > 0 && chosen.length > 0 && i <= index + 1);
  // A fix changes a report, not the dashboard's revision, so checks and previews are refreshed by key.
  const refresh = () => {
    for (const key of ["dashboard-draft", "prechecks", "preview-as"]) void qc.invalidateQueries({ queryKey: [key, id] });
    void qc.invalidateQueries({ queryKey: ["dashboard", id, true] });
    void qc.invalidateQueries({ queryKey: [GROUPS_KEY] }); // the sidebar's groups show what is published (US-171)
  };

  return (
    <div className="tl grid gap-4">
      <header className="grid gap-2">
        <Button asChild variant="ghost" size="sm" className="w-fit"><Link to={`/dashboards/${dash!.id}/edit`}><ArrowLeft aria-hidden />Back to the composer</Link></Button>
        <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
          <h1 className="text-title">{dash!.can_publish ? "Publish" : "Request publish"} · {dash!.title}</h1>
          {request && <span className="text-ink-muted">Requested by {request.requested_by.name} · {formatRelative(request.created_at)}</span>}
        </div>
        <p className="text-small text-ink-muted">
          {dash!.live ? `Live version ${dash!.live.version} stays as it is until you publish.` : "Nothing is live yet. Readers in the chosen roles see it once it is published."}
          {!dash!.can_publish && " Only an Admin publishes to roles; you send them a request with the same checks."}
        </p>
      </header>

      {request && (
        <Alert>
          <div className="flex flex-wrap items-center justify-between gap-2">
            <p>{request.note ? <><strong>{request.requested_by.name}:</strong> “{request.note}”</> : <>{request.requested_by.name} asks to publish this to {request.roles.map((r) => r.name).join(", ") || "its audience"}.</>}</p>
            {dash!.can_publish && <Button variant="secondary" size="sm" onClick={() => setDeclining(true)}>Return to {request.requested_by.name} with a note</Button>}
          </div>
        </Alert>
      )}

      <Steps steps={STEPS} current={step} reachable={reachable} onSelect={go} label="Publish steps" />

      <Card className="grid gap-4 p-4">
        {step === "audience" && <Audience roles={all} targets={targets} onChange={setTargets} onNext={() => go("preview")} />}
        {step === "preview" && <PreviewStep dashboard={dash!} roles={chosen} onBack={() => go("audience")} onNext={() => go("checks")} />}
        {step === "checks" && (
          <ChecksStep dashboard={dash!} roles={chosen} onRemoveRole={(k) => setTargets(targets.filter((t) => t !== k))} onFixed={refresh}
            onBack={() => go("preview")} onNext={() => go("note")} />
        )}
        {step === "note" && (
          <NoteStep dashboard={dash!} roles={chosen} request={request} onBack={() => go("checks")}
            onPublished={(out) => { refresh(); void qc.invalidateQueries({ queryKey: ["versions", id] }); navigate(`/dashboards/${id}/versions`, { state: { published: out } }); }}
            onRequested={() => { refresh(); toast("Your request was sent to the Admins."); navigate(`/dashboards/${id}/edit`); }} />
        )}
      </Card>
      {request && (
        <DeclineDialog open={declining} request={request} onClose={() => setDeclining(false)}
          onDone={() => { refresh(); void qc.invalidateQueries({ queryKey: ["publish-requests"] }); toast(`Returned to ${request.requested_by.name}.`); navigate("/govern/publish-requests"); }} />
      )}
    </div>
  );
}

function StepFooter({ onBack, children }: { onBack?: () => void; children?: React.ReactNode }) {
  return (
    <div className="flex flex-wrap items-center justify-end gap-2 border-t border-line pt-4">
      {onBack && <Button variant="ghost" onClick={onBack}>Back</Button>}
      {children}
    </div>
  );
}

function Audience({ roles, targets, onChange, onNext }: { roles: RoleRef[]; targets: string[]; onChange: (keys: string[]) => void; onNext: () => void }) {
  const [tried, setTried] = useState(false);
  const error = targets.length === 0 ? "Choose at least one role." : targets.length > MAX_ROLES ? `Publish to at most ${MAX_ROLES} roles at a time.` : null;
  const people = roles.filter((r) => targets.includes(r.key)).reduce((n, r) => n + (r.members ?? 0), 0);
  return (
    <>
      <fieldset className="grid gap-3" aria-describedby={tried && error ? "audience-error" : undefined}>
        <legend className="mb-1 text-heading">Who should see it?</legend>
        <p className="text-small text-ink-muted">Each person sees the data their own scope allows. A role with no data scope sees empty widgets, so the checks will flag it.</p>
        <Table>
          <TableHeader><TableRow><TableHead className="w-10"><span className="sr-only">Chosen</span></TableHead><TableHead>Role</TableHead><TableHead className="text-right">People</TableHead></TableRow></TableHeader>
          <TableBody>
            {roles.map((r) => {
              const on = targets.includes(r.key);
              return (
                <TableRow key={r.key} className={on ? "bg-accent-soft" : undefined}>
                  <TableCell><Checkbox id={`role-${r.key}`} checked={on} onCheckedChange={(v) => onChange(v === true ? [...targets, r.key] : targets.filter((t) => t !== r.key))} /></TableCell>
                  <TableCell><label htmlFor={`role-${r.key}`} className="cursor-pointer">{r.name}</label>{r.kind === "system" && <Badge variant="outline" className="ml-2">System</Badge>}</TableCell>
                  <TableCell className="text-right tabular-nums">{r.members ?? "—"}</TableCell>
                </TableRow>
              );
            })}
          </TableBody>
        </Table>
      </fieldset>
      {tried && error && <p id="audience-error" role="alert" className="text-small text-status-danger">{error}</p>}
      <StepFooter>
        <span className="mr-auto text-small text-ink-muted" aria-live="polite">{targets.length} {targets.length === 1 ? "role" : "roles"} · about {people} {people === 1 ? "person" : "people"}</span>
        <Button onClick={() => { setTried(true); if (!error) onNext(); }}>Next: preview</Button>
      </StepFooter>
    </>
  );
}

function PreviewStep({ dashboard: d, roles, onBack, onNext }: { dashboard: DashboardDetail; roles: RoleRef[]; onBack: () => void; onNext: () => void }) {
  const base = `/api/v1/dashboards/${d.id}`;
  const results = useQueries({
    queries: roles.map((r) => ({
      queryKey: ["preview-as", d.id, r.key],
      queryFn: () => api<PreviewResult>(`${base}/preview`, { method: "POST", body: { as: { role: r.key } } }),
    })),
  });
  const [person, setPerson] = useState<Person | null>(null);
  const asPerson = useQuery({
    queryKey: ["preview-as", d.id, "user", person?.id],
    queryFn: () => api<PreviewResult>(`${base}/preview`, { method: "POST", body: { as: { user_id: person!.id } } }),
    enabled: !!person,
    retry: false,
  });
  return (
    <>
      <div className="grid gap-1">
        <h2 className="text-heading">What each role will see</h2>
        <p className="text-small text-ink-muted">Rendered by the server under each role's scope and hidden fields.{d.can_publish ? "" : " Row counts are shown to Admins only."}</p>
      </div>
      <Table>
        <TableHeader><TableRow><TableHead>Role</TableHead><TableHead className="text-right">People</TableHead><TableHead>Sees</TableHead><TableHead>Widgets shown</TableHead></TableRow></TableHeader>
        <TableBody>
          {roles.map((r, i) => {
            const q = results[i]!;
            return (
              <TableRow key={r.key} className="align-top">
                <TableCell className="font-medium">{r.name}</TableCell>
                <TableCell className="text-right tabular-nums">{r.members ?? "—"}</TableCell>
                <TableCell className="max-w-80">{q.isLoading ? <Skeleton className="h-4 w-48" /> : q.error ? <span className="text-status-danger">{asApiError(q.error).message}</span> : q.data!.scope_text}</TableCell>
                <TableCell>{q.data && <WidgetsShown result={q.data} />}</TableCell>
              </TableRow>
            );
          })}
        </TableBody>
      </Table>
      <section aria-labelledby="as-person" className="grid gap-2">
        <h3 id="as-person" className="text-body-strong">Preview as a person</h3>
        <p className="text-small text-ink-muted">Roles like CIO depend on each person. {d.can_publish ? "Pick anyone." : "You can pick people you shared this dashboard with."}</p>
        <div className="max-w-sm"><PeoplePicker label="Person" onPick={setPerson} exclude={person ? [person.id] : []} /></div>
        {person && (
          <div className="grid gap-1 rounded-md bg-surface-sunken p-3" aria-live="polite">
            <p className="font-medium">As {person.name}</p>
            {asPerson.isLoading ? <Skeleton className="h-4 w-64" /> : asPerson.error ? <FormAlert error={asApiError(asPerson.error)} /> : asPerson.data && (
              <>
                <p className="text-small">{asPerson.data.scope_text}{asPerson.data.rows_in_scope ? ` — rows in scope: ${Object.entries(asPerson.data.rows_in_scope).map(([k, n]) => `${k} ${n}`).join(", ")}` : ""}</p>
                <WidgetsShown result={asPerson.data} />
              </>
            )}
          </div>
        )}
      </section>
      <StepFooter onBack={onBack}><Button onClick={onNext}>Next: checks</Button></StepFooter>
    </>
  );
}

function WidgetsShown({ result }: { result: PreviewResult }) {
  const shown = result.widgets.filter((w) => w.shown && !w.reason).length;
  const issues = result.widgets.filter((w) => !w.shown || w.reason);
  const reasons = new Set(issues.map((w) => w.reason));
  // One reason for every widget (typically "depends on each person"): say it once instead of listing each widget.
  if (result.widgets.length > 0 && issues.length === result.widgets.length && reasons.size === 1) {
    const [reason] = reasons;
    return <span className="text-small text-ink-muted">{reason === result.scope_text ? "Per person: preview a named person below." : `None of ${issues.length}: ${reason ?? "hidden"}`}</span>;
  }
  return (
    <details className="text-small">
      <summary className="cursor-pointer">{shown} of {result.widgets.length}{issues.length ? ` · ${issues.length} hidden or empty` : ""}</summary>
      <ul className="mt-1 grid gap-0.5">
        {result.widgets.map((w) => (
          <li key={w.item_id} className="flex gap-1.5">
            {w.shown && !w.reason ? <Check aria-label="Shown" className="mt-0.5 size-3.5 shrink-0 text-status-ok" /> : <EyeOff aria-label="Hidden or empty" className="mt-0.5 size-3.5 shrink-0 text-ink-muted" />}
            <span>{w.title}{w.reason ? ` — ${w.reason}` : ""}{w.rows !== undefined ? ` (${w.rows} rows)` : ""}</span>
          </li>
        ))}
      </ul>
    </details>
  );
}

/** A Creator always fails "You can publish to roles"; that is what the request is for, so it does not block them. */
const blocking = (c: Prechecks["checks"][number], canPublish: boolean) => !c.passed && (canPublish || c.check !== "publish_permission");

function useChecks(d: DashboardDetail, roles: RoleRef[]) {
  return useQuery({
    queryKey: ["prechecks", d.id, d.revision, roles.map((r) => r.key).join(",")],
    queryFn: () => api<Prechecks>(`/api/v1/dashboards/${d.id}/prechecks`, { method: "POST", body: { target_roles: roles.map((r) => r.key) } }),
    enabled: roles.length > 0,
  });
}

function ChecksStep({ dashboard: d, roles, onRemoveRole, onFixed, onBack, onNext }: {
  dashboard: DashboardDetail;
  roles: RoleRef[];
  onRemoveRole: (key: string) => void;
  onFixed: () => void;
  onBack: () => void;
  onNext: () => void;
}) {
  const checks = useChecks(d, roles);
  const [error, setError] = useState<ApiError | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [undo, setUndo] = useState<{ label: string; run: () => Promise<unknown> } | null>(null);
  const base = `/api/v1/dashboards/${d.id}`;

  const fix = async (item: CheckItem, f: CheckFix, key: string) => {
    setError(null);
    setUndo(null);
    if (f.action === "remove_role") { onRemoveRole(f.role!); return; }
    setBusy(key);
    try {
      const itemUrl = `/api/v1/reports/${item.report_id}/items/${item.item_id}`;
      if (f.action === "remove_widget") {
        await api(itemUrl, { method: "DELETE" });
        // The report keeps an undo log; its Undo brings the widget back.
        setUndo({ label: `“${item.title}” removed from this version`, run: () => api(`/api/v1/reports/${item.report_id}/undo`, { method: "POST" }) });
      } else if (f.action === "mark_empty_expected") await api(itemUrl, { method: "PATCH", body: { empty_expected: true } });
      else if (f.action === "hide_for_role") await api(itemUrl, { method: "PATCH", body: { hidden_for_roles: [f.role_id] } });
      else if (f.action === "upgrade_widget") await api(`/api/v1/widgets/${item.widget_id}/upgrade`, { method: "POST" });
      else if (f.action === "exclude_widget") {
        await api(base, { method: "PATCH", body: { shared_filters: d.shared_filters.map((x) => (x.ref === f.filter ? { ...x, exclude_widget_ids: [...(x.exclude_widget_ids ?? []), item.widget_id!] } : x)) } });
      } else if (f.action === "request_certification") {
        for (const ref of f.items ?? []) {
          try {
            await api("/api/v1/certification/requests", { method: "POST", body: { item: ref, note: `Needed to publish ${d.title}` } });
          } catch (e) {
            if (asApiError(e).status !== 409) throw e; // already waiting for review
          }
        }
        toast("Certification requested. The check passes once a reviewer certifies it.");
      }
      onFixed();
    } catch (e) {
      setError(asApiError(e, "That fix did not apply."));
    } finally {
      setBusy(null);
    }
  };

  if (roles.length === 0) return <NoRoles onBack={onBack} />;
  if (checks.isPending) return <div className="grid gap-2" aria-busy="true" aria-label="Running the checks">{STEPS.map((s) => <Skeleton key={s.key} className="h-8" />)}</div>;
  if (checks.error) return <><FormAlert error={asApiError(checks.error, "The checks could not run.")} /><StepFooter onBack={onBack}><Button variant="secondary" onClick={() => void checks.refetch()}>Run again</Button></StepFooter></>;
  const c = checks.data!;
  const failing = c.checks.filter((x) => blocking(x, d.can_publish));
  const ordered = [...c.checks].sort((a, b) => Number(blocking(b, d.can_publish)) - Number(blocking(a, d.can_publish)));

  return (
    <>
      <div className="flex flex-wrap items-center gap-3" aria-live="polite">
        <h2 className="text-heading">Checks</h2>
        {failing.length === 0
          ? <StatusBadge status="ok">All {c.checks.length} checks pass{d.can_publish ? "" : " that you can fix"}</StatusBadge>
          : <StatusBadge status="danger">{failing.length} of {c.checks.length} {failing.length === 1 ? "needs" : "need"} a fix</StatusBadge>}
        {checks.isFetching && <span className="text-small text-ink-muted">Checking again…</span>}
      </div>
      {undo && (
        <Alert>
          <div className="flex flex-wrap items-center justify-between gap-2">
            <p>{undo.label}.</p>
            <Button variant="secondary" size="sm" onClick={async () => { const u = undo; setUndo(null); try { await u.run(); onFixed(); } catch (e) { setError(asApiError(e)); } }}><Undo2 aria-hidden />Undo</Button>
          </div>
        </Alert>
      )}
      <FormAlert error={error} />
      <ul className="grid gap-2" aria-label="Pre-publish checks">
        {ordered.map((x) => {
          const bad = blocking(x, d.can_publish);
          return (
            <li key={x.check} className={cn("grid gap-1.5 rounded-md border px-3 py-2", bad ? "border-status-danger" : "border-line")}>
              <span className="flex flex-wrap items-center gap-2">
                <StatusBadge status={x.passed ? "ok" : bad ? "danger" : "warn"}>{x.passed ? "Passed" : bad ? "Needs a fix" : "Admin step"}</StatusBadge>
                <span className={bad ? "font-medium" : undefined}>{x.label}</span>
              </span>
              {!x.passed && !bad && <p className="text-small text-ink-muted">An Admin publishes for you: send the request on the next step.</p>}
              {bad && x.items.length > 0 && (
                <ul className="grid gap-1 text-small">
                  {x.items.map((it, i) => (
                    <li key={i} className="flex flex-wrap items-center gap-x-2">
                      <span>{it.title ? <>“{it.title}”{it.report ? ` (${it.report})` : ""}: </> : ""}{it.message}</span>
                      {it.fixes.filter((f) => f.action !== "request_publish").map((f) => (
                        <Button key={f.action} variant="link" size="sm" disabled={f.available === false || busy !== null} title={f.note}
                          onClick={() => void fix(it, f, `${x.check}:${i}:${f.action}`)}>
                          {FIX_LABELS[f.action] ?? f.action}{f.action === "remove_role" && it.role_name ? ` ${it.role_name}` : ""}
                        </Button>
                      ))}
                    </li>
                  ))}
                </ul>
              )}
            </li>
          );
        })}
      </ul>
      {c.notes.length > 0 && <ul className="grid gap-0.5 text-small text-ink-muted">{c.notes.map((n) => <li key={n}>{n}</li>)}</ul>}
      <StepFooter onBack={onBack}>
        <Button variant="secondary" onClick={() => void checks.refetch()} disabled={checks.isFetching}>Run again</Button>
        <Button onClick={onNext} disabled={d.can_publish && failing.length > 0} title={d.can_publish && failing.length > 0 ? "Every check must pass before publishing" : undefined}>Next: release note</Button>
      </StepFooter>
    </>
  );
}

function NoRoles({ onBack }: { onBack: () => void }) {
  return (
    <>
      <p>Choose at least one role first.</p>
      <StepFooter onBack={onBack} />
    </>
  );
}

function NoteStep({ dashboard: d, roles, request, onBack, onPublished, onRequested }: {
  dashboard: DashboardDetail;
  roles: RoleRef[];
  request: PublishRequest | null;
  onBack: () => void;
  onPublished: (out: PublishOut) => void;
  onRequested: () => void;
}) {
  const checks = useChecks(d, roles);
  const form = useForm<NoteForm>({ resolver: zodResolver(noteForm), defaultValues: { note: request?.note ?? "" } });
  const [error, setError] = useState<ApiError | null>(null);
  const [key] = useState(() => crypto.randomUUID());
  const failing = checks.data?.checks.filter((x) => blocking(x, d.can_publish)) ?? [];
  const names = roles.map((r) => r.name).join(", ");
  const people = roles.reduce((n, r) => n + (r.members ?? 0), 0);
  const label = `${roles.length} ${roles.length === 1 ? "role" : "roles"}`;

  const submit = async (v: NoteForm) => {
    setError(null);
    const body: z.input<typeof PublishIn> = { target_roles: roles.map((r) => r.key), note: v.note.trim() || null };
    try {
      if (d.can_publish) onPublished(await api<PublishOut>(`/api/v1/dashboards/${d.id}/publish`, { method: "POST", body, idempotencyKey: key }));
      else { await api(`/api/v1/dashboards/${d.id}/request-publish`, { method: "POST", body }); onRequested(); }
    } catch (e) {
      const err = asApiError(e, d.can_publish ? "Publishing failed." : "The request was not sent.");
      if (err.code === "precheck_failed") void checks.refetch();
      setError(applyServerError(form, err));
    }
  };

  return (
    <Form {...form}>
      <form className="grid gap-4" onSubmit={form.handleSubmit(submit)} noValidate>
        <h2 className="text-heading">Release note</h2>
        <dl className="grid gap-1 text-body sm:grid-cols-[10rem_minmax(0,1fr)]">
          <dt className="text-ink-muted">Audience</dt><dd>{names || "—"} · about {people} {people === 1 ? "person" : "people"}</dd>
          <dt className="text-ink-muted">Reports</dt><dd>{d.reports.map((r) => r.title).join(", ")}</dd>
          <dt className="text-ink-muted">Checks</dt>
          <dd>{checks.isPending ? "Running…" : failing.length === 0 ? "All pass" : <Button type="button" variant="link" onClick={onBack}>{failing.length} {failing.length === 1 ? "needs" : "need"} a fix</Button>}</dd>
          <dt className="text-ink-muted">Version</dt><dd>{d.live ? `Replaces live version ${d.live.version}` : "First version"}</dd>
        </dl>
        <Field control={form.control} name="note" label="Release note" description="Optional, up to 500 characters. Shown in version history and in the notification readers get."
          render={(f) => <Textarea {...f} maxLength={500} rows={3} placeholder="What changed and who it is for" />} />
        <FormAlert error={error} />
        <StepFooter onBack={onBack}>
          {d.can_publish
            ? <Button type="submit" disabled={form.formState.isSubmitting || failing.length > 0 || checks.isPending || roles.length === 0}>Publish to {label}</Button>
            : <Button type="submit" disabled={form.formState.isSubmitting || roles.length === 0}>{request ? "Update the request" : "Send request"}</Button>}
        </StepFooter>
      </form>
    </Form>
  );
}

const declineForm = DeclineIn;
type DeclineForm = z.infer<typeof declineForm>;

function DeclineDialog({ open, request, onClose, onDone }: { open: boolean; request: PublishRequest; onClose: () => void; onDone: () => void }) {
  const form = useForm<DeclineForm>({ resolver: zodResolver(declineForm), defaultValues: { note: "" } });
  const [error, setError] = useState<ApiError | null>(null);
  const submit = async (v: DeclineForm) => {
    setError(null);
    try {
      await api(`/api/v1/publish-requests/${request.id}/decline`, { method: "POST", body: { note: v.note.trim() } });
      onDone();
    } catch (e) {
      setError(applyServerError(form, e));
    }
  };
  return (
    <Dialog open={open} onOpenChange={(o) => !o && onClose()}>
      <DialogContent>
        <Form {...form}>
          <form className="grid gap-4" onSubmit={form.handleSubmit(submit)} noValidate>
            <DialogHeader>
              <DialogTitle>Return “{request.dashboard.title}” to {request.requested_by.name}?</DialogTitle>
              <DialogDescription>Nothing is published. They get your note and can change the dashboard and ask again.</DialogDescription>
            </DialogHeader>
            <Field control={form.control} name="note" label="Note for the requester" render={(f) => <Textarea {...f} maxLength={500} rows={3} autoFocus />} />
            <FormAlert error={error} />
            <DialogFooter>
              <Button type="button" variant="secondary" onClick={onClose}>Cancel</Button>
              <Button type="submit" disabled={form.formState.isSubmitting}>Return with note</Button>
            </DialogFooter>
          </form>
        </Form>
      </DialogContent>
    </Dialog>
  );
}
