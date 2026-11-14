"""Scope and field policies (DATA-009, DATA-010; RULE-004, RULE-005, RULE-020).

Predicates are structured JSON, never free SQL:
  [{"field": "region", "op": "in", "values": ["KSA"]},
   {"field": "cio", "op": "=", "attr": "cio"}]
An empty list is not allowed; "all rows" must be stated explicitly with
all_rows = true (function_document FEAT-002 inputs).

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-21
"""

from migrations.helpers import run

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run(
        """
        CREATE TABLE iam.scope_policies (
          id             uuid PRIMARY KEY,
          tenant_id      uuid NOT NULL REFERENCES iam.tenants(id) ON DELETE RESTRICT,
          role_id        uuid NOT NULL REFERENCES iam.roles(id) ON DELETE CASCADE,
          connection_id  uuid NOT NULL REFERENCES conn.connections(id) ON DELETE CASCADE,
          entity_key     text NOT NULL,
          all_rows       boolean NOT NULL DEFAULT false,
          predicate      jsonb NOT NULL DEFAULT '[]',
          description    text NOT NULL,
          is_active      boolean NOT NULL DEFAULT true,
          created_at     timestamptz NOT NULL DEFAULT now(),
          updated_at     timestamptz NOT NULL DEFAULT now(),
          created_by     uuid,
          CONSTRAINT ck_predicate_array CHECK (jsonb_typeof(predicate) = 'array'),
          CONSTRAINT ck_all_rows_or_predicate CHECK (all_rows OR jsonb_array_length(predicate) > 0),
          CONSTRAINT ck_all_rows_exclusive CHECK (NOT all_rows OR jsonb_array_length(predicate) = 0)
        );
        CREATE INDEX ix_scope_policies_lookup ON iam.scope_policies (tenant_id, connection_id, role_id);

        CREATE TABLE iam.field_policies (
          id               uuid PRIMARY KEY,
          tenant_id        uuid NOT NULL REFERENCES iam.tenants(id) ON DELETE RESTRICT,
          role_id          uuid NOT NULL REFERENCES iam.roles(id) ON DELETE CASCADE,
          connection_id    uuid NOT NULL REFERENCES conn.connections(id) ON DELETE CASCADE,
          model_field_key  text NOT NULL,
          effect           text NOT NULL DEFAULT 'hide' CHECK (effect IN ('hide','mask')),
          created_at       timestamptz NOT NULL DEFAULT now(),
          updated_at       timestamptz NOT NULL DEFAULT now(),
          created_by       uuid,
          CONSTRAINT uq_field_policy UNIQUE (role_id, connection_id, model_field_key)
        );
        """
    )
    for table in ("scope_policies", "field_policies"):
        run(
            f"CREATE TRIGGER trg_{table}_updated_at BEFORE UPDATE ON iam.{table} "
            "FOR EACH ROW EXECUTE FUNCTION util.set_updated_at()"
        )


def downgrade() -> None:
    run("DROP TABLE IF EXISTS iam.field_policies CASCADE")
    run("DROP TABLE IF EXISTS iam.scope_policies CASCADE")
