import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

import { openRoute, settle, signIn } from "./support";

/**
 * US-104 on the live stack: a Creator asks for forecast cost by portfolio, opens Suggestions, sees the house chart
 * pattern "Forecast cost next to approved budget" with its reason, applies it (a refinement: paired bars with the
 * approved budget added) and undoes it.
 */
test("chart suggestions on an answer apply as a refinement and undo", async ({ page }) => {
  test.setTimeout(120_000);
  await signIn(page, "nomsa");
  await openRoute(page, "/ask?q=Forecast%20cost%20by%20portfolio");
  const answer = page.getByRole("article", { name: "Answer" }).last();
  await expect(answer.getByRole("button", { name: "Suggestions for this answer" })).toBeVisible({ timeout: 60_000 });
  await answer.getByRole("button", { name: "Suggestions for this answer" }).click();
  const panel = page.getByRole("dialog", { name: "Suggestions for this answer" });
  await expect(panel.getByText("Forecast cost next to approved budget")).toBeVisible({ timeout: 30_000 });
  await expect(panel.getByText(/gap between them is what matters/)).toBeVisible();
  await page.screenshot({ path: "test-results/suggestions-ask.png" });
  for (const theme of ["dark", "light"] as const) {
    await page.emulateMedia({ colorScheme: theme });
    await expect(page.locator("html")).toHaveAttribute("data-theme", theme);
    await settle(page);
    const results = await new AxeBuilder({ page }).include('[data-slot="popover-content"]')
      .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"]).analyze();
    const blocking = results.violations.filter((v) => v.impact === "serious" || v.impact === "critical").map((v) => `${v.id}: ${v.nodes.map((n) => `${n.target.join(" ")} ${n.failureSummary ?? ""}`).join(" | ")}`);
    expect(blocking, `${theme}: ${blocking.join(", ")}`).toEqual([]);
  }
  await panel.getByRole("button", { name: "Apply: Forecast cost next to approved budget" }).click();
  await expect(page.getByText("Refined: Forecast cost next to approved budget")).toBeVisible({ timeout: 30_000 });
  const refined = page.getByRole("article", { name: "Answer" }).last();
  await expect(refined.getByText("Added Approved budget (USD)", { exact: false }).or(
    refined.getByText("Approved budget (USD)", { exact: false }).first())).toBeVisible({ timeout: 60_000 });
  await refined.getByRole("button", { name: "Undo" }).click();
  await expect(page.getByText(/^Undo: /).last()).toBeVisible({ timeout: 30_000 });
});
