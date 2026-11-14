import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { expect, test, type Page } from "@playwright/test";

/**
 * US-144 in a real browser against the running stack: an Admin reads the Connections status cards, opens the Add
 * connection steps (without creating anything), opens Model studio from a connection's model, and walks the approval
 * steps up to the note (without approving). No CSP violations.
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

test("an Admin reads connections, starts adding one and reviews a model for approval", async ({ page }) => {
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

  await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Data" }).click();
  await expect(page.getByRole("heading", { name: "Connections" })).toBeVisible();
  const cards = page.getByRole("list", { name: "Connection status" });
  await expect(cards.getByText("Connected")).toBeVisible();
  await shoot(page, "connections");

  await page.getByRole("link", { name: "Add connection" }).first().click();
  await expect(page).toHaveURL(/\/admin\/connections\/new$/);
  await expect(page.getByRole("button", { name: /Dataverse/ })).toBeDisabled();
  await page.getByRole("button", { name: /PostgreSQL/ }).click();
  await expect(page.getByRole("form", { name: "Connection details · PostgreSQL" })).toBeVisible();
  await shoot(page, "add-connection");
  await page.getByRole("link", { name: "Back to connections" }).click();

  // The first connection with an approved model opens Model studio.
  await page.getByRole("table", { name: "Connections" }).getByRole("link", { name: /^v\d+/ }).first().click();
  await expect(page).toHaveURL(/\/admin\/models\/[^/]+$/);
  await expect(page.getByRole("heading", { name: /model · draft v\d+/ })).toBeVisible();
  await expect(page.getByRole("table", { name: /fields$/ })).toBeVisible();
  await expect(page.getByRole("complementary", { name: "Assistant and changes" }).getByText("Changes in this draft")).toBeVisible();
  await shoot(page, "model-studio");

  await page.getByRole("link", { name: "Review and approve" }).click();
  await expect(page).toHaveURL(/\/approve$/);
  await expect(page.getByRole("navigation", { name: "Approval steps" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Checks" })).toBeVisible();
  const next = page.getByRole("button", { name: "Next" });
  if (await next.isVisible()) {
    await next.click();
    await expect(page.getByRole("heading", { name: /What this draft changes/ })).toBeVisible();
    await page.getByRole("button", { name: "Next" }).click();
    await expect(page.getByRole("heading", { name: /If you approve/ })).toBeVisible();
    await shoot(page, "approve-impact");
  }
  expect(csp).toEqual([]);
});
