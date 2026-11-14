import { QueryClient } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { SshKeyItem } from "../api/app";
import type { Me } from "../api/types";
import { AppRoutes, Providers } from "../app/App";
import { TestAuthProvider } from "../app/auth";
import { setConfigForTests } from "../app/config";

/** Connection forms for every engine (US-115): per-engine fields, validation and request bodies; the Excel/CSV
 * upload and sheet confirmation (US-108); the Dataverse switch to Web API mode when TDS is off (US-111). */

const admin: Me = {
  user_id: "u1", display_name: "Ada Admin", email: null, tenant_id: "t1", roles: ["admin"], level: "admin", audience_roles: [],
  permissions: ["dashboards.view", "ask.use", "connections.manage", "model.edit", "model.approve"], scope_summary: [], locale: null, time_zone: null,
};
type Handler = (url: string, init?: RequestInit) => Response | undefined;
const calls: { url: string; method: string; body: unknown }[] = [];
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
const ok = { reachable: true, authenticated: true, read_only: { verdict: "safe", checks: [], reasons: [] }, latency_ms: 9, server_version: "x", status: "connected", message: null };

function serve(handler: Handler) {
  calls.length = 0;
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    const raw = init?.body;
    calls.push({ url, method: init?.method ?? "GET", body: raw instanceof FormData ? raw : raw ? JSON.parse(String(raw)) : undefined });
    if (url.endsWith("/api/v1/me")) return json(admin);
    if (url.includes("/api/v1/notifications")) return json({ items: [], unread_count: 0 });
    return handler(url, init) ?? json({ error: { code: "not_found", message: "Not found" } }, 404);
  }));
}

function renderAt(path: string) {
  window.history.pushState({}, "", path);
  return render(
    <TestAuthProvider token="tok">
      <Providers client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><AppRoutes /></Providers>
    </TestAuthProvider>,
  );
}

const created = () => serve((url, init) => {
  if (url.endsWith("/api/v1/connections") && init?.method === "POST") return json({ connection_id: "c9", status: "draft" }, 201);
  if (url.endsWith("/api/v1/connections/c9/test")) return json(ok);
  if (url.endsWith("/api/v1/connections/c9")) return json({ id: "c9", name: "x", engine: "mysql", status: "connected", descriptor: {}, secret_set: true,
    read_only: {}, freshness: { mode: "live" }, model: { id: null, version_no: null, status: null }, data_as_of: null, etag: 'W/"1"' });
  return undefined;
});

async function fill(form: HTMLElement, values: [string, string][]) {
  for (const [label, value] of values) {
    const input = within(form).getByLabelText(label);
    await userEvent.clear(input);
    await userEvent.type(input, value);
  }
}

const posted = () => calls.find((c) => c.url.endsWith("/api/v1/connections") && c.method === "POST")?.body as Record<string, unknown>;

beforeEach(() => setConfigForTests({ apiBaseUrl: "http://api.test", oidcAuthority: "http://kc", oidcClientId: "tela-web" }));
afterEach(() => vi.unstubAllGlobals());

describe("Connection forms for every engine (US-115)", () => {
  it("MySQL: default port 3306, the database is the schema, TLS mode kept", async () => {
    created();
    renderAt("/admin/connections/new?step=details&engine=mysql");
    const form = await screen.findByRole("form", { name: "Connection details · MySQL" });
    expect(within(form).getByLabelText("Port")).toHaveValue("3306");
    await fill(form, [["Name people will see", "TPG MySQL"], ["Host", "db"], ["Database", "tpg"], ["Read-only user", "ro"], ["Password", "pw"],
      ["Other databases to include", "archive"]]);
    await userEvent.click(within(form).getByRole("button", { name: "Create and test" }));
    await waitFor(() => expect(posted()).toBeDefined());
    expect(posted()).toMatchObject({ engine: "mysql", port: 3306, database: "tpg", tls: { mode: "verify-full" }, approved_schemas: ["tpg", "archive"] });
  });

  it("SQL Server: always encrypted, trusting the certificate is a development option, dbo by default", async () => {
    created();
    renderAt("/admin/connections/new?step=details&engine=sqlserver");
    const form = await screen.findByRole("form", { name: "Connection details · SQL Server" });
    expect(within(form).getByLabelText("Port")).toHaveValue("1433");
    expect(within(form).getByLabelText("Schemas")).toHaveValue("dbo");
    expect(within(form).queryByLabelText("TLS")).toBeNull();
    await fill(form, [["Name people will see", "TPG SQL"], ["Host", "sql"], ["Database", "tpg"], ["Read-only user", "ro"], ["Password", "pw"]]);
    await userEvent.click(within(form).getByRole("checkbox", { name: /Trust the server certificate/ }));
    await userEvent.click(within(form).getByRole("button", { name: "Create and test" }));
    await waitFor(() => expect(posted()).toBeDefined());
    expect(posted()).toMatchObject({ engine: "sqlserver", port: 1433, tls: { trust_server_certificate: true }, approved_schemas: ["dbo"] });
  });

  it("MongoDB: the connection string may not carry credentials", async () => {
    created();
    renderAt("/admin/connections/new?step=details&engine=mongodb");
    const form = await screen.findByRole("form", { name: "Connection details · MongoDB" });
    await fill(form, [["Name people will see", "Docs"], ["Connection string", "mongodb://u:p@db:27017"], ["Database", "tpg"], ["Read-only user", "ro"], ["Password", "pw"]]);
    await userEvent.click(within(form).getByRole("button", { name: "Create and test" }));
    expect(await within(form).findByText(/Remove the user name and password from the connection string/)).toBeInTheDocument();
    expect(posted()).toBeUndefined();
    await fill(form, [["Connection string", "mongodb://db:27017"]]);
    await userEvent.click(within(form).getByRole("button", { name: "Create and test" }));
    await waitFor(() => expect(posted()).toBeDefined());
    expect(posted()).toMatchObject({ engine: "mongodb", uri: "mongodb://db:27017", read_preference: "secondaryPreferred", tls: { mode: "verify-full" } });
    expect(posted()).not.toHaveProperty("host");
  });

  it("Dataverse: GUIDs and tables are checked; a TDS-off test offers the Web API", async () => {
    let mode = "tds";
    serve((url, init) => {
      if (url.endsWith("/api/v1/connections") && init?.method === "POST") return json({ connection_id: "c9", status: "draft" }, 201);
      if (url.endsWith("/api/v1/connections/c9/test")) return json(mode === "tds"
        ? { ...ok, status: "draft", read_only: { verdict: "unsafe", checks: [], reasons: ["the TDS endpoint is not reachable"] },
          message: "The TDS endpoint is off for this environment. Switch the connection to Web API mode.", suggest_mode: "webapi" }
        : ok);
      if (url.endsWith("/api/v1/connections/c9") && init?.method === "PATCH") { mode = "webapi"; return json({ id: "c9" }); }
      if (url.endsWith("/api/v1/connections/c9")) return json({ id: "c9", name: "TPG", engine: "dataverse", status: "draft", descriptor: { mode },
        secret_set: true, read_only: {}, freshness: { mode: "live" }, model: { id: null, version_no: null, status: null }, data_as_of: null, etag: 'W/"2"' });
      return undefined;
    });
    renderAt("/admin/connections/new?step=details&engine=dataverse");
    const form = await screen.findByRole("form", { name: "Connection details · Dataverse" });
    await fill(form, [["Name people will see", "TPG"], ["Environment URL", "https://contoso.crm4.dynamics.com"], ["Directory (tenant) ID", "not-a-guid"],
      ["Application (client) ID", "11111111-1111-1111-1111-111111111111"], ["Client secret", "s3cret"]]);
    await userEvent.click(within(form).getByRole("button", { name: "Create and test" }));
    expect(await within(form).findByText("Enter the directory (tenant) ID, a GUID.")).toBeInTheDocument();
    expect(within(form).getByText("Name the tables to read, for example tpg_project.")).toBeInTheDocument();
    await fill(form, [["Directory (tenant) ID", "22222222-2222-2222-2222-222222222222"], ["Tables", "tpg_project, tpg_projectrequest"]]);
    await userEvent.click(within(form).getByRole("button", { name: "Create and test" }));
    await waitFor(() => expect(posted()).toBeDefined());
    expect(posted()).toMatchObject({ engine: "dataverse", mode: "tds", client_secret: "s3cret", tables: ["tpg_project", "tpg_projectrequest"] });
    expect(posted()).not.toHaveProperty("password");
    await userEvent.click(await screen.findByRole("button", { name: "Switch to Web API mode and test" }));
    await waitFor(() => expect(calls.some((c) => c.method === "PATCH" && (c.body as { mode?: string }).mode === "webapi")).toBe(true));
    await waitFor(() => expect(screen.queryByRole("button", { name: "Switch to Web API mode and test" })).toBeNull());
  });

  it("Excel or CSV: upload with retention, then confirm sheets, types and personal data", async () => {
    serve((url, init) => {
      if (url.endsWith("/api/v1/connections/files") && init?.method === "POST") return json({ connection_id: "f1", status: "draft" }, 201);
      if (url.endsWith("/api/v1/connections/f1/test")) return json(ok);
      if (url.endsWith("/api/v1/connections/f1/introspect")) return json({ status: "connected", objects: 1, diff: { added: ["main.projects"], removed: [], changed: [] }, model_broken: [] });
      if (url.endsWith("/api/v1/connections/f1/objects")) return json({ items: [{ object: "main.projects", kind: "sheet", columns: [{ name: "cio", type: "varchar", nullable: true }], keys: [], row_estimate: 100, allowed: false, removed: false }], introspected_at: null, schema_checksum: "x" });
      if (url.endsWith("/api/v1/connections/f1/files")) return json({ settings: [], items: [{ version: 1, status: "active", filename: "tpg.xlsx", retention_until: null, uploaded_at: "2026-10-22T00:00:00Z",
        sheets: [{ sheet: "projects", table: "projects", include: true, header_row: 1, rows: 100, merged_cells: 0,
          columns: [{ name: "cio", header: "cio", type: "text", inferred: "text", flags: [], personal: false },
                    { name: "score", header: "Score", type: "text", inferred: "text", flags: ["mixed_types"], personal: false }] }] }] });
      if (url.endsWith("/api/v1/connections/f1/files/settings")) return json({ status: "draft" });
      if (url.includes("/api/v1/starter-packs/match")) return json({ matches: [] });
      if (url.endsWith("/api/v1/connections/f1")) return json({ id: "f1", name: "TPG workbook", engine: "excel", status: "connected", descriptor: {}, secret_set: false,
        read_only: {}, freshness: { mode: "live" }, model: { id: null, version_no: null, status: null }, data_as_of: null, etag: 'W/"3"' });
      return undefined;
    });
    renderAt("/admin/connections/new?step=details&engine=excel");
    const form = await screen.findByRole("form", { name: "Connection details · Excel or CSV" });
    await userEvent.type(within(form).getByLabelText("Name people will see"), "TPG workbook");
    await userEvent.upload(within(form).getByLabelText("File"), new File(["x"], "tpg.xlsx", { type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" }));
    await userEvent.selectOptions(within(form).getByLabelText("Keep the file"), "fixed_days");
    await userEvent.click(within(form).getByRole("button", { name: "Upload and test" }));
    await waitFor(() => expect(calls.some((c) => c.url.endsWith("/connections/files"))).toBe(true));
    const upload = calls.find((c) => c.url.endsWith("/connections/files"))!.body as FormData;
    expect(upload.get("name")).toBe("TPG workbook");
    expect(upload.get("retention_policy")).toBe("fixed_days");
    expect(upload.get("retention_days")).toBe("90");
    expect((upload.get("file") as File).name).toBe("tpg.xlsx");
    await userEvent.click(await screen.findByRole("button", { name: "Read schema" }));
    const sheets = await screen.findByRole("region", { name: /Confirm the sheets/ });
    expect(within(sheets).getByText("Mixed types")).toBeInTheDocument();
    await userEvent.click(within(sheets).getByRole("checkbox", { name: "cio is personal data" }));
    await userEvent.selectOptions(within(sheets).getByLabelText("Type of Score"), "decimal");
    await userEvent.click(within(sheets).getByRole("button", { name: "Save and re-read" }));
    await waitFor(() => expect(calls.some((c) => c.url.endsWith("/files/settings"))).toBe(true));
    expect(calls.find((c) => c.url.endsWith("/files/settings"))!.body).toEqual({ sheets: [{ sheet: "projects", include: true, header_row: 1,
      columns: [{ header: "cio", type: "text", personal: true }, { header: "Score", type: "decimal", personal: false }] }] });
  });

  it("refuses a file Tela cannot take before uploading it", async () => {
    serve(() => undefined);
    renderAt("/admin/connections/new?step=details&engine=excel");
    const form = await screen.findByRole("form", { name: "Connection details · Excel or CSV" });
    await userEvent.type(within(form).getByLabelText("Name people will see"), "Macro book");
    await userEvent.upload(within(form).getByLabelText("File"), new File(["x"], "book.xlsm"), { applyAccept: false });
    await userEvent.click(within(form).getByRole("button", { name: "Upload and test" }));
    expect(await within(form).findByText("Only .xlsx or .csv up to 50 MB, without macros or passwords.")).toBeInTheDocument();
    expect(calls.some((c) => c.url.endsWith("/connections/files"))).toBe(false);
  });
});

describe("SSH tunnel (ENH-003)", () => {
  const PPK = "PuTTY-User-Key-File-3: ssh-ed25519\nEncryption: aes256-cbc\n";
  const PINNED = "SHA256:hostkeyhostkeyhostkeyhostkeyhostkeyhostke";
  const savedConnection = (status = "connected") => ({ id: "c7", name: "TPG behind bastion", engine: "postgres", status, secret_set: true,
    descriptor: { host: "db.internal", port: 5432, database: "tpg", username: "ro", tls: { mode: "verify-full" }, approved_schemas: ["public"],
      ssh: { host: "bastion.example.com", port: 2222, username: "tunnel", auth: "key", key_type: "ssh-ed25519", key_bits: 256,
        key_fingerprint: "SHA256:keykeykey", host_key: PINNED, host_key_pinned: "2026-10-27T00:00:00Z" } },
    read_only: {}, freshness: { mode: "live" }, model: { id: null, version_no: null, status: null }, data_as_of: null, etag: 'W/"7"' });

  it("a new PostgreSQL connection sends the key file once; key problems land on the right field", async () => {
    let attempts = 0;
    serve((url, init) => {
      if (url.endsWith("/api/v1/connections") && init?.method === "POST") {
        attempts += 1;
        if (attempts === 1) return json({ error: { code: "validation_error", message: "This key is protected by a passphrase. Enter the passphrase.",
          details: { field: "ssh.passphrase" } } }, 400);
        return json({ connection_id: "c9", status: "draft" }, 201);
      }
      if (url.endsWith("/api/v1/connections/c9/test")) return json({ ...ok, ssh: { ok: true, stage: null, error: null, host_key: PINNED, pinned: true } });
      if (url.endsWith("/api/v1/connections/c9")) return json({ ...savedConnection(), id: "c9" });
      return undefined;
    });
    renderAt("/admin/connections/new?step=details&engine=postgres");
    const form = await screen.findByRole("form", { name: "Connection details · PostgreSQL" });
    expect(within(form).queryByLabelText("SSH server")).toBeNull();
    await fill(form, [["Name people will see", "TPG behind bastion"], ["Host", "db.internal"], ["Database", "tpg"], ["Read-only user", "ro"], ["Password", "pw"]]);
    await userEvent.click(within(form).getByRole("checkbox", { name: "Connect through an SSH server" }));
    expect(within(form).getByLabelText("SSH port")).toHaveValue("22");
    await userEvent.click(within(form).getByRole("button", { name: "Create and test" }));
    expect(await within(form).findByText("Enter the SSH server, for example bastion.example.com.")).toBeInTheDocument();
    expect(within(form).getByText("Choose the private key file (.pem, .ppk or OpenSSH).")).toBeInTheDocument();
    expect(posted()).toBeUndefined();

    await fill(form, [["SSH server", "bastion.example.com"], ["SSH user", "tunnel"]]);
    await userEvent.upload(within(form).getByLabelText("Private key file"), new File([PPK], "bastion.ppk"));
    await userEvent.click(within(form).getByRole("button", { name: "Create and test" }));
    expect(await within(form).findByText("This key is protected by a passphrase. Enter the passphrase.")).toBeInTheDocument();
    expect(posted()).toMatchObject({ ssh: { host: "bastion.example.com", port: 22, username: "tunnel", auth: "key", private_key: PPK } });
    expect(posted().ssh).not.toHaveProperty("passphrase");

    await fill(form, [["Key passphrase", "tela-test"]]);
    await userEvent.click(within(form).getByRole("button", { name: "Create and test" }));
    const checklist = await screen.findByText("Opened the SSH tunnel and pinned the server's host key");
    expect(checklist).toBeInTheDocument();
    expect(screen.getByText(PINNED)).toBeInTheDocument();
    const second = calls.filter((c) => c.url.endsWith("/api/v1/connections") && c.method === "POST")[1]!.body as { ssh: Record<string, unknown> };
    expect(second.ssh).toMatchObject({ private_key: PPK, passphrase: "tela-test" });
  });

  it("editing keeps the saved key, can re-pin the host key, and can turn the tunnel off", async () => {
    serve((url, init) => {
      if (url.endsWith("/api/v1/connections/c7") && init?.method === "PATCH") return json(savedConnection("draft"));
      if (url.endsWith("/api/v1/connections/c7/test")) return json({ ...ok, status: "error", reachable: false, authenticated: false,
        read_only: { verdict: "unsafe", checks: [], reasons: [] }, message: "The SSH server showed a different host key.",
        ssh: { ok: false, stage: "host_key", error: "The SSH server showed a different host key.", host_key: "SHA256:other" } });
      if (url.endsWith("/api/v1/connections/c7")) return json(savedConnection());
      return undefined;
    });
    renderAt("/admin/connections/new?id=c7&step=details");
    const form = await screen.findByRole("form", { name: "Connection details · PostgreSQL" });
    expect(await within(form).findByLabelText("SSH server")).toHaveValue("bastion.example.com");
    expect(within(form).getByText(/Saved in the vault: ED25519 256-bit key/)).toBeInTheDocument();
    expect(within(form).getByText(PINNED)).toBeInTheDocument();
    await userEvent.click(within(form).getByRole("checkbox", { name: "Trust the SSH server's new host key on the next test" }));
    await userEvent.click(within(form).getByRole("button", { name: "Save and test" }));
    await waitFor(() => expect(calls.some((c) => c.method === "PATCH")).toBe(true));
    const patch = calls.find((c) => c.method === "PATCH")!.body as { ssh: Record<string, unknown> };
    expect(patch.ssh).toEqual({ host: "bastion.example.com", port: 2222, username: "tunnel", auth: "key", key_source: "upload", verify: "pin", repin: true, jump: [] });
    // The SSH step failed: the database checks were not run.
    expect(await screen.findByText("The SSH server could not be verified: its host key or certificate does not match.")).toBeInTheDocument();
    expect(screen.getAllByText("Not checked")).toHaveLength(3);

    await userEvent.click(screen.getByRole("button", { name: "Change details" }));
    const again = await screen.findByRole("form", { name: "Connection details · PostgreSQL" });
    await userEvent.click(within(again).getByRole("checkbox", { name: "Connect through an SSH server" }));
    expect(within(again).queryByLabelText("SSH server")).toBeNull();
    await userEvent.click(within(again).getByRole("button", { name: "Save and test" }));
    await waitFor(() => expect(calls.filter((c) => c.method === "PATCH")).toHaveLength(2));
    expect((calls.filter((c) => c.method === "PATCH")[1]!.body as { ssh: unknown }).ssh).toEqual({ enabled: false });
  });

  it("is offered for PostgreSQL, MySQL, SQL Server and single-host MongoDB, not Dataverse", async () => {
    serve(() => undefined);
    renderAt("/admin/connections/new?step=details&engine=dataverse");
    const dv = await screen.findByRole("form", { name: "Connection details · Dataverse" });
    expect(within(dv).queryByRole("checkbox", { name: "Connect through an SSH server" })).toBeNull();
  });
});

describe("SSH key library, jump hosts, certificates and MongoDB (ENH-004)", () => {
  const KEY: SshKeyItem = { id: "0192f3b2-0000-7000-8000-00000000beef", name: "Ops bastion", key_type: "ssh-ed25519", key_bits: 256,
    fingerprint: "SHA256:libkeylibkeylibkeylibkeylibkeylibkeylibke", certificate: { key_id: "tela", principals: ["tunnel"], ca_fingerprint: "SHA256:ca",
      valid_after: 0, valid_before: 4_102_444_800 }, created_at: "2026-10-27T00:00:00Z", updated_at: "2026-10-27T00:00:00Z", used_by: [] };
  const CA = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIAAmP2vl4H1qU+NEm+bM9WzoSLyQxcl/rUKqeJgfQOmU";

  it("MongoDB through a jump host and a CA-checked SSH server with a library key", async () => {
    serve((url, init) => {
      if (url.endsWith("/api/v1/admin/ssh-keys")) return json({ items: [KEY] });
      if (url.endsWith("/api/v1/connections") && init?.method === "POST") return json({ connection_id: "c9", status: "draft" }, 201);
      if (url.endsWith("/api/v1/connections/c9/test")) return json({ ...ok, ssh: { ok: false, stage: "host_key", hop: "j1", error: "x", host_key: null,
        hops: [{ id: "j1", host: "jump.example.com", port: 22, host_key: "SHA256:jjj", verified: null }] }, status: "draft", message: "The jump host jump.example.com:22 showed a different host key." });
      if (url.endsWith("/api/v1/connections/c9")) return json({ id: "c9", name: "Docs", engine: "mongodb", status: "draft", descriptor: {}, secret_set: true,
        read_only: {}, freshness: { mode: "live" }, model: { id: null, version_no: null, status: null }, data_as_of: null, etag: 'W/"9"' });
      return undefined;
    });
    renderAt("/admin/connections/new?step=details&engine=mongodb");
    const form = await screen.findByRole("form", { name: "Connection details · MongoDB" });
    await fill(form, [["Name people will see", "Docs"], ["Connection string", "mongodb+srv://cluster.example.com"], ["Database", "tpg"], ["Read-only user", "ro"], ["Password", "pw"]]);
    await userEvent.click(within(form).getByRole("checkbox", { name: "Connect through an SSH server" }));
    await fill(form, [["SSH server", "bastion.internal"], ["SSH user", "tunnel"]]);
    await userEvent.selectOptions(within(form).getByLabelText("Sign in with"), "library");
    await userEvent.selectOptions(within(form).getByLabelText("Check the server with"), "ca");
    await userEvent.click(within(form).getByRole("button", { name: "Add a jump host" }));
    await userEvent.click(within(form).getByRole("button", { name: "Create and test" }));
    expect(await within(form).findByText("Through SSH, use a mongodb:// connection string with one host.")).toBeInTheDocument();
    expect(within(form).getByText("Paste the public key of the CA that signs the server's host certificates.")).toBeInTheDocument();
    expect(within(form).getByText("Enter the jump host.")).toBeInTheDocument();
    expect(within(form).getAllByText("Choose a key from the library.")).toHaveLength(2);
    expect(posted()).toBeUndefined();

    await fill(form, [["Connection string", "mongodb://mongo.internal:27017"], ["Trusted host CA", CA], ["Jump host 1 host", "jump.example.com"], ["Jump host 1 user", "hop"]]);
    await userEvent.selectOptions(within(form).getByLabelText("Library key for the SSH server"), KEY.id);
    await userEvent.selectOptions(within(form).getByLabelText("Jump host 1 library key"), KEY.id);
    await userEvent.click(within(form).getByRole("button", { name: "Create and test" }));
    await waitFor(() => expect(posted()).toBeDefined());
    expect(posted()).toMatchObject({ engine: "mongodb", uri: "mongodb://mongo.internal:27017", ssh: {
      host: "bastion.internal", auth: "key", key_source: "library", key_id: KEY.id, verify: "ca", host_ca: CA,
      jump: [{ host: "jump.example.com", port: 22, username: "hop", auth: "library", key_id: KEY.id, verify: "pin" }] } });
    expect((posted().ssh as Record<string, unknown>)).not.toHaveProperty("private_key");
    // The failing hop is named.
    expect(await screen.findByText("Jump host jump.example.com could not be verified: its host key or certificate does not match.")).toBeInTheDocument();
  });

  it("the key library adds keys, names their users and refuses to delete a key in use", async () => {
    let items = [{ ...KEY, used_by: [{ id: "c7", name: "TPG behind bastion", status: "connected" }] }];
    serve((url, init) => {
      if (url.endsWith("/api/v1/admin/ssh-keys") && init?.method === "POST") {
        const added = { ...KEY, id: "k2", name: "Second", certificate: null, used_by: [] };
        items = [...items, added];
        return json(added, 201);
      }
      if (url.endsWith("/api/v1/admin/ssh-keys")) return json({ items });
      return undefined;
    });
    renderAt("/admin/ssh-keys");
    expect(await screen.findByRole("heading", { name: "Ops bastion" })).toBeInTheDocument();
    expect(screen.getByText(/Certificate for tunnel, valid until/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "TPG behind bastion" })).toHaveAttribute("href", "/admin/connections/new?id=c7&step=details");
    expect(screen.getByRole("button", { name: "Delete" })).toBeDisabled();
    const add = screen.getByRole("form", { name: "Add an SSH key" });
    await userEvent.click(within(add).getByRole("button", { name: "Add key" }));
    expect(await within(add).findByText("Choose the private key file (.pem, .ppk or OpenSSH).")).toBeInTheDocument();
    await userEvent.type(within(add).getByLabelText("Name"), "Second");
    await userEvent.upload(within(add).getByLabelText("Private key file"), new File(["-----BEGIN OPENSSH PRIVATE KEY-----"], "id_ed25519"));
    await userEvent.click(within(add).getByRole("button", { name: "Add key" }));
    await waitFor(() => expect(calls.some((c) => c.method === "POST" && c.url.endsWith("/admin/ssh-keys"))).toBe(true));
    expect(calls.find((c) => c.method === "POST")!.body).toEqual({ name: "Second", private_key: "-----BEGIN OPENSSH PRIVATE KEY-----" });
    expect(await screen.findByRole("heading", { name: "Second" })).toBeInTheDocument();
  });
});
