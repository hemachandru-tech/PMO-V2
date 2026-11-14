"""Connections and data model lifecycle (US-028, US-030, US-031, US-035, US-043).

Acceptance: TEST-006 (create, secrets write-only), TEST-007 (read-only test), TEST-008 (introspection
without samples), TEST-011 (approval freezes an immutable version).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import psycopg
import pytest
import tpg_synthetic
from api_testkit import ISSUER, bootstrap, needs_db
from pmo_testkit import World, create_connection, h, run, setup_world, value

pytestmark = [needs_db, pytest.mark.integration]


@pytest.fixture
async def admin_only(stack: httpx.AsyncClient, owner_conn: psycopg.Connection,
                     make_token: Callable[..., str]) -> dict[str, str]:
    bootstrap(owner_conn, "acme", ISSUER, {"TELA-Admins": "admin", "TELA-Viewers": "viewer"})
    return {"admin": make_token("admin-1", groups=["TELA-Admins"]), "viewer": make_token("v-1", groups=["TELA-Viewers"])}


# --------------------------------------------------------------------------- connections


async def test_create_keeps_secret_write_only(stack: httpx.AsyncClient, admin_only: dict[str, str],
                                              owner_conn: psycopg.Connection, tpg_source: dict[str, Any],
                                              secrets_dir: Path) -> None:
    cid = await create_connection(stack, admin_only["admin"], tpg_source)
    body = (await stack.get(f"/api/v1/connections/{cid}", headers=h(admin_only["admin"]))).json()
    assert body["status"] == "draft" and body["secret_set"] is True
    assert "password" not in json.dumps(body) and tpg_source["ro"][1] not in json.dumps(body)
    row = owner_conn.execute("SELECT descriptor::text, secret_ref FROM conn.connections WHERE id = %s",
                             (cid,)).fetchone()
    assert row and tpg_source["ro"][1] not in row[0] and row[1].endswith(f"/connections/{cid}")
    assert any(secrets_dir.iterdir())  # the credential lives in the secrets store only
    # Viewers cannot create or read connection details; unsupported engines are refused.
    assert (await stack.get(f"/api/v1/connections/{cid}", headers=h(admin_only["viewer"]))).status_code == 403
    other = await stack.post("/api/v1/connections", headers=h(admin_only["admin"]), json={
        "name": "Oracle", "engine": "oracle", "host": "x", "database": "y", "username": "u", "password": "p"})
    assert other.status_code == 422 and other.json()["error"]["code"] == "engine_not_supported"
    # MongoDB credentials never travel in the connection string (REQ-031); Excel/CSV sources are uploads.
    mongo = await stack.post("/api/v1/connections", headers=h(admin_only["admin"]), json={
        "name": "Mongo", "engine": "mongodb", "uri": "mongodb://u:p@db:27017", "database": "y", "username": "u",
        "password": "p"})
    assert mongo.status_code == 400 and "connection string" in mongo.text
    sheet = await stack.post("/api/v1/connections", headers=h(admin_only["admin"]), json={
        "name": "Sheet", "engine": "excel"})
    assert sheet.status_code == 400 and "Upload the file" in sheet.text
    dup = await stack.post("/api/v1/connections", headers=h(admin_only["admin"]), json={
        "name": "TPG Postgres", "engine": "postgres", "host": "x", "database": "y", "username": "u", "password": "p"})
    assert dup.status_code == 409
    audit = owner_conn.execute("SELECT details::text FROM ops.audit_events WHERE action = 'connection.created'"
                               ).fetchone()
    assert audit and tpg_source["ro"][1] not in audit[0]


async def test_read_only_verification(stack: httpx.AsyncClient, admin_only: dict[str, str],
                                      tpg_source: dict[str, Any], owner_conn: psycopg.Connection) -> None:
    admin = admin_only["admin"]
    ro = await create_connection(stack, admin, tpg_source)
    ok = (await stack.post(f"/api/v1/connections/{ro}/test", headers=h(admin))).json()
    assert ok["reachable"] and ok["authenticated"] and ok["read_only"]["verdict"] == "safe"
    assert ok["status"] == "connected" and "session read-only applied" in ok["read_only"]["checks"]

    rw = await create_connection(stack, admin, tpg_source, user="rw", name="Writable")
    bad = (await stack.post(f"/api/v1/connections/{rw}/test", headers=h(admin))).json()
    assert bad["read_only"]["verdict"] == "unsafe" and bad["status"] == "draft"
    assert "account has INSERT on public.projects" in bad["read_only"]["reasons"]
    assert bad["message"] == "This account can write. Provide a read-only account."
    sec = owner_conn.execute("SELECT severity FROM ops.audit_events WHERE action = 'connection.tested' "
                             "AND outcome = 'failure'").fetchone()
    assert sec == ("security",)
    # A draft connection cannot be introspected or queried.
    assert (await stack.post(f"/api/v1/connections/{rw}/introspect", headers=h(admin))).status_code == 409

    resp = await stack.post("/api/v1/connections", headers=h(admin), json={
        "name": "Wrong password", "engine": "postgres", "host": tpg_source["host"], "port": tpg_source["port"],
        "database": tpg_source["database"], "username": "tela_ro", "password": "wrong", "tls": {"mode": "disable"}})
    wrong = (await stack.post(f"/api/v1/connections/{resp.json()['connection_id']}/test", headers=h(admin))).json()
    assert wrong["reachable"] and not wrong["authenticated"]
    assert wrong["message"] == "The username or password was rejected."

    resp = await stack.post("/api/v1/connections", headers=h(admin), json={
        "name": "Nowhere", "engine": "postgres", "host": "127.0.0.1", "port": 1, "database": "x",
        "username": "u", "password": "p", "tls": {"mode": "disable"}})
    gone = (await stack.post(f"/api/v1/connections/{resp.json()['connection_id']}/test", headers=h(admin))).json()
    assert not gone["reachable"] and gone["message"].startswith("Tela could not reach")


async def test_rate_limit_and_edit_rules(stack: httpx.AsyncClient, admin_only: dict[str, str],
                                         tpg_source: dict[str, Any]) -> None:
    admin = admin_only["admin"]
    cid = await create_connection(stack, admin, tpg_source)
    for _ in range(10):
        assert (await stack.post(f"/api/v1/connections/{cid}/test", headers=h(admin))).status_code == 200
    assert (await stack.post(f"/api/v1/connections/{cid}/test", headers=h(admin))).status_code == 429

    assert (await stack.patch(f"/api/v1/connections/{cid}", headers=h(admin), json={"name": "X"})).status_code == 412
    stale = await stack.patch(f"/api/v1/connections/{cid}", headers=h(admin, **{"If-Match": 'W/"1"'}),
                              json={"name": "X"})
    assert stale.status_code == 412
    etag = (await stack.get(f"/api/v1/connections/{cid}", headers=h(admin))).json()["etag"]
    renamed = await stack.patch(f"/api/v1/connections/{cid}", headers=h(admin, **{"If-Match": etag}),
                                json={"name": "Renamed"})
    assert renamed.json()["status"] == "connected" and renamed.json()["retest_required"] is False
    etag = renamed.json()["etag"]
    cred = await stack.patch(f"/api/v1/connections/{cid}", headers=h(admin, **{"If-Match": etag}),
                             json={"password": tpg_source["ro"][1]})
    assert cred.json()["status"] == "draft" and cred.json()["retest_required"] is True


async def test_credential_edit_recreates_lost_secret(stack: httpx.AsyncClient, admin_only: dict[str, str],
                                                     tpg_source: dict[str, Any], secrets_dir: Path) -> None:
    """The dev secrets store can lose its data (restart); retyping the password must restore it, not 503."""
    admin = admin_only["admin"]
    cid = await create_connection(stack, admin, tpg_source, name="Lost secret")
    for f in secrets_dir.iterdir():
        f.unlink()
    etag = (await stack.get(f"/api/v1/connections/{cid}", headers=h(admin))).json()["etag"]
    blank = await stack.patch(f"/api/v1/connections/{cid}", headers=h(admin, **{"If-Match": etag}),
                              json={"name": "Lost secret 2"})
    assert blank.status_code == 200  # no credential change, nothing to restore
    etag = blank.json()["etag"]
    only_user = await stack.patch(f"/api/v1/connections/{cid}", headers=h(admin, **{"If-Match": etag}),
                                  json={"username": tpg_source["ro"][0]})
    assert only_user.status_code == 503 and "Enter the password again" in only_user.json()["error"]["message"]
    fixed = await stack.patch(f"/api/v1/connections/{cid}", headers=h(admin, **{"If-Match": etag}),
                              json={"password": tpg_source["ro"][1]})
    assert fixed.status_code == 200 and fixed.json()["retest_required"] is True
    assert any(secrets_dir.iterdir())


async def test_introspection_reads_structure_only(stack: httpx.AsyncClient, admin_only: dict[str, str],
                                                  tpg_source: dict[str, Any], owner_conn: psycopg.Connection) -> None:
    admin = admin_only["admin"]
    cid = await create_connection(stack, admin, tpg_source)
    await stack.post(f"/api/v1/connections/{cid}/test", headers=h(admin))
    result = (await stack.post(f"/api/v1/connections/{cid}/introspect", headers=h(admin))).json()
    assert result["status"] == "connected" and result["samples_stored"] is False
    assert set(result["diff"]["added"]) == {f"public.{t}" for t, _, _ in tpg_synthetic.tables()}
    objects = (await stack.get(f"/api/v1/connections/{cid}/objects", headers=h(admin))).json()
    projects = next(o for o in objects["items"] if o["object"] == "public.projects")
    assert projects["keys"]["primary"] == ["project_id"]
    assert projects["keys"]["foreign"][0]["ref_object"] == "public.project_requests"
    assert {"name": "region", "type": "character varying(64)", "nullable": True} in projects["columns"]
    # Nothing but names, types and keys was stored: no fixture value appears anywhere in the catalogue.
    stored = owner_conn.execute("SELECT columns::text || keys::text FROM conn.connection_objects").fetchall()
    assert not any("Nomsa" in s[0] or "PRJ-0" in s[0] for s in stored)
    again = (await stack.post(f"/api/v1/connections/{cid}/introspect", headers=h(admin))).json()
    assert again["diff"] == {"added": [], "removed": [], "changed": []}


async def test_delete_and_disable(stack: httpx.AsyncClient, admin_only: dict[str, str],
                                  tpg_source: dict[str, Any], secrets_dir: Path) -> None:
    admin = admin_only["admin"]
    cid = await create_connection(stack, admin, tpg_source, name="Temporary")
    before = len(list(secrets_dir.iterdir()))
    assert (await stack.delete(f"/api/v1/connections/{cid}", headers=h(admin))).status_code == 204
    assert (await stack.get(f"/api/v1/connections/{cid}", headers=h(admin))).status_code == 404
    assert len(list(secrets_dir.iterdir())) == before - 1


# --------------------------------------------------------------------------- data model


async def test_manual_model_from_introspection_and_approval_gates(
    stack: httpx.AsyncClient, owner_conn: psycopg.Connection, make_token: Callable[..., str],
    tpg_source: dict[str, Any],
) -> None:
    w = await setup_world(stack, owner_conn, make_token, tpg_source, approve=False)
    admin = w.tokens["admin"]
    created = await stack.post("/api/v1/models", headers=h(admin), json={"connection_id": w.connection_id})
    assert created.status_code == 201, created.text
    model_id = created.json()["model_id"]
    draft = (await stack.get(f"/api/v1/models/{model_id}/draft", headers=h(admin))).json()
    assert {e["key"] for e in draft["entities"]} == {t for t, _, _ in tpg_synthetic.tables()}
    assert draft["links"][0]["origin"] == "foreign_key" and draft["links"][0]["accepted"] is False
    assert any("personal data" in q["question"] for q in draft["open_questions"])
    assert any("currency" in q["question"] for q in draft["open_questions"])
    assert draft["issues"] == []

    approve = await stack.post(f"/api/v1/models/{model_id}/approve", headers=h(admin),
                               json={"draft_etag": draft["draft_etag"]})
    assert approve.status_code == 409 and approve.json()["error"]["code"] == "unapproved_links_in_use"

    links = [{**draft["links"][0], "accepted": True}]
    patched = await stack.patch(f"/api/v1/models/{model_id}/draft", headers=h(admin, **{"If-Match": draft["draft_etag"]}),
                                json={"links": links})
    assert patched.status_code == 200, patched.text
    stale = await stack.patch(f"/api/v1/models/{model_id}/draft", headers=h(admin, **{"If-Match": draft["draft_etag"]}),
                              json={"links": links})
    assert stale.status_code == 412
    draft = patched.json()
    approve = await stack.post(f"/api/v1/models/{model_id}/approve", headers=h(admin),
                               json={"draft_etag": draft["draft_etag"]})
    assert approve.status_code == 409 and approve.json()["error"]["code"] == "open_questions_unresolved"
    for q in draft["open_questions"]:
        r = await stack.post(f"/api/v1/models/{model_id}/questions/{q['id']}/answer", headers=h(admin),
                             json={"answer": q["options"][0]})
        assert r.status_code == 200

    # An invalid edit is refused with the problem named.
    bad = await stack.patch(f"/api/v1/models/{model_id}/draft", headers=h(admin, **{"If-Match": draft["draft_etag"]}),
                            json={"measures": [{"key": "m", "label": "M", "entity": "projects", "plain_rule": "x",
                                                "expression": {"agg": "sum", "field": "no_such_field"}}]})
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "model_invalid"
    # A type mismatch is caught against the catalogue; a measure the source cannot run fails the dry run.
    mismatch = await stack.patch(f"/api/v1/models/{model_id}/draft",
                                 headers=h(admin, **{"If-Match": draft["draft_etag"]}),
                                 json={"measures": [{"key": "m", "label": "M", "entity": "projects", "plain_rule": "x",
                                                     "expression": {"agg": "sum", "field": "region"}}]})
    assert mismatch.status_code == 200 and mismatch.json()["issues"][0]["ref"] == "measure:m"
    broken = await stack.patch(f"/api/v1/models/{model_id}/draft",
                               headers=h(admin, **{"If-Match": mismatch.json()["draft_etag"]}),
                               json={"measures": [{"key": "m", "label": "M", "entity": "projects", "plain_rule": "x",
                                                   "expression": {"agg": "count", "where": {
                                                       "field": "region", "op": ">", "value": 5}}}]})
    resp = await stack.post(f"/api/v1/models/{model_id}/approve", headers=h(admin),
                            json={"draft_etag": broken.json()["draft_etag"]})
    assert resp.status_code == 422 and resp.json()["error"]["details"]["issues"][0]["ref"] == "measure:m"

    good = await stack.patch(f"/api/v1/models/{model_id}/draft",
                             headers=h(admin, **{"If-Match": broken.json()["draft_etag"]}),
                             json={"measures": [{"key": "projects_count", "label": "Projects", "entity": "projects",
                                                 "plain_rule": "Every project", "expression": {"agg": "count"}}]})
    approved = await stack.post(f"/api/v1/models/{model_id}/approve", headers=h(admin),
                                json={"draft_etag": good.json()["draft_etag"], "note": "Manual v1"})
    assert approved.status_code == 201, approved.text
    assert approved.json()["version"] == 1 and approved.json()["change_summary"] == "Manual v1"
    version = await stack.get(f"/api/v1/models/{model_id}/versions/{approved.json()['model_version_id']}",
                              headers=h(admin))
    fields = [f for e in version.json()["definition"]["entities"] for f in e["fields"]]
    assert fields and all(f["source_type"] for f in fields)  # the frozen version records each column's type


async def test_versions_are_immutable_and_superseded(stack: httpx.AsyncClient, owner_conn: psycopg.Connection,
                                                     make_token: Callable[..., str], tpg_source: dict[str, Any]) -> None:
    w: World = await setup_world(stack, owner_conn, make_token, tpg_source)
    admin = w.tokens["admin"]
    v1 = w.version_id
    with pytest.raises(psycopg.errors.RaiseException):
        owner_conn.execute("UPDATE model.model_versions SET definition = '{}' WHERE id = %s", (v1,))
    with pytest.raises(psycopg.errors.RestrictViolation):
        owner_conn.execute("UPDATE model.measures SET label = 'x' WHERE model_version_id = %s", (v1,))
    assert owner_conn.execute("SELECT count(*) FROM model.measures WHERE model_version_id = %s",
                              (v1,)).fetchone()[0] >= 20
    # Objects in the model are now on the allow-list.
    allowed = owner_conn.execute("SELECT qualified_name FROM conn.connection_objects WHERE is_allowed "
                                 "ORDER BY 1").fetchall()
    assert {a[0] for a in allowed} == {f"public.{t}" for t, _, _ in tpg_synthetic.tables()}

    before = value(await run(stack, admin, w.spec("open_projects")))
    draft = (await stack.get(f"/api/v1/models/{w.model_id}/draft", headers=h(admin))).json()
    measures = [m for m in draft["measures"] if m["key"] != "stale_projects"]
    for m in measures:
        if m["key"] == "open_projects":  # v2 changes the rule
            m["expression"] = {"agg": "count", "where": {"all": [
                {"field": "status", "op": "=", "value": "Active"}, {"field": "region", "op": "!=", "value": "DPO"}]}}
    patched = await stack.patch(f"/api/v1/models/{w.model_id}/draft",
                                headers=h(admin, **{"If-Match": draft["draft_etag"]}), json={"measures": measures})
    old_etag = draft["draft_etag"]
    v2 = await stack.post(f"/api/v1/models/{w.model_id}/approve", headers=h(admin),
                          json={"draft_etag": patched.json()["draft_etag"]})
    assert v2.status_code == 201, v2.text
    impact = v2.json()["impact"]["breaking_changes"]
    assert {"measure": "open_projects", "change": "rule_changed"} in impact
    assert {"measure": "stale_projects", "change": "removed"} in impact
    # Concurrent / stale approval is refused.
    again = await stack.post(f"/api/v1/models/{w.model_id}/approve", headers=h(admin), json={"draft_etag": old_etag})
    assert again.status_code == 412
    statuses = dict(owner_conn.execute("SELECT version_no, status FROM model.model_versions WHERE data_model_id = %s",
                                       (w.model_id,)).fetchall())
    assert statuses == {1: "superseded", 2: "approved"}
    # Pinned v1 specs keep working (RULE-015); v2 is usable at once, without a restart.
    assert value(await run(stack, admin, w.spec("open_projects"))) == before
    v2_spec = dict(w.spec("open_projects"), model_version_id=v2.json()["model_version_id"])
    assert value(await run(stack, admin, v2_spec)) < before
    audit = owner_conn.execute("SELECT severity, details->>'version' FROM ops.audit_events "
                               "WHERE action = 'model.approved' ORDER BY id").fetchall()
    assert audit == [("high", "1"), ("high", "2")]
    outbox = owner_conn.execute("SELECT count(*) FROM ops.outbox_events WHERE topic = 'model.version_approved'"
                                ).fetchone()
    assert outbox == (2,)


async def test_schema_drift_marks_model_broken_and_refuses_queries(
    stack: httpx.AsyncClient, owner_conn: psycopg.Connection, make_token: Callable[..., str],
    tpg_source: dict[str, Any],
) -> None:
    from conftest import _dsn_for

    w = await setup_world(stack, owner_conn, make_token, tpg_source)
    admin = w.tokens["admin"]
    assert value(await run(stack, admin, w.spec("open_projects"))) == 65
    with psycopg.connect(_dsn_for(tpg_source["database"]), autocommit=True) as src:
        src.execute("ALTER TABLE projects RENAME COLUMN on_hold_date TO on_hold_since")
    try:
        # Refused, never run against a different shape: either the column check catches the drift or the
        # source rejects the missing column; both surface as schema_changed and drop the column cache.
        resp = await run(stack, admin, w.spec("open_projects"), use_cache=False)
        assert resp.status_code == 409 and resp.json()["error"]["code"] == "schema_changed", resp.text
        again = await run(stack, admin, w.spec("open_projects"), use_cache=False)
        assert again.status_code == 409 and again.json()["error"]["code"] == "schema_changed"
        intro = (await stack.post(f"/api/v1/connections/{w.connection_id}/introspect", headers=h(admin))).json()
        assert intro["model_broken"] == [{"entity": "projects", "problem": "columns removed or changed"}]
        model = (await stack.get(f"/api/v1/models/{w.model_id}", headers=h(admin))).json()
        assert model["status"] == "broken"
        notes = owner_conn.execute("SELECT kind FROM ops.notifications").fetchall()
        assert notes == [("model_broken",)]
    finally:
        with psycopg.connect(_dsn_for(tpg_source["database"]), autocommit=True) as src:
            src.execute("ALTER TABLE projects RENAME COLUMN on_hold_since TO on_hold_date")


async def test_draft_impact_before_approval(stack: httpx.AsyncClient, owner_conn: psycopg.Connection,
                                            make_token: Callable[..., str], tpg_source: dict[str, Any]) -> None:
    """US-144: what approving a draft would change, before approval, without approving anything."""
    w: World = await setup_world(stack, owner_conn, make_token, tpg_source)
    admin = w.tokens["admin"]
    empty = await stack.get(f"/api/v1/models/{w.model_id}/draft/impact", headers=h(admin))
    assert empty.status_code == 200, empty.text
    body = empty.json()
    assert body["base_version"] == 1 and body["breaking_changes"] == [] and body["dashboards"] == []
    assert all(not v for kind in body["changes"].values() for v in kind.values())

    draft = (await stack.get(f"/api/v1/models/{w.model_id}/draft", headers=h(admin))).json()
    measures = [m for m in draft["measures"] if m["key"] != "stale_projects"]
    for m in measures:
        if m["key"] == "open_projects":
            m["expression"] = {"agg": "count", "where": {"all": [{"field": "status", "op": "=", "value": "Active"}]}}
            m["label"] = "Active projects"
    dimensions = [d for d in draft["dimensions"] if d["key"] != "priority"]
    patched = await stack.patch(f"/api/v1/models/{w.model_id}/draft",
                                headers=h(admin, **{"If-Match": draft["draft_etag"]}),
                                json={"measures": measures, "dimensions": dimensions})
    assert patched.status_code == 200, patched.text

    impact = (await stack.get(f"/api/v1/models/{w.model_id}/draft/impact", headers=h(admin))).json()
    assert impact["draft_etag"] == patched.json()["draft_etag"]
    changes = impact["changes"]
    assert [m["key"] for m in changes["measures"]["removed"]] == ["stale_projects"]
    changed = {m["key"]: m["what"] for m in changes["measures"]["changed"]}
    assert set(changed["open_projects"]) >= {"rule", "name"}
    assert [d["key"] for d in changes["dimensions"]["removed"]] == ["priority"]
    assert {"measure": "stale_projects", "change": "removed"} in impact["breaking_changes"]
    # The starter dashboards use these measures and follow the new version at approval.
    assert impact["dashboards"] and impact["widget_count"] == sum(len(d["widgets"]) for d in impact["dashboards"])
    assert all(d["changes_at_approval"] for d in impact["dashboards"])
    reasons = {r for d in impact["dashboards"] for wd in d["widgets"] for r in wd["reasons"]}
    assert any("rule changes" in r for r in reasons)
    # Read-only: nothing was approved.
    statuses = owner_conn.execute("SELECT status FROM model.model_versions WHERE data_model_id = %s ORDER BY 1",
                                  (w.model_id,)).fetchall()
    assert statuses == [("approved",), ("draft",)]
    viewer = await stack.get(f"/api/v1/models/{w.model_id}/draft/impact", headers=h(w.tokens["viewer"]))
    assert viewer.status_code == 403


async def test_tables_added_to_the_source_join_an_existing_model(
    stack: httpx.AsyncClient, owner_conn: psycopg.Connection, make_token: Callable[..., str],
    tpg_source: dict[str, Any],
) -> None:
    """BUG-018: a table created in the source after the model was approved shows up once the schema is read again,
    is listed as not in the model, and joins the draft with its link to a table the model already reads."""
    from conftest import _dsn_for

    w = await setup_world(stack, owner_conn, make_token, tpg_source)
    admin = w.tokens["admin"]
    with psycopg.connect(_dsn_for(tpg_source["database"]), autocommit=True) as src:
        src.execute("CREATE TABLE lessons (lesson_id integer PRIMARY KEY, "
                    "project_id varchar(64) REFERENCES projects(project_id), lesson text)")
    try:
        intro = (await stack.post(f"/api/v1/connections/{w.connection_id}/introspect", headers=h(admin))).json()
        assert intro["diff"]["added"] == ["public.lessons"] and intro["model_broken"] == []
        draft = (await stack.get(f"/api/v1/models/{w.model_id}/draft", headers=h(admin))).json()
        assert "public.lessons" in draft["unmodelled_objects"]
        url = f"/api/v1/models/{w.model_id}/draft/objects"
        stale = await stack.post(url, headers=h(admin, **{"If-Match": 'W/"0"'}), json={"objects": ["public.lessons"]})
        assert stale.status_code == 412
        unknown = await stack.post(url, headers=h(admin, **{"If-Match": draft["draft_etag"]}),
                                   json={"objects": ["public.nope"]})
        assert unknown.status_code == 400 and unknown.json()["error"]["details"]["objects"] == ["public.nope"]
        added = await stack.post(url, headers=h(admin, **{"If-Match": draft["draft_etag"]}),
                                 json={"objects": ["public.lessons"]})
        assert added.status_code == 200, added.text
        body = added.json()
        assert body["added"] == ["lessons"] and "public.lessons" not in body["unmodelled_objects"]
        entity = next(e for e in body["entities"] if e["key"] == "lessons")
        assert entity["source_object"] == "public.lessons" and entity["primary_key"] == ["lesson_id"]
        assert any(ln["from_entity"] == "lessons" and ln["to_entity"] == "projects" and not ln["accepted"]
                   for ln in body["links"])
        assert any(m["key"] == "lessons_count" for m in body["measures"])
        assert len(body["entities"]) == len(draft["entities"]) + 1  # the approved entities are untouched
    finally:
        with psycopg.connect(_dsn_for(tpg_source["database"]), autocommit=True) as src:
            src.execute("DROP TABLE IF EXISTS lessons")


async def test_columns_added_to_the_source_join_an_existing_model(
    stack: httpx.AsyncClient, owner_conn: psycopg.Connection, make_token: Callable[..., str],
    tpg_source: dict[str, Any],
) -> None:
    """BUG-028: a column added to a table the model reads shows up once the schema is read again, is listed as not
    in the model, and joins the entity's fields in the draft; the approved fields are untouched."""
    from conftest import _dsn_for

    w = await setup_world(stack, owner_conn, make_token, tpg_source)
    admin = w.tokens["admin"]
    with psycopg.connect(_dsn_for(tpg_source["database"]), autocommit=True) as src:
        src.execute("ALTER TABLE projects ADD COLUMN benefit_usd numeric(14,2)")
    try:
        intro = (await stack.post(f"/api/v1/connections/{w.connection_id}/introspect", headers=h(admin))).json()
        assert "public.projects" in intro["diff"]["changed"] and intro["model_broken"] == []
        draft = (await stack.get(f"/api/v1/models/{w.model_id}/draft", headers=h(admin))).json()
        new = next(i for i in draft["unmodelled_columns"] if i["object"] == "public.projects")
        assert new["columns"] == ["benefit_usd"]
        entity = new["entity"]
        before = next(e for e in draft["entities"] if e["key"] == entity)["fields"]
        url = f"/api/v1/models/{w.model_id}/draft/columns"
        stale = await stack.post(url, headers=h(admin, **{"If-Match": 'W/"0"'}),
                                 json={"columns": {entity: ["benefit_usd"]}})
        assert stale.status_code == 412
        unknown = await stack.post(url, headers=h(admin, **{"If-Match": draft["draft_etag"]}),
                                   json={"columns": {entity: ["nope"]}})
        assert unknown.status_code == 400 and unknown.json()["error"]["details"]["columns"] == [f"{entity}.nope"]
        added = await stack.post(url, headers=h(admin, **{"If-Match": draft["draft_etag"]}),
                                 json={"columns": {entity: ["benefit_usd"]}})
        assert added.status_code == 200, added.text
        body = added.json()
        assert body["added"] == [f"{entity}.benefit_usd"]
        assert all(i["object"] != "public.projects" for i in body["unmodelled_columns"])
        fields = next(e for e in body["entities"] if e["key"] == entity)["fields"]
        assert len(fields) == len(before) + 1 and fields[-1]["source_column"] == "benefit_usd"
        assert fields[-1]["data_type"] == "decimal" and fields[-1]["semantic_type"] == "currency"
        assert not [i for i in body["issues"] if "benefit_usd" in i["issue"]]
        # BUG-029: the draft still carries the approved checksum of the old columns; approval must not read the
        # added field as source drift.
        for q in body["open_questions"]:
            if q["status"] == "open":
                r = await stack.post(f"/api/v1/models/{w.model_id}/questions/{q['id']}/answer", headers=h(admin),
                                     json={"answer": q["options"][0] if q["options"] else "Yes"})
                assert r.status_code == 200, r.text
        draft = (await stack.get(f"/api/v1/models/{w.model_id}/draft", headers=h(admin))).json()
        approved = await stack.post(f"/api/v1/models/{w.model_id}/approve", headers=h(admin),
                                    json={"draft_etag": draft["draft_etag"]})
        assert approved.status_code == 201, approved.text
    finally:
        with psycopg.connect(_dsn_for(tpg_source["database"]), autocommit=True) as src:
            src.execute("ALTER TABLE projects DROP COLUMN IF EXISTS benefit_usd")
