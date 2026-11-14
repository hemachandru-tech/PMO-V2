"""New pack from a template, re-pointed to a schema live (ENH-025, US-235; FEAT-075).

PMO cloned onto the TPG MySQL source (EXEC-115: the project record state is ``record_status``): the preview shows
the mapping step with the value suggestion and the unmapped items; the clone takes ``record_status`` as a candidate,
a renamed measure and a removed item, and then reads the source by name alone with every mapped measure's result
unchanged.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any

import httpx
import psycopg
import pytest
from api_testkit import needs_db
from pmo_testkit import REVIEWED, create_connection, h, load_packs_open_by_status, setup_world
from psycopg.types.json import Jsonb
from test_mapping_guards_api import _mysql_like_schema

from tela_api.packs.manifest import source_manifest
from tela_api.packs.source import DbPackSource

pytestmark = [needs_db, pytest.mark.integration]

URL = "/api/v1/packs"


async def test_clone_pmo_onto_tpg_mysql_keeps_every_mapped_measure(
    stack: httpx.AsyncClient, owner_conn: psycopg.Connection, make_token: Callable[..., str],
    tpg_source: dict[str, Any],
) -> None:
    _mysql_like_schema()
    w = await setup_world(stack, owner_conn, make_token, tpg_source, approve=False)
    admin = w.tokens["admin"]
    load_packs_open_by_status(os.environ["TELA_TEST_OWNER_DATABASE_URL"])
    cid = await create_connection(stack, admin, tpg_source, name="TPG MySQL names", schemas=["ms"])
    assert (await stack.post(f"/api/v1/connections/{cid}/test", headers=h(admin))).status_code == 200
    assert (await stack.post(f"/api/v1/connections/{cid}/introspect", headers=h(admin))).status_code == 202
    pmo = str(owner_conn.execute("SELECT id FROM model.starter_packs WHERE pack_key = 'pmo_tpg'").fetchone()[0])

    # Only pack authors preview a clone.
    denied = await stack.post(f"{URL}/{pmo}/repoint", headers=h(w.tokens["creator"]), json={"connection_id": cid})
    assert denied.status_code == 403

    # No name rule finds record_status: the values suggest it; Status and what reads it are listed as unmapped.
    pre = await stack.post(f"{URL}/{pmo}/repoint", headers=h(admin), json={"connection_id": cid})
    assert pre.status_code == 200, pre.text
    body = pre.json()
    proj = next(e for e in body["mapping_step"]["entities"] if e["key"] == "projects")
    status = next(f for f in proj["fields"] if f["key"] == "status")
    assert status["column"] is None and status["suggestion"]["column"] == "record_status"
    by_ref = {i["ref"]: i for i in body["unmapped"]}
    assert by_ref["field:projects.status"]["reason"] == ("No column found on this source. It is required: without it "
                                                         "Projects isn't available.")
    assert "dimension:status" in by_ref["field:projects.status"]["removes_also"]
    assert by_ref["dimension:status"]["kind"] == "dimension" and by_ref["dimension:status"]["reason"].startswith("Needs")
    assert body["candidates"] == [] and body["checks"]["ai_available"] is False

    # The Admin uses the suggestion: Status is no longer unmapped and record_status will be added as a candidate.
    mapping = {"fields": {"projects.status": "record_status"}}
    ok = (await stack.post(f"{URL}/{pmo}/repoint", headers=h(admin),
                           json={"connection_id": cid, "mapping": mapping})).json()
    assert ok["matched"] and ok["checks"]["blocking"] == []
    assert ok["candidates"] == [{"ref": "field:projects.status", "kind": "field", "name": "record_status"}]
    assert not {"field:projects.status", "dimension:status"} & {i["ref"] for i in ok["unmapped"]}

    # A mapping that sends two pack fields to one column is refused at create.
    bad = await stack.post(URL, headers=h(admin), json={
        "method": "clone", "pack_key": "tpg_mysql_bad", "title": "TPG MySQL", "from_pack_id": pmo,
        "options": {"connection_id": cid, "mapping": {"fields": {"projects.status": "current_state"}}}})
    assert bad.status_code == 400, bad.text

    # Create the clone with a renamed measure and a removed unmapped item.
    removed = next(i["ref"] for i in ok["unmapped"] if i["kind"] == "report") if any(
        i["kind"] == "report" for i in ok["unmapped"]) else None
    created = await stack.post(URL, headers=h(admin), json={
        "method": "clone", "pack_key": "tpg_mysql", "title": "TPG MySQL", "from_pack_id": pmo,
        "options": {"connection_id": cid, "mapping": mapping, "remove": [removed] if removed else [],
                    "renames": {"measures": {"open_projects": {"key": "live_projects", "label": "Live projects"}}}}})
    assert created.status_code == 201, created.text
    clone = created.json()["id"]
    assert created.json()["based_on_pack_id"] == pmo and created.json()["checks"]["valid"]
    detail = (await stack.get(f"{URL}/{clone}", headers=h(admin))).json()
    ent = next(e for e in detail["manifest"]["entities"] if e["key"] == "projects")
    assert next(f for f in ent["fields"] if f["key"] == "status")["columns"][-1] == "record_status"
    keys = {m["key"] for m in detail["manifest"]["measures"]}
    assert "live_projects" in keys and "open_projects" not in keys
    audit = owner_conn.execute("SELECT details FROM ops.audit_events WHERE action = 'pack.created' "
                               "AND object_id = %s", (clone,)).fetchone()
    assert audit is not None and audit[0]["method"] == "clone"

    # Release the clone (US-238 builds release; here the owner stores it as load-packs does, reports expanded) and
    # try both packs' measures on the data: the clone binds record_status by name alone and every measure PMO
    # computes with the mapping has the same result.
    docs = dict(owner_conn.execute("SELECT kind, body FROM model.pack_documents WHERE pack_id = %s",
                                   (clone,)).fetchall())
    raw = source_manifest(DbPackSource(clone, "tpg_mysql", "0.1.0", None, "draft", detail["manifest"], docs))[1]
    owner_conn.execute("UPDATE model.starter_packs SET status = 'released', released_at = now(), " + REVIEWED +
                       "manifest = %s "
                       "WHERE id = %s", (Jsonb(raw), clone))
    pmo_check = await stack.post(f"/api/v1/starter-packs/{pmo}/check", headers=h(admin),
                                 json={"connection_id": cid, "mapping": mapping})
    clone_check = await stack.post(f"/api/v1/starter-packs/{clone}/check", headers=h(admin),
                                   json={"connection_id": cid})
    assert pmo_check.status_code == 200 and clone_check.status_code == 200, clone_check.text
    before = {m["key"]: m["value"] for m in pmo_check.json()["measures"]}
    after = {m["key"]: m["value"] for m in clone_check.json()["measures"]}
    renamed = {"open_projects": "live_projects"}
    assert len(before) > 100 and before["open_projects"] not in (None, 0)
    assert {renamed.get(k, k): v for k, v in before.items()} == after
