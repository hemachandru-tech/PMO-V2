import { useSyncExternalStore } from "react";

/**
 * The app's layout (ENH-021): Classic (the sidebar with names), Executive (top navigation; dashboards open on a brand
 * band with large figures), Command centre (a slim icon rail; rounded tiles) or Aurora (ENH-027: a floating sidebar
 * with colour chips, a hero band, rounded cards on soft shadows and vivid charts). The company's default comes with the
 * branding (US-212); each person may pick their own in the account menu, saved in their preferences. `index.css`
 * reads `data-layout` on `<html>` for the dashboard looks; the shell picks its frame from `useLayout()`.
 */
export type Layout = "classic" | "executive" | "command" | "aurora";
export type LayoutPreference = "default" | Layout;

export const LAYOUTS: { id: Layout; label: string; note: string }[] = [
  { id: "classic", label: "Classic", note: "A sidebar with names; plain cards." },
  { id: "executive", label: "Executive", note: "Navigation across the top; dashboards open on a brand band with large figures." },
  { id: "command", label: "Command centre", note: "A slim icon rail and rounded tiles with bold figures." },
  { id: "aurora", label: "Aurora", note: "A floating sidebar with colour chips, dashboards on a hero band and vivid charts." },
];

export const DEFAULT_LAYOUT: Layout = "classic";

const known = (v: unknown): v is Layout => LAYOUTS.some((l) => l.id === v);

let tenant: Layout = DEFAULT_LAYOUT;
let own: LayoutPreference = "default";
const listeners = new Set<() => void>();

/** The layout in use: the person's own pick, else the company's default. */
export function resolvedLayout(): Layout {
  return own !== "default" ? own : tenant;
}

function changed(): void {
  if (typeof document !== "undefined") document.documentElement.dataset.layout = resolvedLayout();
  listeners.forEach((l) => l());
}

/** The company's default, from the branding; anything unknown is Classic. */
export function applyTenantLayout(value: unknown): void {
  tenant = known(value) ? value : DEFAULT_LAYOUT;
  changed();
}

/** The signed-in person's own pick ("default" follows the company). */
export function applyUserLayout(value: unknown): void {
  own = known(value) ? value : "default";
  changed();
}

export function userLayout(): LayoutPreference {
  return own;
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function useLayout(): Layout {
  return useSyncExternalStore(subscribe, resolvedLayout, resolvedLayout);
}

export function useUserLayout(): LayoutPreference {
  return useSyncExternalStore(subscribe, userLayout, userLayout);
}
