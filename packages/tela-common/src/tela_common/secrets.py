"""Secrets store adapters (US-007; COMP-013; NFR-013; API-069).

Credentials live only in the secrets store. The metadata database keeps a
``secret_ref`` (a path), and secrets never reach browsers, prompts or logs.

Backends:
* ``vault`` / ``openbao`` — KV v2 over HTTP via ``hvac`` (OpenBao is API compatible).
* ``file`` — JSON files with 0600 permissions, **local development only**.
* ``azure_keyvault`` — reserved; not implemented yet (optional Azure profile, US-122).

Each service signs in with its own identity (AppRole, Kubernetes, or a token file kept fresh by an agent), and
the store's policies decide what it may touch (``deploy/openbao/policies``): the API writes and patches but never
reads, the runner reads source credentials, the gateway reads provider keys.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from tela_common.errors import ErrorCode, TelaError

_REF = re.compile(r"^tenants/[0-9a-f\-]{36}/[a-z_]+/[A-Za-z0-9_\-]{1,80}$")


class SecretNotFound(TelaError):
    """The reference points at nothing (e.g. the dev store restarted); a caller with a full secret may re-create it."""

    def __init__(self) -> None:
        super().__init__(ErrorCode.SECRETS_UNAVAILABLE, "A required credential is not available.")


class SecretsStore(Protocol):
    async def put(self, ref: str, data: dict[str, str]) -> None: ...
    async def patch(self, ref: str, data: dict[str, str]) -> None: ...
    async def get(self, ref: str) -> dict[str, str]: ...
    async def delete(self, ref: str) -> None: ...
    async def healthy(self) -> bool: ...


def secret_ref(tenant_id: str, kind: str, name: str) -> str:
    """Build a validated reference such as ``tenants/<uuid>/connections/<id>``."""
    ref = f"tenants/{tenant_id}/{kind}/{name}"
    if not _REF.match(ref):
        raise ValueError("invalid secret reference")
    return ref


def _check_ref(ref: str) -> None:
    if not _REF.match(ref):
        raise TelaError(ErrorCode.VALIDATION_ERROR, "Invalid secret reference.")


def assert_strong_key(value: bytes | str | None, *, name: str, min_bytes: int = 32) -> bytes:
    """Refuse empty, short or placeholder (all-zero / repeated) keys at start-up (S31 P0 lesson)."""
    raw = value.encode() if isinstance(value, str) else (value or b"")
    if len(raw) < min_bytes:
        raise SystemExit(f"{name}: key missing or shorter than {min_bytes} bytes; refusing to start")
    if len(set(raw)) <= 2:
        raise SystemExit(f"{name}: key looks like a placeholder; refusing to start")
    return raw


class FileSecretsStore:
    """Development-only store. Never enabled outside local/dev/test."""

    def __init__(self, root: str | os.PathLike[str]) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)

    def _path(self, ref: str) -> Path:
        _check_ref(ref)
        return self.root / (ref.replace("/", "__") + ".json")

    async def put(self, ref: str, data: dict[str, str]) -> None:
        path = self._path(ref)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as fh:
            json.dump(data, fh)

    async def patch(self, ref: str, data: dict[str, str]) -> None:
        await self.put(ref, {**await self.get(ref), **data})

    async def get(self, ref: str) -> dict[str, str]:
        path = self._path(ref)
        if not path.exists():
            raise SecretNotFound()
        data: dict[str, str] = json.loads(path.read_text())
        return data

    async def delete(self, ref: str) -> None:
        self._path(ref).unlink(missing_ok=True)

    async def healthy(self) -> bool:
        return self.root.is_dir() and os.access(self.root, os.W_OK)


@dataclass(frozen=True)
class TokenAuth:
    """A token file, e.g. written by an OpenBao/Vault agent; read again whenever the store rejects the token."""

    token_file: str


@dataclass(frozen=True)
class AppRoleAuth:
    role_id_file: str
    secret_id_file: str
    mount: str = "approle"


@dataclass(frozen=True)
class KubernetesAuth:
    role: str
    jwt_file: str = "/var/run/secrets/kubernetes.io/serviceaccount/token"
    mount: str = "kubernetes"


VaultAuth = TokenAuth | AppRoleAuth | KubernetesAuth

_RENEW_MARGIN_S = 60.0


class VaultSecretsStore:
    """HashiCorp Vault / OpenBao KV v2: 5 s timeout, 2 retries on transient errors, fail closed (API-069)."""

    def __init__(self, addr: str, auth: VaultAuth, mount: str = "tela", timeout: float = 5.0,
                 client: Any = None) -> None:
        if client is None:
            import hvac  # imported lazily so services without secrets access do not need it

            client = hvac.Client(url=addr, timeout=timeout)
        self._client: Any = client
        self._auth = auth
        self._mount = mount
        self._expires_at: float | None = 0.0  # 0 = not signed in; None = no known expiry (token file)
        self._lock = asyncio.Lock()

    def _login(self) -> None:
        auth = self._auth
        if isinstance(auth, TokenAuth):
            self._client.token = Path(auth.token_file).read_text().strip()
            self._expires_at = None
            return
        if isinstance(auth, AppRoleAuth):
            resp = self._client.auth.approle.login(
                role_id=Path(auth.role_id_file).read_text().strip(),
                secret_id=Path(auth.secret_id_file).read_text().strip(), mount_point=auth.mount)
        else:
            resp = self._client.auth.kubernetes.login(
                auth.role, Path(auth.jwt_file).read_text().strip(), mount_point=auth.mount)
        self._client.token = resp["auth"]["client_token"]
        lease = float(resp["auth"].get("lease_duration") or 0)
        self._expires_at = time.monotonic() + lease if lease else None

    async def _ensure_login(self) -> None:
        async with self._lock:
            exp = self._expires_at
            if exp == 0.0 or (exp is not None and time.monotonic() > exp - _RENEW_MARGIN_S):
                await asyncio.to_thread(self._login)

    async def _call(self, fn: Any, *args: Any, **kwargs: Any) -> Any:
        from hvac import exceptions as hv

        last: Exception | None = None
        signed_in_again = False
        attempt = 0
        while attempt < 3:  # 2 retries on transient errors
            try:
                await self._ensure_login()
                return await asyncio.to_thread(fn, *args, **kwargs)
            except hv.InvalidPath as exc:
                raise SecretNotFound() from exc
            except (hv.Forbidden, hv.Unauthorized) as exc:
                # An expired or revoked token: sign in once more. A second refusal is a policy denial.
                if signed_in_again:
                    raise TelaError(ErrorCode.SECRETS_UNAVAILABLE,
                                    "This service may not use that credential.") from exc
                signed_in_again = True
                self._expires_at = 0.0
            except TelaError:
                raise
            except Exception as exc:  # noqa: BLE001 - mapped to a typed, fail-closed error below
                last = exc
                attempt += 1
        raise TelaError(ErrorCode.SECRETS_UNAVAILABLE, "The secrets store is unavailable.") from last

    async def put(self, ref: str, data: dict[str, str]) -> None:
        _check_ref(ref)
        await self._call(self._client.secrets.kv.v2.create_or_update_secret,
                         path=ref, secret=data, mount_point=self._mount)

    async def patch(self, ref: str, data: dict[str, str]) -> None:
        """Merge ``data`` into the stored secret without reading it (the API's policy has no read).

        hvac's own ``patch`` reads first, so this sends the KV v2 JSON merge patch itself.
        """
        _check_ref(ref)
        await self._call(self._client.adapter.request, "PATCH", f"/v1/{self._mount}/data/{ref}",
                         json={"data": data}, headers={"Content-Type": "application/merge-patch+json"})

    async def get(self, ref: str) -> dict[str, str]:
        _check_ref(ref)
        resp = await self._call(self._client.secrets.kv.v2.read_secret_version,
                                path=ref, mount_point=self._mount, raise_on_deleted_version=True)
        data: dict[str, str] = resp["data"]["data"]
        return data

    async def delete(self, ref: str) -> None:
        _check_ref(ref)
        await self._call(self._client.secrets.kv.v2.delete_metadata_and_all_versions,
                         path=ref, mount_point=self._mount)

    async def healthy(self) -> bool:
        """The store answers and this service's identity can still sign in."""
        try:
            return bool(await self._call(self._client.auth.token.lookup_self))
        except TelaError:
            return False


def vault_auth_from_env() -> VaultAuth:
    """``TELA_SECRETS_AUTH``: ``approle`` (role/secret id files), ``kubernetes`` or ``token`` (a token file)."""
    method = os.environ.get("TELA_SECRETS_AUTH") or (
        "approle" if os.environ.get("TELA_SECRETS_ROLE_ID_FILE") else "token")
    if method == "approle":
        role_file = os.environ.get("TELA_SECRETS_ROLE_ID_FILE")
        secret_file = os.environ.get("TELA_SECRETS_SECRET_ID_FILE")
        if not role_file or not secret_file:
            raise SystemExit("TELA_SECRETS_ROLE_ID_FILE and TELA_SECRETS_SECRET_ID_FILE are required for AppRole")
        return AppRoleAuth(role_file, secret_file, os.environ.get("TELA_SECRETS_AUTH_MOUNT", "approle"))
    if method == "kubernetes":
        role = os.environ.get("TELA_SECRETS_K8S_ROLE")
        if not role:
            raise SystemExit("TELA_SECRETS_K8S_ROLE is required for Kubernetes auth")
        return KubernetesAuth(role, os.environ.get("TELA_SECRETS_K8S_JWT_FILE", KubernetesAuth.jwt_file),
                              os.environ.get("TELA_SECRETS_AUTH_MOUNT", "kubernetes"))
    if method == "token":
        token_file = os.environ.get("TELA_SECRETS_TOKEN_FILE")
        if not token_file:
            raise SystemExit("TELA_SECRETS_TOKEN_FILE is required for token auth")
        return TokenAuth(token_file)
    raise SystemExit(f"secrets auth method '{method}' is not supported")


def build_secrets_store(backend: str, addr: str | None, env: str) -> SecretsStore:
    if backend == "file":
        if env not in ("local", "dev", "test"):
            raise SystemExit("the 'file' secrets backend is for local development only")
        return FileSecretsStore(os.environ.get("TELA_SECRETS_DIR", ".secrets-dev"))
    if backend in ("vault", "openbao"):
        if not addr:
            raise SystemExit("TELA_SECRETS_ADDR is required for the vault/openbao backend")
        return VaultSecretsStore(addr, vault_auth_from_env(), mount=os.environ.get("TELA_SECRETS_MOUNT", "tela"))
    raise SystemExit(f"secrets backend '{backend}' is not supported yet")
