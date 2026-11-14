import type { Page } from "@playwright/test";

import { openRoute, type UserKey } from "./support";

/**
 * Every redesigned screen (US-148), for the accessibility and screenshot gates. A screen with an id in its route is
 * reached through the UI (the starter dashboard from Home, the fixture below from its list). `mask` names parts that change with the
 * data between runs (counts, lists that grow, timestamps are masked everywhere).
 */
export interface Screen {
  name: string;
  user: UserKey;
  path?: string;
  /** Finds the route of a screen that needs an id; returns null when the stack has no such content. */
  resolve?: (page: Page) => Promise<string | null>;
  mask?: string[];
  /** Compare only the top of the page (header, filters, first rows) where a list grows with every journey run. */
  clipHeight?: number;
}

async function starterDashboard(page: Page) {
  await openRoute(page, "/");
  // Every PMO sample source has its own starter copy since P3; the first is the oldest (the TPG sample).
  const link = page.getByRole("region", { name: "Starter" }).getByRole("link", { name: "PMO — Projects" }).first();
  await link.waitFor({ timeout: 15_000 });
  return link.getAttribute("href");
}

async function model(page: Page) {
  await openRoute(page, "/admin/connections");
  const link = page.getByRole("link", { name: /^v\d/ }).first();
  await link.waitFor({ timeout: 15_000 }).catch(() => undefined);
  return (await link.count()) ? link.getAttribute("href") : null;
}

/**
 * A report and a dashboard that only the screenshot and axe runs use, so the Creator screens show the same content
 * every time. Created once through the UI (as Nomsa) when missing; never shared or published.
 */
export const FIXTURE = { report: "Visual baseline report", dashboard: "Visual baseline" };
const fixture: { report?: string; dashboard?: string } = {};

async function linkIn(page: Page, list: string, table: string, name: string) {
  await openRoute(page, list);
  await page.getByRole("table", { name: table }).or(page.getByText(/No (reports|dashboards) yet/)).first().waitFor({ timeout: 15_000 });
  const link = page.getByRole("table", { name: table }).getByRole("link", { name, exact: true }).first();
  return (await link.count()) ? link.getAttribute("href") : null;
}

async function fixtureReport(page: Page) {
  fixture.report ??= (await linkIn(page, "/reports", "Reports", FIXTURE.report)) ?? undefined;
  if (fixture.report) return fixture.report;
  await openRoute(page, "/reports/new");
  await page.getByLabel("Describe the report").fill("Delivery report with overdue go-lives by region");
  await page.getByRole("button", { name: "Plan", exact: true }).click();
  await page.getByRole("button", { name: "Build report" }).click({ timeout: 45_000 });
  await page.waitForURL(/\/reports\/[^/]+$/, { timeout: 45_000 });
  const title = page.getByLabel("Report title");
  await title.fill(FIXTURE.report);
  await title.press("Enter");
  await page.waitForLoadState("networkidle");
  fixture.report = new URL(page.url()).pathname;
  return fixture.report;
}

async function fixtureDashboard(page: Page) {
  fixture.dashboard ??= (await linkIn(page, "/dashboards", "Dashboards", FIXTURE.dashboard)) ?? undefined;
  if (fixture.dashboard) return fixture.dashboard;
  await fixtureReport(page);
  await openRoute(page, "/dashboards");
  await page.getByRole("button", { name: "New dashboard" }).first().click();
  const dialog = page.getByRole("dialog", { name: "New dashboard" });
  await dialog.getByLabel("Title").fill(FIXTURE.dashboard);
  await dialog.getByRole("button", { name: "Create and open" }).click();
  await page.waitForURL(/\/dashboards\/[^/]+\/edit$/);
  await page.getByRole("button", { name: "Add a report" }).click();
  await page.getByRole("option", { name: new RegExp(FIXTURE.report) }).click();
  await page.getByRole("list", { name: "Reports on this dashboard" }).getByRole("listitem").first().waitFor();
  await page.waitForLoadState("networkidle");
  fixture.dashboard = new URL(page.url()).pathname;
  return fixture.dashboard;
}
const dashboardRoute = async (page: Page, suffix: string) => (await fixtureDashboard(page)).replace(/\/edit$/, suffix);

export const SCREENS: Screen[] = [
  // Explore (Viewer)
  // The greeting follows the clock; shelves and recent questions grow as the other journeys run.
  { name: "home", user: "nomsa", path: "/", mask: ["main h1", '[aria-labelledby="recent-questions"] ul', "main ul"], clipHeight: 420 },
  { name: "ask", user: "nomsa", path: "/ask", mask: ['[aria-label="Conversation history"]'], clipHeight: 420 },
  { name: "dashboard-viewer", user: "nomsa", resolve: starterDashboard },
  { name: "shared-with-me", user: "nomsa", path: "/shared", clipHeight: 360 },
  { name: "notifications", user: "nomsa", path: "/notifications", mask: ["main ul"], clipHeight: 300 },
  // Build (Creator)
  { name: "reports", user: "nomsa", path: "/reports", mask: ['[aria-label="Reports"] tbody', '[aria-label="Widget library"] tbody'], clipHeight: 360 },
  { name: "report-planner", user: "nomsa", path: "/reports/new" },
  { name: "report-canvas", user: "nomsa", resolve: fixtureReport },
  { name: "dashboards", user: "admin", path: "/dashboards", mask: ['[aria-label="Dashboards"] tbody'], clipHeight: 360 },
  { name: "composer", user: "nomsa", resolve: fixtureDashboard },
  { name: "publish", user: "nomsa", resolve: (p) => dashboardRoute(p, "/publish") },
  { name: "versions", user: "nomsa", resolve: (p) => dashboardRoute(p, "/versions") },
  { name: "fields", user: "nomsa", path: "/fields" },
  { name: "field-builder", user: "nomsa", path: "/fields/new" },
  { name: "custom-sql", user: "nomsa", path: "/sql" },
  { name: "custom-sql-editor", user: "nomsa", path: "/sql/new" },
  // Govern and Administer (Admin)
  { name: "admin-overview", user: "admin", path: "/admin", mask: ["main section"] },
  { name: "certification", user: "admin", path: "/govern/certification" },
  { name: "publish-requests", user: "admin", path: "/govern/publish-requests", mask: ["main tbody"], clipHeight: 360 },
  { name: "audit", user: "admin", path: "/admin/audit", mask: ["main tbody", "main tfoot", "main .tabular-nums"], clipHeight: 560 },
  { name: "ai-settings", user: "admin", path: "/admin/ai", mask: ['[aria-label="Usage"]'] },
  { name: "starter-content", user: "admin", path: "/admin/starter", mask: ["main .tabular-nums"] },
  { name: "knowledge", user: "admin", path: "/admin/knowledge", mask: ["main tbody", "main tfoot"], clipHeight: 520 },
  { name: "gold-examples", user: "admin", path: "/admin/knowledge/gold", mask: ["main tbody", "main .text-title"], clipHeight: 520 },
  { name: "access-mappings", user: "admin", path: "/admin/access/mappings" },
  { name: "access-scope", user: "admin", path: "/admin/access/scope" },
  { name: "access-fields", user: "admin", path: "/admin/access/fields" },
  { name: "access-provisioning", user: "admin", path: "/admin/access/provisioning", mask: ["main dl dd", '[aria-labelledby="sync-counts"] > p', '[aria-labelledby="reconcile"] p.text-small', "main table tbody", "main ul"], clipHeight: 720 },
  { name: "connections", user: "admin", path: "/admin/connections" },
  { name: "add-connection", user: "admin", path: "/admin/connections/new" },
  { name: "model-studio", user: "admin", resolve: model },
  { name: "approve-model", user: "admin", resolve: async (p) => { const m = await model(p); return m && `${m}/approve`; } },
];

/**
 * Replaces and masks text that changes on every run (relative times, clock times, dates), and hides the
 * unread count on the bell, which every journey changes.
 */
export async function markVolatileText(page: Page) {
  await page.evaluate(() => {
    for (const badge of document.querySelectorAll<HTMLElement>('button[aria-label^="Notifications,"] > span')) badge.style.visibility = "hidden";
    const volatile = /\b(ago|just now|today|yesterday|\d{1,2}:\d{2}|\d{4}-\d{2}-\d{2}|\d{1,2} (Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*)\b/i;
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    for (let n = walker.nextNode(); n; n = walker.nextNode()) {
      if (!volatile.test(n.textContent ?? "") || !n.parentElement) continue;
      // A fixed placeholder, so a changing width ("just now" → "12 min ago") cannot reflow the layout.
      n.textContent = "Time";
      n.parentElement.setAttribute("data-e2e-mask", "");
    }
  });
}
