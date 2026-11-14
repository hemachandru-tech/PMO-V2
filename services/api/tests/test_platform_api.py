"""Audit facets and the notifications centre (US-146; API-052, API-057)."""

from __future__ import annotations

import uuid
from collections.abc import Callable

import httpx
import psycopg
import pytest
from api_testkit import ISSUER, add_connection, bootstrap, needs_db

pytestmark = [needs_db, pytest.mark.integration]


def _h(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def test_audit_facets_count_each_filter_under_the_others(
    api: httpx.AsyncClient, owner_conn: psycopg.Connection, make_token: Callable[..., str]
) -> None:
    tenant = bootstrap(owner_conn, "acme", ISSUER, {"TELA-Admins": "admin", "TELA-CIO": "cio", "TELA-Viewers": "viewer"})
    cid = add_connection(owner_conn, tenant)
    admin = make_token("admin-1", groups=["TELA-Admins"], name="Ada Admin")
    viewer = make_token("viewer-1", groups=["TELA-Viewers"])
    for entity in ("projects", "requests"):
        created = await api.post("/api/v1/admin/scope-policies", headers=_h(admin), json={
            "connection_id": cid, "entity": entity, "role": "cio", "description": entity, "all_rows": True})
        assert created.status_code == 201, created.text
    await api.post("/api/v1/admin/groups", headers=_h(admin), json={"external_group_id": "G-1", "display_name": "G"})

    facets = await api.get("/api/v1/admin/audit/facets", headers=_h(admin))
    assert facets.status_code == 200, facets.text
    body = facets.json()
    actions = {f["value"]: f["count"] for f in body["action"]}
    assert actions["access.scope_policy.created"] == 2
    assert body["actor"][0]["value"] == "Ada Admin"
    assert body["total"] == sum(f["count"] for f in body["action"])

    # Filtering by an action narrows the other facets but keeps every action counted.
    narrowed = (await api.get("/api/v1/admin/audit/facets?action=access.scope_policy.created", headers=_h(admin))).json()
    assert narrowed["total"] == 2
    assert {f["value"] for f in narrowed["action"]} == set(actions)
    assert narrowed["object_type"] == [{"value": "scope_policy", "count": 2}]
    assert (await api.get("/api/v1/admin/audit/facets", headers=_h(viewer))).status_code == 403


async def test_notifications_page_by_kind_with_counts(
    api: httpx.AsyncClient, owner_conn: psycopg.Connection, make_token: Callable[..., str]
) -> None:
    tenant = bootstrap(owner_conn, "acme", ISSUER, {"TELA-Viewers": "viewer"})
    viewer = make_token("viewer-1", groups=["TELA-Viewers"])
    me = (await api.get("/api/v1/me", headers=_h(viewer))).json()
    for i in range(5):
        owner_conn.execute(
            "INSERT INTO ops.notifications (id, tenant_id, user_id, kind, title, body, created_at, read_at) "
            "VALUES (%s, %s, %s, %s, %s, 'b', now() - make_interval(mins => %s), CASE WHEN %s THEN now() END)",
            (str(uuid.uuid4()), tenant, me["user_id"], "share.created" if i % 2 else "dashboard.published",
             f"n{i}", i, i == 4))

    first = (await api.get("/api/v1/notifications?limit=2", headers=_h(viewer))).json()
    assert [n["title"] for n in first["items"]] == ["n0", "n1"] and first["unread_count"] == 4
    assert {k["kind"]: (k["count"], k["unread"]) for k in first["kinds"]} == {
        "dashboard.published": (3, 2), "share.created": (2, 2)}
    second = (await api.get(f"/api/v1/notifications?limit=2&cursor={first['next_cursor']}", headers=_h(viewer))).json()
    assert [n["title"] for n in second["items"]] == ["n2", "n3"]
    last = (await api.get(f"/api/v1/notifications?limit=2&cursor={second['next_cursor']}", headers=_h(viewer))).json()
    assert [n["title"] for n in last["items"]] == ["n4"] and last["next_cursor"] is None

    shares = (await api.get("/api/v1/notifications?kind=share.created", headers=_h(viewer))).json()
    assert [n["title"] for n in shares["items"]] == ["n1", "n3"]
    unread = (await api.get("/api/v1/notifications?kind=dashboard.published&unread=true", headers=_h(viewer))).json()
    assert [n["title"] for n in unread["items"]] == ["n0", "n2"]
    assert (await api.get("/api/v1/notifications?cursor=nope", headers=_h(viewer))).status_code == 400
