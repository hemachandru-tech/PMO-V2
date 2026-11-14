import { expect, test } from "@playwright/test";

import { openRoute, settle, signIn } from "./support";

/**
 * Pack Studio on the live stack (US-232): the system PMO pack opens read-only with its rule tree; "Clone to edit"
 * makes a draft; a measure is edited in its side panel and saved; the draft exports as a zip and is deleted again,
 * so the run leaves nothing behind. Needs the PMO pack loaded (`tela-admin load-packs`).
 */
test("system pack read-only, clone, edit a measure, try it, export, delete", async ({ page }) => {
  test.setTimeout(120_000);
  await signIn(page, "admin");
  await openRoute(page, "/admin/packs");
  await settle(page);
  const packs = page.getByRole("table", { name: "Packs" });
  const pmo = packs.getByRole("row", { name: /PMO starter pack/ }).first();
  await expect(pmo).toContainText("System · read-only");
  await pmo.click();
  await expect(page.getByText(/System packs are read-only/)).toBeVisible();
  await expect(page.getByRole("button", { name: "Delete draft" })).toHaveCount(0);

  await page.getByRole("tab", { name: /Measures/ }).click();
  await page.getByRole("table", { name: "Measures" }).getByRole("row", { name: /Open projects/ }).first().click();
  let panel = page.getByRole("dialog", { name: "Open projects" });
  await expect(panel.getByRole("list", { name: "The rule as a tree" })).toContainText("Count of records");
  await expect(panel.getByRole("button", { name: "Save measure" })).toHaveCount(0);
  await page.keyboard.press("Escape");

  const key = `e2e_pmo_${Date.now().toString(36)}`;
  await page.getByRole("button", { name: "Clone to edit" }).click();
  const dialog = page.getByRole("dialog", { name: /Clone PMO starter pack/ });
  await dialog.getByLabel("Key").fill(key);
  await dialog.getByRole("button", { name: "Clone" }).click();
  await expect(page.getByRole("heading", { name: "PMO starter pack (TPG) (copy)" })).toBeVisible();
  await expect(page.getByText(`${key} 0.1.0`)).toBeVisible();

  await page.getByRole("tab", { name: /Measures/ }).click();
  await page.getByRole("table", { name: "Measures" }).getByRole("row", { name: /Open projects/ }).first().click();
  panel = page.getByRole("dialog", { name: "Open projects" });
  await panel.getByLabel("Label", { exact: true }).fill("Open portfolio");
  await panel.getByRole("button", { name: "Save measure" }).click();
  await expect(page.getByText("Measure saved")).toBeVisible();
  await expect(page.getByRole("table", { name: "Measures" }).getByRole("row", { name: /Open portfolio/ })).toBeVisible();

  // Try the edited measure on the first connection: a figure, or the reason it can't run there.
  await page.getByRole("table", { name: "Measures" }).getByRole("row", { name: /Open portfolio/ }).click();
  panel = page.getByRole("dialog", { name: "Open portfolio" });
  const trial = panel.getByRole("region", { name: "Try on a connection" });
  const picker = trial.getByLabel("Connection");
  await picker.selectOption({ index: 1 });
  await trial.getByRole("button", { name: "Run the measure" }).click();
  await expect(trial.locator("p[aria-live]")).not.toBeEmpty({ timeout: 60_000 });
  console.log(`try on ${await picker.locator("option:checked").textContent()}: ${await trial.locator("p[aria-live]").textContent()}`);
  await page.keyboard.press("Escape");

  const [download] = await Promise.all([page.waitForEvent("download"), page.getByRole("button", { name: "Export" }).click()]);
  expect(download.suggestedFilename()).toBe(`${key}-0.1.0.zip`);

  await page.getByRole("button", { name: "Delete draft" }).click();
  await page.getByRole("dialog").getByRole("button", { name: "Delete draft" }).click();
  await page.waitForURL((u) => u.pathname === "/admin/packs");
  await settle(page);
  await expect(page.getByRole("table", { name: "Packs" })).not.toContainText(key);
});
