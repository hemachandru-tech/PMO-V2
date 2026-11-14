"""A Direct SQL answer offered as a gold example (ENH-028, US-260; FEAT-078 / FEAT-066).

``rag.curation_evidence.source`` gains ``direct_sql``: "Save as gold example" on a Direct SQL answer. The answer has
SQL, not a Query Spec, so it becomes a gold-example proposal in the curation queue with no spec; its evidence is the
turn and the SQL the model wrote (``detail``, first 500 characters). An Admin writes the spec there and approves it
through the usual gold gate.

Revision ID: 0058
Revises: 0057
Create Date: 2026-11-12
"""

from migrations.helpers import run

revision = "0058"
down_revision = "0057"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run(
        """
        ALTER TABLE rag.curation_evidence DROP CONSTRAINT IF EXISTS curation_evidence_source_check;
        ALTER TABLE rag.curation_evidence ADD CONSTRAINT curation_evidence_source_check
          CHECK (source IN ('refused', 'clarification', 'repair', 'feedback', 'direct_sql'));
        """
    )


def downgrade() -> None:
    run(
        """
        DELETE FROM rag.curation_evidence WHERE source = 'direct_sql';
        ALTER TABLE rag.curation_evidence DROP CONSTRAINT IF EXISTS curation_evidence_source_check;
        ALTER TABLE rag.curation_evidence ADD CONSTRAINT curation_evidence_source_check
          CHECK (source IN ('refused', 'clarification', 'repair', 'feedback'));
        """
    )
