"""SCIM parsing, filters, rate limit and Keycloak sign-out without a database (US-118, US-119)."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from tela_api.core.settings import Settings
from tela_api.identity.provisioning import IdpSessions, _drift_text, _has_drift, _signature, token_hash
from tela_api.identity.scim import RateLimiter, ScimError, UserIn, apply_user_patch, parse_filter, parse_user

USERS = {"username": "lower(u.scim_user_name) = lower(:v)", "externalid": "u.scim_external_id = :v"}


def test_filters() -> None:
    assert parse_filter(None, USERS) is None and parse_filter("  ", USERS) is None
    assert parse_filter('userName eq "a@b.c"', USERS) == (USERS["username"], "a@b.c")
    assert parse_filter('externalId EQ "x \\"y\\""', USERS) == (USERS["externalid"], 'x "y"')
    for bad in ('displayName eq "x"', 'userName co "x"', "userName eq x", 'userName eq "x" and active eq true'):
        with pytest.raises(ScimError) as err:
            parse_filter(bad, USERS)
        assert err.value.status == 400 and err.value.scim_type == "invalidFilter"


def test_parse_user() -> None:
    u = parse_user({"userName": " ada@x.io ", "externalId": "oid", "active": "False",
                    "name": {"givenName": "Ada", "familyName": "Lovelace", "middleName": "ignored"},
                    "emails": [{"value": "home@x.io", "type": "home"}, {"value": "work@x.io", "type": "work"}]})
    assert (u.user_name, u.external_id, u.active, u.email) == ("ada@x.io", "oid", False, "work@x.io")
    assert u.display() == "Ada Lovelace"
    assert parse_user({"userName": "bob"}).display() == "bob"
    assert parse_user({"userName": "c", "displayName": "Cee", "emails": [
        {"value": "a@x", "type": "work"}, {"value": "p@x", "primary": True}]}).email == "p@x"
    for body in ({}, {"userName": ""}, {"userName": "x", "active": "maybe"}, {"userName": "x", "name": "Ada"},
                 {"userName": "x", "emails": "a@b"}, {"userName": "x" * 400}, {"userName": ["x"]}):
        with pytest.raises(ScimError) as err:
            parse_user(body)
        assert err.value.status == 400


def test_user_patch() -> None:
    cur = UserIn(user_name="a@x", external_id="oid", display_name="A", name={"givenName": "A"}, email="a@x")
    out = apply_user_patch(cur, [
        ("replace", "active", "False"),
        ("replace", "name.familyName", "Zed"),
        ("remove", "name.givenName", None),
        ("replace", 'emails[type eq "work"].value', "z@x"),
        ("add", "urn:ietf:params:scim:schemas:extension:enterprise:2.0:User:department", "PMO"),
        ("replace", "title", "ignored"),
        ("replace", None, {"displayName": "Zed", "userName": "z@x", "externalId": "oid-2"}),
    ])
    assert (out.active, out.name, out.email, out.display_name, out.user_name, out.external_id) == (
        False, {"familyName": "Zed"}, "z@x", "Zed", "z@x", "oid-2")
    assert cur.active is True and cur.name == {"givenName": "A"}  # the original is untouched
    full = apply_user_patch(cur, [("replace", "urn:ietf:params:scim:schemas:core:2.0:User:displayName", "Q"),
                                  ("replace", "name", {"formatted": "Q Q"}), ("remove", "emails", None),
                                  ("remove", "externalId", None), ("remove", "displayName", None)])
    assert full.name == {"formatted": "Q Q"} and full.email is None and full.external_id is None
    assert full.display() == "Q Q"
    for ops in ([("remove", "userName", None)], [("replace", None, "x")]):
        with pytest.raises(ScimError):
            apply_user_patch(cur, ops)


async def test_rate_limiter_local_and_redis() -> None:
    local = RateLimiter(2)
    assert [await local.retry_after(None, "t") for _ in range(2)] == [None, None]
    wait = await local.retry_after(None, "t")
    assert wait is not None and 1 <= wait <= 60
    assert await local.retry_after(None, "other") is None

    class FakeRedis:
        def __init__(self) -> None:
            self.n: dict[str, int] = {}
            self.expiries: list[str] = []

        async def incr(self, key: str) -> int:
            self.n[key] = self.n.get(key, 0) + 1
            return self.n[key]

        async def expire(self, key: str, seconds: int) -> None:
            self.expiries.append(key)

    redis = FakeRedis()
    shared = RateLimiter(1)
    assert await shared.retry_after(redis, "t") is None
    assert await shared.retry_after(redis, "t") is not None
    assert len(redis.expiries) == 1


async def test_keycloak_logout() -> None:
    seen: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, str(request.url)))
        if request.url.path.endswith("/token"):
            assert b"client_credentials" in request.content
            return httpx.Response(200, json={"access_token": "at"})
        assert request.headers["authorization"] == "Bearer at"
        return httpx.Response(404 if "gone" in request.url.path else 204)

    settings = Settings(env="test", idp_admin_url="http://keycloak:8080", idp_admin_client_id="tela-scim",
                        idp_admin_client_secret="s")
    sessions = IdpSessions(settings, httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    assert sessions.configured
    await sessions.logout("http://auth.localhost:8088/realms/tela", "kc-user")
    await sessions.logout("http://auth.localhost:8088/realms/tela", "gone")  # no Keycloak account: fine
    assert seen[1] == ("POST", "http://keycloak:8080/admin/realms/tela/users/kc-user/logout")
    with pytest.raises(ValueError):
        await sessions.logout("http://issuer.example/oidc", "x")

    def failing(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"access_token": "at"}) if request.url.path.endswith("/token") \
            else httpx.Response(503)

    broken = IdpSessions(settings, httpx.AsyncClient(transport=httpx.MockTransport(failing)))
    with pytest.raises(httpx.HTTPStatusError):
        await broken.logout("http://kc/realms/tela", "u")
    assert not IdpSessions(Settings(env="test")).configured


def _findings(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {"fixed": {"memberships_removed": 0, "sessions_revoked": 0}, "roles_refreshed": [],
                            "unlinked_users": {"count": 0, "items": []}, "deleted_groups_mapped": [],
                            "deprovisioned_owners": [], "failed_tasks": []}
    return {**base, **over}


def test_drift_summary_and_signature() -> None:
    clean = _findings()
    assert not _has_drift(clean)
    drift = _findings(fixed={"memberships_removed": 2, "sessions_revoked": 0},
                      unlinked_users={"count": 1, "items": [{"id": "u1"}]},
                      deleted_groups_mapped=[{"id": "g1"}], deprovisioned_owners=[{"user": {"id": "u2"}}],
                      failed_tasks=[{"id": "t1"}])
    assert _has_drift(drift)
    text = _drift_text(drift)
    for part in ("sign-out(s) failed", "not provisioned", "deleted in Entra", "still own content", "fixed 2"):
        assert part in text
    # Fixes alone do not change what the Admins are told about.
    assert _signature(drift) == _signature({**drift, "fixed": {"memberships_removed": 0, "sessions_revoked": 0}})
    assert _signature(drift) != _signature(clean)
    assert len(token_hash("x")) == 64
