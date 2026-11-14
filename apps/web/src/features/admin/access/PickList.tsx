import { useId, useState } from "react";

import { Button } from "../../../components/ui/button";
import { Checkbox } from "../../../components/ui/checkbox";
import { Input } from "../../../components/ui/input";
import { cn } from "../../../lib/utils";

export interface PickItem {
  value: string;
  label: string;
  hint?: string;
}

/**
 * A ticked list for choosing several items at once (US-172): search past eight items, "Select all shown" and "Clear".
 * A fieldset with a legend, so screen readers announce the group name with each checkbox.
 */
export function PickList({ label, items, value, onChange, invalid, describedBy, className }: {
  label: string;
  items: PickItem[];
  value: string[];
  onChange: (value: string[]) => void;
  invalid?: boolean;
  describedBy?: string;
  className?: string;
}) {
  const id = useId();
  const [query, setQuery] = useState("");
  const q = query.trim().toLowerCase();
  const shown = q ? items.filter((i) => `${i.label} ${i.hint ?? ""} ${i.value}`.toLowerCase().includes(q)) : items;
  const chosen = new Set(value);
  // Keep the order of `items`, whatever order things were ticked in.
  const set = (next: Set<string>) => onChange(items.map((i) => i.value).filter((v) => next.has(v)));
  const toggle = (v: string, on: boolean) => {
    const next = new Set(chosen);
    if (on) next.add(v);
    else next.delete(v);
    set(next);
  };
  return (
    <fieldset className={cn("grid min-w-0 gap-2", className)} aria-describedby={describedBy} aria-invalid={invalid || undefined}>
      <legend className="text-body-strong">{label}</legend>
      {items.length > 8 && (
        <Input type="search" value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search…"
          aria-label={`Search ${label.toLowerCase()}`} />
      )}
      <div className="flex flex-wrap gap-3">
        <Button type="button" variant="link" size="sm" disabled={!shown.length || shown.every((i) => chosen.has(i.value))}
          onClick={() => set(new Set([...chosen, ...shown.map((i) => i.value)]))}>
          {q ? `Select all ${shown.length} shown` : "Select all"}
        </Button>
        <Button type="button" variant="link" size="sm" disabled={!value.length} onClick={() => set(new Set())}>Clear</Button>
        <span className="ml-auto self-center text-small text-ink-muted" aria-live="polite">{value.length} of {items.length} chosen</span>
      </div>
      <ul className={cn("max-h-56 overflow-y-auto rounded-md border bg-surface-raised p-1", invalid ? "border-status-danger" : "border-line")}>
        {shown.map((i) => (
          <li key={i.value}>
            <label htmlFor={`${id}-${i.value}`} className="flex cursor-pointer items-center gap-2 rounded-sm px-2 py-1.5 hover:bg-surface-sunken">
              <Checkbox id={`${id}-${i.value}`} checked={chosen.has(i.value)} onCheckedChange={(v) => toggle(i.value, v === true)} />
              <span className="min-w-0 truncate">{i.label}</span>
              {i.hint && <span className="min-w-0 truncate text-small text-ink-muted">{i.hint}</span>}
            </label>
          </li>
        ))}
        {!shown.length && <li className="px-2 py-1.5 text-small text-ink-muted">{items.length ? "Nothing matches." : "Nothing to choose yet."}</li>}
      </ul>
    </fieldset>
  );
}
