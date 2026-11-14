import { expect, test } from "@playwright/test";

import { openRoute, settle, signIn } from "./support";

/**
 * Aurora layout (ENH-027): a person picks it in the account menu; the sidebar floats with colour chips, a dashboard
 * opens on the hero band and its charts draw in the vivid colours, in light and dark. The person's own layout is put
 * back to the company default at the end. Set TELA_E2E_SHOTS to a folder to keep screenshots.
 */
const shots = process.env.TELA_E2E_SHOTS;

test("Aurora layout: floating sidebar, hero band, vivid charts, light and dark", async ({ page }) => {
  test.setTimeout(120_000);
  await page.setViewportSize({ width: 1440, height: 1000 });
  await signIn(page, "admin");
  const pick = async (name: string) => {
    await page.getByRole("button", { name: "Account menu" }).click();
    await page.getByRole("menuitemradio", { name }).click();
  };
  try {
    await pick("Aurora");
    await expect(page.locator("html")).toHaveAttribute("data-layout", "aurora");
    const nav = page.locator("aside[data-shell-aside] nav[aria-label='Main']");
    await expect(nav).toBeVisible();
    expect(await nav.evaluate((n) => getComputedStyle(n).borderRadius)).toBe("22px");
    const chip = nav.locator("[data-nav-icon]").first();
    expect(await chip.evaluate((n) => getComputedStyle(n).width)).toBe("28px");
    // Dashboard groups take colour chips too: open the first group to show its dashboards.
    const group = nav.locator("button[data-nav-item][aria-expanded]").first();
    await group.waitFor({ timeout: 10_000 }).catch(() => undefined);
    if (await group.count()) {
      if ((await group.getAttribute("aria-expanded")) === "false") await group.click();
      await expect(group).toHaveAttribute("aria-expanded", "true");
      expect(await group.locator("[data-nav-icon]").evaluate((n) => getComputedStyle(n).width)).toBe("28px");
    }
    await page.mouse.move(700, 600);
    await page.waitForTimeout(400);
    if (shots) await page.screenshot({ path: `${shots}/aurora-home-light.png`, fullPage: true });

    await openRoute(page, "/");
    await settle(page);
    await page.getByRole("link", { name: /PMO Intelligence — PMO/ }).first().click();
    await page.waitForURL(/\/dashboards\/[^/]+$/);
    await settle(page);
    const header = page.locator("[data-dash-header]");
    expect(await header.evaluate((n) => getComputedStyle(n).borderRadius)).toBe("26px");
    expect(await header.evaluate((n) => getComputedStyle(n).backgroundImage)).toContain("gradient");
    await page.waitForTimeout(1500);
    if (shots) await page.screenshot({ path: `${shots}/aurora-dashboard-light.png`, fullPage: true });

    // An Ask answer drawn as bars: each bar takes its own vivid colour.
    await openRoute(page, "/");
    await settle(page);
    await page.getByRole("link", { name: /Open projects for each region/ }).first().click();
    await page.waitForSelector("[data-chart], .tl-plot, .recharts-wrapper", { timeout: 90_000 });
    await page.waitForTimeout(1500);
    await expect(page.locator("header[data-nav-place='topbar']")).toHaveAttribute("data-nav", "standard");
    await expect(page.getByRole("region", { name: "How this was answered" })).toBeVisible();
    expect(await page.locator("[data-ask-question]").first().evaluate((n) => getComputedStyle(n).backgroundImage)).toContain("gradient");
    if (shots) await page.screenshot({ path: `${shots}/aurora-ask-light.png`, fullPage: true });
    await page.getByRole("button", { name: "Account menu" }).click();
    const askTheme = await page.locator("[role='menuitemradio'][aria-checked='true']").first().innerText();
    await page.getByRole("menuitemradio", { name: "Dark" }).click();
    await page.waitForTimeout(800);
    if (shots) await page.screenshot({ path: `${shots}/aurora-ask-dark.png`, fullPage: true });
    await page.getByRole("button", { name: "Account menu" }).click();
    await page.getByRole("menuitemradio", { name: askTheme.trim() }).click();

    await page.getByRole("button", { name: "Account menu" }).click();
    const theme = await page.locator("[role='menuitemradio'][aria-checked='true']").first().innerText();
    await page.getByRole("menuitemradio", { name: "Dark" }).click();
    await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
    await settle(page);
    if (shots) await page.screenshot({ path: `${shots}/aurora-dashboard-dark.png`, fullPage: true });
    await page.getByRole("button", { name: "Account menu" }).click();
    await page.getByRole("menuitemradio", { name: theme.trim() }).click();
  } finally {
    await pick("Company default");
  }
});
