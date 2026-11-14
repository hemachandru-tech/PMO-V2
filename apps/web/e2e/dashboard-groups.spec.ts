import { expect, test } from "@playwright/test";

import { openRoute, signIn } from "./support";

/**
 * ENH-009 (US-170, US-171): an Admin groups a starter dashboard on Build → Dashboards → Groups; a Viewer opens it from
 * the group in the sidebar. The group is deleted at the end so reruns start clean. No CSP violations.
 */
test("a Viewer opens a dashboard from a group an Admin made", async ({ page, browser }) => {
  const name = `E2E group ${Date.now()}`;
  const adminCsp = await signIn(page, "admin");
  await openRoute(page, "/dashboards?tab=groups");
  await page.getByRole("button", { name: "New group" }).click();
  const create = page.getByRole("dialog", { name: "New group" });
  await create.getByLabel("Name").fill(name);
  await create.getByLabel("Icon").selectOption("rocket");
  await create.getByRole("button", { name: "Create group" }).click();
  const card = page.getByRole("listitem").filter({ has: page.getByRole("heading", { name }) });
  await card.getByRole("button", { name: "Add dashboards" }).click();
  const pick = page.getByRole("dialog", { name: `Dashboards in “${name}”` });
  await pick.getByLabel("Find a dashboard").fill("PMO — Projects");
  await pick.getByRole("checkbox", { name: /PMO — Projects/ }).click();
  await pick.getByRole("button", { name: /^Save/ }).click();
  await expect(card.getByRole("link", { name: "PMO — Projects" })).toBeVisible();

  const viewerContext = await browser.newContext();
  const viewer = await viewerContext.newPage();
  const viewerCsp = await signIn(viewer, "viewer");
  const nav = viewer.getByRole("navigation", { name: "Main" });
  const group = nav.getByRole("button", { name });
  await expect(group).toHaveAttribute("aria-expanded", "false");
  await group.click();
  await nav.getByRole("link", { name: "PMO — Projects" }).click();
  await expect(viewer).toHaveURL(/\/dashboards\/[0-9a-f-]+$/);
  await expect(viewer.getByRole("heading", { level: 1, name: "PMO — Projects" })).toBeVisible();
  await expect(nav.getByRole("link", { name: "PMO — Projects" })).toHaveAttribute("aria-current", "page");
  // The page fits its column: no sideways scroll from the tab strip (EXEC-055 fix).
  expect(await viewer.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await viewerContext.close();

  await card.getByRole("button", { name: `Delete ${name}` }).click();
  await page.getByRole("dialog", { name: `Delete “${name}”?` }).getByRole("button", { name: "Delete group" }).click();
  await expect(page.getByRole("heading", { name })).toHaveCount(0);
  expect([...adminCsp, ...viewerCsp]).toEqual([]);
});
