"""Free-hand query screen API (ENH-008, US-162; FEAT-060).

Extend, the Give access connection list, last use, and requests from Ask (ask, approve, decline, notifications,
audit, refusals). Grants themselves are covered by ``test_permission_grants_api.py``.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import psycopg
import pytest
from api_testkit import ISSUER, add_connection, bootstrap, needs_db
from pmo_testkit import h

from tela_api.core.db import tenant_tx
from tela_api.identity import grants
from tela_common.ids import new_id

pytestmark = [needs_db, pytest.mark.integration]

URL = "/api/v1/admin/permission-grants"
REQ = "/api/v1/admin/permission-requests"
MINE = "/api/v1/me/permission-requests"
GROUPS = {"TELA-Admins": "admin", "TELA-Viewers": "viewer"}


def _later(days: float) -> str:
    return (datetime.now(UTC) + timedelta(days=days)).isoformat()


def _policy(owner: psycopg.Connection, tenant: str, connection: str, role: str) -> None:
    owner.execute(
        "INSERT INTO iam.scope_policies (id, tenant_id, role_id, connection_id, entity_key, all_rows, description) "
        "SELECT %s, %s, id, %s, 'project', true, 'all projects' FROM iam.roles WHERE tenant_id IS NULL AND key = %s",
        (new_id(), tenant, connection, role))


def _actions(owner: psycopg.Connection) -> list[str]:
    return [r[0] for r in owner.execute(
        "SELECT action FROM ops.audit_events WHERE action LIKE 'permission.%' ORDER BY occurred_at, action")]


def _notes(owner: psycopg.Connection) -> list[tuple[str, str, str]]:
    return [(r[0], r[1], r[2]) for r in owner.execute(
        "SELECT user_id::text, kind, title FROM ops.notifications ORDER BY created_at, kind")]


@pytest.fixture
async def world(api: httpx.AsyncClient, owner_conn: psycopg.Connection,
                make_token: Callable[..., str]) -> dict[str, Any]:
    tenant = bootstrap(owner_conn, "acme", ISSUER, GROUPS)
    seen = add_connection(owner_conn, tenant, "TPG Postgres")
    hidden = add_connection(owner_conn, tenant, "Finance")
    _policy(owner_conn, tenant, seen, "viewer")
    admin = make_token("admin-1", groups=["TELA-Admins"], name="Ada Admin")
    viewer = make_token("viewer-1", groups=["TELA-Viewers"], name="Vic Viewer")
    ids = {}
    for key, token in (("admin_id", admin), ("viewer_id", viewer)):
        resp = await api.get("/api/v1/me", headers=h(token))
        assert resp.status_code == 200, resp.text
        ids[key] = resp.json()["user_id"]
    return {"tenant": tenant, "seen": seen, "hidden": hidden, "admin": admin, "viewer": viewer, **ids}


async def _grant(api: httpx.AsyncClient, w: dict[str, Any], **kw: Any) -> dict[str, Any]:
    body = {"user_id": w["viewer_id"], "connection_id": w["seen"], "reason": "Portfolio review",
            "expires_at": _later(90), **kw}
    resp = await api.post(URL, headers=h(w["admin"]), json=body)
    assert resp.status_code == 201, resp.text
    return dict(resp.json())


# --------------------------------------------------------------------------- extend, connections, last use


async def test_extend(api: httpx.AsyncClient, owner_conn: psycopg.Connection, world: dict[str, Any]) -> None:
    w = world
    g = await _grant(api, w, expires_at=_later(5))
    async with tenant_tx(w["tenant"]) as conn:
        assert (await grants.sweep_tenant(conn, w["tenant"]))["reminded"] == 1

    resp = await api.patch(f"{URL}/{g['id']}", headers=h(w["admin"]), json={"expires_at": _later(200)})
    assert resp.status_code == 200, resp.text
    out = resp.json()
    assert out["expires_at"] > g["expires_at"] and out["status"] == "active"
    assert owner_conn.execute("SELECT reminded_at FROM iam.permission_grants WHERE id = %s",
                              (g["id"],)).fetchone() == (None,)  # reminded again for the new date
    assert _actions(owner_conn) == ["permission.granted", "permission.extended"]

    assert (await api.patch(f"{URL}/{g['id']}", headers=h(w["admin"]),
                            json={"expires_at": _later(400)})).status_code == 400  # at most a year
    assert (await api.patch(f"{URL}/{g['id']}", headers=h(w["admin"]),
                            json={"expires_at": _later(-1)})).status_code == 400
    assert (await api.patch(f"{URL}/{g['id']}", headers=h(w["viewer"]),
                            json={"expires_at": _later(9)})).status_code == 403
    assert (await api.patch(f"{URL}/{new_id()}", headers=h(w["admin"]),
                            json={"expires_at": _later(9)})).status_code == 404
    # A removed grant can't be extended.
    assert (await api.delete(f"{URL}/{g['id']}", headers=h(w["admin"]))).status_code == 204
    assert (await api.patch(f"{URL}/{g['id']}", headers=h(w["admin"]),
                            json={"expires_at": _later(9)})).status_code == 404


async def test_connections_for_a_person(api: httpx.AsyncClient, world: dict[str, Any]) -> None:
    w = world
    g = await _grant(api, w)
    resp = await api.get(f"{URL}/connections", headers=h(w["admin"]), params={"user_id": w["viewer_id"]})
    assert resp.status_code == 200, resp.text
    items = {x["name"]: x for x in resp.json()["items"]}
    assert items["TPG Postgres"]["visible"] is True and items["TPG Postgres"]["grant"]["id"] == g["id"]
    assert items["Finance"]["visible"] is False and items["Finance"]["grant"] is None
    assert (await api.get(f"{URL}/connections", headers=h(w["admin"]),
                          params={"user_id": new_id()})).status_code == 404
    assert (await api.get(f"{URL}/connections", headers=h(w["viewer"]),
                          params={"user_id": w["viewer_id"]})).status_code == 403


async def test_last_used(api: httpx.AsyncClient, world: dict[str, Any]) -> None:
    w = world
    g = await _grant(api, w)
    assert g["last_used_at"] is None
    async with tenant_tx(w["tenant"]) as conn:
        await grants.mark_used(conn, w["viewer_id"], "ask.freehand", w["seen"])
    listed = (await api.get(URL, headers=h(w["admin"]))).json()["items"]
    assert listed[0]["last_used_at"] is not None


# --------------------------------------------------------------------------- requests from Ask


async def _ask(api: httpx.AsyncClient, w: dict[str, Any], **kw: Any) -> httpx.Response:
    return await api.post(MINE, headers=h(w["viewer"]),
                          json={"connection_id": w["seen"], "note": "Stage-date questions", **kw})


async def test_request_then_approve(api: httpx.AsyncClient, owner_conn: psycopg.Connection,
                                    world: dict[str, Any]) -> None:
    w = world
    resp = await _ask(api, w)
    assert resp.status_code == 201, resp.text
    q = resp.json()
    assert q["status"] == "pending" and q["connection"]["name"] == "TPG Postgres"
    assert (await _ask(api, w)).status_code == 409  # one pending request per connection
    assert [x["id"] for x in (await api.get(MINE, headers=h(w["viewer"]))).json()["items"]] == [q["id"]]
    assert _notes(owner_conn) == [(w["admin_id"], "permission.requested",
                                   "Vic Viewer asks to ask free-hand questions on TPG Postgres")]

    pending = (await api.get(REQ, headers=h(w["admin"]))).json()["items"]
    assert [x["id"] for x in pending] == [q["id"]]
    assert (await api.get(REQ, headers=h(w["viewer"]))).status_code == 403

    ok = await api.post(f"{REQ}/{q['id']}/approve", headers=h(w["admin"]), json={"expires_at": _later(30)})
    assert ok.status_code == 200, ok.text
    body = ok.json()
    assert body["request"]["status"] == "approved" and body["request"]["grant_id"] == body["grant"]["id"]
    assert body["grant"]["reason"] == "Stage-date questions"  # the note when no reason is given
    me = (await api.get("/api/v1/me", headers=h(w["viewer"]))).json()
    assert [x["connection_id"] for x in me["grants"]] == [w["seen"]]
    assert _notes(owner_conn)[-1] == (w["viewer_id"], "permission.request_approved",
                                      "You can now ask free-hand questions on TPG Postgres")
    assert _actions(owner_conn) == ["permission.requested", "permission.granted"]
    assert (await api.get(REQ, headers=h(w["admin"]))).json()["items"] == []
    decided = (await api.get(REQ, headers=h(w["admin"]), params={"include_decided": True})).json()["items"]
    assert [x["status"] for x in decided] == ["approved"]

    # Decided once only; with a live grant there is nothing to ask for.
    assert (await api.post(f"{REQ}/{q['id']}/decline", headers=h(w["admin"]),
                           json={"reason": "late"})).status_code == 409
    assert (await _ask(api, w)).status_code == 409


async def test_request_then_decline(api: httpx.AsyncClient, owner_conn: psycopg.Connection,
                                    world: dict[str, Any]) -> None:
    w = world
    q = (await _ask(api, w, note=None)).json()
    assert (await api.post(f"{REQ}/{q['id']}/decline", headers=h(w["admin"]),
                           json={"reason": "   "})).status_code == 400  # a reason is required
    resp = await api.post(f"{REQ}/{q['id']}/decline", headers=h(w["admin"]),
                          json={"reason": "Use the Past planning measure"})
    assert resp.status_code == 200, resp.text
    out = resp.json()
    assert out["status"] == "declined" and out["decided_by"]["display_name"] == "Ada Admin"
    assert out["decision_reason"] == "Use the Past planning measure"
    assert (await api.get("/api/v1/me", headers=h(w["viewer"]))).json()["grants"] == []
    kind, title = _notes(owner_conn)[-1][1:]
    assert kind == "permission.request_declined" and "was declined" in title
    assert _actions(owner_conn) == ["permission.requested", "permission.request_declined"]
    # Declined: the person may ask again.
    assert (await _ask(api, w)).status_code == 201


async def test_request_refusals(api: httpx.AsyncClient, world: dict[str, Any]) -> None:
    w = world
    assert (await _ask(api, w, connection_id=w["hidden"])).status_code == 404  # can't see it
    assert (await _ask(api, w, connection_id="nope")).status_code == 404
    assert (await _ask(api, w, permission="audit.read")).status_code == 400
    assert (await _ask(api, w, note="x" * 501)).status_code in (400, 422)
    assert (await api.post(f"{REQ}/{new_id()}/approve", headers=h(w["admin"]), json={})).status_code == 404
    q = (await _ask(api, w)).json()
    assert (await api.post(f"{REQ}/{q['id']}/approve", headers=h(w["viewer"]), json={})).status_code == 403
    assert (await api.post(f"{REQ}/{q['id']}/approve", headers=h(w["admin"]),
                           json={"expires_at": _later(400)})).status_code == 400
    # No end date given: 90 days.
    resp = await api.post(f"{REQ}/{q['id']}/approve", headers=h(w["admin"]), json={})
    assert resp.status_code == 200, resp.text
    days = (datetime.fromisoformat(resp.json()["grant"]["expires_at"]) - datetime.now(UTC)).days
    assert days in (89, 90)
