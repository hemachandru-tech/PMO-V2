"""Dashboard groups shown in everyone's sidebar (ENH-009, US-169; FEAT-061).

* ``content.dashboard_groups`` — one set of named groups per tenant (W1), ordered by ``position``. Soft-deleted with
  ``deleted_at`` so the name can be reused at once (the unique name index ignores deleted groups).
* ``content.dashboard_group_items`` — which dashboards a group holds, in order. A dashboard may sit in several groups
  (W4). Membership never widens access: readers see only the dashboards they can already open (W3).
* New permission ``dashboards.group`` for the Admin and Creator system roles (W2).

Revision ID: 0029
Revises: 0028
Create Date: 2026-10-28
"""

from alembic import context

from migrations.helpers import run

revision = "0029"
down_revision = "0028"
branch_labels = None
depends_on = None

ADMIN_ROLE = "00000000-0000-7000-8000-00000000a001"
CREATOR_ROLE = "00000000-0000-7000-8000-00000000a002"


def _app_role() -> str:
    role = context.get_x_argument(as_dictionary=True).get("tela.app_role", "tela_app")
    if not role.replace("_", "").isalnum():
        raise ValueError("invalid app role name")
    return role


def upgrade() -> None:
    run(
        """
        CREATE TABLE content.dashboard_groups (
          id          uuid PRIMARY KEY,
          tenant_id   uuid NOT NULL REFERENCES iam.tenants(id) ON DELETE RESTRICT,
          name        text NOT NULL CHECK (char_length(btrim(name)) BETWEEN 1 AND 60),
          icon        text CHECK (icon IS NULL OR icon ~ '^[a-z][a-z0-9-]{0,39}$'),
          position    integer NOT NULL DEFAULT 0,
          created_by  uuid NOT NULL REFERENCES iam.users(id),
          created_at  timestamptz NOT NULL DEFAULT now(),
          updated_at  timestamptz NOT NULL DEFAULT now(),
          deleted_at  timestamptz
        );
        CREATE UNIQUE INDEX uq_dashboard_group_name ON content.dashboard_groups (tenant_id, lower(btrim(name)))
          WHERE deleted_at IS NULL;
        CREATE TRIGGER trg_dashboard_groups_updated_at BEFORE UPDATE ON content.dashboard_groups
          FOR EACH ROW EXECUTE FUNCTION util.set_updated_at();

        CREATE TABLE content.dashboard_group_items (
          tenant_id     uuid NOT NULL REFERENCES iam.tenants(id) ON DELETE RESTRICT,
          group_id      uuid NOT NULL REFERENCES content.dashboard_groups(id) ON DELETE CASCADE,
          dashboard_id  uuid NOT NULL REFERENCES content.dashboards(id) ON DELETE CASCADE,
          position      integer NOT NULL,
          added_by      uuid NOT NULL REFERENCES iam.users(id),
          added_at      timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (group_id, dashboard_id)
        );
        CREATE INDEX ix_dashboard_group_items_dashboard ON content.dashboard_group_items (dashboard_id);

        ALTER TABLE content.dashboard_groups ENABLE ROW LEVEL SECURITY;
        CREATE POLICY tenant_isolation ON content.dashboard_groups
          USING (tenant_id = util.current_tenant()) WITH CHECK (tenant_id = util.current_tenant());
        ALTER TABLE content.dashboard_group_items ENABLE ROW LEVEL SECURITY;
        CREATE POLICY tenant_isolation ON content.dashboard_group_items
          USING (tenant_id = util.current_tenant()) WITH CHECK (tenant_id = util.current_tenant());
        """
    )
    for role_id in (ADMIN_ROLE, CREATOR_ROLE):
        run(
            f"INSERT INTO iam.role_permissions (role_id, permission_key) VALUES ('{role_id}', 'dashboards.group') "
            "ON CONFLICT DO NOTHING"
        )
    role = _app_role()
    run(
        f"""
        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN
            GRANT SELECT, INSERT, UPDATE ON content.dashboard_groups TO {role};
            GRANT SELECT, INSERT, UPDATE, DELETE ON content.dashboard_group_items TO {role};
          END IF;
        END $$;
        """
    )


def downgrade() -> None:
    run("DELETE FROM iam.role_permissions WHERE permission_key = 'dashboards.group'")
    run(
        """
        DROP TABLE IF EXISTS content.dashboard_group_items;
        DROP TABLE IF EXISTS content.dashboard_groups;
        """
    )
