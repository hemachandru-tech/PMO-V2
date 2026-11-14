"""SSH tunnels to sources behind a bastion (ENH-003, ENH-004).

A connection whose descriptor has an ``ssh`` block is reached through a local port forward: the runner signs
in to the SSH server with the key (or password) from the secrets store, forwards ``127.0.0.1:<port>`` to the
database host as the SSH server sees it, and the adapter connects to that port. The descriptor gains
``via = {host, port}``; adapters keep the real host name for TLS (libpq ``hostaddr``, MySQL SNI, ODBC
``HostNameInCertificate``), so certificate checks are unchanged. MongoDB (one host only) skips just the
certificate's name match, as pymongo cannot send another name.

Jump chains: ``ssh.jump`` lists the hosts before the SSH server, in order (like OpenSSH ``-J``); each hop
connects through the previous one and has its own user, key (from the library) or password, and host check.
Keys come from the connection's own secret (an upload) or from the shared library (``tenants/<t>/ssh_keys/<id>``,
fetched by the caller and passed in as ``lib_<id>_private_key`` / ``lib_<id>_certificate``). A key may carry an
OpenSSH user certificate.

Host checks happen during each handshake, before any password is sent: the hop's pinned SHA256 fingerprint
must match, or, with ``verify = "ca"``, the server must show a host certificate signed by the hop's trusted
CA for that host name. With no pin yet, any key is accepted and reported back so the API can pin it on the
first passing test (trust on first use, by an admin). Nothing reads ``~/.ssh``.

Tunnels are shared per connection and credential set, kept alive with SSH keepalives, closed after
``idle_s`` without use, and re-opened on the same local port when possible so adapter pools stay valid.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import re
import time
from dataclasses import dataclass, replace
from typing import Any

import asyncssh
from asyncssh.public_key import get_default_certificate_algs, get_default_public_key_algs

from tela_common.errors import ErrorCode, TelaError
from tela_common.logging import get_logger
from tela_common.sshkeys import mongo_tunnel_target
from tela_runner.adapters.base import ConnectionInfo

SSH_ENGINES = ("postgres", "mysql", "sqlserver", "mongodb")
DEFAULT_PORTS = {"postgres": 5432, "mysql": 3306, "sqlserver": 1433}
MAX_JUMPS = 3
_KEY_ID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_HOP_ID = re.compile(r"^[a-z0-9]{1,16}$")
_PIN_FIELDS = ("host_key", "host_key_pinned")
log = get_logger(__name__)
# A pinned hop asks only for plain host keys (a server with a host certificate still has the key behind it); a
# CA-checked hop asks only for host certificates, so it can never fall back to an unchecked bare key.
PLAIN_HOST_KEY_ALGS = [a.decode() for a in get_default_public_key_algs()]
CERT_HOST_KEY_ALGS = [a.decode() for a in get_default_certificate_algs()]


class SshTunnelError(TelaError):
    """The tunnel could not be opened; ``stage`` says where (connect, host_key, auth, forward), ``hop`` which hop
    ("" for the SSH server itself, a jump host's id otherwise)."""

    def __init__(self, stage: str, message: str, host_key: str | None = None, hop: str = "",
                 hops: list[dict[str, Any]] | None = None) -> None:
        super().__init__(ErrorCode.SOURCE_UNREACHABLE, message, {"ssh_stage": stage})
        self.stage, self.host_key, self.hop, self.hops = stage, host_key, hop, hops or []

    def report(self) -> dict[str, Any]:
        return {"ok": False, "stage": self.stage, "error": self.message, "host_key": self.host_key, "hop": self.hop,
                "hops": self.hops}


def target(info: ConnectionInfo) -> tuple[str, int]:
    """Where the last SSH server forwards to: the database as that server sees it."""
    d = info.descriptor
    if info.engine == "mongodb":
        try:
            return mongo_tunnel_target(str(d.get("uri") or ""))
        except ValueError as exc:
            raise TelaError(ErrorCode.VALIDATION_ERROR, f"MongoDB through SSH: {exc}.") from exc
    return str(d["host"]), int(d.get("port") or DEFAULT_PORTS[info.engine])


def hops(descriptor: dict[str, Any]) -> list[dict[str, Any]]:
    """The chain in connection order: jump hosts first, then the SSH server (id "")."""
    ssh = descriptor.get("ssh") or {}
    jumps = list(ssh.get("jump") or [])
    if len(jumps) > MAX_JUMPS:
        raise TelaError(ErrorCode.VALIDATION_ERROR, f"Use at most {MAX_JUMPS} jump hosts.")
    for j in jumps:
        if not _HOP_ID.match(str(j.get("id") or "")):
            raise TelaError(ErrorCode.SIGNATURE_INVALID, "bad jump host id")
    main = {k: v for k, v in ssh.items() if k != "jump"}
    return [*jumps, {**main, "id": ""}]


def _uses_key(hop: dict[str, Any]) -> bool:
    """Jump hosts sign in with a library key or a password; the SSH server with a key (uploaded or from the
    library) or a password."""
    return hop.get("auth") == "library" if hop.get("id") else hop.get("auth", "key") == "key"


def _library_id(hop: dict[str, Any]) -> str | None:
    uses = _uses_key(hop) and (bool(hop.get("id")) or hop.get("key_source") == "library")
    if not uses:
        return None
    key_id = str(hop.get("key_id") or "")
    if not _KEY_ID.match(key_id):
        raise TelaError(ErrorCode.SIGNATURE_INVALID, "bad SSH key id")
    return key_id


def library_refs(tenant_id: str, descriptor: dict[str, Any]) -> dict[str, str]:
    """Secret references of the library keys the chain uses, inside the signed tenant only."""
    out: dict[str, str] = {}
    for hop in hops(descriptor):
        key_id = _library_id(hop)
        if key_id:
            out[key_id] = f"tenants/{tenant_id}/ssh_keys/{key_id}"
    return out


def hop_secret(hop: dict[str, Any], creds: dict[str, str]) -> tuple[str, str, str]:
    """(private key, certificate, password) for one hop; blanks where unused."""
    key_id = _library_id(hop)
    if key_id:
        return creds.get(f"lib_{key_id}_private_key", ""), creds.get(f"lib_{key_id}_certificate", ""), ""
    if hop.get("id"):
        return "", "", creds.get(f"ssh_jump_{hop['id']}_password", "")
    if hop.get("auth", "key") == "password":
        return "", "", creds.get("ssh_password", "")
    return creds.get("ssh_private_key", ""), creds.get("ssh_certificate", ""), ""


class _Client(asyncssh.SSHClient):
    def __init__(self, pinned: str | None, ca: bool) -> None:
        self.pinned, self.ca = pinned, ca
        self.seen: str | None = None

    def validate_host_public_key(self, host: str, addr: str, port: int, key: asyncssh.SSHKey) -> bool:
        self.seen = key.get_fingerprint("sha256")
        if self.ca:  # a CA-checked hop needs a host certificate; a bare key is refused
            return False
        return self.pinned is None or hmac.compare_digest(self.seen, self.pinned)


@dataclass
class _Tunnel:
    conns: list[asyncssh.SSHClientConnection]
    listener: asyncssh.SSHListener
    port: int
    seen: list[dict[str, Any]]  # per hop: id, host, port, host_key, verified
    last_used: float

    @property
    def alive(self) -> bool:
        return all(not c.is_closed() for c in self.conns)

    @property
    def host_key(self) -> str:
        return str(self.seen[-1]["host_key"] or "")

    async def close(self) -> None:
        self.listener.close()
        for conn in reversed(self.conns):
            conn.close()
            try:
                await asyncio.wait_for(conn.wait_closed(), timeout=5)
            except OSError:  # TimeoutError included
                pass


def _fingerprint(host_key: Any) -> str | None:
    if host_key is None:
        return None
    key = getattr(host_key, "key", None) or host_key  # an OpenSSH host certificate wraps the key
    return str(key.get_fingerprint("sha256"))


class TunnelManager:
    def __init__(self, *, idle_s: int = 900, max_tunnels: int = 32, connect_timeout_s: int = 15,
                 keepalive_s: int = 30) -> None:
        self.idle_s, self.max_tunnels = idle_s, max_tunnels
        self.connect_timeout_s, self.keepalive_s = connect_timeout_s, keepalive_s
        self._tunnels: dict[str, _Tunnel] = {}
        self._ports: dict[str, int] = {}  # last local port per key, reused on reconnect
        self._locks: dict[str, asyncio.Lock] = {}

    @staticmethod
    def _key(info: ConnectionInfo) -> str:
        # Pinned host keys are left out: pinning them after the first test must not open a second tunnel.
        chain = [{k: v for k, v in hop.items() if k not in _PIN_FIELDS} for hop in hops(info.descriptor)]
        secrets = sorted((k, v) for k, v in info.credentials.items() if k.startswith(("ssh_", "lib_")))
        material = json.dumps([info.connection_id, chain, target(info), secrets], sort_keys=True, default=str)
        return hashlib.sha256(material.encode()).hexdigest()

    async def route(self, info: ConnectionInfo) -> tuple[ConnectionInfo, dict[str, Any]]:
        """The connection info to hand the adapter (connecting to the tunnel) and the report of each hop."""
        if info.engine not in SSH_ENGINES:
            raise TelaError(ErrorCode.VALIDATION_ERROR, "SSH tunnels are available for PostgreSQL, MySQL, SQL Server "
                            "and MongoDB connections.")
        chain = hops(info.descriptor)
        key = self._key(info)
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            await self._reap(keep=key)
            tunnel = self._tunnels.get(key)
            if tunnel is not None and (not tunnel.alive or self._pins_changed(chain, tunnel)):
                await tunnel.close()
                self._tunnels.pop(key, None)
                tunnel = None
            if tunnel is None:
                tunnel = await self._open(info, chain, self._ports.get(key, 0))
                self._tunnels[key] = tunnel
                self._ports[key] = tunnel.port
            tunnel.last_used = time.monotonic()
        descriptor = {**info.descriptor, "via": {"host": "127.0.0.1", "port": tunnel.port}}
        report = {"ok": True, "stage": None, "error": None, "host_key": tunnel.host_key, "hop": None,
                  "hops": tunnel.seen}
        return replace(info, descriptor=descriptor), report

    @staticmethod
    def _pins_changed(chain: list[dict[str, Any]], tunnel: _Tunnel) -> bool:
        return any(hop.get("host_key") and hop.get("verify") != "ca" and seen["host_key"] != hop["host_key"]
                   for hop, seen in zip(chain, tunnel.seen, strict=False))

    async def _open(self, info: ConnectionInfo, chain: list[dict[str, Any]], port: int) -> _Tunnel:
        conns: list[asyncssh.SSHClientConnection] = []
        seen: list[dict[str, Any]] = []
        try:
            for hop in chain:
                conn, report = await self._connect(hop, info.credentials, conns[-1] if conns else None, seen)
                conns.append(conn)
                seen.append(report)
            target_host, target_port = target(info)
            where = self._where(chain[-1])
            try:
                # Prove the SSH server can reach the database before any driver tries (a refused forward otherwise
                # surfaces as a bare connection reset).
                _, writer = await asyncio.wait_for(conns[-1].open_connection(target_host, target_port),
                                                   self.connect_timeout_s)
                writer.close()
                listener = await self._listen(conns[-1], port, target_host, target_port)
            except (OSError, asyncssh.Error) as exc:
                raise SshTunnelError(
                    "forward", f"The SSH server {where} could not open a connection to {target_host}:{target_port}. "
                    "Check that the database is reachable from the SSH server and that it allows TCP forwarding.",
                    seen[-1]["host_key"], "", seen) from exc
        except BaseException:
            for conn in reversed(conns):
                conn.close()
            raise
        log.info("runner.ssh_tunnel_opened", connection_id=info.connection_id, hops=len(chain),
                 local_port=listener.get_port())
        return _Tunnel(conns, listener, listener.get_port(), seen, time.monotonic())

    @staticmethod
    def _where(hop: dict[str, Any]) -> str:
        return f"{hop['host']}:{int(hop.get('port') or 22)}"

    async def _connect(self, hop: dict[str, Any], creds: dict[str, str], through: Any,
                       seen: list[dict[str, Any]]) -> tuple[asyncssh.SSHClientConnection, dict[str, Any]]:
        hop_id = str(hop.get("id") or "")
        name = f"jump host {self._where(hop)}" if hop_id else f"SSH server {self._where(hop)}"
        private_key, certificate, password = hop_secret(hop, creds)
        ca_line = str(hop.get("host_ca") or "") if hop.get("verify") == "ca" else ""
        pinned = None if ca_line else (hop.get("host_key") or None)

        def fail(stage: str, message: str, host_key: str | None = None) -> SshTunnelError:
            report = {"id": hop_id, "host": hop.get("host"), "port": int(hop.get("port") or 22),
                      "host_key": host_key, "verified": None}
            return SshTunnelError(stage, message, host_key, hop_id, [*seen, report])

        options: dict[str, Any] = {}
        uses_key = _uses_key(hop)
        if uses_key:
            if not private_key:
                raise fail("auth", f"The SSH key for the {name} is missing from the secrets store. Upload the key "
                           "again.")
            try:
                key = asyncssh.import_private_key(private_key)
                cert = asyncssh.import_certificate(certificate) if certificate else None
            except (asyncssh.KeyImportError, ValueError) as exc:
                raise fail("auth", f"The saved SSH key for the {name} could not be read. Upload the key again.") \
                    from exc
            options.update(client_keys=[(key, cert) if cert else key], preferred_auth="publickey")
        else:
            options.update(password=password, client_keys=None, preferred_auth="keyboard-interactive,password")
        trusted_ca: list[Any] = []
        if ca_line:
            try:
                trusted_ca = [asyncssh.import_public_key(ca_line)]
            except (asyncssh.KeyImportError, ValueError) as exc:
                raise fail("host_key", f"The trusted host CA for the {name} could not be read.") from exc
        client = _Client(pinned, bool(ca_line))
        try:
            conn, _ = await asyncssh.create_connection(
                lambda: client, hop["host"], int(hop.get("port") or 22), tunnel=through, username=hop["username"],
                known_hosts=lambda *_: ([], trusted_ca, []),
                server_host_key_algs=CERT_HOST_KEY_ALGS if ca_line else PLAIN_HOST_KEY_ALGS, config=[],
                agent_path=None, connect_timeout=self.connect_timeout_s, login_timeout=self.connect_timeout_s,
                keepalive_interval=self.keepalive_s, keepalive_count_max=3, **options)
        except asyncssh.HostKeyNotVerifiable as exc:
            log.warning("runner.ssh_host_key_refused", hop=hop_id or "server", ca=bool(ca_line))
            if ca_line:
                raise fail("host_key", f"The {name} did not show a host certificate signed by the trusted CA for "
                           f"this host name.", client.seen) from exc
            raise fail("host_key", f"The {name} showed a different host key ({client.seen}) from the one Tela "
                       f"pinned ({pinned}). If the server was rebuilt, re-pin its key on the connection.",
                       client.seen) from exc
        except asyncssh.PermissionDenied as exc:
            secret = "key" if uses_key else "password"
            raise fail("auth", f"The {name} rejected the user name or {secret}.", client.seen) from exc
        except (OSError, asyncssh.Error) as exc:
            via = " through the previous hop" if through is not None else ""
            raise fail("connect", f"Tela could not reach the {name}{via}. Check its host, port and firewall.",
                       client.seen) from exc
        fingerprint = _fingerprint(conn.get_server_host_key()) or client.seen
        verified = "ca" if ca_line else ("pin" if pinned else "new")
        return conn, {"id": hop_id, "host": hop["host"], "port": int(hop.get("port") or 22), "host_key": fingerprint,
                      "verified": verified}

    @staticmethod
    async def _listen(conn: asyncssh.SSHClientConnection, port: int, host: str, target: int) -> asyncssh.SSHListener:
        if port:
            try:
                return await conn.forward_local_port("127.0.0.1", port, host, target)
            except OSError:
                pass  # taken since: any free port will do
        return await conn.forward_local_port("127.0.0.1", 0, host, target)

    async def _reap(self, *, keep: str) -> None:
        now = time.monotonic()
        idle = sorted((t.last_used, k) for k, t in self._tunnels.items() if k != keep)
        for used, k in idle:
            if now - used > self.idle_s or not self._tunnels[k].alive or len(self._tunnels) >= self.max_tunnels:
                await self._tunnels.pop(k).close()

    async def close(self) -> None:
        for tunnel in self._tunnels.values():
            await tunnel.close()
        self._tunnels.clear()
