import { useMutation, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";

import { api } from "../../../api/client";
import type { PermissionGrant, Role, ScopePreview } from "../../../api/types";
import { can, useMe } from "../../../app/me";
import { PeoplePicker } from "../../../components/PeoplePicker";
import { Alert, FormAlert } from "../../../components/ui/alert";
import { Badge } from "../../../components/ui/badge";
import { Label } from "../../../components/ui/label";
import { NativeSelect } from "../../../components/ui/native-select";
import { RadioGroup, RadioGroupItem } from "../../../components/ui/radio-group";
import { Skeleton } from "../../../components/ui/skeleton";
import { asApiError } from "../../../lib/api-errors";
import { ConnectionPicker } from "./ConnectionPicker";

/** What each permission lets a person do, in plain words, in the order people think of them. */
const CAN: [string, string][] = [
  ["dashboards.view", "read shared dashboards"], ["ask.use", "ask"], ["drill.use", "drill to records"], ["export.use", "export"],
  ["reports.create", "build reports"], ["dashboards.create", "build dashboards"], ["fields.create", "make personal fields"],
  ["customsql.use", "write custom SQL"], ["share.create", "share"], ["dashboards.group", "group dashboards"], ["dashboards.publish", "publish to roles"], ["packs.author", "author packs"],
  ["certification.decide", "certify definitions"], ["connections.manage", "manage connections"], ["model.approve", "approve models"],
  ["mappings.manage", "map groups"], ["policies.manage", "set access policies"], ["audit.read", "read the audit log"],
  ["settings.manage", "change AI settings"],
];

/** Permissions given to one person on one connection (US-161), in plain words. */
const GRANTED: Record<string, string> = { "ask.freehand": "ask free-hand questions" };

const LEVELS: Record<string, string> = { admin: "Admin", creator: "Creator", viewer: "Viewer" };

function describeCondition(c: { field: string; op: string; values: unknown[] }): string {
  if (c.op === "is null") return `${c.field} is blank`;
  const values = c.values.map(String).join(", ");
  return c.op === "=" ? `${c.field} is ${values}` : c.op === "in" ? `${c.field} is one of ${values}` : c.op === "not in" ? `${c.field} is not ${values}` : `${c.field} ${c.op} ${values}`;
}

/**
 * Effective access (US-145; board Access): pick a person or a role and see what the server resolves on a connection —
 * level, roles, rows per entity, hidden and masked fields, and what they can do. Nothing is resolved in the browser;
 * every preview is audited.
 */
export function EffectiveAccess({ connectionId, onConnection }: { connectionId: string; onConnection: (id: string) => void }) {
  const { data: me } = useMe();
  const [kind, setKind] = useState<"person" | "role">("person");
  const roles = useQuery({ queryKey: ["admin", "roles"], queryFn: () => api<{ items: Role[] }>("/api/v1/admin/roles") });
  const preview = useMutation({
    mutationFn: (who: { user_id: string } | { role: string }) =>
      api<ScopePreview>(`/api/v1/admin/scope-policies/preview?${new URLSearchParams({ connection_id: connectionId, ...who }).toString()}`, { method: "POST" }),
  });
  const p = preview.data;
  return (
    <div className="grid gap-4">
      <ConnectionPicker id="effective-connection" value={connectionId} onChange={(id) => { onConnection(id); preview.reset(); }} />
      <fieldset className="grid gap-2">
        <legend className="mb-1 text-body-strong">Show access for</legend>
        <RadioGroup value={kind} onValueChange={(v) => { setKind(v as "person" | "role"); preview.reset(); }} className="flex gap-5" aria-label="Show access for">
          <label className="flex items-center gap-2"><RadioGroupItem value="person" />A person</label>
          <label className="flex items-center gap-2"><RadioGroupItem value="role" />A role</label>
        </RadioGroup>
      </fieldset>
      {!connectionId ? (
        <p className="text-ink-muted">Choose a connection: rows and fields are set per connection.</p>
      ) : kind === "person" ? (
        <PeoplePicker label="Person" placeholder="Name or e-mail" pickHint="Enter to show" single admin={can(me, "mappings.manage")}
          onPick={(person) => preview.mutate({ user_id: person.id })} />
      ) : (
        <div className="grid gap-1">
          <Label htmlFor="effective-role">Role</Label>
          <NativeSelect id="effective-role" defaultValue="" onChange={(e) => e.target.value && preview.mutate({ role: e.target.value })}>
            <option value="" disabled>Choose a role…</option>
            {(roles.data?.items ?? []).map((r) => <option key={r.id} value={r.key}>{r.name} ({r.kind === "system" ? "level" : "audience"})</option>)}
          </NativeSelect>
        </div>
      )}
      <FormAlert error={preview.error ? asApiError(preview.error) : null} />
      {preview.isPending && <Skeleton className="h-40" />}
      {p && !preview.isPending && <Resolved p={p} />}
    </div>
  );
}

function Resolved({ p }: { p: ScopePreview }) {
  const isRole = p.subject.kind === "role";
  const doing = CAN.filter(([perm]) => p.permissions.includes(perm)).map(([, words]) => words);
  const entities = Object.entries(p.scope.entities);
  const grants = p.grants ?? [];
  return (
    <section className="grid gap-3 rounded-md border border-line p-3" aria-label={`Access for ${p.subject.display_name}`} aria-live="polite">
      <dl className="grid gap-x-4 gap-y-2 text-small sm:grid-cols-[max-content_1fr]">
        <dt className="text-ink-muted">{isRole ? "Role" : "Person"}</dt>
        <dd className="text-body-strong">{p.subject.display_name}{isRole && " (someone holding only this role)"}</dd>
        {!isRole && (<><dt className="text-ink-muted">Groups</dt><dd>{p.groups.join(", ") || "None"}</dd></>)}
        <dt className="text-ink-muted">Level</dt>
        <dd>{p.level ? LEVELS[p.level] ?? p.level : "None: cannot sign in"}{!isRole && p.groups.length > 1 && p.level && " (the highest of these groups)"}</dd>
        <dt className="text-ink-muted">Roles</dt>
        <dd>{p.audience_roles.length ? p.audience_roles.join(", ") : "No audience roles"}</dd>
        <dt className="text-ink-muted">Rows</dt>
        <dd className="grid gap-1">
          {p.scope.error ? (
            <span className="text-status-danger">{p.scope.error.message}</span>
          ) : entities.length === 0 ? (
            <span>No rows on this connection: no policy covers {isRole ? "this role" : "their roles"}.</span>
          ) : entities.map(([entity, e]) => (
            <span key={entity}>
              <span className="text-body-strong">{entity}</span>{": "}
              {e.all_rows ? "all rows" : e.alternatives.map((alt) => alt.map(describeCondition).join(" and ")).join(", or ")}
              {p.rows_in_scope?.[entity] !== undefined && <Badge variant="neutral" className="ml-2">{p.rows_in_scope[entity]!.toLocaleString()} rows</Badge>}
            </span>
          ))}
        </dd>
        <dt className="text-ink-muted">Hidden</dt>
        <dd>{p.scope.hidden_fields.length ? p.scope.hidden_fields.join(", ") : "No hidden fields"}</dd>
        {p.scope.masked_fields.length > 0 && (<><dt className="text-ink-muted">Masked</dt><dd>{p.scope.masked_fields.join(", ")}</dd></>)}
        <dt className="text-ink-muted">Can</dt>
        <dd>{doing.length ? doing.join(", ").replace(/^./, (c) => c.toUpperCase()) : "Nothing yet"}{doing.length > 0 && <span className="text-ink-muted"> (from {isRole ? "this role" : "their roles"})</span>}</dd>
        {!isRole && (
          <>
            <dt className="text-ink-muted">Given</dt>
            <dd className="grid gap-1">
              {grants.length ? grants.map((g) => <GrantLine key={g.id} g={g} />) : <span>Nothing given to them personally on this connection</span>}
            </dd>
          </>
        )}
      </dl>
      {p.scope.error?.details?.missing_attribute != null && isRole && (
        <Alert><p>This role's rows depend on each person's {String(p.scope.error.details.missing_attribute)}. Show a named person to see their rows.</p></Alert>
      )}
      <p className="text-small text-ink-muted">
        Worked out by the server from {isRole ? "this role's" : "the person's groups and their roles'"} policies. Rows allowed by any role are combined; the preview is recorded in the audit log.
      </p>
      {!isRole && (
        <Link className="text-small text-link underline-offset-4 hover:underline" to={`/admin/audit?${new URLSearchParams({ actor: p.subject.display_name }).toString()}`}>
          {p.subject.display_name}'s recent activity
        </Link>
      )}
    </section>
  );
}

const day = (iso: string) => new Date(iso).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });

/** A personal grant and where it comes from: who gave it, when, why and until when. */
function GrantLine({ g }: { g: PermissionGrant }) {
  return (
    <span>
      <span className="text-body-strong">{(GRANTED[g.permission] ?? g.permission).replace(/^./, (c) => c.toUpperCase())}</span>
      {" until "}{day(g.expires_at)}
      <span className="text-ink-muted"> (given by {g.granted_by?.display_name ?? "an Admin"} on {day(g.granted_at)}: {g.reason})</span>
    </span>
  );
}
