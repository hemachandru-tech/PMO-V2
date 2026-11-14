import type { PackChange } from "../../../../api/packs";
import { Badge } from "../../../../components/ui/badge";
import { DataTable } from "../../../../components/ui/data-table";

const KIND: Record<string, string> = {
  entity: "Entity", field: "Field", link: "Link", measure: "Measure", dimension: "Dimension", report: "Starter report",
  dashboard: "Dashboard", glossary: "Glossary", value_synonyms: "Value synonym", dashboard_rules: "Dashboard rule",
  chart_patterns: "Chart pattern", analysis_recipes: "Analysis recipe", report_blueprints: "Report blueprint",
  gold_examples: "Gold example", settings: "Settings",
};

/** What a pack version adds, changes and removes against the previous release (US-238), a measure's rule text before and after. */
export function DiffTable({ changes, first }: { changes: PackChange[]; first?: boolean }) {
  // A first release lists everything as added: show the counts by kind, not every item.
  if (first) {
    const counts = new Map<string, number>();
    for (const c of changes) counts.set(c.kind, (counts.get(c.kind) ?? 0) + 1);
    return <p className="text-small">{[...counts].map(([k, n]) => `${n} ${(KIND[k] ?? k).toLowerCase()}${n === 1 ? "" : "s"}`).join(", ")}.</p>;
  }
  return (
    <DataTable<PackChange>
      label="Changes" data={changes} search={false} getRowId={(c) => `${c.kind}:${c.key}`}
      columns={[
        { id: "kind", header: "Kind", value: (c) => KIND[c.kind] ?? c.kind },
        { id: "item", header: "Item", alwaysVisible: true, value: (c) => c.title || c.key },
        { id: "change", header: "Change", value: (c) => c.change,
          cell: (c) => <Badge variant={c.change === "removed" ? "stale" : c.change === "added" ? "certified" : "neutral"}>{c.change}</Badge> },
        { id: "detail", header: "Detail", value: (c) => (c.rule ? `${c.rule.before ?? ""} ${c.rule.after ?? ""}` : (c.fields ?? []).join(", ")),
          cell: (c) => c.rule && c.change === "changed" && c.rule.before !== c.rule.after
            ? <span className="grid gap-0.5 text-small"><span className="text-ink-muted line-through">{c.rule.before}</span><span>{c.rule.after}</span></span>
            : <span className="text-small text-ink-muted">{(c.fields ?? []).join(", ")}</span> },
      ]}
    />
  );
}
