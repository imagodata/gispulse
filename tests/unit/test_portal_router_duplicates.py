"""Tests for #72 — duplicate import detection helpers in portal_router."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from gispulse.adapters.http.routers._upload_utils import sha256_file as _sha256, find_duplicate_by_hash as _find_duplicate
from gispulse.persistence.repository import InMemoryRepository
from gispulse.core.models import Dataset


@pytest.fixture
def sample_file(tmp_path) -> Path:
    """Write a small binary file and return its path."""
    p = tmp_path / "data.bin"
    p.write_bytes(b"hello geospatial world")
    return p


@pytest.fixture
def empty_repo() -> InMemoryRepository:
    return InMemoryRepository()


@pytest.fixture
def repo_with_dataset() -> tuple[InMemoryRepository, Dataset]:
    repo = InMemoryRepository()
    ds = Dataset(name="existing", crs="EPSG:4326", format="GeoJSON")
    ds.metadata = {"file_hash": "abc123def456"}
    repo.save(ds)
    return repo, ds


class TestSha256:
    def test_correct_hash(self, sample_file):
        expected = hashlib.sha256(b"hello geospatial world").hexdigest()
        assert _sha256(sample_file) == expected

    def test_returns_64_char_hex(self, sample_file):
        result = _sha256(sample_file)
        assert len(result) == 64
        assert all(c in "0123456789abcdef" for c in result)

    def test_different_content_different_hash(self, tmp_path):
        p1 = tmp_path / "a.bin"
        p2 = tmp_path / "b.bin"
        p1.write_bytes(b"content A")
        p2.write_bytes(b"content B")
        assert _sha256(p1) != _sha256(p2)

    def test_identical_content_same_hash(self, tmp_path):
        p1 = tmp_path / "copy1.bin"
        p2 = tmp_path / "copy2.bin"
        p1.write_bytes(b"same bytes")
        p2.write_bytes(b"same bytes")
        assert _sha256(p1) == _sha256(p2)

    def test_empty_file(self, tmp_path):
        p = tmp_path / "empty.bin"
        p.write_bytes(b"")
        expected = hashlib.sha256(b"").hexdigest()
        assert _sha256(p) == expected

    def test_large_file_chunked(self, tmp_path):
        """Files > 64KB should still hash correctly (chunked read)."""
        content = b"x" * (128 * 1024)  # 128 KB
        p = tmp_path / "large.bin"
        p.write_bytes(content)
        expected = hashlib.sha256(content).hexdigest()
        assert _sha256(p) == expected


class TestFindDuplicate:
    def test_returns_none_when_repo_empty(self, empty_repo):
        assert _find_duplicate(empty_repo, "somehash") is None

    def test_returns_none_when_no_match(self, repo_with_dataset):
        repo, _ = repo_with_dataset
        assert _find_duplicate(repo, "nonexistent_hash") is None

    def test_finds_existing_dataset_by_hash(self, repo_with_dataset):
        repo, ds = repo_with_dataset
        found = _find_duplicate(repo, "abc123def456")
        assert found is not None
        assert found.id == ds.id

    def test_returns_none_when_dataset_has_no_metadata(self, empty_repo):
        ds = Dataset(name="no_meta", crs="EPSG:4326", format="GeoJSON")
        ds.metadata = None
        empty_repo.save(ds)
        assert _find_duplicate(empty_repo, "anyhash") is None

    def test_returns_none_when_metadata_lacks_file_hash(self, empty_repo):
        ds = Dataset(name="partial_meta", crs="EPSG:4326", format="GeoJSON")
        ds.metadata = {"other_key": "value"}
        empty_repo.save(ds)
        assert _find_duplicate(empty_repo, "anyhash") is None

    def test_multiple_datasets_finds_correct_one(self):
        repo = InMemoryRepository()
        ds1 = Dataset(name="first", crs="EPSG:4326", format="GeoJSON")
        ds1.metadata = {"file_hash": "hash_one"}
        ds2 = Dataset(name="second", crs="EPSG:4326", format="GeoJSON")
        ds2.metadata = {"file_hash": "hash_two"}
        repo.save(ds1)
        repo.save(ds2)

        found = _find_duplicate(repo, "hash_two")
        assert found is not None
        assert found.id == ds2.id

    def test_uses_exact_hash_match(self, empty_repo):
        """Partial or prefix matches should not be returned."""
        ds = Dataset(name="exact", crs="EPSG:4326", format="GeoJSON")
        ds.metadata = {"file_hash": "abcdef1234567890"}
        empty_repo.save(ds)

        assert _find_duplicate(empty_repo, "abcdef") is None
        assert _find_duplicate(empty_repo, "abcdef1234567890") is not None
