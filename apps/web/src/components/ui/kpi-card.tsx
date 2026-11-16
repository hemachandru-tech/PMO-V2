import { ArrowDownRight, ArrowRight, ArrowUpRight, CircleAlert, CircleCheck, OctagonAlert } from "lucide-react";
import type { ComponentProps, ReactNode } from "react";

import { cn } from "@/lib/utils";
import { Card } from "./card";
import { Skeleton } from "./skeleton";

export interface KpiDelta {
  /** Text as returned by the server, e.g. "+4 vs last month" (US-050). */
  text: string;
  direction: "up" | "down" | "flat";
  /** Whether the change is good news; colours the delta. Omit when it is neither. */
  good?: boolean;
}

export interface KpiTone {
  tone: "good" | "warning" | "critical";
  /** "Critical" — always shown as a word with its icon, never colour alone. */
  label: string;
  /** The threshold in words, e.g. "Critical at or above 10". */
  rule: string;
}

const TONES = {
  good: { icon: CircleCheck, text: "bg-status-ok-soft text-status-ok", edge: "border-l-status-ok" },
  warning: { icon: CircleAlert, text: "bg-status-warn-soft text-status-warn", edge: "border-l-status-warn" },
  critical: { icon: OctagonAlert, text: "bg-status-danger-soft text-status-danger", edge: "border-l-status-danger" },
} as const;

/**
 * KPI card (design system: KpiCard). Shows a value, delta and badges exactly as the widget run API returns them;
 * the web app never computes figures (COMP-001).
 */
export function KpiCard({
  label,
  value,
  delta,
  badges,
  footer,
  onValueClick,
  valueTitle = "Show how this figure is defined",
  tone,
  definition,
  filters,
  loading = false,
  className,
  ...props
}: Omit<ComponentProps<"div">, "children"> & {
  label: string;
  value: string;
  delta?: KpiDelta;
  badges?: ReactNode;
  footer?: ReactNode;
  onValueClick?: () => void;
  /** What clicking the value does, as a tooltip. */
  valueTitle?: string;
  /** Good / warning / critical from the widget's threshold (US-157). */
  tone?: KpiTone | null;
  /** The measure's one-line definition, under the figure. */
  definition?: string | null;
  /** The widget's own filters (ENH-023), as a compact control beside the name. */
  filters?: ReactNode;
  /** The figure is still loading: a placeholder where the value goes, same card. */
  loading?: boolean;
  className?: string;
}) {
  const Arrow = delta?.direction === "up" ? ArrowUpRight : delta?.direction === "down" ? ArrowDownRight : ArrowRight;
  const deltaTone = delta?.good === undefined ? "text-ink-muted" : delta.good ? "text-status-ok" : "text-status-danger";
  return (
    <Card data-slot="kpi-card" data-tone={tone?.tone} className={cn("h-full gap-2 p-4", tone && cn("border-l-4", TONES[tone.tone].edge), className)} {...props}>
      {/* One line: the name (cut short, in full on hover), then filters, badges and the definition as icons. */}
      <div data-kpi-head className="flex min-h-control-sm min-w-0 items-center gap-2">
        <span data-kpi-label className="min-w-0 flex-1 truncate text-label uppercase text-ink-muted" title={label}>{label}</span>
        {(filters || badges) && <span className="flex shrink-0 items-center gap-0.5">{filters}{badges}</span>}
      </div>
      {/* The figure always sits right under the name, so figures in a row line up. */}
      {loading ? (
        <Skeleton className="h-10 w-28" aria-label="Loading" />
      ) : onValueClick ? (
        <button type="button" onClick={onValueClick} className="w-fit rounded-sm text-left font-mono text-figure-xl tabular-nums text-ink hover:text-accent focus-visible:focus-ring" title={valueTitle}>
          {value}
        </button>
      ) : (
        <span className="font-mono text-figure-xl tabular-nums text-ink">{value}</span>
      )}
      {tone && <ToneBadge tone={tone} />}
      {delta && (
        <span data-delta-pill className={cn("inline-flex items-center gap-1 text-small", deltaTone)}>
          <Arrow aria-hidden className="size-3.5" />
          {delta.text}
        </span>
      )}
      {definition && <p className="line-clamp-2 text-small text-ink-muted" title={definition}>{definition}</p>}
      {footer && <div className="mt-auto text-small text-ink-muted">{footer}</div>}
    </Card>
  );
}

function ToneBadge({ tone }: { tone: KpiTone }) {
  const t = TONES[tone.tone];
  const Icon = t.icon;
  return (
    <span className={cn("inline-flex w-fit items-center gap-1 rounded-sm px-1.5 py-0.5 text-small font-medium", t.text)} title={tone.rule}>
      <Icon aria-hidden className="size-3.5" />
      {tone.label}
      <span className="font-normal">· {tone.rule}</span>
    </span>
  );
}
