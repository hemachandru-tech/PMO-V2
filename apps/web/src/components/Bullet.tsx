import { CircleAlert, CircleCheck, OctagonAlert } from "lucide-react";

import { cn } from "../lib/utils";
import { formatValue } from "./format";

type Band = "good" | "warning" | "critical";

export interface BulletFigures {
  rows: { label: string; value: number | null; band: Band }[];
  warning: number | null;
  critical: number | null;
  higher_is_worse: boolean;
  max: number;
  target: number | null;
}

const BAND = {
  good: { icon: CircleCheck, className: "text-status-ok", soft: "bg-status-ok-soft", word: "Good" },
  warning: { icon: CircleAlert, className: "text-status-warn", soft: "bg-status-warn-soft", word: "Warning" },
  critical: { icon: OctagonAlert, className: "text-status-danger", soft: "bg-status-danger-soft", word: "Critical" },
} as const;

type Edges = Pick<BulletFigures, "warning" | "critical" | "higher_is_worse" | "max">;

/** The band of a value: at or past critical, at or past warning, else good (the server's rule). */
export function bandAt(v: number, f: Edges): Band {
  const past = (t: number | null) => t != null && (f.higher_is_worse ? v >= t : v <= t);
  if (past(f.critical)) return "critical";
  if (past(f.warning)) return "warning";
  return "good";
}

/** The qualitative bands along the axis, as [band, from, to] in axis units (presentation of the server's edges). */
export function bulletBands(f: Edges): [Band, number, number][] {
  const cuts = [...new Set([0, f.warning, f.critical, f.max].filter((v): v is number => v != null && v >= 0 && v <= f.max))].sort((a, b) => a - b);
  return cuts.slice(1).map((to, i) => [bandAt((cuts[i]! + to) / 2, f), cuts[i]!, to]);
}

/**
 * Bullet (US-187): each item's value as a bar over its target bands, the target as a tick, and the band in words
 * with an icon, so colour is never the only signal. Bands and target come from the server (COMP-001).
 */
export function Bullet({ figures, format, title }: { figures: BulletFigures; format?: string | null; title?: string }) {
  const pct = (v: number) => `${Math.max(0, Math.min(100, (v / (figures.max || 1)) * 100))}%`;
  const bands = bulletBands(figures);
  return (
    <div className="grid gap-2" data-chart="bullet">
      {figures.target !== null && (
        <p className="text-small text-ink-muted">Target {formatValue(figures.target, format)}{figures.critical !== null && figures.critical !== figures.target ? ` · critical ${formatValue(figures.critical, format)}` : ""}</p>
      )}
      <ul className="grid gap-2" aria-label={title ?? "Against target"}>
      {figures.rows.map((r) => {
        const band = BAND[r.band];
        const Icon = band.icon;
        return (
          <li key={r.label} className="grid grid-cols-[1fr_auto] items-center gap-x-3 gap-y-0.5"
            aria-label={`${r.label}: ${formatValue(r.value, format)}, ${band.word.toLowerCase()}`}>
            <span className="col-span-2 truncate text-small text-ink" title={r.label}>{r.label}</span>
            <div className="relative h-5 overflow-hidden rounded-sm border border-line" aria-hidden>
              {bands.map(([b, from, to]) => (
                <div key={b + from} className={cn("absolute inset-y-0", BAND[b].soft)} style={{ left: pct(from), width: `calc(${pct(to)} - ${pct(from)})` }} data-band={b} />
              ))}
              <div className="absolute inset-y-1.5 left-0 rounded-sm bg-ink" style={{ width: pct(r.value ?? 0) }} />
              {figures.target !== null && <div className="absolute inset-y-0 w-0.5 bg-ink" style={{ left: pct(figures.target) }} data-target="" />}
            </div>
            <span className={cn("flex items-center gap-1 font-mono text-small tabular-nums", band.className)}>
              <Icon className="size-3.5" aria-hidden />
              <span className="text-ink">{formatValue(r.value, format)}</span>
            </span>
          </li>
        );
      })}
      </ul>
    </div>
  );
}
