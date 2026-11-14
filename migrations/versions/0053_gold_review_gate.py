"""Gold gate for LLM review (ENH-026, US-246; FEAT-076; DEC-28 point 3).

* ``rag.eval_runs.review`` — the run plans with LLM review forced on (true) or off (false); null follows the tenant's
  AI settings, as every run did before. Two runs on the same examples, one each way, are the gate.
  ``IF NOT EXISTS``: the dev stack ran this as 0052 before US-238 took that number.
* ``rag.eval_results.llm_calls`` — model calls made while planning the example (review, vote, back-check, wording).

Revision ID: 0053
Revises: 0052
Create Date: 2026-11-06
"""

from migrations.helpers import run

revision = "0053"
down_revision = "0052"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run("ALTER TABLE rag.eval_runs ADD COLUMN IF NOT EXISTS review boolean;")
    run("ALTER TABLE rag.eval_results ADD COLUMN IF NOT EXISTS llm_calls integer;")


def downgrade() -> None:
    run("ALTER TABLE rag.eval_results DROP COLUMN IF EXISTS llm_calls;")
    run("ALTER TABLE rag.eval_runs DROP COLUMN IF EXISTS review;")
