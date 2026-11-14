import { useDraggable } from "@dnd-kit/core";
import { useQuery } from "@tanstack/react-query";
import { GripVertical, MessageSquarePlus, Search } from "lucide-react";
import { useId, useState } from "react";
import { Link } from "react-router-dom";

import { api } from "../../../api/client";
import type { Home } from "../../../api/app";
import { VISUAL_NAMES, type LibraryWidget } from "../../../api/authoring";
import type { Catalog } from "../../../api/govern";
import { ContentBadge } from "../../../components/ContentBadge";
import { Button } from "../../../components/ui/button";
import { Input } from "../../../components/ui/input";
import { NativeSelect } from "../../../components/ui/native-select";
import { Skeleton } from "../../../components/ui/skeleton";
import { cn } from "../../../lib/utils";

export type LibraryEntry =
  | { kind: "widget"; id: string; title: string; visual: string; badges: string[] }
  | { kind: "measure"; ref: string; title: string; connectionId: string };

export const entryKey = (e: LibraryEntry) => (e.kind === "widget" ? `lib:widget:${e.id}` : `lib:measure:${e.connectionId}:${e.ref}`);

/**
 * The canvas library (FEAT-024; US-141): certified measures of the report's data source and the Creator's own widgets.
 * Drag an entry onto a section (it lands where it is dropped), or press Enter on it to add it at the end of the first
 * section. "Ask for a new widget" opens Ask, which pins its answer back to this report.
 */
export function Library({ reportId, onWidgets, connectionId, onConnection, onAdd, busy }: {
  reportId: string;
  /** Widget ids already on the report; they are listed but can't be added twice. */
  onWidgets: Set<string>;
  connectionId: string | null;
  onConnection: (id: string) => void;
  onAdd: (entry: LibraryEntry) => void;
  busy: boolean;
}) {
  const searchId = useId();
  const [q, setQ] = useState("");
  const home = useQuery({ queryKey: ["home"], queryFn: () => api<Home>("/api/v1/home") });
  const sources = home.data?.greeting.scopes ?? [];
  const catalog = useQuery({
    queryKey: ["catalog", connectionId],
    queryFn: () => api<Catalog>(`/api/v1/query/catalog?connection_id=${connectionId}`),
    enabled: !!connectionId,
    retry: false,
  });
  const lib = useQuery({ queryKey: ["library"], queryFn: () => api<{ items: LibraryWidget[] }>("/api/v1/widgets") });
  const match = (t: string) => t.toLowerCase().includes(q.trim().toLowerCase());
  const measures = (catalog.data?.measures ?? []).filter((m) => match(m.label));
  const widgets = (lib.data?.items ?? []).filter((w) => match(w.title));

  return (
    <aside aria-label="Add to the report" className="grid max-h-[calc(100dvh-8rem)] content-start gap-3 overflow-y-auto rounded-lg border border-line bg-surface-raised p-3">
      <h2 className="text-heading">Add to the report</h2>
      <div className="relative">
        <label htmlFor={searchId} className="sr-only">Search measures and widgets</label>
        <Search aria-hidden className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-ink-muted" />
        <Input id={searchId} type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search measures and widgets" className="pl-8" />
      </div>
      {sources.length > 1 && (
        <NativeSelect aria-label="Data source" value={connectionId ?? ""} onChange={(e) => onConnection(e.target.value)}>
          {sources.map((s) => <option key={s.connection_id} value={s.connection_id}>{s.connection_name}</option>)}
        </NativeSelect>
      )}

      <section aria-labelledby={`${searchId}-m`} className="grid gap-1.5">
        <h3 id={`${searchId}-m`} className="text-small font-semibold uppercase tracking-wide text-ink-muted">Certified measures</h3>
        {catalog.isLoading && <Skeleton className="h-20" />}
        {catalog.error && <p className="text-small text-ink-muted">{catalog.error.message}</p>}
        {!connectionId && !home.isLoading && <p className="text-small text-ink-muted">No data source in your scope.</p>}
        {catalog.data && measures.length === 0 && <p className="text-small text-ink-muted">{q ? "No measure matches." : "No certified measures yet."}</p>}
        <ul className="grid gap-1">
          {measures.map((m) => (
            <Entry key={m.ref} disabled={busy} title={m.plain_rule ?? undefined}
              entry={{ kind: "measure", ref: m.ref, title: m.label, connectionId: catalog.data!.connection_id }}
              onAdd={onAdd} badge={<ContentBadge badge="certified" />} />
          ))}
        </ul>
      </section>

      <section aria-labelledby={`${searchId}-w`} className="grid gap-1.5">
        <h3 id={`${searchId}-w`} className="text-small font-semibold uppercase tracking-wide text-ink-muted">My widgets</h3>
        {lib.isLoading && <Skeleton className="h-20" />}
        {lib.data && widgets.length === 0 && <p className="text-small text-ink-muted">{q ? "No widget matches." : "Pin an answer from Ask to start your library."}</p>}
        <ul className="grid gap-1">
          {widgets.map((w) => {
            const on = onWidgets.has(w.id);
            return (
              <Entry key={w.id} disabled={busy || on} note={on ? "On this report" : VISUAL_NAMES[w.visual] ?? w.visual}
                entry={{ kind: "widget", id: w.id, title: w.title, visual: w.visual, badges: w.badges }} onAdd={onAdd}
                badge={w.badges[0] && w.badges[0] !== "certified" ? <ContentBadge badge={w.badges[0]} /> : undefined} />
            );
          })}
        </ul>
      </section>

      <Button asChild variant="secondary"><Link to={`/ask?report=${reportId}`}><MessageSquarePlus aria-hidden />Ask for a new widget</Link></Button>
      <p className="text-small text-ink-muted">Drag onto the canvas, or select and press Enter to add at the end.</p>
    </aside>
  );
}

function Entry({ entry, onAdd, disabled, badge, note, title }: {
  entry: LibraryEntry; onAdd: (e: LibraryEntry) => void; disabled: boolean; badge?: React.ReactNode; note?: string; title?: string;
}) {
  const { setNodeRef, listeners, attributes, isDragging } = useDraggable({ id: entryKey(entry), data: { entry }, disabled });
  // Pointer drag only: the keyboard path is Enter, which adds at the end (dnd-kit's keyboard sensor would take Enter).
  return (
    <li>
      <button ref={setNodeRef} type="button" disabled={disabled} title={title} onPointerDown={listeners?.onPointerDown as React.PointerEventHandler<HTMLButtonElement> | undefined}
        aria-roledescription={attributes["aria-roledescription"]} aria-describedby={attributes["aria-describedby"]}
        aria-label={`Add ${entry.title}${note ? ` (${note})` : ""}`} onClick={() => onAdd(entry)}
        className={cn("flex w-full touch-none items-center gap-2 rounded-md border border-line bg-surface px-2 py-1.5 text-left hover:border-line-strong focus-visible:focus-ring disabled:cursor-not-allowed disabled:opacity-60",
          isDragging && "opacity-40")}>
        <GripVertical aria-hidden className="size-4 shrink-0 cursor-grab text-ink-muted" />
        <span className="grid min-w-0 flex-1">
          <span className="truncate">{entry.title}</span>
          {note && <span className="truncate text-small text-ink-muted">{note}</span>}
        </span>
        {badge}
      </button>
    </li>
  );
}

/** What follows the pointer while an entry is dragged. */
export function EntryOverlay({ entry }: { entry: LibraryEntry }) {
  return (
    <div className="flex w-60 items-center gap-2 rounded-md border border-accent bg-surface-raised px-2 py-1.5 shadow-drag">
      <GripVertical aria-hidden className="size-4 text-ink-muted" />
      <span className="truncate">{entry.title}</span>
    </div>
  );
}
