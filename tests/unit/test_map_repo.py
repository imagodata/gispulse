"""Unit tests for `persistence.map_io.MapRepository`."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest

from core.models import CocarteMap, MapVisibility
from persistence.map_io import MapRepository


@pytest.fixture
def repo(tmp_path: Path) -> MapRepository:
    return MapRepository(db_path=tmp_path / "test_maps.db")


def _make_map(
    *,
    title: str = "Test Map",
    slug: str = "test-map",
    owner_id=None,
    visibility: MapVisibility = MapVisibility.PRIVATE,
    share_token: str | None = None,
) -> CocarteMap:
    return CocarteMap(
        title=title,
        slug=slug,
        owner_id=owner_id,
        visibility=visibility,
        share_token=share_token,
    )


class TestBasicCRUD:
    def test_save_and_get(self, repo: MapRepository) -> None:
        m = _make_map()
        repo.save(m)
        loaded = repo.get(m.id)
        assert loaded is not None
        assert loaded.id == m.id
        assert loaded.slug == "test-map"
        assert loaded.visibility is MapVisibility.PRIVATE

    def test_get_missing_returns_none(self, repo: MapRepository) -> None:
        assert repo.get(uuid4()) is None

    def test_visibility_enum_round_trip(self, repo: MapRepository) -> None:
        m = _make_map(visibility=MapVisibility.PUBLIC)
        repo.save(m)
        loaded = repo.get(m.id)
        assert loaded is not None
        assert loaded.visibility is MapVisibility.PUBLIC
        assert loaded.visibility.value == "public"

    def test_save_updates_updated_at(self, repo: MapRepository) -> None:
        m = _make_map()
        repo.save(m)
        first_updated = m.updated_at
        # Mutate and re-save
        m.title = "renamed"
        repo.save(m)
        loaded = repo.get(m.id)
        assert loaded is not None
        assert loaded.updated_at >= first_updated


class TestSlugLookup:
    def test_get_by_slug(self, repo: MapRepository) -> None:
        m = _make_map(slug="my-unique-slug")
        repo.save(m)
        assert repo.get_by_slug("my-unique-slug") is not None
        assert repo.get_by_slug("does-not-exist") is None

    def test_unique_slug_constraint(self, repo: MapRepository) -> None:
        repo.save(_make_map(slug="duplicated"))
        with pytest.raises(Exception):  # sqlite IntegrityError
            repo.save(_make_map(slug="duplicated"))

    def test_allocate_slug_unique(self, repo: MapRepository) -> None:
        repo.save(_make_map(slug="elections-2026"))
        candidate = repo.allocate_slug("Élections 2026")
        assert candidate != "elections-2026"
        assert candidate.startswith("elections-2026-")

    def test_allocate_slug_avoids_reserved(self, repo: MapRepository) -> None:
        candidate = repo.allocate_slug("admin")
        assert candidate.startswith("admin-")

    def test_get_by_slug_skips_trashed(self, repo: MapRepository) -> None:
        m = _make_map(slug="will-trash")
        repo.save(m)
        repo.soft_delete(m.id)
        assert repo.get_by_slug("will-trash") is None


class TestShareToken:
    def test_get_by_share_token(self, repo: MapRepository) -> None:
        m = _make_map(visibility=MapVisibility.UNLISTED, share_token="abc123token")
        repo.save(m)
        loaded = repo.get_by_share_token("abc123token")
        assert loaded is not None
        assert loaded.id == m.id

    def test_get_by_share_token_wrong_token(self, repo: MapRepository) -> None:
        m = _make_map(visibility=MapVisibility.UNLISTED, share_token="real-token")
        repo.save(m)
        assert repo.get_by_share_token("wrong-token") is None

    def test_get_by_share_token_empty(self, repo: MapRepository) -> None:
        assert repo.get_by_share_token("") is None

    def test_get_by_share_token_skips_trashed(self, repo: MapRepository) -> None:
        m = _make_map(visibility=MapVisibility.UNLISTED, share_token="will-trash-tok")
        repo.save(m)
        repo.soft_delete(m.id)
        assert repo.get_by_share_token("will-trash-tok") is None


class TestOwnerScoping:
    def test_count_for_owner(self, repo: MapRepository) -> None:
        owner_a, owner_b = uuid4(), uuid4()
        repo.save(_make_map(slug="a1", owner_id=owner_a))
        repo.save(_make_map(slug="a2", owner_id=owner_a))
        repo.save(_make_map(slug="b1", owner_id=owner_b))
        assert repo.count_for_owner(owner_a) == 2
        assert repo.count_for_owner(owner_b) == 1
        assert repo.count_for_owner(uuid4()) == 0

    def test_count_for_owner_none(self, repo: MapRepository) -> None:
        repo.save(_make_map(slug="legacy", owner_id=None))
        repo.save(_make_map(slug="owned", owner_id=uuid4()))
        assert repo.count_for_owner(None) == 1

    def test_count_excludes_trashed(self, repo: MapRepository) -> None:
        owner = uuid4()
        m1 = _make_map(slug="keep", owner_id=owner)
        m2 = _make_map(slug="trash", owner_id=owner)
        repo.save(m1)
        repo.save(m2)
        repo.soft_delete(m2.id)
        assert repo.count_for_owner(owner) == 1

    def test_list_for_owner_includes_trashed_when_requested(self, repo: MapRepository) -> None:
        owner = uuid4()
        m1 = _make_map(slug="keep2", owner_id=owner)
        m2 = _make_map(slug="trash2", owner_id=owner)
        repo.save(m1)
        repo.save(m2)
        repo.soft_delete(m2.id)
        active = repo.list_for_owner(owner)
        full = repo.list_for_owner(owner, include_trashed=True)
        assert len(active) == 1
        assert len(full) == 2


class TestSoftDelete:
    def test_soft_delete_then_restore(self, repo: MapRepository) -> None:
        m = _make_map(slug="soft")
        repo.save(m)
        assert repo.soft_delete(m.id) is True
        assert repo.get(m.id) is None
        assert repo.get(m.id, include_trashed=True) is not None
        assert repo.restore(m.id) is True
        assert repo.get(m.id) is not None

    def test_soft_delete_missing_returns_false(self, repo: MapRepository) -> None:
        assert repo.soft_delete(uuid4()) is False

    def test_restore_active_returns_false(self, repo: MapRepository) -> None:
        m = _make_map(slug="active")
        repo.save(m)
        assert repo.restore(m.id) is False

    def test_purge_older_than(self, repo: MapRepository) -> None:
        m1 = _make_map(slug="old")
        m2 = _make_map(slug="recent")
        repo.save(m1)
        repo.save(m2)
        repo.soft_delete(m1.id)
        repo.soft_delete(m2.id)
        # Backdate m1's deleted_at by raw SQL
        old_ts = (datetime.now(timezone.utc) - timedelta(days=60)).isoformat()
        repo._inner._execute(  # noqa: SLF001
            "UPDATE maps SET deleted_at = ? WHERE id = ?",
            (old_ts, str(m1.id)),
        )
        cutoff = datetime.now(timezone.utc) - timedelta(days=30)
        purged = repo.purge_older_than(cutoff)
        assert purged == 1
        assert repo.get(m1.id, include_trashed=True) is None
        assert repo.get(m2.id, include_trashed=True) is not None
