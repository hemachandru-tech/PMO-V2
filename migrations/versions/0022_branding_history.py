"""Branding history and logo sizes (ENH-006, US-151 / US-152).

* ``ops.tenant_branding.logo_sizes`` — how tall the logo is drawn in the sidebar, on the sign-in screens and in the
  PDF export header (px). Empty means the Tela defaults.
* ``ops.tenant_branding_history`` — what the branding looked like after each change (names, assets, palette, logo
  sizes), so an Admin can see past versions and roll back to one. Replaced logo files are kept while a retained
  version still points at them. Current rows are copied in as their first entry.

Revision ID: 0022
Revises: 0021
Create Date: 2026-10-27
"""

from alembic import context

from migrations.helpers import run

revision = "0022"
down_revision = "0021"
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
        ALTER TABLE ops.tenant_branding ADD COLUMN logo_sizes jsonb NOT NULL DEFAULT '{}';
        CREATE TABLE ops.tenant_branding_history (
          tenant_id   uuid NOT NULL REFERENCES iam.tenants(id) ON DELETE RESTRICT,
          version     integer NOT NULL,
          change      text NOT NULL,
          snapshot    jsonb NOT NULL,
          changed_by  uuid REFERENCES iam.users(id),
          changed_at  timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (tenant_id, version)
        );
        ALTER TABLE ops.tenant_branding_history ENABLE ROW LEVEL SECURITY;
        CREATE POLICY tenant_isolation ON ops.tenant_branding_history
          USING (tenant_id = util.current_tenant()) WITH CHECK (tenant_id = util.current_tenant());
        INSERT INTO ops.tenant_branding_history (tenant_id, version, change, snapshot, changed_by, changed_at)
          SELECT tenant_id, version, 'current',
                 jsonb_build_object('app_name', app_name, 'tagline', tagline, 'assets', assets, 'palette', palette,
                                    'logo_sizes', logo_sizes),
                 updated_by, updated_at
            FROM ops.tenant_branding;
        """
    )
    role = _app_role()
    run(
        f"""
        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN
            GRANT SELECT, INSERT, DELETE ON ops.tenant_branding_history TO {role};
          END IF;
        END $$;
        """
    )


def downgrade() -> None:
    run("DROP TABLE IF EXISTS ops.tenant_branding_history;")
    run("ALTER TABLE ops.tenant_branding DROP COLUMN IF EXISTS logo_sizes;")
