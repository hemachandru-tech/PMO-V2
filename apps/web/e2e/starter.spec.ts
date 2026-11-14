import { expect, test } from "@playwright/test";

import { openRoute, signIn } from "./support";

/**
 * J10 on the live stack (US-098, US-099): an Admin deletes a starter report after reading what that does, the
 * starter dashboard loses its tab, and Restore from pack brings it back; a Creator copies a starter report.
 */
test("deletes a starter report with its effects explained, then restores it", async ({ page }) => {
  test.setTimeout(90_000);
  await signIn(page, "admin");
  await openRoute(page, "/admin/starter");
  await expect(page.getByRole("heading", { level: 1, name: "Starter content" })).toBeVisible();
  // The dev stack installs the pack on every sample source (P3): act on the PostgreSQL one.
  const flow = /Projects — Flow metrics Sample TPG \(dev\)/;
  const row = page.getByRole("row", { name: flow });
  await expect(row.getByText("Active")).toBeVisible();

  await row.getByRole("button", { name: "Delete" }).click();
  const dialog = page.getByRole("dialog", { name: "Delete Projects — Flow metrics?" });
  await expect(dialog.getByText(/It leaves PMO — Projects at once/)).toBeVisible();
  await expect(dialog.getByRole("button", { name: "Cancel" })).toBeFocused();
  await dialog.getByRole("button", { name: "Delete starter report" }).click();
  await expect(row.getByText("Deleted")).toBeVisible();

  await openRoute(page, "/");
  await page.getByRole("link", { name: "PMO — Projects" }).first().click();
  await expect(page.getByRole("tab", { name: "Delayed projects" })).toBeVisible();
  await expect(page.getByRole("tab", { name: "Flow metrics" })).toHaveCount(0);

  await openRoute(page, "/admin/starter");
  await page.getByRole("row", { name: flow }).getByRole("button", { name: "Restore from pack" }).click();
  await expect(page.getByRole("row", { name: flow }).getByText("Active")).toBeVisible();
  await openRoute(page, "/");
  await page.getByRole("link", { name: "PMO — Projects" }).first().click();
  await expect(page.getByRole("tab", { name: "Flow metrics" })).toBeVisible();
});

test("a Creator copies a starter report to make their own", async ({ page }) => {
  await signIn(page, "nomsa");
  await page.getByRole("link", { name: "PMO — Projects" }).first().click();
  await page.getByRole("tab", { name: "Delayed projects" }).click();
  await page.getByRole("tabpanel").getByRole("button", { name: "Copy to my reports" }).click();
  await expect(page).toHaveURL(/\/reports\/[0-9a-f-]+$/);
  await expect(page.getByLabel("Report title")).toHaveValue("Projects — Delayed projects (copy)");
});
