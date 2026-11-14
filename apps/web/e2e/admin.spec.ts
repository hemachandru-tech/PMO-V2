import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { expect, test } from "@playwright/test";

/**
 * US-143 in a real browser against the running stack: an Admin opens the Admin overview from the sidebar, sees the
 * attention list and the cards, follows the Security card to the audit log filtered to security events, and moves
 * between areas with the Admin sub-navigation. No CSP violations.
 */
const secrets = resolve(dirname(fileURLToPath(import.meta.url)), "../../../deploy/compose/secrets");
const password = (name: string) => readFileSync(resolve(secrets, `dev_${name}_password`), "utf8").trim();
const shots = process.env.TELA_SHOTS;

test("an Admin reads the overview and follows its cards", async ({ page }) => {
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

  await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Overview" }).click();
  await expect(page).toHaveURL(/\/admin$/);
  await expect(page.getByRole("heading", { name: "Admin overview" })).toBeVisible();
  await expect(page.getByRole("region", { name: "Needs your attention" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Background jobs" })).toBeVisible();
  await expect(page.getByText("AI usage today")).toBeVisible();
  if (shots) {
    await page.screenshot({ path: `${shots}/admin-overview-light.png`, fullPage: true });
    await page.emulateMedia({ colorScheme: "dark" });
    await page.waitForTimeout(400); // let colour transitions finish
    await page.screenshot({ path: `${shots}/admin-overview-dark.png`, fullPage: true });
    await page.emulateMedia({ colorScheme: "light" });
  }

  await page.getByRole("main").getByRole("link", { name: /^Security, last 7 days/ }).click();
  await expect(page).toHaveURL(/\/admin\/audit\?security=true&from=/);
  await expect(page.getByRole("switch", { name: "Security events only" })).toBeChecked();
  const admin = page.getByRole("navigation", { name: "Admin" });
  // Platform has two screens (Audit log, AI settings since US-146), so the screen is the current page.
  await expect(admin.getByRole("link", { name: "Audit log" })).toHaveAttribute("aria-current", "page");

  await admin.getByRole("link", { name: "Content" }).click();
  await expect(page).toHaveURL(/\/govern\/certification/);
  await admin.getByRole("link", { name: "Publish requests" }).click();
  await expect(page.getByRole("heading", { name: "Publish requests" })).toBeVisible();
  await admin.getByRole("link", { name: "Overview" }).click();
  await expect(page.getByRole("heading", { name: "Admin overview" })).toBeVisible();
  expect(csp).toEqual([]);
});
