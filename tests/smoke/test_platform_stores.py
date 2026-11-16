"""Secrets and object storage on the running Compose stack (US-007, US-008).

The API signs in to OpenBao with an identity that may write and patch credentials but never read them, and the
runner with one that may only read them. Changing one field of a stored credential must therefore work as a merge
patch: the runner then signs in to the source with the old username and the new password. Readiness reports the
secrets store and object storage.

    TELA_SMOKE=1 uv run pytest -q tests/smoke/test_platform_stores.py
"""

from __future__ import annotations

import pytest
from test_p1_read_path import Api, _pw, apis  # noqa: F401 - shared fixture

pytestmark = pytest.mark.skipif(
    not __import__("os").environ.get("TELA_SMOKE"), reason="set TELA_SMOKE=1 with the stack running")

NAME = "Credential patch probe (dev)"


def test_password_change_is_a_merge_patch_the_runner_can_read(apis: dict[str, Api]) -> None:
    admin = apis["admin"]
    for c in admin.call("GET", "/connections").json()["items"]:
        if c["name"] == NAME:
            assert admin.call("DELETE", f"/connections/{c['id']}").status_code == 204

    resp = admin.call("POST", "/connections", json={
        "name": NAME, "engine": "postgres", "host": "sample-tpg", "database": "tpg", "username": "tela_ro",
        "password": "not-the-password", "tls": {"mode": "disable"}})
    assert resp.status_code == 201, resp.text
    cid = resp.json()["connection_id"]
    try:
        assert admin.call("POST", f"/connections/{cid}/test").json()["status"] != "connected"

        etag = admin.call("GET", f"/connections/{cid}").headers["etag"]
        patched = admin.call("PATCH", f"/connections/{cid}", headers={"If-Match": etag},
                             json={"password": _pw("sample_tpg_ro_password")})
        assert patched.status_code == 200, patched.text

        test = admin.call("POST", f"/connections/{cid}/test").json()
        assert test["status"] == "connected", test  # username kept, password replaced, read by the runner
    finally:
        assert admin.call("DELETE", f"/connections/{cid}").status_code == 204  # the API may delete, too


def test_readiness_reports_both_stores() -> None:
    import subprocess

    out = subprocess.run(  # /readyz is not exposed at the edge
        ["docker", "exec", "tela-api-1", "python", "-c",  # noqa: S607
         "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/readyz').read().decode())"],
        capture_output=True, text=True, check=True).stdout
    assert '"secrets":"ok"' in out and '"storage":"ok"' in out, out
