import { z } from "zod";

import { required } from "./schemas";

/**
 * Pack Studio items (US-232): PackField, PackEntity, PackLink, PackMeasure, PackDimension — the pack item models the
 * API puts in openapi.json for these forms. Sections are saved whole and checked again by the API.
 * Kept apart from `schemas.ts` so the screens that import those don't load these.
 */
const KEY = /^[a-z][a-z0-9_]*$/;
const key = (what: string) => required(what, 80).regex(KEY, "Use lower-case letters, digits and _, starting with a letter.");
const names = (what: string) => z.array(z.string().min(1)).min(1, `Enter at least one ${what}.`);
export const FIELD_TYPES = ["text", "integer", "decimal", "date", "datetime", "boolean", "json"] as const;
export const SENSITIVITIES = ["public", "internal", "personal", "confidential", "sensitive"] as const;
export const CARDINALITIES = ["many_to_one", "one_to_many", "one_to_one"] as const;
export const MEASURE_FORMATS = ["integer", "decimal", "currency", "percent"] as const;
export const ANSWERS = ["why", "how_long", "how_late", "when"] as const;
export const PackField = z.object({
  key: key("a field key"),
  label: required("a label", 120),
  data_type: z.enum(FIELD_TYPES),
  columns: names("source column"),
  required: z.boolean().optional(),
  semantic_type: z.string().max(40).nullable().optional(),
  sensitivity: z.enum(SENSITIVITIES).optional(),
  description: z.string().max(500).nullable().optional(),
  synonyms: z.array(z.string()).optional(),
  values: z.array(z.string()).optional(),
});
export const PackEntity = z.object({
  key: key("an entity key"),
  label: required("a label", 120),
  grain: z.string().max(200).nullable().optional(),
  object_candidates: names("source table"),
  primary_key: z.array(z.string().min(1)),
  display_fields: z.array(z.string()).optional(),
  synonyms: z.array(z.string()).optional(),
  fields: z.array(PackField),
});
export const PackLink = z.object({
  from_entity: required("the entity the link starts from", 80),
  to_entity: required("the entity it points to", 80),
  from_fields: names("field"),
  to_fields: names("field"),
  cardinality: z.enum(CARDINALITIES),
  inherits_scope: z.boolean().optional(),
});
export const PackMeasure = z.object({
  key: key("a measure key"),
  label: required("a label", 120),
  entity: required("the entity it counts", 80),
  plain_rule: required("the rule in plain words", 500),
  expression: z.record(z.string(), z.unknown()),
  definition: z.string().max(500).nullable().optional(),
  format: z.enum(MEASURE_FORMATS).optional(),
  rule_ref: z.string().max(80).nullable().optional(),
  synonyms: z.array(z.string()).optional(),
});
export const PackDimension = z.object({
  key: key("a dimension key"),
  label: required("a label", 120),
  entity: required("the entity", 80),
  field: required("the field", 80),
  is_status: z.boolean().optional(),
  is_temporal: z.boolean().optional(),
  sort_order: z.array(z.string()).nullable().optional(),
  synonyms: z.array(z.string()).optional(),
  answers: z.array(z.enum(ANSWERS)).optional(),
});

export const PACK_SCHEMAS = { PackField, PackEntity, PackLink, PackMeasure, PackDimension } as const;
