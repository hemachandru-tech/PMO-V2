"""Navigation colours (ENH-019, US-209).

* ``ops.tenant_branding.nav_colors`` — how the sidebar and the top bar are coloured from the brand:
  ``{"sidebar": …, "topbar": …}``, each ``standard`` (the neutral surfaces), ``tinted`` (a wash of the accent),
  ``brand`` (filled with the brand colour) or ``dark``. Empty = both standard. Kept in the branding history snapshots
  like the logo sizes, so a rollback brings it back too.

Revision ID: 0045
Revises: 0044
Create Date: 2026-11-04
"""

from migrations.helpers import run

revision = "0045"
down_revision = "0044"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run("ALTER TABLE ops.tenant_branding ADD COLUMN nav_colors jsonb NOT NULL DEFAULT '{}';")


def downgrade() -> None:
    run("ALTER TABLE ops.tenant_branding DROP COLUMN IF EXISTS nav_colors;")
