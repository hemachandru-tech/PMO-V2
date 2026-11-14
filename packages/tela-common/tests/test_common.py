import uuid

from tela_common.errors import ErrorCode, TelaError
from tela_common.ids import uuid7
from tela_common.logging import REDACTED, hash_identifier, redact


def test_uuid7_version_variant_and_order() -> None:
    ids = [uuid7() for _ in range(2000)]
    assert all(i.version == 7 for i in ids)
    assert all(i.variant == uuid.RFC_4122 for i in ids)
    assert ids == sorted(ids), "uuid7 must be monotonic within a process"
    assert len(set(ids)) == len(ids)


def test_redaction_of_sensitive_keys_and_values() -> None:
    event = {
        "event": "connection.tested",
        "password": "hunter2",
        "api_key": "abc",
        "prompt": "what is the overdue count",
        "rows": [[1, 2]],
        "detail": "dsn postgres://tela_ro:s3cret@db.internal:5432/tpg",
        "nested": {"token": "x", "ok": "fine", "key": "sk-ant-abcdefghijklmnopqrstuvwxyz"},
        "user_id": "plain",
        "prompt_tokens": 120,
    }
    out = redact(dict(event))
    assert out["password"] == REDACTED
    assert out["api_key"] == REDACTED
    assert out["prompt"] == REDACTED
    assert out["rows"] == REDACTED
    assert out["detail"] == REDACTED
    assert out["nested"]["token"] == REDACTED
    assert out["nested"]["key"] == REDACTED
    assert out["nested"]["ok"] == "fine"
    assert out["event"] == "connection.tested"
    assert out["prompt_tokens"] == 120


def test_hash_identifier_is_stable_and_not_plaintext() -> None:
    assert hash_identifier("user-1") == hash_identifier("user-1")
    assert hash_identifier("user-1") != "user-1"
    assert hash_identifier(None) is None


def test_tela_error_status_mapping() -> None:
    assert TelaError(ErrorCode.TOKEN_INVALID, "x").status == 401
    assert TelaError(ErrorCode.FORBIDDEN, "x").status == 403
    assert TelaError(ErrorCode.PENDING_APPROVAL, "x").status == 409
