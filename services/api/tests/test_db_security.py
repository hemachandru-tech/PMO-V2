"""Database-level guarantees: tenant RLS (TEST-091), audit immutability and hash chain."""

from __future__ import annotations

import psycopg
import pytest
from api_testkit import APP_URL, ISSUER, OTHER_ISSUER, add_connection, bootstrap, needs_db, reset_data

pytestmark = [needs_db, pytest.mark.integration]


def _app() -> psycopg.Connection:
    assert APP_URL
    return psycopg.connect(APP_URL)


def test_app_role_cannot_bypass_rls(owner_conn: psycopg.Connection) -> None:
    row = owner_conn.execute(
        "SELECT rolbypassrls, rolsuper FROM pg_roles WHERE rolname = current_user"
    ).fetchone()
    with _app() as conn:
        app_row = conn.execute("SELECT rolbypassrls, rolsuper FROM pg_roles WHERE rolname = current_user").fetchone()
        owns = conn.execute(
            "SELECT count(*) FROM pg_tables WHERE schemaname IN ('iam','conn','ops') AND tableowner = current_user"
        ).fetchone()
    assert row is not None
    assert app_row == (False, False)
    assert owns == (0,)


def test_tenant_isolation(owner_conn: psycopg.Connection) -> None:
    reset_data(owner_conn)
    a = bootstrap(owner_conn, "acme", ISSUER, {"acme-admins": "admin"})
    b = bootstrap(owner_conn, "globex", OTHER_ISSUER, {"globex-admins": "admin"})
    cb = add_connection(owner_conn, b, "Globex DB")

    with _app() as conn:
        # No tenant context: nothing is visible.
        assert conn.execute("SELECT count(*) FROM iam.idp_groups").fetchone() == (0,)
        assert conn.execute("SELECT count(*) FROM iam.tenants").fetchone() == (0,)
        conn.rollback()

        with conn.transaction():
            conn.execute("SELECT set_config('app.tenant_id', %s, true)", (a,))
            groups = conn.execute("SELECT external_group_id FROM iam.idp_groups").fetchall()
            assert groups == [("acme-admins",)]
            # A crafted id from tenant B returns nothing.
            assert conn.execute("SELECT count(*) FROM conn.connections WHERE id = %s", (cb,)).fetchone() == (0,)
            # Global system roles are visible, tenant B rows are not.
            assert conn.execute("SELECT count(*) FROM iam.roles WHERE tenant_id IS NULL").fetchone() == (6,)

        # Writing a row for another tenant is rejected by WITH CHECK.
        with pytest.raises(psycopg.errors.InsufficientPrivilege), conn.transaction():
            conn.execute("SELECT set_config('app.tenant_id', %s, true)", (a,))
            conn.execute(
                "INSERT INTO conn.connections (id, tenant_id, name, engine) "
                "VALUES (gen_random_uuid(), %s, 'sneaky', 'postgres')",
                (b,),
            )

        # The app role cannot modify global seed roles.
        with conn.transaction():
            conn.execute("SELECT set_config('app.tenant_id', %s, true)", (a,))
            res = conn.execute("UPDATE iam.roles SET name = 'x' WHERE key = 'admin'")
            assert res.rowcount == 0


def test_secrets_never_in_descriptor(owner_conn: psycopg.Connection) -> None:
    reset_data(owner_conn)
    a = bootstrap(owner_conn, "acme", ISSUER, {})
    with pytest.raises(psycopg.errors.CheckViolation):
        owner_conn.execute(
            "INSERT INTO conn.connections (id, tenant_id, name, engine, descriptor) "
            "VALUES (gen_random_uuid(), %s, 'x', 'postgres', '{\"password\": \"p\"}')",
            (a,),
        )


def test_audit_is_append_only_and_hash_chained(owner_conn: psycopg.Connection) -> None:
    reset_data(owner_conn)
    a = bootstrap(owner_conn, "acme", ISSUER, {})
    with _app() as conn:
        with conn.transaction():
            conn.execute("SELECT set_config('app.tenant_id', %s, true)", (a,))
            for action in ("auth.login", "access.mapping.changed", "auth.logout"):
                conn.execute(
                    "INSERT INTO ops.audit_events (tenant_id, action, object_type, outcome, correlation_id) "
                    "VALUES (%s, %s, 'user', 'success', 'test')",
                    (a, action),
                )
            rows = conn.execute("SELECT prev_hash, row_hash FROM ops.audit_events ORDER BY id").fetchall()
        assert rows[0][0] is None
        assert rows[1][0] == rows[0][1] and rows[2][0] == rows[1][1]
        assert len({r[1] for r in rows}) == 3

        with pytest.raises(psycopg.errors.InsufficientPrivilege), conn.transaction():
            conn.execute("SELECT set_config('app.tenant_id', %s, true)", (a,))
            conn.execute("UPDATE ops.audit_events SET outcome = 'deny'")
        with pytest.raises(psycopg.errors.InsufficientPrivilege), conn.transaction():
            conn.execute("SELECT set_config('app.tenant_id', %s, true)", (a,))
            conn.execute("DELETE FROM ops.audit_events")

    # Even the owner cannot rewrite history (trigger).
    with pytest.raises(psycopg.errors.RestrictViolation):
        owner_conn.execute("UPDATE ops.audit_events SET outcome = 'deny'")
