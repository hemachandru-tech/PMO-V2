"""Value-based mapping suggestions live (US-200; FEAT-067): on a TPG source whose record state is ``record_status``
the preview suggests it for Status by its values, unmapped or mapped by hand to ``current_state`` (EXEC-115); the
fixture source, whose names all match, gets no suggestion."""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any

import httpx
import psycopg
import pytest
from api_testkit import needs_db
from pmo_testkit import create_connection, h, load_packs_open_by_status, setup_world
from test_mapping_guards_api import _mysql_like_schema

pytestmark = [needs_db, pytest.mark.integration]


def _status_row(preview: dict[str, Any]) -> dict[str, Any]:
    proj = next(e for e in preview["mapping_step"]["entities"] if e["key"] == "projects")
    return next(f for f in proj["fields"] if f["key"] == "status")


async def test_record_status_suggested_by_its_values(
    stack: httpx.AsyncClient, owner_conn: psycopg.Connection, make_token: Callable[..., str],
    tpg_source: dict[str, Any],
) -> None:
    _mysql_like_schema()
    w = await setup_world(stack, owner_conn, make_token, tpg_source, approve=False)
    admin = w.tokens["admin"]
    load_packs_open_by_status(os.environ["TELA_TEST_OWNER_DATABASE_URL"])
    pack_id = next(x for x in (await stack.get(f"/api/v1/starter-packs/match?connection_id={w.connection_id}",
                                               headers=h(admin))).json()["matches"] if x["pack_key"] == "pmo_tpg")[
        "pack_id"]
    url = f"/api/v1/starter-packs/{pack_id}/preview"

    # The fixture source: every name matches and its values fit, so nothing is suggested.
    plain = (await stack.post(url, headers=h(admin), json={"connection_id": w.connection_id})).json()
    assert plain["checks"]["suggestions"] == 0
    assert not [f for e in plain["mapping_step"]["entities"] for f in e["fields"] if "suggestion" in f]

    cid = await create_connection(stack, admin, tpg_source, name="TPG MySQL names", schemas=["ms"])
    assert (await stack.post(f"/api/v1/connections/{cid}/test", headers=h(admin))).status_code == 200
    assert (await stack.post(f"/api/v1/connections/{cid}/introspect", headers=h(admin))).status_code == 202

    # No name rule finds record_status: the values do. Suggested, not applied.
    pre = await stack.post(url, headers=h(admin), json={"connection_id": cid})
    assert pre.status_code == 200, pre.text
    row = _status_row(pre.json())
    assert row["column"] is None and row["how"] == "missing"
    s = row["suggestion"]
    assert s["column"] == "record_status" and s["reason"] == "unmapped" and s["confidence"] == 1.0
    assert s["evidence"] == "Values Active, Inactive match the pack's Status."
    assert "projects.status" not in pre.json()["mapping"]["fields"]

    # EXEC-115's hand mapping: Status on current_state is flagged, with record_status as the column to use.
    wrong = await stack.post(url, headers=h(admin),
                             json={"connection_id": cid, "mapping": {"fields": {"projects.status": "current_state"}}})
    row = _status_row(wrong.json())
    assert row["column"] == "current_state"
    assert row["suggestion"]["column"] == "record_status" and row["suggestion"]["reason"] == "values_dont_fit"

    # The Admin confirms it: the column fits and nothing more is suggested for Status.
    ok = await stack.post(url, headers=h(admin),
                          json={"connection_id": cid, "mapping": {"fields": {"projects.status": "record_status"}}})
    row = _status_row(ok.json())
    assert row["column"] == "record_status" and "suggestion" not in row
    assert ok.json()["checks"]["blocking"] == []
