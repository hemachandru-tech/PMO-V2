import type { PackLink } from "../../../../api/packs";
import { CARDINALITIES, PackLink as LinkSchema } from "../../../../api/packSchemas";
import { CheckInput, ListInput, SelectInput } from "../fields";
import { usePack } from "../PackContext";
import { SectionEditor } from "../SectionEditor";
import { joinList } from "../yaml";
import type { PackTab } from "./registry";

const CARD: Record<string, string> = { many_to_one: "many to one", one_to_many: "one to many", one_to_one: "one to one" };

/** Links (US-232): how the pack's entities join, by field. */
function LinksTab() {
  const { pack } = usePack();
  const entities = pack.manifest.entities.map((e) => [e.key, e.label] as [string, string]);
  return (
    <SectionEditor<PackLink>
      section="links" noun={["link", "links"]} items={pack.manifest.links ?? []} schema={LinkSchema}
      blank={() => ({ from_entity: "", to_entity: "", from_fields: [], to_fields: [], cardinality: "many_to_one" })}
      title={(l) => `${l.from_entity} → ${l.to_entity}`}
      columns={[
        { id: "from", header: "From", alwaysVisible: true, value: (l) => `${l.from_entity}.${joinList(l.from_fields)}`, cell: (l) => <span className="font-mono">{l.from_entity}.{joinList(l.from_fields)}</span> },
        { id: "to", header: "To", value: (l) => `${l.to_entity}.${joinList(l.to_fields)}`, cell: (l) => <span className="font-mono">{l.to_entity}.{joinList(l.to_fields)}</span> },
        { id: "cardinality", header: "Cardinality", filter: "select", value: (l) => CARD[l.cardinality] ?? l.cardinality },
        { id: "scope", header: "Passes scope on", value: (l) => (l.inherits_scope ? "Yes" : "") },
      ]}
      renderForm={(form) => (
        <div className="grid gap-3 sm:grid-cols-2">
          <SelectInput form={form} name="from_entity" label="From entity" options={entities} allowEmpty="Choose" />
          <SelectInput form={form} name="to_entity" label="To entity" options={entities} allowEmpty="Choose" />
          <ListInput form={form} name="from_fields" label="From fields" />
          <ListInput form={form} name="to_fields" label="To fields" />
          <SelectInput form={form} name="cardinality" label="Cardinality" options={CARDINALITIES.map((c) => [c, CARD[c]!] as [string, string])} />
          <CheckInput form={form} name="inherits_scope" label="Rows follow the linked row's scope" description="Many to one on one field only." />
        </div>
      )}
    />
  );
}

export const linksTab: PackTab = { key: "links", label: "Links", count: (p) => (p.manifest.links ?? []).length, Component: LinksTab };
