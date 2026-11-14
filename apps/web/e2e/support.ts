import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import type { Page } from "@playwright/test";

/** Shared helpers for the US-148 gates (accessibility, keyboard, screenshots, performance) on the live stack. */
const secrets = resolve(dirname(fileURLToPath(import.meta.url)), "../../../deploy/compose/secrets");
export const password = (name: string) => readFileSync(resolve(secrets, `dev_${name}_password`), "utf8").trim();

export const USERS = {
  admin: { username: "admin", password: () => password("admin") },
  nomsa: { username: "nomsa", password: () => password("creator") },
  viewer: { username: "viewer", password: () => password("viewer") },
} as const;
export type UserKey = keyof typeof USERS;

export const ORIGIN = "http://tela.localhost:8088";

/** Signs in through Keycloak and returns the list of CSP violations seen on the page from then on. */
export async function signIn(page: Page, user: UserKey) {
  const csp: string[] = [];
  page.on("console", (msg) => {
    if (msg.type() === "error" && /Content Security Policy/i.test(msg.text())) csp.push(msg.text());
  });
  await page.goto("/");
  await page.waitForURL(/auth\.localhost:8088/);
  await page.locator("#username").fill(USERS[user].username);
  await page.locator("#password").fill(USERS[user].password());
  await page.locator("#kc-login").click();
  await page.waitForURL(`${ORIGIN}/`);
  return csp;
}

/**
 * Opens a route inside the signed-in app without a reload (tokens live in memory, so a full navigation would run
 * the sign-in redirect again): push the URL and let React Router render it.
 */
export async function openRoute(page: Page, path: string) {
  const target = new URL(path, ORIGIN);
  const here = new URL(page.url());
  if (here.pathname === target.pathname && here.search === target.search) return;
  await page.evaluate((p) => {
    window.history.pushState({}, "", p);
    window.dispatchEvent(new PopStateEvent("popstate"));
  }, path);
  // Screens may add their own state to the query string (publish steps), so match the path only.
  await page.waitForURL((u) => u.pathname === target.pathname);
}

/** Waits until nothing on the page is loading: no busy regions, skeletons or pending API calls. */
export async function settle(page: Page) {
  await page.waitForLoadState("networkidle");
  await page
    .waitForFunction(() => !document.querySelector('[aria-busy="true"], [data-slot="skeleton"]'), undefined, { timeout: 15_000 })
    .catch(() => undefined);
  // Fonts and ECharts' first frame.
  await page.evaluate(() => document.fonts.ready);
  await page.waitForTimeout(300);
}

/** Sets the theme to follow the (emulated) OS setting, so a test can choose light or dark with emulateMedia. */
export async function matchSystemTheme(page: Page) {
  await page.getByRole("button", { name: "Account menu" }).click();
  const item = page.getByRole("menuitemradio", { name: "Match my system" });
  if ((await item.getAttribute("aria-checked")) !== "true") await item.click();
  else await page.keyboard.press("Escape");
}
