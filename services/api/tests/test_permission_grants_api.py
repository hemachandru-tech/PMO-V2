"""Per-user permission grants (ENH-008, US-161; FEAT-060).

Grant, expiry (checked at use, reminder and audit by the sweep), removal, wrong tenant, a connection the person
cannot see, SCIM removal and Effective Access.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import psycopg
import pytest
from api_testkit import ISSUER, OTHER_ISSUER, add_connection, bootstrap, needs_db
from pmo_testkit import h

from tela_api.core.db import fetch_all, tenant_tx
from tela_api.identity import grants
from tela_api.identity.principal import Grant, Principal
from tela_common.ids import new_id

pytestmark = [needs_db, pytest.mark.integration]

URL = "/api/v1/admin/permission-grants"
GROUPS = {"TELA-Admins": "admin", "TELA-Viewers": "viewer"}


def _later(days: float) -> str:
    return (datetime.now(UTC) + timedelta(days=days)).isoformat()


def _policy(owner: psycopg.Connection, tenant: str, connection: str, role: str) -> None:
    owner.execute(
        "INSERT INTO iam.scope_policies (id, tenant_id, role_id, connection_id, entity_key, all_rows, description) "
        "SELECT %s, %s, id, %s, 'project', true, 'all projects' FROM iam.roles WHERE tenant_id IS NULL AND key = %s",
        (new_id(), tenant, connection, role))


async def _me(api: httpx.AsyncClient, token: str) -> dict[str, Any]:
    resp = await api.get("/api/v1/me", headers=h(token))
    assert resp.status_code == 200, resp.text
    return dict(resp.json())


@pytest.fixture
async def world(api: httpx.AsyncClient, owner_conn: psycopg.Connection,
                make_token: Callable[..., str]) -> dict[str, Any]:
    tenant = bootstrap(owner_conn, "acme", ISSUER, GROUPS)
    seen = add_connection(owner_conn, tenant, "TPG Postgres")
    hidden = add_connection(owner_conn, tenant, "Finance")
    _policy(owner_conn, tenant, seen, "viewer")
    admin = make_token("admin-1", groups=["TELA-Admins"], name="Ada Admin")
    viewer = make_token("viewer-1", groups=["TELA-Viewers"], name="Vic Viewer",
                        extra={"oid": "oid-vic", "tid": "t1"})
    me_admin, me_viewer = await _me(api, admin), await _me(api, viewer)
    return {"tenant": tenant, "seen": seen, "hidden": hidden, "admin": admin, "viewer": viewer,
            "admin_id": me_admin["user_id"], "viewer_id": me_viewer["user_id"]}


async def _grant(api: httpx.AsyncClient, w: dict[str, Any], **kw: Any) -> httpx.Response:
    body = {"user_id": w["viewer_id"], "connection_id": w["seen"], "reason": "Portfolio review",
            "expires_at": _later(90), **kw}
    return await api.post(URL, headers=h(w["admin"]), json=body)


def _actions(owner: psycopg.Connection) -> list[str]:
    return [r[0] for r in owner.execute(
        "SELECT action FROM ops.audit_events WHERE action LIKE 'permission.%' ORDER BY occurred_at, action")]


# --------------------------------------------------------------------------- unit: checked at use


def test_principal_can_checks_connection_and_expiry() -> None:
    now = datetime.now(UTC)
    p = Principal(user_id="u", tenant_id="t", display_name="Vic", email=None, level="viewer", roles=("viewer",),
                  role_ids=(), audience_roles=(), permissions=frozenset({"ask.use"}), groups=(),
                  grants=(Grant("ask.freehand", "c1", now + timedelta(days=1)),
                          Grant("ask.freehand", "c2", now - timedelta(seconds=1))))
    assert p.can("ask.freehand", "c1")
    assert not p.can("ask.freehand", "c2")  # ended: no job needed
    assert not p.can("ask.freehand", "c3")
    assert not p.has("ask.freehand")  # never a role permission
    assert p.can("ask.use", "anything")  # role permissions hold on every connection
    assert p.granted_connections("ask.freehand") == ["c1"]


def test_freehand_is_in_no_system_role(owner_conn: psycopg.Connection) -> None:
    assert owner_conn.execute(
        "SELECT count(*) FROM iam.role_permissions WHERE permission_key = 'ask.freehand'").fetchone() == (0,)


# --------------------------------------------------------------------------- API


async def test_grant_shows_on_me_and_effective_access(api: httpx.AsyncClient, owner_conn: psycopg.Connection,
                                                      world: dict[str, Any]) -> None:
    w = world
    assert (await _me(api, w["viewer"]))["grants"] == []
    resp = await _grant(api, w)
    assert resp.status_code == 201, resp.text
    g = resp.json()
    assert g["status"] == "active" and g["permission"] == "ask.freehand"
    assert g["user"]["display_name"] == "Vic Viewer" and g["connection"]["name"] == "TPG Postgres"
    assert g["granted_by"]["display_name"] == "Ada Admin" and g["reason"] == "Portfolio review"

    me = await _me(api, w["viewer"])
    assert [(x["permission"], x["connection_id"]) for x in me["grants"]] == [("ask.freehand", w["seen"])]
    assert "ask.freehand" not in me["permissions"]  # per connection, never in the role list

    listed = (await api.get(URL, headers=h(w["admin"]), params={"user_id": w["viewer_id"]})).json()["items"]
    assert [x["id"] for x in listed] == [g["id"]]

    preview = await api.post("/api/v1/admin/scope-policies/preview", headers=h(w["admin"]),
                             params={"connection_id": w["seen"], "user_id": w["viewer_id"]})
    assert preview.status_code == 200, preview.text
    shown = preview.json()["grants"]
    assert [(x["permission"], x["granted_by"]["display_name"], x["reason"]) for x in shown] == [
        ("ask.freehand", "Ada Admin", "Portfolio review")]
    role_view = await api.post("/api/v1/admin/scope-policies/preview", headers=h(w["admin"]),
                               params={"connection_id": w["seen"], "role": "viewer"})
    assert role_view.json()["grants"] == []

    again = await _grant(api, w)
    assert again.status_code == 409 and "already has this" in again.json()["error"]["message"]
    assert _actions(owner_conn) == ["permission.granted"]


async def test_admin_also_needs_a_grant(api: httpx.AsyncClient, world: dict[str, Any]) -> None:
    w = world
    assert (await _me(api, w["admin"]))["grants"] == []
    resp = await _grant(api, w, user_id=w["admin_id"], connection_id=w["hidden"])
    assert resp.status_code == 201, resp.text  # an Admin sees every connection
    assert [x["connection_id"] for x in (await _me(api, w["admin"]))["grants"]] == [w["hidden"]]


async def test_refusals(api: httpx.AsyncClient, world: dict[str, Any]) -> None:
    w = world
    hidden = await _grant(api, w, connection_id=w["hidden"])
    assert hidden.status_code == 400
    err = hidden.json()["error"]
    assert err["details"]["reason"] == "connection_not_visible"
    assert "Vic Viewer can't see Finance" in err["message"]

    assert (await _grant(api, w, permission="audit.read")).status_code == 400  # not grantable
    assert (await _grant(api, w, expires_at=_later(-1))).status_code == 400
    assert (await _grant(api, w, expires_at=_later(400))).status_code == 400  # at most a year
    assert (await _grant(api, w, reason="   ")).status_code == 400
    assert (await _grant(api, w, user_id=new_id())).status_code == 404
    assert (await _grant(api, w, user_id="nope")).status_code == 404
    assert (await _grant(api, w, connection_id=new_id())).status_code == 404

    # Only mappings.manage may give, list or remove.
    assert (await api.get(URL, headers=h(w["viewer"]))).status_code == 403
    assert (await api.post(URL, headers=h(w["viewer"]), json={
        "user_id": w["viewer_id"], "connection_id": w["seen"], "reason": "me", "expires_at": _later(9)})
    ).status_code == 403


async def test_wrong_tenant(api: httpx.AsyncClient, owner_conn: psycopg.Connection,
                            world: dict[str, Any]) -> None:
    w = world
    other = bootstrap(owner_conn, "globex", OTHER_ISSUER, GROUPS)
    other_conn = add_connection(owner_conn, other, "Globex DB")
    _policy(owner_conn, other, other_conn, "viewer")
    stranger = new_id()
    owner_conn.execute("INSERT INTO iam.users (id, tenant_id, display_name, status) "
                       "VALUES (%s, %s, 'Gus Globex', 'active')", (stranger, other))
    # Row-level security: Acme's Admin can't reach Globex's people or connections.
    assert (await _grant(api, w, user_id=stranger)).status_code == 404
    assert (await _grant(api, w, connection_id=other_conn)).status_code == 404
    g = (await _grant(api, w)).json()
    # A Globex grant row is invisible to Acme: not listed, not removable.
    foreign = new_id()
    owner_conn.execute(
        "INSERT INTO iam.permission_grants (id, tenant_id, user_id, permission_key, connection_id, reason, expires_at) "
        "VALUES (%s, %s, %s, 'ask.freehand', %s, 'theirs', now() + interval '9 days')",
        (foreign, other, stranger, other_conn))
    listed = (await api.get(URL, headers=h(w["admin"]), params={"include_ended": True})).json()["items"]
    assert [x["id"] for x in listed] == [g["id"]]
    assert (await api.delete(f"{URL}/{foreign}", headers=h(w["admin"]))).status_code == 404


async def test_remove_takes_effect_at_once(api: httpx.AsyncClient, owner_conn: psycopg.Connection,
                                           world: dict[str, Any]) -> None:
    w = world
    g = (await _grant(api, w)).json()
    assert (await api.delete(f"{URL}/{g['id']}", headers=h(w["admin"]))).status_code == 204
    assert (await _me(api, w["viewer"]))["grants"] == []
    assert (await api.delete(f"{URL}/{g['id']}", headers=h(w["admin"]))).status_code == 404
    assert (await api.get(URL, headers=h(w["admin"]))).json()["items"] == []
    ended = (await api.get(URL, headers=h(w["admin"]), params={"include_ended": True})).json()["items"]
    assert [(x["status"], x["removed_reason"], x["removed_by"]["display_name"]) for x in ended] == [
        ("removed", "admin", "Ada Admin")]
    assert _actions(owner_conn) == ["permission.granted", "permission.removed"]
    # Removed, so it can be given again.
    assert (await _grant(api, w)).status_code == 201


async def test_expiry_reminder_and_audit(api: httpx.AsyncClient, owner_conn: psycopg.Connection,
                                         world: dict[str, Any]) -> None:
    w = world
    g = (await _grant(api, w, expires_at=_later(5))).json()

    async with tenant_tx(w["tenant"]) as conn:
        assert await grants.sweep_tenant(conn, w["tenant"]) == {"reminded": 1, "expired": 0}
    async with tenant_tx(w["tenant"]) as conn:
        assert await grants.sweep_tenant(conn, w["tenant"]) == {"reminded": 0, "expired": 0}  # once only
    notes = owner_conn.execute("SELECT user_id::text, kind, title FROM ops.notifications ORDER BY title").fetchall()
    assert {(n[0], n[1]) for n in notes} == {(w["viewer_id"], "permission.expiring"),
                                             (w["admin_id"], "permission.expiring")}
    assert any("Your access to ask free-hand questions on TPG Postgres ends on" in n[2] for n in notes)

    # The end date passes: access stops at once, with no job; the sweep records it once.
    owner_conn.execute("UPDATE iam.permission_grants SET granted_at = now() - interval '2 days', "
                       "expires_at = now() - interval '1 minute' WHERE id = %s", (g["id"],))
    assert (await _me(api, w["viewer"]))["grants"] == []
    listed = (await api.get(URL, headers=h(w["admin"]), params={"include_ended": True})).json()["items"]
    assert [x["status"] for x in listed] == ["expired"]
    async with tenant_tx(None) as conn:
        tenants = [str(r["tenant_id"]) for r in await fetch_all(conn, "SELECT tenant_id FROM "
                                                                      "iam.f_permission_grant_tenants()")]
    assert tenants == [w["tenant"]]
    async with tenant_tx(w["tenant"]) as conn:
        assert await grants.sweep_tenant(conn, w["tenant"]) == {"reminded": 0, "expired": 1}
    async with tenant_tx(w["tenant"]) as conn:
        assert await grants.sweep_tenant(conn, w["tenant"]) == {"reminded": 0, "expired": 0}
    assert _actions(owner_conn) == ["permission.granted", "permission.expired"]

    # An ended grant does not block a new one: it is closed as expired.
    assert (await _grant(api, w)).status_code == 201
    reasons = owner_conn.execute("SELECT removed_reason FROM iam.permission_grants ORDER BY granted_at").fetchall()
    assert reasons == [("expired",), (None,)]


async def test_sweep_needs_the_worker(api: httpx.AsyncClient) -> None:
    assert (await api.post("/api/v1/internal/permission-grants/sweep")).status_code == 401


async def test_scim_removal_ends_grants(api: httpx.AsyncClient, owner_conn: psycopg.Connection,
                                        world: dict[str, Any]) -> None:
    w = world
    g = (await _grant(api, w)).json()
    token = (await api.post("/api/v1/admin/provisioning/token", headers=h(w["admin"]))).json()["token"]
    owner_conn.execute("UPDATE iam.users SET scim_external_id = 'oid-vic' WHERE id = %s", (w["viewer_id"],))
    gone = await api.delete(f"/scim/v2/Users/{w['viewer_id']}", headers={"Authorization": f"Bearer {token}"})
    assert gone.status_code == 204, gone.text
    row = owner_conn.execute("SELECT removed_reason, removed_at IS NOT NULL FROM iam.permission_grants "
                             "WHERE id = %s", (g["id"],)).fetchone()
    assert row == ("scim", True)
    assert _actions(owner_conn) == ["permission.granted", "permission.removed"]
    detail = owner_conn.execute("SELECT details FROM ops.audit_events WHERE action = 'scim.user.deprovisioned'"
                                ).fetchone()
    assert detail is not None and detail[0]["grants_removed"] == 1


async def test_reconcile_removes_grants_of_deprovisioned_people(api: httpx.AsyncClient,
                                                                 owner_conn: psycopg.Connection,
                                                                 world: dict[str, Any]) -> None:
    w = world
    g = (await _grant(api, w)).json()
    # Deprovisioned outside the SCIM call (directory drift): the reconcile finds and removes the grant.
    owner_conn.execute("UPDATE iam.users SET status = 'deprovisioned', deprovisioned_at = now() WHERE id = %s",
                       (w["viewer_id"],))
    run = await api.post("/api/v1/admin/provisioning/reconcile", headers=h(w["admin"]))
    assert run.status_code in (200, 201, 202), run.text
    row = owner_conn.execute("SELECT removed_reason FROM iam.permission_grants WHERE id = %s", (g["id"],)).fetchone()
    assert row == ("scim",)
