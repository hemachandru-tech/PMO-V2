import { readFileSync } from "node:fs";

import { expect, test } from "@playwright/test";

import { signIn } from "./support";

/**
 * Export journey on the live stack (US-095 – US-097): a CIO exports the starter dashboard with a filter; the worker
 * renders it (Chromium for PDF, openpyxl for Excel) and the dialog offers the signed download.
 */
for (const format of ["Excel", "PDF"] as const) {
  test(`exports the dashboard as ${format} in the reader's scope`, async ({ page }, info) => {
    test.setTimeout(120_000);
    await signIn(page, "nomsa");
    await page.getByRole("link", { name: "PMO — Projects" }).first().click();
    await expect(page.getByRole("heading", { level: 1, name: "PMO — Projects" })).toBeVisible();
    await page.getByRole("button", { name: "Export" }).click();
    await page.getByRole("menuitem", { name: new RegExp(format) }).click();
    const dialog = page.getByRole("dialog", { name: `${format} export` });
    await expect(dialog).toBeVisible();
    const download = dialog.getByRole("link", { name: "Download" });
    await expect(download).toBeVisible({ timeout: 90_000 });
    await expect(dialog).toContainText(/PMO-Projects-\d{4}-\d{2}-\d{2}\.(xlsx|pdf)/);
    await expect(dialog).toContainText("The link works for 15 minutes");

    const [file] = await Promise.all([page.waitForEvent("download"), download.click()]);
    const path = info.outputPath(file.suggestedFilename());
    await file.saveAs(path);
    const head = readFileSync(path).subarray(0, 5).toString("latin1");
    expect(head).toBe(format === "PDF" ? "%PDF-" : "PK\u0003\u0004\u0014".slice(0, 5));
    if (process.env.TELA_E2E_EXPORT_DIR) await file.saveAs(`${process.env.TELA_E2E_EXPORT_DIR}/${file.suggestedFilename()}`);
  });
}
