import { useMutation, useQuery } from "@tanstack/react-query";
import { History } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { api } from "../../../api/client";
import type { Branding, BrandSlot, LogoSizes } from "../../../app/branding";
import { formatAsOf } from "../../../components/format";
import { FormAlert } from "../../../components/ui/alert";
import { Badge } from "../../../components/ui/badge";
import { Button } from "../../../components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../../../components/ui/card";
import type { ConfirmRequest } from "../../../components/ui/confirm-dialog";
import { CHART_LIBRARIES, type ChartLibrary } from "../../../design/chartLibrary";
import { LAYOUTS, type Layout } from "../../../design/layout";
import { NAV_STYLES, type NavColors } from "../../../design/navColors";
import { asApiError } from "../../../lib/api-errors";

const navLabel = (id: string | undefined) => (NAV_STYLES.find((s) => s.id === id) ?? NAV_STYLES[0]!).label.toLowerCase();

/** GET /api/v1/admin/branding/history (US-151): what the branding looked like after each kept change. */
export interface BrandingVersion {
  version: number;
  change: string;
  changed_at: string;
  changed_by: { id: string; name: string | null } | null;
  current: boolean;
  app_name: string;
  tagline: string | null;
  logo_sizes: LogoSizes;
  /** ENH-018, US-206; absent in versions kept before it. */
  chart_library?: ChartLibrary;
  /** ENH-019, US-209; absent in versions kept before it. */
  nav_colors?: NavColors;
  /** ENH-021; absent in versions kept before it. */
  layout?: Layout;
  palette: { name: string; accent: string } | null;
  assets: Record<BrandSlot, boolean>;
}

const CHANGES: Record<string, string> = {
  names: "Name changed",
  logo_light_uploaded: "New logo",
  logo_dark_uploaded: "New dark-theme logo",
  favicon_uploaded: "New tab icon",
  logo_light_removed: "Logo removed",
  logo_dark_removed: "Dark-theme logo removed",
  favicon_removed: "Tab icon removed",
  palette: "Colours changed",
  palette_removed: "Default colours restored",
  logo_size: "Logo size changed",
  chart_library: "Chart library changed",
  nav_colors: "Bar colours changed",
  layout: "Default layout changed",
  reset: "Reset to defaults",
  rollback: "Rolled back",
  current: "Branding as it was",
};

const SHOWN = 10;

/**
 * History (ENH-006, US-151): every change to the branding, newest first, with roll back to any kept version. A roll
 * back is itself a new version, so it can be undone the same way.
 */
export function HistoryCard({ branding, onSaved, onConfirm }: {
  branding: Branding; onSaved: (b: Branding) => void; onConfirm: (r: ConfirmRequest) => void;
}) {
  const [all, setAll] = useState(false);
  const history = useQuery({
    queryKey: ["branding-history", branding.version],
    queryFn: () => api<{ items: BrandingVersion[]; version: number; keep: number }>("/api/v1/admin/branding/history"),
  });
  const rollback = useMutation({
    mutationFn: (v: BrandingVersion) => api<Branding>("/api/v1/admin/branding/rollback", { method: "POST", body: { version: v.version, expected_version: branding.version } }),
    onSuccess: (b, v) => {
      onSaved(b);
      toast.success(`Rolled back to version ${v.version} — everyone sees it now`);
    },
    onError: (e) => toast.error("The branding could not be rolled back", { description: asApiError(e).message }),
  });
  const items = history.data?.items ?? [];
  const shown = all ? items : items.slice(0, SHOWN);
  return (
    <Card>
      <CardHeader>
        <CardTitle>History</CardTitle>
        <CardDescription>Every change to the branding. Roll back to bring a version back for everyone; the roll back is kept as a new version{history.data ? ` (the last ${history.data.keep} are kept)` : ""}.</CardDescription>
      </CardHeader>
      <CardContent className="grid gap-3">
        {history.isLoading && <p aria-busy="true" className="text-ink-muted">Loading…</p>}
        {history.error && <FormAlert error={asApiError(history.error)} />}
        {history.data && items.length === 0 && <p className="text-ink-muted">No changes yet — {branding.app_name} shows its defaults.</p>}
        {items.length > 0 && (
          <ol className="grid divide-y divide-line rounded-md border border-line" aria-label="Branding versions">
            {shown.map((v) => (
              <li key={v.version} className="flex flex-wrap items-center gap-x-4 gap-y-2 px-3 py-2" aria-label={`Version ${v.version}`}>
                <div className="grid min-w-0 flex-1 gap-0.5">
                  <span className="flex flex-wrap items-center gap-2">
                    <span className="font-medium">{CHANGES[v.change] ?? v.change}</span>
                    <span className="text-small text-ink-muted">v{v.version}</span>
                    {v.current && <Badge variant="certified">Current</Badge>}
                  </span>
                  <span className="text-small text-ink-muted">
                    {formatAsOf(v.changed_at)}{v.changed_by?.name ? ` · ${v.changed_by.name}` : ""}
                  </span>
                  <span className="flex flex-wrap items-center gap-x-3 gap-y-1 text-small text-ink-muted">
                    <span className="text-ink">{v.app_name}{v.tagline ? ` · ${v.tagline}` : ""}</span>
                    <span className="inline-flex items-center gap-1">
                      {v.palette && <span aria-hidden className="inline-block size-3 rounded-full border border-line" style={{ backgroundColor: v.palette.accent }} />}
                      {v.palette ? v.palette.name : "Default colours"}
                    </span>
                    <span>{v.assets.logo_light ? "Own logo" : "Default logo"}</span>
                    <span>Logo {v.logo_sizes.sidebar} / {v.logo_sizes.sign_in} / {v.logo_sizes.export} px</span>
                    <span>{CHART_LIBRARIES.find((l) => l.id === (v.chart_library ?? "echarts"))?.label} charts</span>
                    <span>Sidebar {navLabel(v.nav_colors?.sidebar)} · top bar {navLabel(v.nav_colors?.topbar)}</span>
                    <span>{LAYOUTS.find((l) => l.id === (v.layout ?? "classic"))?.label} layout</span>
                  </span>
                </div>
                {!v.current && (
                  <Button variant="secondary" size="sm" disabled={rollback.isPending} onClick={() => onConfirm({
                    title: `Roll back to version ${v.version}?`,
                    body: `Everyone sees “${v.app_name}” with ${v.palette ? `the ${v.palette.name} colours` : "the default colours"} and ${v.assets.logo_light ? "that version’s logo" : "the default logo"} straight away. The current branding stays in the history.`,
                    confirm: "Roll back", onConfirm: () => rollback.mutate(v),
                  })}>
                    <History aria-hidden className="size-4" />Roll back<span className="sr-only"> to version {v.version}</span>
                  </Button>
                )}
              </li>
            ))}
          </ol>
        )}
        {items.length > SHOWN && (
          <Button variant="link" size="sm" className="justify-self-start" onClick={() => setAll(!all)}>{all ? "Show fewer" : `Show all ${items.length} versions`}</Button>
        )}
      </CardContent>
    </Card>
  );
}
