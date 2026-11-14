import {
  closestCenter, DndContext, DragOverlay, KeyboardSensor, PointerSensor, pointerWithin, rectIntersection, useSensor, useSensors,
  type Announcements, type CollisionDetection, type DragEndEvent, type DragStartEvent,
} from "@dnd-kit/core";
import { SortableContext, sortableKeyboardCoordinates, verticalListSortingStrategy } from "@dnd-kit/sortable";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Asterisk, CircleAlert, Cloud, Info, LayoutDashboard, Library as LibraryIcon, Loader2, MessageSquareText, PackagePlus, Plus, Share2, StickyNote, Undo2 } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { Link, useLocation, useParams } from "react-router-dom";
import { toast } from "sonner";

import { api, ApiError, apiRequest } from "../../api/client";
import type { Home, PageFilter } from "../../api/app";
import type { Report, ReportItem, ReportSection } from "../../api/authoring";
import type { Catalog } from "../../api/govern";
import { can, useMe } from "../../app/me";
import { ContentBadge } from "../../components/ContentBadge";
import { DataAsOf } from "../../components/DataAsOf";
import { CopyStarterButton } from "../../components/CopyStarterButton";
import { DrillSheet, type DrillTarget } from "../../components/DrillSheet";
import { ShareDialog } from "../../components/ShareDialog";
import { FormAlert } from "../../components/ui/alert";
import { Badge } from "../../components/ui/badge";
import { Button } from "../../components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "../../components/ui/dialog";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuLabel, DropdownMenuTrigger } from "../../components/ui/dropdown-menu";
import { Skeleton } from "../../components/ui/skeleton";
import { Tooltip, TooltipContent, TooltipTrigger } from "../../components/ui/tooltip";
import { asApiError } from "../../lib/api-errors";
import { cn } from "../../lib/utils";
import { FilterBar, type FilterDef } from "../viewer/FilterBar";
import { CanvasItem, type Call } from "./canvas/CanvasItem";
import { CanvasSection, sectionDropId } from "./canvas/CanvasSection";
import { ChatPanel } from "./canvas/ChatPanel";
import { dropCell, describeBox, nudge, type Box, type Nudge } from "./canvas/layout";
import { EntryOverlay, Library, type LibraryEntry } from "./canvas/Library";
import { PreviewAsRole } from "./canvas/PreviewAsRole";
import type { BuiltState } from "./ReportPlannerPage";
import { useAppName } from "../../app/branding";
import { SaveAsBlueprint } from "../admin/packs/reports/SaveFromDialog";

export { settle } from "./canvas/layout";

type SaveState = { kind: "idle" } | { kind: "pending" } | { kind: "saving" } | { kind: "saved"; at: Date } | { kind: "error" };

const boxesOf = (s: ReportSection): Box[] => s.items.map((i) => ({ id: i.id, x: i.x, y: i.y, w: i.w, h: i.h }));
const sameBoxes = (a: Box[], b: Box[]) => a.length === b.length && a.every((x) => {
  const y = b.find((o) => o.id === x.id);
  return !!y && y.x === x.x && y.y === x.y && y.w === x.w && y.h === x.h;
});
const withBoxes = (r: Report, sectionId: string, boxes: Box[]): Report => ({
  ...r,
  sections: r.sections.map((s) => (s.id !== sectionId ? s : {
    ...s, items: s.items.map((i) => { const b = boxes.find((x) => x.id === i.id); return b ? { ...i, x: b.x, y: b.y, w: b.w, h: b.h } : i; }),
  })),
});
const TEXT_LABELS: Record<string, string> = { text_note: "Text note", callout: "Callout", footnote: "Footnote" };
const itemLabel = (i: ReportItem) => (i.kind === "widget" ? i.title ?? "Widget" : TEXT_LABELS[i.kind] ?? "Text");

/** Canvas columns: library, canvas, chat panel; either side panel can be closed. */
const COLUMNS: Record<string, string> = {
  lc: "lg:grid-cols-[14rem_minmax(0,1fr)_18rem]", l: "lg:grid-cols-[15rem_minmax(0,1fr)]", c: "lg:grid-cols-[minmax(0,1fr)_19rem]", "": "",
};

/** Only one Undo toast at a time: each change replaces the last one. */
const CHANGE_TOAST = "canvas-change";

/**
 * Report canvas (FEAT-024 – 027; SCR-03; US-141). A library panel (certified measures, my widgets, Ask) with drag-in,
 * sections reordered with dnd-kit, a 12-column snap grid per section with a floating toolbar on the selected widget,
 * keyboard move and resize with live announcements, autosave with an indicator, Undo and Undo all as toasts, and edit
 * with chat as a side panel. Every change is checked and versioned by the API; the browser never computes figures.
 */
export function ReportCanvasPage() {
  const appName = useAppName();
  const { id } = useParams();
  const location = useLocation();
  const qc = useQueryClient();
  const [report, setReportState] = useState<Report | null>(null);
  const [loadError, setLoadError] = useState<ApiError | null>(null);
  const [save, setSave] = useState<SaveState>({ kind: "idle" });
  const [selected, setSelected] = useState<string | null>(null);
  const [focusId, setFocusId] = useState<string | null>(null);
  const [highlights, setHighlights] = useState<string[]>([]);
  const [chatOpen, setChatOpen] = useState(false);
  const [libraryOpen, setLibraryOpen] = useState(true);
  const [sharing, setSharing] = useState(false);
  const [confirm, setConfirm] = useState<null | { kind: "undo-all" } | { kind: "section"; section: ReportSection }>(null);
  const [filters, setFilters] = useState<PageFilter[]>([]);
  const [drill, setDrill] = useState<DrillTarget | null>(null);
  const [dragging, setDragging] = useState<LibraryEntry | null>(null);
  const [announcement, setAnnouncement] = useState("");
  const [connectionChoice, setConnectionChoice] = useState<string | null>(null);

  const reportRef = useRef<Report | null>(null);
  const etag = useRef<string | null>(null);
  const chain = useRef<Promise<unknown>>(Promise.resolve());
  const dirty = useRef(new Set<string>());
  const timer = useRef<ReturnType<typeof setTimeout>>();
  const setReport = useCallback((r: Report) => { reportRef.current = r; setReportState(r); }, []);

  const load = useCallback(async () => {
    try {
      const r = await apiRequest<Report>(`/api/v1/reports/${id}`);
      setReport(r.data);
      etag.current = r.etag;
    } catch (e) {
      setLoadError(asApiError(e, "This report could not be loaded."));
    }
  }, [id, setReport]);
  useEffect(() => { void load(); }, [load]);

  // A report just built from the planner says what happened to each widget, once.
  useEffect(() => {
    const built = (location.state as BuiltState | null)?.built;
    if (!built) return;
    const off = built.filter((b) => b.status !== "ok");
    toast.success(`Built ${built.length} ${built.length === 1 ? "widget" : "widgets"}`, {
      description: off.length ? off.map((b) => `${b.title}: ${b.message ?? b.status}`).join(" · ") : "Every widget returned data in your scope.",
    });
    window.history.replaceState({}, "");
  }, [location.state]);

  const home = useQuery({ queryKey: ["home"], queryFn: () => api<Home>("/api/v1/home") });
  const allItems = useMemo(() => report?.sections.flatMap((s) => s.items) ?? [], [report]);
  const firstWidget = allItems.find((i) => i.kind === "widget");
  const connectionId = connectionChoice ?? firstWidget?.connection_id ?? home.data?.greeting.scopes[0]?.connection_id ?? null;
  const catalog = useQuery({
    queryKey: ["catalog", connectionId],
    queryFn: () => api<Catalog>(`/api/v1/query/catalog?connection_id=${connectionId}`),
    enabled: !!connectionId && (report?.page_filters.length ?? 0) > 0,
    retry: false,
  });

  const callRef = useRef<Call>();
  const undo = useCallback(() => void callRef.current?.(`/api/v1/reports/${id}/undo`, { method: "POST" }), [id]);

  /** Every change goes through one queue so revisions stay in order; layout saves send If-Match. */
  const call: Call = useCallback((path, opts, done) => {
    const run = async (): Promise<Report | null> => {
      setSave({ kind: "saving" });
      try {
        const layout = path.endsWith("/layout");
        const r = await apiRequest<Report>(path, { ...opts, ifMatch: layout ? etag.current ?? undefined : undefined });
        if (r.data?.id) {
          const before = reportRef.current;
          setReport(r.data);
          etag.current = r.etag;
          // A widget's run is cached by its id: a new chart type, Top N or filter keeps the id, so refetch it (BUG-019).
          for (const wid of changedWidgets(before, r.data)) void qc.invalidateQueries({ queryKey: ["widget", wid] });
        } else {
          await load();
        }
        setSave({ kind: "saved", at: new Date() });
        void qc.invalidateQueries({ queryKey: ["home"] });
        if (path.endsWith("/items")) void qc.invalidateQueries({ queryKey: ["library"] });
        if (path.endsWith("/undo")) {
          toast(r.data?.undone ? `Undone: ${r.data.undone}` : "Undone", { id: CHANGE_TOAST });
        } else if (done) {
          toast(done, { id: CHANGE_TOAST, action: { label: "Undo", onClick: undo } });
        }
        return r.data;
      } catch (e) {
        const err = asApiError(e, "That change could not be saved.");
        setSave({ kind: "error" });
        toast.error(err.status === 412 ? "This report changed in another window. It has been reloaded." : err.message, { id: CHANGE_TOAST });
        await load();
        return null;
      }
    };
    const next = chain.current.then(run, run);
    chain.current = next;
    return next;
  }, [load, qc, setReport, undo]);
  callRef.current = call;

  const saveLayout = useCallback((sectionId: string, boxes: Box[], done = "Layout saved") =>
    call(`/api/v1/reports/${id}/layout`, {
      method: "PUT", body: { items: boxes.map((b) => ({ item_id: b.id, section_id: sectionId, x: b.x, y: b.y, w: b.w, h: b.h })) },
    }, done), [call, id]);

  const flush = useCallback(() => {
    clearTimeout(timer.current);
    const r = reportRef.current;
    if (!r) return;
    for (const sid of dirty.current) {
      const s = r.sections.find((x) => x.id === sid);
      if (s) void saveLayout(sid, boxesOf(s));
    }
    dirty.current.clear();
  }, [saveLayout]);
  useEffect(() => () => flush(), [flush]);

  /** Keyboard move/resize: shown at once, announced, saved after a short pause so a run of key presses is one change. */
  const onNudge = useCallback((item: ReportItem, dir: Nudge, resize: boolean) => {
    const r = reportRef.current;
    const s = r?.sections.find((x) => x.id === item.section_id || x.items.some((i) => i.id === item.id));
    if (!r || !s) return;
    const boxes = nudge(boxesOf(s), item.id, dir, resize);
    const b = boxes.find((x) => x.id === item.id)!;
    setReport(withBoxes(r, s.id, boxes));
    setAnnouncement(describeBox(itemLabel(item), b));
    dirty.current.add(s.id);
    setSave({ kind: "pending" });
    clearTimeout(timer.current);
    timer.current = setTimeout(flush, 700);
  }, [flush, setReport]);

  const onLayout = useCallback((section: ReportSection, boxes: Box[]) => {
    const r = reportRef.current;
    if (!r || sameBoxes(boxesOf(section), boxes)) return;
    setReport(withBoxes(r, section.id, boxes));
    void saveLayout(section.id, boxes);
  }, [saveLayout, setReport]);

  const add = useCallback(async (entry: LibraryEntry, sectionId?: string, cell?: { x: number; y: number }) => {
    const body = entry.kind === "widget"
      ? { kind: "widget", widget_id: entry.id }
      : { kind: "measure", measure: entry.ref, connection_id: entry.connectionId };
    const out = await call(`/api/v1/reports/${id}/items`, { method: "POST", body: { ...body, section_id: sectionId, ...cell } }, `Added ${entry.title}`);
    if (out?.item_id) { setSelected(out.item_id); setFocusId(out.item_id); }
  }, [call, id]);

  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 6 } }),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }),
  );
  // Library entries drop on section grids; sections reorder among themselves.
  const collision: CollisionDetection = useCallback((args) => {
    const lib = String(args.active.id).startsWith("lib:");
    const droppableContainers = args.droppableContainers.filter((c) => String(c.id).startsWith("section:") === lib);
    if (!lib) return closestCenter({ ...args, droppableContainers });
    const within = pointerWithin({ ...args, droppableContainers });
    return within.length ? within : rectIntersection({ ...args, droppableContainers });
  }, []);
  const titleOf = (sid: unknown) => reportRef.current?.sections.find((s) => s.id === sid || sectionDropId(s.id) === sid)?.title ?? "a section";
  const announcements: Announcements = {
    onDragStart: ({ active }) => (active.data.current?.entry ? `Picked up ${(active.data.current.entry as LibraryEntry).title}.` : `Picked up section ${titleOf(active.id)}.`),
    onDragOver: ({ active, over }) => (over ? `${active.data.current?.entry ? "Over" : "Moved to the place of"} ${titleOf(over.id)}.` : "Not over a section."),
    onDragEnd: ({ over }) => (over ? `Dropped on ${titleOf(over.id)}.` : "Dropped outside the canvas; nothing changed."),
    onDragCancel: () => "Cancelled; nothing changed.",
  };
  const onDragStart = (e: DragStartEvent) => setDragging((e.active.data.current?.entry as LibraryEntry | undefined) ?? null);
  const onDragEnd = (e: DragEndEvent) => {
    setDragging(null);
    const { active, over } = e;
    const r = reportRef.current;
    if (!over || !r) return;
    const entry = active.data.current?.entry as LibraryEntry | undefined;
    if (entry) {
      const sid = over.data.current?.section as string;
      const card = active.rect.current.translated;
      const w = entry.kind === "measure" || entry.visual === "figure" ? 3 : entry.visual === "table" ? 12 : 6;
      void add(entry, sid, card ? dropCell(over.rect, card, w) : undefined);
      return;
    }
    const to = r.sections.findIndex((s) => s.id === over.id);
    const from = r.sections.findIndex((s) => s.id === active.id);
    if (from < 0 || to < 0 || from === to) return;
    void call(`/api/v1/reports/${r.id}/sections/${active.id}`, { method: "PATCH", body: { position: to } }, `Moved section ${r.sections[from]!.title}`);
  };

  if (loadError && !report) return <div className="tl"><FormAlert error={loadError} /></div>;
  if (!report) return <CanvasSkeleton />;
  const base = `/api/v1/reports/${report.id}`;
  const edit = report.can_edit;
  const filterLabels = Object.fromEntries(report.page_filters.map((f) => [f.ref, labelOf(f.ref, catalog.data)]));
  const filterDefs: FilterDef[] = report.page_filters.map((f) => ({
    ref: f.ref, label: filterLabels[f.ref]!, connectionId: firstWidget?.connection_id, modelVersionId: firstWidget?.model_version_id,
  }));
  const onReport = new Set(allItems.map((i) => i.widget_id).filter((x): x is string => !!x));

  const canvas = (
    <div className="grid min-w-0 content-start gap-6 pt-10" onMouseDown={(e) => { if (e.target === e.currentTarget) setSelected(null); }}>
      <SortableContext items={report.sections.map((s) => s.id)} strategy={verticalListSortingStrategy}>
        {report.sections.map((s, si) => (
          <CanvasSection key={s.id} section={s} edit={edit} index={si} count={report.sections.length} selectedId={selected}
            onRename={(t) => void call(`${base}/sections/${s.id}`, { method: "PATCH", body: { title: t } }, `Renamed section to ${t}`)}
            onToggle={() => (edit ? void call(`${base}/sections/${s.id}`, { method: "PATCH", body: { collapsed: !s.collapsed } }) : undefined)}
            onRemove={() => setConfirm({ kind: "section", section: s })}
            onLayout={(boxes) => onLayout(s, boxes)}
            emptyHint={edit ? "Drag a measure or widget here, or pin an answer from Ask." : "Nothing in this section yet."}
            renderItem={(itemId) => {
              const item = s.items.find((i) => i.id === itemId)!;
              return (
                <CanvasItem item={item} report={report} section={s} edit={edit} selected={selected === item.id} focus={focusId === item.id}
                  highlighted={highlights.includes(item.id)} filters={filters} filterLabels={filterLabels} call={call}
                  onSelect={setSelected} onNudge={onNudge} onDrill={setDrill} />
              );
            }} />
        ))}
      </SortableContext>
      {edit && (
        <div className="flex flex-wrap gap-2">
          <Button variant="secondary" onClick={() => void call(`${base}/sections`, { method: "POST", body: { title: "New section" } }, "Added a section")}><Plus aria-hidden />Add section</Button>
          {([
            ["text_note", { text: "New note" }, "Added a text note", <StickyNote key="i" aria-hidden />, "Add text note"],
            ["callout", { title: "Read this first", text: "What this report counts, and any caveat.", level: "info" }, "Added a callout", <Info key="i" aria-hidden />, "Add callout"],
            ["footnote", { text: "Population and data limits of this report." }, "Added a footnote", <Asterisk key="i" aria-hidden />, "Add footnote"],
          ] as const).map(([kind, body, done, icon, label]) => (
            <Button key={kind} variant="ghost" onClick={async () => {
              const out = await call(`${base}/items`, { method: "POST", body: { kind, ...body } }, done);
              if (out?.item_id) { setSelected(out.item_id); setFocusId(out.item_id); }
            }}>{icon}{label}</Button>
          ))}
        </div>
      )}
    </div>
  );

  return (
    <div className="tl grid gap-4">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex min-w-0 flex-wrap items-center gap-2">
          {edit ? <TitleInput report={report} onRename={(t) => void call(base, { method: "PATCH", body: { title: t } }, `Renamed to ${t}`)} />
            : <h1 className="text-title">{report.title}</h1>}
          <ContentBadge badge={report.origin === "starter" ? "starter" : "draft"} />
          {report.published && <Badge variant="certified" title="Readers of the dashboard see the last published version">Published version live</Badge>}
          {report.reporting_date && <DataAsOf reportingDate={report.reporting_date} />}
          {edit ? <SaveIndicator state={save} updatedAt={report.updated_at} />
            : report.origin === "starter"
              ? <span className="text-small text-ink-muted">{appName} starter — view only, in your own data scope</span>
              : <span className="text-small text-ink-muted">Shared by {report.owner.name} — view only, in your own data scope</span>}
        </div>
        {report.origin === "starter" && <StarterCopy reportId={report.id} />}
        {edit && (
          <div className="flex flex-wrap items-center gap-1" role="toolbar" aria-label="Report">
            <Tooltip>
              <TooltipTrigger asChild>
                <span tabIndex={report.can_undo ? -1 : 0}>
                  <Button variant="ghost" size="sm" disabled={!report.can_undo} onClick={undo}
                    aria-label={report.undo_summary ? `Undo: ${report.undo_summary}` : "Undo"}><Undo2 aria-hidden />Undo</Button>
                </span>
              </TooltipTrigger>
              <TooltipContent>{report.undo_summary ? `Undo: ${report.undo_summary}` : "Nothing to undo"}</TooltipContent>
            </Tooltip>
            <Button variant="ghost" size="sm" onClick={() => setConfirm({ kind: "undo-all" })}>Undo all</Button>
            <Button variant={libraryOpen ? "secondary" : "ghost"} size="sm" aria-pressed={libraryOpen} onClick={() => setLibraryOpen(!libraryOpen)}>
              <LibraryIcon aria-hidden />Library
            </Button>
            <PreviewAsRole reportId={report.id} />
            <Button variant={chatOpen ? "secondary" : "ghost"} size="sm" aria-pressed={chatOpen} onClick={() => {
              // Below 1536 px the canvas would be too narrow with both panels; the Library button brings it back.
              if (!chatOpen && window.innerWidth < 1536) setLibraryOpen(false);
              setChatOpen(!chatOpen);
            }}>
              <MessageSquareText aria-hidden />Edit with chat
            </Button>
            <Button variant="ghost" size="sm" onClick={() => setSharing(true)}><Share2 aria-hidden />Share</Button>
            <PackAuthorOnly>
              <SaveAsBlueprint kind="report" sourceId={report.id}>
                {(open) => <Button variant="ghost" size="sm" onClick={open}><PackagePlus aria-hidden />Save as blueprint</Button>}
              </SaveAsBlueprint>
            </PackAuthorOnly>
            {report.in_dashboards.length > 0 ? (
              <DropdownMenu>
                <DropdownMenuTrigger asChild><Button variant="secondary" size="sm"><LayoutDashboard aria-hidden />On {report.in_dashboards.length} {report.in_dashboards.length === 1 ? "dashboard" : "dashboards"}</Button></DropdownMenuTrigger>
                <DropdownMenuContent align="end">
                  <DropdownMenuLabel>Open in the composer</DropdownMenuLabel>
                  {report.in_dashboards.map((d) => <DropdownMenuItem key={d.id} asChild><Link to={`/dashboards/${d.id}/edit`}>{d.title}</Link></DropdownMenuItem>)}
                </DropdownMenuContent>
              </DropdownMenu>
            ) : (
              <Button asChild variant="secondary" size="sm"><Link to={`/dashboards/new?report=${report.id}`}><LayoutDashboard aria-hidden />Add to a dashboard</Link></Button>
            )}
          </div>
        )}
      </header>

      {report.page_filters.length > 0 && (
        <div className="flex flex-wrap items-center gap-2">
          <FilterBar filters={filterDefs} value={filters} onChange={setFilters} />
          {edit && (
            <DropdownMenu>
              <DropdownMenuTrigger asChild><Button variant="ghost" size="sm">Edit page filters</Button></DropdownMenuTrigger>
              <DropdownMenuContent>
                <DropdownMenuLabel>Remove a page filter</DropdownMenuLabel>
                {report.page_filters.map((f) => (
                  <DropdownMenuItem key={f.ref} onSelect={() => {
                    setFilters(filters.filter((x) => x.ref !== f.ref));
                    void call(base, { method: "PATCH", body: { page_filters: report.page_filters.filter((x) => x.ref !== f.ref) } }, `Removed the ${filterLabels[f.ref]} filter`);
                  }}>{filterLabels[f.ref]}</DropdownMenuItem>
                ))}
              </DropdownMenuContent>
            </DropdownMenu>
          )}
        </div>
      )}

      <p className="sr-only" role="status" aria-live="assertive">{announcement}</p>

      {edit ? (
        <DndContext sensors={sensors} collisionDetection={collision} onDragStart={onDragStart} onDragEnd={onDragEnd}
          onDragCancel={() => setDragging(null)} accessibility={{ announcements }}>
          <div className={cn("grid items-start gap-4", COLUMNS[`${libraryOpen ? "l" : ""}${chatOpen ? "c" : ""}`])}>
            {libraryOpen && (
              <div className="lg:sticky lg:top-4">
                <Library reportId={report.id} onWidgets={onReport} connectionId={connectionId} onConnection={setConnectionChoice}
                  onAdd={(e) => void add(e)} busy={save.kind === "saving"} />
              </div>
            )}
            {canvas}
            {chatOpen && (
              <div className="lg:sticky lg:top-4">
                <ChatPanel report={report} onHighlight={setHighlights} onUndo={undo} onClose={() => setChatOpen(false)}
                  onApplied={(r, summary) => { setReport(r); etag.current = `"${r.revision}"`; setSave({ kind: "saved", at: new Date() }); toast(summary, { id: CHANGE_TOAST, action: { label: "Undo", onClick: undo } }); }}
                  onAlternative={(itemId, visual, label) => void call(`${base}/items/${itemId}`, { method: "PATCH", body: { visual } }, label)} />
              </div>
            )}
          </div>
          <DragOverlay dropAnimation={null}>{dragging && <EntryOverlay entry={dragging} />}</DragOverlay>
        </DndContext>
      ) : canvas}

      <DrillSheet target={drill} onClose={() => setDrill(null)} />
      {sharing && <ShareDialog kind="report" id={report.id} title={report.title} onClose={() => setSharing(false)} />}
      <Dialog open={confirm !== null} onOpenChange={(o) => !o && setConfirm(null)}>
        <DialogContent>
          {confirm?.kind === "undo-all" ? (
            <>
              <DialogHeader>
                <DialogTitle>{report.published ? "Go back to the published version?" : "Remove everything from this report?"}</DialogTitle>
                <DialogDescription>
                  {report.published ? "Every change since the last publish is undone. Undo brings them back." : "This report was never published, so Undo all empties it. Undo brings everything back."}
                </DialogDescription>
              </DialogHeader>
              <DialogFooter>
                <Button variant="secondary" onClick={() => setConfirm(null)}>Cancel</Button>
                <Button variant="danger" onClick={() => { setConfirm(null); void call(`${base}/undo-all`, { method: "POST" }, report.published ? "Back to the published version" : "Removed everything"); }}>Undo all</Button>
              </DialogFooter>
            </>
          ) : confirm?.kind === "section" ? (
            <>
              <DialogHeader>
                <DialogTitle>Remove the section “{confirm.section.title}”?</DialogTitle>
                <DialogDescription>Its {confirm.section.items.length} {confirm.section.items.length === 1 ? "item leaves" : "items leave"} the report; widgets stay in your library. Undo brings it back.</DialogDescription>
              </DialogHeader>
              <DialogFooter>
                <Button variant="secondary" onClick={() => setConfirm(null)}>Cancel</Button>
                <Button variant="danger" onClick={() => { setConfirm(null); void call(`${base}/sections/${confirm.section.id}`, { method: "DELETE" }, `Removed section ${confirm.section.title}`); }}>Remove section</Button>
              </DialogFooter>
            </>
          ) : null}
        </DialogContent>
      </Dialog>
    </div>
  );
}

function labelOf(ref: string, catalog?: Catalog): string {
  const known = catalog?.dimensions.find((d) => d.ref === ref)?.label;
  if (known) return known;
  const words = (ref.split(":")[1] ?? ref).replace(/_/g, " ");
  return words.charAt(0).toUpperCase() + words.slice(1);
}

function TitleInput({ report, onRename }: { report: Report; onRename: (t: string) => void }) {
  const [title, setTitle] = useState(report.title);
  useEffect(() => setTitle(report.title), [report.title]);
  return (
    <h1 className="min-w-0">
      <input aria-label="Report title" value={title} maxLength={200} onChange={(e) => setTitle(e.target.value)}
        onBlur={() => { const t = title.trim(); if (t && t !== report.title) onRename(t); else setTitle(report.title); }}
        onKeyDown={(e) => { if (e.key === "Enter") e.currentTarget.blur(); if (e.key === "Escape") { setTitle(report.title); e.currentTarget.blur(); } }}
        size={Math.max(8, Math.min(48, title.length + 1))}
        className="max-w-full rounded-sm bg-transparent px-1 text-title hover:bg-surface-sunken focus-visible:focus-ring" />
    </h1>
  );
}

/** Autosave indicator: unsaved (keyboard moves wait a moment), saving, saved at a time, or not saved. */
function SaveIndicator({ state, updatedAt }: { state: SaveState; updatedAt: string }) {
  const time = (d: Date) => d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  const [icon, text] =
    state.kind === "pending" ? [<Cloud key="i" aria-hidden className="size-4" />, "Unsaved changes"]
      : state.kind === "saving" ? [<Loader2 key="i" aria-hidden className="size-4 animate-spin motion-reduce:animate-none" />, "Saving…"]
        : state.kind === "error" ? [<CircleAlert key="i" aria-hidden className="size-4 text-status-danger" />, "Not saved"]
          : [<Cloud key="i" aria-hidden className="size-4" />, `Saved ${time(state.kind === "saved" ? state.at : new Date(updatedAt))}`];
  return <span role="status" aria-live="polite" className="flex items-center gap-1 text-small text-ink-muted">{icon}{text}</span>;
}

function CanvasSkeleton() {
  return (
    <div className="tl grid gap-4" aria-busy="true" aria-label="Loading the report">
      <Skeleton className="h-10 w-1/3" />
      <div className="grid gap-4 lg:grid-cols-[15rem_minmax(0,1fr)]"><Skeleton className="h-96" /><Skeleton className="h-96" /></div>
    </div>
  );
}

/** Pack authors can turn a report into a blueprint of one of their pack drafts (US-234). */
function PackAuthorOnly({ children }: { children: ReactNode }) {
  const me = useMe().data;
  return can(me, "packs.author") ? <>{children}</> : null;
}

/** A starter report is read-only; Creators copy it to make their own (REQ-114). */
function StarterCopy({ reportId }: { reportId: string }) {
  const me = useMe().data;
  return can(me, "reports.create") ? <CopyStarterButton reportId={reportId} /> : null;
}

/** Widgets whose definition (chart type, query, title, filters) differs between two states of a report. */
export function changedWidgets(before: Report | null | undefined, after: Report): string[] {
  const sig = (i: ReportItem) => JSON.stringify([i.visual, i.spec, i.title, i.follow_page_filters, i.own_filters, i.measure]);
  const old = new Map<string, string>();
  for (const i of before?.sections.flatMap((s) => s.items) ?? []) if (i.widget_id) old.set(i.widget_id, sig(i));
  const out = new Set<string>();
  for (const i of after.sections.flatMap((s) => s.items)) {
    if (i.widget_id && old.has(i.widget_id) && old.get(i.widget_id) !== sig(i)) out.add(i.widget_id);
  }
  return [...out];
}
