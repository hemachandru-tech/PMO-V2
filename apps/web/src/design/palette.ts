import { useSyncExternalStore } from "react";

import type { ResolvedTheme } from "./theme";

export type PaletteTokens = Record<ResolvedTheme, Record<string, string>>;

interface Active {
  key: string;
  tokens: PaletteTokens | null;
}

let active: Active = { key: "tela", tokens: null };
let sheet: CSSStyleSheet | null = null;
const listeners = new Set<() => void>();

const NAME = /^[a-z0-9-]+$/;
const VALUE = /^#[0-9a-f]{6}([0-9a-f]{2})?$/i;

// The focus ring is a shadow with the focus colour inside it (tokens.css): rebuilt here on each theme's raised surface.
const RING_GAP: Record<ResolvedTheme, string> = { light: "#ffffff", dark: "#161e23" };

function block(selector: string, tokens: Record<string, string>, theme: ResolvedTheme): string {
  // Only token names and hex colours reach the sheet: the palette comes from the API, but CSS is no place for surprises.
  const safe = Object.entries(tokens).filter(([k, v]) => NAME.test(k) && VALUE.test(v));
  const body = safe.map(([k, v]) => `--tl-${k}: ${v};`);
  const focus = safe.find(([k]) => k === "focus")?.[1];
  if (focus) body.push(`--tl-focus-ring: 0 0 0 2px ${RING_GAP[theme]}, 0 0 0 4px ${focus};`);
  return `${selector} { ${body.join(" ")} }`;
}

/** The palette's `--tl-*` overrides for both themes, mirroring the selectors of tokens.css (incl. system dark). */
export function paletteCss(tokens: PaletteTokens): string {
  return [
    block(':root, [data-theme="light"]', tokens.light, "light"),
    block('[data-theme="dark"]', tokens.dark, "dark"),
    `@media (prefers-color-scheme: dark) { ${block(":root:not([data-theme])", tokens.dark, "dark")} }`,
  ].join("\n");
}

/**
 * Applies the tenant's palette (ENH-006, US-151) on top of tokens.css, for the whole page. A constructable stylesheet,
 * not a <style> tag, so the strict CSP (`style-src 'self'`) allows it; it comes after the document's sheets, so it wins
 * over tokens.css at the same specificity. `null` goes back to the Tela colours. Charts follow through `usePaletteKey`.
 */
export function applyPalette(tokens: PaletteTokens | null, key: string): void {
  const next = tokens ? key : "tela";
  if (next === active.key) return;
  active = { key: next, tokens };
  if (typeof document !== "undefined" && "adoptedStyleSheets" in document && typeof CSSStyleSheet !== "undefined") {
    try {
      sheet ??= new CSSStyleSheet();
      sheet.replaceSync(tokens ? paletteCss(tokens) : "");
      if (!document.adoptedStyleSheets.includes(sheet)) document.adoptedStyleSheets = [...document.adoptedStyleSheets, sheet];
    } catch {
      // A browser without constructable stylesheets keeps the Tela colours.
    }
  }
  listeners.forEach((l) => l());
}

/** A palette colour for a theme, if the active palette sets that token. */
export function paletteColor(name: string, theme: ResolvedTheme): string | undefined {
  return active.tokens?.[theme][name];
}

/** Changes whenever another palette is applied; charts use it to rebuild their theme. */
export function usePaletteKey(): string {
  return useSyncExternalStore(
    (l) => {
      listeners.add(l);
      return () => listeners.delete(l);
    },
    () => active.key,
  );
}

export function activePaletteKey(): string {
  return active.key;
}
