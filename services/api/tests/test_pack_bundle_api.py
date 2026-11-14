"""One YAML file for a draft's measures, dimensions, report blueprints and starter dashboards (Pack Studio import),
on the TPG fixture.

A tenant draft cloned from PMO exports its four parts and imports them back; a check (``dry_run``) saves nothing; an
import adds a measure, a blueprint using it and a dashboard in one go, on the revision in ``If-Match``; a file with
errors saves nothing and names each path; a system pack is read-only and a Creator may not import.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx
import psycopg
import pytest
import yaml
from api_testkit import needs_db
from pmo_testkit import World, h, setup_world
from test_pack_blueprints_api import _clone

pytestmark = [needs_db, pytest.mark.integration]


@pytest.fixture
async def world(stack: httpx.AsyncClient, owner_conn: psycopg.Connection, make_token: Callable[..., str],
                tpg_source: dict[str, Any]) -> World:
    return await setup_world(stack, owner_conn, make_token, tpg_source)


async def test_export_check_and_import_a_bundle(stack: httpx.AsyncClient, world: World,
                                                owner_conn: psycopg.Connection) -> None:
    admin = world.tokens["admin"]
    pid = _clone(owner_conn, world.tenant_id)
    got = await stack.get(f"/api/v1/packs/{pid}/bundle", headers=h(admin))
    assert got.status_code == 200, got.text
    etag = got.headers["ETag"]
    bundle = yaml.safe_load(got.json()["yaml"])
    assert got.json()["file_name"] == "acme_pmo-0.1.0-items.yaml"
    assert {"measures", "dimensions", "reports", "dashboards"} <= set(bundle) and len(bundle["reports"]) > 30

    url = f"/api/v1/packs/{pid}/bundle"
    same = await stack.post(url, headers=h(admin), json={"yaml": got.json()["yaml"], "dry_run": True})
    assert same.status_code == 200, same.text
    assert same.json()["valid"] and all(not c["added"] and not c["updated"] for c in same.json()["changes"].values())

    bp = next(b for b in bundle["reports"] if b["id"] == "projects_overview")
    text = yaml.safe_dump({
        "measures": [{"key": "gulf_projects", "label": "Gulf projects", "entity": "projects",
                      "plain_rule": "Every project record.", "expression": {"agg": "count"}}],
        "reports": [{**bp, "id": "gulf_overview", "title": "Gulf overview", "tab": "Gulf", "dashboard": "gulf",
                     "kpis": ["gulf_projects"]}],
        "dashboards": {"gulf": {"title": "Gulf", "filters": ["region"]}},
    }, sort_keys=False)
    checked = await stack.post(url, headers=h(admin), json={"yaml": text, "dry_run": True})
    assert checked.status_code == 200 and checked.json()["valid"], checked.text
    assert checked.json()["changes"]["measures"]["added"] == ["gulf_projects"]
    # A check saves nothing, so the revision is the same.
    assert (await stack.get(f"/api/v1/packs/{pid}/bundle", headers=h(admin))).headers["ETag"] == etag

    assert (await stack.post(url, headers=h(admin), json={"yaml": text})).status_code == 412
    done = await stack.post(url, headers=h(admin, **{"If-Match": etag}), json={"yaml": text})
    assert done.status_code == 200, done.text
    assert done.headers["ETag"] != etag
    assert "gulf_projects" in {m["key"] for m in done.json()["manifest"]["measures"]}
    reports = (await stack.get(f"/api/v1/packs/{pid}/reports", headers=h(admin))).json()
    gulf = next(d for d in reports["dashboards"] if d["key"] == "gulf")
    assert gulf["reports"] == ["gulf_overview"] and gulf["filters"] == ["region"] and reports["problems"] == {}
    audit = owner_conn.execute("SELECT details FROM ops.audit_events WHERE action = 'pack.bundle_imported' AND "
                               "object_id = %s", (pid,)).fetchone()
    assert audit and audit[0]["changes"]["reports"]["added"] == 1

    # Errors save nothing and name their path in the file.
    bad = yaml.safe_dump({"reports": [{**bp, "kpis": ["no_such_measure"]}]})
    refused = await stack.post(url, headers=h(admin, **{"If-Match": done.headers["ETag"]}), json={"yaml": bad})
    assert refused.status_code == 400, refused.text
    assert refused.json()["error"]["details"]["errors"][0]["path"] == "reports[0].kpis.0"
    assert (await stack.post(url, headers=h(admin), json={"yaml": "measures: [", "dry_run": True})).status_code == 400


async def test_who_may_import(stack: httpx.AsyncClient, world: World, owner_conn: psycopg.Connection) -> None:
    pid = _clone(owner_conn, world.tenant_id)
    sys_id = owner_conn.execute("SELECT id FROM model.starter_packs WHERE pack_key = 'pmo_tpg' AND tenant_id IS "
                                "NULL").fetchone()[0]
    body = {"yaml": "measures: []\n", "dry_run": True}
    assert (await stack.post(f"/api/v1/packs/{sys_id}/bundle", headers=h(world.tokens["admin"]),
                             json=body)).status_code == 409
    assert (await stack.post(f"/api/v1/packs/{pid}/bundle", headers=h(world.tokens["creator"]),
                             json=body)).status_code == 403
