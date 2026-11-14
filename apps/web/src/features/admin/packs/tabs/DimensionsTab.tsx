import type { UseFormReturn } from "react-hook-form";

import type { PackDimension } from "../../../../api/packs";
import { ANSWERS, PackDimension as DimensionSchema } from "../../../../api/packSchemas";
import { Checkbox } from "../../../../components/ui/checkbox";
import { Field } from "../../../../components/ui/form";
import { CheckInput, ListInput, SelectInput, TextInput } from "../fields";
import { usePack } from "../PackContext";
import { SectionEditor } from "../SectionEditor";
import { joinList } from "../yaml";
import type { PackTab } from "./registry";

const ANSWER_WORDS: Record<string, string> = { why: "why", how_long: "how long", how_late: "how late", when: "when" };

function AnswersInput({ form }: { form: UseFormReturn<PackDimension> }) {
  return (
    <Field control={form.control} name="answers" label="Answers questions asking" className="sm:col-span-2"
           description="The planner adds this breakdown when a question asks this way."
           render={(f) => {
             const value = (f.value as string[] | undefined) ?? [];
             return (
               <div className="flex flex-wrap gap-4">
                 {ANSWERS.map((a) => (
                   <label key={a} className="flex items-center gap-2">
                     <Checkbox checked={value.includes(a)} onCheckedChange={(c) => f.onChange(c === true ? [...value, a] : value.filter((x) => x !== a))} />
                     {ANSWER_WORDS[a]}
                   </label>
                 ))}
               </div>
             );
           }} />
  );
}

/** Dimensions (US-232): the breakdowns, each on one entity's field; bands and computed values in the YAML view. */
function DimensionsTab() {
  const { pack } = usePack();
  const entities = pack.manifest.entities;
  return (
    <SectionEditor<PackDimension>
      section="dimensions" noun={["dimension", "dimensions"]} items={pack.manifest.dimensions ?? []} schema={DimensionSchema}
      blank={() => ({ key: "", label: "", entity: pack.manifest.signature.primary_entity ?? "", field: "" })}
      title={(d) => d.label || d.key}
      columns={[
        { id: "label", header: "Dimension", alwaysVisible: true, value: (d) => d.label, cell: (d) => <span className="font-medium">{d.label}</span> },
        { id: "field", header: "Field", value: (d) => `${d.entity}.${d.field}`, cell: (d) => <span className="font-mono">{d.entity}.{d.field}</span> },
        { id: "kind", header: "Kind", filter: "select", value: (d) => (d.bands ? "Banded" : d.is_status ? "Status" : d.is_temporal ? "Date" : "Value") },
        { id: "order", header: "Order", hidden: true, value: (d) => joinList(d.sort_order) },
      ]}
      renderForm={(form) => {
        const entity = form.watch("entity");
        const fields = entities.find((e) => e.key === entity)?.fields ?? [];
        return (
          <div className="grid gap-3 sm:grid-cols-2">
            <TextInput form={form} name="key" label="Key" />
            <TextInput form={form} name="label" label="Label" />
            <SelectInput form={form} name="entity" label="Entity" options={entities.map((e) => [e.key, e.label] as [string, string])} allowEmpty="Choose" />
            <SelectInput form={form} name="field" label="Field" options={fields.map((f) => [f.key, f.label] as [string, string])} allowEmpty="Choose" />
            <CheckInput form={form} name="is_status" label="A status" />
            <CheckInput form={form} name="is_temporal" label="A date" />
            <ListInput form={form} name="sort_order" label="Value order" className="sm:col-span-2" />
            <ListInput form={form} name="synonyms" label="Other names" className="sm:col-span-2" />
            <AnswersInput form={form} />
          </div>
        );
      }}
    />
  );
}

export const dimensionsTab: PackTab = { key: "dimensions", label: "Dimensions", count: (p) => (p.manifest.dimensions ?? []).length, Component: DimensionsTab };
