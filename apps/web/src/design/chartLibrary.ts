import { useSyncExternalStore } from "react";

/** Which library draws the standard charts (ENH-018, US-206): ECharts, shadcn/ui Charts on Recharts, Observable Plot. */
export type ChartLibrary = "echarts" | "recharts" | "plot";

export const CHART_LIBRARIES: { id: ChartLibrary; label: string; note: string }[] = [
  { id: "echarts", label: "ECharts", note: "Rich and interactive, with zoom-friendly canvas drawing. Best for large result sets." },
  { id: "recharts", label: "Modern (shadcn/ui)", note: "Clean, minimal charts in the same style as the rest of the app." },
  { id: "plot", label: "Observable Plot", note: "Plain, analytical charts in the style of the Observable D3 gallery." },
];

let active: ChartLibrary = "echarts";
const listeners = new Set<() => void>();

const known = (v: unknown): v is ChartLibrary => CHART_LIBRARIES.some((l) => l.id === v);

/** Applies the tenant's choice for the whole page; anything unknown gives ECharts. Charts follow through the hook. */
export function applyChartLibrary(library: string | null | undefined): void {
  const next = known(library) ? library : "echarts";
  if (next === active) return;
  active = next;
  listeners.forEach((l) => l());
}

export function useChartLibrary(): ChartLibrary {
  return useSyncExternalStore(
    (l) => {
      listeners.add(l);
      return () => listeners.delete(l);
    },
    () => active,
  );
}

export function activeChartLibrary(): ChartLibrary {
  return active;
}
