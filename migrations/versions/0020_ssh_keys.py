"""Shared SSH key library (ENH-004).

* ``conn.ssh_keys`` — keys an Admin uploads once and picks on many connections (and on jump hosts). The row keeps
  what may be shown: name, key type and size, SHA256 fingerprint and, for an OpenSSH user certificate, its
  principals, validity and signing CA. The private key and the certificate live in the secrets store at
  ``secret_ref`` (``tenants/<t>/ssh_keys/<id>``): the API writes it, only the connector runner reads it.

Revision ID: 0020
Revises: 0019
Create Date: 2026-10-27
"""

from alembic import context

from migrations.helpers import run

revision = "0020"
down_revision = "0019"
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
        CREATE TABLE conn.ssh_keys (
          id               uuid PRIMARY KEY,
          tenant_id        uuid NOT NULL REFERENCES iam.tenants(id) ON DELETE RESTRICT,
          name             text NOT NULL CHECK (char_length(name) BETWEEN 1 AND 80),
          key_type         text NOT NULL CHECK (char_length(key_type) <= 64),
          key_bits         integer NOT NULL CHECK (key_bits > 0),
          fingerprint      text NOT NULL CHECK (fingerprint ~ '^SHA256:[A-Za-z0-9+/]{43}$'),
          certificate      jsonb,
          secret_ref       text NOT NULL,
          created_by       uuid REFERENCES iam.users(id),
          created_at       timestamptz NOT NULL DEFAULT now(),
          updated_at       timestamptz NOT NULL DEFAULT now(),
          deleted_at       timestamptz
        );
        CREATE UNIQUE INDEX uq_ssh_keys_name ON conn.ssh_keys (tenant_id, lower(name)) WHERE deleted_at IS NULL;
        CREATE TRIGGER trg_ssh_keys_updated_at BEFORE UPDATE ON conn.ssh_keys
          FOR EACH ROW EXECUTE FUNCTION util.set_updated_at();
        ALTER TABLE conn.ssh_keys ENABLE ROW LEVEL SECURITY;
        CREATE POLICY tenant_isolation ON conn.ssh_keys
          USING (tenant_id = util.current_tenant()) WITH CHECK (tenant_id = util.current_tenant());
        """
    )
    role = _app_role()
    run(
        f"""
        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN
            GRANT SELECT, INSERT, UPDATE ON conn.ssh_keys TO {role};
          END IF;
        END $$;
        """
    )


def downgrade() -> None:
    run("DROP TABLE IF EXISTS conn.ssh_keys;")
