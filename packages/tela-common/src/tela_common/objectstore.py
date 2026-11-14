"""S3-compatible object storage (US-008; COMP-014; API-068).

Keys are always prefixed by tenant (``tenant/<uuid>/<class>/...``) so one tenant can
never address another's files. Downloads go through short-lived pre-signed
URLs (exports 15 minutes). SeaweedFS in Compose; AWS S3 or an S3-compatible
gateway in other profiles. An Azure Blob adapter is planned with the optional
Azure profile (US-122).

Every put asks for server-side encryption and checks that the store confirms it
(a gateway that silently ignores the header gets the object deleted again), and
tags the object with its data class (``tela-class=exports|files|knowledge``) so
bucket lifecycle rules can act per class (db_scheme §11). The API's own sweeps
stay the primary retention mechanism; the bucket rules are a backstop that an
operator applies with ``tela-admin storage-lifecycle --apply``.
"""

from __future__ import annotations

import asyncio
import contextlib
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote

from tela_common.errors import ErrorCode, TelaError

_SEGMENT = re.compile(r"^[A-Za-z0-9_.\-]{1,120}$")

CLASS_TAG = "tela-class"
SSE_MODES = ("AES256", "aws:kms", "none")


def parse_sse(value: str | bool | None, env: str) -> str:
    """``TELA_OBJECT_STORE_SSE``: ``AES256`` (default), ``aws:kms`` or ``none`` (also true/false).

    Encryption may only be switched off in local development, where SeaweedFS's gateway has none.
    """
    raw = "AES256" if value is None or value is True else ("none" if value is False else str(value).strip())
    mode = {"true": "AES256", "false": "none", "off": "none", "": "AES256"}.get(raw.lower(), raw)
    if mode not in SSE_MODES:
        raise ValueError(f"object store encryption must be one of {', '.join(SSE_MODES)}")
    if mode == "none" and env not in ("local", "dev", "test"):
        raise ValueError("object store encryption can only be switched off in local development")
    return mode


def data_class(key: str) -> str:
    """The data class of a tenant key: ``tenant/<uuid>/<class>/...``."""
    parts = key.split("/")
    return parts[2] if len(parts) > 3 and parts[0] == "tenant" else "other"


def lifecycle_rules(export_days: int = 2, abort_upload_days: int = 1,
                    noncurrent_days: int = 1) -> dict[str, Any]:
    """Bucket rules (db_scheme §11). Exports are kept 24 h by the API's hourly sweep; the bucket deletes any
    left after ``export_days``. Uploaded files and knowledge documents follow per-file retention in the API, so
    they get no expiry here. Unfinished multipart uploads and deleted versions (versioned buckets) go quickly."""
    return {"Rules": [
        {"ID": "tela-exports-expire", "Status": "Enabled",
         "Filter": {"Tag": {"Key": CLASS_TAG, "Value": "exports"}},
         "Expiration": {"Days": export_days}},
        {"ID": "tela-abort-uploads", "Status": "Enabled", "Filter": {"Prefix": "tenant/"},
         "AbortIncompleteMultipartUpload": {"DaysAfterInitiation": abort_upload_days}},
        {"ID": "tela-noncurrent-expire", "Status": "Enabled", "Filter": {"Prefix": "tenant/"},
         "NoncurrentVersionExpiration": {"NoncurrentDays": noncurrent_days}},
    ]}


def _normalise_rules(rules: list[dict[str, Any]]) -> list[tuple[Any, ...]]:
    def one(r: dict[str, Any]) -> tuple[Any, ...]:
        flt = r.get("Filter") or ({"Prefix": r["Prefix"]} if "Prefix" in r else {})
        return (r.get("ID"), r.get("Status"), repr(sorted(flt.items())),
                repr(r.get("Expiration")), repr(r.get("AbortIncompleteMultipartUpload")),
                repr(r.get("NoncurrentVersionExpiration")))
    return sorted(one(r) for r in rules)


class LifecycleNotSupported(RuntimeError):
    """The store accepted the rules but stored something else (SeaweedFS drops tag filters, for example)."""


def tenant_key(tenant_id: str, *parts: str) -> str:
    if not re.match(r"^[0-9a-f\-]{36}$", tenant_id):
        raise ValueError("tenant id must be a uuid")
    for part in parts:
        if not _SEGMENT.match(part) or part in (".", ".."):
            raise ValueError(f"invalid key segment: {part!r}")
    return "/".join(("tenant", tenant_id, *parts))


@dataclass
class ObjectStore:
    endpoint: str | None
    bucket: str
    access_key: str | None = None
    secret_key: str | None = None
    region: str = "us-east-1"
    sse: str = "AES256"  # AES256 | aws:kms | none (local development only, see parse_sse)
    kms_key_id: str | None = None
    _client_obj: Any = field(default=None, init=False, repr=False)

    def _client(self) -> Any:
        if self._client_obj is None:  # boto3 clients are thread-safe; build one per store
            import boto3
            from botocore.config import Config

            self._client_obj = boto3.client(
                "s3",
                endpoint_url=self.endpoint,
                aws_access_key_id=self.access_key,
                aws_secret_access_key=self.secret_key,
                region_name=self.region,
                config=Config(retries={"max_attempts": 3}, connect_timeout=5, read_timeout=30),
            )
        return self._client_obj

    async def _run(self, fn: str, **kwargs: Any) -> Any:
        try:
            return await asyncio.to_thread(getattr(self._client(), fn), **kwargs)
        except Exception as exc:
            raise TelaError(ErrorCode.STORAGE_UNAVAILABLE, "File storage is unavailable.") from exc

    async def ensure_bucket(self) -> None:
        """Create the bucket when it does not exist yet, best effort.

        Managed buckets already exist, and an application identity usually may not create buckets (SeaweedFS needs
        its Admin action; some gateways create a bucket on first write). A real problem surfaces on the first write.
        """
        try:
            await self._run("head_bucket", Bucket=self.bucket)
        except TelaError:
            try:
                await self._run("create_bucket", Bucket=self.bucket)
            except TelaError:
                return

    def _encryption(self) -> dict[str, str]:
        if self.sse == "none":
            return {}
        extra = {"ServerSideEncryption": self.sse}
        if self.sse == "aws:kms" and self.kms_key_id:
            extra["SSEKMSKeyId"] = self.kms_key_id
        return extra

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        resp = await self._run("put_object", Bucket=self.bucket, Key=key, Body=data, ContentType=content_type,
                               Tagging=f"{CLASS_TAG}={quote(data_class(key))}", **self._encryption())
        if self.sse != "none" and resp.get("ServerSideEncryption") != self.sse:
            # The store ignored the encryption request: do not leave an unencrypted copy behind.
            with contextlib.suppress(TelaError):
                await self._run("delete_object", Bucket=self.bucket, Key=key)
            raise TelaError(ErrorCode.STORAGE_UNAVAILABLE, "File storage did not confirm encryption.")

    async def get(self, key: str) -> bytes:
        resp = await self._run("get_object", Bucket=self.bucket, Key=key)
        body: bytes = await asyncio.to_thread(resp["Body"].read)
        return body

    async def delete(self, key: str) -> None:
        await self._run("delete_object", Bucket=self.bucket, Key=key)

    async def presigned_get(self, key: str, expires_s: int = 900) -> str:
        url: str = await self._run(
            "generate_presigned_url", ClientMethod="get_object",
            Params={"Bucket": self.bucket, "Key": key}, ExpiresIn=min(expires_s, 3600),
        )
        return url

    async def healthy(self) -> bool:
        """The store answers for this bucket. A missing bucket still counts: it is created on first write."""
        from botocore.exceptions import ClientError

        try:
            await asyncio.to_thread(self._client().head_bucket, Bucket=self.bucket)
            return True
        except ClientError as exc:
            return str(exc.response.get("Error", {}).get("Code")) in ("404", "NoSuchBucket")
        except Exception:  # noqa: BLE001 - readiness reports, never raises
            return False

    async def lifecycle(self) -> list[dict[str, Any]]:
        from botocore.exceptions import ClientError

        try:
            resp = await asyncio.to_thread(self._client().get_bucket_lifecycle_configuration, Bucket=self.bucket)
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") == "NoSuchLifecycleConfiguration":
                return []
            raise TelaError(ErrorCode.STORAGE_UNAVAILABLE, "File storage is unavailable.") from exc
        rules: list[dict[str, Any]] = resp.get("Rules", [])
        return rules

    async def apply_lifecycle(self, config: dict[str, Any]) -> None:
        """Put the bucket rules, read them back, and remove them again if the store changed them.

        Some S3-compatible stores accept rules they cannot honour and keep a broader one: SeaweedFS 3.80 drops
        a tag filter and would expire the whole bucket. A mismatch is never left in place.
        """
        await self._run("put_bucket_lifecycle_configuration", Bucket=self.bucket, LifecycleConfiguration=config)
        stored = await self.lifecycle()
        if _normalise_rules(stored) != _normalise_rules(config["Rules"]):
            await self._run("delete_bucket_lifecycle", Bucket=self.bucket)
            raise LifecycleNotSupported(
                "the object store did not keep the lifecycle rules as written; they were removed again "
                f"(stored: {stored})")
