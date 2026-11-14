"""Gold set 2.0 scoring (ENH-013, US-184; FEAT-064 / FEAT-015).

* ``rag.gold_examples.turns`` — the earlier messages of a multi-turn example, in order (the first a question, the
  rest refinements); the example's ``question`` is the last message.
* ``rag.eval_runs``: ``pass_rate`` is now execution accuracy (same rows as the expected spec); ``spec_match_rate``
  keeps the old measure; ``mrr``, ``ndcg_at_8``, ``recall_by_kind``, ``by_error`` (error taxonomy) and
  ``value_linking`` (filter values right, for questions with filters).
* ``rag.eval_results``: ``spec_match``, ``exec_match``, ``error_class``, ``reciprocal_rank``, ``ndcg_at_8``,
  ``values_linked``.

Revision ID: 0031
Revises: 0030
Create Date: 2026-10-28
"""

from migrations.helpers import run

revision = "0031"
down_revision = "0030"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run(
        """
        ALTER TABLE rag.gold_examples ADD COLUMN turns text[] NOT NULL DEFAULT '{}'
          CHECK (cardinality(turns) <= 5);

        ALTER TABLE rag.eval_runs
          ADD COLUMN spec_match_rate numeric(5,4),
          ADD COLUMN mrr             numeric(5,4),
          ADD COLUMN ndcg_at_8       numeric(5,4),
          ADD COLUMN recall_by_kind  jsonb NOT NULL DEFAULT '{}',
          ADD COLUMN by_error        jsonb NOT NULL DEFAULT '{}',
          ADD COLUMN value_linking   jsonb NOT NULL DEFAULT '{}';
        UPDATE rag.eval_runs SET spec_match_rate = pass_rate;

        ALTER TABLE rag.eval_results
          ADD COLUMN spec_match      boolean,
          ADD COLUMN exec_match      boolean,
          ADD COLUMN error_class     text CHECK (error_class IS NULL OR error_class IN ('linking', 'measure',
                                       'filter', 'time', 'shape', 'over_clarification', 'result', 'error')),
          ADD COLUMN reciprocal_rank numeric(5,4),
          ADD COLUMN ndcg_at_8       numeric(5,4),
          ADD COLUMN values_linked   boolean;
        UPDATE rag.eval_results SET spec_match = passed;
        """
    )


def downgrade() -> None:
    run(
        """
        ALTER TABLE rag.eval_results DROP COLUMN spec_match, DROP COLUMN exec_match, DROP COLUMN error_class,
          DROP COLUMN reciprocal_rank, DROP COLUMN ndcg_at_8, DROP COLUMN values_linked;
        ALTER TABLE rag.eval_runs DROP COLUMN spec_match_rate, DROP COLUMN mrr, DROP COLUMN ndcg_at_8,
          DROP COLUMN recall_by_kind, DROP COLUMN by_error, DROP COLUMN value_linking;
        ALTER TABLE rag.gold_examples DROP COLUMN turns;
        """
    )
