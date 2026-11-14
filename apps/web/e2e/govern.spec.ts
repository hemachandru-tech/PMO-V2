import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { expect, test, type Page } from "@playwright/test";

/** EPIC-07/08 screens against the running stack: field builder preview (US-078), custom SQL (US-080), certification (US-087). */
const secrets = resolve(dirname(fileURLToPath(import.meta.url)), "../../../deploy/compose/secrets");
const password = (name: string) => readFileSync(resolve(secrets, `dev_${name}_password`), "utf8").trim();

async function signIn(page: Page, username: string, pw: string) {
  const csp: string[] = [];
  page.on("console", (msg) => {
    if (msg.type() === "error" && /Content Security Policy/i.test(msg.text())) csp.push(msg.text());
  });
  await page.goto("/");
  await page.waitForURL(/auth\.localhost:8088/);
  await page.locator("#username").fill(username);
  await page.locator("#password").fill(pw);
  await page.locator("#kc-login").click();
  await page.waitForURL("http://tela.localhost:8088/");
  return csp;
}

test("Creator previews bands and a grouping in their scope without saving", async ({ page }) => {
  const csp = await signIn(page, "nomsa", password("creator"));
  await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Fields" }).click();
  await page.getByRole("button", { name: "New field" }).click();
  await page.getByRole("radio", { name: /Bands/ }).click();
  await page.getByRole("textbox", { name: "Field name" }).fill("E2E band");
  const basedOn = page.getByRole("combobox", { name: "Based on" });
  await basedOn.selectOption({ index: 1 });
  const preview = page.getByRole("region", { name: "Preview" });
  await expect(preview.getByRole("list", { name: "Counts per bucket" })).toBeVisible({ timeout: 15_000 });
  await expect(preview.getByText(/every row falls in exactly one bucket/)).toBeVisible();

  await page.getByRole("radio", { name: /Group values/ }).click();
  await page.getByRole("combobox", { name: "Based on" }).selectOption({ label: "Region" });
  await page.getByRole("textbox", { name: "Group 1 name" }).fill("Gulf");
  await page.getByRole("checkbox", { name: "KSA" }).click();
  await expect(preview.getByRole("list", { name: "Counts per bucket" }).getByText("Gulf", { exact: true })).toBeVisible({ timeout: 15_000 });
  expect(csp).toEqual([]);
});

test("Creator drafts, checks and runs custom SQL; the editor is styled under the strict CSP", async ({ page }) => {
  const csp = await signIn(page, "nomsa", password("creator"));
  await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Custom SQL" }).click();
  await page.getByRole("button", { name: "New custom SQL" }).click();
  await page.getByLabel("Describe the query in words").fill("open projects by region");
  await page.getByRole("button", { name: "Draft SQL" }).click();
  const editor = page.getByRole("textbox", { name: "SQL" });
  await expect(editor).toContainText(/SELECT/i, { timeout: 15_000 });
  // CodeMirror's theme is a constructable stylesheet: the editor must have its token background, not the default.
  const gutter = page.locator(".cm-gutters");
  await expect(gutter).toBeVisible();
  expect(await gutter.evaluate((el) => getComputedStyle(el).display)).toBe("flex");
  await page.getByRole("button", { name: "Run" }).click();
  await expect(page.getByRole("region", { name: "Checks" }).getByText(/All checks passed/)).toBeVisible({ timeout: 15_000 });
  await expect(page.getByRole("region", { name: "Result" }).getByRole("table")).toBeVisible();
  await expect(page.getByRole("region", { name: "Save as a widget" })).toBeVisible();
  expect(csp).toEqual([]);
});

test("Admin sees the certification queue under Govern", async ({ page }) => {
  const csp = await signIn(page, "admin", password("admin"));
  await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Certification" }).click();
  await expect(page.getByRole("heading", { name: "Certification", level: 1 })).toBeVisible();
  await expect(page.getByRole("tab", { name: "Waiting" })).toHaveAttribute("aria-selected", "true");
  await expect(page.getByRole("table", { name: "Certification queue" }).or(page.getByText("Nothing is waiting")).first()).toBeVisible();
  expect(csp).toEqual([]);
});
