"""Starter pack updates (US-120; FEAT-047; API-048; REQ-113).

``model.data_models`` gains:

* ``pending_pack_id`` — a newer pack version the Admin applied whose measure or field changes wait in the model
  draft. On approval the model switches to it and its starter reports are rebuilt; until then the installed version
  stays in force.
* ``pack_update_notified_id`` — the newest version the daily ``pack_update_check`` already told the Admins about,
  so each release is announced once per model.

``model.f_pack_update_tenants()`` lists the tenants with a model whose installed pack has a newer version in the
catalogue that nobody was told about yet (SECURITY DEFINER, like ``rag.f_eval_tenants``).

Revision ID: 0017
Revises: 0016
Create Date: 2026-10-22
"""

from alembic import context

from migrations.helpers import run

revision = "0017"
down_revision = "0016"
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
        ALTER TABLE model.data_models
          ADD COLUMN pending_pack_id uuid REFERENCES model.starter_packs(id) ON DELETE SET NULL,
          ADD COLUMN pack_update_notified_id uuid REFERENCES model.starter_packs(id) ON DELETE SET NULL;

        CREATE FUNCTION model.f_pack_update_tenants() RETURNS TABLE (tenant_id uuid)
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = model, pg_temp AS $$
          SELECT DISTINCT m.tenant_id
          FROM model.data_models m
          JOIN model.starter_packs i ON i.id = m.installed_pack_id
          JOIN model.starter_packs n ON n.pack_key = i.pack_key
            AND string_to_array(n.pack_version, '.')::int[] > string_to_array(i.pack_version, '.')::int[]
          WHERE m.pack_update_notified_id IS DISTINCT FROM n.id
        $$;
        REVOKE ALL ON FUNCTION model.f_pack_update_tenants() FROM PUBLIC;
        """
    )
    role = _app_role()
    run(
        f"""
        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN
            GRANT EXECUTE ON FUNCTION model.f_pack_update_tenants() TO {role};
          END IF;
        END $$;
        """
    )


def downgrade() -> None:
    run(
        """
        DROP FUNCTION IF EXISTS model.f_pack_update_tenants();
        ALTER TABLE model.data_models DROP COLUMN IF EXISTS pack_update_notified_id,
          DROP COLUMN IF EXISTS pending_pack_id;
        """
    )
