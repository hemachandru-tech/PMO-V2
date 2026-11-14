import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

import { SCREENS } from "./screens";
import { matchSystemTheme, openRoute, settle, signIn, type UserKey } from "./support";

/**
 * US-148: axe on every redesigned screen in both themes, with no serious or critical violations (WCAG 2.2 AA rule
 * set). One signed-in page per user; the theme follows the emulated OS setting ("Match my system").
 */
// Not serial, so one screen failing does not hide the others; pages are shared per user within the worker.
const pages = new Map<UserKey, Page>();
const csp: string[] = [];

async function pageFor(browser: import("@playwright/test").Browser, user: UserKey) {
  if (!pages.has(user)) {
    const context = await browser.newContext({ viewport: { width: 1280, height: 900 } });
    const page = await context.newPage();
    csp.push(...(await signIn(page, user)));
    await matchSystemTheme(page);
    pages.set(user, page);
  }
  return pages.get(user)!;
}

test.afterAll(async () => {
  for (const p of pages.values()) await p.context().close();
  expect(csp).toEqual([]);
});

for (const screen of SCREENS) {
  for (const theme of ["light", "dark"] as const) {
    test(`${screen.name} (${theme}) has no serious or critical axe violations`, async ({ browser }) => {
      const page = await pageFor(browser, screen.user);
      await page.emulateMedia({ colorScheme: theme });
      const path = screen.path ?? (await screen.resolve!(page));
      test.skip(!path, `no content on the stack for ${screen.name}`);
      await openRoute(page, path!);
      await page.getByRole("heading", { level: 1 }).first().waitFor();
      await settle(page);
      await expect(page.locator("html")).toHaveAttribute("data-theme", theme);
      const results = await new AxeBuilder({ page })
        .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"])
        .analyze();
      const blocking = results.violations
        .filter((v) => v.impact === "serious" || v.impact === "critical")
        .map((v) => ({ id: v.id, impact: v.impact, help: v.help, nodes: v.nodes.slice(0, 5).map((n) => `${n.target.join(" ")} — ${n.failureSummary?.split("\n")[1]?.trim() ?? ""}`) }));
      expect(blocking, JSON.stringify(blocking, null, 2)).toEqual([]);
    });
  }
}
