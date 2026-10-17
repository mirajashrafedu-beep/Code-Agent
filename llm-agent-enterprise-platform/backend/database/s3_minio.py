"""MinIO (S3) layer on aioboto3.

- Two endpoints: an internal one for server-side calls, and a public one
  for presigned URLs. A presigned URL is signed for one host name, and the
  browser must be able to reach that host.
- One long-lived client instead of a new connection for every call.
- Bucket names and object keys are validated before any request.
"""
from __future__ import annotations

import asyncio
import logging
import os
from contextlib import AsyncExitStack
from dataclasses import dataclass
from functools import lru_cache, partial
from typing import Any
from urllib.parse import urlsplit

import aioboto3
from botocore.config import Config
from botocore.exceptions import ClientError

from ._env import env_bool, env_int
from .errors import DatabaseConfigError

logger = logging.getLogger(__name__)

MAX_KEY_BYTES = 1024
MAX_PRESIGN_SECONDS = 7 * 24 * 3600  # SigV4 limit
_MISSING_BUCKET_CODES = {"404", "NoSuchBucket", "NotFound"}


class S3ConfigError(DatabaseConfigError):
    """Raised when MinIO settings are missing or invalid."""


_env_int = partial(env_int, error=S3ConfigError)


@dataclass(frozen=True)
class S3Settings:
    endpoint: str              # used by the server for real requests
    public_endpoint: str       # used only to sign URLs for browsers
    access_key: str
    secret_key: str
    region: str
    bucket_uploads: str
    bucket_exports: str
    auto_create_buckets: bool
    max_download_bytes: int
    connect_timeout_s: int
    read_timeout_s: int
    max_pool_connections: int

    @property
    def buckets(self) -> tuple[str, str]:
        return (self.bucket_uploads, self.bucket_exports)


def _normalize_endpoint(raw: str, name: str) -> str:
    """Add http:// to bare host:port values and check the result."""
    value = raw.strip().rstrip("/")
    if not value.startswith(("http://", "https://")):
        value = f"http://{value}"
    if not urlsplit(value).hostname:
        raise S3ConfigError(f"{name} is not a valid endpoint: {raw!r}")
    return value


@lru_cache(maxsize=1)
def get_settings() -> S3Settings:
    """Read and validate settings. Call get_settings.cache_clear() in tests."""
    # An app-specific key is better than root credentials in production.
    access_key = os.getenv("MINIO_ACCESS_KEY") or os.getenv("MINIO_ROOT_USER")
    secret_key = os.getenv("MINIO_SECRET_KEY") or os.getenv("MINIO_ROOT_PASSWORD")
    if not access_key or not secret_key:
        raise S3ConfigError(
            "MinIO credentials are not set (MINIO_ACCESS_KEY/MINIO_SECRET_KEY "
            "or MINIO_ROOT_USER/MINIO_ROOT_PASSWORD)"
        )
    endpoint = _normalize_endpoint(os.getenv("MINIO_ENDPOINT", "minio:9000"), "MINIO_ENDPOINT")
    public = os.getenv("MINIO_PUBLIC_ENDPOINT")
    public_endpoint = _normalize_endpoint(public, "MINIO_PUBLIC_ENDPOINT") if public else endpoint
    return S3Settings(
        endpoint=endpoint,
        public_endpoint=public_endpoint,
        access_key=access_key,
        secret_key=secret_key,
        region=os.getenv("MINIO_REGION", "us-east-1"),
        bucket_uploads=os.getenv("MINIO_BUCKET_UPLOADS", "user-uploads"),
        bucket_exports=os.getenv("MINIO_BUCKET_EXPORTS", "exports"),
        auto_create_buckets=env_bool("MINIO_AUTO_CREATE_BUCKETS", True),
        max_download_bytes=_env_int("MINIO_MAX_DOWNLOAD_BYTES", 100 * 1024 * 1024),
        connect_timeout_s=_env_int("MINIO_CONNECT_TIMEOUT_S", 5),
        read_timeout_s=_env_int("MINIO_READ_TIMEOUT_S", 60),
        max_pool_connections=_env_int("MINIO_MAX_POOL_CONNECTIONS", 50),
    )


def _validate_key(key: str) -> None:
    if (
        not key
        or len(key.encode("utf-8")) > MAX_KEY_BYTES
        or key.startswith("/")
        or "\\" in key
        or "\x00" in key
        or ".." in key.split("/")
    ):
        raise ValueError(f"Invalid object key: {key!r}")


def _validate_expiry(expires_in: int) -> None:
    if not 1 <= expires_in <= MAX_PRESIGN_SECONDS:
        raise ValueError(f"expires_in must be between 1 and {MAX_PRESIGN_SECONDS} seconds")


class S3MinioClient:
    """Async MinIO client. Create one per process; get_s3_client() does that."""

    def __init__(self, settings: S3Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._session = aioboto3.Session()
        self._lock = asyncio.Lock()
        self._stack: AsyncExitStack | None = None
        self._ops: Any = None
        self._presign: Any = None

    # -- connection --------------------------------------------------------

    def _open_client(self, stack: AsyncExitStack, endpoint: str):
        s = self._settings
        return stack.enter_async_context(
            self._session.client(
                "s3",
                endpoint_url=endpoint,
                aws_access_key_id=s.access_key,
                aws_secret_access_key=s.secret_key,
                region_name=s.region,
                config=Config(
                    signature_version="s3v4",
                    s3={"addressing_style": "path"},  # MinIO needs path-style
                    retries={"max_attempts": 3, "mode": "standard"},
                    connect_timeout=s.connect_timeout_s,
                    read_timeout=s.read_timeout_s,
                    max_pool_connections=s.max_pool_connections,
                ),
            )
        )

    async def _clients(self) -> tuple[Any, Any]:
        """Return (operations client, presigning client), opening them once."""
        if self._ops is None:
            async with self._lock:
                if self._ops is None:
                    s = self._settings
                    stack = AsyncExitStack()
                    try:
                        ops = await self._open_client(stack, s.endpoint)
                        same = s.public_endpoint == s.endpoint
                        presign = ops if same else await self._open_client(stack, s.public_endpoint)
                    except BaseException:
                        await stack.aclose()
                        raise
                    self._stack, self._ops, self._presign = stack, ops, presign
        return self._ops, self._presign

    async def aclose(self) -> None:
        """Close the underlying clients. Safe to call more than once."""
        async with self._lock:
            stack, self._stack = self._stack, None
            self._ops = self._presign = None
        if stack is not None:
            await stack.aclose()

    def _check_bucket(self, bucket: str) -> None:
        if bucket not in self._settings.buckets:
            raise ValueError(f"Unknown bucket: {bucket!r}")

    # -- lifecycle ---------------------------------------------------------

    async def ensure_buckets(self) -> None:
        """Check that both buckets exist; create them when allowed."""
        ops, _ = await self._clients()
        for bucket in self._settings.buckets:
            try:
                await ops.head_bucket(Bucket=bucket)
            except ClientError as exc:
                code = str(exc.response.get("Error", {}).get("Code", ""))
                if code in _MISSING_BUCKET_CODES and self._settings.auto_create_buckets:
                    await ops.create_bucket(Bucket=bucket)
                    logger.info("Created bucket %s", bucket)
                else:
                    raise

    async def ping(self) -> None:
        """Check access to the uploads bucket. Raises on failure."""
        ops, _ = await self._clients()
        await ops.head_bucket(Bucket=self._settings.bucket_uploads)

    # -- presigned URLs ----------------------------------------------------

    async def generate_presigned_upload_url(
        self, bucket: str, key: str, expires_in: int = 3600, content_type: str | None = None
    ) -> str:
        """Presigned PUT URL. If content_type is set, the client must send it."""
        self._check_bucket(bucket)
        _validate_key(key)
        _validate_expiry(expires_in)
        _, presign = await self._clients()
        params: dict[str, Any] = {"Bucket": bucket, "Key": key}
        if content_type:
            params["ContentType"] = content_type
        return await presign.generate_presigned_url(
            ClientMethod="put_object", Params=params, ExpiresIn=expires_in
        )

    async def generate_presigned_upload_post(
        self,
        bucket: str,
        key: str,
        max_bytes: int,
        expires_in: int = 3600,
        content_type: str | None = None,
    ) -> dict[str, Any]:
        """Presigned POST that MinIO enforces to a size limit.

        Returns {"url": ..., "fields": {...}}. A presigned PUT cannot limit size.
        """
        self._check_bucket(bucket)
        _validate_key(key)
        _validate_expiry(expires_in)
        if max_bytes < 1:
            raise ValueError("max_bytes must be at least 1")
        _, presign = await self._clients()
        conditions: list[Any] = [["content-length-range", 1, max_bytes]]
        fields: dict[str, str] = {}
        if content_type:
            fields["Content-Type"] = content_type
            conditions.append({"Content-Type": content_type})
        return await presign.generate_presigned_post(
            Bucket=bucket,
            Key=key,
            Fields=fields or None,
            Conditions=conditions,
            ExpiresIn=expires_in,
        )

    async def generate_presigned_download_url(
        self, bucket: str, key: str, expires_in: int = 3600
    ) -> str:
        self._check_bucket(bucket)
        _validate_key(key)
        _validate_expiry(expires_in)
        _, presign = await self._clients()
        return await presign.generate_presigned_url(
            ClientMethod="get_object",
            Params={"Bucket": bucket, "Key": key},
            ExpiresIn=expires_in,
        )

    # -- object operations -------------------------------------------------

    async def upload_bytes(
        self, bucket: str, key: str, data: bytes, content_type: str = "application/octet-stream"
    ) -> None:
        self._check_bucket(bucket)
        _validate_key(key)
        ops, _ = await self._clients()
        await ops.put_object(Bucket=bucket, Key=key, Body=data, ContentType=content_type)

    async def download_bytes(self, bucket: str, key: str, max_bytes: int | None = None) -> bytes:
        """Read an object into memory. Raises ValueError above max_bytes."""
        self._check_bucket(bucket)
        _validate_key(key)
        limit = max_bytes or self._settings.max_download_bytes
        ops, _ = await self._clients()
        res = await ops.get_object(Bucket=bucket, Key=key)
        length = res.get("ContentLength")
        if length is not None and length > limit:
            raise ValueError(f"Object is {length} bytes, over the {limit} byte limit")
        buffer = bytearray()
        async with res["Body"] as stream:
            while chunk := await stream.read(1024 * 1024):
                buffer += chunk
                if len(buffer) > limit:
                    raise ValueError(f"Object is over the {limit} byte limit")
        return bytes(buffer)

    async def delete_object(self, bucket: str, key: str) -> None:
        self._check_bucket(bucket)
        _validate_key(key)
        ops, _ = await self._clients()
        await ops.delete_object(Bucket=bucket, Key=key)

    async def list_keys(self, bucket: str, prefix: str = "", max_keys: int = 10_000) -> list[str]:
        """List object keys under a prefix. Raises if there are more than max_keys."""
        self._check_bucket(bucket)
        if prefix:
            _validate_key(prefix)
        ops, _ = await self._clients()
        keys: list[str] = []
        paginator = ops.get_paginator("list_objects_v2")
        async for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                keys.append(obj["Key"])
                if len(keys) > max_keys:
                    raise ValueError(f"More than {max_keys} objects under prefix {prefix!r}")
        return keys


# ---- module-level singleton and lifecycle ----------------------------------

_client: S3MinioClient | None = None


def get_s3_client() -> S3MinioClient:
    """Return the shared client. It opens its connections on first use."""
    global _client
    if _client is None:
        _client = S3MinioClient()
    return _client


async def init_s3() -> None:
    """Verify access and make sure the buckets exist."""
    client = get_s3_client()
    await client.ensure_buckets()
    await client.ping()
    logger.info("MinIO ready at %s", get_settings().endpoint)


async def ping_s3() -> None:
    await get_s3_client().ping()


async def close_s3() -> None:
    """Close the shared client. Safe to call more than once."""
    global _client
    client, _client = _client, None
    if client is not None:
        await client.aclose()


def __getattr__(name: str) -> str:
    """Keep old module-level constants working, read lazily."""
    if name == "MINIO_BUCKET_UPLOADS":
        return get_settings().bucket_uploads
    if name == "MINIO_BUCKET_EXPORTS":
        return get_settings().bucket_exports
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")