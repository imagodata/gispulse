"""Tests for persistence.storage — DatasetStorage abstraction."""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from io import BytesIO
from unittest.mock import MagicMock, patch

import pytest

from gispulse.persistence.storage import (
    LocalStorage,
    StorageError,
    create_storage,
    _make_s3_boto_config,
    validate_storage_key,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run(coro):
    """Run an async coroutine synchronously."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _make_mock_boto3():
    """Build mock boto3 and botocore modules for S3Storage tests."""
    mock_client = MagicMock()
    mock_client.exceptions = MagicMock()
    mock_client.exceptions.NoSuchKey = type("NoSuchKey", (Exception,), {})
    mock_client.head_bucket.return_value = {}

    mock_boto3 = MagicMock()
    mock_boto3.client.return_value = mock_client

    mock_botocore_config = MagicMock()

    return mock_boto3, mock_botocore_config, mock_client


def _create_s3_storage(mock_boto3, mock_botocore_config):
    """Create an S3Storage with mocked boto3."""
    # Temporarily inject mocks into sys.modules so local imports resolve
    saved_boto3 = sys.modules.get("boto3")
    saved_botocore = sys.modules.get("botocore")
    saved_botocore_config = sys.modules.get("botocore.config")

    sys.modules["boto3"] = mock_boto3
    if "botocore" not in sys.modules:
        sys.modules["botocore"] = MagicMock()
    sys.modules["botocore.config"] = mock_botocore_config

    try:
        # Re-import to pick up mocked modules
        from gispulse.persistence.storage import S3Storage

        storage = S3Storage(
            endpoint_url="http://localhost:9000",
            bucket="test-bucket",
            access_key="minioadmin",
            secret_key="minioadmin",
        )
        return storage
    finally:
        # Restore
        if saved_boto3 is not None:
            sys.modules["boto3"] = saved_boto3
        elif "boto3" in sys.modules:
            del sys.modules["boto3"]
        if saved_botocore is not None:
            sys.modules["botocore"] = saved_botocore
        if saved_botocore_config is not None:
            sys.modules["botocore.config"] = saved_botocore_config
        elif "botocore.config" in sys.modules:
            del sys.modules["botocore.config"]


# ---------------------------------------------------------------------------
# LocalStorage
# ---------------------------------------------------------------------------


class TestLocalStorage:
    def setup_method(self):
        self._tmpdir = tempfile.mkdtemp(prefix="gispulse_test_storage_")
        self.storage = LocalStorage(base_path=self._tmpdir)

    def teardown_method(self):
        import shutil

        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_upload_bytes(self):
        key = _run(self.storage.upload("org1/ds1/test.gpkg", b"fake-gpkg-data"))
        assert key == "org1/ds1/test.gpkg"
        assert _run(self.storage.exists(key))

    def test_upload_binary_io(self):
        bio = BytesIO(b"binary-io-content")
        key = _run(self.storage.upload("org1/ds2/data.geojson", bio))
        assert _run(self.storage.exists(key))

    def test_download(self):
        _run(self.storage.upload("test/file.bin", b"hello-world"))
        data = _run(self.storage.download("test/file.bin"))
        assert data == b"hello-world"

    def test_download_missing_key(self):
        with pytest.raises(StorageError, match="Key not found"):
            _run(self.storage.download("nonexistent/key"))

    def test_delete(self):
        _run(self.storage.upload("to-delete/file.txt", b"temp"))
        assert _run(self.storage.exists("to-delete/file.txt"))
        _run(self.storage.delete("to-delete/file.txt"))
        assert not _run(self.storage.exists("to-delete/file.txt"))

    def test_delete_nonexistent(self):
        # Should not raise
        _run(self.storage.delete("nonexistent/key"))

    def test_list_keys(self):
        _run(self.storage.upload("org1/ds1/a.gpkg", b"a"))
        _run(self.storage.upload("org1/ds1/b.gpkg", b"b"))
        _run(self.storage.upload("org2/ds2/c.gpkg", b"c"))

        all_keys = _run(self.storage.list_keys())
        assert len(all_keys) == 3

        org1_keys = _run(self.storage.list_keys("org1/"))
        assert len(org1_keys) == 2
        assert all(k.startswith("org1/") for k in org1_keys)

    def test_list_keys_with_dataset_prefix(self):
        _run(self.storage.upload("org1/ds1/file.gpkg", b"data"))
        keys = _run(self.storage.list_keys("org1/ds1/"))
        assert keys == ["org1/ds1/file.gpkg"]

    def test_get_presigned_url_returns_key(self):
        _run(self.storage.upload("test/data.gpkg", b"data"))
        url = _run(self.storage.get_presigned_url("test/data.gpkg"))
        assert url == "test/data.gpkg"

    def test_get_local_path(self):
        _run(self.storage.upload("test/local.gpkg", b"data"))
        path = _run(self.storage.get_local_path("test/local.gpkg"))
        assert path is not None
        assert path.exists()
        assert path.read_bytes() == b"data"

    def test_get_local_path_missing(self):
        path = _run(self.storage.get_local_path("nonexistent"))
        assert path is None

    def test_path_traversal_blocked(self):
        with pytest.raises(StorageError, match="Path traversal"):
            _run(self.storage.upload("../../etc/passwd", b"hack"))

    def test_path_traversal_dotdot(self):
        with pytest.raises(StorageError, match="Path traversal"):
            _run(self.storage.download("foo/../../etc/shadow"))

    def test_upload_and_overwrite(self):
        _run(self.storage.upload("test/overwrite.txt", b"version1"))
        _run(self.storage.upload("test/overwrite.txt", b"version2"))
        data = _run(self.storage.download("test/overwrite.txt"))
        assert data == b"version2"

    def test_empty_prefix_lists_all(self):
        _run(self.storage.upload("a/1.txt", b"a"))
        _run(self.storage.upload("b/2.txt", b"b"))
        keys = _run(self.storage.list_keys(""))
        assert len(keys) == 2


# ---------------------------------------------------------------------------
# S3Storage (mocked boto3)
# ---------------------------------------------------------------------------


class TestS3Storage:
    """Test S3Storage with mocked boto3 client."""

    def _make(self):
        mock_boto3, mock_botocore_config, mock_client = _make_mock_boto3()
        storage = _create_s3_storage(mock_boto3, mock_botocore_config)
        return storage, mock_client

    def test_upload(self):
        storage, client = self._make()
        key = _run(storage.upload("org1/ds1/file.gpkg", b"data", "application/geopackage"))
        assert key == "org1/ds1/file.gpkg"
        client.upload_fileobj.assert_called_once()

    def test_boto_config_uses_garage_compatible_checksums(self):
        mock_boto3, mock_botocore_config, _ = _make_mock_boto3()
        _create_s3_storage(mock_boto3, mock_botocore_config)

        mock_botocore_config.Config.assert_called_once_with(
            signature_version="s3v4",
            retries={"max_attempts": 3, "mode": "standard"},
            request_checksum_calculation="when_required",
            response_checksum_validation="when_required",
        )

    def test_boto_config_falls_back_for_older_botocore(self):
        mock_config = MagicMock(side_effect=[TypeError("unknown"), "legacy-config"])

        assert _make_s3_boto_config(mock_config) == "legacy-config"
        assert mock_config.call_args_list == [
            (
                (),
                {
                    "signature_version": "s3v4",
                    "retries": {"max_attempts": 3, "mode": "standard"},
                    "request_checksum_calculation": "when_required",
                    "response_checksum_validation": "when_required",
                },
            ),
            (
                (),
                {
                    "signature_version": "s3v4",
                    "retries": {"max_attempts": 3, "mode": "standard"},
                },
            ),
        ]

    def test_download(self):
        storage, client = self._make()

        def fake_download(bucket, key, buf):
            buf.write(b"downloaded-data")

        client.download_fileobj.side_effect = fake_download

        data = _run(storage.download("org1/ds1/file.gpkg"))
        assert data == b"downloaded-data"

    def test_download_not_found(self):
        storage, client = self._make()
        client.download_fileobj.side_effect = Exception("404 NoSuchKey")

        with pytest.raises(StorageError, match="Key not found"):
            _run(storage.download("nonexistent/key"))

    def test_delete(self):
        storage, client = self._make()
        _run(storage.delete("org1/ds1/file.gpkg"))
        client.delete_object.assert_called_once_with(
            Bucket="test-bucket", Key="org1/ds1/file.gpkg"
        )

    def test_exists_true(self):
        storage, client = self._make()
        client.head_object.return_value = {}
        assert _run(storage.exists("org1/ds1/file.gpkg")) is True

    def test_exists_false(self):
        storage, client = self._make()
        client.head_object.side_effect = Exception("404")
        assert _run(storage.exists("nonexistent")) is False

    def test_list_keys(self):
        storage, client = self._make()
        paginator = MagicMock()
        client.get_paginator.return_value = paginator
        paginator.paginate.return_value = [
            {"Contents": [{"Key": "org1/ds1/a.gpkg"}, {"Key": "org1/ds1/b.gpkg"}]},
        ]

        keys = _run(storage.list_keys("org1/ds1/"))
        assert keys == ["org1/ds1/a.gpkg", "org1/ds1/b.gpkg"]

    def test_presigned_url(self):
        storage, client = self._make()
        client.generate_presigned_url.return_value = (
            "https://minio:9000/test-bucket/org1/ds1/file.gpkg?sig=abc"
        )

        url = _run(storage.get_presigned_url("org1/ds1/file.gpkg", expires_in=600))
        assert "minio" in url
        client.generate_presigned_url.assert_called_once()

    def test_get_local_path_returns_none(self):
        storage, _ = self._make()
        path = _run(storage.get_local_path("any/key"))
        assert path is None


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


class TestCreateStorage:
    def test_default_local(self):
        """Without S3 env vars, create_storage returns LocalStorage."""
        env = {k: v for k, v in os.environ.items() if not k.startswith("GISPULSE_S3_")}
        with patch.dict(os.environ, env, clear=True):
            storage = create_storage()
        assert isinstance(storage, LocalStorage)

    def test_s3_without_pro_tier_falls_back(self):
        """S3 endpoint set but tier is community => falls back to local."""
        env = {
            "GISPULSE_S3_ENDPOINT": "http://localhost:9000",
            "GISPULSE_TIER": "community",
        }
        with patch.dict(os.environ, env, clear=False):
            storage = create_storage()
        assert isinstance(storage, LocalStorage)

    def test_s3_with_pro_tier(self):
        """S3 endpoint set with pro tier => creates S3Storage."""
        from gispulse.persistence.storage import S3Storage

        env = {
            "GISPULSE_S3_ENDPOINT": "http://localhost:9000",
            "GISPULSE_S3_BUCKET": "test",
            "GISPULSE_S3_ACCESS_KEY": "admin",
            "GISPULSE_S3_SECRET_KEY": "secret",
            "GISPULSE_TIER": "pro",
            "GISPULSE_LICENCE_SKIP_VERIFY": "true",
            "GISPULSE_LICENSE_KEY": "eyJvcmciOiAidGVzdCIsICJ0aWVyIjogInBybyIsICJleHAiOiAiMjAzMC0wMS0wMVQwMDowMDowMFoifQ.AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
        }
        mock_boto3, mock_botocore_config, mock_client = _make_mock_boto3()

        saved_boto3 = sys.modules.get("boto3")
        saved_botocore = sys.modules.get("botocore")
        saved_botocore_config = sys.modules.get("botocore.config")
        sys.modules["boto3"] = mock_boto3
        if "botocore" not in sys.modules:
            sys.modules["botocore"] = MagicMock()
        sys.modules["botocore.config"] = mock_botocore_config

        try:
            with patch.dict(os.environ, env, clear=False):
                storage = create_storage()
            assert isinstance(storage, S3Storage)
        finally:
            if saved_boto3 is not None:
                sys.modules["boto3"] = saved_boto3
            elif "boto3" in sys.modules:
                del sys.modules["boto3"]
            if saved_botocore is not None:
                sys.modules["botocore"] = saved_botocore
            if saved_botocore_config is not None:
                sys.modules["botocore.config"] = saved_botocore_config
            elif "botocore.config" in sys.modules:
                del sys.modules["botocore.config"]


# ---------------------------------------------------------------------------
# #309: validate_storage_key + S3 path traversal
# ---------------------------------------------------------------------------


class TestValidateStorageKey309:
    """Shared key validation rejects traversal, absolute paths, null bytes."""

    def test_normal_key_passes(self):
        assert validate_storage_key("org1/ds1/file.gpkg") == "org1/ds1/file.gpkg"

    def test_leading_slash_stripped(self):
        assert validate_storage_key("/org1/file.gpkg") == "org1/file.gpkg"

    def test_backslash_normalized(self):
        assert validate_storage_key("org1\\ds1\\file.gpkg") == "org1/ds1/file.gpkg"

    def test_dotdot_rejected(self):
        with pytest.raises(StorageError, match="Path traversal"):
            validate_storage_key("../etc/passwd")

    def test_dotdot_mid_path_rejected(self):
        with pytest.raises(StorageError, match="Path traversal"):
            validate_storage_key("org1/../../etc/shadow")

    def test_null_byte_rejected(self):
        with pytest.raises(StorageError, match="Null byte"):
            validate_storage_key("org1/file\x00.gpkg")

    def test_empty_key_rejected(self):
        with pytest.raises(StorageError, match="Empty storage key"):
            validate_storage_key("")

    def test_only_slashes_rejected(self):
        with pytest.raises(StorageError, match="Empty storage key"):
            validate_storage_key("///")


class TestS3PathTraversal309:
    """S3Storage must reject path traversal on all operations."""

    def _make(self):
        mock_boto3, mock_botocore_config, mock_client = _make_mock_boto3()
        storage = _create_s3_storage(mock_boto3, mock_botocore_config)
        return storage, mock_client

    def test_s3_upload_traversal_rejected(self):
        storage, _ = self._make()
        with pytest.raises(StorageError, match="Path traversal"):
            _run(storage.upload("../../etc/passwd", b"hack"))

    def test_s3_download_traversal_rejected(self):
        storage, _ = self._make()
        with pytest.raises(StorageError, match="Path traversal"):
            _run(storage.download("foo/../../etc/shadow"))

    def test_s3_delete_traversal_rejected(self):
        storage, _ = self._make()
        with pytest.raises(StorageError, match="Path traversal"):
            _run(storage.delete("../secret"))

    def test_s3_exists_traversal_rejected(self):
        storage, _ = self._make()
        with pytest.raises(StorageError, match="Path traversal"):
            _run(storage.exists("../../etc/hosts"))

    def test_s3_presigned_url_traversal_rejected(self):
        storage, _ = self._make()
        with pytest.raises(StorageError, match="Path traversal"):
            _run(storage.get_presigned_url("../secret/key"))

    def test_s3_null_byte_rejected(self):
        storage, _ = self._make()
        with pytest.raises(StorageError, match="Null byte"):
            _run(storage.upload("org1/file\x00.gpkg", b"data"))

    def test_s3_list_keys_traversal_rejected(self):
        storage, _ = self._make()
        with pytest.raises(StorageError, match="Path traversal"):
            _run(storage.list_keys("../../"))
