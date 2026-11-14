"""US-175: query rewriting and typed retrieval.

* ``ops.query_executions.surface`` accepts ``field_profile``: the field profiles AG-03 reads (fill rate, most
  frequent in-scope values, date range) go through the one scoped query path and are recorded like any other read.
* ``rag.gold_examples.origin`` accepts ``clarification`` (a question answered after the person chose what they
  meant) and ``freehand`` (a free-hand question whose rule was certified, US-166). Both arrive as ``suggested`` and
  only become few-shots after an Admin approves them (FEAT-014 review gate).
* ``rag.value_synonyms.source`` accepts ``answer``: an approved answer's question that named a filter value its own
  way ("Saudi" for KSA) proposes that synonym, pending review like a clarification's.

Revision ID: 0034
Revises: 0033
Create Date: 2026-10-28
"""

from migrations.helpers import run

revision = "0034"
down_revision = "0033"
branch_labels = None
depends_on = None

SURFACES = ("'ask','widget_run','drill','export','custom_sql_check','custom_sql_run','filter_values','precheck',"
            "'preview','certification_preview','evaluation','value_index'")
ORIGINS = "'manual','answer','pack_draft'"


def upgrade() -> None:
    run(
        f"""
        ALTER TABLE ops.query_executions DROP CONSTRAINT query_executions_surface_check;
        ALTER TABLE ops.query_executions ADD CONSTRAINT query_executions_surface_check
          CHECK (surface IN ({SURFACES},'field_profile'));
        ALTER TABLE rag.gold_examples DROP CONSTRAINT gold_examples_origin_check;
        ALTER TABLE rag.gold_examples ADD CONSTRAINT gold_examples_origin_check
          CHECK (origin IN ({ORIGINS},'clarification','freehand'));
        ALTER TABLE rag.value_synonyms DROP CONSTRAINT value_synonyms_source_check;
        ALTER TABLE rag.value_synonyms ADD CONSTRAINT value_synonyms_source_check
          CHECK (source IN ('pack', 'clarification', 'admin', 'answer'));
        """
    )


def downgrade() -> None:
    run(
        f"""
        DELETE FROM ops.query_executions WHERE surface = 'field_profile';
        ALTER TABLE ops.query_executions DROP CONSTRAINT query_executions_surface_check;
        ALTER TABLE ops.query_executions ADD CONSTRAINT query_executions_surface_check
          CHECK (surface IN ({SURFACES}));
        DELETE FROM rag.gold_examples WHERE origin IN ('clarification', 'freehand');
        ALTER TABLE rag.gold_examples DROP CONSTRAINT gold_examples_origin_check;
        ALTER TABLE rag.gold_examples ADD CONSTRAINT gold_examples_origin_check CHECK (origin IN ({ORIGINS}));
        DELETE FROM rag.value_synonyms WHERE source = 'answer';
        ALTER TABLE rag.value_synonyms DROP CONSTRAINT value_synonyms_source_check;
        ALTER TABLE rag.value_synonyms ADD CONSTRAINT value_synonyms_source_check
          CHECK (source IN ('pack', 'clarification', 'admin'));
        """
    )
