"""The PMO pack read from the database behaves exactly like its folder (ENH-025, US-231; FEAT-075).

On the TPG fixture: install, preview and pack updates bind the stored manifest, which is the folder's; the model
installed from it has the definition and items the folder gives; knowledge indexing (chart patterns, dashboard
rules, report blueprints, analysis recipes, glossary) stores the same chunks from the stored documents as from the
folder, and really reads the stored ones; the wording check reads the stored dashboard rules.
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
from conftest import FakeGateway
from pmo_testkit import World, h, load_packs, setup_world
from test_exports_api import worker
from test_knowledge_api import bag

from tela_api.content import wording
from tela_api.core.db import tenant_tx
from tela_api.packs.manifest import DEFAULT_PACKS_DIR, bind, load_manifest, match, source_manifest
from tela_api.packs.source import FilePackSource, db_source, installed_source, released_sources

pytestmark = [needs_db, pytest.mark.integration]
PMO = DEFAULT_PACKS_DIR / "pmo"
KINDS = ("chart_pattern", "dashboard_rule", "report_blueprint", "analysis_recipe", "glossary")


@pytest.fixture
async def world(stack: httpx.AsyncClient, owner_conn: psycopg.Connection, make_token: Callable[..., str],
                tpg_source: dict[str, Any]) -> World:
    w = await setup_world(stack, owner_conn, make_token, tpg_source)
    stack.app.state.gateway = FakeGateway(embedder=bag).client()  # type: ignore[attr-defined]
    return w


def _json(value: Any) -> Any:
    return json.loads(json.dumps(value, default=str))


def _catalog(owner: psycopg.Connection, connection_id: str) -> list[dict[str, Any]]:
    return [{"qualified_name": q, "columns": c} for q, c in owner.execute(
        "SELECT qualified_name, columns FROM conn.connection_objects WHERE connection_id = %s AND removed_at IS NULL "
        "AND readable IS NOT FALSE", (connection_id,))]


async def _chunks(api: httpx.AsyncClient, world: World, owner: psycopg.Connection,
                  keys: dict[str, Any]) -> dict[str, list[str]]:
    resp = await api.post(f"/api/v1/internal/knowledge/model-versions/{world.version_id}/index",
                          headers=worker(keys), json={"tenant_id": world.tenant_id})
    assert resp.status_code == 200, resp.text
    rows = owner.execute("SELECT d.source_kind, c.content FROM rag.knowledge_chunks c JOIN rag.knowledge_documents d "
                         "ON d.id = c.document_id WHERE d.model_version_id = %s AND d.source_kind = ANY(%s) AND "
                         "d.review_status <> 'retired'", (world.version_id, list(KINDS))).fetchall()
    out: dict[str, list[str]] = {k: [] for k in KINDS}
    for kind, content in rows:
        out[kind].append(content)
    return {k: sorted(v) for k, v in out.items()}


async def test_install_from_the_stored_pack_matches_the_folder(stack: httpx.AsyncClient, world: World,
                                                               owner_conn: psycopg.Connection) -> None:
    pack_id, raw, sha = owner_conn.execute("SELECT id, manifest, manifest_sha256 FROM model.starter_packs WHERE "
                                           "pack_key = 'pmo_tpg'").fetchone()
    folder_manifest, folder_raw, folder_sha = load_manifest(PMO / "manifest.yaml")
    assert raw == _json(folder_raw) and sha == folder_sha
    async with tenant_tx(world.tenant_id) as conn:
        stored = await installed_source(conn, str(world.model_id))
        assert stored is not None and type(stored).__name__ == "DbPackSource"
        assert stored == await db_source(conn, str(pack_id))
    stored_manifest, _, stored_sha = source_manifest(stored)
    assert stored_sha == folder_sha and stored_manifest == folder_manifest
    # The same definition, items and dashboards on the TPG catalogue, whichever copy is bound.
    catalog = _catalog(owner_conn, world.connection_id)
    from_db = bind(stored_manifest, match(stored_manifest, catalog), reporting_date="2026-10-15")
    from_file = bind(folder_manifest, match(folder_manifest, catalog), reporting_date="2026-10-15")
    assert _json(from_db.definition) == _json(from_file.definition)

    def by_key(items: list[dict[str, Any]]) -> list[dict[str, Any]]:  # the stored manifest's reports: jsonb order
        return sorted(items, key=lambda i: (i["kind"], i["key"]))

    assert by_key(from_db.items) == by_key(from_file.items)
    assert stored_manifest.dashboards == folder_manifest.dashboards
    # The model installed through the API is that binding.
    definition = owner_conn.execute("SELECT definition FROM model.model_versions WHERE id = %s",
                                    (world.version_id,)).fetchone()[0]
    assert [e["key"] for e in definition["entities"]] == [e["key"] for e in from_file.definition["entities"]]
    assert {m["key"] for m in definition["measures"]} == {m["key"] for m in from_file.definition["measures"]}
    items = {(k, key, a) for k, key, a in owner_conn.execute(
        "SELECT item_kind, item_key, availability FROM model.starter_pack_items WHERE connection_id = %s "
        "AND item_kind IN ('measure', 'dimension', 'report')", (world.connection_id,))}
    assert items == {(i["kind"], i["key"], i["availability"]) for i in from_file.items}
    # Preview and pack updates list the stored, released versions.
    packs = (await stack.get("/api/v1/starter-packs", headers=h(world.tokens["admin"]))).json()["items"]
    assert [p["pack_key"] for p in packs] == ["pmo_tpg"]
    preview = await stack.get("/api/v1/starter-packs/match", headers=h(world.tokens["admin"]),
                              params={"connection_id": world.connection_id})
    assert preview.status_code == 200, preview.text
    [m] = preview.json()["matches"] if "matches" in preview.json() else preview.json()["items"]
    assert m["field_mapping"] == match(folder_manifest, catalog).out()["field_mapping"]


async def test_knowledge_indexed_from_the_stored_documents_matches_the_folder(
    stack: httpx.AsyncClient, world: World, owner_conn: psycopg.Connection, signing_keys: dict[str, Any],
) -> None:
    from_db = await _chunks(stack, world, owner_conn, signing_keys)
    assert all(from_db[k] for k in KINDS), {k: len(v) for k, v in from_db.items()}
    # The same pack with no stored documents reads its folder: the chunks are the same.
    owner_conn.execute("DELETE FROM model.pack_documents")
    from_folder = await _chunks(stack, world, owner_conn, signing_keys)
    assert from_folder == from_db
    # And the stored documents are what is read: a stored chart pattern's title shows in its chunk.
    load_packs(os.environ["TELA_TEST_OWNER_DATABASE_URL"])
    owner_conn.execute("SET session_replication_role = replica")  # past the freeze trigger, for this test only
    try:
        owner_conn.execute("UPDATE model.pack_documents SET body = jsonb_set(body::jsonb, '{patterns,0,title}', "
                           "'\"Stored copy title\"')::json WHERE kind = 'chart_patterns'")
    finally:
        owner_conn.execute("SET session_replication_role = DEFAULT")
    changed = await _chunks(stack, world, owner_conn, signing_keys)
    assert any(c.startswith("Chart pattern: Stored copy title") for c in changed["chart_pattern"])
    assert {k: v for k, v in changed.items() if k != "chart_pattern"} == \
        {k: v for k, v in from_db.items() if k != "chart_pattern"}


async def test_wording_check_reads_the_stored_rules(stack: httpx.AsyncClient, world: World) -> None:
    folder = wording.combined([FilePackSource(PMO)])
    try:
        async with tenant_tx(None) as conn:
            stored = await wording.load_stored(conn)
            [src] = await released_sources(conn, system_only=True)
        assert stored == folder and wording.terms() is stored and type(src).__name__ == "DbPackSource"
        assert not wording.ok("We spent 4M on projects") and wording.fix("Total spend") == "Total forecast cost"
    finally:
        wording._stored = None
