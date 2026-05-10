"""Pre-flight migration test: a v1.6.x database keeps working under v1.7.

Issue #56 introduces a new `maps` table to `_TABLE_DEFS`. Existing
GISPulse instances on v1.6.x carry SQLite databases without that table.
On startup the new code must:

1. Create the `maps` table idempotently (CREATE TABLE IF NOT EXISTS).
2. Leave existing rows in `projects`, `triggers`, `rules`, … untouched.
3. Not throw on `MapRepository.__init__()`.

This is the only "migration" surface — there is no Alembic, no schema
version table, just the bootstrap that runs in
`SQLiteRepository.__init__`. The test simulates the v1.6 → v1.7
upgrade by:

- creating a temp DB with v1.6-shaped tables (projects only, no maps);
- instantiating `MapRepository` on that DB;
- asserting both `projects` data survives and `maps` table is now present.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from uuid import uuid4

import pytest

from core.models import Project
from persistence.map_io import MapRepository
from persistence.sqlite_repository import SQLiteRepository


def _seed_v16_database(db_path: Path) -> str:
    """Write a v1.6-shaped DB (no `maps` table) and return a project id."""
    # Create the projects table with the v1.6 shape (verbatim from
    # persistence/schema.py @ SCHEMA_VERSION=3 to avoid coupling to the
    # current v4 _TABLE_DEFS, which would mask any unintended addition).
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """
        CREATE TABLE projects (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            description TEXT DEFAULT '',
            schema_name TEXT DEFAULT 'public',
            engine_backend TEXT DEFAULT 'duckdb',
            dsn TEXT,
            datasets TEXT DEFAULT '[]',
            rules TEXT DEFAULT '[]',
            triggers TEXT DEFAULT '[]',
            metadata TEXT DEFAULT '{}',
            created_at TEXT,
            updated_at TEXT
        )
        """
    )
    pid = str(uuid4())
    conn.execute(
        "INSERT INTO projects (id, name, description) VALUES (?, ?, ?)",
        (pid, "Pre-existing v1.6 project", "Should survive the upgrade"),
    )
    conn.commit()
    conn.close()
    return pid


def test_v16_database_survives_v17_startup(tmp_path: Path) -> None:
    db_path = tmp_path / "v16.db"
    pre_existing_pid = _seed_v16_database(db_path)

    # Verify pre-state: no maps table, one project row.
    raw = sqlite3.connect(str(db_path))
    tables = {r[0] for r in raw.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )}
    assert "projects" in tables
    assert "maps" not in tables
    raw.close()

    # Simulate v1.7 startup: instantiating SQLiteRepository[Project]
    # bootstraps the projects schema via CREATE TABLE IF NOT EXISTS,
    # and MapRepository creates the maps table the same way.
    project_repo = SQLiteRepository(Project, db_path=db_path)
    map_repo = MapRepository(db_path=db_path)

    # 1. The pre-existing project row is intact.
    from uuid import UUID

    loaded = project_repo.get(UUID(pre_existing_pid))
    assert loaded is not None
    assert loaded.name == "Pre-existing v1.6 project"

    # 2. The maps table now exists and is empty.
    raw = sqlite3.connect(str(db_path))
    tables = {r[0] for r in raw.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )}
    assert "maps" in tables
    rows = raw.execute("SELECT COUNT(*) FROM maps").fetchone()
    assert rows[0] == 0

    # 3. The maps indices are present (4 indices on maps).
    indices = {
        r[0] for r in raw.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='index' AND tbl_name='maps'"
        )
    }
    raw.close()
    assert "maps_slug_idx" in indices
    assert "maps_owner_idx" in indices
    assert "maps_share_token_idx" in indices
    assert "maps_active_idx" in indices

    # 4. The Map repo is functional after the upgrade.
    assert map_repo.count() == 0
    assert map_repo.list_all() == []


def test_repeated_startup_is_idempotent(tmp_path: Path) -> None:
    """Two consecutive instantiations of MapRepository must not error."""
    db_path = tmp_path / "idempotent.db"

    first = MapRepository(db_path=db_path)
    assert first.count() == 0

    # Simulate a server restart — second instantiation against the same DB.
    second = MapRepository(db_path=db_path)
    assert second.count() == 0

    # Both can read the same row.
    from core.models import CocarteMap

    m = CocarteMap(slug="restart-survives", title="Restart test")
    first.save(m)
    assert second.get(m.id) is not None
    # Ensure the first connection is closed cleanly; pytest tmp_path
    # cleanup will fail on Windows otherwise.
    pass


@pytest.mark.parametrize("preset_visibility", ["private", "unlisted", "public"])
def test_v17_round_trip_after_upgrade(tmp_path: Path, preset_visibility: str) -> None:
    """End-to-end : seed v1.6 DB, boot v1.7, write a map, read it back."""
    db_path = tmp_path / "roundtrip.db"
    _seed_v16_database(db_path)

    repo = MapRepository(db_path=db_path)

    from core.models import CocarteMap, MapVisibility

    m = CocarteMap(
        slug=f"upgrade-{preset_visibility}",
        title="upgraded map",
        visibility=MapVisibility(preset_visibility),
    )
    repo.save(m)

    loaded = repo.get(m.id)
    assert loaded is not None
    assert loaded.visibility == MapVisibility(preset_visibility)
    assert loaded.slug == f"upgrade-{preset_visibility}"
