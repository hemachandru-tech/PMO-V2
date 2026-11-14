import { describe, expect, it } from "vitest";

import tokens from "../design/tokens.json";

/**
 * US-148: contrast on every colour token pair the UI uses, in both themes (WCAG 2.2 AA: 4.5:1 for text, 3:1 for
 * control edges, focus rings, chart marks and axes).
 */
type Theme = "light" | "dark";
const byName = new Map(tokens.color.tokens.map((t) => [t.name, t.value as string | Record<Theme, string>]));

function colour(name: string, theme: Theme): string {
  const v = byName.get(name);
  if (v === undefined) throw new Error(`No token ${name}`);
  if (typeof v === "string") return v.startsWith("{") ? colour(v.slice(1, -1), theme) : v;
  return v[theme];
}

function luminance(hex: string) {
  const [r, g, b] = [0, 2, 4].map((i) => parseInt(hex.slice(1 + i, 3 + i), 16) / 255).map((c) => (c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4));
  return 0.2126 * r! + 0.7152 * g! + 0.0722 * b!;
}

function contrast(a: string, b: string) {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi! + 0.05) / (lo! + 0.05);
}

const SURFACES = ["surface", "surface-raised", "surface-sunken"];
const TEXT: [string, string][] = [
  ...["ink", "ink-muted", "accent", "accent-strong", "link"].flatMap((fg) => SURFACES.map((bg) => [fg, bg] as [string, string])),
  ["on-accent", "accent"], ["on-accent", "accent-strong"], ["on-danger", "status-danger"],
  ["ink", "accent-soft"], ["ink-muted", "accent-soft"], ["accent", "accent-soft"], ["accent-strong", "accent-soft"],
  ...["ok", "warn", "danger"].flatMap((s) => [[`status-${s}`, `status-${s}-soft`], [`status-${s}`, "surface-raised"], ["ink", `status-${s}-soft`]] as [string, string][]),
  ["personal", "personal-soft"], ["personal", "surface-raised"],
];
// Charts are drawn on the raised card surface only.
const NON_TEXT: [string, string][] = [
  ...["line-strong", "focus"].flatMap((fg) => SURFACES.map((bg) => [fg, bg] as [string, string])),
  ["chart-axis", "surface-raised"],
  ...Array.from({ length: 8 }, (_, i) => [`chart-${i + 1}`, "surface-raised"] as [string, string]),
];

describe("token contrast (US-148)", () => {
  for (const theme of ["light", "dark"] as const) {
    it(`text pairs reach 4.5:1 in ${theme}`, () => {
      const failing = TEXT.map(([fg, bg]) => ({ fg, bg, ratio: contrast(colour(fg, theme), colour(bg, theme)) })).filter((p) => p.ratio < 4.5);
      expect(failing).toEqual([]);
    });
    it(`control edges, focus, axes and chart marks reach 3:1 in ${theme}`, () => {
      const failing = NON_TEXT.map(([fg, bg]) => ({ fg, bg, ratio: contrast(colour(fg, theme), colour(bg, theme)) })).filter((p) => p.ratio < 3);
      expect(failing).toEqual([]);
    });
  }
  it("checks known ratios correctly", () => {
    expect(contrast("#000000", "#ffffff")).toBeCloseTo(21, 5);
    expect(contrast("#777777", "#ffffff")).toBeCloseTo(4.48, 2);
  });
});
