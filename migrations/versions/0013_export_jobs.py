"""Export jobs (EPIC-10 part 2; US-095; FEAT-045; API-046; DATA-049; DEC-15 working position).

``ops.export_jobs`` exists since 0005. This adds what the job API needs:

* ``scope_summary`` — the requester's scope text when the file was rendered (printed in the file and audited);
* ``file_name``, ``size_bytes``, ``row_count``, ``excluded_widgets`` — what the file holds, without its data;
* ``error_code`` next to the person-facing ``error``;
* an index for the 10-per-hour-per-user limit and the person's recent exports;
* ``ops.f_expire_export_jobs`` — a narrow SECURITY DEFINER sweep across tenants (like the outbox relay): marks
  finished jobs past their 24-hour retention as ``expired`` and returns their object keys for deletion.

Revision ID: 0013
Revises: 0012
Create Date: 2026-10-21
"""

from migrations.helpers import run

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run(
        """
        ALTER TABLE ops.export_jobs
          ADD COLUMN scope_summary    text,
          ADD COLUMN file_name        text CHECK (file_name IS NULL OR char_length(file_name) <= 200),
          ADD COLUMN size_bytes       bigint,
          ADD COLUMN row_count        integer,
          ADD COLUMN excluded_widgets integer,
          ADD COLUMN error_code       text,
          ADD COLUMN started_at       timestamptz,
          ADD COLUMN finished_at      timestamptz;
        CREATE INDEX ix_export_jobs_requester ON ops.export_jobs (requested_by, created_at DESC);
        CREATE INDEX ix_export_jobs_expiry ON ops.export_jobs (expires_at) WHERE status = 'done';
        COMMENT ON COLUMN ops.export_jobs.filters IS 'Page filters as requested; printed in the file, audited';

        CREATE OR REPLACE FUNCTION ops.f_expire_export_jobs(p_batch int DEFAULT 200)
        RETURNS TABLE (id uuid, tenant_id uuid, object_key text)
        LANGUAGE sql SECURITY DEFINER SET search_path = ops, pg_temp AS $$
          UPDATE ops.export_jobs j SET status = 'expired'
          WHERE j.id IN (
            SELECT e.id FROM ops.export_jobs e
            WHERE e.status = 'done' AND e.expires_at < now()
            ORDER BY e.expires_at
            LIMIT p_batch
            FOR UPDATE SKIP LOCKED)
          RETURNING j.id, j.tenant_id, j.object_key
        $$;
        """
    )


def downgrade() -> None:
    run("DROP FUNCTION IF EXISTS ops.f_expire_export_jobs(int)")
    run(
        """
        DROP INDEX IF EXISTS ops.ix_export_jobs_expiry;
        DROP INDEX IF EXISTS ops.ix_export_jobs_requester;
        ALTER TABLE ops.export_jobs
          DROP COLUMN IF EXISTS finished_at, DROP COLUMN IF EXISTS started_at, DROP COLUMN IF EXISTS error_code,
          DROP COLUMN IF EXISTS excluded_widgets, DROP COLUMN IF EXISTS row_count, DROP COLUMN IF EXISTS size_bytes,
          DROP COLUMN IF EXISTS file_name, DROP COLUMN IF EXISTS scope_summary;
        """
    )
