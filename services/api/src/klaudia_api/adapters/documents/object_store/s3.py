"""MinIO object store wrapper (S3-compatible) using aioboto3.

Layout (per-user prefix; matches per-user dedup):
    blobs/u<user_id>/<hash[0:2]>/<hash[2:4]>/<hash>.<ext>      # original upload
    pages/u<user_id>/<hash[0:2]>/<hash[2:4]>/<hash>.jpg        # PDF page renders

Keys are content-addressed → idempotent put. If a put races with itself
(two concurrent uploads of the same bytes by the same user), the second
just overwrites identical content; safe.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import AsyncIterator

import aioboto3
from botocore.exceptions import ClientError

from klaudia_api.adapters.documents.media.hasher import shard_path
from klaudia_api.composition.settings import Settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class StoredObject:
    key: str
    size_bytes: int
    content_type: str


class ObjectStoreError(RuntimeError):
    pass


class MinIOClient:
    """Async S3-compatible client. Single shared session; clients are
    short-lived per call (boto3 best practice for aiobotocore).
    """

    def __init__(self, settings: Settings) -> None:
        self._endpoint = settings.minio_endpoint
        self._region = settings.minio_region
        self._access_key = settings.minio_access_key
        self._secret_key = settings.minio_secret_key
        self._bucket = settings.minio_bucket
        self._secure = settings.minio_secure
        self._session = aioboto3.Session()

    @property
    def bucket(self) -> str:
        return self._bucket

    @asynccontextmanager
    async def _client(self) -> AsyncIterator:
        async with self._session.client(
            "s3",
            endpoint_url=self._endpoint,
            region_name=self._region,
            aws_access_key_id=self._access_key,
            aws_secret_access_key=self._secret_key,
            use_ssl=self._secure,
        ) as client:
            yield client

    async def ensure_bucket(self) -> None:
        """Create bucket on startup if it doesn't exist. Idempotent."""
        async with self._client() as s3:
            try:
                await s3.head_bucket(Bucket=self._bucket)
                return
            except ClientError as e:
                code = e.response.get("Error", {}).get("Code", "")
                if code not in ("404", "NoSuchBucket", "NotFound"):
                    raise
            try:
                await s3.create_bucket(Bucket=self._bucket)
                logger.info("MinIO bucket created: %s", self._bucket)
            except ClientError as e:
                code = e.response.get("Error", {}).get("Code", "")
                if code == "BucketAlreadyOwnedByYou":
                    return
                raise ObjectStoreError(
                    f"Failed to ensure bucket {self._bucket!r}: {e}"
                ) from e

    @staticmethod
    def build_blob_key(
        user_id: int, blake3_hex: str, *, extension: str, prefix: str = "blobs"
    ) -> str:
        ext = extension.lstrip(".")
        return f"{prefix}/u{user_id}/{shard_path(blake3_hex)}/{blake3_hex}.{ext}"

    async def exists(self, key: str) -> bool:
        async with self._client() as s3:
            try:
                await s3.head_object(Bucket=self._bucket, Key=key)
                return True
            except ClientError as e:
                code = e.response.get("Error", {}).get("Code", "")
                if code in ("404", "NoSuchKey", "NotFound"):
                    return False
                raise ObjectStoreError(f"head_object failed for {key!r}: {e}") from e

    async def put(
        self,
        key: str,
        data: bytes,
        *,
        content_type: str,
    ) -> StoredObject:
        async with self._client() as s3:
            try:
                await s3.put_object(
                    Bucket=self._bucket,
                    Key=key,
                    Body=data,
                    ContentType=content_type,
                )
            except ClientError as e:
                raise ObjectStoreError(f"put_object failed for {key!r}: {e}") from e
        return StoredObject(key=key, size_bytes=len(data), content_type=content_type)

    async def delete(self, key: str) -> None:
        """Delete an object. Idempotent: a missing key is not an error."""
        async with self._client() as s3:
            try:
                await s3.delete_object(Bucket=self._bucket, Key=key)
            except ClientError as e:
                code = e.response.get("Error", {}).get("Code", "")
                if code in ("404", "NoSuchKey", "NotFound"):
                    return
                raise ObjectStoreError(f"delete_object failed for {key!r}: {e}") from e

    async def get(self, key: str) -> bytes:
        async with self._client() as s3:
            try:
                obj = await s3.get_object(Bucket=self._bucket, Key=key)
                async with obj["Body"] as stream:
                    return await stream.read()
            except ClientError as e:
                raise ObjectStoreError(f"get_object failed for {key!r}: {e}") from e
