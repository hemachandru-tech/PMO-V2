import { Plus, X } from "lucide-react";
import { useFieldArray, type UseFormReturn } from "react-hook-form";

import type { PackEntity } from "../../../../api/packs";
import { FIELD_TYPES, PackEntity as EntitySchema, SENSITIVITIES } from "../../../../api/packSchemas";
import { Button } from "../../../../components/ui/button";
import { CheckInput, ListInput, SelectInput, TextInput } from "../fields";
import { usePack } from "../PackContext";
import { SectionEditor } from "../SectionEditor";
import { joinList } from "../yaml";
import type { PackTab } from "./registry";

function FieldRows({ form }: { form: UseFormReturn<PackEntity> }) {
  const { fields, append, remove } = useFieldArray({ control: form.control, name: "fields" });
  return (
    <fieldset className="grid gap-3">
      <legend className="mb-1 font-medium">Fields</legend>
      {fields.map((f, i) => (
        <div key={f.id} className="grid gap-2 rounded-md border border-line p-3 sm:grid-cols-2">
          <TextInput form={form} name={`fields.${i}.key`} label="Key" />
          <TextInput form={form} name={`fields.${i}.label`} label="Label" />
          <SelectInput form={form} name={`fields.${i}.data_type`} label="Type" options={FIELD_TYPES} />
          <SelectInput form={form} name={`fields.${i}.sensitivity`} label="Sensitivity" options={SENSITIVITIES} allowEmpty="internal (default)" />
          <ListInput form={form} name={`fields.${i}.columns`} label="Source columns" description="Names a source may use, first match wins." className="sm:col-span-2" />
          <CheckInput form={form} name={`fields.${i}.required`} label="Required to match" />
          <Button type="button" variant="ghost" size="sm" className="w-fit justify-self-end" onClick={() => remove(i)}
                  aria-label={`Remove field ${form.getValues(`fields.${i}.label`) || i + 1}`}><X aria-hidden />Remove field</Button>
        </div>
      ))}
      <Button type="button" variant="secondary" size="sm" className="w-fit"
              onClick={() => append({ key: "", label: "", data_type: "text", columns: [] })}><Plus aria-hidden />Add field</Button>
    </fieldset>
  );
}

/** Entities & fields (US-232): each entity's source table names, key and fields with their candidate columns. */
function EntitiesTab() {
  const { pack } = usePack();
  return (
    <SectionEditor<PackEntity>
      section="entities" noun={["entity", "entities"]} items={pack.manifest.entities} schema={EntitySchema}
      blank={() => ({ key: "", label: "", object_candidates: [], primary_key: [], fields: [] })}
      title={(e) => e.label || e.key}
      columns={[
        { id: "label", header: "Entity", alwaysVisible: true, value: (e) => e.label, cell: (e) => <span className="font-medium">{e.label}</span> },
        { id: "key", header: "Key", value: (e) => e.key, cell: (e) => <span className="font-mono">{e.key}</span> },
        { id: "tables", header: "Source tables", value: (e) => joinList(e.object_candidates) },
        { id: "fields", header: "Fields", numeric: true, value: (e) => e.fields.length },
        { id: "pk", header: "Primary key", value: (e) => joinList(e.primary_key) },
      ]}
      renderForm={(form) => (
        <>
          <div className="grid gap-3 sm:grid-cols-2">
            <TextInput form={form} name="key" label="Key" />
            <TextInput form={form} name="label" label="Label" />
            <TextInput form={form} name="grain" label="Grain" description="What one row is, e.g. One row per project." className="sm:col-span-2" />
            <ListInput form={form} name="object_candidates" label="Source tables" className="sm:col-span-2" />
            <ListInput form={form} name="primary_key" label="Primary key fields" />
            <ListInput form={form} name="display_fields" label="Fields shown for a record" />
            <ListInput form={form} name="synonyms" label="Other names" className="sm:col-span-2" />
          </div>
          <FieldRows form={form} />
        </>
      )}
    />
  );
}

export const entitiesTab: PackTab = { key: "entities", label: "Entities & fields", count: (p) => p.manifest.entities.length, Component: EntitiesTab };
