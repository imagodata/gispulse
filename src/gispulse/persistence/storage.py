"""
Dataset storage abstraction — local filesystem (default) and S3/MinIO.

Provides a unified interface for storing, retrieving, and managing dataset
files regardless of the underlying storage backend.

Usage::

    from gispulse.persistence.storage import create_storage

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
from io import BytesIO
from pathlib import Path
from typing import BinaryIO, Literal, cast

from gispulse.core.config import settings
from gispulse.core.logging import get_logger

log = get_logger(__name__)


StorageMode = Literal["auto", "local", "s3"]


class StorageError(Exception):
    """Raised when a storage operation fails.

    The string message remains backward-compatible, while ``code``/``context``/
    ``recovery`` give CLIs and agents a stable machine-readable surface.
    """

    def __init__(
        self,
        message: str,
        *,
        code: str = "STORAGE_ERROR",
        context: dict[str, object] | None = None,
        recovery: str = "",
    ) -> None:
        self.code = code
        self.context = context or {}
        self.recovery = recovery
        super().__init__(message)

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "code": self.code,
            "message": str(self),
            "context": self.context,
        }
        if self.recovery:
            payload["recovery"] = self.recovery
        return payload


def _make_s3_boto_config(BotoConfig):
    base = {
        "signature_version": "s3v4",
        "retries": {"max_attempts": 3, "mode": "standard"},
    }
    checksum_compat = {
        "request_checksum_calculation": "when_required",
        "response_checksum_validation": "when_required",
    }
    try:
        return BotoConfig(**base, **checksum_compat)
    except TypeError:
        return BotoConfig(**base)


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
# S3Storage — S3/MinIO backend
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
            config=_make_s3_boto_config(BotoConfig),
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
            body = cast(BinaryIO, BytesIO(data))
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
# Factory / diagnostics
# ---------------------------------------------------------------------------


def _normalize_storage_mode(mode: str) -> StorageMode:
    clean = mode.strip().lower()
    if clean in {"auto", "local", "s3"}:
        return clean  # type: ignore[return-value]
    raise StorageError(
        f"Invalid storage mode {mode!r}; expected auto, local, or s3",
        code="STORAGE_MODE_INVALID",
        context={"mode": mode},
        recovery="Use mode='auto', mode='local', or mode='s3'.",
    )


def _s3_not_configured_error(mode: StorageMode) -> StorageError:
    return StorageError(
        "S3/Garage storage was explicitly requested but GISPULSE_S3_ENDPOINT is not configured",
        code="STORAGE_S3_NOT_CONFIGURED",
        context={"mode": mode, "endpoint_configured": False},
        recovery=(
            "Set GISPULSE_S3_ENDPOINT plus GISPULSE_S3_BUCKET/"
            "GISPULSE_S3_ACCESS_KEY/GISPULSE_S3_SECRET_KEY, or choose mode='local' "
            "explicitly for local development."
        ),
    )


def describe_storage_backend(mode: str = "auto") -> dict[str, object]:
    """Describe the storage backend selection without opening network connections.

    ``mode='auto'`` preserves the historical behavior: S3/Garage when
    ``GISPULSE_S3_ENDPOINT`` is configured, local storage otherwise. Explicit
    ``mode='s3'`` never falls back to local storage.
    """
    requested_mode = _normalize_storage_mode(mode)
    s3_url = settings.s3.endpoint
    endpoint_configured = bool(s3_url)
    local_path = str(Path(settings.storage.data_dir).expanduser().resolve())

    if requested_mode == "local":
        return {
            "requested_mode": requested_mode,
            "backend": "local",
            "storage_class": "LocalStorage",
            "endpoint_configured": endpoint_configured,
            "bucket": None,
            "region": None,
            "local_path": local_path,
            "selection_reason": "mode=local explicitly selected local storage.",
            "fallback": False,
        }

    if requested_mode == "s3" and not endpoint_configured:
        error = _s3_not_configured_error(requested_mode)
        return {
            "requested_mode": requested_mode,
            "backend": "unconfigured",
            "storage_class": None,
            "endpoint_configured": False,
            "bucket": settings.s3.bucket,
            "region": settings.s3.region,
            "local_path": None,
            "selection_reason": "mode=s3 requires GISPULSE_S3_ENDPOINT.",
            "fallback": False,
            "error": error.to_dict(),
        }

    if endpoint_configured:
        reason = (
            "mode=s3 explicitly selected S3/Garage storage."
            if requested_mode == "s3"
            else "GISPULSE_S3_ENDPOINT is set; auto mode selected S3/Garage storage."
        )
        return {
            "requested_mode": requested_mode,
            "backend": "s3",
            "storage_class": "S3Storage",
            "endpoint_configured": True,
            "endpoint": s3_url,
            "bucket": settings.s3.bucket,
            "region": settings.s3.region,
            "local_path": None,
            "selection_reason": reason,
            "fallback": False,
        }

    return {
        "requested_mode": requested_mode,
        "backend": "local",
        "storage_class": "LocalStorage",
        "endpoint_configured": False,
        "bucket": None,
        "region": None,
        "local_path": local_path,
        "selection_reason": "GISPULSE_S3_ENDPOINT is not set; auto mode selected local storage.",
        "fallback": False,
    }


def create_storage(mode: str = "auto") -> DatasetStorage:
    """Create a storage backend from an explicit mode.

    ``mode='auto'`` keeps backward compatibility: S3/Garage when
    ``GISPULSE_S3_ENDPOINT`` is set, local storage otherwise. Explicit
    ``mode='s3'`` refuses to fall back to local storage.
    """
    report = describe_storage_backend(mode)
    backend = report["backend"]

    if backend == "s3":
        log.info(
            "storage_backend_selected",
            requested_mode=report["requested_mode"],
            backend=backend,
            storage_class=report["storage_class"],
            endpoint_configured=report["endpoint_configured"],
            bucket=report["bucket"],
        )
        return S3Storage(
            endpoint_url=str(report["endpoint"]),
            bucket=settings.s3.bucket,
            access_key=settings.s3.access_key,
            secret_key=settings.s3.secret_key,
            region=settings.s3.region,
        )

    if backend == "local":
        log.info(
            "storage_backend_selected",
            requested_mode=report["requested_mode"],
            backend=backend,
            storage_class=report["storage_class"],
            endpoint_configured=report["endpoint_configured"],
            bucket=report["bucket"],
        )
        return LocalStorage(base_path=settings.storage.data_dir)

    error_payload = report.get("error")
    if isinstance(error_payload, dict):
        raise StorageError(
            str(error_payload.get("message", "Storage backend is not configured")),
            code=str(error_payload.get("code", "STORAGE_BACKEND_UNCONFIGURED")),
            context=(
                error_payload.get("context")
                if isinstance(error_payload.get("context"), dict)
                else {}
            ),
            recovery=str(error_payload.get("recovery", "")),
        )
    raise StorageError(
        "Storage backend is not configured",
        code="STORAGE_BACKEND_UNCONFIGURED",
        context={"mode": mode},
        recovery="Check storage configuration and retry.",
    )
