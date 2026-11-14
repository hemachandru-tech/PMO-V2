import { z } from "zod";

import { required } from "./schemas";

/**
 * Pack knowledge items (US-233), the forms' checks: the same rules as the API's models in `knowledge/guidance.py`
 * (GlossaryTerm, DashboardRule, Forbidden, Recipe), `knowledge/patterns.py` (ChartPattern) and `knowledge/values.py`
 * (PackValue). The API checks the whole document again on save and names each problem's path.
 */
const ID = /^[a-z][a-z0-9_]{1,60}$/;
const id = required("an id", 61).regex(ID, "Use 2 to 61 lower-case letters, digits or _, starting with a letter.");
const words = (what: string) => z.array(z.string().min(1)).min(1, `Enter at least one ${what}.`);
const keys = z.array(z.string().min(1));

export const GlossaryTerm = z.object({
  term: required("the term", 40).regex(/^[A-Za-z0-9][\w&/.-]*( [\w&/.-]+){0,3}$/, "One to four words: letters, digits, & / . -"),
  means: required("what it means", 80),
  explain: z.string().max(600).optional(),
  fields: z.array(z.string().regex(/^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$/, "Write entity.field")).optional(),
  reading: z.string().max(80).nullable().optional(),
  instead_of: z.array(z.string()).max(4).optional(),
});
export type GlossaryTerm = z.infer<typeof GlossaryTerm>;

export const PackValue = z.object({
  dimensions: words("dimension"),
  value: required("the stored value", 500),
  synonyms: words("synonym"),
});
export type PackValue = z.infer<typeof PackValue>;

export const DashboardRule = z.object({
  id,
  title: required("a title", 120),
  rule: required("the rule", 1200),
  measures: keys.optional(),
});
export type DashboardRule = z.infer<typeof DashboardRule>;

export const ForbiddenWords = z.object({
  words: words("word"),
  use: required("the words to use instead", 60),
});
export type ForbiddenWords = z.infer<typeof ForbiddenWords>;

export const ChartPattern = z.object({
  id,
  title: required("a title", 120),
  guidance: required("the guidance", 1200),
  reason: z.string().max(200).nullable().optional(),
  when: z.object({
    measure: keys.optional(), breakdown: keys.optional(), also_breakdown: keys.optional(),
    no_breakdown: z.boolean().optional(), no_measure: keys.optional(),
  }).nullable().optional(),
  suggest: z.object({ ops: z.array(z.record(z.string(), z.unknown())).min(1).max(3), visual: z.string().nullable().optional() }).nullable().optional(),
});
export type ChartPattern = z.infer<typeof ChartPattern>;

export const RECIPE_OPERATIONS = ["share_of_total", "sort", "limit", "moving_avg", "running_total", "rank", "bucket", "percentile", "having", "top_n_per_group", "compare"] as const;
export const INSIGHT_TYPES = ["change", "driver", "trend", "break", "concentration", "outlier", "distribution", "gap", "data_note"] as const;
const RecipeSlot = z.object({ kind: z.enum(["measure", "dimension", "grain"]), options: z.array(z.string()).min(1).max(20), default: z.string() })
  .refine((s) => s.options.includes(s.default), { message: "The default must be one of the options.", path: ["default"] });
export const Recipe = z.object({
  id,
  title: required("a title", 120),
  triggers: words("trigger phrase"),
  question: required("the question", 300),
  slots: z.record(z.string(), RecipeSlot).refine((s) => "measure" in s, "A recipe has a measure slot."),
  spec: z.record(z.string(), z.unknown()),
  operations: z.array(z.enum(RECIPE_OPERATIONS)).optional(),
  chart: required("a chart", 40),
  insights: z.array(z.enum(INSIGHT_TYPES)).optional(),
});
export type Recipe = z.infer<typeof Recipe>;

export const GoldExample = z.object({
  question: required("the question", 500),
  spec: z.record(z.string(), z.unknown()),
  tags: z.array(z.string()).optional(),
  note: z.string().optional(),
  held_out: z.boolean().optional(),
}).loose();
export type GoldExample = z.infer<typeof GoldExample>;
