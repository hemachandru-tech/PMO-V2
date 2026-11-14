"""Operations tables (DATA-045..051): audit, LLM ledger, executions, cache index,
exports, outbox and notifications.

* audit_events and query_executions are range-partitioned by month with a
  DEFAULT partition; util.ensure_month_partitions() creates monthly partitions
  ahead of time (scheduled by the worker).
* audit_events is append-only with a per-tenant hash chain (tamper evidence).
* The outbox relay claims events through SECURITY DEFINER functions so the
  worker never needs an RLS bypass role.

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-21
"""

from migrations.helpers import run

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run(
        """
        CREATE TABLE ops.audit_events (
          id              bigint GENERATED ALWAYS AS IDENTITY,
          tenant_id       uuid NOT NULL REFERENCES iam.tenants(id) ON DELETE RESTRICT,
          occurred_at     timestamptz NOT NULL DEFAULT now(),
          actor_id        uuid,
          actor_ip        inet,
          action          text NOT NULL CHECK (action ~ '^[a-z_]+(\\.[a-z_]+)+$'),
          object_type     text NOT NULL,
          object_id       text,
          outcome         text NOT NULL CHECK (outcome IN ('allow','deny','success','failure')),
          severity        text NOT NULL DEFAULT 'normal' CHECK (severity IN ('normal','high','security')),
          correlation_id  text NOT NULL,
          details         jsonb NOT NULL DEFAULT '{}',
          prev_hash       text,
          row_hash        text NOT NULL,
          PRIMARY KEY (id, occurred_at)
        ) PARTITION BY RANGE (occurred_at);
        CREATE TABLE ops.audit_events_default PARTITION OF ops.audit_events DEFAULT;
        CREATE INDEX ix_audit_tenant_time ON ops.audit_events (tenant_id, occurred_at DESC);
        CREATE INDEX ix_audit_object ON ops.audit_events (tenant_id, object_type, object_id);

        -- Hash chain per tenant. An advisory lock serialises writers of one tenant.
        CREATE OR REPLACE FUNCTION ops.audit_hash_chain() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE
          v_prev text;
        BEGIN
          PERFORM pg_advisory_xact_lock(hashtext('audit:' || NEW.tenant_id::text));
          SELECT row_hash INTO v_prev FROM ops.audit_events
            WHERE tenant_id = NEW.tenant_id ORDER BY id DESC LIMIT 1;
          NEW.prev_hash := v_prev;
          NEW.row_hash := encode(digest(
              coalesce(v_prev, '') || '|' || NEW.tenant_id::text || '|' || NEW.occurred_at::text || '|' ||
              coalesce(NEW.actor_id::text, '') || '|' || NEW.action || '|' || NEW.object_type || '|' ||
              coalesce(NEW.object_id, '') || '|' || NEW.outcome || '|' || NEW.details::text,
              'sha256'), 'hex');
          RETURN NEW;
        END $$;
        CREATE TRIGGER trg_audit_hash_chain BEFORE INSERT ON ops.audit_events
          FOR EACH ROW EXECUTE FUNCTION ops.audit_hash_chain();
        CREATE TRIGGER trg_audit_immutable BEFORE UPDATE OR DELETE ON ops.audit_events
          FOR EACH ROW EXECUTE FUNCTION util.forbid_update();

        CREATE TABLE ops.llm_invocations (
          id                 uuid PRIMARY KEY,
          tenant_id          uuid NOT NULL REFERENCES iam.tenants(id) ON DELETE RESTRICT,
          user_id            uuid,
          turn_id            uuid,
          agent              text NOT NULL,
          task               text NOT NULL,
          provider           text NOT NULL,
          model              text NOT NULL,
          payload_hash       text NOT NULL,
          scanner_verdict    text NOT NULL CHECK (scanner_verdict IN ('clean','blocked')),
          blocked_reason     text,
          prompt_tokens      integer,
          completion_tokens  integer,
          est_cost_usd       numeric(12,6),
          latency_ms         integer,
          status             text NOT NULL CHECK (status IN ('ok','blocked','invalid_output','timeout','error')),
          created_at         timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX ix_llm_invocations_tenant_time ON ops.llm_invocations (tenant_id, created_at DESC);

        CREATE TABLE ops.query_executions (
          id              uuid NOT NULL,
          tenant_id       uuid NOT NULL REFERENCES iam.tenants(id) ON DELETE RESTRICT,
          connection_id   uuid NOT NULL,
          surface         text NOT NULL CHECK (surface IN ('ask','widget_run','drill','export','custom_sql_check',
                            'custom_sql_run','filter_values','precheck','preview','certification_preview')),
          turn_id         uuid,
          widget_id       uuid,
          user_id         uuid,
          scope_hash      text NOT NULL,
          statement_hash  text NOT NULL,
          statement_text  text,
          row_count       integer,
          truncated       boolean NOT NULL DEFAULT false,
          duration_ms     integer,
          outcome         text NOT NULL CHECK (outcome IN ('ok','timeout','rejected','error')),
          cache_hit       boolean NOT NULL DEFAULT false,
          created_at      timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (id, created_at)
        ) PARTITION BY RANGE (created_at);
        CREATE TABLE ops.query_executions_default PARTITION OF ops.query_executions DEFAULT;
        CREATE INDEX ix_query_exec_connection ON ops.query_executions (connection_id, created_at DESC);

        CREATE TABLE ops.result_cache_index (
          cache_key      text PRIMARY KEY,
          tenant_id      uuid NOT NULL REFERENCES iam.tenants(id) ON DELETE RESTRICT,
          connection_id  uuid NOT NULL,
          widget_id      uuid,
          as_of          timestamptz NOT NULL,
          expires_at     timestamptz NOT NULL,
          created_at     timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX ix_cache_index_connection ON ops.result_cache_index (connection_id);
        CREATE INDEX ix_cache_index_expiry ON ops.result_cache_index (expires_at);

        CREATE TABLE ops.export_jobs (
          id                    uuid PRIMARY KEY,
          tenant_id             uuid NOT NULL REFERENCES iam.tenants(id) ON DELETE RESTRICT,
          requested_by          uuid NOT NULL REFERENCES iam.users(id),
          object_type           text NOT NULL CHECK (object_type IN ('dashboard','report','widget','drill_list')),
          object_id             uuid NOT NULL,
          dashboard_version_id  uuid,
          format                text NOT NULL CHECK (format IN ('pdf','xlsx')),
          filters               jsonb NOT NULL DEFAULT '[]',
          status                text NOT NULL DEFAULT 'queued'
                                  CHECK (status IN ('queued','running','done','failed','expired')),
          object_key            text,
          expires_at            timestamptz,
          error                 text,
          created_at            timestamptz NOT NULL DEFAULT now(),
          updated_at            timestamptz NOT NULL DEFAULT now(),
          created_by            uuid
        );
        CREATE TRIGGER trg_export_jobs_updated_at BEFORE UPDATE ON ops.export_jobs
          FOR EACH ROW EXECUTE FUNCTION util.set_updated_at();

        CREATE TABLE ops.outbox_events (
          id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
          tenant_id     uuid NOT NULL REFERENCES iam.tenants(id) ON DELETE RESTRICT,
          topic         text NOT NULL CHECK (topic ~ '^[a-z_]+(\\.[a-z_]+)+$'),
          aggregate_id  uuid NOT NULL,
          payload       jsonb NOT NULL,
          created_at    timestamptz NOT NULL DEFAULT now(),
          published_at  timestamptz,
          attempts      integer NOT NULL DEFAULT 0,
          last_error    text,
          dead_at       timestamptz
        );
        CREATE INDEX ix_outbox_pending ON ops.outbox_events (created_at)
          WHERE published_at IS NULL AND dead_at IS NULL;

        CREATE TABLE ops.notifications (
          id          uuid PRIMARY KEY,
          tenant_id   uuid NOT NULL REFERENCES iam.tenants(id) ON DELETE RESTRICT,
          user_id     uuid NOT NULL REFERENCES iam.users(id) ON DELETE CASCADE,
          kind        text NOT NULL,
          title       text NOT NULL,
          body        text NOT NULL,
          link        text,
          read_at     timestamptz,
          created_at  timestamptz NOT NULL DEFAULT now(),
          updated_at  timestamptz NOT NULL DEFAULT now(),
          created_by  uuid
        );
        CREATE INDEX ix_notifications_user ON ops.notifications (user_id, read_at);
        """
    )

    # Monthly partitions, created ahead by the maintenance worker (US-005).
    run(
        """
        CREATE OR REPLACE FUNCTION util.ensure_month_partitions(p_table regclass, p_months_ahead int DEFAULT 2)
        RETURNS int LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
        DECLARE
          v_start date := date_trunc('month', now())::date;
          v_from date;
          v_to date;
          v_name text;
          v_schema text;
          v_rel text;
          v_created int := 0;
        BEGIN
          SELECT n.nspname, c.relname INTO v_schema, v_rel
            FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace WHERE c.oid = p_table;
          FOR i IN 0..p_months_ahead LOOP
            v_from := (v_start + make_interval(months => i))::date;
            v_to := (v_from + interval '1 month')::date;
            v_name := v_rel || '_' || to_char(v_from, 'YYYYMM');
            IF NOT EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                           WHERE n.nspname = v_schema AND c.relname = v_name) THEN
              EXECUTE format('CREATE TABLE %I.%I PARTITION OF %I.%I FOR VALUES FROM (%L) TO (%L)',
                             v_schema, v_name, v_schema, v_rel, v_from, v_to);
              v_created := v_created + 1;
            END IF;
          END LOOP;
          RETURN v_created;
        END $$;
        """
    )
    run("SELECT util.ensure_month_partitions('ops.audit_events', 2)")
    run("SELECT util.ensure_month_partitions('ops.query_executions', 2)")

    # Outbox relay helpers: claim a batch across tenants, then mark the result.
    run(
        """
        CREATE OR REPLACE FUNCTION ops.f_claim_outbox(p_batch int DEFAULT 100)
        RETURNS SETOF ops.outbox_events
        LANGUAGE sql SECURITY DEFINER SET search_path = ops, pg_temp AS $$
          UPDATE ops.outbox_events SET attempts = attempts + 1
          WHERE id IN (
            SELECT id FROM ops.outbox_events
            WHERE published_at IS NULL AND dead_at IS NULL
            ORDER BY created_at
            LIMIT p_batch
            FOR UPDATE SKIP LOCKED)
          RETURNING *
        $$;

        CREATE OR REPLACE FUNCTION ops.f_mark_outbox(p_id bigint, p_ok boolean, p_error text DEFAULT NULL,
                                                     p_max_attempts int DEFAULT 10)
        RETURNS void LANGUAGE sql SECURITY DEFINER SET search_path = ops, pg_temp AS $$
          UPDATE ops.outbox_events SET
            published_at = CASE WHEN p_ok THEN now() ELSE published_at END,
            last_error   = CASE WHEN p_ok THEN NULL ELSE left(p_error, 500) END,
            dead_at      = CASE WHEN NOT p_ok AND attempts >= p_max_attempts THEN now() ELSE dead_at END
          WHERE id = p_id
        $$;

        CREATE OR REPLACE FUNCTION ops.f_outbox_lag_seconds() RETURNS double precision
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = ops, pg_temp AS $$
          SELECT coalesce(extract(epoch FROM now() - min(created_at)), 0)
          FROM ops.outbox_events WHERE published_at IS NULL AND dead_at IS NULL
        $$;
        """
    )


def downgrade() -> None:
    run("DROP FUNCTION IF EXISTS ops.f_outbox_lag_seconds()")
    run("DROP FUNCTION IF EXISTS ops.f_mark_outbox(bigint, boolean, text, int)")
    run("DROP FUNCTION IF EXISTS ops.f_claim_outbox(int)")
    run("DROP FUNCTION IF EXISTS util.ensure_month_partitions(regclass, int)")
    for table in (
        "notifications",
        "outbox_events",
        "export_jobs",
        "result_cache_index",
        "query_executions",
        "llm_invocations",
        "audit_events",
    ):
        run(f"DROP TABLE IF EXISTS ops.{table} CASCADE")
    run("DROP FUNCTION IF EXISTS ops.audit_hash_chain()")
