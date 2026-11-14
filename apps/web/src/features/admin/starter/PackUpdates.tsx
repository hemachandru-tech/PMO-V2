import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowUpCircle } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";
import { toast } from "sonner";

import { api } from "../../../api/client";
import type { PackChange, PackUpdate } from "../../../api/authoring";
import { formatRelative } from "../../../components/format";
import { FormAlert } from "../../../components/ui/alert";
import { Badge } from "../../../components/ui/badge";
import { Button } from "../../../components/ui/button";
import { ConfirmDialog, type ConfirmRequest } from "../../../components/ui/confirm-dialog";
import { asApiError } from "../../../lib/api-errors";

export const UPDATES_KEY = ["admin-pack-updates"];
const KIND: Record<PackChange["kind"], string> = {
  report: "Starter report", measure: "Measure", dimension: "Dimension", entity: "Table", field: "Field",
};
const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;

type Applied = { status: "applied" | "awaiting_approval"; summary: string; owners_notified?: number };

/**
 * Newer starter pack versions (US-120; SCR-11; FEAT-047). Each card says what the version changes on that connection,
 * that people's copies are never touched and which dashboards get a new draft. Applying it replaces the starter reports
 * only; measure changes go into a model draft first and the reports follow when it is approved.
 */
export function PackUpdates({ onApplied }: { onApplied: () => void }) {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: UPDATES_KEY, queryFn: () => api<{ items: PackUpdate[] }>("/api/v1/admin/packs/updates") });
  const [confirm, setConfirm] = useState<ConfirmRequest | null>(null);
  const apply = useMutation({
    mutationFn: (u: PackUpdate) => api<Applied>(`/api/v1/admin/packs/${u.pack_id}/update`, {
      method: "POST", body: { connection_id: u.connection.id },
    }),
    onSuccess: (r, u) => {
      void qc.invalidateQueries({ queryKey: UPDATES_KEY });
      onApplied();
      toast.success(r.status === "applied"
        ? `${u.title} ${u.available} is applied to ${u.connection.name}${r.owners_notified ? `; ${plural(r.owners_notified, "dashboard owner")} notified` : ""}`
        : `${u.available} is in the model draft. Approve it to update the starter reports.`);
    },
    onError: (e) => toast.error(asApiError(e, "The update could not be applied.").message),
  });

  if (q.error) return <FormAlert error={asApiError(q.error)} />;
  const items = q.data?.items ?? [];
  if (items.length === 0) return null;
  const ask = (u: PackUpdate) => setConfirm({
    title: `Update ${u.connection.name} to ${u.available}?`,
    body: (
      <ul className="grid list-disc gap-2 pl-5">
        <li>{u.changes.length === 0 ? "Nothing changes on this connection; only the version is recorded." : u.summary}</li>
        {u.model_draft_required
          ? <li>Measure and field changes go into the model draft. The starter reports change when you approve it.</li>
          : <li>The starter reports are replaced now.</li>}
        <li>{u.user_copies_unaffected === 0 ? "Nobody has copied a starter report here." : `${plural(u.user_copies_unaffected, "user copy", "user copies")} stay exactly as they are.`}</li>
        {u.dashboards_getting_new_draft.length > 0 && <li>{plural(u.dashboards_getting_new_draft.length, "dashboard gets", "dashboards get")} a new draft; live versions stay until their owners republish.</li>}
      </ul>
    ),
    confirm: u.model_draft_required ? "Put changes in the model draft" : "Apply update",
    onConfirm: () => apply.mutate(u),
  });

  return (
    <section aria-labelledby="pack-updates" className="grid gap-3">
      <h2 id="pack-updates" className="text-heading">Pack updates</h2>
      {items.map((u) => (
        <article key={`${u.pack_id}-${u.connection.id}`} aria-label={`${u.title} ${u.available} for ${u.connection.name}`}
          className="grid gap-2 rounded-md border border-line bg-surface p-4">
          <div className="flex flex-wrap items-start justify-between gap-2">
            <div className="grid gap-0.5">
              <span className="font-medium">{u.title} {u.available} · {u.connection.name}</span>
              <span className="text-small text-ink-muted">Installed {u.installed} · released {formatRelative(u.released_at)}</span>
            </div>
            {u.status === "awaiting_approval" ? (
              <span className="flex flex-wrap items-center gap-2">
                <Badge variant="outline">Waiting for model approval</Badge>
                <Button asChild variant="link" size="sm"><Link to={`/admin/models/${u.model_id}/approve`}>Review the model draft</Link></Button>
              </span>
            ) : (
              <Button size="sm" disabled={!!u.error || apply.isPending} onClick={() => ask(u)}><ArrowUpCircle aria-hidden />Update…</Button>
            )}
          </div>
          {u.error ? <p className="text-small text-status-danger">{u.error}</p> : (
            <>
              {u.changes.length === 0
                ? <p className="text-small">Nothing changes on this connection: updating only records the new version.</p>
                : <p className="text-small">{u.summary} Updating changes starter reports only; people's copies are never touched{u.user_copies_unaffected ? ` (${plural(u.user_copies_unaffected, "copy", "copies")} here)` : ""}.</p>}
              {u.model_draft_required && u.status === "available" && <p className="text-small text-ink-muted">Measures or fields change, so this goes through a model draft that needs approval.</p>}
              {u.changes.length > 0 && (
                <details>
                  <summary className="cursor-pointer text-small">What changes ({u.changes.length})</summary>
                  <ul className="mt-2 grid gap-1">
                    {u.changes.map((c) => (
                      <li key={`${c.kind}-${c.key}`} className="text-small">
                        <Badge variant={c.change === "removed" ? "stale" : c.change === "added" ? "certified" : "neutral"}>{c.change}</Badge>{" "}
                        {KIND[c.kind]}: {c.title}
                      </li>
                    ))}
                  </ul>
                </details>
              )}
              {u.dashboards_getting_new_draft.length > 0 && (
                <ul className="grid gap-0.5">
                  {u.dashboards_getting_new_draft.map((d) => (
                    <li key={`${d.dashboard_id}-${d.report}`} className="text-small text-ink-muted">
                      {d.title} gets a new draft ({d.report} {d.change}){d.live_version_stays ? ` — version ${d.live_version_stays} stays live` : ""}{d.owner ? ` · ${d.owner} is notified` : ""}
                    </li>
                  ))}
                </ul>
              )}
            </>
          )}
        </article>
      ))}
      <ConfirmDialog request={confirm} onClose={() => setConfirm(null)} busy={apply.isPending} />
    </section>
  );
}
