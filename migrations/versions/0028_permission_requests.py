"""Free-hand query screen: access requests and last use (ENH-008, US-162; FEAT-060).

* ``iam.permission_requests`` — a person asks for a grantable permission (today ``ask.freehand``) on one connection,
  from Ask. It waits as ``pending`` until an Admin approves it (which creates the grant) or declines it with a reason.
  At most one pending request per person, permission and connection.
* ``iam.permission_grants.last_used_at`` — when the grant was last used (set by the free-hand run, US-163), shown on
  the Admin screen.

Revision ID: 0028
Revises: 0027
Create Date: 2026-10-27
"""

from alembic import context

from migrations.helpers import run

revision = "0028"
down_revision = "0027"
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
        ALTER TABLE iam.permission_grants ADD COLUMN last_used_at timestamptz;

        CREATE TABLE iam.permission_requests (
          id               uuid PRIMARY KEY,
          tenant_id        uuid NOT NULL REFERENCES iam.tenants(id) ON DELETE RESTRICT,
          user_id          uuid NOT NULL REFERENCES iam.users(id) ON DELETE CASCADE,
          permission_key   text NOT NULL CHECK (permission_key IN ('ask.freehand')),
          connection_id    uuid NOT NULL REFERENCES conn.connections(id) ON DELETE CASCADE,
          note             text CHECK (note IS NULL OR char_length(note) <= 500),
          status           text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'approved', 'declined')),
          requested_at     timestamptz NOT NULL DEFAULT now(),
          decided_by       uuid REFERENCES iam.users(id),
          decided_at       timestamptz,
          decision_reason  text CHECK (decision_reason IS NULL OR char_length(decision_reason) <= 500),
          grant_id         uuid REFERENCES iam.permission_grants(id) ON DELETE SET NULL,
          CONSTRAINT ck_request_decided CHECK ((status = 'pending') = (decided_at IS NULL)),
          CONSTRAINT ck_request_declined CHECK (status <> 'declined' OR char_length(btrim(decision_reason)) > 0)
        );
        CREATE UNIQUE INDEX uq_permission_request_pending
          ON iam.permission_requests (user_id, permission_key, connection_id) WHERE status = 'pending';
        CREATE INDEX ix_permission_requests_status ON iam.permission_requests (status, requested_at DESC);

        ALTER TABLE iam.permission_requests ENABLE ROW LEVEL SECURITY;
        CREATE POLICY tenant_isolation ON iam.permission_requests
          USING (tenant_id = util.current_tenant()) WITH CHECK (tenant_id = util.current_tenant());
        """
    )
    role = _app_role()
    run(
        f"""
        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN
            GRANT SELECT, INSERT, UPDATE ON iam.permission_requests TO {role};
          END IF;
        END $$;
        """
    )


def downgrade() -> None:
    run(
        """
        DROP TABLE IF EXISTS iam.permission_requests;
        ALTER TABLE iam.permission_grants DROP COLUMN IF EXISTS last_used_at;
        """
    )
