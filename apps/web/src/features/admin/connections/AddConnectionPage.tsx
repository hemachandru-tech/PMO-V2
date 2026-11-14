import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft } from "lucide-react";
import { useEffect, useState } from "react";
import { useForm } from "react-hook-form";
import { Link, useNavigate, useSearchParams } from "react-router-dom";

import { api, apiRequest, type ApiError } from "../../../api/client";
import type { ConnectionItem, ConnectionObjects, IntrospectResult, PackMatch, TestResult } from "../../../api/app";
import { Alert, FormAlert } from "../../../components/ui/alert";
import { Button } from "../../../components/ui/button";
import { Card } from "../../../components/ui/card";
import { Field, Form } from "../../../components/ui/form";
import { Input } from "../../../components/ui/input";
import { NativeSelect } from "../../../components/ui/native-select";
import { ENGINES, EngineFields, SSH_FIELDS, asEngine, bodyFor, defaults, engineName, rulesFor, savedSsh, type Details, type EngineKey } from "./engines";
import { FileUploadStep, ReplaceFile, SheetSettings } from "./FileSource";
import { Skeleton } from "../../../components/ui/skeleton";
import { Steps } from "../../../components/ui/steps";
import { applyServerError, asApiError } from "../../../lib/api-errors";
import { cn } from "../../../lib/utils";
import { ApplyPackDialog } from "../starter/ApplyPackDialog";
import { schemaRead } from "./schemaCache";
import { TestChecklist } from "./TestChecklist";
import { useAppName } from "../../../app/branding";

const STEPS = [
  { key: "engine", label: "Engine" },
  { key: "details", label: "Details" },
  { key: "test", label: "Test" },
  { key: "schema", label: "Read schema" },
] as const;
type Step = (typeof STEPS)[number]["key"];


const testKey = (id: string) => ["connection-test", id];
const schemaKey = (id: string) => ["connection-introspect", id];

/**
 * Add a connection as a full-page stepper (FEAT-005, FEAT-006; SCR-09; US-144; board ConnectionFlow): engine →
 * details → test → read schema, then on to a starter pack or Model studio. The step and the connection live in the
 * URL, so Back works and "Update details" on a broken connection opens the same flow at its details step.
 */
export function AddConnectionPage() {
  const appName = useAppName();
  const [params, setParams] = useSearchParams();
  const id = params.get("id");
  const step = (STEPS.some((s) => s.key === params.get("step")) ? params.get("step") : id ? "details" : "engine") as Step;
  const go = (next: Step, extra: Record<string, string> = {}) =>
    setParams((p) => { const n = new URLSearchParams(p); n.set("step", next); for (const [k, v] of Object.entries(extra)) n.set(k, v); return n; });
  const qc = useQueryClient();
  const connection = useQuery({ queryKey: ["connection", id], queryFn: () => api<ConnectionItem>(`/api/v1/connections/${id}`), enabled: !!id });
  const tested = id ? qc.getQueryData<TestResult>(testKey(id)) : undefined;
  const connected = connection.data?.status === "connected" || tested?.status === "connected";
  const index = STEPS.findIndex((s) => s.key === step);
  const reachable = (i: number) => i <= index || (!!id && (i <= 2 || connected));
  const engine = asEngine(connection.data?.engine ?? params.get("engine"));
  const onTested = (cid: string, result: TestResult) => {
    qc.setQueryData(testKey(cid), result);
    void qc.invalidateQueries({ queryKey: ["connection", cid] });
    go("test", { id: cid });
  };

  return (
    <div className="tl grid gap-4">
      <header className="grid gap-2">
        <Button asChild variant="ghost" size="sm" className="w-fit"><Link to="/admin/connections"><ArrowLeft aria-hidden />Back to connections</Link></Button>
        <h1 className="text-title">{id && connection.data ? connection.data.name : "Add a connection"}</h1>
        <p className="text-ink-muted">{appName} only ever reads. Use an account that cannot change data.</p>
      </header>
      <Steps steps={STEPS} current={step} reachable={reachable} onSelect={(k) => go(k)} label="Connection steps" />
      <Card className="grid gap-4 p-4">
        {step === "engine" && <EngineStep onPick={(e) => go("details", { engine: e })} />}
        {step === "details" && engine === "excel" && !id && (
          <FileUploadStep onBack={() => go("engine")} onTested={onTested} />
        )}
        {step === "details" && (engine !== "excel" || !!id) && (
          <DetailsStep engine={engine} existing={connection.data} loading={!!id && connection.isLoading}
            onBack={() => go("engine")} onTested={onTested} />
        )}
        {step === "test" && id && <TestStep id={id} name={connection.data?.name} onBack={() => go("details")} onNext={() => go("schema")} />}
        {step === "schema" && id && <SchemaStep id={id} connection={connection.data} onBack={() => go("test")} />}
      </Card>
    </div>
  );
}

function EngineStep({ onPick }: { onPick: (engine: EngineKey) => void }) {
  return (
    <section className="grid gap-3" aria-labelledby="engine-title">
      <h2 id="engine-title" className="text-heading">Which system?</h2>
      <ul className="grid gap-3 sm:grid-cols-2">
        {ENGINES.map((e) => (
          <li key={e.key}>
            <button type="button" onClick={() => onPick(e.key)}
              className={cn("flex w-full items-start gap-3 rounded-lg border border-line p-4 text-left focus-visible:focus-ring",
                "hover:border-accent hover:bg-accent-soft")}>
              <e.icon aria-hidden className="mt-0.5 size-5 text-ink-muted" />
              <span className="grid gap-0.5">
                <span className="text-body-strong">{e.name}</span>
                <span className="text-small text-ink-muted">{e.note}</span>
              </span>
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}

function DetailsStep({ engine, existing, loading, onBack, onTested }: {
  engine: EngineKey;
  existing?: ConnectionItem;
  loading: boolean;
  onBack: () => void;
  onTested: (id: string, result: TestResult) => void;
}) {
  const [error, setError] = useState<ApiError | null>(null);
  const ssh = savedSsh(existing);
  const form = useForm<Details>({ resolver: zodResolver(rulesFor(engine, !!existing, ssh)), mode: "onTouched", defaultValues: defaults(engine, existing) });
  useEffect(() => { if (existing) form.reset(defaults(engine, existing)); }, [existing?.id]); // eslint-disable-line react-hooks/exhaustive-deps
  const freshness = form.watch("freshness");
  const submit = async (v: Details) => {
    setError(null);
    const body = bodyFor(engine, v, !!ssh);
    try {
      let cid = existing?.id;
      if (cid) {
        await apiRequest(`/api/v1/connections/${cid}`, { method: "PATCH", body, ifMatch: existing!.etag });
      } else {
        cid = (await api<{ connection_id: string }>("/api/v1/connections", { method: "POST", body: { ...body, engine } })).connection_id;
      }
      for (const k of ["password", "ssh_key", "ssh_cert", "ssh_cert_remove", "ssh_passphrase", "ssh_password", "ssh_repin"] as const) form.resetField(k);
      onTested(cid, await api<TestResult>(`/api/v1/connections/${cid}/test`, { method: "POST" }));
    } catch (err) {
      setError(applyServerError(form, err, { fields: { approved_schemas: "schemas", "tls.mode": "tls", tls: "tls", freshness: "freshness",
        client_secret: "password", "tls.trust_server_certificate": "trust", ...SSH_FIELDS } }));
    }
  };
  if (loading) return <Skeleton className="h-64" />;
  const c = form.control;
  const busy = form.formState.isSubmitting;
  const file = existing && (existing.engine === "excel" || existing.engine === "csv");
  return (
    <Form {...form}>
      <form className="grid gap-4" onSubmit={form.handleSubmit(submit)} aria-labelledby="details-title" noValidate>
        <h2 id="details-title" className="text-heading">Connection details · {engineName(existing?.engine ?? engine)}</h2>
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          <Field control={c} name="name" label="Name people will see" render={(f) => <Input {...f} maxLength={80} />} />
          {!file && <EngineFields engine={engine} control={c} existing={!!existing} saved={ssh} />}
          <Field control={c} name="freshness" label="Freshness" description="Answers always show the time the data was read." render={(f) => (
            <NativeSelect {...f}>
              <option value="live">Live, cached for up to 15 minutes</option>
              <option value="cached">Cached for a set time</option>
            </NativeSelect>
          )} />
          {freshness === "cached" && <Field control={c} name="minutes" label="Cache for (minutes)" render={(f) => <Input {...f} inputMode="numeric" />} />}
        </div>
        {existing && existing.status !== "draft" && !file && (
          <Alert><p>Changing where the data lives, the account or its secret puts the connection back to draft until it passes the test again.</p></Alert>
        )}
        {file && <ReplaceFile id={existing!.id} onDone={() => void 0} />}
        <FormAlert error={error} />
        <div className="flex flex-wrap gap-2">
          <Button type="button" variant="ghost" onClick={onBack} disabled={!!existing}>Back</Button>
          <Button type="submit" disabled={busy}>{busy ? "Testing…" : existing ? "Save and test" : "Create and test"}</Button>
        </div>
      </form>
    </Form>
  );
}

function TestStep({ id, name, onBack, onNext }: { id: string; name?: string; onBack: () => void; onNext: () => void }) {
  const appName = useAppName();
  const qc = useQueryClient();
  const result = useQuery({ queryKey: testKey(id), queryFn: () => null as TestResult | null, enabled: false, initialData: null, staleTime: Infinity });
  const run = useMutation({
    mutationFn: () => api<TestResult>(`/api/v1/connections/${id}/test`, { method: "POST" }),
    onSuccess: (r) => { qc.setQueryData(testKey(id), r); void qc.invalidateQueries({ queryKey: ["connection", id] }); void qc.invalidateQueries({ queryKey: ["connections"] }); },
  });
  const read = useMutation({
    mutationFn: () => api<IntrospectResult>(`/api/v1/connections/${id}/introspect`, { method: "POST" }),
    onSuccess: (r) => { qc.setQueryData(schemaKey(id), r); schemaRead(qc, id); onNext(); },
  });
  const switchMode = useMutation({
    mutationFn: async () => {
      const current = await api<ConnectionItem>(`/api/v1/connections/${id}`);
      await apiRequest(`/api/v1/connections/${id}`, { method: "PATCH", body: { mode: "webapi" }, ifMatch: current.etag });
      return api<TestResult>(`/api/v1/connections/${id}/test`, { method: "POST" });
    },
    onSuccess: (r) => { qc.setQueryData(testKey(id), r); void qc.invalidateQueries({ queryKey: ["connection", id] }); },
  });
  const r = result.data;
  return (
    <section className="grid gap-4" aria-labelledby="test-title">
      <h2 id="test-title" className="text-heading">Test{name ? ` · ${name}` : ""}</h2>
      {r ? <TestChecklist r={r} /> : <p className="text-ink-muted">Run the test to check that {appName} can reach the database, sign in, and cannot write.</p>}
      {r?.suggest_mode === "webapi" && (
        <Alert variant="warn">
          <p>{r.message}</p>
          <div className="mt-2"><Button size="sm" onClick={() => switchMode.mutate()} disabled={switchMode.isPending}>
            {switchMode.isPending ? "Switching…" : "Switch to Web API mode and test"}</Button></div>
        </Alert>
      )}
      <FormAlert error={run.error ? asApiError(run.error) : read.error ? asApiError(read.error) : null} />
      <div className="flex flex-wrap gap-2">
        <Button variant="ghost" onClick={onBack}>{r && r.status !== "connected" ? "Change details" : "Back"}</Button>
        <Button variant={r?.status === "connected" ? "secondary" : "primary"} onClick={() => run.mutate()} disabled={run.isPending}>
          {run.isPending ? "Testing…" : r ? "Test again" : "Run the test"}
        </Button>
        {r?.status === "connected" && <Button onClick={() => read.mutate()} disabled={read.isPending}>{read.isPending ? "Reading the schema…" : "Read schema"}</Button>}
      </div>
    </section>
  );
}


function SchemaStep({ id, connection, onBack }: { id: string; connection?: ConnectionItem; onBack: () => void }) {
  const appName = useAppName();
  const qc = useQueryClient();
  const navigate = useNavigate();
  const last = useQuery({ queryKey: schemaKey(id), queryFn: () => null as IntrospectResult | null, enabled: false, initialData: null, staleTime: Infinity });
  const objects = useQuery({ queryKey: ["connection-objects", id], queryFn: () => api<ConnectionObjects>(`/api/v1/connections/${id}/objects`) });
  const packs = useQuery({ queryKey: ["pack-match", id], queryFn: () => api<{ matches: PackMatch[] }>(`/api/v1/starter-packs/match?connection_id=${id}`), enabled: !!objects.data?.items.length });
  const [packOpen, setPackOpen] = useState(false);
  const read = useMutation({
    mutationFn: () => api<IntrospectResult>(`/api/v1/connections/${id}/introspect`, { method: "POST" }),
    onSuccess: (r) => { qc.setQueryData(schemaKey(id), r); schemaRead(qc, id); },
  });
  const install = useMutation({
    mutationFn: (pack: PackMatch) => api<{ model_id: string }>(`/api/v1/starter-packs/${pack.pack_id}/install`, { method: "POST", body: { connection_id: id } }),
    onSuccess: (r) => { void qc.invalidateQueries({ queryKey: ["connections"] }); navigate(`/admin/models/${r.model_id}`); },
  });
  const build = useMutation({
    mutationFn: () => api<{ model_id: string }>("/api/v1/models", { method: "POST", body: { connection_id: id } }),
    onSuccess: (r) => { void qc.invalidateQueries({ queryKey: ["connections"] }); navigate(`/admin/models/${r.model_id}`); },
  });

  const items = (objects.data?.items ?? []).filter((o) => !o.removed);
  const columns = items.reduce((n, o) => n + o.columns.length, 0);
  const schemas = [...new Set(items.map((o) => o.object.split(".")[0]))];
  const unreadable = items.filter((o) => o.readable === false);
  const largest = items.reduce<(typeof items)[number] | null>((a, o) => ((o.row_estimate ?? -1) > (a?.row_estimate ?? -1) ? o : a), null);
  const match = packs.data?.matches.find((m) => m.matched);
  // Close by name but not matched: the Admin can map the pack's fields to this source (US-154).
  const close = match ? undefined : packs.data?.matches.find((m) => m.confidence >= 0.1);
  const r = last.data;
  const error = [read.error, install.error, build.error, objects.error].find(Boolean);

  return (
    <section className="grid gap-4" aria-labelledby="schema-title">
      <h2 id="schema-title" className="text-heading">Read schema</h2>
      <p className="text-small text-ink-muted">{appName} reads names, types and keys only — never data values.</p>
      {r?.status === "incomplete" && <Alert variant="warn"><p>{r.message ?? r.error?.message ?? "The schema was only partly read. Try again."}</p></Alert>}
      {objects.isLoading ? <Skeleton className="h-24" /> : items.length === 0 ? (
        <p className="text-ink-muted">Nothing has been read yet.</p>
      ) : (
        <dl className="grid gap-x-6 gap-y-2 sm:grid-cols-[max-content_1fr]">
          <dt className="text-ink-muted">Read</dt>
          <dd>{items.length.toLocaleString()} {connection?.engine === "mongodb" ? "collections" : connection?.engine === "excel" || connection?.engine === "csv" ? "sheets" : "tables and views"}, {columns.toLocaleString()} {connection?.engine === "mongodb" ? "fields" : "columns"}{r?.diff ? ` · ${r.diff.added.length} new, ${r.diff.removed.length} removed, ${r.diff.changed.length} changed since last time` : ""}</dd>
          <dt className="text-ink-muted">Schemas</dt>
          <dd>{schemas.join(", ")}</dd>
          {largest?.row_estimate != null && (<><dt className="text-ink-muted">Largest table</dt><dd>{largest.object} · about {largest.row_estimate.toLocaleString()} rows</dd></>)}
          <dt className="text-ink-muted">Starter pack</dt>
          <dd>{packs.isLoading ? "Checking…" : match ? `${match.pack_key} ${match.version} matches (${Math.round(match.confidence * 100)}% confidence)${connection?.model.id ? "; it can be added to the existing model" : ""}` : close ? `${close.pack_key} ${close.version} is close (${Math.round(close.confidence * 100)}% of its fields found by name); map its fields to use it.` : "No pack matches this source; describe it in Model studio."}</dd>
        </dl>
      )}
      {unreadable.length > 0 && (
        <Alert variant="warn">
          <p><strong>{unreadable.length === 1 ? "1 table can't be read" : `${unreadable.length} tables can't be read`}:</strong> {unreadable.map((o) => o.object).join(", ")}.</p>
          <p>The account {appName} signs in with may see {unreadable.length === 1 ? "this table" : "these tables"} but not read {unreadable.length === 1 ? "its" : "their"} rows, so {unreadable.length === 1 ? "it stays" : "they stay"} out of the model and the starter pack. To use {unreadable.length === 1 ? "it" : "them"}, give the account's security role Read on {unreadable.length === 1 ? "it" : "them"}, then read the schema again.</p>
        </Alert>
      )}
      {/* An empty list means nothing broke (or there is no model yet): only a non-empty list is a warning (BUG-003). */}
      {!!r?.model_broken?.length && (
        <Alert variant="warn"><p>Tables the model uses changed or can no longer be read ({r.model_broken.map((b) => b.entity).join(", ")}). Open Model studio to fix the model.</p></Alert>
      )}
      {!!connection?.model.id && !!r?.diff?.added.length && (
        <Alert><p><strong>{r.diff.added.length === 1 ? "1 new table" : `${r.diff.added.length} new tables`}</strong> ({r.diff.added.slice(0, 5).join(", ")}{r.diff.added.length > 5 ? " …" : ""}) {r.diff.added.length === 1 ? "is" : "are"} not in the model yet. Open Model studio and add {r.diff.added.length === 1 ? "it" : "them"} under “Tables not in the model”, then approve the model.</p></Alert>
      )}
      {!!connection?.model.id && !!r?.diff?.changed.length && (
        <Alert><p><strong>{r.diff.changed.length === 1 ? "1 table changed" : `${r.diff.changed.length} tables changed`}</strong> ({r.diff.changed.slice(0, 5).join(", ")}{r.diff.changed.length > 5 ? " …" : ""}). New columns are not in the model until you add them: open Model studio, pick them under “Columns not in the model”, then approve the model.</p></Alert>
      )}
      {(connection?.engine === "excel" || connection?.engine === "csv") && items.length > 0 && (
        <SheetSettings id={id} onSaved={() => void qc.invalidateQueries({ queryKey: ["pack-match", id] })} />
      )}
      <FormAlert error={error ? asApiError(error) : null} />
      {items.length > 0 && !connection?.model.id && (
        <Alert>
          <p><strong>{connection?.name ?? "The connection"} is connected.</strong> Nobody can ask about it yet: add its tables to a model, describe what the fields mean, then approve the model.</p>
        </Alert>
      )}
      <div className="flex flex-wrap gap-2">
        <Button variant="ghost" onClick={onBack}>Back</Button>
        <Button variant="secondary" onClick={() => read.mutate()} disabled={read.isPending}>{read.isPending ? "Reading…" : items.length ? "Read again" : "Read schema"}</Button>
        {connection?.model.id ? (
          <>
            {(match || close) && <Button variant="secondary" onClick={() => setPackOpen(true)}>{match ? "Add starter pack" : "Map the starter pack"}</Button>}
            <Button asChild><Link to={`/admin/models/${connection.model.id}`}>Open Model studio</Link></Button>
          </>
        ) : items.length > 0 && (
          <>
            {match && <Button onClick={() => install.mutate(match)} disabled={install.isPending}>{install.isPending ? "Installing…" : `Install ${match.pack_key}`}</Button>}
            {close && <Button variant="secondary" onClick={() => setPackOpen(true)}>Map the starter pack</Button>}
            <Button variant={match ? "secondary" : "primary"} onClick={() => build.mutate()} disabled={build.isPending}>{build.isPending ? "Starting…" : "Build the model in Model studio"}</Button>
          </>
        )}
      </div>
      {connection && (
        <ApplyPackDialog connectionId={id} connectionName={connection.name} open={packOpen} onClose={() => setPackOpen(false)}
          onApplied={(r) => { void qc.invalidateQueries({ queryKey: ["connections"] }); navigate(`/admin/models/${r.model_id}`); }} />
      )}
    </section>
  );
}
