"""Excel and CSV sources through the API and the in-process runner (US-108, US-109; TEST-013, TEST-085; E-13).

Upload checks (type, size, macros, passwords, content, UTF-8), the governed path on a workbook (test, schema
read with the sheet profile, PMO pack, approval pinning the file version, scoped queries), the Admin's
confirmed settings, replacement as a pending version with its diff, activation breaking the model when a sheet
disappears, discarding, and the retention sweep sparing pinned versions.
"""

from __future__ import annotations

import io
import os
import sys
import zipfile
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
import psycopg
import pytest
from api_testkit import needs_db
from pmo_testkit import AS_OF, World, h, load_packs, setup_world

from tela_common import service_auth

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "connector-runner" / "tests"))
from sources import tpg_csv, tpg_workbook

pytestmark = needs_db
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


class Store:
    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}

    async def ensure_bucket(self) -> None:
        return None

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        self.files[key] = data

    async def get(self, key: str) -> bytes:
        return self.files[key]

    async def delete(self, key: str) -> None:
        self.files.pop(key, None)


@pytest.fixture
async def world(stack: httpx.AsyncClient, owner_conn: psycopg.Connection, make_token: Callable[..., str],
                tpg_source: dict[str, Any], runner_app: Any, tmp_path: Path) -> World:
    w = await setup_world(stack, owner_conn, make_token, tpg_source, approve=False, policies=False)
    store = Store()
    stack.app.state.object_store = store  # type: ignore[attr-defined]
    stack.app.state.object_store_ready = True  # type: ignore[attr-defined]
    files = runner_app.state.adapters["excel"]
    files.store, files.cache_dir = store, tmp_path / "runner-files"
    w.extra["store"] = store
    return w


async def upload(api: httpx.AsyncClient, token: str, name: str, filename: str, data: bytes,
                 **form: str) -> httpx.Response:
    return await api.post("/api/v1/connections/files", headers=h(token),
                          files={"file": (filename, data, XLSX)}, data={"name": name, **form})


def _zip(parts: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in parts.items():
            z.writestr(name, data)
    return buf.getvalue()


async def test_upload_checks(stack: httpx.AsyncClient, world: World) -> None:
    admin = world.tokens["admin"]
    book = tpg_workbook()
    cases = {
        ("x.xlsm", book): "macro_or_legacy",
        ("x.xls", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 64): "legacy_format",
        ("x.xlsx", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + "EncryptedPackage".encode("utf-16-le")): "password_protected",
        ("x.xlsx", b"just text"): "content_mismatch",
        ("x.xlsx", _zip({"[Content_Types].xml": b"<Types/>", "xl/workbook.xml": b"<w/>",
                         "xl/vbaProject.bin": b"x"})): "macro_enabled",
        ("x.xlsx", _zip({"[Content_Types].xml": b"<Types>macroEnabled</Types>", "xl/workbook.xml": b"<w/>"})):
            "macro_enabled",
        ("x.csv", "région;caf\xe9\n".encode("latin-1")): "not_utf8",
        ("x.csv", b"a,b\n1,\x00\n"): "content_mismatch",
        ("x.pdf", b"%PDF"): "unsupported_type",
    }
    for (filename, data), reason in cases.items():
        resp = await upload(stack, admin, "Bad", filename, data)
        assert resp.status_code == 400 and resp.json()["error"]["details"]["reason"] == reason, (filename, resp.text)
    stack.app.state.settings.file_source_max_upload_mb = 1  # type: ignore[attr-defined]
    try:
        big = await upload(stack, admin, "Big", "big.csv", b"a\n" + b"1\n" * 600_000)
        assert big.status_code == 422 and big.json()["error"]["details"]["reason"] == "too_large"
    finally:
        stack.app.state.settings.file_source_max_upload_mb = 50  # type: ignore[attr-defined]
    retention = await upload(stack, admin, "R", "x.csv", tpg_csv(), retention_policy="fixed_days")
    assert retention.status_code == 400 and retention.json()["error"]["details"]["reason"] == "retention_required"
    viewer = await upload(stack, world.tokens["viewer"], "V", "x.csv", tpg_csv())
    assert viewer.status_code == 403
    assert not world.extra["store"].files  # nothing refused was stored


async def test_workbook_source_end_to_end(stack: httpx.AsyncClient, world: World,
                                          owner_conn: psycopg.Connection, signing_keys: dict[str, Any]) -> None:
    admin = world.tokens["admin"]
    created = await upload(stack, admin, "TPG workbook", "TPG extract.xlsx", tpg_workbook(extras=True))
    assert created.status_code == 201, created.text
    cid = created.json()["connection_id"]
    assert created.json()["engine"] == "excel" and created.json()["status"] == "draft"
    assert created.json()["secret_set"] is False
    test = (await stack.post(f"/api/v1/connections/{cid}/test", headers=h(admin))).json()
    assert test["status"] == "connected" and test["read_only"]["verdict"] == "safe", test
    intro = (await stack.post(f"/api/v1/connections/{cid}/introspect", headers=h(admin))).json()
    assert intro["status"] == "connected" and intro["objects"] == 3, intro
    files = (await stack.get(f"/api/v1/connections/{cid}/files", headers=h(admin))).json()
    v1 = files["items"][0]
    assert v1["version"] == 1 and v1["status"] == "active" and v1["retention_policy"] == "replaced_plus_30d"
    notes = next(s for s in v1["sheets"] if s["sheet"] == "Notes")
    assert notes["merged_cells"] == 1 and {c["header"]: c["flags"] for c in notes["columns"]}["Mixed"] == ["mixed_types"]

    # The Admin drops the notes sheet and marks the CIO column as personal data; a re-test and re-read follow.
    settings = await stack.put(f"/api/v1/connections/{cid}/files/settings", headers=h(admin), json={
        "sheets": [{"sheet": "projects", "columns": [{"header": "cio", "personal": True}]},
                   {"sheet": "project_requests"}, {"sheet": "Notes", "include": False}],
        "classification": "restricted"})
    assert settings.status_code == 200 and settings.json()["status"] == "draft", settings.text
    assert (await stack.post(f"/api/v1/connections/{cid}/test", headers=h(admin))).json()["status"] == "connected"
    intro = (await stack.post(f"/api/v1/connections/{cid}/introspect", headers=h(admin))).json()
    assert intro["diff"]["removed"] == ["main.notes"]

    # The PMO pack matches the sheets; approval pins file version 1.
    load_packs(os.environ["TELA_TEST_OWNER_DATABASE_URL"])
    packs = (await stack.get("/api/v1/starter-packs", headers=h(admin))).json()["items"]
    pack_id = next(p["id"] for p in packs if p["pack_key"] == "pmo_tpg")
    install = await stack.post(f"/api/v1/starter-packs/{pack_id}/install", headers=h(admin),
                               json={"connection_id": cid, "reporting_date": AS_OF})
    assert install.status_code == 202, install.text
    model_id = install.json()["model_id"]
    draft = (await stack.get(f"/api/v1/models/{model_id}/draft", headers=h(admin))).json()
    approved = await stack.post(f"/api/v1/models/{model_id}/approve", headers=h(admin),
                                json={"draft_etag": draft["draft_etag"]})
    assert approved.status_code == 201, approved.text
    version_id = approved.json()["model_version_id"]
    pin = owner_conn.execute("SELECT definition->'source_pin'->>'file_version' FROM model.model_versions "
                             "WHERE id = %s", (version_id,)).fetchone()
    assert pin and pin[0] == "1"
    for entity in ("projects", "project_requests"):
        resp = await stack.post("/api/v1/admin/scope-policies", headers=h(admin), json={
            "connection_id": cid, "entity": entity, "role": "admin", "all_rows": True, "description": "All"})
        assert resp.status_code == 201, resp.text
    spec = {"model_version_id": version_id, "connection_id": cid, "measure": {"ref": "measure:open_projects"}}
    run = await stack.post("/api/v1/query/run", headers=h(admin), json={"spec": spec, "use_cache": False})
    assert run.status_code == 200 and run.json()["rows"] == [[65]], run.text
    uploaded = owner_conn.execute("SELECT created_at FROM conn.file_sources WHERE connection_id = %s AND "
                                  "version_no = 1", (cid,)).fetchone()
    assert uploaded and abs((datetime.fromisoformat(run.json()["as_of"]) - uploaded[0]).total_seconds()) < 5

    # A replacement without the requests sheet: pending, with the diff and the definitions it would break.
    import openpyxl

    book = openpyxl.load_workbook(io.BytesIO(tpg_workbook()))
    del book["project_requests"]
    buf = io.BytesIO()
    book.save(buf)
    pending = await stack.post(f"/api/v1/connections/{cid}/files", headers=h(admin),
                               files={"file": ("TPG v2.xlsx", buf.getvalue(), XLSX)})
    assert pending.status_code == 201, pending.text
    diff = pending.json()["diff"]
    assert pending.json()["version"] == 2 and diff["removed"] == ["main.project_requests"]
    assert any(b["entity"] == "project_requests" for b in diff["breaks_model"])
    again = await stack.post(f"/api/v1/connections/{cid}/files", headers=h(admin),
                             files={"file": ("TPG v3.xlsx", buf.getvalue(), XLSX)})
    assert again.status_code == 409
    # Until activation the approved model still reads version 1.
    run = await stack.post("/api/v1/query/run", headers=h(admin), json={"spec": spec, "use_cache": False})
    assert run.json()["rows"] == [[65]]
    activated = await stack.post(f"/api/v1/connections/{cid}/files/2/activate", headers=h(admin))
    assert activated.status_code == 200, activated.text
    assert activated.json()["introspection"]["model_broken"], activated.json()
    rows = owner_conn.execute("SELECT version_no, status, retention_until > now() + interval '29 days' "
                              "FROM conn.file_sources WHERE connection_id = %s ORDER BY version_no", (cid,)).fetchall()
    assert rows == [(1, "replaced", True), (2, "active", None)]
    # The approved model stays pinned to version 1 until a new version is approved.
    run = await stack.post("/api/v1/query/run", headers=h(admin), json={"spec": spec, "use_cache": False})
    assert run.status_code == 200 and run.json()["rows"] == [[65]], run.text

    # A discarded upload is purged by the sweep; the pinned version is not, even once expired.
    third = await stack.post(f"/api/v1/connections/{cid}/files", headers=h(admin),
                             files={"file": ("TPG v3.xlsx", tpg_workbook(), XLSX)})
    assert third.status_code == 201
    assert (await stack.post(f"/api/v1/connections/{cid}/files/3/discard", headers=h(admin))).status_code == 200
    owner_conn.execute("UPDATE conn.file_sources SET retention_until = now() - interval '1 day' "
                       "WHERE connection_id = %s AND version_no = 1", (cid,))
    token = service_auth.issue(signing_keys["service"].encode(), sub="tela-worker", aud="tela-api-internal", ttl_s=60)
    swept = await stack.post("/api/v1/internal/files/sweep", headers={"Authorization": f"Bearer {token}"})
    assert swept.status_code == 200 and swept.json()["purged"] == 1, swept.text
    status = dict(owner_conn.execute("SELECT version_no, status FROM conn.file_sources WHERE connection_id = %s",
                                     (cid,)).fetchall())
    assert status == {1: "replaced", 2: "active", 3: "purged"}
    audit = owner_conn.execute("SELECT count(*) FROM ops.audit_events WHERE action LIKE 'connection.file_%%'"
                               ).fetchone()
    assert audit and audit[0] >= 5


async def test_csv_source_and_engine_mismatch(stack: httpx.AsyncClient, world: World) -> None:
    admin = world.tokens["admin"]
    created = await upload(stack, admin, "Projects CSV", "projects.csv", tpg_csv(), retention_policy="fixed_days",
                           retention_days="90")
    assert created.status_code == 201 and created.json()["engine"] == "csv", created.text
    cid = created.json()["connection_id"]
    assert (await stack.post(f"/api/v1/connections/{cid}/test", headers=h(admin))).json()["status"] == "connected"
    intro = (await stack.post(f"/api/v1/connections/{cid}/introspect", headers=h(admin))).json()
    assert intro["objects"] == 1
    wrong = await stack.post(f"/api/v1/connections/{cid}/files", headers=h(admin),
                             files={"file": ("p.xlsx", tpg_workbook(), XLSX)})
    assert wrong.status_code == 400
    etag = (await stack.get(f"/api/v1/connections/{cid}", headers=h(admin))).json()["etag"]
    patch = await stack.patch(f"/api/v1/connections/{cid}", headers=h(admin, **{"If-Match": etag}),
                              json={"host": "elsewhere"})
    assert patch.status_code == 400
