"""Knowledge, retrieval and gold examples (EPIC-03: DATA-042, DATA-043, DATA-044; US-056, US-057, US-100 – US-103).

* ``rag.knowledge_documents`` — one approved model version's definitions (auto-approved), or an uploaded
  reference document that waits in ``pending_review`` until an Admin approves it. Only ``approved`` documents
  are ever retrieved.
* ``rag.knowledge_chunks`` — ≤ 2,000-character chunks with a generated ``tsvector`` and a 1024-dimension
  embedding (HNSW, cosine). The embedding is NULL while the embedding service is down; full-text still works.
* ``rag.gold_examples`` — verified question → Query Spec pairs: few-shot context for AG-03 and the
  ground-truth suite. Creators suggest, Admins approve.
* ``rag.eval_runs`` / ``rag.eval_results`` — evaluation suite runs, pass rate by tag, spec diffs.
* ``ops.query_executions.surface`` may be ``evaluation`` (gold-set result checks).
* ``rag.policies`` — the tenant's pass-rate threshold (DEC-11 working position 0.80, warn only).
* ``rag.f_eval_tenants()`` / ``rag.f_reembed_tenants()`` — cross-tenant lists for the nightly ``eval_gold_set``
  job and the re-embed sweep.

Revision ID: 0015
Revises: 0014
Create Date: 2026-10-21
"""

from alembic import context

from migrations.helpers import run

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None

TABLES = ["rag.knowledge_documents", "rag.knowledge_chunks", "rag.gold_examples", "rag.eval_runs",
          "rag.eval_results", "rag.policies"]


def _app_role() -> str:
    role = context.get_x_argument(as_dictionary=True).get("tela.app_role", "tela_app")
    if not role.replace("_", "").isalnum():
        raise ValueError("invalid app role name")
    return role


def upgrade() -> None:
    run(
        """
        CREATE SCHEMA IF NOT EXISTS rag;

        CREATE TABLE rag.knowledge_documents (
          id                 uuid PRIMARY KEY,
          tenant_id          uuid NOT NULL REFERENCES iam.tenants(id) ON DELETE RESTRICT,
          connection_id      uuid REFERENCES conn.connections(id) ON DELETE CASCADE,
          model_version_id   uuid REFERENCES model.model_versions(id) ON DELETE CASCADE,
          source_kind        text NOT NULL CHECK (source_kind IN ('model_definition', 'glossary', 'uploaded_doc',
                                                                  'chart_pattern', 'gold_summary')),
          title              text NOT NULL CHECK (char_length(title) BETWEEN 1 AND 200),
          object_key         text,
          original_filename  text,
          content_type       text,
          size_bytes         bigint CHECK (size_bytes IS NULL OR size_bytes >= 0),
          content_hash       text NOT NULL,
          review_status      text NOT NULL DEFAULT 'pending_review'
                               CHECK (review_status IN ('pending_review', 'approved', 'rejected', 'retired')),
          index_status       text NOT NULL DEFAULT 'queued'
                               CHECK (index_status IN ('queued', 'indexing', 'indexed', 'failed')),
          index_error        text,
          scan_flags         jsonb NOT NULL DEFAULT '{}',
          injection_flagged  boolean NOT NULL DEFAULT false,
          acknowledged_by    uuid REFERENCES iam.users(id),
          reviewed_by        uuid REFERENCES iam.users(id),
          reviewed_at        timestamptz,
          review_note        text CHECK (review_note IS NULL OR char_length(review_note) <= 1000),
          embedding_model    text,
          chunk_count        integer NOT NULL DEFAULT 0,
          indexed_at         timestamptz,
          created_by         uuid REFERENCES iam.users(id),
          created_at         timestamptz NOT NULL DEFAULT now(),
          updated_at         timestamptz NOT NULL DEFAULT now(),
          CONSTRAINT ck_model_definition_version CHECK (source_kind <> 'model_definition' OR model_version_id IS NOT NULL),
          CONSTRAINT ck_injection_ack CHECK (review_status <> 'approved' OR NOT injection_flagged
                                             OR acknowledged_by IS NOT NULL)
        );
        CREATE UNIQUE INDEX uq_model_definition_doc ON rag.knowledge_documents (model_version_id)
          WHERE source_kind = 'model_definition';
        CREATE INDEX ix_knowledge_documents_status ON rag.knowledge_documents (tenant_id, review_status);

        CREATE TABLE rag.knowledge_chunks (
          id           uuid PRIMARY KEY,
          tenant_id    uuid NOT NULL REFERENCES iam.tenants(id) ON DELETE RESTRICT,
          document_id  uuid NOT NULL REFERENCES rag.knowledge_documents(id) ON DELETE CASCADE,
          chunk_no     integer NOT NULL CHECK (chunk_no >= 0),
          content      text NOT NULL CHECK (char_length(content) BETWEEN 1 AND 2000),
          metadata     jsonb NOT NULL DEFAULT '{}',
          tsv          tsvector GENERATED ALWAYS AS (to_tsvector('english', content)) STORED,
          embedding    vector(1024),
          created_at   timestamptz NOT NULL DEFAULT now(),
          CONSTRAINT uq_chunk_no UNIQUE (document_id, chunk_no)
        );
        CREATE INDEX ix_knowledge_chunks_embedding ON rag.knowledge_chunks
          USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64);
        CREATE INDEX ix_knowledge_chunks_tsv ON rag.knowledge_chunks USING gin (tsv);
        CREATE INDEX ix_knowledge_chunks_document ON rag.knowledge_chunks (tenant_id, document_id);

        CREATE TABLE rag.gold_examples (
          id                           uuid PRIMARY KEY,
          tenant_id                    uuid NOT NULL REFERENCES iam.tenants(id) ON DELETE RESTRICT,
          connection_id                uuid NOT NULL REFERENCES conn.connections(id) ON DELETE CASCADE,
          model_version_id             uuid NOT NULL REFERENCES model.model_versions(id) ON DELETE CASCADE,
          question                     text NOT NULL CHECK (char_length(question) BETWEEN 3 AND 500),
          expected_spec                jsonb NOT NULL,
          expected_result_fingerprint  jsonb,
          test_scope_role_id           uuid REFERENCES iam.roles(id) ON DELETE SET NULL,
          tags                         text[] NOT NULL DEFAULT '{}',
          use_for_fewshot              boolean NOT NULL DEFAULT true,
          status                       text NOT NULL DEFAULT 'suggested'
                                         CHECK (status IN ('suggested', 'approved', 'rejected')),
          origin                       text NOT NULL DEFAULT 'manual' CHECK (origin IN ('manual', 'answer', 'pack_draft')),
          source_turn_id               uuid,
          notes                        text CHECK (notes IS NULL OR char_length(notes) <= 1000),
          embedding                    vector(1024),
          embedding_model              text,
          suggested_by                 uuid REFERENCES iam.users(id),
          verified_by                  uuid REFERENCES iam.users(id),
          verified_at                  timestamptz,
          created_at                   timestamptz NOT NULL DEFAULT now(),
          updated_at                   timestamptz NOT NULL DEFAULT now(),
          CONSTRAINT ck_fingerprint_scope CHECK (expected_result_fingerprint IS NULL OR test_scope_role_id IS NOT NULL)
        );
        CREATE UNIQUE INDEX uq_gold_question ON rag.gold_examples (connection_id, lower(question))
          WHERE status <> 'rejected';
        CREATE INDEX ix_gold_examples_embedding ON rag.gold_examples
          USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64);
        CREATE INDEX ix_gold_examples_status ON rag.gold_examples (tenant_id, status);

        CREATE TABLE rag.eval_runs (
          id                uuid PRIMARY KEY,
          tenant_id         uuid NOT NULL REFERENCES iam.tenants(id) ON DELETE RESTRICT,
          connection_id     uuid REFERENCES conn.connections(id) ON DELETE CASCADE,
          trigger           text NOT NULL CHECK (trigger IN ('manual', 'nightly', 'model_approval', 'llm_settings')),
          tags              text[] NOT NULL DEFAULT '{}',
          status            text NOT NULL DEFAULT 'queued' CHECK (status IN ('queued', 'running', 'done', 'failed')),
          total             integer NOT NULL DEFAULT 0,
          passed            integer NOT NULL DEFAULT 0,
          pass_rate         numeric(5,4),
          recall_at_8       numeric(5,4),
          by_tag            jsonb NOT NULL DEFAULT '{}',
          threshold         numeric(5,4) NOT NULL,
          below_threshold   boolean,
          planner           jsonb NOT NULL DEFAULT '{}',
          error             text,
          run_as            uuid NOT NULL REFERENCES iam.users(id),
          started_at        timestamptz,
          finished_at       timestamptz,
          created_at        timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX ix_eval_runs_recent ON rag.eval_runs (tenant_id, created_at DESC);

        CREATE TABLE rag.eval_results (
          id                uuid PRIMARY KEY,
          tenant_id         uuid NOT NULL REFERENCES iam.tenants(id) ON DELETE RESTRICT,
          run_id            uuid NOT NULL REFERENCES rag.eval_runs(id) ON DELETE CASCADE,
          gold_example_id   uuid REFERENCES rag.gold_examples(id) ON DELETE SET NULL,
          question          text NOT NULL,
          tags              text[] NOT NULL DEFAULT '{}',
          passed            boolean NOT NULL,
          planner           text,
          actual_spec       jsonb,
          diff              jsonb NOT NULL DEFAULT '[]',
          retrieval_hit     boolean,
          fingerprint_ok    boolean,
          error             text,
          created_at        timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX ix_eval_results_run ON rag.eval_results (run_id);

        CREATE TABLE rag.policies (
          tenant_id       uuid PRIMARY KEY REFERENCES iam.tenants(id) ON DELETE RESTRICT,
          eval_threshold  numeric(5,4) NOT NULL DEFAULT 0.80 CHECK (eval_threshold BETWEEN 0 AND 1),
          updated_at      timestamptz NOT NULL DEFAULT now(),
          updated_by      uuid
        );

        CREATE FUNCTION rag.f_eval_tenants() RETURNS TABLE (tenant_id uuid, run_as uuid)
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = rag, pg_temp AS $$
          -- The nightly suite runs as the Admin who last started a manual run (their scope applies).
          SELECT DISTINCT ON (g.tenant_id) g.tenant_id, r.run_as
          FROM rag.gold_examples g
          JOIN rag.eval_runs r ON r.tenant_id = g.tenant_id AND r.trigger = 'manual'
          WHERE g.status = 'approved'
          ORDER BY g.tenant_id, r.created_at DESC
        $$;
        REVOKE ALL ON FUNCTION rag.f_eval_tenants() FROM PUBLIC;

        CREATE FUNCTION rag.f_reembed_tenants() RETURNS TABLE (tenant_id uuid)
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = rag, pg_temp AS $$
          SELECT c.tenant_id FROM rag.knowledge_chunks c
          WHERE c.embedding IS NULL AND NOT coalesce((c.metadata->>'injection_flagged')::boolean, false)
          UNION
          SELECT g.tenant_id FROM rag.gold_examples g WHERE g.embedding IS NULL AND g.status <> 'rejected'
        $$;
        REVOKE ALL ON FUNCTION rag.f_reembed_tenants() FROM PUBLIC;

        ALTER TABLE ops.query_executions DROP CONSTRAINT query_executions_surface_check;
        ALTER TABLE ops.query_executions ADD CONSTRAINT query_executions_surface_check
          CHECK (surface IN ('ask','widget_run','drill','export','custom_sql_check','custom_sql_run','filter_values','precheck','preview','certification_preview','evaluation'));
        """
    )
    for table in ("rag.knowledge_documents", "rag.gold_examples"):
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
            GRANT USAGE ON SCHEMA rag TO {role};
            GRANT SELECT, INSERT, UPDATE, DELETE ON rag.knowledge_documents, rag.knowledge_chunks,
              rag.gold_examples, rag.eval_runs, rag.eval_results, rag.policies TO {role};
            GRANT EXECUTE ON FUNCTION rag.f_eval_tenants(), rag.f_reembed_tenants() TO {role};
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
            REVOKE ALL ON ALL TABLES IN SCHEMA rag FROM {role};
            REVOKE ALL ON ALL FUNCTIONS IN SCHEMA rag FROM {role};
            REVOKE USAGE ON SCHEMA rag FROM {role};
          END IF;
        END $$;
        DROP FUNCTION IF EXISTS rag.f_eval_tenants(), rag.f_reembed_tenants();
        DELETE FROM ops.query_executions WHERE surface = 'evaluation';
        ALTER TABLE ops.query_executions DROP CONSTRAINT query_executions_surface_check;
        ALTER TABLE ops.query_executions ADD CONSTRAINT query_executions_surface_check CHECK (surface IN ('ask','widget_run','drill','export','custom_sql_check','custom_sql_run','filter_values','precheck','preview','certification_preview'));
        DROP TABLE IF EXISTS rag.eval_results, rag.eval_runs, rag.policies, rag.gold_examples,
          rag.knowledge_chunks, rag.knowledge_documents;
        DROP SCHEMA IF EXISTS rag;
        """
    )
