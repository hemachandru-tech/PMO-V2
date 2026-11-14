import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import { MOTION } from "../components/ui/motion";

/** US-147: motion presets stay at or under 200 ms, and nothing moves under prefers-reduced-motion. */
const here = dirname(fileURLToPath(import.meta.url));
const read = (p: string) => readFileSync(resolve(here, "..", p), "utf8");

describe("motion presets (US-147)", () => {
  it("keep every duration at or under 200 ms", () => {
    expect(Math.max(MOTION.fast, MOTION.base, MOTION.panel)).toBeLessThanOrEqual(0.2);
    const css = read("design/motion.css");
    const ms = [...css.matchAll(/--tl-motion-[a-z]+:\s*(\d+)ms/g)].map((m) => Number(m[1]));
    expect(ms.length).toBe(3);
    expect(Math.max(...ms)).toBeLessThanOrEqual(200);
  });
  it("stop animations and transitions under prefers-reduced-motion", () => {
    const css = read("design/motion.css");
    const block = css.slice(css.indexOf("@media (prefers-reduced-motion: reduce)"));
    expect(block).toMatch(/animation-duration: 0\.01ms !important/);
    expect(block).toMatch(/transition-duration: 0\.01ms !important/);
  });
  it("give every animated overlay a preset instead of the 150 ms library default", () => {
    for (const file of ["dialog", "sheet", "popover", "dropdown-menu", "select", "tooltip"]) {
      const src = read(`components/ui/${file}.tsx`);
      expect(src, file).toMatch(/motion-(fast|base|panel)/);
      expect(src, file).not.toMatch(/duration-\d{3}/);
    }
  });
});
