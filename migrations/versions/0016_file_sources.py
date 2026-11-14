"""Uploaded Excel and CSV sources (US-108; DATA-013; ADR-012; DEC-07).

``conn.file_sources`` already holds one row per uploaded file version. This adds:

* ``status`` — ``pending`` (a replacement uploaded and profiled, waiting for the Admin to confirm the diff,
  E-13), ``active`` (the version new queries read), ``replaced`` (kept for models still pinned to it, then until
  retention ends), ``discarded`` (a pending upload the Admin dropped) and ``purged`` (object deleted);
* ``retention_policy`` — ``replaced_plus_30d`` (DEC-07 working position) or ``fixed_days`` with
  ``retention_days``; ``retention_until`` is set when a version is replaced or discarded (or at upload for a
  fixed period) and the nightly sweep deletes expired objects that no approved model version pins;
* ``classification`` — declared at upload (DATA-013 default Confidential), with ``purged_at``.

The "one current file" index now means one ``active`` version per connection.
``conn.f_expired_file_versions(n)`` lists expired versions across tenants for the sweep (SECURITY DEFINER,
like ``ops.f_expire_export_jobs``); it never returns a version an approved model version still pins.

Revision ID: 0016
Revises: 0015
Create Date: 2026-10-22
"""

from alembic import context

from migrations.helpers import run

revision = "0016"
down_revision = "0015"
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
        ALTER TABLE conn.file_sources
          ADD COLUMN status text NOT NULL DEFAULT 'active'
            CHECK (status IN ('pending', 'active', 'replaced', 'discarded', 'purged')),
          ADD COLUMN retention_policy text NOT NULL DEFAULT 'replaced_plus_30d'
            CHECK (retention_policy IN ('replaced_plus_30d', 'fixed_days')),
          ADD COLUMN retention_days integer CHECK (retention_days BETWEEN 1 AND 3650),
          ADD COLUMN classification text NOT NULL DEFAULT 'confidential'
            CHECK (classification IN ('internal', 'confidential', 'restricted')),
          ADD COLUMN purged_at timestamptz,
          ADD CONSTRAINT ck_file_retention_days CHECK ((retention_policy = 'fixed_days') = (retention_days IS NOT NULL));
        UPDATE conn.file_sources SET status = 'replaced' WHERE replaced_at IS NOT NULL;
        DROP INDEX conn.uq_file_current;
        CREATE UNIQUE INDEX uq_file_active ON conn.file_sources (connection_id) WHERE status = 'active';
        CREATE UNIQUE INDEX uq_file_pending ON conn.file_sources (connection_id) WHERE status = 'pending';
        CREATE INDEX ix_file_retention ON conn.file_sources (retention_until)
          WHERE status IN ('replaced', 'discarded', 'active') AND retention_until IS NOT NULL;

        CREATE FUNCTION conn.f_expired_file_versions(max_rows integer)
        RETURNS TABLE (id uuid, tenant_id uuid, connection_id uuid, version_no integer, object_key text)
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = conn, model, pg_temp AS $$
          SELECT f.id, f.tenant_id, f.connection_id, f.version_no, f.object_key
          FROM conn.file_sources f
          WHERE f.status IN ('replaced', 'discarded', 'active') AND f.retention_until < now()
            AND NOT EXISTS (
              SELECT 1 FROM model.data_models m JOIN model.model_versions v ON v.data_model_id = m.id
              WHERE m.connection_id = f.connection_id AND v.status = 'approved'
                AND (v.definition -> 'source_pin' ->> 'file_version')::integer = f.version_no)
          ORDER BY f.retention_until
          LIMIT max_rows
        $$;
        REVOKE ALL ON FUNCTION conn.f_expired_file_versions(integer) FROM PUBLIC;
        """
    )
    role = _app_role()
    run(
        f"""
        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN
            GRANT EXECUTE ON FUNCTION conn.f_expired_file_versions(integer) TO {role};
          END IF;
        END $$;
        """
    )


def downgrade() -> None:
    run(
        """
        DROP FUNCTION IF EXISTS conn.f_expired_file_versions(integer);
        DROP INDEX IF EXISTS conn.ix_file_retention;
        DROP INDEX IF EXISTS conn.uq_file_pending;
        DROP INDEX IF EXISTS conn.uq_file_active;
        DELETE FROM conn.file_sources WHERE status IN ('pending', 'discarded');
        CREATE UNIQUE INDEX uq_file_current ON conn.file_sources (connection_id) WHERE replaced_at IS NULL;
        ALTER TABLE conn.file_sources DROP CONSTRAINT IF EXISTS ck_file_retention_days,
          DROP COLUMN purged_at, DROP COLUMN classification, DROP COLUMN retention_days,
          DROP COLUMN retention_policy, DROP COLUMN status;
        """
    )
