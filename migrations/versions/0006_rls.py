"""Row-level security and application-role grants (US-006, NFR-023).

Every tenant-scoped table gets a ``tenant_isolation`` policy keyed on the
transaction-local ``app.tenant_id`` setting. Global catalogue rows
(``tenant_id IS NULL``, e.g. system roles) are readable but not writable by the
application role. The application role (``tela_app`` unless overridden with
the ``tela.app_role`` alembic -x argument) must not own the tables and must
not have BYPASSRLS; it is created by the deployment, not by this migration.

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-21
"""

from alembic import context

from migrations.helpers import run

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

# (schema.table, has nullable tenant_id for global rows)
TENANT_TABLES: list[tuple[str, bool]] = [
    ("iam.users", False),
    ("iam.external_identities", False),
    ("iam.idp_groups", False),
    ("iam.idp_group_members", False),
    ("iam.roles", True),
    ("iam.group_role_mappings", False),
    ("iam.scope_policies", False),
    ("iam.field_policies", False),
    ("conn.connections", False),
    ("conn.connection_objects", False),
    ("conn.file_sources", False),
    ("ops.audit_events", False),
    ("ops.llm_invocations", False),
    ("ops.query_executions", False),
    ("ops.result_cache_index", False),
    ("ops.export_jobs", False),
    ("ops.outbox_events", False),
    ("ops.notifications", False),
]

APPEND_ONLY = {"ops.audit_events"}


def _app_role() -> str:
    role = context.get_x_argument(as_dictionary=True).get("tela.app_role", "tela_app")
    if not role.replace("_", "").isalnum():
        raise ValueError("invalid app role name")
    return role


def upgrade() -> None:
    for table, has_global in TENANT_TABLES:
        run(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        if has_global:
            run(
                f"CREATE POLICY tenant_read ON {table} FOR SELECT "
                "USING (tenant_id IS NULL OR tenant_id = util.current_tenant())"
            )
            run(
                f"CREATE POLICY tenant_write ON {table} FOR ALL "
                "USING (tenant_id = util.current_tenant()) WITH CHECK (tenant_id = util.current_tenant())"
            )
        else:
            run(
                f"CREATE POLICY tenant_isolation ON {table} "
                "USING (tenant_id = util.current_tenant()) WITH CHECK (tenant_id = util.current_tenant())"
            )

    # iam.tenants: a session sees only its own tenant row.
    run("ALTER TABLE iam.tenants ENABLE ROW LEVEL SECURITY")
    run("CREATE POLICY tenant_self ON iam.tenants FOR SELECT USING (id = util.current_tenant())")

    # role_permissions has no tenant_id; it follows the visibility of its role.
    run("ALTER TABLE iam.role_permissions ENABLE ROW LEVEL SECURITY")
    run(
        "CREATE POLICY via_role ON iam.role_permissions FOR SELECT USING ("
        "EXISTS (SELECT 1 FROM iam.roles r WHERE r.id = role_id))"
    )
    run(
        "CREATE POLICY via_role_write ON iam.role_permissions FOR ALL USING ("
        "EXISTS (SELECT 1 FROM iam.roles r WHERE r.id = role_id AND r.tenant_id = util.current_tenant())) "
        "WITH CHECK (EXISTS (SELECT 1 FROM iam.roles r WHERE r.id = role_id AND r.tenant_id = util.current_tenant()))"
    )

    role = _app_role()
    run(
        f"""
        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN
            GRANT USAGE ON SCHEMA util, iam, conn, ops TO {role};
            GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA util, iam, ops TO {role};
            GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA iam, conn, ops TO {role};
            REVOKE UPDATE, DELETE ON ops.audit_events FROM {role};
            REVOKE INSERT, UPDATE, DELETE ON iam.tenants FROM {role};
            GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA iam, conn, ops TO {role};
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
            REVOKE ALL ON ALL TABLES IN SCHEMA iam, conn, ops FROM {role};
            REVOKE ALL ON ALL SEQUENCES IN SCHEMA iam, conn, ops FROM {role};
            REVOKE ALL ON ALL FUNCTIONS IN SCHEMA util, iam, ops FROM {role};
            REVOKE USAGE ON SCHEMA util, iam, conn, ops FROM {role};
          END IF;
        END $$;
        """
    )
    run("DROP POLICY IF EXISTS via_role_write ON iam.role_permissions")
    run("DROP POLICY IF EXISTS via_role ON iam.role_permissions")
    run("ALTER TABLE iam.role_permissions DISABLE ROW LEVEL SECURITY")
    run("DROP POLICY IF EXISTS tenant_self ON iam.tenants")
    run("ALTER TABLE iam.tenants DISABLE ROW LEVEL SECURITY")
    for table, has_global in TENANT_TABLES:
        if has_global:
            run(f"DROP POLICY IF EXISTS tenant_write ON {table}")
            run(f"DROP POLICY IF EXISTS tenant_read ON {table}")
        else:
            run(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
        run(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
