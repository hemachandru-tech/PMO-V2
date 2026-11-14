"""Extensions, schemas and utility functions (db_scheme §12 revision 0001).

Revision ID: 0001
Revises:
Create Date: 2026-10-21
"""

from migrations.helpers import run

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    run("CREATE EXTENSION IF NOT EXISTS vector")
    run("CREATE EXTENSION IF NOT EXISTS citext")
    run("CREATE EXTENSION IF NOT EXISTS pgcrypto")

    for schema in ("util", "iam", "conn", "ops"):
        run(f"CREATE SCHEMA IF NOT EXISTS {schema}")

    run(
        """
        CREATE OR REPLACE FUNCTION util.set_updated_at() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          NEW.updated_at := now();
          RETURN NEW;
        END $$;
        """
    )

    # Raises on UPDATE or DELETE of immutable rows (audit events, decisions,
    # approved versions). Tables that allow one specific status change attach
    # their own guard instead.
    run(
        """
        CREATE OR REPLACE FUNCTION util.forbid_update() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          RAISE EXCEPTION 'rows of %.% are immutable', TG_TABLE_SCHEMA, TG_TABLE_NAME
            USING ERRCODE = 'restrict_violation';
        END $$;
        """
    )

    # Current tenant from the transaction-local setting (SET LOCAL app.tenant_id).
    run(
        """
        CREATE OR REPLACE FUNCTION util.current_tenant() RETURNS uuid
        LANGUAGE sql STABLE AS $$
          SELECT NULLIF(current_setting('app.tenant_id', true), '')::uuid
        $$;
        """
    )


def downgrade() -> None:
    run("DROP FUNCTION IF EXISTS util.current_tenant()")
    run("DROP FUNCTION IF EXISTS util.forbid_update()")
    run("DROP FUNCTION IF EXISTS util.set_updated_at()")
    for schema in ("ops", "conn", "iam", "util"):
        run(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
