"""Personal fields in words over the API (US-075 draft, US-076 edit a band in words with a diff)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx
import psycopg
import pytest
from api_testkit import needs_db
from pmo_testkit import World, h, setup_world

pytestmark = [needs_db, pytest.mark.integration]


@pytest.fixture
async def world(stack: httpx.AsyncClient, owner_conn: psycopg.Connection, make_token: Callable[..., str],
                tpg_source: dict[str, Any]) -> World:
    return await setup_world(stack, owner_conn, make_token, tpg_source)


async def test_draft_then_save_then_edit_in_words(stack: httpx.AsyncClient, world: World) -> None:
    tok = h(world.tokens["creator"])
    draft = await stack.post("/api/v1/fields/draft", headers=tok, json={
        "text": "band forecast cost into <1M, 1-5M, >5M", "connection_id": world.connection_id})
    assert draft.status_code == 200, draft.text
    body = draft.json()["draft"]
    assert body["edges"] == [0, 1_000_000, 5_000_000]
    saved = await stack.post("/api/v1/fields", headers=tok, json=body)  # the draft is a valid request as is
    assert saved.status_code == 201, saved.text
    fid = saved.json()["id"]

    edit = await stack.post(f"/api/v1/fields/{fid}/edit-in-words", headers=tok,
                            json={"text": "make the top band 2M and above"})
    assert edit.status_code == 200, edit.text
    out = edit.json()
    assert [r["status"] for r in out["diff"]["after"]] == ["same", "new", "new"]
    assert "2M+" in out["rule_after"] and out["rule_before"] == saved.json()["plain_rule"]
    # Nothing changed until the proposal is applied.
    assert (await stack.get(f"/api/v1/fields/{fid}", headers=tok)).json()["plain_rule"] == out["rule_before"]
    applied = await stack.patch(f"/api/v1/fields/{fid}", headers=tok, json=out["proposal"])
    assert applied.status_code == 200, applied.text
    assert [b["label"] for b in applied.json()["definition"]["bands"]] == ["< 1M", "1M - 2M", "2M+"]

    clash = await stack.post(f"/api/v1/fields/{fid}/edit-in-words", headers=tok, json={"text": "move 1M to 3M"})
    assert clash.status_code == 400 and "2M must be above 3M" in clash.json()["error"]["message"]


async def test_words_are_checked(stack: httpx.AsyncClient, world: World) -> None:
    tok = h(world.tokens["creator"])
    grouping = await stack.post("/api/v1/fields/draft", headers=tok, json={
        "text": "group platforms into Core and Growth", "connection_id": world.connection_id})
    assert grouping.json()["draft"]["kind"] == "grouping"
    saved = await stack.post("/api/v1/fields", headers=tok, json={
        **grouping.json()["draft"], "groups": [{"label": "Core", "values": ["Network One"]}]})
    assert saved.status_code == 201, saved.text
    refused = await stack.post(f"/api/v1/fields/{saved.json()['id']}/edit-in-words", headers=tok,
                               json={"text": "add an edge at 1M"})
    assert refused.status_code == 400 and "works for bands" in refused.json()["error"]["message"]
    missing = await stack.post("/api/v1/fields/draft", headers=tok, json={"text": "group platforms into A"})
    assert missing.status_code == 400
    viewer = await stack.post("/api/v1/fields/draft", headers=h(world.tokens["viewer"]), json={
        "text": "group platforms into Core", "connection_id": world.connection_id})
    assert viewer.status_code == 403
