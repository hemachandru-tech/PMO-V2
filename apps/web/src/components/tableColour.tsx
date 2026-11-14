import { CircleAlert, CircleCheck, OctagonAlert } from "lucide-react";
import type { ReactNode } from "react";

import { cn } from "../lib/utils";
import { rampStep } from "./HeatGrid";

export type Tones = Record<string, "good" | "warning" | "critical" | "neutral">;

const BADGE = {
  good: { icon: CircleCheck, className: "bg-status-ok-soft", iconClass: "text-status-ok" },
  warning: { icon: CircleAlert, className: "bg-status-warn-soft", iconClass: "text-status-warn" },
  critical: { icon: OctagonAlert, className: "bg-status-danger-soft", iconClass: "text-status-danger" },
} as const;

/** Lowest and highest number of each column (presentation only), for the heat tint of a table in colour. */
export function columnRanges(rows: unknown[][], indexes: number[]): Map<number, [number, number]> {
  const out = new Map<number, [number, number]>();
  for (const j of indexes) {
    const nums = rows.map((r) => r[j]).filter((v): v is number => typeof v === "number");
    if (nums.length > 1) out.set(j, [Math.min(...nums), Math.max(...nums)]);
  }
  return out;
}

/**
 * A figure in a table in colour (ENH-020): a single-hue tint by its size within the column, the number on a plain
 * chip so it reads the same on every step (as the heat grid).
 */
export function HeatCell({ value, range, children }: { value: unknown; range?: [number, number]; children: ReactNode }) {
  if (typeof value !== "number" || !range) return <>{children}</>;
  const step = rampStep(value, range[0], range[1]);
  return (
    <span className="inline-flex rounded-sm py-0.5 pl-3 pr-0.5" style={{ backgroundColor: `var(--tl-seq-${step})` }} data-step={step}>
      <span className="rounded-sm bg-surface-raised px-1">{children}</span>
    </span>
  );
}

/** A status word in a table in colour (ENH-020): a RAG badge with an icon, so colour is never the only signal. */
export function ToneCell({ text, tone }: { text: string; tone: Tones[string] | undefined }) {
  if (!tone || tone === "neutral") return <>{text}</>;
  const b = BADGE[tone];
  const Icon = b.icon;
  return (
    <span className={cn("inline-flex items-center gap-1 rounded-sm px-1.5 py-0.5 text-ink", b.className)} data-tone={tone}>
      <Icon aria-hidden className={cn("size-3.5", b.iconClass)} />{text}
    </span>
  );
}
