/** Hand-written for P0; replaced by openapi-typescript generated types when the CI diff check lands (US-004). */

export interface ScopeSummary {
  connection_id: string;
  connection_name: string;
  text: string;
}

/** How readily Ask asks a clarifying question (US-182). */
export type AskVerbosity = "assume_first" | "balanced" | "ask_freely";

export interface Me {
  user_id: string;
  display_name: string;
  email: string | null;
  tenant_id: string;
  roles: string[];
  level: "admin" | "creator" | "viewer";
  audience_roles: string[];
  permissions: string[];
  /** Permissions given to this person on one connection (US-161), on top of their roles. */
  grants?: { permission: string; connection_id: string; expires_at: string }[];
  scope_summary: ScopeSummary[];
  locale: string | null;
  time_zone: string | null;
  /** Interface preferences remembered per user (US-134); older API builds may omit it. */
  preferences?: { theme: "system" | "light" | "dark"; ask_verbosity?: AskVerbosity; layout?: "default" | "classic" | "executive" | "command" | "aurora" };
}

export interface Role {
  id: string;
  key: string;
  name: string;
  kind: "system" | "audience";
  is_protected: boolean;
}

export interface IdpGroup {
  id: string;
  external_group_id: string;
  display_name: string;
  source: string;
  member_count?: number;
}

export interface Mapping {
  id: string;
  idp_group_id: string;
  group_name: string;
  role: string;
  role_kind: "system" | "audience";
  requires_approval: boolean;
  status: "active" | "pending_approval";
  scope_attributes: Record<string, string[]>;
  requested_by: string | null;
}

export interface MappingsResponse {
  etag: string;
  mappings: Mapping[];
}

export type PredicateOp = "=" | "in" | "not in" | "is null";

export interface Predicate {
  field: string;
  op: PredicateOp;
  values?: string[];
  attr?: string;
}

export interface ScopePolicy {
  id: string;
  connection_id: string;
  entity: string;
  role: string;
  all_rows: boolean;
  predicates: Predicate[];
  description: string;
  is_active: boolean;
}

/** Entities and fields a scope policy can name, from the connection's approved model. */
export interface PolicyEntities {
  model_version_id: string | null;
  entities: { key: string; label: string; table: string; fields: { key: string; label: string }[] }[];
}

export interface Connection {
  id: string;
  name: string;
  engine: string;
  status: string;
}

export interface UserSummary {
  id: string;
  display_name: string;
  email: string | null;
  status: string;
  roles: string[];
}

export interface FieldRule {
  field_ref: string;
  hidden_for: string[];
  effect: "hide" | "mask";
}

export interface ScopePreview {
  /** Who was resolved: a named person, or a role on its own (US-145). */
  subject: { kind: "person" | "role"; id: string; display_name: string; key?: string };
  user: { id: string; display_name: string };
  /** Microsoft groups the person is in (empty for a role). */
  groups: string[];
  permissions: string[];
  level: string | null;
  audience_roles: string[];
  scope: {
    summary: string | null;
    entities: Record<string, { all_rows: boolean; alternatives: { field: string; op: string; values: unknown[] }[][] }>;
    hidden_fields: string[];
    masked_fields: string[];
    error: { code: string; message: string; details: Record<string, unknown> } | null;
  };
  /** Rows each entity shows under this scope, when the connection has an approved model. */
  rows_in_scope: Record<string, number> | null;
  /** Permissions given to this person on this connection (US-161), apart from their roles; empty for a role. */
  grants?: PermissionGrant[];
}

/** One permission given to one person on one connection (US-161). */
export interface PermissionGrant {
  id: string;
  user: { id: string; display_name: string; email: string | null };
  permission: string;
  connection: { id: string; name: string };
  reason: string;
  granted_by: { id: string; display_name: string } | null;
  granted_at: string;
  expires_at: string;
  removed_at: string | null;
  removed_by: { id: string; display_name: string } | null;
  removed_reason: "admin" | "scim" | "expired" | null;
  /** Last free-hand run under this grant (US-162; set from US-163 on). */
  last_used_at?: string | null;
  status: "active" | "expired" | "removed";
}

/** A connection in the Give access dialog: whether the person can see it, and their live grant on it (US-162). */
export interface GrantConnection {
  id: string;
  name: string;
  visible: boolean;
  grant: { id: string; expires_at: string } | null;
}

/** A person's request for free-hand access on one connection, from Ask (US-162). */
export interface AccessRequest {
  id: string;
  user: { id: string; display_name: string; email: string | null };
  permission: string;
  connection: { id: string; name: string };
  note: string | null;
  status: "pending" | "approved" | "declined";
  requested_at: string;
  decided_by: { id: string; display_name: string } | null;
  decided_at: string | null;
  decision_reason: string | null;
  grant_id: string | null;
}
