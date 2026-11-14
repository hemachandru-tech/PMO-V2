"""Signed execution envelopes (US-021; technical_document §Execution envelope).

The API signs every statement it hands to the connector runner with an
Ed25519 private key; the runner holds only the public key, so it can verify
but never mint an envelope. The envelope binds the statement hash, tenant,
connection, scope hash, limits, allowed objects and an expiry. The runner
recomputes the statement hash before it executes anything.
"""

from __future__ import annotations

import base64
import hashlib
import json
import time
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from tela_common.errors import ErrorCode, TelaError

ENVELOPE_TTL_S = 60


def statement_sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def canonical(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()


def load_private_key(pem: bytes) -> Ed25519PrivateKey:
    key = serialization.load_pem_private_key(pem, password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise SystemExit("execution signing key must be an Ed25519 private key")
    return key


def load_public_key(pem: bytes) -> Ed25519PublicKey:
    key = serialization.load_pem_public_key(pem)
    if not isinstance(key, Ed25519PublicKey):
        raise SystemExit("execution verify key must be an Ed25519 public key")
    return key


def sign(key: Ed25519PrivateKey, payload: dict[str, Any], *, ttl_s: int = ENVELOPE_TTL_S) -> dict[str, Any]:
    body = {**payload, "exp": int(time.time()) + ttl_s}
    sig = base64.b64encode(key.sign(canonical(body))).decode()
    return {"payload": body, "signature": sig}


def verify(key: Ed25519PublicKey, envelope: dict[str, Any], *, statement: str | None = None) -> dict[str, Any]:
    """Return the payload of a valid, unexpired envelope; raise ``signature_invalid`` otherwise."""
    try:
        payload = envelope["payload"]
        sig = base64.b64decode(envelope["signature"], validate=True)
        key.verify(sig, canonical(payload))
    except (KeyError, TypeError, ValueError, InvalidSignature) as exc:
        raise TelaError(ErrorCode.SIGNATURE_INVALID, "execution envelope rejected") from exc
    if int(payload.get("exp", 0)) < int(time.time()):
        raise TelaError(ErrorCode.SIGNATURE_INVALID, "execution envelope expired")
    if statement is not None and statement_sha256(statement) != payload.get("statement_sha256"):
        raise TelaError(ErrorCode.SIGNATURE_INVALID, "statement does not match its signed hash")
    return dict(payload)


def generate_keypair() -> tuple[bytes, bytes]:
    """PEM (private, public) — used by dev-setup and tests."""
    key = Ed25519PrivateKey.generate()
    private = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                serialization.NoEncryption())
    public = key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
    return private, public
