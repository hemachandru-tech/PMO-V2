"""Weighted full-text vector for knowledge chunks (ENH-011, US-174; FEAT-062 / FEAT-014).

* ``rag.knowledge_chunks.tsv`` is rebuilt with weights: the chunk's title or term (``metadata.term``) is A, its
  synonyms (``metadata.synonyms``) B and the text C, so a question naming a blueprint, rule or measure finds that
  chunk before a long one that repeats a common word. Retrieval adds word rarity on top (``knowledge/retrieval.py``).
* When the database has ParadeDB's ``pg_search`` (not in the pgvector image used today), a BM25 index on the chunk
  text is created for the optional ``TELA_KNOWLEDGE_LEXICAL=pg_search`` setting; otherwise nothing is created.

Revision ID: 0032
Revises: 0031
Create Date: 2026-10-28
"""

from migrations.helpers import run

revision = "0032"
down_revision = "0031"
branch_labels = None
depends_on = None

WEIGHTED = """
  setweight(to_tsvector('english', coalesce(metadata->>'term', '')), 'A')
  || setweight(to_tsvector('english', coalesce(metadata->>'synonyms', '')), 'B')
  || setweight(to_tsvector('english', content), 'C')
"""


def upgrade() -> None:
    run(
        f"""
        DROP INDEX rag.ix_knowledge_chunks_tsv;
        ALTER TABLE rag.knowledge_chunks DROP COLUMN tsv;
        ALTER TABLE rag.knowledge_chunks ADD COLUMN tsv tsvector GENERATED ALWAYS AS ({WEIGHTED}) STORED;
        CREATE INDEX ix_knowledge_chunks_tsv ON rag.knowledge_chunks USING gin (tsv);

        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM pg_available_extensions WHERE name = 'pg_search') THEN
            CREATE EXTENSION IF NOT EXISTS pg_search;
            EXECUTE 'CREATE INDEX IF NOT EXISTS ix_knowledge_chunks_bm25 ON rag.knowledge_chunks '
                    'USING bm25 (id, content) WITH (key_field = ''id'')';
          END IF;
        END $$;
        """
    )


def downgrade() -> None:
    run(
        """
        DROP INDEX IF EXISTS rag.ix_knowledge_chunks_bm25;
        DROP INDEX rag.ix_knowledge_chunks_tsv;
        ALTER TABLE rag.knowledge_chunks DROP COLUMN tsv;
        ALTER TABLE rag.knowledge_chunks ADD COLUMN tsv tsvector
          GENERATED ALWAYS AS (to_tsvector('english', content)) STORED;
        CREATE INDEX ix_knowledge_chunks_tsv ON rag.knowledge_chunks USING gin (tsv);
        """
    )
