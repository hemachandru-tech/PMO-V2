"""Agent run log (ENH-024, US-225 to US-229; DEC-25).

* ``ops.agent_runs`` — one row per run of the agents: an Ask turn, a model assistant proposal, a pack mapping
  suggestion, a certification rephrase, a custom SQL draft, a report plan or a canvas edit. ``agents`` lists the
  agents (AG-01 to AG-10) that took part; ``outcome`` is ``running`` until it ends.
* ``ops.agent_steps`` — the run's ordered steps (rule, retrieve, llm_call, validate, retry, fallback, decision,
  query, error, done). An ``llm_call`` step holds the text the gateway sent to the model after masking and the raw
  reply (DEC-25): kept only here, never in logs or ``ops.llm_invocations``, and cleared after the tenant's retention.
* ``ops.agent_trace_settings`` — per tenant: keep LLM text or not (default on) and for how many days (default 14).
* ``ops.f_agent_run_tenants()`` — narrow SECURITY DEFINER lookup of the tenants the retention sweep has work in.

Revision ID: 0047
Revises: 0046
Create Date: 2026-11-05
"""

from alembic import context

from migrations.helpers import run

revision = "0047"
down_revision = "0046"
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
        CREATE TABLE ops.agent_runs (
          id                 uuid PRIMARY KEY,
          tenant_id          uuid NOT NULL REFERENCES iam.tenants(id) ON DELETE RESTRICT,
          kind               text NOT NULL CHECK (kind IN ('ask', 'model_assist', 'pack_mapping', 'certification',
                               'custom_sql', 'report_plan', 'canvas_edit', 'agent')),
          title              text NOT NULL DEFAULT '',
          agents             text[] NOT NULL DEFAULT '{}',
          turn_id            uuid,
          object_ref         text,
          user_id            uuid,
          outcome            text NOT NULL DEFAULT 'running' CHECK (outcome IN ('running', 'ok', 'fallback',
                               'clarify', 'refused', 'error')),
          capture_text       boolean NOT NULL DEFAULT true,
          llm_calls          integer NOT NULL DEFAULT 0,
          prompt_tokens      integer NOT NULL DEFAULT 0,
          completion_tokens  integer NOT NULL DEFAULT 0,
          started_at         timestamptz NOT NULL DEFAULT now(),
          ended_at           timestamptz,
          duration_ms        integer
        );
        CREATE INDEX ix_agent_runs_tenant_time ON ops.agent_runs (tenant_id, started_at DESC, id DESC);
        CREATE INDEX ix_agent_runs_turn ON ops.agent_runs (turn_id) WHERE turn_id IS NOT NULL;
        CREATE INDEX ix_agent_runs_agents ON ops.agent_runs USING gin (agents);

        CREATE TABLE ops.agent_steps (
          run_id             uuid NOT NULL REFERENCES ops.agent_runs(id) ON DELETE CASCADE,
          seq                integer NOT NULL,
          tenant_id          uuid NOT NULL REFERENCES iam.tenants(id) ON DELETE RESTRICT,
          agent              text,
          kind               text NOT NULL CHECK (kind IN ('rule', 'retrieve', 'llm_call', 'validate', 'retry',
                               'fallback', 'decision', 'query', 'error', 'done')),
          label              text NOT NULL,
          status             text NOT NULL DEFAULT 'ok' CHECK (status IN ('ok', 'failed', 'blocked')),
          started_at         timestamptz NOT NULL DEFAULT now(),
          duration_ms        integer,
          detail             jsonb NOT NULL DEFAULT '{}',
          task               text,
          provider           text,
          model              text,
          prompt_tokens      integer,
          completion_tokens  integer,
          prompt_text        jsonb,
          reply_text         text,
          text_cut           boolean NOT NULL DEFAULT false,
          text_cleared_at    timestamptz,
          PRIMARY KEY (run_id, seq)
        );
        CREATE INDEX ix_agent_steps_text ON ops.agent_steps (tenant_id, started_at)
          WHERE prompt_text IS NOT NULL OR reply_text IS NOT NULL;

        CREATE TABLE ops.agent_trace_settings (
          tenant_id          uuid PRIMARY KEY REFERENCES iam.tenants(id) ON DELETE RESTRICT,
          capture_text       boolean NOT NULL DEFAULT true,
          text_days          integer NOT NULL DEFAULT 14 CHECK (text_days BETWEEN 1 AND 90),
          updated_at         timestamptz NOT NULL DEFAULT now(),
          updated_by         uuid
        );

        CREATE FUNCTION ops.f_agent_run_tenants() RETURNS TABLE (tenant_id uuid)
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = ops, pg_temp AS $$
          SELECT DISTINCT r.tenant_id FROM ops.agent_runs r
        $$;
        REVOKE ALL ON FUNCTION ops.f_agent_run_tenants() FROM PUBLIC;
        """
    )
    for table in ("ops.agent_runs", "ops.agent_steps", "ops.agent_trace_settings"):
        run(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        run(f"CREATE POLICY tenant_isolation ON {table} "
            "USING (tenant_id = util.current_tenant()) WITH CHECK (tenant_id = util.current_tenant())")
    role = _app_role()
    run(
        f"""
        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN
            GRANT SELECT, INSERT, UPDATE, DELETE ON ops.agent_runs, ops.agent_steps, ops.agent_trace_settings
              TO {role};
            GRANT EXECUTE ON FUNCTION ops.f_agent_run_tenants() TO {role};
          END IF;
        END $$;
        """
    )


def downgrade() -> None:
    run(
        """
        DROP FUNCTION IF EXISTS ops.f_agent_run_tenants();
        DROP TABLE IF EXISTS ops.agent_trace_settings;
        DROP TABLE IF EXISTS ops.agent_steps;
        DROP TABLE IF EXISTS ops.agent_runs;
        """
    )
