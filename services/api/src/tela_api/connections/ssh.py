"""SSH tunnels for connections (ENH-003, ENH-004) and the shared SSH key library.

A connection's ``ssh`` block describes the SSH server that reaches the database, and ``ssh.jump`` the hosts in
front of it, in order (like OpenSSH ``-J``). Each hop signs in with a key or a password and checks the server
either against a pinned host key (pinned by the first passing test) or against a trusted host CA.

Keys: the SSH server may use a key uploaded just for this connection (kept in the connection's secret as
``ssh_private_key`` / ``ssh_certificate``) or a key from the library; jump hosts use library keys or a password
(``ssh_jump_<id>_password``). The library (``conn.ssh_keys`` + ``tenants/<t>/ssh_keys/<id>``) holds a key once
for many connections: replacing it puts every connection that uses it back to draft until it passes a test,
and a key in use cannot be deleted. The API writes secrets and never reads them back; the runner reads them.

Uploaded key files are read once with their passphrase and stored as OpenSSH (``tela_common.sshkeys``). An
OpenSSH user certificate may come with a key; it must certify that key and be valid now.
"""

from __future__ import annotations

import json
import secrets as pysecrets
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from tela_api import outbox
from tela_api.audit import service as audit
from tela_api.core.db import fetch_all, fetch_one, tenant_tx
from tela_api.identity.deps import require
from tela_api.identity.permissions import Perm
from tela_api.identity.principal import Principal
from tela_common.errors import ErrorCode, TelaError
from tela_common.ids import new_id
from tela_common.secrets import SecretsStore, secret_ref
from tela_common.sshkeys import (
    MAX_KEY_FILE_BYTES,
    MSG_BAD_PASSPHRASE,
    MSG_NEEDS_PASSPHRASE,
    KeyFileError,
    SshKey,
    load_certificate,
    load_private_key,
    load_public_key,
    mongo_tunnel_target,
)

router = APIRouter(prefix="/api/v1", tags=["connections"])

SSH_ENGINES = ("postgres", "mysql", "sqlserver", "mongodb")
MAX_JUMPS = 3
_HOST = r"^[A-Za-z0-9.:-]+$"
_UUID = r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
_PIN = ("host_key", "host_key_pinned")
MSG_ENGINES = "SSH tunnels are available for PostgreSQL, MySQL, SQL Server and MongoDB connections."


class HostCheck(BaseModel):
    verify: Literal["pin", "ca"] | None = None  # pin the first key seen, or trust host certificates from a CA
    host_ca: str | None = Field(default=None, min_length=1, max_length=4096)  # the CA's public key line
    repin: bool = False  # forget the pinned host key; the next passing test pins the key the server shows


class JumpIn(HostCheck):
    """A host in front of the SSH server. ``id`` keeps a saved hop (its pinned key and password)."""

    id: str | None = Field(default=None, pattern=r"^[a-z0-9]{1,16}$")
    host: str = Field(min_length=1, max_length=255, pattern=_HOST)
    port: int = Field(default=22, ge=1, le=65535)
    username: str = Field(min_length=1, max_length=64)
    auth: Literal["library", "password"] = "library"
    key_id: str | None = Field(default=None, pattern=_UUID)
    password: str | None = Field(default=None, min_length=1, max_length=1024)


class SshIn(HostCheck):
    """Reach the database through an SSH server, e.g. a bastion. On an edit, blank fields keep what is saved.
    Key files and passwords go to the secrets store only and are never shown again."""

    enabled: bool = True
    host: str | None = Field(default=None, min_length=1, max_length=255, pattern=_HOST)
    port: int | None = Field(default=None, ge=1, le=65535)
    username: str | None = Field(default=None, min_length=1, max_length=64)
    auth: Literal["key", "password"] | None = None
    key_source: Literal["upload", "library"] | None = None
    key_id: str | None = Field(default=None, pattern=_UUID)
    private_key: str | None = Field(default=None, min_length=1, max_length=MAX_KEY_FILE_BYTES)  # file contents
    passphrase: str | None = Field(default=None, max_length=1024)
    certificate: str | None = Field(default=None, max_length=MAX_KEY_FILE_BYTES)  # -cert.pub; "" removes it
    password: str | None = Field(default=None, min_length=1, max_length=1024)
    jump: list[JumpIn] | None = Field(default=None, max_length=MAX_JUMPS)


def _invalid(message: str, field: str) -> TelaError:
    return TelaError(ErrorCode.VALIDATION_ERROR, message, {"field": field})


def check_target(engine: str, descriptor: dict[str, Any]) -> None:
    """The engine can go through a tunnel, and (MongoDB) the connection string names one host."""
    if engine not in SSH_ENGINES:
        raise _invalid(MSG_ENGINES, "ssh")
    if engine == "mongodb":
        try:
            mongo_tunnel_target(str(descriptor.get("uri") or ""))
        except ValueError as exc:
            raise _invalid(f"{str(exc)[0].upper()}{str(exc)[1:]}.", "uri") from None


def _read_key(private_key: str, passphrase: str | None, prefix: str) -> SshKey:
    try:
        return load_private_key(private_key, passphrase)
    except KeyFileError as exc:
        field = "passphrase" if str(exc) in (MSG_NEEDS_PASSPHRASE, MSG_BAD_PASSPHRASE) else "private_key"
        raise _invalid(str(exc), f"{prefix}{field}") from None


def _read_certificate(certificate: str, key_fingerprint: str, field: str) -> dict[str, Any]:
    try:
        return load_certificate(certificate, key_fingerprint).summary()
    except KeyFileError as exc:
        raise _invalid(str(exc), field) from None


def _host_check(check: HostCheck, saved: dict[str, Any], prefix: str, moved: bool) -> dict[str, Any]:
    verify = check.verify or saved.get("verify") or "pin"
    # Defaults are not stored ("pin", an uploaded key), so ENH-003 descriptors compare equal when unchanged.
    out: dict[str, Any] = {"host_key": saved.get("host_key"), "host_key_pinned": saved.get("host_key_pinned")}
    if verify == "ca":
        out["verify"] = "ca"
        line = check.host_ca or saved.get("host_ca")
        if not line:
            raise _invalid("Paste the public key of the CA that signs the server's host certificates.",
                           f"{prefix}host_ca")
        try:
            out["host_ca"], out["host_ca_fingerprint"] = load_public_key(line)
        except KeyFileError as exc:
            raise _invalid(str(exc), f"{prefix}host_ca") from None
        out["host_key"] = out["host_key_pinned"] = None  # certificates are checked, nothing is pinned
    if check.repin or moved:
        out["host_key"] = out["host_key_pinned"] = None  # a different server: pin again on the next passing test
    return out


async def _library_key(conn: AsyncConnection, key_id: str | None, field: str) -> dict[str, Any]:
    row = await fetch_one(conn, "SELECT id, name, key_type, key_bits, fingerprint, certificate FROM conn.ssh_keys "
                                "WHERE id = CAST(:id AS uuid) AND deleted_at IS NULL", {"id": key_id}) \
        if key_id else None
    if row is None:
        raise _invalid("Choose a key from the SSH key library.", field)
    return {"key_id": str(row["id"]), "key_type": row["key_type"], "key_bits": row["key_bits"],
            "key_fingerprint": row["fingerprint"], "certificate": row["certificate"]}


async def _jumps(conn: AsyncConnection, jumps: list[JumpIn], saved: list[dict[str, Any]],
                 secret: dict[str, str]) -> list[dict[str, Any]]:
    by_id = {j["id"]: j for j in saved}
    out = []
    for i, j in enumerate(jumps):
        prefix = f"ssh.jump.{i}."
        prev = by_id.get(j.id or "", {})
        hop_id = j.id if prev else pysecrets.token_hex(4)
        hop: dict[str, Any] = {"id": hop_id, "host": j.host, "port": j.port, "username": j.username, "auth": j.auth}
        if j.auth == "library":
            hop.update(await _library_key(conn, (j.key_id or prev.get("key_id") or "").lower() or None,
                                          f"{prefix}key_id"))
            secret[f"ssh_jump_{hop_id}_password"] = ""
        elif j.password:
            secret[f"ssh_jump_{hop_id}_password"] = j.password
        elif prev.get("auth") != "password":
            raise _invalid("Enter the jump host's password.", f"{prefix}password")
        moved = bool(prev) and (j.host, j.port) != (prev.get("host"), prev.get("port"))
        hop.update(_host_check(j, prev, prefix, moved))
        out.append(hop)
    kept = {h["id"] for h in out}
    for gone in set(by_id) - kept:
        secret[f"ssh_jump_{gone}_password"] = ""
    return out


async def ssh_settings(conn: AsyncConnection, ssh: SshIn,
                       saved: dict[str, Any] | None) -> tuple[dict[str, Any] | None, dict[str, str]]:
    """The descriptor's ``ssh`` block and the secret fields to write (blank = clear), from the form and what is
    saved."""
    cur = saved or {}
    if not ssh.enabled:
        clear = {"ssh_private_key": "", "ssh_certificate": "", "ssh_password": ""}
        clear.update({f"ssh_jump_{j['id']}_password": "" for j in cur.get("jump") or []})
        return None, clear
    out: dict[str, Any] = {"host": ssh.host or cur.get("host"), "port": ssh.port or cur.get("port") or 22,
                           "username": ssh.username or cur.get("username"),
                           "auth": ssh.auth or cur.get("auth") or "key"}
    for name in ("host", "username"):
        if not out[name]:
            raise _invalid(f"Enter the SSH {'server' if name == 'host' else 'user'}.", f"ssh.{name}")
    secret: dict[str, str] = {}
    if out["auth"] == "key":
        source = ssh.key_source or cur.get("key_source") or "upload"
        if source == "library":
            out["key_source"] = "library"
            key_id = (ssh.key_id or (cur.get("key_id") if cur.get("key_source") == "library" else None) or "")
            out.update(await _library_key(conn, key_id.lower() or None, "ssh.key_id"))
            secret.update(ssh_private_key="", ssh_certificate="", ssh_password="")
        else:
            had_key = cur.get("auth", "key") == "key" and cur.get("key_source", "upload") == "upload" \
                and cur.get("key_fingerprint")
            if ssh.private_key:
                key = _read_key(ssh.private_key, ssh.passphrase, "ssh.")
                out.update(key_type=key.key_type, key_bits=key.bits, key_fingerprint=key.fingerprint, certificate=None)
                secret.update(ssh_private_key=key.openssh, ssh_certificate="", ssh_password="")
            elif had_key:
                out.update({k: cur.get(k) for k in ("key_type", "key_bits", "key_fingerprint", "certificate")})
            else:
                raise _invalid("Choose the private key file (.pem, .ppk or OpenSSH).", "ssh.private_key")
            if ssh.certificate:
                out["certificate"] = _read_certificate(ssh.certificate, out["key_fingerprint"], "ssh.certificate")
                secret["ssh_certificate"] = ssh.certificate.strip()
            elif ssh.certificate == "" and out.get("certificate"):
                out["certificate"] = None
                secret["ssh_certificate"] = ""
    elif ssh.password:
        secret.update(ssh_password=ssh.password, ssh_private_key="", ssh_certificate="")
    elif cur.get("auth") != "password":
        raise _invalid("Enter the SSH password.", "ssh.password")
    moved = bool(cur) and (out["host"], out["port"]) != (cur.get("host"), cur.get("port"))
    out.update(_host_check(ssh, cur, "ssh.", moved))
    jumps = await _jumps(conn, ssh.jump, list(cur.get("jump") or []), secret) if ssh.jump is not None \
        else list(cur.get("jump") or [])
    if jumps:
        out["jump"] = jumps
    return {k: v for k, v in out.items() if v is not None or k in _PIN}, secret


def unchanged(block: dict[str, Any] | None, saved: dict[str, Any] | None, secret: dict[str, str]) -> bool:
    """The form sent the saved tunnel back as it was: not a credential change."""
    return block == saved and all(v == "" for v in secret.values())


async def pin_host_keys(conn: AsyncConnection, connection_id: Any, report: dict[str, Any]) -> list[dict[str, str]]:
    """Trust on first use, by an admin who just proved the whole path works: pin each hop's host key that is not
    pinned yet and not checked by a CA. Locks the row, so an edit made meanwhile is never overwritten."""
    row = await fetch_one(conn, "SELECT descriptor FROM conn.connections WHERE id = :id FOR UPDATE",
                          {"id": connection_id})
    ssh = dict((row or {}).get("descriptor", {}).get("ssh") or {})
    if not ssh:
        return []
    seen = {str(h.get("id") or ""): h.get("host_key") for h in report.get("hops") or []}
    seen.setdefault("", report.get("host_key"))
    at = datetime.now(UTC).isoformat()
    pinned: list[dict[str, str]] = []

    def pin(hop: dict[str, Any], hop_id: str) -> dict[str, Any]:
        if hop.get("verify") == "ca" or hop.get("host_key") or not seen.get(hop_id):
            return hop
        pinned.append({"hop": hop_id, "host_key": str(seen[hop_id])})
        return {**hop, "host_key": seen[hop_id], "host_key_pinned": at}

    ssh = pin(ssh, "")
    if ssh.get("jump"):
        ssh["jump"] = [pin(dict(j), str(j["id"])) for j in ssh["jump"]]
    if pinned:
        await conn.execute(text("UPDATE conn.connections SET descriptor = jsonb_set(descriptor, '{ssh}', "
                                "CAST(:ssh AS jsonb)) WHERE id = :id"), {"ssh": json.dumps(ssh), "id": connection_id})
    return pinned


# --------------------------------------------------------------------------- key library


class SshKeyIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    private_key: str = Field(min_length=1, max_length=MAX_KEY_FILE_BYTES)
    passphrase: str | None = Field(default=None, max_length=1024)
    certificate: str | None = Field(default=None, max_length=MAX_KEY_FILE_BYTES)


class SshKeyPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    private_key: str | None = Field(default=None, min_length=1, max_length=MAX_KEY_FILE_BYTES)
    passphrase: str | None = Field(default=None, max_length=1024)
    certificate: str | None = Field(default=None, max_length=MAX_KEY_FILE_BYTES)  # "" removes it


_USERS = """SELECT id, name, status FROM conn.connections WHERE deleted_at IS NULL
            AND jsonb_path_exists(descriptor, '$.ssh.** ? (@.key_id == $id)',
                                  jsonb_build_object('id', CAST(:id AS text)))
            ORDER BY name"""  # connections using a library key: the SSH server or any jump host


def _store(request: Request) -> SecretsStore:
    store: SecretsStore = request.app.state.secrets
    return store


def _key_out(row: dict[str, Any], used_by: list[dict[str, Any]]) -> dict[str, Any]:
    return {"id": str(row["id"]), "name": row["name"], "key_type": row["key_type"], "key_bits": row["key_bits"],
            "fingerprint": row["fingerprint"], "certificate": row["certificate"],
            "created_at": row["created_at"], "updated_at": row["updated_at"],
            "used_by": [{"id": str(u["id"]), "name": u["name"], "status": u["status"]} for u in used_by]}


async def _load_key(conn: AsyncConnection, key_id: str) -> dict[str, Any]:
    row = await fetch_one(conn, "SELECT * FROM conn.ssh_keys WHERE id = CAST(:id AS uuid) AND deleted_at IS NULL",
                          {"id": key_id})
    if row is None:
        raise TelaError(ErrorCode.NOT_FOUND, "SSH key not found.")
    return row


async def _name_free(conn: AsyncConnection, name: str, other: str | None = None) -> None:
    taken = await fetch_one(conn, "SELECT 1 FROM conn.ssh_keys WHERE lower(name) = lower(:n) AND deleted_at IS NULL "
                                  "AND (CAST(:o AS uuid) IS NULL OR id <> CAST(:o AS uuid))", {"n": name, "o": other})
    if taken:
        raise TelaError(ErrorCode.CONFLICT, "A key with this name already exists.", {"field": "name"})


@router.get("/admin/ssh-keys")
async def list_keys(principal: Principal = Depends(require(Perm.CONNECTIONS_MANAGE))) -> dict[str, Any]:
    async with tenant_tx(principal.tenant_id) as conn:
        rows = await fetch_all(conn, "SELECT * FROM conn.ssh_keys WHERE deleted_at IS NULL ORDER BY lower(name)")
        return {"items": [_key_out(r, await fetch_all(conn, _USERS, {"id": str(r["id"])})) for r in rows]}


@router.post("/admin/ssh-keys", status_code=201)
async def create_key(body: SshKeyIn, request: Request,
                     principal: Principal = Depends(require(Perm.CONNECTIONS_MANAGE))) -> dict[str, Any]:
    key = _read_key(body.private_key, body.passphrase, "")
    cert = _read_certificate(body.certificate, key.fingerprint, "certificate") if body.certificate else None
    key_id = new_id()
    ref = secret_ref(principal.tenant_id, "ssh_keys", key_id)
    async with tenant_tx(principal.tenant_id) as conn:
        await _name_free(conn, body.name)
        await _store(request).put(ref, {"private_key": key.openssh, "certificate": (body.certificate or "").strip()})
        await conn.execute(
            text("INSERT INTO conn.ssh_keys (id, tenant_id, name, key_type, key_bits, fingerprint, certificate, "
                 "secret_ref, created_by) VALUES (:id, :t, :n, :kt, :kb, :fp, CAST(:c AS jsonb), :ref, :by)"),
            {"id": key_id, "t": principal.tenant_id, "n": body.name, "kt": key.key_type, "kb": key.bits,
             "fp": key.fingerprint, "c": json.dumps(cert) if cert else None, "ref": ref, "by": principal.user_id})
        await audit.record(conn, tenant_id=principal.tenant_id, action="ssh_key.created", object_type="ssh_key",
                           object_id=key_id, outcome="success", actor_id=principal.user_id,
                           correlation_id=getattr(request.state, "request_id", None),
                           details={"name": body.name, "fingerprint": key.fingerprint, "certificate": bool(cert)})
        return _key_out(await _load_key(conn, key_id), [])


@router.patch("/admin/ssh-keys/{key_id}")
async def update_key(key_id: str, body: SshKeyPatch, request: Request,
                     principal: Principal = Depends(require(Perm.CONNECTIONS_MANAGE))) -> dict[str, Any]:
    """Rename, rotate the key, or set or remove its certificate. A new key or certificate is a credential change
    for every connection that uses it: they go back to draft until they pass a test (A4)."""
    async with tenant_tx(principal.tenant_id) as conn:
        row = await _load_key(conn, key_id)
        if body.name and body.name != row["name"]:
            await _name_free(conn, body.name, key_id)
        fingerprint, key_type, bits, cert = row["fingerprint"], row["key_type"], row["key_bits"], row["certificate"]
        update: dict[str, str] = {}
        if body.private_key:
            key = _read_key(body.private_key, body.passphrase, "")
            fingerprint, key_type, bits = key.fingerprint, key.key_type, key.bits
            update["private_key"] = key.openssh
            if key.fingerprint != row["fingerprint"] and body.certificate is None and cert:
                update["certificate"], cert = "", None  # the old certificate belongs to the old key
        if body.certificate:
            cert = _read_certificate(body.certificate, fingerprint, "certificate")
            update["certificate"] = body.certificate.strip()
        elif body.certificate == "" and cert:
            update["certificate"], cert = "", None
        if update:
            await _store(request).patch(row["secret_ref"], update)
        await conn.execute(
            text("UPDATE conn.ssh_keys SET name = :n, key_type = :kt, key_bits = :kb, fingerprint = :fp, "
                 "certificate = CAST(:c AS jsonb) WHERE id = CAST(:id AS uuid)"),
            {"n": body.name or row["name"], "kt": key_type, "kb": bits, "fp": fingerprint,
             "c": json.dumps(cert) if cert else None, "id": key_id})
        used_by = await fetch_all(conn, _USERS, {"id": key_id})
        if update:
            for c in used_by:
                await _retest(request, conn, principal, str(c["id"]), c["status"])
        await audit.record(conn, tenant_id=principal.tenant_id, action="ssh_key.updated", object_type="ssh_key",
                           object_id=key_id, outcome="success", actor_id=principal.user_id, severity="high" if
                           update else "normal", correlation_id=getattr(request.state, "request_id", None),
                           details={"fields": sorted(update) + (["name"] if body.name else []),
                                    "fingerprint": fingerprint, "connections_to_retest": len(used_by) if update else 0})
        return _key_out(await _load_key(conn, key_id), await fetch_all(conn, _USERS, {"id": key_id}))


async def _retest(request: Request, conn: AsyncConnection, principal: Principal, connection_id: str,
                  status: str) -> None:
    from tela_api.connections.router import _invalidate_cache

    if status != "disabled":
        await conn.execute(text("UPDATE conn.connections SET status = 'draft', read_only_verified_at = NULL "
                                "WHERE id = :id"), {"id": connection_id})
    await _invalidate_cache(request, conn, connection_id)
    await outbox.emit(conn, tenant_id=principal.tenant_id, topic="connection.changed", aggregate_id=connection_id,
                      payload={"connection_id": connection_id, "retest_required": True})


@router.delete("/admin/ssh-keys/{key_id}", status_code=204)
async def delete_key(key_id: str, request: Request,
                     principal: Principal = Depends(require(Perm.CONNECTIONS_MANAGE))) -> Response:
    async with tenant_tx(principal.tenant_id) as conn:
        row = await _load_key(conn, key_id)
        used_by = await fetch_all(conn, _USERS, {"id": key_id})
        if used_by:
            raise TelaError(ErrorCode.CONFLICT, f"{len(used_by)} connection(s) use this key: "
                            f"{', '.join(u['name'] for u in used_by[:5])}. Choose another key on them first.")
        await conn.execute(text("UPDATE conn.ssh_keys SET deleted_at = now() WHERE id = CAST(:id AS uuid)"),
                           {"id": key_id})
        await audit.record(conn, tenant_id=principal.tenant_id, action="ssh_key.deleted", object_type="ssh_key",
                           object_id=key_id, outcome="success", actor_id=principal.user_id, severity="high",
                           correlation_id=getattr(request.state, "request_id", None),
                           details={"name": row["name"], "fingerprint": row["fingerprint"]})
    await _store(request).delete(row["secret_ref"])
    return Response(status_code=204)
