"""Answer feedback (ENH-013, US-183; FEAT-064 / FEAT-018).

* ``chat.answer_feedback`` — a person's thumbs up or down on one of their Ask answers, at most one per person and
  turn (a second vote replaces the first). A down-vote may say why (``wrong`` or ``not_what_i_asked``, "This isn't
  what I asked") with a short comment; it keeps a copy of the question and the Query Spec — never rows, headlines or
  numbers — and waits as ``open`` in the Admin queue until it is ``fixed`` (a gold example made from it,
  ``gold_example_id``) or ``dismissed``. An up-vote is ``none``: counted, never queued.

Revision ID: 0035
Revises: 0034
Create Date: 2026-10-29
"""

from alembic import context

from migrations.helpers import run

revision = "0035"
down_revision = "0034"
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
        CREATE TABLE chat.answer_feedback (
          id               uuid PRIMARY KEY,
          tenant_id        uuid NOT NULL REFERENCES iam.tenants(id) ON DELETE RESTRICT,
          turn_id          uuid NOT NULL REFERENCES chat.turns(id) ON DELETE CASCADE,
          user_id          uuid NOT NULL REFERENCES iam.users(id) ON DELETE CASCADE,
          vote             text NOT NULL CHECK (vote IN ('up', 'down')),
          reason           text CHECK (reason IN ('wrong', 'not_what_i_asked')),
          comment          text CHECK (comment IS NULL OR char_length(comment) <= 500),
          question         text NOT NULL CHECK (char_length(question) BETWEEN 1 AND 2000),
          spec             jsonb,
          connection_id    uuid,
          status           text NOT NULL CHECK (status IN ('none', 'open', 'fixed', 'dismissed')),
          gold_example_id  uuid REFERENCES rag.gold_examples(id) ON DELETE SET NULL,
          resolved_by      uuid REFERENCES iam.users(id),
          resolved_at      timestamptz,
          resolution_note  text CHECK (resolution_note IS NULL OR char_length(resolution_note) <= 500),
          created_at       timestamptz NOT NULL DEFAULT now(),
          updated_at       timestamptz NOT NULL DEFAULT now(),
          CONSTRAINT ck_feedback_up CHECK ((vote = 'up') = (status = 'none')),
          CONSTRAINT ck_feedback_reason CHECK (vote = 'down' OR (reason IS NULL AND comment IS NULL)),
          CONSTRAINT ck_feedback_resolved CHECK ((status IN ('fixed', 'dismissed')) = (resolved_at IS NOT NULL)),
          CONSTRAINT ck_feedback_fixed CHECK (status <> 'fixed' OR gold_example_id IS NOT NULL)
        );
        CREATE UNIQUE INDEX uq_answer_feedback_turn_user ON chat.answer_feedback (turn_id, user_id);
        CREATE INDEX ix_answer_feedback_status ON chat.answer_feedback (status, created_at DESC);
        CREATE TRIGGER trg_answer_feedback_updated_at BEFORE UPDATE ON chat.answer_feedback
          FOR EACH ROW EXECUTE FUNCTION util.set_updated_at();

        ALTER TABLE chat.answer_feedback ENABLE ROW LEVEL SECURITY;
        CREATE POLICY tenant_isolation ON chat.answer_feedback
          USING (tenant_id = util.current_tenant()) WITH CHECK (tenant_id = util.current_tenant());
        """
    )
    role = _app_role()
    run(
        f"""
        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN
            GRANT SELECT, INSERT, UPDATE, DELETE ON chat.answer_feedback TO {role};
          END IF;
        END $$;
        """
    )


def downgrade() -> None:
    run("DROP TABLE IF EXISTS chat.answer_feedback;")
