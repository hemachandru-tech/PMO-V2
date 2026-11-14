"""Readable catalogue objects (BUG-013 follow-up).

* ``conn.connection_objects.readable``: whether the source account may read the object's rows, checked when the
  schema is read. NULL when the engine does not check (its schema lists only what the account can read).

Revision ID: 0040
Revises: 0039
Create Date: 2026-10-30
"""

from migrations.helpers import run

revision = "0040"
down_revision = "0039"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run("ALTER TABLE conn.connection_objects ADD COLUMN readable boolean")


def downgrade() -> None:
    run("ALTER TABLE conn.connection_objects DROP COLUMN readable")
