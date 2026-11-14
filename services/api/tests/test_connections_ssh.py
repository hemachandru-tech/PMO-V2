"""Connections through an SSH tunnel (ENH-003), end to end: API → secrets store → runner → in-process SSH server
→ the test PostgreSQL.

A PuTTY .ppk key with a passphrase is uploaded, normalised and kept in the secrets store only; the first passing
test pins the SSH server's host key; a rebuilt server (new host key) is refused until an admin re-pins it; the
schema is read through the tunnel; turning the tunnel off clears the key from the secret.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

import asyncssh
import httpx
import psycopg
import pytest
from api_testkit import ISSUER, bootstrap, needs_db
from pmo_testkit import h

pytestmark = [needs_db, pytest.mark.integration]

KEYS = Path(__file__).parents[3] / "packages" / "tela-common" / "tests" / "fixtures" / "ssh"
PPK = KEYS / "ed25519-v3-passphrase.ppk"
FINGERPRINT = json.loads((KEYS / "fingerprints.json").read_text())[PPK.name]


class Bastion:
    def __init__(self) -> None:
        self.acceptor: asyncssh.SSHAcceptor | None = None
        self.conns: list[asyncssh.SSHServerConnection] = []

    async def start(self, host_key: asyncssh.SSHKey, port: int = 0) -> int:
        bastion = self

        class Server(asyncssh.SSHServer):
            def connection_made(self, conn: asyncssh.SSHServerConnection) -> None:
                bastion.conns.append(conn)

            def begin_auth(self, username: str) -> bool:
                return True

            def public_key_auth_supported(self) -> bool:
                return True

            def validate_public_key(self, username: str, key: asyncssh.SSHKey) -> bool:
                return username == "tunnel" and key.get_fingerprint("sha256") == FINGERPRINT

            def connection_requested(self, *_: Any) -> bool:
                return True

        self.acceptor = await asyncssh.listen("127.0.0.1", port, server_factory=Server, server_host_keys=[host_key])
        return int(self.acceptor.get_port())

    async def stop(self) -> None:
        for conn in self.conns:
            conn.close()
        self.conns.clear()
        if self.acceptor:
            self.acceptor.close()
            await self.acceptor.wait_closed()


@pytest.fixture
async def bastion() -> AsyncIterator[Bastion]:
    b = Bastion()
    yield b
    await b.stop()


@pytest.fixture
async def admin(stack: httpx.AsyncClient, owner_conn: psycopg.Connection, make_token: Callable[..., str]) -> str:
    bootstrap(owner_conn, "acme", ISSUER, {"TELA-Admins": "admin"})
    return make_token("admin-1", groups=["TELA-Admins"])


def _secret(secrets_dir: Path, cid: str) -> dict[str, str]:
    for f in secrets_dir.rglob("*"):
        if f.is_file() and cid in str(f):
            return dict(json.loads(f.read_text()))
    raise AssertionError("no secret written")


async def test_ssh_tunnel_end_to_end(stack: httpx.AsyncClient, admin: str, owner_conn: psycopg.Connection,
                                     tpg_source: dict[str, Any], secrets_dir: Path, bastion: Bastion) -> None:
    host_key = asyncssh.generate_private_key("ssh-ed25519")
    ssh_port = await bastion.start(host_key)
    login, password = tpg_source["ro"]
    body = {"name": "TPG via bastion", "engine": "postgres", "host": tpg_source["host"], "port": tpg_source["port"],
            "database": tpg_source["database"], "username": login, "password": password, "tls": {"mode": "disable"},
            "approved_schemas": ["public"],
            "ssh": {"host": "127.0.0.1", "port": ssh_port, "username": "tunnel", "auth": "key",
                    "private_key": PPK.read_text()}}

    # Key problems come back under the field that needs fixing.
    no_pass = await stack.post("/api/v1/connections", headers=h(admin), json=body)
    assert no_pass.status_code == 400 and no_pass.json()["error"]["details"]["field"] == "ssh.passphrase"
    public = await stack.post("/api/v1/connections", headers=h(admin), json={
        **body, "ssh": {**body["ssh"], "private_key": "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAI tela"}})
    assert public.json()["error"]["details"]["field"] == "ssh.private_key" and "public key" in public.text
    mongo = await stack.post("/api/v1/connections", headers=h(admin), json={
        "name": "Mongo", "engine": "mongodb", "uri": "mongodb+srv://cluster.example.com", "database": "y",
        "username": "u", "password": "p", "ssh": body["ssh"]})
    assert mongo.status_code == 400 and mongo.json()["error"]["details"]["field"] == "uri"  # one host only

    body["ssh"]["passphrase"] = "tela-test"
    created = await stack.post("/api/v1/connections", headers=h(admin), json=body)
    assert created.status_code == 201, created.text
    cid = created.json()["connection_id"]
    ssh = created.json()["descriptor"]["ssh"]
    assert ssh["key_fingerprint"] == FINGERPRINT and ssh["key_type"] == "ssh-ed25519" and ssh["host_key"] is None
    # The key lives only in the secrets store, unencrypted OpenSSH; never in the database, response or audit.
    secret = _secret(secrets_dir, cid)
    assert secret["ssh_private_key"].startswith("-----BEGIN OPENSSH PRIVATE KEY-----")
    stored = owner_conn.execute("SELECT descriptor::text FROM conn.connections WHERE id = %s", (cid,)).fetchone()
    audit = owner_conn.execute("SELECT string_agg(details::text, ' ') FROM ops.audit_events WHERE object_id = %s",
                               (cid,)).fetchone()
    for text in (created.text, stored[0] if stored else "", audit[0] if audit else ""):
        assert "PRIVATE KEY" not in text and "PuTTY" not in text and "tela-test" not in text

    # First passing test pins the SSH server's host key.
    tested = (await stack.post(f"/api/v1/connections/{cid}/test", headers=h(admin))).json()
    assert tested["status"] == "connected", tested
    assert tested["ssh"]["ok"] and tested["ssh"]["pinned"] and tested["ssh"]["host_key"] == host_key.get_fingerprint()
    conn = (await stack.get(f"/api/v1/connections/{cid}", headers=h(admin))).json()
    assert conn["descriptor"]["ssh"]["host_key"] == host_key.get_fingerprint()
    # The schema is read through the tunnel.
    read = await stack.post(f"/api/v1/connections/{cid}/introspect", headers=h(admin))
    assert read.status_code == 202, read.text
    objects = (await stack.get(f"/api/v1/connections/{cid}/objects", headers=h(admin))).json()["items"]
    assert any(o["object"].endswith("projects") for o in objects)

    # The server is rebuilt with a new host key on the same port: refused until re-pinned.
    await bastion.stop()
    new_key = asyncssh.generate_private_key("ssh-ed25519")
    await bastion.start(new_key, ssh_port)
    refused = (await stack.post(f"/api/v1/connections/{cid}/test", headers=h(admin))).json()
    assert refused["status"] == "error" and not refused["reachable"]
    assert refused["ssh"]["stage"] == "host_key" and "different host key" in refused["message"]
    etag = (await stack.get(f"/api/v1/connections/{cid}", headers=h(admin))).json()["etag"]
    repin = await stack.patch(f"/api/v1/connections/{cid}", headers=h(admin, **{"If-Match": etag}),
                              json={"ssh": {"repin": True}})
    assert repin.status_code == 200 and repin.json()["retest_required"] is True
    assert repin.json()["descriptor"]["ssh"]["host_key"] is None
    assert repin.json()["descriptor"]["ssh"]["key_fingerprint"] == FINGERPRINT  # the saved key is kept
    again = (await stack.post(f"/api/v1/connections/{cid}/test", headers=h(admin))).json()
    assert again["status"] == "connected" and again["ssh"]["host_key"] == new_key.get_fingerprint()

    # The form sends the saved tunnel back unchanged when only the name is edited: no re-test needed.
    etag = (await stack.get(f"/api/v1/connections/{cid}", headers=h(admin))).json()["etag"]
    renamed = await stack.patch(f"/api/v1/connections/{cid}", headers=h(admin, **{"If-Match": etag}), json={
        "name": "TPG via bastion 2", "ssh": {"host": "127.0.0.1", "port": ssh_port, "username": "tunnel", "auth": "key"}})
    assert renamed.status_code == 200 and renamed.json()["retest_required"] is False
    assert renamed.json()["status"] == "connected"

    # Turning the tunnel off clears the key from the secret.
    etag = renamed.json()["etag"]
    off = await stack.patch(f"/api/v1/connections/{cid}", headers=h(admin, **{"If-Match": etag}),
                            json={"ssh": {"enabled": False}})
    assert off.status_code == 200 and "ssh" not in off.json()["descriptor"]
    assert _secret(secrets_dir, cid)["ssh_private_key"] == ""
    assert (await stack.post(f"/api/v1/connections/{cid}/test", headers=h(admin))).json()["status"] == "connected"
