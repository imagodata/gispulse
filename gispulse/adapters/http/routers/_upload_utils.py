"""Utilities for dataset upload — no FastAPI dependency (testable in isolation)."""

from __future__ import annotations

import hashlib
from pathlib import Path


def sha256_file(path: Path) -> str:
    """Compute SHA-256 hex digest of a file."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def find_duplicate_by_hash(dataset_repo, file_hash: str):
    """Return an existing dataset whose file_hash metadata matches, or None."""
    for ds in dataset_repo.list_all():
        if ds.metadata and ds.metadata.get("file_hash") == file_hash:
            return ds
    return None
