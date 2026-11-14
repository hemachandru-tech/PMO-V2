import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowDown, ArrowUp, FilePlus2, Import, LayoutDashboard, Pencil, Plus, Trash2, X } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import {
  deleteBlueprint, deleteDashboard, getPackReports, packReportsKey, saveBlueprint, saveDashboard,
  type Blueprint, type PackReports, type Problem, type StarterDashboard,
} from "../../../../api/packReports";
import { FormAlert } from "../../../../components/ui/alert";
import { Badge } from "../../../../components/ui/badge";
import { Button } from "../../../../components/ui/button";
import { ConfirmDialog, type ConfirmRequest } from "../../../../components/ui/confirm-dialog";
import { EmptyState } from "../../../../components/ui/empty-state";
import { Input } from "../../../../components/ui/input";
import { NativeSelect } from "../../../../components/ui/native-select";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "../../../../components/ui/sheet";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "../../../../components/ui/table";
import { ApiError } from "../../../../api/client";
import { asApiError } from "../../../../lib/api-errors";
import { BlueprintEditor } from "./BlueprintEditor";
import { SaveFromDialog } from "./SaveFromDialog";

const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;

function problemsOf(e: unknown): Problem[] {
  if (!(e instanceof ApiError)) return [];
  const p = e.details?.problems;
  if (Array.isArray(p)) return p as Problem[];
  if (p && typeof p === "object") return Object.values(p as Record<string, Problem[]>).flat();
  return [];
}

function DashboardCard({ d, data, readOnly, onSave, onDelete }: {
  d: StarterDashboard;
  data: PackReports;
  readOnly: boolean;
  onSave: (d: StarterDashboard) => void;
  onDelete: () => void;
}) {
  const [draft, setDraft] = useState(d);
  const changed = JSON.stringify(draft) !== JSON.stringify(d);
  const title = (id: string) => data.blueprints.find((b) => b.id === id)?.title ?? id;
  const offTab = data.blueprints.filter((b) => !b.unavailable && !draft.reports.includes(b.id));
  const dimLabel = (k: string) => data.names.dimensions.find((x) => x.key === k)?.label ?? k;
  const move = (i: number, by: number) => {
    const r = [...draft.reports];
    const [x] = r.splice(i, 1);
    r.splice(i + by, 0, x!);
    setDraft({ ...draft, reports: r });
  };
  return (
    <article aria-label={`Dashboard ${d.title}`} className="grid gap-3 rounded-md border border-line bg-surface p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <Input aria-label="Dashboard title" value={draft.title} disabled={readOnly} className="max-w-sm"
          onChange={(e) => setDraft({ ...draft, title: e.target.value })} />
        <span className="text-small text-ink-muted">{d.key} · {plural(draft.reports.length, "tab")}</span>
      </div>
      <ol aria-label={`Tabs of ${d.title}`} className="grid gap-1">
        {draft.reports.map((r, i) => (
          <li key={r} className="flex items-center justify-between gap-2 text-small">
            <span>{i + 1}. {title(r)}</span>
            {!readOnly && (
              <span className="flex gap-1">
                <Button variant="ghost" size="sm" aria-label={`Move ${title(r)} up`} disabled={i === 0} onClick={() => move(i, -1)}><ArrowUp aria-hidden /></Button>
                <Button variant="ghost" size="sm" aria-label={`Move ${title(r)} down`} disabled={i === draft.reports.length - 1} onClick={() => move(i, 1)}><ArrowDown aria-hidden /></Button>
                <Button variant="ghost" size="sm" aria-label={`Take ${title(r)} off the dashboard`} onClick={() => setDraft({ ...draft, reports: draft.reports.filter((x) => x !== r) })}><X aria-hidden /></Button>
              </span>
            )}
          </li>
        ))}
      </ol>
      {!readOnly && offTab.length > 0 && (
        <NativeSelect aria-label={`Add a tab to ${d.title}`} value="" className="max-w-sm"
          onChange={(e) => e.target.value && setDraft({ ...draft, reports: [...draft.reports, e.target.value] })}>
          <option value="">Add a blueprint as a tab…</option>
          {offTab.map((b) => <option key={b.id} value={b.id}>{b.title}</option>)}
        </NativeSelect>
      )}
      <div className="flex flex-wrap items-center gap-2" aria-label={`Shared filters of ${d.title}`}>
        <span className="text-small text-ink-muted">Shared filters:</span>
        {draft.filters.map((f) => (
          <Badge key={f} variant="outline">
            {dimLabel(f)}
            {!readOnly && <button type="button" className="ml-1" aria-label={`Remove filter ${dimLabel(f)}`} onClick={() => setDraft({ ...draft, filters: draft.filters.filter((x) => x !== f) })}>×</button>}
          </Badge>
        ))}
        {!readOnly && draft.filters.length < 10 && (
          <NativeSelect aria-label={`Add a shared filter to ${d.title}`} value="" className="max-w-56"
            onChange={(e) => e.target.value && setDraft({ ...draft, filters: [...draft.filters, e.target.value] })}>
            <option value="">Add…</option>
            {data.names.dimensions.filter((x) => !draft.filters.includes(x.key)).map((x) => <option key={x.key} value={x.key}>{x.label}</option>)}
          </NativeSelect>
        )}
      </div>
      {!readOnly && (
        <div className="flex justify-end gap-2">
          <Button variant="ghost" size="sm" onClick={onDelete}><Trash2 aria-hidden /> Delete</Button>
          <Button size="sm" disabled={!changed || !draft.title.trim()} onClick={() => onSave(draft)}>Save dashboard</Button>
        </div>
      )}
    </article>
  );
}

/**
 * Pack Studio › Reports & dashboards (US-234; FEAT-075). The pack's report blueprints (each a starter report and a
 * layout the report planner proposes) and its starter dashboards (blueprints as tabs, shared filters). A system pack
 * or a released version is read-only.
 */
export function ReportsTab({ packId }: { packId: string }) {
  const qc = useQueryClient();
  const key = packReportsKey(packId);
  const q = useQuery({ queryKey: key, queryFn: () => getPackReports(packId) });
  const [editing, setEditing] = useState<{ blueprint: Blueprint; isNew: boolean } | null>(null);
  const [problems, setProblems] = useState<Problem[]>([]);
  const [saveFrom, setSaveFrom] = useState<"report" | "dashboard" | null>(null);
  const [newDash, setNewDash] = useState<{ key: string; title: string } | null>(null);
  const [confirm, setConfirm] = useState<ConfirmRequest | null>(null);

  const put = (out: PackReports) => {
    qc.setQueryData(key, out);
    // The pack screen's other tabs hold the manifest and its revision: reload them after a change here.
    void qc.invalidateQueries({ queryKey: ["pack", packId] });
  };
  const failed = (e: unknown, fallback: string) => {
    const err = asApiError(e, fallback);
    setProblems(problemsOf(e));
    if (err.status === 412) void qc.invalidateQueries({ queryKey: key });
    toast.error(err.status === 412 ? "Someone changed this pack's reports. It has been reloaded: try again." : err.message);
  };
  const saveBp = useMutation({
    mutationFn: (b: Blueprint) => saveBlueprint(packId, q.data!.etag, editing?.isNew ? b.id : editing!.blueprint.id, b),
    onSuccess: (out, b) => { put(out); setEditing(null); setProblems([]); toast.success(`${b.title} is saved in the draft.`); },
    onError: (e) => failed(e, "The blueprint could not be saved."),
  });
  const dropBp = useMutation({
    mutationFn: (id: string) => deleteBlueprint(packId, q.data!.etag, id),
    onSuccess: (out) => { put(out); toast.success("The blueprint is deleted from the draft."); },
    onError: (e) => failed(e, "The blueprint could not be deleted."),
  });
  const saveDash = useMutation({
    mutationFn: (d: StarterDashboard) => saveDashboard(packId, q.data!.etag, d),
    onSuccess: (out, d) => { put(out); setNewDash(null); toast.success(`${d.title} is saved in the draft.`); },
    onError: (e) => failed(e, "The dashboard could not be saved."),
  });
  const dropDash = useMutation({
    mutationFn: (k: string) => deleteDashboard(packId, q.data!.etag, k),
    onSuccess: (out) => { put(out); toast.success("The dashboard is deleted from the draft."); },
    onError: (e) => failed(e, "The dashboard could not be deleted."),
  });

  if (q.error) return <FormAlert error={asApiError(q.error)} />;
  if (!q.data) return <p className="text-small text-ink-muted">Loading the pack's reports…</p>;
  const data = q.data;
  const ro = data.pack.read_only;
  const dashTitle = (k: string) => data.dashboards.find((d) => d.key === k)?.title ?? k;
  const blank = (): Blueprint => ({
    id: "", title: "", dashboard: data.dashboards[0]?.key ?? "", triggers: [], kpis: [], widgets: [], footnote: "",
  });

  return (
    <div className="grid gap-6">
      {ro && (
        <p role="note" className="rounded-md border border-line bg-surface-muted p-3 text-small">
          {data.pack.system ? "A system pack is read-only: clone it to change its reports." : "This version is released and frozen: open the next draft to change it."}
        </p>
      )}

      <section aria-labelledby="pack-dashboards" className="grid gap-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h2 id="pack-dashboards" className="text-heading">Starter dashboards</h2>
          {!ro && <Button variant="secondary" size="sm" onClick={() => setNewDash({ key: "", title: "" })}><LayoutDashboard aria-hidden /> Add a dashboard</Button>}
        </div>
        {newDash && (
          <div className="flex flex-wrap items-end gap-2" aria-label="New dashboard">
            <Input aria-label="New dashboard key" placeholder="key, e.g. risks" value={newDash.key} className="max-w-40"
              onChange={(e) => setNewDash({ ...newDash, key: e.target.value })} />
            <Input aria-label="New dashboard title" placeholder="Title" value={newDash.title} className="max-w-sm"
              onChange={(e) => setNewDash({ ...newDash, title: e.target.value })} />
            <Button size="sm" disabled={!/^[a-z][a-z0-9_]{0,40}$/.test(newDash.key) || !newDash.title.trim()}
              onClick={() => saveDash.mutate({ key: newDash.key, title: newDash.title.trim(), reports: [], filters: [] })}>Add</Button>
            <Button variant="ghost" size="sm" onClick={() => setNewDash(null)}>Cancel</Button>
          </div>
        )}
        {data.dashboards.length === 0 && !newDash && (
          <EmptyState icon={LayoutDashboard} title="No starter dashboards">Add one, then put blueprints on it as tabs.</EmptyState>
        )}
        <div className="grid gap-3 lg:grid-cols-2">
          {data.dashboards.map((d) => (
            <DashboardCard key={`${d.key}-${data.etag}`} d={d} data={data} readOnly={ro} onSave={(x) => saveDash.mutate(x)}
              onDelete={() => setConfirm({
                title: `Delete ${d.title}?`, body: "Only an empty dashboard can go: move or delete its blueprints first.",
                confirm: "Delete dashboard", onConfirm: () => dropDash.mutate(d.key),
              })} />
          ))}
        </div>
      </section>

      <section aria-labelledby="pack-blueprints" className="grid gap-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h2 id="pack-blueprints" className="text-heading">Report blueprints</h2>
          {!ro && (
            <span className="flex flex-wrap gap-2">
              <Button variant="secondary" size="sm" onClick={() => setSaveFrom("report")}><Import aria-hidden /> From a report</Button>
              <Button variant="secondary" size="sm" onClick={() => setSaveFrom("dashboard")}><FilePlus2 aria-hidden /> From a dashboard</Button>
              <Button size="sm" disabled={data.dashboards.length === 0} onClick={() => { setProblems([]); setEditing({ blueprint: blank(), isNew: true }); }}>
                <Plus aria-hidden /> New blueprint
              </Button>
            </span>
          )}
        </div>
        {data.blueprints.length === 0 ? (
          <EmptyState title="No report blueprints">A blueprint is a starter report: its question, headline figures, visuals and detail table.</EmptyState>
        ) : (
          <Table aria-label="Report blueprints">
            <TableHeader>
              <TableRow>
                <TableHead>Blueprint</TableHead>
                <TableHead>Dashboard</TableHead>
                <TableHead>Parts</TableHead>
                <TableHead>Checks</TableHead>
                <TableHead><span className="sr-only">Actions</span></TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {data.blueprints.map((b) => {
                const p = data.problems[b.id] ?? [];
                const onTab = data.dashboards.some((d) => d.reports.includes(b.id));
                return (
                  <TableRow key={b.id}>
                    <TableCell><span className="font-medium">{b.title}</span><br /><span className="text-small text-ink-muted">{b.question ?? ""}</span></TableCell>
                    <TableCell>{b.unavailable?.length ? <Badge variant="outline">Data gap</Badge> : `${dashTitle(b.dashboard)}${onTab ? "" : " (no tab)"}`}</TableCell>
                    <TableCell className="text-small">
                      {b.unavailable?.length ? `Needs ${b.unavailable.join(", ")}` : `${plural(b.kpis?.length ?? 0, "figure")} · ${plural(b.widgets?.length ?? 0, "visual")}${b.table ? " · table" : ""}`}
                    </TableCell>
                    <TableCell>{p.length ? <Badge variant="stale" title={p.map((x) => x.message).join("; ")}>{plural(p.length, "problem")}</Badge> : <Badge variant="outline">OK</Badge>}</TableCell>
                    <TableCell className="text-right">
                      <Button variant="ghost" size="sm" aria-label={`${ro ? "Open" : "Edit"} ${b.title}`} onClick={() => { setProblems(p); setEditing({ blueprint: b, isNew: false }); }}><Pencil aria-hidden /></Button>
                      {!ro && (
                        <Button variant="ghost" size="sm" aria-label={`Delete ${b.title}`} onClick={() => setConfirm({
                          title: `Delete ${b.title}?`, body: "It leaves its dashboard's tabs. Installed connections keep it until the next release is applied.",
                          confirm: "Delete blueprint", onConfirm: () => dropBp.mutate(b.id),
                        })}><Trash2 aria-hidden /></Button>
                      )}
                    </TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        )}
      </section>

      <Sheet open={editing !== null} onOpenChange={(o) => !o && setEditing(null)}>
        <SheetContent className="w-full overflow-y-auto sm:max-w-2xl">
          <SheetHeader>
            <SheetTitle>{editing?.isNew ? "New blueprint" : editing?.blueprint.title}</SheetTitle>
            <SheetDescription>Installing the pack makes it a starter report; the report planner proposes it when someone asks for it.</SheetDescription>
          </SheetHeader>
          {editing && (
            <BlueprintEditor key={editing.blueprint.id || "new"} data={data} blueprint={editing.blueprint} readOnly={ro}
              saving={saveBp.isPending} problems={problems} onSave={(b) => saveBp.mutate(b)} onCancel={() => setEditing(null)} />
          )}
        </SheetContent>
      </Sheet>
      {saveFrom && (
        <SaveFromDialog kind={saveFrom} packId={packId} onClose={() => setSaveFrom(null)}
          onSaved={(out, left) => {
            put(out);
            setSaveFrom(null);
            toast.success(left.length ? `Saved; ${plural(left.length, "widget")} left out.` : "Saved in the draft.");
          }} />
      )}
      <ConfirmDialog request={confirm} onClose={() => setConfirm(null)} />
    </div>
  );
}
