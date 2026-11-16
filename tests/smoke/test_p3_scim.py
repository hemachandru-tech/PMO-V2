"""SCIM provisioning on the running Compose stack (US-118, US-119), through the edge proxy like Entra would.

Turns provisioning on, provisions a person and a group, suspends and removes the person, runs Reconcile and reads
the status page, then turns provisioning off again. Uses its own names, so the dev users are untouched.
"""

from __future__ import annotations

import uuid

import httpx
import pytest
from test_compose_smoke import EDGE
from test_p1_read_path import Api, _pw, apis  # noqa: F401 - shared fixture

pytestmark = pytest.mark.skipif(
    not __import__("os").environ.get("TELA_SMOKE"), reason="set TELA_SMOKE=1 with the stack running")


def test_scim_round_trip(apis: dict[str, Api]) -> None:
    admin = apis["admin"]
    created = admin.call("POST", "/admin/provisioning/token")
    assert created.status_code == 201, created.text
    scim = httpx.Client(base_url=f"{EDGE}/scim/v2", timeout=30, headers={
        "Host": "tela.localhost:8088", "Authorization": f"Bearer {created.json()['token']}",
        "Content-Type": "application/scim+json"})
    try:
        assert scim.get("/ServiceProviderConfig").json()["patch"]["supported"] is True
        ext = f"smoke-{uuid.uuid4().hex[:12]}"
        user = scim.post("/Users", json={"externalId": ext, "userName": f"{ext}@example.com",
                                         "name": {"givenName": "Smoke", "familyName": "Tester"}, "active": True})
        assert user.status_code == 201, user.text
        uid = user.json()["id"]
        found = scim.get("/Users", params={"filter": f'externalId eq "{ext}"'}).json()
        assert found["totalResults"] == 1
        group = scim.post("/Groups", json={"displayName": f"Smoke {ext}", "externalId": f"grp-{ext}",
                                           "members": [{"value": uid}]})
        assert group.status_code == 201 and len(group.json()["members"]) == 1
        off = scim.patch(f"/Users/{uid}", json={"Operations": [{"op": "Replace", "path": "active", "value": "False"}]})
        assert off.json()["active"] is False
        assert scim.delete(f"/Groups/{group.json()['id']}").status_code == 204
        assert scim.delete(f"/Users/{uid}").status_code == 204
        assert scim.get(f"/Users/{uid}").status_code == 404

        run = admin.call("POST", "/admin/provisioning/reconcile")
        assert run.status_code == 200 and run.json()["outcome"] in ("ok", "drift"), run.text
        status = admin.call("GET", "/admin/provisioning").json()
        assert status["enabled"] is True and status["counts"]["requests_24h"] >= 7
        assert status["last_run"]["id"] == run.json()["id"]
    finally:
        scim.close()
        assert admin.call("DELETE", "/admin/provisioning/token").status_code == 204
