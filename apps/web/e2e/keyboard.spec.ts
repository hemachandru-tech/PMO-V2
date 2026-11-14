import { expect, test, type Locator, type Page } from "@playwright/test";

import { signIn } from "./support";

/**
 * US-148: journeys J1, J5 and J9 with the keyboard only. After sign-in nothing is clicked: focus moves with Tab,
 * controls are used with Enter, Space, arrows and Escape, and the command palette (Ctrl+K) moves between screens.
 * Each journey checks that focus lands somewhere sensible (in a dialog when it opens, back on its trigger when it
 * closes). Content created here is removed again (J5's report) or unpublished (J9's dashboard).
 */

/**
 * Presses Tab (or Shift+Tab) until `target` has the focus; fails if it never does, which is a focus-order bug. The cap
 * is a whole-page walk with room to spare: the J5 canvas has about 125 stops (library, sections, widgets, shell).
 */
async function tabTo(page: Page, target: Locator, { back = false, max = 200 } = {}) {
  await target.first().waitFor();
  for (let i = 0; i < max; i++) {
    if (await target.first().evaluate((el) => el === document.activeElement || el.contains(document.activeElement))) return;
    await page.keyboard.press(back ? "Shift+Tab" : "Tab");
  }
  const active = await page.evaluate(() => { const a = document.activeElement; return a ? `${a.tagName} ${a.getAttribute("aria-label") ?? a.textContent?.slice(0, 40) ?? ""}` : "none"; });
  throw new Error(`Could not reach ${target} with ${back ? "Shift+Tab" : "Tab"}; the focus is on ${active}`);
}

async function palette(page: Page, text: string, option: RegExp) {
  await page.keyboard.press("Control+k");
  const dialog = page.getByRole("dialog", { name: "Command palette" });
  await expect(dialog.getByRole("combobox")).toBeFocused();
  await page.keyboard.type(text);
  await expect(dialog.getByRole("option", { name: option }).first()).toHaveAttribute("aria-selected", "true");
  await page.keyboard.press("Enter");
  await expect(dialog).toBeHidden();
}

const focusedIn = (l: Locator) => l.evaluate((el) => el.contains(document.activeElement));

test("J1 with the keyboard: ask from Home, drill to records, open the dashboard and filter it", async ({ page }) => {
  test.setTimeout(120_000);
  const csp = await signIn(page, "nomsa");

  // The ask bar in the top bar (the skip link comes first; shell.spec checks it).
  await tabTo(page, page.getByRole("searchbox", { name: "Ask anything about your data" }));
  await page.keyboard.type("Open projects by region");
  await page.keyboard.press("Enter");
  const answer = page.getByRole("article", { name: "Answer" }).last();
  await expect(answer.getByRole("heading")).toBeVisible({ timeout: 45_000 });

  // Drill to records opens a sheet with the focus inside; Escape closes it and returns the focus.
  const drill = answer.getByRole("button", { name: "Drill to records" });
  await tabTo(page, drill);
  await page.keyboard.press("Enter");
  const sheet = page.getByRole("dialog").filter({ hasText: "Drill to records" });
  await expect(sheet.getByRole("table")).toBeVisible({ timeout: 30_000 });
  expect(await focusedIn(sheet)).toBe(true);
  await page.keyboard.press("Escape");
  await expect(sheet).toBeHidden();
  await expect(drill).toBeFocused();

  // The command palette opens the dashboard; a filter chip is set from the keyboard.
  await palette(page, "PMO — Projects", /PMO — Projects/);
  await expect(page.getByRole("heading", { level: 1 })).toContainText("PMO — Projects");
  const chip = page.getByRole("button", { name: "Filter by Region" });
  await tabTo(page, chip);
  await page.keyboard.press("Enter");
  const values = page.getByRole("dialog", { name: "Region values" });
  await expect(values).toBeVisible();
  await tabTo(page, values.getByRole("checkbox").first());
  await page.keyboard.press("Space");
  await page.keyboard.press("Escape");
  await expect(page.getByRole("button", { name: /^Region: .+ Change filter$/ })).toBeFocused();
  expect(csp).toEqual([]);
});

test("J5 with the keyboard: plan a report, build it, share it, then delete it", async ({ page }) => {
  test.setTimeout(150_000);
  const csp = await signIn(page, "nomsa");

  await tabTo(page, page.getByRole("link", { name: "Build with chat" }));
  await page.keyboard.press("Enter");
  const describe = page.getByLabel("Describe the report");
  await tabTo(page, describe);
  await page.keyboard.type("Delivery report with overdue go-lives by region");
  await tabTo(page, page.getByRole("button", { name: "Plan", exact: true }));
  await page.keyboard.press("Enter");
  const build = page.getByRole("button", { name: "Build report" });
  await expect(build).toBeEnabled({ timeout: 45_000 });
  await tabTo(page, build);
  await page.keyboard.press("Enter");
  await expect(page.getByRole("article").first()).toBeVisible({ timeout: 45_000 });
  await page.waitForURL(/\/reports\/[^/]+$/);
  const title = `Keyboard J5 ${Date.now()}`;

  // Rename it so it can be found and removed; the title saves on Enter.
  const titleInput = page.getByLabel("Report title");
  await tabTo(page, titleInput, { back: true });
  await page.keyboard.press("ControlOrMeta+a");
  await page.keyboard.type(title);
  await page.keyboard.press("Enter");
  await expect(titleInput).toHaveValue(title);

  // Share: the people picker works with type, arrows and Enter; Cancel shares nothing.
  const share = page.getByRole("button", { name: "Share", exact: true });
  await tabTo(page, share);
  await page.keyboard.press("Enter");
  const dialog = page.getByRole("dialog");
  await expect(dialog).toBeVisible();
  expect(await focusedIn(dialog)).toBe(true);
  await tabTo(page, dialog.getByRole("combobox", { name: "People" }));
  await page.keyboard.type("Vera");
  // The first suggestion is already highlighted; Enter adds it.
  await expect(dialog.getByRole("option", { name: /Vera Viewer/ })).toHaveAttribute("aria-selected", "true");
  await page.keyboard.press("Enter");
  await expect(dialog.getByRole("button", { name: "Cancel" })).toBeVisible();
  await tabTo(page, dialog.getByRole("button", { name: "Cancel" }));
  await page.keyboard.press("Enter");
  await expect(dialog).toBeHidden();
  await expect(share).toBeFocused();

  // Clean up from the Reports list: Delete asks first, the focus starts on Cancel.
  await palette(page, "Reports", /^Reports/);
  await expect(page.getByRole("heading", { name: "Reports", level: 1 })).toBeVisible();
  await tabTo(page, page.getByRole("button", { name: `Delete ${title}` }));
  await page.keyboard.press("Enter");
  const confirm = page.getByRole("dialog", { name: new RegExp(`Delete “${title}”`) });
  await tabTo(page, confirm.getByRole("button", { name: "Delete report" }));
  await page.keyboard.press("Enter");
  await expect(confirm).toBeHidden();
  await expect(page.getByRole("link", { name: title })).toHaveCount(0);
  expect(csp).toEqual([]);
});

test("J9 with the keyboard: compose and request publish, then an Admin publishes, sees the version and unpublishes", async ({ browser }) => {
  test.setTimeout(240_000);
  const creator = await browser.newPage();
  const cspCreator = await signIn(creator, "nomsa");
  const title = `Keyboard J9 ${Date.now()}`;

  // New dashboard from the Dashboards list, named in its dialog.
  await palette(creator, "Dashboards", /^Dashboards/);
  await tabTo(creator, creator.getByRole("button", { name: "New dashboard" }));
  await creator.keyboard.press("Enter");
  const create = creator.getByRole("dialog", { name: "New dashboard" });
  await expect(create.getByLabel("Title")).toBeFocused();
  await creator.keyboard.press("ControlOrMeta+a");
  await creator.keyboard.type(title);
  await creator.keyboard.press("Enter");
  await creator.waitForURL(/\/dashboards\/[^/]+\/edit$/);

  // Add a report from the list with arrows and Enter.
  await tabTo(creator, creator.getByRole("button", { name: "Add a report" }));
  await creator.keyboard.press("Enter");
  const option = creator.getByRole("option").first();
  test.skip(!(await option.isVisible({ timeout: 10_000 }).catch(() => false)), "nomsa needs a report (run authoring.spec first)");
  await creator.keyboard.press("Enter");
  await expect(creator.getByRole("list", { name: "Reports on this dashboard" }).getByRole("listitem")).toHaveCount(1);

  // Request publish through the steps.
  await tabTo(creator, creator.getByRole("link", { name: "Request publish" }), { back: true });
  await creator.keyboard.press("Enter");
  await tabTo(creator, creator.getByRole("checkbox", { name: "CIO" }));
  await creator.keyboard.press("Space");
  await tabTo(creator, creator.getByRole("button", { name: "Next: preview" }));
  await creator.keyboard.press("Enter");
  await tabTo(creator, creator.getByRole("button", { name: "Next: checks" }));
  await creator.keyboard.press("Enter");
  const toNote = creator.getByRole("button", { name: "Next: release note" });
  await expect(toNote).toBeVisible({ timeout: 45_000 });
  await tabTo(creator, toNote);
  await creator.keyboard.press("Enter");
  await tabTo(creator, creator.getByLabel("Release note"));
  await creator.keyboard.type("Keyboard journey: please publish for the CIOs.");
  await tabTo(creator, creator.getByRole("button", { name: "Send request" }));
  await creator.keyboard.press("Enter");
  await expect(creator.getByText(/You asked .* to publish this to CIO/)).toBeVisible();

  // The Admin opens the request from Govern → Publish requests and publishes it.
  const admin = await browser.newPage();
  const cspAdmin = await signIn(admin, "admin");
  await palette(admin, "Publish requests", /Publish requests/);
  await tabTo(admin, admin.getByRole("link", { name: title }));
  await admin.keyboard.press("Enter");
  await expect(admin.getByRole("checkbox", { name: "CIO" })).toBeChecked();
  await tabTo(admin, admin.getByRole("button", { name: "Next: preview" }));
  await admin.keyboard.press("Enter");
  await tabTo(admin, admin.getByRole("button", { name: "Next: checks" }));
  await admin.keyboard.press("Enter");
  const checks = admin.getByRole("list", { name: "Pre-publish checks" });
  await expect(checks).toBeVisible({ timeout: 45_000 });

  // A failing check is fixed from its own fix action (for example, remove the widget), still by keyboard.
  const next = admin.getByRole("button", { name: "Next: release note" });
  for (let i = 0; i < 6 && !(await next.isEnabled()); i++) {
    const fix = checks.getByRole("button").filter({ hasNot: admin.locator(":disabled") }).first();
    test.skip(!(await fix.count()), "a check fails with no fix the keyboard journey can apply");
    await tabTo(admin, fix);
    await admin.keyboard.press("Enter");
    await admin.waitForLoadState("networkidle");
  }
  await expect(next).toBeEnabled({ timeout: 30_000 });
  await tabTo(admin, next);
  await admin.keyboard.press("Enter");
  const publish = admin.getByRole("button", { name: /^Publish to / });
  await expect(publish).toBeEnabled({ timeout: 30_000 });
  await tabTo(admin, publish);
  await admin.keyboard.press("Enter");

  // Version history shows the new live version; Unpublish (asked first) cleans up.
  await admin.waitForURL(/\/dashboards\/[^/]+\/versions$/, { timeout: 30_000 });
  await expect(admin.getByRole("heading", { name: title, level: 1 })).toBeVisible();
  await tabTo(admin, admin.getByRole("button", { name: "Unpublish" }));
  await admin.keyboard.press("Enter");
  const confirm = admin.getByRole("dialog", { name: "Unpublish this dashboard?" });
  await expect(confirm).toBeVisible();
  await tabTo(admin, confirm.getByRole("button", { name: /^Unpublish/ }));
  await admin.keyboard.press("Enter");
  await expect(admin.getByText(/Unpublished/).first()).toBeVisible();
  expect([...cspCreator, ...cspAdmin]).toEqual([]);
  await creator.close();
  await admin.close();
});
