"""Secrets store identities and object storage encryption, tags and lifecycle (US-007, US-008). No network."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from botocore.exceptions import ClientError
from hvac import exceptions as hv

from tela_common.errors import ErrorCode, TelaError
from tela_common.objectstore import (
    LifecycleNotSupported,
    ObjectStore,
    data_class,
    lifecycle_rules,
    parse_sse,
    tenant_key,
)
from tela_common.secrets import (
    AppRoleAuth,
    FileSecretsStore,
    KubernetesAuth,
    TokenAuth,
    VaultSecretsStore,
    build_secrets_store,
    secret_ref,
    vault_auth_from_env,
)

TENANT = "0192f3b2-0000-7000-8000-000000000001"
REF = secret_ref(TENANT, "connections", "c1")


# --------------------------------------------------------------------------- secrets store


class _Ns:
    def __init__(self, **kw: Any) -> None:
        self.__dict__.update(kw)


class FakeVault:
    """Just enough of hvac.Client: AppRole/Kubernetes login, KV v2, token lookup and the raw adapter."""

    def __init__(self, lease: int = 3600) -> None:
        self.token: str | None = None
        self.calls: list[tuple[str, Any]] = []
        self.logins = 0
        self.lease = lease
        self.fail: list[Exception] = []  # raised (in order) by the next KV calls
        self.store: dict[str, dict[str, str]] = {}

        def login(**kw: Any) -> dict[str, Any]:
            self.logins += 1
            self.calls.append(("login", kw))
            return {"auth": {"client_token": f"t{self.logins}", "lease_duration": self.lease}}

        def k8s_login(role: str, jwt: str, mount_point: str) -> dict[str, Any]:
            return login(role=role, jwt=jwt, mount_point=mount_point)

        self.auth = _Ns(approle=_Ns(login=login), kubernetes=_Ns(login=k8s_login),
                        token=_Ns(lookup_self=lambda: self._kv("lookup", None) or {"data": {}}))
        self.secrets = _Ns(kv=_Ns(v2=_Ns(
            create_or_update_secret=lambda path, secret, mount_point: self._kv("put", (path, secret)),
            read_secret_version=lambda path, mount_point, raise_on_deleted_version: self._kv("get", path),
            delete_metadata_and_all_versions=lambda path, mount_point: self._kv("delete", path),
        )))
        self.adapter = _Ns(request=lambda method, url, json, headers: self._kv(method, (url, json, headers)))

    def _kv(self, op: str, arg: Any) -> Any:
        self.calls.append((op, arg))
        if self.fail:
            raise self.fail.pop(0)
        if op == "put":
            self.store[arg[0]] = dict(arg[1])
        if op == "get":
            if arg not in self.store:
                raise hv.InvalidPath("not found")
            return {"data": {"data": self.store[arg]}}
        return None


def _approle(tmp_path: Path) -> AppRoleAuth:
    (tmp_path / "role").write_text("role-1\n")
    (tmp_path / "sid").write_text("secret-1\n")
    return AppRoleAuth(str(tmp_path / "role"), str(tmp_path / "sid"))


async def test_approle_signs_in_once_and_again_near_expiry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeVault(lease=3600)
    store = VaultSecretsStore("http://bao", _approle(tmp_path), mount="secret", client=fake)
    now = [1000.0]
    monkeypatch.setattr("tela_common.secrets.time.monotonic", lambda: now[0])

    await store.put(REF, {"username": "u", "password": "p"})
    assert await store.get(REF) == {"username": "u", "password": "p"}
    assert fake.logins == 1 and fake.token == "t1"
    assert fake.calls[0] == ("login", {"role_id": "role-1", "secret_id": "secret-1", "mount_point": "approle"})

    now[0] += 3600 - 30  # inside the renewal margin
    await store.get(REF)
    assert fake.logins == 2 and fake.token == "t2"


async def test_rejected_token_signs_in_again_then_fails_closed_on_policy_denial(tmp_path: Path) -> None:
    fake = FakeVault()
    store = VaultSecretsStore("http://bao", _approle(tmp_path), client=fake)
    fake.store[REF] = {"password": "p"}

    fake.fail = [hv.Forbidden("token expired")]
    assert await store.get(REF) == {"password": "p"}
    assert fake.logins == 2  # first call + one more after the refusal

    fake.fail = [hv.Forbidden("permission denied"), hv.Forbidden("permission denied")]
    with pytest.raises(TelaError) as denied:
        await store.get(REF)
    assert denied.value.code == ErrorCode.SECRETS_UNAVAILABLE
    assert "may not use" in denied.value.message


async def test_missing_secret_is_not_retried_and_transient_errors_are(tmp_path: Path) -> None:
    fake = FakeVault()
    store = VaultSecretsStore("http://bao", _approle(tmp_path), client=fake)

    with pytest.raises(TelaError) as missing:
        await store.get(REF)
    assert missing.value.code == ErrorCode.SECRETS_UNAVAILABLE
    assert [c for c in fake.calls if c[0] == "get"] == [("get", REF)]

    fake.store[REF] = {"password": "p"}
    fake.fail = [ConnectionError("down"), TimeoutError("slow")]
    assert await store.get(REF) == {"password": "p"}  # third attempt succeeds

    fake.fail = [ConnectionError("down")] * 3
    with pytest.raises(TelaError) as down:
        await store.get(REF)
    assert down.value.message == "The secrets store is unavailable."


async def test_patch_is_a_merge_patch_without_a_read(tmp_path: Path) -> None:
    fake = FakeVault()
    store = VaultSecretsStore("http://bao", _approle(tmp_path), mount="secret", client=fake)
    await store.patch(REF, {"password": "p2"})
    ops = [c for c in fake.calls if c[0] != "login"]
    assert ops == [("PATCH", (f"/v1/secret/data/{REF}", {"data": {"password": "p2"}},
                              {"Content-Type": "application/merge-patch+json"}))]
    with pytest.raises(TelaError):
        await store.patch("tenants/../x", {"a": "b"})


async def test_token_file_and_kubernetes_auth(tmp_path: Path) -> None:
    (tmp_path / "token").write_text("agent-token\n")
    fake = FakeVault()
    store = VaultSecretsStore("http://bao", TokenAuth(str(tmp_path / "token")), client=fake)
    assert await store.healthy()
    assert fake.token == "agent-token" and fake.logins == 0

    (tmp_path / "jwt").write_text("sa-jwt")
    fake = FakeVault(lease=0)  # no lease: no scheduled renewal
    store = VaultSecretsStore("http://bao", KubernetesAuth("tela-runner", str(tmp_path / "jwt")), client=fake)
    await store.delete(REF)
    await store.delete(REF)
    assert fake.logins == 1
    assert fake.calls[0] == ("login", {"role": "tela-runner", "jwt": "sa-jwt", "mount_point": "kubernetes"})

    fake.fail = [ConnectionError("down")] * 3
    assert not await store.healthy()


def test_auth_method_from_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("TELA_SECRETS_AUTH", "TELA_SECRETS_ROLE_ID_FILE", "TELA_SECRETS_SECRET_ID_FILE",
                 "TELA_SECRETS_TOKEN_FILE", "TELA_SECRETS_K8S_ROLE", "TELA_SECRETS_AUTH_MOUNT"):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(SystemExit):
        vault_auth_from_env()  # token is the default and needs a file

    monkeypatch.setenv("TELA_SECRETS_ROLE_ID_FILE", "/r")
    with pytest.raises(SystemExit):
        vault_auth_from_env()  # AppRole needs both files
    monkeypatch.setenv("TELA_SECRETS_SECRET_ID_FILE", "/s")
    assert vault_auth_from_env() == AppRoleAuth("/r", "/s", "approle")

    monkeypatch.setenv("TELA_SECRETS_AUTH", "kubernetes")
    with pytest.raises(SystemExit):
        vault_auth_from_env()
    monkeypatch.setenv("TELA_SECRETS_K8S_ROLE", "tela-api")
    assert vault_auth_from_env() == KubernetesAuth("tela-api")

    monkeypatch.setenv("TELA_SECRETS_AUTH", "token")
    monkeypatch.setenv("TELA_SECRETS_TOKEN_FILE", "/t")
    assert vault_auth_from_env() == TokenAuth("/t")

    monkeypatch.setenv("TELA_SECRETS_AUTH", "userpass")
    with pytest.raises(SystemExit):
        vault_auth_from_env()

    monkeypatch.setenv("TELA_SECRETS_AUTH", "token")
    assert isinstance(build_secrets_store("openbao", "http://bao:8200", "prod"), VaultSecretsStore)
    with pytest.raises(SystemExit):
        build_secrets_store("openbao", None, "prod")


async def test_file_store_patch_merges(tmp_path: Path) -> None:
    store = FileSecretsStore(tmp_path / "s")
    await store.put(REF, {"username": "u", "password": "p"})
    await store.patch(REF, {"password": "p2"})
    assert await store.get(REF) == {"username": "u", "password": "p2"}


# --------------------------------------------------------------------------- object storage


class FakeS3:
    def __init__(self, sse_echo: bool = True) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.sse_echo = sse_echo
        self.rules: list[dict[str, Any]] | None = None
        self.mangle_rules = False
        self.head_error: Exception | None = None

    def put_object(self, **kw: Any) -> dict[str, Any]:
        self.calls.append(("put_object", kw))
        return {"ServerSideEncryption": kw["ServerSideEncryption"]} if self.sse_echo and \
            "ServerSideEncryption" in kw else {}

    def delete_object(self, **kw: Any) -> dict[str, Any]:
        self.calls.append(("delete_object", kw))
        return {}

    def head_bucket(self, **kw: Any) -> dict[str, Any]:
        if self.head_error:
            raise self.head_error
        return {}

    def put_bucket_lifecycle_configuration(self, Bucket: str, LifecycleConfiguration: dict[str, Any]) -> None:
        rules = LifecycleConfiguration["Rules"]
        # SeaweedFS 3.80: keeps the first expiration only, with an empty prefix and no ID or tag filter.
        self.rules = [{"Expiration": rules[0]["Expiration"], "Prefix": "", "Status": "Enabled"}] \
            if self.mangle_rules else rules

    def get_bucket_lifecycle_configuration(self, Bucket: str) -> dict[str, Any]:
        if self.rules is None:
            raise ClientError({"Error": {"Code": "NoSuchLifecycleConfiguration"}}, "GetBucketLifecycleConfiguration")
        return {"Rules": self.rules}

    def delete_bucket_lifecycle(self, Bucket: str) -> None:
        self.calls.append(("delete_bucket_lifecycle", {}))
        self.rules = None


def _store(fake: FakeS3, **kw: Any) -> ObjectStore:
    store = ObjectStore("http://s3", "tela", **kw)
    store._client_obj = fake
    return store


async def test_put_tags_the_data_class_and_requires_confirmed_encryption() -> None:
    fake = FakeS3()
    key = tenant_key(TENANT, "exports", "job.pdf")
    await _store(fake).put(key, b"x", "application/pdf")
    put = fake.calls[0][1]
    assert put["Tagging"] == "tela-class=exports" and put["ServerSideEncryption"] == "AES256"

    ignoring = FakeS3(sse_echo=False)
    with pytest.raises(TelaError) as exc:
        await _store(ignoring).put(key, b"x", "application/pdf")
    assert exc.value.code == ErrorCode.STORAGE_UNAVAILABLE
    assert [c[0] for c in ignoring.calls] == ["put_object", "delete_object"]  # no unencrypted copy left


async def test_kms_and_no_encryption_modes() -> None:
    fake = FakeS3()
    await _store(fake, sse="aws:kms", kms_key_id="kms-1").put(tenant_key(TENANT, "files", "f", "v1"), b"x", "t")
    assert fake.calls[0][1]["SSEKMSKeyId"] == "kms-1"

    plain = FakeS3(sse_echo=False)
    await _store(plain, sse="none").put(tenant_key(TENANT, "knowledge", "d.pdf"), b"x", "t")
    assert "ServerSideEncryption" not in plain.calls[0][1] and len(plain.calls) == 1


@pytest.mark.parametrize(("value", "env", "want"), [
    (None, "prod", "AES256"), (True, "prod", "AES256"), ("true", "prod", "AES256"), ("aws:kms", "prod", "aws:kms"),
    (False, "dev", "none"), ("false", "local", "none"), ("none", "test", "none"),
])
def test_parse_sse(value: Any, env: str, want: str) -> None:
    assert parse_sse(value, env) == want


@pytest.mark.parametrize(("value", "env"), [("none", "prod"), (False, "uat"), ("rot13", "dev")])
def test_parse_sse_refuses(value: Any, env: str) -> None:
    with pytest.raises(ValueError):
        parse_sse(value, env)


def test_data_class() -> None:
    assert data_class(tenant_key(TENANT, "files", "f1", "v2")) == "files"
    assert data_class("tenant/x") == "other"
    assert data_class("packs/pmo.zip") == "other"


async def test_lifecycle_is_read_back_and_removed_when_the_store_changes_it() -> None:
    fake = FakeS3()
    store = _store(fake)
    assert await store.lifecycle() == []
    config = lifecycle_rules(export_days=3)
    await store.apply_lifecycle(config)
    assert await store.lifecycle() == config["Rules"]
    assert config["Rules"][0]["Filter"] == {"Tag": {"Key": "tela-class", "Value": "exports"}}

    seaweed = FakeS3()
    seaweed.mangle_rules = True
    with pytest.raises(LifecycleNotSupported):
        await _store(seaweed).apply_lifecycle(config)
    assert seaweed.rules is None  # the whole-bucket expiry it kept was removed again


async def test_storage_health() -> None:
    fake = FakeS3()
    assert await _store(fake).healthy()
    fake.head_error = ClientError({"Error": {"Code": "404"}}, "HeadBucket")
    assert await _store(fake).healthy()  # created on first write
    fake.head_error = ClientError({"Error": {"Code": "403"}}, "HeadBucket")
    assert not await _store(fake).healthy()
    fake.head_error = ConnectionError("down")
    assert not await _store(fake).healthy()
