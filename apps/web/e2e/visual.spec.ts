import { expect, test, type Page } from "@playwright/test";

import { markVolatileText, SCREENS } from "./screens";
import { matchSystemTheme, openRoute, settle, signIn, type UserKey } from "./support";

/**
 * US-148: screenshot comparisons of every redesigned screen at 1280 px (building) and 768 px (reading), light theme,
 * against the live dev stack. Timestamps and the parts listed in each screen's `mask` change with the data, so they
 * are masked. Baselines live in `e2e/__screenshots__` (macOS Chromium) and were reviewed against the Claude Design
 * boards; after an intended change run `npx playwright test e2e/visual.spec.ts --update-snapshots` and review the
 * images in the diff.
 */
const pages = new Map<UserKey, Page>();

async function pageFor(browser: import("@playwright/test").Browser, user: UserKey) {
  if (!pages.has(user)) {
    const context = await browser.newContext({ viewport: { width: 1280, height: 900 }, colorScheme: "light", reducedMotion: "reduce" });
    const page = await context.newPage();
    await signIn(page, user);
    await matchSystemTheme(page);
    pages.set(user, page);
  }
  return pages.get(user)!;
}

test.afterAll(async () => {
  for (const p of pages.values()) await p.context().close();
});

for (const screen of SCREENS) {
  for (const width of [1280, 768] as const) {
    test(`${screen.name} at ${width} px matches its baseline`, async ({ browser }) => {
      const page = await pageFor(browser, screen.user);
      await page.setViewportSize({ width, height: 900 });
      const path = screen.path ?? (await screen.resolve!(page));
      test.skip(!path, `no content on the stack for ${screen.name}`);
      await openRoute(page, path!);
      await page.getByRole("heading", { level: 1 }).first().waitFor();
      await settle(page);
      await page.mouse.move(0, 0);
      await page.evaluate(() => (document.activeElement as HTMLElement | null)?.blur());
      await markVolatileText(page);
      await expect(page).toHaveScreenshot(`${screen.name}-${width}.png`, {
        mask: [page.locator("[data-e2e-mask]"), ...(screen.mask ?? []).map((m) => page.locator(m))],
        clip: screen.clipHeight ? { x: 0, y: 0, width, height: screen.clipHeight } : undefined,
        animations: "disabled",
        caret: "hide",
        maxDiffPixelRatio: 0.02,
      });
    });
  }
}
