"""Dashboard groups for the sidebar (ENH-009, US-169; FEAT-061).

Create, rename, reorder and delete; who may change what (W2); each reader sees only the dashboards they can open and
no empty groups (W3); a dashboard in several groups (W4); "Add to group…" from a dashboard; other tenant; audit.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx
import psycopg
import pytest
from api_testkit import APP_URL, ISSUER, bootstrap, needs_db
from pmo_testkit import h

from tela_common.ids import new_id

pytestmark = [needs_db, pytest.mark.integration]

URL = "/api/v1/dashboard-groups"
MINE = "/api/v1/me/dashboard-groups"
GROUPS = {"TELA-Admins": "admin", "TELA-Creators": "creator", "TELA-Viewers": "viewer"}
ADMIN_ROLE = "00000000-0000-7000-8000-00000000a001"


def _dashboard(owner: psycopg.Connection, tenant: str, title: str, owner_id: str, *,
               published_to: str | None = None, deleted: bool = False) -> str:
    did = new_id()
    owner.execute("INSERT INTO content.dashboards (id, tenant_id, title, kind, status, owner_id, deleted_at) "
                  "VALUES (%s, %s, %s, 'user', %s, %s, CASE WHEN %s THEN now() END)",
                  (did, tenant, title, "published" if published_to else "draft", owner_id, deleted))
    if published_to:
        owner.execute("INSERT INTO content.publications (id, tenant_id, dashboard_id, role_id, all_roles) "
                      "VALUES (%s, %s, %s, %s, %s)", (new_id(), tenant, did, None if published_to == "all" else
                                                      published_to, published_to == "all"))
    return did


def _actions(owner: psycopg.Connection) -> list[str]:
    return [r[0] for r in owner.execute(
        "SELECT action FROM ops.audit_events WHERE action LIKE 'dashboard_group.%' ORDER BY occurred_at")]


@pytest.fixture
async def world(api: httpx.AsyncClient, owner_conn: psycopg.Connection,
                make_token: Callable[..., str]) -> dict[str, Any]:
    tenant = bootstrap(owner_conn, "acme", ISSUER, GROUPS)
    w: dict[str, Any] = {"tenant": tenant}
    for key, groups, name in (("admin", ["TELA-Admins"], "Ada Admin"), ("creator", ["TELA-Creators"], "Cleo Creator"),
                              ("other", ["TELA-Creators"], "Otto Creator"), ("viewer", ["TELA-Viewers"], "Vic Viewer")):
        w[key] = make_token(f"{key}-1", groups=groups, name=name)
        resp = await api.get("/api/v1/me", headers=h(w[key]))
        assert resp.status_code == 200, resp.text
        w[f"{key}_id"] = resp.json()["user_id"]
    w["for_viewers"] = _dashboard(owner_conn, tenant, "Delivery", w["creator_id"], published_to="all")
    w["for_admins"] = _dashboard(owner_conn, tenant, "Board pack", w["admin_id"], published_to=ADMIN_ROLE)
    w["draft"] = _dashboard(owner_conn, tenant, "Cleo's draft", w["creator_id"])
    w["others_draft"] = _dashboard(owner_conn, tenant, "Otto's draft", w["other_id"])
    return w


async def _create(api: httpx.AsyncClient, token: str, name: str, **kw: Any) -> dict[str, Any]:
    resp = await api.post(URL, headers=h(token), json={"name": name, **kw})
    assert resp.status_code == 201, resp.text
    return dict(resp.json())


async def _items(api: httpx.AsyncClient, token: str, gid: str, ids: list[str]) -> httpx.Response:
    return await api.put(f"{URL}/{gid}/items", headers=h(token), json={"dashboard_ids": ids})


async def test_create_rename_reorder_delete(api: httpx.AsyncClient, owner_conn: psycopg.Connection,
                                            world: dict[str, Any]) -> None:
    w = world
    a = await _create(api, w["admin"], "  Delivery  ", icon="truck")
    assert a["name"] == "Delivery" and a["icon"] == "truck" and a["position"] == 0 and a["can_edit"]
    assert a["created_by"] == {"id": w["admin_id"], "name": "Ada Admin"} and a["dashboards"] == []
    b = await _create(api, w["creator"], "Finance")
    assert b["position"] == 1

    clash = await api.post(URL, headers=h(w["creator"]), json={"name": "delivery"})
    assert clash.status_code == 409 and "already exists" in clash.json()["error"]["message"]
    blank = await api.post(URL, headers=h(w["creator"]), json={"name": "   "})
    assert blank.status_code in (400, 422)
    bad_icon = await api.post(URL, headers=h(w["creator"]), json={"name": "X", "icon": "<svg>"})
    assert bad_icon.status_code in (400, 422)

    renamed = await api.patch(f"{URL}/{b['id']}", headers=h(w["creator"]), json={"name": "Money", "clear_icon": True})
    assert renamed.status_code == 200 and renamed.json()["name"] == "Money" and renamed.json()["icon"] is None
    taken = await api.patch(f"{URL}/{b['id']}", headers=h(w["creator"]), json={"name": "DELIVERY"})
    assert taken.status_code == 409

    order = await api.put(f"{URL}/order", headers=h(w["admin"]), json={"group_ids": [b["id"], a["id"]]})
    assert order.status_code == 200, order.text
    assert [g["name"] for g in order.json()["items"]] == ["Money", "Delivery"] and order.json()["can_reorder"]
    partial = await api.put(f"{URL}/order", headers=h(w["admin"]), json={"group_ids": [b["id"]]})
    assert partial.status_code == 400

    assert (await api.delete(f"{URL}/{a['id']}", headers=h(w["admin"]))).status_code == 204
    assert [g["name"] for g in (await api.get(URL, headers=h(w["admin"]))).json()["items"]] == ["Money"]
    assert (await api.delete(f"{URL}/{a['id']}", headers=h(w["admin"]))).status_code == 404
    again = await _create(api, w["admin"], "Delivery")  # a deleted group's name is free again
    assert again["id"] != a["id"]
    assert _actions(owner_conn) == ["dashboard_group.created", "dashboard_group.created", "dashboard_group.updated",
                                    "dashboard_group.reordered", "dashboard_group.deleted",
                                    "dashboard_group.created"]


async def test_who_may_change_what(api: httpx.AsyncClient, world: dict[str, Any]) -> None:
    w = world
    mine = await _create(api, w["creator"], "Cleo's group")
    theirs = await _create(api, w["other"], "Otto's group")

    listing = {g["name"]: g for g in (await api.get(URL, headers=h(w["creator"]))).json()["items"]}
    assert listing["Cleo's group"]["can_edit"] and not listing["Otto's group"]["can_edit"]
    assert (await api.get(URL, headers=h(w["creator"]))).json()["can_reorder"] is False

    for call in (api.patch(f"{URL}/{theirs['id']}", headers=h(w["creator"]), json={"name": "Mine now"}),
                 api.delete(f"{URL}/{theirs['id']}", headers=h(w["creator"])),
                 _items(api, w["creator"], theirs["id"], [w["for_viewers"]])):
        resp = await call
        assert resp.status_code == 403 and "Only the person who created" in resp.json()["error"]["message"]
    no_order = await api.put(f"{URL}/order", headers=h(w["creator"]), json={"group_ids": [mine["id"], theirs["id"]]})
    assert no_order.status_code == 403

    # An Admin changes any group.
    assert (await api.patch(f"{URL}/{theirs['id']}", headers=h(w["admin"]), json={"name": "Renamed"})).status_code == 200
    # A Viewer cannot manage groups at all.
    assert (await api.get(URL, headers=h(w["viewer"]))).status_code == 403
    assert (await api.post(URL, headers=h(w["viewer"]), json={"name": "V"})).status_code == 403
    assert (await api.patch(f"{URL}/not-a-uuid", headers=h(w["admin"]), json={"name": "Z"})).status_code == 404


async def test_readers_see_only_what_they_can_open(api: httpx.AsyncClient, owner_conn: psycopg.Connection,
                                                   world: dict[str, Any]) -> None:
    w = world
    g = await _create(api, w["admin"], "Delivery")
    empty_for_viewer = await _create(api, w["admin"], "Board")
    await _create(api, w["admin"], "Empty")
    resp = await _items(api, w["admin"], g["id"], [w["for_admins"], w["for_viewers"], w["draft"]])
    assert resp.status_code == 200, resp.text
    assert [d["title"] for d in resp.json()["dashboards"]] == ["Board pack", "Delivery", "Cleo's draft"]
    assert (await _items(api, w["admin"], empty_for_viewer["id"], [w["for_admins"]])).status_code == 200

    viewer = (await api.get(MINE, headers=h(w["viewer"]))).json()["items"]
    assert viewer == [{"id": g["id"], "name": "Delivery", "icon": None, "dashboards": [
        {"id": w["for_viewers"], "title": "Delivery", "kind": "user", "link": f"/dashboards/{w['for_viewers']}",
         "published": True}]}]  # "Board" and "Empty" hold nothing the Viewer can open
    assert "Board pack" not in str(viewer) and "Cleo's draft" not in str(viewer)

    creator = (await api.get(MINE, headers=h(w["creator"]))).json()["items"]
    assert [(x["name"], [d["title"] for d in x["dashboards"]]) for x in creator] == [
        ("Delivery", ["Delivery", "Cleo's draft"])]
    assert creator[0]["dashboards"][1]["link"] == f"/dashboards/{w['draft']}/draft"

    # A share opens a draft to the person it was shared with, and so the group shows it.
    owner_conn.execute("INSERT INTO content.shares (id, tenant_id, object_type, object_id, grantee_user_id, "
                       "shared_by) VALUES (%s, %s, 'dashboard', %s, %s, %s)",
                       (new_id(), w["tenant"], w["draft"], w["viewer_id"], w["creator_id"]))
    viewer = (await api.get(MINE, headers=h(w["viewer"]))).json()["items"]
    assert [d["title"] for d in viewer[0]["dashboards"]] == ["Delivery", "Cleo's draft"]

    # A Creator editing the group sees how many items are hidden from them, never their titles, and keeps them.
    other = (await api.get(URL, headers=h(w["other"]))).json()["items"]
    first = next(x for x in other if x["name"] == "Delivery")
    assert first["hidden_count"] == 2 and [d["title"] for d in first["dashboards"]] == ["Delivery"]
    # Unpublishing takes a dashboard out of the sidebar at once; deleting it too.
    owner_conn.execute("DELETE FROM content.publications WHERE dashboard_id = %s", (w["for_viewers"],))
    owner_conn.execute("UPDATE content.dashboards SET deleted_at = now() WHERE id = %s", (w["draft"],))
    assert (await api.get(MINE, headers=h(w["viewer"]))).json()["items"] == []


async def test_items_keep_what_the_editor_cannot_see(api: httpx.AsyncClient, owner_conn: psycopg.Connection,
                                                     world: dict[str, Any]) -> None:
    w = world
    g = await _create(api, w["other"], "Mixed")
    # Otto cannot add a dashboard he cannot open; the refusal names nothing.
    refused = await _items(api, w["other"], g["id"], [w["for_admins"]])
    assert refused.status_code == 404 and "Board pack" not in refused.text
    assert (await _items(api, w["other"], g["id"], [w["for_viewers"], w["for_viewers"]])).status_code == 400
    assert (await _items(api, w["other"], g["id"], [w["for_viewers"], w["others_draft"]])).status_code == 200
    # The Admin adds one Otto cannot see.
    assert (await _items(api, w["admin"], g["id"], [w["for_admins"], w["for_viewers"], w["others_draft"]])
            ).status_code == 200
    # Otto reorders and removes what he sees; the Admin's item stays.
    resp = await _items(api, w["other"], g["id"], [w["others_draft"]])
    assert resp.status_code == 200 and resp.json()["hidden_count"] == 1
    kept = [str(r[0]) for r in owner_conn.execute(
        "SELECT dashboard_id FROM content.dashboard_group_items WHERE group_id = %s ORDER BY position", (g["id"],))]
    assert kept == [w["others_draft"], w["for_admins"]]
    # Removing the dashboard row removes the item.
    owner_conn.execute("DELETE FROM content.dashboards WHERE id = %s", (w["others_draft"],))
    assert [d["id"] for d in (await api.get(URL, headers=h(w["admin"]))).json()["items"][0]["dashboards"]] == [
        w["for_admins"]]
    assert _actions(owner_conn).count("dashboard_group.items_changed") == 3


async def test_add_to_group_from_a_dashboard(api: httpx.AsyncClient, world: dict[str, Any]) -> None:
    w = world
    a = await _create(api, w["creator"], "A")
    b = await _create(api, w["creator"], "B")
    otto = await _create(api, w["other"], "Otto's")
    assert (await _items(api, w["other"], otto["id"], [w["for_viewers"]])).status_code == 200

    url = f"/api/v1/dashboards/{w['for_viewers']}/groups"
    resp = await api.put(url, headers=h(w["creator"]), json={"group_ids": [a["id"], b["id"]]})
    assert resp.status_code == 200, resp.text
    assert set(resp.json()["group_ids"]) == {a["id"], b["id"], otto["id"]}  # Otto's group is left as it is
    resp = await api.put(url, headers=h(w["creator"]), json={"group_ids": [b["id"]]})
    assert set(resp.json()["group_ids"]) == {b["id"], otto["id"]}
    mine = {g["name"]: [d["id"] for d in g["dashboards"]] for g in (await api.get(MINE, headers=h(w["creator"]))
                                                                     ).json()["items"]}
    assert mine == {"B": [w["for_viewers"]], "Otto's": [w["for_viewers"]]}  # one dashboard in two groups (W4)

    assert (await api.put(url, headers=h(w["creator"]), json={"group_ids": [otto["id"]]})).status_code == 403
    assert (await api.put(url, headers=h(w["creator"]), json={"group_ids": [new_id()]})).status_code == 404
    hidden = f"/api/v1/dashboards/{w['for_admins']}/groups"
    assert (await api.put(hidden, headers=h(w["creator"]), json={"group_ids": [a["id"]]})).status_code == 404


async def test_other_tenant_sees_nothing(api: httpx.AsyncClient, owner_conn: psycopg.Connection,
                                         world: dict[str, Any]) -> None:
    w = world
    g = await _create(api, w["admin"], "Delivery")
    assert (await _items(api, w["admin"], g["id"], [w["for_viewers"]])).status_code == 200
    other = bootstrap(owner_conn, "globex", ISSUER + "/globex", {"G-Admins": "admin"})
    assert APP_URL
    with psycopg.connect(APP_URL) as app, app.transaction():
        app.execute("SELECT set_config('app.tenant_id', %s, true)", (other,))
        assert app.execute("SELECT count(*) FROM content.dashboard_groups").fetchone() == (0,)
        assert app.execute("SELECT count(*) FROM content.dashboard_group_items").fetchone() == (0,)
        app.execute("SELECT set_config('app.tenant_id', %s, true)", (w["tenant"],))
        assert app.execute("SELECT count(*) FROM content.dashboard_groups").fetchone() == (1,)
