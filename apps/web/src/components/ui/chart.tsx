import { forwardRef, type ComponentPropsWithoutRef, type CSSProperties, type ReactNode } from "react";

import { cn } from "@/lib/utils";

/**
 * shadcn/ui Charts (ENH-018, US-207), trimmed for Tela: the container names each series' colour as `--color-<key>`,
 * and the tooltip and legend look like the rest of the app. shadcn's own container writes those variables in a
 * <style> tag, which the strict CSP (`style-src 'self'`) blocks; here they are set on the element instead.
 */
export type ChartConfig = Record<string, { label: string; color: string }>;

export const ChartContainer = forwardRef<HTMLDivElement, { config: ChartConfig; children: ReactNode } & ComponentPropsWithoutRef<"div">>(
  function ChartContainer({ config, className, children, ...props }, ref) {
    const vars = Object.fromEntries(Object.entries(config).map(([key, v]) => [`--color-${key}`, v.color])) as CSSProperties;
    return (
      <div ref={ref} data-slot="chart" className={cn("w-full text-small [&_.recharts-surface]:outline-none [&_.recharts-wrapper]:outline-none", className)} style={vars} {...props}>
        {children}
      </div>
    );
  },
);

export interface ChartTooltipItem {
  key: string;
  label: string;
  value: string;
  color: string;
}

/** The tooltip body: the category on top, one line per series with its colour, label and formatted value. */
export function ChartTooltipContent({ title, items }: { title?: string; items: ChartTooltipItem[] }) {
  if (items.length === 0) return null;
  return (
    <div className="grid min-w-32 gap-1.5 rounded-md border border-line bg-surface-raised px-3 py-2 text-small shadow-overlay">
      {title && <span className="font-medium text-ink">{title}</span>}
      {items.map((i) => (
        <div key={i.key} className="flex items-center gap-2">
          <span className="size-2.5 shrink-0 rounded-[2px]" style={{ background: i.color }} />
          <span className="text-ink-muted">{i.label}</span>
          <span className="ml-auto pl-3 font-mono tabular-nums text-ink">{i.value}</span>
        </div>
      ))}
    </div>
  );
}

export function ChartLegendContent({ config }: { config: ChartConfig }) {
  return (
    <div className="flex flex-wrap items-center justify-center gap-x-4 gap-y-1 pt-2 text-small text-ink-muted" aria-hidden>
      {Object.entries(config).map(([key, v]) => (
        <span key={key} className="flex items-center gap-1.5">
          <span className="size-2.5 shrink-0 rounded-[2px]" style={{ background: v.color }} />
          {v.label}
        </span>
      ))}
    </div>
  );
}
