import { api } from "./client";
/** Response shapes of the P1 read path (Home, Ask, viewer, connections, model). Hand-written to the API. */
import type { FieldDraft } from "./govern";
import type { ChartSpec, QuerySpec } from "./querySpec";

export interface Column {
  name: string;
  kind: "dimension" | "period" | "measure" | "currency";
  label: string;
  ref: string;
  data_type: string;
  format: string | null;
  /** A derived figure or share of the total (US-176): the measure column it is worked out from. */
  of?: string;
}

export interface Definition {
  ref: string;
  label: string;
  plain_rule: string;
  blank_handling: string;
  rule_ref: string | null;
  format: string;
}

export interface Viz {
  visual: ChartSpec["type"] | null;
  alternatives: string[];
  disabled: { visual: string; suitable: boolean; reason: string | null }[];
  chart_spec: ChartSpec | null;
  note: string | null;
}

export interface QueryResult {
  columns: Column[];
  rows: unknown[][];
  row_count: number;
  truncated: boolean;
  as_of: string | null;
  hidden: boolean;
  message?: string;
  notes?: string[];
  scope: { summary: string; locked: boolean };
  definitions: Definition[];
  model_version: { id: string; version_no: number | null };
  connection_id: string;
  cache_hit?: boolean;
}

/** KPI extras on a single-figure widget run (US-139): computed by the server, shown as returned (COMP-001). */
export interface Kpi {
  value: number;
  format: string | null;
  change: { previous: number; change: number; change_pct: number | null; previous_range: string } | null;
  sparkline: { grain: "month"; by: string; range: string; points: { period: string; value: number | null }[] } | null;
  /** From the widget's threshold (US-157); the rule says it in words. */
  tone?: KpiTone | null;
  /** The measure's one-line definition (US-156). */
  definition?: string | null;
}

export interface KpiTone {
  tone: "good" | "warning" | "critical";
  label: string;
  rule: string;
}

export interface WidgetRun extends QueryResult {
  viz: Viz;
  widget: { id: string; title: string };
  badges: string[];
  filters_applied: string[];
  filters_not_applied: string[];
  kpi?: Kpi | null;
  /** A detail table (US-158): the columns it shows (the key comes first in `columns`, for the record drawer). */
  detail?: DetailRun | null;
  notes?: string[];
  /** Fields a reader can filter this widget by, from inside it (ENH-023 US-221). */
  controls?: { ref: string; label: string }[];
  /** The Creator's fixed filters on this widget, always applied. */
  fixed_filters?: { ref: string; label: string; op: string | null; values: unknown[] }[];
}

export interface DetailRun {
  columns: string[];
  sort: { field: string; dir: "asc" | "desc" } | null;
  export_name: string | null;
  entity: string;
  primary_key: string[];
}

/** Report text blocks (US-158): a note anywhere, a callout (title, info or warning) and a footnote at the end. */
export type TextKind = "text_note" | "callout" | "footnote";
export type CalloutLevel = "info" | "warning";

export interface StarterCallout {
  kind: "callout";
  title: string | null;
  text: string;
  level: CalloutLevel;
}

export interface Tile {
  id: string;
  type: "report" | "dashboard";
  title: string;
  badges: string[];
  version: number | null;
  published?: boolean;
  count: { tabs?: number; widgets?: number };
  last_edited_at: string;
  /** Owner's name; "Tela starter" for starter content (US-138). */
  owner?: string | null;
}

export interface Home {
  greeting: { name: string; scopes: { connection_id: string; connection_name: string; text: string }[]; as_of: string | null };
  shelves: { starter: Tile[]; published_to_my_role: Tile[]; mine?: Tile[]; shared_with_me: Tile[] };
  empty_text: Record<string, string>;
  can_create: boolean;
  /** Questions built from certified measures the person can read (US-138); empty without Ask. */
  suggested_questions?: string[];
}

export interface ConversationItem {
  id: string;
  title: string | null;
  updated_at: string;
  turns: number;
}

export interface PageFilter {
  ref: string;
  op: "=" | "in";
  values: (string | number)[];
}

export interface DashboardWidget {
  id: string;
  connection_id?: string;
  model_version_id?: string;
  title: string;
  visual: string;
  placement: { x: number; y: number; w: number; h: number };
  follow_page_filters: boolean;
  hidden: boolean;
  hidden_message: string | null;
  badges: string[];
  measure: string;
}

export interface DashboardItem extends Partial<Omit<DashboardWidget, "title">> {
  kind: "widget" | TextKind;
  item_id: string;
  text?: string;
  /** A widget's title, or a callout's (may be null). */
  title?: string | null;
  level?: CalloutLevel;
  placement: { x: number; y: number; w: number; h: number };
  connection_id?: string;
  model_version_id?: string;
}

export interface Dashboard {
  id: string;
  title: string;
  kind: string;
  layout: "tabs" | "grouped";
  version: number | null;
  version_id?: string | null;
  draft?: boolean;
  connection_id: string | null;
  connection_ids?: string[];
  model_version_id: string | null;
  locked_scope_chip: string;
  scope_message: string;
  as_of: string | null;
  /** The reporting date the model's rules count from (US-158 "Data as of"); null when tabs differ or it is unknown. */
  reporting_date?: string | null;
  filters: { ref: string; label: string; connection_id?: string }[];
  tabs: {
    id: string;
    title: string;
    group_title?: string | null;
    /** "starter", plus "deleted" when an Admin deleted the starter report a live version still shows (RULE-017). */
    badges?: string[];
    /** A starter dashboard tab's pack-owned report, which Creators can copy (REQ-114). */
    report_id?: string | null;
    /** A starter tab's callouts (above its widgets) and footnotes (below them). */
    callouts?: StarterCallout[];
    footnotes?: string[];
    reporting_date?: string | null;
    widgets: DashboardWidget[];
    sections?: { id: string; title: string; kind: string; collapsed: boolean; items: DashboardItem[] }[];
  }[];
}

export interface DefinitionPopover {
  ref: string;
  kind: "measure" | "dimension";
  label: string;
  rule_text: string;
  rule_ref?: string | null;
  fields_used: string[];
  blank_handling: string;
  badge_text: string;
  in_scope_text: string;
  can_show_query: boolean;
}

export interface Records {
  columns: Column[];
  rows: unknown[][];
  total: number;
  offset: number;
  next_offset: number | null;
  notice: string | null;
  hidden?: boolean;
  message?: string;
}

export interface Chip {
  kind: string;
  label: string;
  ref?: string;
  freehand?: boolean;
}

/** One condition of a free-hand rule (US-165), at ``path`` in the rule; the person can change it. */
export interface RuleChip extends Chip {
  kind: "rule";
  path: string;
  field: string;
  field_label: string;
  field_type: string | null;
  op: string;
  value: unknown;
}

/** A free-hand answer's rule (ENH-008): never certified, always shown in words. */
export interface FreehandRule {
  label: string;
  plain_rule: string;
  entity: string;
  chips: RuleChip[];
}

/** US-204: the question's criteria, or an approved business definition (US-203), in place of a measure's rule. */
export interface CountedBy {
  source: "question" | "definition";
  text: string;
  rule: string;
  measure: { ref: string; label: string; plain_rule: string };
  definition: {
    id: string; term: string; meaning: string; document: { id: string; title: string | null };
    chunk_id: string | null; passage: string | null;
  } | null;
}

/** The other answer shown beside this one: the person's rule beside the certified measure, or the reverse (D1). */
export interface BesideAnswer {
  kind: "freehand" | "certified";
  label: string;
  plain_rule: string;
  value?: number | null;
  format?: string | null;
  certified?: boolean;
  ref?: string;
  chips?: RuleChip[];
  error?: string;
}

/** A fact of the insight engine (US-185): numbers computed in the API; `members` are aggregated labels, never rows. */
export interface InsightFact {
  id: string;
  type: "concentration" | "outlier" | "change" | "trend" | "break" | "driver" | "distribution" | "gap" | "data_note" | "summary";
  priority: number;
  text: string;
  numbers: Record<string, unknown>;
  members: string[];
  /** On the driver fact: the dimension its members belong to (US-186). */
  dimension?: { ref: string; label: string };
}

/** A sentence of the answer that cites the facts it uses (AG-08 v2, US-186). */
export interface CitedSentence {
  text: string;
  facts: string[];
  source: "llm" | "template";
}

/** "Ask next" (US-186): a follow-up built from the spec, applied as refinement chip ops. */
export interface FollowUp {
  kind: "driver" | "compare" | "drill";
  label: string;
  ops: Record<string, unknown>[];
}

/** A data note on the card: an insight data note (US-185, `f…`) or a sanity check (US-183, `s…`) that may offer a refinement. */
export interface DataNote {
  text: string;
  fact: string;
  kind?: "empty" | "single_row" | "filters_no_effect" | "filtered_above_total" | "fan_out" | "excluded";
  action?: { label: string; ops: Record<string, unknown>[] };
}

/** The `narrative` event (US-050; v2 US-186). */
export interface Narrative {
  headline: string;
  sentences?: { text: string }[];
  headline_facts?: string[];
  insights?: InsightFact[];
  cited?: CitedSentence[];
  notes?: DataNote[];
  driver?: InsightFact | null;
  next?: FollowUp[];
  source?: "llm" | "template" | "mixed";
}

export interface TurnComplete {
  turn_id: string;
  /** The source's Ask answer mode this answer was made in (US-256). */
  ask_mode?: AskMode;
  certified?: boolean;
  scope_text?: string;
  filters_text?: string;
  as_of?: string | null;
  interpreted?: string[];
  columns?: Column[];
  rows?: unknown[][];
  row_count?: number;
  truncated?: boolean;
  notes?: string[];
  definitions?: Definition[];
  spec?: QuerySpec;
  connection?: string;
  connection_id?: string;
  model_version?: number;
  hidden?: boolean;
  message?: string | null;
  definition?: {
    ref: string; label: string; rule_text: string; rule_ref: string | null; blank_handling: string;
    /** Approved knowledge the explanation cites (US-103). */
    citations?: { chunk_id: string; document_id: string; kind: string; title: string; snippet: string }[];
  };
  /** Knowledge search fell back to keywords (FEAT-014 A2). */
  limited_context?: boolean;
  chips?: Chip[];
  can_pin?: boolean;
  refined_from?: string | null;
  undo_of?: string | null;
  changes?: string[];
  comparison?: { previous: number; current: number; change: number; change_pct: number | null; previous_range: string; text: string } | null;
  widget?: { id: string; title: string };
  action?: { kind: "plan_report"; request: string } | ({ kind: "build_field" } & FieldDraft);
  badge?: string;
  /** Free-hand answers (US-165): the rule, the answer beside it, and no export until certified. */
  rule?: FreehandRule | null;
  beside?: BesideAnswer | null;
  can_export?: boolean;
  /** What the answer was counted by instead of a certified measure's rule (US-204): the question's or an approved definition. */
  counted_by?: CountedBy | null;
  /** How sure Ask is of its reading (US-182): one score against the person's threshold, and why. */
  confidence?: AnswerConfidence;
  /** A multi-step plan's steps in order, the last being the answer (US-179); empty for one step. */
  steps?: PlanStep[];
  /** The analysis recipe the plan was filled from (US-179). */
  recipe?: { id: string; title: string; chart: string; slots: Record<string, string>; insights: string[] } | null;
  /** Plan B (US-254): the conditions the answer used, where each came from, and whether it is certified (W5). */
  fields?: FieldsAnswer;
  /** Direct SQL (US-257): the SQL the model wrote, checked and scoped; never certified or publishable. */
  sql?: string | null;
  shape?: string;
  repaired?: boolean;
  publishable?: boolean;
  publish_note?: string;
  conditions?: { term: string; sql: string | null; rule: string | null }[];
  /** Direct SQL with no rows: the filter values the data doesn't hold, and some it does (run 01a1ce59). */
  value_notes?: string[];
  /** Direct SQL: what the answer relied on — data date, tables, the model's business rules, session memory. */
  context?: AnswerContext;
}

export interface AnswerContext {
  data_date?: string;
  data_date_set?: boolean;
  model_version?: number | null;
  tables?: string[];
  rules?: { term: string; sql: string | null; always: boolean; origin: string; cite: string | null }[];
  rules_in_model?: number;
  prompt_chars?: number;
  previous_question?: string | null;
  follow_up?: string;
  turns_in_conversation?: number;
  /** Asked on a dashboard: which one, how many of its widgets were read and the widget asked about. */
  /** Tables whose 5 sample rows the model saw; earlier turns (with their SQL) a follow-up was read with. */
  samples?: string[];
  earlier_turns?: number;
  dashboard?: { dashboard: string; widgets: number; page_filters: number; about_widget: string | null } | null;
}

export interface FieldsCondition {
  phrase: string;
  term?: string;
  rule: string | null;
  source: string;
  source_label: string;
  how: string;
  score: number;
}

export interface FieldsAnswer {
  certified: boolean;
  certified_by: string | null;
  not_certified_because: string | null;
  conditions_used: FieldsCondition[];
}

export interface PlanStep {
  step: number;
  text: string;
  row_count: number | null;
  value?: unknown;
  values_count?: number | null;
}

export interface AnswerConfidence {
  score: number;
  threshold: number;
  verbosity: string;
  why: string[];
  reasoning?: string | null;
  signals?: Record<string, unknown>;
}

export interface Clarify {
  key: string;
  /** "value": which stored value a word means (US-173); "reading": which whole reading (US-182); else which measure. */
  kind?: "value" | "reading";
  /** Why Ask is unsure (US-182). */
  why?: string[];
  question: string;
  options: { id: string; label: string; measure?: string; value?: string }[];
}

export interface ConnectionItem {
  id: string;
  name: string;
  engine: string;
  status: string;
  descriptor: Record<string, unknown>;
  secret_set: boolean;
  read_only: { verified_at: string | null; verdict?: string; reasons?: string[]; checks?: string[] };
  freshness: Record<string, unknown>;
  model: { id: string | null; version_no: number | null; status: string | null };
  data_as_of: string | null;
  etag: string;
  /** How Ask answers for this source (US-256). */
  ask_mode?: AskMode;
}

/** Ask answer modes (ENH-028, DEC-29; US-256). */
export type AskMode = "certified" | "fields" | "hybrid" | "direct_sql";

/** A mode's latest finished gold run on this source. */
export interface AskModeScore {
  run_id: string;
  run_at: string;
  plan_only: boolean;
  tags: string[];
  total: number;
  right: number;
  confident_wrong: number;
  clarified: number;
  refused: number;
  median_ms: number | null;
}

export interface AskModeChoice {
  mode: AskMode;
  label: string;
  note: string;
  default: boolean;
  /** Why it can't be chosen for this source; null when it can. */
  unavailable: string | null;
  score: AskModeScore | null;
}

/** `GET/PUT /connections/{id}/ask-mode`. */
export interface AskModeInfo {
  connection_id: string;
  connection: string;
  engine: string;
  mode: AskMode;
  /** The mode Ask actually runs (the stored one, or certified when it isn't available). */
  answers_with: AskMode;
  fallback_reason: string | null;
  default: AskMode;
  choices: AskModeChoice[];
}

export interface TestResult {
  reachable: boolean;
  authenticated: boolean;
  read_only: { verdict: string; checks: string[]; reasons: string[] };
  latency_ms: number | null;
  server_version: string | null;
  status: string;
  message: string | null;
  /** Dataverse: the TDS endpoint is off, the Web API works (US-111). */
  suggest_mode?: "webapi" | null;
  /** SSH tunnel (ENH-003): which step failed, and the server's host key (pinned by this test when `pinned`). */
  ssh?: { ok: boolean; stage: "connect" | "host_key" | "auth" | "forward" | null; error: string | null; host_key: string | null; pinned?: boolean;
    /** ENH-004: the failing hop ("" = the SSH server, else a jump host id) and each hop reached, in order. */
    hop?: string | null; pinned_hops?: string[];
    hops?: { id: string; host: string; port: number; host_key: string | null; verified: "pin" | "ca" | "new" | null }[] } | null;
}

/** An OpenSSH user certificate as the API shows it (ENH-004): never the certificate itself. */
export interface SshCertificate { key_id: string; principals: string[]; ca_fingerprint: string; valid_after: number; valid_before: number | null }

/** `GET /admin/ssh-keys`: the shared SSH key library (ENH-004). The key stays in the vault. */
export interface SshKeyItem {
  id: string; name: string; key_type: string; key_bits: number; fingerprint: string; certificate: SshCertificate | null;
  created_at: string; updated_at: string; used_by: { id: string; name: string; status: string }[];
}

/** `POST /connections/{id}/introspect`: structure only, never data values (US-030). */
export interface IntrospectResult {
  status: "connected" | "incomplete";
  objects?: number;
  diff?: { added: string[]; removed: string[]; changed: string[] };
  /** Entities of the approved model whose columns changed or disappeared; empty when nothing broke or there is no model. */
  model_broken?: { entity: string; problem: string }[];
  message?: string;
  error?: { code: string; message: string };
}

/** `GET /connections/{id}/objects`: tables and views read from the source (names, types and keys). */
export interface ConnectionObjects {
  items: {
    object: string;
    kind: string;
    columns: { name: string; type: string; nullable: boolean }[];
    keys: unknown;
    row_estimate: number | null;
    allowed: boolean;
    /** False when the source account may not read the rows (Dataverse role without Read); null when not checked. */
    readable: boolean | null;
    removed: boolean;
  }[];
  introspected_at: string | null;
  schema_checksum: string | null;
}

export interface PackMatch {
  pack_id: string;
  pack_key: string;
  version: string;
  /** The pack's name, e.g. "PMO starter pack (TPG)"; may be missing from older answers. */
  title?: string | null;
  confidence: number;
  matched: boolean;
  missing_objects: string[];
}

/** The Admin's mapping of a pack to a source with other names (US-154). `null` = "not in this source". */
export interface PackMapping {
  entities: Record<string, string | null>;
  fields: Record<string, string | null>;
  values: Record<string, Record<string, string>>;
}

/** How a pack field or table was found: by name, Dataverse label column, the model's labels, or the Admin. */
export type PackFoundBy = "exact" | "dataverse_label" | "model_label" | "similar_name" | "mapped" | "not_in_source" | "missing" | "column_taken";

export interface PackMappingField {
  key: string;
  label: string;
  data_type: string;
  semantic_type: string | null;
  required: boolean;
  column: string | null;
  how: PackFoundBy;
  /** Present on status-like fields: the values the pack's rules speak of, and the source → pack value map. */
  values?: string[];
  value_map?: Record<string, string>;
  source_values_ref?: { model_version_id: string; ref: string };
  /** The mapped column's most frequent values with their row counts (US-199). */
  source_values?: { value: string | null; rows: number }[];
  /** A column suggested by its values (US-200), for a field the names left unmapped or whose values don't fit. */
  suggestion?: PackValueSuggestion;
}

/** A column whose values match a pack field's (US-200): never applied until the Admin picks it. */
export interface PackValueSuggestion {
  column: string;
  confidence: number;
  reason: "unmapped" | "values_dont_fit";
  evidence: string;
  alternatives: string[];
}

/** A column the tenant's model suggests (US-201), with its reason; `flags` are what US-199's guards would refuse. */
export interface PackAiSuggestion {
  column: string | null;
  confidence: number;
  reason: string;
  why: "unmapped" | "values_dont_fit";
  source: "ai";
  flags: string[];
}

/** `POST /starter-packs/{id}/ai-suggestions` (US-201): nothing is applied; `available` is false with no model. */
export interface PackAiSuggestions {
  available: boolean;
  message: string | null;
  suggestions: Record<string, PackAiSuggestion>;
  tables: { entity: string; asked: number; status: "ok" | "failed"; suggested?: number; code?: string }[];
  dropped?: number;
}

/** A mapping guard's warning (US-199): rule values not in the column, a measure returning 0, or the same figure. */
export interface GuardWarning {
  kind: "values" | "zero" | "same_figure";
  ref: string;
  issue: string;
  missing?: string[];
  measures?: string[];
}

/** `POST /starter-packs/{id}/check` (US-199): a trial figure of each bound pack measure. */
export interface PackMeasureCheck {
  measures: { key: string; label: string; value: number | string | null }[];
  warnings: GuardWarning[];
}

/** `GET /models/{id}/draft/checks` (US-199): the mapping guards approval runs. */
export interface DraftChecks {
  draft_etag: string;
  blocking: { ref: string; issue: string }[];
  warnings: GuardWarning[];
}

export interface PackMappingEntity {
  key: string;
  label: string;
  object: string | null;
  how: PackFoundBy;
  confidence: number;
  primary: boolean;
  fields: PackMappingField[];
  columns: { name: string; type: string | null }[];
}

/** A model field under another key that reads a pack field's column (BUG-016). */
export interface SameColumn { entity: string; column: string; mine: string; mine_label: string; pack: string; pack_label: string }

export interface PackClash {
  kind: "entity" | "field" | "measure" | "dimension";
  ref: string;
  title: string;
  mine: string;
  pack: string;
  choice: "mine" | "pack";
}

export interface PackUnavailable {
  kind: string;
  key: string;
  title: string;
  gaps: string[];
}

/** `POST /starter-packs/{id}/preview` (US-153): what applying the pack to this connection would do. */
export interface PackPreview {
  pack_id: string;
  pack: string;
  version: string;
  confidence: number;
  matched: boolean;
  model_exists: boolean;
  installed_pack_id: string | null;
  mapping: PackMapping;
  mapping_step: { objects: string[]; entities: PackMappingEntity[] };
  /** Mapping guards (US-199): fields sharing a column block install; values missing from a column are warned. */
  checks: { blocking: { ref: string; message: string }[]; warnings: GuardWarning[]; values_read: boolean; suggestions?: number; ai_available?: boolean };
  added: { entities: number; fields: number; measures: number; dimensions: number; links: number };
  kept_own: { entities: number; measures: number; dimensions: number };
  clashes: PackClash[];
  same_source: { pack_entity: string; model_entity: string; source_object: string }[];
  folded: SameColumn[];
  same_column: SameColumn[];
  reports: { available: number; unavailable: number };
  unavailable: PackUnavailable[];
}

export interface PackInstallResult {
  model_id: string;
  mode: "new" | "merge";
  installed: { measures: number; reports: number };
  unavailable: PackUnavailable[];
}

export interface DraftModel {
  model_id: string;
  draft_version_id: string;
  draft_etag: string;
  entities: {
    key: string;
    label: string;
    source_object: string;
    description?: string | null;
    primary_key?: string[];
    fields: { key: string; label: string; source_column?: string; description?: string | null; data_type: string; sensitivity: string }[];
  }[];
  links: { from_entity: string; to_entity: string; from_fields: string[]; to_fields: string[]; cardinality: string; origin: string; accepted: boolean }[];
  measures: { key: string; label: string; entity: string; plain_rule: string; rule_ref?: string | null; status?: string }[];
  dimensions: { key: string; label: string; entity: string; field: string }[];
  settings: Record<string, string>;
  open_questions: { id: string; question: string; options: string[]; status: string; blocking: boolean; answer: string | null }[];
  issues: { ref: string; issue: string }[];
  unaccepted_links: unknown[];
  /** Tables read from the source that no entity uses yet (BUG-018). */
  unmodelled_objects?: string[];
  /** Columns of modelled tables that no field reads yet, e.g. added to the source later (BUG-028). */
  unmodelled_columns?: { entity: string; object: string; columns: string[] }[];
}

export interface PatchOp {
  index: number;
  op: string;
  valid: boolean;
  target: string;
  before: string;
  after: string;
  detail: Record<string, unknown>;
}

export interface Patch {
  patch_id: string;
  kind: "proposal" | "correction";
  source: "llm" | "rules";
  ops: PatchOp[];
  dropped: { op: string; error: string }[];
  questions: { question: string; options: string[]; clarify?: boolean; target?: string }[];
  affected: string[];
  /** US-245: with LLM review on, what AG-04 made of the rules' patch. */
  review?: { verdict: "confirm" | "correct" | "ask" | "fallback"; reason: string; rules_left_out?: unknown[] };
}

interface ChangeItem { key: string; label: string }
interface Changed extends ChangeItem { what: string[]; before?: unknown; after?: unknown }

/** `GET /models/{id}/draft/impact` (US-144): what approving the draft would change, before approval. */
export interface DraftImpact {
  draft_etag: string;
  base_version: number | null;
  changes: {
    entities: { added: ChangeItem[]; removed: ChangeItem[] };
    fields: { added: ChangeItem[]; removed: ChangeItem[]; changed: Changed[] };
    measures: { added: ChangeItem[]; removed: ChangeItem[]; changed: Changed[] };
    dimensions: { added: ChangeItem[]; removed: ChangeItem[] };
    links: { added: ChangeItem[]; removed: ChangeItem[] };
  };
  breaking_changes: { measure?: string; field?: string; change: string }[];
  dashboards: { id: string; title: string; version: number; changes_at_approval: boolean; widgets: { title: string; reasons: string[]; breaks: boolean }[] }[];
  widget_count: number;
  personal_fields_broken: { id: string; label: string; owner: string; reason: string }[];
}

export interface ModelInfo {
  model_id: string;
  connection_id: string;
  current_version_id: string | null;
  draft_version_id: string | null;
  installed_pack_id: string | null;
  status: string;
  broken_reasons: unknown[];
}

export interface ModelVersion { id: string; version_no: number; status: string; change_summary: string | null; approved_at: string }

/** `GET /api/v1/admin/overview` (US-143): counts and short labels; a section is null without its screen's permission. */
export interface AdminOverview {
  generated_at: string;
  tz: string;
  today: string;
  connections: {
    total: number;
    healthy: number;
    by_status: Record<string, number>;
    attention: { id: string; name: string; status: string }[];
  } | null;
  models: {
    total: number;
    drafts: number;
    broken: number;
    items: {
      id: string;
      connection: string;
      version_no: number | null;
      approved_at: string | null;
      has_draft: boolean;
      pending_changes: number;
      open_questions: number;
      broken: boolean;
    }[];
  } | null;
  certification: { pending: number; blocks_publish: number; with_lookalikes: number; oldest_at: string | null } | null;
  publish_requests: { open: number; oldest_at: string | null; oldest_by: string | null } | null;
  ai: {
    provider: string | null;
    today: { calls: number; tokens: number; blocked: number; failed: number; estimated_cost: number };
    daily_calls: { day: string; calls: number }[];
  } | null;
  security: {
    days: number;
    since: string;
    total: number;
    by_action: Record<string, number>;
    latest: { action: string; at: string; actor: string | null } | null;
  } | null;
  jobs: {
    hours: number;
    failed: number;
    items: {
      key: "introspection" | "cache_refresh" | "exports" | "events";
      label: string;
      runs: number;
      failed: number;
      last_run_at: string | null;
      last_failure: { at: string; label: string | null } | null;
    }[];
  } | null;
  /** Value synonyms waiting for review (US-215); null without `knowledge.manage`. */
  value_synonyms?: {
    pending: number;
    pack_pending: number;
    connections: { id: string; name: string; pending: number; pack_pending: number }[];
  } | null;
}

/**
 * The published dashboard query, shared by the viewer and the app shell: the shell starts it alongside `/me` when a
 * dashboard link is opened, so the viewer does not wait for one and then the other (US-148, LCP).
 */
export function publishedDashboardQuery(id: string | undefined, draft = false) {
  return {
    queryKey: ["dashboard", id, draft] as const,
    queryFn: () => api<Dashboard>(draft ? `/api/v1/dashboards/${id}/view` : `/api/v1/published/${id}`),
    staleTime: 15_000,
  };
}

