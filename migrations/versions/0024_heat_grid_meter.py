"""Heat grid and meter visuals (ENH-007, US-157).

* ``content.widgets.visual`` accepts ``heat_grid`` and ``meter``. A figure's tone threshold and a meter's note live
  in the widget's ``spec.visual`` (no new column).

Revision ID: 0024
Revises: 0023
Create Date: 2026-10-27
"""

from migrations.helpers import run

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None

OLD = "'figure', 'ranked_bar', 'columns', 'trend', 'paired_bars', 'stacked_status', 'donut', 'table'"
NEW = OLD + ", 'heat_grid', 'meter'"


def upgrade() -> None:
    run(
        f"""
        ALTER TABLE content.widgets DROP CONSTRAINT ck_widgets_visual;
        ALTER TABLE content.widgets ADD CONSTRAINT ck_widgets_visual CHECK (visual IN ({NEW}));
        """
    )


def downgrade() -> None:
    run(
        f"""
        UPDATE content.widgets SET visual = 'table' WHERE visual IN ('heat_grid', 'meter');
        ALTER TABLE content.widgets DROP CONSTRAINT ck_widgets_visual;
        ALTER TABLE content.widgets ADD CONSTRAINT ck_widgets_visual CHECK (visual IN ({OLD}));
        """
    )
