import { useInfiniteQuery, useMutation, useQuery } from "@tanstack/react-query";
import { Download, ShieldAlert } from "lucide-react";
import { useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";

import { api, getToken } from "../../../api/client";
import type { AuditFacets, AuditItem } from "../../../api/authoring";
import { getConfig } from "../../../app/config";
import { FormAlert } from "../../../components/ui/alert";
import { Badge } from "../../../components/ui/badge";
import { Button } from "../../../components/ui/button";
import { DataTable, type DataColumn } from "../../../components/ui/data-table";
import { FilterChip } from "../../../components/ui/filter-chip";
import { MotionList } from "../../../components/ui/motion";
import { Input } from "../../../components/ui/input";
import { Label } from "../../../components/ui/label";
import { NativeSelect } from "../../../components/ui/native-select";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "../../../components/ui/sheet";
import { Switch } from "../../../components/ui/switch";
import { asApiError } from "../../../lib/api-errors";

interface Filters { action: string; actor: string; object_type: string; object_id: string; outcome: string; security: string; from: string; to: string }
const EMPTY: Filters = { action: "", actor: "", object_type: "", object_id: "", outcome: "", security: "", from: "", to: "" };
const KEYS = Object.keys(EMPTY) as (keyof Filters)[];
const LABELS: Record<keyof Filters, string> = {
  action: "Action", actor: "Person", object_type: "Object", object_id: "Object id", outcome: "Outcome", security: "Security", from: "From", to: "To",
};

/** Filters live in the URL (dates as datetime-local values), so the Admin overview can link to a filtered log (US-143). */
function fromParams(p: URLSearchParams): Filters {
  return Object.fromEntries(KEYS.map((k) => [k, p.get(k) ?? ""])) as unknown as Filters;
}

function query(f: Filters, cursor?: string | null): string {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(f)) if (v) p.set(k, k === "from" || k === "to" ? new Date(v).toISOString() : v);
  if (cursor) p.set("cursor", cursor);
  return p.toString();
}

const when = (iso: string) => new Date(iso).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "medium" });

function localInput(d: Date): string {
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`;
}
const RANGES = [
  { key: "24h", label: "Last 24 hours", hours: 24 },
  { key: "7d", label: "Last 7 days", hours: 24 * 7 },
  { key: "30d", label: "Last 30 days", hours: 24 * 30 },
] as const;

/**
 * Audit log (FEAT-048; API-052; US-146; board Audit): who did what, when, to which object. Faceted filters with counts
 * from the server, applied at once and kept in the URL; the list loads 100 at a time and is virtualised past 200
 * rows; each event opens a detail sheet with links to the object, its snapshot and related events. CSV export is
 * itself audited.
 */
export function AuditPage() {
  const [params, setParams] = useSearchParams();
  const filters = useMemo(() => fromParams(params), [params]);
  const [open, setOpen] = useState<AuditItem | null>(null);
  const log = useInfiniteQuery({
    queryKey: ["audit", filters],
    queryFn: ({ pageParam }) => api<{ items: AuditItem[]; next_cursor: string | null }>(`/api/v1/admin/audit?${query(filters, pageParam)}&limit=100`),
    initialPageParam: null as string | null,
    getNextPageParam: (last) => last.next_cursor,
  });
  const facets = useQuery({ queryKey: ["audit-facets", filters], queryFn: () => api<AuditFacets>(`/api/v1/admin/audit/facets?${query(filters)}`) });
  const items = useMemo(() => log.data?.pages.flatMap((p) => p.items) ?? [], [log.data]);
  const set = (changes: Partial<Filters & { range: string }>) => setParams((p) => {
    const n = new URLSearchParams(p);
    for (const [k, v] of Object.entries(changes)) { if (v) n.set(k, v); else n.delete(k); }
    return n;
  });
  const exportCsv = useMutation({
    mutationFn: async () => {
      const resp = await fetch(`${getConfig().apiBaseUrl}/api/v1/admin/audit/export?${query(filters)}`, {
        method: "POST", headers: { Authorization: `Bearer ${getToken() ?? ""}` },
      });
      if (!resp.ok) throw new Error("export failed");
      const url = URL.createObjectURL(await resp.blob());
      const a = document.createElement("a");
      a.href = url;
      a.download = "tela-audit.csv";
      a.click();
      URL.revokeObjectURL(url);
    },
  });
  const range = params.get("range") ?? (filters.from || filters.to ? "custom" : "");
  const pickRange = (key: string) => {
    const r = RANGES.find((x) => x.key === key);
    if (r) set({ range: key, from: localInput(new Date(Date.now() - r.hours * 3_600_000)), to: "" });
    else set({ range: key === "custom" ? "custom" : "", ...(key === "custom" ? {} : { from: "", to: "" }) });
  };
  const applied = KEYS.filter((k) => filters[k] && k !== "from" && k !== "to");
  const total = facets.data?.total;

  const columns = useMemo<DataColumn<AuditItem>[]>(() => [
    { id: "at", header: "When", alwaysVisible: true, cell: (i) => <span className="whitespace-nowrap font-mono text-small tabular-nums">{when(i.at)}</span> },
    { id: "actor", header: "Person", value: (i) => i.actor.name ?? "System" },
    { id: "action", header: "Action", value: (i) => i.action, cell: (i) => (
      <span className="flex items-center gap-2">
        <span className="font-mono text-small">{i.action}</span>
        {i.security && <Badge variant="stale"><ShieldAlert aria-hidden className="size-3.5" /> Security</Badge>}
      </span>
    ) },
    { id: "object", header: "Object", value: (i) => i.object.type, cell: (i) => (
      <span className="text-small">{i.object.type} <span className="font-mono text-ink-muted">{i.object.id?.slice(0, 8)}</span></span>
    ) },
    { id: "outcome", header: "Outcome" },
    { id: "details", header: "Details", alwaysVisible: true, value: () => "", cell: (i) => (
      <Button variant="link" size="sm" onClick={() => setOpen(i)}>Details</Button>
    ) },
  ], []);

  return (
    <section className="tl grid gap-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="grid gap-1">
          <h1 className="text-title">Audit log</h1>
          <p className="text-ink-muted">Who did what, when, and under which definition. Details never hold data values or secrets.</p>
        </div>
        <Button variant="secondary" onClick={() => exportCsv.mutate()} disabled={exportCsv.isPending}><Download aria-hidden />{exportCsv.isPending ? "Exporting…" : "Export CSV"}</Button>
      </div>
      <div className="grid gap-3 rounded-lg border border-line bg-surface-raised p-4" role="group" aria-label="Filter the audit log">
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
          <div className="grid gap-1">
            <Label htmlFor="audit-range">When</Label>
            <NativeSelect id="audit-range" value={range} onChange={(e) => pickRange(e.target.value)}>
              <option value="">Any time</option>
              {RANGES.map((r) => <option key={r.key} value={r.key}>{r.label}</option>)}
              <option value="custom">Custom range</option>
            </NativeSelect>
          </div>
          <Facet id="audit-actor" label="Person" value={filters.actor} options={facets.data?.actor} onChange={(v) => set({ actor: v })} />
          <Facet id="audit-action" label="Action" value={filters.action} options={facets.data?.action} onChange={(v) => set({ action: v })} mono />
          <Facet id="audit-object" label="Object" value={filters.object_type} options={facets.data?.object_type} onChange={(v) => set({ object_type: v, object_id: "" })} />
          <Facet id="audit-outcome" label="Outcome" value={filters.outcome} options={facets.data?.outcome} onChange={(v) => set({ outcome: v })} />
        </div>
        <div className="flex flex-wrap items-end gap-4">
          {range === "custom" && (
            <>
              <div className="grid gap-1"><Label htmlFor="audit-from">From</Label><Input id="audit-from" type="datetime-local" value={filters.from} onChange={(e) => set({ from: e.target.value, range: "custom" })} /></div>
              <div className="grid gap-1"><Label htmlFor="audit-to">To</Label><Input id="audit-to" type="datetime-local" value={filters.to} onChange={(e) => set({ to: e.target.value, range: "custom" })} /></div>
            </>
          )}
          <label className="flex h-control-md items-center gap-2">
            <Switch checked={filters.security === "true"} onCheckedChange={(on) => set({ security: on ? "true" : "" })} aria-label="Security events only" />
            Security events only{facets.data && <span className="font-mono text-small tabular-nums text-ink-muted">({facets.data.security.toLocaleString()})</span>}
          </label>
        </div>
        {applied.length > 0 && (
          <div className="flex flex-wrap items-center gap-2">
            <MotionList as="div" className="contents" items={applied.map((k) => ({
              key: k,
              content: (
                <FilterChip field={LABELS[k]} value={k === "security" ? (filters.security === "true" ? "Security events" : "Other events") : filters[k]} applied
                  onClear={() => set({ [k]: "" })} onClick={() => set({ [k]: "" })} />
              ),
            }))} />
            <Button variant="link" size="sm" onClick={() => setParams({})}>Clear all</Button>
          </div>
        )}
      </div>
      <DataTable
        data={items}
        columns={columns}
        label="Audit events"
        getRowId={(i) => String(i.id)}
        serverPaged
        compact
        maxHeight={640}
        virtualizeAbove={50}
        loading={log.isLoading}
        error={log.error && <FormAlert error={asApiError(log.error)} />}
        empty={{ title: "No events match", body: "Change or clear the filters." }}
        onRowActivate={setOpen}
        rowLabel={(i) => `${i.action} by ${i.actor.name ?? "System"}, ${when(i.at)}`}
        footer={
          <span className="flex items-center gap-3">
            <span>{total !== undefined ? `1–${items.length.toLocaleString()} of ${total.toLocaleString()} events` : `${items.length.toLocaleString()} events loaded`} · newest first</span>
            {log.hasNextPage && (
              <Button variant="secondary" size="sm" onClick={() => void log.fetchNextPage()} disabled={log.isFetchingNextPage}>
                {log.isFetchingNextPage ? "Loading…" : "Load more"}
              </Button>
            )}
          </span>
        }
      />
      <FormAlert error={exportCsv.error ? asApiError(exportCsv.error, "The export failed. Try again.") : null} />
      <Sheet open={!!open} onOpenChange={(o) => !o && setOpen(null)}>
        <SheetContent className="w-full overflow-y-auto sm:max-w-lg">
          {open && <EventDetail item={open} onFilter={(f) => { setOpen(null); setParams(f); }} />}
        </SheetContent>
      </Sheet>
    </section>
  );
}

function Facet({ id, label, value, options, onChange, mono }: {
  id: string;
  label: string;
  value: string;
  options?: { value: string; count: number }[];
  onChange: (v: string) => void;
  mono?: boolean;
}) {
  const list = options ?? [];
  // A value from the URL (a link from the overview) stays selectable even when it has no events now.
  const values = value && !list.some((o) => o.value === value) ? [{ value, count: 0 }, ...list] : list;
  return (
    <div className="grid gap-1">
      <Label htmlFor={id}>{label}</Label>
      <NativeSelect id={id} value={value} onChange={(e) => onChange(e.target.value)} className={mono ? "font-mono text-small" : undefined}>
        <option value="">Any</option>
        {values.map((o) => <option key={o.value} value={o.value}>{o.value} ({o.count.toLocaleString()})</option>)}
      </NativeSelect>
    </div>
  );
}

/** Links to what an event is about: the object, the definition version it used, and related events. */
function snapshotLinks(i: AuditItem): { label: string; to: string }[] {
  const d = i.details;
  const out: { label: string; to: string }[] = [];
  if (i.object.link) out.push({ label: `Open the ${i.object.type.replace(/_/g, " ")}`, to: i.object.link });
  if (typeof d.model_id === "string") out.push({ label: `Model${d.version ? ` v${String(d.version)}` : ""} in Model studio`, to: `/admin/models/${d.model_id}` });
  if (i.object.type === "dashboard" && i.object.id && (d.version !== undefined || i.action.startsWith("dashboard.publish"))) {
    out.push({ label: `Dashboard versions${d.version ? ` (v${String(d.version)})` : ""}`, to: `/dashboards/${i.object.id}/versions` });
  }
  if (typeof d.connection_id === "string") out.push({ label: "Connections", to: "/admin/connections" });
  return out;
}

function EventDetail({ item: i, onFilter }: { item: AuditItem; onFilter: (f: Record<string, string>) => void }) {
  const links = snapshotLinks(i);
  const details = Object.entries(i.details ?? {});
  return (
    <>
      <SheetHeader>
        <SheetTitle className="font-mono">{i.action}</SheetTitle>
        <SheetDescription>{i.actor.name ?? "System"} · {when(i.at)}</SheetDescription>
      </SheetHeader>
      <div className="grid gap-4 px-4 pb-4">
        {i.security && <Badge variant="stale" className="w-fit"><ShieldAlert aria-hidden className="size-3.5" /> Security event</Badge>}
        <dl className="grid gap-x-4 gap-y-2 text-small sm:grid-cols-[max-content_1fr]">
          <dt className="text-ink-muted">When</dt><dd>{when(i.at)}</dd>
          <dt className="text-ink-muted">Person</dt><dd>{i.actor.name ?? "System"}</dd>
          <dt className="text-ink-muted">Object</dt><dd>{i.object.type} <span className="font-mono text-ink-muted">{i.object.id ?? ""}</span></dd>
          <dt className="text-ink-muted">Outcome</dt><dd>{i.outcome}</dd>
          {details.map(([k, v]) => (
            <div key={k} className="contents">
              <dt className="text-ink-muted">{k.replace(/_/g, " ")}</dt>
              <dd className="break-words font-mono">{typeof v === "object" ? JSON.stringify(v) : String(v)}</dd>
            </div>
          ))}
          <dt className="text-ink-muted">Request id</dt><dd className="font-mono break-all">{i.request_id}</dd>
        </dl>
        <p className="text-small text-ink-muted">The log keeps fingerprints and counts, never data values or statement text.</p>
        {links.length > 0 && (
          <nav aria-label="Related" className="grid gap-1">
            {links.map((l) => <Link key={l.to} className="text-link underline-offset-4 hover:underline" to={l.to}>{l.label}</Link>)}
          </nav>
        )}
        <div className="flex flex-wrap gap-2">
          {i.actor.name && <Button variant="secondary" size="sm" onClick={() => onFilter({ actor: i.actor.name! })}>Events from {i.actor.name}</Button>}
          {i.object.id && <Button variant="secondary" size="sm" onClick={() => onFilter({ object_type: i.object.type, object_id: i.object.id! })}>Events on this {i.object.type.replace(/_/g, " ")}</Button>}
        </div>
      </div>
    </>
  );
}
