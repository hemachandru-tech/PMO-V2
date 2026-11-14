"""Named conditions and order of authority (ENH-028, US-252; FEAT-078 / FEAT-007).

* ``rag.named_conditions`` — a business term stored once as a condition Tela can run, per connection and model
  version: the term (``term``, ``term_key`` as definitions match it), its ``synonyms``, the ``entity`` it selects rows
  of and the ``condition`` in the measure-condition format (``tela_query_spec.expr.Condition`` JSON). ``source`` says
  where it came from: ``document`` (an approved definition read from a document, US-203), ``definition_sheet`` (a row
  of the business-context spreadsheet, US-251) or ``pack_measure`` (the rule of a certified count measure, approved
  with the model). ``status``: ``proposed`` (carried to a model version that lacks its field, with ``gaps``),
  ``approved``, ``retired``.
* ``measure_ref`` is the certified measure of the same term. When the two select different rows ``conflict`` holds
  both rules and a row count each; Ask uses the certified measure and notes the conflict until the Admin chooses
  (``prefer``: ``condition`` or ``measure``).
* Backfill: every approved definition with a rule becomes an approved ``document`` condition. Definitions approved
  before this story were applied over a disagreeing measure (US-204), so a backfilled conflict keeps that choice
  (``prefer = 'condition'``).

Revision ID: 0055
Revises: 0054
Create Date: 2026-11-12
"""

from alembic import context

from migrations.helpers import run

revision = "0055"
down_revision = "0054"
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
        CREATE TABLE rag.named_conditions (
          id                uuid PRIMARY KEY,
          tenant_id         uuid NOT NULL REFERENCES iam.tenants(id) ON DELETE RESTRICT,
          connection_id     uuid NOT NULL REFERENCES conn.connections(id) ON DELETE CASCADE,
          model_version_id  uuid NOT NULL REFERENCES model.model_versions(id) ON DELETE CASCADE,
          term              text NOT NULL CHECK (char_length(term) BETWEEN 1 AND 80),
          term_key          text NOT NULL CHECK (char_length(term_key) BETWEEN 1 AND 80),
          synonyms          jsonb NOT NULL DEFAULT '[]',
          entity            text NOT NULL CHECK (char_length(entity) BETWEEN 1 AND 63),
          condition         jsonb NOT NULL,
          source            text NOT NULL CHECK (source IN ('document', 'definition_sheet', 'pack_measure')),
          definition_id     uuid REFERENCES rag.knowledge_definitions(id) ON DELETE SET NULL,
          measure_ref       text CHECK (measure_ref IS NULL OR char_length(measure_ref) <= 200),
          status            text NOT NULL DEFAULT 'proposed' CHECK (status IN ('proposed', 'approved', 'retired')),
          gaps              jsonb NOT NULL DEFAULT '[]',
          conflict          jsonb,
          prefer            text CHECK (prefer IS NULL OR prefer IN ('condition', 'measure')),
          decided_by        uuid REFERENCES iam.users(id) ON DELETE SET NULL,
          decided_at        timestamptz,
          note              text CHECK (note IS NULL OR char_length(note) <= 500),
          created_at        timestamptz NOT NULL DEFAULT now(),
          updated_at        timestamptz NOT NULL DEFAULT now()
        );
        CREATE UNIQUE INDEX uq_named_condition
          ON rag.named_conditions (tenant_id, connection_id, model_version_id, term_key);
        CREATE INDEX ix_named_conditions_use
          ON rag.named_conditions (tenant_id, connection_id, model_version_id, status);
        CREATE INDEX ix_named_conditions_definition ON rag.named_conditions (definition_id);
        CREATE TRIGGER trg_named_conditions_updated_at BEFORE UPDATE ON rag.named_conditions
          FOR EACH ROW EXECUTE FUNCTION util.set_updated_at();

        ALTER TABLE rag.named_conditions ENABLE ROW LEVEL SECURITY;
        CREATE POLICY tenant_isolation ON rag.named_conditions
          USING (tenant_id = util.current_tenant()) WITH CHECK (tenant_id = util.current_tenant());

        INSERT INTO rag.named_conditions (id, tenant_id, connection_id, model_version_id, term, term_key, synonyms,
            entity, condition, source, definition_id, measure_ref, status, conflict, prefer, decided_by, decided_at)
        SELECT DISTINCT ON (d.tenant_id, d.connection_id, d.model_version_id, d.term_key)
               gen_random_uuid(), d.tenant_id, d.connection_id, d.model_version_id, d.term, d.term_key, '[]',
               d.rule->>'entity',
               jsonb_build_object('field', d.rule->'field', 'op', d.rule->'op', 'values', d.rule->'values'),
               'document', d.id, d.measure_ref, 'approved',
               CASE WHEN coalesce((d.checks->>'conflict')::boolean, false) THEN jsonb_build_object(
                 'condition', jsonb_build_object('figure', d.checks->'figure'),
                 'measure', d.checks->'measure', 'checked_at', d.checks->'checked_at') END,
               CASE WHEN coalesce((d.checks->>'conflict')::boolean, false) THEN 'condition' END,
               d.reviewed_by, d.reviewed_at
        FROM rag.knowledge_definitions d
        WHERE d.status = 'approved' AND d.expressible AND d.rule IS NOT NULL
        ORDER BY d.tenant_id, d.connection_id, d.model_version_id, d.term_key, d.reviewed_at DESC NULLS LAST;
        """
    )
    role = _app_role()
    run(
        f"""
        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN
            GRANT SELECT, INSERT, UPDATE, DELETE ON rag.named_conditions TO {role};
          END IF;
        END $$;
        """
    )


def downgrade() -> None:
    run("DROP TABLE IF EXISTS rag.named_conditions;")
