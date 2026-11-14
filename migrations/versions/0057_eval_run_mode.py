"""A gold run in a chosen answer mode (ENH-028, US-259; FEAT-078 / TEST).

``rag.eval_runs.ask_mode`` becomes nullable with no default. A run queued with a mode (``certified``, ``fields``,
``hybrid`` or ``direct_sql``) plans every example in that mode, whatever the source's own setting, so each mode is
measured on the same questions without switching what people get. A run queued without one (null) follows each
source's mode, and is stamped with that mode when all its sources share one; a run over sources in different modes
stays null and is not shown as any mode's score. Existing runs keep their ``certified``.

Revision ID: 0057
Revises: 0056
Create Date: 2026-11-12
"""

from migrations.helpers import run

revision = "0057"
down_revision = "0056"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run(
        """
        ALTER TABLE rag.eval_runs ALTER COLUMN ask_mode DROP NOT NULL, ALTER COLUMN ask_mode DROP DEFAULT;
        """
    )


def downgrade() -> None:
    run(
        """
        UPDATE rag.eval_runs SET ask_mode = 'certified' WHERE ask_mode IS NULL;
        ALTER TABLE rag.eval_runs ALTER COLUMN ask_mode SET DEFAULT 'certified', ALTER COLUMN ask_mode SET NOT NULL;
        """
    )
