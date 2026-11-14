import { expect, test, type Page } from "@playwright/test";

import { openRoute, signIn } from "./support";

/**
 * BUG-004 / US-153 / US-154 on the live stack: "Starter pack" is offered on connections that already have a model,
 * the preview explains what applying it would do, and a source with other names can be mapped. Read-only: every
 * dialog is cancelled, nothing is installed. Each test needs its connection and skips when the stack lacks it.
 */
async function openPack(page: Page, name: string) {
  await signIn(page, "admin");
  await openRoute(page, "/admin/connections");
  const row = page.getByRole("table", { name: "Connections" }).getByRole("row").filter({ has: page.getByText(name, { exact: true }) });
  await expect(page.getByRole("table", { name: "Connections" })).toBeVisible();
  test.skip((await row.count()) === 0, `needs the "${name}" connection`);
  await row.getByRole("button", { name: "Starter pack" }).click();
  return page.getByRole("dialog", { name: `Starter pack for ${name}` });
}

test("a connection whose model came from the pack: already applied, adds nothing twice", async ({ page }) => {
  test.setTimeout(90_000);
  const dialog = await openPack(page, "TPG via bastion (live)");
  await expect(dialog.getByTestId("pack-preview")).toBeVisible();
  await expect(dialog.getByText(/already applied here/)).toBeVisible();
  await expect(dialog.getByText(/Keeps your \d+ tables/)).toBeVisible();
  await expect(dialog.getByRole("button", { name: "Add to the model draft" })).toBeEnabled();
  await dialog.getByRole("button", { name: "Cancel" }).click();
});

test("a source with other names: the required Status is mapped by hand and the pack then matches (US-154)", async ({ page }) => {
  test.setTimeout(90_000);
  const dialog = await openPack(page, "TPG");
  const mapping = dialog.getByTestId("pack-mapping");
  await expect(mapping).toBeVisible();
  await expect(dialog.getByText(/This source uses other names than the pack/)).toBeVisible();
  await expect(mapping.getByText(/1 required field needs a column/)).toBeVisible();
  await expect(dialog.getByRole("button", { name: "Add to the model draft" })).toBeDisabled();

  await mapping.getByRole("button", { name: "Column for Status" }).click();
  const list = page.getByRole("listbox");
  await page.getByPlaceholder("Search columns").fill("record_status");
  await list.getByRole("option", { name: /^record_status\b/ }).first().click();

  await expect(dialog.getByText(/This source uses other names than the pack/)).toBeHidden();
  await expect(mapping.getByText(/required field/)).toBeHidden();
  await expect(dialog.getByRole("button", { name: "Add to the model draft" })).toBeEnabled();
  await dialog.screenshot({ path: test.info().outputPath("pack-mapping.png") });
  await dialog.getByRole("button", { name: "Cancel" }).click();
});
