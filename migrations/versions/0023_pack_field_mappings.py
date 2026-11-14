"""Starter pack field mappings per connection (ENH-007, US-154).

* ``model.pack_field_mappings`` — the Admin's mapping of a pack to one connection's names: pack tables and fields
  to source objects and columns (or "not in this source") and source values to the pack's values
  (``mapping``), plus what the last install resolved for every pack field (``resolved``), so re-apply and pack
  updates (US-120) bind the same names. One row per connection and pack key (all versions of a pack share it).

Revision ID: 0023
Revises: 0022
Create Date: 2026-10-27
"""

from alembic import context

from migrations.helpers import run

revision = "0023"
down_revision = "0022"
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
        CREATE TABLE model.pack_field_mappings (
          tenant_id      uuid NOT NULL REFERENCES iam.tenants(id) ON DELETE RESTRICT,
          connection_id  uuid NOT NULL REFERENCES conn.connections(id) ON DELETE CASCADE,
          pack_key       text NOT NULL,
          mapping        jsonb NOT NULL DEFAULT '{}',
          resolved       jsonb NOT NULL DEFAULT '{}',
          updated_by     uuid REFERENCES iam.users(id),
          updated_at     timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (tenant_id, connection_id, pack_key)
        );
        ALTER TABLE model.pack_field_mappings ENABLE ROW LEVEL SECURITY;
        CREATE POLICY tenant_isolation ON model.pack_field_mappings
          USING (tenant_id = util.current_tenant()) WITH CHECK (tenant_id = util.current_tenant());
        """
    )
    role = _app_role()
    run(
        f"""
        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN
            GRANT SELECT, INSERT, UPDATE, DELETE ON model.pack_field_mappings TO {role};
          END IF;
        END $$;
        """
    )


def downgrade() -> None:
    run("DROP TABLE IF EXISTS model.pack_field_mappings;")
