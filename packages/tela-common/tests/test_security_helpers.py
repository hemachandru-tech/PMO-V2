import time
from pathlib import Path

import pytest

from tela_common import service_auth
from tela_common.errors import ErrorCode, TelaError
from tela_common.objectstore import tenant_key
from tela_common.secrets import FileSecretsStore, assert_strong_key, build_secrets_store, secret_ref

TENANT = "0192f3b2-0000-7000-8000-000000000001"
KEY = b"0123456789abcdefghijklmnopqrstuvwxyzABCDEF"


def test_service_token_roundtrip_and_rejections(monkeypatch: pytest.MonkeyPatch) -> None:
    token = service_auth.issue(KEY, sub="tela-api", aud="tela-llm-gateway", ttl_s=30, claims={"tenant": TENANT})
    assert service_auth.verify(KEY, token, aud="tela-llm-gateway")["tenant"] == TENANT

    with pytest.raises(TelaError) as wrong_aud:
        service_auth.verify(KEY, token, aud="tela-connector-runner")
    assert wrong_aud.value.code == ErrorCode.SIGNATURE_INVALID

    body, sig = token.split(".")
    with pytest.raises(TelaError):
        service_auth.verify(KEY, f"{body}x.{sig}", aud="tela-llm-gateway")
    with pytest.raises(TelaError):
        service_auth.verify(b"another-key-0123456789abcdefghijk", token, aud="tela-llm-gateway")

    expired = service_auth.issue(KEY, sub="tela-api", aud="a", ttl_s=1)
    monkeypatch.setattr(time, "time", lambda: 10_000_000_000.0)
    with pytest.raises(TelaError):
        service_auth.verify(KEY, expired, aud="a")


@pytest.mark.parametrize("value", [None, "", "short", "0" * 64, "ab" * 32])
def test_weak_or_placeholder_keys_refused(value: str | None) -> None:
    with pytest.raises(SystemExit):
        assert_strong_key(value, name="TEST_KEY")


def test_strong_key_accepted() -> None:
    assert assert_strong_key(KEY.decode(), name="TEST_KEY") == KEY


async def test_file_secrets_store_roundtrip(tmp_path: Path) -> None:
    store = FileSecretsStore(tmp_path / "s")
    ref = secret_ref(TENANT, "connections", "abc-123")
    await store.put(ref, {"password": "p"})
    assert await store.get(ref) == {"password": "p"}
    stored = next((tmp_path / "s").iterdir())
    assert oct(stored.stat().st_mode & 0o777) == "0o600"
    await store.delete(ref)
    with pytest.raises(TelaError) as exc:
        await store.get(ref)
    assert exc.value.code == ErrorCode.SECRETS_UNAVAILABLE
    assert await store.healthy()


def test_secret_refs_are_validated() -> None:
    with pytest.raises(ValueError):
        secret_ref("not-a-uuid", "connections", "x")
    with pytest.raises(ValueError):
        secret_ref(TENANT, "connections", "../../etc/passwd")


def test_file_backend_refused_outside_dev() -> None:
    with pytest.raises(SystemExit):
        build_secrets_store("file", None, "prod")
    with pytest.raises(SystemExit):
        build_secrets_store("azure_keyvault", None, "prod")


def test_object_keys_are_tenant_prefixed_and_safe() -> None:
    assert tenant_key(TENANT, "files", "f1", "v1.xlsx") == f"tenant/{TENANT}/files/f1/v1.xlsx"
    for bad in ("..", "a/b", ""):
        with pytest.raises(ValueError):
            tenant_key(TENANT, "files", bad)
    with pytest.raises(ValueError):
        tenant_key("tenant-x", "files")
