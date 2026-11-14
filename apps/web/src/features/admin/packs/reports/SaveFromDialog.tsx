import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type ReactNode } from "react";
import { toast } from "sonner";

import { api } from "../../../../api/client";
import type { ReportListItem } from "../../../../api/authoring";
import {
  blueprintFromReport, blueprintsFromDashboard, getPackReports, packReportsKey,
  type FromDashboard, type FromReport, type LeftOut, type PackReports,
} from "../../../../api/packReports";
import { listPacks } from "../../../../api/packs";
import { FormAlert } from "../../../../components/ui/alert";
import { Button } from "../../../../components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "../../../../components/ui/dialog";
import { NativeSelect } from "../../../../components/ui/native-select";
import { asApiError } from "../../../../lib/api-errors";

const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;

/**
 * Saves a report as a blueprint, or a dashboard as a starter dashboard, in a pack draft (US-234). From the pack's
 * Reports & dashboards tab the pack is given and the report or dashboard is chosen; from the report canvas or the
 * dashboard composer the source is given and the person picks one of their tenant's pack drafts.
 */
export function SaveFromDialog({ kind, packId: givenPack, sourceId, onClose, onSaved }: {
  kind: "report" | "dashboard";
  packId?: string;
  sourceId?: string;
  onClose: () => void;
  onSaved: (out: PackReports, leftOut: LeftOut[]) => void;
}) {
  const qc = useQueryClient();
  const [pickedPack, setPickedPack] = useState("");
  const [source, setSource] = useState(sourceId ?? "");
  const [dashboard, setDashboard] = useState("");
  const packId = givenPack ?? pickedPack;
  const drafts = useQuery({
    queryKey: ["packs"], queryFn: listPacks, enabled: !givenPack,
    select: (l) => l.items.flatMap((p) => p.versions.filter((v) => v.editable).map((v) => ({ id: v.id, label: `${v.title} ${v.version}` }))),
  });
  const reports = useQuery({ queryKey: packReportsKey(packId), queryFn: () => getPackReports(packId), enabled: Boolean(packId) });
  const data = reports.data;
  const dash = dashboard || data?.dashboards[0]?.key || "";
  const list = useQuery({
    queryKey: [kind === "report" ? "reports" : "dashboards"], enabled: !sourceId,
    queryFn: () => api<{ items: ReportListItem[] }>(kind === "report" ? "/api/v1/reports" : "/api/v1/dashboards"),
  });
  const shown = useQuery<FromReport | FromDashboard>({
    queryKey: ["pack-save-from", packId, kind, source, dash],
    enabled: Boolean(packId && source) && (kind === "dashboard" || Boolean(dash)),
    queryFn: () => (kind === "report"
      ? blueprintFromReport(packId, undefined, { report_id: source, dashboard: dash, save: false })
      : blueprintsFromDashboard(packId, undefined, { dashboard_id: source, save: false })),
  });
  const save = useMutation<FromReport | FromDashboard>({
    mutationFn: () => (kind === "report"
      ? blueprintFromReport(packId, data!.etag, { report_id: source, dashboard: dash, save: true })
      : blueprintsFromDashboard(packId, data!.etag, { dashboard_id: source, save: true })),
    onSuccess: (r) => {
      qc.setQueryData(packReportsKey(packId), r.reports!);
      void qc.invalidateQueries({ queryKey: ["pack", packId] });
      onSaved(r.reports!, r.left_out);
    },
  });
  const left = shown.data?.left_out ?? [];
  const noDrafts = !givenPack && drafts.data?.length === 0;
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{kind === "report" ? "Save a report as a blueprint" : "Save a dashboard as a starter dashboard"}</DialogTitle>
          <DialogDescription>
            Widgets become blueprint parts by measure and dimension key. Widgets on uncertified measures, their own rules
            or custom SQL are left out and named here.
          </DialogDescription>
        </DialogHeader>
        <div className="grid gap-3">
          {!givenPack && (noDrafts
            ? <p className="text-small text-ink-muted">There is no pack draft to save into. Create or clone a pack in Admin › Packs first.</p>
            : (
              <NativeSelect aria-label="Pack draft" value={pickedPack} onChange={(e) => setPickedPack(e.target.value)}>
                <option value="">Choose a pack draft…</option>
                {(drafts.data ?? []).map((p) => <option key={p.id} value={p.id}>{p.label}</option>)}
              </NativeSelect>
            ))}
          {!sourceId && (
            <NativeSelect aria-label={kind === "report" ? "Report" : "Dashboard"} value={source} onChange={(e) => setSource(e.target.value)}>
              <option value="">{kind === "report" ? "Choose a report…" : "Choose a dashboard…"}</option>
              {(list.data?.items ?? []).map((r) => <option key={r.id} value={r.id}>{r.title}</option>)}
            </NativeSelect>
          )}
          {kind === "report" && data && (
            data.dashboards.length === 0
              ? <p className="text-small text-ink-muted">This pack has no starter dashboard yet. Add one on its Reports & dashboards tab first.</p>
              : (
                <NativeSelect aria-label="Its dashboard" value={dash} onChange={(e) => setDashboard(e.target.value)}>
                  {data.dashboards.map((d) => <option key={d.key} value={d.key}>{d.title}</option>)}
                </NativeSelect>
              )
          )}
          {reports.error && <FormAlert error={asApiError(reports.error)} />}
          {shown.error && <FormAlert error={asApiError(shown.error)} />}
          {shown.data && (
            <div className="grid gap-1 text-small">
              <p>
                {"blueprint" in shown.data
                  ? `${shown.data.blueprint.title}: ${plural(shown.data.blueprint.kpis?.length ?? 0, "figure")}, ${plural(shown.data.blueprint.widgets?.length ?? 0, "visual")}${shown.data.blueprint.table ? ", a detail table" : ""}.`
                  : `${shown.data.dashboard.title}: ${plural(shown.data.blueprints.length, "tab")}, ${plural(shown.data.dashboard.filters.length, "shared filter")}.`}
              </p>
              {left.length > 0 && (
                <>
                  <p className="font-medium">Left out ({left.length})</p>
                  <ul aria-label="Left out" className="grid list-disc gap-1 pl-5">
                    {left.map((l, i) => <li key={i}>{l.report ? `${l.report} › ` : ""}{l.title}: {l.reason}</li>)}
                  </ul>
                </>
              )}
            </div>
          )}
          {save.error && <FormAlert error={asApiError(save.error)} />}
        </div>
        <DialogFooter>
          <Button variant="ghost" onClick={onClose}>Cancel</Button>
          <Button disabled={!shown.data || !data || save.isPending} onClick={() => save.mutate()}>{save.isPending ? "Saving…" : "Save to the pack"}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

/** "Save as blueprint" for the report canvas and the dashboard composer; shown to pack authors only. */
export function SaveAsBlueprint({ kind, sourceId, children }: {
  kind: "report" | "dashboard";
  sourceId: string;
  children: (open: () => void) => ReactNode;
}) {
  const [open, setOpen] = useState(false);
  return (
    <>
      {children(() => setOpen(true))}
      {open && (
        <SaveFromDialog kind={kind} sourceId={sourceId} onClose={() => setOpen(false)}
          onSaved={(_, left) => {
            setOpen(false);
            toast.success(left.length ? `Saved in the pack draft; ${plural(left.length, "widget")} left out.` : "Saved in the pack draft.");
          }} />
      )}
    </>
  );
}
