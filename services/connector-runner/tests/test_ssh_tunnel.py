"""SSH tunnels (ENH-003) against an in-process SSH server in front of a TCP echo service: key and password
sign-in, host key pinning checked before any password is sent, a refused forward, an unreachable server,
reconnecting on the same local port, and how each adapter keeps TLS checks on the real host name."""

from __future__ import annotations

import asyncio
import socket
from collections.abc import AsyncIterator
from typing import Any

import asyncssh
import pytest

from tela_common.errors import ErrorCode, TelaError
from tela_runner.adapters.base import ConnectionInfo
from tela_runner.adapters.mongodb import MongoAdapter
from tela_runner.adapters.mysql import MySqlAdapter
from tela_runner.adapters.postgres import PostgresAdapter
from tela_runner.adapters.sqlserver import SqlServerAdapter
from tela_runner.tunnel import SshTunnelError, TunnelManager, library_refs, target

USER = "tunnel"


class Bastion:
    """A minimal bastion: one user, a key or a password, TCP forwarding on or off."""

    def __init__(self, host_key: asyncssh.SSHKey, client_key: asyncssh.SSHKey | None = None,
                 password: str | None = None, forward: bool = True, user_ca: asyncssh.SSHKey | None = None,
                 host_cert: Any = None) -> None:
        self.host_key, self.client_key, self.password, self.forward = host_key, client_key, password, forward
        self.user_ca, self.host_cert = user_ca, host_cert
        self.forwards: list[tuple[str, int]] = []
        self.passwords_seen: list[str] = []
        self.connections: list[asyncssh.SSHServerConnection] = []
        self.acceptor: asyncssh.SSHAcceptor | None = None

    async def start(self, port: int = 0) -> int:
        bastion = self

        class Server(asyncssh.SSHServer):
            def connection_made(self, conn: asyncssh.SSHServerConnection) -> None:
                bastion.connections.append(conn)

            def begin_auth(self, username: str) -> bool:
                return True

            def public_key_auth_supported(self) -> bool:
                return bastion.client_key is not None or bastion.user_ca is not None

            def validate_ca_key(self, username: str, key: asyncssh.SSHKey) -> bool:
                return bastion.user_ca is not None and key.public_data == bastion.user_ca.public_data

            def validate_public_key(self, username: str, key: asyncssh.SSHKey) -> bool:
                return (bastion.client_key is not None and username == USER
                        and key.public_data == bastion.client_key.public_data)

            def password_auth_supported(self) -> bool:
                return bastion.password is not None

            def validate_password(self, username: str, password: str) -> bool:
                bastion.passwords_seen.append(password)
                return username == USER and password == bastion.password

            def connection_requested(self, dest_host: str, dest_port: int, orig_host: str, orig_port: int) -> bool:
                bastion.forwards.append((dest_host, dest_port))
                return bastion.forward

        keys = [(self.host_key, self.host_cert)] if self.host_cert else [self.host_key]
        self.acceptor = await asyncssh.listen("127.0.0.1", port, server_factory=Server, server_host_keys=keys)
        return int(self.acceptor.get_port())

    async def stop(self) -> None:
        for conn in self.connections:
            conn.close()
        if self.acceptor:
            self.acceptor.close()
            await self.acceptor.wait_closed()


@pytest.fixture
async def echo() -> AsyncIterator[int]:
    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        while data := await reader.read(1024):
            writer.write(data)
            await writer.drain()
        writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    yield server.sockets[0].getsockname()[1]
    server.close()


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


_EXPORTED: dict[int, str] = {}


def _openssh(key: asyncssh.SSHKey) -> str:
    """Export once: OpenSSH files carry random check bytes, and the secrets store always returns the same text."""
    return _EXPORTED.setdefault(id(key), key.export_private_key().decode())


def _info(target: int, ssh_port: int, *, key: asyncssh.SSHKey | None = None, password: str | None = None,
          pinned: str | None = None, engine: str = "postgres") -> ConnectionInfo:
    ssh: dict[str, Any] = {"host": "127.0.0.1", "port": ssh_port, "username": USER,
                           "auth": "key" if key else "password", "host_key": pinned}
    creds = {"username": "ro", "password": "db-pw", "ssh_private_key": _openssh(key) if key else "",
             "ssh_password": password or ""}
    return ConnectionInfo("c-1", engine, {"host": "127.0.0.1", "port": target, "database": "tpg", "ssh": ssh}, creds)


async def _roundtrip(port: int, payload: bytes = b"ping") -> bytes:
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(payload)
    await writer.drain()
    data = await reader.read(len(payload))
    writer.close()
    return data


async def test_key_tunnel_is_shared_and_reports_the_host_key(echo: int) -> None:
    host_key, client_key = asyncssh.generate_private_key("ssh-ed25519"), asyncssh.generate_private_key("ssh-ed25519")
    bastion = Bastion(host_key, client_key)
    port = await bastion.start()
    tunnels = TunnelManager()
    try:
        info, report = await tunnels.route(_info(echo, port, key=client_key))
        seen = report["host_key"]
        assert seen == host_key.get_fingerprint("sha256") and report["ok"]
        assert report["hops"] == [{"id": "", "host": "127.0.0.1", "port": port, "host_key": seen, "verified": "new"}]
        via = info.descriptor["via"]
        assert via["host"] == "127.0.0.1" and via["port"] != echo and info.descriptor["host"] == "127.0.0.1"
        assert await _roundtrip(via["port"]) == b"ping"
        # Pinning the key afterwards reuses the same tunnel (and so the same adapter pool).
        again, _ = await tunnels.route(_info(echo, port, key=client_key, pinned=seen))
        assert again.descriptor["via"] == via and len(bastion.connections) == 1
    finally:
        await tunnels.close()
        await bastion.stop()


async def test_host_key_mismatch_is_refused_before_the_password_is_sent(echo: int) -> None:
    bastion = Bastion(asyncssh.generate_private_key("ssh-ed25519"), password="ssh-pw")
    port = await bastion.start()
    tunnels = TunnelManager()
    other = asyncssh.generate_private_key("ssh-ed25519").get_fingerprint("sha256")
    try:
        with pytest.raises(SshTunnelError) as exc:
            await tunnels.route(_info(echo, port, password="ssh-pw", pinned=other))
        assert exc.value.stage == "host_key" and exc.value.code == ErrorCode.SOURCE_UNREACHABLE
        assert "different host key" in exc.value.message and exc.value.host_key == bastion.host_key.get_fingerprint()
        assert bastion.passwords_seen == []
        info, _ = await tunnels.route(_info(echo, port, password="ssh-pw",
                                            pinned=bastion.host_key.get_fingerprint("sha256")))
        assert await _roundtrip(info.descriptor["via"]["port"], b"pw") == b"pw"
        assert bastion.passwords_seen == ["ssh-pw"]
    finally:
        await tunnels.close()
        await bastion.stop()


async def test_sign_in_and_forward_failures(echo: int) -> None:
    client_key = asyncssh.generate_private_key("ssh-ed25519")
    bastion = Bastion(asyncssh.generate_private_key("ecdsa-sha2-nistp256"), client_key, password="ssh-pw")
    port = await bastion.start()
    tunnels = TunnelManager(connect_timeout_s=5)
    try:
        with pytest.raises(SshTunnelError) as wrong_key:
            await tunnels.route(_info(echo, port, key=asyncssh.generate_private_key("ssh-ed25519")))
        assert wrong_key.value.stage == "auth" and "rejected the user name or key" in wrong_key.value.message
        with pytest.raises(SshTunnelError) as wrong_pw:
            await tunnels.route(_info(echo, port, password="nope"))
        assert wrong_pw.value.stage == "auth" and "password" in wrong_pw.value.message
        # The SSH server cannot reach the database (nothing listens there).
        with pytest.raises(SshTunnelError) as closed:
            await tunnels.route(_info(_free_port(), port, key=client_key))
        assert closed.value.stage == "forward" and "allows TCP forwarding" in closed.value.message
        bastion.forward = False
        with pytest.raises(SshTunnelError) as refused:
            await tunnels.route(_info(echo, port, key=client_key))
        assert refused.value.stage == "forward"
        with pytest.raises(SshTunnelError) as gone:
            await tunnels.route(_info(echo, _free_port(), key=client_key))
        assert gone.value.stage == "connect" and "could not reach the SSH server" in gone.value.message
        assert gone.value.report()["ok"] is False
        with pytest.raises(SshTunnelError, match="Upload the key again"):
            await tunnels.route(_info(echo, port, key=client_key).__class__(
                "c-1", "postgres", {"host": "x", "ssh": {"host": "127.0.0.1", "port": port, "username": USER}}, {}))
        with pytest.raises(TelaError) as engine:
            await tunnels.route(_info(echo, port, key=client_key, engine="excel"))
        assert engine.value.code == ErrorCode.VALIDATION_ERROR
    finally:
        await tunnels.close()
        await bastion.stop()


async def test_a_dropped_tunnel_reopens_on_the_same_local_port(echo: int) -> None:
    host_key, client_key = asyncssh.generate_private_key("ssh-ed25519"), asyncssh.generate_private_key("ssh-ed25519")
    bastion = Bastion(host_key, client_key)
    port = await bastion.start()
    tunnels = TunnelManager()
    try:
        first, _ = await tunnels.route(_info(echo, port, key=client_key))
        for conn in bastion.connections:
            conn.close()
        for _ in range(50):  # the client notices the server hung up
            if not any(t.alive for t in tunnels._tunnels.values()):
                break
            await asyncio.sleep(0.05)
        second, _ = await tunnels.route(_info(echo, port, key=client_key))
        assert second.descriptor["via"] == first.descriptor["via"] and len(bastion.connections) == 2
        assert await _roundtrip(second.descriptor["via"]["port"]) == b"ping"
    finally:
        await tunnels.close()
        await bastion.stop()


def test_adapters_connect_to_the_tunnel_but_check_tls_for_the_real_host() -> None:
    via = {"via": {"host": "127.0.0.1", "port": 40001}}
    pg = PostgresAdapter()
    base = {"host": "db.internal", "port": 5432, "database": "tpg", "tls": {"mode": "verify-full"}}
    conninfo = pg._conninfo(ConnectionInfo("c", "postgres", {**base, **via}, {"username": "u", "password": "p"}))
    assert "host=db.internal" in conninfo and "hostaddr=127.0.0.1" in conninfo and "port=40001" in conninfo
    assert "hostaddr" not in pg._conninfo(ConnectionInfo("c", "postgres", base, {"username": "u", "password": "p"}))

    my = MySqlAdapter()
    ctx = my._ssl(ConnectionInfo("c", "mysql", {**base, **via}, {}))
    assert ctx is not None and ctx.check_hostname and getattr(ctx, "server_name", "") == "db.internal"

    ms = SqlServerAdapter()
    attrs = ms._base_attrs(ConnectionInfo("c", "sqlserver", {**base, "port": 1433, **via}, {}))
    assert "Server={127.0.0.1,40001}" in attrs and "HostNameInCertificate={db.internal}" in attrs
    assert not any(a.startswith("HostNameInCertificate") for a in
                   ms._base_attrs(ConnectionInfo("c", "sqlserver", {**base, "port": 1433}, {})))


async def test_jump_chain_with_a_library_key_and_a_password(echo: int) -> None:
    """runner → jump (library key) → SSH server (password) → database; each hop pinned separately."""
    jump_host_key, server_host_key = (asyncssh.generate_private_key("ssh-ed25519") for _ in range(2))
    lib_key = asyncssh.generate_private_key("ssh-ed25519")
    jump = Bastion(jump_host_key, lib_key)
    server = Bastion(server_host_key, password="ssh-pw")
    jump_port, server_port = await jump.start(), await server.start()
    key_id = "0192f3b2-0000-7000-8000-00000000beef"
    jumps = [{"id": "j1", "host": "127.0.0.1", "port": jump_port, "username": USER, "auth": "library",
              "key_id": key_id}]
    info = _info(echo, server_port, password="ssh-pw")
    info.descriptor["ssh"]["jump"] = jumps
    assert library_refs("t-1", info.descriptor) == {key_id: f"tenants/t-1/ssh_keys/{key_id}"}
    info.credentials[f"lib_{key_id}_private_key"] = _openssh(lib_key)
    tunnels = TunnelManager()
    try:
        routed, report = await tunnels.route(info)
        assert await _roundtrip(routed.descriptor["via"]["port"]) == b"ping"
        # The SSH server was reached through the jump host, and only it forwarded to the database.
        assert jump.forwards == [("127.0.0.1", server_port)] and ("127.0.0.1", echo) in server.forwards
        assert [h["id"] for h in report["hops"]] == ["j1", ""]
        assert report["hops"][0]["host_key"] == jump_host_key.get_fingerprint()
        assert report["host_key"] == server_host_key.get_fingerprint()
        # A wrong pin on the jump host stops the chain there, before the SSH server sees a password.
        await tunnels.close()
        server.passwords_seen.clear()
        info.descriptor["ssh"]["jump"] = [{**jumps[0], "host_key": server_host_key.get_fingerprint()}]
        with pytest.raises(SshTunnelError) as exc:
            await tunnels.route(info)
        assert exc.value.stage == "host_key" and exc.value.hop == "j1" and "jump host" in exc.value.message
        assert server.passwords_seen == []
        info.descriptor["ssh"]["jump"] = jumps
        del info.credentials[f"lib_{key_id}_private_key"]
        with pytest.raises(SshTunnelError, match="missing from the secrets store") as missing:
            await tunnels.route(info)
        assert missing.value.hop == "j1"
    finally:
        await tunnels.close()
        await jump.stop()
        await server.stop()


async def test_user_certificate_and_host_ca(echo: int) -> None:
    user_ca, host_ca = asyncssh.generate_private_key("ssh-ed25519"), asyncssh.generate_private_key("ssh-ed25519")
    host_key, client_key = asyncssh.generate_private_key("ssh-ed25519"), asyncssh.generate_private_key("ssh-ed25519")
    host_cert = host_ca.generate_host_certificate(host_key, "bastion", principals=["127.0.0.1"])
    user_cert = user_ca.generate_user_certificate(client_key, "tela", principals=[USER])
    bastion = Bastion(host_key, user_ca=user_ca, host_cert=host_cert)  # knows no client key: the CA vouches
    port = await bastion.start()
    info = _info(echo, port, key=client_key)
    info.credentials["ssh_certificate"] = user_cert.export_certificate().decode()
    ca_line = host_ca.export_public_key().decode().strip()
    tunnels = TunnelManager()
    try:
        info.descriptor["ssh"].update(verify="ca", host_ca=ca_line)
        routed, report = await tunnels.route(info)
        assert await _roundtrip(routed.descriptor["via"]["port"]) == b"ping"
        assert report["hops"][0]["verified"] == "ca"
        await tunnels.close()
        # Signed by another CA: refused during the handshake.
        info.descriptor["ssh"]["host_ca"] = asyncssh.generate_private_key("ssh-ed25519").export_public_key().decode()
        with pytest.raises(SshTunnelError, match="host certificate signed by the trusted CA") as wrong_ca:
            await tunnels.route(info)
        assert wrong_ca.value.stage == "host_key"
        # Without the certificate the bastion does not know the key.
        info.descriptor["ssh"].update(verify="pin", host_ca=None)
        del info.credentials["ssh_certificate"]
        with pytest.raises(SshTunnelError) as no_cert:
            await tunnels.route(info)
        assert no_cert.value.stage == "auth"
    finally:
        await tunnels.close()
        await bastion.stop()


def test_mongodb_through_a_tunnel() -> None:
    info = ConnectionInfo("m", "mongodb", {"uri": "mongodb://mongo.internal:27018/tpg?replicaSet=rs0&authSource=admin",
                                           "database": "tpg", "tls": {"mode": "verify-full"}}, {"username": "ro"})
    assert target(info) == ("mongo.internal", 27018)
    opts = MongoAdapter()._options(replace_descriptor(info, via={"host": "127.0.0.1", "port": 40002}))
    assert opts["host"] == "mongodb://127.0.0.1:40002/tpg?authSource=admin"
    assert opts["directConnection"] is True and opts["tls"] is True and opts["tlsAllowInvalidHostnames"] is True
    assert "tlsAllowInvalidHostnames" not in MongoAdapter()._options(info)
    for uri in ("mongodb+srv://cluster.example.com/tpg", "mongodb://a:27017,b:27017/tpg"):
        with pytest.raises(TelaError, match="MongoDB through SSH"):
            target(ConnectionInfo("m", "mongodb", {"uri": uri}, {}))


def replace_descriptor(info: ConnectionInfo, **extra: Any) -> ConnectionInfo:
    return ConnectionInfo(info.connection_id, info.engine, {**info.descriptor, **extra}, info.credentials)
