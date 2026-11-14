import { expect, test, type Page } from "@playwright/test";

import { openRoute, signIn } from "./support";

/**
 * US-075 / US-076 on the live stack: a grouping and bands drafted from words in Ask, opened in the field builder,
 * saved, and a band edited in words through its diff. The fields are deleted at the end.
 */
async function askForField(page: Page, words: string) {
  await openRoute(page, `/ask?q=${encodeURIComponent(words)}`);
  const open = page.getByRole("link", { name: "Open in the field builder" }).last();
  const choose = page.getByRole("button", { name: "Sample TPG (dev)", exact: true }).last();
  await expect(open.or(choose)).toBeVisible({ timeout: 20_000 });
  if (await choose.isVisible()) await choose.click(); // several PMO sample sources on the dev stack
  await open.click();
  await expect(page.getByText("Drafted from your words. Check it, then save.")).toBeVisible();
}

async function deleteField(page: Page, name: string) {
  await openRoute(page, "/fields");
  await page.getByRole("button", { name: `Delete ${name}` }).click();
  await page.getByRole("button", { name: "Delete field" }).click();
  await expect(page.getByText(`${name} was deleted`)).toBeVisible();
}

test("grouping and bands from words, then a band edited in words", async ({ page }) => {
  test.setTimeout(120_000);
  const stamp = Date.now().toString(36);
  await signIn(page, "nomsa");

  await askForField(page, "group platforms into Core (network one) and Growth");
  await expect(page.getByRole("combobox", { name: "Based on" })).toHaveValue("dimension:platform");
  const core = page.getByRole("list", { name: "Values in this group" }).first();
  await expect(core.getByRole("checkbox", { name: "Network One" })).toBeChecked();
  await page.getByRole("list", { name: "Values in this group" }).nth(1).getByRole("checkbox").first().click();
  await page.getByRole("textbox", { name: "Field name" }).fill(`Platform family ${stamp}`);
  await expect(page.getByRole("list", { name: "Counts per bucket" })).toContainText("Everything else", { timeout: 10_000 });
  await page.getByRole("button", { name: "Save", exact: true }).click();
  await expect(page).toHaveURL(/\/fields\/(?!new$)[^/]+$/); // saved: the builder moves to the field's own URL

  await askForField(page, "band forecast cost into <1M, 1-5M, >5M");
  await expect(page.getByRole("textbox", { name: "Band 1 name" })).toHaveValue("< 1M");
  await expect(page.getByRole("textbox", { name: "Band 3 name" })).toHaveValue("5M+");
  await expect(page.getByRole("region", { name: "Preview" })).toContainText("exactly 1M goes into 1M - 5M", { timeout: 10_000 });
  await page.getByRole("textbox", { name: "Field name" }).fill(`Cost band ${stamp}`);
  await page.getByRole("button", { name: "Save", exact: true }).click();
  await expect(page).toHaveURL(/\/fields\/(?!new$)[^/]+$/); // saved: the builder moves to the field's own URL

  await page.getByRole("textbox", { name: "Change the bands in words" }).fill("make the top band 2M and above");
  await page.getByRole("button", { name: "Show the change" }).click();
  const dialog = page.getByRole("dialog", { name: `Change the bands of Cost band ${stamp}?` });
  await expect(dialog.getByText(/Edges above it \(5M\) are removed/)).toBeVisible();
  await expect(dialog.getByRole("list", { name: "Bands now" }).getByText("Removed")).toHaveCount(2);
  await dialog.getByRole("button", { name: "Apply" }).click();
  await expect(page.getByRole("textbox", { name: "Band 3 name" })).toHaveValue("2M+");
  await expect(page.getByRole("region", { name: "Preview" })).toContainText("2M+");

  await deleteField(page, `Cost band ${stamp}`);
  await deleteField(page, `Platform family ${stamp}`);
});
