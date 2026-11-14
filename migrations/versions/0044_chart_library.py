"""Chart library choice (ENH-018, US-206; FEAT-068).

* ``ops.tenant_branding.chart_library`` — which library draws the tenant's standard charts: ``echarts`` (the
  default), ``recharts`` (shadcn/ui Charts) or ``plot`` (Observable Plot). The visual catalogue keeps its own drawers.
  Kept in the branding history snapshots like the palette, so a rollback brings it back too.

Revision ID: 0044
Revises: 0043
Create Date: 2026-11-04
"""

from migrations.helpers import run

revision = "0044"
down_revision = "0043"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run(
        """
        ALTER TABLE ops.tenant_branding ADD COLUMN chart_library text NOT NULL DEFAULT 'echarts'
          CHECK (chart_library IN ('echarts', 'recharts', 'plot'));
        """
    )


def downgrade() -> None:
    run("ALTER TABLE ops.tenant_branding DROP COLUMN IF EXISTS chart_library;")
