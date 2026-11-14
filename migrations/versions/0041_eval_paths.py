"""Evaluation per planner path and per set (ENH-016, US-190; FEAT-066 / FEAT-064).

* ``rag.eval_runs``: ``plan_only`` (plan and compare specs, fetch no result rows: a live source), ``by_path``
  (scores per planner path: rules, free-hand, LLM) and ``by_set`` (the original questions beside the held-out
  paraphrases, each with its own ``by_path``).
* ``rag.eval_results``: the planner's ``status`` and ``confidence``, ``confident_wrong`` (a wrong answer at or above
  the confidence threshold), ``clarify_right`` (a clarification offered the expected measure), ``required``
  (required-item recall: the expected measure and dimensions among the linking candidates and the retrieved
  knowledge, also for a refused plan) and ``latency_ms`` (planning time).

Revision ID: 0041
Revises: 0040
Create Date: 2026-11-01
"""

from migrations.helpers import run

revision = "0041"
down_revision = "0040"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run(
        """
        ALTER TABLE rag.eval_runs
          ADD COLUMN plan_only boolean NOT NULL DEFAULT false,
          ADD COLUMN by_path   jsonb NOT NULL DEFAULT '{}',
          ADD COLUMN by_set    jsonb NOT NULL DEFAULT '{}';

        ALTER TABLE rag.eval_results
          ADD COLUMN status          text CHECK (status IS NULL OR status IN ('ok', 'clarify', 'unsupported',
                                       'error')),
          ADD COLUMN confidence      numeric(5,4),
          ADD COLUMN confident_wrong boolean,
          ADD COLUMN clarify_right   boolean,
          ADD COLUMN required        jsonb,
          ADD COLUMN latency_ms      integer;
        """
    )


def downgrade() -> None:
    run(
        """
        ALTER TABLE rag.eval_results DROP COLUMN status, DROP COLUMN confidence, DROP COLUMN confident_wrong,
          DROP COLUMN clarify_right, DROP COLUMN required, DROP COLUMN latency_ms;
        ALTER TABLE rag.eval_runs DROP COLUMN plan_only, DROP COLUMN by_path, DROP COLUMN by_set;
        """
    )
