"""Visual catalogue wave 2 (ENH-014, US-188).

* ``content.widgets.visual`` accepts ``calendar``, ``sankey``, ``upset``, ``beeswarm``, ``ridgeline``,
  ``survival``, ``bump``, ``treemap`` and ``waffle``.

Revision ID: 0039
Revises: 0038
Create Date: 2026-10-29
"""

from migrations.helpers import run

revision = "0039"
down_revision = "0038"
branch_labels = None
depends_on = None

OLD = ("'figure', 'ranked_bar', 'columns', 'trend', 'paired_bars', 'stacked_status', 'donut', 'table', 'heat_grid', "
       "'meter', 'dumbbell', 'waterfall', 'bullet', 'pareto', 'xmr', 'gantt', 'funnel', 'small_multiples'")
ADDED = "'calendar', 'sankey', 'upset', 'beeswarm', 'ridgeline', 'survival', 'bump', 'treemap', 'waffle'"
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
