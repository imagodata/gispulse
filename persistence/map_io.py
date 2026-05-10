"""Cocarte Map persistence helpers.

Wraps the generic ``SQLiteRepository[CocarteMap]`` with the Map-specific
queries Cocarte needs:

- ``get_by_slug`` / ``get_by_share_token`` (constant-time)
- ``count_for_owner`` (tier gating against ``_MAP_LIMITS``)
- ``soft_delete`` / ``restore`` (trash semantics; cascade-on-owner-delete)
- ``list_active`` (filters out trashed rows by default)
- ``slugify`` + ``ensure_unique_slug`` integration via ``allocate_slug``
"""

from __future__ import annotations

import hmac
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

from core.models import CocarteMap
from core.slug import ensure_unique_slug, slugify
from persistence.repository import Repository
from persistence.sqlite_repository import (
    DEFAULT_DB_PATH,
    SQLiteRepository,
    _row_to_model,
)


class MapRepository(Repository[CocarteMap]):
    """Map-specific repository with slug, share-token, tier and soft-delete.

    Internally delegates basic CRUD to ``SQLiteRepository[CocarteMap]`` and
    adds the queries Cocarte needs. Lives in its own class (rather than
    monkey-patching the generic repo) so the Map domain semantics are
    visible at import time.
    """

    def __init__(self, db_path: str | Path = DEFAULT_DB_PATH) -> None:
        self._inner: SQLiteRepository[CocarteMap] = SQLiteRepository(CocarteMap, db_path=db_path)
        self._ensure_indices()

    # ------------------------------------------------------------------
    # Index DDL — created idempotently on init.
    # Mirrors the audit.py / licence.py pattern (no global _INDEX_DEFS).
    # ------------------------------------------------------------------

    _INDICES: tuple[str, ...] = (
        "CREATE UNIQUE INDEX IF NOT EXISTS maps_slug_idx ON maps(slug)",
        "CREATE INDEX IF NOT EXISTS maps_owner_idx ON maps(owner_id)",
        (
            "CREATE UNIQUE INDEX IF NOT EXISTS maps_share_token_idx "
            "ON maps(share_token) WHERE share_token IS NOT NULL"
        ),
        (
            "CREATE INDEX IF NOT EXISTS maps_active_idx "
            "ON maps(owner_id, deleted_at) WHERE deleted_at IS NULL"
        ),
    )

    def _ensure_indices(self) -> None:
        for ddl in self._INDICES:
            self._inner._execute(ddl)  # noqa: SLF001 — accessing inner conn intentionally

    # ------------------------------------------------------------------
    # Repository[T] contract — delegated
    # ------------------------------------------------------------------

    def save(self, obj: CocarteMap) -> CocarteMap:
        obj.updated_at = datetime.now(timezone.utc)
        return self._inner.save(obj)

    def get(self, obj_id: UUID, *, include_trashed: bool = False) -> CocarteMap | None:
        sql = "SELECT * FROM maps WHERE id = ?"
        if not include_trashed:
            sql += " AND deleted_at IS NULL"
        rows = self._inner._execute(sql, (str(obj_id),))  # noqa: SLF001
        if not rows:
            return None
        return _row_to_model(CocarteMap, dict(rows[0]))

    def list_all(self, *, include_trashed: bool = False) -> list[CocarteMap]:
        if include_trashed:
            return self._inner.list_all()
        rows = self._inner._execute(  # noqa: SLF001
            "SELECT * FROM maps WHERE deleted_at IS NULL ORDER BY created_at DESC",
        )
        return [_row_to_model(CocarteMap, dict(r)) for r in rows]

    def count(self) -> int:
        rows = self._inner._execute(  # noqa: SLF001
            "SELECT COUNT(*) AS c FROM maps WHERE deleted_at IS NULL",
        )
        return rows[0]["c"] if rows else 0

    def delete(self, obj_id: UUID) -> bool:
        """Hard-delete. Prefer ``soft_delete`` for user-facing trash."""
        return self._inner.delete(obj_id)

    def clear(self) -> None:
        self._inner.clear()

    # ------------------------------------------------------------------
    # Map-specific queries
    # ------------------------------------------------------------------

    def get_by_slug(self, slug: str) -> CocarteMap | None:
        rows = self._inner._execute(  # noqa: SLF001
            "SELECT * FROM maps WHERE slug = ? AND deleted_at IS NULL",
            (slug,),
        )
        if not rows:
            return None
        return _row_to_model(CocarteMap, dict(rows[0]))

    def get_by_share_token(self, token: str) -> CocarteMap | None:
        """Lookup by share token using a constant-time compare.

        SQLite's ``WHERE share_token = ?`` is implemented via the unique
        index and does not leak per-byte timing. The constant-time check
        below applies when *multiple* candidate rows could exist (currently
        impossible thanks to the UNIQUE index, but kept for defence in
        depth if the constraint is ever relaxed).
        """
        if not token:
            return None
        rows = self._inner._execute(  # noqa: SLF001
            "SELECT * FROM maps WHERE share_token IS NOT NULL AND deleted_at IS NULL",
        )
        for row in rows:
            stored = row["share_token"]
            if stored is not None and hmac.compare_digest(stored, token):
                return _row_to_model(CocarteMap, dict(row))
        return None

    def count_for_owner(self, owner_id: UUID | None) -> int:
        """Count active (non-trashed) maps owned by ``owner_id``.

        ``owner_id=None`` matches the legacy single-user instance where
        maps may have no recorded owner.
        """
        if owner_id is None:
            rows = self._inner._execute(  # noqa: SLF001
                "SELECT COUNT(*) AS c FROM maps WHERE owner_id IS NULL AND deleted_at IS NULL",
            )
        else:
            rows = self._inner._execute(  # noqa: SLF001
                "SELECT COUNT(*) AS c FROM maps WHERE owner_id = ? AND deleted_at IS NULL",
                (str(owner_id),),
            )
        return rows[0]["c"] if rows else 0

    def list_for_owner(
        self,
        owner_id: UUID | None,
        *,
        include_trashed: bool = False,
    ) -> list[CocarteMap]:
        sql = "SELECT * FROM maps WHERE "
        params: tuple
        if owner_id is None:
            sql += "owner_id IS NULL"
            params = ()
        else:
            sql += "owner_id = ?"
            params = (str(owner_id),)
        if not include_trashed:
            sql += " AND deleted_at IS NULL"
        sql += " ORDER BY created_at DESC"
        rows = self._inner._execute(sql, params)  # noqa: SLF001
        return [_row_to_model(CocarteMap, dict(r)) for r in rows]

    # ------------------------------------------------------------------
    # Soft-delete
    # ------------------------------------------------------------------

    def soft_delete(self, obj_id: UUID) -> bool:
        rows = self._inner._execute(  # noqa: SLF001
            "SELECT 1 FROM maps WHERE id = ? AND deleted_at IS NULL",
            (str(obj_id),),
        )
        if not rows:
            return False
        now = datetime.now(timezone.utc).isoformat()
        self._inner._execute(  # noqa: SLF001
            "UPDATE maps SET deleted_at = ?, updated_at = ? WHERE id = ?",
            (now, now, str(obj_id)),
        )
        return True

    def restore(self, obj_id: UUID) -> bool:
        now = datetime.now(timezone.utc).isoformat()
        rows_before = self._inner._execute(  # noqa: SLF001
            "SELECT 1 FROM maps WHERE id = ? AND deleted_at IS NOT NULL",
            (str(obj_id),),
        )
        if not rows_before:
            return False
        self._inner._execute(  # noqa: SLF001
            "UPDATE maps SET deleted_at = NULL, updated_at = ? WHERE id = ?",
            (now, str(obj_id)),
        )
        return True

    def purge_older_than(self, cutoff: datetime) -> int:
        """Hard-delete trashed rows older than ``cutoff``. Returns row count."""
        rows = self._inner._execute(  # noqa: SLF001
            "SELECT COUNT(*) AS c FROM maps WHERE deleted_at IS NOT NULL AND deleted_at < ?",
            (cutoff.isoformat(),),
        )
        count = rows[0]["c"] if rows else 0
        if count:
            self._inner._execute(  # noqa: SLF001
                "DELETE FROM maps WHERE deleted_at IS NOT NULL AND deleted_at < ?",
                (cutoff.isoformat(),),
            )
        return count

    # ------------------------------------------------------------------
    # Slug allocation
    # ------------------------------------------------------------------

    def allocate_slug(self, title: str) -> str:
        """Generate a kebab-case slug for *title* unique against the table."""
        base = slugify(title)
        return ensure_unique_slug(
            base,
            exists=lambda candidate: self.get_by_slug(candidate) is not None,
        )
