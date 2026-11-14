"""A pack draft from an approved model (ENH-025, US-236; FEAT-075), on the TPG fixture.

The PMO model approved on TPG Postgres, with its knowledge partly reviewed, a report built from the projects overview
and a dashboard holding it, becomes a tenant pack draft: entities, fields with the source names as candidates,
accepted links, certified measures, dimensions, the approved knowledge (cut to the pack) and the report and dashboard
as blueprints. Released and installed on a second connection to the same data, it gives the same measures and the
same report results. Only ``packs.author`` may do it, and another tenant's connection is not found.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import httpx
import psycopg
import pytest
from api_testkit import needs_db
from conftest import FakeGateway
from pmo_testkit import AS_OF, REVIEWED, World, create_connection, h, setup_world
from test_exports_api import worker
from test_knowledge_api import bag

from tela_api.core.db import tenant_tx
from tela_api.packs.manifest import expand_reports, source_manifest, spec_of
from tela_api.packs.source import db_source, document_sha
from tela_common.ids import new_id

pytestmark = [needs_db, pytest.mark.integration]
KEY = "acme_from_model"


@pytest.fixture
async def world(stack: httpx.AsyncClient, owner_conn: psycopg.Connection, make_token: Callable[..., str],
                tpg_source: dict[str, Any]) -> World:
    w = await setup_world(stack, owner_conn, make_token, tpg_source)
    stack.app.state.gateway = FakeGateway(embedder=bag).client()  # type: ignore[attr-defined]
    return w


async def _no_failures(**_: Any) -> list[dict[str, Any]]:
    return []


async def _approve(api: httpx.AsyncClient, token: str, model_id: str) -> str:
    draft = await api.get(f"/api/v1/models/{model_id}/draft", headers=h(token))
    service = api.app.state.query_service  # type: ignore[attr-defined]
    service.dry_run = _no_failures  # as setup_world: the approval tests cover the dry run
    try:
        resp = await api.post(f"/api/v1/models/{model_id}/approve", headers=h(token),
                              json={"draft_etag": draft.json()["draft_etag"]})
    finally:
        del service.dry_run
    assert resp.status_code == 201, resp.text
    return str(resp.json()["model_version_id"])


async def _rows(api: httpx.AsyncClient, token: str, spec: dict[str, Any]) -> Any:
    resp = await api.post("/api/v1/query/run", headers=h(token), json={"spec": spec})
    assert resp.status_code == 200, resp.text
    return resp.json()["rows"]


def _widget_key(spec: dict[str, Any]) -> str:
    """A report widget and a blueprint widget that ask the same question share this key."""
    return json.dumps({"m": spec["measure"]["ref"], "x": [r["ref"] for r in spec.get("extra_measures", [])],
                       "b": [b["ref"] for b in spec.get("breakdowns", [])],
                       "f": sorted(json.dumps(f, sort_keys=True) for f in spec.get("filters", [])),
                       "t": spec.get("time"), "d": bool((spec.get("visual") or {}).get("detail"))}, sort_keys=True)


async def _knowledge(api: httpx.AsyncClient, world: World, owner: psycopg.Connection, keys: dict[str, Any]) -> None:
    """Index the model's knowledge; approve the glossary, rules and recipes (chart patterns stay in review), one value
    synonym and two gold examples (one on a measure no pack has)."""
    resp = await api.post(f"/api/v1/internal/knowledge/model-versions/{world.version_id}/index",
                          headers=worker(keys), json={"tenant_id": world.tenant_id})
    assert resp.status_code == 200, resp.text
    owner.execute("UPDATE rag.knowledge_documents SET review_status = 'approved' WHERE model_version_id = %s AND "
                  "source_kind IN ('glossary', 'dashboard_rule', 'analysis_recipe')", (world.version_id,))
    owner.execute("UPDATE rag.knowledge_documents SET review_status = 'pending_review' WHERE model_version_id = %s "
                  "AND source_kind = 'chart_pattern'", (world.version_id,))
    owner.execute("INSERT INTO rag.value_synonyms (id, tenant_id, connection_id, dimension, stored_value, synonym, "
                  "normalised, source, status) VALUES (%s, %s, %s, 'region', 'KSA', 'the Kingdom', 'the kingdom', "
                  "'admin', 'approved')", (new_id(), world.tenant_id, world.connection_id))
    for question, spec in (("How many open projects are there?", world.spec("open_projects")),
                           ("Made-up measure", world.spec("no_such_measure"))):
        owner.execute("INSERT INTO rag.gold_examples (id, tenant_id, connection_id, model_version_id, question, "
                      "expected_spec, tags, status) VALUES (%s, %s, %s, %s, %s, %s, '{baseline}', 'approved')",
                      (new_id(), world.tenant_id, world.connection_id, world.version_id, question, json.dumps(spec)))


async def test_model_to_pack_to_a_second_connection(stack: httpx.AsyncClient, world: World,
                                                    owner_conn: psycopg.Connection, signing_keys: dict[str, Any],
                                                    tpg_source: dict[str, Any]) -> None:
    admin = world.tokens["admin"]
    await _knowledge(stack, world, owner_conn, signing_keys)
    # A report built by the planner from the projects overview, and a dashboard holding it.
    plan = (await stack.post("/api/v1/reports/plan", headers=h(admin),
                             json={"request": "Projects overview", "connection_id": world.connection_id})).json()
    built = await stack.post(f"/api/v1/reports/plans/{plan['plan_id']}/build",
                             headers=h(admin, **{"Idempotency-Key": "fm1"}))
    assert built.status_code == 201, built.text
    report_id = built.json()["report_id"]
    dash = await stack.post("/api/v1/dashboards", headers=h(admin), json={
        "title": "Ops review", "report_ids": [report_id], "shared_filters": [{"ref": "dimension:region"}]})
    assert dash.status_code == 201, dash.text

    # The Create dialog's choices.
    models = (await stack.get("/api/v1/packs/from-model/models", headers=h(admin))).json()["items"]
    assert [m["connection_id"] for m in models] == [world.connection_id]
    got = await stack.get(f"/api/v1/packs/from-model/models/{world.connection_id}", headers=h(admin))
    assert got.status_code == 200, got.text
    offer = got.json()
    definition = owner_conn.execute("SELECT definition FROM model.model_versions WHERE id = %s",
                                    (world.version_id,)).fetchone()[0]
    assert offer["installed_pack"] == "pmo_tpg" and offer["reporting_date"] == AS_OF
    assert len(offer["measures"]) == len(definition["measures"])
    assert {"id": report_id, "title": plan["title"], "origin": "user"} in offer["reports"]
    assert {"id": dash.json()["id"], "title": "Ops review", "tabs": 1} in offer["dashboards"]
    knowledge = {k["kind"]: k for k in offer["knowledge"]}
    assert knowledge["glossary"]["available"] and knowledge["value_synonyms"]["count"] == 1
    assert not knowledge["chart_patterns"]["available"] and "not approved" in knowledge["chart_patterns"]["reason"]

    # The draft.
    resp = await stack.post("/api/v1/packs", headers=h(admin), json={
        "method": "model", "pack_key": KEY, "title": "Acme PMO", "options": {
            "connection_id": world.connection_id, "report_ids": [report_id], "dashboard_ids": [dash.json()["id"]]}})
    assert resp.status_code == 201, resp.text
    out = resp.json()
    pid = out["id"]
    assert out["checks"]["valid"], out["checks"]
    assert out["source"] == {"connection_id": world.connection_id, "model_version_id": world.version_id,
                             "version_no": 1, "reporting_date": AS_OF}
    assert out["counts"] == {"entities": len(definition["entities"]), "links": len(definition["links"]),
                             "measures": len(definition["measures"]), "dimensions": len(definition["dimensions"])}
    left = {(x["kind"], x["key"]) for x in out["left_out"]}
    assert ("knowledge", "chart_patterns") in left and ("gold_example", "Made-up measure") in left
    events = [r[0] for r in owner_conn.execute("SELECT details FROM ops.audit_events WHERE object_id = %s AND "
                                               "action = 'pack.created'", (pid,))]
    assert events and events[0]["method"] == "model" and events[0]["source"]["model_version_id"] == world.version_id

    head, sys_head = (owner_conn.execute("SELECT manifest FROM model.starter_packs WHERE id = %s", (p,)).fetchone()[0]
                      for p in (pid, owner_conn.execute("SELECT id FROM model.starter_packs WHERE pack_key = "
                                                        "'pmo_tpg'").fetchone()[0]))
    projects = next(e for e in head["entities"] if e["key"] == "projects")
    model_projects = next(e for e in definition["entities"] if e["key"] == "projects")
    assert projects["object_candidates"][0] == model_projects["source_object"].rpartition(".")[2]
    status = next(f for f in projects["fields"] if f["key"] == "project_id")
    assert status["columns"][0] == next(f["source_column"] for f in model_projects["fields"]
                                        if f["key"] == "project_id") and status["required"]
    sys_projects = next(e for e in sys_head["entities"] if e["key"] == "projects")
    assert set(next(f for f in sys_projects["fields"] if f["key"] == "project_id")["columns"]) <= set(status["columns"])
    sens = {f["key"]: f.get("sensitivity") for e in definition["entities"] for f in e["fields"]}
    assert all(f["sensitivity"] == sens[f["key"]] for e in head["entities"] for f in e["fields"])
    assert head["signature"]["primary_entity"] == "projects" and head["reports_from"] == "report_blueprints.yaml"
    assert set(head["dashboards"]) == {"ops_review", "projects"}
    docs = dict(owner_conn.execute("SELECT kind, body FROM model.pack_documents WHERE pack_id = %s", (pid,)).fetchall())
    assert set(docs) == {"glossary", "dashboard_rules", "analysis_recipes", "value_synonyms", "gold_examples",
                         "report_blueprints"}
    assert docs["value_synonyms"]["values"] == [{"dimensions": ["region"], "value": "KSA", "synonyms": ["the Kingdom"]}]
    assert [e["question"] for e in docs["gold_examples"]["examples"]] == ["How many open projects are there?"]
    assert "model_version_id" not in docs["gold_examples"]["examples"][0]["spec"]
    assert len(docs["report_blueprints"]["blueprints"]) == 2  # the report, and the dashboard's tab

    # Released as US-238 will release it (the manifest stored with its starter reports expanded, as load-packs
    # stores a pack) and installed on a second connection to the same data.
    stored = expand_reports(head, lambda kind: docs.get(kind))
    owner_conn.execute("UPDATE model.starter_packs SET status = 'released', released_at = now(), " + REVIEWED +
                       "manifest = %s, "
                       "manifest_sha256 = %s WHERE id = %s", (json.dumps(stored), document_sha(stored), pid))
    cid2 = await create_connection(stack, admin, tpg_source, name="TPG Replica")
    assert (await stack.post(f"/api/v1/connections/{cid2}/test", headers=h(admin))).status_code == 200
    assert (await stack.post(f"/api/v1/connections/{cid2}/introspect", headers=h(admin))).status_code == 202
    install = await stack.post(f"/api/v1/starter-packs/{pid}/install", headers=h(admin),
                               json={"connection_id": cid2, "reporting_date": AS_OF})
    assert install.status_code == 202, install.text
    v2 = await _approve(stack, admin, install.json()["model_id"])
    for entity in ("projects", "project_requests"):
        policy = await stack.post("/api/v1/admin/scope-policies", headers=h(admin), json={
            "connection_id": cid2, "entity": entity, "role": "admin", "all_rows": True, "description": "All rows"})
        assert policy.status_code == 201, policy.text
    definition2 = owner_conn.execute("SELECT definition FROM model.model_versions WHERE id = %s", (v2,)).fetchone()[0]

    # The same measures and dimensions …
    def rules(defn: dict[str, Any]) -> dict[str, Any]:
        return {m["key"]: (m["entity"], m["label"], m["expression"], m["plain_rule"]) for m in defn["measures"]}

    assert rules(definition2) == rules(definition)
    assert {d["key"] for d in definition2["dimensions"]} == {d["key"] for d in definition["dimensions"]}
    for key in ("open_projects", "in_delivery", "overdue_go_lives", "forecast_cost_total"):
        a = await _rows(stack, admin, world.spec(key))
        b = await _rows(stack, admin, {**world.spec(key), "model_version_id": v2, "connection_id": cid2})
        assert a == b and a, key
    items = {(k, a) for k, a in owner_conn.execute(
        "SELECT item_key, availability FROM model.starter_pack_items WHERE connection_id = %s AND item_kind = "
        "'report'", (cid2,))}
    async with tenant_tx(world.tenant_id) as conn:
        src = await db_source(conn, pid)
    assert src is not None
    manifest, _, _ = source_manifest(src)
    assert items == {(k, "available") for k in manifest.reports}

    # … and the same report results: every widget of the original report that became a blueprint widget.
    original: dict[str, Any] = {}
    for (spec,) in owner_conn.execute(
            "SELECT w.spec FROM content.report_items i JOIN content.widgets w ON w.id = i.widget_id WHERE "
            "i.report_id = %s", (report_id,)):
        original[_widget_key(spec)] = await _rows(stack, admin, spec)
    bid = next(b["id"] for b in docs["report_blueprints"]["blueprints"] if b["dashboard"] == "projects")
    compared = 0
    for w in manifest.reports[bid]["widgets"]:
        spec = spec_of(w, model_version_id=v2, connection_id=cid2)
        key = _widget_key(spec)
        if key in original:
            assert await _rows(stack, admin, spec) == original[key], w["title"]
            compared += 1
    assert compared == len(manifest.reports[bid]["widgets"]) >= 5, (compared, len(manifest.reports[bid]["widgets"]))


async def test_only_pack_authors_and_own_connections(stack: httpx.AsyncClient, world: World) -> None:
    creator = world.tokens["creator"]
    assert (await stack.get("/api/v1/packs/from-model/models", headers=h(creator))).status_code == 403
    assert (await stack.post("/api/v1/packs", headers=h(creator), json={
        "method": "model", "pack_key": KEY, "title": "x", "options": {"connection_id": world.connection_id}})
            ).status_code == 403
    admin = world.tokens["admin"]
    missing = await stack.get(f"/api/v1/packs/from-model/models/{new_id()}", headers=h(admin))
    assert missing.status_code == 404
    no_conn = await stack.post("/api/v1/packs", headers=h(admin), json={"method": "model", "pack_key": KEY,
                                                                          "title": "x"})
    assert no_conn.status_code == 400 and "connection" in no_conn.json()["error"]["message"]
    bad = await stack.post("/api/v1/packs", headers=h(admin), json={
        "method": "model", "pack_key": KEY, "title": "x",
        "options": {"connection_id": world.connection_id, "measures": ["no_such_measure"]}})
    assert bad.status_code == 400 and "no_such_measure" in bad.json()["error"]["message"]
    # Without reports the pack has no blueprints and no dashboards; without knowledge, no documents.
    plain = await stack.post("/api/v1/packs", headers=h(admin), json={
        "method": "model", "pack_key": KEY, "title": "Plain", "options": {
            "connection_id": world.connection_id, "knowledge": []}})
    assert plain.status_code == 201, plain.text
    detail = (await stack.get(f"/api/v1/packs/{plain.json()['id']}", headers=h(admin))).json()
    assert detail["documents"] == {} and detail["manifest"]["dashboards"] == {}
    assert "reports_from" not in detail["manifest"]
