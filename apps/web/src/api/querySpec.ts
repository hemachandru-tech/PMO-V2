// Generated from packages/query-spec (tela_query_spec). Do not edit by hand.
// Regenerate: uv run python packages/query-spec/scripts/gen_ts.py > apps/web/src/api/querySpec.ts

export interface AdhocMeasure {
  entity: string;
  agg: "count" | "count_distinct" | "sum" | "avg" | "min" | "max";
  field?: string | null;
  where?: tela_query_spec__expr__Compare | InSet | NullCheck | Check | All | Any_ | Not | null;
  label: string;
  plain_rule: string;
}

export interface AggExpr {
  agg: "count" | "count_distinct" | "sum" | "avg" | "min" | "max" | "median" | "percentile";
  field?: string | null;
  value?: DaysBetween | Calc | Calculator | null;
  p?: number | null;
  where?: Compare | InSet | NullCheck | Check | All | Any_ | Not | null;
}

export interface All {
  all: (Compare | InSet | NullCheck | Check | All | Any_ | Not)[];
}

export interface Any_ {
  any: (Compare | InSet | NullCheck | Check | All | Any_ | Not)[];
}

export interface Arith {
  op: "add" | "sub" | "mul" | "div";
  left: AggExpr | Arith | number;
  right: AggExpr | Arith | number;
  scale?: number | null;
}

export interface AsOf {
  asof_offset_days: number;
}

export interface Band {
  label: string;
  above?: number | null;
  upto?: number | null;
}

export interface Breakdown {
  ref: string;
}

export interface Bucket {
  k: number;
  measure?: string | null;
  per?: string | null;
  order?: "desc" | "asc";
}

export interface BusinessRule {
  key: string;
  term: string;
  meaning?: string;
  entity?: string | null;
  sql?: string | null;
  kind?: "condition" | "expression" | "population" | "display";
  synonyms?: string[];
  always?: boolean;
  origin?: "document" | "inferred" | "admin";
  cite?: string | null;
  status?: "ok" | "needs_review" | "off";
  issue?: string | null;
}

export interface Calc {
  calc: "add" | "sub" | "mul" | "div";
  left: DaysBetween | Calc | Calculator | number | string;
  right: DaysBetween | Calc | Calculator | number | string;
}

export interface Calculator {
  calculator: string;
  output: string;
}

export interface ChartColours {
  scheme?: "brand" | "single" | "status" | "warm" | "cool" | "grey" | "vivid";
  overrides?: Record<string, "chart-1" | "chart-2" | "chart-3" | "chart-4" | "chart-5" | "chart-6" | "chart-7" | "chart-8" | "chart-other" | "accent" | "status-ok" | "status-warn" | "status-danger">;
}

export interface Check {
  of: DaysBetween | Calc | Calculator;
  op: "=" | "!=" | "<" | "<=" | ">" | ">=" | "in" | "not_in";
  value?: string | number | boolean | null;
  values?: (string | number | boolean)[] | null;
}

export interface Cohort {
  ref: string;
  grain: "day" | "week" | "month" | "quarter" | "year";
  agg?: "min" | "max";
}

export interface Compare {
  field: string;
  op: "=" | "!=" | "<" | "<=" | ">" | ">=";
  value?: string | number | boolean | AsOf | Today | null;
  other_field?: string | null;
}

export interface DaysBetween {
  days_from: string | AsOf | Today;
  days_to: string | AsOf | Today;
}

export interface Derived {
  key: string;
  op: "ratio" | "difference" | "percent_of" | "percent_change";
  of: string;
  to: string;
  label?: string | null;
}

export interface Detail {
  columns: string[];
  sort?: DetailSort | null;
  export_name?: string | null;
}

export interface DetailSort {
  field: string;
  dir?: "asc" | "desc";
}

export interface Dimension {
  key: string;
  label: string;
  entity: string;
  field: string;
  is_temporal?: boolean;
  is_status?: boolean;
  sort_order?: string[] | null;
  value_labels?: Record<string, string>;
  synonyms?: string[];
  of?: DaysBetween | Calc | Calculator | null;
  bands?: Band[] | null;
  not_set?: string | null;
  other?: string;
  answers?: ("why" | "how_long" | "how_late" | "when")[];
}

export interface ExistsIn {
  entity: string;
  filters?: Filter[];
  measure?: string | null;
}

export interface FigureTone {
  warning?: number | null;
  critical?: number | null;
  higher_is_worse?: boolean;
}

export interface Filter {
  ref: string;
  op: "=" | "!=" | "in" | "not_in" | "<" | "<=" | ">" | ">=" | "between" | "is_null" | "not_null";
  values?: (string | number | boolean)[];
}

export interface Group {
  label: string;
  values: (string | number | boolean)[];
}

export interface Having {
  measure?: string | null;
  op: "=" | "!=" | "<" | "<=" | ">" | ">=";
  value: number;
}

export interface InSet {
  field: string;
  op: "in" | "not_in";
  values: (string | number | boolean)[];
}

export interface Measure {
  key: string;
  label: string;
  entity: string;
  expression: AggExpr | Arith;
  plain_rule: string;
  definition?: string | null;
  blank_handling?: string;
  format?: "integer" | "decimal" | "currency" | "percent";
  currency_field?: string | null;
  rule_ref?: string | null;
  synonyms?: string[];
  status?: "certified" | "deprecated" | "adhoc";
}

export interface MeasureRef {
  ref: string;
  adhoc?: AdhocMeasure | null;
}

export interface ModelEntity {
  key: string;
  label: string;
  source_object: string;
  primary_key: string[];
  source_checksum?: string | null;
  display_fields?: string[];
  description?: string | null;
  grain?: string | null;
  fields: ModelField[];
}

export interface ModelField {
  key: string;
  source_column: string;
  label: string;
  data_type: "text" | "integer" | "decimal" | "date" | "datetime" | "boolean" | "json";
  source_type?: "text" | "integer" | "decimal" | "date" | "datetime" | "boolean" | "json" | null;
  description?: string | null;
  semantic_type?: string | null;
  format?: Record<string, string | number>;
  sensitivity?: "public" | "internal" | "confidential" | "personal" | "sensitive";
  is_queryable?: boolean;
  blank_handling?: string | null;
  values?: string[];
}

export interface ModelLink {
  from_entity: string;
  to_entity: string;
  from_fields: string[];
  to_fields: string[];
  cardinality: "one_to_one" | "many_to_one" | "one_to_many";
  origin?: "foreign_key" | "proposed" | "admin" | "pack";
  accepted?: boolean;
  inherits_scope?: boolean;
}

export interface MovingAvg {
  window: number;
  measure?: string | null;
  per?: string | null;
}

export interface Not {
  not: Compare | InSet | NullCheck | Check | All | Any_ | Not;
}

export interface NullCheck {
  field: string;
  op: "is_null" | "not_null";
}

export interface PercentileOp {
  p: "p50" | "p85" | "p90";
  measure?: string | null;
  per?: string | null;
}

export interface PersonalField {
  key: string;
  kind: "grouping" | "band";
  source: string;
  groups?: Group[];
  catch_all?: string;
  not_set?: string;
  case_sensitive?: boolean;
  bands?: Band[];
  closing_max?: number | null;
}

export interface PersonalMeasure {
  key: string;
  label: string;
  entity: string;
  agg: "count" | "count_distinct" | "sum" | "avg" | "min" | "max";
  field?: string | null;
  linked_to?: string | null;
}

export interface Rank {
  measure?: string | null;
  per?: string | null;
  order?: "desc" | "asc";
}

export interface RunningTotal {
  measure?: string | null;
  per?: string | null;
}

export interface Sort {
  by: string;
  dir?: "asc" | "desc";
}

export interface TimeRange {
  relative?: "today" | "last_7_days" | "last_30_days" | "last_90_days" | "this_month" | "last_month" | "this_quarter" | "last_quarter" | "this_year" | "last_year" | "last_6_months" | "last_12_months" | "next_30_days" | "next_90_days" | null;
  from?: string | null;
  to?: string | null;
}

export interface TimeSpec {
  ref: string;
  grain?: "day" | "week" | "month" | "quarter" | "year" | null;
  range?: TimeRange | null;
}

export interface Today {
  today: true;
  offset_days?: number;
}

export interface TopNPerGroup {
  by: string;
  n: number;
  order?: "desc" | "asc";
}

export interface Visual {
  type: "figure" | "ranked_bar" | "columns" | "trend" | "paired_bars" | "stacked_status" | "donut" | "heat_grid" | "meter" | "dumbbell" | "waterfall" | "bullet" | "pareto" | "xmr" | "gantt" | "funnel" | "small_multiples" | "calendar" | "sankey" | "upset" | "beeswarm" | "ridgeline" | "survival" | "bump" | "treemap" | "waffle" | "table";
  tone?: FigureTone | null;
  note?: string | null;
  detail?: Detail | null;
  colour?: boolean | null;
  card?: "default" | "accent" | "neutral" | "good" | "warning" | "critical" | "brand" | "dark" | null;
  colours?: ChartColours | null;
  controls?: string[] | null;
}

export interface tela_query_spec__expr__Compare {
  field: string;
  op: "=" | "!=" | "<" | "<=" | ">" | ">=";
  value?: string | number | boolean | AsOf | Today | null;
  other_field?: string | null;
}

export interface tela_query_spec__spec__Compare {
  kind: "previous_period" | "same_period_last_year";
}

export interface QuerySpec {
  spec_version?: "1";
  model_version_id: string;
  connection_id: string;
  measure: MeasureRef;
  extra_measures?: MeasureRef[];
  derived?: Derived[];
  share_of_total?: string[];
  breakdowns?: Breakdown[];
  time?: TimeSpec | null;
  filters?: Filter[];
  personal_fields?: PersonalField[];
  personal_measures?: PersonalMeasure[];
  sort?: Sort[];
  limit?: number | null;
  compare?: tela_query_spec__spec__Compare | null;
  exists_in?: ExistsIn[];
  cohort?: Cohort | null;
  top_n_per_group?: TopNPerGroup | null;
  running_total?: RunningTotal | null;
  moving_avg?: MovingAvg | null;
  rank?: Rank | null;
  bucket?: Bucket | null;
  percentile?: PercentileOp | null;
  having?: Having | null;
  visual?: Visual | null;
  follow_page_filters?: boolean;
}

export interface ModelSnapshot {
  model_version_id: string;
  connection_id: string;
  version_no?: number | null;
  engine?: string;
  entities: ModelEntity[];
  links?: ModelLink[];
  measures?: Measure[];
  dimensions?: Dimension[];
  rules?: BusinessRule[];
  settings?: Record<string, string>;
  source_pin?: Record<string, unknown> | null;
}

export interface ChartSpec {
  type: "figure" | "ranked_bar" | "columns" | "trend" | "paired_bars" | "stacked_status" | "donut" | "heat_grid" | "meter" | "dumbbell" | "waterfall" | "bullet" | "pareto" | "xmr" | "gantt" | "funnel" | "small_multiples" | "calendar" | "sankey" | "upset" | "beeswarm" | "ridgeline" | "survival" | "bump" | "treemap" | "waffle" | "table";
  x?: string | null;
  series?: string[];
  stack?: string | null;
  top_n?: number | null;
  labels?: Record<string, string>;
  formats?: Record<string, string>;
  y?: string | null;
  ramp?: "rag" | "sequential" | "ordinal" | null;
  tones?: Record<string, "good" | "warning" | "critical" | "neutral">;
  meter?: Record<string, unknown> | null;
  companions?: Record<string, string>;
  figures?: Record<string, unknown> | null;
  colour?: boolean;
  card?: string | null;
  colours?: Record<string, unknown> | null;
}

export interface ResultShape {
  row_count: number;
  categories?: string[];
  dates?: string[];
  statuses?: string[];
  ordered?: string[];
  measures?: string[];
  measure_formats?: string[];
  additive?: boolean;
  non_negative?: boolean;
  distinct_counts?: Record<string, number>;
  part_within_whole?: boolean;
  companions?: Record<string, string>;
  has_target?: boolean;
  descending?: boolean;
  flags?: string[];
  daily?: boolean;
  months?: number;
}
