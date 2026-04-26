"""Shared dataset lifecycle operations used by both the public datasets
router (`/datasets/*`) and the portal router (`/api/portal/datasets/*`).

The two routers expose different URL spaces and auth profiles by design,
but the underlying repo + filesystem cleanup is identical. Keeping the
logic here avoids the drift class of bug spotted by #416 (the public
`DELETE /{id}` handler shipped in #437 originally duplicated the
portal handler's body, including the cleanup ordering).

Each helper is a small pure function that takes the repo + cache it
needs. Routers are responsible for their own auth, rate-limiting and
HTTP response shaping.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Any
from uuid import UUID

from core.models import Dataset
from persistence.repository import Repository

logger = logging.getLogger(__name__)


def delete_dataset(
    *,
    dataset_id: UUID,
    repo: Repository,
    layer_cache: dict[str, Any] | None = None,
) -> Dataset:
    """Delete a dataset and the local files backing it.

    The repo row is the source of truth: filesystem cleanup is
    best-effort and wrapped in try/except so a stale mount can never
    block the row deletion (an orphan file is recoverable, an orphan
    repo row leaves the caller without a UI handle to retry).

    Args:
        dataset_id: UUID of the dataset to delete.
        repo:        Dataset repository (in-memory or SQLite-backed).
        layer_cache: Optional in-process layer cache to invalidate.

    Returns:
        The deleted :class:`Dataset` (useful for logging / response
        shaping by the caller).

    Raises:
        KeyError: When no dataset matches *dataset_id*. Routers must
            translate this to their preferred 404 shape.
    """
    ds = repo.get(dataset_id)
    if ds is None:
        raise KeyError(str(dataset_id))

    if isinstance(layer_cache, dict):
        layer_cache.pop(str(dataset_id), None)

    if ds.source_path and not str(ds.source_path).startswith("s3://"):
        try:
            dataset_dir = Path(ds.source_path).parent
            if dataset_dir.exists():
                shutil.rmtree(dataset_dir, ignore_errors=True)
        except Exception as exc:
            logger.warning(
                "dataset_delete_fs_cleanup_failed dataset_id=%s err=%s",
                dataset_id,
                exc,
            )

    repo.delete(dataset_id)
    logger.info("dataset_deleted id=%s name=%s", dataset_id, ds.name)
    return ds
