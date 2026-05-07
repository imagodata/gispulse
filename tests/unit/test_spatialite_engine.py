"""Tests for ``persistence.spatialite_engine`` — issue #105 (Format Frontier T1).

Covers:
- :func:`is_spatialite_file` detection (returns True only for SpatiaLite,
  not GPKG and not bare SQLite).
- :func:`bootstrap_spatialite_project` creates ``_gispulse_*`` tables
  WITHOUT GPKG identity markers.
- :class:`SpatiaLiteEngine` lifecycle (open/close, layer write+read
  roundtrip via pyogrio's ``SQLite`` driver with ``SPATIALITE=YES``).
- Engine factory registration and tier gating (Community).
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import geopandas as gpd
import pytest
from shapely.geometry import Point

from persistence.gpkg_schema import (
    bootstrap_gpkg_project,
    bootstrap_spatialite_project,
)
from persistence.spatialite_engine import SpatiaLiteEngine, is_spatialite_file


@pytest.fixture
def sample_gdf() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {
            "name": ["A", "B", "C"],
            "category": [1, 2, 3],
            "geometry": [Point(0, 0), Point(1, 1), Point(2, 2)],
        },
        crs="EPSG:4326",
    )


@pytest.fixture
def fresh_spatialite_path(tmp_path: Path, sample_gdf: gpd.GeoDataFrame) -> Path:
    """Create a SpatiaLite file with one layer via pyogrio."""
    path = tmp_path / "fixture.sqlite"
    sample_gdf.to_file(
        str(path),
        driver="SQLite",
        layer="places",
        SPATIALITE="YES",
    )
    return path


# ---------------------------------------------------------------------------
# is_spatialite_file
# ---------------------------------------------------------------------------


class TestIsSpatialiteFile:
    def test_detects_spatialite_file(self, fresh_spatialite_path: Path) -> None:
        assert is_spatialite_file(fresh_spatialite_path) is True

    def test_rejects_gpkg(self, tmp_path: Path, sample_gdf: gpd.GeoDataFrame) -> None:
        gpkg = tmp_path / "x.gpkg"
        sample_gdf.to_file(str(gpkg), driver="GPKG", layer="places")
        assert is_spatialite_file(gpkg) is False

    def test_rejects_plain_sqlite(self, tmp_path: Path) -> None:
        path = tmp_path / "plain.sqlite"
        with sqlite3.connect(str(path)) as conn:
            conn.execute("CREATE TABLE t (x INTEGER)")
            conn.commit()
        assert is_spatialite_file(path) is False

    def test_rejects_missing_file(self, tmp_path: Path) -> None:
        assert is_spatialite_file(tmp_path / "ghost.sqlite") is False

    def test_rejects_directory(self, tmp_path: Path) -> None:
        assert is_spatialite_file(tmp_path) is False


# ---------------------------------------------------------------------------
# bootstrap_spatialite_project — does NOT corrupt SpatiaLite identity
# ---------------------------------------------------------------------------


class TestBootstrapSpatialite:
    def test_creates_gispulse_internals(self, fresh_spatialite_path: Path) -> None:
        with sqlite3.connect(str(fresh_spatialite_path)) as conn:
            bootstrap_spatialite_project(conn)
            tables = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' "
                    "AND name LIKE '_gispulse_%'"
                )
            }
        assert "_gispulse_change_log" in tables
        assert "_gispulse_kv" in tables

    def test_does_not_set_gpkg_application_id(
        self, fresh_spatialite_path: Path
    ) -> None:
        with sqlite3.connect(str(fresh_spatialite_path)) as conn:
            bootstrap_spatialite_project(conn)
            app_id = conn.execute("PRAGMA application_id").fetchone()[0]
        # GPKG application_id is 0x47504B47 = 1196444487. SpatiaLite must
        # NOT carry that marker — pyogrio SQLite driver leaves it 0 by
        # default and bootstrap_spatialite_project must preserve that.
        assert app_id != 1196444487

    def test_does_not_create_gpkg_catalog(
        self, fresh_spatialite_path: Path
    ) -> None:
        with sqlite3.connect(str(fresh_spatialite_path)) as conn:
            bootstrap_spatialite_project(conn)
            tables = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master "
                    "WHERE type='table' AND name LIKE 'gpkg_%'"
                )
            }
        # We must not have polluted the SpatiaLite file with GPKG catalog
        # tables — that would confuse OGR / QGIS auto-detection.
        assert tables == set()

    def test_idempotent(self, fresh_spatialite_path: Path) -> None:
        with sqlite3.connect(str(fresh_spatialite_path)) as conn:
            bootstrap_spatialite_project(conn)
            bootstrap_spatialite_project(conn)  # second call must not raise

    def test_gpkg_bootstrap_unchanged(
        self, tmp_path: Path, sample_gdf: gpd.GeoDataFrame
    ) -> None:
        # Regression guard — the refactor that extracted
        # _bootstrap_gispulse_internals must not have broken
        # bootstrap_gpkg_project's GPKG-identity behaviour.
        gpkg = tmp_path / "x.gpkg"
        sample_gdf.to_file(str(gpkg), driver="GPKG", layer="places")
        with sqlite3.connect(str(gpkg)) as conn:
            bootstrap_gpkg_project(conn)
            app_id = conn.execute("PRAGMA application_id").fetchone()[0]
            tables = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master "
                    "WHERE name IN ('gpkg_contents', 'gpkg_extensions', "
                    "'_gispulse_change_log')"
                )
            }
        assert app_id == 1196444487
        assert "gpkg_contents" in tables
        assert "gpkg_extensions" in tables
        assert "_gispulse_change_log" in tables


# ---------------------------------------------------------------------------
# SpatiaLiteEngine — lifecycle + I/O
# ---------------------------------------------------------------------------


class TestSpatiaLiteEngineLifecycle:
    def test_open_creates_internal_tables(self, fresh_spatialite_path: Path) -> None:
        engine = SpatiaLiteEngine(fresh_spatialite_path)
        with engine:
            with sqlite3.connect(str(fresh_spatialite_path)) as conn:
                row = conn.execute(
                    "SELECT 1 FROM sqlite_master "
                    "WHERE type='table' AND name='_gispulse_change_log'"
                ).fetchone()
            assert row is not None

    def test_open_does_not_corrupt_spatialite_identity(
        self, fresh_spatialite_path: Path
    ) -> None:
        engine = SpatiaLiteEngine(fresh_spatialite_path)
        with engine:
            pass
        # After open(), the file must still be detected as SpatiaLite.
        assert is_spatialite_file(fresh_spatialite_path) is True

    def test_open_then_close_cleanly(self, fresh_spatialite_path: Path) -> None:
        engine = SpatiaLiteEngine(fresh_spatialite_path)
        engine.open()
        engine.close()
        # Second close is a no-op
        engine.close()

    def test_list_layers_excludes_gispulse_internals(
        self, fresh_spatialite_path: Path
    ) -> None:
        engine = SpatiaLiteEngine(fresh_spatialite_path)
        with engine:
            layers = engine.list_layers()
        assert "places" in layers
        assert not any(name.startswith("_gispulse_") for name in layers)


class TestSpatiaLiteEngineIO:
    def test_write_then_read_roundtrip(
        self, tmp_path: Path, sample_gdf: gpd.GeoDataFrame
    ) -> None:
        path = tmp_path / "rt.sqlite"
        engine = SpatiaLiteEngine(path)
        with engine:
            engine.write_layer(sample_gdf, layer="places")
            loaded = engine.load_layer("places", layer="places")
        assert len(loaded) == 3
        assert set(loaded["name"]) == {"A", "B", "C"}

    def test_replace_existing_layer(
        self, tmp_path: Path, sample_gdf: gpd.GeoDataFrame
    ) -> None:
        path = tmp_path / "rt.sqlite"
        engine = SpatiaLiteEngine(path)
        # First write
        with engine:
            engine.write_layer(sample_gdf, layer="places", if_exists="replace")
            engine.write_layer(
                sample_gdf.head(1), layer="places", if_exists="replace"
            )
            loaded = engine.load_layer("places", layer="places")
        assert len(loaded) == 1

    def test_creates_fresh_file(
        self, tmp_path: Path, sample_gdf: gpd.GeoDataFrame
    ) -> None:
        path = tmp_path / "fresh.sqlite"
        assert not path.exists()
        engine = SpatiaLiteEngine(path)
        with engine:
            engine.write_layer(sample_gdf, layer="places")
        assert path.exists()
        assert is_spatialite_file(path) is True


# ---------------------------------------------------------------------------
# Engine factory registration
# ---------------------------------------------------------------------------


class TestEngineFactoryRegistration:
    def test_spatialite_factory_registered(self) -> None:
        from persistence.engine_factory import _BACKENDS

        assert "spatialite" in _BACKENDS

    def test_factory_callable_returns_spatialite(self, tmp_path: Path) -> None:
        # ``create_spatial_engine`` does not pass arbitrary kwargs to the
        # backend factories (they read paths from settings). Test the
        # registered factory directly to confirm it returns the right
        # class for the right backend name.
        from persistence.engine_factory import _BACKENDS

        path = tmp_path / "x.sqlite"
        engine = _BACKENDS["spatialite"](spatialite_path=str(path))
        assert isinstance(engine, SpatiaLiteEngine)

    def test_spatialite_is_community_tier(self) -> None:
        from persistence.tier import enforce_engine_tier

        # Must not raise TierError for community-tier callers.
        enforce_engine_tier("spatialite")
