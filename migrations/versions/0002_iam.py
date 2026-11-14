"""Identity and access tables plus seed roles (db_scheme §12 revision 0002; DATA-001..008).

The permission catalogue follows technical_document.md §Authentication and
authorization (PRE-06 resolution of inconsistency I-2).

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-21
"""

from migrations.helpers import run

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

AUDIT_COLS = """
  created_at  timestamptz NOT NULL DEFAULT now(),
  updated_at  timestamptz NOT NULL DEFAULT now(),
  created_by  uuid
"""

VIEWER = ["dashboards.view", "definitions.view", "drill.use", "ask.use", "export.use"]
CREATOR = [
    *VIEWER,
    "widgets.create",
    "reports.create",
    "dashboards.create",
    "fields.create",
    "share.create",
    "certification.request",
    "customsql.use",
]
ADMIN = [
    *CREATOR,
    "connections.manage",
    "model.edit",
    "model.approve",
    "starter.manage",
    "mappings.manage",
    "policies.manage",
    "certification.decide",
    "dashboards.publish",
    "audit.read",
    "settings.manage",
    "knowledge.manage",
]

# Fixed UUIDs for global seed roles so other seeds and tests can reference them.
SEED_ROLES = [
    ("00000000-0000-7000-8000-00000000a001", "admin", "Admin", "system", True, ADMIN),
    ("00000000-0000-7000-8000-00000000a002", "creator", "Creator", "system", False, CREATOR),
    ("00000000-0000-7000-8000-00000000a003", "viewer", "Viewer", "system", False, VIEWER),
    ("00000000-0000-7000-8000-00000000b001", "cio", "CIO", "audience", False, []),
    ("00000000-0000-7000-8000-00000000b002", "executive", "Executive", "audience", False, []),
    ("00000000-0000-7000-8000-00000000b003", "delivery_manager", "Delivery manager", "audience", False, []),
]


def upgrade() -> None:
    run(
        f"""
        CREATE TABLE iam.tenants (
          id                  uuid PRIMARY KEY,
          slug                text NOT NULL UNIQUE CHECK (slug ~ '^[a-z0-9-]{{3,40}}$'),
          name                text NOT NULL,
          status              text NOT NULL DEFAULT 'active'
                                CHECK (status IN ('active','suspended','deprovisioned')),
          deployment_profile  text NOT NULL DEFAULT 'saas'
                                CHECK (deployment_profile IN ('saas','customer_hosted')),
          oidc_issuer         text UNIQUE,
          settings            jsonb NOT NULL DEFAULT '{{}}',
          {AUDIT_COLS}
        );
        COMMENT ON COLUMN iam.tenants.oidc_issuer IS
          'Trusted token issuer (Keycloak realm URL) that identifies this tenant at sign-in';

        CREATE TABLE iam.users (
          id                     uuid PRIMARY KEY,
          tenant_id              uuid NOT NULL REFERENCES iam.tenants(id) ON DELETE RESTRICT,
          display_name           text NOT NULL,
          email                  citext,
          status                 text NOT NULL DEFAULT 'pending'
                                   CHECK (status IN ('pending','active','suspended','deprovisioned')),
          locale                 text,
          time_zone              text,
          last_login_at          timestamptz,
          effective_roles_cache  jsonb NOT NULL DEFAULT '[]',
          {AUDIT_COLS}
        );
        CREATE UNIQUE INDEX uq_users_email ON iam.users (tenant_id, lower(email::text)) WHERE email IS NOT NULL;
        CREATE INDEX ix_users_status ON iam.users (tenant_id, status);

        CREATE TABLE iam.external_identities (
          id                   uuid PRIMARY KEY,
          tenant_id            uuid NOT NULL REFERENCES iam.tenants(id) ON DELETE RESTRICT,
          user_id              uuid NOT NULL REFERENCES iam.users(id) ON DELETE CASCADE,
          provider             text NOT NULL DEFAULT 'entra' CHECK (provider IN ('entra','oidc','saml')),
          issuer               text NOT NULL,
          subject              text NOT NULL,
          directory_tenant_id  text,
          directory_object_id  text,
          last_seen_at         timestamptz,
          {AUDIT_COLS},
          CONSTRAINT uq_external_identity UNIQUE (issuer, subject)
        );
        CREATE UNIQUE INDEX uq_external_identity_oid ON iam.external_identities
          (directory_tenant_id, directory_object_id) WHERE directory_object_id IS NOT NULL;

        CREATE TABLE iam.idp_groups (
          id                 uuid PRIMARY KEY,
          tenant_id          uuid NOT NULL REFERENCES iam.tenants(id) ON DELETE RESTRICT,
          external_group_id  text NOT NULL,
          display_name       text NOT NULL,
          source             text NOT NULL DEFAULT 'claim' CHECK (source IN ('claim','scim','graph','admin')),
          last_synced_at     timestamptz,
          {AUDIT_COLS},
          CONSTRAINT uq_idp_group UNIQUE (tenant_id, external_group_id)
        );

        CREATE TABLE iam.idp_group_members (
          tenant_id    uuid NOT NULL REFERENCES iam.tenants(id) ON DELETE RESTRICT,
          group_id     uuid NOT NULL REFERENCES iam.idp_groups(id) ON DELETE CASCADE,
          user_id      uuid NOT NULL REFERENCES iam.users(id) ON DELETE CASCADE,
          source       text NOT NULL CHECK (source IN ('claim','scim','graph')),
          observed_at  timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (group_id, user_id)
        );
        CREATE INDEX ix_group_members_user ON iam.idp_group_members (user_id);

        CREATE TABLE iam.roles (
          id            uuid PRIMARY KEY,
          tenant_id     uuid REFERENCES iam.tenants(id) ON DELETE RESTRICT,
          key           text NOT NULL CHECK (key ~ '^[a-z_]{{2,40}}$'),
          name          text NOT NULL,
          kind          text NOT NULL CHECK (kind IN ('system','audience')),
          is_protected  boolean NOT NULL DEFAULT false,
          {AUDIT_COLS}
        );
        CREATE UNIQUE INDEX uq_roles_key ON iam.roles
          (coalesce(tenant_id, '00000000-0000-0000-0000-000000000000'::uuid), key);

        CREATE TABLE iam.role_permissions (
          role_id         uuid NOT NULL REFERENCES iam.roles(id) ON DELETE CASCADE,
          permission_key  text NOT NULL CHECK (permission_key ~ '^[a-z]+\\.[a-z_]+$'),
          PRIMARY KEY (role_id, permission_key)
        );

        CREATE TABLE iam.group_role_mappings (
          id                uuid PRIMARY KEY,
          tenant_id         uuid NOT NULL REFERENCES iam.tenants(id) ON DELETE RESTRICT,
          group_id          uuid NOT NULL REFERENCES iam.idp_groups(id) ON DELETE CASCADE,
          role_id           uuid NOT NULL REFERENCES iam.roles(id) ON DELETE RESTRICT,
          scope_attributes  jsonb NOT NULL DEFAULT '{{}}',
          approved_by       uuid REFERENCES iam.users(id),
          approved_at       timestamptz,
          {AUDIT_COLS},
          CONSTRAINT uq_group_role UNIQUE (group_id, role_id),
          CONSTRAINT ck_mapping_scope_attributes CHECK (jsonb_typeof(scope_attributes) = 'object')
        );
        CREATE INDEX ix_mappings_tenant ON iam.group_role_mappings (tenant_id);
        """
    )

    for table in ("tenants", "users", "external_identities", "idp_groups", "roles", "group_role_mappings"):
        run(
            f"CREATE TRIGGER trg_{table}_updated_at BEFORE UPDATE ON iam.{table} "
            "FOR EACH ROW EXECUTE FUNCTION util.set_updated_at()"
        )

    for role_id, key, name, kind, protected, perms in SEED_ROLES:
        run(
            f"INSERT INTO iam.roles (id, tenant_id, key, name, kind, is_protected) "
            f"VALUES ('{role_id}', NULL, '{key}', '{name}', '{kind}', {str(protected).lower()})"
        )
        for perm in perms:
            run(f"INSERT INTO iam.role_permissions (role_id, permission_key) VALUES ('{role_id}', '{perm}')")

    # Tenant lookup at sign-in happens before a tenant context exists, so it is
    # a narrow SECURITY DEFINER function rather than an RLS bypass.
    run(
        """
        CREATE OR REPLACE FUNCTION iam.f_tenant_for_issuer(p_issuer text)
        RETURNS TABLE (id uuid, status text)
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = iam, pg_temp AS $$
          SELECT t.id, t.status FROM iam.tenants t WHERE t.oidc_issuer = p_issuer
        $$;
        """
    )

    # Effective roles of a user: protected roles count only when the mapping is approved.
    run(
        """
        CREATE OR REPLACE FUNCTION iam.f_effective_roles(p_user_id uuid)
        RETURNS TABLE (role_id uuid, role_key text, kind text, scope_attributes jsonb)
        LANGUAGE sql STABLE AS $$
          SELECT r.id, r.key, r.kind, m.scope_attributes
          FROM iam.idp_group_members gm
          JOIN iam.group_role_mappings m ON m.group_id = gm.group_id
          JOIN iam.roles r ON r.id = m.role_id
          WHERE gm.user_id = p_user_id
            AND (NOT r.is_protected OR m.approved_at IS NOT NULL)
        $$;
        """
    )


def downgrade() -> None:
    run("DROP FUNCTION IF EXISTS iam.f_effective_roles(uuid)")
    run("DROP FUNCTION IF EXISTS iam.f_tenant_for_issuer(text)")
    for table in (
        "group_role_mappings",
        "role_permissions",
        "roles",
        "idp_group_members",
        "idp_groups",
        "external_identities",
        "users",
        "tenants",
    ):
        run(f"DROP TABLE IF EXISTS iam.{table} CASCADE")
