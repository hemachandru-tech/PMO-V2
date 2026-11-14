"""Dashboard-building knowledge (ENH-007, US-159).

``rag.knowledge_documents.source_kind`` accepts ``dashboard_rule`` (the pack's dashboard rules) and
``report_blueprint`` (the report catalogue's screens as layouts). Like ``chart_pattern``, both are built from a
pack file for each approved model version and follow the model (approved with it, retired with older versions).

Revision ID: 0026
Revises: 0025
Create Date: 2026-10-27
"""

from migrations.helpers import run

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run(
        """
        ALTER TABLE rag.knowledge_documents DROP CONSTRAINT knowledge_documents_source_kind_check;
        ALTER TABLE rag.knowledge_documents ADD CONSTRAINT knowledge_documents_source_kind_check
          CHECK (source_kind IN ('model_definition', 'glossary', 'uploaded_doc', 'chart_pattern', 'gold_summary',
                                 'dashboard_rule', 'report_blueprint'));
        """
    )


def downgrade() -> None:
    run(
        """
        DELETE FROM rag.knowledge_documents WHERE source_kind IN ('dashboard_rule', 'report_blueprint');
        ALTER TABLE rag.knowledge_documents DROP CONSTRAINT knowledge_documents_source_kind_check;
        ALTER TABLE rag.knowledge_documents ADD CONSTRAINT knowledge_documents_source_kind_check
          CHECK (source_kind IN ('model_definition', 'glossary', 'uploaded_doc', 'chart_pattern', 'gold_summary'));
        """
    )
