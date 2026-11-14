"""Analysis recipes as knowledge (ENH-012, US-179).

``rag.knowledge_documents.source_kind`` accepts ``analysis_recipe`` (the pack's named, parameterised analyses:
variance bridge, Pareto 80/20, aging buckets …). Like ``report_blueprint``, one document is built from a pack file for
each approved model version and follows the model (approved with it, retired with older versions).

Revision ID: 0038
Revises: 0037
Create Date: 2026-10-29
"""

from migrations.helpers import run

revision = "0038"
down_revision = "0037"
branch_labels = None
depends_on = None

OLD = "'model_definition', 'glossary', 'uploaded_doc', 'chart_pattern', 'gold_summary', 'dashboard_rule', " \
      "'report_blueprint'"


def upgrade() -> None:
    run(
        f"""
        ALTER TABLE rag.knowledge_documents DROP CONSTRAINT knowledge_documents_source_kind_check;
        ALTER TABLE rag.knowledge_documents ADD CONSTRAINT knowledge_documents_source_kind_check
          CHECK (source_kind IN ({OLD}, 'analysis_recipe'));
        """
    )


def downgrade() -> None:
    run(
        f"""
        DELETE FROM rag.knowledge_documents WHERE source_kind = 'analysis_recipe';
        ALTER TABLE rag.knowledge_documents DROP CONSTRAINT knowledge_documents_source_kind_check;
        ALTER TABLE rag.knowledge_documents ADD CONSTRAINT knowledge_documents_source_kind_check
          CHECK (source_kind IN ({OLD}));
        """
    )
