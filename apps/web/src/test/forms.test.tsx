import { QueryClient } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "../api/client";
import type { Me } from "../api/types";
import { AppRoutes, Providers } from "../app/App";
import { TestAuthProvider } from "../app/auth";
import { setConfigForTests } from "../app/config";
import { splitServerError } from "../lib/api-errors";

const admin: Me = {
  user_id: "u1", display_name: "Ada Admin", email: null, tenant_id: "t1", roles: ["admin"], level: "admin", audience_roles: [],
  permissions: ["dashboards.view", "ask.use", "connections.manage", "mappings.manage", "policies.manage"],
  scope_summary: [], locale: null, time_zone: null,
};

type Handler = (url: string, init?: RequestInit) => Response | undefined;
const calls: { url: string; method: string; body: unknown }[] = [];

function serve(handler: Handler) {
  calls.length = 0;
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ url, method: init?.method ?? "GET", body: init?.body ? JSON.parse(String(init.body)) : undefined });
    if (url.endsWith("/api/v1/me")) return json(admin);
    if (url.includes("/api/v1/notifications")) return json({ items: [], unread_count: 0 });
    return handler(url, init) ?? json({ error: { code: "not_found", message: "Not found" } }, 404);
  }));
}

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

function renderAt(path: string) {
  window.history.pushState({}, "", path);
  return render(
    <TestAuthProvider token="tok">
      <Providers client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <AppRoutes />
      </Providers>
    </TestAuthProvider>,
  );
}

beforeEach(() => setConfigForTests({ apiBaseUrl: "http://api.test", oidcAuthority: "http://kc", oidcClientId: "tela-web" }));
afterEach(() => vi.unstubAllGlobals());

describe("server errors", () => {
  const fields = ["name", "port", "schemas", "tls"];
  it("maps validation errors to fields by their body path", () => {
    const e = new ApiError(400, "validation_error", "The request is not valid.", "r1", {
      errors: [{ loc: ["body", "port"], msg: "Input should be less than or equal to 65535" }, { loc: ["body", "tls", "mode"], msg: "bad" }],
    });
    expect(splitServerError(e, fields, { fields: { "tls.mode": "tls" } })).toEqual({
      fieldErrors: [{ field: "port", message: "Input should be less than or equal to 65535." }, { field: "tls", message: "Bad." }],
      formLevel: false,
    });
  });

  it("keeps errors it cannot place for the form alert", () => {
    const e = new ApiError(400, "validation_error", "The request is not valid.", null, { errors: [{ loc: ["body", "row_cap"], msg: "too big" }] });
    expect(splitServerError(e, fields).formLevel).toBe(true);
    expect(splitServerError(new ApiError(409, "conflict", "Taken", null), fields).formLevel).toBe(true);
  });

  it("maps business errors by details.field or by code", () => {
    expect(splitServerError(new ApiError(409, "conflict", "That name is taken.", null, { field: "name" }), fields).fieldErrors)
      .toEqual([{ field: "name", message: "That name is taken." }]);
    expect(splitServerError(new ApiError(422, "unknown_group", "Unknown.", null), ["group"], { codes: { unknown_group: "group" } }).fieldErrors)
      .toEqual([{ field: "group", message: "Unknown." }]);
  });
});

describe("connection form", () => {
  const listOnly: Handler = (url, init) => (url.endsWith("/api/v1/connections") && (init?.method ?? "GET") === "GET" ? json({ items: [] }) : undefined);

  it("explains what to fix before anything is sent", async () => {
    serve(listOnly);
    renderAt("/admin/connections/new?step=details");
    const form = await screen.findByRole("form", { name: "Connection details · PostgreSQL" });
    const port = within(form).getByLabelText("Port");
    await userEvent.clear(port);
    await userEvent.type(port, "70000");
    await userEvent.click(within(form).getByRole("button", { name: "Create and test" }));
    expect(await within(form).findByText("Enter a name.")).toBeInTheDocument();
    expect(within(form).getByText("Enter a port between 1 and 65535.")).toBeInTheDocument();
    const name = within(form).getByLabelText("Name people will see");
    expect(name).toHaveAttribute("aria-invalid", "true");
    expect(name.getAttribute("aria-describedby")).toContain(within(form).getByText("Enter a name.").id);
    expect(calls.some((c) => c.method === "POST")).toBe(false);
    // Validates as you type once a field has been touched.
    await userEvent.type(name, "TPG");
    await waitFor(() => expect(within(form).queryByText("Enter a name.")).not.toBeInTheDocument());
  });

  it("puts server errors under the right field, and the rest in an alert with the request id", async () => {
    let attempt = 0;
    serve((url, init) => {
      if (url.endsWith("/api/v1/connections") && init?.method === "POST") {
        attempt++;
        return attempt === 1
          ? json({ error: { code: "validation_error", message: "The request is not valid.", request_id: "req-9", details: { errors: [{ loc: ["body", "approved_schemas"], msg: "List should have at least 1 item after validation" }] } } }, 400)
          : json({ error: { code: "connection_exists", message: "A connection with this name already exists.", request_id: "req-10", details: {} } }, 409);
      }
      return listOnly(url, init);
    });
    renderAt("/admin/connections/new?step=details");
    const form = await screen.findByRole("form", { name: "Connection details · PostgreSQL" });
    for (const [label, value] of [["Name people will see", "TPG"], ["Host", "db"], ["Database", "tpg"], ["Read-only user", "ro"], ["Password", "pw"]] as const) {
      await userEvent.type(within(form).getByLabelText(label), value);
    }
    await userEvent.click(within(form).getByRole("button", { name: "Create and test" }));
    expect(await within(form).findByText("List should have at least 1 item after validation.")).toBeInTheDocument();
    expect(within(form).getByLabelText("Schemas")).toHaveAttribute("aria-invalid", "true");
    expect(within(form).queryByRole("alert")).not.toBeInTheDocument();
    expect(calls.find((c) => c.method === "POST")!.body).toMatchObject({ name: "TPG", engine: "postgres", port: 5432, tls: { mode: "verify-full" }, approved_schemas: ["public"] });

    await userEvent.click(within(form).getByRole("button", { name: "Create and test" }));
    const alert = await within(form).findByRole("alert");
    expect(alert).toHaveTextContent("A connection with this name already exists.");
    expect(alert).toHaveTextContent("Request id req-10");
  });
});

describe("field policy form", () => {
  it("asks for an entity.field and at least one role", async () => {
    serve((url) => {
      if (url.endsWith("/api/v1/connections")) return json({ items: [{ id: "c1", name: "TPG", engine: "postgres", status: "connected" }] });
      if (url.includes("/api/v1/admin/field-policies")) return json({ rules: [] });
      if (url.endsWith("/api/v1/admin/roles")) return json({ items: [{ id: "r1", key: "cio", name: "CIO", kind: "audience", is_protected: false }] });
      return undefined;
    });
    renderAt("/admin/access/fields");
    await screen.findByRole("option", { name: "TPG (postgres)" });
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Connection" }), "c1");
    const form = await screen.findByRole("form", { name: "Hide a field" });
    await userEvent.type(within(form).getByLabelText("Field (entity.field)"), "budget");
    await userEvent.click(within(form).getByRole("button", { name: "Save" }));
    expect(await within(form).findByText(/Write the field as entity.field/)).toBeInTheDocument();
    expect(within(form).getByText("Choose at least one role.")).toBeInTheDocument();
    expect(calls.some((c) => c.method === "PUT")).toBe(false);
  });
});
