import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { expect, test, type Page } from "@playwright/test";

/** J2 sign-in and Home (TEST-001) and the Admin access screen (FEAT-002) in a real browser. */
const secrets = resolve(dirname(fileURLToPath(import.meta.url)), "../../../deploy/compose/secrets");
const password = (name: string) => readFileSync(resolve(secrets, `dev_${name}_password`), "utf8").trim();

async function signIn(page: Page, username: string, pw: string) {
  const cspViolations: string[] = [];
  page.on("console", (msg) => {
    if (msg.type() === "error" && /Content Security Policy/i.test(msg.text())) cspViolations.push(msg.text());
  });
  await page.goto("/");
  await page.waitForURL(/auth\.localhost:8088\/realms\/tela\/protocol\/openid-connect\/auth/);
  await page.locator("#username").fill(username);
  await page.locator("#password").fill(pw);
  await page.locator("#kc-login").click();
  await page.waitForURL("http://tela.localhost:8088/");
  return cspViolations;
}

test("viewer signs in with PKCE, sees Home, no Admin menu, no token in web storage", async ({ page }) => {
  const csp = await signIn(page, "viewer", password("viewer"));
  await expect(page.getByRole("heading", { level: 1 })).toContainText("Vera");
  await expect(page.getByRole("searchbox", { name: "Ask anything about your data" })).toBeVisible();
  const nav = page.getByRole("navigation", { name: "Main" });
  await expect(nav.getByRole("link", { name: "Access", exact: true })).toHaveCount(0);
  await expect(nav.getByText("Administer")).toHaveCount(0);

  const stored = await page.evaluate(() => JSON.stringify({ ...window.localStorage }) + JSON.stringify({ ...window.sessionStorage }));
  expect(stored).not.toMatch(/eyJ[A-Za-z0-9_-]{10,}\./); // no JWTs persisted in browser storage
  expect(csp).toEqual([]);
});

test("admin opens Access and sees the seeded group mappings", async ({ page }) => {
  const csp = await signIn(page, "admin", password("admin"));
  await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Access", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Access" })).toBeVisible();
  const table = page.getByRole("table");
  await expect(table.getByText("TELA-Admins").first()).toBeVisible();
  await expect(table.getByText("TELA-Creators").first()).toBeVisible();
  await expect(table.getByText("Active").first()).toBeVisible();
  expect(csp).toEqual([]);
});

test("sign out ends the broker session, so the next visit asks for credentials again", async ({ page }) => {
  await signIn(page, "nomsa", password("creator"));
  await expect(page.getByRole("heading", { level: 1 })).toContainText("Nomsa");
  await page.getByRole("button", { name: "Account menu" }).click();
  await page.getByRole("menuitem", { name: "Sign out" }).click();
  // Post-logout redirect returns to the app, which redirects to Keycloak; with the SSO session gone
  // the login form is shown instead of an automatic sign-in.
  await expect(page.locator("#kc-form-login")).toBeVisible({ timeout: 20_000 });
  await page.goto("/");
  await expect(page.locator("#kc-form-login")).toBeVisible({ timeout: 20_000 });
});
