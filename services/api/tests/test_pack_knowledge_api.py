"""Business knowledge editors of a pack (ENH-025, US-233; FEAT-075), on the TPG fixture.

A tenant draft cloned from PMO: every knowledge kind reads and saves back unchanged (round trip); a save needs the
kind's ``If-Match`` (412 when stale) and a schema error is refused with its path; a rule naming a missing measure is
saved and flagged; a system pack is read-only and only ``packs.author`` may author. A context document attached to
the draft is kept by a clone and, when the pack is installed on a connection, lands in that connection's knowledge
waiting for review (US-102 gate).
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from typing import Any

import httpx
import psycopg
import pytest
from api_testkit import needs_db
from pmo_testkit import AS_OF, REVIEWED, World, h, load_packs, setup_world
from test_exports_api import MemoryStore
from test_pack_blueprints_api import _clone

from tela_api.packs.studio.knowledge import KNOWLEDGE_KINDS, LISTS

pytestmark = [needs_db, pytest.mark.integration]
NOTES = b"# Acme PMO context\n\nA project is *open* until it is closed. Steering reviews open projects monthly.\n"


@pytest.fixture
async def world(stack: httpx.AsyncClient, owner_conn: psycopg.Connection, make_token: Callable[..., str],
                tpg_source: dict[str, Any]) -> World:
    w = await setup_world(stack, owner_conn, make_token, tpg_source, approve=False)
    load_packs(os.environ["TELA_TEST_OWNER_DATABASE_URL"])
    w.extra["store"] = MemoryStore()
    stack.app.state.object_store = w.extra["store"]  # type: ignore[attr-defined]
    return w


def _url(pack_id: str, kind: str = "") -> str:
    return f"/api/v1/packs/{pack_id}/knowledge" + (f"/{kind}" if kind else "")


async def test_each_kind_round_trips_and_checks_its_items(stack: httpx.AsyncClient, world: World,
                                                          owner_conn: psycopg.Connection) -> None:
    admin = world.tokens["admin"]
    pid = _clone(owner_conn, world.tenant_id)
    over = await stack.get(_url(pid), headers=h(admin))
    assert over.status_code == 200, over.text
    kinds = {k["kind"]: k for k in over.json()["kinds"]}
    assert list(kinds) == list(KNOWLEDGE_KINDS) and all(k["stored"] and k["errors"] == 0 for k in kinds.values())
    assert kinds["gold_examples"]["count"] > 500 and over.json()["attachments"] == []
    for kind in KNOWLEDGE_KINDS:
        got = await stack.get(_url(pid, kind), headers=h(admin))
        assert got.status_code == 200, (kind, got.text)
        body = got.json()
        assert got.headers["ETag"] == body["etag"] == kinds[kind]["etag"] and body["checks"]["valid"]
        assert len(body["checks"]["items"]) == len(body["document"][LISTS[kind]])
        saved = await stack.put(_url(pid, kind), headers=h(admin, **{"If-Match": body["etag"]}),
                                json={"document": body["document"]})
        assert saved.status_code == 200, (kind, saved.text)
        assert saved.json()["etag"] == body["etag"]  # unchanged
        stored = owner_conn.execute("SELECT body::text FROM model.pack_documents WHERE pack_id = %s AND kind = %s",
                                    (pid, kind)).fetchone()[0]
        assert json.loads(stored) == body["document"]
        assert list(json.loads(stored)) == list(body["document"])  # the file's key order is kept
    # An edited glossary term reads back as saved; the stale revision is refused.
    got = (await stack.get(_url(pid, "glossary"), headers=h(admin))).json()
    doc = got["document"]
    doc["terms"].append({"term": "BAU", "means": "business as usual", "explain": "Run work, not a project.",
                         "fields": ["projects.name"]})
    saved = await stack.put(_url(pid, "glossary"), headers=h(admin, **{"If-Match": got["etag"]}),
                            json={"document": doc})
    assert saved.status_code == 200, saved.text
    item = saved.json()["checks"]["items"][-1]
    assert item["title"] == "BAU" and item["refs"]["fields"] == ["projects.name"] and item["missing"] == []
    again = await stack.put(_url(pid, "glossary"), headers=h(admin, **{"If-Match": got["etag"]}),
                            json={"document": doc})
    assert again.status_code == 412
    assert (await stack.put(_url(pid, "glossary"), headers=h(admin), json={"document": doc})).status_code == 412
    assert (await stack.get(_url(pid, "glossary"), headers=h(admin))).json()["document"]["terms"][-1]["term"] == "BAU"
    # A schema error is refused with its path; nothing is saved.
    etag_now = saved.json()["etag"]
    bad = {**doc, "terms": [*doc["terms"], {"term": "Bad", "means": ""}]}
    refused = await stack.put(_url(pid, "glossary"), headers=h(admin, **{"If-Match": etag_now}),
                              json={"document": bad})
    assert refused.status_code == 400, refused.text
    assert refused.json()["error"]["details"]["errors"][0]["path"] == f"glossary.terms[{len(doc['terms'])}].means"
    # A kind not stored yet starts empty and is created on the first save.
    owner_conn.execute("DELETE FROM model.pack_documents WHERE pack_id = %s AND kind = 'analysis_recipes'", (pid,))
    fresh = (await stack.get(_url(pid, "analysis_recipes"), headers=h(admin))).json()
    assert not fresh["stored"] and fresh["document"] == {"version": "0.1.0", "status": "draft", "recipes": []}
    made = await stack.put(_url(pid, "analysis_recipes"), headers=h(admin, **{"If-Match": fresh["etag"]}),
                           json={"document": fresh["document"]})
    assert made.status_code == 200 and made.json()["stored"]
    # Report blueprints are the Reports tab's; unknown kinds are 404.
    assert (await stack.get(_url(pid, "report_blueprints"), headers=h(admin))).status_code == 404
    assert (await stack.get(_url(pid, "nonsense"), headers=h(admin))).status_code == 404


async def test_a_rule_naming_a_missing_measure_is_flagged(stack: httpx.AsyncClient, world: World,
                                                          owner_conn: psycopg.Connection) -> None:
    admin = world.tokens["admin"]
    pid = _clone(owner_conn, world.tenant_id)
    got = (await stack.get(_url(pid, "dashboard_rules"), headers=h(admin))).json()
    doc = got["document"]
    doc["rules"].append({"id": "no_burn", "title": "No burn rate", "rule": "Never show a burn rate.",
                         "measures": ["burn_rate"]})
    checked = await stack.post(f"{_url(pid, 'dashboard_rules')}/check", headers=h(admin), json={"document": doc})
    assert checked.status_code == 200 and checked.json()["valid"]
    flagged = checked.json()["items"][-1]
    assert flagged["missing"] == ["measure:burn_rate"]
    assert flagged["warnings"] == [{"path": f"dashboard_rules.rules[{len(doc['rules']) - 1}].measures[0]",
                                    "message": "There is no measure 'burn_rate' in this pack."}]
    saved = await stack.put(_url(pid, "dashboard_rules"), headers=h(admin, **{"If-Match": got["etag"]}),
                            json={"document": doc})
    assert saved.status_code == 200 and saved.json()["checks"]["items"][-1]["missing"] == ["measure:burn_rate"]
    over = {k["kind"]: k for k in (await stack.get(_url(pid), headers=h(admin))).json()["kinds"]}
    assert over["dashboard_rules"]["warnings"] == 1 and over["dashboard_rules"]["errors"] == 0


async def test_system_packs_are_read_only_and_only_authors_edit(stack: httpx.AsyncClient, world: World,
                                                                owner_conn: psycopg.Connection) -> None:
    admin, creator = world.tokens["admin"], world.tokens["creator"]
    sys_id = str(owner_conn.execute("SELECT id FROM model.starter_packs WHERE pack_key = 'pmo_tpg' AND tenant_id IS "
                                    "NULL").fetchone()[0])
    got = await stack.get(_url(sys_id, "glossary"), headers=h(admin))
    assert got.status_code == 200 and got.json()["pack"]["read_only"]
    put = await stack.put(_url(sys_id, "glossary"), headers=h(admin, **{"If-Match": got.json()["etag"]}),
                          json={"document": got.json()["document"]})
    assert put.status_code == 409 and "clone" in put.json()["error"]["message"].lower()
    attach = await stack.post(f"{_url(sys_id)}/attachments", headers=h(admin), files={"file": ("a.md", NOTES)})
    assert attach.status_code == 409
    pid = _clone(owner_conn, world.tenant_id)
    assert (await stack.get(_url(pid), headers=h(creator))).status_code == 403
    owner_conn.execute("UPDATE model.starter_packs SET status = 'released', " + REVIEWED +
                       "released_at = now() WHERE id = %s", (pid,))
    frozen = (await stack.get(_url(pid, "glossary"), headers=h(admin))).json()
    assert frozen["pack"]["read_only"]
    assert (await stack.put(_url(pid, "glossary"), headers=h(admin, **{"If-Match": frozen["etag"]}),
                            json={"document": frozen["document"]})).status_code == 409


async def test_an_attached_document_lands_in_review_on_install(stack: httpx.AsyncClient, world: World,
                                                               owner_conn: psycopg.Connection) -> None:
    admin = world.tokens["admin"]
    pid = _clone(owner_conn, world.tenant_id)
    bad = await stack.post(f"{_url(pid)}/attachments", headers=h(admin), files={"file": ("a.exe", b"MZ")})
    assert bad.status_code == 400
    fake_pdf = await stack.post(f"{_url(pid)}/attachments", headers=h(admin), files={"file": ("a.pdf", b"nope")})
    assert fake_pdf.status_code == 400
    up = await stack.post(f"{_url(pid)}/attachments", headers=h(admin), files={"file": ("acme_context.md", NOTES)},
                          data={"title": "Acme PMO context"})
    assert up.status_code == 201, up.text
    att = up.json()
    assert att["title"] == "Acme PMO context" and att["content_type"] == "text/markdown"
    assert (await stack.post(f"{_url(pid)}/attachments", headers=h(admin),
                             files={"file": ("again.md", NOTES)})).status_code == 409
    listed = (await stack.get(_url(pid), headers=h(admin))).json()["attachments"]
    assert [a["id"] for a in listed] == [att["id"]]
    file = await stack.get(f"{_url(pid)}/attachments/{att['id']}/file", headers=h(admin))
    assert file.status_code == 200 and file.content == NOTES
    # A clone keeps it; taking it off the clone leaves the source's file in place.
    clone = await stack.post("/api/v1/packs", headers=h(admin), json={
        "method": "clone", "from_pack_id": pid, "pack_key": "acme_pmo_two", "title": "Acme PMO two",
        "version": "0.1.0"})
    assert clone.status_code == 201, clone.text
    cid = clone.json()["id"]
    kept = (await stack.get(_url(cid), headers=h(admin))).json()["attachments"]
    assert [a["title"] for a in kept] == ["Acme PMO context"]
    gone = await stack.delete(f"{_url(cid)}/attachments/{kept[0]['id']}", headers=h(admin))
    assert gone.status_code == 204 and (await stack.get(_url(cid), headers=h(admin))).json()["attachments"] == []
    assert (await stack.get(f"{_url(pid)}/attachments/{att['id']}/file", headers=h(admin))).content == NOTES

    # Install (once released): the document is copied into the connection's knowledge, waiting for review.
    owner_conn.execute("UPDATE model.starter_packs SET status = 'released', " + REVIEWED +
                       "released_at = now() WHERE id = %s", (pid,))
    install = await stack.post(f"/api/v1/starter-packs/{pid}/install", headers=h(admin),
                               json={"connection_id": world.connection_id, "reporting_date": AS_OF})
    assert install.status_code == 202, install.text
    copied = install.json()["documents"]
    assert copied["attached"] == 1 and len(copied["copied"]) == 1
    doc_id = copied["copied"][0]["document_id"]
    row = owner_conn.execute("SELECT connection_id, review_status, index_status, source_kind, title, object_key, "
                             "content_hash FROM rag.knowledge_documents WHERE id = %s", (doc_id,)).fetchone()
    assert (str(row[0]), row[1], row[2], row[3], row[4]) == (world.connection_id, "pending_review", "queued",
                                                             "uploaded_doc", "Acme PMO context")
    assert row[6] == att["sha256"] and world.extra["store"].files[row[5]] == NOTES
    assert owner_conn.execute("SELECT count(*) FROM ops.outbox_events WHERE topic = 'knowledge.document_uploaded' AND "
                              "aggregate_id = %s", (doc_id,)).fetchone()[0] == 1
    review = await stack.get("/api/v1/knowledge/documents?status=pending_review", headers=h(admin))
    assert doc_id in [d["id"] for d in review.json()["items"]]
    # Installing again does not copy it twice.
    again = await stack.post(f"/api/v1/starter-packs/{pid}/install", headers=h(admin),
                             json={"connection_id": world.connection_id, "reporting_date": AS_OF})
    assert again.status_code == 202, again.text
    assert again.json()["documents"] == {"attached": 1, "copied": [], "already_there": 1}
    # The released pack's attachments are frozen.
    assert (await stack.delete(f"{_url(pid)}/attachments/{att['id']}", headers=h(admin))).status_code == 409
