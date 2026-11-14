import { zodResolver } from "@hookform/resolvers/zod";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus, Trash2, WandSparkles } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { useFieldArray, useForm, useWatch, type UseFormReturn } from "react-hook-form";
import { Link, useLocation, useNavigate, useParams } from "react-router-dom";
import { toast } from "sonner";
import { z } from "zod";

import { api, type ApiError } from "../../api/client";
import type { Home } from "../../api/app";
import type { BandEdit, BandRow, Catalog, FieldBody, FieldDraft, FieldKind, FieldPreview, PersonalField } from "../../api/govern";
import { AGGS, FieldIn } from "../../api/schemas";
import { ContentBadge } from "../../components/ContentBadge";
import { formatAsOf, formatValue } from "../../components/format";
import { Alert, FormAlert } from "../../components/ui/alert";
import { Button } from "../../components/ui/button";
import { Card } from "../../components/ui/card";
import { Checkbox } from "../../components/ui/checkbox";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "../../components/ui/dialog";
import { Field, Form, FormControl, FormField, FormItem, FormLabel, FormMessage } from "../../components/ui/form";
import { Input } from "../../components/ui/input";
import { NativeSelect } from "../../components/ui/native-select";
import { RadioGroup, RadioGroupItem } from "../../components/ui/radio-group";
import { Skeleton } from "../../components/ui/skeleton";
import { applyServerError, asApiError } from "../../lib/api-errors";
import { cn } from "../../lib/utils";
import { RequestCertification } from "./RequestCertification";

const KINDS: { value: FieldKind; title: string; help: string }[] = [
  { value: "grouping", title: "Group values", help: "For example platforms into Infrastructure and Apps" },
  { value: "band", title: "Bands", help: "Number ranges with names" },
  { value: "join_measure", title: "Cross-table measure", help: "For example projects per request, along approved links" },
];
const AGG_LABEL: Record<(typeof AGGS)[number], string> = {
  count: "Count of rows", count_distinct: "Count of distinct values", sum: "Sum", avg: "Average", min: "Minimum", max: "Maximum",
};
const NUMERIC = ["integer", "decimal", "number", "numeric", "float", "currency"];
const num = (v: string) => (v.trim() === "" ? NaN : Number(v));

const builderForm = z.object({
  kind: FieldIn.shape.kind,
  name: FieldIn.shape.name,
  connection_id: z.string().min(1, "Choose a data source."),
  source_ref: z.string(),
  groups: z.array(z.object({ label: z.string().max(80, "Keep a group name to 80 characters or fewer."), values: z.array(z.string()) })),
  catch_all_label: z.string().min(1, "Name the group for everything else.").max(80),
  not_set_label: z.string().min(1, "Name the group for blank values.").max(80),
  case_sensitive: z.boolean(),
  from: z.string(),
  bands: z.array(z.object({ to: z.string(), label: z.string().max(80) })),
  open_ended_top: z.boolean(),
  entity: z.string(),
  linked_to: z.string(),
  agg: z.enum(AGGS),
  target: z.string(),
}).superRefine((v, ctx) => {
  const issue = (path: (string | number)[], message: string) => ctx.addIssue({ code: "custom", path, message });
  if (v.kind !== "join_measure" && !v.source_ref) issue(["source_ref"], "Choose the field to build on.");
  if (v.kind === "grouping") {
    if (v.groups.length === 0) issue(["groups"], "Add at least one group.");
    v.groups.forEach((g, i) => {
      if (!g.label.trim()) issue(["groups", i, "label"], "Name this group.");
      if (g.values.length === 0) issue(["groups", i, "values"], "Pick at least one value for this group.");
    });
  }
  if (v.kind === "band") {
    const edges = bandEdges(v);
    if (edges.some(Number.isNaN)) issue(["bands"], "Every edge needs a number.");
    for (let i = 1; i < edges.length; i++) {
      if (edges[i]! <= edges[i - 1]!) issue(["bands"], `Band edges must go up: ${formatValue(edges[i])} must be above ${formatValue(edges[i - 1])}.`);
    }
    if (edges.length < 2) issue(["bands"], "Give at least two edges.");
  }
  if (v.kind === "join_measure") {
    if (!v.entity) issue(["entity"], "Choose what the measure counts.");
    if (v.agg !== "count" && !v.target) issue(["target"], `${AGG_LABEL[v.agg]} needs a field.`);
  }
});
type BuilderForm = z.infer<typeof builderForm>;

/** Edges for the API: the first band's lower edge, then each band's upper edge (the last one only when closed). */
function bandEdges(v: Pick<BuilderForm, "from" | "bands" | "open_ended_top">): number[] {
  const tops = v.open_ended_top ? v.bands.slice(0, -1) : v.bands;
  return [num(v.from), ...tops.map((b) => num(b.to))];
}

function toBody(v: BuilderForm): FieldBody {
  const base = { kind: v.kind, name: v.name.trim(), connection_id: v.connection_id };
  if (v.kind === "grouping") {
    return { ...base, source_ref: v.source_ref, groups: v.groups.map((g) => ({ label: g.label.trim(), values: g.values })),
      catch_all_label: v.catch_all_label.trim(), not_set_label: v.not_set_label.trim(), case_sensitive: v.case_sensitive };
  }
  if (v.kind === "band") {
    const labels = v.bands.map((b) => b.label.trim());
    return { ...base, source_ref: v.source_ref, edges: bandEdges(v), labels: labels.every(Boolean) ? labels : [], open_ended_top: v.open_ended_top };
  }
  return { ...base, path: v.linked_to ? [v.entity, v.linked_to] : [v.entity], agg: v.agg, target: v.target ? `${v.entity}.${v.target}` : null };
}

function fromField(f: PersonalField): Partial<BuilderForm> {
  const d = f.definition as Record<string, unknown>;
  if (f.kind === "grouping") {
    return { source_ref: String(d.source), groups: (d.groups as { label: string; values: unknown[] }[]).map((g) => ({ label: g.label, values: g.values.map(String) })),
      catch_all_label: String(d.catch_all ?? "Everything else"), not_set_label: String(d.not_set ?? "Not set"), case_sensitive: Boolean(d.case_sensitive) };
  }
  if (f.kind === "band") {
    const bands = d.bands as { label: string; lower: number; upper: number | null }[];
    const open = d.closing_max === null || d.closing_max === undefined;
    return { source_ref: String(d.source), from: String(bands[0]?.lower ?? 0), open_ended_top: open,
      bands: bands.map((b) => ({ to: b.upper === null ? "" : String(b.upper), label: b.label })) };
  }
  return { entity: String(d.entity), linked_to: d.linked_to ? String(d.linked_to) : "", agg: d.agg as BuilderForm["agg"], target: d.field ? String(d.field) : "" };
}

/** Builder values from a draft in words (Ask's "Open in the field builder" or an answer's New field menu; US-075). */
function fromDraft({ draft: d }: FieldDraft): Partial<BuilderForm> {
  const base = { kind: d.kind, connection_id: d.connection_id, ...(d.name ? { name: d.name } : {}), ...(d.source_ref ? { source_ref: d.source_ref } : {}) };
  if (d.kind === "grouping") return { ...base, ...(d.groups?.length ? { groups: d.groups.map((g) => ({ label: g.label, values: g.values.map(String) })) } : {}) };
  if (d.kind === "band" && d.edges && d.edges.length >= 2) {
    const edges = d.edges;
    const open = d.open_ended_top ?? true;
    const n = open ? edges.length : edges.length - 1;
    return { ...base, from: String(edges[0]), open_ended_top: open,
      bands: Array.from({ length: n }, (_, i) => ({ to: i + 1 < edges.length ? String(edges[i + 1]) : "", label: d.labels?.[i] ?? "" })) };
  }
  return base;
}

const DEFAULTS: BuilderForm = {
  kind: "grouping", name: "", connection_id: "", source_ref: "", groups: [{ label: "", values: [] }], catch_all_label: "Everything else",
  not_set_label: "Not set", case_sensitive: false, from: "0", bands: [{ to: "10", label: "" }, { to: "", label: "" }], open_ended_top: true,
  entity: "", linked_to: "", agg: "count_distinct", target: "",
};

/**
 * Field builder (FEAT-028 – 030; SCR-05; US-078): one screen for groupings, bands and cross-table measures, with a live
 * preview of counts per bucket in the Creator's scope and the rule in words, then Save and Request certification.
 */
export function FieldBuilderPage() {
  const { id } = useParams();
  const existing = useQuery({ queryKey: ["field", id], queryFn: () => api<PersonalField>(`/api/v1/fields/${id}`), enabled: !!id });
  if (id && existing.isLoading) return <div className="tl grid gap-4" aria-busy="true"><Skeleton className="h-10 w-1/3" /><Skeleton className="h-96" /></div>;
  if (existing.error) return <div className="tl"><FormAlert error={asApiError(existing.error)} /></div>;
  return <Builder key={id ?? "new"} field={existing.data ?? null} />;
}

function Builder({ field }: { field: PersonalField | null }) {
  const location = useLocation();
  const [draft] = useState(() => (field ? null : (location.state as { draft?: FieldDraft } | null)?.draft ?? null));
  const navigate = useNavigate();
  const qc = useQueryClient();
  const home = useQuery({ queryKey: ["home"], queryFn: () => api<Home>("/api/v1/home") });
  const sources = useMemo(() => home.data?.greeting.scopes ?? [], [home.data]);
  const form = useForm<BuilderForm>({
    resolver: zodResolver(builderForm),
    defaultValues: field ? { ...DEFAULTS, kind: field.kind, name: field.name, connection_id: field.connection_id, ...fromField(field) }
      : draft ? { ...DEFAULTS, ...fromDraft(draft) } : DEFAULTS,
    mode: "onTouched",
  });
  useEffect(() => {
    if (!form.getValues("connection_id") && sources[0]) form.setValue("connection_id", sources[0].connection_id);
  }, [sources, form]);
  const connectionId = useWatch({ control: form.control, name: "connection_id" });
  const kind = useWatch({ control: form.control, name: "kind" });
  const catalog = useQuery({
    queryKey: ["catalog", connectionId],
    queryFn: () => api<Catalog>(`/api/v1/query/catalog?connection_id=${connectionId}`),
    enabled: !!connectionId,
  });
  const [error, setError] = useState<ApiError | null>(null);
  const [saved, setSaved] = useState<PersonalField | null>(field);
  // A first save moves to /fields/:id (a fresh builder); "and request certification" travels in the navigation state.
  const [requesting, setRequesting] = useState(() => (location.state as { request?: boolean } | null)?.request === true);

  const save = async (v: BuilderForm, thenRequest: boolean) => {
    setError(null);
    try {
      const out = await api<PersonalField>(saved ? `/api/v1/fields/${saved.id}` : "/api/v1/fields", {
        method: saved ? "PATCH" : "POST", body: toBody(v) satisfies z.input<typeof FieldIn>,
      });
      setSaved(out);
      void qc.invalidateQueries({ queryKey: ["fields"] });
      toast.success(`${out.name} is saved`, { description: "Use it in Ask, reports and custom SQL. Only you can see it until it is certified." });
      if (!saved) navigate(`/fields/${out.id}`, { replace: true, state: { request: thenRequest } });
      else if (thenRequest) setRequesting(true);
    } catch (e) {
      setError(applyServerError(form, e, { fields: { source_ref: "source_ref", name: "name" } }));
    }
  };
  const title = useWatch({ control: form.control, name: "name" });

  return (
    <div className="tl grid gap-4">
      <Form {...form}>
        <form noValidate onSubmit={form.handleSubmit((v) => save(v, false))} className="grid gap-4">
          <div className="flex flex-wrap items-end justify-between gap-3">
            <div className="grid gap-1">
              <div className="flex flex-wrap items-center gap-2">
                <h1 className="text-title">{title.trim() || (saved ? saved.name : "New field")}</h1>
                {saved && <ContentBadge badge={saved.badge} />}
              </div>
              <p className="text-ink-muted">A personal field: only you can use it until it is certified.</p>
            </div>
            <div className="flex flex-wrap gap-2">
              <Button asChild variant="ghost"><Link to="/fields">Cancel</Link></Button>
              <Button type="submit" variant="secondary" disabled={form.formState.isSubmitting}>Save</Button>
              {saved?.badge !== "certified" && (
                <Button type="button" disabled={form.formState.isSubmitting} onClick={form.handleSubmit((v) => save(v, true))}>Save and request certification</Button>
              )}
            </div>
          </div>
          {draft && !saved && (
            <Alert>
              <p className="text-body-strong">Drafted from your words. Check it, then save.</p>
              {[...(draft.understood ?? []), ...(draft.missing ?? [])].map((t) => <p key={t}>{t}</p>)}
            </Alert>
          )}
          {saved?.broken && <Alert variant="danger"><p className="text-body-strong">This field is broken.</p><p>{saved.broken_reason}</p></Alert>}
          <FormAlert error={error} />
          <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_22rem]">
            <Card className="gap-5 p-5">
              <FormField control={form.control} name="kind" render={({ field: f }) => (
                <FormItem>
                  <span className="text-body-strong">Kind of field</span>
                  <FormControl>
                    <RadioGroup aria-label="Kind of field" value={f.value} onValueChange={f.onChange} disabled={!!saved} className="gap-2 sm:grid-cols-3">
                      {KINDS.map((k) => (
                        <label key={k.value} htmlFor={`kind-${k.value}`}
                          className={cn("flex cursor-pointer items-start gap-2 rounded-md border p-3", f.value === k.value ? "border-accent bg-accent-soft" : "border-line")}>
                          <RadioGroupItem id={`kind-${k.value}`} value={k.value} className="mt-0.5" aria-describedby={`kind-${k.value}-help`} />
                          <span className="grid gap-0.5"><span className="text-body-strong">{k.title}</span><span id={`kind-${k.value}-help`} className="text-small text-ink-muted">{k.help}</span></span>
                        </label>
                      ))}
                    </RadioGroup>
                  </FormControl>
                  {saved && <p className="text-small text-ink-muted">The kind can't change after saving; build a new field instead.</p>}
                </FormItem>
              )} />
              <div className="grid gap-4 sm:grid-cols-2">
                <Field control={form.control} name="name" label="Field name" render={(f) => <Input {...f} maxLength={120} placeholder={kind === "band" ? "Delay band" : kind === "grouping" ? "Platform family" : "Projects per request"} />} />
                <Field control={form.control} name="connection_id" label="Data source" render={(f) => (
                  <NativeSelect {...f} disabled={!!saved}>
                    {sources.length === 0 && <option value="">No data source in your scope</option>}
                    {sources.map((s) => <option key={s.connection_id} value={s.connection_id}>{s.connection_name}</option>)}
                  </NativeSelect>
                )} />
              </div>
              {catalog.isLoading && <Skeleton className="h-32" />}
              {catalog.error && <FormAlert error={asApiError(catalog.error)} />}
              {catalog.data && kind === "grouping" && <GroupingEditor form={form} catalog={catalog.data} />}
              {catalog.data && kind === "band" && <BandEditor form={form} catalog={catalog.data} />}
              {saved && saved.kind === "band" && saved.badge !== "certified" && (
                <BandWords field={saved} onApplied={(out) => { setSaved(out); form.reset({ ...form.getValues(), ...fromField(out) }); void qc.invalidateQueries({ queryKey: ["fields"] }); }} />
              )}
              {catalog.data && kind === "join_measure" && <MeasureEditor form={form} catalog={catalog.data} />}
            </Card>
            <aside className="grid content-start gap-4">
              <Preview form={form} />
              <Card className="gap-2 p-4">
                <h2 className="text-heading">Before you request certification</h2>
                <p className="text-small text-ink-muted">
                  An Admin reviews the rule, checks it doesn't duplicate a certified field, and may merge it into one.
                  A field can be requested once it is shared or used on a dashboard; until it is certified, dashboards
                  that use it can't be published to a role.
                </p>
              </Card>
            </aside>
          </div>
        </form>
      </Form>
      <RequestCertification item={saved && { kind: saved.kind, id: saved.id }} name={saved?.name ?? ""} open={requesting} onOpenChange={setRequesting} />
    </div>
  );
}

type EditorProps = { form: UseFormReturn<BuilderForm>; catalog: Catalog };

function SourceSelect({ form, options, help }: { form: UseFormReturn<BuilderForm>; options: { ref: string; label: string }[]; help: string }) {
  return (
    <Field control={form.control} name="source_ref" label="Based on" description={help} render={(f) => (
      <NativeSelect {...f}>
        <option value="">Choose…</option>
        {options.map((o) => <option key={o.ref} value={o.ref}>{o.label}</option>)}
      </NativeSelect>
    )} />
  );
}

function GroupingEditor({ form, catalog }: EditorProps) {
  const groups = useFieldArray({ control: form.control, name: "groups" });
  const source = useWatch({ control: form.control, name: "source_ref" });
  const all = useWatch({ control: form.control, name: "groups" });
  const options = catalog.dimensions.filter((d) => !d.temporal).map((d) => ({ ref: d.ref, label: d.label }));
  const values = useQuery({
    queryKey: ["filter-values", catalog.connection_id, catalog.model_version_id, source],
    queryFn: () => api<{ values: unknown[] }>("/api/v1/query/filter-values", { method: "POST", body: { connection_id: catalog.connection_id, model_version_id: catalog.model_version_id, ref: source } }),
    enabled: !!source,
  });
  const taken = (except: number) => new Set(all.flatMap((g, i) => (i === except ? [] : g.values)));
  const list = useMemo(() => (values.data?.values ?? []).map(String), [values.data]);
  // Values typed in words ("network one") become the stored spelling; unknown ones are dropped and named once.
  const [unknown, setUnknown] = useState<string[]>([]);
  const matched = useRef<string | null>(null);
  useEffect(() => {
    if (!values.data || matched.current === source) return;
    matched.current = source;
    const canon = new Map(list.map((v) => [v.trim().toLowerCase(), v]));
    const missing: string[] = [];
    form.getValues("groups").forEach((g, i) => {
      const next = g.values.map((v) => canon.get(v.trim().toLowerCase()) ?? (missing.push(v), null)).filter((v): v is string => v !== null);
      if (next.join("\u0000") !== g.values.join("\u0000")) form.setValue(`groups.${i}.values`, next);
    });
    setUnknown(missing);
  }, [values.data, list, source, form]);
  const unassigned = list.filter((v) => !all.some((g) => g.values.includes(v)));
  return (
    <div className="grid gap-4">
      <SourceSelect form={form} options={options} help="The values of this field are split into your groups." />
      {source && (
        <div className="grid gap-3">
          <div className="flex items-baseline justify-between gap-2">
            <h2 className="text-heading">Groups</h2>
            {values.data && <span className="text-small text-ink-muted">{unassigned.length} of {list.length} values go to “{form.getValues("catch_all_label")}”</span>}
          </div>
          {values.isLoading && <Skeleton className="h-24" />}
          {unknown.length > 0 && <Alert variant="warn"><p>Not in your data, so left out: {unknown.join(", ")}.</p></Alert>}
          {groups.fields.map((g, i) => (
            <fieldset key={g.id} className="grid gap-2 rounded-md border border-line p-3">
              <legend className="sr-only">Group {i + 1}</legend>
              <div className="flex items-end gap-2">
                <Field control={form.control} name={`groups.${i}.label`} label={`Group ${i + 1} name`} className="flex-1" render={(f) => <Input {...f} maxLength={80} />} />
                <Button type="button" variant="ghost" size="icon" aria-label={`Remove group ${i + 1}`} onClick={() => groups.remove(i)} disabled={groups.fields.length === 1}><Trash2 aria-hidden /></Button>
              </div>
              <FormField control={form.control} name={`groups.${i}.values`} render={({ field: f }) => (
                <FormItem>
                  <span className="text-small text-ink-muted" id={`group-${i}-values`}>Values in this group</span>
                  <ul className="flex max-h-40 flex-wrap gap-1.5 overflow-y-auto" aria-labelledby={`group-${i}-values`}>
                    {list.map((v) => {
                      const used = taken(i).has(v);
                      const on = f.value.includes(v);
                      return (
                        <li key={v}>
                          <label className={cn("inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-small",
                            on ? "border-accent bg-accent-soft" : "border-line", used && "opacity-50")} title={used ? "Already in another group" : undefined}>
                            <Checkbox checked={on} disabled={used} onCheckedChange={(c) => f.onChange(c === true ? [...f.value, v] : f.value.filter((x) => x !== v))} />
                            {v}
                          </label>
                        </li>
                      );
                    })}
                  </ul>
                  <FormMessage />
                </FormItem>
              )} />
            </fieldset>
          ))}
          <Button type="button" variant="secondary" size="sm" className="justify-self-start" onClick={() => groups.append({ label: "", values: [] })} disabled={groups.fields.length >= 50}>
            <Plus aria-hidden />Add group
          </Button>
          <div className="grid gap-4 sm:grid-cols-2">
            <Field control={form.control} name="catch_all_label" label="Everything else goes to" render={(f) => <Input {...f} maxLength={80} />} />
            <Field control={form.control} name="not_set_label" label="Blank values go to" render={(f) => <Input {...f} maxLength={80} />} />
          </div>
          <FormField control={form.control} name="case_sensitive" render={({ field: f }) => (
            <FormItem>
              <div className="flex items-center gap-2">
                <FormControl><Checkbox checked={f.value} onCheckedChange={(c) => f.onChange(c === true)} /></FormControl>
                <FormLabel className="font-normal">Match case exactly (otherwise case and extra spaces are ignored)</FormLabel>
              </div>
            </FormItem>
          )} />
        </div>
      )}
    </div>
  );
}

function BandEditor({ form, catalog }: EditorProps) {
  const bands = useFieldArray({ control: form.control, name: "bands" });
  const [from, open] = useWatch({ control: form.control, name: ["from", "open_ended_top"] });
  const rows = useWatch({ control: form.control, name: "bands" });
  const options = catalog.entities.flatMap((e) => e.fields.filter((f) => NUMERIC.includes(f.data_type)).map((f) => ({ ref: f.ref, label: `${e.label} · ${f.label}` })));
  const bandError = form.formState.errors.bands?.root?.message ?? form.formState.errors.bands?.message;
  return (
    <div className="grid gap-4">
      <SourceSelect form={form} options={options} help="Bands need a number field. Each value falls in exactly one band." />
      <div className="grid gap-2">
        <h2 className="text-heading">Bands</h2>
        <p className="text-small text-ink-muted">Each band includes its lower edge and stops before its upper edge, so there are no gaps or overlaps.</p>
        <div className="overflow-x-auto">
          <table className="w-full text-body">
            <thead className="text-left text-small text-ink-muted">
              <tr><th className="py-1 pr-2 font-medium">From</th><th className="py-1 pr-2 font-medium">Up to</th><th className="py-1 pr-2 font-medium">Name (optional)</th><th><span className="sr-only">Actions</span></th></tr>
            </thead>
            <tbody>
              {bands.fields.map((b, i) => {
                const last = i === bands.fields.length - 1;
                const lower = i === 0 ? from : rows[i - 1]?.to;
                return (
                  <tr key={b.id} className="align-top">
                    <td className="py-1 pr-2">
                      {i === 0 ? <Input {...form.register("from")} inputMode="decimal" aria-label="Band 1 from" className="w-28" />
                        : <span className="inline-flex h-control-md items-center font-mono tabular-nums text-ink-muted">{lower || "—"}</span>}
                    </td>
                    <td className="py-1 pr-2">
                      {last && open ? <span className="inline-flex h-control-md items-center text-ink-muted">and above</span>
                        : <Input {...form.register(`bands.${i}.to`)} inputMode="decimal" aria-label={`Band ${i + 1} up to`} className="w-28" />}
                    </td>
                    <td className="py-1 pr-2"><Input {...form.register(`bands.${i}.label`)} maxLength={80} aria-label={`Band ${i + 1} name`} placeholder={autoLabel(lower, last && open ? "" : rows[i]?.to)} /></td>
                    <td className="py-1">
                      <Button type="button" variant="ghost" size="icon" aria-label={`Remove band ${i + 1}`} onClick={() => bands.remove(i)} disabled={bands.fields.length <= 1}><Trash2 aria-hidden /></Button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        {bandError && <p role="alert" className="text-small text-status-danger">{bandError}</p>}
        <div className="flex flex-wrap items-center gap-4">
          <Button type="button" variant="secondary" size="sm" onClick={() => bands.insert(Math.max(0, bands.fields.length - (open ? 1 : 0)), { to: "", label: "" })} disabled={bands.fields.length >= 50}>
            <Plus aria-hidden />Add band
          </Button>
          <FormField control={form.control} name="open_ended_top" render={({ field: f }) => (
            <FormItem>
              <div className="flex items-center gap-2">
                <FormControl><Checkbox checked={f.value} onCheckedChange={(c) => f.onChange(c === true)} /></FormControl>
                <FormLabel className="font-normal">Last band has no upper edge</FormLabel>
              </div>
            </FormItem>
          )} />
        </div>
      </div>
    </div>
  );
}

/** Edit a saved band in words (FEAT-029 A2; US-076): the change is shown as a diff and applied only on confirm. */
function BandWords({ field, onApplied }: { field: PersonalField; onApplied: (f: PersonalField) => void }) {
  const [text, setText] = useState("");
  const [edit, setEdit] = useState<BandEdit | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [busy, setBusy] = useState(false);
  const propose = async () => {
    if (!text.trim()) return;
    setError(null);
    setBusy(true);
    try {
      setEdit(await api<BandEdit>(`/api/v1/fields/${field.id}/edit-in-words`, { method: "POST", body: { text: text.trim() } }));
    } catch (e) {
      setError(asApiError(e));
    } finally {
      setBusy(false);
    }
  };
  const apply = async () => {
    if (!edit) return;
    setBusy(true);
    try {
      const out = await api<PersonalField>(`/api/v1/fields/${field.id}`, { method: "PATCH", body: edit.proposal });
      toast.success("Bands changed", { description: edit.changes.join(" ") });
      setEdit(null);
      setText("");
      onApplied(out);
    } catch (e) {
      setError(asApiError(e));
      setEdit(null);
    } finally {
      setBusy(false);
    }
  };
  return (
    <section className="grid gap-2 border-t border-line pt-4" aria-labelledby="band-words">
      <h2 id="band-words" className="text-heading">Edit in words</h2>
      <div className="flex gap-2">
        <label htmlFor="band-words-input" className="sr-only">Change the bands in words</label>
        <Input id="band-words-input" value={text} maxLength={500} onChange={(e) => setText(e.target.value)} placeholder="Make the top band 5M and above"
          onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); void propose(); } }} />
        <Button type="button" variant="secondary" onClick={() => void propose()} disabled={busy || !text.trim()}><WandSparkles aria-hidden />Show the change</Button>
      </div>
      <p className="text-small text-ink-muted">For example “add an edge at 2M”, “remove the edge at 1M”, “cap at 10M” or “rename 1M - 5M to Medium”.</p>
      <FormAlert error={error} />
      <Dialog open={edit !== null} onOpenChange={(o) => { if (!o) setEdit(null); }}>
        <DialogContent className="sm:max-w-2xl">
          <DialogHeader>
            <DialogTitle>Change the bands of {field.name}?</DialogTitle>
            <DialogDescription>{edit?.changes.join(" ")}</DialogDescription>
          </DialogHeader>
          {edit && (
            <div className="grid gap-4 sm:grid-cols-2">
              <BandList title="Now" rows={edit.diff.before} />
              <BandList title="After" rows={edit.diff.after} />
              <p className="text-small text-ink-muted sm:col-span-2">{edit.rule_after}</p>
            </div>
          )}
          <DialogFooter>
            <Button variant="secondary" onClick={() => setEdit(null)}>Cancel</Button>
            <Button onClick={() => void apply()} disabled={busy}>Apply</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </section>
  );
}

const ROW_STATUS: Record<BandRow["status"], string | null> = { same: null, renamed: "Renamed", new: "New", removed: "Removed" };

function BandList({ title, rows }: { title: string; rows: BandRow[] }) {
  return (
    <div className="grid content-start gap-1.5">
      <h3 className="text-small font-medium text-ink-muted">{title}</h3>
      <ul className="grid gap-1" aria-label={`Bands ${title.toLowerCase()}`}>
        {rows.map((r) => (
          <li key={`${r.lower}-${r.upper}-${r.label}`} className={cn("flex items-baseline justify-between gap-2 rounded-sm px-2 py-1 text-body",
            r.status === "removed" && "bg-status-danger-soft line-through", (r.status === "new" || r.status === "renamed") && "bg-accent-soft")}>
            <span>{r.label} <span className="text-small text-ink-muted">{formatValue(r.lower)} {r.upper === null ? "and above" : `up to ${formatValue(r.upper)}`}</span></span>
            {ROW_STATUS[r.status] && <span className="text-small font-medium">{ROW_STATUS[r.status]}</span>}
          </li>
        ))}
      </ul>
    </div>
  );
}

const autoLabel = (lower?: string, upper?: string) => (lower && upper ? `${lower} - ${upper}` : lower ? `${lower}+` : "");

function MeasureEditor({ form, catalog }: EditorProps) {
  const [entity, agg] = useWatch({ control: form.control, name: ["entity", "agg"] });
  const linked = useMemo(() => catalog.links.filter((l) => l.includes(entity)).map((l) => (l[0] === entity ? l[1] : l[0])), [catalog.links, entity]);
  const fields = catalog.entities.find((e) => e.key === entity)?.fields ?? [];
  const label = (key: string) => catalog.entities.find((e) => e.key === key)?.label ?? key;
  return (
    <div className="grid gap-4 sm:grid-cols-2">
      <Field control={form.control} name="entity" label="Count" render={(f) => (
        <NativeSelect {...f} onChange={(e) => { f.onChange(e); form.setValue("linked_to", ""); form.setValue("target", ""); }}>
          <option value="">Choose the data…</option>
          {catalog.entities.map((e) => <option key={e.key} value={e.key}>{e.label}</option>)}
        </NativeSelect>
      )} />
      <Field control={form.control} name="agg" label="How" render={(f) => (
        <NativeSelect {...f}>{AGGS.map((a) => <option key={a} value={a}>{AGG_LABEL[a]}</option>)}</NativeSelect>
      )} />
      {agg !== "count" && (
        <Field control={form.control} name="target" label="Of field" render={(f) => (
          <NativeSelect {...f}>
            <option value="">Choose a field…</option>
            {fields.filter((x) => agg === "count_distinct" || NUMERIC.includes(x.data_type)).map((x) => <option key={x.key} value={x.key}>{x.label}</option>)}
          </NativeSelect>
        )} />
      )}
      <Field control={form.control} name="linked_to" label="Only rows linked to" description={entity && linked.length === 0 ? "No approved link from this data; the measure counts all of it." : "Follows approved links only, so nothing is double counted."}
        render={(f) => (
          <NativeSelect {...f} disabled={!entity || linked.length === 0}>
            <option value="">All rows</option>
            {linked.map((l) => <option key={l} value={l}>{label(l)}</option>)}
          </NativeSelect>
        )} />
    </div>
  );
}

/** Live preview (API-031): runs the unsaved definition in the Creator's scope once the form is valid. */
function Preview({ form }: { form: UseFormReturn<BuilderForm> }) {
  const values = useWatch({ control: form.control }) as BuilderForm;
  const [body, setBody] = useState<FieldBody | null>(null);
  useEffect(() => {
    const t = setTimeout(() => {
      const parsed = builderForm.safeParse({ ...values, name: values.name?.trim() || "Preview" });
      setBody(parsed.success ? toBody(parsed.data) : null);
    }, 400);
    return () => clearTimeout(t);
  }, [values]);
  const preview = useQuery({
    queryKey: ["field-preview", body],
    queryFn: () => api<FieldPreview>("/api/v1/fields/preview", { method: "POST", body: { field: body } }),
    enabled: body !== null,
    retry: false,
  });
  const p = preview.data;
  const max = Math.max(1, ...(p?.buckets ?? []).map((b) => b.count));
  const total = (p?.buckets ?? []).reduce((n, b) => n + b.count, 0);
  return (
    <Card className="gap-3 p-4" role="region" aria-label="Preview" aria-live="polite" aria-busy={preview.isFetching}>
      <div className="grid gap-0.5">
        <h2 className="text-heading">Preview</h2>
        <span className="text-small text-ink-muted">{p?.measure ? `${p.measure} in your scope` : "In your data scope"}</span>
      </div>
      {!body ? <p className="text-small text-ink-muted">Fill in the field to see how your data falls into it.</p>
        : preview.isLoading ? <Skeleton className="h-32" />
          : preview.error ? <FormAlert error={asApiError(preview.error)} />
            : p?.hidden ? <p className="text-ink-muted">{p.message}</p>
              : p && (
                <>
                  {p.buckets && (
                    <ul className="grid gap-1.5" aria-label="Counts per bucket">
                      {p.buckets.map((b) => (
                        <li key={b.label} className="grid gap-0.5">
                          <span className="flex justify-between gap-2 text-body"><span className="truncate">{b.label}</span><span className="font-mono tabular-nums">{formatValue(b.count)}</span></span>
                          <span aria-hidden className="h-1.5 rounded-full bg-accent" style={{ width: `${(b.count / max) * 100}%` }} />
                        </li>
                      ))}
                    </ul>
                  )}
                  {p.buckets && <p className="text-small text-ink-muted">{formatValue(total)} in total · every row falls in exactly one bucket.</p>}
                  {p.value !== undefined && <p className="font-mono text-figure-xl tabular-nums">{formatValue(p.value)}</p>}
                  {p.semantics && <p className="text-small">{p.semantics}</p>}
                  <p className="text-small text-ink-muted">{p.edge_rules}</p>
                  <p className="text-small text-ink-muted">{p.scope_text ? `${p.scope_text} · ` : ""}Data as of {formatAsOf(p.as_of)}</p>
                </>
              )}
    </Card>
  );
}
