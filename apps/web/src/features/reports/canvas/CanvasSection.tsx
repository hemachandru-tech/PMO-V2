import { useDroppable } from "@dnd-kit/core";
import { useSortable } from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import { ChevronDown, ChevronRight, EllipsisVertical, GripVertical, Trash2 } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";
import GridLayout, { WidthProvider, type Layout } from "react-grid-layout";
import "react-grid-layout/css/styles.css";

import type { ReportSection } from "../../../api/authoring";
import { Button } from "../../../components/ui/button";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from "../../../components/ui/dropdown-menu";
import { cn } from "../../../lib/utils";
import { COLS, GAP, MIN_H, MIN_W, ROW, type Box } from "./layout";

const Grid = WidthProvider(GridLayout);

export const sectionDropId = (id: string) => `section:${id}`;

/**
 * A report section on the canvas (FEAT-025; US-141): a sortable header (drag the grip, or focus it and use Space and
 * the arrow keys) with an editable title, and a 12-column snap grid that accepts drops from the library.
 */
export function CanvasSection({ section, edit, index, count, selectedId, onRename, onToggle, onRemove, onLayout, renderItem, emptyHint }: {
  section: ReportSection;
  edit: boolean;
  index: number;
  count: number;
  /** The selected item is raised so its floating toolbar stays above its neighbours. */
  selectedId: string | null;
  onRename: (title: string) => void;
  onToggle: () => void;
  onRemove: () => void;
  onLayout: (boxes: Box[]) => void;
  renderItem: (itemId: string) => ReactNode;
  emptyHint: string;
}) {
  const sortable = useSortable({ id: section.id, disabled: !edit, data: { section: section.id } });
  const drop = useDroppable({ id: sectionDropId(section.id), disabled: !edit, data: { section: section.id } });
  const [title, setTitle] = useState(section.title);
  useEffect(() => setTitle(section.title), [section.title]);
  const headline = section.kind === "headline";
  const toBoxes = (l: Layout[]) => l.map((b) => ({ id: b.i, x: b.x, y: b.y, w: b.w, h: b.h }));

  return (
    <section ref={sortable.setNodeRef} aria-label={section.title}
      style={{ transform: CSS.Translate.toString(sortable.transform), transition: sortable.transition }}
      className={cn("grid gap-2 rounded-lg", sortable.isDragging && "relative z-30 bg-surface shadow-drag", headline && "bg-accent-soft/30 p-2")}>
      <header className="flex items-center gap-1">
        {edit && (
          <Button variant="ghost" size="icon" className="size-control-sm cursor-grab touch-none text-ink-muted" aria-label={`Reorder section ${section.title}, ${index + 1} of ${count}`}
            {...sortable.attributes} {...sortable.listeners}>
            <GripVertical aria-hidden />
          </Button>
        )}
        <Button variant="ghost" size="icon" className="size-control-sm" aria-expanded={!section.collapsed}
          aria-label={section.collapsed ? `Expand ${section.title}` : `Collapse ${section.title}`} onClick={onToggle}>
          {section.collapsed ? <ChevronRight aria-hidden /> : <ChevronDown aria-hidden />}
        </Button>
        <span className="text-small font-semibold uppercase tracking-wide text-ink-muted">Section</span>
        {edit ? (
          <input aria-label="Section title" value={title} maxLength={120} onChange={(e) => setTitle(e.target.value)}
            onBlur={() => { const t = title.trim(); if (t && t !== section.title) onRename(t); else setTitle(section.title); }}
            onKeyDown={(e) => { if (e.key === "Enter") e.currentTarget.blur(); }}
            className="min-w-0 flex-1 rounded-sm bg-transparent px-1 text-heading hover:bg-surface-sunken focus-visible:focus-ring" />
        ) : <h2 className="flex-1 text-heading">{section.title}</h2>}
        {edit && count > 1 && (
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button variant="ghost" size="icon" className="size-control-sm" aria-label={`Section actions for ${section.title}`}><EllipsisVertical aria-hidden /></Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              <DropdownMenuItem className="text-status-danger" onSelect={onRemove}><Trash2 aria-hidden />Remove section</DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        )}
      </header>
      {!section.collapsed && (
        <div ref={drop.setNodeRef} className={cn("min-h-24 rounded-lg transition-colors", drop.isOver && "bg-accent-soft/40 outline-2 outline-dashed outline-accent")}>
          {section.items.length === 0 ? (
            <p className="grid min-h-24 place-items-center rounded-lg border border-dashed border-line px-4 text-center text-small text-ink-muted">{emptyHint}</p>
          ) : (
            <Grid className="tl-canvas-grid" cols={COLS} rowHeight={ROW} margin={[GAP, GAP]} containerPadding={[0, 0]} compactType="vertical"
              isDraggable={edit} isResizable={edit} draggableCancel="button, a, input, select, textarea, [role=menuitem], [role=toolbar], canvas, [data-chart]"
              layout={section.items.map((i) => ({ i: i.id, x: i.x, y: i.y, w: i.w, h: i.h, minW: MIN_W, minH: MIN_H }))}
              onDragStop={(l: Layout[]) => onLayout(toBoxes(l))} onResizeStop={(l: Layout[]) => onLayout(toBoxes(l))}>
              {section.items.map((i) => <div key={i.id} className={i.id === selectedId ? "z-20" : undefined}>{renderItem(i.id)}</div>)}
            </Grid>
          )}
        </div>
      )}
    </section>
  );
}
