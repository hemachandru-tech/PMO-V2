"""SSH key files for tunnelled connections (ENH-003): PuTTY .ppk v2/v3, PEM and OpenSSH, with and without a
passphrase, all normalised to an unencrypted OpenSSH key with the fingerprint ssh-keygen prints."""

import json
import re
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed25519, rsa

from tela_common.sshkeys import (
    MSG_BAD_PASSPHRASE,
    MSG_NEEDS_PASSPHRASE,
    MSG_NOT_A_KEY,
    MSG_PUBLIC_KEY,
    KeyFileError,
    load_certificate,
    load_private_key,
    load_public_key,
    mongo_tunnel_target,
)

FIXTURES = Path(__file__).parent / "fixtures" / "ssh"
FINGERPRINTS: dict[str, str] = json.loads((FIXTURES / "fingerprints.json").read_text())
PASSPHRASE = "tela-test"


@pytest.mark.parametrize("name", sorted(FINGERPRINTS))
def test_putty_keys_match_ssh_keygen(name: str) -> None:
    text = (FIXTURES / name).read_text()
    key = load_private_key(text, PASSPHRASE if "passphrase" in name else None)
    assert key.fingerprint == FINGERPRINTS[name]
    assert key.key_type == text.split(": ", 1)[1].split("\n", 1)[0]
    # The output is an unencrypted OpenSSH key with the same public half.
    assert key.openssh.startswith("-----BEGIN OPENSSH PRIVATE KEY-----")
    again = load_private_key(key.openssh)
    assert again.fingerprint == key.fingerprint  # (the text differs: OpenSSH files carry random check bytes)
    assert "PRIVATE" not in repr(key)  # never in logs or tracebacks


def test_windows_line_endings_and_extra_passphrase() -> None:
    text = (FIXTURES / "rsa2048-v3.ppk").read_text().replace("\n", "\r\n")
    assert load_private_key(text.encode()).fingerprint == FINGERPRINTS["rsa2048-v3.ppk"]
    # A passphrase typed for a key that has none is ignored.
    assert load_private_key(text, "anything").fingerprint == FINGERPRINTS["rsa2048-v3.ppk"]


@pytest.mark.parametrize("name", ["ed25519-v3-passphrase.ppk", "rsa2048-v2-passphrase.ppk",
                                  "ecdsa384-v3-passphrase.ppk"])
def test_putty_passphrase_errors(name: str) -> None:
    text = (FIXTURES / name).read_text()
    with pytest.raises(KeyFileError, match=re.escape(MSG_NEEDS_PASSPHRASE)):
        load_private_key(text)
    with pytest.raises(KeyFileError, match=re.escape(MSG_BAD_PASSPHRASE)):
        load_private_key(text, "wrong")


def test_putty_tampering_and_weak_keys_are_refused() -> None:
    text = (FIXTURES / "ed25519-v3.ppk").read_text()
    with pytest.raises(KeyFileError, match="not a private key"):
        load_private_key(text.replace("Comment: tela test key", "Comment: someone else"))  # MAC covers it
    with pytest.raises(KeyFileError, match="not a private key"):
        load_private_key(text.split("Private-Lines")[0])
    with pytest.raises(KeyFileError, match="1024 bits"):
        load_private_key((FIXTURES / "rsa1024-too-small.ppk").read_text())
    with pytest.raises(KeyFileError, match="too old"):
        load_private_key("PuTTY-User-Key-File-1: ssh-rsa\n")


def _pem(key: object, fmt: serialization.PrivateFormat, password: bytes | None = None) -> bytes:
    enc = serialization.BestAvailableEncryption(password) if password else serialization.NoEncryption()
    return key.private_bytes(serialization.Encoding.PEM, fmt, enc)  # type: ignore[attr-defined, no-any-return]


@pytest.mark.parametrize(("make", "fmt", "expected"), [
    (lambda: rsa.generate_private_key(65537, 2048), serialization.PrivateFormat.TraditionalOpenSSL, "ssh-rsa"),
    (lambda: rsa.generate_private_key(65537, 3072), serialization.PrivateFormat.PKCS8, "ssh-rsa"),
    (lambda: ec.generate_private_key(ec.SECP256R1()), serialization.PrivateFormat.TraditionalOpenSSL,
     "ecdsa-sha2-nistp256"),
    (ed25519.Ed25519PrivateKey.generate, serialization.PrivateFormat.OpenSSH, "ssh-ed25519"),
    (ed25519.Ed25519PrivateKey.generate, serialization.PrivateFormat.PKCS8, "ssh-ed25519"),
])
def test_pem_and_openssh_keys(make: object, fmt: serialization.PrivateFormat, expected: str) -> None:
    key = make()  # type: ignore[operator]
    plain = load_private_key(_pem(key, fmt))
    assert plain.key_type == expected
    protected = _pem(key, fmt, b"s3cret")
    assert load_private_key(protected, "s3cret").fingerprint == plain.fingerprint
    with pytest.raises(KeyFileError, match=re.escape(MSG_NEEDS_PASSPHRASE)):
        load_private_key(protected)
    with pytest.raises(KeyFileError, match=re.escape(MSG_BAD_PASSPHRASE)):
        load_private_key(protected, "wrong")


def test_not_a_private_key() -> None:
    key = ed25519.Ed25519PrivateKey.generate()
    public = key.public_key().public_bytes(serialization.Encoding.OpenSSH, serialization.PublicFormat.OpenSSH)
    with pytest.raises(KeyFileError, match=re.escape(MSG_PUBLIC_KEY)):
        load_private_key(public)
    with pytest.raises(KeyFileError, match=re.escape(MSG_NOT_A_KEY)):
        load_private_key("hello")
    with pytest.raises(KeyFileError, match=re.escape(MSG_NOT_A_KEY)):
        load_private_key(b"x" * 20_000)
    with pytest.raises(KeyFileError, match=re.escape(MSG_NOT_A_KEY)):
        load_private_key("-----BEGIN RSA PRIVATE KEY-----\nbm9wZQ==\n-----END RSA PRIVATE KEY-----\n")


# --------------------------------------------------------------------------- ENH-004: certificates, CA keys, targets


def _cert(ca: ed25519.Ed25519PrivateKey, key: ed25519.Ed25519PrivateKey, *, host: bool = False,
          after: int = 0, before: int = 2**64 - 1) -> str:
    cert = (serialization.SSHCertificateBuilder().public_key(key.public_key()).serial(1)
            .type(serialization.SSHCertificateType.HOST if host else serialization.SSHCertificateType.USER)
            .key_id(b"tela").valid_principals([b"tunnel"]).valid_after(after).valid_before(before).sign(ca))
    return cert.public_bytes().decode()


def test_user_certificates() -> None:
    ca, key, other = (ed25519.Ed25519PrivateKey.generate() for _ in range(3))
    mine = load_private_key(_pem(key, serialization.PrivateFormat.OpenSSH))
    cert = load_certificate(_cert(ca, key), mine.fingerprint)
    ca_fp = load_public_key(ca.public_key().public_bytes(serialization.Encoding.OpenSSH,
                                                         serialization.PublicFormat.OpenSSH).decode())[1]
    assert cert.summary() == {"key_id": "tela", "principals": ["tunnel"], "ca_fingerprint": ca_fp, "valid_after": 0,
                              "valid_before": None}
    for text, message in [(_cert(ca, other), "different key"), (_cert(ca, key, host=True), "host certificate"),
                          (_cert(ca, key, before=1_000), "expired"), (_cert(ca, key, after=2**40), "not valid yet"),
                          ("ssh-ed25519 AAAA", "not an OpenSSH certificate")]:
        with pytest.raises(KeyFileError, match=message):
            load_certificate(text, mine.fingerprint)


def test_ca_public_keys_and_mongo_targets() -> None:
    ca = ed25519.Ed25519PrivateKey.generate().public_key().public_bytes(
        serialization.Encoding.OpenSSH, serialization.PublicFormat.OpenSSH).decode()
    line, fp = load_public_key(f"@cert-authority *.corp {ca} ops CA")
    assert line == ca and fp.startswith("SHA256:")
    with pytest.raises(KeyFileError):
        load_public_key("not a key")
    assert mongo_tunnel_target("mongodb://db.internal:27018/tpg?replicaSet=rs0") == ("db.internal", 27018)
    assert mongo_tunnel_target("mongodb://[::1]") == ("::1", 27017)
    for bad in ("mongodb+srv://c.example.com", "mongodb://a,b", "postgres://x", "mongodb://db:x"):
        with pytest.raises(ValueError):
            mongo_tunnel_target(bad)
