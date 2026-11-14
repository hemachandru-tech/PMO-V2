import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

import { openRoute, settle, signIn } from "./support";

/**
 * US-162 in a real browser against the running stack: an Admin opens Access → Free-hand query, gives themself
 * free-hand access on the first connection (an Admin sees every connection, and needs a grant like everyone else),
 * extends it, then removes it, so the stack ends as it started. axe finds nothing serious in either theme, and there
 * are no CSP violations.
 */
async function axe(page: Page) {
  for (const theme of ["light", "dark"] as const) {
    await page.emulateMedia({ colorScheme: theme });
    await settle(page);
    const results = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"]).analyze();
    const blocking = results.violations.filter((v) => v.impact === "serious" || v.impact === "critical").map((v) => v.id);
    expect(blocking, `${theme}: ${blocking.join(", ")}`).toEqual([]);
  }
  await page.emulateMedia({ colorScheme: "light" });
}

test("an Admin gives, extends and removes free-hand access", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 1100 });
  const csp = await signIn(page, "admin");
  await openRoute(page, "/admin/access");
  await page.getByRole("link", { name: /Free-hand query/ }).click();
  await expect(page).toHaveURL(/\/admin\/access\/freehand/);
  const table = page.getByRole("table", { name: "People with free-hand access" });
  await expect(table).toBeVisible();
  await axe(page);

  await page.getByRole("button", { name: "Give access" }).first().click();
  const dialog = page.getByRole("dialog", { name: "Give free-hand access" });
  await dialog.getByRole("combobox", { name: "Person" }).fill("Ada");
  await dialog.getByRole("option", { name: /Ada Admin/ }).click();
  const first = dialog.locator("label").filter({ has: page.getByRole("checkbox", { disabled: false }) }).first();
  const connection = (await first.locator("span > span").first().textContent())!.trim();
  await first.getByRole("checkbox").click();
  await dialog.getByLabel("Reason").fill("e2e: free-hand journey");
  await axe(page);
  await dialog.getByRole("button", { name: "Continue" }).click();
  const confirm = page.getByRole("dialog", { name: "Give Ada Admin free-hand access?" });
  await expect(confirm).toContainText(connection);
  await confirm.getByRole("button", { name: "Give access" }).click();
  await expect(confirm).toBeHidden();

  const row = table.getByRole("row").filter({ hasText: "e2e: free-hand journey" });
  await expect(row).toContainText(connection);
  await expect(row).toContainText("Never");

  await row.getByRole("button", { name: /^Extend/ }).click();
  const extend = page.getByRole("dialog", { name: "Extend Ada Admin's access" });
  await extend.getByLabel("New end date").fill(await page.evaluate(() => {
    const d = new Date(); d.setDate(d.getDate() + 200);
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
  }));
  await extend.getByRole("button", { name: "Save end date" }).click();
  await expect(extend).toBeHidden();
  await expect(row).toContainText("in 7 months");

  await row.getByRole("button", { name: /^Remove/ }).click();
  const remove = page.getByRole("dialog", { name: /Remove Ada Admin's free-hand access/ });
  await expect(remove.getByRole("button", { name: "Cancel" })).toBeFocused();
  await remove.getByRole("button", { name: "Remove access" }).click();
  await expect(row).toHaveCount(0);

  await page.getByLabel("Show ended").check();
  await expect(table.getByRole("row").filter({ hasText: "e2e: free-hand journey" }).first()).toContainText("Removed");
  expect(csp).toEqual([]);
});
