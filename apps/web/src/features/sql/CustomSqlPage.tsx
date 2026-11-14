import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CircleCheck, CircleDashed, Code2, OctagonAlert, Play, Plus, ShieldCheck, Sparkles } from "lucide-react";
import { lazy, Suspense, useMemo, useRef, useState, type FormEvent } from "react";
import { Link, useLocation, useNavigate, useParams } from "react-router-dom";
import { toast } from "sonner";
import type { z } from "zod";

import { api, type ApiError } from "../../api/client";
import type { Home } from "../../api/app";
import { VISUAL_NAMES, type ReportListItem } from "../../api/authoring";
import type { Catalog, SqlCheck, SqlCheckResult, SqlDefinition, SqlRunResult } from "../../api/govern";
import type { DraftIn, SaveIn, SqlIn } from "../../api/schemas";
import { ResultTable } from "../../components/ResultTable";
import { ContentBadge } from "../../components/ContentBadge";
import { formatAsOf, formatRelative } from "../../components/format";
import type { SqlEditorHandle } from "../../components/SqlEditor";
import { Alert, FormAlert } from "../../components/ui/alert";
import { Badge } from "../../components/ui/badge";
import { Button } from "../../components/ui/button";
import { Card } from "../../components/ui/card";
import { Checkbox } from "../../components/ui/checkbox";
import { DataTable, type DataColumn } from "../../components/ui/data-table";
import { Input } from "../../components/ui/input";
import { Label } from "../../components/ui/label";
import { NativeSelect } from "../../components/ui/native-select";
import { RadioGroup, RadioGroupItem } from "../../components/ui/radio-group";
import { Skeleton } from "../../components/ui/skeleton";
import { asApiError } from "../../lib/api-errors";
import { cn } from "../../lib/utils";
import { RequestCertification } from "../fields/RequestCertification";

// CodeMirror loads with this screen only.
const SqlEditor = lazy(() => import("../../components/SqlEditor").then((m) => ({ default: m.SqlEditor })));

/** The validator's steps in order (RULE-002 – 007); the list stops at the first failure. */
const STEPS: [string, string][] = [
  ["engine_supported", "This data source takes custom SQL"],
  ["page_filters_placeholder", ":page_filters placeholder"],
  ["parse", "The SQL can be read"],
  ["single_select", "Exactly one SELECT statement"],
  ["read_only", "Read-only: no changes, comments, locks or system functions"],
  ["allowed_objects", "Only tables in the approved model"],
  ["visible_fields", "Only fields you can see"],
  ["placeholder_position", ":page_filters sits in a WHERE condition"],
  ["scope_applied", "Your data scope is added"],
  ["limits", "Row and time limits"],
];
const TOKEN_TTL_MS = 4.5 * 60 * 1000;

/** Build → Custom SQL (FEAT-031; SCR-06; US-080): my custom SQL definitions. */
export function CustomSqlListPage() {
  const navigate = useNavigate();
  const list = useQuery({ queryKey: ["custom-sql"], queryFn: () => api<{ items: SqlDefinition[] }>("/api/v1/custom-sql") });
  const columns = useMemo<DataColumn<SqlDefinition>[]>(() => [
    { id: "title", header: "Title", alwaysVisible: true, cell: (d) => <Link to={`/sql/${d.id}`} className="font-medium text-link hover:underline focus-visible:focus-ring">{d.title}</Link> },
    { id: "badge", header: "Status", cell: (d) => <ContentBadge badge={d.badge} /> },
    { id: "follows_page_filters", header: "Page filters", value: (d) => (d.follows_page_filters ? "Follows" : "Ignores") },
    { id: "rows", header: "Rows at last run", numeric: true, value: (d) => d.last_check?.row_count ?? null },
    { id: "updated_at", header: "Updated", date: true, cell: (d) => formatRelative(d.updated_at) },
  ], []);
  return (
    <div className="tl grid gap-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div className="grid gap-1">
          <h1 className="text-title">Custom SQL</h1>
          <p className="text-ink-muted">Read-only SQL for questions the model can't express yet. Your scope and hidden fields are applied when it runs.</p>
        </div>
        <Button onClick={() => navigate("/sql/new")}><Plus aria-hidden />New custom SQL</Button>
      </div>
      <DataTable data={list.data?.items ?? []} columns={columns} label="My custom SQL" getRowId={(d) => d.id} loading={list.isLoading}
        error={list.error && <FormAlert error={asApiError(list.error)} />}
        empty={{ title: "No custom SQL yet", body: "Write or draft a SELECT, check it, run it, then save it as a widget.", action: <Button asChild variant="secondary"><Link to="/sql/new"><Code2 aria-hidden />Write SQL</Link></Button> }}
        onRowActivate={(d) => navigate(`/sql/${d.id}`)} />
    </div>
  );
}

/** The editor (FEAT-031; SCR-06; US-080): draft with chat, edit, check, run, then save as a widget. Never runs on its own. */
export function CustomSqlPage() {
  const { id } = useParams();
  const existing = useQuery({ queryKey: ["custom-sql", id], queryFn: () => api<SqlDefinition>(`/api/v1/custom-sql/${id}`), enabled: !!id });
  if (id && existing.isLoading) return <div className="tl grid gap-4" aria-busy="true"><Skeleton className="h-10 w-1/3" /><Skeleton className="h-96" /></div>;
  if (existing.error) return <div className="tl"><FormAlert error={asApiError(existing.error)} /></div>;
  return <Editor key={id ?? "new"} definition={existing.data ?? null} />;
}

interface Checked { sql: string; at: number; result: SqlCheckResult }

/** SQL handed over from a Direct SQL answer in Ask (US-257b): a new definition starts from it, not yet saved. */
interface FromAsk { sql?: string; connection_id?: string; drafted_by_ai?: boolean }

function Editor({ definition }: { definition: SqlDefinition | null }) {
  const navigate = useNavigate();
  const qc = useQueryClient();
  const location = useLocation();
  const fromAsk = (definition ? null : location.state) as FromAsk | null;
  const home = useQuery({ queryKey: ["home"], queryFn: () => api<Home>("/api/v1/home") });
  const sources = home.data?.greeting.scopes ?? [];
  const [connectionId, setConnectionId] = useState(definition?.connection_id ?? fromAsk?.connection_id ?? "");
  const cid = connectionId || sources[0]?.connection_id || "";
  const catalog = useQuery({ queryKey: ["catalog", cid], queryFn: () => api<Catalog>(`/api/v1/query/catalog?connection_id=${cid}`), enabled: !!cid });
  const schema = useMemo(() => Object.fromEntries((catalog.data?.entities ?? []).map((e) => [e.table, e.fields.map((f) => f.column)])), [catalog.data]);
  const editor = useRef<SqlEditorHandle>(null);

  const [sql, setSql] = useState(definition?.sql ?? fromAsk?.sql ?? "");
  const [drafted, setDrafted] = useState(definition?.drafted_by_ai ?? fromAsk?.drafted_by_ai ?? false);
  const [checked, setChecked] = useState<Checked | null>(null);
  const [failed, setFailed] = useState<{ sql: string; error: ApiError; checks: SqlCheck[] } | null>(null);
  const [run, setRun] = useState<{ sql: string; result: SqlRunResult } | null>(null);
  const [runError, setRunError] = useState<ApiError | null>(null);
  const [busy, setBusy] = useState<"check" | "run" | null>(null);
  const current = (x: { sql: string } | null) => x !== null && x.sql === sql;

  const check = async (): Promise<Checked | null> => {
    if (current(checked) && Date.now() - checked!.at < TOKEN_TTL_MS) return checked;
    setBusy("check");
    setFailed(null);
    try {
      const result = await api<SqlCheckResult>("/api/v1/custom-sql/check", { method: "POST", body: { connection_id: cid, sql } satisfies z.input<typeof SqlIn> });
      const c = { sql, at: Date.now(), result };
      setChecked(c);
      return c;
    } catch (e) {
      const error = asApiError(e);
      setChecked(null);
      setFailed({ sql, error, checks: (error.details.checks as SqlCheck[] | undefined) ?? [] });
      return null;
    } finally {
      setBusy(null);
    }
  };
  const runNow = async () => {
    setRunError(null);
    const c = await check();
    if (!c) return;
    setBusy("run");
    try {
      const result = await api<SqlRunResult>("/api/v1/custom-sql/run", { method: "POST", body: { connection_id: cid, sql: c.sql, check_token: c.result.check_token } });
      setRun({ sql: c.sql, result });
    } catch (e) {
      setRunError(asApiError(e));
    } finally {
      setBusy(null);
    }
  };

  const checks = current(checked) ? checked!.result.checks : current(failed) ? failed!.checks : null;
  const ran = current(run) ? run!.result : null;
  const ready = !!ran && current(checked);

  return (
    <div className="tl grid gap-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div className="grid gap-1">
          <div className="flex flex-wrap items-center gap-2">
            <h1 className="text-title">{definition?.title ?? "New custom SQL"}</h1>
            <ContentBadge badge={definition?.badge ?? "custom_sql"} />
          </div>
          <p className="text-ink-muted">Read-only SQL{catalog.data ? ` on ${sources.find((s) => s.connection_id === cid)?.connection_name ?? "this source"}` : ""}. Your scope and hidden fields are applied when it runs.</p>
        </div>
        <Button asChild variant="ghost"><Link to="/sql">Cancel</Link></Button>
      </div>
      <div className="grid gap-4 lg:grid-cols-[16rem_minmax(0,1fr)]">
        <aside aria-label="Tables you can use" className="min-w-0">
          <SchemaList catalog={catalog.data} loading={catalog.isLoading} onPick={(t) => editor.current?.insert(t)} />
        </aside>
        <div className="grid min-w-0 content-start gap-4">
          <Card className="gap-3 p-4">
            {!definition && sources.length > 1 && (
              <div className="grid gap-1.5 sm:max-w-xs">
                <Label htmlFor="sql-source">Data source</Label>
                <NativeSelect id="sql-source" value={cid} onChange={(e) => { setConnectionId(e.target.value); setChecked(null); setRun(null); }}>
                  {sources.map((s) => <option key={s.connection_id} value={s.connection_id}>{s.connection_name}</option>)}
                </NativeSelect>
              </div>
            )}
            <DraftWithChat connectionId={cid} onDraft={(text) => { setSql(text); setDrafted(true); }} />
            <Suspense fallback={<Skeleton className="h-48" />}>
              <SqlEditor ref={editor} value={sql} onChange={setSql} engine={catalog.data?.engine} schema={schema} label="SQL" describedBy="sql-help"
                placeholder="SELECT … FROM projects WHERE :page_filters" />
            </Suspense>
            <div className="flex flex-wrap items-center justify-between gap-2">
              <p id="sql-help" className="text-small text-ink-muted">
                {drafted ? "Drafted from your words · edit freely. " : ""}Add <code className="font-mono">:page_filters</code> to a WHERE condition so dashboard filters reach this widget.
              </p>
              <div className="flex gap-2">
                <Button variant="secondary" size="sm" disabled={!sql.trim() || !cid || busy !== null} onClick={() => void check()}><ShieldCheck aria-hidden />Check</Button>
                <Button size="sm" disabled={!sql.trim() || !cid || busy !== null} onClick={() => void runNow()}><Play aria-hidden />Run</Button>
              </div>
            </div>
          </Card>
          {(checks || busy === "check") && <Checks checks={checks} result={current(checked) ? checked!.result : null} error={current(failed) ? failed!.error : null} />}
          <FormAlert error={runError} />
          {busy === "run" && <Skeleton className="h-40" />}
          {ran && <Result r={ran} />}
          {ready && <SaveAsWidget definition={definition} connectionId={cid} checked={checked!} result={ran!} drafted={drafted}
            onSaved={(definitionId) => {
              void qc.invalidateQueries({ queryKey: ["custom-sql"] });
              if (!definition) navigate(`/sql/${definitionId}`, { replace: true });
            }} />}
          {!ready && sql.trim() && <p className="text-small text-ink-muted">Check and run the SQL to save it as a widget.</p>}
        </div>
      </div>
    </div>
  );
}

function SchemaList({ catalog, loading, onPick }: { catalog?: Catalog; loading: boolean; onPick: (text: string) => void }) {
  const [q, setQ] = useState("");
  const term = q.trim().toLowerCase();
  const entities = (catalog?.entities ?? []).map((e) => ({
    ...e, fields: e.fields.filter((f) => !term || e.table.toLowerCase().includes(term) || f.column.toLowerCase().includes(term) || f.label.toLowerCase().includes(term)),
  })).filter((e) => e.fields.length > 0);
  return (
    <Card className="gap-3 p-4">
      <h2 className="text-heading">Tables you can use</h2>
      <Input type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search tables and fields" aria-label="Search tables and fields" className="h-control-sm" />
      {loading && <Skeleton className="h-32" />}
      <div className="grid max-h-[28rem] gap-1 overflow-y-auto">
        {entities.map((e, i) => (
          <details key={e.key} open={i === 0 || !!term} className="grid gap-1">
            <summary className="cursor-pointer rounded-sm font-mono text-code focus-visible:focus-ring">
              {e.table}
            </summary>
            <ul className="grid gap-0.5 pl-3" aria-label={`${e.label} fields`}>
              <li><button type="button" className="min-h-6 text-small text-link hover:underline focus-visible:focus-ring" onClick={() => onPick(e.table)}>Insert table name</button></li>
              {e.fields.map((f) => (
                <li key={f.ref}>
                  <button type="button" onClick={() => onPick(f.column)} title={`${f.label} · insert at the cursor`}
                    className="flex min-h-6 w-full items-center justify-between gap-2 rounded-sm px-1 text-left font-mono text-code hover:bg-surface-sunken focus-visible:focus-ring">
                    <span className="truncate">{f.column}</span><span className="shrink-0 text-ink-muted">{f.data_type}</span>
                  </button>
                </li>
              ))}
            </ul>
          </details>
        ))}
      </div>
      <p className="text-small text-ink-muted">Fields hidden from your role are not listed and can't be used.</p>
    </Card>
  );
}

function DraftWithChat({ connectionId, onDraft }: { connectionId: string; onDraft: (sql: string) => void }) {
  const [question, setQuestion] = useState("");
  const draft = useMutation({
    mutationFn: () => api<{ sql: string; explanation: string | null; drafted_by_ai: boolean; note: string }>("/api/v1/custom-sql/draft", {
      method: "POST", body: { connection_id: connectionId, question: question.trim() } satisfies z.input<typeof DraftIn>,
    }),
    onSuccess: (d) => {
      onDraft(d.sql);
      toast("SQL drafted", { description: d.explanation ?? d.note });
    },
  });
  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (question.trim()) draft.mutate();
  };
  return (
    <form onSubmit={submit} className="grid gap-1.5">
      <Label htmlFor="sql-ask">Describe the query in words</Label>
      <div className="flex gap-2">
        <Input id="sql-ask" value={question} maxLength={1000} onChange={(e) => setQuestion(e.target.value)} placeholder="Average days late per platform for overdue projects" />
        <Button type="submit" variant="secondary" disabled={!question.trim() || !connectionId || draft.isPending}><Sparkles aria-hidden />Draft SQL</Button>
      </div>
      <span className="text-small text-ink-muted">A draft replaces the SQL below. It is never run until you check and run it.</span>
      <FormAlert error={draft.error ? asApiError(draft.error) : null} />
    </form>
  );
}

function Checks({ checks, result, error }: { checks: SqlCheck[] | null; result: SqlCheckResult | null; error: ApiError | null }) {
  const byName = new Map((checks ?? []).map((c) => [c.name, c]));
  const failedAt = STEPS.findIndex(([n]) => byName.get(n)?.ok === false);
  return (
    <Card className="gap-3 p-4" role="region" aria-label="Checks">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="text-heading">Checks</h2>
        {result && <span className="text-small text-status-ok">All checks passed · model v{result.model_version}</span>}
        {error && <span className="text-small text-status-danger">Stopped at the first failure</span>}
      </div>
      {!checks ? <Skeleton className="h-24" /> : (
        <ol className="grid gap-1.5">
          {STEPS.map(([name, label], i) => {
            const c = byName.get(name);
            const state = c?.ok ? "pass" : c && !c.ok ? "fail" : failedAt >= 0 && i > failedAt ? "skipped" : "pass";
            const Icon = state === "pass" ? CircleCheck : state === "fail" ? OctagonAlert : CircleDashed;
            return (
              <li key={name} className={cn("flex items-start gap-2", state === "skipped" && "text-ink-muted")}>
                <Icon aria-hidden className={cn("mt-0.5 size-4 shrink-0", state === "pass" && "text-status-ok", state === "fail" && "text-status-danger")} />
                <span>
                  {label}
                  <span className="sr-only">: {state === "pass" ? "passed" : state === "fail" ? "failed" : "not checked"}</span>
                  {c?.detail && <span className={cn("block text-small", state === "fail" ? "text-status-danger" : "text-ink-muted")}>{c.detail}</span>}
                </span>
              </li>
            );
          })}
        </ol>
      )}
      {result?.warnings.map((w) => <Alert key={w} variant="warn"><p>{w}</p></Alert>)}
      {error && checks?.length === 0 && <FormAlert error={error} />}
    </Card>
  );
}

function Result({ r }: { r: SqlRunResult }) {
  return (
    <Card className="gap-3 p-4" role="region" aria-label="Result">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-heading">Result</h2>
        <span className="flex flex-wrap items-center gap-2 text-small text-ink-muted">
          {r.row_count.toLocaleString()} {r.row_count === 1 ? "row" : "rows"} · {(r.duration_ms / 1000).toFixed(1)} s
          {r.truncated && <Badge variant="stale">Truncated</Badge>}
          {r.suggested_visual && <Badge variant="outline">Suggested: {VISUAL_NAMES[r.suggested_visual] ?? r.suggested_visual}</Badge>}
        </span>
      </div>
      <ResultTable columns={r.columns} rows={r.rows} caption="Custom SQL result" />
      <p className="text-small text-ink-muted">
        {r.rows.length < r.row_count ? `First ${r.rows.length} rows shown. ` : ""}{r.scope?.summary ? `Your scope: ${r.scope.summary}. ` : ""}Data as of {formatAsOf(r.as_of)}.
      </p>
      {r.notes?.map((n) => <p key={n} className="text-small text-ink-muted">{n}</p>)}
    </Card>
  );
}

function SaveAsWidget({ definition, connectionId, checked, result, drafted, onSaved }: {
  definition: SqlDefinition | null;
  connectionId: string;
  checked: Checked;
  result: SqlRunResult;
  drafted: boolean;
  onSaved: (definitionId: string) => void;
}) {
  const reports = useQuery({ queryKey: ["reports"], queryFn: () => api<{ items: ReportListItem[] }>("/api/v1/reports"), enabled: !definition });
  const options = result.visuals;
  const [title, setTitle] = useState(definition?.title ?? "");
  const [visual, setVisual] = useState(options?.recommended ?? result.suggested_visual ?? "table");
  const [follow, setFollow] = useState(checked.result.follows_page_filters);
  const [reportId, setReportId] = useState("");
  const [error, setError] = useState<ApiError | null>(null);
  const [saved, setSaved] = useState<{ id: string; title: string } | null>(definition && { id: definition.id, title: definition.title });
  const [requesting, setRequesting] = useState(false);
  const save = useMutation({
    mutationFn: async () => {
      if (definition) {
        const out = await api<SqlDefinition>(`/api/v1/custom-sql/${definition.id}`, {
          method: "PATCH", body: { connection_id: connectionId, sql: checked.sql, check_token: checked.result.check_token, title: title.trim() || definition.title },
        });
        return { definitionId: out.id, title: out.title };
      }
      const out = await api<{ custom_sql_id: string; widget_id: string }>("/api/v1/widgets/custom-sql", {
        method: "POST",
        body: {
          connection_id: connectionId, sql: checked.sql, check_token: checked.result.check_token, title: title.trim(), visual,
          follow_page_filters: follow, drafted_by_ai: drafted, report_id: reportId || null,
        } satisfies z.input<typeof SaveIn>,
      });
      return { definitionId: out.custom_sql_id, title: title.trim() };
    },
    onSuccess: (out) => {
      setSaved({ id: out.definitionId, title: out.title });
      toast.success(definition ? "Custom SQL updated; widgets that use it follow." : `${out.title} saved as a widget`, {
        description: "It shows a Custom SQL badge everywhere. To use it on a published dashboard, request certification.",
      });
      onSaved(out.definitionId);
    },
    onError: (e) => setError(asApiError(e)),
  });
  const visuals = options ? [...options.suitable.map((v) => ({ v, reason: null as string | null })), ...options.disabled.map((d) => ({ v: d.visual, reason: d.reason }))] : [];
  return (
    <Card className="gap-4 p-4" role="region" aria-label="Save as a widget">
      <h2 className="text-heading">{definition ? "Save changes" : "Save as a widget"}</h2>
      <div className="grid gap-1.5 sm:max-w-md">
        <Label htmlFor="sql-title">Title</Label>
        <Input id="sql-title" value={title} maxLength={200} onChange={(e) => setTitle(e.target.value)} placeholder="Average days late by platform" />
      </div>
      {!definition && (
        <>
          {visuals.length > 0 && (
            <div className="grid gap-1.5">
              <span className="text-body-strong" id="sql-visual">Visual</span>
              <RadioGroup aria-labelledby="sql-visual" value={visual} onValueChange={setVisual} className="grid-cols-2 sm:grid-cols-4">
                {visuals.map(({ v, reason }) => (
                  <span key={v} className="flex items-center gap-2" title={reason ?? undefined}>
                    <RadioGroupItem id={`sql-visual-${v}`} value={v} disabled={!!reason} />
                    <Label htmlFor={`sql-visual-${v}`} className={cn("font-normal", reason && "text-ink-disabled")}>{VISUAL_NAMES[v] ?? v}</Label>
                  </span>
                ))}
              </RadioGroup>
            </div>
          )}
          <div className="flex items-center gap-2">
            <Checkbox id="sql-follow" checked={follow} disabled={!checked.result.follows_page_filters} onCheckedChange={(on) => setFollow(on === true)} />
            <Label htmlFor="sql-follow" className="font-normal">Follows page filters{checked.result.follows_page_filters ? "" : " (add :page_filters to turn this on)"}</Label>
          </div>
          <div className="grid gap-1.5 sm:max-w-md">
            <Label htmlFor="sql-report">Add to a report (optional)</Label>
            <NativeSelect id="sql-report" value={reportId} onChange={(e) => setReportId(e.target.value)}>
              <option value="">Library only</option>
              {(reports.data?.items ?? []).filter((r) => r.mine).map((r) => <option key={r.id} value={r.id}>{r.title}</option>)}
            </NativeSelect>
          </div>
        </>
      )}
      <FormAlert error={error} />
      <div className="flex flex-wrap gap-2">
        <Button disabled={save.isPending || (!definition && !title.trim())} onClick={() => { setError(null); save.mutate(); }}>{definition ? "Save changes" : "Save widget"}</Button>
        {saved && definition?.badge !== "certified" && <Button variant="secondary" onClick={() => setRequesting(true)}>Request certification</Button>}
      </div>
      <p className="text-small text-ink-muted">This widget is personal with a Custom SQL badge. To use it on a published dashboard, request certification.</p>
      <RequestCertification item={saved && { kind: "custom_sql", id: saved.id }} name={saved?.title ?? ""} open={requesting} onOpenChange={setRequesting} />
    </Card>
  );
}
