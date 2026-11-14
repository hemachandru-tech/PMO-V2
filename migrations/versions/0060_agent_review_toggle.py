"""LLM review on or off per agent, from the Agents screen (ENH-030, US-268; DEC-28).

* ``ops.llm_settings.review_agents`` — an Admin's choice per agent, ``{"AG-05": true, "AG-06": false}``. An agent left
  out follows the deployment default (``TELA_LLM_REVIEW_OFF_AGENTS``, AG-05 and AG-06 off). The tenant's
  ``llm_review`` switch still has to be on for any agent to review.

Revision ID: 0060
Revises: 0059
Create Date: 2026-11-13
"""

from migrations.helpers import run

revision = "0060"
down_revision = "0059"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run("ALTER TABLE ops.llm_settings ADD COLUMN review_agents jsonb NOT NULL DEFAULT '{}'::jsonb;")


def downgrade() -> None:
    run("ALTER TABLE ops.llm_settings DROP COLUMN IF EXISTS review_agents;")
