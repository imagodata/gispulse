"""
Dataset storage abstraction — local filesystem (default) and S3/MinIO (Pro).

Provides a unified interface for storing, retrieving, and managing dataset
files regardless of the underlying storage backend.

Usage::

    from persistence.storage import create_storage

    storage = create_storage()
    key = await storage.upload("org1/ds123/parcels.gpkg", data)
    raw = await storage.download("org1/ds123/parcels.gpkg")
    url = await storage.get_presigned_url("org1/ds123/parcels.gpkg")

Environment variables (S3 mode):
    GISPULSE_S3_ENDPOINT    S3/MinIO endpoint URL (enables S3 mode)
    GISPULSE_S3_BUCKET      Bucket name (default: "gispulse")
    GISPULSE_S3_ACCESS_KEY  Access key
    GISPULSE_S3_SECRET_KEY  Secret key
    GISPULSE_S3_REGION      Region (default: "us-east-1")
"""

from __future__ import annotations

import asyncio
import functools
import shutil
from abc import ABC, abstractmethod
from pathlib import Path
from typing import BinaryIO

from core.config import settings
from core.logging import get_logger

log = get_logger(__name__)


class StorageError(Exception):
    """Raised when a storage operation fails."""


def validate_storage_key(key: str) -> str:
    """Validate and normalize a storage key, rejecting traversal attempts.

    Raises:
        StorageError: On ``..`` components, absolute paths, or null bytes.

    Returns:
        The cleaned key with normalized forward slashes.
    """
    if "\x00" in key:
        raise StorageError(f"Null byte in storage key: {key!r}")
    clean = key.replace("\\", "/").lstrip("/")
    if not clean:
        raise StorageError("Empty storage key")
    parts = clean.split("/")
    if ".." in parts:
        raise StorageError(f"Path traversal detected: {key!r}")
    return clean


class DatasetStorage(ABC):
    """Abstract interface for dataset file storage."""

    @abstractmethod
    async def upload(self, key: str, data: bytes | BinaryIO, content_type: str = "") -> str:
        """Store data under *key*. Returns the canonical key."""
        ...

    @abstractmethod
    async def download(self, key: str) -> bytes:
        """Retrieve the raw bytes for *key*.

        Raises:
            StorageError: If the key does not exist.
        """
        ...

    @abstractmethod
    async def delete(self, key: str) -> None:
        """Delete the object at *key*. No-op if it does not exist."""
        ...

    @abstractmethod
    async def exists(self, key: str) -> bool:
        """Return True if *key* exists in storage."""
        ...

    @abstractmethod
    async def list_keys(self, prefix: str = "") -> list[str]:
        """List all keys matching *prefix*."""
        ...

    @abstractmethod
    async def get_presigned_url(self, key: str, expires_in: int = 3600) -> str:
        """Return a time-limited URL for direct download.

        For local storage, this returns a relative path (caller must
        map it to a route). For S3, returns a real presigned URL.

        Args:
            key:        Object key.
            expires_in: URL lifetime in seconds (S3 only).
        """
        ...

    @abstractmethod
    async def get_local_path(self, key: str) -> Path | None:
        """Return a local filesystem path if the backend supports it.

        LocalStorage returns the actual path.
        S3Storage returns None (caller must use download() or presigned URL).
        """
        ...


# ---------------------------------------------------------------------------
# LocalStorage — filesystem backend (default, Community tier)
# ---------------------------------------------------------------------------


class LocalStorage(DatasetStorage):
    """Store datasets on the local filesystem.

    Files are written under ``base_path / key`` where *key* uses forward
    slashes as separators (e.g. ``org1/ds123/parcels.gpkg``).
    """

    def __init__(self, base_path: str | Path = "~/.gispulse/data") -> None:
        self._base = Path(base_path).expanduser().resolve()
        self._base.mkdir(parents=True, exist_ok=True)
        log.info("local_storage_init", base_path=str(self._base))

    def _resolve(self, key: str) -> Path:
        """Resolve a key to an absolute path, guarding against traversal."""
        clean = validate_storage_key(key)
        resolved = (self._base / clean).resolve()
        if not str(resolved).startswith(str(self._base)):
            raise StorageError(f"Path traversal detected: {key!r}")
        return resolved

    async def upload(self, key: str, data: bytes | BinaryIO, content_type: str = "") -> str:
        dest = self._resolve(key)
        dest.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(data, bytes):
            dest.write_bytes(data)
        else:
            with open(dest, "wb") as f:
                shutil.copyfileobj(data, f)
        log.debug("local_upload", key=key, size=dest.stat().st_size)
        return key

    async def download(self, key: str) -> bytes:
        path = self._resolve(key)
        if not path.exists():
            raise StorageError(f"Key not found: {key!r}")
        return path.read_bytes()

    async def delete(self, key: str) -> None:
        path = self._resolve(key)
        if path.is_file():
            path.unlink()
            log.debug("local_delete", key=key)
            # Remove empty parent directories up to base
            parent = path.parent
            while parent != self._base:
                try:
                    parent.rmdir()  # only succeeds if empty
                    parent = parent.parent
                except OSError:
                    break

    async def exists(self, key: str) -> bool:
        return self._resolve(key).exists()

    async def list_keys(self, prefix: str = "") -> list[str]:
        search_dir = self._resolve(prefix) if prefix else self._base
        if not search_dir.is_dir():
            # prefix points to a file, list its parent with the file as filter
            search_dir = search_dir.parent
        keys: list[str] = []
        if search_dir.exists():
            for p in search_dir.rglob("*"):
                if p.is_file():
                    rel = str(p.relative_to(self._base)).replace("\\", "/")
                    if not prefix or rel.startswith(prefix.replace("\\", "/")):
                        keys.append(rel)
        return sorted(keys)

    async def get_presigned_url(self, key: str, expires_in: int = 3600) -> str:
        # Local storage has no real presigned URLs; return a relative path
        # that the HTTP layer can serve via FileResponse.
        return key

    async def get_local_path(self, key: str) -> Path | None:
        path = self._resolve(key)
        return path if path.exists() else None


# ---------------------------------------------------------------------------
# S3Storage — S3/MinIO backend (Pro tier)
# ---------------------------------------------------------------------------


class S3Storage(DatasetStorage):
    """Store datasets in an S3-compatible bucket (AWS S3 or MinIO).

    Requires ``boto3`` (install via ``pip install gispulse[s3]``).
    """

    def __init__(
        self,
        endpoint_url: str,
        bucket: str = "gispulse",
        access_key: str = "",
        secret_key: str = "",
        region: str = "us-east-1",
    ) -> None:
        try:
            import boto3
            from botocore.config import Config as BotoConfig
        except ImportError:
            raise ImportError(
                "boto3 is required for S3 storage. "
                "Install it with: pip install gispulse[s3]"
            ) from None

        self._bucket = bucket
        self._endpoint_url = endpoint_url

        self._client = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            aws_access_key_id=access_key or None,
            aws_secret_access_key=secret_key or None,
            region_name=region,
            config=BotoConfig(
                signature_version="s3v4",
                retries={"max_attempts": 3, "mode": "standard"},
            ),
        )

        # Ensure bucket exists (MinIO-friendly)
        self._ensure_bucket()
        log.info("s3_storage_init", endpoint=endpoint_url, bucket=bucket)

    def _ensure_bucket(self) -> None:
        """Create the bucket if it does not exist."""
        try:
            self._client.head_bucket(Bucket=self._bucket)
        except Exception:
            try:
                self._client.create_bucket(Bucket=self._bucket)
                log.info("s3_bucket_created", bucket=self._bucket)
            except Exception as exc:
                log.warning("s3_bucket_create_failed", bucket=self._bucket, error=str(exc))

    async def _run_sync(self, fn, *args, **kwargs):
        """Run a synchronous boto3 call in the default executor."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None, functools.partial(fn, *args, **kwargs)
        )

    async def upload(self, key: str, data: bytes | BinaryIO, content_type: str = "") -> str:
        key = validate_storage_key(key)
        extra: dict = {}
        if content_type:
            extra["ContentType"] = content_type

        if isinstance(data, bytes):
            from io import BytesIO
            body = BytesIO(data)
        else:
            body = data

        await self._run_sync(
            self._client.upload_fileobj, body, self._bucket, key,
            ExtraArgs=extra or None,
        )
        log.debug("s3_upload", key=key, bucket=self._bucket)
        return key

    async def download(self, key: str) -> bytes:
        key = validate_storage_key(key)
        from io import BytesIO

        buf = BytesIO()
        try:
            await self._run_sync(
                self._client.download_fileobj, self._bucket, key, buf,
            )
        except self._client.exceptions.NoSuchKey:
            raise StorageError(f"Key not found in S3: {key!r}")
        except Exception as exc:
            if "404" in str(exc) or "NoSuchKey" in str(exc):
                raise StorageError(f"Key not found in S3: {key!r}")
            raise StorageError(f"S3 download failed for {key!r}: {exc}")
        buf.seek(0)
        return buf.read()

    async def delete(self, key: str) -> None:
        key = validate_storage_key(key)
        try:
            await self._run_sync(
                self._client.delete_object, Bucket=self._bucket, Key=key,
            )
            log.debug("s3_delete", key=key)
        except Exception as exc:
            log.warning("s3_delete_failed", key=key, error=str(exc))

    async def exists(self, key: str) -> bool:
        key = validate_storage_key(key)
        try:
            await self._run_sync(
                self._client.head_object, Bucket=self._bucket, Key=key,
            )
            return True
        except Exception:
            return False

    async def list_keys(self, prefix: str = "") -> list[str]:
        if prefix:
            prefix = validate_storage_key(prefix)

        def _list_sync():
            keys: list[str] = []
            paginator = self._client.get_paginator("list_objects_v2")
            pages = paginator.paginate(Bucket=self._bucket, Prefix=prefix)
            for page in pages:
                for obj in page.get("Contents", []):
                    keys.append(obj["Key"])
            return keys

        return await self._run_sync(_list_sync)

    async def get_presigned_url(self, key: str, expires_in: int = 3600) -> str:
        key = validate_storage_key(key)
        url: str = await self._run_sync(
            self._client.generate_presigned_url,
            "get_object",
            Params={"Bucket": self._bucket, "Key": key},
            ExpiresIn=expires_in,
        )
        return url

    async def get_local_path(self, key: str) -> Path | None:
        # S3 storage does not provide local paths
        return None


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def create_storage() -> DatasetStorage:
    """Create the appropriate storage backend based on environment variables.

    If ``GISPULSE_S3_ENDPOINT`` is set, returns an :class:`S3Storage`
    (requires Pro tier). Otherwise returns a :class:`LocalStorage`.
    """
    s3_url = settings.s3.endpoint
    if s3_url:
        from persistence.tier import check_tier, TierError

        try:
            check_tier("pro")
        except TierError:
            log.warning(
                "s3_tier_blocked",
                msg="GISPULSE_S3_ENDPOINT is set but S3 storage requires Pro tier. "
                "Falling back to local storage.",
            )
            return LocalStorage(base_path=settings.storage.data_dir)

        return S3Storage(
            endpoint_url=s3_url,
            bucket=settings.s3.bucket,
            access_key=settings.s3.access_key,
            secret_key=settings.s3.secret_key,
            region=settings.s3.region,
        )

    return LocalStorage(base_path=settings.storage.data_dir)
