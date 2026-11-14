"""Export jobs (US-095; FEAT-045; API-046; DATA-049).

Acceptance: REQ-108 (access re-checked when the job runs; refused and audited when revoked), TEST-054 groundwork
(the payload holds every visible widget in the reader's scope with the filters and date), TEST-062 (a hidden field is
absent from the export), MSG-028 (too large), the 10-per-hour limit, idempotent requests, signed 15-minute links and
24-hour retention. The worker's part is played here by calling the internal endpoints with its service token.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import httpx
import psycopg
import pytest
from api_testkit import needs_db
from pmo_testkit import World, h, setup_world

from tela_common import service_auth

pytestmark = [needs_db, pytest.mark.integration]
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
KSA = [{"ref": "dimension:region", "op": "=", "values": ["KSA"]}]


class MemoryStore:
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
                tpg_source: dict[str, Any]) -> World:
    w = await setup_world(stack, owner_conn, make_token, tpg_source)
    w.extra["store"] = MemoryStore()
    stack.app.state.object_store = w.extra["store"]  # type: ignore[attr-defined]
    return w


def worker(signing_keys: dict[str, Any], sub: str = "tela-worker") -> dict[str, str]:
    token = service_auth.issue(signing_keys["service"].encode(), sub=sub, aud="tela-api-internal", ttl_s=60)
    return {"Authorization": f"Bearer {token}"}


async def projects(api: httpx.AsyncClient, token: str) -> str:
    items = (await api.get("/api/v1/published", headers=h(token))).json()["items"]
    return next(d["id"] for d in items if d["title"] == "PMO — Projects")


async def request_export(api: httpx.AsyncClient, token: str, dashboard_id: str, fmt: str = "xlsx",
                         filters: list[dict[str, Any]] | None = None, **headers: str) -> httpx.Response:
    return await api.post("/api/v1/exports", headers=h(token, **headers),
                          json={"dashboard_id": dashboard_id, "format": fmt, "page_filters": filters or []})


def widgets(payload: dict[str, Any]) -> list[dict[str, Any]]:
    return [i for t in payload["tabs"] for s in t["sections"] for i in s["items"] if i["kind"] == "widget"]


async def test_export_lifecycle(stack: httpx.AsyncClient, world: World, owner_conn: psycopg.Connection,
                                signing_keys: dict[str, Any]) -> None:
    nomsa = world.tokens["nomsa"]
    did = await projects(stack, nomsa)
    resp = await request_export(stack, nomsa, did, filters=KSA)
    assert resp.status_code == 202, resp.text
    job = resp.json()
    assert job["status"] == "queued" and job["download_url"] is None
    assert resp.headers["location"] == f"/api/v1/exports/{job['job_id']}"
    jid = job["job_id"]
    event = owner_conn.execute("SELECT payload FROM ops.outbox_events WHERE topic = 'export.requested'").fetchone()
    assert event and event[0] == {"job_id": jid, "format": "xlsx"}
    # Only the requester sees the job.
    assert (await stack.get(f"/api/v1/exports/{jid}", headers=h(world.tokens["karim"]))).status_code == 404

    # The internal endpoints take the worker's service token only — never a person's token.
    start = f"/api/v1/internal/exports/{jid}/start"
    body = {"tenant_id": world.tenant_id}
    assert (await stack.post(start, json=body, headers=h(nomsa))).status_code == 401
    assert (await stack.post(start, json=body, headers=worker(signing_keys, sub="someone"))).status_code == 403
    payload = (await stack.post(start, json=body, headers=worker(signing_keys))).json()
    assert payload["dashboard"]["title"] == "PMO — Projects" and len(payload["tabs"]) == 26
    assert "Own CIO" in payload["scope"] and payload["filters_text"] == "Region = KSA"
    assert payload["footer"].startswith("Region = KSA · Scope: ") and "Data as of" in payload["footer"]
    assert payload["file_name"].startswith("PMO-Projects-") and payload["file_name"].endswith(".xlsx")
    items = widgets(payload)
    assert len(items) == 203 and payload["excluded_widgets"] == 0  # pack 2.0: 26 tabs (US-155)
    assert payload["row_count"] == sum(len(i.get("rows", [])) for i in items)
    for i in items:
        assert "error" not in i, (i["title"], i.get("error"))
        names = [c["name"] for c in i["columns"]]
        for col, allowed in (("region", {"KSA"}), ("cio", {"Nomsa Osei"})):
            if col in names:
                assert {r[names.index(col)] for r in i["rows"]} <= allowed, i["title"]
        assert i["definition"]["label"]
    assert (await stack.get(f"/api/v1/exports/{jid}", headers=h(nomsa))).json()["status"] == "running"

    put = await stack.put(f"/api/v1/internal/exports/{jid}/file", content=b"PK fake workbook", headers={
        **worker(signing_keys), "Content-Type": XLSX}, params={
        "tenant_id": world.tenant_id, "file_name": payload["file_name"], "row_count": payload["row_count"],
        "excluded_widgets": 0})
    assert put.status_code == 200, put.text
    done = (await stack.get(f"/api/v1/exports/{jid}", headers=h(nomsa))).json()
    assert done["status"] == "succeeded" and done["size_bytes"] == 16 and done["file_name"] == payload["file_name"]
    assert done["download_url"] and done["expires_at"] and done["file_expires_at"]

    # The signed link needs no bearer token; tampering or another job id fails.
    file = await stack.get(done["download_url"])
    assert file.status_code == 200 and file.content == b"PK fake workbook"
    assert file.headers["content-type"] == XLSX and "attachment" in file.headers["content-disposition"]
    assert (await stack.get(done["download_url"] + "x")).status_code == 404
    token = done["download_url"].split("token=")[1]
    assert (await stack.get(f"/api/v1/exports/{world.tenant_id}/download?token={token}")).status_code == 404

    audit = owner_conn.execute("SELECT action, outcome, details FROM ops.audit_events WHERE action LIKE 'export.%' "
                               "ORDER BY occurred_at").fetchall()
    assert [a[0] for a in audit] == ["export.requested", "export.created", "export.downloaded"]
    created = audit[1][2]
    assert created["filters"] == KSA and "Own CIO" in created["scope"] and created["row_count"] == payload["row_count"]
    assert "rows_data" not in created and "Nomsa" not in json.dumps(created["filters"])
    note = owner_conn.execute("SELECT kind, title, link FROM ops.notifications WHERE kind = 'export.ready'").fetchone()
    assert note and note[1] == "Your Excel export is ready" and note[2] == f"/dashboards/{did}?export={jid}"

    # Finished jobs can't be started or written again.
    assert (await stack.post(start, json=body, headers=worker(signing_keys))).status_code == 409


async def test_access_rechecked_when_the_job_runs(stack: httpx.AsyncClient, world: World,
                                                  owner_conn: psycopg.Connection,
                                                  signing_keys: dict[str, Any]) -> None:
    """REQ-108: a person removed from their groups after asking gets nothing, and the refusal is audited."""
    nomsa = world.tokens["nomsa"]
    jid = (await request_export(stack, nomsa, await projects(stack, nomsa), "pdf")).json()["job_id"]
    owner_conn.execute("DELETE FROM iam.idp_group_members m USING iam.users u WHERE u.id = m.user_id "
                       "AND u.display_name = 'Nomsa Osei'")
    resp = await stack.post(f"/api/v1/internal/exports/{jid}/start", json={"tenant_id": world.tenant_id},
                            headers=worker(signing_keys))
    assert resp.status_code == 403
    job = owner_conn.execute("SELECT status, error_code, error FROM ops.export_jobs WHERE id = %s", (jid,)).fetchone()
    assert job == ("failed", "no_access", "You no longer have access to export this dashboard.")
    deny = owner_conn.execute("SELECT outcome, severity FROM ops.audit_events WHERE action = 'export.created'").fetchone()
    assert deny == ("deny", "security")
    note = owner_conn.execute("SELECT title FROM ops.notifications WHERE kind = 'export.failed'").fetchone()
    assert note == ("Your export didn't finish",)


async def test_hidden_fields_and_widgets_left_out(stack: httpx.AsyncClient, world: World,
                                                  signing_keys: dict[str, Any]) -> None:
    """TEST-062 and FEAT-045 A2: widgets hidden for the role are not in the file; no column of a hidden field."""
    admin, nomsa = world.tokens["admin"], world.tokens["nomsa"]
    await stack.put("/api/v1/admin/field-policies", headers=h(admin), json={
        "connection_id": world.connection_id,
        "rules": [{"field_ref": "field:projects.approved_budget_value_usd", "hidden_for": ["cio"]}]})
    jid = (await request_export(stack, nomsa, await projects(stack, nomsa))).json()["job_id"]
    payload = (await stack.post(f"/api/v1/internal/exports/{jid}/start", json={"tenant_id": world.tenant_id},
                                headers=worker(signing_keys))).json()
    assert payload["excluded_widgets"] > 0
    items = widgets(payload)
    assert len(items) == 203 - payload["excluded_widgets"]
    text = json.dumps(payload)
    assert "approved_budget_value_usd" not in text and "Approved budget" not in text


async def test_limits_and_idempotency(stack: httpx.AsyncClient, world: World, owner_conn: psycopg.Connection,
                                      signing_keys: dict[str, Any]) -> None:
    creator = world.tokens["creator"]
    did = await projects(stack, creator)
    first = await request_export(stack, creator, did, **{"Idempotency-Key": "k-1"})
    again = await request_export(stack, creator, did, **{"Idempotency-Key": "k-1"})
    assert again.json()["job_id"] == first.json()["job_id"]
    for _ in range(9):
        assert (await request_export(stack, creator, did)).status_code == 202
    limited = await request_export(stack, creator, did)
    assert limited.status_code == 429 and int(limited.headers["retry-after"]) >= 60
    assert "10 times an hour" in limited.json()["error"]["message"]
    # Unknown or invisible dashboards look the same: no access.
    assert (await request_export(stack, world.tokens["admin"], world.tenant_id)).status_code == 404

    # MSG-028: over the row limit the job fails with a plain message.
    stack.app.state.settings.export_max_rows = 1  # type: ignore[attr-defined]
    jid = first.json()["job_id"]
    resp = await stack.post(f"/api/v1/internal/exports/{jid}/start", json={"tenant_id": world.tenant_id},
                            headers=worker(signing_keys))
    assert resp.status_code == 422 and resp.json()["error"]["code"] == "payload_too_large"
    out = (await stack.get(f"/api/v1/exports/{jid}", headers=h(creator))).json()
    assert out["status"] == "failed" and out["error"]["code"] == "export_too_large"
    assert out["error"]["message"].startswith("This export is larger than allowed.")

    # A renderer failure is a plain, retryable failure.
    other = (await request_export(stack, world.tokens["nomsa"], did)).json()["job_id"]
    failed = await stack.post(f"/api/v1/internal/exports/{other}/fail", headers=worker(signing_keys),
                              json={"tenant_id": world.tenant_id, "error_code": "render_failed"})
    assert failed.json() == {"status": "failed"}
    out = (await stack.get(f"/api/v1/exports/{other}", headers=h(world.tokens["nomsa"]))).json()
    assert out["error"] == {"code": "render_failed", "message": "The export couldn't be created. Try again in a few "
                                                                "minutes."}


async def test_retention_sweep(stack: httpx.AsyncClient, world: World, owner_conn: psycopg.Connection,
                               signing_keys: dict[str, Any]) -> None:
    nomsa = world.tokens["nomsa"]
    jid = (await request_export(stack, nomsa, await projects(stack, nomsa))).json()["job_id"]
    await stack.post(f"/api/v1/internal/exports/{jid}/start", json={"tenant_id": world.tenant_id},
                     headers=worker(signing_keys))
    await stack.put(f"/api/v1/internal/exports/{jid}/file", content=b"PK", headers={
        **worker(signing_keys), "Content-Type": XLSX}, params={"tenant_id": world.tenant_id, "file_name": "a.xlsx"})
    link = (await stack.get(f"/api/v1/exports/{jid}", headers=h(nomsa))).json()["download_url"]
    store: MemoryStore = world.extra["store"]
    assert len(store.files) == 1
    owner_conn.execute("UPDATE ops.export_jobs SET expires_at = now() - interval '1 minute' WHERE id = %s", (jid,))
    assert (await stack.get(link)).status_code == 404
    swept = (await stack.post("/api/v1/internal/exports/sweep", headers=worker(signing_keys))).json()
    assert swept == {"expired": 1, "deleted": 1} and store.files == {}
    out = (await stack.get(f"/api/v1/exports/{jid}", headers=h(nomsa))).json()
    assert out["status"] == "expired" and out["download_url"] is None
