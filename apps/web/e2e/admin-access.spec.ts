import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { expect, test, type Page } from "@playwright/test";

/**
 * US-145 in a real browser against the running stack: an Admin reads group mappings and scope policies, opens a
 * delete confirmation and cancels it, then uses the Effective access panel for a person and for a role. Nothing is
 * changed. No CSP violations.
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

test("an Admin checks who can see what without changing anything", async ({ page }) => {
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

  await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Access" }).click();
  await expect(page.getByRole("table", { name: "Group mappings" })).toBeVisible();
  await shoot(page, "access-mappings");

  await page.getByRole("link", { name: "Scope policies" }).click();
  const table = page.getByRole("table", { name: "Scope policies" });
  await expect(table).toBeVisible();
  await expect(page).toHaveURL(/connection=/);
  await table.getByRole("button", { name: "Delete" }).first().click();
  const dialog = page.getByRole("dialog");
  await expect(dialog.getByRole("button", { name: "Cancel" })).toBeFocused();
  await dialog.getByRole("button", { name: "Cancel" }).click();
  await expect(dialog).toBeHidden();

  await page.getByRole("button", { name: "Effective access" }).click();
  const panel = page.getByRole("dialog", { name: "Effective access" });
  await panel.getByRole("combobox", { name: "Person" }).fill("Nomsa");
  await panel.getByRole("option", { name: /Nomsa Osei/ }).click();
  const person = panel.getByRole("region", { name: "Access for Nomsa Osei" });
  await expect(person).toBeVisible();
  await expect(person.getByText("Groups", { exact: true })).toBeVisible();
  await shoot(page, "access-effective-person");

  await panel.getByRole("radio", { name: "A role" }).click();
  await panel.getByLabel("Role", { exact: true }).selectOption("viewer");
  await expect(panel.getByRole("region", { name: "Access for Viewer" })).toBeVisible();
  expect(csp).toEqual([]);
});
