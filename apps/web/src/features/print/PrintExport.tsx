import { SVGRenderer } from "echarts/renderers";
import * as echarts from "echarts/core";
import { useEffect } from "react";

import type { CalloutLevel, Column, KpiTone, StarterCallout } from "../../api/app";
import type { ChartSpec } from "../../api/querySpec";
import { Chart, isCatalogueVisual, isStandardVisual, loadCatalogue, loadChartLibrary } from "../../components/Chart";
import { activeChartLibrary } from "../../design/chartLibrary";
import { ContentBadge } from "../../components/ContentBadge";
import { formatValue } from "../../components/format";
import { TextBlock } from "../../components/TextBlock";
import defaultLogo from "../../design/tela-logo.png";
import { ThemeScope } from "../../design/theme";

echarts.use([SVGRenderer]);

/** Rows printed per table; the Excel export holds every row (US-097). */
export const PRINT_TABLE_ROWS = 40;

type Placement = { x: number; y: number; w: number; h: number };

export interface PrintWidget {
  kind: "widget";
  id: string;
  title: string;
  visual: string;
  placement: Placement;
  badges: string[];
  error?: string;
  columns?: Column[];
  rows?: unknown[][];
  row_count?: number;
  truncated?: boolean;
  filters_not_applied?: string[];
  /** A detail table's shown columns (US-158); the key columns stay out of print. */
  detail_columns?: string[];
  viz?: { visual: string; chart_spec: ChartSpec | null };
  kpi?: { value: number; format: string | null; change: { change: number; change_pct: number | null; previous_range: string } | null; tone?: KpiTone | null; definition?: string | null } | null;
}

export interface PrintNote {
  kind: "text_note" | "callout" | "footnote";
  text: string;
  title?: string | null;
  level?: CalloutLevel;
  placement: Placement;
}

/** What the API's export start returns for one person (US-095); the worker injects it as `window.__TELA_EXPORT__`. */
export interface ExportPayload {
  dashboard: { id: string; title: string; kind: string; layout: string; version: number | null };
  scope: string;
  as_of: string;
  /** The reporting date in words ("30 Sep 2026"; US-158), when the dashboard has one. */
  reporting_date?: string | null;
  filters_text: string;
  exported_at: string;
  footer: string;
  excluded_widgets: number;
  tabs: {
    title: string;
    sections: { title: string | null; kind: string | null; items: (PrintWidget | PrintNote)[] }[];
    reporting_date?: string | null;
    /** A starter tab's callouts and footnotes (US-158). */
    callouts?: StarterCallout[];
    footnotes?: string[];
  }[];
  /** The tenant's brand (US-151/152): logo inline as a data URL (none = Tela's), its height and light-theme colours. */
  branding?: { app_name: string; logo: string | null; logo_height: number; palette: Record<string, string> | null; chart_library?: string };
}

declare global {
  interface Window {
    __TELA_EXPORT__?: ExportPayload;
    __TELA_PRINT_READY__?: boolean;
  }
}

/**
 * Print-only route for the PDF renderer (US-096; FEAT-045; completes US-139's print styles). Every tab starts a new
 * A4 landscape page with the dashboard header; widgets keep their 12-column widths; charts are still SVG; tables print
 * their first rows. The footer on every page (filters, scope, data date, version, export time) is drawn by the PDF
 * renderer from the payload. The page never calls the API and needs no sign-in: the worker hands it the payload,
 * already scoped to the person who asked.
 */
export function PrintExport({ payload }: { payload: ExportPayload | undefined }) {
  useEffect(() => {
    document.documentElement.dataset.theme = "light";
    document.title = payload ? payload.dashboard.title : "Export";
    let cancelled = false;
    // Charts draw synchronously without animation; wait for fonts (and the visual catalogue's chunk when a widget
    // uses it, US-187, or the chosen chart library's, US-206) and two frames so layout has settled.
    const uses = (test: (type: string | undefined) => boolean) =>
      payload?.tabs.some((t) => t.sections.some((s) => s.items.some((i) => "viz" in i && test(i.viz?.chart_spec?.type))));
    const library = uses(isStandardVisual) ? loadChartLibrary(activeChartLibrary()) : null;
    void Promise.all([document.fonts.ready, uses(isCatalogueVisual) ? loadCatalogue() : null, library]).then(() => requestAnimationFrame(() => requestAnimationFrame(() => {
      if (!cancelled) window.__TELA_PRINT_READY__ = true;
    })));
    return () => {
      cancelled = true;
    };
  }, [payload]);

  if (!payload) return <p role="alert">Nothing to print.</p>;
  const d = payload.dashboard;
  return (
    <ThemeScope theme="light" className="tl print-export bg-surface-raised text-ink">
      {payload.tabs.map((tab, ti) => (
        <section key={ti} className="print-tab space-y-4" aria-label={tab.title}>
          <header className="grid gap-1 border-b border-line pb-2">
            {payload.branding && (
              <div className="flex items-center gap-2 text-small text-ink-muted" data-print-brand="">
                <img src={payload.branding.logo ?? defaultLogo} alt="" className="object-contain" style={{ height: payload.branding.logo_height, width: "auto", maxWidth: 240 }} />
                <span className="font-semibold text-ink">{payload.branding.app_name}</span>
              </div>
            )}
            <div className="flex items-baseline justify-between gap-4">
              <h1 className="text-title">{d.title}</h1>
              <span className="text-small text-ink-muted">{payload.tabs.length > 1 ? `${tab.title} · ${ti + 1} of ${payload.tabs.length}` : tab.title}</span>
            </div>
            <p className="text-small text-ink-muted">
              {d.version ? `Version ${d.version} · ` : ""}Scope: {payload.scope} · {(tab.reporting_date ?? payload.reporting_date)
                ? `Data as of ${tab.reporting_date ?? payload.reporting_date} · Refreshed ${payload.as_of}` : `Data as of ${payload.as_of}`} · {payload.filters_text}
            </p>
            {ti === 0 && payload.excluded_widgets > 0 && (
              <p className="text-small text-ink-muted">
                {payload.excluded_widgets} {payload.excluded_widgets === 1 ? "widget is" : "widgets are"} hidden for your role and not included.
              </p>
            )}
          </header>
          {(tab.callouts ?? []).map((c, n) => <TextBlock key={n} kind="callout" title={c.title} text={c.text} level={c.level} className="print-card" />)}
          {tab.sections.map((s, si) => (
            <div key={si} className="space-y-3">
              {s.title && (tab.sections.length > 1 || s.kind === "headline") && <h2 className="text-heading text-ink-muted">{s.title}</h2>}
              <Grid items={s.items} />
            </div>
          ))}
          {(tab.footnotes ?? []).map((f, n) => <TextBlock key={n} kind="footnote" text={f} className="print-card" />)}
        </section>
      ))}
    </ThemeScope>
  );
}

/**
 * Rows of cards in block layout (Chromium does not split CSS grid items across pages): items whose boxes overlap
 * vertically share a row, each as wide as its 12-column span. Long tables sit in rows of their own and may continue
 * on the next page with their header repeated; every other card stays whole.
 */
function Grid({ items }: { items: (PrintWidget | PrintNote)[] }) {
  const sorted = [...items].sort((a, b) => a.placement.y - b.placement.y || a.placement.x - b.placement.x);
  if (sorted.length === 0) return <p className="text-ink-muted">No widgets in this report.</p>;
  const rows: (PrintWidget | PrintNote)[][] = [];
  let bottom = -1;
  for (const i of sorted) {
    if (rows.length === 0 || i.placement.y >= bottom || isLong(i) || isLong(rows[rows.length - 1]![0]!)) {
      rows.push([i]);
      bottom = i.placement.y + i.placement.h;
    } else {
      rows[rows.length - 1]!.push(i);
      bottom = Math.max(bottom, i.placement.y + i.placement.h);
    }
  }
  return (
    <div className="space-y-3">
      {rows.map((row, r) => isLong(row[0]!) ? (
        <div key={r} className="print-card-long"><Widget w={row[0] as PrintWidget} /></div>
      ) : (
        <div key={r} className="print-card flex items-stretch gap-3">
          {row.map((i, n) => {
            const span = Math.min(12, Math.max(1, i.placement.w));
            const style = { width: `calc(${(span / 12) * 100}% - ${((12 - span) / 12) * 0.75}rem)` };
            return i.kind !== "widget"
              ? <div key={n} className="min-w-0" style={style}><TextBlock kind={i.kind} text={i.text} title={i.title} level={i.level} /></div>
              : <div key={n} className="min-w-0" style={style}><Widget w={i} /></div>;
          })}
        </div>
      ))}
    </div>
  );
}

function isLong(i: PrintWidget | PrintNote) {
  return i.kind === "widget" && !i.error && i.viz?.chart_spec?.type === "table" && (i.rows?.length ?? 0) > 12;
}

function Widget({ w }: { w: PrintWidget }) {
  const spec = w.viz?.chart_spec ?? null;
  const figure = w.kpi ?? null;
  const missed = w.filters_not_applied ?? [];
  return (
    <article className="h-full space-y-2 rounded-md border border-line p-3" aria-label={w.title}>
      <div className="flex items-start justify-between gap-2">
        <h3 className="text-body-strong">{w.title}</h3>
        <span className="flex gap-1">{w.badges.map((b) => <ContentBadge key={b} badge={b} text />)}</span>
      </div>
      {w.error ? (
        <p className="text-small text-status-danger">{w.error}</p>
      ) : figure || spec?.type === "figure" ? (
        <div className="grid gap-1">
          <span className="font-mono text-figure-xl tabular-nums">{figure ? formatValue(figure.value, figure.format) : firstFigure(w)}</span>
          {figure?.tone && <span className="text-small font-medium" data-tone={figure.tone.tone}>{figure.tone.label} · {figure.tone.rule}</span>}
          {figure?.definition && <span className="text-small text-ink-muted">{figure.definition}</span>}
          {figure?.change && (
            <span className="text-small text-ink-muted">
              {figure.change.change > 0 ? "+" : ""}{formatValue(figure.change.change, figure.format)}
              {figure.change.change_pct !== null ? ` (${figure.change.change > 0 ? "+" : ""}${figure.change.change_pct}%)` : ""} vs {figure.change.previous_range}
            </span>
          )}
        </div>
      ) : spec && spec.type !== "table" ? (
        <Chart spec={spec} columns={w.columns ?? []} rows={w.rows ?? []} title={w.title} view="chart" still />
      ) : (
        <PrintTable {...shownColumns(w)} total={w.row_count ?? 0} />
      )}
      {!w.error && missed.length > 0 && <p className="text-small text-ink-muted">Not affected by {missed.join(", ")}</p>}
    </article>
  );
}

function shownColumns(w: PrintWidget): { columns: Column[]; rows: unknown[][] } {
  const columns = w.columns ?? [];
  const rows = w.rows ?? [];
  if (!w.detail_columns) return { columns, rows };
  const keep = columns.map((c, i) => [c, i] as const).filter(([c]) => w.detail_columns!.includes(c.name));
  return { columns: keep.map(([c]) => c), rows: rows.map((r) => keep.map(([, i]) => r[i])) };
}

function firstFigure(w: PrintWidget) {
  const cols = w.columns ?? [];
  const mi = cols.findIndex((c) => c.kind === "measure");
  return mi < 0 ? "–" : formatValue(w.rows?.[0]?.[mi], cols[mi]?.format);
}

function PrintTable({ columns, rows, total }: { columns: Column[]; rows: unknown[][]; total: number }) {
  if (rows.length === 0) return <p className="text-small text-ink-muted">No records match the current scope and filters.</p>;
  const shown = rows.slice(0, PRINT_TABLE_ROWS);
  return (
    <div className="space-y-1">
      <table className="w-full border-collapse text-small">
        <thead>
          <tr>{columns.map((c) => <th key={c.name} scope="col" className={`border-b border-line px-1 py-0.5 text-left font-medium ${c.kind === "measure" ? "text-right" : ""}`}>{c.label}</th>)}</tr>
        </thead>
        <tbody>
          {shown.map((r, ri) => (
            <tr key={ri}>
              {columns.map((c, ci) => (
                <td key={c.name} className={`border-b border-line/60 px-1 py-0.5 ${c.kind === "measure" ? "text-right font-mono tabular-nums" : ""}`}>
                  {c.kind === "measure" ? formatValue(r[ci], c.format) : r[ci] === null ? "Not set" : String(r[ci])}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      {total > shown.length && <p className="text-small text-ink-muted">First {shown.length} of {total.toLocaleString()} rows — the Excel export has them all.</p>}
    </div>
  );
}
