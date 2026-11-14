import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { zodResolver } from "@hookform/resolvers/zod";
import { useEffect, useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";

import { api, apiRequest, ApiError } from "../../../api/client";
import { GroupIn, MappingIn } from "../../../api/schemas";
import type { IdpGroup, Mapping, MappingsResponse, Role } from "../../../api/types";
import { useMe } from "../../../app/me";
import { FormAlert } from "../../../components/ui/alert";
import { Badge } from "../../../components/ui/badge";
import { Button } from "../../../components/ui/button";
import { ConfirmDialog, type ConfirmRequest } from "../../../components/ui/confirm-dialog";
import { DataTable, type DataColumn } from "../../../components/ui/data-table";
import { Field, Form } from "../../../components/ui/form";
import { Input } from "../../../components/ui/input";
import { NativeSelect } from "../../../components/ui/native-select";
import { StatusBadge } from "../../../components/ui/status-badge";
import { applyServerError, asApiError } from "../../../lib/api-errors";

interface Draft {
  idp_group_id: string;
  role: string;
  scope_attributes: Record<string, string[]>;
}

const toDraft = (m: Mapping): Draft => ({
  idp_group_id: m.idp_group_id,
  role: m.role,
  scope_attributes: m.scope_attributes,
});

/** Group → role mappings, saved as a whole set with If-Match (API-004). Admin grants need a second Admin. */
export function GroupMappingsPage() {
  const qc = useQueryClient();
  const { data: me } = useMe();
  const mappings = useQuery({
    queryKey: ["admin", "mappings"],
    queryFn: () => api<MappingsResponse>("/api/v1/admin/group-mappings"),
  });
  const roles = useQuery({ queryKey: ["admin", "roles"], queryFn: () => api<{ items: Role[] }>("/api/v1/admin/roles") });
  const groups = useQuery({ queryKey: ["admin", "groups"], queryFn: () => api<{ items: IdpGroup[] }>("/api/v1/admin/groups") });

  const [error, setError] = useState<ApiError | null>(null);
  const refresh = () => qc.invalidateQueries({ queryKey: ["admin"] });

  const save = useMutation({
    mutationFn: async (next: Draft[]) =>
      (await apiRequest<MappingsResponse>("/api/v1/admin/group-mappings", {
        method: "PUT",
        body: { mappings: next },
        ifMatch: mappings.data?.etag,
      })).data,
    onSuccess: (data) => {
      setError(null);
      qc.setQueryData(["admin", "mappings"], data);
      void qc.invalidateQueries({ queryKey: ["me"] });
    },
    onError: (e: ApiError) => setError(e),
  });

  const approve = useMutation({
    mutationFn: (id: string) => api<MappingsResponse>(`/api/v1/admin/group-mappings/${id}/approve`, { method: "POST" }),
    onSuccess: (data) => {
      setError(null);
      qc.setQueryData(["admin", "mappings"], data);
    },
    onError: (e: ApiError) => setError(e),
  });

  const current = mappings.data?.mappings ?? [];
  // The add form shows its own errors, so it saves without the page-level error handler.
  const saveSet = async (next: Draft[]) => {
    const data = (await apiRequest<MappingsResponse>("/api/v1/admin/group-mappings", {
      method: "PUT", body: { mappings: next }, ifMatch: mappings.data?.etag,
    })).data;
    setError(null);
    qc.setQueryData(["admin", "mappings"], data);
    void qc.invalidateQueries({ queryKey: ["me"] });
  };
  const [confirm, setConfirm] = useState<ConfirmRequest | null>(null);
  const [prefill, setPrefill] = useState<string>("");
  const remove = (m: Mapping) => setConfirm({
    title: `Remove the mapping for ${m.group_name}?`,
    body: (
      <p>
        Members of {m.group_name} lose the {m.role} role{m.role_kind === "system" ? " and, if no other group gives them a level, can no longer sign in" : ""}.
        This takes effect at their next request.
      </p>
    ),
    confirm: "Remove mapping", danger: true,
    onConfirm: () => save.mutate(current.filter((x) => x.id !== m.id).map(toDraft)),
  });

  // Mappings, then the registered groups no mapping uses: their members cannot sign in.
  const allGroups = groups.data?.items ?? [];
  const members = new Map(allGroups.map((g) => [g.external_group_id, g.member_count ?? null]));
  const mapped = new Set(current.map((m) => m.idp_group_id));
  const rows: Row[] = [
    ...current.map((m): Row => ({ kind: "mapping", id: m.id, group: m.group_name, groupId: m.idp_group_id, members: members.get(m.idp_group_id) ?? null, mapping: m })),
    ...allGroups.filter((g) => !mapped.has(g.external_group_id)).map((g): Row => ({ kind: "unmapped", id: `g-${g.id}`, group: g.display_name, groupId: g.external_group_id, members: g.member_count ?? null })),
  ];
  const unmappedCount = rows.filter((r) => r.kind === "unmapped").length;

  const columns: DataColumn<Row>[] = [
    { id: "group", header: "Group", alwaysVisible: true, cell: (r) => (
      <span className="grid"><span>{r.group}</span><span className="text-small text-ink-muted">{r.groupId}</span></span>
    ) },
    { id: "role", header: "Role", filter: "select", value: (r) => r.mapping?.role ?? "Not mapped", cell: (r) => (r.mapping
      ? <>{r.mapping.role} <span className="text-small text-ink-muted">({r.mapping.role_kind === "system" ? "level" : "audience"})</span></>
      : <span className="text-ink-muted">Not mapped: members cannot sign in</span>) },
    { id: "scope", header: "Scope attributes", sortable: false,
      value: (r) => Object.entries(r.mapping?.scope_attributes ?? {}).map(([k, v]) => `${k} = ${v.join(", ")}`).join("; ") },
    { id: "members", header: "Members", numeric: true, value: (r) => r.members },
    { id: "status", header: "Status", filter: "select",
      value: (r) => (!r.mapping ? "Not mapped" : r.mapping.status === "pending_approval" ? "Pending approval" : "Active"),
      cell: (r) => (!r.mapping ? <Badge variant="neutral">Not mapped</Badge>
        : r.mapping.status === "pending_approval" ? <StatusBadge status="warn">Pending approval</StatusBadge> : <StatusBadge status="ok">Active</StatusBadge>) },
    { id: "actions", header: "Actions", sortable: false, alwaysVisible: true, value: () => "", cell: (r) => (r.mapping ? (
      <span className="flex gap-3">
        {r.mapping.status === "pending_approval" && r.mapping.requested_by !== me?.user_id && (
          <Button variant="secondary" size="sm" onClick={() => approve.mutate(r.mapping!.id)} disabled={approve.isPending}>Approve</Button>
        )}
        <Button variant="link" size="sm" className="text-status-danger" onClick={() => remove(r.mapping!)} disabled={save.isPending}>Remove</Button>
      </span>
    ) : (
      <Button variant="link" size="sm" onClick={() => { setPrefill(r.groupId); document.getElementById("add-mapping-title")?.scrollIntoView({ block: "center" }); }}>Map</Button>
    )) },
  ];

  return (
    <div className="grid gap-4">
      <p className="text-ink-muted">
        Each Microsoft group maps to a permission level (Admin, Creator, Viewer) and optional audience roles. A person in
        several groups gets the highest level. Mappings that grant Admin stay pending until a different Admin approves them.
      </p>
      <FormAlert error={error ?? (mappings.error ? asApiError(mappings.error) : null)} />
      <DataTable data={rows} columns={columns} label="Group mappings" getRowId={(r) => r.id} loading={mappings.isLoading}
        columnFilters rowClassName={(r) => (r.mapping ? undefined : "text-ink-muted")}
        footer={rows.length > 0 && <span>{current.length} {current.length === 1 ? "mapping" : "mappings"}{unmappedCount ? ` · ${unmappedCount} ${unmappedCount === 1 ? "group" : "groups"} not mapped` : ""} · a person in several groups gets the highest level</span>}
        empty={{ title: "No mappings yet", body: "Add a mapping below so people in a group can sign in." }} />
      <AddMapping
        roles={roles.data?.items ?? []}
        groups={allGroups}
        group={prefill}
        onAdd={(d) => saveSet([...current.map(toDraft), d])}
      />
      <RegisterGroup onDone={refresh} />
      <ConfirmDialog request={confirm} onClose={() => setConfirm(null)} />
    </div>
  );
}

/** A mapping, or a registered group no mapping uses. */
interface Row { kind: "mapping" | "unmapped"; id: string; group: string; groupId: string; members: number | null; mapping?: Mapping }

const mappingForm = z.object({
  group: z.string().min(1, "Choose a group.").max(200),
  role: MappingIn.shape.role,
  attrKey: z.string().regex(/^([a-z_][a-z0-9_]*)?$/, "Use lower-case letters, digits and underscores, for example cio."),
  attrValues: z.string(),
}).refine((v) => !v.attrKey || splitList(v.attrValues).length > 0, {
  path: ["attrValues"], message: "Enter at least one value for this attribute, or clear the attribute.",
});
type MappingForm = z.infer<typeof mappingForm>;

const splitList = (v: string) => v.split(",").map((x) => x.trim()).filter(Boolean);

function AddMapping({ roles, groups, group, onAdd }: { roles: Role[]; groups: IdpGroup[]; group: string; onAdd: (d: Draft) => Promise<unknown> }) {
  const [error, setError] = useState<ApiError | null>(null);
  const form = useForm<MappingForm>({
    resolver: zodResolver(mappingForm),
    mode: "onTouched",
    defaultValues: { group: "", role: "", attrKey: "", attrValues: "" },
  });
  // "Map" on an unmapped group fills it in here.
  useEffect(() => { if (group) form.setValue("group", group, { shouldValidate: true }); }, [group]); // eslint-disable-line react-hooks/exhaustive-deps
  const submit = async (v: MappingForm) => {
    setError(null);
    const values = splitList(v.attrValues);
    try {
      await onAdd({ idp_group_id: v.group, role: v.role, scope_attributes: v.attrKey && values.length ? { [v.attrKey]: values } : {} });
      form.reset({ ...v, attrKey: "", attrValues: "" });
    } catch (e) {
      setError(applyServerError(form, e, { codes: { unknown_group: "group" } }));
    }
  };
  const c = form.control;
  return (
    <Form {...form}>
      <form className="tl mt-4 grid gap-4 rounded-lg border border-line bg-surface-raised p-5" onSubmit={form.handleSubmit(submit)}
        aria-labelledby="add-mapping-title" noValidate>
        <h2 id="add-mapping-title" className="text-heading">Add a mapping</h2>
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <Field control={c} name="group" label="Group" render={(f) => (
            <NativeSelect {...f}>
              <option value="">Choose a group…</option>
              {groups.map((g) => <option key={g.id} value={g.external_group_id}>{g.display_name}</option>)}
            </NativeSelect>
          )} />
          <Field control={c} name="role" label="Role" render={(f) => (
            <NativeSelect {...f}>
              <option value="">Choose a role…</option>
              {roles.map((r) => (
                <option key={r.id} value={r.key}>{r.name} ({r.kind}{r.is_protected ? ", needs approval" : ""})</option>
              ))}
            </NativeSelect>
          )} />
          <Field control={c} name="attrKey" label="Scope attribute (optional)" render={(f) => <Input {...f} placeholder="e.g. cio" />} />
          <Field control={c} name="attrValues" label="Values" description="Comma separated." render={(f) => <Input {...f} placeholder="e.g. Nomsa Osei" />} />
        </div>
        <FormAlert error={error} />
        <div><Button type="submit" disabled={form.formState.isSubmitting}>Add and save</Button></div>
      </form>
    </Form>
  );
}

function RegisterGroup({ onDone }: { onDone: () => void }) {
  const [error, setError] = useState<ApiError | null>(null);
  const form = useForm<z.infer<typeof GroupIn>>({
    resolver: zodResolver(GroupIn),
    mode: "onTouched",
    defaultValues: { external_group_id: "", display_name: "" },
  });
  const submit = async (v: z.infer<typeof GroupIn>) => {
    setError(null);
    try {
      await api("/api/v1/admin/groups", { method: "POST", body: v });
      form.reset();
      onDone();
    } catch (e) {
      setError(applyServerError(form, e));
    }
  };
  const c = form.control;
  return (
    <Form {...form}>
      <form className="tl mt-4 grid gap-4 rounded-lg border border-line bg-surface-raised p-5" onSubmit={form.handleSubmit(submit)}
        aria-labelledby="register-group-title" noValidate>
        <div className="grid gap-1">
          <h2 id="register-group-title" className="text-heading">Register a Microsoft group</h2>
          <p className="text-small text-ink-muted">Until provisioning (SCIM) is enabled, groups appear after a member signs in, or you can add them here by object id.</p>
        </div>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field control={c} name="external_group_id" label="Group object id" render={(f) => <Input {...f} maxLength={200} />} />
          <Field control={c} name="display_name" label="Display name" render={(f) => <Input {...f} maxLength={200} />} />
        </div>
        <FormAlert error={error} />
        <div><Button type="submit" disabled={form.formState.isSubmitting}>Register</Button></div>
      </form>
    </Form>
  );
}
