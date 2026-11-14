import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus, Trash2 } from "lucide-react";
import { useEffect, useId, useMemo, useState } from "react";

import { api } from "../../../api/client";
import type { PageFilter, WidgetRun } from "../../../api/app";
import type { ReportItem } from "../../../api/authoring";
import type { Catalog } from "../../../api/govern";
import type { ChartColours } from "../../../api/querySpec";
import { cssColour, SCHEMES, SWATCHES, type Scheme, type SwatchName } from "../../../components/chartColours";
import { formatValue } from "../../../components/format";
import { Button } from "../../../components/ui/button";
import { Checkbox } from "../../../components/ui/checkbox";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "../../../components/ui/dialog";
import { NativeSelect } from "../../../components/ui/native-select";
import { RadioGroup, RadioGroupItem } from "../../../components/ui/radio-group";
import { Skeleton } from "../../../components/ui/skeleton";
import { cn } from "../../../lib/utils";
import { ValueList } from "../../viewer/FilterBar";

/** Readers can filter a widget by up to three of its fields (ENH-023, US-221; the API's limit). */
export const MAX_CONTROLS = 3;
/** Visuals without series colours (the API's NO_CHART). */
export const NO_CHART = new Set(["figure", "table", "meter", "heat_grid"]);
const MAX_OVERRIDES = 24;

type Card = NonNullable<NonNullable<NonNullable<ReportItem["spec"]>["visual"]>["card"]>;

export const CARDS: { key: Card; label: string; figureOnly?: boolean }[] = [
  { key: "default", label: "Default" },
  { key: "accent", label: "Brand tint" },
  { key: "neutral", label: "Neutral" },
  { key: "good", label: "Good" },
  { key: "warning", label: "Warning" },
  { key: "critical", label: "Critical" },
  { key: "brand", label: "Brand filled", figureOnly: true },
  { key: "dark", label: "Dark", figureOnly: true },
];

interface Row { key: number; ref: string; values: string[] }

function useCatalog(connectionId: string | undefined, enabled: boolean) {
  return useQuery({
    queryKey: ["catalog", connectionId],
    queryFn: () => api<Catalog>(`/api/v1/query/catalog?connection_id=${connectionId}`),
    enabled: enabled && !!connectionId,
    retry: false,
  });
}

/**
 * A widget's own filters (US-221), for its Creator: fixed filters that always apply to it (narrowing what it counts,
 * shown to readers with a lock), and up to three fields readers can filter it by from inside the widget. Both are saved
 * with the widget on this report and undone like any canvas change.
 */
export function WidgetFiltersDialog({ open, onOpenChange, item, onSave }: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  item: ReportItem;
  onSave: (body: { filters: PageFilter[]; controls: string[] | null }) => void;
}) {
  const id = useId();
  const catalog = useCatalog(item.connection_id, open);
  const dims = useMemo(() => (catalog.data?.dimensions ?? []).filter((d) => !d.temporal), [catalog.data]);
  const [rows, setRows] = useState<Row[]>([]);
  const [controls, setControls] = useState<string[]>([]);
  const [q, setQ] = useState("");
  useEffect(() => {
    if (!open) return;
    const own = (item.own_filters ?? []) as PageFilter[];
    setRows(own.map((f, i) => ({ key: i, ref: f.ref, values: f.values.map(String) })));
    setControls(item.spec?.visual?.controls ?? []);
    setQ("");
  }, [open, item]);
  const label = (ref: string) => dims.find((d) => d.ref === ref)?.label ?? ref.split(":")[1] ?? ref;
  const incomplete = rows.some((r) => !r.ref || r.values.length === 0);
  const shown = dims.filter((d) => d.label.toLowerCase().includes(q.trim().toLowerCase()));
  const save = () => {
    onSave({
      filters: rows.map((r) => ({ ref: r.ref, op: r.values.length === 1 ? "=" as const : "in" as const, values: r.values })),
      controls: controls.length ? controls : null,
    });
    onOpenChange(false);
  };
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[calc(100dvh-32px)] overflow-y-auto sm:max-w-2xl">
        <form className="grid gap-5" onSubmit={(e) => { e.preventDefault(); if (!incomplete) save(); }}>
          <DialogHeader>
            <DialogTitle>Widget filters</DialogTitle>
            <DialogDescription>Filters that belong to this widget only. Page filters still apply on top, unless the widget ignores them.</DialogDescription>
          </DialogHeader>
          {catalog.isLoading ? <Skeleton className="h-32" aria-label="Loading fields" /> : catalog.error ? (
            <p role="alert" className="text-small text-status-danger">The fields of this model could not be loaded.</p>
          ) : (
            <>
              <section aria-labelledby={`${id}-fixed`} className="grid gap-3">
                <div className="grid gap-0.5">
                  <h3 id={`${id}-fixed`} className="text-heading">Always filter to</h3>
                  <p className="text-small text-ink-muted">Readers see these with a lock and can't change them.</p>
                </div>
                {rows.map((r) => (
                  <div key={r.key} className="grid gap-2 rounded-md border border-line p-3 sm:grid-cols-[14rem_minmax(0,1fr)_auto]">
                    <div className="grid gap-1 self-start">
                      <label htmlFor={`${id}-f${r.key}`} className="text-small font-medium">Field</label>
                      <NativeSelect id={`${id}-f${r.key}`} value={r.ref}
                        onChange={(e) => setRows(rows.map((x) => (x.key === r.key ? { ...x, ref: e.target.value, values: [] } : x)))}>
                        <option value="">Choose a field</option>
                        {dims.map((d) => <option key={d.ref} value={d.ref}>{d.label}</option>)}
                      </NativeSelect>
                    </div>
                    <div className="grid min-w-0 gap-1">
                      <span className="text-small font-medium">Values{r.values.length ? ` (${r.values.length})` : ""}</span>
                      {r.ref ? (
                        <ValueList filter={{ ref: r.ref, label: label(r.ref), connectionId: item.connection_id, modelVersionId: item.model_version_id }}
                          selected={r.values} onChange={(v) => setRows(rows.map((x) => (x.key === r.key ? { ...x, values: v } : x)))} />
                      ) : <span className="text-small text-ink-muted">Choose a field first.</span>}
                    </div>
                    <Button type="button" variant="ghost" size="icon" className="self-start" aria-label={`Remove the ${r.ref ? label(r.ref) : "new"} filter`}
                      onClick={() => setRows(rows.filter((x) => x.key !== r.key))}><Trash2 aria-hidden /></Button>
                  </div>
                ))}
                <Button type="button" variant="secondary" size="sm" className="justify-self-start" disabled={rows.length >= 10}
                  onClick={() => setRows([...rows, { key: Math.max(-1, ...rows.map((x) => x.key)) + 1, ref: "", values: [] }])}>
                  <Plus aria-hidden />Add a fixed filter
                </Button>
              </section>
              <section aria-labelledby={`${id}-ctl`} className="grid gap-3">
                <div className="grid gap-0.5">
                  <h3 id={`${id}-ctl`} className="text-heading">Readers can filter by</h3>
                  <p className="text-small text-ink-muted">Up to {MAX_CONTROLS} fields, shown as filters inside the widget. A reader's choice changes this widget only.</p>
                </div>
                {dims.length > 8 && (
                  <input type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search fields" aria-label="Search fields"
                    className="h-control-sm rounded-md border border-line-strong bg-surface-raised px-2 text-body focus-visible:focus-ring" />
                )}
                <ul className="grid max-h-56 gap-1 overflow-y-auto sm:grid-cols-2" aria-label="Fields readers can filter by">
                  {shown.map((d) => {
                    const on = controls.includes(d.ref);
                    return (
                      <li key={d.ref} className="flex items-center gap-2 rounded-sm px-1 py-0.5 hover:bg-surface-sunken">
                        <Checkbox id={`${id}-c-${d.ref}`} checked={on} disabled={!on && controls.length >= MAX_CONTROLS}
                          onCheckedChange={(v) => setControls(v === true ? [...controls, d.ref] : controls.filter((x) => x !== d.ref))} />
                        <label htmlFor={`${id}-c-${d.ref}`} className="flex-1 cursor-pointer text-body">{d.label}</label>
                      </li>
                    );
                  })}
                </ul>
                {controls.length >= MAX_CONTROLS && <p className="text-small text-ink-muted">{MAX_CONTROLS} fields chosen: clear one to pick another.</p>}
              </section>
            </>
          )}
          <DialogFooter>
            <Button type="button" variant="secondary" onClick={() => onOpenChange(false)}>Cancel</Button>
            <Button type="submit" disabled={incomplete || catalog.isLoading}>Save</Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

/** The series and bar labels of a widget's last run on the canvas (what the colour overrides name). */
export function chartLabels(run: WidgetRun | undefined): { series: string[]; categories: string[] } {
  const spec = run?.viz?.chart_spec;
  if (!run || !spec) return { series: [], categories: [] };
  const series = (spec.series ?? []).map((s) => run.columns.find((c) => c.name === s)?.label ?? s);
  const xi = run.columns.findIndex((c) => c.name === spec.x);
  const categories = xi < 0 ? [] : [...new Set(run.rows.map((r) => (r[xi] == null ? "Not set" : formatValue(r[xi]))))];
  return { series: spec.type === "donut" ? series.slice(0, 1) : series, categories };
}

/**
 * A widget's look (US-223, US-224): its card colour and its chart colours — a scheme, and a colour for any one series
 * or bar. Every choice is a palette swatch, so light, dark and the tenant's palette carry over.
 */
export function WidgetStyleDialog({ open, onOpenChange, item, onSave }: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  item: ReportItem;
  onSave: (body: { card: Card; colours?: ChartColours | null }) => void;
}) {
  const id = useId();
  const visual = item.visual ?? "table";
  const charted = !NO_CHART.has(visual);
  const qc = useQueryClient();
  const run = open ? qc.getQueriesData<WidgetRun>({ queryKey: ["widget", item.widget_id] }).map(([, d]) => d).find(Boolean) : undefined;
  const labels = chartLabels(run);
  // A chart of several series names its series; one series names its bars (or a donut its slices).
  const named = (labels.series.length > 1 ? labels.series : labels.categories).slice(0, MAX_OVERRIDES);
  const [card, setCard] = useState<Card>("default");
  const [scheme, setScheme] = useState<Scheme>("brand");
  const [overrides, setOverrides] = useState<Record<string, SwatchName>>({});
  useEffect(() => {
    if (!open) return;
    setCard(item.spec?.visual?.card ?? "default");
    setScheme(item.spec?.visual?.colours?.scheme ?? "brand");
    setOverrides({ ...(item.spec?.visual?.colours?.overrides ?? {}) });
  }, [open, item]);
  const cards = CARDS.filter((c) => !c.figureOnly || visual === "figure");
  const tokens = (SCHEMES.find((s) => s.key === scheme) ?? SCHEMES[0]!).tokens;
  // The colour a label gets from the scheme alone: each series (or slice) its own, every bar of one series the first.
  const schemeColour = (i: number) => tokens[(labels.series.length > 1 || visual === "donut" ? i : 0) % tokens.length]!;
  const save = () => {
    const kept = Object.fromEntries(Object.entries(overrides).filter(([k]) => named.includes(k) || !labels.categories.length));
    const plain = scheme === "brand" && Object.keys(kept).length === 0;
    onSave({ card, ...(charted ? { colours: plain ? null : { scheme, overrides: kept } } : {}) });
    onOpenChange(false);
  };
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[calc(100dvh-32px)] overflow-y-auto sm:max-w-2xl">
        <form className="grid gap-5" onSubmit={(e) => { e.preventDefault(); save(); }}>
          <DialogHeader>
            <DialogTitle>Widget style</DialogTitle>
            <DialogDescription>Colours come from your brand palette, so they follow light and dark mode and stay readable.</DialogDescription>
          </DialogHeader>
          <section aria-labelledby={`${id}-card`} className="grid gap-2">
            <h3 id={`${id}-card`} className="text-heading">Card colour</h3>
            <RadioGroup value={card} onValueChange={(v) => setCard(v as Card)} aria-labelledby={`${id}-card`} className="grid grid-cols-2 gap-2 sm:grid-cols-4">
              {cards.map((c) => (
                <label key={c.key} htmlFor={`${id}-card-${c.key}`}
                  className="grid cursor-pointer gap-2 rounded-md border border-line p-2 has-[[data-state=checked]]:border-accent has-[[data-state=checked]]:ring-1 has-[[data-state=checked]]:ring-accent">
                  <span data-slot="card" data-card={c.key === "default" ? undefined : c.key} data-theme={c.key === "dark" ? "dark" : undefined} aria-hidden
                    className="flex h-10 items-end rounded-md border border-line bg-surface-raised px-2 pb-1 font-mono text-small text-ink">128</span>
                  <span className="flex items-center gap-2 text-small">
                    <RadioGroupItem id={`${id}-card-${c.key}`} value={c.key} />{c.label}
                  </span>
                </label>
              ))}
            </RadioGroup>
            {visual !== "figure" && <p className="text-small text-ink-muted">Brand filled and Dark are for single figures, so a chart's axes stay readable.</p>}
          </section>
          {charted && (
            <section aria-labelledby={`${id}-chart`} className="grid gap-2">
              <h3 id={`${id}-chart`} className="text-heading">Chart colours</h3>
              <RadioGroup value={scheme} onValueChange={(v) => setScheme(v as Scheme)} aria-labelledby={`${id}-chart`} className="grid grid-cols-2 gap-2 sm:grid-cols-3">
                {SCHEMES.map((s) => (
                  <label key={s.key} htmlFor={`${id}-s-${s.key}`}
                    className="grid cursor-pointer gap-2 rounded-md border border-line p-2 has-[[data-state=checked]]:border-accent has-[[data-state=checked]]:ring-1 has-[[data-state=checked]]:ring-accent">
                    <span className="flex h-4 gap-0.5 overflow-hidden rounded-sm" aria-hidden>
                      {s.tokens.slice(0, 6).map((t) => <span key={t} className="flex-1" style={{ background: cssColour(t) }} />)}
                    </span>
                    <span className="flex items-center gap-2 text-small"><RadioGroupItem id={`${id}-s-${s.key}`} value={s.key} />{s.label}</span>
                  </label>
                ))}
              </RadioGroup>
              <div className="grid gap-1 pt-2">
                <h4 className="text-small font-medium">{labels.series.length > 1 ? "Colour of a series" : visual === "donut" ? "Colour of a slice" : "Colour of a bar"}</h4>
                {named.length === 0 ? (
                  <p className="text-small text-ink-muted">Run the widget first: its {labels.series.length > 1 ? "series" : "bars"} are listed here.</p>
                ) : (
                  <ul className="grid max-h-64 gap-1 overflow-y-auto sm:grid-cols-2">
                    {named.map((n, i) => {
                      const pick = overrides[n];
                      return (
                        <li key={n} className="flex items-center gap-2">
                          <span aria-hidden className={cn("size-3 shrink-0 rounded-sm", !pick && "opacity-60")}
                            style={{ background: cssColour(pick ?? schemeColour(i)) }} />
                          <label htmlFor={`${id}-o-${i}`} className="min-w-0 flex-1 truncate text-small" title={n}>{n}</label>
                          <NativeSelect id={`${id}-o-${i}`} className="h-control-sm w-36" value={pick ?? ""}
                            onChange={(e) => {
                              const next = { ...overrides };
                              if (e.target.value) next[n] = e.target.value as SwatchName;
                              else delete next[n];
                              setOverrides(next);
                            }}>
                            <option value="">Scheme colour</option>
                            {SWATCHES.map((w) => <option key={w.key} value={w.key}>{w.label}</option>)}
                          </NativeSelect>
                        </li>
                      );
                    })}
                  </ul>
                )}
              </div>
            </section>
          )}
          <DialogFooter>
            <Button type="button" variant="secondary" onClick={() => onOpenChange(false)}>Cancel</Button>
            <Button type="submit">Save</Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
