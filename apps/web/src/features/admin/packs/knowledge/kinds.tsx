import type { ReactNode } from "react";

import { ChartPattern, DashboardRule, ForbiddenWords, GlossaryTerm, GoldExample, INSIGHT_TYPES, PackValue, RECIPE_OPERATIONS, Recipe } from "../../../../api/knowledgeSchemas";
import type { KnowledgeKind, KnowledgeKindView } from "../../../../api/packKnowledge";
import { CheckInput, ListInput, TextInput } from "../fields";
import { joinList } from "../yaml";
import { ListEditor } from "./ListEditor";

const keysHint = (view: KnowledgeKindView, what: "measures" | "dimensions") => {
  const keys = view.names[what].slice(0, 4).map((n) => n.key);
  return keys.length ? `${what === "measures" ? "Measure" : "Dimension"} keys, separated by commas (e.g. ${keys.join(", ")}).` : "Separated by commas.";
};

function Glossary({ view }: { view: KnowledgeKindView }) {
  return (
    <ListEditor<GlossaryTerm> view={view} at={["terms"]} noun={["term", "terms"]} schema={GlossaryTerm}
      blank={() => ({ term: "", means: "" })} title={(t) => t.term || "New term"}
      columns={[{ id: "means", header: "Means", value: (t) => t.means }]}
      intro={<p className="text-small text-ink-muted">Business words and abbreviations Ask reads as their meaning (“S2” → planning stage). A term is kept on a connection only when its model has the fields named; a term with a reading is a phrase with more than one meaning.</p>}
      renderForm={(form) => (
        <div className="grid gap-3 sm:grid-cols-2">
          <TextInput form={form} name="term" label="Term" />
          <TextInput form={form} name="means" label="Means" description="The words Ask reads it as." />
          <TextInput form={form} name="explain" label="Explanation" multiline className="sm:col-span-2" />
          <ListInput form={form} name="fields" label="Fields" className="sm:col-span-2" description="entity.field, separated by commas (e.g. projects.cost_total_usd)." />
          <TextInput form={form} name="reading" label="Read as measure" description="For a phrase with more than one meaning." />
          <ListInput form={form} name="instead_of" label="Not as measures" description={keysHint(view, "measures")} />
        </div>
      )} />
  );
}

function ValueSynonyms({ view }: { view: KnowledgeKindView }) {
  return (
    <ListEditor<PackValue> view={view} at={["values"]} noun={["value", "values"]} schema={PackValue}
      blank={() => ({ dimensions: [], value: "", synonyms: [] })} title={(v) => v.value || "New value"}
      columns={[{ id: "synonyms", header: "Other words", value: (v) => joinList(v.synonyms) }]}
      intro={<p className="text-small text-ink-muted">Other words people use for a stored value (“Saudi” → KSA). On install they wait for review under Admin → Knowledge.</p>}
      renderForm={(form) => (
        <div className="grid gap-3 sm:grid-cols-2">
          <TextInput form={form} name="value" label="Stored value" />
          <ListInput form={form} name="dimensions" label="Dimensions" description={keysHint(view, "dimensions")} />
          <ListInput form={form} name="synonyms" label="Other words" className="sm:col-span-2" />
        </div>
      )} />
  );
}

function DashboardRules({ view }: { view: KnowledgeKindView }) {
  return (
    <div className="grid gap-6">
      <ListEditor<DashboardRule> view={view} at={["rules"]} noun={["rule", "rules"]} schema={DashboardRule}
        blank={() => ({ id: "", title: "", rule: "" })} title={(r) => r.title || r.id || "New rule"}
        intro={<p className="text-small text-ink-muted">House rules the planner and the advisor follow when they build a dashboard (“forecast cost, never spend”). A rule is shown only to people who can see all of its measures.</p>}
        renderForm={(form) => (
          <div className="grid gap-3 sm:grid-cols-2">
            <TextInput form={form} name="id" label="Id" />
            <TextInput form={form} name="title" label="Title" />
            <TextInput form={form} name="rule" label="Rule" multiline className="sm:col-span-2" />
            <ListInput form={form} name="measures" label="Measures" className="sm:col-span-2" description={keysHint(view, "measures")} />
          </div>
        )} />
      <section aria-label="Wording terms" className="grid gap-2">
        <h3 className="text-body font-semibold">Wording terms</h3>
        <ListEditor<ForbiddenWords> view={view} at={["wording", "forbidden"]} noun={["wording term", "wording terms"]} schema={ForbiddenWords}
          blank={() => ({ words: [], use: "" })} title={(w) => joinList(w.words) || "New wording term"}
          columns={[{ id: "use", header: "Say instead", value: (w) => w.use }]}
          intro={<p className="text-small text-ink-muted">Words reports and answers never use, and what to say instead. Status words are in the document's YAML.</p>}
          renderForm={(form) => (
            <div className="grid gap-3 sm:grid-cols-2">
              <ListInput form={form} name="words" label="Never say" />
              <TextInput form={form} name="use" label="Say instead" />
            </div>
          )} />
      </section>
    </div>
  );
}

function ChartPatterns({ view }: { view: KnowledgeKindView }) {
  return (
    <ListEditor<ChartPattern> view={view} at={["patterns"]} noun={["pattern", "patterns"]} schema={ChartPattern}
      blank={() => ({ id: "", title: "", guidance: "" })} title={(p) => p.title || p.id || "New pattern"}
      intro={<p className="text-small text-ink-muted">House chart conventions the advisor applies (“keep lifecycle phases in order”). A pattern is kept on a connection only when its model has the measures and dimensions it names; a suggested change is written in the YAML view.</p>}
      renderForm={(form) => (
        <div className="grid gap-3 sm:grid-cols-2">
          <TextInput form={form} name="id" label="Id" />
          <TextInput form={form} name="title" label="Title" />
          <TextInput form={form} name="guidance" label="Guidance" multiline className="sm:col-span-2" />
          <TextInput form={form} name="reason" label="Reason shown" className="sm:col-span-2" />
          <ListInput form={form} name="when.measure" label="When the measure is one of" description={keysHint(view, "measures")} />
          <ListInput form={form} name="when.no_measure" label="Unless the measure is" description={keysHint(view, "measures")} />
          <ListInput form={form} name="when.breakdown" label="When broken down by" description={keysHint(view, "dimensions")} />
          <ListInput form={form} name="when.also_breakdown" label="And also by one of" description={keysHint(view, "dimensions")} />
          <CheckInput form={form} name="when.no_breakdown" label="Only when there is no breakdown" />
        </div>
      )} />
  );
}

function AnalysisRecipes({ view }: { view: KnowledgeKindView }) {
  return (
    <ListEditor<Recipe> view={view} at={["recipes"]} noun={["recipe", "recipes"]} schema={Recipe}
      blank={() => ({ id: "", title: "", triggers: [], question: "", chart: "ranked_bar",
                      slots: { measure: { kind: "measure", options: [], default: "" } }, spec: { measure: "{measure}" } })}
      title={(r) => r.title || r.id || "New recipe"}
      columns={[{ id: "chart", header: "Chart", value: (r) => r.chart }]}
      intro={<p className="text-small text-ink-muted">Named analyses Ask can run (“Pareto 80/20”): a question template with slots and the Query Spec it runs. Slots and the spec are written in the YAML view.</p>}
      renderForm={(form) => (
        <div className="grid gap-3 sm:grid-cols-2">
          <TextInput form={form} name="id" label="Id" />
          <TextInput form={form} name="title" label="Title" />
          <TextInput form={form} name="question" label="Question" className="sm:col-span-2" description="Slots in braces, e.g. Which {dimension} make up {measure}?" />
          <ListInput form={form} name="triggers" label="Trigger phrases" className="sm:col-span-2" />
          <TextInput form={form} name="chart" label="Chart" />
          <ListInput form={form} name="operations" label="Operations" description={`Of: ${RECIPE_OPERATIONS.join(", ")}.`} />
          <ListInput form={form} name="insights" label="Insights" className="sm:col-span-2" description={`Of: ${INSIGHT_TYPES.join(", ")}.`} />
        </div>
      )} />
  );
}

function GoldExamples({ view }: { view: KnowledgeKindView }) {
  return (
    <ListEditor<GoldExample> view={view} at={["examples"]} noun={["example", "examples"]} schema={GoldExample}
      blank={() => ({ question: "", spec: { measure: { ref: "measure:" } } })} title={(g) => g.question || "New example"}
      columns={[{ id: "tags", header: "Tags", filter: "select", value: (g) => joinList(g.tags) }]}
      intro={<p className="text-small text-ink-muted">Questions with the Query Spec of their right answer: the planner's worked examples and the gold set Ask is scored on. Imported as suggestions an Admin approves. The spec is written in the YAML view.</p>}
      renderForm={(form) => (
        <div className="grid gap-3 sm:grid-cols-2">
          <TextInput form={form} name="question" label="Question" className="sm:col-span-2" />
          <ListInput form={form} name="tags" label="Tags" />
          <CheckInput form={form} name="held_out" label="Held out (never a worked example)" />
          <TextInput form={form} name="note" label="Note" multiline className="sm:col-span-2" />
        </div>
      )} />
  );
}

export const KIND_LABEL: Record<KnowledgeKind, string> = {
  glossary: "Glossary", value_synonyms: "Value synonyms", dashboard_rules: "Dashboard rules and wording",
  chart_patterns: "Chart patterns", analysis_recipes: "Analysis recipes", gold_examples: "Gold examples",
};

export const KIND_EDITOR: Record<KnowledgeKind, (p: { view: KnowledgeKindView }) => ReactNode> = {
  glossary: Glossary, value_synonyms: ValueSynonyms, dashboard_rules: DashboardRules, chart_patterns: ChartPatterns,
  analysis_recipes: AnalysisRecipes, gold_examples: GoldExamples,
};
