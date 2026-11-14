import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { expect, test, type Browser, type Page } from "@playwright/test";

/**
 * US-142 in a real browser against the running stack: a Creator composes a dashboard (adds a report, reorders with the
 * keyboard, previews), shares it with a per-person warning, and requests publish through the steps; an Admin finds the
 * request under Govern → Publish requests, previews and checks it, and returns it with a note, which the Creator sees.
 * No CSP violations.
 */
const secrets = resolve(dirname(fileURLToPath(import.meta.url)), "../../../deploy/compose/secrets");
const password = (name: string) => readFileSync(resolve(secrets, `dev_${name}_password`), "utf8").trim();

async function signIn(browser: Browser, username: string, pw: string): Promise<{ page: Page; csp: string[] }> {
  const page = await (await browser.newContext()).newPage();
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
  return { page, csp };
}

test("a Creator composes and requests publish; an Admin reviews and returns it with a note", async ({ browser }) => {
  test.setTimeout(120_000);
  const title = `E2E publish ${Date.now()}`;
  const creator = await signIn(browser, "nomsa", password("creator"));
  const page = creator.page;

  // Compose: a new dashboard, two reports, keyboard reorder, live preview.
  await page.goto("/dashboards/new");
  await page.waitForURL(/\/dashboards\/[^/]+\/edit$/);
  const titleInput = page.getByLabel("Dashboard title");
  await titleInput.fill(title);
  await titleInput.press("Enter");
  for (let i = 0; i < 2; i++) {
    await page.getByRole("button", { name: "Add a report" }).click();
    const option = page.getByRole("option").first();
    test.skip(!(await option.isVisible().catch(() => false)), "nomsa needs two reports (run authoring.spec first)");
    await option.click();
    await expect(page.getByRole("list", { name: "Reports on this dashboard" }).getByRole("listitem")).toHaveCount(i + 1);
  }
  const list = page.getByRole("list", { name: "Reports on this dashboard" });
  const first = await list.getByRole("listitem").first().getByRole("link").textContent();
  await list.getByRole("button", { name: /position 1$/ }).press("Alt+ArrowDown");
  await expect(list.getByRole("listitem").nth(1).getByRole("link")).toHaveText(first!);
  await expect(page.getByRole("status").filter({ hasText: /Saved \d/ })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Preview" })).toBeVisible();
  await expect(page.getByRole("tab").nth(1)).toHaveText(first!);

  // Share: the dialog warns about a colleague without data access before anything is shared.
  await page.getByRole("button", { name: "Share" }).click();
  const share = page.getByRole("dialog");
  await share.getByRole("combobox", { name: "People" }).fill("Vera");
  await share.getByRole("option", { name: /Vera Viewer/ }).click();
  await expect(share.getByText(/Vera Viewer cannot use/)).toBeVisible();
  await share.getByRole("button", { name: "Cancel" }).click();

  // Request publish through the steps.
  await page.getByRole("link", { name: "Request publish" }).click();
  await page.getByRole("checkbox", { name: "CIO" }).check();
  await page.getByRole("button", { name: "Next: preview" }).click();
  await expect(page.getByRole("cell", { name: "CIO" })).toBeVisible();
  await page.getByRole("button", { name: "Next: checks" }).click();
  await expect(page.getByText("Admin step")).toBeVisible({ timeout: 30_000 });
  await page.getByRole("button", { name: "Next: release note" }).click();
  await page.getByLabel("Release note").fill("Please publish for the CIOs.");
  await page.getByRole("button", { name: "Send request" }).click();
  await expect(page.getByText(/You asked .* to publish this to CIO/)).toBeVisible();

  // The Admin reviews it from Govern → Publish requests and returns it.
  const admin = await signIn(browser, "admin", password("admin"));
  await admin.page.getByRole("link", { name: "Publish requests" }).click();
  await admin.page.getByRole("link", { name: title }).click();
  await expect(admin.page.getByText("“Please publish for the CIOs.”")).toBeVisible();
  await expect(admin.page.getByRole("checkbox", { name: "CIO" })).toBeChecked();
  await admin.page.getByRole("button", { name: "Next: preview" }).click();
  await expect(admin.page.getByRole("cell", { name: "CIO" })).toBeVisible();
  await admin.page.getByRole("button", { name: "Next: checks" }).click();
  await expect(admin.page.getByRole("list", { name: "Pre-publish checks" })).toBeVisible({ timeout: 30_000 });
  await admin.page.getByRole("button", { name: /Return to .* with a note/ }).click();
  await admin.page.getByLabel("Note for the requester").fill("Add the finance report first.");
  await admin.page.getByRole("button", { name: "Return with note" }).click();
  await expect(admin.page.getByRole("heading", { name: "Publish requests" })).toBeVisible();
  await expect(admin.page.getByRole("link", { name: title })).toHaveCount(0);

  await page.reload();
  await expect(page.getByText("“Add the finance report first.”")).toBeVisible();
  expect([...creator.csp, ...admin.csp]).toEqual([]);
});
