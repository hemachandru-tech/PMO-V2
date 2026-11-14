"""Publish requests (EPIC-UX US-142; FEAT-039; DEC-05).

A Creator cannot publish to roles (Admin only, DEC-05); they ask for it. ``content.publish_requests`` keeps each
request so Admins see a queue (Govern → Publish requests) instead of only a notification:

* one ``open`` request per dashboard — asking again replaces the open one (it becomes ``withdrawn``);
* publishing the dashboard closes it as ``published`` and links the version it produced;
* an Admin can decline it with a note (``declined``); the requester is notified either way.

Revision ID: 0012
Revises: 0011
Create Date: 2026-10-21
"""

from alembic import context

from migrations.helpers import run

revision = "0012"
down_revision = "0011"
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
        CREATE TABLE content.publish_requests (
          id               uuid PRIMARY KEY,
          tenant_id        uuid NOT NULL REFERENCES iam.tenants(id) ON DELETE RESTRICT,
          dashboard_id     uuid NOT NULL REFERENCES content.dashboards(id) ON DELETE CASCADE,
          requested_by     uuid NOT NULL REFERENCES iam.users(id),
          target_role_ids  uuid[] NOT NULL DEFAULT '{}',
          note             text CHECK (note IS NULL OR char_length(note) <= 500),
          status           text NOT NULL DEFAULT 'open'
                             CHECK (status IN ('open', 'published', 'declined', 'withdrawn')),
          decided_by       uuid REFERENCES iam.users(id),
          decided_at       timestamptz,
          decision_note    text CHECK (decision_note IS NULL OR char_length(decision_note) <= 500),
          version_id       uuid REFERENCES content.dashboard_versions(id) ON DELETE SET NULL,
          created_at       timestamptz NOT NULL DEFAULT now(),
          CONSTRAINT ck_decline_note CHECK (status <> 'declined' OR coalesce(char_length(decision_note), 0) > 0)
        );
        CREATE UNIQUE INDEX uq_publish_request_open ON content.publish_requests (dashboard_id) WHERE status = 'open';
        CREATE INDEX ix_publish_requests_status ON content.publish_requests (tenant_id, status, created_at DESC);
        COMMENT ON TABLE content.publish_requests IS 'Creators asking an Admin to publish a dashboard to roles';
        ALTER TABLE content.publish_requests ENABLE ROW LEVEL SECURITY;
        CREATE POLICY tenant_isolation ON content.publish_requests
          USING (tenant_id = util.current_tenant()) WITH CHECK (tenant_id = util.current_tenant());
        """
    )
    role = _app_role()
    run(
        f"""
        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN
            GRANT SELECT, INSERT, UPDATE ON content.publish_requests TO {role};
          END IF;
        END $$;
        """
    )


def downgrade() -> None:
    run("DROP TABLE IF EXISTS content.publish_requests")
