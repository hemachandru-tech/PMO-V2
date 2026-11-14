import { useQuery } from "@tanstack/react-query";
import { ArrowRightLeft, ChartColumn, Copy, EllipsisVertical, Filter, FilterX, Gauge, Paintbrush, Palette, Pencil, SlidersHorizontal, Trash2 } from "lucide-react";
import { useEffect, useId, useRef, useState, type KeyboardEvent } from "react";

import { api } from "../../../api/client";
import type { CalloutLevel, PageFilter, TextKind } from "../../../api/app";
import { VISUAL_NAMES, type Report, type ReportItem, type ReportSection, type VisualOptions } from "../../../api/authoring";
import type { FigureTone } from "../../../api/querySpec";
import type { DrillTarget } from "../../../components/DrillSheet";
import { SuggestionsButton } from "../../../components/Suggestions";
import { TextBlock } from "../../../components/TextBlock";
import { Button } from "../../../components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "../../../components/ui/dialog";
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuLabel, DropdownMenuSeparator, DropdownMenuTrigger,
} from "../../../components/ui/dropdown-menu";
import { Input } from "../../../components/ui/input";
import { NativeSelect } from "../../../components/ui/native-select";
import { Textarea } from "../../../components/ui/textarea";
import { cn } from "../../../lib/utils";
import { ViewerWidget } from "../../viewer/ViewerWidget";
import type { Nudge } from "./layout";
import { WidgetFiltersDialog, WidgetStyleDialog } from "./WidgetSettings";

export type Call = (path: string, opts: { method: "POST" | "PATCH" | "DELETE" | "PUT"; body?: unknown }, done?: string) => Promise<Report | null>;

const TEXT_LABELS: Record<string, string> = { text_note: "Text note", callout: "Callout", footnote: "Footnote" };
const KEYS: Record<string, Nudge> = { ArrowLeft: "left", ArrowRight: "right", ArrowUp: "up", ArrowDown: "down" };

/**
 * One item on the canvas (FEAT-025/026; US-141). Click or Tab selects it; a floating toolbar offers chart type, top N,
 * page filters and more. Arrow keys move it and Shift + arrow keys resize it, announced in words; the mouse drags and
 * resizes it on the grid. The live widget runs in the Creator's own scope.
 */
export function CanvasItem({ item, report, section, edit, selected, focus, highlighted, filters, filterLabels, call, onSelect, onNudge, onDrill }: {
  item: ReportItem;
  report: Report;
  section: ReportSection;
  edit: boolean;
  selected: boolean;
  /** Move keyboard focus here (after adding the item). */
  focus: boolean;
  highlighted: boolean;
  filters: PageFilter[];
  filterLabels: Record<string, string>;
  call: Call;
  onSelect: (id: string | null) => void;
  onNudge: (item: ReportItem, dir: Nudge, resize: boolean) => void;
  onDrill: (t: DrillTarget) => void;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const hint = useId();
  const label = item.kind === "widget" ? item.title ?? "Widget" : TEXT_LABELS[item.kind] ?? "Text";
  useEffect(() => { if (focus) ref.current?.focus(); }, [focus]);

  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    if (!edit || e.target !== e.currentTarget) return;
    const dir = KEYS[e.key];
    if (dir) {
      e.preventDefault();
      onNudge(item, dir, e.shiftKey);
    } else if (e.key === "Escape") {
      onSelect(null);
    }
  };

  const body = item.kind !== "widget" ? (
    <TextBlock kind={item.kind as TextKind} text={item.text ?? ""} title={item.title} level={item.level} className="h-full overflow-auto" />
  ) : (
    <ViewerWidget id={item.widget_id!} title={item.title ?? ""} visual={item.visual ?? "table"} measure={item.measure}
      connectionId={item.connection_id} modelVersionId={item.model_version_id} versionId={null}
      filters={item.follow_page_filters === false ? [] : filters} filterLabels={item.follow_page_filters === false ? {} : filterLabels}
      badges={item.badges} onDrill={onDrill} className="h-full overflow-auto" />
  );
  if (!edit) return <div className="h-full">{body}</div>;

  return (
    <div ref={ref} tabIndex={0} role="group" aria-roledescription="canvas item" aria-label={label} aria-describedby={hint}
      onFocus={(e) => { if (e.target === e.currentTarget) onSelect(item.id); }} onMouseDown={() => onSelect(item.id)} onKeyDown={onKeyDown}
      className={cn("relative h-full cursor-grab rounded-lg outline-none active:cursor-grabbing",
        selected && "ring-2 ring-accent ring-offset-2 ring-offset-surface",
        highlighted && "ring-2 ring-status-warn ring-offset-2 ring-offset-surface")}>
      <span id={hint} className="sr-only">
        {selected ? `Selected. Arrow keys move, Shift + arrow keys resize: ${item.w} of 12 columns.` : "Press an arrow key to move, Shift + arrow keys to resize."}
      </span>
      {body}
      {selected && <Toolbar item={item} report={report} section={section} call={call} label={label} onRemoved={() => onSelect(null)} />}
    </div>
  );
}

function Toolbar({ item, report, section, call, label, onRemoved }: {
  item: ReportItem; report: Report; section: ReportSection; call: Call; label: string; onRemoved: () => void;
}) {
  const base = `/api/v1/reports/${report.id}/items/${item.id}`;
  const [renaming, setRenaming] = useState(false);
  const [toning, setToning] = useState(false);
  const [noting, setNoting] = useState(false);
  const [filtering, setFiltering] = useState(false);
  const [styling, setStyling] = useState(false);
  const others = report.sections.filter((s) => s.id !== section.id);
  const widget = item.kind === "widget";
  return (
    <div role="toolbar" aria-label={`${label} tools`} onMouseDown={(e) => e.stopPropagation()}
      className={cn("absolute z-20 flex cursor-default items-center gap-0.5 rounded-md border border-line bg-surface-raised p-1 shadow-overlay",
        // Above the widget, except on a section's first row, where that would cover the section header.
        item.y === 0 ? "top-full mt-2" : "bottom-full mb-2",
        // Kept on the canvas: anchored to the widget's right edge on the right half of the grid.
        item.x + item.w > 8 ? "right-0" : "left-0")}>
      {widget && <ChartTypeMenu item={item} base={base} call={call} />}
      {widget && <TopN item={item} base={base} call={call} />}
      {widget && (item.visual ?? "table") === "table" && (
        // ENH-020: status words as RAG badges and figures tinted by size; saved with the widget.
        <Button variant="ghost" size="sm" className="h-control-sm px-2" aria-pressed={!!item.spec?.visual?.colour}
          title={item.spec?.visual?.colour ? "Shown in colour" : "Shown without colour"}
          onClick={() => void call(base, { method: "PATCH", body: { colour: !item.spec?.visual?.colour } },
            item.spec?.visual?.colour ? `${label} shown without colour` : `${label} shown in colour`)}>
          <Palette aria-hidden />Colours
        </Button>
      )}
      {widget && (
        // ENH-023 US-221: the widget's own filters (fixed, and for readers).
        <Button variant="ghost" size="sm" className="h-control-sm px-2" onClick={() => setFiltering(true)}
          aria-label={`Widget filters${ownCount(item) ? `: ${ownCount(item)} set` : ""}`}>
          <SlidersHorizontal aria-hidden />Filters{ownCount(item) > 0 && <span className="rounded-full bg-accent-soft px-1.5 text-small text-accent-strong">{ownCount(item)}</span>}
        </Button>
      )}
      {widget && (
        // US-223 / US-224: card colour and chart colours.
        <Button variant="ghost" size="sm" className="h-control-sm px-2" onClick={() => setStyling(true)}><Paintbrush aria-hidden />Style</Button>
      )}
      {widget && (
        <SuggestionsButton url={`${base}/suggestions`} label={label}
          onApply={(s) => call(`${base}/suggestions/apply`, { method: "POST", body: { ops: s.ops, title: s.title } }, `Applied: ${s.title}`)} />
      )}
      {widget && (
        <Button variant="ghost" size="sm" className="h-control-sm px-2" aria-pressed={item.follow_page_filters !== false}
          title={item.follow_page_filters !== false ? "Follows the page filters" : "Ignores the page filters"}
          onClick={() => void call(base, { method: "PATCH", body: { follow_page_filters: item.follow_page_filters === false } },
            item.follow_page_filters === false ? `${label} follows the page filters` : `${label} ignores the page filters`)}>
          {item.follow_page_filters !== false ? <Filter aria-hidden /> : <FilterX aria-hidden />}Page filters
        </Button>
      )}
      {!widget && (
        <Button variant="ghost" size="sm" className="h-control-sm px-2" onClick={() => setRenaming(true)}><Pencil aria-hidden />Edit</Button>
      )}
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button variant="ghost" size="icon" className="size-control-sm" aria-label={`More actions for ${label}`}><EllipsisVertical aria-hidden /></Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="start">
          {widget && <DropdownMenuItem onSelect={() => setRenaming(true)}><Pencil aria-hidden />Rename</DropdownMenuItem>}
          {item.visual === "figure" && <DropdownMenuItem onSelect={() => setToning(true)}><Gauge aria-hidden />Tone threshold…</DropdownMenuItem>}
          {item.visual === "meter" && <DropdownMenuItem onSelect={() => setNoting(true)}><Pencil aria-hidden />Meter note…</DropdownMenuItem>}
          <DropdownMenuItem onSelect={() => void call(`${base}/duplicate`, { method: "POST" }, `Duplicated ${label}`)}><Copy aria-hidden />Duplicate</DropdownMenuItem>
          {others.length > 0 && (
            <>
              <DropdownMenuSeparator />
              <DropdownMenuLabel>Move to section</DropdownMenuLabel>
              {others.map((s) => (
                <DropdownMenuItem key={s.id} onSelect={() => void call(base, { method: "PATCH", body: { section_id: s.id } }, `Moved ${label} to ${s.title}`)}>
                  <ArrowRightLeft aria-hidden />{s.title}
                </DropdownMenuItem>
              ))}
            </>
          )}
          <DropdownMenuSeparator />
          <DropdownMenuItem className="text-status-danger" onSelect={() => { onRemoved(); void call(base, { method: "DELETE" }, `Removed ${label}`); }}>
            <Trash2 aria-hidden />Remove from report
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
      {item.kind === "callout" ? (
        <CalloutDialog open={renaming} onOpenChange={setRenaming} item={item}
          onSave={(body) => void call(base, { method: "PATCH", body }, "Callout saved")} />
      ) : (
        <TextDialog open={renaming} onOpenChange={setRenaming} note={!widget} footnote={item.kind === "footnote"}
          initial={widget ? item.title ?? "" : item.text ?? ""}
          onSave={(v) => void call(base, { method: "PATCH", body: widget ? { title: v } : { text: v } }, widget ? `Renamed to ${v}` : `${label} saved`)} />
      )}
      {item.visual === "figure" && (
        <ToneDialog open={toning} onOpenChange={setToning} tone={item.spec?.visual?.tone ?? null}
          onSave={(tone) => void call(base, { method: "PATCH", body: { tone } }, tone ? `Tone threshold set on ${label}` : `Tone threshold removed from ${label}`)} />
      )}
      {widget && (
        <WidgetFiltersDialog open={filtering} onOpenChange={setFiltering} item={item}
          onSave={(body) => void call(base, { method: "PATCH", body }, `Filters saved on ${label}`)} />
      )}
      {widget && (
        <WidgetStyleDialog open={styling} onOpenChange={setStyling} item={item}
          onSave={(body) => void call(base, { method: "PATCH", body }, `Style saved on ${label}`)} />
      )}
      {item.visual === "meter" && (
        <TextDialog open={noting} onOpenChange={setNoting} note={false} meter initial={item.spec?.visual?.note ?? ""}
          onSave={(v) => void call(base, { method: "PATCH", body: { note: v } }, "Meter note saved")} />
      )}
    </div>
  );
}

/** How many own filters a widget has: fixed ones and the fields offered to readers (US-221). */
function ownCount(item: ReportItem): number {
  return (item.own_filters?.length ?? 0) + (item.spec?.visual?.controls?.length ?? 0);
}

/**
 * A figure's tone (US-157): warning and critical thresholds and which way is worse. The figure then shows Good,
 * Warning or Critical as a word and icon with the rule, never by colour alone.
 */
function ToneDialog({ open, onOpenChange, tone, onSave }: {
  open: boolean; onOpenChange: (o: boolean) => void; tone: FigureTone | null; onSave: (t: FigureTone | null) => void;
}) {
  const id = useId();
  const [warning, setWarning] = useState("");
  const [critical, setCritical] = useState("");
  const [worse, setWorse] = useState("higher");
  useEffect(() => {
    if (!open) return;
    setWarning(tone?.warning != null ? String(tone.warning) : "");
    setCritical(tone?.critical != null ? String(tone.critical) : "");
    setWorse(tone?.higher_is_worse === false ? "lower" : "higher");
  }, [open, tone]);
  const num = (v: string) => (v.trim() === "" ? null : Number(v));
  const w = num(warning);
  const c = num(critical);
  const higher = worse === "higher";
  const invalid = (w !== null && Number.isNaN(w)) || (c !== null && Number.isNaN(c));
  const order = w !== null && c !== null && (higher ? c < w : c > w);
  const error = invalid ? "Thresholds are numbers." : order ? `Critical must be ${higher ? "at or above" : "at or below"} the warning threshold.` : null;
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <form className="grid gap-4" onSubmit={(e) => {
          e.preventDefault();
          if (error || (w === null && c === null)) return;
          onSave({ warning: w, critical: c, higher_is_worse: higher });
          onOpenChange(false);
        }}>
          <DialogHeader>
            <DialogTitle>Tone threshold</DialogTitle>
            <DialogDescription>The figure shows Good, Warning or Critical with the rule in words. Leave a box empty to skip that level.</DialogDescription>
          </DialogHeader>
          <div className="grid gap-3 sm:grid-cols-3">
            <div className="grid gap-1">
              <label htmlFor={`${id}-dir`} className="text-small font-medium">Higher values are</label>
              <NativeSelect id={`${id}-dir`} value={worse} onChange={(e) => setWorse(e.target.value)}>
                <option value="higher">Worse</option>
                <option value="lower">Better</option>
              </NativeSelect>
            </div>
            <div className="grid gap-1">
              <label htmlFor={`${id}-w`} className="text-small font-medium">Warning {higher ? "at or above" : "at or below"}</label>
              <Input id={`${id}-w`} inputMode="decimal" value={warning} onChange={(e) => setWarning(e.target.value)} aria-invalid={!!error} />
            </div>
            <div className="grid gap-1">
              <label htmlFor={`${id}-c`} className="text-small font-medium">Critical {higher ? "at or above" : "at or below"}</label>
              <Input id={`${id}-c`} inputMode="decimal" value={critical} onChange={(e) => setCritical(e.target.value)} aria-invalid={!!error} />
            </div>
          </div>
          {error && <p role="alert" className="text-small text-status-danger">{error}</p>}
          <DialogFooter>
            {tone && <Button type="button" variant="secondary" className="sm:mr-auto" onClick={() => { onSave(null); onOpenChange(false); }}>Remove threshold</Button>}
            <Button type="button" variant="secondary" onClick={() => onOpenChange(false)}>Cancel</Button>
            <Button type="submit" disabled={!!error || (w === null && c === null)}>Save</Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function ChartTypeMenu({ item, base, call }: { item: ReportItem; base: string; call: Call }) {
  const [open, setOpen] = useState(false);
  const options = useQuery({
    queryKey: ["suitable", item.spec],
    queryFn: () => api<VisualOptions>("/api/v1/visuals/suitable", { method: "POST", body: { spec: item.spec } }),
    enabled: open && !!item.spec,
  });
  const current = item.visual ?? "table";
  return (
    <DropdownMenu open={open} onOpenChange={setOpen}>
      <DropdownMenuTrigger asChild>
        <Button variant="ghost" size="sm" className="h-control-sm px-2" aria-label={`Chart type: ${VISUAL_NAMES[current] ?? current}`}>
          <ChartColumn aria-hidden />{VISUAL_NAMES[current] ?? current}
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" className="w-72">
        <DropdownMenuLabel>Chart type</DropdownMenuLabel>
        {options.isLoading && <DropdownMenuItem disabled>Checking which charts fit…</DropdownMenuItem>}
        {(options.data?.suitable ?? []).map((v) => {
          // A single figure has nothing to tabulate: Table breaks it down first, so it shows real rows.
          const fix = v === "table" && options.data?.recommended === "figure" ? options.data.fixes?.[v] : undefined;
          return (
            <DropdownMenuItem key={v} disabled={v === current && !fix}
              onSelect={() => void call(base, { method: "PATCH", body: { visual: v, ...(fix ? { ops: fix.ops } : {}) } },
                `Shown as ${(VISUAL_NAMES[v] ?? v).toLowerCase()}${fix ? ` ${fix.label}` : ""}`)}>
              {VISUAL_NAMES[v] ?? v}
              {fix && <span className="ml-auto text-small text-ink-muted">{fix.label}</span>}
              {!fix && v === current && <span className="ml-auto text-small text-ink-muted">Current</span>}
            </DropdownMenuItem>
          );
        })}
        {(options.data?.disabled ?? []).map((d) => {
          const fix = options.data?.fixes?.[d.visual];
          return fix ? (
            <DropdownMenuItem key={d.visual} className="grid items-start gap-0"
              onSelect={() => void call(base, { method: "PATCH", body: { visual: d.visual, ops: fix.ops } },
                `Shown as ${(VISUAL_NAMES[d.visual] ?? d.visual).toLowerCase()} ${fix.label}`)}>
              <span>{VISUAL_NAMES[d.visual] ?? d.label}<span className="ml-2 text-small text-ink-muted">{fix.label}</span></span>
              <span className="text-small text-ink-muted">{d.reason}. Adds this to the widget.</span>
            </DropdownMenuItem>
          ) : (
            <DropdownMenuItem key={d.visual} disabled className="grid items-start gap-0">
              <span>{VISUAL_NAMES[d.visual] ?? d.label}</span>
              <span className="text-small text-ink-muted">{d.reason}</span>
            </DropdownMenuItem>
          );
        })}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

function TopN({ item, base, call }: { item: ReportItem; base: string; call: Call }) {
  const id = useId();
  const limit = item.spec?.limit;
  const [value, setValue] = useState(limit ? String(limit) : "");
  useEffect(() => setValue(limit ? String(limit) : ""), [limit]);
  const save = () => {
    const n = Number(value);
    if (Number.isInteger(n) && n > 0 && n <= 1000 && n !== limit) void call(base, { method: "PATCH", body: { limit: n } }, `Limited ${item.title} to the top ${n}`);
    else setValue(limit ? String(limit) : "");
  };
  return (
    <span className="flex items-center gap-1 px-1 text-small">
      <label htmlFor={id}>Top</label>
      <Input id={id} type="number" inputMode="numeric" min={1} max={1000} value={value} placeholder="All"
        onChange={(e) => setValue(e.target.value)} onBlur={save} onKeyDown={(e) => { if (e.key === "Enter") save(); }}
        className="h-control-sm w-16 px-2" />
    </span>
  );
}

/** A callout (US-158): title, Note or Warning, and the text; readers see the level as icon and word. */
function CalloutDialog({ open, onOpenChange, item, onSave }: {
  open: boolean; onOpenChange: (o: boolean) => void; item: ReportItem; onSave: (b: { title: string; text: string; level: CalloutLevel }) => void;
}) {
  const id = useId();
  const [title, setTitle] = useState("");
  const [text, setText] = useState("");
  const [level, setLevel] = useState<CalloutLevel>("info");
  useEffect(() => {
    if (!open) return;
    setTitle(item.title ?? "");
    setText(item.text ?? "");
    setLevel(item.level ?? "info");
  }, [open, item]);
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <form className="grid gap-4" onSubmit={(e) => { e.preventDefault(); if (text.trim()) { onSave({ title: title.trim(), text: text.trim(), level }); onOpenChange(false); } }}>
          <DialogHeader>
            <DialogTitle>Edit callout</DialogTitle>
            <DialogDescription>A short note above the figures: what the report counts, or a caveat readers must see.</DialogDescription>
          </DialogHeader>
          <div className="grid gap-3 sm:grid-cols-[minmax(0,1fr)_10rem]">
            <div className="grid gap-1">
              <label htmlFor={`${id}-t`} className="text-small font-medium">Title</label>
              <Input id={`${id}-t`} maxLength={200} value={title} onChange={(e) => setTitle(e.target.value)} />
            </div>
            <div className="grid gap-1">
              <label htmlFor={`${id}-l`} className="text-small font-medium">Kind</label>
              <NativeSelect id={`${id}-l`} value={level} onChange={(e) => setLevel(e.target.value as CalloutLevel)}>
                <option value="info">Note</option>
                <option value="warning">Warning</option>
              </NativeSelect>
            </div>
          </div>
          <div className="grid gap-1">
            <label htmlFor={`${id}-x`} className="text-small font-medium">Text</label>
            <Textarea id={`${id}-x`} rows={4} maxLength={4000} value={text} onChange={(e) => setText(e.target.value)} />
            <span className="text-small text-ink-muted">Paragraphs, “- ” bullets and **bold**.</span>
          </div>
          <DialogFooter>
            <Button type="button" variant="secondary" onClick={() => onOpenChange(false)}>Cancel</Button>
            <Button type="submit" disabled={!text.trim()}>Save</Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function TextDialog({ open, onOpenChange, note, footnote, meter, initial, onSave }: {
  open: boolean; onOpenChange: (o: boolean) => void; note: boolean; footnote?: boolean; meter?: boolean; initial: string; onSave: (v: string) => void;
}) {
  const id = useId();
  const [value, setValue] = useState(initial);
  useEffect(() => { if (open) setValue(initial); }, [open, initial]);
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <form className="grid gap-4" onSubmit={(e) => { e.preventDefault(); if (value.trim()) { onSave(value.trim()); onOpenChange(false); } }}>
          <DialogHeader>
            <DialogTitle>{footnote ? "Edit footnote" : note ? "Edit text note" : meter ? "Meter note" : "Rename widget"}</DialogTitle>
            <DialogDescription>{footnote ? "The population and data limits of this report, shown at the end. Paragraphs, “- ” bullets and **bold**." : note ? "Paragraphs, “- ” bullets and **bold**." : meter ? "What the part counts, e.g. “Open projects with a budget code”." : "The new title shows on this report. Other reports that use this widget keep theirs."}</DialogDescription>
          </DialogHeader>
          <label htmlFor={id} className="sr-only">{footnote ? "Footnote text" : note ? "Note text" : meter ? "Meter note" : "Widget title"}</label>
          {note ? <Textarea id={id} rows={6} maxLength={4000} value={value} onChange={(e) => setValue(e.target.value)} />
            : <Input id={id} maxLength={200} value={value} onChange={(e) => setValue(e.target.value)} />}
          <DialogFooter>
            <Button type="button" variant="secondary" onClick={() => onOpenChange(false)}>Cancel</Button>
            <Button type="submit" disabled={!value.trim()}>Save</Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
