import { useQuery } from "@tanstack/react-query";
import { useEffect } from "react";

import { api } from "../api/client";
import type { DashboardListItem } from "../api/authoring";
import { Label } from "./ui/label";
import { NativeSelect } from "./ui/native-select";

export interface PinPage { dashboard_id: string; dashboard: string; report_id: string; section_id: string | null }

/** The "Pinned from chat" page of a dashboard (made and put first the first time): where a pinned answer goes. */
export const pinPageOf = (dashboardId: string) =>
  api<PinPage>(`/api/v1/dashboards/${dashboardId}/pin-page`, { method: "POST" });

/** Pick one of your dashboards to show a pinned chat answer on (its "Pinned from chat" tab). */
export function DashboardPinPick({ value, onChange, id = "pin-dashboard" }: { value: string; onChange: (v: string) => void; id?: string }) {
  const list = useQuery({ queryKey: ["dashboards", false], queryFn: () => api<{ items: DashboardListItem[] }>("/api/v1/dashboards") });
  const mine = (list.data?.items ?? []).filter((d) => d.mine);
  // A dashboard you can't change (shared or published to you) isn't offered: start on "No dashboard" instead.
  useEffect(() => {
    if (list.data && value && !mine.some((d) => d.id === value)) onChange("");
  }, [list.data]); // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <div className="grid gap-1.5">
      <Label htmlFor={id}>Show on dashboard</Label>
      <NativeSelect id={id} value={value} onChange={(e) => onChange(e.target.value)}>
        <option value="">No dashboard (report or library only)</option>
        {mine.map((d) => <option key={d.id} value={d.id}>{d.title}</option>)}
      </NativeSelect>
      <p className="text-small text-ink-muted">It goes on the dashboard's “Pinned from chat” tab, first in the dashboard.</p>
    </div>
  );
}
