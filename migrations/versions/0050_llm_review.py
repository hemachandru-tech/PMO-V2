"""LLM review on every agent run (ENH-026, US-239; FEAT-076; DEC-28).

* ``ops.llm_settings.llm_review`` — each agent's rules draft is sent to the LLM, which confirms, corrects or asks.
  The API sets it on for a hosted provider and off for a local one when the provider changes (DEC-28 point 3); the
  Admin may turn it either way. Existing tenants start off, so nothing changes until an Admin saves.

Revision ID: 0050
Revises: 0049
Create Date: 2026-11-06
"""

from migrations.helpers import run

revision = "0050"
down_revision = "0049"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run("ALTER TABLE ops.llm_settings ADD COLUMN llm_review boolean NOT NULL DEFAULT false;")


def downgrade() -> None:
    run("ALTER TABLE ops.llm_settings DROP COLUMN IF EXISTS llm_review;")
