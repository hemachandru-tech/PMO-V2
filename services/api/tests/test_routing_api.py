"""Multi-connection routing through the API (US-116; REQ-033, REQ-034).

A second approved connection with the same PMO model makes a question ambiguous: Ask lists both sources to
choose from. Naming the source, choosing it, or following up in the same conversation routes without asking,
and the new connection answers right after approval, without a restart.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any

import httpx
import psycopg
import pytest
from api_testkit import needs_db
from pmo_testkit import AS_OF, World, create_connection, h, load_packs, setup_world
from test_ask_api import ask, by_event

pytestmark = [needs_db, pytest.mark.integration]


@pytest.fixture
async def world(stack: httpx.AsyncClient, owner_conn: psycopg.Connection, make_token: Callable[..., str],
                tpg_source: dict[str, Any]) -> World:
    return await setup_world(stack, owner_conn, make_token, tpg_source)


async def second_source(api: httpx.AsyncClient, world: World, src: dict[str, Any]) -> str:
    admin = world.tokens["admin"]
    cid = await create_connection(api, admin, src, name="TPG Replica")
    assert (await api.post(f"/api/v1/connections/{cid}/test", headers=h(admin))).json()["status"] == "connected"
    assert (await api.post(f"/api/v1/connections/{cid}/introspect", headers=h(admin))).status_code == 202
    load_packs(os.environ["TELA_TEST_OWNER_DATABASE_URL"])
    packs = (await api.get("/api/v1/starter-packs", headers=h(admin))).json()["items"]
    pack_id = max((p for p in packs if p["pack_key"] == "pmo_tpg"),
                  key=lambda p: tuple(int(x) for x in p["pack_version"].split(".")))["id"]
    install = await api.post(f"/api/v1/starter-packs/{pack_id}/install", headers=h(admin),
                             json={"connection_id": cid, "reporting_date": AS_OF})
    model_id = install.json()["model_id"]
    draft = (await api.get(f"/api/v1/models/{model_id}/draft", headers=h(admin))).json()
    approved = await api.post(f"/api/v1/models/{model_id}/approve", headers=h(admin),
                              json={"draft_etag": draft["draft_etag"]})
    assert approved.status_code == 201, approved.text
    for entity in ("projects", "project_requests"):
        await api.post("/api/v1/admin/scope-policies", headers=h(admin), json={
            "connection_id": cid, "entity": entity, "role": "admin", "all_rows": True, "description": "All"})
    return cid


async def test_routing_between_two_sources(stack: httpx.AsyncClient, world: World,
                                           tpg_source: dict[str, Any]) -> None:
    admin = world.tokens["admin"]
    _, events = await ask(stack, admin, "How many open projects are there?")
    assert by_event(events)["intent"]["routing"] == "only"
    replica = await second_source(stack, world, tpg_source)

    # Right after approval the new source is a candidate: the question is now ambiguous.
    _, events = await ask(stack, admin, "How many open projects are there?")
    err = by_event(events)["error"]
    assert err["code"] == "choose_source" and {c["connection_id"] for c in err["choices"]} == {
        world.connection_id, replica}
    assert "TPG Postgres" in err["message"] and "TPG Replica" in err["message"]

    # Choosing a source answers from it; a follow-up in the same conversation stays there.
    resp = await stack.post("/api/v1/ask/turns", headers=h(admin),
                            json={"question": "How many open projects are there?", "connection_id": replica})
    first = resp.json()
    stream = await stack.get(first["stream_url"], headers=h(admin))
    assert f'"connection_id": "{replica}"' in stream.text and "event: complete" in stream.text
    _, events = await ask(stack, admin, "Open projects by region", conversation_id=first["conversation_id"])
    ev = by_event(events)
    assert ev["intent"]["connection_id"] == replica and ev["intent"]["routing"] == "conversation"
    assert "complete" in ev

    # Naming a source routes to it.
    _, events = await ask(stack, admin, "How many open projects in the replica?")
    ev = by_event(events)
    assert ev["intent"]["connection_id"] == replica and ev["intent"]["routing"] == "named"
