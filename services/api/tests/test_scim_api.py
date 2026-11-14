"""SCIM 2.0 provisioning and provisioning status (US-118, US-119; FEAT-004; API-003).

Acceptance: TEST-005 (a deprovisioned user loses access at once; what they shared stays readable).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx
import psycopg
import pytest
from api_testkit import ISSUER, bootstrap, needs_db
from pmo_testkit import World, h, setup_world
from test_authoring_api import built_report

pytestmark = [needs_db, pytest.mark.integration]

USER = "urn:ietf:params:scim:schemas:core:2.0:User"
PATCH = "urn:ietf:params:scim:api:messages:2.0:PatchOp"


def s(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/scim+json"}


def user_body(ext: str, user_name: str, given: str = "Alex", family: str = "Morgan", active: bool = True,
              ) -> dict[str, Any]:
    return {"schemas": [USER], "externalId": ext, "userName": user_name, "active": active,
            "name": {"givenName": given, "familyName": family},
            "emails": [{"value": user_name, "type": "work", "primary": True}]}


def patch(*ops: dict[str, Any]) -> dict[str, Any]:
    return {"schemas": [PATCH], "Operations": list(ops)}


async def scim_token(api: httpx.AsyncClient, admin: str) -> str:
    resp = await api.post("/api/v1/admin/provisioning/token", headers=h(admin))
    assert resp.status_code == 201, resp.text
    return str(resp.json()["token"])


@pytest.fixture
async def setup(api: httpx.AsyncClient, owner_conn: psycopg.Connection,
                make_token: Callable[..., str]) -> dict[str, Any]:
    bootstrap(owner_conn, "acme", ISSUER, {"TELA-Admins": "admin", "TELA-Creators": "creator",
                                           "TELA-Viewers": "viewer"})
    admin = make_token("admin-1", groups=["TELA-Admins"], name="Ada Admin")
    assert (await api.get("/api/v1/me", headers=h(admin))).status_code == 200
    return {"admin": admin, "scim": await scim_token(api, admin)}


# --------------------------------------------------------------------------- protocol


async def test_token_required_and_discovery(api: httpx.AsyncClient, setup: dict[str, Any]) -> None:
    none = await api.get("/scim/v2/Users")
    assert none.status_code == 401 and none.headers["content-type"].startswith("application/scim+json")
    assert none.json()["schemas"] == ["urn:ietf:params:scim:api:messages:2.0:Error"]
    assert (await api.get("/scim/v2/Users", headers=s("tela_scim_wrong"))).status_code == 401
    # The Admin API rejects the SCIM token and the SCIM API rejects user tokens.
    assert (await api.get("/api/v1/me", headers=s(setup["scim"]))).status_code == 401
    assert (await api.get("/scim/v2/Users", headers=h(setup["admin"]))).status_code == 401

    spc = (await api.get("/scim/v2/ServiceProviderConfig", headers=s(setup["scim"]))).json()
    assert spc["patch"]["supported"] is True and spc["bulk"]["supported"] is False
    types = (await api.get("/scim/v2/ResourceTypes", headers=s(setup["scim"]))).json()
    assert {t["id"] for t in types["Resources"]} == {"User", "Group"}
    schemas = (await api.get("/scim/v2/Schemas", headers=s(setup["scim"]))).json()
    assert {x["name"] for x in schemas["Resources"]} == {"User", "Group"}


async def test_user_lifecycle_is_idempotent_on_external_id(
    api: httpx.AsyncClient, owner_conn: psycopg.Connection, setup: dict[str, Any]
) -> None:
    tok = setup["scim"]
    created = await api.post("/scim/v2/Users", headers=s(tok), json=user_body("oid-alex", "alex@example.com"))
    assert created.status_code == 201, created.text
    alex = created.json()
    assert alex["externalId"] == "oid-alex" and alex["active"] is True and alex["displayName"] == "Alex Morgan"
    assert created.headers["location"].endswith(f"/scim/v2/Users/{alex['id']}")

    # A retried create (A1) is the same person, never a second one.
    again = await api.post("/scim/v2/Users", headers=s(tok), json=user_body("oid-alex", "alex@example.com"))
    assert again.status_code == 201 and again.json()["id"] == alex["id"]
    assert owner_conn.execute("SELECT count(*) FROM iam.users WHERE scim_external_id = 'oid-alex'").fetchone() == (1,)

    # Uniqueness: another externalId with the same userName.
    clash = await api.post("/scim/v2/Users", headers=s(tok), json=user_body("oid-other", "ALEX@example.com"))
    assert clash.status_code == 409 and clash.json()["scimType"] == "uniqueness"

    # Filters and pagination.
    found = (await api.get('/scim/v2/Users?filter=userName eq "alex@example.com"', headers=s(tok))).json()
    assert found["totalResults"] == 1 and found["Resources"][0]["id"] == alex["id"]
    by_ext = (await api.get('/scim/v2/Users?filter=externalId eq "oid-alex"', headers=s(tok))).json()
    assert by_ext["totalResults"] == 1
    bad = await api.get('/scim/v2/Users?filter=emails co "x"', headers=s(tok))
    assert bad.status_code == 400 and bad.json()["scimType"] == "invalidFilter"
    page = (await api.get("/scim/v2/Users?startIndex=2&count=1", headers=s(tok))).json()
    assert page["totalResults"] == 2 and page["itemsPerPage"] == 1 and page["startIndex"] == 2

    # PATCH as Entra sends it: "False" as a string, paths for names.
    renamed = await api.patch(f"/scim/v2/Users/{alex['id']}", headers=s(tok), json=patch(
        {"op": "Replace", "path": "name.familyName", "value": "Moran"},
        {"op": "Replace", "path": 'emails[type eq "work"].value', "value": "alex.moran@example.com"},
        {"op": "Add", "path": "urn:ietf:params:scim:schemas:extension:enterprise:2.0:User:department",
         "value": "PMO"}))
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["name"]["familyName"] == "Moran"
    assert renamed.json()["emails"][0]["value"] == "alex.moran@example.com"

    suspended = await api.patch(f"/scim/v2/Users/{alex['id']}", headers=s(tok), json=patch(
        {"op": "Replace", "path": "active", "value": "False"}))
    assert suspended.json()["active"] is False
    row = owner_conn.execute("SELECT status, sessions_revoked_at IS NOT NULL FROM iam.users WHERE id = %s",
                             (alex["id"],)).fetchone()
    assert row == ("suspended", True)
    # No Keycloak admin client in tests: the sign-out task is skipped, Tela refuses the tokens itself.
    assert owner_conn.execute("SELECT status FROM ops.provisioning_tasks").fetchall() == [("skipped",)]

    back = await api.put(f"/scim/v2/Users/{alex['id']}", headers=s(tok),
                         json=user_body("oid-alex", "alex.moran@example.com", family="Moran"))
    assert back.json()["active"] is True

    assert (await api.delete(f"/scim/v2/Users/{alex['id']}", headers=s(tok))).status_code == 204
    assert (await api.get(f"/scim/v2/Users/{alex['id']}", headers=s(tok))).status_code == 404
    kept = owner_conn.execute("SELECT status, deprovisioned_at IS NOT NULL FROM iam.users WHERE id = %s",
                              (alex["id"],)).fetchone()
    assert kept == ("deprovisioned", True)  # records kept (S03 §7)
    actions = {r[0] for r in owner_conn.execute("SELECT action FROM ops.audit_events WHERE action LIKE 'scim.%'")}
    assert {"scim.user.created", "scim.user.updated", "scim.user.suspended", "scim.user.reactivated",
            "scim.user.deprovisioned"} <= actions
    logged = owner_conn.execute("SELECT count(*) FILTER (WHERE outcome = 'error') FROM ops.provisioning_log"
                                ).fetchone()
    assert logged is not None and logged[0] >= 2  # the 409 and the invalid filter


async def test_groups_drive_roles_and_suspension_ends_sessions(
    api: httpx.AsyncClient, owner_conn: psycopg.Connection, make_token: Callable[..., str], setup: dict[str, Any]
) -> None:
    tok = setup["scim"]
    alex = (await api.post("/scim/v2/Users", headers=s(tok), json=user_body("oid-alex", "alex@example.com"))).json()

    # The group already exists (registered by an Admin, mapped to Creator): SCIM adopts it, no duplicate.
    group = await api.post("/scim/v2/Groups", headers=s(tok), json={
        "schemas": ["urn:ietf:params:scim:schemas:core:2.0:Group"], "displayName": "TELA Creators",
        "externalId": "TELA-Creators", "members": [{"value": alex["id"]}]})
    assert group.status_code == 201, group.text
    gid = group.json()["id"]
    assert [m["value"] for m in group.json()["members"]] == [alex["id"]]
    assert owner_conn.execute("SELECT count(*) FROM iam.idp_groups WHERE external_group_id = 'TELA-Creators'"
                              ).fetchone() == (1,)

    # First sign-in links by the Entra object id; the token carries no groups, SCIM membership gives the role.
    token = make_token("kc-alex", groups=[], name="Alex Morgan", extra={"oid": "oid-alex", "tid": "t1"})
    me = await api.get("/api/v1/me", headers=h(token))
    assert me.status_code == 200, me.text
    assert me.json()["level"] == "creator" and me.json()["user_id"] == alex["id"]

    # HR moves Alex to Viewers (worked example): the next request has the lower role.
    viewers = await api.post("/scim/v2/Groups", headers=s(tok), json={"displayName": "TELA Viewers",
                                                                    "externalId": "TELA-Viewers"})
    vid = viewers.json()["id"]
    await api.patch(f"/scim/v2/Groups/{vid}", headers=s(tok), json=patch(
        {"op": "Add", "path": "members", "value": [{"value": alex["id"]}]}))
    removed = await api.patch(f"/scim/v2/Groups/{gid}", headers=s(tok), json=patch(
        {"op": "Remove", "path": f'members[value eq "{alex["id"]}"]'}))
    assert removed.status_code == 204
    assert (await api.get("/api/v1/me", headers=h(token))).json()["level"] == "viewer"
    cache = owner_conn.execute("SELECT effective_roles_cache FROM iam.users WHERE id = %s", (alex["id"],)).fetchone()
    assert cache == (["viewer"],)
    lean = (await api.get(f"/scim/v2/Groups/{vid}?excludedAttributes=members", headers=s(tok))).json()
    assert "members" not in lean
    by_name = (await api.get('/scim/v2/Groups?filter=displayName eq "tela viewers"', headers=s(tok))).json()
    assert by_name["totalResults"] == 1

    # Suspension: the token in hand stops working at once; reactivation needs a new sign-in.
    await api.patch(f"/scim/v2/Users/{alex['id']}", headers=s(tok), json=patch(
        {"op": "Replace", "value": {"active": False}}))
    assert (await api.get("/api/v1/me", headers=h(token))).status_code == 403
    await api.patch(f"/scim/v2/Users/{alex['id']}", headers=s(tok), json=patch(
        {"op": "Replace", "path": "active", "value": True}))
    old = await api.get("/api/v1/me", headers=h(token))
    assert old.status_code == 401 and old.json()["error"]["details"]["reason"] == "session_revoked"

    # Deleting a group removes memberships but keeps its role mapping for the Admin to see.
    assert (await api.delete(f"/scim/v2/Groups/{vid}", headers=s(tok))).status_code == 204
    assert (await api.get(f"/scim/v2/Groups/{vid}", headers=s(tok))).status_code == 404
    assert owner_conn.execute("SELECT count(*) FROM iam.group_role_mappings m JOIN iam.idp_groups g "
                              "ON g.id = m.group_id WHERE g.external_group_id = 'TELA-Viewers'").fetchone() == (1,)
    # ...but the directory no longer has it, so it is not offered for a new mapping (the kept one still lists).
    groups = (await api.get("/api/v1/admin/groups", headers=h(setup["admin"]))).json()["items"]
    assert "TELA-Viewers" not in {g["external_group_id"] for g in groups}
    assert "TELA-Creators" in {g["external_group_id"] for g in groups}
    mappings = (await api.get("/api/v1/admin/group-mappings", headers=h(setup["admin"]))).json()["mappings"]
    assert any(m["idp_group_id"] == "TELA-Viewers" for m in mappings)


async def test_rate_limit_and_token_rotation(api: httpx.AsyncClient, setup: dict[str, Any]) -> None:
    api.app.state.scim_limiter.per_minute = 3  # type: ignore[attr-defined]
    codes = [(await api.get("/scim/v2/Users", headers=s(setup["scim"]))).status_code for _ in range(4)]
    assert codes == [200, 200, 200, 429]
    api.app.state.scim_limiter.per_minute = 600  # type: ignore[attr-defined]
    api.app.state.scim_limiter._local.clear()  # type: ignore[attr-defined]

    new = await scim_token(api, setup["admin"])
    assert (await api.get("/scim/v2/Users", headers=s(setup["scim"]))).status_code == 401
    assert (await api.get("/scim/v2/Users", headers=s(new))).status_code == 200
    assert (await api.delete("/api/v1/admin/provisioning/token", headers=h(setup["admin"]))).status_code == 204
    assert (await api.get("/scim/v2/Users", headers=s(new))).status_code == 401


# --------------------------------------------------------------------------- status and reconcile (US-119)


class FailingSessions:
    configured = True

    def __init__(self) -> None:
        self.calls = 0
        self.fail = True

    async def logout(self, issuer: str, subject: str) -> None:
        self.calls += 1
        if self.fail:
            raise RuntimeError("keycloak unavailable")


async def test_reconcile_retries_fixes_drift_and_alerts(
    api: httpx.AsyncClient, owner_conn: psycopg.Connection, make_token: Callable[..., str], setup: dict[str, Any]
) -> None:
    admin, tok = setup["admin"], setup["scim"]
    sessions = FailingSessions()
    api.app.state.idp_sessions = sessions  # type: ignore[attr-defined]
    alex = (await api.post("/scim/v2/Users", headers=s(tok), json=user_body("oid-alex", "alex@example.com"))).json()
    await api.post("/scim/v2/Groups", headers=s(tok), json={"displayName": "Creators", "externalId": "TELA-Creators",
                                                          "members": [{"value": alex["id"]}]})
    await api.get("/api/v1/me", headers=h(make_token("kc-alex", extra={"oid": "oid-alex"})))
    await api.patch(f"/scim/v2/Users/{alex['id']}", headers=s(tok), json=patch(
        {"op": "replace", "path": "active", "value": False}))
    task = owner_conn.execute("SELECT status, attempts, last_error FROM ops.provisioning_tasks").fetchone()
    assert task is not None and task[0] == "pending" and task[1] == 1 and "keycloak unavailable" in task[2]

    # Drift Tela can fix: a deprovisioned person still in a group.
    owner_conn.execute("UPDATE iam.users SET status = 'deprovisioned' WHERE id = %s", (alex["id"],))
    owner_conn.execute("UPDATE ops.provisioning_tasks SET next_attempt_at = now()")
    sessions.fail = False
    run = await api.post("/api/v1/admin/provisioning/reconcile", headers=h(admin))
    assert run.status_code == 200, run.text
    summary = run.json()["summary"]
    assert run.json()["outcome"] == "drift" and run.json()["trigger"] == "manual"
    assert summary["fixed"]["memberships_removed"] == 1 and summary["tasks"]["done"] == 1
    assert summary["unlinked_users"]["count"] == 1  # Ada signed in but is not provisioned from Entra
    assert summary["alerted"] == 1
    note = owner_conn.execute("SELECT title, link FROM ops.notifications WHERE kind = 'scim.drift'").fetchone()
    assert note == ("Provisioning needs attention", "/admin/provisioning")

    # Same findings next hour: no second alert.
    again = (await api.post("/api/v1/admin/provisioning/reconcile", headers=h(admin))).json()
    assert again["summary"]["alerted"] == 0 and again["summary"]["fixed"]["memberships_removed"] == 0

    status = (await api.get("/api/v1/admin/provisioning", headers=h(admin))).json()
    assert status["enabled"] is True and status["token"]["prefix"].startswith("tela_scim_")
    assert status["counts"]["users"]["deprovisioned"] == 1 and status["counts"]["requests_24h"] == 3
    assert status["last_run"]["id"] == again["id"] and len(status["runs"]) == 2
    assert status["recent"][0]["resource"] in ("User", "Group")
    assert "token" not in status["token"]


async def test_failed_sign_out_gives_up_and_alerts(
    api: httpx.AsyncClient, owner_conn: psycopg.Connection, make_token: Callable[..., str], setup: dict[str, Any]
) -> None:
    sessions = FailingSessions()
    api.app.state.idp_sessions = sessions  # type: ignore[attr-defined]
    alex = (await api.post("/scim/v2/Users", headers=s(setup["scim"]),
                           json=user_body("oid-alex", "alex@example.com"))).json()
    await api.get("/api/v1/me", headers=h(make_token("kc-alex", extra={"oid": "oid-alex"})))
    await api.delete(f"/scim/v2/Users/{alex['id']}", headers=s(setup["scim"]))
    for _ in range(4):
        owner_conn.execute("UPDATE ops.provisioning_tasks SET next_attempt_at = now() WHERE status = 'pending'")
        await api.post("/api/v1/admin/provisioning/reconcile", headers=h(setup["admin"]))
    assert owner_conn.execute("SELECT status, attempts FROM ops.provisioning_tasks").fetchone() == ("failed", 5)
    titles = [r[0] for r in owner_conn.execute("SELECT body FROM ops.notifications WHERE kind = 'scim.drift'")]
    assert any("sign-out(s) failed" in t for t in titles)


async def test_internal_reconcile_needs_the_worker(api: httpx.AsyncClient, setup: dict[str, Any]) -> None:
    assert (await api.post("/api/v1/internal/scim/reconcile", headers=h(setup["admin"]))).status_code == 401


# --------------------------------------------------------------------------- TEST-005 and E-17


@pytest.fixture
async def world(stack: httpx.AsyncClient, owner_conn: psycopg.Connection, make_token: Callable[..., str],
                tpg_source: dict[str, Any]) -> World:
    return await setup_world(stack, owner_conn, make_token, tpg_source)


async def test_deprovisioned_user_loses_access_content_stays(
    stack: httpx.AsyncClient, world: World, owner_conn: psycopg.Connection, make_token: Callable[..., str]
) -> None:
    admin, nomsa = world.tokens["admin"], world.tokens["nomsa"]
    # The Creator signs in with an Entra object id, then Entra provisioning is turned on and matches them.
    creator = make_token("creator-1", groups=["TELA-Creators"], name="Cleo Creator", extra={"oid": "oid-cleo"})
    await stack.get("/api/v1/me", headers=h(nomsa))
    rid = await built_report(stack, creator)
    people = {u["name"]: u["id"] for u in
              (await stack.get("/api/v1/directory/users", headers=h(creator))).json()["items"]}
    shared = await stack.post("/api/v1/shares", headers=h(creator), json={
        "object": {"kind": "report", "id": rid}, "with_user_ids": [people["Nomsa Osei"]]})
    assert shared.status_code == 201, shared.text
    tok = await scim_token(stack, admin)
    cleo = await stack.post("/scim/v2/Users", headers=s(tok), json=user_body("oid-cleo", "cleo@example.com",
                                                                             "Cleo", "Creator"))
    assert cleo.status_code == 201
    cleo_id = cleo.json()["id"]
    assert owner_conn.execute("SELECT count(*) FROM iam.users WHERE display_name LIKE 'Cleo%%'").fetchone() == (1,)

    assert (await stack.delete(f"/scim/v2/Users/{cleo_id}", headers=s(tok))).status_code == 204
    denied = await stack.get("/api/v1/reports", headers=h(creator))
    assert denied.status_code in (401, 403)
    fresh = make_token("creator-1", groups=["TELA-Creators"], name="Cleo Creator", extra={"oid": "oid-cleo"})
    assert (await stack.get("/api/v1/me", headers=h(fresh))).status_code == 403  # a new sign-in is refused too
    assert (await stack.get(f"/api/v1/reports/{rid}", headers=h(nomsa))).status_code == 200  # still shared

    # E-17: the Admin sees the former owner and hands their content to someone else.
    status = (await stack.get("/api/v1/admin/provisioning", headers=h(admin))).json()
    owner = next(o for o in status["deprovisioned_owners"] if o["user"]["id"] == cleo_id)
    assert owner["owned"]["reports"] >= 1
    new_owner = (await stack.get("/api/v1/me", headers=h(admin))).json()["user_id"]
    moved = await stack.post(f"/api/v1/admin/users/{cleo_id}/reassign-content", headers=h(admin),
                             json={"to_user_id": new_owner})
    assert moved.status_code == 200, moved.text
    assert moved.json()["moved"]["reports"] >= 1 and moved.json()["skipped"]["reports"] == 0
    assert owner_conn.execute("SELECT owner_id::text FROM content.reports WHERE id = %s", (rid,)).fetchone() == \
        (new_owner,)
    active = await stack.post(f"/api/v1/admin/users/{new_owner}/reassign-content", headers=h(admin),
                              json={"to_user_id": cleo_id})
    assert active.status_code == 409
