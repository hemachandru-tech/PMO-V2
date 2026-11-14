import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { expect, test, type Page } from "@playwright/test";

/**
 * P1 journeys in a real browser against the running stack (core + dev profiles, model approved by the
 * smoke suite): Home → starter dashboard (TEST-050 filter bar, locked chip, charts), definition (TEST-052),
 * drill (TEST-053), Ask (TEST-020), and the Admin data screens. Every page must render without CSP violations.
 */
const secrets = resolve(dirname(fileURLToPath(import.meta.url)), "../../../deploy/compose/secrets");
const password = (name: string) => readFileSync(resolve(secrets, `dev_${name}_password`), "utf8").trim();

async function signIn(page: Page, username: string, pw: string) {
  const csp: string[] = [];
  page.on("console", (msg) => {
    if (msg.type() === "error" && /Content Security Policy/i.test(msg.text())) csp.push(msg.text());
  });
  await page.goto("/");
  await page.waitForURL(/auth\.localhost:8088\/realms\/tela\/protocol\/openid-connect\/auth/);
  await page.locator("#username").fill(username);
  await page.locator("#password").fill(pw);
  await page.locator("#kc-login").click();
  await page.waitForURL("http://tela.localhost:8088/");
  return csp;
}

test("CIO reads the starter dashboard: filters, locked scope, definition and drill", async ({ page }) => {
  const csp = await signIn(page, "nomsa", password("creator"));
  const starter = page.getByRole("region", { name: "Starter" });
  await starter.getByRole("link", { name: "PMO — Projects" }).click();
  await expect(page.getByRole("heading", { level: 1 })).toContainText("PMO — Projects");
  await expect(page.getByLabel(/^Your data scope:/)).toBeVisible();
  const openFigure = page.getByRole("article", { name: "Open projects", exact: true });
  const figure = openFigure.getByRole("button").filter({ hasText: /^\d/ });
  const before = await figure.innerText();
  await page.getByRole("button", { name: "Filter by Region" }).click();
  await page.getByRole("dialog", { name: "Region values" }).getByRole("checkbox").first().click();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("button", { name: /^Region: .+ Change filter$/ })).toBeVisible();
  await expect(figure).not.toHaveText(before, { timeout: 15_000 });
  await expect(page.locator("canvas").first()).toBeVisible();
  await openFigure.getByRole("button", { name: "How Open projects is defined" }).click();
  const popover = page.getByRole("dialog", { name: "Definition of Open projects" });
  await expect(popover.getByText(/Projects with status Active/)).toBeVisible();
  await expect(popover.getByText(/Within your scope/)).toBeVisible();
  await page.keyboard.press("Escape");
  const byRegion = page.getByRole("article", { name: "Open projects by region" });
  await byRegion.getByRole("button", { name: "Table" }).click();
  await byRegion.getByRole("table").getByRole("cell").getByRole("button").first().click();
  const sheet = page.getByRole("dialog").filter({ hasText: "Drill to records" });
  await expect(sheet.getByText(/records/).first()).toBeVisible();
  await expect(sheet.getByRole("table")).toContainText("Nomsa Osei");
  await page.keyboard.press("Escape");
  await expect(sheet).toBeHidden();
  await page.getByRole("tab", { name: "CAPEX budget vs forecast" }).click();
  await expect(page.getByRole("article", { name: "Forecast cost (EAC)" })).toBeVisible();
  expect(csp).toEqual([]);
});

test("ask a question and get a certified, scoped answer card", async ({ page }) => {
  const csp = await signIn(page, "nomsa", password("creator"));
  await page.getByRole("searchbox", { name: "Ask anything about your data" }).fill("Which regions have the most overdue go-lives?");
  // The top bar's Ask; Home has its own once it has loaded.
  await page.getByRole("banner").getByRole("button", { name: "Ask", exact: true }).click();
  const card = page.getByRole("article", { name: "Answer" });
  await expect(card.getByRole("heading")).toContainText(/overdue go-lives/, { timeout: 30_000 });
  await expect(card.getByText("Certified", { exact: true }).first()).toBeVisible();
  await page.getByLabel("Ask a follow-up").fill("total cost by region");
  await page.getByRole("button", { name: "Ask", exact: true }).last().click();
  await page.getByRole("button", { name: "Forecast cost (EAC, USD)" }).click();
  await expect(page.getByRole("article", { name: "Answer" }).nth(1).getByRole("heading")).toContainText(/forecast cost/i, { timeout: 30_000 });
  expect(csp).toEqual([]);
});

test("admin sees connections and the model studio", async ({ page }) => {
  const csp = await signIn(page, "admin", password("admin"));
  await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Data", exact: true }).click();
  const row = page.getByRole("row", { name: /Sample TPG/ });
  await expect(row.getByText("connected")).toBeVisible();
  await row.getByRole("link", { name: /^v\d/ }).click();
  await expect(page.getByRole("heading", { name: /model · draft v\d+/ })).toBeVisible();
  await page.getByLabel("Describe a correction").fill("Treat Yellow as Amber everywhere");
  await page.getByRole("button", { name: "Send" }).click();
  await expect(page.getByRole("article", { name: /Proposal|Change list/ }).first()).toContainText("Yellow");
  expect(csp).toEqual([]);
});
