import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

import { openRoute, settle, signIn } from "./support";

/**
 * US-166 in a real browser against the running stack: a free-hand question in Ask → "Add as widget" (Pending
 * certification, library only) → the Admin certification queue → Approve as a certified measure → the widget is
 * certified and the same question gets a certified answer.
 *
 * On the dev stack only the Admin role has a data scope, so Ada Admin plays both parts: she needs her own free-hand
 * grant on the source (given here if missing) and the tenant policy must allow self-certification for the run. The
 * stack ends as it started: the policy and grant are put back and the new measure is taken out of the model again (a
 * further model version). The widget stays in Ada's library — widgets have no delete yet.
 */
const QUESTION = "How many projects without a budget code and whose planned deployment date is before today?";

type Call = <T = unknown>(method: string, path: string, body?: unknown, headers?: Record<string, string>) => Promise<{ status: number; json: T }>;

/** The API as the signed-in person: the access token lives in memory, so take it from the app's own requests. */
async function apiAs(page: Page): Promise<Call> {
  const seen = page.waitForRequest((r) => r.url().includes("/api/v1/") && !!r.headers().authorization);
  await openRoute(page, "/reports");
  const auth = (await seen).headers().authorization!;
  return (method, path, body, headers = {}) => page.evaluate(async ({ method, path, body, headers, auth }) => {
    const resp = await fetch(`/api/v1${path}`, {
      method, headers: { Authorization: auth, ...(body === undefined ? {} : { "Content-Type": "application/json" }), ...headers },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    return { status: resp.status, json: resp.status === 204 ? null : await resp.json() };
  }, { method, path, body, headers, auth }) as never;
}

async function axe(page: Page) {
  for (const theme of ["light", "dark"] as const) {
    await page.emulateMedia({ colorScheme: theme });
    await settle(page);
    const results = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"]).analyze();
    const blocking = results.violations.filter((v) => v.impact === "serious" || v.impact === "critical").map((v) => v.id);
    expect(blocking, `${theme}: ${blocking.join(", ")}`).toEqual([]);
  }
  await page.emulateMedia({ colorScheme: "light" });
}

test("a free-hand answer becomes a widget, then a certified measure", async ({ page }) => {
  test.setTimeout(240_000);
  await page.setViewportSize({ width: 1440, height: 1100 });
  const csp = await signIn(page, "admin");
  const call = await apiAs(page);
  const stamp = Date.now().toString(36);
  const label = `E2E free-hand ${stamp}`;
  const key = `e2e_free_hand_${stamp}`;

  const me = (await call<{ user_id: string }>("GET", "/me")).json;
  const connections = (await call<{ items: { id: string; name: string }[] }>("GET", "/connections")).json.items;
  type Model = { model_id: string; connection_id: string; current_version_id: string | null };
  const models = (await Promise.all(connections.map(async (c) =>
    (await call<{ items: Model[] }>("GET", `/models?connection_id=${c.id}`)).json.items))).flat();
  // The source with an approved model (the dev stack has one).
  const model = models.find((m) => m.current_version_id);
  expect(model, "an approved model on some source").toBeTruthy();
  const { model_id: modelId, connection_id: connectionId } = model!;
  const policy = (await call<{ allow_self_certification: boolean }>("GET", "/admin/certification-policy")).json;
  // Live grants only (ended ones are left out by default).
  const grants = (await call<{ items: unknown[] }>("GET", `/admin/permission-grants?user_id=${me.user_id}&connection_id=${connectionId}`)).json.items;
  const hasGrant = grants.length > 0;
  let grantId: string | null = null;
  let certified = false;

  try {
    if (!policy.allow_self_certification) {
      expect((await call("PUT", "/admin/certification-policy", { allow_self_certification: true })).status).toBe(200);
    }
    if (!hasGrant) {
      const made = await call<{ id: string }>("POST", "/admin/permission-grants", {
        user_id: me.user_id, connection_id: connectionId, reason: "e2e: free-hand certification journey",
        expires_at: new Date(Date.now() + 86_400_000).toISOString() });
      expect(made.status).toBe(201);
      grantId = made.json.id;
    }

    // 1. Ask: the answer is free-hand, with the rule in chips.
    await openRoute(page, `/ask?q=${encodeURIComponent(QUESTION)}`);
    const answer = page.getByRole("button", { name: "Add as widget" });
    await expect(answer).toBeVisible({ timeout: 60_000 });
    await expect(page.getByText("Not certified · built from your question", { exact: true })).toBeVisible();
    const rule = page.getByRole("region", { name: "Your rule" });
    await expect(rule).toContainText("Budget code is blank");
    await expect(rule).toContainText("Planned deployment date is before today");

    // 2. Add as widget: no report to choose; it waits in the library as Pending certification.
    await answer.click();
    const pin = page.getByRole("dialog", { name: "Add as widget" });
    await expect(pin.getByRole("combobox", { name: "Report" })).toHaveCount(0);
    await pin.getByRole("textbox", { name: "Title" }).fill(label);
    const created = page.waitForResponse((r) => r.url().endsWith("/api/v1/widgets") && r.request().method() === "POST");
    await pin.getByRole("button", { name: "Add as widget" }).click();
    const widget = await (await created).json() as { id: string; badges: string[]; certification_request_id: string };
    expect(widget.badges).toEqual(["pending_certification"]);
    await expect(pin).toContainText("Added to your library as Pending certification");
    await pin.getByRole("button", { name: "Done" }).click();

    await openRoute(page, "/reports");
    const library = page.getByRole("table", { name: "Widget library" });
    await expect(library.getByRole("row").filter({ hasText: label })).toContainText("Pending certification");
    // Held: it can't go on a report or be shared yet.
    const report = await call<{ id: string }>("POST", "/reports", { title: `E2E hold ${stamp}` });
    expect((await call("POST", `/reports/${report.json.id}/items`, { kind: "widget", widget_id: widget.id })).status).toBe(409);
    await call("DELETE", `/reports/${report.json.id}`);

    // 3. The Admin queue: the question, the rule in plain words, the number with a sample of records.
    await openRoute(page, `/govern/certification?r=${widget.certification_request_id}`);
    const queue = page.getByRole("table", { name: "Certification queue" });
    await expect(queue.getByRole("row").filter({ hasText: "Free-hand rule" }).first()).toBeVisible();
    const detail = page.getByRole("region", { name: "Selected item" });
    await expect(detail).toContainText(`“${QUESTION}”`);
    await expect(detail).toContainText("Count of projects where Budget code is blank and Planned deployment date is before today.");
    await expect(detail).toContainText("Pending certification");
    await expect(detail.getByText(/Sample of records/)).toBeVisible({ timeout: 30_000 });
    const decision = page.getByRole("region", { name: "Decision" });
    await expect(decision.getByRole("radio", { name: /Approve as a certified measure/ })).toBeChecked();
    await axe(page);

    // 4. Approve: name the measure; the model gets a new version and the widget is certified.
    await decision.getByRole("textbox", { name: "Measure name" }).fill(label);
    await expect(decision.getByRole("textbox", { name: "Key" })).toHaveValue(key);
    await decision.getByRole("textbox", { name: "Definition (one line shown with the figure)" })
      .fill("Projects with no budget code whose planned deployment date has passed.");
    const decided = page.waitForResponse((r) => r.url().includes(`/certification/requests/${widget.certification_request_id}/decision`));
    await decision.getByRole("button", { name: "Approve and notify Ada" }).click();
    const out = await (await decided).json() as { status: string; measure: string; model_version: number };
    expect(out).toMatchObject({ status: "certified", measure: `measure:${key}` });
    certified = true;
    await expect(page.getByText(`${label} is certified`)).toBeVisible();
    await expect(page.getByText(`Added to the model as version ${out.model_version}.`)).toBeVisible();

    await openRoute(page, "/reports");
    await expect(library.getByRole("row").filter({ hasText: label })).toContainText("Certified");
    const after = (await call<{ badges: string[]; spec: { measure: unknown } }>("GET", `/widgets/${widget.id}`)).json;
    expect(after.badges).toEqual(["certified"]);
    expect(after.spec.measure).toEqual({ ref: `measure:${key}` });

    // 5. The same question now gets the certified measure (the question was kept as a synonym).
    await openRoute(page, `/ask?q=${encodeURIComponent(QUESTION)}`);
    await expect(page.getByRole("button", { name: "Pin" }).last()).toBeVisible({ timeout: 60_000 });
    await expect(page.getByText("Certified", { exact: true }).last()).toBeVisible();
    await expect(page.getByRole("region", { name: "Your rule" })).toHaveCount(0);
    expect(csp).toEqual([]);
  } finally {
    if (certified) {
      // Take the measure out again through a further model version, so the next run asks free-hand once more.
      const draft = (await call<{ draft_etag: string; measures: { key: string }[] }>("GET", `/models/${modelId}/draft`)).json;
      const patched = await call<{ draft_etag: string }>("PATCH", `/models/${modelId}/draft`,
        { measures: draft.measures.filter((m) => m.key !== key) }, { "If-Match": draft.draft_etag });
      expect(patched.status, "measure taken out of the draft").toBe(200);
      const approved = await call("POST", `/models/${modelId}/approve`, { draft_etag: patched.json.draft_etag, note: "e2e: free-hand journey cleanup" });
      expect(approved.status, JSON.stringify(approved.json)).toBe(201);
    }
    if (grantId) await call("DELETE", `/admin/permission-grants/${grantId}`, { reason: "e2e done" });
    if (!policy.allow_self_certification) await call("PUT", "/admin/certification-policy", { allow_self_certification: false });
  }
});
