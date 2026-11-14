import { useSyncExternalStore } from "react";

/**
 * How the sidebar and the top bar take the brand's colours (ENH-019, US-209): the neutral surfaces, a wash of the
 * accent, filled with the brand colour, or dark. The colours come from the tenant's palette (US-150), so a new palette
 * recolours the bars too. `index.css` turns `data-nav` into colours; dark is drawn in the dark theme (`ThemeScope`).
 */
export type NavStyle = "standard" | "tinted" | "brand" | "dark";
export type NavPlace = "sidebar" | "topbar";
export type NavColors = Record<NavPlace, NavStyle>;

export const NAV_STYLES: { id: NavStyle; label: string; note: string }[] = [
  { id: "standard", label: "Standard", note: "Neutral greys; the brand colour marks only the current screen." },
  { id: "tinted", label: "Tinted", note: "A light wash of the brand's accent colour." },
  { id: "brand", label: "Brand", note: "Filled with the brand's deep colour, with light text. A softer brand tint in dark mode." },
  { id: "dark", label: "Dark", note: "Always dark, whatever theme people use." },
];

export const NAV_PLACES: { id: NavPlace; label: string }[] = [
  { id: "sidebar", label: "Sidebar" },
  { id: "topbar", label: "Top bar" },
];

export const DEFAULT_NAV_COLORS: NavColors = { sidebar: "standard", topbar: "standard" };

let active: NavColors = DEFAULT_NAV_COLORS;
const listeners = new Set<() => void>();

const known = (v: unknown): v is NavStyle => NAV_STYLES.some((s) => s.id === v);

/** The colours to use: anything unknown is standard. */
export function navColors(value: Partial<Record<string, unknown>> | null | undefined): NavColors {
  return {
    sidebar: known(value?.sidebar) ? value.sidebar : "standard",
    topbar: known(value?.topbar) ? value.topbar : "standard",
  };
}

/** Applies the tenant's choice for the whole page; the bars follow through the hook. */
export function applyNavColors(value: Partial<Record<string, unknown>> | null | undefined): void {
  const next = navColors(value);
  if (next.sidebar === active.sidebar && next.topbar === active.topbar) return;
  active = next;
  listeners.forEach((l) => l());
}

export function useNavColors(): NavColors {
  return useSyncExternalStore(
    (l) => {
      listeners.add(l);
      return () => listeners.delete(l);
    },
    () => active,
  );
}
