import { X } from "lucide-react";
import type { ComponentProps } from "react";

import { cn } from "@/lib/utils";

/**
 * Filter chip (design system: FilterChip): a pill showing one filter. Applied chips use the accent-soft fill and can
 * be cleared; the label names both the field and the value so it reads without colour.
 */
export function FilterChip({
  field,
  value,
  applied = false,
  onClear,
  className,
  ...props
}: ComponentProps<"button"> & { field: string; value?: string; applied?: boolean; onClear?: () => void }) {
  return (
    <span
      data-slot="filter-chip"
      className={cn(
        "inline-flex h-control-sm items-center rounded-full border text-small",
        applied ? "border-accent bg-accent-soft text-ink" : "border-line-strong bg-surface-raised text-ink-muted",
        className,
      )}
    >
      <button type="button" className="h-full rounded-full px-3 focus-visible:focus-ring" aria-pressed={applied} {...props}>
        <span className={applied ? "text-ink-muted" : undefined}>{field}</span>
        {value && <span className="font-medium text-ink">: {value}</span>}
      </button>
      {applied && onClear && (
        <button type="button" onClick={onClear} aria-label={`Clear ${field} filter`} className="-ml-1 mr-1 rounded-full p-1 text-ink-muted hover:text-ink focus-visible:focus-ring">
          <X className="size-3" />
        </button>
      )}
    </span>
  );
}
