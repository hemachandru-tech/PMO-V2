"""Per-user interface preferences (EPIC-UX US-134).

``iam.users.preferences`` holds small interface settings the user chooses, starting with the theme
(``{"theme": "light" | "dark" | "system"}``). It is not data and carries no permissions; the web app reads it
from ``GET /api/v1/me`` and writes it with ``PUT /api/v1/me/preferences``.

Revision ID: 0011
Revises: 0010
Create Date: 2026-10-21
"""

from migrations.helpers import run

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run(
        """
        ALTER TABLE iam.users ADD COLUMN preferences jsonb NOT NULL DEFAULT '{}'
          CHECK (jsonb_typeof(preferences) = 'object');
        COMMENT ON COLUMN iam.users.preferences IS 'Interface preferences chosen by the user (theme); not data';
        """
    )


def downgrade() -> None:
    run("ALTER TABLE iam.users DROP COLUMN IF EXISTS preferences")
