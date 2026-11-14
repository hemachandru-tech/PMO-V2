import { expect, test } from "@playwright/test";

import { openRoute, signIn } from "./support";

/**
 * US-056/057 on the live stack: an Admin loads the PMO draft gold set, approves it and runs the suite; the worker
 * plans every question the way Ask does and the accuracy card reports the pass rate by tag. Idempotent: a second run
 * loads nothing new and evaluates again.
 */
test("loads the draft gold set, approves it and evaluates", async ({ page }) => {
  test.setTimeout(300_000);
  await signIn(page, "admin");
  await openRoute(page, "/admin/knowledge/gold");
  await expect(page.getByRole("heading", { level: 1, name: "Knowledge" })).toBeVisible();
  await page.getByRole("button", { name: "Load PMO draft set" }).click();
  const dialog = page.getByRole("dialog", { name: "Load the PMO draft set" });
  await dialog.getByRole("button", { name: "Load as suggestions" }).click();
  await expect(dialog).toBeHidden();

  const table = page.getByRole("table", { name: "Gold examples" });
  await expect(table.getByRole("row", { name: /How many projects are overdue\?/ })).toBeVisible();
  const suggested = table.getByText("Suggested");
  if (await suggested.count()) {
    await table.getByRole("checkbox", { name: "Select all rows" }).check();
    await page.getByRole("button", { name: /^Approve \d+$/ }).click();
    await expect(suggested).toHaveCount(0);
  }
  await page.getByRole("button", { name: "Evaluate" }).click();
  await expect(page.getByRole("button", { name: "Evaluating…" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Evaluate" })).toBeEnabled({ timeout: 240_000 });
  await expect(page.getByRole("table", { name: "Pass rate by tag" }).getByRole("row", { name: /look_alike/ })).toBeVisible();
  await page.getByRole("button", { name: "See every result" }).click();
  const sheet = page.getByRole("dialog", { name: "Evaluation results" });
  await expect(sheet.getByRole("list", { name: "Results" }).getByRole("listitem").first()).toBeVisible();
  console.log(await sheet.getByRole("heading").first().locator("..").innerText());
});
