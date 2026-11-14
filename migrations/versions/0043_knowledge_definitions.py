"""Business definitions from knowledge (ENH-017, US-203; FEAT-067 / FEAT-014 / FEAT-062).

* ``rag.knowledge_definitions`` — a business definition read from an uploaded document or glossary, one per
  connection, model version, document and term: the term, its meaning, the passage it came from (``chunk_id``, kept
  as ``passage`` when the chunks are replaced), and, where the model can express it, a rule in the measure
  expression format (``rule``: entity, field, operator, values). ``gaps`` say why a definition is text only (a field
  or value the model lacks); ``expressible`` is a rule with no gaps. ``measure_ref`` is the certified measure of the
  same term; ``checks`` the Admin's trial counts (the measure's figure beside the definition's, ``conflict`` when
  they differ). A proposal waits as ``pending`` until an Admin approves or rejects it (FEAT-014 review gate);
  ``retired`` when its passage is gone or a newer model version carries it.

Revision ID: 0043
Revises: 0042
Create Date: 2026-11-03
"""

from alembic import context

from migrations.helpers import run

revision = "0043"
down_revision = "0042"
branch_labels = None
depends_on = None


def _app_role() -> str:
    role = context.get_x_argument(as_dictionary=True).get("tela.app_role", "tela_app")
    if not role.replace("_", "").isalnum():
        raise ValueError("invalid app role name")
    return role


def upgrade() -> None:
    run(
        """
        CREATE TABLE rag.knowledge_definitions (
          id                uuid PRIMARY KEY,
          tenant_id         uuid NOT NULL REFERENCES iam.tenants(id) ON DELETE RESTRICT,
          connection_id     uuid NOT NULL REFERENCES conn.connections(id) ON DELETE CASCADE,
          model_version_id  uuid NOT NULL REFERENCES model.model_versions(id) ON DELETE CASCADE,
          document_id       uuid NOT NULL REFERENCES rag.knowledge_documents(id) ON DELETE CASCADE,
          chunk_id          uuid REFERENCES rag.knowledge_chunks(id) ON DELETE SET NULL,
          chunk_no          integer NOT NULL CHECK (chunk_no >= 0),
          heading           text CHECK (heading IS NULL OR char_length(heading) <= 200),
          passage           text NOT NULL CHECK (char_length(passage) BETWEEN 1 AND 2000),
          term              text NOT NULL CHECK (char_length(term) BETWEEN 1 AND 80),
          term_key          text NOT NULL CHECK (char_length(term_key) BETWEEN 1 AND 80),
          meaning           text NOT NULL CHECK (char_length(meaning) BETWEEN 1 AND 600),
          rule              jsonb,
          gaps              jsonb NOT NULL DEFAULT '[]',
          expressible       boolean NOT NULL DEFAULT false,
          measure_ref       text CHECK (measure_ref IS NULL OR char_length(measure_ref) <= 200),
          checks            jsonb,
          status            text NOT NULL DEFAULT 'pending'
                              CHECK (status IN ('pending', 'approved', 'rejected', 'retired')),
          reviewed_by       uuid REFERENCES iam.users(id) ON DELETE SET NULL,
          reviewed_at       timestamptz,
          review_note       text CHECK (review_note IS NULL OR char_length(review_note) <= 500),
          created_at        timestamptz NOT NULL DEFAULT now(),
          updated_at        timestamptz NOT NULL DEFAULT now(),
          CONSTRAINT ck_definition_expressible CHECK (NOT expressible OR (rule IS NOT NULL AND gaps = '[]'))
        );
        CREATE UNIQUE INDEX uq_knowledge_definition
          ON rag.knowledge_definitions (tenant_id, connection_id, model_version_id, document_id, term_key);
        CREATE INDEX ix_knowledge_definitions_use
          ON rag.knowledge_definitions (tenant_id, connection_id, model_version_id, status);
        CREATE INDEX ix_knowledge_definitions_document ON rag.knowledge_definitions (document_id);
        CREATE TRIGGER trg_knowledge_definitions_updated_at BEFORE UPDATE ON rag.knowledge_definitions
          FOR EACH ROW EXECUTE FUNCTION util.set_updated_at();

        ALTER TABLE rag.knowledge_definitions ENABLE ROW LEVEL SECURITY;
        CREATE POLICY tenant_isolation ON rag.knowledge_definitions
          USING (tenant_id = util.current_tenant()) WITH CHECK (tenant_id = util.current_tenant());
        """
    )
    role = _app_role()
    run(
        f"""
        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN
            GRANT SELECT, INSERT, UPDATE, DELETE ON rag.knowledge_definitions TO {role};
          END IF;
        END $$;
        """
    )


def downgrade() -> None:
    run("DROP TABLE IF EXISTS rag.knowledge_definitions;")
