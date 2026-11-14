import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { expect, test, type Page } from "@playwright/test";

/**
 * P2 journeys in a real browser against the running stack (after the smoke suite): plan and build a report with
 * chat (TEST-026/027), arrange it on the canvas with chat edit and Undo (TEST-028/029), pin an answer with the
 * visual picker (TEST-024), and publish through the checks as the Admin (TEST-046/047). No CSP violations.
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

test("a Creator plans, builds and edits a report, then pins an answer to it", async ({ page }) => {
  const csp = await signIn(page, "nomsa", password("creator"));
  await page.getByRole("link", { name: "Build with chat" }).click();
  await page.getByLabel("Describe the report").fill("Delivery report with overdue go-lives by region and team workload");
  await page.getByRole("button", { name: "Plan" }).click();
  const plan = page.getByLabel("Report plan");
  await expect(plan.getByText("Overdue go-lives by region").first()).toBeVisible({ timeout: 30_000 });
  await expect(page.getByRole("region", { name: "Not included" })).toContainText("workload");
  await page.getByLabel("Change the plan").fill("remove Overdue go-lives by platform");
  await plan.getByRole("button", { name: "Apply" }).click();
  await expect(plan.getByLabel("Changes")).toContainText("Overdue go-lives by platform");
  await page.getByRole("button", { name: "Build report" }).click();
  await expect(page.getByRole("article", { name: "Overdue go-lives by region" })).toBeVisible({ timeout: 30_000 });
  await expect(page.getByText(/^Built \d+ widgets?$/)).toBeVisible();

  await page.getByRole("button", { name: "Edit with chat" }).click();
  await page.getByLabel("Describe a change to this report").fill("make Overdue go-lives by region full width");
  await page.getByRole("button", { name: "Preview", exact: true }).click();
  await expect(page.getByText(/Resize Overdue go-lives by region to 12/)).toBeVisible();
  await page.getByRole("region", { name: "Proposed changes" }).getByRole("button", { name: "Apply" }).click();
  await expect(page.getByRole("button", { name: /^Undo: Chat edit/ })).toBeEnabled();
  // Keyboard move (US-141): focus the widget, Shift + arrow narrows it, announced and autosaved.
  const widget = page.getByRole("group", { name: "Overdue go-lives by region" });
  await widget.focus();
  await page.keyboard.press("Shift+ArrowLeft");
  await expect(page.getByText(/^Overdue go-lives by region: column \d+, row \d+, 11 of 12 columns wide/)).toBeAttached();
  await expect(page.getByText("Layout saved")).toBeVisible();
  await expect(page.getByRole("button", { name: "Undo: Rearranged the layout" })).toBeEnabled();
  const reportUrl = page.url();

  // Opening edit with chat at 1280 px hides the library; the Library button brings it back.
  await page.getByRole("button", { name: "Library" }).click();
  await page.getByRole("link", { name: "Ask for a new widget" }).click();
  await page.getByLabel("Ask a follow-up").fill("Open projects by region");
  await page.getByRole("button", { name: "Ask", exact: true }).last().click();
  await page.getByRole("button", { name: "Pin" }).click();
  const dialog = page.getByRole("dialog", { name: "Pin to a report" });
  await expect(dialog.getByRole("radio", { name: /Trend/ })).toBeDisabled();
  await dialog.getByRole("button", { name: "Pin" }).click();
  await expect(dialog.getByText(/Pinned as a live widget|already in your library|already on this report/)).toBeVisible();
  await page.goto(reportUrl);
  await expect(page.getByRole("article", { name: /Open projects by region/ }).first()).toBeVisible({ timeout: 30_000 });
  expect(csp).toEqual([]);
});
