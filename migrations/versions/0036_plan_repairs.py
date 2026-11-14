"""US-181: spec repair log.

* ``rag.plan_repairs`` — one row per reason an AG-03 plan was sent back for repair (ENH-013, FEAT-064): the plan it
  refused (1 = AG-03's first plan, 2 and 3 = its repairs), the kind (unknown measure or dimension, not reachable,
  value not found, invalid extra, other validator refusal, compiler refusal), the reference it names, the typed text
  without filter values, and whether a later plan passed (never for the third). The top reasons show what context
  the planner lacks. Kept with the source; a deleted turn keeps its rows (``turn_id`` set to NULL).

Revision ID: 0036
Revises: 0035
Create Date: 2026-10-29
"""

from alembic import context

from migrations.helpers import run

revision = "0036"
down_revision = "0035"
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
        CREATE TABLE rag.plan_repairs (
          id                uuid PRIMARY KEY,
          tenant_id         uuid NOT NULL REFERENCES iam.tenants(id) ON DELETE RESTRICT,
          connection_id     uuid NOT NULL REFERENCES conn.connections(id) ON DELETE CASCADE,
          model_version_id  uuid NOT NULL REFERENCES model.model_versions(id) ON DELETE CASCADE,
          turn_id           uuid REFERENCES chat.turns(id) ON DELETE SET NULL,
          attempt           smallint NOT NULL CHECK (attempt BETWEEN 1 AND 3),
          kind              text NOT NULL CHECK (kind IN ('unknown_measure', 'unknown_dimension', 'not_reachable',
                                                          'value_not_found', 'invalid_extra', 'invalid', 'compile')),
          ref               text CHECK (ref IS NULL OR char_length(ref) <= 200),
          detail            text NOT NULL CHECK (char_length(detail) BETWEEN 1 AND 500),
          repaired          boolean NOT NULL,
          created_at        timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX ix_plan_repairs_connection ON rag.plan_repairs (tenant_id, connection_id, created_at DESC);
        ALTER TABLE rag.plan_repairs ENABLE ROW LEVEL SECURITY;
        CREATE POLICY tenant_isolation ON rag.plan_repairs
          USING (tenant_id = util.current_tenant()) WITH CHECK (tenant_id = util.current_tenant());
        """
    )
    role = _app_role()
    run(
        f"""
        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN
            GRANT SELECT, INSERT ON rag.plan_repairs TO {role};
          END IF;
        END $$;
        """
    )


def downgrade() -> None:
    run("DROP TABLE rag.plan_repairs")
