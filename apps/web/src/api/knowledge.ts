import type { AskMode } from "./app";
/** Knowledge documents, retrieval and gold examples (API-049 – API-051; US-056, US-057, US-100 – US-103). */

export type ReviewStatus = "pending_review" | "approved" | "rejected" | "retired";
export type IndexStatus = "queued" | "indexing" | "indexed" | "failed";
export type SourceKind = "model_definition" | "glossary" | "uploaded_doc" | "chart_pattern" | "gold_summary"
  | "dashboard_rule" | "report_blueprint" | "analysis_recipe";

export interface ScanFlags {
  dropped?: number;
  redacted?: Record<string, number>;
  injection_chunks?: number[];
  leak_classes?: string[];
  extractor?: string;
}

export interface KnowledgeDocument {
  id: string;
  connection_id: string | null;
  connection_name?: string | null;
  model_version_id: string | null;
  model_version_no?: number | null;
  source_kind: SourceKind;
  title: string;
  original_filename: string | null;
  content_type: string | null;
  size_bytes: number | null;
  review_status: ReviewStatus;
  index_status: IndexStatus;
  index_error: string | null;
  scan_flags: ScanFlags;
  injection_flagged: boolean;
  acknowledged_by: string | null;
  reviewed_by: string | null;
  reviewed_by_name?: string | null;
  reviewed_at: string | null;
  review_note: string | null;
  embedding_model: string | null;
  chunk_count: number;
  indexed_at: string | null;
  created_by: string | null;
  created_by_name?: string | null;
  created_at: string;
  updated_at: string;
}

export interface KnowledgeChunk {
  id: string;
  chunk_no: number;
  content: string;
  metadata: { heading?: string; page?: number; injection_flagged?: boolean; kind?: string };
  embedded: boolean;
}

export interface KnowledgeDocumentDetail extends KnowledgeDocument {
  chunks: KnowledgeChunk[];
}

export interface SearchHit {
  chunk_id: string;
  document_id: string;
  kind: string;
  title: string;
  snippet: string;
  score: number;
}

export interface SearchResult {
  hits: SearchHit[];
  limited_context: boolean;
  note: string | null;
}

export interface SpecDiff {
  path: string;
  expected: unknown;
  actual: unknown;
}

export interface GoldExample {
  id: string;
  connection_id: string;
  connection_name?: string;
  model_version_id: string;
  question: string;
  /** Earlier messages of a multi-turn example (US-184); ``question`` is then the follow-up. */
  turns?: string[];
  expected_spec: { measure: { ref: string }; [key: string]: unknown };
  expected_result_fingerprint: Record<string, unknown> | null;
  test_scope_role_id: string | null;
  tags: string[];
  use_for_fewshot: boolean;
  status: "suggested" | "approved" | "rejected";
  origin: "manual" | "answer" | "pack_draft" | "clarification" | "freehand";
  notes: string | null;
  embedded: boolean;
  suggested_by_name?: string | null;
  verified_by_name?: string | null;
  last_passed?: boolean | null;
  last_diff?: SpecDiff[] | null;
  /** US-250: the kind of answer the expected spec gives. */
  shape?: AnswerShape | null;
  created_at: string;
}

export interface TagRate {
  total: number;
  passed: number;
  spec_matched?: number;
  pass_rate: number;
}

/** US-190: scores of one group of results (a planner path, or a set). */
export type Outcome = "right" | "wrong" | "clarified" | "refused" | "error";

export interface GroupScores {
  total: number;
  passed: number;
  spec_matched: number;
  outcomes: Partial<Record<Outcome, number>>;
  /** Wrong answers given at or above the confidence threshold. */
  confident_wrong: number;
  /** Clarifications that offered the expected measure. */
  clarify_right: number;
  pass_rate: number | null;
  spec_match_rate: number | null;
  refusal_rate: number | null;
  clarify_rate: number | null;
  confident_wrong_rate: number | null;
  /** Required-item recall: the expected measure and dimensions among linking candidates and retrieved knowledge. */
  required_recall: { total: number; found: number | null; measure_linked: number | null; measure_retrieved: number | null };
  latency_ms: { p50: number; p95?: number; max: number } | null;
  /** US-246: model calls made while planning (review, vote, back-check, wording). */
  llm_calls?: { total: number; per_question: number | null } | null;
}

export type PlannerPath = "rules" | "freehand" | "llm" | "none";
export interface SetScores extends GroupScores { by_path: Partial<Record<PlannerPath, GroupScores>> }

export const PATH_LABEL: Record<PlannerPath, string> = {
  rules: "Rules", freehand: "Free-hand", llm: "Language model", none: "Not planned",
};

/** US-184 error taxonomy: one cause per failed example. */
export type ErrorClass = "linking" | "measure" | "filter" | "time" | "shape" | "over_clarification" | "result" | "error";

export const ERROR_LABEL: Record<ErrorClass, string> = {
  linking: "Words not linked to the model",
  measure: "Wrong measure",
  filter: "Wrong or missing filter",
  time: "Wrong time",
  shape: "Wrong kind of answer, breakdown, sort or limit",
  over_clarification: "Asked to clarify",
  result: "Different result",
  error: "Planning or running failed",
};

export interface EvalRun {
  id: string;
  connection_id: string | null;
  /** The Ask answer mode the run measured (US-259); null: sources in different modes, each in its own. */
  ask_mode?: AskMode | null;
  trigger: "manual" | "nightly" | "model_approval" | "llm_settings";
  tags: string[];
  status: "queued" | "running" | "done" | "failed";
  total: number;
  passed: number;
  /** Execution accuracy: same rows as the expected spec (US-184). */
  pass_rate: number | null;
  spec_match_rate?: number | null;
  recall_at_8: number | null;
  mrr?: number | null;
  ndcg_at_8?: number | null;
  recall_by_kind?: { measure?: number | null; dimension?: number | null };
  by_error?: Partial<Record<ErrorClass, number>>;
  value_linking?: { total?: number; correct?: number; rate?: number | null };
  /** US-190: planned and compared only, no result rows fetched (pass = spec match). */
  plan_only?: boolean;
  /** US-246: LLM review forced on (true) or off (false) for this run; null follows the AI settings. */
  review?: boolean | null;
  by_path?: Partial<Record<PlannerPath, GroupScores>>;
  by_set?: { original?: SetScores; paraphrase?: SetScores };
  by_tag: Record<string, TagRate>;
  threshold: number;
  below_threshold: boolean | null;
  planner: Record<string, number>;
  error: string | null;
  run_as_name?: string | null;
  started_at: string | null;
  finished_at: string | null;
  created_at: string;
}

export interface EvalResult {
  id: string;
  gold_example_id: string | null;
  question: string;
  tags: string[];
  passed: boolean;
  planner: string | null;
  actual_spec: Record<string, unknown> | null;
  diff: SpecDiff[];
  retrieval_hit: boolean | null;
  fingerprint_ok: boolean | null;
  error: string | null;
  spec_match?: boolean | null;
  exec_match?: boolean | null;
  error_class?: ErrorClass | null;
  reciprocal_rank?: number | null;
  ndcg_at_8?: number | null;
  values_linked?: boolean | null;
  status?: "ok" | "clarify" | "unsupported" | "error" | null;
  confidence?: number | null;
  confident_wrong?: boolean | null;
  clarify_right?: boolean | null;
  required?: { measure: { linked: boolean; retrieved: boolean } | null; dimensions: { linked: number; retrieved: number } | null; found: boolean } | null;
  latency_ms?: number | null;
  /** US-250: the kind of answer the example expects (null when the example was deleted). */
  shape?: AnswerShape | null;
}

export interface EvalRunDetail extends EvalRun {
  results: EvalResult[];
  /** US-250: outcomes per answer shape (rows, totalling the run) and per shape × tag (cells). */
  shape_grid?: ShapeGrid;
}

/** US-250: the kind of answer a question asks for (the question frame's shape, ENH-028). */
export type AnswerShape = "number" | "list_of_records" | "breakdown" | "ranking" | "trend" | "comparison" | "share"
  | "distribution" | "record_detail" | "definition";

export const SHAPE_LABEL: Record<AnswerShape | "unknown", string> = {
  number: "One number", list_of_records: "List of records", breakdown: "Breakdown", ranking: "Ranking", trend: "Trend",
  comparison: "Comparison", share: "Share of total", distribution: "Distribution", record_detail: "One record's details",
  definition: "Definition", unknown: "Unknown (example deleted)",
};

export interface ShapeCell {
  total: number;
  right: number;
  confident_wrong: number;
  clarified: number;
  refused: number;
  wrong: number;
  error: number;
}

export interface ShapeGrid {
  rows: Partial<Record<AnswerShape | "unknown", ShapeCell>>;
  cells: Partial<Record<AnswerShape | "unknown", Record<string, ShapeCell>>>;
  tags: string[];
  total: ShapeCell;
}

export const REVIEW_LABEL: Record<ReviewStatus, string> = {
  pending_review: "Pending review", approved: "Approved", rejected: "Rejected", retired: "Retired",
};

export const KIND_LABEL: Record<string, string> = {
  model_definition: "Model definitions", glossary: "Glossary", uploaded_doc: "Reference document",
  chart_pattern: "Chart pattern", gold_summary: "Gold summary", entity: "Entity", certified_measure: "Certified measure",
  dashboard_rule: "Dashboard rule", report_blueprint: "Report blueprint", analysis_recipe: "Analysis recipe",
};

export const percent = (v: number | null | undefined) => (v == null ? "—" : `${Math.round(v * 1000) / 10}%`);

/** Value synonyms (US-173): people's words for a stored value, Admin-reviewed before Ask links them. */
export type SynonymStatus = "pending_review" | "approved" | "rejected";

export interface ValueSynonym {
  id: string;
  dimension: string;
  dimension_label: string;
  stored_value: string;
  synonym: string;
  source: "pack" | "clarification" | "admin" | "answer";
  status: SynonymStatus;
  note: string | null;
  created_at: string;
  reviewed_at: string | null;
  proposed_by: string | null;
  reviewed_by: string | null;
  in_data: boolean;
}

export interface ValueSynonymList {
  items: ValueSynonym[];
  counts: Partial<Record<SynonymStatus, number>>;
  index: { values: number; dimensions: number; refreshed_at: string | null };
}

export interface IndexedDimension {
  key: string;
  label: string;
  values: { stored: string; label: string; synonyms: string[] }[];
}

export type LinkHow = "exact" | "synonym" | "partial" | "spelling" | "meaning";

export interface ValueLinkResult {
  links: { dimension: string; stored: string; label: string; phrase: string; how: LinkHow; score: number }[];
  choices: { dimension: string; phrase: string; options: { stored: string; label: string }[] }[];
}

/** Answer feedback (US-183): a person's vote on their own answer, and the Admin queue of down-votes. */
export type FeedbackReason = "wrong" | "not_what_i_asked";
export type FeedbackStatus = "none" | "open" | "fixed" | "dismissed";

export interface MyFeedback {
  id: string;
  turn_id: string;
  vote: "up" | "down";
  reason: FeedbackReason | null;
  comment: string | null;
  status: FeedbackStatus;
  created_at: string;
  updated_at: string;
}

export interface FeedbackItem extends MyFeedback {
  user: { id: string; display_name: string };
  question: string;
  spec: Record<string, unknown> | null;
  connection: { id: string; name: string } | null;
  gold_example_id: string | null;
  resolved_by: { id: string; display_name: string } | null;
  resolved_at: string | null;
  resolution_note: string | null;
}

export interface FeedbackQueue {
  items: FeedbackItem[];
  counts: { open: number; fixed: number; dismissed: number; up: number };
}

/** Curation queue from Ask's misses (US-198): proposals waiting for an Admin. */
export type CurationKind = "measure_synonym" | "glossary_term" | "gold_example";
export type CurationSource = "refused" | "clarification" | "repair" | "feedback" | "direct_sql";

export interface CurationProposal {
  id: string;
  kind: CurationKind;
  status: "pending" | "approved" | "dismissed";
  connection: { id: string; name: string };
  question: string;
  phrase: string | null;
  meaning: string | null;
  measure: { ref: string; label: string | null } | null;
  options: { ref: string; label: string }[];
  spec: Record<string, unknown> | null;
  needs_fix: boolean;
  applied: Record<string, unknown> | null;
  seen: number;
  by_source: Partial<Record<CurationSource, number>>;
  questions: { question: string; source: CurationSource; detail: string | null; turn_id: string | null; at: string }[];
  reviewed_by: { id: string; display_name: string } | null;
  reviewed_at: string | null;
  review_note: string | null;
  created_at: string;
  updated_at: string;
}

export interface CurationQueue {
  items: CurationProposal[];
  counts: {
    pending: number; approved: number; dismissed: number;
    pending_by_kind: Record<CurationKind, number>;
    pending_by_source: Record<CurationSource, number>;
  };
}

export interface CurationCollected {
  added: number;
  read: Record<Exclude<CurationSource, "direct_sql">, number>;
  held_out_skipped: number;
}

/** Business definitions read from knowledge (US-203): proposals and approved definitions for an Admin. */
export type DefinitionStatus = "pending" | "approved" | "rejected" | "retired";

export interface DefinitionRule {
  entity: string;
  field: string;
  op: "in" | "not_in";
  values: string[];
}

export interface DefinitionChecks {
  checked_at: string;
  figure: number | null;
  values: Record<string, number> | null;
  measure: { ref: string; label: string; rule: string; figure: number | null } | null;
  same_rule: boolean | null;
  conflict: boolean;
}

export interface KnowledgeDefinition {
  id: string;
  status: DefinitionStatus;
  term: string;
  meaning: string;
  rule: DefinitionRule | null;
  rule_text: string | null;
  expressible: boolean;
  gaps: string[];
  measure: { ref: string; label: string | null } | null;
  checks: DefinitionChecks | null;
  conflict: boolean;
  connection: { id: string; name: string };
  model_version: { id: string; version_no: number | null };
  document: { id: string; title: string; status: string };
  passage: { chunk_id: string | null; chunk_no: number; heading: string | null; text: string };
  reviewed_by: { id: string; display_name: string } | null;
  reviewed_at: string | null;
  review_note: string | null;
  created_at: string;
  updated_at: string;
}

export interface DefinitionList {
  items: KnowledgeDefinition[];
  counts: { pending: number; approved: number; rejected: number; conflicts: number; text_only: number };
}

/** Named conditions (US-252): a business term stored once as a condition Tela can run. */
export type ConditionStatus = "proposed" | "approved" | "retired";
export type ConditionSource = "document" | "definition_sheet" | "pack_measure";

export interface ConditionConflict {
  condition: { rule: string | null; figure: number | null };
  measure: { ref: string; label: string; rule: string; figure: number | null };
  checked_at: string | null;
}

export interface NamedCondition {
  id: string;
  status: ConditionStatus;
  term: string;
  synonyms: string[];
  name: string;
  entity: string;
  condition: Record<string, unknown>;
  rule_text: string | null;
  source: ConditionSource;
  gaps: string[];
  measure: { ref: string; label: string | null } | null;
  conflict: ConditionConflict | null;
  prefer: "condition" | "measure" | null;
  uses: "condition" | "measure" | null;
  definition: { id: string; document: { id: string; title: string | null } } | null;
  connection: { id: string; name: string };
  model_version: { id: string; version_no: number };
  decided_by: { id: string; display_name: string | null } | null;
  decided_at: string | null;
  note: string | null;
  created_at: string;
  updated_at: string;
}

export interface ConditionList {
  items: NamedCondition[];
  counts: { proposed: number; approved: number; conflicts: number };
}
