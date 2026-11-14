"""Ask answer mode per connection (ENH-028, US-256; FEAT-078 / FEAT-018; DEC-29).

* ``conn.connections.ask_mode`` — how Ask answers questions about the source: ``certified`` (the certified measures,
  today's planner), ``fields`` (Plan B: fields and approved named conditions, US-254), ``hybrid`` (Plan B with the
  certified measures as trusted shortcuts, US-255) or ``direct_sql`` (AG-10 writes SQL, the Custom SQL pipeline checks,
  scopes and runs it; DEC-30, US-257). ``certified`` is the default until the US-259 gate decides otherwise.
* ``ops.agent_runs.ask_mode`` and ``chat.turns.ask_mode`` — the mode an Ask turn was answered in (null: not an Ask
  answer, or answered before this story).
* ``rag.eval_runs.ask_mode`` — the mode a gold run measured; the Connections > Ask screen shows each mode's latest
  finished run. Every existing run measured today's planner, so they are ``certified``.

Revision ID: 0056
Revises: 0055
Create Date: 2026-11-12
"""

from migrations.helpers import run

revision = "0056"
down_revision = "0055"
branch_labels = None
depends_on = None

MODES = "('certified', 'fields', 'hybrid', 'direct_sql')"


def upgrade() -> None:
    run(
        f"""
        ALTER TABLE conn.connections
          ADD COLUMN ask_mode text NOT NULL DEFAULT 'certified' CHECK (ask_mode IN {MODES});
        ALTER TABLE ops.agent_runs
          ADD COLUMN ask_mode text CHECK (ask_mode IS NULL OR ask_mode IN {MODES});
        ALTER TABLE chat.turns
          ADD COLUMN ask_mode text CHECK (ask_mode IS NULL OR ask_mode IN {MODES});
        ALTER TABLE rag.eval_runs
          ADD COLUMN ask_mode text NOT NULL DEFAULT 'certified' CHECK (ask_mode IN {MODES});
        CREATE INDEX ix_eval_runs_connection_mode ON rag.eval_runs (connection_id, ask_mode, finished_at DESC)
          WHERE status = 'done';
        """
    )


def downgrade() -> None:
    run(
        """
        DROP INDEX IF EXISTS rag.ix_eval_runs_connection_mode;
        ALTER TABLE rag.eval_runs DROP COLUMN IF EXISTS ask_mode;
        ALTER TABLE chat.turns DROP COLUMN IF EXISTS ask_mode;
        ALTER TABLE ops.agent_runs DROP COLUMN IF EXISTS ask_mode;
        ALTER TABLE conn.connections DROP COLUMN IF EXISTS ask_mode;
        """
    )
