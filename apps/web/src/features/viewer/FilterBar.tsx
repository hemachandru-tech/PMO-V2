import { useQuery } from "@tanstack/react-query";
import { ChevronDown, SlidersHorizontal, X } from "lucide-react";
import { useId, useMemo, useState } from "react";

import { api } from "../../api/client";
import type { PageFilter } from "../../api/app";
import { Button } from "../../components/ui/button";
import { Checkbox } from "../../components/ui/checkbox";
import { Input } from "../../components/ui/input";
import { Popover, PopoverContent, PopoverTrigger } from "../../components/ui/popover";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "../../components/ui/sheet";
import { Skeleton } from "../../components/ui/skeleton";
import { cn } from "../../lib/utils";

export interface FilterDef {
  ref: string;
  label: string;
  connectionId?: string | null;
  modelVersionId?: string | null;
}

/** Chips shown in the bar; the rest sit in the "More filters" sheet (all of them are there too). */
const INLINE = 5;

/**
 * Dashboard filter bar (FEAT-042; US-139; design system: FilterChip): one chip per shared filter with a searchable
 * value list, "More filters" in a sheet, and Reset. Values come from the API in the reader's scope. Filters can narrow
 * the locked scope but never widen it; the API applies them.
 */
export function FilterBar({ filters, value, onChange }: { filters: FilterDef[]; value: PageFilter[]; onChange: (next: PageFilter[]) => void }) {
  const [more, setMore] = useState(false);
  if (filters.length === 0) return null;
  const selected = (ref: string) => value.find((f) => f.ref === ref)?.values.map(String) ?? [];
  const set = (ref: string, values: string[]) =>
    onChange([...value.filter((f) => f.ref !== ref), ...(values.length ? [{ ref, op: values.length === 1 ? "=" as const : "in" as const, values }] : [])]);
  return (
    <div role="group" aria-label="Filters" className="flex flex-wrap items-center gap-2">
      {filters.slice(0, INLINE).map((f) => (
        <FilterChipPicker key={f.ref} filter={f} selected={selected(f.ref)} onChange={(v) => set(f.ref, v)} />
      ))}
      {filters.length > INLINE && (
        <Button variant="ghost" size="sm" onClick={() => setMore(true)}>
          <SlidersHorizontal aria-hidden />More filters
          {value.some((v) => filters.slice(INLINE).some((f) => f.ref === v.ref)) && <span className="sr-only">(some applied)</span>}
        </Button>
      )}
      {value.length > 0 && <Button variant="ghost" size="sm" onClick={() => onChange([])}>Reset filters</Button>}
      <Sheet open={more} onOpenChange={setMore}>
        <SheetContent className="overflow-y-auto">
          <SheetHeader>
            <SheetTitle>Filters</SheetTitle>
            <SheetDescription>Every filter on this dashboard. They narrow your data scope; they never widen it.</SheetDescription>
          </SheetHeader>
          <div className="grid gap-6 px-4 pb-6">
            {filters.map((f) => (
              <section key={f.ref} aria-label={f.label} className="grid gap-2">
                <h3 className="text-heading">{f.label}</h3>
                <ValueList filter={f} selected={selected(f.ref)} onChange={(v) => set(f.ref, v)} />
              </section>
            ))}
          </div>
        </SheetContent>
      </Sheet>
    </div>
  );
}

export function FilterChipPicker({ filter, selected, onChange }: { filter: FilterDef; selected: string[]; onChange: (values: string[]) => void }) {
  const applied = selected.length > 0;
  const summary = selected.length === 1 ? selected[0] : `${selected.length} selected`;
  return (
    <Popover>
      <span
        data-slot="filter-chip"
        className={cn(
          "inline-flex h-control-sm items-center rounded-full border text-small",
          applied ? "border-accent bg-accent-soft text-ink" : "border-line-strong bg-surface-raised text-ink-muted",
        )}
      >
        <PopoverTrigger asChild>
          <button type="button" className="inline-flex h-full items-center gap-1 rounded-full pl-3 pr-2 focus-visible:focus-ring"
            aria-label={applied ? `${filter.label}: ${summary}. Change filter` : `Filter by ${filter.label}`}>
            <span className={applied ? "text-ink-muted" : undefined}>{filter.label}</span>
            {applied && <span className="font-medium text-ink">: {summary}</span>}
            <ChevronDown aria-hidden className="size-3.5" />
          </button>
        </PopoverTrigger>
        {applied && (
          <button type="button" onClick={() => onChange([])} aria-label={`Clear ${filter.label} filter`}
            className="mr-1 rounded-full p-1 text-ink-muted hover:text-ink focus-visible:focus-ring"><X aria-hidden className="size-3.5" /></button>
        )}
      </span>
      <PopoverContent align="start" className="w-72 p-3" aria-label={`${filter.label} values`}>
        <ValueList filter={filter} selected={selected} onChange={onChange} />
      </PopoverContent>
    </Popover>
  );
}

export function ValueList({ filter, selected, onChange }: { filter: FilterDef; selected: string[]; onChange: (values: string[]) => void }) {
  const id = useId();
  const [q, setQ] = useState("");
  const values = useQuery({
    queryKey: ["filter-values", filter.connectionId, filter.modelVersionId, filter.ref],
    queryFn: () => api<{ values: unknown[] }>("/api/v1/query/filter-values", {
      method: "POST", body: { connection_id: filter.connectionId, model_version_id: filter.modelVersionId, ref: filter.ref },
    }),
    enabled: !!filter.connectionId && !!filter.modelVersionId,
  });
  const all = useMemo(() => (values.data?.values ?? []).map(String), [values.data]);
  const shown = all.filter((v) => v.toLowerCase().includes(q.trim().toLowerCase()));
  const toggle = (v: string, on: boolean) => onChange(on ? [...selected, v] : selected.filter((x) => x !== v));
  if (values.isLoading) return <div className="grid gap-2"><Skeleton className="h-6" /><Skeleton className="h-6" /></div>;
  if (values.error) return <p className="text-small text-status-danger" role="alert">Values could not be loaded.</p>;
  if (all.length === 0) return <p className="text-small text-ink-muted">No values in your data scope.</p>;
  return (
    <div className="grid gap-2">
      {all.length > 8 && (
        <Input type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder={`Search ${filter.label.toLowerCase()}`}
          aria-label={`Search ${filter.label} values`} className="h-control-sm" />
      )}
      <ul className="grid max-h-64 gap-1 overflow-y-auto" aria-label={`${filter.label} values`}>
        {shown.map((v, i) => (
          <li key={v} className="flex items-center gap-2 rounded-sm px-1 py-0.5 hover:bg-surface-sunken">
            <Checkbox id={`${id}-${i}`} checked={selected.includes(v)} onCheckedChange={(on) => toggle(v, on === true)} />
            <label htmlFor={`${id}-${i}`} className="flex-1 cursor-pointer text-body">{v}</label>
          </li>
        ))}
        {shown.length === 0 && <li className="text-small text-ink-muted">No values match.</li>}
      </ul>
      {selected.length > 0 && <Button variant="link" size="sm" className="justify-self-start" onClick={() => onChange([])}>Clear</Button>}
    </div>
  );
}
