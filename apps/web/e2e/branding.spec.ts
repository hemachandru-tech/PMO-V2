import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

import { matchSystemTheme, openRoute, settle, signIn } from "./support";

/**
 * ENH-006 / US-149 on the live stack: an Admin renames the app to "Insights Intelligence" and uploads an SVG logo;
 * the sidebar, tab title, favicon and a Viewer's session follow. The branding is reset at the end.
 */
const LOGO = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><rect width="64" height="64" rx="12" fill="#5b3cc4"/>
<path d="M18 46V18h8v28zm14 0V18h8l8 14V18h0v28h-8l-8-14v14z" fill="#fff"/></svg>`;

test("an Admin renames the app and uploads a logo; everyone sees it", async ({ page, browser }) => {
  test.setTimeout(120_000);
  const csp = await signIn(page, "admin");
  await openRoute(page, "/admin/branding");
  await expect(page.getByRole("heading", { name: "Branding" })).toBeVisible();
  try {
    const name = page.getByRole("textbox", { name: "App name" });
    await name.fill("Insights Intelligence");
    await page.getByRole("textbox", { name: /Tagline/ }).fill("Portfolio insights");
    await page.getByRole("button", { name: "Save", exact: true }).click();
    await expect(page.getByText("Saved — everyone now sees Insights Intelligence")).toBeVisible();

    await page.locator("#brand-logo_light").setInputFiles({ name: "logo.svg", mimeType: "image/svg+xml", buffer: Buffer.from(LOGO) });
    await expect(page.getByText("Logo uploaded")).toBeVisible();
    await expect(page.getByText(/^SVG · 64 × 64 px/)).toBeVisible();

    const nav = page.getByRole("navigation", { name: "Main" });
    await expect(nav.getByText("Insights Intelligence")).toBeVisible();
    await expect(nav.getByText("Portfolio insights")).toBeVisible();
    await expect(page).toHaveTitle("Branding · Insights Intelligence");
    const logo = nav.locator("img").first();
    await expect(logo).toHaveAttribute("src", /\/api\/v1\/branding\/assets\/.+\/logo_light\?v=\d+$/);
    expect(await logo.evaluate((img: HTMLImageElement) => img.naturalWidth)).toBeGreaterThan(0);
    await expect(page.locator("link[rel='icon']")).toHaveAttribute("href", /logo_light/);

    // An unsafe SVG is refused with the reason.
    await page.locator("#brand-logo_dark").setInputFiles({ name: "evil.svg", mimeType: "image/svg+xml",
      buffer: Buffer.from('<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>') });
    await expect(page.getByText("The SVG contains a <script> element, which isn't allowed in a logo.")).toBeVisible();
    await page.screenshot({ path: "test-results/branding-admin.png", fullPage: true });

    // A Viewer signing in afterwards sees the new name.
    const viewer = await browser.newPage();
    await signIn(viewer, "viewer");
    await expect(viewer.getByRole("navigation", { name: "Main" }).getByText("Insights Intelligence")).toBeVisible();
    await expect(viewer).toHaveTitle(/· Insights Intelligence$/);
    await viewer.close();
    expect(csp).toEqual([]);
  } finally {
    await openRoute(page, "/admin/branding");
    const reset = page.getByRole("button", { name: "Reset to defaults" });
    if (await reset.isVisible()) {
      await reset.click();
      await page.getByRole("button", { name: "Reset branding" }).click();
      await expect(page.getByRole("navigation", { name: "Main" }).getByText("Tela", { exact: true })).toBeVisible();
    }
  }
});

/**
 * US-150 on the live stack: palettes are made from the logo the browser reads under the real CSP (first the Tela
 * logo, then an uploaded purple SVG); the Admin previews one, saves it, and a red accent of their own is refused.
 */
test("an Admin picks a palette made from the logo", async ({ page }) => {
  test.setTimeout(120_000);
  const csp = await signIn(page, "admin");
  await openRoute(page, "/admin/branding");
  const group = page.getByRole("radiogroup", { name: "Palette" });
  try {
    await expect(page.getByLabel("Colours found in the logo").locator("[title^='#']").first()).toBeVisible();
    await expect(group.getByRole("radio")).toHaveCount(6); // Tela + 5 from the Tela logo
    await expect(group.getByRole("radio", { name: /Tela/ })).toBeChecked();
    await group.getByRole("radio").nth(2).click();
    const preview = page.getByLabel("Palette preview");
    await expect(preview.getByText(/^All \d+ checks pass$/)).toBeVisible();
    const accent = await preview.locator("[data-theme='light']").evaluate((el: HTMLElement) => el.style.getPropertyValue("--tl-accent"));
    expect(accent).toMatch(/^#[0-9a-f]{6}$/);
    const button = preview.locator("[data-theme='light']").getByRole("button", { name: "Primary" });
    await expect.poll(() => button.evaluate((el) => getComputedStyle(el).backgroundColor)).toBe( // after the colour transition
      `rgb(${[1, 3, 5].map((i) => parseInt(accent.slice(i, i + 2), 16)).join(", ")})`);
    const name = (await group.getByRole("radio").nth(2).locator("..").innerText()).split("\n")[0]!;
    await page.getByRole("button", { name: `Use ${name}` }).click();
    await expect(page.getByText(`Palette “${name}” saved`)).toBeVisible();
    await expect(group.getByText("Current")).toBeVisible();

    await page.getByLabel("Or start from your own accent colour").fill("#d62828");
    await page.getByRole("button", { name: "Try this colour" }).click();
    await expect(page.getByText(/reads as red, which means Off track/)).toBeVisible();

    await page.locator("#brand-logo_light").setInputFiles({ name: "logo.svg", mimeType: "image/svg+xml", buffer: Buffer.from(LOGO) });
    await expect(page.getByText("Logo uploaded")).toBeVisible();
    await expect(page.getByLabel("Colours found in the logo").locator("[title='#5b3cc4']")).toBeVisible();
    await expect(group.getByRole("radio").nth(1)).toHaveAccessibleName(/Indigo|Violet/);
    await page.screenshot({ path: "test-results/branding-palettes.png", fullPage: true });
    expect(csp).toEqual([]);
  } finally {
    await openRoute(page, "/admin/branding");
    const reset = page.getByRole("button", { name: "Reset to defaults" });
    if (await reset.isVisible()) {
      await reset.click();
      await page.getByRole("button", { name: "Reset branding" }).click();
      await expect(group.getByRole("radio", { name: /Tela/ })).toBeChecked();
    }
  }
});

async function resetBranding(page: Page) {
  await openRoute(page, "/admin/branding");
  const reset = page.getByRole("button", { name: "Reset to defaults" });
  if (await reset.isVisible()) {
    await reset.click();
    await page.getByRole("button", { name: "Reset branding" }).click();
    await expect(page.getByRole("navigation", { name: "Main" }).getByText("Tela", { exact: true })).toBeVisible();
  }
}

const accentOf = (page: Page, theme: "light" | "dark") => page.evaluate((t) => {
  const probe = document.createElement("div");
  probe.dataset.theme = t;
  document.body.appendChild(probe);
  const v = getComputedStyle(probe).getPropertyValue("--tl-accent").trim();
  probe.remove();
  return v;
}, theme);

/**
 * US-151 / US-152 on the live stack: an Admin uploads a logo, picks a palette, renames the app to "Insights
 * Intelligence" and makes the sidebar logo taller; a Viewer signing in afterwards sees the name, logo, size and colours
 * in both themes, axe-clean. The Admin then rolls back to the version before the palette; the branding is reset at the end.
 */
test("the branding applies across the app for everyone, and rolls back", async ({ page, browser }) => {
  test.setTimeout(180_000);
  const csp = await signIn(page, "admin");
  await openRoute(page, "/admin/branding");
  try {
    await page.getByRole("textbox", { name: "App name" }).fill("Insights Intelligence");
    await page.getByRole("button", { name: "Save", exact: true }).click();
    await expect(page.getByText("Saved — everyone now sees Insights Intelligence")).toBeVisible();
    await page.locator("#brand-logo_light").setInputFiles({ name: "logo.svg", mimeType: "image/svg+xml", buffer: Buffer.from(LOGO) });
    await expect(page.getByText("Logo uploaded")).toBeVisible();

    // Logo size (US-152): the preview follows before saving, the sidebar after.
    const sizes = page.getByRole("form", { name: "Logo size" });
    await sizes.getByLabel("Sidebar logo height in pixels").fill("44");
    await expect(page.getByLabel("Logo size preview").locator("[data-preview='sidebar']").first()).toHaveCSS("height", "44px");
    await sizes.getByRole("button", { name: "Save sizes" }).click();
    await expect(page.getByText("Logo size saved — everyone sees it now")).toBeVisible();
    const nav = page.getByRole("navigation", { name: "Main" });
    await expect(nav.locator("img[data-logo='sidebar']")).toHaveCSS("height", "44px");

    // A palette from the purple logo, applied to the whole app straight away (US-151).
    const group = page.getByRole("radiogroup", { name: "Palette" });
    await expect(group.getByRole("radio").nth(1)).toHaveAccessibleName(/Indigo|Violet/);
    await group.getByRole("radio").nth(1).click();
    const preview = page.getByLabel("Palette preview");
    const light = await preview.locator("[data-theme='light']").evaluate((el: HTMLElement) => el.style.getPropertyValue("--tl-accent"));
    const dark = await preview.locator("[data-theme='dark']").evaluate((el: HTMLElement) => el.style.getPropertyValue("--tl-accent"));
    const name = (await group.getByRole("radio").nth(1).locator("..").innerText()).split("\n")[0]!;
    await page.getByRole("button", { name: `Use ${name}` }).click();
    await expect(page.getByText(`Palette “${name}” saved`)).toBeVisible();
    await expect.poll(() => accentOf(page, "light")).toBe(light);
    expect(await accentOf(page, "dark")).toBe(dark);
    await page.screenshot({ path: "test-results/branding-applied-admin.png", fullPage: true });

    // A Viewer signing in afterwards: name, logo and size, and the colours in both themes — axe-clean in each.
    const ctx = await browser.newContext();
    const viewer = await ctx.newPage();
    const viewerCsp = await signIn(viewer, "viewer");
    const vnav = viewer.getByRole("navigation", { name: "Main" });
    await expect(vnav.getByText("Insights Intelligence")).toBeVisible();
    await expect(vnav.locator("img[data-logo='sidebar']")).toHaveAttribute("src", /logo_light\?v=\d+$/);
    await expect(vnav.locator("img[data-logo='sidebar']")).toHaveCSS("height", "44px");
    await matchSystemTheme(viewer);
    for (const theme of ["light", "dark"] as const) {
      await viewer.emulateMedia({ colorScheme: theme });
      await expect.poll(() => viewer.evaluate(() => document.documentElement.dataset.theme)).toBe(theme);
      const want = theme === "light" ? light : dark;
      await expect.poll(() => viewer.evaluate(() => getComputedStyle(document.documentElement).getPropertyValue("--tl-accent").trim())).toBe(want);
      await settle(viewer);
      const axe = await new AxeBuilder({ page: viewer }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"]).analyze();
      const bad = axe.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
      expect(bad.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`)).toEqual([]);
      await viewer.screenshot({ path: `test-results/branding-applied-viewer-${theme}.png`, fullPage: true });
    }
    expect(viewerCsp).toEqual([]);
    await ctx.close();

    // History: roll back to the version before the palette; the Tela colours come back, the name and logo stay.
    const versions = page.getByRole("list", { name: "Branding versions" });
    await expect(versions.getByRole("listitem").first()).toContainText("Colours changed");
    const before = versions.getByRole("listitem").nth(1);
    await expect(before).toContainText("Logo size changed");
    await before.getByRole("button", { name: /Roll back/ }).click();
    await page.getByRole("dialog").getByRole("button", { name: "Roll back" }).click();
    await expect(page.getByText(/Rolled back to version \d+/)).toBeVisible();
    await expect.poll(() => accentOf(page, "light")).toBe("#236f8d");
    await expect(page.getByRole("navigation", { name: "Main" }).getByText("Insights Intelligence")).toBeVisible();
    await expect(versions.getByRole("listitem").first()).toContainText("Rolled back");
    expect(csp).toEqual([]);
  } finally {
    await resetBranding(page);
  }
});
