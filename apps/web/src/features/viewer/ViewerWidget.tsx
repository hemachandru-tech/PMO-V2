import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { BarChart3, EllipsisVertical, EyeOff, ListFilter, Lock, MessageSquare, RotateCw, Rows3, Table2 } from "lucide-react";
import { lazy, Suspense, useEffect, useState } from "react";

import { api } from "../../api/client";
import type { Column, Kpi, PageFilter, WidgetRun } from "../../api/app";
import { can, useMe } from "../../app/me";
import { Chart, Sparkline } from "../../components/Chart";
import { ContentBadge } from "../../components/ContentBadge";
import { DefinitionButton, type DefinitionProps } from "../../components/Definition";
import type { DrillTarget } from "../../components/DrillSheet";
import { formatValue } from "../../components/format";
import { FormAlert } from "../../components/ui/alert";
import { Button } from "../../components/ui/button";
import { Card } from "../../components/ui/card";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from "../../components/ui/dropdown-menu";
import { KpiCard, type KpiDelta } from "../../components/ui/kpi-card";
import { Popover, PopoverContent, PopoverTrigger } from "../../components/ui/popover";
import { Skeleton } from "../../components/ui/skeleton";
import { Tooltip, TooltipContent, TooltipTrigger } from "../../components/ui/tooltip";
import { asApiError } from "../../lib/api-errors";
import { cn } from "../../lib/utils";
import { FilterChipPicker } from "./FilterBar";

// A detail table brings the data-table library; it loads with the first report that has one (US-158).
const DetailTable = lazy(() => import("../../components/DetailTable").then((m) => ({ default: m.DetailTable })));

export interface ViewerWidgetProps {
  id: string;
  title: string;
  visual: string;
  measure?: string;
  connectionId?: string | null;
  modelVersionId?: string | null;
  versionId: string | null;
  filters: PageFilter[];
  /** Labels of the dashboard's filters, to say which ones don't reach this widget. */
  filterLabels: Record<string, string>;
  hidden?: boolean;
  hiddenMessage?: string | null;
  badges?: string[];
  className?: string;
  onAsk?: (w: { id: string; title: string }) => void;
  onDrill: (target: DrillTarget) => void;
  /** Within a report with a detail table (US-157): a chart mark filters the tables instead of drilling. */
  onCrossFilter?: (c: CrossFilter) => void;
  /** The mark of this widget the report is cross-filtered by. */
  crossSelected?: Record<string, unknown> | null;
}

export interface CrossFilter {
  widgetId: string;
  title: string;
  mark: Record<string, unknown>;
  filters: PageFilter[];
  /** "Region: KSA, Reported status: Red". */
  text: string;
}

/**
 * Page filters from a chart mark: one per breakdown column the mark names. Null when a value is blank (Not set) or a
 * column is not a breakdown — page filters can't say "blank", so that mark drills to the records instead.
 */
export function markFilters(mark: Record<string, unknown>, columns: Column[]): { filters: PageFilter[]; text: string } | null {
  const filters: PageFilter[] = [];
  const words: string[] = [];
  for (const [name, value] of Object.entries(mark)) {
    const col = columns.find((c) => c.name === name);
    if (!col?.ref || col.kind === "measure" || !/^(dimension|field):/.test(col.ref)) return null;
    if (value === null || value === undefined || (typeof value !== "string" && typeof value !== "number")) return null;
    filters.push({ ref: col.ref, op: "=", values: [value] });
    words.push(`${col.label}: ${value}`);
  }
  return filters.length ? { filters, text: words.join(", ") } : null;
}

const filtersText = (filters: PageFilter[], labels: Record<string, string>) =>
  filters.map((f) => `${labels[f.ref] ?? f.ref.split(":")[1]}: ${f.values.join(", ")}`).join("; ");

/**
 * One live widget on the dashboard viewer (FEAT-042 – 044; US-139). It runs for the reader (scope, filters, cache),
 * shows a KPI card for single figures and a widget card otherwise, with one header pattern: title, badges, definition,
 * chart ↔ table, menu. Each widget fails on its own (TEST-051).
 */
export function ViewerWidget(props: ViewerWidgetProps) {
  const { id, filters, versionId, hidden } = props;
  // The reader's picks in the widget's own filters (US-221): this widget only, kept while the page is open.
  const [own, setOwn] = useState<PageFilter[]>([]);
  const run = useQuery({
    queryKey: ["widget", id, versionId, filters, own],
    queryFn: () => api<WidgetRun>(`/api/v1/widgets/${id}/run`, {
      method: "POST", body: { page_filters: filters, widget_filters: own, context: { dashboard_version_id: versionId } },
    }),
    enabled: !hidden,
    retry: false,
    // A refresh (new filters, a cross-filter) keeps the old figures on screen, dimmed, instead of a blank loader.
    placeholderData: keepPreviousData,
  });
  const refreshing = run.isFetching && run.data !== undefined;
  // A filter the author took away is dropped, so the next run does not ask for it.
  const offered = run.data?.controls;
  useEffect(() => {
    if (offered && own.some((f) => !offered.some((c) => c.ref === f.ref))) setOwn(own.filter((f) => offered.some((c) => c.ref === f.ref)));
  }, [offered, own]);
  const connectionId = props.connectionId ?? run.data?.connection_id;
  const modelVersionId = props.modelVersionId ?? run.data?.model_version?.id;
  const definition: DefinitionProps | undefined = props.measure && connectionId
    ? { refId: props.measure, connectionId, modelVersionId: modelVersionId ?? undefined, filtersText: filtersText(filters, props.filterLabels) }
    : undefined;
  const badges = props.badges ?? run.data?.badges ?? [];
  const isFigure = props.visual === "figure" || run.data?.viz?.visual === "figure";
  const own_ = { own, onOwn: setOwn, connectionId: connectionId ?? null, modelVersionId: modelVersionId ?? null };
  // Every figure is a KPI card, so a row of figures matches (EXEC-214): while it loads, and when the run has no KPI
  // extras (a Custom SQL figure), the card shows the run's own first value with its format.
  const kpi = run.data && !run.data.hidden ? run.data.kpi ?? plainKpi(run.data) : null;
  if (isFigure && !hidden && (run.isLoading || (run.data && kpi))) {
    return <KpiWidget {...props} {...own_} run={run.data} kpi={kpi} badges={badges} definition={definition} refreshing={refreshing} />;
  }
  return <WidgetCard {...props} {...own_} run={run} badges={badges} definition={definition} refreshing={refreshing} />;
}

/** A figure's value as the run returned it (first measure, or first column, of the first row), with no extras. */
function plainKpi(run: WidgetRun): Kpi | null {
  if (!run.rows?.length || !run.columns?.length) return null;
  const i = Math.max(0, run.columns.findIndex((c) => c.kind === "measure"));
  const raw = run.rows[0]![i];
  const value = typeof raw === "number" ? raw : typeof raw === "string" && raw.trim() !== "" ? Number(raw) : NaN;
  if (!Number.isFinite(value)) return null;
  return { value, format: run.columns[i]!.format ?? null, change: null, sparkline: null, tone: null, definition: null };
}

interface OwnFilters {
  own: PageFilter[];
  onOwn: (next: PageFilter[]) => void;
  connectionId: string | null;
  modelVersionId: string | null;
}

const OP_WORDS: Record<string, string> = { "!=": "not ", not_in: "not ", "<": "below ", "<=": "at most ", ">": "above ", ">=": "at least " };

/**
 * A widget's own filters (ENH-023, US-221): the author's fixed filters as a lock (always applied; the words in its
 * tooltip and accessible text), and a chip for each field the author offered, which narrows this widget only. Values
 * come in the reader's scope. They sit in the widget's header row; on a KPI card (`compact`) they fold into one
 * filter button so the card keeps one line.
 */
function WidgetFilters({ run, own, onOwn, connectionId, modelVersionId, compact = false }: OwnFilters & { run?: WidgetRun; compact?: boolean }) {
  const controls = run?.controls ?? [];
  const fixed = run?.fixed_filters ?? [];
  if (!controls.length && !fixed.length) return null;
  const selected = (ref: string) => own.find((f) => f.ref === ref)?.values.map(String) ?? [];
  const set = (ref: string, values: string[]) =>
    onOwn([...own.filter((f) => f.ref !== ref), ...(values.length ? [{ ref, op: values.length === 1 ? "=" as const : "in" as const, values }] : [])]);
  const fixedText = fixed.map((f) => `${f.label}: ${OP_WORDS[f.op ?? ""] ?? ""}${f.values.map((v) => formatValue(v)).join(", ")}`).join("; ");
  const lock = fixed.length > 0 && (
    <Tooltip>
      <TooltipTrigger asChild>
        <span tabIndex={0} className="inline-flex size-control-sm items-center justify-center rounded-full text-ink-muted focus-visible:focus-ring">
          <Lock aria-hidden className="size-3.5" />
          <span className="sr-only">Always filtered to {fixedText}</span>
        </span>
      </TooltipTrigger>
      <TooltipContent className="max-w-72"><span className="font-semibold">Always filtered</span><span className="block">{fixedText}</span></TooltipContent>
    </Tooltip>
  );
  const chips = controls.map((c) => (
    <FilterChipPicker key={c.ref} filter={{ ref: c.ref, label: c.label, connectionId, modelVersionId }}
      selected={selected(c.ref)} onChange={(v) => set(c.ref, v)} />
  ));
  if (compact && controls.length) {
    const picked = own.filter((f) => controls.some((c) => c.ref === f.ref)).length;
    return (
      <>
        {lock}
        <Popover>
          <PopoverTrigger asChild>
            <Button variant="ghost" size="icon" className="relative size-control-sm" aria-label={picked ? `Widget filters (${picked} applied)` : "Widget filters"} title="Filter this widget">
              <ListFilter aria-hidden />
              {picked > 0 && <span aria-hidden className="absolute right-0.5 top-0.5 size-1.5 rounded-full bg-accent" />}
            </Button>
          </PopoverTrigger>
          <PopoverContent align="end" className="w-auto max-w-80 p-3">
            <div role="group" aria-label="Widget filters" data-widget-filters className="flex flex-wrap items-center gap-1.5">{chips}</div>
          </PopoverContent>
        </Popover>
      </>
    );
  }
  return (
    <div role="group" aria-label="Widget filters" data-widget-filters className="flex flex-wrap items-center justify-end gap-1.5">
      {lock}
      {chips}
    </div>
  );
}

function Reach({ run, labels }: { run?: WidgetRun; labels: Record<string, string> }) {
  if (!run || Object.keys(labels).length === 0) return null;
  const missed = run.filters_not_applied.map((r) => labels[r] ?? r.split(":")[1]);
  return <span>{missed.length ? `Not affected by ${missed.join(", ")}` : "All filters apply"}</span>;
}

function KpiWidget({ title, run, kpi, badges, definition, className, filterLabels, onDrill, onAsk, id, versionId, filters, refreshing, ...own }: ViewerWidgetProps & OwnFilters & {
  run?: WidgetRun; kpi: Kpi | null; badges: string[]; definition?: DefinitionProps; refreshing: boolean;
}) {
  const me = useMe().data;
  const c = kpi?.change;
  const delta: KpiDelta | undefined = c ? {
    direction: c.change > 0 ? "up" : c.change < 0 ? "down" : "flat",
    text: `${c.change > 0 ? "+" : ""}${formatValue(c.change, kpi?.format)}${c.change_pct !== null ? ` (${c.change > 0 ? "+" : ""}${c.change_pct}%)` : ""} vs ${c.previous_range}`,
  } : undefined;
  const s = kpi?.sparkline;
  const range = s?.range === "last_12_months" ? "last 12 months" : s?.range.replace(/_/g, " ");
  const card = run?.viz.chart_spec?.card ?? undefined;
  const drillable = can(me, "drill.use") && !badges.includes("custom_sql");
  const drill = () => onDrill({ title, mark: {}, widgetId: id, versionId, pageFilters: filters, widgetFilters: own.own, definition });
  return (
    <KpiCard
      data-card={card}
      // A dark card draws its content in the dark theme, whatever the page's (US-223).
      data-theme={card === "dark" ? "dark" : undefined}
      filters={<WidgetFilters run={run} {...own} compact />}
      role="article"
      aria-label={title}
      aria-busy={refreshing || !kpi || undefined}
      className={cn(className, "transition-opacity", refreshing && "opacity-60")}
      label={title}
      loading={!kpi}
      value={kpi ? formatValue(kpi.value, kpi.format) : ""}
      delta={delta}
      tone={kpi?.tone}
      definition={kpi?.definition}
      valueTitle="See the definition and the records behind this figure"
      onValueClick={drillable && kpi ? drill : undefined}
      badges={<>{badges.map((b) => <ContentBadge key={b} badge={b} />)}{definition && <DefinitionButton title={title} {...definition} />}
        {run && <WidgetMenu title={title} onRecords={drillable ? drill : undefined} onAsk={onAsk ? () => onAsk({ id, title }) : undefined} />}</>}
      footer={(
        <div className="grid gap-1">
          {s && (
            <>
              <Sparkline points={s.points} label={`${title} by ${s.by}, ${range}: ${s.points.map((p) => `${p.period.slice(0, 7)} ${formatValue(p.value, kpi.format)}`).join(", ")}`} />
              <span>By {s.by.toLowerCase()}, {range}</span>
            </>
          )}
          <Reach run={run} labels={filterLabels} />
        </div>
      )}
    />
  );
}

function WidgetCard(props: ViewerWidgetProps & OwnFilters & { run: ReturnType<typeof useQuery<WidgetRun>>; badges: string[]; definition?: DefinitionProps; refreshing: boolean }) {
  const { id, title, run, badges, definition, hidden, versionId, filters, onAsk, onDrill, refreshing } = props;
  const me = useMe().data;
  const [view, setView] = useState<"chart" | "table">("chart");
  const data = run.data;
  const ready = !hidden && data && !data.hidden;
  const chartable = ready && !data.detail && data.viz.chart_spec && data.viz.chart_spec.type !== "table" && data.viz.chart_spec.type !== "figure";
  const drillable = can(me, "drill.use") && !badges.includes("custom_sql");
  const drill = (mark: Record<string, unknown>) => onDrill({ title, mark, widgetId: id, versionId, pageFilters: filters, widgetFilters: props.own, definition });
  const onCross = props.onCrossFilter;
  const markClick = (mark: Record<string, unknown>) => {
    const cross = onCross && data ? markFilters(mark, data.columns) : null;
    if (cross && onCross) onCross({ widgetId: id, title, mark, ...cross });
    else if (drillable) drill(mark);
  };
  return (
    <Card data-widget data-card={(ready && data.viz.chart_spec?.card) || undefined} className={cn("min-w-0", props.className)} aria-label={title} role="article">
      {/* One header row: the title with its badges, then the widget's filters and actions; it wraps only when narrow. */}
      <div data-widget-head className="flex flex-wrap items-center justify-between gap-x-3 gap-y-2 px-4 pt-3">
        <div className="flex min-w-0 flex-1 basis-48 items-center gap-1.5">
          <h3 data-widget-title className="truncate text-heading" title={title}>{title}</h3>
          {badges.length > 0 && <span className="flex shrink-0 items-center">{badges.map((b) => <ContentBadge key={b} badge={b} />)}</span>}
        </div>
        <div className="ml-auto flex min-w-0 flex-wrap items-center justify-end gap-1">
          <WidgetFilters run={data} own={props.own} onOwn={props.onOwn} connectionId={props.connectionId} modelVersionId={props.modelVersionId} />
          {chartable && (
            <Button variant="ghost" size="sm" className="h-control-sm px-2" aria-pressed={view === "table"} onClick={() => setView(view === "chart" ? "table" : "chart")}>
              {view === "chart" ? <><Table2 aria-hidden />Table</> : <><BarChart3 aria-hidden />Chart</>}
            </Button>
          )}
          {definition && <DefinitionButton title={title} {...definition} />}
          {ready && <WidgetMenu title={title} onRecords={drillable ? () => drill({}) : undefined} onAsk={onAsk ? () => onAsk({ id, title }) : undefined} />}
        </div>
      </div>
      <div data-widget-body className={cn("min-h-24 px-4 py-3 transition-opacity", refreshing && "opacity-60")} aria-busy={refreshing || undefined}>
        {hidden ? (
          <HiddenNote message={props.hiddenMessage ?? "Hidden for your role"} />
        ) : run.isLoading ? (
          <Skeleton className={props.visual === "figure" ? "h-16" : "h-48"} aria-label="Loading" />
        ) : run.error ? (
          <FormAlert error={asApiError(run.error)}>
            <Button variant="secondary" size="sm" className="justify-self-start" onClick={() => void run.refetch()}><RotateCw aria-hidden />Retry</Button>
          </FormAlert>
        ) : data?.hidden ? (
          <HiddenNote message={data.message ?? "Hidden for your role"} />
        ) : data?.detail ? (
          <Suspense fallback={<Skeleton className="h-48" aria-label="Loading" />}>
            <DetailTable title={title} columns={data.columns} rows={data.rows} detail={data.detail} total={data.row_count}
              tones={data.viz.chart_spec?.colour ? data.viz.chart_spec.tones ?? {} : undefined} onSeeAll={drillable ? () => drill({}) : undefined} />
          </Suspense>
        ) : data ? (
          <Chart spec={data.viz.chart_spec} columns={data.columns} rows={data.rows} title={title} view={chartable ? view : undefined}
            onMark={drillable || onCross ? markClick : undefined} selected={props.crossSelected} />
        ) : null}
      </div>
      {ready && (
        <div data-widget-foot className="flex flex-wrap justify-between gap-x-4 gap-y-1 border-t border-line px-4 py-2 text-small text-ink-muted">
          <span>{data.truncated ? data.notes?.[0] ?? "Showing the first rows only." : `${data.row_count.toLocaleString()} ${data.detail ? (data.row_count === 1 ? "record" : "records") : data.row_count === 1 ? "row" : "rows"}`}</span>
          <Reach run={data} labels={props.filterLabels} />
        </div>
      )}
    </Card>
  );
}

/** The widget's ⋮ menu: see the records behind it, or ask about it. Shown on charts, tables and KPI cards alike. */
function WidgetMenu({ title, onRecords, onAsk }: { title: string; onRecords?: () => void; onAsk?: () => void }) {
  if (!onRecords && !onAsk) return null;
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="ghost" size="icon" className="size-control-sm" aria-label={`More actions for ${title}`}><EllipsisVertical aria-hidden /></Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end">
        {onRecords && <DropdownMenuItem onSelect={onRecords}><Rows3 aria-hidden />See records</DropdownMenuItem>}
        {onAsk && <DropdownMenuItem onSelect={onAsk}><MessageSquare aria-hidden />Ask about this</DropdownMenuItem>}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

function HiddenNote({ message }: { message: string }) {
  return (
    <p className="flex items-center gap-2 text-ink-muted"><EyeOff aria-hidden className="size-4" />{message}</p>
  );
}
