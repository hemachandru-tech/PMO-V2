"""Shared SSH key library, jump chains, certificates and host CAs (ENH-004), end to end: API → secrets store →
runner → in-process SSH servers → the test PostgreSQL.

A library key with an OpenSSH user certificate signs in to a jump host; the SSH server behind it trusts a host CA
instead of a pinned key. Rotating the library key sends the connection back to draft; a key in use cannot be
deleted; the key and certificate never leave the secrets store.
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


class Server:
    """An SSH server that trusts one user CA, shows a host certificate (or a bare key) and logs forwards."""

    def __init__(self, host_key: asyncssh.SSHKey, user_ca: asyncssh.SSHKey, host_cert: Any = None) -> None:
        self.host_key, self.user_ca, self.host_cert = host_key, user_ca, host_cert
        self.forwards: list[tuple[str, int]] = []
        self.conns: list[asyncssh.SSHServerConnection] = []
        self.acceptor: asyncssh.SSHAcceptor | None = None

    async def start(self) -> int:
        server = self

        class Handler(asyncssh.SSHServer):
            def connection_made(self, conn: asyncssh.SSHServerConnection) -> None:
                server.conns.append(conn)

            def begin_auth(self, username: str) -> bool:
                return True

            def public_key_auth_supported(self) -> bool:
                return True

            def validate_ca_key(self, username: str, key: asyncssh.SSHKey) -> bool:
                return key.public_data == server.user_ca.public_data

            def connection_requested(self, dest_host: str, dest_port: int, *_: Any) -> bool:
                server.forwards.append((dest_host, dest_port))
                return True

        keys = [(self.host_key, self.host_cert)] if self.host_cert else [self.host_key]
        self.acceptor = await asyncssh.listen("127.0.0.1", 0, server_factory=Handler, server_host_keys=keys)
        return int(self.acceptor.get_port())

    async def stop(self) -> None:
        for conn in self.conns:  # the runner keeps its tunnels open; end them so the listener can close
            conn.close()
        if self.acceptor:
            self.acceptor.close()
            await self.acceptor.wait_closed()


@pytest.fixture
async def servers() -> AsyncIterator[dict[str, Any]]:
    user_ca, host_ca = asyncssh.generate_private_key("ssh-ed25519"), asyncssh.generate_private_key("ssh-ed25519")
    jump = Server(asyncssh.generate_private_key("ssh-ed25519"), user_ca)
    inner_key = asyncssh.generate_private_key("ssh-ed25519")
    inner = Server(inner_key, user_ca, host_ca.generate_host_certificate(inner_key, "inner", principals=["127.0.0.1"]))
    out = {"user_ca": user_ca, "host_ca": host_ca, "jump": jump, "inner": inner,
           "jump_port": await jump.start(), "inner_port": await inner.start()}
    yield out
    await jump.stop()
    await inner.stop()


@pytest.fixture
async def admin(stack: httpx.AsyncClient, owner_conn: psycopg.Connection, make_token: Callable[..., str]) -> str:
    bootstrap(owner_conn, "acme", ISSUER, {"TELA-Admins": "admin"})
    return make_token("admin-1", groups=["TELA-Admins"])


def _key_with_cert(user_ca: asyncssh.SSHKey) -> tuple[str, str]:
    key = asyncssh.generate_private_key("ssh-ed25519")
    cert = user_ca.generate_user_certificate(key, "tela", principals=["tunnel"])
    return key.export_private_key().decode(), cert.export_certificate().decode()


def _secret(secrets_dir: Path, name: str) -> dict[str, str]:
    for f in secrets_dir.iterdir():
        if name in f.name:
            return dict(json.loads(f.read_text()))
    raise AssertionError("no secret written")


async def test_library_key_jump_chain_and_host_ca(stack: httpx.AsyncClient, admin: str, owner_conn: psycopg.Connection,
                                                  tpg_source: dict[str, Any], secrets_dir: Path,
                                                  servers: dict[str, Any]) -> None:
    key_text, cert_text = _key_with_cert(servers["user_ca"])
    other_text, _ = _key_with_cert(servers["user_ca"])

    # The library refuses a certificate issued for another key, then takes the key with its own certificate.
    bad = await stack.post("/api/v1/admin/ssh-keys", headers=h(admin), json={
        "name": "Ops bastion", "private_key": other_text, "certificate": cert_text})
    assert bad.status_code == 400 and bad.json()["error"]["details"]["field"] == "certificate"
    made = await stack.post("/api/v1/admin/ssh-keys", headers=h(admin), json={
        "name": "Ops bastion", "private_key": key_text, "certificate": cert_text})
    assert made.status_code == 201, made.text
    key = made.json()
    assert key["certificate"]["principals"] == ["tunnel"] and key["used_by"] == []
    assert "PRIVATE" not in made.text and "cert-v01" not in made.text
    stored = _secret(secrets_dir, f"ssh_keys__{key['id']}")
    assert stored["private_key"].startswith("-----BEGIN OPENSSH PRIVATE KEY-----") and "cert-v01" in stored["certificate"]
    dup = await stack.post("/api/v1/admin/ssh-keys", headers=h(admin), json={"name": "ops BASTION", "private_key": key_text})
    assert dup.status_code == 409

    # runner → jump host (library key + certificate, pinned) → SSH server (same key, host CA) → PostgreSQL.
    login, password = tpg_source["ro"]
    host_ca = servers["host_ca"].export_public_key().decode().strip()
    body = {"name": "TPG via two hops", "engine": "postgres", "host": tpg_source["host"], "port": tpg_source["port"],
            "database": tpg_source["database"], "username": login, "password": password, "tls": {"mode": "disable"},
            "approved_schemas": ["public"],
            "ssh": {"host": "127.0.0.1", "port": servers["inner_port"], "username": "tunnel", "auth": "key",
                    "key_source": "library", "key_id": key["id"], "verify": "ca", "host_ca": f"@cert-authority * {host_ca}",
                    "jump": [{"host": "127.0.0.1", "port": servers["jump_port"], "username": "tunnel",
                              "auth": "library", "key_id": key["id"]}]}}
    created = await stack.post("/api/v1/connections", headers=h(admin), json=body)
    assert created.status_code == 201, created.text
    cid = created.json()["connection_id"]
    ssh = created.json()["descriptor"]["ssh"]
    assert ssh["verify"] == "ca" and ssh["host_ca"] == host_ca and ssh["key_id"] == key["id"]
    assert ssh["jump"][0]["key_fingerprint"] == key["fingerprint"] and ssh["jump"][0]["host_key"] is None

    tested = (await stack.post(f"/api/v1/connections/{cid}/test", headers=h(admin))).json()
    assert tested["status"] == "connected", tested
    assert [hop["verified"] for hop in tested["ssh"]["hops"]] == ["new", "ca"]
    assert tested["ssh"]["pinned_hops"] == [ssh["jump"][0]["id"]]  # the jump host is pinned, the CA hop is not
    assert servers["jump"].forwards == [("127.0.0.1", servers["inner_port"])]
    assert servers["inner"].forwards[-1] == (tpg_source["host"], tpg_source["port"])
    saved = (await stack.get(f"/api/v1/connections/{cid}", headers=h(admin))).json()["descriptor"]["ssh"]
    assert saved["jump"][0]["host_key"] == servers["jump"].host_key.get_fingerprint() and saved["host_key"] is None
    assert (await stack.post(f"/api/v1/connections/{cid}/introspect", headers=h(admin))).status_code == 202

    listed = (await stack.get("/api/v1/admin/ssh-keys", headers=h(admin))).json()["items"]
    assert listed[0]["used_by"] == [{"id": cid, "name": "TPG via two hops", "status": "connected"}]
    refused = await stack.delete(f"/api/v1/admin/ssh-keys/{key['id']}", headers=h(admin))
    assert refused.status_code == 409 and "TPG via two hops" in refused.text

    # Rotation: a new key (and its certificate) puts the connection back to draft until it passes a test.
    new_key, new_cert = _key_with_cert(servers["user_ca"])
    rotated = await stack.patch(f"/api/v1/admin/ssh-keys/{key['id']}", headers=h(admin),
                                json={"private_key": new_key, "certificate": new_cert})
    assert rotated.status_code == 200 and rotated.json()["fingerprint"] != key["fingerprint"]
    assert (await stack.get(f"/api/v1/connections/{cid}", headers=h(admin))).json()["status"] == "draft"
    again = (await stack.post(f"/api/v1/connections/{cid}/test", headers=h(admin))).json()
    assert again["status"] == "connected", again
    events = owner_conn.execute("SELECT string_agg(details::text, ' ') FROM ops.audit_events WHERE object_type = "
                                "'ssh_key'").fetchone()
    assert events and "PRIVATE" not in events[0] and '"connections_to_retest": 1' in events[0]

    # A wrong CA stops the chain at the SSH server; the message names the step.
    etag = (await stack.get(f"/api/v1/connections/{cid}", headers=h(admin))).json()["etag"]
    wrong = asyncssh.generate_private_key("ssh-ed25519").export_public_key().decode()
    patched = await stack.patch(f"/api/v1/connections/{cid}", headers=h(admin, **{"If-Match": etag}),
                                json={"ssh": {**body["ssh"], "host_ca": wrong,
                                              "jump": [{**body["ssh"]["jump"][0], "id": saved["jump"][0]["id"]}]}})
    assert patched.status_code == 200 and patched.json()["retest_required"] is True
    assert patched.json()["descriptor"]["ssh"]["jump"][0]["host_key"] == saved["jump"][0]["host_key"]  # kept
    failed = (await stack.post(f"/api/v1/connections/{cid}/test", headers=h(admin))).json()
    assert failed["ssh"]["stage"] == "host_key" and failed["ssh"]["hop"] == "" and "trusted CA" in failed["message"]

    # Jump host with a password: blank keeps it on later edits; removing the hop clears it.
    etag = (await stack.get(f"/api/v1/connections/{cid}", headers=h(admin))).json()["etag"]
    no_pw = await stack.patch(f"/api/v1/connections/{cid}", headers=h(admin, **{"If-Match": etag}), json={
        "ssh": {"jump": [{"host": "127.0.0.1", "port": servers["jump_port"], "username": "tunnel", "auth": "password"}]}})
    assert no_pw.status_code == 400 and no_pw.json()["error"]["details"]["field"] == "ssh.jump.0.password"
    pw = await stack.patch(f"/api/v1/connections/{cid}", headers=h(admin, **{"If-Match": etag}), json={
        "ssh": {"jump": [{"host": "127.0.0.1", "port": servers["jump_port"], "username": "tunnel", "auth": "password",
                          "password": "jump-pw"}]}})
    hop_id = pw.json()["descriptor"]["ssh"]["jump"][0]["id"]
    assert _secret(secrets_dir, cid)[f"ssh_jump_{hop_id}_password"] == "jump-pw"
    gone = await stack.patch(f"/api/v1/connections/{cid}", headers=h(admin, **{"If-Match": pw.json()["etag"]}),
                             json={"ssh": {"jump": []}})
    assert "jump" not in gone.json()["descriptor"]["ssh"] and _secret(secrets_dir, cid)[f"ssh_jump_{hop_id}_password"] == ""

    # No longer used: the key can be deleted, and its secret goes with it.
    etag = gone.json()["etag"]
    await stack.patch(f"/api/v1/connections/{cid}", headers=h(admin, **{"If-Match": etag}),
                      json={"ssh": {"enabled": False}})
    assert (await stack.delete(f"/api/v1/admin/ssh-keys/{key['id']}", headers=h(admin))).status_code == 204
    assert not any(f"ssh_keys__{key['id']}" in f.name for f in secrets_dir.iterdir())
    assert (await stack.get("/api/v1/admin/ssh-keys", headers=h(admin))).json()["items"] == []
