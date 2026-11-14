import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { zodResolver } from "@hookform/resolvers/zod";
import { useEffect, useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";

import { api, ApiError } from "../../../api/client";
import { FieldRuleIn } from "../../../api/schemas";
import type { FieldRule, Role } from "../../../api/types";
import { FormAlert } from "../../../components/ui/alert";
import { Button } from "../../../components/ui/button";
import { Checkbox } from "../../../components/ui/checkbox";
import { ConfirmDialog, type ConfirmRequest } from "../../../components/ui/confirm-dialog";
import { DataTable, type DataColumn } from "../../../components/ui/data-table";
import { Field, Form, FormField, FormItem, FormMessage } from "../../../components/ui/form";
import { Input } from "../../../components/ui/input";
import { NativeSelect } from "../../../components/ui/native-select";
import { applyServerError } from "../../../lib/api-errors";
import { ConnectionPicker } from "./ConnectionPicker";

/** Field hiding per role (API-006; FEAT-003). The most restrictive rule wins for people with several roles. */
export function FieldPoliciesPage({ connectionId, onConnection }: { connectionId: string; onConnection: (id: string) => void }) {
  return (
    <div className="grid gap-4">
      <p className="text-ink-muted">
        Hidden fields disappear from answers, widgets, drill lists, exports, filters and the AI's context for that role —
        for example finance fields hidden from CIOs.
      </p>
      <ConnectionPicker value={connectionId} onChange={onConnection} />
      {connectionId && <RulesFor key={connectionId} connectionId={connectionId} />}
    </div>
  );
}

function RulesFor({ connectionId }: { connectionId: string }) {
  const qc = useQueryClient();
  const key = ["admin", "fields", connectionId];
  const rules = useQuery({
    queryKey: key,
    queryFn: () => api<{ rules: FieldRule[] }>(`/api/v1/admin/field-policies?connection_id=${connectionId}`),
  });
  const roles = useQuery({ queryKey: ["admin", "roles"], queryFn: () => api<{ items: Role[] }>("/api/v1/admin/roles") });
  const [draft, setDraft] = useState<FieldRule[]>([]);
  const [error, setError] = useState<ApiError | null>(null);
  const [confirm, setConfirm] = useState<ConfirmRequest | null>(null);
  useEffect(() => setDraft(rules.data?.rules ?? []), [rules.data]);

  const save = useMutation({
    mutationFn: (next: FieldRule[]) =>
      api<{ rules: FieldRule[] }>("/api/v1/admin/field-policies", { method: "PUT", body: { connection_id: connectionId, rules: next } }),
    onSuccess: (data) => {
      setError(null);
      qc.setQueryData(key, data);
    },
    onError: (e: ApiError) => setError(e),
  });

  // The add form shows its own errors, so it saves without the table's error handler.
  const saveRules = async (next: FieldRule[]) => {
    const data = await api<{ rules: FieldRule[] }>("/api/v1/admin/field-policies", { method: "PUT", body: { connection_id: connectionId, rules: next } });
    setError(null);
    qc.setQueryData(key, data);
  };

  const columns: DataColumn<FieldRule>[] = [
    { id: "field_ref", header: "Field", alwaysVisible: true, value: (r) => r.field_ref.replace(/^field:/, "") },
    { id: "hidden_for", header: "Hidden for", value: (r) => r.hidden_for.join(", ") },
    { id: "effect", header: "Effect", filter: "select", value: (r) => (r.effect === "hide" ? "Hidden" : "Masked") },
    { id: "actions", header: "Actions", sortable: false, alwaysVisible: true, value: () => "", cell: (r) => (
      <Button variant="link" size="sm" className="text-status-danger" onClick={() => setConfirm({
        title: `Show ${r.field_ref.replace(/^field:/, "")} again?`,
        body: <p>{r.hidden_for.join(", ")} will see this field in answers, widgets, drill lists, exports, filters and the AI's context from now on.</p>,
        confirm: "Remove rule", danger: true, onConfirm: () => save.mutate(draft.filter((x) => x !== r)),
      })}>Remove</Button>
    ) },
  ];

  return (
    <>
      <FormAlert error={error} />
      <DataTable data={draft} columns={columns} label="Field policies" getRowId={(r) => r.field_ref + r.effect} loading={rules.isLoading}
        columnFilters empty={{ title: "No fields are hidden on this connection" }} />
      <HideField roles={roles.data?.items ?? []} onAdd={(rule) => saveRules([...draft.filter((r) => r.field_ref !== rule.field_ref), rule])} />
      <ConfirmDialog request={confirm} onClose={() => setConfirm(null)} />
    </>
  );
}

const ruleForm = z.object({
  field_ref: FieldRuleIn.shape.field_ref,
  hidden_for: FieldRuleIn.shape.hidden_for,
  effect: z.enum(["hide", "mask"]),
});
type RuleForm = z.infer<typeof ruleForm>;

function HideField({ roles, onAdd }: { roles: Role[]; onAdd: (rule: FieldRule) => Promise<unknown> }) {
  const [error, setError] = useState<ApiError | null>(null);
  const form = useForm<RuleForm>({ resolver: zodResolver(ruleForm), mode: "onTouched", defaultValues: { field_ref: "", hidden_for: [], effect: "hide" } });
  const submit = async (v: RuleForm) => {
    setError(null);
    try {
      await onAdd(v);
      form.reset({ field_ref: "", hidden_for: [], effect: v.effect });
    } catch (e) {
      setError(applyServerError(form, e));
    }
  };
  const c = form.control;
  return (
    <Form {...form}>
      <form className="tl mt-4 grid gap-4 rounded-lg border border-line bg-surface-raised p-5" onSubmit={form.handleSubmit(submit)}
        aria-labelledby="hide-field-title" noValidate>
        <h2 id="hide-field-title" className="text-heading">Hide a field</h2>
        <div className="grid gap-4 sm:grid-cols-[2fr_1fr]">
          <Field control={c} name="field_ref" label="Field (entity.field)" render={(f) => <Input {...f} placeholder="projects.approved_budget_value_usd" />} />
          <Field control={c} name="effect" label="Effect" description="Masked fields show as ••• ; hidden fields disappear." render={(f) => (
            <NativeSelect {...f}>
              <option value="hide">Hide</option>
              <option value="mask">Mask</option>
            </NativeSelect>
          )} />
        </div>
        <FormField control={c} name="hidden_for" render={({ field }) => (
          <FormItem>
            <fieldset className="grid gap-2">
              <legend className="mb-1 text-body-strong">For roles</legend>
              <div className="flex flex-wrap gap-x-5 gap-y-2">
                {roles.map((r) => (
                  <label key={r.id} className="flex items-center gap-2">
                    <Checkbox
                      checked={field.value.includes(r.key)}
                      aria-invalid={!!form.formState.errors.hidden_for}
                      onCheckedChange={(on) => field.onChange(on === true ? [...field.value, r.key] : field.value.filter((k) => k !== r.key))}
                    />
                    {r.name}
                  </label>
                ))}
              </div>
            </fieldset>
            <FormMessage />
          </FormItem>
        )} />
        <FormAlert error={error} />
        <div><Button type="submit" disabled={form.formState.isSubmitting}>Save</Button></div>
      </form>
    </Form>
  );
}
