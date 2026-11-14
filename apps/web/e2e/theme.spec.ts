import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { expect, test } from "@playwright/test";

/** US-134: the theme switch is remembered per user and the new styling stack runs under the strict CSP. */
const secrets = resolve(dirname(fileURLToPath(import.meta.url)), "../../../deploy/compose/secrets");
const password = (name: string) => readFileSync(resolve(secrets, `dev_${name}_password`), "utf8").trim();

test("viewer switches to dark, the choice survives a reload, and no CSP violations occur", async ({ page }) => {
  const csp: string[] = [];
  page.on("console", (msg) => {
    if (msg.type() === "error" && /Content Security Policy/i.test(msg.text())) csp.push(msg.text());
  });
  await page.goto("/");
  await page.waitForURL(/auth\.localhost:8088/);
  await page.locator("#username").fill("viewer");
  await page.locator("#password").fill(password("viewer"));
  await page.locator("#kc-login").click();
  await page.waitForURL("http://tela.localhost:8088/");

  const html = page.locator("html");
  await page.getByRole("button", { name: "Account menu" }).click(); // opens a modal Radix menu (scroll lock)
  await page.getByRole("menuitemradio", { name: "Dark" }).click();
  await expect(html).toHaveAttribute("data-theme", "dark");
  // Token variables switch with the theme.
  const surface = await page.evaluate(() => getComputedStyle(document.documentElement).getPropertyValue("--tl-surface").trim());
  expect(surface).toBe("#0e1418");

  await page.reload();
  await page.waitForURL("http://tela.localhost:8088/");
  await expect(html).toHaveAttribute("data-theme", "dark");
  await page.getByRole("button", { name: "Account menu" }).click();
  await expect(page.getByRole("menuitemradio", { name: "Dark" })).toBeChecked();

  // Back to the default for other journeys.
  const saved = page.waitForResponse((r) => r.url().endsWith("/api/v1/me/preferences") && r.ok());
  await page.getByRole("menuitemradio", { name: "Match my system" }).click();
  await saved;
  await page.getByRole("button", { name: "Account menu" }).click();
  await expect(page.getByRole("menuitemradio", { name: "Match my system" })).toBeChecked();
  await page.keyboard.press("Escape");
  expect(csp).toEqual([]);
});
