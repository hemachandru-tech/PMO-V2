"""Aurora layout (ENH-027, US-248; FEAT-077).

* ``ops.tenant_branding.layout`` also takes ``aurora``: a floating sidebar with colour chips, dashboards on a rounded
  hero band, rounded cards on soft shadows and vivid charts. People pick it in their preferences as before
  (``iam.users.preferences.layout``, a JSON value with no constraint). Downgrade puts ``aurora`` back to ``classic``.

Revision ID: 0054
Revises: 0053
Create Date: 2026-11-11
"""

from migrations.helpers import run

revision = "0054"
down_revision = "0053"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run("ALTER TABLE ops.tenant_branding DROP CONSTRAINT IF EXISTS tenant_branding_layout_check;")
    run("ALTER TABLE ops.tenant_branding ADD CONSTRAINT tenant_branding_layout_check "
        "CHECK (layout IN ('classic', 'executive', 'command', 'aurora'));")


def downgrade() -> None:
    run("UPDATE ops.tenant_branding SET layout = 'classic' WHERE layout = 'aurora';")
    run("ALTER TABLE ops.tenant_branding DROP CONSTRAINT IF EXISTS tenant_branding_layout_check;")
    run("ALTER TABLE ops.tenant_branding ADD CONSTRAINT tenant_branding_layout_check "
        "CHECK (layout IN ('classic', 'executive', 'command'));")
