import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation, useQuery } from "@tanstack/react-query";
import { ArrowDown, ArrowUp, Eye, Plus, Trash2 } from "lucide-react";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";

import { api } from "../../../../api/client";
import {
  kpiMeasure, previewBlueprint, type Blueprint, type BlueprintKpi, type BlueprintWidget, type PackReports,
  type Preview, type Problem,
} from "../../../../api/packReports";
import { FormAlert } from "../../../../components/ui/alert";
import { Badge } from "../../../../components/ui/badge";
import { Button } from "../../../../components/ui/button";
import { Field, Form } from "../../../../components/ui/form";
import { Input } from "../../../../components/ui/input";
import { NativeSelect } from "../../../../components/ui/native-select";
import { Textarea } from "../../../../components/ui/textarea";
import { asApiError } from "../../../../lib/api-errors";
import { fromYaml, toYaml } from "../yaml";

/** The visuals a blueprint widget may use (Query Spec `Visual.type`, figures and tables aside). */
export const VISUALS = ["ranked_bar", "columns", "trend", "paired_bars", "stacked_status", "donut", "heat_grid", "meter",
  "dumbbell", "waterfall", "bullet", "pareto", "funnel", "small_multiples", "treemap", "waffle", "table"] as const;

const ID = /^[a-z][a-z0-9_]{1,60}$/;
const textForm = z.object({
  id: z.string().regex(ID, "Lower case letters, digits and _, starting with a letter."),
  title: z.string().trim().min(1, "Give it a title.").max(120),
  tab: z.string().max(40),
  dashboard: z.string().min(1, "Choose its dashboard."),
  question: z.string().trim().min(1, "Say what question it answers.").max(300),
  population: z.string().trim().min(1, "Say which records it counts.").max(300),
  triggers: z.string().trim().min(1, "Add at least one phrase people use for it."),
  footnote: z.string().trim().min(1, "A footnote says how to read the figures.").max(600),
  callout: z.string().max(1000),
  callout_level: z.enum(["info", "warning"]),
});
type TextForm = z.infer<typeof textForm>;

const split = (s: string) => s.split(",").map((x) => x.trim()).filter(Boolean);

/** A blueprint built from the form and the figure / visual / table lists, dropping empty optional parts. */
function assemble(t: TextForm, base: Blueprint, kpis: (string | BlueprintKpi)[], widgets: BlueprintWidget[],
  table: Blueprint["table"]): Blueprint {
  const out: Blueprint = {
    ...base, id: t.id, title: t.title.trim(), dashboard: t.dashboard, question: t.question.trim(),
    population: t.population.trim(), triggers: split(t.triggers), footnote: t.footnote.trim(), kpis, widgets,
  };
  if (t.tab.trim()) out.tab = t.tab.trim(); else delete out.tab;
  if (table) out.table = table; else delete out.table;
  if (t.callout.trim()) out.callout = { ...(base.callout ?? {}), text: t.callout.trim(), level: t.callout_level };
  else delete out.callout;
  return out;
}

function ProblemList({ problems }: { problems: Problem[] }) {
  if (problems.length === 0) return null;
  return (
    <ul aria-label="Problems" className="grid list-disc gap-1 pl-5 text-small text-status-danger">
      {problems.map((p, i) => <li key={i}>{p.path ? <code className="mr-1">{p.path}</code> : null}{p.message}</li>)}
    </ul>
  );
}

function PreviewPanel({ preview }: { preview: Preview }) {
  const value = (ref: string) => {
    const r = preview.results[ref];
    if (!r) return null;
    if (r.hidden) return r.message ?? "Hidden for your role";
    const first = r.rows[0];
    return first === undefined ? "No data" : r.rows.length === 1 && first.length === 1 ? String(first[0]) : `${r.row_count ?? r.rows.length} rows`;
  };
  return (
    <section aria-label="Live preview" className="grid gap-3 rounded-md border border-line p-3">
      <p className="text-small text-ink-muted">
        On {preview.connection?.name ?? "the connection"} (model v{preview.model_version}), as the report planner lays it out.
      </p>
      {preview.sections.map((s) => (
        <div key={s.key} className="grid gap-1">
          <h4 className="font-medium">{s.title}</h4>
          {(s.callouts ?? []).map((c, i) => <p key={i} className="text-small">{c.text}</p>)}
          <ul className="grid gap-1">
            {s.widgets.map((w) => {
              const box = preview.layout_preview.find((p) => p.widget_ref === w.ref);
              return (
                <li key={w.ref} className="flex flex-wrap items-baseline justify-between gap-2 text-small">
                  <span>{w.title} <Badge variant="outline">{w.visual.replace(/_/g, " ")}</Badge></span>
                  <span className="text-ink-muted">{value(w.ref)}{box ? ` · ${box.w}×${box.h} at ${box.x},${box.y}` : ""}</span>
                </li>
              );
            })}
          </ul>
          {s.footnote && <p className="text-small text-ink-muted">{s.footnote}</p>}
        </div>
      ))}
      {preview.gaps.length > 0 && (
        <div>
          <h4 className="font-medium">Not included on this connection</h4>
          <ul className="grid list-disc gap-1 pl-5 text-small">
            {preview.gaps.map((g, i) => <li key={i}>{g.request_part}: {g.reason}</li>)}
          </ul>
        </div>
      )}
    </section>
  );
}

/**
 * One report blueprint (US-234): the question, population, headline figures, visuals, detail table, callout and
 * footnote, edited in a form or as source, with a live preview on a chosen connection.
 */
export function BlueprintEditor({ data, blueprint, readOnly, saving, problems, onSave, onCancel }: {
  data: PackReports;
  blueprint: Blueprint;
  readOnly: boolean;
  saving: boolean;
  problems: Problem[];
  onSave: (b: Blueprint) => void;
  onCancel: () => void;
}) {
  const { measures, dimensions, fields } = data.names;
  const label = (k: string) => measures.find((m) => m.key === k)?.label ?? k;
  const [kpis, setKpis] = useState<(string | BlueprintKpi)[]>(blueprint.kpis ?? []);
  const [widgets, setWidgets] = useState<BlueprintWidget[]>(blueprint.widgets ?? []);
  const [table, setTable] = useState<Blueprint["table"]>(blueprint.table ?? null);
  const [source, setSource] = useState<string | null>(null);
  const [sourceError, setSourceError] = useState<string | null>(null);
  const [connectionId, setConnectionId] = useState("");
  const form = useForm<TextForm>({
    resolver: zodResolver(textForm), mode: "onTouched",
    defaultValues: {
      id: blueprint.id, title: blueprint.title, tab: blueprint.tab ?? "", dashboard: blueprint.dashboard,
      question: blueprint.question ?? "", population: blueprint.population ?? "", triggers: (blueprint.triggers ?? []).join(", "),
      footnote: blueprint.footnote ?? "", callout: blueprint.callout?.text ?? "", callout_level: blueprint.callout?.level ?? "info",
    },
  });
  const c = form.control;
  const connections = useQuery({ queryKey: ["connections"], queryFn: () => api<{ items: { id: string; name: string }[] }>("/api/v1/connections") });
  const preview = useMutation({ mutationFn: (b: Blueprint) => previewBlueprint(data.pack.id, b, connectionId) });
  const current = () => assemble(form.getValues(), blueprint, kpis, widgets, table);

  const openSource = () => { setSource(toYaml(current())); setSourceError(null); };
  const closeSource = () => {
    const parsed = fromYaml<Blueprint>(source ?? "");
    if (!parsed.ok) { setSourceError(parsed.error); return; }
    const b = parsed.value;
    form.reset({
      id: b.id ?? "", title: b.title ?? "", tab: b.tab ?? "", dashboard: b.dashboard ?? "", question: b.question ?? "",
      population: b.population ?? "", triggers: (b.triggers ?? []).join(", "), footnote: b.footnote ?? "",
      callout: b.callout?.text ?? "", callout_level: b.callout?.level ?? "info",
    });
    setKpis(b.kpis ?? []); setWidgets(b.widgets ?? []); setTable(b.table ?? null); setSource(null);
  };
  const move = <T,>(list: T[], i: number, d: number) => {
    const out = [...list];
    const [x] = out.splice(i, 1);
    out.splice(i + d, 0, x!);
    return out;
  };
  const entityFields = table ? fields[measures.find((m) => m.key === table.measure)?.entity ?? ""] ?? [] : [];

  return (
    <Form {...form}>
      <form className="grid gap-4" aria-label="Blueprint" onSubmit={form.handleSubmit((t) => onSave(assemble(t, blueprint, kpis, widgets, table)))}>
        <ProblemList problems={problems} />
        <div className="flex justify-end">
          {source === null
            ? <Button type="button" variant="secondary" size="sm" onClick={openSource}>Source</Button>
            : <Button type="button" variant="secondary" size="sm" onClick={closeSource}>Form</Button>}
        </div>
        {source !== null ? (
          <div className="grid gap-1">
            <Textarea aria-label="Blueprint source (YAML)" rows={24} className="font-mono text-small" value={source}
              readOnly={readOnly} onChange={(e) => setSource(e.target.value)} />
            {sourceError && <p role="alert" className="text-small text-status-danger">{sourceError}</p>}
          </div>
        ) : (
          <fieldset disabled={readOnly} className="grid gap-4">
            <div className="grid gap-3 sm:grid-cols-2">
              <Field control={c} name="title" label="Title" render={(f) => <Input {...f} />} />
              <Field control={c} name="id" label="Key" render={(f) => <Input {...f} />} />
              <Field control={c} name="tab" label="Tab name" description="Its tab on the starter dashboard." render={(f) => <Input {...f} />} />
              <Field control={c} name="dashboard" label="Dashboard" render={(f) => (
                <NativeSelect {...f}>
                  <option value="">Choose…</option>
                  {data.dashboards.map((d) => <option key={d.key} value={d.key}>{d.title}</option>)}
                </NativeSelect>
              )} />
            </div>
            <Field control={c} name="question" label="Question it answers" render={(f) => <Input {...f} />} />
            <Field control={c} name="population" label="Population" render={(f) => <Input {...f} />} />
            <Field control={c} name="triggers" label="Asked as" description="Phrases, separated by commas: the planner and Ask match them." render={(f) => <Input {...f} />} />

            <section aria-label="Headline figures" className="grid gap-2">
              <h3 className="font-medium">Headline figures</h3>
              {kpis.map((k, i) => (
                <div key={i} className="flex flex-wrap items-center gap-2">
                  <NativeSelect aria-label={`Figure ${i + 1} measure`} value={kpiMeasure(k)} className="min-w-48 flex-1"
                    onChange={(e) => setKpis(kpis.map((x, j) => (j !== i ? x : typeof x === "string" ? e.target.value : { ...x, measure: e.target.value })))}>
                    {[...new Set([kpiMeasure(k), ...measures.map((m) => m.key)])].map((m) => <option key={m} value={m}>{label(m)}</option>)}
                  </NativeSelect>
                  <Button type="button" variant="ghost" size="sm" aria-label={`Move figure ${i + 1} up`} disabled={i === 0} onClick={() => setKpis(move(kpis, i, -1))}><ArrowUp aria-hidden /></Button>
                  <Button type="button" variant="ghost" size="sm" aria-label={`Remove figure ${i + 1}`} onClick={() => setKpis(kpis.filter((_, j) => j !== i))}><Trash2 aria-hidden /></Button>
                </div>
              ))}
              {kpis.length < 5 && (
                <Button type="button" variant="secondary" size="sm" className="justify-self-start" disabled={measures.length === 0}
                  onClick={() => setKpis([...kpis, measures[0]!.key])}><Plus aria-hidden /> Add a figure</Button>
              )}
            </section>

            <section aria-label="Visuals" className="grid gap-2">
              <h3 className="font-medium">Visuals</h3>
              {widgets.map((w, i) => (
                <div key={i} className="grid gap-2 rounded-md border border-line p-2 sm:grid-cols-2">
                  <Input aria-label={`Visual ${i + 1} title`} value={w.title} onChange={(e) => setWidgets(widgets.map((x, j) => (j === i ? { ...x, title: e.target.value } : x)))} />
                  <NativeSelect aria-label={`Visual ${i + 1} measure`} value={w.measure} onChange={(e) => setWidgets(widgets.map((x, j) => (j === i ? { ...x, measure: e.target.value } : x)))}>
                    {[...new Set([w.measure, ...measures.map((m) => m.key)])].map((m) => <option key={m} value={m}>{label(m)}</option>)}
                  </NativeSelect>
                  <Input aria-label={`Visual ${i + 1} breakdowns`} placeholder="Breakdowns (dimension keys)" value={(w.breakdowns ?? []).join(", ")}
                    list="pack-dimensions" onChange={(e) => setWidgets(widgets.map((x, j) => (j === i ? { ...x, breakdowns: split(e.target.value) } : x)))} />
                  <div className="flex items-center gap-2">
                    <NativeSelect aria-label={`Visual ${i + 1} chart`} value={w.visual} className="flex-1" onChange={(e) => setWidgets(widgets.map((x, j) => (j === i ? { ...x, visual: e.target.value } : x)))}>
                      {[...new Set([w.visual, ...VISUALS])].map((v) => <option key={v} value={v}>{v.replace(/_/g, " ")}</option>)}
                    </NativeSelect>
                    <Button type="button" variant="ghost" size="sm" aria-label={`Move visual ${i + 1} down`} disabled={i === widgets.length - 1} onClick={() => setWidgets(move(widgets, i, 1))}><ArrowDown aria-hidden /></Button>
                    <Button type="button" variant="ghost" size="sm" aria-label={`Remove visual ${i + 1}`} onClick={() => setWidgets(widgets.filter((_, j) => j !== i))}><Trash2 aria-hidden /></Button>
                  </div>
                </div>
              ))}
              <datalist id="pack-dimensions">{dimensions.map((d) => <option key={d.key} value={d.key}>{d.label}</option>)}</datalist>
              {widgets.length < 6 && (
                <Button type="button" variant="secondary" size="sm" className="justify-self-start" disabled={measures.length === 0}
                  onClick={() => setWidgets([...widgets, { title: "New visual", measure: measures[0]!.key, visual: "ranked_bar" }])}><Plus aria-hidden /> Add a visual</Button>
              )}
            </section>

            <section aria-label="Detail table" className="grid gap-2">
              <h3 className="font-medium">Detail table</h3>
              {table ? (
                <div className="grid gap-2 sm:grid-cols-2">
                  <Input aria-label="Table title" value={table.title} onChange={(e) => setTable({ ...table, title: e.target.value })} />
                  <NativeSelect aria-label="Table measure" value={table.measure} onChange={(e) => setTable({ ...table, measure: e.target.value })}>
                    {[...new Set([table.measure, ...measures.map((m) => m.key)])].map((m) => <option key={m} value={m}>{label(m)}</option>)}
                  </NativeSelect>
                  <Input aria-label="Table columns" className="sm:col-span-2" list="pack-fields" value={table.columns.join(", ")}
                    onChange={(e) => setTable({ ...table, columns: split(e.target.value) })} />
                  <datalist id="pack-fields">{entityFields.map((f) => <option key={f} value={f} />)}</datalist>
                  <Button type="button" variant="ghost" size="sm" className="justify-self-start" onClick={() => setTable(null)}>Remove the table</Button>
                </div>
              ) : (
                <Button type="button" variant="secondary" size="sm" className="justify-self-start" disabled={measures.length === 0}
                  onClick={() => setTable({ title: "Records", measure: kpis[0] ? kpiMeasure(kpis[0]) : measures[0]!.key, columns: [] })}>
                  <Plus aria-hidden /> Add a detail table
                </Button>
              )}
            </section>

            <div className="grid gap-3 sm:grid-cols-[1fr_auto]">
              <Field control={c} name="callout" label="Callout" render={(f) => <Textarea rows={2} {...f} />} />
              <Field control={c} name="callout_level" label="Level" render={(f) => (
                <NativeSelect {...f}><option value="info">Info</option><option value="warning">Warning</option></NativeSelect>
              )} />
            </div>
            <Field control={c} name="footnote" label="Footnote" render={(f) => <Textarea rows={2} {...f} />} />
          </fieldset>
        )}

        <section aria-label="Preview on a connection" className="grid gap-2">
          <div className="flex flex-wrap items-end gap-2">
            <NativeSelect aria-label="Preview connection" value={connectionId} className="min-w-48 flex-1" onChange={(e) => setConnectionId(e.target.value)}>
              <option value="">Choose a connection…</option>
              {(connections.data?.items ?? []).map((x) => <option key={x.id} value={x.id}>{x.name}</option>)}
            </NativeSelect>
            <Button type="button" variant="secondary" disabled={!connectionId || preview.isPending}
              onClick={() => {
                if (source === null) { preview.mutate(current()); return; }
                const parsed = fromYaml<Blueprint>(source);
                if (parsed.ok) preview.mutate(parsed.value); else setSourceError(parsed.error);
              }}>
              <Eye aria-hidden /> {preview.isPending ? "Running…" : "Preview"}
            </Button>
          </div>
          {preview.error && <FormAlert error={asApiError(preview.error)} />}
          {preview.data && <PreviewPanel preview={preview.data} />}
        </section>

        <div className="flex justify-end gap-2">
          <Button type="button" variant="ghost" onClick={onCancel}>{readOnly ? "Close" : "Cancel"}</Button>
          {!readOnly && (
            <Button type="submit" disabled={saving}
              onClick={(e) => {
                if (source === null) return;
                e.preventDefault();
                const parsed = fromYaml<Blueprint>(source);
                if (parsed.ok) onSave(parsed.value); else setSourceError(parsed.error);
              }}>
              {saving ? "Saving…" : "Save blueprint"}
            </Button>
          )}
        </div>
      </form>
    </Form>
  );
}
