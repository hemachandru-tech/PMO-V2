"""Tenant branding: app name, tagline, logos and favicon (ENH-006, US-149).

* ``ops.tenant_branding`` — one row per tenant once an Admin changes anything; no row means the Tela defaults.
  ``assets`` maps ``logo_light`` / ``logo_dark`` / ``favicon`` to what may be shown about the stored file (object
  key, content type, size, pixel size, SHA-256). The files live in the object store under
  ``tenant/<t>/branding/``. ``palette`` is kept for US-150. ``version`` goes up on every change, so asset URLs can be
  cached for good and still change when the brand does.
* New permission ``branding.manage`` for the Admin system role.

Revision ID: 0021
Revises: 0020
Create Date: 2026-10-27
"""

from alembic import context

from migrations.helpers import run

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None

ADMIN_ROLE = "00000000-0000-7000-8000-00000000a001"


def _app_role() -> str:
    role = context.get_x_argument(as_dictionary=True).get("tela.app_role", "tela_app")
    if not role.replace("_", "").isalnum():
        raise ValueError("invalid app role name")
    return role


def upgrade() -> None:
    run(
        """
        CREATE TABLE ops.tenant_branding (
          tenant_id    uuid PRIMARY KEY REFERENCES iam.tenants(id) ON DELETE RESTRICT,
          app_name     text NOT NULL CHECK (char_length(app_name) BETWEEN 2 AND 40),
          tagline      text CHECK (char_length(tagline) <= 60),
          assets       jsonb NOT NULL DEFAULT '{}',
          palette      jsonb,
          version      integer NOT NULL DEFAULT 1 CHECK (version >= 0),
          updated_by   uuid REFERENCES iam.users(id),
          created_at   timestamptz NOT NULL DEFAULT now(),
          updated_at   timestamptz NOT NULL DEFAULT now()
        );
        CREATE TRIGGER trg_tenant_branding_updated_at BEFORE UPDATE ON ops.tenant_branding
          FOR EACH ROW EXECUTE FUNCTION util.set_updated_at();
        ALTER TABLE ops.tenant_branding ENABLE ROW LEVEL SECURITY;
        CREATE POLICY tenant_isolation ON ops.tenant_branding
          USING (tenant_id = util.current_tenant()) WITH CHECK (tenant_id = util.current_tenant());
        """
    )
    run(
        f"INSERT INTO iam.role_permissions (role_id, permission_key) VALUES ('{ADMIN_ROLE}', 'branding.manage') "
        "ON CONFLICT DO NOTHING"
    )
    role = _app_role()
    run(
        f"""
        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN
            GRANT SELECT, INSERT, UPDATE, DELETE ON ops.tenant_branding TO {role};
          END IF;
        END $$;
        """
    )


def downgrade() -> None:
    run(f"DELETE FROM iam.role_permissions WHERE role_id = '{ADMIN_ROLE}' AND permission_key = 'branding.manage'")
    run("DROP TABLE IF EXISTS ops.tenant_branding;")
