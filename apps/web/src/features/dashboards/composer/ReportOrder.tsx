import {
  closestCenter, DndContext, KeyboardSensor, PointerSensor, useSensor, useSensors, type Announcements, type DragEndEvent,
} from "@dnd-kit/core";
import { arrayMove, SortableContext, sortableKeyboardCoordinates, useSortable, verticalListSortingStrategy } from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import { GripVertical, Plus, X } from "lucide-react";
import { useState, type KeyboardEvent } from "react";
import { Link } from "react-router-dom";

import type { DashboardDetail, ReportListItem, StarterReport } from "../../../api/authoring";
import { ContentBadge } from "../../../components/ContentBadge";
import { Badge } from "../../../components/ui/badge";
import { Button } from "../../../components/ui/button";
import { Command, CommandEmpty, CommandGroup, CommandInput, CommandItem, CommandList } from "../../../components/ui/command";
import { Input } from "../../../components/ui/input";
import { Popover, PopoverContent, PopoverTrigger } from "../../../components/ui/popover";
import { cn } from "../../../lib/utils";

export type OrderItem = { report_id: string; group_title?: string | null };
type Row = DashboardDetail["reports"][number];

/**
 * The dashboard's reports in order (US-142): drag by the handle, or with the keyboard — Space picks a report up, arrows
 * move it, Space drops, Escape cancels — or Alt + arrow keys on the handle to move one place at once. Every move is
 * announced. In the grouped layout each report can carry a group title. Starter reports can be added as they are
 * (FEAT-046): they follow the pack, and an Admin deleting one takes it out of this draft (RULE-017).
 */
export function ReportOrder({ dashboard, reports, starters = [], onOrder }: {
  dashboard: DashboardDetail;
  reports: ReportListItem[];
  starters?: StarterReport[];
  onOrder: (items: OrderItem[], label: string) => void;
}) {
  const edit = dashboard.can_edit;
  const rows = dashboard.reports;
  const current: OrderItem[] = rows.map((r) => ({ report_id: r.id, group_title: r.group_title }));
  const [announcement, setAnnouncement] = useState("");
  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 4 } }),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }),
  );
  const titleOf = (id: unknown) => rows.find((r) => r.id === id)?.title ?? "the report";
  const position = (id: unknown) => `position ${rows.findIndex((r) => r.id === id) + 1} of ${rows.length}`;
  const announcements: Announcements = {
    onDragStart: ({ active }) => `Picked up ${titleOf(active.id)}, ${position(active.id)}.`,
    onDragOver: ({ active, over }) => (over ? `Moving ${titleOf(active.id)}: ${position(over.id)}.` : "Not over the list."),
    onDragEnd: ({ active, over }) => (over ? `${titleOf(active.id)} dropped at ${position(over.id)}.` : "Dropped outside the list; nothing changed."),
    onDragCancel: ({ active }) => `Cancelled; ${titleOf(active.id)} stays where it was.`,
  };
  const move = (from: number, to: number) => {
    if (from === to || to < 0 || to >= rows.length) return;
    onOrder(arrayMove(current, from, to), `Moved ${rows[from]!.title} to position ${to + 1}`);
  };
  const onDragEnd = ({ active, over }: DragEndEvent) => {
    if (!over) return;
    move(rows.findIndex((r) => r.id === active.id), rows.findIndex((r) => r.id === over.id));
  };
  const onAltKey = (e: KeyboardEvent, i: number) => {
    if (!e.altKey || (e.key !== "ArrowUp" && e.key !== "ArrowDown")) return;
    e.preventDefault();
    e.stopPropagation();
    const to = i + (e.key === "ArrowUp" ? -1 : 1);
    if (to < 0 || to >= rows.length) return;
    move(i, to);
    setAnnouncement(`Moved ${rows[i]!.title}: position ${to + 1} of ${rows.length}.`);
  };
  const available = reports.filter((r) => r.mine && !rows.some((x) => x.id === r.id));
  const starterAvailable = starters.filter((r) => !rows.some((x) => x.id === r.id));

  return (
    <section aria-labelledby="reports-heading" className="grid min-w-0 gap-3">
      <div className="flex items-baseline justify-between gap-2">
        <h2 id="reports-heading" className="text-heading">Reports, in order</h2>
        {edit && rows.length > 1 && <span className="text-small text-ink-muted">Drag, or Alt + arrow keys</span>}
      </div>
      {rows.length === 0 ? (
        <p className="text-small text-ink-muted">Add at least one report. <Link to="/reports/new">Build one with chat</Link>.</p>
      ) : edit ? (
        <DndContext sensors={sensors} collisionDetection={closestCenter} onDragEnd={onDragEnd} accessibility={{ announcements }}>
          <SortableContext items={rows.map((r) => r.id)} strategy={verticalListSortingStrategy}>
            <ol className="grid gap-1.5" aria-label="Reports on this dashboard">
              {rows.map((r, i) => (
                <SortableRow key={r.id} row={r} index={i} grouped={dashboard.layout === "grouped"} onAltKey={(e) => onAltKey(e, i)}
                  onGroupTitle={(t) => onOrder(current.map((c) => (c.report_id === r.id ? { ...c, group_title: t } : c)), `Renamed the group to ${t ?? r.title}`)}
                  onRemove={() => onOrder(current.filter((c) => c.report_id !== r.id), `Removed ${r.title}`)} />
              ))}
            </ol>
          </SortableContext>
        </DndContext>
      ) : (
        <ol className="grid gap-1.5" aria-label="Reports on this dashboard">
          {rows.map((r, i) => <li key={r.id} className="flex items-center gap-2 rounded-md border border-line px-3 py-2"><span className="w-4 text-small text-ink-muted">{i + 1}</span><Link to={`/reports/${r.id}`}>{r.title}</Link>{r.starter && <ContentBadge badge="starter" />}</li>)}
        </ol>
      )}
      <p className="sr-only" role="status" aria-live="assertive">{announcement}</p>
      {edit && <AddReport available={available} starters={starterAvailable} onAdd={(r) => onOrder([...current, { report_id: r.id }], `Added ${r.title}`)} />}
    </section>
  );
}

function SortableRow({ row, index, grouped, onAltKey, onGroupTitle, onRemove }: {
  row: Row;
  index: number;
  grouped: boolean;
  onAltKey: (e: KeyboardEvent) => void;
  onGroupTitle: (t: string | null) => void;
  onRemove: () => void;
}) {
  const s = useSortable({ id: row.id });
  const style = { transform: CSS.Transform.toString(s.transform), transition: s.transition };
  const listeners = s.listeners ?? {};
  return (
    <li ref={s.setNodeRef} style={style}
      className={cn("grid min-w-0 gap-2 rounded-md border border-line bg-surface-raised px-2 py-2", s.isDragging && "relative z-10 shadow-lg ring-2 ring-accent")}>
      <div className="flex min-w-0 items-center gap-2">
        <button type="button" {...s.attributes} {...listeners}
          onKeyDown={(e) => { onAltKey(e); if (!e.defaultPrevented) listeners.onKeyDown?.(e); }}
          aria-label={`Move ${row.title}, position ${index + 1}`} aria-roledescription="sortable report"
          className="cursor-grab rounded-sm p-1 text-ink-muted hover:bg-surface-sunken focus-visible:focus-ring active:cursor-grabbing">
          <GripVertical aria-hidden className="size-4" />
        </button>
        <span className="w-4 text-small tabular-nums text-ink-muted" aria-hidden>{index + 1}</span>
        <Link to={`/reports/${row.id}`} title={row.title} className="min-w-0 flex-1 truncate font-medium">{row.title}</Link>
        {row.starter && <ContentBadge badge="starter" />}
        <Badge>{row.widgets} {row.widgets === 1 ? "widget" : "widgets"}</Badge>
        <Button variant="ghost" size="icon" className="size-control-sm" aria-label={`Remove ${row.title} from the dashboard`} onClick={onRemove}><X aria-hidden /></Button>
      </div>
      {grouped && (
        <Input aria-label={`Group title for ${row.title}`} placeholder={`Group title (default: ${row.title})`} defaultValue={row.group_title ?? ""}
          key={row.group_title ?? ""} maxLength={120} className="h-control-sm"
          onBlur={(e) => { const t = e.target.value.trim() || null; if (t !== row.group_title) onGroupTitle(t); }} />
      )}
    </li>
  );
}

function AddReport({ available, starters, onAdd }: {
  available: ReportListItem[];
  starters: StarterReport[];
  onAdd: (r: { id: string; title: string }) => void;
}) {
  const [open, setOpen] = useState(false);
  const none = available.length === 0 && starters.length === 0;
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button variant="secondary" size="sm" className="w-fit" disabled={none}
          title={none ? "Every report you can add is on this dashboard" : undefined}>
          <Plus aria-hidden />Add a report
        </Button>
      </PopoverTrigger>
      <PopoverContent align="start" className="w-80 p-0">
        <Command>
          <CommandInput placeholder="Find a report" />
          <CommandList>
            <CommandEmpty>No report matches.</CommandEmpty>
            {available.length > 0 && (
              <CommandGroup heading="Your reports">
                {available.map((r) => (
                  <CommandItem key={r.id} value={`${r.title} ${r.id}`} onSelect={() => { setOpen(false); onAdd(r); }}>
                    <span className="min-w-0 flex-1 truncate">{r.title}</span>
                  </CommandItem>
                ))}
              </CommandGroup>
            )}
            {starters.length > 0 && (
              <CommandGroup heading="Starter reports">
                {starters.map((r) => (
                  <CommandItem key={r.id} value={`${r.title} ${r.id}`} onSelect={() => { setOpen(false); onAdd(r); }}>
                    <span className="min-w-0 flex-1 truncate">{r.title}</span>
                    <span className="text-small text-ink-muted">{r.widgets} widgets</span>
                  </CommandItem>
                ))}
              </CommandGroup>
            )}
          </CommandList>
        </Command>
      </PopoverContent>
    </Popover>
  );
}
