import { useQuery } from "@tanstack/react-query";
import { ArrowLeft, Lock, X } from "lucide-react";
import { lazy, Suspense, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { publishedDashboardQuery, type Dashboard, type DashboardItem, type DashboardWidget, type PageFilter, type TextKind } from "../../api/app";
import { can, useMe } from "../../app/me";
import { ContentBadge } from "../../components/ContentBadge";
import { CopyStarterButton } from "../../components/CopyStarterButton";
import type { DrillTarget } from "../../components/DrillSheet";
import { DataAsOf } from "../../components/DataAsOf";
import { formatReportingDate } from "../../components/format";
import { TextBlock } from "../../components/TextBlock";
import { Alert, FormAlert } from "../../components/ui/alert";
import { Badge } from "../../components/ui/badge";
import { Button } from "../../components/ui/button";
import { EmptyState } from "../../components/ui/empty-state";
import { Skeleton } from "../../components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "../../components/ui/tabs";
import { Tooltip, TooltipContent, TooltipTrigger } from "../../components/ui/tooltip";
import { asApiError } from "../../lib/api-errors";
import { type AskAbout, DashboardAskLauncher, loadDashboardAskPanel } from "../ask/DashboardAskLauncher";
import { useLayout } from "../../design/layout";
import { cn } from "../../lib/utils";
import { ExportMenu } from "./ExportMenu";
import { FilterBar, type FilterDef } from "./FilterBar";
import { type CrossFilter, ViewerWidget } from "./ViewerWidget";

// Drill to records loads on first use; it stays mounted after that so the sheet can animate closed.
const DrillSheet = lazy(() => import("../../components/DrillSheet").then((m) => ({ default: m.DrillSheet })));
// So does the Ask panel (US-189), which keeps its conversation while the reader is on the dashboard.
const DashboardAskPanel = lazy(() => loadDashboardAskPanel().then((m) => ({ default: m.DashboardAskPanel })));

type Tab = Dashboard["tabs"][number];
type Placed = { key: string; placement: DashboardWidget["placement"]; widget?: DashboardWidget; text?: DashboardItem };

/**
 * Dashboard viewer (FEAT-042 – 044; SCR-04; US-139): header with certification, version, as-of and the locked scope;
 * the filter bar; tabs or grouped reports; KPI and widget cards on the 12-column grid (two columns below 1024 px, one
 * below 768 px); definition popovers; drill to records in a side sheet. Published dashboards render their live
 * version; `draft` renders the owner's draft, always in the reader's own scope.
 */
export function DashboardPage({ draft = false }: { draft?: boolean }) {
  const { id } = useParams();
  const dash = useQuery(publishedDashboardQuery(id, draft));
  const me = useMe().data;
  const layout = useLayout();
  const [filters, setFilters] = useState<PageFilter[]>([]);
  const d = dash.data;

  if (dash.isLoading) return <ViewerSkeleton />;
  if (dash.error) return <div className="tl grid gap-4"><FormAlert error={asApiError(dash.error)} /><div><Button asChild variant="secondary"><Link to="/">Go to Home</Link></Button></div></div>;
  if (!d) return null;

  return (
    <div className="tl grid min-w-0 grid-cols-1 gap-4">
      {/* Executive layout (ENH-021): the header sits on a brand band; Aurora (ENH-027) on a rounded hero band (index.css). */}
      <header data-dash-header {...(layout === "executive" || layout === "aurora" ? { "data-nav": "brand" } : {})} className="flex flex-wrap items-start justify-between gap-3">
        <div className="grid min-w-0 gap-2">
          <h1 className="text-display">{d.title}</h1>
          <div className="flex flex-wrap items-center gap-1.5">
            <ContentBadge badge={draft ? "draft" : "certified"} />
            {d.kind === "starter" && <ContentBadge badge="starter" />}
            {d.version ? <Badge>Version {d.version}</Badge> : null}
            <DataAsOf reportingDate={d.reporting_date} refreshedAt={d.as_of} />
            <Tooltip>
              <TooltipTrigger asChild>
                <Badge variant="outline" tabIndex={0} aria-label={`Your data scope: ${d.locked_scope_chip}. ${d.scope_message}`}>
                  <Lock aria-hidden />{d.locked_scope_chip}
                </Badge>
              </TooltipTrigger>
              <TooltipContent className="max-w-xs">{d.scope_message}</TooltipContent>
            </Tooltip>
          </div>
        </div>
        {draft ? (
          <Button asChild variant="secondary"><Link to={`/dashboards/${d.id}/edit`}><ArrowLeft aria-hidden />Back to the composer</Link></Button>
        ) : can(me, "export.use") ? (
          <ExportMenu dashboardId={d.id} versionId={d.version_id ?? null} filters={filters} />
        ) : null}
      </header>
      <DashboardBody dashboard={d} draft={draft} filters={filters} onFiltersChange={setFilters} />
    </div>
  );
}

/**
 * Filter bar, tabs or grouped reports, drill and the Ask panel (US-189) for one rendered dashboard. The composer
 * reuses it for its live preview (US-142): `preview` sizes the grid by its container instead of the window, without Ask.
 */
export function DashboardBody({ dashboard: d, draft = false, preview = false, filters: controlled, onFiltersChange }: {
  dashboard: Dashboard;
  draft?: boolean;
  preview?: boolean;
  /** Controlled page filters: the viewer page owns them so Export uses what the reader sees. */
  filters?: PageFilter[];
  onFiltersChange?: (f: PageFilter[]) => void;
}) {
  const me = useMe().data;
  const [own, setOwn] = useState<PageFilter[]>([]);
  const filters = controlled ?? own;
  const setFilters = onFiltersChange ?? setOwn;
  const [drill, setDrillTarget] = useState<DrillTarget | null>(null);
  const [drillUsed, setDrillUsed] = useState(false);
  const setDrill = (t: DrillTarget | null) => {
    if (t) setDrillUsed(true);
    setDrillTarget(t);
  };
  const canAsk = !preview && can(me, "ask.use");
  const [chat, setChat] = useState(false);
  const [chatUsed, setChatUsed] = useState(false);
  const [about, setAbout] = useState<AskAbout | null>(null);
  const openChat = () => {
    setChatUsed(true);
    setChat(true);
  };
  const askAbout = (w: AskAbout) => {
    setAbout(w);
    openChat();
  };
  const tabs = useMemo(() => (d.tabs ?? []).filter(Boolean), [d]);
  const filterDefs = useMemo<FilterDef[]>(() => (d.filters ?? []).map((f) => {
    const connectionId = f.connection_id ?? d.connection_id ?? null;
    const modelVersionId = d.model_version_id ?? tabs.flatMap((t) => t.widgets ?? []).find((w) => w.connection_id === connectionId)?.model_version_id ?? null;
    return { ref: f.ref, label: f.label, connectionId, modelVersionId };
  }), [d, tabs]);
  const filterLabels = useMemo(() => Object.fromEntries(filterDefs.map((f) => [f.ref, f.label])), [filterDefs]);

  const body = (t: Tab) => (
    <div className="grid gap-3">
      {t.reporting_date && t.reporting_date !== d.reporting_date && (
        <p className="text-small text-ink-muted">Data as of {formatReportingDate(t.reporting_date)}</p>
      )}
      <StarterTabNotice tab={t} canCopy={!preview && can(me, "reports.create")} />
      <ReportBody tab={t} dashboard={d} filters={filters} filterLabels={filterLabels} onDrill={setDrill}
        onAsk={canAsk ? askAbout : undefined} preview={preview} />
    </div>
  );

  return (
    <div className={cn("grid min-w-0 grid-cols-1 gap-4", preview && "@container")}>
      <FilterBar filters={filterDefs} value={filters} onChange={setFilters} />

      {tabs.length === 0 ? (
        <EmptyState title="This dashboard has no reports yet">{draft ? "Add reports in the composer." : "Its owner hasn't added any reports."}</EmptyState>
      ) : d.layout === "grouped" || tabs.length === 1 ? (
        <div className="grid gap-8">
          {tabs.map((t) => (
            <section key={t.id} aria-label={t.group_title ?? t.title} className="grid gap-3">
              {tabs.length > 1 && <h2 className="flex flex-wrap items-center gap-2 text-title">{t.group_title ?? t.title}{deleted(t) && <ContentBadge badge="deleted" />}</h2>}
              {body(t)}
            </section>
          ))}
        </div>
      ) : (
        <Tabs defaultValue={tabs[0]!.id} className="min-w-0">
          <TabsList aria-label="Reports" className="min-w-0 overflow-x-auto overflow-y-hidden">
            {tabs.map((t) => <TabsTrigger key={t.id} value={t.id} className="gap-1.5">{t.title}{deleted(t) && <ContentBadge badge="deleted" />}</TabsTrigger>)}
          </TabsList>
          {tabs.map((t) => <TabsContent key={t.id} value={t.id}>{body(t)}</TabsContent>)}
        </Tabs>
      )}

      {drillUsed && <Suspense fallback={null}><DrillSheet target={drill} onClose={() => setDrill(null)} /></Suspense>}
      {canAsk && !chat && <DashboardAskLauncher onOpen={openChat} />}
      {canAsk && chatUsed && (
        <Suspense fallback={null}>
          <DashboardAskPanel dashboard={d} filters={filters} open={chat} onOpenChange={setChat} about={about} onAboutChange={setAbout} />
        </Suspense>
      )}
    </div>
  );
}

const deleted = (t: Tab) => !!t.badges?.includes("deleted");

/**
 * Above a report: why a deleted starter report is still here (RULE-017: the live version stays until republished), and
 * "Copy to my reports" on a starter dashboard's tab for Creators (REQ-114). The copy is theirs; pack updates skip it.
 */
function StarterTabNotice({ tab, canCopy }: { tab: Tab; canCopy: boolean }) {
  if (deleted(tab)) {
    return <Alert variant="warn">An Admin deleted the starter report {tab.title}. It stays on this version until the dashboard is republished.</Alert>;
  }
  if (!tab.report_id || !canCopy) return null;
  return <div className="flex justify-end"><CopyStarterButton reportId={tab.report_id} /></div>;
}

function ReportBody({ tab, dashboard, filters, filterLabels, onDrill, onAsk, preview }: {
  tab: Tab;
  preview?: boolean;
  dashboard: Dashboard;
  filters: PageFilter[];
  filterLabels: Record<string, string>;
  onDrill: (t: DrillTarget) => void;
  onAsk?: (w: { id: string; title: string }) => void;
}) {
  // Cross-filter within a report (US-157): a click on a chart mark filters the report's detail tables; clicking
  // the same mark again, or Clear, removes it. Without a table on the report a click drills, as before.
  const [cross, setCross] = useState<CrossFilter | null>(null);
  const widgets = tab.sections
    ? tab.sections.flatMap((s) => (s.items ?? []).filter((i) => i.kind === "widget") as unknown as DashboardWidget[])
    : tab.widgets ?? [];
  const hasTable = widgets.some((w) => w.visual === "table" && !w.hidden);
  const toggle = (next: CrossFilter) => setCross((cur) => (cur && cur.widgetId === next.widgetId && cur.text === next.text ? null : next));
  const grid = (items: Placed[]) => (
    <WidgetGrid items={items} dashboard={dashboard} filters={filters} filterLabels={filterLabels} onDrill={onDrill} onAsk={onAsk} preview={preview}
      cross={cross} onCrossFilter={hasTable ? toggle : undefined} />
  );
  const bar = (
    <div aria-live="polite">
      {cross && (
        <div className="flex flex-wrap items-center gap-2 rounded-md border border-line bg-accent-soft px-3 py-2 text-small text-ink" role="status">
          <span>Tables filtered by <strong className="font-medium">{cross.text}</strong> (from {cross.title})</span>
          <Button variant="ghost" size="sm" className="h-control-sm px-2" onClick={() => setCross(null)}><X aria-hidden />Clear</Button>
        </div>
      )}
    </div>
  );
  if (!tab.sections) {
    // A starter tab: the pack's callouts open the report and its footnotes close it (US-158).
    return (
      <div className="grid gap-3">
        {(tab.callouts ?? []).map((c, n) => <TextBlock key={n} kind="callout" title={c.title} text={c.text} level={c.level} />)}
        {bar}
        {grid((tab.widgets ?? []).map((w) => ({ key: w.id, placement: w.placement, widget: w })))}
        {(tab.footnotes ?? []).map((f, n) => <TextBlock key={n} kind="footnote" text={f} />)}
      </div>
    );
  }
  return (
    <div className="grid gap-6">
      {bar}
      {tab.sections.map((s) => (
        <section key={s.id} aria-label={s.title} className="grid gap-3">
          {(tab.sections!.length > 1 || s.kind === "headline") && <h3 className="text-heading text-ink-muted">{s.title}</h3>}
          {grid((s.items ?? []).map((i: DashboardItem) => i.kind !== "widget"
            ? { key: i.item_id, placement: i.placement, text: i }
            : { key: i.item_id, placement: i.placement, widget: i as unknown as DashboardWidget }))}
        </section>
      ))}
    </div>
  );
}

function WidgetGrid({ items, dashboard: d, filters, filterLabels, onDrill, onAsk, preview, cross, onCrossFilter }: {
  items: Placed[];
  preview?: boolean;
  dashboard: Dashboard;
  filters: PageFilter[];
  filterLabels: Record<string, string>;
  onDrill: (t: DrillTarget) => void;
  onAsk?: (w: { id: string; title: string }) => void;
  cross?: CrossFilter | null;
  onCrossFilter?: (c: CrossFilter) => void;
}) {
  const sorted = [...items].filter((i) => i.placement).sort((a, b) => a.placement.y - b.placement.y || a.placement.x - b.placement.x);
  if (sorted.length === 0) return <p className="text-ink-muted">No widgets in this report.</p>;
  return (
    <div className={cn("grid grid-cols-1 gap-4", preview ? "@md:grid-cols-2 @3xl:grid-cols-12" : "md:grid-cols-2 lg:grid-cols-12")}>
      {sorted.map((i) => {
        const w = Math.min(12, Math.max(1, i.placement.w));
        const cell = preview
          ? cn("min-w-0 @3xl:[grid-column:span_var(--span)/span_var(--span)]", w > 6 ? "@md:col-span-2" : "@md:col-span-1")
          : cn("min-w-0 lg:[grid-column:span_var(--span)/span_var(--span)]", w > 6 ? "md:col-span-2" : "md:col-span-1");
        const style = { "--span": w } as React.CSSProperties;
        if (i.text) {
          const t = i.text;
          return <TextBlock key={i.key} kind={t.kind as TextKind} text={t.text ?? ""} title={t.title} level={t.level} className={cell} style={style} />;
        }
        const wd = i.widget!;
        const table = wd.visual === "table";
        return (
          <div key={i.key} className={cn(cell, "grid")} style={style}>
            <ViewerWidget id={wd.id} title={wd.title} visual={wd.visual} measure={wd.measure}
              connectionId={wd.connection_id ?? d.connection_id} modelVersionId={wd.model_version_id ?? d.model_version_id}
              versionId={d.version_id ?? null} filters={table && cross ? [...filters, ...cross.filters] : filters}
              filterLabels={filterLabels} hidden={wd.hidden}
              hiddenMessage={wd.hidden_message} badges={wd.badges} onDrill={onDrill} onAsk={onAsk}
              onCrossFilter={table ? undefined : onCrossFilter} crossSelected={cross?.widgetId === wd.id ? cross.mark : null} />
          </div>
        );
      })}
    </div>
  );
}

function ViewerSkeleton() {
  return (
    <div className="tl grid gap-4" aria-busy="true" aria-label="Loading">
      <Skeleton className="h-9 w-1/2" />
      <Skeleton className="h-6 w-2/3" />
      <div className="grid gap-4 md:grid-cols-3"><Skeleton className="h-28" /><Skeleton className="h-28" /><Skeleton className="h-28" /></div>
      <Skeleton className="h-64" />
    </div>
  );
}
