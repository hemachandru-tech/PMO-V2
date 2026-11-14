"""Visual catalogue wave 1 (ENH-014, US-187).

* ``content.widgets.visual`` accepts ``dumbbell``, ``waterfall``, ``bullet``, ``pareto``, ``xmr``, ``gantt``,
  ``funnel`` and ``small_multiples``. A bullet's target band lives in the widget's ``spec.visual.tone`` (no new
  column).

Revision ID: 0037
Revises: 0036
Create Date: 2026-10-29
"""

from migrations.helpers import run

revision = "0037"
down_revision = "0036"
branch_labels = None
depends_on = None

OLD = ("'figure', 'ranked_bar', 'columns', 'trend', 'paired_bars', 'stacked_status', 'donut', 'table', 'heat_grid', "
       "'meter'")
ADDED = "'dumbbell', 'waterfall', 'bullet', 'pareto', 'xmr', 'gantt', 'funnel', 'small_multiples'"
NEW = f"{OLD}, {ADDED}"


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
        UPDATE content.widgets SET visual = 'table' WHERE visual IN ({ADDED});
        ALTER TABLE content.widgets DROP CONSTRAINT ck_widgets_visual;
        ALTER TABLE content.widgets ADD CONSTRAINT ck_widgets_visual CHECK (visual IN ({OLD}));
        """
    )
