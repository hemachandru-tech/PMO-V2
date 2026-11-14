"""SCIM 2.0 provisioning, sync status and reconcile (EPIC-01 part 2; US-118, US-119; FEAT-004; API-003).

* ``iam.users`` gains the SCIM view of a person: ``scim_external_id`` (Entra object id, the idempotency key),
  ``scim_user_name``, ``scim_name`` (given/family/formatted), ``provisioned_by`` (``login`` or ``scim``),
  ``sessions_revoked_at`` (tokens issued before it are refused: suspension and deprovisioning end sessions) and
  ``deprovisioned_at``. Deprovisioned users keep their row and their content (S03 §7, E-17).
* ``iam.idp_groups.scim_deleted_at`` — Entra deleted the group: memberships go, the role mappings stay so the
  Admin sees what the group used to grant.
* ``iam.scim_tokens`` — one active provisioning bearer token per tenant, stored as a SHA-256 hash.
* ``ops.provisioning_log`` — one row per SCIM call (no payloads), for the sync status page.
* ``ops.provisioning_tasks`` — side effects that may fail and are retried with backoff by ``scim_reconcile``
  (ending the person's identity-provider sessions).
* ``ops.provisioning_runs`` — each reconcile run (hourly or by an Admin) and what it found.
* ``iam.f_tenant_for_scim_token(hash)`` and ``iam.f_scim_tenants()`` — narrow SECURITY DEFINER lookups made before
  a tenant context exists (like ``iam.f_tenant_for_issuer``).

Revision ID: 0018
Revises: 0017
Create Date: 2026-10-22
"""

from alembic import context

from migrations.helpers import run

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None

NEW_TABLES = ("iam.scim_tokens", "ops.provisioning_log", "ops.provisioning_tasks", "ops.provisioning_runs")


def _app_role() -> str:
    role = context.get_x_argument(as_dictionary=True).get("tela.app_role", "tela_app")
    if not role.replace("_", "").isalnum():
        raise ValueError("invalid app role name")
    return role


def upgrade() -> None:
    run(
        """
        ALTER TABLE iam.users
          ADD COLUMN scim_external_id     text CHECK (scim_external_id IS NULL OR char_length(scim_external_id) <= 200),
          ADD COLUMN scim_user_name       text CHECK (scim_user_name IS NULL OR char_length(scim_user_name) <= 320),
          ADD COLUMN scim_name            jsonb NOT NULL DEFAULT '{}',
          ADD COLUMN provisioned_by       text NOT NULL DEFAULT 'login' CHECK (provisioned_by IN ('login', 'scim')),
          ADD COLUMN sessions_revoked_at  timestamptz,
          ADD COLUMN deprovisioned_at     timestamptz;
        CREATE UNIQUE INDEX uq_users_scim_external_id ON iam.users (tenant_id, scim_external_id)
          WHERE scim_external_id IS NOT NULL;
        CREATE UNIQUE INDEX uq_users_scim_user_name ON iam.users (tenant_id, lower(scim_user_name))
          WHERE scim_user_name IS NOT NULL;

        ALTER TABLE iam.idp_groups ADD COLUMN scim_deleted_at timestamptz;

        CREATE TABLE iam.scim_tokens (
          id            uuid PRIMARY KEY,
          tenant_id     uuid NOT NULL REFERENCES iam.tenants(id) ON DELETE RESTRICT,
          token_hash    text NOT NULL UNIQUE CHECK (token_hash ~ '^[0-9a-f]{64}$'),
          prefix        text NOT NULL,
          created_by    uuid REFERENCES iam.users(id),
          created_at    timestamptz NOT NULL DEFAULT now(),
          last_used_at  timestamptz,
          revoked_at    timestamptz,
          revoked_by    uuid REFERENCES iam.users(id)
        );
        CREATE UNIQUE INDEX uq_scim_token_active ON iam.scim_tokens (tenant_id) WHERE revoked_at IS NULL;

        CREATE TABLE ops.provisioning_log (
          id           uuid PRIMARY KEY,
          tenant_id    uuid NOT NULL REFERENCES iam.tenants(id) ON DELETE RESTRICT,
          at           timestamptz NOT NULL DEFAULT now(),
          method       text NOT NULL,
          resource     text NOT NULL,
          resource_id  uuid,
          operation    text NOT NULL,
          status       integer NOT NULL,
          outcome      text NOT NULL CHECK (outcome IN ('ok', 'error')),
          detail       text CHECK (detail IS NULL OR char_length(detail) <= 500)
        );
        CREATE INDEX ix_provisioning_log_at ON ops.provisioning_log (tenant_id, at DESC);

        CREATE TABLE ops.provisioning_tasks (
          id               uuid PRIMARY KEY,
          tenant_id        uuid NOT NULL REFERENCES iam.tenants(id) ON DELETE RESTRICT,
          kind             text NOT NULL CHECK (kind IN ('revoke_sessions')),
          user_id          uuid NOT NULL REFERENCES iam.users(id) ON DELETE CASCADE,
          status           text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'done', 'failed', 'skipped')),
          attempts         integer NOT NULL DEFAULT 0,
          next_attempt_at  timestamptz NOT NULL DEFAULT now(),
          last_error       text CHECK (last_error IS NULL OR char_length(last_error) <= 500),
          alerted          boolean NOT NULL DEFAULT false,
          created_at       timestamptz NOT NULL DEFAULT now(),
          finished_at      timestamptz
        );
        CREATE INDEX ix_provisioning_tasks_due ON ops.provisioning_tasks (tenant_id, next_attempt_at)
          WHERE status = 'pending';

        CREATE TABLE ops.provisioning_runs (
          id            uuid PRIMARY KEY,
          tenant_id     uuid NOT NULL REFERENCES iam.tenants(id) ON DELETE RESTRICT,
          trigger       text NOT NULL CHECK (trigger IN ('schedule', 'manual')),
          triggered_by  uuid REFERENCES iam.users(id),
          started_at    timestamptz NOT NULL DEFAULT now(),
          finished_at   timestamptz,
          outcome       text NOT NULL DEFAULT 'running' CHECK (outcome IN ('running', 'ok', 'drift', 'failed')),
          summary       jsonb NOT NULL DEFAULT '{}',
          error         text CHECK (error IS NULL OR char_length(error) <= 500)
        );
        CREATE INDEX ix_provisioning_runs_at ON ops.provisioning_runs (tenant_id, started_at DESC);

        CREATE FUNCTION iam.f_tenant_for_scim_token(p_hash text)
        RETURNS TABLE (tenant_id uuid, status text, token_id uuid)
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = iam, pg_temp AS $$
          SELECT t.id, t.status, k.id FROM iam.scim_tokens k JOIN iam.tenants t ON t.id = k.tenant_id
          WHERE k.token_hash = p_hash AND k.revoked_at IS NULL
        $$;
        REVOKE ALL ON FUNCTION iam.f_tenant_for_scim_token(text) FROM PUBLIC;

        CREATE FUNCTION iam.f_scim_tenants() RETURNS TABLE (tenant_id uuid)
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = iam, pg_temp AS $$
          SELECT k.tenant_id FROM iam.scim_tokens k JOIN iam.tenants t ON t.id = k.tenant_id
          WHERE k.revoked_at IS NULL AND t.status = 'active'
        $$;
        REVOKE ALL ON FUNCTION iam.f_scim_tenants() FROM PUBLIC;
        """
    )
    for table in NEW_TABLES:
        run(
            f"""
            ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;
            CREATE POLICY tenant_isolation ON {table}
              USING (tenant_id = util.current_tenant()) WITH CHECK (tenant_id = util.current_tenant());
            """
        )
    role = _app_role()
    run(
        f"""
        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN
            GRANT SELECT, INSERT, UPDATE ON iam.scim_tokens, ops.provisioning_tasks, ops.provisioning_runs TO {role};
            GRANT SELECT, INSERT, DELETE ON ops.provisioning_log TO {role};
            GRANT DELETE ON ops.provisioning_runs TO {role};
            GRANT EXECUTE ON FUNCTION iam.f_tenant_for_scim_token(text), iam.f_scim_tenants() TO {role};
          END IF;
        END $$;
        """
    )


def downgrade() -> None:
    run(
        """
        DROP FUNCTION IF EXISTS iam.f_scim_tenants();
        DROP FUNCTION IF EXISTS iam.f_tenant_for_scim_token(text);
        DROP TABLE IF EXISTS ops.provisioning_runs;
        DROP TABLE IF EXISTS ops.provisioning_tasks;
        DROP TABLE IF EXISTS ops.provisioning_log;
        DROP TABLE IF EXISTS iam.scim_tokens;
        ALTER TABLE iam.idp_groups DROP COLUMN IF EXISTS scim_deleted_at;
        DROP INDEX IF EXISTS iam.uq_users_scim_user_name;
        DROP INDEX IF EXISTS iam.uq_users_scim_external_id;
        ALTER TABLE iam.users
          DROP COLUMN IF EXISTS deprovisioned_at, DROP COLUMN IF EXISTS sessions_revoked_at,
          DROP COLUMN IF EXISTS provisioned_by, DROP COLUMN IF EXISTS scim_name,
          DROP COLUMN IF EXISTS scim_user_name, DROP COLUMN IF EXISTS scim_external_id;
        """
    )
