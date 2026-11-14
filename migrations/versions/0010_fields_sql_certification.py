"""Personal fields, custom SQL and certification (P2: EPIC-07, EPIC-08; DATA-024, DATA-025, DATA-036 to DATA-038).

* ``content.personal_fields`` — a Creator's grouping, band or cross-table measure, pinned to the model version
  it was built on; applied at query level only.
* ``content.custom_sql_definitions`` — a checked custom SQL statement (read-only SELECT) for one connection.
* ``content.widgets`` gains ``definition_refs`` (the personal fields / custom SQL a widget uses) and
  ``custom_sql_id``; ``certification_state`` may now be ``custom_sql``.
* ``gov`` — certification requests (one pending per item), insert-only decisions, look-alike findings, the
  tenant certification policy (self-certification off by default) and an embedding cache for look-alikes.

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-21
"""

from alembic import context

from migrations.helpers import run

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None

TABLES = [
    "content.personal_fields", "content.custom_sql_definitions", "gov.certification_requests",
    "gov.certification_decisions", "gov.lookalike_findings", "gov.policies", "gov.definition_embeddings",
]
CERT_STATUS = "('personal', 'pending', 'certified', 'rejected', 'merged')"


def _app_role() -> str:
    role = context.get_x_argument(as_dictionary=True).get("tela.app_role", "tela_app")
    if not role.replace("_", "").isalnum():
        raise ValueError("invalid app role name")
    return role


def upgrade() -> None:
    run(
        f"""
        CREATE SCHEMA IF NOT EXISTS gov;

        CREATE TABLE content.personal_fields (
          id                uuid PRIMARY KEY,
          tenant_id         uuid NOT NULL REFERENCES iam.tenants(id) ON DELETE RESTRICT,
          owner_id          uuid NOT NULL REFERENCES iam.users(id) ON DELETE RESTRICT,
          connection_id     uuid NOT NULL REFERENCES conn.connections(id) ON DELETE CASCADE,
          model_version_id  uuid NOT NULL REFERENCES model.model_versions(id) ON DELETE RESTRICT,
          kind              text NOT NULL CHECK (kind IN ('grouping', 'band', 'join_measure')),
          key               text NOT NULL CHECK (key ~ '^[a-z_][a-z0-9_]{{0,62}}$'),
          label             text NOT NULL CHECK (char_length(label) BETWEEN 1 AND 120),
          definition        jsonb NOT NULL,
          definition_hash   text NOT NULL,
          plain_rule        text NOT NULL,
          cert_status       text NOT NULL DEFAULT 'personal' CHECK (cert_status IN {CERT_STATUS}),
          certified_as      text,
          broken_reason     text,
          deleted_at        timestamptz,
          created_at        timestamptz NOT NULL DEFAULT now(),
          updated_at        timestamptz NOT NULL DEFAULT now()
        );
        CREATE UNIQUE INDEX uq_personal_field_key ON content.personal_fields (owner_id, model_version_id, key)
          WHERE deleted_at IS NULL;
        CREATE UNIQUE INDEX uq_personal_field_label ON content.personal_fields (owner_id, lower(label))
          WHERE deleted_at IS NULL;

        CREATE TABLE content.custom_sql_definitions (
          id                uuid PRIMARY KEY,
          tenant_id         uuid NOT NULL REFERENCES iam.tenants(id) ON DELETE RESTRICT,
          owner_id          uuid NOT NULL REFERENCES iam.users(id) ON DELETE RESTRICT,
          connection_id     uuid NOT NULL REFERENCES conn.connections(id) ON DELETE RESTRICT,
          model_version_id  uuid NOT NULL REFERENCES model.model_versions(id) ON DELETE RESTRICT,
          title             text NOT NULL CHECK (char_length(title) BETWEEN 1 AND 200),
          dialect           text NOT NULL CHECK (dialect IN ('postgres', 'mysql', 'tsql', 'duckdb')),
          sql_text          text NOT NULL CHECK (char_length(sql_text) BETWEEN 1 AND 20000),
          ast_hash          text NOT NULL,
          has_page_filters  boolean NOT NULL,
          last_check        jsonb,
          drafted_by_ai     boolean NOT NULL DEFAULT false,
          cert_status       text NOT NULL DEFAULT 'personal' CHECK (cert_status IN {CERT_STATUS}),
          certified_as      text,
          deleted_at        timestamptz,
          created_at        timestamptz NOT NULL DEFAULT now(),
          updated_at        timestamptz NOT NULL DEFAULT now()
        );

        ALTER TABLE content.widgets
          ADD COLUMN definition_refs  jsonb NOT NULL DEFAULT '[]',
          ADD COLUMN custom_sql_id    uuid REFERENCES content.custom_sql_definitions(id) ON DELETE RESTRICT;
        ALTER TABLE content.widgets DROP CONSTRAINT IF EXISTS widgets_certification_state_check;
        ALTER TABLE content.widgets ADD CONSTRAINT widgets_certification_state_check
          CHECK (certification_state IN ('certified', 'personal', 'exploratory', 'custom_sql'));
        CREATE INDEX ix_widgets_definition_refs ON content.widgets USING gin (definition_refs);

        CREATE TABLE gov.certification_requests (
          id                       uuid PRIMARY KEY,
          tenant_id                uuid NOT NULL REFERENCES iam.tenants(id) ON DELETE RESTRICT,
          item_type                text NOT NULL CHECK (item_type IN ('grouping', 'band', 'join_measure', 'custom_sql')),
          item_id                  uuid NOT NULL,
          item_snapshot            jsonb NOT NULL,
          definition_hash          text NOT NULL,
          trigger                  text NOT NULL CHECK (trigger IN ('shared', 'publish_bound', 'manual')),
          note                     text CHECK (note IS NULL OR char_length(note) <= 1000),
          requested_by             uuid NOT NULL REFERENCES iam.users(id),
          status                   text NOT NULL DEFAULT 'pending'
                                     CHECK (status IN ('pending', 'certified', 'merged', 'rejected', 'withdrawn')),
          depends_on               uuid[] NOT NULL DEFAULT '{{}}',
          usage_snapshot           jsonb NOT NULL DEFAULT '{{}}',
          changed_since_requested  boolean NOT NULL DEFAULT false,
          created_at               timestamptz NOT NULL DEFAULT now(),
          updated_at               timestamptz NOT NULL DEFAULT now()
        );
        CREATE UNIQUE INDEX uq_cert_pending ON gov.certification_requests (item_type, item_id) WHERE status = 'pending';
        CREATE INDEX ix_cert_status ON gov.certification_requests (tenant_id, status);

        CREATE TABLE gov.certification_decisions (
          id                   uuid PRIMARY KEY,
          tenant_id            uuid NOT NULL REFERENCES iam.tenants(id) ON DELETE RESTRICT,
          request_id           uuid NOT NULL REFERENCES gov.certification_requests(id) ON DELETE RESTRICT,
          decision             text NOT NULL CHECK (decision IN ('certify', 'merge', 'reject')),
          merged_into_ref      jsonb,
          note                 text CHECK (note IS NULL OR char_length(note) <= 1000),
          definition_snapshot  jsonb NOT NULL,
          decided_by           uuid NOT NULL REFERENCES iam.users(id),
          decided_at           timestamptz NOT NULL DEFAULT now(),
          CONSTRAINT ck_reject_note CHECK (decision <> 'reject' OR coalesce(char_length(note), 0) > 0),
          CONSTRAINT uq_one_decision UNIQUE (request_id)
        );
        CREATE TRIGGER trg_cert_decisions_immutable BEFORE UPDATE OR DELETE ON gov.certification_decisions
          FOR EACH ROW EXECUTE FUNCTION util.forbid_update();

        CREATE TABLE gov.lookalike_findings (
          id                  uuid PRIMARY KEY,
          tenant_id           uuid NOT NULL REFERENCES iam.tenants(id) ON DELETE RESTRICT,
          request_id          uuid NOT NULL REFERENCES gov.certification_requests(id) ON DELETE CASCADE,
          similar_kind        text NOT NULL CHECK (similar_kind IN ('measure', 'dimension', 'custom_sql')),
          similar_ref         text NOT NULL,
          similarity          numeric(5,4) NOT NULL CHECK (similarity BETWEEN 0 AND 1),
          reasons             text[] NOT NULL DEFAULT '{{}}',
          difference_summary  text NOT NULL,
          result_delta        jsonb,
          created_at          timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX ix_lookalike_request ON gov.lookalike_findings (request_id);

        CREATE TABLE gov.policies (
          tenant_id                 uuid PRIMARY KEY REFERENCES iam.tenants(id) ON DELETE RESTRICT,
          allow_self_certification  boolean NOT NULL DEFAULT false,
          updated_at                timestamptz NOT NULL DEFAULT now(),
          updated_by                uuid
        );

        CREATE TABLE gov.definition_embeddings (
          tenant_id   uuid NOT NULL REFERENCES iam.tenants(id) ON DELETE RESTRICT,
          text_hash   text NOT NULL,
          model       text NOT NULL,
          embedding   vector(1024) NOT NULL,
          created_at  timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (tenant_id, model, text_hash)
        );
        """
    )
    for table in ("content.personal_fields", "content.custom_sql_definitions", "gov.certification_requests"):
        name = table.split(".")[1]
        run(f"CREATE TRIGGER trg_{name}_updated_at BEFORE UPDATE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION util.set_updated_at()")
    for table in TABLES:
        run(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        run(f"CREATE POLICY tenant_isolation ON {table} "
            "USING (tenant_id = util.current_tenant()) WITH CHECK (tenant_id = util.current_tenant())")
    role = _app_role()
    run(
        f"""
        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN
            GRANT USAGE ON SCHEMA gov TO {role};
            GRANT SELECT, INSERT, UPDATE, DELETE ON content.personal_fields, content.custom_sql_definitions,
              gov.certification_requests, gov.lookalike_findings, gov.policies, gov.definition_embeddings TO {role};
            GRANT SELECT, INSERT ON gov.certification_decisions TO {role};
          END IF;
        END $$;
        """
    )


def downgrade() -> None:
    role = _app_role()
    run(
        f"""
        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN
            REVOKE ALL ON ALL TABLES IN SCHEMA gov FROM {role};
            REVOKE USAGE ON SCHEMA gov FROM {role};
          END IF;
        END $$;
        DROP TABLE IF EXISTS gov.definition_embeddings, gov.policies, gov.lookalike_findings,
          gov.certification_decisions, gov.certification_requests;
        DROP SCHEMA IF EXISTS gov;
        DELETE FROM content.report_items WHERE widget_id IN (SELECT id FROM content.widgets WHERE custom_sql_id IS NOT NULL);
        DELETE FROM content.widgets WHERE custom_sql_id IS NOT NULL;
        UPDATE content.widgets SET certification_state = 'personal' WHERE certification_state = 'custom_sql';
        DROP INDEX IF EXISTS content.ix_widgets_definition_refs;
        ALTER TABLE content.widgets DROP COLUMN IF EXISTS custom_sql_id, DROP COLUMN IF EXISTS definition_refs;
        ALTER TABLE content.widgets DROP CONSTRAINT IF EXISTS widgets_certification_state_check;
        ALTER TABLE content.widgets ADD CONSTRAINT widgets_certification_state_check
          CHECK (certification_state IN ('certified', 'personal', 'exploratory'));
        DROP TABLE IF EXISTS content.custom_sql_definitions, content.personal_fields;
        """
    )
