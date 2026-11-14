import { z } from "zod";

/**
 * Request bodies as Zod schemas (US-135). Each mirrors an OpenAPI request model (named in its comment) with the same
 * limits; `schemas.test.ts` fails if one drifts from `openapi.json`. Messages say how to fix the value, not just that it
 * is wrong (design system: Field). Forms build their own schemas from these fields so limits stay in one place.
 */

export const required = (what: string, max: number, min = 1) =>
  z
    .string()
    .min(min, min === 1 ? `Enter ${what}.` : `Enter ${what} of at least ${min} characters.`)
    .max(max, `Keep ${what} to ${max} characters or fewer.`);

// ConnectionIn — one model for every engine; the engine decides which fields are required (US-115).
export const TLS_MODES = ["disable", "require", "verify-ca", "verify-full"] as const;
export const ENGINES = ["postgres", "mysql", "sqlserver", "mongodb", "dataverse"] as const;
export const READ_PREFERENCES = ["primary", "primaryPreferred", "secondary", "secondaryPreferred"] as const;
export const GUID = /^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$/;
const optional = (what: string, max: number) => required(what, max).optional();
// SshIn — a tunnel through an SSH server (ENH-003). The key file's text and the passwords are write-only.
export const SSH_HOST = /^[A-Za-z0-9.:-]+$/;
export const SSH_KEY_MAX = 16384;
const HostCheck = {
  verify: z.enum(["pin", "ca"]).optional(),
  host_ca: z.string().min(1, "Paste the CA's public key.").max(4096, "Paste one public key line.").optional(),
  repin: z.boolean().optional(),
};
const UUID = /^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$/;
// JumpIn — a host in front of the SSH server (ENH-004).
export const JumpIn = z.object({
  ...HostCheck,
  id: z.string().regex(/^[a-z0-9]{1,16}$/).optional(),
  host: required("the jump host", 255).regex(SSH_HOST, "Enter a host name or IP address."),
  port: z.number().int().min(1, "Enter a port between 1 and 65535.").max(65535, "Enter a port between 1 and 65535.").optional(),
  username: required("the user", 64),
  auth: z.enum(["library", "password"]).optional(),
  key_id: z.string().regex(UUID, "Choose a key from the library.").optional(),
  password: optional("the password", 1024),
});
export const SshIn = z.object({
  ...HostCheck,
  enabled: z.boolean().optional(),
  host: required("the SSH server", 255).regex(SSH_HOST, "Enter a host name or IP address, for example bastion.example.com.").optional(),
  port: z.number().int().min(1, "Enter a port between 1 and 65535.").max(65535, "Enter a port between 1 and 65535.").optional(),
  username: optional("the SSH user", 64),
  auth: z.enum(["key", "password"]).optional(),
  private_key: optional("the private key file", SSH_KEY_MAX),
  passphrase: z.string().max(1024, "Keep the passphrase to 1024 characters or fewer.").optional(),
  password: optional("the SSH password", 1024),
  key_source: z.enum(["upload", "library"]).optional(),
  key_id: z.string().regex(UUID, "Choose a key from the library.").optional(),
  certificate: z.string().max(SSH_KEY_MAX, "This file is too large to be a certificate.").optional(),
  jump: z.array(JumpIn).max(3, "Use at most 3 jump hosts.").optional(),
});
// SshKeyIn and SshKeyPatch — the SSH key library (ENH-004).
export const SshKeyIn = z.object({
  name: required("a name", 80),
  private_key: required("the private key file", SSH_KEY_MAX),
  passphrase: z.string().max(1024).optional(),
  certificate: z.string().max(SSH_KEY_MAX).optional(),
});
export const SshKeyPatch = z.object({
  name: optional("a name", 80),
  private_key: optional("the private key file", SSH_KEY_MAX),
  passphrase: z.string().max(1024).optional(),
  certificate: z.string().max(SSH_KEY_MAX).optional(),
});
export const ConnectionIn = z.object({
  name: required("a name", 80),
  engine: z.string(),
  host: optional("the host", 255),
  port: z.number().int().min(1, "Enter a port between 1 and 65535.").max(65535, "Enter a port between 1 and 65535.").optional(),
  database: optional("the database name", 128),
  username: optional("the read-only user", 128),
  password: optional("the password", 1024),
  tls: z.object({ mode: z.enum(TLS_MODES).optional(), trust_server_certificate: z.boolean().optional() }).optional(),
  approved_schemas: z.array(z.string()).min(1, "Name at least one schema, for example public.").max(50, "Approve at most 50 schemas.").optional(),
  uri: z.string().max(1024, "Keep the connection string to 1024 characters or fewer.").optional(),
  read_preference: z.enum(READ_PREFERENCES).optional(),
  environment_url: z.string().max(255, "Keep the environment URL to 255 characters or fewer.").optional(),
  tenant_id: z.string().regex(GUID, "Enter the directory (tenant) ID, a GUID.").optional(),
  client_id: z.string().regex(GUID, "Enter the application (client) ID, a GUID.").optional(),
  client_secret: optional("the client secret", 2048),
  mode: z.enum(["tds", "webapi"]).optional(),
  tables: z.array(z.string()).max(200, "Choose at most 200 tables.").optional(),
  ssh: SshIn.optional(),
});

// MappingIn (inside MappingsPut)
export const MappingIn = z.object({
  idp_group_id: required("the group", 200),
  role: z.string().min(2, "Choose a role.").max(40, "Choose a role."),
  scope_attributes: z.record(z.string(), z.array(z.string())).optional(),
});
export const MappingsPut = z.object({ mappings: z.array(MappingIn).max(500, "A tenant can have at most 500 mappings.") });

// GroupIn (identity admin router)
export const GroupIn = z.object({
  external_group_id: required("the group object id", 200),
  display_name: required("a display name", 200),
});

// Predicate and ScopePolicyIn
export const PREDICATE_OPS = ["=", "in", "not in", "is null"] as const;
export const Predicate = z.object({
  field: z.string(),
  op: z.enum(PREDICATE_OPS),
  values: z.array(z.string()).nullable().optional(),
  attr: z.string().nullable().optional(),
});
export const ScopePolicyIn = z.object({
  connection_id: z.string(),
  entity: required("the entity", 128),
  role: z.string().min(2, "Choose a role.").max(40, "Choose a role."),
  all_rows: z.boolean().optional(),
  predicates: z.array(Predicate).optional(),
  description: required("what the scope chip should say", 300),
});
/** The same policy for several entities and roles at once (US-172). */
export const ScopePolicyBulkIn = z.object({
  connection_id: z.string(),
  entities: z.array(z.string().min(1).max(128)).min(1, "Choose at least one entity.").max(500, "Choose 500 entities or fewer."),
  roles: z.array(z.string().min(2).max(40)).min(1, "Choose at least one role.").max(50, "Choose 50 roles or fewer."),
  all_rows: z.boolean().optional(),
  predicates: z.array(Predicate).optional(),
  description: required("what the scope chip should say", 300),
});

// FieldRuleIn and FieldPoliciesPut
export const FIELD_REF = /^(field:)?[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*$/;
export const FieldRuleIn = z.object({
  field_ref: z.string().regex(FIELD_REF, "Write the field as entity.field, for example projects.approved_budget_value_usd."),
  hidden_for: z.array(z.string()).min(1, "Choose at least one role."),
  effect: z.enum(["hide", "mask"]).optional(),
});
export const FieldPoliciesPut = z.object({ connection_id: z.string(), rules: z.array(FieldRuleIn).max(1000) });

// ShareIn
export const ShareIn = z.object({
  object: z.object({ kind: z.enum(["widget", "report", "dashboard"]), id: z.string().max(64) }),
  with_user_ids: z.array(z.string()).min(1, "Add at least one colleague.").max(50, "Share with at most 50 people at a time."),
  permission: z.enum(["view", "edit"]).optional(),
});

// PinIn
export const PinIn = z.object({
  from_turn_id: z.string(),
  title: required("a title", 200),
  visual: z.string().min(1, "Choose a visual.").max(40),
  target: z.object({ report_id: z.string(), section_id: z.string().nullable().optional() }).nullable().optional(),
  follow_page_filters: z.boolean().optional(),
  ops: z.array(z.object({ op: z.string().max(30) }).catchall(z.unknown())).max(4).nullable().optional(),
});

// PublishIn
export const PublishIn = z.object({
  target_roles: z.array(z.string()).max(20, "Publish to at most 20 roles at a time.").optional(),
  note: z.string().max(500, "Keep the release note to 500 characters or fewer.").nullable().optional(),
});

// DeclineIn (return a publish request to its Creator, US-142)
export const DeclineIn = z.object({ note: required("a note for the requester", 500) });

// ShareCheckIn (per-person warnings before sharing, US-142)
export const ShareCheckIn = ShareIn.omit({ permission: true });

// tela_api__content__fields__GroupIn and FieldIn (personal fields, US-078)
export const FieldGroupIn = z.object({
  label: required("a group name", 80),
  values: z.array(z.union([z.string(), z.number()])).min(1, "Pick at least one value for this group.").max(500, "A group can hold at most 500 values."),
});
export const FIELD_KINDS = ["grouping", "band", "join_measure"] as const;
export const AGGS = ["count", "count_distinct", "sum", "avg", "min", "max"] as const;
export const FieldIn = z.object({
  kind: z.enum(FIELD_KINDS),
  name: required("a name", 120),
  connection_id: z.string(),
  source_ref: z.string().max(140).nullable().optional(),
  groups: z.array(FieldGroupIn).max(50, "Use at most 50 groups.").optional(),
  catch_all_label: required("a name for everything else", 80).optional(),
  not_set_label: required("a name for blank values", 80).optional(),
  case_sensitive: z.boolean().optional(),
  edges: z.array(z.number()).max(51, "Use at most 51 edges.").optional(),
  labels: z.array(z.string()).max(50).optional(),
  open_ended_top: z.boolean().optional(),
  path: z.array(z.string()).max(5, "A path can cross at most 5 tables.").optional(),
  agg: z.enum(AGGS).optional(),
  target: z.string().max(140).nullable().optional(),
});

// ItemRef and RequestIn (request certification, US-081)
export const RequestIn = z.object({
  item: z.object({ kind: z.enum(["grouping", "band", "join_measure", "custom_sql", "freehand"]), id: z.string() }),
  note: z.string().max(1000, "Keep the note to 1,000 characters or fewer.").nullable().optional(),
});

// DecisionIn (certification decision, US-086)
export const DecisionIn = z.object({
  decision: z.enum(["certify", "merge", "reject", "request_changes"]),
  // Approve of a free-hand rule names the certified measure it becomes (US-166).
  measure: z.object({
    key: z.string().regex(/^[a-z][a-z0-9_]{1,62}$/, "Use lower-case letters, digits and underscores, starting with a letter."),
    label: z.string().min(2, "Give the measure a name.").max(120, "Keep the name to 120 characters or fewer."),
    definition: z.string().min(3, "Say in one line what it shows.").max(300, "Keep the definition to 300 characters or fewer."),
    plain_rule: z.string().min(3, "Write the rule in plain words.").max(500, "Keep the rule to 500 characters or fewer."),
  }).nullable().optional(),
  merge_into: z.string().regex(/^(measure|dimension):[a-z_][a-z0-9_]{0,62}$/, "Choose the certified field to merge into.").nullable().optional(),
  note: z.string().max(1000, "Keep the note to 1,000 characters or fewer.").nullable().optional(),
  confirm_different: z.boolean().optional(),
  reviewed_changes: z.boolean().optional(),
});

// SqlIn, DraftIn and SaveIn (custom SQL, US-080)
export const SqlIn = z.object({ connection_id: z.string(), sql: required("a SELECT statement", 20000) });
export const DraftIn = z.object({ connection_id: z.string(), question: required("what the SQL should answer", 1000) });
export const SaveIn = SqlIn.extend({
  check_token: z.string().min(10).max(2000),
  page_filters: z.array(z.record(z.string(), z.unknown())).max(20).optional(),
  title: required("a title", 200),
  visual: z.string().max(40).optional(),
  follow_page_filters: z.boolean().optional(),
  drafted_by_ai: z.boolean().optional(),
  report_id: z.string().nullable().optional(),
  section_id: z.string().nullable().optional(),
});

// GoldIn (gold examples, US-056)
export const GoldIn = z.object({
  question: required("the question", 500, 3),
  expected_spec: z.record(z.string(), z.unknown()),
  connection_id: z.string().max(64),
  tags: z.array(z.string()).max(12, "Use at most 12 tags.").optional(),
  use_for_fewshot: z.boolean().optional(),
  notes: z.string().max(1000, "Keep the note to 1,000 characters or fewer.").nullable().optional(),
  test_scope_role_id: z.string().max(64).nullable().optional(),
  expected_result_fingerprint: z.record(z.string(), z.unknown()).nullable().optional(),
  turn_id: z.string().max(64).nullable().optional(),
});

/** Which OpenAPI component each schema mirrors; read by the drift test. */

export const REQUEST_SCHEMAS = {
  ConnectionIn,
  MappingsPut,
  MappingIn,
  tela_api__identity__admin_router__GroupIn: GroupIn,
  ScopePolicyIn,
  ScopePolicyBulkIn,
  Predicate,
  FieldPoliciesPut,
  FieldRuleIn,
  ShareIn,
  PinIn,
  PublishIn,
  DeclineIn,
  ShareCheckIn,
  tela_api__content__fields__GroupIn: FieldGroupIn,
  FieldIn,
  RequestIn,
  DecisionIn,
  SqlIn,
  DraftIn,
  SaveIn,
  GoldIn,
  SshKeyIn,
  SshKeyPatch,
} as const;
