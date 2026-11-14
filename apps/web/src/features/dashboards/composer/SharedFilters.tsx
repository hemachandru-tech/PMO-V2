import { Plus, X } from "lucide-react";
import { useState } from "react";

import type { DashboardDetail, SharedFilter } from "../../../api/authoring";
import { Button } from "../../../components/ui/button";
import { Command, CommandEmpty, CommandInput, CommandItem, CommandList } from "../../../components/ui/command";
import { Popover, PopoverContent, PopoverTrigger } from "../../../components/ui/popover";
import { StatusBadge } from "../../../components/ui/status-badge";

/**
 * Shared filters with reach (FEAT-038; RULE-024 check 4): how many widgets each filter reaches, why a widget is not
 * reached, and Exclude (the widget then says "Not affected by …") or Include again. Unreached widgets block publishing
 * until they are excluded.
 */
export function SharedFilters({ dashboard: d, onChange }: { dashboard: DashboardDetail; onChange: (f: SharedFilter[], label: string) => void }) {
  const edit = d.can_edit;
  const setExcluded = (ref: string, widgetId: string, on: boolean) => onChange(d.shared_filters.map((x) => {
    if (x.ref !== ref) return x;
    const ids = x.exclude_widget_ids ?? [];
    return { ...x, exclude_widget_ids: on ? [...ids, widgetId] : ids.filter((w) => w !== widgetId) };
  }), on ? "Excluded the widget from the filter" : "The filter applies to the widget again");
  const unused = d.available_filters.filter((f) => !d.shared_filters.some((x) => x.ref === f.ref));

  return (
    <section aria-labelledby="filters-heading" className="grid min-w-0 gap-3">
      <div className="flex items-center justify-between gap-2">
        <h2 id="filters-heading" className="text-heading">Shared filters</h2>
        {edit && <AddFilter options={unused} onAdd={(f) => onChange([...d.shared_filters, { ref: f.ref }], `Added the ${f.label} filter`)} />}
      </div>
      {d.reach.length === 0 && (
        <p className="text-small text-ink-muted">
          {d.reports.length === 0 ? "Add a report first; filters come from its model." : "No shared filters. Readers can still filter each report."}
        </p>
      )}
      <ul className="grid gap-2">
        {d.reach.map((f) => {
          const total = f.widgets.length;
          const reached = f.widgets.filter((w) => w.reached).length;
          const missing = f.widgets.filter((w) => !w.reached);
          return (
            <li key={f.ref} className="grid gap-1.5 rounded-md border border-line px-3 py-2">
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-medium">{f.label}</span>
                {reached === total
                  ? <StatusBadge status="ok">Reaches all {total} {total === 1 ? "widget" : "widgets"}</StatusBadge>
                  : <StatusBadge status={f.reaches_all ? "ok" : "warn"}>Reaches {reached} of {total}</StatusBadge>}
                {edit && (
                  <Button variant="ghost" size="icon" className="ml-auto size-control-sm" aria-label={`Remove the ${f.label} filter`}
                    onClick={() => onChange(d.shared_filters.filter((x) => x.ref !== f.ref), `Removed the ${f.label} filter`)}>
                    <X aria-hidden />
                  </Button>
                )}
              </div>
              {missing.length > 0 && (
                <ul className="grid gap-1 text-small">
                  {missing.map((w) => (
                    <li key={w.widget_id} className="flex flex-wrap items-baseline gap-x-2">
                      <span className="text-ink-muted">
                        {w.excluded ? <>Excluded: “{w.title}” will say “Not affected by {f.label}”.</>
                          : <>Not reached: “{w.title}” ({w.report}) has no link to {f.label}. Exclude it to publish.</>}
                      </span>
                      {edit && (w.excluded
                        ? <Button variant="link" size="sm" onClick={() => setExcluded(f.ref, w.widget_id, false)}>Include again</Button>
                        : <Button variant="link" size="sm" onClick={() => setExcluded(f.ref, w.widget_id, true)}>Exclude widget</Button>)}
                    </li>
                  ))}
                </ul>
              )}
            </li>
          );
        })}
      </ul>
      {d.connections.length > 0 && <p className="text-small text-ink-muted">Sources: {d.connections.map((c) => c.name).join(", ")}</p>}
    </section>
  );
}

function AddFilter({ options, onAdd }: { options: DashboardDetail["available_filters"]; onAdd: (f: DashboardDetail["available_filters"][number]) => void }) {
  const [open, setOpen] = useState(false);
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button variant="ghost" size="sm" disabled={options.length === 0}><Plus aria-hidden />Add filter</Button>
      </PopoverTrigger>
      <PopoverContent align="end" className="w-72 p-0">
        <Command>
          <CommandInput placeholder="Find a dimension" />
          <CommandList>
            <CommandEmpty>No dimension matches.</CommandEmpty>
            {options.map((f) => <CommandItem key={f.ref} value={`${f.label} ${f.ref}`} onSelect={() => { setOpen(false); onAdd(f); }}>{f.label}</CommandItem>)}
          </CommandList>
        </Command>
      </PopoverContent>
    </Popover>
  );
}
