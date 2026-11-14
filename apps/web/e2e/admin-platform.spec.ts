import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { expect, test, type Page } from "@playwright/test";

/**
 * US-146 in a real browser against the running stack: an Admin filters the audit log by a facet and opens an event,
 * reads AI settings and usage (without saving), and opens the notifications centre from the bell. Nothing is
 * changed. No CSP violations (ECharts draws on canvas).
 */
const secrets = resolve(dirname(fileURLToPath(import.meta.url)), "../../../deploy/compose/secrets");
const password = (name: string) => readFileSync(resolve(secrets, `dev_${name}_password`), "utf8").trim();
const shots = process.env.TELA_SHOTS;

async function shoot(page: Page, name: string) {
  if (!shots) return;
  await page.screenshot({ path: `${shots}/${name}-light.png`, fullPage: true });
  await page.emulateMedia({ colorScheme: "dark" });
  await page.waitForTimeout(400); // let colour transitions finish
  await page.screenshot({ path: `${shots}/${name}-dark.png`, fullPage: true });
  await page.emulateMedia({ colorScheme: "light" });
}

test("an Admin investigates the audit log, reads AI usage and opens notifications", async ({ page }) => {
  const csp: string[] = [];
  page.on("console", (msg) => {
    if (msg.type() === "error" && /Content Security Policy/i.test(msg.text())) csp.push(msg.text());
  });
  await page.setViewportSize({ width: 1440, height: 1100 });
  await page.goto("/");
  await page.waitForURL(/auth\.localhost:8088\/realms\/tela\/protocol\/openid-connect\/auth/);
  await page.locator("#username").fill("admin");
  await page.locator("#password").fill(password("admin"));
  await page.locator("#kc-login").click();
  await page.waitForURL("http://tela.localhost:8088/");

  const nav = page.getByRole("navigation", { name: "Main" });
  await nav.getByRole("link", { name: "Audit log" }).click();
  await expect(page.getByText(/of [\d,]+ events · newest first/)).toBeVisible();
  const action = page.getByLabel("Action");
  const first = await action.locator("option").nth(1).getAttribute("value");
  await action.selectOption(first!);
  await expect(page).toHaveURL(new RegExp(`action=${first!.replace(/\./g, "\\.")}`));
  await page.getByRole("button", { name: "Details" }).first().click();
  await expect(page.getByRole("dialog", { name: first! })).toBeVisible();
  await shoot(page, "audit-detail");
  await page.keyboard.press("Escape");

  await nav.getByRole("link", { name: "AI settings" }).click();
  await expect(page.getByRole("form", { name: "AI settings" })).toBeVisible();
  await expect(page.getByRole("img", { name: "Calls per day" })).toBeVisible();
  await expect(page.getByRole("table", { name: "Usage by model" })).toBeVisible();
  await shoot(page, "ai-settings");

  await page.getByRole("button", { name: /^Notifications, \d+ unread$/ }).click();
  await page.getByRole("button", { name: "See all notifications" }).click();
  await expect(page.getByRole("heading", { name: "Notifications", level: 1 })).toBeVisible();
  await expect(page.getByRole("dialog", { name: "Notifications" })).toBeHidden();
  await expect(page.getByRole("tab", { name: /All/ })).toHaveAttribute("aria-selected", "true");
  await shoot(page, "notifications");
  expect(csp).toEqual([]);
});
