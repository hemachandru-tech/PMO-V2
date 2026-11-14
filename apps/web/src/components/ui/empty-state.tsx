import type { LucideIcon } from "lucide-react";
import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

/** Empty state (design system: EmptyState): what is missing, why, and the one next step. */
export function EmptyState({
  icon: Icon,
  title,
  children,
  action,
  className,
}: {
  icon?: LucideIcon;
  title: string;
  children?: ReactNode;
  action?: ReactNode;
  className?: string;
}) {
  return (
    <div data-slot="empty-state" className={cn("flex flex-col items-center gap-2 rounded-lg border border-dashed border-line px-6 py-10 text-center", className)}>
      {Icon && (
        <span className="mb-1 flex size-10 items-center justify-center rounded-full bg-surface-sunken text-ink-muted">
          <Icon aria-hidden className="size-5" />
        </span>
      )}
      <p className="text-heading text-ink">{title}</p>
      {children && <div className="max-w-sm text-body text-ink-muted">{children}</div>}
      {action && <div className="mt-2">{action}</div>}
    </div>
  );
}
