"""SSH private key files for tunnelled connections (ENH-003).

Admins upload the key file they already have: OpenSSH (``id_ed25519``), PEM (``.pem``: PKCS#1, PKCS#8 or EC)
or PuTTY (``.ppk`` versions 2 and 3). The key is read once, with its passphrase if it has one, and handed back
as an unencrypted OpenSSH key for the secrets store, plus the public facts the UI may show (type, size,
SHA256 fingerprint). The passphrase is not kept: the secrets store is the key's protection from then on, and
the runner only ever sees one format.

An OpenSSH user certificate (``id_ed25519-cert.pub``, ENH-004) may come with the key: it must certify that key,
be a user certificate and still be valid. Its principals, validity and signing CA are shown; the certificate
itself is stored next to the key. Host CAs (``@cert-authority`` lines) are read with ``load_public_key``.

PuTTY files are parsed here because neither cryptography nor asyncssh reads them. Encrypted version 3 files
derive their key with Argon2; only Argon2id (PuTTYgen's default) is supported.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import struct
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed25519, rsa
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

MAX_KEY_FILE_BYTES = 16 * 1024
MIN_RSA_BITS = 2048
_CURVES: dict[str, ec.EllipticCurve] = {"nistp256": ec.SECP256R1(), "nistp384": ec.SECP384R1(),
                                        "nistp521": ec.SECP521R1()}

MSG_NOT_A_KEY = "This is not a private key file Tela can read. Use an OpenSSH, PEM (.pem) or PuTTY (.ppk) private key."
MSG_PUBLIC_KEY = "This is a public key. Upload the private key file instead."
MSG_NEEDS_PASSPHRASE = "This key is protected by a passphrase. Enter the passphrase."  # noqa: S105 - a message, not a secret
MSG_NOT_A_CERT = "This is not an OpenSSH certificate. Choose the file ending in -cert.pub."
MSG_NOT_A_PUBLIC_KEY = "Paste the CA's public key: one line starting with ssh-ed25519, ecdsa-sha2- or ssh-rsa."
MSG_BAD_PASSPHRASE = "The passphrase does not match this key."  # noqa: S105


class KeyFileError(ValueError):
    """The upload is not a usable private key; the message says how to fix it."""


@dataclass(frozen=True)
class SshKey:
    openssh: str = field(repr=False)  # unencrypted private key: for the secrets store only
    key_type: str  # ssh-ed25519 | ssh-rsa | ecdsa-sha2-nistp256 ...
    bits: int
    fingerprint: str  # SHA256:<base64>, as ssh-keygen -l prints it


def fingerprint(public_blob: bytes) -> str:
    return "SHA256:" + base64.b64encode(hashlib.sha256(public_blob).digest()).decode().rstrip("=")


def load_private_key(data: bytes | str, passphrase: str | None = None) -> SshKey:
    raw = data.encode() if isinstance(data, str) else data
    if len(raw) > MAX_KEY_FILE_BYTES:
        raise KeyFileError(MSG_NOT_A_KEY)
    text = raw.decode("utf-8", errors="replace").replace("\r\n", "\n").strip()
    secret = passphrase.encode() if passphrase else None
    if text.startswith("PuTTY-User-Key-File-"):
        key = _load_ppk(text, secret)
    elif text.startswith(("ssh-", "ecdsa-", "---- BEGIN SSH2 PUBLIC KEY")) or "PUBLIC KEY-----" in text[:40]:
        raise KeyFileError(MSG_PUBLIC_KEY)
    elif "-----BEGIN OPENSSH PRIVATE KEY-----" in text:
        key = _load_pem(lambda pw: serialization.load_ssh_private_key(text.encode(), pw), secret)
    elif "-----BEGIN" in text and "PRIVATE KEY-----" in text:
        key = _load_pem(lambda pw: serialization.load_pem_private_key(text.encode(), pw), secret)
    else:
        raise KeyFileError(MSG_NOT_A_KEY)
    return _describe(key)


Key = rsa.RSAPrivateKey | ed25519.Ed25519PrivateKey | ec.EllipticCurvePrivateKey


def _load_pem(load: Callable[[bytes | None], object], secret: bytes | None) -> Key:
    try:
        key = load(secret)
    except TypeError as exc:  # encrypted without a password, or a password for an unencrypted key
        if secret is None:
            raise KeyFileError(MSG_NEEDS_PASSPHRASE) from exc
        try:
            key = load(None)  # a passphrase was typed for a key that has none
        except (TypeError, ValueError):
            raise KeyFileError(MSG_BAD_PASSPHRASE) from exc
    except ValueError as exc:
        # PEM says "bad decrypt"/"incorrect password"; OpenSSH keys fail their check bytes ("broken checksum").
        if secret is not None and any(w in str(exc).lower() for w in ("password", "decrypt", "checksum")):
            raise KeyFileError(MSG_BAD_PASSPHRASE) from exc
        raise KeyFileError(MSG_NOT_A_KEY) from exc
    except Exception as exc:  # unsupported algorithms surface as various errors
        raise KeyFileError(MSG_NOT_A_KEY) from exc
    if not isinstance(key, (rsa.RSAPrivateKey, ed25519.Ed25519PrivateKey, ec.EllipticCurvePrivateKey)):
        raise KeyFileError("Use an Ed25519, ECDSA or RSA key. DSA keys are no longer accepted by SSH servers.")
    return key


def _describe(key: Key) -> SshKey:
    if isinstance(key, rsa.RSAPrivateKey) and key.key_size < MIN_RSA_BITS:
        raise KeyFileError(f"This RSA key has {key.key_size} bits. Use at least {MIN_RSA_BITS}.")
    public = key.public_key().public_bytes(serialization.Encoding.OpenSSH, serialization.PublicFormat.OpenSSH)
    key_type, blob = public.split()[:2]
    openssh = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.OpenSSH,
                                serialization.NoEncryption()).decode()
    bits = 256 if isinstance(key, ed25519.Ed25519PrivateKey) else key.key_size
    return SshKey(openssh=openssh, key_type=key_type.decode(), bits=bits,
                  fingerprint=fingerprint(base64.b64decode(blob)))


# --------------------------------------------------------------------------- PuTTY .ppk


class _Wire:
    """SSH wire-format reader (RFC 4251 string and mpint)."""

    def __init__(self, data: bytes) -> None:
        self.data, self.pos = data, 0

    def string(self) -> bytes:
        if self.pos + 4 > len(self.data):
            raise KeyFileError(MSG_NOT_A_KEY)
        (n,) = struct.unpack(">I", self.data[self.pos:self.pos + 4])
        start, self.pos = self.pos + 4, self.pos + 4 + n
        if self.pos > len(self.data):
            raise KeyFileError(MSG_NOT_A_KEY)
        return self.data[start:self.pos]

    def mpint(self) -> int:
        return int.from_bytes(self.string(), "big", signed=True)


def _ssh_string(value: bytes) -> bytes:
    return struct.pack(">I", len(value)) + value


def _ppk_fields(text: str) -> dict[str, str]:
    """Header lines, with the base64 blocks after Public-Lines / Private-Lines joined into one value."""
    lines = text.split("\n")
    out: dict[str, str] = {}
    i = 0
    while i < len(lines):
        name, sep, value = lines[i].partition(": ")
        if not sep:
            raise KeyFileError(MSG_NOT_A_KEY)
        value = value.strip()
        if name in ("Public-Lines", "Private-Lines"):
            if not value.isdigit() or i + 1 + int(value) > len(lines):
                raise KeyFileError(MSG_NOT_A_KEY)
            count = int(value)
            out[name.split("-")[0]] = "".join(s.strip() for s in lines[i + 1:i + 1 + count])
            i += 1 + count
        else:
            out[name] = value
            i += 1
    return out


def _load_ppk(text: str, secret: bytes | None) -> Key:
    version = text[len("PuTTY-User-Key-File-"):].split(":", 1)[0]
    if version not in ("2", "3"):
        raise KeyFileError("This PuTTY key file is too old. Re-save it with PuTTYgen, or export it as OpenSSH.")
    f = _ppk_fields(text)
    algorithm = f.get(f"PuTTY-User-Key-File-{version}", "")
    encryption = f.get("Encryption", "")
    try:
        public = base64.b64decode(f["Public"], validate=True)
        private = base64.b64decode(f["Private"], validate=True)
        mac = bytes.fromhex(f["Private-MAC"])
    except (KeyError, ValueError) as exc:
        raise KeyFileError(MSG_NOT_A_KEY) from exc
    if encryption not in ("none", "aes256-cbc"):
        raise KeyFileError(MSG_NOT_A_KEY)
    encrypted = encryption == "aes256-cbc"
    if encrypted and not secret:
        raise KeyFileError(MSG_NEEDS_PASSPHRASE)
    cipher_key, iv, mac_key = _ppk_keys(version, f, secret if encrypted and secret else b"", encrypted)
    if encrypted:
        if len(private) % 16:
            raise KeyFileError(MSG_NOT_A_KEY)
        decryptor = Cipher(algorithms.AES(cipher_key), modes.CBC(iv)).decryptor()
        private = decryptor.update(private) + decryptor.finalize()
    signed = b"".join(_ssh_string(v) for v in (algorithm.encode(), encryption.encode(),
                                                f.get("Comment", "").encode(), public, private))
    if not hmac.compare_digest(hmac.new(mac_key, signed, "sha1" if version == "2" else "sha256").digest(), mac):
        raise KeyFileError(MSG_BAD_PASSPHRASE if encrypted else MSG_NOT_A_KEY)
    return _ppk_key(algorithm, _Wire(public), _Wire(private))


def _ppk_keys(version: str, f: dict[str, str], passphrase: bytes, encrypted: bool) -> tuple[bytes, bytes, bytes]:
    """Cipher key, IV and MAC key. Version 2 hashes the passphrase with SHA-1 (the format's own choice, not ours);
    version 3 uses Argon2 when encrypted and an empty MAC key when not."""
    if version == "2":
        cipher_key = (hashlib.sha1(b"\0\0\0\0" + passphrase).digest()  # noqa: S324
                      + hashlib.sha1(b"\0\0\0\1" + passphrase).digest())[:32]  # noqa: S324
        return cipher_key, bytes(16), hashlib.sha1(b"putty-private-key-file-mac-key" + passphrase).digest()  # noqa: S324
    return _argon2(f, passphrase) if encrypted else (b"", b"", b"")


def _argon2(f: dict[str, str], passphrase: bytes) -> tuple[bytes, bytes, bytes]:
    if f.get("Key-Derivation") != "Argon2id":
        raise KeyFileError("This PuTTY key uses an Argon2 variant Tela cannot read. Re-save it with PuTTYgen's "
                           "default settings (Argon2id), or export it as OpenSSH.")
    from cryptography.hazmat.primitives.kdf.argon2 import Argon2id

    try:
        out = Argon2id(salt=bytes.fromhex(f["Argon2-Salt"]), length=80, iterations=int(f["Argon2-Passes"]),
                       lanes=int(f["Argon2-Parallelism"]), memory_cost=int(f["Argon2-Memory"])).derive(passphrase)
    except (KeyError, ValueError) as exc:
        raise KeyFileError(MSG_NOT_A_KEY) from exc
    return out[:32], out[32:48], out[48:]


def _ppk_key(algorithm: str, public: _Wire, private: _Wire) -> Key:
    if public.string().decode() != algorithm:
        raise KeyFileError(MSG_NOT_A_KEY)
    if algorithm == "ssh-rsa":
        e, n = public.mpint(), public.mpint()
        d, p, q, iqmp = private.mpint(), private.mpint(), private.mpint(), private.mpint()
        try:
            return rsa.RSAPrivateNumbers(p, q, d, d % (p - 1), d % (q - 1), iqmp,
                                         rsa.RSAPublicNumbers(e, n)).private_key()
        except ValueError as exc:
            raise KeyFileError(MSG_NOT_A_KEY) from exc
    if algorithm == "ssh-ed25519":
        expected = public.string()
        seed = private.string()
        key = ed25519.Ed25519PrivateKey.from_private_bytes(seed[:32])
    elif algorithm.startswith("ecdsa-sha2-"):
        curve = _CURVES.get(public.string().decode())
        if curve is None:
            raise KeyFileError(MSG_NOT_A_KEY)
        expected = public.string()
        key = ec.derive_private_key(private.mpint(), curve)  # type: ignore[assignment]
    elif algorithm == "ssh-dss":
        raise KeyFileError("Use an Ed25519, ECDSA or RSA key. DSA keys are no longer accepted by SSH servers.")
    else:
        raise KeyFileError(f"Tela cannot use {algorithm} keys yet. Use an Ed25519, ECDSA or RSA key.")
    raw = (key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
           if isinstance(key, ed25519.Ed25519PrivateKey)
           else key.public_key().public_bytes(serialization.Encoding.X962,
                                               serialization.PublicFormat.UncompressedPoint))
    if raw != expected:  # the private half must belong to the public half the MAC covers
        raise KeyFileError(MSG_NOT_A_KEY)
    return key


# --------------------------------------------------------------------------- certificates and CA keys


@dataclass(frozen=True)
class SshCertificate:
    text: str = field(repr=False)  # the -cert.pub line, normalised
    key_id: str
    principals: list[str]
    valid_after: int  # unix seconds
    valid_before: int  # unix seconds; 2**64 - 1 means "forever"
    ca_fingerprint: str

    def summary(self) -> dict[str, Any]:
        forever = self.valid_before >= 2**63
        return {"key_id": self.key_id, "principals": self.principals, "ca_fingerprint": self.ca_fingerprint,
                "valid_after": self.valid_after, "valid_before": None if forever else self.valid_before}


def load_certificate(data: bytes | str, key_fingerprint: str, *, now: float | None = None) -> SshCertificate:
    """An OpenSSH user certificate for the key with ``key_fingerprint`` (the API never reads a saved key back,
    so a certificate is matched by fingerprint); refused when it certifies another key, is a host certificate,
    or is not valid now."""
    text = (data.decode("utf-8", errors="replace") if isinstance(data, bytes) else data).strip()
    if len(text) > MAX_KEY_FILE_BYTES or "-cert-v01@openssh.com " not in text.split("\n", 1)[0] + " ":
        raise KeyFileError(MSG_NOT_A_CERT)
    line = text.split("\n", 1)[0].strip()
    try:
        cert = serialization.load_ssh_public_identity(line.encode())
    except (ValueError, TypeError) as exc:
        raise KeyFileError(MSG_NOT_A_CERT) from exc
    if not isinstance(cert, serialization.SSHCertificate):
        raise KeyFileError(MSG_NOT_A_CERT)
    if cert.type != serialization.SSHCertificateType.USER:
        raise KeyFileError("This is a host certificate. Use the user certificate issued for this key.")
    if _public_fingerprint(cert.public_key()) != key_fingerprint:
        raise KeyFileError("This certificate was issued for a different key.")
    moment = int(now if now is not None else time.time())
    if moment >= cert.valid_before:
        raise KeyFileError("This certificate has expired. Ask your SSH certificate authority for a new one.")
    if moment < cert.valid_after:
        raise KeyFileError("This certificate is not valid yet.")
    try:
        cert.verify_cert_signature()
    except Exception as exc:  # InvalidSignature and unsupported CA algorithms
        raise KeyFileError("This certificate's signature does not check out.") from exc
    return SshCertificate(
        text=line, key_id=cert.key_id.decode(errors="replace"),
        principals=[p.decode(errors="replace") for p in cert.valid_principals],
        valid_after=int(cert.valid_after), valid_before=int(cert.valid_before),
        ca_fingerprint=_public_fingerprint(cert.signature_key()))


def load_public_key(data: str) -> tuple[str, str]:
    """A CA public key line (``ssh-ed25519 AAAA… comment``, optionally after ``@cert-authority <hosts>``):
    the normalised line without comment, and its fingerprint."""
    parts = data.strip().split()
    if parts[:1] == ["@cert-authority"]:
        parts = parts[2:]
    if len(parts) < 2:
        raise KeyFileError(MSG_NOT_A_PUBLIC_KEY)
    line = f"{parts[0]} {parts[1]}"
    try:
        key = serialization.load_ssh_public_key(line.encode())
    except Exception as exc:  # unsupported algorithms raise various errors
        raise KeyFileError(MSG_NOT_A_PUBLIC_KEY) from exc
    return line, _public_fingerprint(key)


def _public_fingerprint(public: Any) -> str:
    blob = public.public_bytes(serialization.Encoding.OpenSSH, serialization.PublicFormat.OpenSSH).split()[1]
    return fingerprint(base64.b64decode(blob))


# --------------------------------------------------------------------------- tunnel targets


def mongo_tunnel_target(uri: str) -> tuple[str, int]:
    """The one host and port a MongoDB connection string names, for an SSH tunnel (ENH-004). A tunnel forwards a
    single address, so ``mongodb+srv://`` and several hosts (replica set seed lists) are refused."""
    from urllib.parse import urlsplit

    if uri.startswith("mongodb+srv://"):
        raise ValueError("an SSH tunnel needs a mongodb:// connection string with one host, not mongodb+srv://")
    if not uri.startswith("mongodb://"):
        raise ValueError("use a mongodb:// connection string")
    parts = urlsplit(uri)
    if "," in parts.netloc or not parts.hostname:
        raise ValueError("an SSH tunnel reaches one MongoDB host: list a single host in the connection string")
    try:
        port = parts.port or 27017
    except ValueError as exc:
        raise ValueError("the port in the connection string is not a number") from exc
    return parts.hostname, port
