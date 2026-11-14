import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { zodResolver } from "@hookform/resolvers/zod";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";

import { api, ApiError } from "../../../api/client";
import { PREDICATE_OPS, ScopePolicyBulkIn } from "../../../api/schemas";
import type { PolicyEntities, Predicate, Role, ScopePolicy } from "../../../api/types";
import { Alert, FormAlert } from "../../../components/ui/alert";
import { Badge } from "../../../components/ui/badge";
import { Button } from "../../../components/ui/button";
import { Checkbox } from "../../../components/ui/checkbox";
import { ConfirmDialog, type ConfirmRequest } from "../../../components/ui/confirm-dialog";
import { DataTable, type DataColumn } from "../../../components/ui/data-table";
import { Field, Form, FormControl, FormField, FormItem, FormLabel, FormMessage } from "../../../components/ui/form";
import { Input } from "../../../components/ui/input";
import { NativeSelect } from "../../../components/ui/native-select";
import { StatusBadge } from "../../../components/ui/status-badge";
import { applyServerError } from "../../../lib/api-errors";
import { ConnectionPicker } from "./ConnectionPicker";
import { PickList } from "./PickList";

/**
 * Row-scope policies per role and connection (API-005; RULE-004, RULE-005). Structured conditions only, never SQL.
 * Deleting or deactivating asks first (US-145); what a person or role ends up seeing is in the Effective access panel.
 */
export function ScopePoliciesPage({ connectionId, onConnection }: { connectionId: string; onConnection: (id: string) => void }) {
  return (
    <div className="grid gap-4">
      <p className="text-ink-muted">
        A person sees the rows allowed by any of their roles' policies (union). Filters can only narrow this. An entity
        with no policy for any of their roles is not visible at all. "All rows" must be chosen explicitly.
      </p>
      <ConnectionPicker value={connectionId} onChange={onConnection} />
      {connectionId && <PoliciesFor key={connectionId} connectionId={connectionId} />}
    </div>
  );
}

function PoliciesFor({ connectionId }: { connectionId: string }) {
  const qc = useQueryClient();
  const key = ["admin", "scope", connectionId];
  const policies = useQuery({
    queryKey: key,
    queryFn: () => api<{ items: ScopePolicy[] }>(`/api/v1/admin/scope-policies?connection_id=${connectionId}`),
  });
  const roles = useQuery({ queryKey: ["admin", "roles"], queryFn: () => api<{ items: Role[] }>("/api/v1/admin/roles") });
  const entities = useQuery({
    queryKey: ["admin", "scope", connectionId, "entities"],
    queryFn: () => api<PolicyEntities>(`/api/v1/admin/scope-policies/entities?connection_id=${connectionId}`),
  });
  const [error, setError] = useState<ApiError | null>(null);
  const [confirm, setConfirm] = useState<ConfirmRequest | null>(null);
  const onError = (e: ApiError) => setError(e);
  const onSuccess = () => {
    setError(null);
    void qc.invalidateQueries({ queryKey: key });
  };

  const remove = useMutation({
    mutationFn: (id: string) => api(`/api/v1/admin/scope-policies/${id}`, { method: "DELETE" }),
    onSuccess,
    onError,
  });
  const toggle = useMutation({
    mutationFn: (p: ScopePolicy) =>
      api(`/api/v1/admin/scope-policies/${p.id}`, { method: "PATCH", body: { is_active: !p.is_active } }),
    onSuccess,
    onError,
  });

  const columns: DataColumn<ScopePolicy>[] = [
    { id: "entity", header: "Entity", filter: "select", alwaysVisible: true },
    { id: "role", header: "Role", filter: "select" },
    { id: "rows", header: "Rows", value: (p) => (p.all_rows ? "All rows" : p.predicates.map(describePredicate).join(" and ")),
      cell: (p) => (p.all_rows ? <strong>All rows</strong> : p.predicates.map(describePredicate).join(" and ")) },
    { id: "description", header: "Shown on the scope chip" },
    { id: "is_active", header: "State", value: (p) => (p.is_active ? "Active" : "Inactive"), filter: "select",
      cell: (p) => (p.is_active ? <StatusBadge status="ok">Active</StatusBadge> : <Badge variant="neutral">Inactive</Badge>) },
    { id: "actions", header: "Actions", sortable: false, alwaysVisible: true, value: () => "", cell: (p) => (
      <span className="flex gap-3">
        <Button variant="link" size="sm" onClick={() => (p.is_active ? setConfirm({
          title: `Deactivate the ${p.role} policy on ${p.entity}?`,
          body: <p>People whose only access to {p.entity} comes from this policy stop seeing its rows at once. You can activate it again later.</p>,
          confirm: "Deactivate", danger: true, onConfirm: () => toggle.mutate(p),
        }) : toggle.mutate(p))}>{p.is_active ? "Deactivate" : "Activate"}</Button>
        <Button variant="link" size="sm" className="text-status-danger" onClick={() => setConfirm({
          title: `Delete the ${p.role} policy on ${p.entity}?`,
          body: <p>{p.all_rows ? "All rows" : p.predicates.map(describePredicate).join(" and ")} — people whose only access to {p.entity} comes from this policy stop seeing its rows at once. This cannot be undone.</p>,
          confirm: "Delete policy", danger: true, onConfirm: () => remove.mutate(p.id),
        })}>Delete</Button>
      </span>
    ) },
  ];

  return (
    <>
      <FormAlert error={error} />
      <DataTable data={policies.data?.items ?? []} columns={columns} label="Scope policies" getRowId={(p) => p.id}
        loading={policies.isLoading} columnFilters rowClassName={(p) => (p.is_active ? undefined : "text-ink-muted")}
        empty={{ title: "No policies for this connection", body: "Nobody can see its data yet. Add a policy below." }} />
      {/* Remounts when the model's entities arrive: the form switches from typed entities to a ticked list. */}
      <NewPolicy key={entities.data?.entities.length ? "model" : "typed"} connectionId={connectionId}
        roles={roles.data?.items ?? []} entities={entities.data?.entities ?? []} onSaved={onSuccess} />
      <ConfirmDialog request={confirm} onClose={() => setConfirm(null)} />
    </>
  );
}

export function describePredicate(p: Predicate): string {
  if (p.op === "is null") return `${p.field} is blank`;
  const rhs = p.attr ? `the person's ${p.attr}` : (p.values ?? []).join(", ");
  return `${p.field} ${p.op} ${rhs}`;
}

/** Without an approved model the entities are typed, comma separated; with one they are ticked in a list. */
function policyForm(typed: boolean) {
  return z.object({
    entities: z.array(z.string()),
    entityText: z.string(),
    roles: z.array(z.string()),
    allRows: z.boolean(),
    field: z.string(),
    op: z.enum(PREDICATE_OPS),
    source: z.enum(["values", "attr"]),
    values: z.string(),
    description: ScopePolicyBulkIn.shape.description,
  }).superRefine((v, ctx) => {
    if (typed && !splitList(v.entityText).length) ctx.addIssue({ code: "custom", path: ["entityText"], message: "Enter at least one entity, for example projects." });
    if (!typed && !v.entities.length) ctx.addIssue({ code: "custom", path: ["entities"], message: "Choose at least one entity." });
    if (!v.roles.length) ctx.addIssue({ code: "custom", path: ["roles"], message: "Choose at least one role." });
    if (v.allRows) return;
    if (!v.field.trim()) ctx.addIssue({ code: "custom", path: ["field"], message: "Enter the field to filter on, for example region." });
    if (v.op !== "is null" && !v.values.trim()) {
      ctx.addIssue({ code: "custom", path: ["values"], message: v.source === "attr" ? "Enter the attribute, for example cio." : "Enter at least one value." });
    }
  });
}
type PolicyForm = z.infer<ReturnType<typeof policyForm>>;
const EMPTY_POLICY: PolicyForm = { entities: [], entityText: "", roles: [], allRows: false, field: "", op: "in", source: "values", values: "", description: "" };

const splitList = (text: string) => [...new Set(text.split(",").map((x) => x.trim()).filter(Boolean))];

interface BulkResult {
  created: ScopePolicy[];
  skipped: { entity: string; role: string; id: string; is_active: boolean }[];
}

/** Fields every chosen entity has, in the first entity's order: one condition is applied to all of them. */
export function sharedFields(chosen: PolicyEntities["entities"]): { key: string; label: string }[] {
  const [first, ...rest] = chosen;
  if (!first) return [];
  return first.fields.filter((f) => rest.every((e) => e.fields.some((x) => x.key === f.key)));
}

/**
 * One form for one or many policies (US-172): tick several entities and several roles and the same condition is saved
 * for every pair, all or nothing. Pairs that already have an identical policy are skipped and listed.
 * With an approved model, entities and fields are picked from it; without one any entity is accepted, so they are typed.
 */
function NewPolicy({ connectionId, roles, entities, onSaved }: {
  connectionId: string; roles: Role[]; entities: PolicyEntities["entities"]; onSaved: () => void;
}) {
  const typed = !entities.length;
  const [error, setError] = useState<ApiError | null>(null);
  const [saved, setSaved] = useState<BulkResult | null>(null);
  const form = useForm<PolicyForm>({ resolver: zodResolver(policyForm(typed)), mode: "onTouched", defaultValues: EMPTY_POLICY });
  const [allRows, op, source, picked, entityText, pickedRoles] = form.watch(["allRows", "op", "source", "entities", "entityText", "roles"]);
  const chosen = entities.filter((e) => picked.includes(e.key));
  const fields = sharedFields(chosen);
  const entityCount = typed ? splitList(entityText).length : picked.length;
  const count = entityCount * pickedRoles.length;
  const pickEntities = (next: string[]) => {
    form.setValue("entities", next, { shouldValidate: form.formState.isSubmitted, shouldDirty: true });
    const still = sharedFields(entities.filter((e) => next.includes(e.key)));
    if (!still.some((f) => f.key === form.getValues("field"))) form.setValue("field", "");
  };
  const submit = async (v: PolicyForm) => {
    setError(null);
    setSaved(null);
    const predicate: Predicate = { field: v.field.trim(), op: v.op };
    if (v.op !== "is null") {
      if (v.source === "attr") predicate.attr = v.values.trim();
      else predicate.values = splitList(v.values);
    }
    try {
      const result = await api<BulkResult>("/api/v1/admin/scope-policies/bulk", {
        method: "POST",
        body: { connection_id: connectionId, entities: typed ? splitList(v.entityText) : v.entities, roles: v.roles,
          all_rows: v.allRows, predicates: v.allRows ? [] : [predicate], description: v.description },
      });
      form.reset({ ...EMPTY_POLICY, roles: v.roles });
      setSaved(result);
      onSaved();
    } catch (e) {
      setError(applyServerError(form, e, { fields: { entities: typed ? "entityText" : "entities", all_rows: "allRows",
        "predicates.0.field": "field", "predicates.0.values": "values", "predicates.0.attr": "values", "predicates.0.op": "op" } }));
    }
  };
  const c = form.control;
  return (
    <Form {...form}>
      <form className="tl mt-4 grid gap-4 rounded-lg border border-line bg-surface-raised p-5" onSubmit={form.handleSubmit(submit)}
        aria-labelledby="new-policy-title" noValidate>
        <div className="grid gap-1">
          <h2 id="new-policy-title" className="text-heading">New scope policy</h2>
          <p className="text-small text-ink-muted">Choose one or more entities and roles. The same rows rule is saved for every entity and role you choose.</p>
        </div>
        <div className="grid gap-4 sm:grid-cols-2">
          {typed ? (
            <Field control={c} name="entityText" label="Entities (comma separated)" description="No approved model yet, so type the entity keys."
              render={(f) => <Input {...f} placeholder="projects, risks" />} />
          ) : (
            <FormField control={c} name="entities" render={({ field, fieldState }) => (
              <FormItem>
                <PickList label="Entities" value={field.value} onChange={pickEntities} invalid={!!fieldState.error}
                  items={entities.map((e) => ({ value: e.key, label: e.label, hint: e.table }))} />
                <FormMessage />
              </FormItem>
            )} />
          )}
          <FormField control={c} name="roles" render={({ field, fieldState }) => (
            <FormItem>
              <PickList label="Roles" value={field.value} invalid={!!fieldState.error}
                onChange={(next) => form.setValue("roles", next, { shouldValidate: form.formState.isSubmitted, shouldDirty: true })}
                items={roles.map((r) => ({ value: r.key, label: r.name }))} />
              <FormMessage />
            </FormItem>
          )} />
        </div>
        <FormField control={c} name="allRows" render={({ field }) => (
          <FormItem>
            <div className="flex h-control-md items-center gap-2">
              <FormControl>
                <Checkbox checked={field.value} onCheckedChange={(v) => field.onChange(v === true)} onBlur={field.onBlur} />
              </FormControl>
              <FormLabel className="font-normal">All rows</FormLabel>
            </div>
            <FormMessage />
          </FormItem>
        )} />
        {!allRows && (
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <Field control={c} name="field" label="Field"
              description={!typed && chosen.length > 1 ? "Only fields every chosen entity has." : undefined}
              render={(f) => (typed ? <Input {...f} placeholder="region" /> : (
                <NativeSelect {...f} disabled={!chosen.length || !fields.length}>
                  <option value="">{!chosen.length ? "Choose an entity first" : fields.length ? "Choose…" : "No field common to these entities"}</option>
                  {fields.map((x) => <option key={x.key} value={x.key}>{x.label} ({x.key})</option>)}
                </NativeSelect>
              ))} />
            <Field control={c} name="op" label="Condition" render={(f) => (
              <NativeSelect {...f}>
                <option value="=">equals</option>
                <option value="in">is one of</option>
                <option value="not in">is not one of</option>
                <option value="is null">is blank</option>
              </NativeSelect>
            )} />
            {op !== "is null" && (
              <>
                <Field control={c} name="source" label="Compare with" render={(f) => (
                  <NativeSelect {...f}>
                    <option value="values">fixed values</option>
                    <option value="attr">the person's attribute</option>
                  </NativeSelect>
                )} />
                <Field control={c} name="values" label={source === "attr" ? "Attribute" : "Values (comma separated)"}
                  render={(f) => <Input {...f} placeholder={source === "attr" ? "cio" : "KSA, GCC - UAE"} />} />
              </>
            )}
          </div>
        )}
        <Field control={c} name="description" label="Shown on the scope chip" description="What people see on their scope chip, for example Region: KSA."
          render={(f) => <Input {...f} maxLength={300} placeholder="Region: KSA" />} />
        {allRows && <Alert variant="warn"><p>Everyone with {pickedRoles.length > 1 ? "these roles" : "this role"} will see every row of {entityCount > 1 ? "these entities" : "this entity"}.</p></Alert>}
        <FormAlert error={error} />
        {saved && <SavedNotice result={saved} />}
        <div className="flex flex-wrap items-center gap-3">
          <Button type="submit" disabled={form.formState.isSubmitting}>{count > 1 ? `Save ${count} policies` : "Save policy"}</Button>
          {count > 1 && <span className="text-small text-ink-muted">{entityCount} {entityCount === 1 ? "entity" : "entities"} × {pickedRoles.length} {pickedRoles.length === 1 ? "role" : "roles"}</span>}
        </div>
      </form>
    </Form>
  );
}

function SavedNotice({ result }: { result: BulkResult }) {
  const { created, skipped } = result;
  const n = created.length;
  const list = skipped.slice(0, 5).map((s) => `${s.entity} for ${s.role}${s.is_active ? "" : " (inactive)"}`).join(", ");
  return (
    <Alert>
      <p>{n ? `Added ${n} ${n === 1 ? "policy" : "policies"}.` : "Nothing new to add."}</p>
      {skipped.length > 0 && (
        <p>{skipped.length === 1 ? "1 pair already had" : `${skipped.length} pairs already had`} this exact policy and {skipped.length === 1 ? "was" : "were"} left as {skipped.length === 1 ? "it is" : "they are"}: {list}{skipped.length > 5 ? `, and ${skipped.length - 5} more` : ""}.</p>
      )}
    </Alert>
  );
}
