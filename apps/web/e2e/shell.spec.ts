import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { expect, test, type Page } from "@playwright/test";

/** US-137: role-aware sidebar, command palette, drawer below 1024 px and skip link, under the strict CSP. */
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

test("admin navigates by group, with the command palette and breadcrumbs", async ({ page }) => {
  const csp = await signIn(page, "admin", password("admin"));
  const nav = page.getByRole("navigation", { name: "Main" });
  for (const group of ["Explore", "Build", "Administer"]) await expect(nav.getByText(group, { exact: true })).toBeVisible();

  await page.keyboard.press("Control+k");
  const palette = page.getByRole("dialog", { name: "Command palette" });
  await palette.getByRole("combobox").fill("audit");
  await expect(palette.getByRole("option", { name: /Audit log/ })).toHaveAttribute("aria-selected", "true");
  await page.keyboard.press("Enter");
  await expect(page.getByRole("heading", { name: "Audit log" })).toBeVisible();
  await expect(nav.getByRole("link", { name: "Audit log" })).toHaveAttribute("aria-current", "page");
  await expect(page.getByRole("navigation", { name: "Breadcrumb" })).toContainText("Administer/Audit log");
  await expect(page).toHaveTitle("Audit log · Tela");
  expect(csp).toEqual([]);
});

test("below 1024 px the sidebar is a drawer, and the skip link comes first", async ({ page }) => {
  await page.setViewportSize({ width: 800, height: 900 });
  const csp = await signIn(page, "viewer", password("viewer"));
  await expect(page.getByRole("navigation", { name: "Main" })).toBeHidden();
  await page.getByRole("button", { name: "Open navigation" }).click();
  const drawer = page.getByRole("dialog", { name: "Navigation" });
  await drawer.getByRole("link", { name: "Shared with me" }).click();
  await expect(drawer).toBeHidden();
  await expect(page.getByRole("heading", { name: "Shared with me" })).toBeVisible();
  // Closing the drawer returns the focus to its button (US-148).
  await expect(page.getByRole("button", { name: "Open navigation" })).toBeFocused();

  // The skip link is the only stop before it: the first on the page.
  await page.keyboard.press("Shift+Tab");
  await expect(page.getByRole("link", { name: "Skip to content" })).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(page.locator("#main")).toBeFocused();
  expect(csp).toEqual([]);
});
