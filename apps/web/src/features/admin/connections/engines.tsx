import { Boxes, Cloud, Database, FileSpreadsheet, Server, Table2, type LucideIcon } from "lucide-react";
import { useQuery } from "@tanstack/react-query";
import { ArrowUp, Plus, X } from "lucide-react";
import type { ComponentProps, Ref } from "react";
import { useFieldArray, useWatch, type Control } from "react-hook-form";
import { z } from "zod";

import type { ConnectionItem, SshCertificate, SshKeyItem } from "../../../api/app";
import { api } from "../../../api/client";
import { ConnectionIn, GUID, READ_PREFERENCES, SSH_HOST, SSH_KEY_MAX, TLS_MODES } from "../../../api/schemas";
import { Button } from "../../../components/ui/button";
import { Checkbox } from "../../../components/ui/checkbox";
import { Field } from "../../../components/ui/form";
import { Input } from "../../../components/ui/input";
import { NativeSelect } from "../../../components/ui/native-select";
import { Textarea } from "../../../components/ui/textarea";
import { useAppName } from "../../../app/branding";

/** The engines Tela connects to (US-115; connection form validation table, function_document SCR-09). */
export type EngineKey = "postgres" | "mysql" | "sqlserver" | "mongodb" | "dataverse" | "excel";
export const ENGINES: { key: EngineKey; name: string; note: string; icon: LucideIcon }[] = [
  { key: "postgres", name: "PostgreSQL", note: "Read-only role over TLS", icon: Database },
  { key: "mysql", name: "MySQL", note: "A user with SELECT only, over TLS", icon: Table2 },
  { key: "sqlserver", name: "SQL Server", note: "A db_datareader user; always encrypted", icon: Server },
  { key: "mongodb", name: "MongoDB", note: "A user with the read role", icon: Boxes },
  { key: "dataverse", name: "Dataverse", note: "An app registration with a read-only role", icon: Cloud },
  { key: "excel", name: "Excel or CSV", note: "Upload a .xlsx or .csv file, up to 50 MB", icon: FileSpreadsheet },
];
export const engineName = (key: string) =>
  key === "csv" ? "Excel or CSV" : ENGINES.find((e) => e.key === key)?.name ?? key;
export const asEngine = (key: string | null | undefined): EngineKey =>
  key === "csv" ? "excel" : (ENGINES.find((e) => e.key === key)?.key ?? "postgres");

const PORTS: Partial<Record<EngineKey, number>> = { postgres: 5432, mysql: 3306, sqlserver: 1433 };
const SCHEMAS: Partial<Record<EngineKey, string>> = { postgres: "public", sqlserver: "dbo" };
/** Engines that can be reached through an SSH tunnel (ENH-003; MongoDB with one host, ENH-004). */
export const SSH_ENGINES: readonly EngineKey[] = ["postgres", "mysql", "sqlserver", "mongodb"];
export const MAX_JUMPS = 3;
type HostCheckSaved = { verify?: "ca"; host_ca?: string; host_ca_fingerprint?: string; host_key?: string | null; host_key_pinned?: string | null };
export type JumpSaved = HostCheckSaved & { id: string; host: string; port: number; username: string; auth: "library" | "password"; key_id?: string;
  key_fingerprint?: string };
/** What the API keeps about a tunnel: never a key or a password. */
export type SshSaved = HostCheckSaved & { host?: string; port?: number; username?: string; auth?: "key" | "password"; key_source?: "library";
  key_id?: string; key_type?: string; key_bits?: number; key_fingerprint?: string; certificate?: SshCertificate | null; jump?: JumpSaved[] };
export const savedSsh = (c?: ConnectionItem) => (c?.descriptor as { ssh?: SshSaved } | undefined)?.ssh;
const splitList = (v: string) => v.split(",").map((s) => s.trim()).filter(Boolean);

export const details = z.object({
  name: ConnectionIn.shape.name,
  host: z.string().max(255, "Keep the host to 255 characters or fewer."),
  port: z.string().regex(/^\d{0,5}$/, "Enter the port as a number, for example 5432."),
  database: z.string().max(128, "Keep the database name to 128 characters or fewer."),
  username: z.string().max(128, "Keep the user to 128 characters or fewer."),
  /** Required for a new connection; blank keeps the saved one. */
  password: z.string().max(1024),
  tls: z.enum(TLS_MODES),
  trust: z.boolean(),
  schemas: z.string(),
  uri: z.string().max(1024, "Keep the connection string to 1024 characters or fewer."),
  read_preference: z.enum(READ_PREFERENCES),
  environment_url: z.string().max(255),
  tenant_id: z.string(),
  client_id: z.string(),
  mode: z.enum(["tds", "webapi"]),
  tables: z.string(),
  ssh_on: z.boolean(),
  ssh_host: z.string().max(255, "Keep the SSH server to 255 characters or fewer."),
  ssh_port: z.string().regex(/^\d{0,5}$/, "Enter the port as a number, for example 22."),
  ssh_user: z.string().max(64, "Keep the SSH user to 64 characters or fewer."),
  /** An uploaded key file, a key from the library, or a password. */
  ssh_auth: z.enum(["upload", "library", "password"]),
  ssh_key_id: z.string(),
  /** The chosen key file's text; blank keeps the saved key. */
  ssh_key: z.string().max(SSH_KEY_MAX, "This file is too large to be a private key. Choose the private key file."),
  ssh_cert: z.string().max(SSH_KEY_MAX, "This file is too large to be a certificate. Choose the file ending in -cert.pub."),
  ssh_cert_remove: z.boolean(),
  ssh_verify: z.enum(["pin", "ca"]),
  ssh_host_ca: z.string().max(4096, "Paste one public key line."),
  ssh_jumps: z.array(z.object({
    id: z.string(), host: z.string().max(255, "Keep the host to 255 characters or fewer."), port: z.string().regex(/^\d{0,5}$/, "Enter the port as a number, for example 22."),
    user: z.string().max(64, "Keep the user to 64 characters or fewer."), auth: z.enum(["library", "password"]), key_id: z.string(),
    password: z.string().max(1024, "Keep the password to 1024 characters or fewer."), verify: z.enum(["pin", "ca"]),
    host_ca: z.string().max(4096, "Paste one public key line."), repin: z.boolean(),
  })).max(MAX_JUMPS, `Use at most ${MAX_JUMPS} jump hosts.`),
  ssh_passphrase: z.string().max(1024, "Keep the passphrase to 1024 characters or fewer."),
  ssh_password: z.string().max(1024, "Keep the SSH password to 1024 characters or fewer."),
  ssh_repin: z.boolean(),
  freshness: z.enum(["live", "cached"]),
  minutes: z.string().regex(/^\d{1,4}$/, "Enter minutes as a number.").refine((v) => Number(v) >= 1 && Number(v) <= 1440, "Enter between 1 and 1,440 minutes."),
});
export type Details = z.infer<typeof details>;

/** Per-engine required fields and rules, as the API checks them (messages say how to fix the value). */
export function rulesFor(engine: EngineKey, existing: boolean, saved?: SshSaved) {
  return details.superRefine((v, ctx) => {
    const need = (path: keyof Details, message: string) => { if (!String(v[path] ?? "").trim()) ctx.addIssue({ code: "custom", path: [path], message }); };
    if (engine === "mongodb") {
      need("uri", "Enter the connection string, for example mongodb://db.example.com:27017.");
      if (v.uri && !/^mongodb(\+srv)?:\/\//.test(v.uri)) ctx.addIssue({ code: "custom", path: ["uri"], message: "Start with mongodb:// or mongodb+srv://." });
      if (/^mongodb(\+srv)?:\/\/[^/]*@/.test(v.uri)) ctx.addIssue({ code: "custom", path: ["uri"], message: "Remove the user name and password from the connection string; enter them below." });
    } else if (engine === "dataverse") {
      need("environment_url", "Enter the environment URL, for example https://contoso.crm4.dynamics.com.");
      if (v.environment_url && !/^https?:\/\/[^/]+\/?$/.test(v.environment_url)) ctx.addIssue({ code: "custom", path: ["environment_url"], message: "Enter only the environment address, for example https://contoso.crm4.dynamics.com." });
      for (const k of ["tenant_id", "client_id"] as const) if (!GUID.test(v[k])) ctx.addIssue({ code: "custom", path: [k], message: k === "tenant_id" ? "Enter the directory (tenant) ID, a GUID." : "Enter the application (client) ID, a GUID." });
      if (!splitList(v.tables).length) ctx.addIssue({ code: "custom", path: ["tables"], message: "Name the tables to read, for example tpg_project." });
    } else {
      need("host", "Enter the host.");
      if (!v.port || Number(v.port) < 1 || Number(v.port) > 65535) ctx.addIssue({ code: "custom", path: ["port"], message: "Enter a port between 1 and 65535." });
      if (engine !== "mysql" && !splitList(v.schemas).length) ctx.addIssue({ code: "custom", path: ["schemas"], message: `Name at least one schema, for example ${SCHEMAS[engine] ?? "public"}.` });
    }
    if (engine !== "dataverse") {
      need("database", "Enter the database name.");
      need("username", "Enter the read-only user.");
    }
    if (!existing && !v.password) ctx.addIssue({ code: "custom", path: ["password"], message: engine === "dataverse" ? "Enter the client secret." : "Enter the password." });
    if (v.ssh_on && SSH_ENGINES.includes(engine)) {
      need("ssh_host", "Enter the SSH server, for example bastion.example.com.");
      if (v.ssh_host.trim() && !SSH_HOST.test(v.ssh_host.trim())) ctx.addIssue({ code: "custom", path: ["ssh_host"], message: "Enter a host name or IP address, for example bastion.example.com." });
      if (!v.ssh_port || Number(v.ssh_port) < 1 || Number(v.ssh_port) > 65535) ctx.addIssue({ code: "custom", path: ["ssh_port"], message: "Enter a port between 1 and 65535, usually 22." });
      need("ssh_user", "Enter the SSH user.");
      const savedUpload = saved?.auth === "key" && saved.key_source !== "library" && saved.key_fingerprint;
      if (v.ssh_auth === "upload" && !v.ssh_key && !savedUpload) ctx.addIssue({ code: "custom", path: ["ssh_key"], message: "Choose the private key file (.pem, .ppk or OpenSSH)." });
      if (v.ssh_auth === "library" && !v.ssh_key_id) ctx.addIssue({ code: "custom", path: ["ssh_key_id"], message: "Choose a key from the library." });
      if (v.ssh_auth === "password" && !v.ssh_password && saved?.auth !== "password") ctx.addIssue({ code: "custom", path: ["ssh_password"], message: "Enter the SSH password." });
      if (v.ssh_verify === "ca" && !v.ssh_host_ca.trim() && !saved?.host_ca) ctx.addIssue({ code: "custom", path: ["ssh_host_ca"], message: "Paste the public key of the CA that signs the server's host certificates." });
      if (engine === "mongodb" && v.uri && (/^mongodb\+srv:/.test(v.uri) || /^mongodb:\/\/[^/]*,/.test(v.uri))) {
        ctx.addIssue({ code: "custom", path: ["uri"], message: "Through SSH, use a mongodb:// connection string with one host." });
      }
      v.ssh_jumps.forEach((j, i) => {
        const at = (k: string, message: string) => ctx.addIssue({ code: "custom", path: ["ssh_jumps", i, k], message });
        if (!j.host.trim()) at("host", "Enter the jump host.");
        else if (!SSH_HOST.test(j.host.trim())) at("host", "Enter a host name or IP address.");
        if (!j.port || Number(j.port) < 1 || Number(j.port) > 65535) at("port", "Enter a port between 1 and 65535, usually 22.");
        if (!j.user.trim()) at("user", "Enter the user on the jump host.");
        if (j.auth === "library" && !j.key_id) at("key_id", "Choose a key from the library.");
        const had = saved?.jump?.find((s) => s.id === j.id);
        if (j.auth === "password" && !j.password && had?.auth !== "password") at("password", "Enter the jump host's password.");
        if (j.verify === "ca" && !j.host_ca.trim() && !had?.host_ca) at("host_ca", "Paste the public key of the CA that signs the host certificates.");
      });
    }
  });
}

type Descriptor = { host?: string; port?: number; database?: string; username?: string; tls?: { mode?: string; trust_server_certificate?: boolean };
  approved_schemas?: string[]; uri?: string; read_preference?: string; environment_url?: string; tenant_id?: string; client_id?: string;
  mode?: string; tables?: string[] };

export function defaults(engine: EngineKey, c?: ConnectionItem): Details {
  const d = (c?.descriptor ?? {}) as Descriptor;
  const f = (c?.freshness ?? {}) as { mode?: string; cache_ttl_minutes?: number };
  return {
    name: c?.name ?? "", host: d.host ?? "", port: String(d.port ?? PORTS[engine] ?? ""), database: d.database ?? "", username: d.username ?? "",
    password: "", tls: (TLS_MODES as readonly string[]).includes(d.tls?.mode ?? "") ? (d.tls!.mode as Details["tls"]) : "verify-full",
    trust: !!d.tls?.trust_server_certificate,
    schemas: (d.approved_schemas ?? (SCHEMAS[engine] ? [SCHEMAS[engine]!] : [])).join(", "),
    uri: d.uri ?? "", read_preference: (READ_PREFERENCES as readonly string[]).includes(d.read_preference ?? "") ? (d.read_preference as Details["read_preference"]) : "secondaryPreferred",
    environment_url: d.environment_url ?? "", tenant_id: d.tenant_id ?? "", client_id: d.client_id ?? "", mode: d.mode === "webapi" ? "webapi" : "tds",
    tables: (d.tables ?? []).join(", "), freshness: f.mode === "cached" ? "cached" : "live", minutes: String(f.cache_ttl_minutes ?? 60),
    ...sshDefaults(savedSsh(c)),
  };
}

function sshDefaults(s?: SshSaved): Pick<Details, `ssh_${string}` & keyof Details> {
  return { ssh_on: !!s, ssh_host: s?.host ?? "", ssh_port: String(s?.port ?? 22), ssh_user: s?.username ?? "",
    ssh_auth: s?.auth === "password" ? "password" : s?.key_source === "library" ? "library" : "upload", ssh_key_id: s?.key_source === "library" ? (s.key_id ?? "") : "",
    ssh_key: "", ssh_cert: "", ssh_cert_remove: false, ssh_passphrase: "", ssh_password: "", ssh_repin: false,
    ssh_verify: s?.verify === "ca" ? "ca" : "pin", ssh_host_ca: s?.host_ca ?? "",
    ssh_jumps: (s?.jump ?? []).map((j) => ({ id: j.id, host: j.host, port: String(j.port), user: j.username, auth: j.auth, key_id: j.key_id ?? "",
      password: "", verify: j.verify === "ca" ? "ca" : "pin", host_ca: j.host_ca ?? "", repin: false })) };
}

export const newJump = (): Details["ssh_jumps"][number] =>
  ({ id: "", host: "", port: "22", user: "", auth: "library", key_id: "", password: "", verify: "pin", host_ca: "", repin: false });

/** Where the API names a field (`ssh.jump.1.password`), the form field it belongs to. */
export const SSH_FIELDS: Record<string, string> = {
  ssh: "ssh_on", "ssh.host": "ssh_host", "ssh.port": "ssh_port", "ssh.username": "ssh_user", "ssh.auth": "ssh_auth", "ssh.key_id": "ssh_key_id",
  "ssh.private_key": "ssh_key", "ssh.passphrase": "ssh_passphrase", "ssh.password": "ssh_password", "ssh.certificate": "ssh_cert", "ssh.host_ca": "ssh_host_ca",
  ...Object.fromEntries(Array.from({ length: MAX_JUMPS }, (_, i) => [["host", "host"], ["port", "port"], ["username", "user"], ["key_id", "key_id"],
    ["password", "password"], ["host_ca", "host_ca"]].map(([api, form]) => [`ssh.jump.${i}.${api}`, `ssh_jumps.${i}.${form}`])).flat()),
};

/** The `ssh` block of the request: blank secrets keep the saved ones; switching it off on an edit says so. */
export function sshBody(v: Details, hadSsh: boolean): Record<string, unknown> | undefined {
  if (!v.ssh_on) return hadSsh ? { enabled: false } : undefined;
  const check = (verify: "pin" | "ca", ca: string, repin: boolean) =>
    ({ verify, ...(verify === "ca" && ca.trim() ? { host_ca: ca.trim() } : {}), ...(repin ? { repin: true } : {}) });
  return {
    host: v.ssh_host.trim(), port: Number(v.ssh_port), username: v.ssh_user.trim(), auth: v.ssh_auth === "password" ? "password" : "key",
    ...(v.ssh_auth !== "password" ? { key_source: v.ssh_auth } : {}),
    ...(v.ssh_auth === "library" ? { key_id: v.ssh_key_id } : {}),
    ...(v.ssh_auth === "upload" && v.ssh_key ? { private_key: v.ssh_key, ...(v.ssh_passphrase ? { passphrase: v.ssh_passphrase } : {}) } : {}),
    ...(v.ssh_auth === "upload" && v.ssh_cert ? { certificate: v.ssh_cert } : v.ssh_auth === "upload" && v.ssh_cert_remove ? { certificate: "" } : {}),
    ...(v.ssh_auth === "password" && v.ssh_password ? { password: v.ssh_password } : {}),
    ...check(v.ssh_verify, v.ssh_host_ca, v.ssh_repin),
    jump: v.ssh_jumps.map((j) => ({ ...(j.id ? { id: j.id } : {}), host: j.host.trim(), port: Number(j.port), username: j.user.trim(), auth: j.auth,
      ...(j.auth === "library" ? { key_id: j.key_id } : j.password ? { password: j.password } : {}), ...check(j.verify, j.host_ca, j.repin) })),
  };
}

/** The request body for POST or PATCH /connections, with only the fields the engine uses. */
export function bodyFor(engine: EngineKey, v: Details, hadSsh = false): Record<string, unknown> {
  const common = { name: v.name, freshness: v.freshness === "cached" ? { mode: "cached", cache_ttl_minutes: Number(v.minutes) } : { mode: "live" } };
  if (engine === "dataverse") {
    return { ...common, environment_url: v.environment_url.trim(), tenant_id: v.tenant_id.trim(), client_id: v.client_id.trim(), mode: v.mode,
      tables: splitList(v.tables), ...(v.password ? { client_secret: v.password } : {}) };
  }
  const secret = v.password ? { password: v.password } : {};
  const ssh = sshBody(v, hadSsh);
  if (engine === "mongodb") {
    return { ...common, uri: v.uri.trim(), database: v.database, username: v.username, read_preference: v.read_preference,
      tls: { mode: v.tls === "disable" ? "disable" : "verify-full" }, ...secret, ...(ssh ? { ssh } : {}) };
  }
  const base = { ...common, host: v.host, port: Number(v.port), database: v.database, username: v.username, ...secret, ...(ssh ? { ssh } : {}) };
  if (engine === "sqlserver") return { ...base, tls: { trust_server_certificate: v.trust }, approved_schemas: splitList(v.schemas) };
  if (engine === "mysql") {
    const extra = splitList(v.schemas).filter((s) => s !== v.database);
    return { ...base, tls: { mode: v.tls }, ...(extra.length ? { approved_schemas: [v.database, ...extra] } : {}) };
  }
  return { ...base, tls: { mode: v.tls }, approved_schemas: splitList(v.schemas) };
}

const TlsSelect = (props: object) => (
  <NativeSelect {...props}>
    <option value="verify-full">verify-full</option>
    <option value="verify-ca">verify-ca</option>
    <option value="require">require</option>
    <option value="disable">disable (development only)</option>
  </NativeSelect>
);

/** The fields one engine needs; the common ones (name, freshness) are rendered by the page. */
export function EngineFields({ engine, control, existing, saved }: { engine: EngineKey; control: Control<Details>; existing: boolean; saved?: SshSaved }) {
  const secretNote = existing ? "Leave blank to keep the saved one. A new one is never shown again." : "Never shown again after you save.";
  if (engine === "dataverse") {
    return (
      <>
        <Field control={control} name="environment_url" label="Environment URL" description="For example https://contoso.crm4.dynamics.com." render={(f) => <Input {...f} autoComplete="off" />} />
        <Field control={control} name="tenant_id" label="Directory (tenant) ID" render={(f) => <Input {...f} autoComplete="off" />} />
        <Field control={control} name="client_id" label="Application (client) ID" render={(f) => <Input {...f} autoComplete="off" />} />
        <Field control={control} name="password" label="Client secret" description={secretNote} render={(f) => <Input {...f} type="password" autoComplete="new-password" />} />
        <Field control={control} name="mode" label="Mode" description="TDS reads through the SQL endpoint; Web API works when TDS is off, for fewer kinds of figures." render={(f) => (
          <NativeSelect {...f}>
            <option value="tds">TDS (SQL endpoint)</option>
            <option value="webapi">Web API</option>
          </NativeSelect>
        )} />
        <Field control={control} name="tables" label="Tables" description="Logical names, comma separated, for example tpg_project, tpg_projectrequest." render={(f) => <Input {...f} />} />
      </>
    );
  }
  if (engine === "mongodb") {
    return (
      <>
        <Field control={control} name="uri" label="Connection string" description="Without a user name or password, for example mongodb://db.example.com:27017." render={(f) => <Input {...f} autoComplete="off" />} />
        <Field control={control} name="database" label="Database" render={(f) => <Input {...f} />} />
        <Field control={control} name="username" label="Read-only user" render={(f) => <Input {...f} autoComplete="off" />} />
        <Field control={control} name="password" label="Password" description={secretNote} render={(f) => <Input {...f} type="password" autoComplete="new-password" />} />
        <Field control={control} name="tls" label="TLS" render={(f) => (
          <NativeSelect {...f} value={f.value === "disable" ? "disable" : "verify-full"}>
            <option value="verify-full">On</option>
            <option value="disable">Off (development only)</option>
          </NativeSelect>
        )} />
        <Field control={control} name="read_preference" label="Read preference" description="Secondary preferred keeps load off the primary." render={(f) => (
          <NativeSelect {...f}>
            {READ_PREFERENCES.map((p) => <option key={p} value={p}>{p}</option>)}
          </NativeSelect>
        )} />
        <SshFields control={control} saved={saved} engine={engine} />
      </>
    );
  }
  return (
    <>
      <Field control={control} name="host" label="Host" render={(f) => <Input {...f} autoComplete="off" />} />
      <Field control={control} name="port" label="Port" render={(f) => <Input {...f} inputMode="numeric" />} />
      <Field control={control} name="database" label="Database" render={(f) => <Input {...f} />} />
      <Field control={control} name="username" label="Read-only user" render={(f) => <Input {...f} autoComplete="off" />} />
      <Field control={control} name="password" label="Password" description={secretNote} render={(f) => <Input {...f} type="password" autoComplete="new-password" />} />
      {engine === "sqlserver" ? (
        <Field control={control} name="trust" label="Encryption" description="Always on. Trust the server certificate only in development." render={(f) => (
          <label className="flex items-center gap-2 text-body">
            <Checkbox checked={f.value} onCheckedChange={(v) => f.onChange(v === true)} aria-label="Trust the server certificate (development only)" />
            Trust the server certificate (development only)
          </label>
        )} />
      ) : (
        <Field control={control} name="tls" label="TLS" render={(f) => <TlsSelect {...f} />} />
      )}
      <Field control={control} name="schemas" label={engine === "mysql" ? "Other databases to include" : "Schemas"}
        description={engine === "mysql" ? "Optional, comma separated. The database above is always included." : `Comma separated, for example ${SCHEMAS[engine] ?? "public"}.`}
        render={(f) => <Input {...f} />} />
      {SSH_ENGINES.includes(engine) && <SshFields control={control} saved={saved} engine={engine} />}
    </>
  );
}

/**
 * The SSH tunnel section (ENH-003, ENH-004). Key files are read in the browser and sent once; the API checks them,
 * keeps them in the vault and shows only fingerprints from then on. Library keys are picked by name; jump hosts sit
 * in front of the SSH server, in the order Tela connects through them.
 */
function SshFields({ control, saved, engine }: { control: Control<Details>; saved?: SshSaved; engine: EngineKey }) {
  const appName = useAppName();
  const on = useWatch({ control, name: "ssh_on" });
  const auth = useWatch({ control, name: "ssh_auth" });
  const keys = useQuery({ queryKey: ["ssh-keys"], queryFn: () => api<{ items: SshKeyItem[] }>("/api/v1/admin/ssh-keys"), enabled: on, staleTime: 30_000 });
  const savedKey = saved?.auth === "key" && saved.key_source !== "library" && saved.key_fingerprint ? saved : undefined;
  // keyName: our rows carry their own `id` (the saved hop), so the field array keys rows by `key`.
  const jumps = useFieldArray({ control, name: "ssh_jumps", keyName: "key" });
  const library = keys.data?.items ?? [];
  return (
    <fieldset className="col-span-full grid gap-4 rounded-lg border border-line p-4 sm:grid-cols-2 lg:grid-cols-3">
      <legend className="px-1 text-body-strong">SSH tunnel</legend>
      <Field control={control} name="ssh_on" label="Network path" className="col-span-full"
        description={engine === "mongodb"
          ? `For a database on a private network: ${appName} signs in to an SSH server (a bastion) and reaches MongoDB from there. Use a mongodb:// connection string with one host, as the SSH server sees it. With TLS on, the certificate is checked but not its host name.`
          : `For a database on a private network: ${appName} signs in to an SSH server (a bastion) and reaches the database from there. The host and port above are as the SSH server sees them.`}
        render={(f) => (
          <label className="flex items-center gap-2 text-body">
            <Checkbox checked={f.value} onCheckedChange={(v) => f.onChange(v === true)} aria-label="Connect through an SSH server" />
            Connect through an SSH server
          </label>
        )} />
      {on && (
        <>
          <Field control={control} name="ssh_host" label="SSH server" description="Host name or IP address, for example bastion.example.com." render={(f) => <Input {...f} autoComplete="off" />} />
          <Field control={control} name="ssh_port" label="SSH port" render={(f) => <Input {...f} inputMode="numeric" />} />
          <Field control={control} name="ssh_user" label="SSH user" render={(f) => <Input {...f} autoComplete="off" />} />
          <Field control={control} name="ssh_auth" label="Sign in with" render={(f) => (
            <NativeSelect {...f}>
              <option value="upload">Private key file (.pem, .ppk)</option>
              <option value="library">Key from the library</option>
              <option value="password">Password</option>
            </NativeSelect>
          )} />
          {auth === "upload" && (
            <>
              <Field control={control} name="ssh_key" label="Private key file"
                description={savedKey ? <>Saved in the vault: {keyLabel(savedKey)} <Fingerprint value={savedKey.key_fingerprint!} />. Choose a file only to replace it.</> : "PEM (.pem), PuTTY (.ppk) or OpenSSH. Kept in the vault and never shown again."}
                render={fileInput} />
              <Field control={control} name="ssh_passphrase" label="Key passphrase" description="Only if the key has one. Used once to read the key; not stored."
                render={(f) => <Input {...f} type="password" autoComplete="off" />} />
              <Field control={control} name="ssh_cert" label="Certificate (optional)"
                description={savedKey?.certificate ? <>Saved: {certificateNote(savedKey.certificate)} Choose a file only to replace it.</> : "An OpenSSH user certificate for this key, the file ending in -cert.pub."}
                render={fileInput} />
              {savedKey?.certificate && (
                <Field control={control} name="ssh_cert_remove" label="Saved certificate" render={(f) => (
                  <label className="flex items-center gap-2 text-body">
                    <Checkbox checked={f.value} onCheckedChange={(v) => f.onChange(v === true)} aria-label="Stop using the saved certificate" />
                    Stop using the saved certificate
                  </label>
                )} />
              )}
            </>
          )}
          {auth === "library" && (
            <Field control={control} name="ssh_key_id" label="Library key" className="sm:col-span-2"
              description={<>Keys shared by connections, managed on <a className="text-link underline-offset-4 hover:underline" href="/admin/ssh-keys">SSH keys</a>.</>}
              render={(f) => keySelect(f, library, keys.isLoading, "Library key for the SSH server")} />
          )}
          {auth === "password" && (
            <Field control={control} name="ssh_password" label="SSH password"
              description={saved?.auth === "password" ? "Leave blank to keep the saved one. A new one is never shown again." : "Kept in the vault and never shown again."}
              render={(f) => <Input {...f} type="password" autoComplete="new-password" />} />
          )}
          <HostCheck control={control} prefix="ssh_" saved={saved} who="the SSH server" />
          <div className="col-span-full grid gap-3">
            <div className="grid gap-1">
              <h3 className="text-body-strong">Jump hosts</h3>
              <p className="text-small text-ink-muted">Only if the SSH server is itself behind other hosts. {appName} connects through them in this order, then to the SSH server above. Jump hosts sign in with a library key or a password.</p>
            </div>
            {jumps.fields.map((j, i) => (
              <JumpFields key={j.key} control={control} index={i} saved={j.id ? saved?.jump?.find((s) => s.id === j.id) : undefined}
                keys={library} loading={keys.isLoading} onRemove={() => jumps.remove(i)} onUp={i > 0 ? () => jumps.move(i, i - 1) : undefined} />
            ))}
            {jumps.fields.length < MAX_JUMPS && (
              <div><Button type="button" variant="secondary" size="sm" onClick={() => jumps.append(newJump())}><Plus aria-hidden />Add a jump host</Button></div>
            )}
          </div>
        </>
      )}
    </fieldset>
  );
}

type JumpField = `ssh_jumps.${number}`;

function JumpFields({ control, index, saved, keys, loading, onRemove, onUp }: {
  control: Control<Details>; index: number; saved?: JumpSaved; keys: SshKeyItem[]; loading: boolean; onRemove: () => void; onUp?: () => void;
}) {
  const appName = useAppName();
  const at = `ssh_jumps.${index}` as JumpField;
  const auth = useWatch({ control, name: `${at}.auth` });
  const prev = saved;
  const label = `Jump host ${index + 1}`;
  return (
    <fieldset className="grid gap-3 rounded-md border border-line p-3 sm:grid-cols-2 lg:grid-cols-3" aria-label={label}>
      <legend className="px-1 text-small font-medium">{label}{index === 0 ? ` (${appName} connects here first)` : ""}</legend>
      <Field control={control} name={`${at}.host`} label="Jump host" render={(f) => <Input {...f} autoComplete="off" aria-label={`${label} host`} />} />
      <Field control={control} name={`${at}.port`} label="Port" render={(f) => <Input {...f} inputMode="numeric" aria-label={`${label} port`} />} />
      <Field control={control} name={`${at}.user`} label="User" render={(f) => <Input {...f} autoComplete="off" aria-label={`${label} user`} />} />
      <Field control={control} name={`${at}.auth`} label="Sign in with" render={(f) => (
        <NativeSelect {...f} aria-label={`${label} sign in with`}>
          <option value="library">Key from the library</option>
          <option value="password">Password</option>
        </NativeSelect>
      )} />
      {auth === "library" ? (
        <Field control={control} name={`${at}.key_id`} label="Library key" className="sm:col-span-2"
          render={(f) => keySelect(f, keys, loading, `${label} library key`)} />
      ) : (
        <Field control={control} name={`${at}.password`} label="Password"
          description={prev?.auth === "password" ? "Leave blank to keep the saved one." : "Kept in the vault and never shown again."}
          render={(f) => <Input {...f} type="password" autoComplete="new-password" aria-label={`${label} password`} />} />
      )}
      <HostCheck control={control} prefix={`${at}.`} saved={prev} who={label.toLowerCase()} />
      <div className="col-span-full flex flex-wrap gap-2">
        {onUp && <Button type="button" variant="ghost" size="sm" onClick={onUp}><ArrowUp aria-hidden />Move earlier</Button>}
        <Button type="button" variant="ghost" size="sm" onClick={onRemove}><X aria-hidden />Remove {label.toLowerCase()}</Button>
      </div>
    </fieldset>
  );
}

/** Pin the first host key seen, or trust host certificates from a CA; with a pinned key, offer to trust a new one. */
function HostCheck({ control, prefix, saved, who }: { control: Control<Details>; prefix: "ssh_" | `${JumpField}.`; saved?: HostCheckSaved; who: string }) {
  const appName = useAppName();
  const name = (k: "verify" | "host_ca" | "repin") => `${prefix}${k}` as "ssh_verify";
  const verify = useWatch({ control, name: name("verify") });
  const label = who === "the SSH server" ? "" : `${who[0]!.toUpperCase()}${who.slice(1)} `;
  return (
    <>
      <Field control={control} name={name("verify")} label="Check the server with" render={(f) => (
        <NativeSelect {...f} aria-label={label ? `${label}check the server with` : undefined}>
          <option value="pin">Its host key, pinned on the first test</option>
          <option value="ca">Host certificates from a CA</option>
        </NativeSelect>
      )} />
      {verify === "ca" ? (
        <Field control={control} name={name("host_ca") as "ssh_host_ca"} label="Trusted host CA" className="sm:col-span-2"
          description={saved?.host_ca_fingerprint ? <>Saved CA <Fingerprint value={saved.host_ca_fingerprint} />. Paste another key only to replace it.</> : "The CA's public key, e.g. the line after @cert-authority in known_hosts."}
          render={(f) => <Textarea {...f} rows={2} className="font-mono text-small" aria-label={label ? `${label}trusted host CA` : undefined} />} />
      ) : saved?.host_key ? (
        <Field control={control} name={name("repin") as "ssh_repin"} label={`${label ? label : "SSH server "}identity`} className="sm:col-span-2"
          description={<>Pinned host key <Fingerprint value={saved.host_key} />. {appName} refuses a server that shows a different key. If it was rebuilt, trust its new key on the next test.</>}
          render={(f) => (
            <label className="flex items-center gap-2 text-body">
              <Checkbox checked={!!f.value} onCheckedChange={(v) => f.onChange(v === true)}
                aria-label={who === "the SSH server" ? "Trust the SSH server's new host key on the next test" : `Trust the ${who}'s new host key on the next test`} />
              {who === "the SSH server" ? "Trust the SSH server's new host key on the next test" : `Trust the ${who}'s new host key on the next test`}
            </label>
          )} />
      ) : (
        <p className="text-small text-ink-muted sm:col-span-2">The first successful test pins {who}'s host key; later connections must show the same key.</p>
      )}
    </>
  );
}

/** The library keys as a select (a render function, like `fileInput`). */
function keySelect(f: ComponentProps<typeof NativeSelect>, keys: SshKeyItem[], loading: boolean, ariaLabel?: string) {
  return (
    <NativeSelect {...f} aria-label={ariaLabel}>
      <option value="">{loading ? "Loading keys…" : keys.length ? "Choose a key" : "No keys yet: add one on SSH keys"}</option>
      {keys.map((k) => <option key={k.id} value={k.id}>{k.name} · {keyLabel(k)} {k.fingerprint.slice(0, 19)}…{k.certificate ? " · certificate" : ""}</option>)}
    </NativeSelect>
  );
}

/** A file picker whose value is the file's text. A render function, not a component, so the field's id and aria props reach the input. */
function fileInput(f: { name: string; ref: Ref<HTMLInputElement>; onBlur: () => void; disabled?: boolean; onChange: (v: string) => void }) {
  return <Input name={f.name} ref={f.ref} onBlur={f.onBlur} disabled={f.disabled} type="file" onChange={async (e) => f.onChange(await keyFileText(e.target.files?.[0]))} />;
}

/** "Certificate for tunnel, ops from CA SHA256:… — valid until 3 Oct 2027." */
export function certificateNote(c: SshCertificate): string {
  const until = c.valid_before ? `valid until ${new Date(c.valid_before * 1000).toLocaleDateString()}` : "valid with no end date";
  const expired = c.valid_before !== null && c.valid_before * 1000 < Date.now();
  return `Certificate for ${c.principals.length ? c.principals.join(", ") : "any user"}, ${expired ? "expired" : until}.`;
}

export const keyLabel = (s: { key_type?: string; key_bits?: number }) => `${(s.key_type ?? "").replace(/^ssh-|^ecdsa-sha2-/, "").toUpperCase()}${s.key_bits ? ` ${s.key_bits}-bit` : ""} key`;
/** SHA256 fingerprints are one long word: let them wrap inside a narrow column. */
export const Fingerprint = ({ value }: { value: string }) => <span className="break-all font-mono">{value}</span>;

/** The file's text, or a value the schema refuses as too large (a private key file is a few kilobytes). */
export async function keyFileText(file?: File): Promise<string> {
  if (!file) return "";
  if (file.size > SSH_KEY_MAX) return " ".repeat(SSH_KEY_MAX + 1);
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result ?? ""));
    reader.onerror = () => reject(reader.error ?? new Error("The file could not be read."));
    reader.readAsText(file);
  });
}
