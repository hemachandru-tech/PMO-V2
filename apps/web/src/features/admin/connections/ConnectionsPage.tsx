import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus } from "lucide-react";
import { useCallback, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";

import { api, type ApiError } from "../../../api/client";
import type { ConnectionItem, TestResult } from "../../../api/app";
import { formatAsOf } from "../../../components/format";
import { Alert, FormAlert } from "../../../components/ui/alert";
import { Badge } from "../../../components/ui/badge";
import { Button } from "../../../components/ui/button";
import { Card } from "../../../components/ui/card";
import { DataTable, type DataColumn } from "../../../components/ui/data-table";
import { FilterChip } from "../../../components/ui/filter-chip";
import { StatusBadge } from "../../../components/ui/status-badge";
import { asApiError } from "../../../lib/api-errors";
import { cn } from "../../../lib/utils";
import { ApplyPackDialog } from "../starter/ApplyPackDialog";
import { ASK_MODE_LABELS, AskModeDialog } from "./AskModeDialog";
import { schemaRead } from "./schemaCache";
import { TestChecklist } from "./TestChecklist";
import { useAppName } from "../../../app/branding";

/** Statuses the Admin overview counts as needing attention (`?status=attention`, US-143). */
const ATTENTION = ["error", "incomplete"];

interface RowOutcome { result?: TestResult | null; message?: string | null; error?: ApiError | null }

/**
 * Connections (FEAT-005, FEAT-006, FEAT-010; SCR-09; J1; US-144): status cards over a table of every connection.
 * Adding one is a full-page stepper (`/admin/connections/new`); "Update details" reopens it for a connection.
 */
export function ConnectionsPage() {
  const appName = useAppName();
  const list = useQuery({ queryKey: ["connections"], queryFn: () => api<{ items: ConnectionItem[] }>("/api/v1/connections") });
  const [params, setParams] = useSearchParams();
  const attentionOnly = params.get("status") === "attention";
  const all = list.data?.items ?? [];
  const items = attentionOnly ? all.filter((c) => ATTENTION.includes(c.status)) : all;
  const [outcomes, setOutcomes] = useState<Record<string, RowOutcome>>({});
  const report = useCallback((id: string, o: RowOutcome) => setOutcomes((all) => ({ ...all, [id]: { ...all[id], ...o } })), []);
  const columns = useMemo<DataColumn<ConnectionItem>[]>(() => [
    { id: "name", header: "Name", alwaysVisible: true },
    { id: "engine", header: "Engine" },
    { id: "status", header: "Status", cell: (c) => <ConnectionStatus status={c.status} /> },
    {
      id: "read_only", header: "Read-only",
      value: (c) => (c.read_only.verified_at ? "verified" : c.read_only.verdict === "unsafe" ? "unsafe" : "not tested"),
      cell: (c) => (c.read_only.verified_at ? <StatusBadge status="ok">Verified {formatAsOf(c.read_only.verified_at)}</StatusBadge>
        : c.read_only.verdict === "unsafe" ? <StatusBadge status="danger">Not read-only</StatusBadge>
          : <span className="text-ink-muted">Not tested</span>),
    },
    {
      id: "model", header: "Model", value: (c) => c.model.version_no ?? (c.model.id ? 0 : null),
      cell: (c) => (c.model.id ? (
        <Link className="text-link underline-offset-4 hover:underline" to={`/admin/models/${c.model.id}`}>
          {c.model.version_no ? `v${c.model.version_no}` : "draft"}{c.model.status === "broken" ? " (needs attention)" : ""}
        </Link>
      ) : "—"),
    },
    { id: "ask_mode", header: "Ask answers with", value: (c) => ASK_MODE_LABELS[c.ask_mode ?? "certified"] },
    { id: "data_as_of", header: "Data as of", date: true, cell: (c) => formatAsOf(c.data_as_of) },
    { id: "actions", header: "Actions", sortable: false, alwaysVisible: true, value: () => "", cell: (c) => <ConnectionActions c={c} report={report} /> },
  ], [report]);
  return (
    <section className="tl grid gap-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="grid gap-1">
          <h1 className="text-title">Connections</h1>
          <p className="text-ink-muted">Read-only links to the systems {appName} answers from. A connection stays a draft until its test proves the account cannot change data.</p>
        </div>
        <Button asChild><Link to="/admin/connections/new"><Plus aria-hidden />Add connection</Link></Button>
      </div>
      {all.length > 0 && <StatusCards items={all} attentionOnly={attentionOnly} onAttention={() => setParams(attentionOnly ? {} : { status: "attention" })} />}
      {attentionOnly && (
        <div><FilterChip field="Status" value="Needs attention" applied onClear={() => setParams({})} onClick={() => setParams({})} /></div>
      )}
      <DataTable
        data={items}
        columns={columns}
        label="Connections"
        getRowId={(c) => c.id}
        loading={list.isLoading}
        error={list.error && <FormAlert error={asApiError(list.error)} />}
        empty={attentionOnly ? { title: "Every connection is working", body: "Clear the status filter to see them all." }
          : { title: "No connections yet", body: "Add a read-only database account to start.", action: <Button asChild><Link to="/admin/connections/new">Add connection</Link></Button> }}
        search={items.length > 5}
        renderDetail={(c) => {
          const o = outcomes[c.id];
          if (!o || (!o.result && !o.message && !o.error)) return null;
          return (
            <div className="grid gap-2">
              <FormAlert error={o.error} />
              {o.message && <Alert><p>{o.message}</p></Alert>}
              {o.result && <TestChecklist r={o.result} />}
            </div>
          );
        }}
      />
    </section>
  );
}

/** Counts by state; "Needs attention" toggles the table's filter (`?status=attention`, as the Admin overview links). */
function StatusCards({ items, attentionOnly, onAttention }: { items: ConnectionItem[]; attentionOnly: boolean; onAttention: () => void }) {
  const n = (f: (c: ConnectionItem) => boolean) => items.filter(f).length;
  const attention = n((c) => ATTENTION.includes(c.status));
  const cards: { label: string; value: number; note: string; onClick?: () => void; tone?: string }[] = [
    { label: "Connected", value: n((c) => c.status === "connected"), note: `of ${items.length} ${items.length === 1 ? "connection" : "connections"}` },
    { label: "Needs attention", value: attention, note: attention ? (attentionOnly ? "Showing these only" : "Show these only") : "Nothing failing",
      onClick: attention || attentionOnly ? onAttention : undefined, tone: attention ? "text-status-danger" : undefined },
    { label: "Drafts", value: n((c) => c.status === "draft"), note: "Waiting for a passing test" },
    { label: "In a model", value: n((c) => !!c.model.version_no), note: "People can ask about these" },
  ];
  return (
    <ul className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4" aria-label="Connection status">
      {cards.map((c) => {
        const body = (
          <>
            <span className="text-label uppercase text-ink-muted">{c.label}</span>
            <span className={cn("font-mono text-figure-xl tabular-nums", c.tone)}>{c.value}</span>
            <span className="text-small text-ink-muted">{c.note}</span>
          </>
        );
        return (
          <li key={c.label}>
            {c.onClick ? (
              <button type="button" onClick={c.onClick} aria-pressed={attentionOnly}
                className={cn("grid h-full w-full gap-1 rounded-lg border bg-surface-raised p-4 text-left hover:bg-surface-sunken focus-visible:focus-ring",
                  attentionOnly ? "border-accent" : "border-line")}>{body}</button>
            ) : <Card className="h-full gap-1 p-4">{body}</Card>}
          </li>
        );
      })}
    </ul>
  );
}

function ConnectionStatus({ status }: { status: string }) {
  if (status === "connected") return <StatusBadge status="ok">connected</StatusBadge>;
  if (status === "error") return <StatusBadge status="danger">error</StatusBadge>;
  return <Badge variant="neutral">{status}</Badge>;
}

function ConnectionActions({ c, report }: { c: ConnectionItem; report: (id: string, o: RowOutcome) => void }) {
  const qc = useQueryClient();
  const refresh = () => void qc.invalidateQueries({ queryKey: ["connections"] });
  const act = useMutation({
    mutationFn: async (action: "test" | "introspect") => {
      report(c.id, { error: null });
      if (action === "test") report(c.id, { result: await api<TestResult>(`/api/v1/connections/${c.id}/test`, { method: "POST" }) });
      if (action === "introspect") {
        const r = await api<{ status: string; objects?: number; message?: string; error?: { message: string } }>(`/api/v1/connections/${c.id}/introspect`, { method: "POST" });
        report(c.id, { message: r.status === "connected" ? `Read ${r.objects} tables and views (structure only — no data values).` : r.message ?? r.error?.message ?? "Incomplete — retry." });
        schemaRead(qc, c.id);
      }
    },
    onSuccess: refresh,
    onError: (e) => report(c.id, { error: asApiError(e) }),
  });
  const createModel = useMutation({
    mutationFn: () => api("/api/v1/models", { method: "POST", body: { connection_id: c.id } }),
    onSuccess: refresh,
    onError: (e) => report(c.id, { error: asApiError(e) }),
  });
  const [packOpen, setPackOpen] = useState(false);
  const [modeOpen, setModeOpen] = useState(false);
  const draft = c.status === "draft";
  return (
    <span className="flex flex-wrap gap-x-3">
      <Button asChild variant="link" size="sm"><Link to={`/admin/connections/new?id=${c.id}&step=details`}>{c.status === "error" ? "Fix details" : "Update details"}</Link></Button>
      <Button variant="link" size="sm" onClick={() => act.mutate("test")} disabled={act.isPending}>Test</Button>
      <Button variant="link" size="sm" onClick={() => act.mutate("introspect")} disabled={act.isPending || draft}>Read schema</Button>
      <Button variant="link" size="sm" onClick={() => setPackOpen(true)} disabled={act.isPending || draft}>Starter pack</Button>
      <Button variant="link" size="sm" onClick={() => setModeOpen(true)} disabled={draft}>Ask mode</Button>
      {!c.model.id && <Button variant="link" size="sm" onClick={() => createModel.mutate()} disabled={draft}>Build model</Button>}
      <AskModeDialog connectionId={c.id} connectionName={c.name} open={modeOpen} onClose={() => setModeOpen(false)} />
      <ApplyPackDialog connectionId={c.id} connectionName={c.name} open={packOpen} onClose={() => setPackOpen(false)} onApplied={(r) => {
        refresh();
        report(c.id, { message: `${r.mode === "merge" ? "Added the starter pack to the model draft" : "Installed the starter pack"}: ${r.installed.reports} starter reports; ${r.unavailable.length} items unavailable. Review and approve the model.` });
      }} />
    </span>
  );
}

