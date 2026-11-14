"""Dimension value index (ENH-011, US-173; FEAT-062 / FEAT-014).

* ``rag.dimension_values`` — the stored values of a model version's text dimensions (and the labels of computed
  ones), with a normalised spelling (lower case, words only), the approved synonyms (normalised) and an embedding.
  Refreshed when a model version is approved and nightly; read by Ask to link "KSA", "Saudi" or "capex" to the
  stored spelling before planning. It holds every value; the person's scope is applied when a value is linked.
* ``rag.value_synonyms`` — "Saudi Arabia" means region ``KSA``: from the pack (``value_synonyms.yaml``), from a
  clarification choice, or typed by an Admin. Pack and clarification synonyms wait for Admin review (FEAT-014);
  only approved ones are copied into ``rag.dimension_values.synonyms``.
* ``pg_trgm`` for fuzzy spelling matches (``similarity``), with a trigram index on the normalised value.
* ``rag.f_value_tenants()`` — tenants with a current approved model version, for the nightly refresh.
* ``ops.query_executions.surface`` accepts ``value_index`` (the refresh reads distinct values through the one
  query path).

Revision ID: 0033
Revises: 0032
Create Date: 2026-10-28
"""

from alembic import context

from migrations.helpers import run

revision = "0033"
down_revision = "0032"
branch_labels = None
depends_on = None

SURFACES = ("'ask','widget_run','drill','export','custom_sql_check','custom_sql_run','filter_values','precheck',"
            "'preview','certification_preview','evaluation'")


def _app_role() -> str:
    role = context.get_x_argument(as_dictionary=True).get("tela.app_role", "tela_app")
    if not role.replace("_", "").isalnum():
        raise ValueError("invalid app role name")
    return role


def upgrade() -> None:
    run("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    run(
        """
        CREATE TABLE rag.dimension_values (
          id                uuid PRIMARY KEY,
          tenant_id         uuid NOT NULL REFERENCES iam.tenants(id) ON DELETE RESTRICT,
          connection_id     uuid NOT NULL REFERENCES conn.connections(id) ON DELETE CASCADE,
          model_version_id  uuid NOT NULL REFERENCES model.model_versions(id) ON DELETE CASCADE,
          dimension         text NOT NULL CHECK (char_length(dimension) BETWEEN 1 AND 128),
          stored_value      text NOT NULL CHECK (char_length(stored_value) BETWEEN 1 AND 500),
          label             text NOT NULL CHECK (char_length(label) BETWEEN 1 AND 500),
          normalised        text NOT NULL CHECK (char_length(normalised) BETWEEN 1 AND 500),
          synonyms          text[] NOT NULL DEFAULT '{}',
          embedding         vector(1024),
          embedding_model   text,
          refreshed_at      timestamptz NOT NULL DEFAULT now(),
          CONSTRAINT uq_dimension_value UNIQUE (model_version_id, dimension, stored_value)
        );
        CREATE INDEX ix_dimension_values_version ON rag.dimension_values (tenant_id, model_version_id, dimension);
        CREATE INDEX ix_dimension_values_trgm ON rag.dimension_values USING gin (normalised gin_trgm_ops);
        CREATE INDEX ix_dimension_values_synonyms ON rag.dimension_values USING gin (synonyms);
        CREATE INDEX ix_dimension_values_embedding ON rag.dimension_values
          USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64);

        CREATE TABLE rag.value_synonyms (
          id             uuid PRIMARY KEY,
          tenant_id      uuid NOT NULL REFERENCES iam.tenants(id) ON DELETE RESTRICT,
          connection_id  uuid NOT NULL REFERENCES conn.connections(id) ON DELETE CASCADE,
          dimension      text NOT NULL CHECK (char_length(dimension) BETWEEN 1 AND 128),
          stored_value   text NOT NULL CHECK (char_length(stored_value) BETWEEN 1 AND 500),
          synonym        text NOT NULL CHECK (char_length(synonym) BETWEEN 1 AND 120),
          normalised     text NOT NULL CHECK (char_length(normalised) BETWEEN 1 AND 120),
          source         text NOT NULL CHECK (source IN ('pack', 'clarification', 'admin')),
          status         text NOT NULL DEFAULT 'pending_review'
                           CHECK (status IN ('pending_review', 'approved', 'rejected')),
          proposed_by    uuid REFERENCES iam.users(id) ON DELETE SET NULL,
          reviewed_by    uuid REFERENCES iam.users(id) ON DELETE SET NULL,
          reviewed_at    timestamptz,
          note           text CHECK (note IS NULL OR char_length(note) <= 300),
          created_at     timestamptz NOT NULL DEFAULT now(),
          updated_at     timestamptz NOT NULL DEFAULT now(),
          CONSTRAINT uq_value_synonym UNIQUE (connection_id, dimension, stored_value, normalised)
        );
        CREATE INDEX ix_value_synonyms_status ON rag.value_synonyms (tenant_id, connection_id, status);
        CREATE TRIGGER trg_value_synonyms_updated_at BEFORE UPDATE ON rag.value_synonyms
          FOR EACH ROW EXECUTE FUNCTION util.set_updated_at();

        CREATE FUNCTION rag.f_value_tenants() RETURNS TABLE (tenant_id uuid)
          LANGUAGE sql STABLE SECURITY DEFINER SET search_path = model, pg_temp AS $$
          SELECT DISTINCT m.tenant_id FROM model.data_models m WHERE m.current_version_id IS NOT NULL
        $$;
        REVOKE ALL ON FUNCTION rag.f_value_tenants() FROM PUBLIC;
        """
    )
    run(
        f"""
        ALTER TABLE ops.query_executions DROP CONSTRAINT query_executions_surface_check;
        ALTER TABLE ops.query_executions ADD CONSTRAINT query_executions_surface_check
          CHECK (surface IN ({SURFACES},'value_index'));
        """
    )
    for table in ("rag.dimension_values", "rag.value_synonyms"):
        run(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        run(f"CREATE POLICY tenant_isolation ON {table} "
            "USING (tenant_id = util.current_tenant()) WITH CHECK (tenant_id = util.current_tenant())")
    role = _app_role()
    run(
        f"""
        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN
            GRANT SELECT, INSERT, UPDATE, DELETE ON rag.dimension_values, rag.value_synonyms TO {role};
            GRANT EXECUTE ON FUNCTION rag.f_value_tenants() TO {role};
          END IF;
        END $$;
        """
    )


def downgrade() -> None:
    run(
        f"""
        DELETE FROM ops.query_executions WHERE surface = 'value_index';
        ALTER TABLE ops.query_executions DROP CONSTRAINT query_executions_surface_check;
        ALTER TABLE ops.query_executions ADD CONSTRAINT query_executions_surface_check
          CHECK (surface IN ({SURFACES}));
        DROP FUNCTION rag.f_value_tenants();
        DROP TABLE rag.value_synonyms;
        DROP TABLE rag.dimension_values;
        """
    )
