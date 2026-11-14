"""Pack Studio API (ENH-025, US-232; FEAT-075; DEC-26).

CRUD on tenant drafts; system packs read-only with clone to edit; validation errors with paths; ETag conflicts;
``packs.author`` only; tenant isolation; export → import round trip; a measure tried on a connection.
"""

from __future__ import annotations

import io
import json
import os
import zipfile
from collections.abc import Callable
from typing import Any

import httpx
import psycopg
import pytest
import yaml
from api_testkit import ISSUER, OTHER_ISSUER, bootstrap, needs_db
from pmo_testkit import GROUPS, h, load_packs, setup_world

from tela_api.packs.source import document_sha
from tela_common.ids import new_id

pytestmark = [needs_db, pytest.mark.integration]

URL = "/api/v1/packs"
ENTITY = {"key": "tickets", "label": "Tickets", "object_candidates": ["tickets"], "primary_key": ["ticket_id"],
          "fields": [{"key": "ticket_id", "label": "Ticket id", "data_type": "text", "columns": ["ticket_id"]},
                     {"key": "state", "label": "State", "data_type": "text", "columns": ["state", "status"]}]}
OPEN = {"key": "open_tickets", "label": "Open tickets", "entity": "tickets", "plain_rule": "Tickets in state Open.",
        "expression": {"agg": "count", "where": {"field": "state", "op": "=", "value": "Open"}}}


@pytest.fixture
async def world(api: httpx.AsyncClient, owner_conn: psycopg.Connection,
                make_token: Callable[..., str]) -> dict[str, Any]:
    load_packs(os.environ["TELA_TEST_OWNER_DATABASE_URL"])
    tenant = bootstrap(owner_conn, "acme", ISSUER, GROUPS)
    other = bootstrap(owner_conn, "globex", OTHER_ISSUER, {"TELA-Admins": "admin"})
    w: dict[str, Any] = {"tenant": tenant, "other": other,
                         "admin": make_token("admin-1", groups=["TELA-Admins"], name="Ada Admin"),
                         "creator": make_token("creator-1", groups=["TELA-Creators"], name="Cleo Creator"),
                         "viewer": make_token("viewer-1", groups=["TELA-Viewers"], name="Vic Viewer")}
    w["pmo"] = owner_conn.execute("SELECT id FROM model.starter_packs WHERE pack_key = 'pmo_tpg'").fetchone()[0]
    return w


async def _create(api: httpx.AsyncClient, token: str, key: str, **kw: Any) -> httpx.Response:
    return await api.post(URL, headers=h(token), json={"pack_key": key, "title": key.replace("_", " ").title(), **kw})


async def _patch(api: httpx.AsyncClient, token: str, pack: str, etag: str | None, **sections: Any) -> httpx.Response:
    headers = h(token, **({"If-Match": etag} if etag else {}))
    return await api.patch(f"{URL}/{pack}", headers=headers, json={"sections": sections})


async def test_only_pack_authors_reach_the_studio(api: httpx.AsyncClient, world: dict[str, Any]) -> None:
    for who in ("creator", "viewer"):
        resp = await api.get(URL, headers=h(world[who]))
        assert resp.status_code == 403 and resp.json()["error"]["details"]["missing"] == ["packs.author"]
        assert (await _create(api, world[who], "acme_ops")).status_code == 403
    me = (await api.get("/api/v1/me", headers=h(world["admin"]))).json()
    assert "packs.author" in me["permissions"]


async def test_blank_draft_edit_with_etag(api: httpx.AsyncClient, world: dict[str, Any],
                                          owner_conn: psycopg.Connection) -> None:
    admin = world["admin"]
    made = await _create(api, admin, "acme_tickets")
    assert made.status_code == 201, made.text
    pack, etag = made.json()["id"], made.headers["ETag"]
    assert made.json()["status"] == "draft" and made.json()["editable"] and made.json()["owner"] == "tenant"
    assert made.json()["checks"]["valid"]  # a blank pack only warns: no primary entity yet

    # The same key again, or a system pack's key, is refused.
    assert (await _create(api, admin, "acme_tickets")).status_code == 409
    taken = await _create(api, admin, "pmo_tpg")
    assert taken.status_code == 409 and taken.json()["error"]["details"]["field"] == "pack_key"

    # Without If-Match, or on a stale revision: 412; nothing saved.
    assert (await _patch(api, admin, pack, None, entities=[ENTITY])).status_code == 412
    saved = await _patch(api, admin, pack, etag, entities=[ENTITY], signature={"primary_entity": "tickets",
                                                                               "min_confidence": 0.5})
    assert saved.status_code == 200, saved.text
    assert saved.headers["ETag"] != etag and saved.json()["counts"]["entities"] == 1
    assert saved.json()["checks"]["warnings"] == []
    stale = await _patch(api, admin, pack, etag, measures=[OPEN])
    assert stale.status_code == 412 and stale.json()["error"]["details"]["current_revision"]
    etag = saved.headers["ETag"]

    # Schema errors block the save and name their paths; reference warnings are saved.
    bad = {**OPEN, "expression": {"agg": "tally"}}
    refused = await _patch(api, admin, pack, etag, measures=[OPEN, bad, {**OPEN, "key": "open_tickets"}])
    assert refused.status_code == 400 and refused.json()["error"]["code"] == "validation_error"
    paths = {e["path"] for e in refused.json()["error"]["details"]["errors"]}
    assert "measures[1].expression.agg" in paths and "measures[2].key" in paths
    loose = {**OPEN, "key": "late", "expression": {"agg": "count", "where": {"field": "due", "op": "<", "value": 1}}}
    warned = await _patch(api, admin, pack, etag, measures=[OPEN, loose])
    assert warned.status_code == 200, warned.text
    assert warned.json()["checks"]["warnings"] == [
        {"path": "measures[1].expression", "message": "The rule reads tickets.due, which the pack doesn't have."}]
    assert (await _patch(api, admin, pack, warned.headers["ETag"], pack_key="other")).status_code == 400

    # Validate an unsaved change (a side-panel form) without saving it.
    check = await api.post(f"{URL}/{pack}/validate", headers=h(admin), json={"sections": {"measures": [bad]}})
    assert check.status_code == 200 and not check.json()["valid"]
    got = await api.get(f"{URL}/{pack}", headers=h(admin))
    assert got.json()["counts"]["measures"] == 2 and got.headers["ETag"] == warned.headers["ETag"]
    assert list(got.json()["manifest"])[:4] == ["pack_key", "pack_version", "title", "released_at"]

    actions = [r[0] for r in owner_conn.execute("SELECT action FROM ops.audit_events WHERE object_type = 'pack' "
                                                "ORDER BY occurred_at")]
    assert actions == ["pack.created", "pack.draft_saved", "pack.draft_saved"]

    # A draft can be deleted (on its current revision).
    assert (await api.delete(f"{URL}/{pack}", headers=h(admin, **{"If-Match": etag}))).status_code == 412
    gone = await api.delete(f"{URL}/{pack}", headers=h(admin, **{"If-Match": warned.headers["ETag"]}))
    assert gone.status_code == 204
    assert (await api.get(f"{URL}/{pack}", headers=h(admin))).status_code == 404


async def test_system_pack_is_read_only_and_clones(api: httpx.AsyncClient, world: dict[str, Any],
                                                   owner_conn: psycopg.Connection) -> None:
    admin, pmo = world["admin"], str(world["pmo"])
    listed = (await api.get(URL, headers=h(admin))).json()
    system = next(p for p in listed["items"] if p["pack_key"] == "pmo_tpg")
    assert system["owner"] == "system" and not system["latest"]["editable"]
    assert listed["create"] == {"methods": ["ai", "blank", "clone", "model"], "planned": {}}
    got = await api.get(f"{URL}/{pmo}", headers=h(admin))
    assert got.status_code == 200 and got.json()["checks"]["errors"] == []
    assert set(got.json()["documents"]) == {"glossary", "value_synonyms", "dashboard_rules", "chart_patterns",
                                            "analysis_recipes", "report_blueprints", "gold_examples"}
    refused = await _patch(api, admin, pmo, got.headers["ETag"], title="Mine now")
    assert refused.status_code == 409 and "Clone" in refused.json()["error"]["message"]
    assert (await api.delete(f"{URL}/{pmo}", headers=h(admin, **{"If-Match": got.headers["ETag"]}))).status_code \
        == 409

    # Clone to edit: the manifest and every knowledge document, under a new key, as a draft.
    clone = await _create(api, admin, "acme_pmo", method="clone", from_pack_id=pmo)
    assert clone.status_code == 201, clone.text
    assert clone.json()["based_on_pack_id"] == pmo and clone.json()["checks"]["errors"] == []
    cid = clone.json()["id"]
    copied = (await api.get(f"{URL}/{cid}", headers=h(admin))).json()
    assert copied["documents"] == got.json()["documents"]
    assert copied["counts"] == got.json()["counts"] and copied["manifest"]["pack_key"] == "acme_pmo"
    rules = (await api.get(f"{URL}/{cid}/documents/dashboard_rules", headers=h(admin))).json()["body"]
    assert rules == (await api.get(f"{URL}/{pmo}/documents/dashboard_rules", headers=h(admin))).json()["body"]
    renamed = await _patch(api, admin, cid, clone.headers["ETag"], title="Acme PMO")
    assert renamed.status_code == 200 and renamed.json()["title"] == "Acme PMO"
    # Every Create menu entry is a method now; AI draft needs a connection (US-237, test_pack_author_api.py).
    later = await _create(api, admin, "acme_ai", method="ai")
    assert later.status_code == 400 and "connection" in later.json()["error"]["message"]

    versions = (await api.get(f"{URL}/{pmo}/versions", headers=h(admin))).json()
    assert [v["version"] for v in versions["items"]] == ["2.2.1"]


def _their_pack(owner: psycopg.Connection, tenant: str, key: str) -> tuple[str, str]:
    """Another tenant's draft, written as the owner role (the test API trusts one issuer only)."""
    head = {"pack_key": key, "pack_version": "0.1.0", "title": key, "released_at": "2026-11-06",
            "signature": {}, "entities": [], "measures": []}
    pid, sha = new_id(), document_sha(head)
    owner.execute("INSERT INTO model.starter_packs (id, tenant_id, pack_key, pack_version, title, detection_signature, "
                  "manifest, manifest_sha256, status) VALUES (%s, %s, %s, '0.1.0', %s, '{}', %s, %s, 'draft')",
                  (pid, tenant, key, key, json.dumps(head), sha))
    return pid, sha


async def test_tenant_isolation(api: httpx.AsyncClient, world: dict[str, Any],
                                owner_conn: psycopg.Connection) -> None:
    mine = await _create(api, world["admin"], "acme_ops")
    assert mine.status_code == 201
    other, sha = _their_pack(owner_conn, world["other"], "globex_ops")
    keys = {p["pack_key"] for p in (await api.get(URL, headers=h(world["admin"]))).json()["items"]}
    assert keys == {"pmo_tpg", "acme_ops"}
    assert (await api.get(f"{URL}/{other}", headers=h(world["admin"]))).status_code == 404
    assert (await _patch(api, world["admin"], other, f'"{sha}"', title="x")).status_code == 404
    assert (await api.get(f"{URL}/{other}/export", headers=h(world["admin"]))).status_code == 404
    assert (await api.get(f"{URL}/not-a-uuid", headers=h(world["admin"]))).status_code == 404
    # Another tenant's pack doesn't take the key from this one.
    _their_pack(owner_conn, world["other"], "acme_ops_two")
    assert (await _create(api, world["admin"], "acme_ops_two")).status_code == 201


async def test_export_import_round_trip(api: httpx.AsyncClient, world: dict[str, Any]) -> None:
    admin = world["admin"]
    clone = await _create(api, admin, "acme_pmo", method="clone", from_pack_id=str(world["pmo"]))
    pack = clone.json()["id"]
    exported = await api.get(f"{URL}/{pack}/export", headers=h(admin))
    assert exported.status_code == 200 and exported.headers["content-type"] == "application/zip"
    names = sorted(zipfile.ZipFile(io.BytesIO(exported.content)).namelist())
    assert names[0] == "acme_pmo/analysis_recipes.yaml" and "acme_pmo/manifest.yaml" in names and len(names) == 8
    manifest = yaml.safe_load(zipfile.ZipFile(io.BytesIO(exported.content)).read("acme_pmo/manifest.yaml"))
    assert list(manifest)[:3] == ["pack_key", "pack_version", "title"] and manifest["reports_from"]

    # The same zip again is refused (same key and version); under another key it is a new draft with the same content.
    files = {"file": ("acme_pmo.zip", exported.content, "application/zip")}
    assert (await api.post(f"{URL}/import", headers=h(admin), files=files)).status_code == 409
    imported = await api.post(f"{URL}/import", headers=h(admin), files=files, data={"pack_key": "acme_pmo_copy"})
    assert imported.status_code == 201, imported.text
    a = (await api.get(f"{URL}/{pack}", headers=h(admin))).json()
    b = (await api.get(f"{URL}/{imported.json()['id']}", headers=h(admin))).json()
    assert b["documents"] == a["documents"] and b["counts"] == a["counts"]
    assert {**b["manifest"], "pack_key": "acme_pmo"} == a["manifest"]
    for kind in a["documents"]:
        one = (await api.get(f"{URL}/{pack}/documents/{kind}", headers=h(admin))).json()["body"]
        two = (await api.get(f"{URL}/{imported.json()['id']}/documents/{kind}", headers=h(admin))).json()["body"]
        assert one == two, kind

    # A broken file names its path; a non-zip is refused.
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("x/manifest.yaml", yaml.safe_dump({**manifest, "pack_key": "acme_broken",
                                                      "measures": [{"key": "m", "label": "M"}]}))
        z.writestr("x/notes.txt", "skipped")
    bad = await api.post(f"{URL}/import", headers=h(admin), files={"file": ("x.zip", buf.getvalue())})
    assert bad.status_code == 400
    assert any(e["path"].startswith("measures[0].") for e in bad.json()["error"]["details"]["errors"])
    junk = await api.post(f"{URL}/import", headers=h(admin), files={"file": ("x.zip", b"not a zip")})
    assert junk.status_code == 400 and "zip" in junk.json()["error"]["message"]


async def test_try_a_measure_on_a_connection(stack: httpx.AsyncClient, owner_conn: psycopg.Connection,
                                             make_token: Callable[..., str], tpg_source: dict[str, Any]) -> None:
    w = await setup_world(stack, owner_conn, make_token, tpg_source, approve=False)
    admin = w.tokens["admin"]
    load_packs(os.environ["TELA_TEST_OWNER_DATABASE_URL"])
    pmo = owner_conn.execute("SELECT id FROM model.starter_packs WHERE pack_key = 'pmo_tpg'").fetchone()[0]
    clone = await _create(stack, admin, "acme_pmo", method="clone", from_pack_id=str(pmo))
    pack = clone.json()["id"]
    tried = await stack.post(f"{URL}/{pack}/try", headers=h(admin),
                             json={"connection_id": w.connection_id, "measure": "open_projects"})
    assert tried.status_code == 200, tried.text
    from conftest import SOURCE_DB, _dsn_for

    with psycopg.connect(_dsn_for(SOURCE_DB)) as src:
        active = src.execute("SELECT count(*) FROM projects WHERE status = 'Active'").fetchone()
    assert tried.json()["available"] and int(tried.json()["value"]) == active[0]
    # A measure whose field the source lacks is named, not run.
    detail = (await stack.get(f"{URL}/{pack}", headers=h(admin)))
    measures = detail.json()["manifest"]["measures"]
    ghost = {**OPEN, "key": "ghost", "entity": "projects",
             "expression": {"agg": "count", "where": {"field": "ghost_flag", "op": "=", "value": "Y"}}}
    projects = next(e for e in detail.json()["manifest"]["entities"] if e["key"] == "projects")
    entities = [{**e, "fields": [*e["fields"], {"key": "ghost_flag", "label": "Ghost", "data_type": "text",
                                                "columns": ["ghost_flag"]}]} if e is projects else e
                for e in detail.json()["manifest"]["entities"]]
    saved = await _patch(stack, admin, pack, detail.headers["ETag"], entities=entities, measures=[*measures, ghost])
    assert saved.status_code == 200, saved.text
    missing = await stack.post(f"{URL}/{pack}/try", headers=h(admin),
                               json={"connection_id": w.connection_id, "measure": "ghost"})
    assert missing.status_code == 200 and not missing.json()["available"] and missing.json()["gaps"]
    unknown = await stack.post(f"{URL}/{pack}/try", headers=h(admin),
                               json={"connection_id": w.connection_id, "measure": "nope"})
    assert unknown.status_code == 404
