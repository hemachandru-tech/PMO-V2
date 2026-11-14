"""Layouts (ENH-021, US-212).

* ``ops.tenant_branding.layout`` — the company's default layout: ``classic`` (sidebar), ``executive`` (top
  navigation, a brand band on dashboards) or ``command`` (icon rail, tiles). People may pick their own in their
  preferences (``iam.users.preferences.layout``); the default applies to everyone who has not. Kept in the branding
  history snapshots, so a rollback brings it back too.

Revision ID: 0046
Revises: 0045
Create Date: 2026-11-05
"""

from migrations.helpers import run

revision = "0046"
down_revision = "0045"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run("ALTER TABLE ops.tenant_branding ADD COLUMN layout text NOT NULL DEFAULT 'classic' "
        "CHECK (layout IN ('classic', 'executive', 'command'));")


def downgrade() -> None:
    run("ALTER TABLE ops.tenant_branding DROP COLUMN IF EXISTS layout;")
