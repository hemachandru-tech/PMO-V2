"""Identity and access API: TEST-001 (sign-in), TEST-002 (mapping applies and revokes), US-013 to US-015."""

from __future__ import annotations

from collections.abc import Callable

import httpx
import psycopg
import pytest
from api_testkit import ISSUER, add_connection, bootstrap, needs_db

pytestmark = [needs_db, pytest.mark.integration]


def _h(token: str, **extra: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", **extra}


# --------------------------------------------------------------------------- sign-in (TEST-001)


async def test_no_token_is_401(api: httpx.AsyncClient) -> None:
    resp = await api.get("/api/v1/me")
    assert resp.status_code == 401
    body = resp.json()["error"]
    assert body["code"] == "token_invalid" and body["request_id"]
    assert resp.headers["www-authenticate"] == "Bearer"


async def test_mapped_user_signs_in_and_sees_profile(
    api: httpx.AsyncClient, owner_conn: psycopg.Connection, make_token: Callable[..., str]
) -> None:
    bootstrap(owner_conn, "acme", ISSUER, {"TELA-Creators": "creator", "TELA-CIO": "cio"})
    token = make_token("u-nomsa", groups=["TELA-Creators", "TELA-CIO"], name="Nomsa Osei",
                       email="nomsa@example.com", extra={"tid": "entra-tid", "oid": "entra-oid-1"})
    resp = await api.get("/api/v1/me", headers=_h(token))
    assert resp.status_code == 200, resp.text
    me = resp.json()
    assert me["display_name"] == "Nomsa Osei"
    assert me["level"] == "creator" and me["roles"] == ["creator"]
    assert me["audience_roles"] == ["cio"]
    assert "widgets.create" in me["permissions"] and "mappings.manage" not in me["permissions"]

    # One user, linked by issuer + subject and Entra object id; login audited once.
    row = owner_conn.execute(
        "SELECT count(*), max(e.directory_object_id) FROM iam.users u JOIN iam.external_identities e "
        "ON e.user_id = u.id"
    ).fetchone()
    assert row == (1, "entra-oid-1")
    await api.get("/api/v1/me", headers=_h(token))
    logins = owner_conn.execute(
        "SELECT count(*) FROM ops.audit_events WHERE action = 'auth.login' AND outcome = 'success'"
    ).fetchone()
    assert logins == (1,)


async def test_same_email_different_subject_is_a_different_user(
    api: httpx.AsyncClient, owner_conn: psycopg.Connection, make_token: Callable[..., str]
) -> None:
    bootstrap(owner_conn, "acme", ISSUER, {"TELA-Viewers": "viewer"})
    a = make_token("sub-a", groups=["TELA-Viewers"], email="same@example.com")
    assert (await api.get("/api/v1/me", headers=_h(a))).status_code == 200
    b = make_token("sub-b", groups=["TELA-Viewers"], email="other@example.com")
    assert (await api.get("/api/v1/me", headers=_h(b))).status_code == 200
    assert owner_conn.execute("SELECT count(*) FROM iam.users").fetchone() == (2,)


async def test_unmapped_user_gets_no_access_and_is_audited(
    api: httpx.AsyncClient, owner_conn: psycopg.Connection, make_token: Callable[..., str]
) -> None:
    bootstrap(owner_conn, "acme", ISSUER, {"TELA-Admins": "admin"})
    resp = await api.get("/api/v1/me", headers=_h(make_token("stranger", groups=["Some-Other-Group"])))
    assert resp.status_code == 403
    err = resp.json()["error"]
    assert err["code"] == "no_access"
    assert err["message"] == "You don't have access to Tela yet. Ask your PMO admin."
    row = owner_conn.execute(
        "SELECT outcome, details->>'reason' FROM ops.audit_events WHERE action = 'auth.login'"
    ).fetchone()
    assert row == ("deny", "no_mapped_group")


async def test_unknown_tenant_issuer_gets_no_access(
    api: httpx.AsyncClient, make_token: Callable[..., str]
) -> None:
    resp = await api.get("/api/v1/me", headers=_h(make_token("x", groups=["TELA-Admins"])))
    assert resp.status_code == 403 and resp.json()["error"]["code"] == "no_access"


@pytest.mark.parametrize(
    "kwargs,reason",
    [
        ({"aud": "account"}, "audience"),
        ({"iss": "http://evil.test/realms/acme"}, "issuer"),
        ({"exp_in": -120}, "expired"),
        ({"typ": "ID"}, "not_access_token"),
        ({"kid": "unknown-kid"}, "unknown_kid"),
    ],
)
async def test_invalid_tokens_are_rejected(
    api: httpx.AsyncClient, owner_conn: psycopg.Connection, make_token: Callable[..., str],
    kwargs: dict, reason: str,
) -> None:
    bootstrap(owner_conn, "acme", ISSUER, {"TELA-Admins": "admin"})
    resp = await api.get("/api/v1/me", headers=_h(make_token("u", groups=["TELA-Admins"], **kwargs)))
    assert resp.status_code == 401
    assert resp.json()["error"]["details"]["reason"] == reason


async def test_tampered_token_is_rejected(
    api: httpx.AsyncClient, owner_conn: psycopg.Connection, make_token: Callable[..., str]
) -> None:
    bootstrap(owner_conn, "acme", ISSUER, {"TELA-Admins": "admin"})
    header, _payload, sig = make_token("u", groups=["TELA-Viewers"]).split(".")
    forged = make_token("u", groups=["TELA-Admins"]).split(".")[1]
    resp = await api.get("/api/v1/me", headers=_h(f"{header}.{forged}.{sig}"))
    assert resp.status_code == 401


async def test_suspended_user_is_refused(
    api: httpx.AsyncClient, owner_conn: psycopg.Connection, make_token: Callable[..., str]
) -> None:
    bootstrap(owner_conn, "acme", ISSUER, {"TELA-Viewers": "viewer"})
    token = make_token("u-gone", groups=["TELA-Viewers"])
    assert (await api.get("/api/v1/me", headers=_h(token))).status_code == 200
    owner_conn.execute("UPDATE iam.users SET status = 'suspended'")
    resp = await api.get("/api/v1/me", headers=_h(token))
    assert resp.status_code == 403 and resp.json()["error"]["details"]["reason"] == "user_inactive"


async def test_logout_is_audited(
    api: httpx.AsyncClient, owner_conn: psycopg.Connection, make_token: Callable[..., str]
) -> None:
    bootstrap(owner_conn, "acme", ISSUER, {"TELA-Viewers": "viewer"})
    resp = await api.post("/api/v1/auth/logout", headers=_h(make_token("u", groups=["TELA-Viewers"])))
    assert resp.status_code == 204
    assert owner_conn.execute("SELECT count(*) FROM ops.audit_events WHERE action='auth.logout'").fetchone() == (1,)


# --------------------------------------------------------------------------- permissions


async def test_viewer_cannot_use_admin_endpoints(
    api: httpx.AsyncClient, owner_conn: psycopg.Connection, make_token: Callable[..., str]
) -> None:
    bootstrap(owner_conn, "acme", ISSUER, {"TELA-Viewers": "viewer"})
    token = make_token("v", groups=["TELA-Viewers"])
    for method, path in [("GET", "/api/v1/admin/group-mappings"), ("GET", "/api/v1/admin/scope-policies"),
                         ("GET", "/api/v1/admin/roles"), ("PUT", "/api/v1/admin/group-mappings")]:
        resp = await api.request(method, path, headers=_h(token), json={"mappings": []})
        assert resp.status_code == 403, (method, path)
        assert resp.json()["error"]["code"] == "forbidden"


# --------------------------------------------------------------------------- group mappings (TEST-002)


async def test_mapping_lifecycle_grants_and_revokes_access(
    api: httpx.AsyncClient, owner_conn: psycopg.Connection, make_token: Callable[..., str]
) -> None:
    bootstrap(owner_conn, "acme", ISSUER, {"TELA-Admins": "admin"})
    admin = make_token("admin-1", groups=["TELA-Admins"], name="Admin One")
    member = make_token("member-1", groups=["TELA-KSA"])

    assert (await api.get("/api/v1/me", headers=_h(member))).status_code == 403  # not mapped yet

    reg = await api.post("/api/v1/admin/groups", headers=_h(admin),
                         json={"external_group_id": "TELA-KSA", "display_name": "KSA delivery"})
    assert reg.status_code == 201

    current = await api.get("/api/v1/admin/group-mappings", headers=_h(admin))
    etag = current.json()["etag"]
    assert current.headers["etag"] == etag

    new = [*current.json()["mappings"], {"idp_group_id": "TELA-KSA", "role": "viewer"}]
    payload = {"mappings": [{"idp_group_id": m["idp_group_id"], "role": m["role"]} for m in new]}

    assert (await api.put("/api/v1/admin/group-mappings", headers=_h(admin), json=payload)).status_code == 412
    stale = await api.put("/api/v1/admin/group-mappings", headers=_h(admin, **{"If-Match": 'W/"stale"'}),
                          json=payload)
    assert stale.status_code == 412

    ok = await api.put("/api/v1/admin/group-mappings", headers=_h(admin, **{"If-Match": etag}), json=payload)
    assert ok.status_code == 200, ok.text
    me = await api.get("/api/v1/me", headers=_h(member))
    assert me.status_code == 200 and me.json()["level"] == "viewer"

    # Removing the mapping revokes access on the next request.
    payload2 = {"mappings": [{"idp_group_id": "TELA-Admins", "role": "admin"}]}
    ok2 = await api.put("/api/v1/admin/group-mappings", headers=_h(admin, **{"If-Match": ok.json()["etag"]}),
                        json=payload2)
    assert ok2.status_code == 200
    assert (await api.get("/api/v1/me", headers=_h(member))).status_code == 403

    actions = [r[0] for r in owner_conn.execute(
        "SELECT action FROM ops.audit_events WHERE action LIKE 'access.%' ORDER BY id").fetchall()]
    assert actions.count("access.mapping.changed") == 2
    assert owner_conn.execute(
        "SELECT count(*) FROM ops.outbox_events WHERE topic = 'access.mappings_changed'").fetchone() == (2,)


async def test_unknown_group_and_role_rejected(
    api: httpx.AsyncClient, owner_conn: psycopg.Connection, make_token: Callable[..., str]
) -> None:
    bootstrap(owner_conn, "acme", ISSUER, {"TELA-Admins": "admin"})
    admin = make_token("a", groups=["TELA-Admins"])
    etag = (await api.get("/api/v1/admin/group-mappings", headers=_h(admin))).json()["etag"]
    resp = await api.put("/api/v1/admin/group-mappings", headers=_h(admin, **{"If-Match": etag}),
                         json={"mappings": [{"idp_group_id": "nope", "role": "viewer"}]})
    assert resp.status_code == 400 and resp.json()["error"]["code"] == "unknown_group"
    resp = await api.put("/api/v1/admin/group-mappings", headers=_h(admin, **{"If-Match": etag}),
                         json={"mappings": [{"idp_group_id": "TELA-Admins", "role": "superuser"}]})
    assert resp.status_code == 400


async def test_admin_mapping_needs_second_admin_approval(
    api: httpx.AsyncClient, owner_conn: psycopg.Connection, make_token: Callable[..., str]
) -> None:
    bootstrap(owner_conn, "acme", ISSUER, {"TELA-Admins": "admin"})
    admin1 = make_token("admin-1", groups=["TELA-Admins"])
    admin2 = make_token("admin-2", groups=["TELA-Admins"])
    newbie = make_token("newbie", groups=["TELA-Ops"])
    await api.post("/api/v1/admin/groups", headers=_h(admin1),
                   json={"external_group_id": "TELA-Ops", "display_name": "Ops admins"})
    etag = (await api.get("/api/v1/admin/group-mappings", headers=_h(admin1))).json()["etag"]
    resp = await api.put(
        "/api/v1/admin/group-mappings", headers=_h(admin1, **{"If-Match": etag}),
        json={"mappings": [{"idp_group_id": "TELA-Admins", "role": "admin"},
                           {"idp_group_id": "TELA-Ops", "role": "admin"}]},
    )
    assert resp.status_code == 200
    pending = next(m for m in resp.json()["mappings"] if m["idp_group_id"] == "TELA-Ops")
    assert pending["status"] == "pending_approval"
    assert (await api.get("/api/v1/me", headers=_h(newbie))).status_code == 403  # not effective yet

    self_approve = await api.post(f"/api/v1/admin/group-mappings/{pending['id']}/approve", headers=_h(admin1))
    assert self_approve.status_code == 403 and self_approve.json()["error"]["details"]["reason"] == "self_approval"

    approved = await api.post(f"/api/v1/admin/group-mappings/{pending['id']}/approve", headers=_h(admin2))
    assert approved.status_code == 200
    assert (await api.get("/api/v1/me", headers=_h(newbie))).json()["level"] == "admin"
    sev = owner_conn.execute(
        "SELECT severity FROM ops.audit_events WHERE action = 'access.mapping.approved'").fetchone()
    assert sev == ("high",)


# --------------------------------------------------------------------------- scope and field policies


async def test_scope_policy_crud_preview_and_me_summary(
    api: httpx.AsyncClient, owner_conn: psycopg.Connection, make_token: Callable[..., str]
) -> None:
    tenant = bootstrap(owner_conn, "acme", ISSUER, {"TELA-Admins": "admin", "TELA-CIO": "cio",
                                                    "TELA-Viewers": "viewer"})
    cid = add_connection(owner_conn, tenant)
    owner_conn.execute(
        "UPDATE iam.group_role_mappings m SET scope_attributes = '{\"cio\": [\"Nomsa Osei\"]}' "
        "FROM iam.idp_groups g WHERE g.id = m.group_id AND g.external_group_id = 'TELA-CIO'"
    )
    admin = make_token("admin-1", groups=["TELA-Admins"])
    cio = make_token("cio-1", groups=["TELA-Viewers", "TELA-CIO"], name="Nomsa Osei")
    cio_me = await api.get("/api/v1/me", headers=_h(cio))
    assert cio_me.status_code == 200 and cio_me.json()["scope_summary"] == []

    bad = await api.post("/api/v1/admin/scope-policies", headers=_h(admin), json={
        "connection_id": cid, "entity": "projects", "role": "cio", "description": "no conditions"})
    assert bad.status_code == 400 and bad.json()["error"]["code"] == "validation_error"

    created = await api.post("/api/v1/admin/scope-policies", headers=_h(admin), json={
        "connection_id": cid, "entity": "projects", "role": "cio", "description": "CIO: own projects",
        "predicates": [{"field": "cio", "op": "=", "attr": "cio"}]})
    assert created.status_code == 201, created.text
    policy_id = created.json()["id"]

    me = (await api.get("/api/v1/me", headers=_h(cio))).json()
    assert me["scope_summary"] == [{"connection_id": cid, "connection_name": "TPG Postgres",
                                    "text": "projects: CIO: own projects"}]

    user_id = me["user_id"]
    preview = await api.post(f"/api/v1/admin/scope-policies/preview?connection_id={cid}&user_id={user_id}",
                             headers=_h(admin))
    assert preview.status_code == 200, preview.text
    p = preview.json()
    assert p["level"] == "viewer" and p["audience_roles"] == ["cio"]
    assert p["scope"]["entities"]["projects"]["alternatives"] == [[{"field": "cio", "op": "=",
                                                                    "values": ["Nomsa Osei"]}]]
    assert p["rows_in_scope"] is None

    patched = await api.patch(f"/api/v1/admin/scope-policies/{policy_id}", headers=_h(admin),
                              json={"description": "CIO portfolio"})
    assert patched.status_code == 200 and patched.json()["description"] == "CIO portfolio"

    listing = await api.get(f"/api/v1/admin/scope-policies?connection_id={cid}", headers=_h(admin))
    assert [x["id"] for x in listing.json()["items"]] == [policy_id]

    assert (await api.delete(f"/api/v1/admin/scope-policies/{policy_id}", headers=_h(admin))).status_code == 204
    assert (await api.delete(f"/api/v1/admin/scope-policies/{policy_id}", headers=_h(admin))).status_code == 404


async def test_bulk_scope_policies_for_many_entities_and_roles(
    api: httpx.AsyncClient, owner_conn: psycopg.Connection, make_token: Callable[..., str]
) -> None:
    """US-172: one condition for every entity and role chosen, all or nothing; identical pairs are skipped."""
    tenant = bootstrap(owner_conn, "acme", ISSUER, {"TELA-Admins": "admin", "TELA-CIO": "cio",
                                                    "TELA-Viewers": "viewer"})
    cid = add_connection(owner_conn, tenant)
    admin = make_token("admin-1", groups=["TELA-Admins"])
    url = "/api/v1/admin/scope-policies/bulk"
    region = [{"field": "region", "op": "in", "values": ["KSA"]}]

    created = await api.post(url, headers=_h(admin), json={
        "connection_id": cid, "entities": ["projects", "risks", "projects"], "roles": ["cio", "viewer"],
        "predicates": region, "description": "Region: KSA"})
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["skipped"] == []
    assert sorted((p["entity"], p["role"]) for p in body["created"]) == [
        ("projects", "cio"), ("projects", "viewer"), ("risks", "cio"), ("risks", "viewer")]
    assert all(p["predicates"] == region and p["description"] == "Region: KSA" for p in body["created"])

    # Again with one more entity: the four identical pairs are skipped, only the new ones are added.
    projects_cio = next(p["id"] for p in body["created"] if p["entity"] == "projects" and p["role"] == "cio")
    await api.patch(f"/api/v1/admin/scope-policies/{projects_cio}", headers=_h(admin), json={"is_active": False})
    again = (await api.post(url, headers=_h(admin), json={
        "connection_id": cid, "entities": ["projects", "risks", "tasks"], "roles": ["cio", "viewer"],
        "predicates": region, "description": "Region: KSA"})).json()
    assert sorted((p["entity"], p["role"]) for p in again["created"]) == [("tasks", "cio"), ("tasks", "viewer")]
    assert {(s["entity"], s["role"], s["is_active"]) for s in again["skipped"]} == {
        ("projects", "cio", False), ("projects", "viewer", True), ("risks", "cio", True), ("risks", "viewer", True)}

    # A different condition on the same pair is a new policy (union), as with the single form.
    all_rows = (await api.post(url, headers=_h(admin), json={
        "connection_id": cid, "entities": ["projects"], "roles": ["cio"], "all_rows": True,
        "description": "All projects"})).json()
    assert [(p["entity"], p["all_rows"]) for p in all_rows["created"]] == [("projects", True)]
    listing = (await api.get(f"/api/v1/admin/scope-policies?connection_id={cid}", headers=_h(admin))).json()["items"]
    assert len(listing) == 7

    # All or nothing: an unknown role, a bad entity name or a missing condition writes nothing.
    for bad in ({"roles": ["cio", "nobody"]}, {"entities": ["projects", "bad name"]}, {"predicates": []},
                {"all_rows": True}, {"entities": []}, {"roles": []}):
        resp = await api.post(url, headers=_h(admin), json={
            "connection_id": cid, "entities": ["issues"], "roles": ["cio"], "predicates": region,
            "description": "x", **bad})
        assert resp.status_code == 400 and resp.json()["error"]["code"] == "validation_error", (bad, resp.text)
    assert len((await api.get(f"/api/v1/admin/scope-policies?connection_id={cid}",
                              headers=_h(admin))).json()["items"]) == 7

    viewer = make_token("viewer-1", groups=["TELA-Viewers"])
    denied = await api.post(url, headers=_h(viewer), json={
        "connection_id": cid, "entities": ["projects"], "roles": ["viewer"], "all_rows": True, "description": "x"})
    assert denied.status_code == 403

    audit_rows = owner_conn.execute(
        "SELECT details->>'batch' FROM ops.audit_events WHERE action = 'access.scope_policy.created'").fetchall()
    assert len(audit_rows) == 7 and {r[0] for r in audit_rows} == {"4", "6", "1"}


async def test_preview_reports_missing_attribute(
    api: httpx.AsyncClient, owner_conn: psycopg.Connection, make_token: Callable[..., str]
) -> None:
    tenant = bootstrap(owner_conn, "acme", ISSUER, {"TELA-Admins": "admin", "TELA-CIO": "cio",
                                                    "TELA-Viewers": "viewer"})
    cid = add_connection(owner_conn, tenant)
    admin = make_token("admin-1", groups=["TELA-Admins"])
    cio = make_token("cio-1", groups=["TELA-Viewers", "TELA-CIO"])
    await api.post("/api/v1/admin/scope-policies", headers=_h(admin), json={
        "connection_id": cid, "entity": "projects", "role": "cio", "description": "CIO",
        "predicates": [{"field": "cio", "op": "=", "attr": "cio"}]})
    me = (await api.get("/api/v1/me", headers=_h(cio))).json()
    assert "could not be determined" in me["scope_summary"][0]["text"]
    preview = (await api.post(
        f"/api/v1/admin/scope-policies/preview?connection_id={cid}&user_id={me['user_id']}",
        headers=_h(admin))).json()
    assert preview["scope"]["error"]["code"] == "scope_unresolved"
    assert preview["scope"]["error"]["details"]["missing_attribute"] == "cio"


async def test_effective_access_for_a_person_or_a_role(
    api: httpx.AsyncClient, owner_conn: psycopg.Connection, make_token: Callable[..., str]
) -> None:
    """US-145: the Effective access panel resolves a person (with groups and permissions) or a role on its own."""
    tenant = bootstrap(owner_conn, "acme", ISSUER, {"TELA-Admins": "admin", "TELA-CIO": "cio",
                                                    "TELA-Viewers": "viewer"})
    cid = add_connection(owner_conn, tenant)
    owner_conn.execute(
        "UPDATE iam.group_role_mappings m SET scope_attributes = '{\"cio\": [\"Nomsa Osei\"]}' "
        "FROM iam.idp_groups g WHERE g.id = m.group_id AND g.external_group_id = 'TELA-CIO'"
    )
    admin = make_token("admin-1", groups=["TELA-Admins"])
    cio = make_token("cio-1", groups=["TELA-Viewers", "TELA-CIO"], name="Nomsa Osei")
    for body in ({"entity": "projects", "role": "cio", "description": "Own CIO",
                  "predicates": [{"field": "cio", "op": "=", "attr": "cio"}]},
                 {"entity": "projects", "role": "viewer", "description": "KSA",
                  "predicates": [{"field": "region", "op": "in", "values": ["KSA"]}]}):
        created = await api.post("/api/v1/admin/scope-policies", headers=_h(admin), json={"connection_id": cid, **body})
        assert created.status_code == 201, created.text
    saved = await api.put("/api/v1/admin/field-policies", headers=_h(admin), json={
        "connection_id": cid, "rules": [{"field_ref": "projects.budget", "hidden_for": ["viewer"], "effect": "hide"}]})
    assert saved.status_code == 200, saved.text
    me = (await api.get("/api/v1/me", headers=_h(cio))).json()

    person = (await api.post(f"/api/v1/admin/scope-policies/preview?connection_id={cid}&user_id={me['user_id']}",
                             headers=_h(admin))).json()
    assert person["subject"] == {"kind": "person", "id": me["user_id"], "display_name": "Nomsa Osei"}
    assert person["groups"] == ["TELA-CIO", "TELA-Viewers"]
    assert "ask.use" in person["permissions"] and "connections.manage" not in person["permissions"]
    assert person["level"] == "viewer" and person["audience_roles"] == ["cio"]
    assert len(person["scope"]["entities"]["projects"]["alternatives"]) == 2  # union of both roles' rows

    role = (await api.post(f"/api/v1/admin/scope-policies/preview?connection_id={cid}&role=viewer",
                           headers=_h(admin))).json()
    assert role["subject"]["kind"] == "role" and role["subject"]["key"] == "viewer" and role["groups"] == []
    assert role["level"] == "viewer"
    assert role["scope"]["entities"]["projects"]["alternatives"] == [[{"field": "region", "op": "in",
                                                                      "values": ["KSA"]}]]
    assert role["scope"]["hidden_fields"] == ["projects.budget"]
    # A role on its own has no personal attributes: a policy that reads one says so.
    cio_role = (await api.post(f"/api/v1/admin/scope-policies/preview?connection_id={cid}&role=cio",
                               headers=_h(admin))).json()
    assert cio_role["scope"]["error"]["details"]["missing_attribute"] == "cio"

    neither = await api.post(f"/api/v1/admin/scope-policies/preview?connection_id={cid}", headers=_h(admin))
    assert neither.status_code == 400
    unknown = await api.post(f"/api/v1/admin/scope-policies/preview?connection_id={cid}&role=nope", headers=_h(admin))
    assert unknown.status_code == 404
    audited = owner_conn.execute("SELECT object_type FROM ops.audit_events WHERE action = 'access.scope.previewed' "
                                 "ORDER BY id").fetchall()
    assert audited == [("user",), ("role",), ("role",)]


async def test_field_policies_replace_per_connection(
    api: httpx.AsyncClient, owner_conn: psycopg.Connection, make_token: Callable[..., str]
) -> None:
    tenant = bootstrap(owner_conn, "acme", ISSUER, {"TELA-Admins": "admin"})
    cid = add_connection(owner_conn, tenant)
    admin = make_token("admin-1", groups=["TELA-Admins"])
    body = {"connection_id": cid, "rules": [
        {"field_ref": "field:projects.approved_budget_value_usd", "hidden_for": ["cio"]},
        {"field_ref": "projects.cost_total_usd", "hidden_for": ["cio", "delivery_manager"]},
    ]}
    resp = await api.put("/api/v1/admin/field-policies", headers=_h(admin), json=body)
    assert resp.status_code == 200, resp.text
    rules = {r["field_ref"]: r["hidden_for"] for r in resp.json()["rules"]}
    assert rules == {"field:projects.approved_budget_value_usd": ["cio"],
                     "field:projects.cost_total_usd": ["cio", "delivery_manager"]}
    resp = await api.put("/api/v1/admin/field-policies", headers=_h(admin),
                         json={"connection_id": cid, "rules": []})
    assert resp.json()["rules"] == []
    bad = await api.put("/api/v1/admin/field-policies", headers=_h(admin),
                        json={"connection_id": cid, "rules": [{"field_ref": "x; drop", "hidden_for": ["cio"]}]})
    assert bad.status_code == 400


async def test_health_endpoints(api: httpx.AsyncClient) -> None:
    assert (await api.get("/healthz")).json() == {"status": "ok"}
    ready = await api.get("/readyz")
    assert ready.status_code == 200 and ready.json()["checks"]["db"] == "ok"
    assert "tela_http_request_duration_seconds" in (await api.get("/metrics")).text


async def test_admin_listing_endpoints(
    api: httpx.AsyncClient, owner_conn: psycopg.Connection, make_token: Callable[..., str]
) -> None:
    bootstrap(owner_conn, "acme", ISSUER, {"TELA-Admins": "admin", "TELA-Viewers": "viewer"})
    admin = make_token("admin-1", groups=["TELA-Admins"], name="Admin One", email="admin@example.com")
    await api.get("/api/v1/me", headers=_h(make_token("v-1", groups=["TELA-Viewers"], name="Vera Viewer")))

    roles = (await api.get("/api/v1/admin/roles", headers=_h(admin))).json()["items"]
    assert {r["key"] for r in roles} == {"admin", "creator", "viewer", "cio", "executive", "delivery_manager"}
    assert next(r for r in roles if r["key"] == "admin")["is_protected"] is True

    groups = (await api.get("/api/v1/admin/groups?q=View", headers=_h(admin))).json()["items"]
    assert [g["external_group_id"] for g in groups] == ["TELA-Viewers"]
    assert groups[0]["member_count"] == 1

    users = (await api.get("/api/v1/admin/users?q=vera", headers=_h(admin))).json()["items"]
    assert [u["display_name"] for u in users] == ["Vera Viewer"]
    assert users[0]["roles"] == ["viewer"]


async def test_scope_policy_validation_paths(
    api: httpx.AsyncClient, owner_conn: psycopg.Connection, make_token: Callable[..., str]
) -> None:
    tenant = bootstrap(owner_conn, "acme", ISSUER, {"TELA-Admins": "admin"})
    cid = add_connection(owner_conn, tenant)
    admin = make_token("admin-1", groups=["TELA-Admins"])
    base = {"connection_id": cid, "entity": "projects", "description": "KSA",
            "predicates": [{"field": "region", "op": "in", "values": ["KSA"]}]}

    unknown_role = await api.post("/api/v1/admin/scope-policies", headers=_h(admin), json={**base, "role": "ghost"})
    assert unknown_role.status_code == 400
    missing_conn = await api.post("/api/v1/admin/scope-policies", headers=_h(admin),
                                  json={**base, "role": "viewer", "connection_id": "0192f3b2-0000-7000-8000-00000000dead"})
    assert missing_conn.status_code == 404

    created = (await api.post("/api/v1/admin/scope-policies", headers=_h(admin),
                              json={**base, "role": "viewer"})).json()
    # all_rows together with existing conditions is refused on PATCH as well.
    bad_patch = await api.patch(f"/api/v1/admin/scope-policies/{created['id']}", headers=_h(admin),
                                json={"all_rows": True})
    assert bad_patch.status_code == 400
    switched = await api.patch(f"/api/v1/admin/scope-policies/{created['id']}", headers=_h(admin),
                               json={"all_rows": True, "predicates": [], "is_active": False})
    assert switched.status_code == 200
    assert switched.json()["all_rows"] is True and switched.json()["is_active"] is False
    audited = owner_conn.execute(
        "SELECT count(*) FROM ops.audit_events WHERE action LIKE 'access.scope_policy.%'").fetchone()
    assert audited == (2,)  # create + successful patch; the rejected patch changes nothing
    missing = await api.patch("/api/v1/admin/scope-policies/0192f3b2-0000-7000-8000-00000000dead",
                              headers=_h(admin), json={"description": "x"})
    assert missing.status_code == 404
    # No approved model yet: no entities to pick from, so the form falls back to typing one.
    entities = await api.get(f"/api/v1/admin/scope-policies/entities?connection_id={cid}", headers=_h(admin))
    assert entities.status_code == 200 and entities.json() == {"model_version_id": None, "entities": []}
    no_conn = await api.get("/api/v1/admin/scope-policies/entities?connection_id=0192f3b2-0000-7000-8000-00000000dead",
                            headers=_h(admin))
    assert no_conn.status_code == 404


async def test_approve_rejects_mappings_that_need_no_approval(
    api: httpx.AsyncClient, owner_conn: psycopg.Connection, make_token: Callable[..., str]
) -> None:
    bootstrap(owner_conn, "acme", ISSUER, {"TELA-Admins": "admin", "TELA-Viewers": "viewer"})
    admin = make_token("admin-1", groups=["TELA-Admins"])
    mappings = (await api.get("/api/v1/admin/group-mappings", headers=_h(admin))).json()["mappings"]
    viewer = next(m for m in mappings if m["role"] == "viewer")
    resp = await api.post(f"/api/v1/admin/group-mappings/{viewer['id']}/approve", headers=_h(admin))
    assert resp.status_code == 400
    resp = await api.post("/api/v1/admin/group-mappings/0192f3b2-0000-7000-8000-00000000dead/approve",
                          headers=_h(admin))
    assert resp.status_code == 404


async def test_changed_scope_attributes_on_admin_mapping_need_reapproval(
    api: httpx.AsyncClient, owner_conn: psycopg.Connection, make_token: Callable[..., str]
) -> None:
    bootstrap(owner_conn, "acme", ISSUER, {"TELA-Admins": "admin", "TELA-Admins-2": "admin"})
    admin = make_token("admin-1", groups=["TELA-Admins"])
    etag = (await api.get("/api/v1/admin/group-mappings", headers=_h(admin))).json()["etag"]
    resp = await api.put(
        "/api/v1/admin/group-mappings", headers=_h(admin, **{"If-Match": etag}),
        json={"mappings": [
            {"idp_group_id": "TELA-Admins", "role": "admin", "scope_attributes": {"cio": ["X"]}},
            {"idp_group_id": "TELA-Admins-2", "role": "admin"},
        ]},
    )
    assert resp.status_code == 200
    status = {m["idp_group_id"]: m["status"] for m in resp.json()["mappings"]}
    assert status == {"TELA-Admins": "pending_approval", "TELA-Admins-2": "active"}


async def test_cannot_remove_or_unapprove_the_last_active_admin_mapping(
    api: httpx.AsyncClient, owner_conn: psycopg.Connection, make_token: Callable[..., str]
) -> None:
    bootstrap(owner_conn, "acme", ISSUER, {"TELA-Admins": "admin", "TELA-Viewers": "viewer"})
    admin = make_token("admin-1", groups=["TELA-Admins"])
    etag = (await api.get("/api/v1/admin/group-mappings", headers=_h(admin))).json()["etag"]
    for mappings in (
        [{"idp_group_id": "TELA-Viewers", "role": "viewer"}],
        [{"idp_group_id": "TELA-Admins", "role": "admin", "scope_attributes": {"cio": ["X"]}}],
    ):
        resp = await api.put("/api/v1/admin/group-mappings", headers=_h(admin, **{"If-Match": etag}),
                             json={"mappings": mappings})
        assert resp.status_code == 409
        assert resp.json()["error"]["details"]["reason"] == "last_active_admin"
    # Nothing changed and the admin still has access.
    assert (await api.get("/api/v1/me", headers=_h(admin))).json()["level"] == "admin"


async def test_connections_listing_respects_role(
    api: httpx.AsyncClient, owner_conn: psycopg.Connection, make_token: Callable[..., str]
) -> None:
    tenant = bootstrap(owner_conn, "acme", ISSUER, {"TELA-Admins": "admin", "TELA-Viewers": "viewer"})
    covered = add_connection(owner_conn, tenant, "TPG Postgres")
    add_connection(owner_conn, tenant, "Finance DB")
    admin = make_token("admin-1", groups=["TELA-Admins"])
    viewer = make_token("viewer-1", groups=["TELA-Viewers"])

    admin_view = (await api.get("/api/v1/connections", headers=_h(admin))).json()["items"]
    assert [c["name"] for c in admin_view] == ["Finance DB", "TPG Postgres"]
    assert (await api.get("/api/v1/connections", headers=_h(viewer))).json()["items"] == []

    await api.post("/api/v1/admin/scope-policies", headers=_h(admin), json={
        "connection_id": covered, "entity": "projects", "role": "viewer", "description": "Region: KSA",
        "predicates": [{"field": "region", "op": "in", "values": ["KSA"]}]})
    viewer_view = (await api.get("/api/v1/connections", headers=_h(viewer))).json()["items"]
    assert viewer_view == [{"id": covered, "name": "TPG Postgres", "engine": "postgres"}]


# --------------------------------------------------------------------------- preferences (US-134)


async def test_theme_preference_is_remembered_per_user(
    api: httpx.AsyncClient, owner_conn: psycopg.Connection, make_token: Callable[..., str]
) -> None:
    bootstrap(owner_conn, "acme", ISSUER, {"TELA-Viewers": "viewer"})
    ana = make_token("u-ana", groups=["TELA-Viewers"], name="Ana")
    ben = make_token("u-ben", groups=["TELA-Viewers"], name="Ben")
    assert (await api.get("/api/v1/me", headers=_h(ana))).json()["preferences"] == {
        "theme": "system", "ask_verbosity": "balanced", "layout": "default"}

    resp = await api.put("/api/v1/me/preferences", headers=_h(ana), json={"theme": "dark"})
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"theme": "dark", "ask_verbosity": "balanced", "layout": "default"}
    # US-182: each key is saved on its own — setting verbosity keeps the theme.
    resp = await api.put("/api/v1/me/preferences", headers=_h(ana), json={"ask_verbosity": "ask_freely"})
    assert resp.json() == {"theme": "dark", "ask_verbosity": "ask_freely", "layout": "default"}
    assert (await api.put("/api/v1/me/preferences", headers=_h(ana), json={"ask_verbosity": "often"})).status_code \
        == 400
    assert (await api.get("/api/v1/me", headers=_h(ana))).json()["preferences"]["theme"] == "dark"
    assert (await api.get("/api/v1/me", headers=_h(ben))).json()["preferences"]["theme"] == "system"

    # Only known keys and values are accepted; an unknown stored key never breaks /me.
    assert (await api.put("/api/v1/me/preferences", headers=_h(ana), json={"theme": "neon"})).status_code == 400
    assert (await api.put("/api/v1/me/preferences", headers=_h(ana), json={"density": "x"})).status_code == 400
    owner_conn.execute("UPDATE iam.users SET preferences = preferences || '{\"legacy\": 1}' WHERE display_name = 'Ana'")
    assert (await api.get("/api/v1/me", headers=_h(ana))).json()["preferences"] == {
        "theme": "dark", "ask_verbosity": "ask_freely", "layout": "default"}
    assert (await api.put("/api/v1/me/preferences", headers=_h(ana))).status_code == 400
