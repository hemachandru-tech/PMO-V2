import { expect, test } from "@playwright/test";

import { openRoute, settle, signIn } from "./support";

/**
 * Import YAML on the live stack: a clone of the PMO pack takes the template (a measure, a dimension, a blueprint
 * using the measure and a new dashboard) in one file, checked first and then imported; the new dashboard shows on
 * Reports & dashboards. The draft is deleted again, so the run leaves nothing behind.
 */
test("import measures, dimensions, a blueprint and a dashboard from one YAML file", async ({ page }) => {
  test.setTimeout(120_000);
  await signIn(page, "admin");
  await openRoute(page, "/admin/packs");
  await settle(page);
  await page.getByRole("table", { name: "Packs" }).getByRole("row", { name: /PMO starter pack/ }).first().click();
  const key = `e2e_yaml_${Date.now().toString(36)}`;
  await page.getByRole("button", { name: "Clone to edit" }).click();
  const clone = page.getByRole("dialog", { name: /Clone PMO starter pack/ });
  await clone.getByLabel("Key").fill(key);
  await clone.getByRole("button", { name: "Clone" }).click();
  await expect(page.getByText(`${key} 0.1.0`)).toBeVisible();

  await page.getByRole("button", { name: "Import YAML" }).click();
  const dialog = page.getByRole("dialog", { name: "Import YAML" });
  await dialog.getByRole("button", { name: "Start from the template" }).click();
  await dialog.getByRole("button", { name: "Check" }).click();
  await expect(dialog.getByText(/Ready to import\./)).toBeVisible({ timeout: 30_000 });
  const changes = dialog.getByRole("table", { name: "What changes" });
  await expect(changes.getByRole("row", { name: /Measures/ })).toContainText("1");
  await expect(changes.getByRole("row", { name: /Starter dashboards/ })).toBeVisible();
  await page.screenshot({ path: "test-results/pack-yaml-check.png" });
  await dialog.getByRole("button", { name: "Import" }).click();
  await expect(page.getByText("The YAML is imported into the draft.")).toBeVisible();

  await page.getByRole("tab", { name: /Measures/ }).click();
  await expect(page.getByRole("table", { name: "Measures" }).getByRole("row", { name: /Projects in the Gulf/ })).toBeVisible();
  await page.getByRole("tab", { name: /Reports/ }).click();
  await expect(page.getByRole("article", { name: "Dashboard Gulf" })).toContainText("Gulf overview");

  await page.getByRole("button", { name: "Delete draft" }).click();
  await page.getByRole("dialog").getByRole("button", { name: "Delete draft" }).click();
  await page.waitForURL((u) => u.pathname === "/admin/packs");
});
