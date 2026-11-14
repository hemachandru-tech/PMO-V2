"""Per-user permission grants (ENH-008, US-161; FEAT-060).

* ``iam.permission_grants`` — one permission given to one person on one connection, outside the roles: who gave it,
  why, when it ends and, once taken back, when, by whom and why (``admin``, ``scim`` or ``expired``).
  Only grantable permissions are allowed (today ``ask.freehand``, which no role holds). A grant stops working at
  ``expires_at`` when it is checked; ``reminded_at`` and ``expiry_recorded_at`` record the reminder seven days
  before and the ``permission.expired`` audit event made by the daily sweep.
* ``iam.f_permission_grant_tenants()`` — narrow SECURITY DEFINER lookup of the tenants the sweep has work in
  (like ``model.f_pack_update_tenants``).

Revision ID: 0027
Revises: 0026
Create Date: 2026-10-27
"""

from alembic import context

from migrations.helpers import run

revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None


def _app_role() -> str:
    role = context.get_x_argument(as_dictionary=True).get("tela.app_role", "tela_app")
    if not role.replace("_", "").isalnum():
        raise ValueError("invalid app role name")
    return role


def upgrade() -> None:
    run(
        """
        CREATE TABLE iam.permission_grants (
          id                  uuid PRIMARY KEY,
          tenant_id           uuid NOT NULL REFERENCES iam.tenants(id) ON DELETE RESTRICT,
          user_id             uuid NOT NULL REFERENCES iam.users(id) ON DELETE CASCADE,
          permission_key      text NOT NULL CHECK (permission_key IN ('ask.freehand')),
          connection_id       uuid NOT NULL REFERENCES conn.connections(id) ON DELETE CASCADE,
          reason              text NOT NULL CHECK (char_length(btrim(reason)) BETWEEN 1 AND 500),
          granted_by          uuid REFERENCES iam.users(id),
          granted_at          timestamptz NOT NULL DEFAULT now(),
          expires_at          timestamptz NOT NULL,
          reminded_at         timestamptz,
          expiry_recorded_at  timestamptz,
          removed_at          timestamptz,
          removed_by          uuid REFERENCES iam.users(id),
          removed_reason      text CHECK (removed_reason IN ('admin', 'scim', 'expired')),
          CONSTRAINT ck_grant_expiry CHECK (expires_at > granted_at),
          CONSTRAINT ck_grant_removed CHECK ((removed_at IS NULL) = (removed_reason IS NULL))
        );
        CREATE UNIQUE INDEX uq_permission_grant_open ON iam.permission_grants (user_id, permission_key, connection_id)
          WHERE removed_at IS NULL;
        CREATE INDEX ix_permission_grants_user ON iam.permission_grants (user_id) WHERE removed_at IS NULL;
        CREATE INDEX ix_permission_grants_expiry ON iam.permission_grants (expires_at) WHERE removed_at IS NULL;

        ALTER TABLE iam.permission_grants ENABLE ROW LEVEL SECURITY;
        CREATE POLICY tenant_isolation ON iam.permission_grants
          USING (tenant_id = util.current_tenant()) WITH CHECK (tenant_id = util.current_tenant());

        CREATE FUNCTION iam.f_permission_grant_tenants() RETURNS TABLE (tenant_id uuid)
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = iam, pg_temp AS $$
          SELECT DISTINCT g.tenant_id FROM iam.permission_grants g JOIN iam.tenants t ON t.id = g.tenant_id
          WHERE g.removed_at IS NULL AND t.status = 'active'
            AND ((g.reminded_at IS NULL AND g.expires_at <= now() + interval '7 days')
                 OR (g.expiry_recorded_at IS NULL AND g.expires_at <= now()))
        $$;
        REVOKE ALL ON FUNCTION iam.f_permission_grant_tenants() FROM PUBLIC;
        """
    )
    role = _app_role()
    run(
        f"""
        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN
            GRANT SELECT, INSERT, UPDATE ON iam.permission_grants TO {role};
            GRANT EXECUTE ON FUNCTION iam.f_permission_grant_tenants() TO {role};
          END IF;
        END $$;
        """
    )


def downgrade() -> None:
    run(
        """
        DROP FUNCTION IF EXISTS iam.f_permission_grant_tenants();
        DROP TABLE IF EXISTS iam.permission_grants;
        """
    )
