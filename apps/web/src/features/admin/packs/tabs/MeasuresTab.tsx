import { useMutation } from "@tanstack/react-query";
import { FlaskConical } from "lucide-react";
import { useEffect, useId, useState } from "react";
import type { UseFormReturn } from "react-hook-form";

import { tryMeasure, type PackMeasure } from "../../../../api/packs";
import { MEASURE_FORMATS, PackMeasure as MeasureSchema } from "../../../../api/packSchemas";
import { Button } from "../../../../components/ui/button";
import { Field } from "../../../../components/ui/form";
import { Textarea } from "../../../../components/ui/textarea";
import { asApiError } from "../../../../lib/api-errors";
import { ConnectionPicker } from "../../access/ConnectionPicker";
import { ExpressionTree } from "../ExpressionTree";
import { ListInput, SelectInput, TextInput } from "../fields";
import { usePack } from "../PackContext";
import { SectionEditor } from "../SectionEditor";
import { fromYaml, toYaml } from "../yaml";
import type { PackTab } from "./registry";

/** The rule's expression typed as YAML ({agg: count, where: {field: status, op: "=", value: Active}}). */
function ExpressionInput({ form }: { form: UseFormReturn<PackMeasure> }) {
  return (
    <Field control={form.control} name="expression" label="Rule expression (YAML)" className="sm:col-span-2"
           description="The typed rule the query engine compiles: an aggregate (agg, field, where) or arithmetic (op, left, right)."
           render={(f) => <YamlBox value={f.value as Record<string, unknown>} onChange={f.onChange} onInvalid={(msg) => form.setError("expression", { message: msg })} onValid={() => form.clearErrors("expression")} />} />
  );
}

function YamlBox({ value, onChange, onInvalid, onValid, ...rest }: { value: Record<string, unknown>; onChange: (v: Record<string, unknown>) => void; onInvalid: (m: string) => void; onValid: () => void }) {
  const [text, setText] = useState(() => toYaml(value ?? {}));
  useEffect(() => { const p = fromYaml(text); if (!p.ok || JSON.stringify(p.value) !== JSON.stringify(value)) setText(toYaml(value ?? {})); }, [value]); // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <Textarea {...rest} rows={6} spellCheck={false} className="font-mono text-small" value={text}
              onChange={(e) => { setText(e.target.value); const p = fromYaml(e.target.value); if (p.ok) { onValid(); onChange(p.value); } else onInvalid(p.error); }} />
  );
}

/** "Try on a connection" (US-232): the measure's figure on a connection's data, bound as an install would. */
function TryMeasure({ measure }: { measure: PackMeasure }) {
  const { pack } = usePack();
  const [connection, setConnection] = useState("");
  const pickerId = useId();
  const run = useMutation({ mutationFn: () => tryMeasure(pack.id, connection, measure.key) });
  const out = run.data;
  return (
    <section aria-label="Try on a connection" className="grid gap-2 rounded-md border border-line p-3">
      <span className="font-medium">Try on a connection</span>
      <ConnectionPicker id={pickerId} value={connection} onChange={setConnection} />
      <Button type="button" size="sm" variant="secondary" className="w-fit" disabled={!connection || run.isPending} onClick={() => run.mutate()}>
        <FlaskConical aria-hidden />{run.isPending ? "Running…" : "Run the measure"}
      </Button>
      <p aria-live="polite" className="text-small">
        {run.isError && asApiError(run.error, "The measure could not be tried.").message}
        {out && (out.available
          ? <>{measure.label}: <span className="font-mono font-medium">{typeof out.value === "number" ? out.value.toLocaleString() : out.value}</span> on all rows.</>
          : <>Not available on this source: {out.gaps.join("; ")}.</>)}
      </p>
    </section>
  );
}

/** Measures (US-232): the plain rule people read, the expression as a tree, and a trial figure on real data. */
function MeasuresTab() {
  const { pack } = usePack();
  const entities = pack.manifest.entities.map((e) => [e.key, e.label] as [string, string]);
  return (
    <SectionEditor<PackMeasure>
      section="measures" noun={["measure", "measures"]} items={pack.manifest.measures} schema={MeasureSchema}
      blank={() => ({ key: "", label: "", entity: pack.manifest.signature.primary_entity ?? "", plain_rule: "", expression: { agg: "count" } })}
      title={(m) => m.label || m.key}
      columns={[
        { id: "label", header: "Measure", alwaysVisible: true, value: (m) => m.label, cell: (m) => <span className="font-medium">{m.label}</span> },
        { id: "rule", header: "Rule", value: (m) => m.plain_rule, cell: (m) => <span className="line-clamp-2 max-w-prose">{m.plain_rule}</span> },
        { id: "entity", header: "Entity", filter: "select", value: (m) => m.entity },
        { id: "key", header: "Key", hidden: true, value: (m) => m.key, cell: (m) => <span className="font-mono">{m.key}</span> },
        { id: "format", header: "Format", filter: "select", value: (m) => m.format ?? "integer" },
      ]}
      renderExtra={(m) => (
        <div className="grid gap-3">
          <p className="max-w-prose">{m.plain_rule}</p>
          <ExpressionTree expression={m.expression} />
          <TryMeasure measure={m} />
        </div>
      )}
      renderForm={(form) => (
        <div className="grid gap-3 sm:grid-cols-2">
          <TextInput form={form} name="key" label="Key" />
          <TextInput form={form} name="label" label="Label" />
          <SelectInput form={form} name="entity" label="Counts rows of" options={entities} allowEmpty="Choose" />
          <SelectInput form={form} name="format" label="Format" options={MEASURE_FORMATS} allowEmpty="integer (default)" />
          <TextInput form={form} name="plain_rule" label="Rule in plain words" multiline className="sm:col-span-2" />
          <TextInput form={form} name="definition" label="One-line definition" description="Shown with the figure." className="sm:col-span-2" />
          <ExpressionInput form={form} />
          <TextInput form={form} name="rule_ref" label="Business rule reference" />
          <ListInput form={form} name="synonyms" label="Other names" />
        </div>
      )}
    />
  );
}

export const measuresTab: PackTab = { key: "measures", label: "Measures", count: (p) => p.manifest.measures.length, Component: MeasuresTab };
