import { readFileSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { expect, test, type Page } from "@playwright/test";

import { ORIGIN, signIn } from "./support";

/**
 * US-148: no Largest Contentful Paint regression on the dashboard viewer against the pre-redesign baseline.
 *
 * The baseline was measured on the pre-redesign web app (commit 253d7c8) on the same stack with
 * `TELA_PERF_RECORD=1`, which writes `perf-baseline.json` instead of comparing. Each run reloads the starter
 * dashboard (sign-in redirect included, as tokens live in memory) and reads the LCP of the final document; the
 * median of RUNS, with the CPU slowed 4× (a mid-range laptop), is compared with the baseline median, allowing
 * 80 ms for noise (runs on a dev laptop spread by about ± 40 ms).
 */
const baselineFile = resolve(dirname(fileURLToPath(import.meta.url)), "perf-baseline.json");
const RUNS = 7;

async function lcpOnce(page: Page, path: string) {
  await page.goto(path);
  await page.waitForURL(`${ORIGIN}${path}`);
  await page.waitForLoadState("networkidle");
  await page.waitForTimeout(500);
  const lcp = await page.evaluate(
    () =>
      new Promise<{ ms: number; element: string }>((done) => {
        new PerformanceObserver((list) => {
          const entries = list.getEntries() as (PerformanceEntry & { element?: Element | null })[];
          const last = entries[entries.length - 1]!;
          done({ ms: last.startTime, element: `${last.element?.tagName ?? "?"} ${last.element?.textContent?.slice(0, 40) ?? ""}` });
        }).observe({ type: "largest-contentful-paint", buffered: true });
      }),
  );
  return lcp;
}

const median = (xs: number[]) => [...xs].sort((a, b) => a - b)[Math.floor(xs.length / 2)]!;

test("dashboard viewer LCP has not regressed", async ({ page }) => {
  test.setTimeout(180_000);
  await signIn(page, "nomsa");
  // The starter dashboard's link is on Home in both the old and the new app.
  await page.getByRole("link", { name: "PMO — Projects" }).first().click();
  await page.waitForURL(/\/dashboards\/[^/]+$/);
  const path = new URL(page.url()).pathname;

  const cdp = await page.context().newCDPSession(page);
  await cdp.send("Emulation.setCPUThrottlingRate", { rate: 4 });
  await lcpOnce(page, path); // warm caches
  const runs: number[] = [];
  for (let i = 0; i < RUNS; i++) {
    const { ms, element } = await lcpOnce(page, path);
    console.log(`LCP ${Math.round(ms)} ms on ${element}`);
    runs.push(ms);
  }
  const measured = Math.round(median(runs));
  console.log(`LCP runs (ms): ${runs.map(Math.round).join(", ")}; median ${measured}`);

  if (process.env.TELA_PERF_RECORD) {
    writeFileSync(
      baselineFile,
      JSON.stringify({ route: "/dashboards/:id (PMO — Projects)", commit: process.env.TELA_PERF_COMMIT ?? "unknown", runs: runs.map(Math.round), median_ms: measured, recorded: new Date().toISOString().slice(0, 10) }, null, 2) + "\n",
    );
    return;
  }
  const baseline = JSON.parse(readFileSync(baselineFile, "utf8")) as { median_ms: number };
  expect(measured, `LCP median ${measured} ms vs baseline ${baseline.median_ms} ms`).toBeLessThanOrEqual(baseline.median_ms + 80);
});
