import { expect, test } from "@playwright/test";

import { openRoute, signIn } from "./support";

/**
 * EPIC-03 on the live stack (US-100 – US-103, TEST-016): an Admin uploads a glossary, the worker indexes it, it waits
 * for review and is not cited; after approval the definition panel in Ask cites it. The document is deleted at the
 * end so the run can repeat.
 */
test("an approved glossary is cited on the definition panel", async ({ page }) => {
  test.setTimeout(120_000);
  const stamp = Date.now().toString(36); // not a long digit run: the scanner treats those as card numbers
  const title = `E2E glossary ${stamp}`;
  await signIn(page, "admin");
  await openRoute(page, "/admin/knowledge");
  await expect(page.getByRole("heading", { level: 1, name: "Knowledge" })).toBeVisible();
  await expect(page.getByRole("row", { name: /Model definitions v\d+/ }).first().getByText("Approved")).toBeVisible();

  await page.getByRole("button", { name: "Upload document" }).click();
  const dialog = page.getByRole("dialog", { name: "Upload a document" });
  await dialog.getByLabel("File").setInputFiles({
    name: `glossary-${stamp}.md`, mimeType: "text/markdown",
    buffer: Buffer.from(`# Open projects\n\nOpen projects are the steering committee's book of work (${stamp}), including projects waiting to start and those on hold.\n`),
  });
  await dialog.getByLabel(/Title/).fill(title);
  await dialog.getByLabel("Kind").selectOption("glossary");
  await dialog.getByRole("button", { name: "Upload" }).click();

  const row = page.getByRole("row", { name: new RegExp(title) });
  await expect(row.getByText("Pending review")).toBeVisible();
  await expect(row.getByText(/1 passage/)).toBeVisible({ timeout: 60_000 }); // the worker indexed it

  // Pending: Ask does not cite it.
  await openRoute(page, "/ask?q=What%20does%20open%20projects%20mean%3F");
  const first = page.getByRole("article", { name: "Answer" }).last();
  await expect(first.getByRole("heading", { name: "Open projects" })).toBeVisible({ timeout: 30_000 });
  await expect(first.getByText(title)).toHaveCount(0);

  await openRoute(page, "/admin/knowledge");
  await page.getByRole("row", { name: new RegExp(title) }).getByRole("button", { name: "Review" }).click();
  const sheet = page.getByRole("dialog", { name: title });
  await expect(sheet.getByText(/steering committee's book of work/)).toBeVisible();
  await sheet.getByRole("button", { name: "Approve" }).click();
  await expect(page.getByRole("row", { name: new RegExp(title) }).getByText("Approved")).toBeVisible();

  await openRoute(page, "/ask?q=What%20does%20open%20projects%20mean%3F");
  const answer = page.getByRole("article", { name: "Answer" }).last();
  const cited = answer.getByRole("region", { name: "From approved knowledge" });
  await expect(cited.getByText(title)).toBeVisible({ timeout: 30_000 });

  await openRoute(page, "/admin/knowledge");
  await page.getByRole("row", { name: new RegExp(title) }).getByRole("button", { name: `Delete ${title}` }).click();
  await page.getByRole("dialog").getByRole("button", { name: "Delete document" }).click();
  await expect(page.getByRole("row", { name: new RegExp(title) })).toHaveCount(0);
});
