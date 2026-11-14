"""Curation queue from Ask's misses (ENH-016, US-198; FEAT-066 / FEAT-014).

* ``rag.curation_proposals`` — one proposal per connection, kind and wording: a measure synonym (``phrase`` →
  ``measure_ref``), a glossary term (``phrase`` means ``meaning``) or a gold example (``question`` → ``spec``). It
  waits as ``pending`` until an Admin approves it (``applied`` names what was made: the model version, the glossary
  document or the gold example) or dismisses it; a dismissed wording is not proposed again. ``options`` are the
  measures the planner considered, for the Admin to pick from.
* ``rag.curation_evidence`` — the misses behind a proposal: a refused question, a clarification choice, a repaired
  plan or a down-vote marked wrong or "not what I asked", each with the question asked (never rows or numbers). A
  source is read once (``source``, ``source_id``), so collecting again only adds new misses.

Revision ID: 0042
Revises: 0041
Create Date: 2026-11-02
"""

from alembic import context

from migrations.helpers import run

revision = "0042"
down_revision = "0041"
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
        CREATE TABLE rag.curation_proposals (
          id             uuid PRIMARY KEY,
          tenant_id      uuid NOT NULL REFERENCES iam.tenants(id) ON DELETE RESTRICT,
          connection_id  uuid NOT NULL REFERENCES conn.connections(id) ON DELETE CASCADE,
          kind           text NOT NULL CHECK (kind IN ('measure_synonym', 'glossary_term', 'gold_example')),
          key            text NOT NULL CHECK (char_length(key) BETWEEN 1 AND 500),
          question       text NOT NULL CHECK (char_length(question) BETWEEN 1 AND 2000),
          phrase         text CHECK (phrase IS NULL OR char_length(phrase) <= 100),
          measure_ref    text CHECK (measure_ref IS NULL OR char_length(measure_ref) <= 200),
          meaning        text CHECK (meaning IS NULL OR char_length(meaning) <= 200),
          spec           jsonb,
          options        jsonb NOT NULL DEFAULT '[]',
          needs_fix      boolean NOT NULL DEFAULT false,
          status         text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'approved', 'dismissed')),
          applied        jsonb,
          reviewed_by    uuid REFERENCES iam.users(id),
          reviewed_at    timestamptz,
          review_note    text CHECK (review_note IS NULL OR char_length(review_note) <= 500),
          created_at     timestamptz NOT NULL DEFAULT now(),
          updated_at     timestamptz NOT NULL DEFAULT now(),
          CONSTRAINT ck_curation_phrase CHECK (kind = 'gold_example' OR phrase IS NOT NULL),
          CONSTRAINT ck_curation_reviewed CHECK ((status = 'pending') = (reviewed_at IS NULL))
        );
        CREATE UNIQUE INDEX uq_curation_key ON rag.curation_proposals (tenant_id, connection_id, kind, key);
        CREATE INDEX ix_curation_status ON rag.curation_proposals (tenant_id, status, updated_at DESC);
        CREATE TRIGGER trg_curation_proposals_updated_at BEFORE UPDATE ON rag.curation_proposals
          FOR EACH ROW EXECUTE FUNCTION util.set_updated_at();

        CREATE TABLE rag.curation_evidence (
          id           uuid PRIMARY KEY,
          tenant_id    uuid NOT NULL REFERENCES iam.tenants(id) ON DELETE RESTRICT,
          proposal_id  uuid NOT NULL REFERENCES rag.curation_proposals(id) ON DELETE CASCADE,
          source       text NOT NULL CHECK (source IN ('refused', 'clarification', 'repair', 'feedback')),
          source_id    uuid NOT NULL,
          turn_id      uuid REFERENCES chat.turns(id) ON DELETE SET NULL,
          question     text NOT NULL CHECK (char_length(question) BETWEEN 1 AND 2000),
          detail       text CHECK (detail IS NULL OR char_length(detail) <= 500),
          created_at   timestamptz NOT NULL DEFAULT now()
        );
        CREATE UNIQUE INDEX uq_curation_evidence ON rag.curation_evidence (tenant_id, source, source_id, proposal_id);
        CREATE INDEX ix_curation_evidence_proposal ON rag.curation_evidence (proposal_id, created_at DESC);

        ALTER TABLE rag.curation_proposals ENABLE ROW LEVEL SECURITY;
        CREATE POLICY tenant_isolation ON rag.curation_proposals
          USING (tenant_id = util.current_tenant()) WITH CHECK (tenant_id = util.current_tenant());
        ALTER TABLE rag.curation_evidence ENABLE ROW LEVEL SECURITY;
        CREATE POLICY tenant_isolation ON rag.curation_evidence
          USING (tenant_id = util.current_tenant()) WITH CHECK (tenant_id = util.current_tenant());
        """
    )
    role = _app_role()
    run(
        f"""
        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN
            GRANT SELECT, INSERT, UPDATE ON rag.curation_proposals TO {role};
            GRANT SELECT, INSERT ON rag.curation_evidence TO {role};
          END IF;
        END $$;
        """
    )


def downgrade() -> None:
    run("DROP TABLE IF EXISTS rag.curation_evidence; DROP TABLE IF EXISTS rag.curation_proposals;")
